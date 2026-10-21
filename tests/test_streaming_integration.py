"""Offline integration: recorder -> live session -> ghost job -> insertion.

No test opens a network connection or touches user state: sockets are
blocked, history and ghost-job persistence are redirected, and every live
session uses an in-memory fake transport.
"""
import base64
import json
import queue
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


class FakeWhisper:
    def __init__(self, text="batch text"):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.text


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
    def __init__(self, result=None, error=None, gate=None):
        self.result = result
        self.error = error
        self.gate = gate
        self.finish_calls = 0
        self.cancelled = threading.Event()

    def finish(self, timeout=None):
        self.finish_calls += 1
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.cancelled.is_set():
            return st.StreamingResult(False, "", "cancelled")
        if self.error is not None:
            raise self.error
        return self.result

    def cancel(self):
        self.cancelled.set()
        if self.gate is not None:
            self.gate.set()


class FakeTransport:
    def __init__(self, on_send=None):
        self.sent = []
        self.inbox = queue.Queue()
        self.closed = threading.Event()
        self.on_send = on_send

    def send(self, text):
        if self.closed.is_set():
            raise st.TransportClosed("closed")
        message = json.loads(text)
        self.sent.append(message)
        if self.on_send is not None:
            self.on_send(self, message)

    def recv(self, timeout):
        if self.closed.is_set():
            raise st.TransportClosed("closed")
        try:
            return json.dumps(self.inbox.get(timeout=timeout))
        except queue.Empty:
            return None

    def close(self):
        self.closed.set()

    def appended_samples(self):
        data = b"".join(base64.b64decode(m["audio"]) for m in self.sent
                        if m["type"] == "input_audio_buffer.append")
        return len(data) // 2


def make_recorder():
    recorder = audio_module.AudioRecorder(sample_rate=16000)
    recorder.is_recording = True
    recorder.actual_sample_rate = 16000
    recorder.channels = 1
    return recorder


def feed(recorder, n=1600, value=0.1):
    recorder._audio_callback(np.full((n, 1), value, dtype=np.float32), n, None, None)


class GhostTestCase(unittest.TestCase):
    def setUp(self):
        self.history = []
        patcher = mock.patch.object(ghost_jobs, "add_history", self.history.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = FakeApp()

    def make_jobs(self):
        with mock.patch.object(ghost_jobs.Path, "home", return_value=Path(self.tmp.name)):
            jobs = ghost_jobs.GhostJobs(self.app)
        self.addCleanup(jobs.executor.shutdown, wait=False)
        return jobs

    @staticmethod
    def audio():
        return np.zeros(16000, dtype=np.float32)


class GhostStreamingTests(GhostTestCase):
    def test_verified_stream_skips_batch_and_inserts_once(self):
        jobs = self.make_jobs()
        stream = FakeStream(st.StreamingResult(True, "live words", "verified"))
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(self.app.pasted, ["live words"])
        self.assertEqual(self.app.whisper_engine.calls, 0)
        self.assertEqual(self.history, ["live words"])
        self.assertEqual(stream.finish_calls, 1)
        self.assertIsNone(job.stream)

    def test_unverified_stream_falls_back_to_batch_once(self):
        jobs = self.make_jobs()
        stream = FakeStream(st.StreamingResult(False, "", "timeout"))
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(self.app.pasted, ["batch text"])
        self.assertEqual(self.app.whisper_engine.calls, 1)
        self.assertEqual(self.history, ["batch text"])

    def test_stream_exception_falls_back_to_batch(self):
        jobs = self.make_jobs()
        stream = FakeStream(error=RuntimeError("boom"))
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

    def test_cancel_during_finalize_makes_no_batch_call(self):
        jobs = self.make_jobs()
        stream = FakeStream(st.StreamingResult(True, "never", "verified"), gate=threading.Event())
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: stream.finish_calls == 1))
        self.assertTrue(jobs.cancel(job.id)["ok"])
        self.assertTrue(stream.cancelled.is_set())
        time.sleep(0.2)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(self.app.whisper_engine.calls, 0)
        self.assertEqual(self.app.pasted, [])
        self.assertEqual(self.history, [])

    def test_double_control_dismiss_cancels_live_stream(self):
        jobs = self.make_jobs()
        stream = FakeStream(st.StreamingResult(True, "never", "verified"), gate=threading.Event())
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: stream.finish_calls == 1))
        self.assertTrue(jobs.dismiss_visible())
        self.assertTrue(stream.cancelled.is_set())
        time.sleep(0.2)
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(self.app.whisper_engine.calls, 0)
        self.assertEqual(self.app.pasted, [])

    def test_focus_change_keeps_ready_chip_without_insert(self):
        jobs = self.make_jobs()
        stream = FakeStream(st.StreamingResult(True, "live words", "verified"))
        job = jobs.enqueue(self.audio(), FakeTarget(active=False), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "ready"))
        time.sleep(0.1)
        self.assertEqual(self.app.pasted, [])
        self.assertEqual(job.text, "live words")
        self.assertEqual(self.history, ["live words"])

    def test_enter_queued_during_finalize_sends_once(self):
        self.app.enter_interceptor = FakeHook()
        jobs = self.make_jobs()
        gate = threading.Event()
        stream = FakeStream(st.StreamingResult(True, "send me", "verified"), gate=gate)
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "processing" and stream.finish_calls == 1))
        self.assertTrue(jobs.queue_send()["send_queued"])
        gate.set()
        self.assertTrue(wait_until(lambda: job.status == "inserted" and self.app.enters == 1))
        self.assertEqual(self.app.pasted, ["send me"])
        self.assertEqual(self.app.enters, 1)

    def test_retry_after_error_uses_batch_only(self):
        self.app.whisper_engine.text = ""
        jobs = self.make_jobs()
        stream = FakeStream(st.StreamingResult(False, "", "timeout"))
        job = jobs.enqueue(self.audio(), FakeTarget(), stream=stream)
        self.assertTrue(wait_until(lambda: job.status == "error"))
        self.app.whisper_engine.text = "retried"
        self.assertTrue(jobs.retry(job.id)["ok"])
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(stream.finish_calls, 1)
        self.assertEqual(self.app.pasted, ["retried"])


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


class EndToEndTests(GhostTestCase):
    def session(self, transport_or_factory, timeout=3.0):
        partials = []
        factory = transport_or_factory if callable(transport_or_factory) else (lambda _s: transport_or_factory)
        session = st.StreamingSession(
            st.StreamingSettings(api_key="test-key", settle_seconds=0.05, finalize_timeout=timeout),
            input_rate=16000, transport_factory=factory, on_partial=partials.append,
        )
        self.addCleanup(session.cancel)
        return session, partials

    def test_live_final_is_used_and_all_audio_streamed(self):
        state = {"first": True}

        def server(transport, message):
            if message["type"] == "input_audio_buffer.append" and state["first"]:
                state["first"] = False
                transport.inbox.put({"type": st.EVENT_COMMITTED, "item_id": "item_1", "previous_item_id": None})
                transport.inbox.put({"type": st.EVENT_DELTA, "item_id": "item_1", "delta": "streamed"})
            if message["type"] == "input_audio_buffer.commit":
                transport.inbox.put({"type": st.EVENT_COMPLETED, "item_id": "item_1", "transcript": "streamed words"})
                transport.inbox.put({"type": "error", "error": {"code": st.ERROR_COMMIT_EMPTY}})
        transport = FakeTransport(server)
        session, partials = self.session(transport)
        recorder = make_recorder()
        for _ in range(3):
            feed(recorder)
        session.start()
        recorder.attach_stream_listener(session.push_audio)
        for _ in range(7):
            feed(recorder)
        self.assertTrue(wait_until(lambda: partials))
        audio = recorder.stop_recording()
        jobs = self.make_jobs()
        job = jobs.enqueue(audio, FakeTarget(), stream=session)
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(partials[0], "streamed")
        self.assertEqual(self.app.pasted, ["streamed words"])
        self.assertEqual(self.app.whisper_engine.calls, 0)
        self.assertEqual(transport.appended_samples(), len(audio) * 3 // 2)

    def test_connect_failure_uses_batch_once(self):
        def factory(_settings):
            raise st.TransportClosed("offline")
        session, partials = self.session(factory)
        recorder = make_recorder()
        session.start()
        recorder.attach_stream_listener(session.push_audio)
        for _ in range(10):
            feed(recorder)
        audio = recorder.stop_recording()
        jobs = self.make_jobs()
        job = jobs.enqueue(audio, FakeTarget(), stream=session)
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        self.assertEqual(self.app.pasted, ["batch text"])
        self.assertEqual(self.app.whisper_engine.calls, 1)
        self.assertEqual(partials, [])

    def test_late_final_after_timeout_never_double_inserts(self):
        def server(transport, message):
            if message["type"] == "input_audio_buffer.commit":
                transport.inbox.put({"type": st.EVENT_COMMITTED, "item_id": "item_1", "previous_item_id": None})
        transport = FakeTransport(server)
        session, _ = self.session(transport, timeout=0.3)
        recorder = make_recorder()
        session.start()
        recorder.attach_stream_listener(session.push_audio)
        for _ in range(10):
            feed(recorder)
        audio = recorder.stop_recording()
        jobs = self.make_jobs()
        job = jobs.enqueue(audio, FakeTarget(), stream=session)
        self.assertTrue(wait_until(lambda: job.status == "inserted"))
        session._dispatch(json.dumps({"type": st.EVENT_COMPLETED, "item_id": "item_1", "transcript": "late"}))
        time.sleep(0.1)
        self.assertEqual(session.finish().reason, "timeout")
        self.assertEqual(self.app.pasted, ["batch text"])
        self.assertEqual(self.history, ["batch text"])

    def test_default_config_never_creates_session_or_transport(self):
        self.assertFalse(DEFAULT_CONFIG["streaming_stt_enabled"])
        self.assertFalse(DEFAULT_CONFIG["streaming_stt_billing_authorized"])
        self.assertEqual(DEFAULT_CONFIG["streaming_stt_provider"], "")
        with mock.patch.object(st, "default_transport_factory",
                               side_effect=AssertionError("no transport by default")):
            self.assertEqual(st.availability(dict(DEFAULT_CONFIG)), "disabled")
            self.assertIsNone(st.create_session(dict(DEFAULT_CONFIG)))

    def test_enabled_without_billing_authorization_stays_off(self):
        config = dict(DEFAULT_CONFIG, streaming_stt_enabled=True,
                      streaming_stt_provider="openai-realtime", streaming_stt_api_key="k")
        self.assertEqual(st.availability(config), "billing not authorized")
        self.assertIsNone(st.create_session(config))


if __name__ == "__main__":
    unittest.main()
