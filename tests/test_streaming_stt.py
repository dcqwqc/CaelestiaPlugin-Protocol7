"""Threaded session tests for the opt-in groq-chunks live preview.

These replace the former OpenAI Realtime session tests while keeping their
intent: capture never blocks, overflow and failures only degrade the
preview, late provider results are ignored after stop/cancel, nothing secret
or spoken is logged. Everything is offline: sockets are blocked for the
module and Groq is a local fake object. Audio is synthesized.
"""
import socket
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

import streaming_stt as st

R = 16000
SECRET = "gsk_test-SECRET-do-not-log"
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


def voiced(seconds, amp=0.1, f0=140.0):
    t = np.arange(int(round(seconds * R))) / R
    tone = sum((0.6 ** k) * np.sin(2 * np.pi * f0 * (k + 1) * t) for k in range(5))
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t)
    return (amp * tone * envelope / np.max(np.abs(tone))).astype(np.float32)


def quiet(seconds):
    rng = np.random.default_rng(0)
    return (rng.standard_normal(int(round(seconds * R))) * 0.0005).astype(np.float32)


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def fast_settings(**overrides):
    # Short windows keep threaded tests quick; production values are tested
    # in test_groq_chunks.WindowPlannerTests.
    values = dict(interval_seconds=0.5, overlap_seconds=0.2, first_preview_seconds=0.5,
                  min_window_speech_seconds=0.3, min_new_speech_seconds=0.2,
                  request_timeout=1.0, idle_timeout=30.0)
    values.update(overrides)
    return st.PreviewSettings(**values)


class StatusError(Exception):
    def __init__(self, status, headers=None):
        super().__init__("upstream echoed: private words")
        self.status_code = status
        self.response = types.SimpleNamespace(status_code=status, headers=headers or {})


class APITimeoutError(Exception):
    pass


class FakeTranscriptions:
    def __init__(self, replies=None, error=None, gate=None):
        self.replies = list(replies or [])
        self.error = error
        self.gate = gate
        self.calls = []
        self.threads = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def _begin(self, kwargs):
        with self._lock:
            self.calls.append(kwargs)
            self.threads.append(threading.current_thread())
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def _response(self):
        try:
            if self.gate is not None:
                self.gate.wait(5.0)
            if self.error is not None:
                raise self.error
            text = self.replies.pop(0) if self.replies else "private words"
            return {"text": text}
        finally:
            with self._lock:
                self.active -= 1

    def create(self, **kwargs):
        self._begin(kwargs)
        return self._response()

    def start(self, **kwargs):
        from concurrent.futures import Future
        future = Future()
        # The fake HTTP send is recorded before returning, under the scheduler
        # lock. Only response completion runs asynchronously.
        self._begin(kwargs)
        def complete():
            try:
                future.set_result(self._response())
            except BaseException as error:
                future.set_exception(error)
        threading.Thread(target=complete, daemon=True).start()
        return future


class FakeClient:
    def start_preview_transport(self, **kwargs):
        return self.audio.transcriptions.start(**kwargs)

    def __init__(self, transcriptions):
        self.audio = types.SimpleNamespace(transcriptions=transcriptions)
        self.base_url = "https://api.groq.com/openai/v1"
        self.api_key = SECRET


class SessionTestCase(unittest.TestCase):
    def make(self, api=None, *, settings=None, budget=None, provider=None, model="whisper-large-v3-turbo"):
        api = api if api is not None else FakeTranscriptions()
        client = FakeClient(api)
        partials, logs = [], []
        budget = budget or st.PreviewBudget()
        session = st.GroqChunkSession(
            settings or fast_settings(), input_rate=R,
            client_provider=provider or (lambda: (client, model)),
            budget=budget, on_partial=partials.append, log=logs.append,
        )
        self.addCleanup(session.join, 1.0)
        self.addCleanup(session.cancel)
        return session, api, partials, logs, budget

    @staticmethod
    def push(session, audio, chunk=512):
        for i in range(0, len(audio), chunk):
            session.push_audio(audio[i:i + chunk])


class PreviewFlowTests(SessionTestCase):
    def test_preview_is_published_while_recording(self):
        s, api, partials, logs, _ = self.make(FakeTranscriptions(["Hello there."]))
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: partials == ["Hello there."]))
        self.assertEqual(s.preview_text, "Hello there.")
        call = api.calls[0]
        self.assertEqual(call["model"], "whisper-large-v3-turbo")
        self.assertEqual(call["response_format"], "verbose_json")
        self.assertEqual(call["timeout"], 1.0)
        self.assertTrue(call["file"][1].startswith(b"RIFF"))
        self.assertEqual(logs, [])  # nothing noteworthy happened

    def test_overlapping_windows_merge_without_duplicates(self):
        s, api, partials, _, _ = self.make(FakeTranscriptions([
            "We should meet on Monday at.", "Monday at noon to plan it."]))
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: len(partials) == 1))
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: len(partials) == 2))
        self.assertEqual(partials[-1], "We should meet on Monday at noon to plan it.")

    def test_language_hint_and_selected_model_are_forwarded(self):
        s, api, partials, _, _ = self.make(settings=fast_settings(language="de"), model="whisper-large-v3")
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: api.calls))
        self.assertEqual(api.calls[0]["language"], "de")
        self.assertEqual(api.calls[0]["model"], "whisper-large-v3")

    def test_silence_never_reaches_groq(self):
        s, api, partials, _, _ = self.make()
        s.start()
        self.push(s, quiet(4.0))
        self.assertTrue(wait_until(lambda: s.stats()["silence"] >= 1))
        time.sleep(0.1)
        self.assertEqual(api.calls, [])
        self.assertEqual(partials, [])

    def test_hallucinated_output_is_not_published(self):
        s, api, partials, _, _ = self.make(FakeTranscriptions(["Thank you.", "Real words."]))
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: s.stats()["filtered"] == 1))
        self.assertEqual(partials, [])
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: partials == ["Real words."]))


class CaptureThreadTests(SessionTestCase):
    def test_requests_never_run_on_the_capture_thread(self):
        s, api, partials, _, _ = self.make()
        s.start()
        pusher = threading.Thread(target=self.push, args=(s, voiced(1.2)), name="fake-portaudio")
        pusher.start()
        pusher.join()
        self.assertTrue(wait_until(lambda: len(api.calls) >= 1))
        for thread in api.threads:
            self.assertIsNot(thread, pusher)
            self.assertEqual(thread.name, "protocol7-preview-request")

    def test_push_audio_never_blocks_while_request_hangs(self):
        gate = threading.Event()
        s, api, partials, _, budget = self.make(FakeTranscriptions(gate=gate))
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: api.calls))
        chunk = voiced(0.032)
        started = time.monotonic()
        for _ in range(3000):
            s.push_audio(chunk)
        self.assertLess(time.monotonic() - started, 0.25)
        started = time.monotonic()
        s.stop()
        self.assertLess(time.monotonic() - started, 0.1)  # in-flight request not awaited
        gate.set()
        self.assertTrue(wait_until(lambda: not budget.in_flight()))
        self.assertEqual(partials, [])  # late result discarded
        self.assertEqual(api.max_active, 1)

    def test_queue_overflow_only_degrades_preview(self):
        s, api, partials, _, _ = self.make(settings=fast_settings(max_queue_chunks=4))
        for _ in range(20):  # worker not started yet: queue saturates
            s.push_audio(voiced(0.032))
        s.start()
        self.assertTrue(wait_until(lambda: s._queue.empty()))
        s.push_audio(voiced(0.032))
        self.assertTrue(wait_until(lambda: s.stats()["overflows"] == 1))
        self.push(s, voiced(0.6), chunk=256)
        self.assertTrue(s.active)


class FailureTests(SessionTestCase):
    def test_timeouts_disable_session_after_two_failures(self):
        clock_budget = st.PreviewBudget()
        api = FakeTranscriptions(error=APITimeoutError())
        s, api, partials, logs, budget = self.make(api, budget=clock_budget)
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: len(api.calls) == 1))
        self.assertTrue(wait_until(lambda: s.stats()["failed"] == 1))
        budget._cooldown_until = 0.0  # skip the 10 s global cooldown in this test
        time.sleep(0.6)
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: s.stats()["disabled"] == "repeated failures"))
        self.push(s, voiced(2.0))
        time.sleep(0.2)
        self.assertEqual(len(api.calls), 2)
        self.assertEqual(partials, [])
        self.assertIn("preview request failed (timeout)", logs)

    def test_rate_limit_disables_session_and_cools_down_all_previews(self):
        api = FakeTranscriptions(error=StatusError(429, {"retry-after": "30"}))
        s, api, partials, logs, budget = self.make(api)
        s.start()
        self.push(s, voiced(1.5))
        self.assertTrue(wait_until(lambda: s.stats()["disabled"] == "rate limited"))
        self.assertEqual(len(api.calls), 1)
        self.assertGreaterEqual(budget.cooldown_remaining(), 59.0)
        other, other_api, _, _, _ = self.make(budget=budget)
        other.start()
        self.push(other, voiced(1.5))
        self.assertTrue(wait_until(lambda: other.stats()["denied"] >= 1))
        self.assertEqual(other_api.calls, [])
        self.assertNotIn("private words", " ".join(logs))

    def test_auth_failure_disables_previews_process_wide(self):
        s, api, _, _, budget = self.make(FakeTranscriptions(error=StatusError(401)))
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: budget.disabled_reason() == "authentication failed"))

    def test_client_not_ready_is_free_and_retried(self):
        ready = threading.Event()
        api = FakeTranscriptions(["Now ready."])
        client = FakeClient(api)
        s, _, partials, _, budget = self.make(
            provider=lambda: (client, "whisper-large-v3-turbo") if ready.is_set() else None)
        s.start()
        self.push(s, voiced(0.6))
        time.sleep(0.15)
        self.assertEqual(api.calls, [])
        self.assertEqual(budget.usage()["requests_minute"], 0)
        ready.set()
        time.sleep(0.3)
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: partials == ["Now ready."]))

    def test_provider_exception_never_breaks_session_or_costs_budget(self):
        calls = []

        def broken():
            calls.append(1)
            raise RuntimeError("no client")
        s, api, partials, _, budget = self.make(provider=broken)
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: calls))
        self.assertTrue(wait_until(lambda: not budget.in_flight()))
        self.assertTrue(s.active)
        self.assertEqual(s.stats()["failed"], 0)
        self.assertEqual(budget.usage()["requests_minute"], 0)
        self.assertEqual(budget.cooldown_remaining(), 0.0)


class LifecycleTests(SessionTestCase):
    def test_stop_before_send_never_calls_groq(self):
        entered, release = threading.Event(), threading.Event()
        api = FakeTranscriptions()
        client = FakeClient(api)

        def slow_provider():
            entered.set()
            release.wait(5.0)
            return client, "whisper-large-v3-turbo"
        s, _, partials, _, budget = self.make(provider=slow_provider)
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(entered.wait(2.0))
        s.stop()
        release.set()
        self.assertTrue(wait_until(lambda: not budget.in_flight()))
        self.assertEqual(api.calls, [])
        self.assertEqual(partials, [])

    def test_cancel_suppresses_late_partials(self):
        gate = threading.Event()
        s, api, partials, _, _ = self.make(FakeTranscriptions(["late"], gate=gate))
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: api.calls))
        s.cancel()
        gate.set()
        s.join(1.0)
        self.assertEqual(partials, [])
        self.assertEqual(s.stop_reason, "cancelled")
        s.push_audio(voiced(0.1))  # harmless after cancel
        self.assertFalse(s.active)

    def test_idle_session_stops_itself_without_calls(self):
        s, api, _, _, _ = self.make(settings=fast_settings(idle_timeout=0.2))
        s.start()
        self.assertTrue(wait_until(lambda: not s.active))
        self.assertEqual(s.stop_reason, "idle")
        self.push(s, voiced(1.0))
        time.sleep(0.1)
        self.assertEqual(api.calls, [])

    def test_stopped_session_never_starts(self):
        s, api, _, _, _ = self.make()
        s.stop()
        s.start()
        self.push(s, voiced(1.0))
        time.sleep(0.1)
        self.assertIsNone(s._thread)
        self.assertEqual(api.calls, [])

    def test_stop_is_idempotent(self):
        s, _, _, logs, _ = self.make()
        s.start()
        s.stop()
        s.stop()
        s.cancel()
        self.assertEqual(s.stop_reason, "stopped")
        self.assertEqual(sum("preview stopped" in line for line in logs), 1)

    def test_request_cap_per_recording(self):
        s, api, _, _, _ = self.make(settings=fast_settings(max_requests=2))
        budget = s._core.budget
        s.start()
        for _ in range(4):
            self.push(s, voiced(0.6))
            wait_until(lambda: api.calls and not budget.in_flight(), 1.0)
            time.sleep(0.6)  # past the planner's per-window cadence
        self.assertTrue(wait_until(lambda: s.stats()["disabled"] == "request limit"))
        self.assertEqual(len(api.calls), 2)


class PriorityTests(SessionTestCase):
    def test_final_in_flight_defers_previews(self):
        s, api, partials, _, budget = self.make()
        with budget.final_priority(10.0):
            s.start()
            self.push(s, voiced(0.6))
            time.sleep(0.2)
            self.assertEqual(api.calls, [])
        self.assertTrue(wait_until(lambda: api.calls))

    def test_single_flight_across_sessions(self):
        gate = threading.Event()
        shared = st.PreviewBudget()
        a, api_a, _, _, _ = self.make(FakeTranscriptions(gate=gate), budget=shared)
        b, api_b, _, _, _ = self.make(FakeTranscriptions(gate=gate), budget=shared)
        a.start()
        b.start()
        self.push(a, voiced(0.6))
        self.assertTrue(wait_until(lambda: api_a.calls))
        self.push(b, voiced(0.6))
        self.assertTrue(wait_until(lambda: b.stats()["denied"] >= 1))
        self.assertEqual(api_b.calls, [])
        gate.set()


class PrivacyTests(SessionTestCase):
    def test_secret_and_transcript_never_in_logs_or_reprs(self):
        s, api, partials, logs, _ = self.make(FakeTranscriptions(["private words"]))
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: partials))
        s.stop()
        visible = " ".join(logs) + repr(s) + repr(s.settings) + str(s.stats())
        self.assertNotIn(SECRET, visible)
        self.assertNotIn("private words", visible)
        window = st.Window(1, 0, 10, R, np.zeros(10, dtype=np.float32))
        self.assertNotIn("array", repr(window))

    def test_error_text_from_provider_is_never_logged(self):
        s, api, _, logs, _ = self.make(FakeTranscriptions(error=StatusError(500)))
        s.start()
        self.push(s, voiced(0.6))
        self.assertTrue(wait_until(lambda: s.stats()["failed"] == 1))
        self.assertNotIn("private words", " ".join(logs))
        self.assertIn("preview request failed (error)", logs)


if __name__ == "__main__":
    unittest.main()
