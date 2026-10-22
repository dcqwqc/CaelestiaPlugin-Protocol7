"""Offline integration: recorder -> Groq preview -> ghost job -> final -> insert.

The live preview is display-only. These tests pin the contract that the
final always transcribes the complete original recording exactly once per
attempt, whatever the preview did. No test opens a network connection or
touches user state: sockets are blocked, history and ghost-job persistence
are redirected, and Groq is an in-memory fake object.
"""
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

try:
    import sounddevice  # noqa: F401
except Exception:
    sys.modules["sounddevice"] = types.ModuleType("sounddevice")

try:
    import ghost_wave  # noqa: F401
except Exception:
    _ghost_wave = types.ModuleType("ghost_wave")

    class _Target:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    _ghost_wave.Target = _Target
    sys.modules["ghost_wave"] = _ghost_wave

import audio as audio_module
import ghost_jobs
import streaming_stt as st
from config import DEFAULT_CONFIG

R = 16000
_socket_guard = None


def setUpModule():
    global _socket_guard
    _socket_guard = mock.patch.object(
        socket.socket, "connect",
        side_effect=AssertionError("network access is forbidden in tests"),
    )
    _socket_guard.start()


def tearDownModule():
    _socket_guard.stop()


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def voiced(seconds, amp=0.1, f0=140.0):
    t = np.arange(int(round(seconds * R))) / R
    tone = sum((0.6 ** k) * np.sin(2 * np.pi * f0 * (k + 1) * t) for k in range(5))
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t)
    return (amp * tone * envelope / np.max(np.abs(tone))).astype(np.float32)


class FakeUI:
    def __init__(self):
        self.rows = []
        self.live = []

    def set_jobs(self, rows):
        self.rows.append(rows)

    def set_live_text(self, text):
        self.live.append(text)

    def hide(self):
        pass

    def show(self):
        pass


class FakeWhisper:
    """Final-dictation double: records exactly what the final received."""

    def __init__(self, text="batch text", gate=None):
        self.text = text
        self.gate = gate
        self.calls = 0
        self.audio = []
        self.finals_in_flight = []
        self.client = None

    def transcribe(self, audio):
        self.calls += 1
        self.audio.append(np.array(audio, copy=True))
        self.finals_in_flight.append(st.default_budget()._finals_in_flight)
        if self.gate is not None:
            self.gate.wait(5.0)
        return self.text

    def groq_preview_client(self):
        return (self.client, "whisper-large-v3-turbo") if self.client is not None else None


class FakeRewriter:
    def rewrite(self, text):
        return text


class FakeTarget:
    def __init__(self, active=True):
        self.active = active

    def same_window_active(self):
        return self.active

    def as_public(self):
        return {"app": "test"}


class FakeHook:
    def __init__(self):
        self.armed = False

    def arm(self):
        self.armed = True
        return True

    def disarm(self):
        self.armed = False
        return True

    def forward_return(self):
        return True


class FakeApp:
    def __init__(self):
        self.ui_manager = FakeUI()
        self.whisper_engine = FakeWhisper()
        self.llm_rewriter = FakeRewriter()
        self.is_active = False
        self.hotkey = None
        self.enter_interceptor = None
        self.pasted = []
        self.enters = 0

    def paste_text(self, text):
        self.pasted.append(text)
        return True

    def send_enter(self):
        self.enters += 1
        return True


class FakeStream:
    """A preview session double holding text the final must never use."""

    def __init__(self, preview="live words that must not be pasted", stop_error=None):
        self.preview_text = preview
        self.stop_error = stop_error
        self.stops = 0
        self.cancelled = threading.Event()

    def stop(self):
        self.stops += 1
        if self.stop_error is not None:
            raise self.stop_error

    def cancel(self):
        self.cancelled.set()


class Transcriptions:
    def __init__(self, text="Preview words."):
        self.text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": self.text}


class FakeGroqClient:
    def __init__(self, transcriptions):
        self.audio = types.SimpleNamespace(transcriptions=transcriptions)
        self.base_url = "https://api.groq.com/openai/v1"


def make_recorder():
    recorder = audio_module.AudioRecorder(sample_rate=16000)
    recorder.is_recording = True
    recorder.actual_sample_rate = 16000
    recorder.channels = 1
    return recorder


def feed(recorder, n=1600, value=0.1):
    recorder._audio_callback(np.full((n, 1), value, dtype=np.float32), n, None, None)


def feed_audio(recorder, audio, chunk=512):
    for i in range(0, len(audio), chunk):
        part = audio[i:i + chunk].reshape(-1, 1)
        recorder._audio_callback(part, len(part), None, None)


class GhostTestCase(unittest.TestCase):
    def setUp(self):
        self.history = []
        patcher = mock.patch.object(ghost_jobs, "add_history", self.history.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Fresh process-wide budget: finals recorded by other tests must not
        # rate-limit this test's previews.
        budget = mock.patch.object(st, "_DEFAULT_BUDGET", st.PreviewBudget())
        budget.start()
        self.addCleanup(budget.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = FakeApp()

    def make_jobs(self, app=None):
        with mock.patch.object(ghost_jobs.Path, "home", return_value=Path(self.tmp.name)):
            jobs = ghost_jobs.GhostJobs(app or self.app)
        # Drain this test's workers before its patches and tmpdir go away, so
        # a late job can never write into the next test's history.
        self.addCleanup(jobs.executor.shutdown, wait=True)
        return jobs

    @staticmethod
    def audio():
        return np.linspace(-0.5, 0.5, 24000, dtype=np.float32)


class FinalAlwaysFullRecordingTests(GhostTestCase):
    def test_preview_text_never_replaces_the_final(self):
        jobs = self.make_jobs()
        stream = FakeStream()
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(self.app.pasted, ["batch text"])
        self.assertEqual(self.history, ["batch text"])
        self.assertEqual(self.app.whisper_engine.calls, 1)
        self.assertEqual(stream.stops, 1)
        self.assertIsNone(job.stream)

    def test_final_receives_the_complete_original_audio(self):
        jobs = self.make_jobs()
        audio = self.audio()
        job = jobs.enqueue(audio, FakeTarget(), stream=FakeStream())
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        np.testing.assert_array_equal(self.app.whisper_engine.audio[0], audio)

    def test_final_runs_with_preview_priority(self):
        jobs = self.make_jobs()
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=FakeStream())
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertGreaterEqual(self.app.whisper_engine.finals_in_flight[0], 1)
        self.assertEqual(st.default_budget()._finals_in_flight, 0)

    def test_failed_preview_stop_never_prevents_final(self):
        jobs = self.make_jobs()
        stream = FakeStream(stop_error=RuntimeError("preview broke"))
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(self.app.pasted, ["batch text"])
        self.assertTrue(stream.cancelled.is_set())

    def test_without_stream_behaviour_is_unchanged(self):
        jobs = self.make_jobs()
        job = jobs.enqueue(self.audio(), FakeTarget())
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(self.app.pasted, ["batch text"])
        self.assertEqual(self.app.whisper_engine.calls, 1)

    def test_retry_transcribes_full_audio_again_without_preview(self):
        self.app.whisper_engine.text = ""
        jobs = self.make_jobs()
        stream = FakeStream()
        audio = self.audio()
        job = jobs.enqueue(audio, FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "error"))
        self.app.whisper_engine.text = "retried"
        self.assertTrue(jobs.retry(job.id)["ok"])
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(stream.stops, 1)
        self.assertEqual(self.app.whisper_engine.calls, 2)
        np.testing.assert_array_equal(self.app.whisper_engine.audio[1], audio)
        self.assertEqual(self.app.pasted, ["retried"])

    def test_too_short_recording_stops_preview_and_makes_no_call(self):
        jobs = self.make_jobs()
        stream = FakeStream()
        job = jobs.enqueue(np.zeros(4000, dtype=np.float32), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "error"))
        self.assertEqual(stream.stops, 1)
        self.assertEqual(self.app.whisper_engine.calls, 0)


class CancelFocusSafetyTests(GhostTestCase):
    def test_dismissed_job_cancels_preview_and_makes_no_final_call(self):
        jobs = self.make_jobs()
        stream = FakeStream()
        with mock.patch.object(jobs.executor, "submit") as submit:
            job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(jobs.dismiss_visible())
        self.assertTrue(stream.cancelled.is_set())
        jobs._run(submit.call_args.args[1])  # the queued run arrives late
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(self.app.whisper_engine.calls, 0)
        self.assertEqual(self.app.pasted, [])
        self.assertEqual(self.history, [])

    def test_cancel_during_final_never_inserts(self):
        gate = threading.Event()
        self.app.whisper_engine.gate = gate
        jobs = self.make_jobs()
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=FakeStream())
        self.assertTrue(wait_until(lambda: self.app.whisper_engine.calls == 1))
        self.assertTrue(jobs.cancel(job.id)["ok"])
        gate.set()
        time.sleep(0.2)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(self.app.whisper_engine.calls, 1)
        self.assertEqual(self.app.pasted, [])
        self.assertEqual(self.history, [])

    def test_final_timeout_shows_retry_and_late_result_is_dropped(self):
        gate = threading.Event()
        self.app.whisper_engine.gate = gate
        jobs = self.make_jobs()
        jobs.job_timeout_seconds = 0.2
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=FakeStream())
        self.assertTrue(wait_until(lambda: job.status == "error"))
        gate.set()
        time.sleep(0.2)
        self.assertEqual(job.status, "error")
        self.assertEqual(self.app.pasted, [])
        self.assertEqual(self.history, [])

    def test_focus_change_keeps_ready_chip_without_insert(self):
        jobs = self.make_jobs()
        job = jobs.enqueue(self.audio(), FakeTarget(active=False), stream=FakeStream())
        self.assertTrue(wait_until(lambda: job.status == "ready"))
        time.sleep(0.1)
        self.assertEqual(self.app.pasted, [])
        self.assertEqual(job.text, "batch text")

    def test_enter_queued_during_final_sends_once(self):
        gate = threading.Event()
        self.app.whisper_engine.gate = gate
        self.app.enter_interceptor = FakeHook()
        jobs = self.make_jobs()
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=FakeStream())
        self.assertTrue(wait_until(lambda: job.status == "processing" and self.app.whisper_engine.calls == 1))
        self.assertTrue(jobs.queue_send()["send_queued"])
        gate.set()
        self.assertTrue(wait_until(lambda: job.status == "inserted" and self.app.enters == 1))
        self.assertEqual(self.app.pasted, ["batch text"])

    def test_busy_queue_rejects_and_cancels_preview(self):
        gate = threading.Event()
        self.app.whisper_engine.gate = gate
        jobs = self.make_jobs()
        for _ in range(4):
            jobs.enqueue(self.audio(), FakeTarget())
        stream = FakeStream()
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertEqual(job.status, "error")
        self.assertTrue(stream.cancelled.is_set())
        gate.set()


class RecorderTests(unittest.TestCase):
    def test_attach_replays_and_forwards_without_loss_or_duplication(self):
        recorder = make_recorder()
        received = []
        feed(recorder)
        feed(recorder)
        recorder.attach_stream_listener(received.append)
        feed(recorder)
        feed(recorder, value=0.2)
        audio = recorder.stop_recording()
        self.assertEqual(len(audio), 6400)
        np.testing.assert_array_equal(np.concatenate(received), audio)
        self.assertIsNone(recorder._stream_listener)

    def test_listener_failure_never_breaks_capture(self):
        recorder = make_recorder()

        def broken(_chunk):
            raise RuntimeError("listener failed")
        recorder._stream_listener = broken
        feed(recorder)
        feed(recorder)
        self.assertEqual(len(recorder.stop_recording()), 3200)

    def test_preview_queue_pressure_never_loses_final_audio_or_blocks(self):
        recorder = make_recorder()
        session = st.GroqChunkSession(st.PreviewSettings(max_queue_chunks=2), client_provider=lambda: None)
        recorder.attach_stream_listener(session.push_audio)  # worker never started
        source = voiced(5.0)
        started = time.monotonic()
        feed_audio(recorder, source)
        elapsed = time.monotonic() - started
        audio = recorder.stop_recording()
        np.testing.assert_array_equal(audio, source)
        self.assertGreater(session._overflow_samples, 0)
        self.assertLess(elapsed, 2.0)
        session.cancel()


class EndToEndTests(GhostTestCase):
    def test_preview_during_recording_then_final_on_full_audio(self):
        api = Transcriptions("Preview words.")
        client = FakeGroqClient(api)
        partials = []
        session = st.GroqChunkSession(
            st.PreviewSettings(interval_seconds=4.0, first_preview_seconds=1.0, idle_timeout=30.0),
            input_rate=R, client_provider=lambda: (client, "whisper-large-v3-turbo"),
            budget=st.PreviewBudget(), on_partial=partials.append,
        )
        self.addCleanup(session.cancel)
        recorder = make_recorder()
        feed_audio(recorder, voiced(0.4))
        session.start()
        recorder.attach_stream_listener(session.push_audio)
        feed_audio(recorder, voiced(1.0))
        self.assertTrue(wait_until(lambda: partials == ["Preview words."]))
        audio = recorder.stop_recording()
        session.stop()
        calls = len(api.calls)
        feed_audio(recorder, voiced(6.0))  # no longer recording: nothing reaches Groq
        session.push_audio(voiced(6.0))
        jobs = self.make_jobs()
        job = jobs.enqueue(audio, FakeTarget(), stream=session)
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(self.app.pasted, ["batch text"])
        self.assertEqual(self.app.whisper_engine.calls, 1)
        np.testing.assert_array_equal(self.app.whisper_engine.audio[0], audio)
        self.assertEqual(len(audio), int(1.4 * R))
        time.sleep(0.15)
        self.assertEqual(len(api.calls), calls)

    def test_default_config_never_creates_session(self):
        self.assertFalse(DEFAULT_CONFIG["streaming_stt_enabled"])
        provider = mock.Mock(side_effect=AssertionError("no client by default"))
        self.assertEqual(st.availability(dict(DEFAULT_CONFIG)), "disabled")
        self.assertIsNone(st.create_session(dict(DEFAULT_CONFIG), client_provider=provider))
        provider.assert_not_called()

    def test_legacy_openai_realtime_config_stays_off(self):
        config = dict(DEFAULT_CONFIG, streaming_stt_enabled=True, use_groq=True, groq_api_key="gsk",
                      streaming_stt_provider="openai-realtime", streaming_stt_api_key="k",
                      streaming_stt_billing_authorized=True)
        self.assertEqual(st.availability(config), "unsupported provider")
        self.assertIsNone(st.create_session(config, client_provider=lambda: None))


def import_main_linux():
    """Import main_linux with only its unavailable hardware deps stubbed."""
    stubs = {}
    try:
        import hotkey_linux  # noqa: F401
    except Exception:
        module = types.ModuleType("hotkey_linux")
        module.HotkeyListener = object
        stubs["hotkey_linux"] = module
    try:
        import companion.wake_runtime  # noqa: F401
    except Exception:
        package = types.ModuleType("companion")
        module = types.ModuleType("companion.wake_runtime")
        module.WakeRuntime = object
        package.wake_runtime = module
        stubs.update({"companion": package, "companion.wake_runtime": module})
    with mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("main_linux", None)
        import main_linux
    return main_linux


class MainWiringTests(GhostTestCase):
    def make_app(self, config):
        main_linux = import_main_linux()
        self.debug_log = []
        patcher = mock.patch.object(main_linux, "log_debug", self.debug_log.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = main_linux.Protocol7App.__new__(main_linux.Protocol7App)
        app.config = config
        app.audio_recorder = make_recorder()
        app.ui_manager = self.app.ui_manager
        app.whisper_engine = self.app.whisper_engine
        app.llm_rewriter = FakeRewriter()
        app.hotkey = types.SimpleNamespace(focus_epoch=lambda: 1, non_enter_focus_epoch=lambda: 1)
        app.enter_interceptor = None
        app.is_active = False
        app.is_processing = False
        app._dictation_lock = threading.RLock()
        app._recording_target = None
        app._stream_session = None
        app.paste_text = self.app.paste_text
        app._ghost = self.make_jobs(app)
        return app

    def test_enabled_preview_uses_final_groq_client_and_stops_with_recording(self):
        api = Transcriptions("Shown while speaking.")
        self.app.whisper_engine.client = FakeGroqClient(api)
        config = dict(DEFAULT_CONFIG, streaming_stt_enabled=True, use_groq=True, groq_api_key="gsk_x",
                      streaming_stt_free_tier_verified=True, streaming_stt_quota_partition_verified=True,
                      streaming_stt_verified_at=time.time(), streaming_stt_quota_ledger="/offline-test-ledger")
        patch = mock.patch.object(st, "configure_budget")
        patch.start()
        self.addCleanup(patch.stop)
        app = self.make_app(config)
        with mock.patch.object(st, "settings_from_config",
                               return_value=st.PreviewSettings(first_preview_seconds=1.0, idle_timeout=30.0)):
            app.is_active = True
            app._start_stream_session()
        session = app._stream_session
        self.assertIsInstance(session, st.GroqChunkSession)
        feed_audio(app.audio_recorder, voiced(1.2))
        self.assertTrue(wait_until(lambda: self.app.ui_manager.live == ["Shown while speaking."]))
        status = app._toggle_ghost("control-stop")
        self.assertTrue(status["ok"])
        self.assertFalse(session.active)
        self.assertIsNone(app._stream_session)
        self.assertTrue(wait_until(lambda: any(j.status == "ready" for j in app._ghost.jobs.values())))
        job = next(iter(app._ghost.jobs.values()))
        self.assertEqual(job.text, "batch text")
        self.assertEqual(self.app.whisper_engine.calls, 1)
        self.assertEqual(len(self.app.whisper_engine.audio[0]), int(1.2 * R))
        self.assertEqual(self.app.ui_manager.live, ["Shown while speaking."])
        self.assertEqual(len(api.calls), 1)
        self.assertNotIn("Shown while speaking", " ".join(self.debug_log))
        self.assertNotIn("gsk_x", " ".join(self.debug_log))

    def test_disabled_preview_creates_nothing(self):
        app = self.make_app(dict(DEFAULT_CONFIG, use_groq=True, groq_api_key="gsk_x"))
        app._start_stream_session()
        self.assertIsNone(app._stream_session)
        self.assertIsNone(app.audio_recorder._stream_listener)

    def test_partials_from_stale_session_are_ignored(self):
        app = self.make_app(dict(DEFAULT_CONFIG))
        app.is_active = True
        app._stream_session = object()
        app._on_stream_partial(object(), "stale")
        app.is_active = False
        app._on_stream_partial(app._stream_session, "after stop")
        self.assertEqual(self.app.ui_manager.live, [])


if __name__ == "__main__":
    unittest.main()
