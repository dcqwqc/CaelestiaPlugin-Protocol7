"""Unit tests for the opt-in live transcription session.

Everything runs offline: sockets are blocked for this module and every
session uses an in-memory fake transport.
"""
import base64
import json
import queue
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

import streaming_stt as st

KEY = "sk-test-SECRET-do-not-log"
DROP = object()
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


def enabled_config(**overrides):
    config = {
        "streaming_stt_enabled": True,
        "streaming_stt_provider": "openai-realtime",
        "streaming_stt_api_key": KEY,
        "streaming_stt_billing_authorized": True,
        "streaming_stt_model": "gpt-live-transcribe",
    }
    config.update(overrides)
    return config


def fast_settings(**overrides):
    values = {"api_key": KEY, "settle_seconds": 0.05, "finalize_timeout": 2.0,
              "reconnect_delay": 0.01}
    values.update(overrides)
    return st.StreamingSettings(**values)


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def committed(item, previous=None):
    return {"type": st.EVENT_COMMITTED, "item_id": item, "previous_item_id": previous}


def delta(item, text):
    return {"type": st.EVENT_DELTA, "item_id": item, "content_index": 0, "delta": text}


def completed(item, text):
    return {"type": st.EVENT_COMPLETED, "item_id": item, "content_index": 0, "transcript": text}


def commit_empty():
    return {"type": "error", "error": {"type": "invalid_request_error",
                                       "code": st.ERROR_COMMIT_EMPTY,
                                       "message": "buffer empty"}}


def chunk(n=1600, value=0.1):
    return np.full(n, value, dtype=np.float32)


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
            item = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is DROP:
            self.closed.set()
            raise st.TransportClosed("dropped")
        return json.dumps(item)

    def emit(self, event):
        self.inbox.put(event)

    def drop(self):
        self.inbox.put(DROP)

    def close(self):
        self.closed.set()

    def types(self):
        return [m["type"] for m in self.sent]

    def appended_bytes(self):
        return b"".join(base64.b64decode(m["audio"]) for m in self.sent
                        if m["type"] == "input_audio_buffer.append")


def respond_on_commit(*events):
    def hook(transport, message):
        if message["type"] == "input_audio_buffer.commit":
            for event in events:
                transport.emit(event)
    return hook


class SessionTests(unittest.TestCase):
    def make(self, transport, factory=None, settings=None):
        partials, logs = [], []
        session = st.StreamingSession(
            settings or fast_settings(), input_rate=16000,
            transport_factory=factory or (lambda _settings: transport),
            on_partial=partials.append, log=logs.append,
        )
        self.addCleanup(session.cancel)
        return session, partials, logs

    def test_session_update_requests_transcription_session(self):
        t = FakeTransport()
        s, _, _ = self.make(t)
        s.start()
        self.assertTrue(wait_until(lambda: t.sent))
        message = t.sent[0]
        self.assertEqual(message["type"], "session.update")
        session = message["session"]
        self.assertEqual(session["type"], "transcription")
        audio_in = session["audio"]["input"]
        self.assertEqual(audio_in["transcription"]["model"], "gpt-live-transcribe")
        self.assertEqual(audio_in["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertEqual(audio_in["turn_detection"]["type"], "server_vad")
        self.assertNotIn(KEY, json.dumps(message))

    def test_audio_resampled_to_24k_pcm16_without_loss(self):
        t = FakeTransport(respond_on_commit(committed("i1"), completed("i1", "hello there")))
        s, _, _ = self.make(t)
        # Chunks pushed before the connection exists are queued, not lost.
        for _ in range(3):
            s.push_audio(chunk(1600))
        s.start()
        for _ in range(6):
            s.push_audio(chunk(1601))
        result = s.finish()
        self.assertTrue(result.verified, result.reason)
        self.assertEqual(result.text, "hello there")
        total = 3 * 1600 + 6 * 1601
        wire = np.frombuffer(t.appended_bytes(), dtype="<i2")
        self.assertEqual(len(wire), round(total * 24000 / 16000))
        self.assertTrue(np.all(np.abs(wire.astype(int) - 3277) <= 1))
        self.assertEqual(t.types().count("input_audio_buffer.commit"), 1)

    def test_partials_follow_item_order_and_finals_replace_deltas(self):
        t = FakeTransport()
        s, partials, _ = self.make(t)
        s.start()
        # Item b names a as its predecessor before a's own commit arrives.
        t.emit(committed("b", "a"))
        t.emit(delta("b", "two"))
        t.emit(committed("a", None))
        t.emit(delta("a", "one"))
        self.assertTrue(wait_until(lambda: partials and partials[-1] == "one two"))
        t.emit(completed("a", "One."))
        self.assertTrue(wait_until(lambda: partials[-1] == "One. two"))
        t.emit(delta("a", " ignored late delta"))
        t.emit(completed("b", "Two."))
        self.assertTrue(wait_until(lambda: partials[-1] == "One. Two."))

    def test_commit_empty_after_server_vad_is_verified(self):
        t = FakeTransport(respond_on_commit(commit_empty()))
        s, _, _ = self.make(t)
        s.start()
        s.push_audio(chunk())
        t.emit(committed("i1"))
        t.emit(completed("i1", "said it"))
        result = s.finish()
        self.assertTrue(result.verified, result.reason)
        self.assertEqual(result.text, "said it")

    def test_final_waits_for_pending_speech_burst(self):
        t = FakeTransport(respond_on_commit(commit_empty()))
        s, _, _ = self.make(t)
        s.start()
        s.push_audio(chunk())
        t.emit(committed("i1"))
        t.emit(completed("i1", "first burst"))
        t.emit(committed("i2", "i1"))
        timer = threading.Timer(0.15, t.emit, args=(completed("i2", "second burst"),))
        timer.start()
        self.addCleanup(timer.cancel)
        result = s.finish()
        self.assertTrue(result.verified, result.reason)
        self.assertEqual(result.text, "first burst second burst")

    def test_timeout_when_final_never_arrives(self):
        t = FakeTransport(respond_on_commit(committed("i1")))
        s, _, _ = self.make(t)
        s.start()
        s.push_audio(chunk())
        result = s.finish(0.3)
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "timeout")
        self.assertEqual(result.text, "")

    def test_late_result_after_finish_is_ignored(self):
        t = FakeTransport(respond_on_commit(committed("i1")))
        s, partials, _ = self.make(t)
        s.start()
        s.push_audio(chunk())
        first = s.finish(0.2)
        count = len(partials)
        s._dispatch(json.dumps(completed("i1", "too late")))
        self.assertEqual(len(partials), count)
        self.assertIs(s.finish(), first)
        self.assertFalse(first.verified)

    def test_failed_item_is_not_verified(self):
        failed = {"type": st.EVENT_FAILED, "item_id": "i1",
                  "error": {"code": "x", "message": "spoken words"}}
        t = FakeTransport(respond_on_commit(committed("i1"), failed))
        s, _, _ = self.make(t)
        s.start()
        s.push_audio(chunk())
        result = s.finish()
        self.assertFalse(result.verified)
        self.assertIn("failed", result.reason)

    def test_provider_error_keeps_only_code(self):
        t = FakeTransport()
        s, _, logs = self.make(t)
        s.start()
        s.push_audio(chunk())
        t.emit({"type": "error", "error": {"code": "server_error", "message": "echo private words"}})
        self.assertTrue(wait_until(lambda: any("server_error" in line for line in logs)))
        result = s.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "provider error (server_error)")
        self.assertNotIn("private words", " ".join(logs))

    def test_connect_failure_fails_safe(self):
        def factory(_settings):
            raise st.TransportClosed("connect failed")
        s, _, _ = self.make(None, factory=factory)
        s.start()
        s.push_audio(chunk())
        result = s.finish()
        self.assertFalse(result.verified)
        self.assertTrue(result.reason.startswith("connect failed"))
        s.push_audio(chunk())  # harmless after failure

    def test_missing_websocket_dependency_fails_safe(self):
        with mock.patch.dict(sys.modules, {"websocket": None}):
            with self.assertRaises(st.StreamingUnavailable):
                st.WebsocketTransport(fast_settings())
            session = st.StreamingSession(fast_settings())
            session.start()
            session.push_audio(chunk())
            result = session.finish()
        self.assertFalse(result.verified)
        self.assertIn("StreamingUnavailable", result.reason)

    def test_cancel_suppresses_late_partials(self):
        t = FakeTransport()
        s, partials, _ = self.make(t)
        s.start()
        t.emit(committed("i1"))
        t.emit(delta("i1", "hello"))
        self.assertTrue(wait_until(lambda: partials))
        s.cancel()
        count = len(partials)
        t.emit(delta("i1", " again"))
        time.sleep(0.1)
        self.assertEqual(len(partials), count)
        self.assertTrue(t.closed.is_set())
        result = s.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "cancelled")

    def test_push_audio_never_blocks_and_overflow_forces_fallback(self):
        gate = threading.Event()
        t = FakeTransport()

        def factory(_settings):
            gate.wait(2.0)
            return t
        s, _, _ = self.make(t, factory=factory, settings=fast_settings(max_queue_chunks=2))
        s.start()
        started = time.monotonic()
        for _ in range(50):
            s.push_audio(chunk())
        self.assertLess(time.monotonic() - started, 0.2)
        gate.set()
        result = s.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "audio dropped")

    def test_slow_transport_does_not_block_capture(self):
        gate = threading.Event()

        def slow(_transport, message):
            if message["type"] == "input_audio_buffer.append":
                gate.wait(2.0)
        t = FakeTransport(slow)
        s, _, _ = self.make(t)
        s.start()
        started = time.monotonic()
        for _ in range(100):
            s.push_audio(chunk(160))
        self.assertLess(time.monotonic() - started, 0.2)
        gate.set()
        s.cancel()

    def test_reconnect_keeps_preview_but_forces_batch_final(self):
        first = FakeTransport()
        second = FakeTransport(respond_on_commit(committed("x2", "x1"), completed("x2", "")))
        transports = [first, second]
        s, partials, logs = self.make(None, factory=lambda _settings: transports.pop(0))
        s.start()
        s.push_audio(chunk())
        first.emit(committed("a1"))
        first.emit(completed("a1", "first part"))
        first.emit(committed("a2", "a1"))
        first.emit(delta("a2", "lost"))
        first.drop()
        self.assertTrue(wait_until(lambda: second.sent))
        self.assertEqual(second.sent[0]["type"], "session.update")
        second.emit(committed("x1", None))
        second.emit(delta("x1", "second part"))
        self.assertTrue(wait_until(lambda: partials and partials[-1] == "first part second part"))
        s.push_audio(chunk())
        result = s.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "connection interrupted")
        self.assertIn("reconnecting", logs)

    def test_reconnect_limit_fails_safe(self):
        first = FakeTransport()
        calls = []

        def factory(_settings):
            calls.append(1)
            if len(calls) == 1:
                return first
            raise st.TransportClosed("refused")
        s, _, _ = self.make(None, factory=factory)
        s.start()
        s.push_audio(chunk())
        first.drop()
        self.assertTrue(wait_until(lambda: len(calls) == 2))
        result = s.finish()
        self.assertFalse(result.verified)
        self.assertEqual(len(calls), 2)

    def test_no_audio_is_not_verified_and_sends_no_commit(self):
        t = FakeTransport()
        s, _, _ = self.make(t)
        s.start()
        result = s.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, "empty transcript")
        self.assertNotIn("input_audio_buffer.commit", t.types())

    def test_finish_is_idempotent(self):
        t = FakeTransport(respond_on_commit(committed("i1"), completed("i1", "once")))
        s, _, _ = self.make(t)
        s.start()
        s.push_audio(chunk())
        self.assertIs(s.finish(), s.finish())
        self.assertEqual(t.types().count("input_audio_buffer.commit"), 1)

    def test_secret_and_transcript_never_in_logs_or_reprs(self):
        t = FakeTransport(respond_on_commit(committed("i1"), completed("i1", "private words")))
        s, _, logs = self.make(t)
        s.start()
        s.push_audio(chunk())
        result = s.finish()
        self.assertTrue(result.verified)
        visible = " ".join(logs) + repr(s) + repr(s.settings) + str(s.settings) + repr(result)
        self.assertNotIn(KEY, visible)
        self.assertNotIn("private words", visible)


class GatingTests(unittest.TestCase):
    def test_streaming_is_opt_in(self):
        present = lambda _name: object()
        self.assertEqual(st.availability({}, find_spec=present), "disabled")
        self.assertIsNone(st.availability(enabled_config(), find_spec=present))
        self.assertEqual(st.availability(enabled_config(streaming_stt_enabled="yes"), find_spec=present), "disabled")
        self.assertEqual(st.availability(enabled_config(streaming_stt_provider=""), find_spec=present), "no provider configured")
        self.assertEqual(st.availability(enabled_config(streaming_stt_provider="groq"), find_spec=present), "unsupported provider")
        self.assertEqual(st.availability(enabled_config(streaming_stt_api_key="  "), find_spec=present), "no API key configured")
        self.assertEqual(st.availability(enabled_config(streaming_stt_billing_authorized=False), find_spec=present), "billing not authorized")

    def test_missing_dependency_disables_streaming(self):
        reason = st.availability(enabled_config(), find_spec=lambda _name: None)
        self.assertIn("websocket", reason)
        self.assertIsNone(st.create_session(enabled_config(), find_spec=lambda _name: None))

    def test_create_session_never_builds_transport_when_disabled(self):
        factory = mock.Mock(side_effect=AssertionError("transport must not be built"))
        self.assertIsNone(st.create_session({}, transport_factory=factory))
        self.assertIsNone(st.create_session(
            enabled_config(streaming_stt_billing_authorized=False), transport_factory=factory))
        factory.assert_not_called()

    def test_settings_from_config(self):
        settings = st.settings_from_config(enabled_config(
            streaming_stt_api_key=f"  {KEY}  ", auto_detect_language=False, language="de",
            streaming_stt_finalize_timeout_seconds=999,
        ))
        self.assertEqual(settings.api_key, KEY)
        self.assertEqual(settings.language, "de")
        self.assertEqual(settings.model, "gpt-live-transcribe")
        self.assertEqual(settings.finalize_timeout, 30.0)
        self.assertEqual(st.settings_from_config(enabled_config()).language, "")


class TranscriptStateTests(unittest.TestCase):
    def test_items_without_previous_keep_arrival_order(self):
        state = st.TranscriptState()
        state.delta("x", "alpha")
        state.delta("y", "beta")
        self.assertEqual(state.text(), "alpha beta")

    def test_chain_committed_in_reverse(self):
        state = st.TranscriptState()
        state.commit("c", "b", True)
        state.commit("b", "a", True)
        state.commit("a", None, True)
        for item, word in (("a", "1"), ("b", "2"), ("c", "3")):
            state.complete(item, word)
        self.assertEqual(state.text(), "1 2 3")
        self.assertEqual(state.pending(), 0)


if __name__ == "__main__":
    unittest.main()
