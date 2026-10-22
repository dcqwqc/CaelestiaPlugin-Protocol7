"""Deterministic offline tests for the groq-chunks live preview.

Covers voice activity, window boundaries/overlap, budget and rate limits,
reconciliation, response filtering, provider gating (no paid route) and a
virtual-time synthetic benchmark. Nothing here contacts a network: sockets
are blocked for the module and Groq is always a local fake object. No real
voice recordings are used; all audio is synthesized from seeded generators.

Run the benchmark report directly with:
    python tests/test_groq_chunks.py --benchmark
"""
import io
import json
import random
import re
import socket
import sys
import time
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

import streaming_stt as st

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


# --------------------------------------------------------------------------
# Synthetic audio (no recordings)


def voiced(seconds, amp=0.1, f0=140.0, rate=R):
    """Harmonic, syllable-modulated tone: speech-like energy and ZCR."""
    t = np.arange(int(round(seconds * rate))) / rate
    tone = sum((0.6 ** k) * np.sin(2 * np.pi * f0 * (k + 1) * t) for k in range(5))
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t)
    return (amp * tone * envelope / np.max(np.abs(tone))).astype(np.float32)


def word_burst(seconds, amp, f0, rate=R):
    t = np.arange(int(round(seconds * rate))) / rate
    tone = sum((0.55 ** k) * np.sin(2 * np.pi * f0 * (k + 1) * t) for k in range(5))
    envelope = np.sin(np.pi * np.clip(t / max(seconds, 1e-6), 0, 1)) ** 0.5
    return (amp * tone * envelope / np.max(np.abs(tone))).astype(np.float32)


def silence(seconds, rate=R, noise=0.0005, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(round(seconds * rate))) * noise).astype(np.float32)


def clicks(seconds, period, rate=R, seed=1):
    rng = np.random.default_rng(seed)
    out = np.zeros(int(round(seconds * rate)), dtype=np.float32)
    step = int(period * rate)
    for k in range(0, out.size - 400, step):
        out[k:k + 320] = rng.standard_normal(320) * 0.3
    return out


def detected_speech(audio, rate=R, chunk=512):
    detector = st.SpeechDetector(rate)
    for i in range(0, len(audio), chunk):
        detector.process(audio[i:i + chunk])
    return detector.speech_samples(0, len(audio)) / rate


def feed_planner(planner, audio, chunk=480):
    for i in range(0, len(audio), chunk):
        planner.add(audio[i:i + chunk])


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


# --------------------------------------------------------------------------
# Voice activity


class SpeechDetectorTests(unittest.TestCase):
    def test_silence_and_low_noise_are_not_speech(self):
        self.assertEqual(detected_speech(np.zeros(3 * R, dtype=np.float32)), 0.0)
        self.assertEqual(detected_speech(silence(3, noise=0.001)), 0.0)

    def test_loud_broadband_noise_is_not_speech(self):
        noise = (np.random.default_rng(3).standard_normal(3 * R) * 0.05).astype(np.float32)
        self.assertEqual(detected_speech(noise), 0.0)

    def test_key_taps_and_clicks_are_not_speech(self):
        for period in (0.08, 0.125, 0.25):
            self.assertEqual(detected_speech(clicks(3, period)), 0.0, period)

    def test_short_blip_is_rejected(self):
        audio = np.concatenate([silence(0.5), voiced(0.15), silence(1.0)])
        self.assertEqual(detected_speech(audio), 0.0)

    def test_voiced_speech_is_detected_with_lead_in(self):
        audio = np.concatenate([silence(0.5), voiced(2.0, amp=0.1)])
        self.assertGreater(detected_speech(audio), 1.8)
        audio = np.concatenate([silence(0.5), voiced(2.0, amp=0.03)])
        self.assertGreater(detected_speech(audio), 1.8)

    def test_constant_hum_adapts_but_speech_over_it_counts(self):
        t = np.arange(6 * R) / R
        hum = (0.005 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
        self.assertEqual(detected_speech(hum), 0.0)
        mixed = hum.copy()
        mixed[3 * R:5 * R] += voiced(2.0, amp=0.15)
        self.assertGreater(detected_speech(mixed), 1.8)

    def test_chunking_does_not_change_result(self):
        audio = np.concatenate([silence(0.5), voiced(1.3), silence(0.7), voiced(0.9)])
        results = {round(detected_speech(audio, chunk=n), 3) for n in (37, 160, 512, 4096)}
        self.assertEqual(len(results), 1, results)


# --------------------------------------------------------------------------
# Window planning


class WindowPlannerTests(unittest.TestCase):
    def planner(self, **overrides):
        return st.WindowPlanner(st.PreviewSettings(**overrides), R)

    def test_first_window_after_four_seconds_then_six_second_windows_overlap_one(self):
        p = self.planner()
        feed_planner(p, voiced(3.9))
        self.assertEqual(p.propose(), (None, "waiting"))
        feed_planner(p, voiced(0.1))
        first, reason = p.propose()
        self.assertIsNone(reason)
        self.assertEqual((first.start, first.end), (0, 4 * R))
        self.assertFalse(first.overlap)
        self.assertFalse(first.gap)
        p.commit(first)
        feed_planner(p, voiced(4.9))
        self.assertEqual(p.propose(), (None, "waiting"))
        feed_planner(p, voiced(0.1))
        second, _ = p.propose()
        self.assertEqual((second.start, second.end), (3 * R, 9 * R))
        self.assertEqual(second.duration, 6.0)
        self.assertTrue(second.overlap)
        self.assertFalse(second.gap)
        self.assertEqual(second.audio.size, 6 * R)
        p.commit(second)
        feed_planner(p, voiced(5.0))
        third, _ = p.propose()
        self.assertEqual((third.start, third.end), (8 * R, 14 * R))
        self.assertEqual(second.end - third.start, R)  # exactly one second of overlap

    def test_window_audio_matches_captured_samples(self):
        p = self.planner()
        audio = np.concatenate([silence(0.3), voiced(9.7)])
        feed_planner(p, audio[:4 * R], chunk=333)
        w1, _ = p.propose()
        np.testing.assert_array_equal(w1.audio, audio[:4 * R])
        p.commit(w1)
        feed_planner(p, audio[4 * R:], chunk=333)
        w2, _ = p.propose()
        np.testing.assert_array_equal(w2.audio, audio[w2.start:w2.end])

    def test_silence_is_never_sent_and_not_reconsidered(self):
        p = self.planner()
        feed_planner(p, silence(4.0))
        self.assertEqual(p.propose(), (None, "silence"))
        self.assertEqual(p.propose(), (None, "waiting"))  # same silence not re-judged
        feed_planner(p, silence(5.0))
        self.assertEqual(p.propose(), (None, "silence"))

    def test_no_repeated_window_without_new_voiced_samples(self):
        p = self.planner()
        feed_planner(p, np.concatenate([silence(0.5), voiced(3.5)]))
        w1, _ = p.propose()
        p.commit(w1)
        # Five more seconds of silence: the old speech is still inside the
        # 6 s window, but there is nothing new to transcribe.
        feed_planner(p, silence(5.0))
        self.assertEqual(p.propose(), (None, "silence"))
        feed_planner(p, silence(5.0))
        self.assertEqual(p.propose(), (None, "silence"))

    def test_pause_then_speech_appends_without_gap_marker(self):
        p = self.planner()
        feed_planner(p, voiced(4.0))
        w1, _ = p.propose()
        p.commit(w1)
        feed_planner(p, silence(8.0))
        self.assertEqual(p.propose(), (None, "silence"))
        feed_planner(p, voiced(5.0))
        w2, _ = p.propose()
        self.assertFalse(w2.overlap)
        self.assertFalse(w2.gap)  # skipped audio was silent, nothing was lost

    def test_delayed_window_over_unsent_speech_is_marked_gap(self):
        p = self.planner()
        feed_planner(p, voiced(4.0))
        w1, _ = p.propose()
        p.commit(w1)
        feed_planner(p, voiced(9.0))  # e.g. rate-limited: 9 s pass unsent
        w2, _ = p.propose()
        self.assertEqual(w2.duration, 6.0)
        self.assertFalse(w2.overlap)
        self.assertTrue(w2.gap)

    def test_failed_delivery_marks_next_window_gap(self):
        p = self.planner()
        feed_planner(p, voiced(4.0))
        w1, _ = p.propose()
        p.commit(w1, delivered=False)
        feed_planner(p, voiced(5.0))
        w2, _ = p.propose()
        self.assertTrue(w2.gap)
        self.assertFalse(w2.overlap)

    def test_discontinuity_never_stitches_across_dropped_audio(self):
        p = self.planner()
        feed_planner(p, voiced(4.0))
        w1, _ = p.propose()
        p.commit(w1)
        feed_planner(p, voiced(2.0))
        p.discontinuity(lost_samples=R)
        self.assertEqual(p.buffered_samples(), 0)
        feed_planner(p, voiced(5.0))
        w2, _ = p.propose()
        self.assertGreaterEqual(w2.start, 7 * R)
        self.assertTrue(w2.gap)
        self.assertFalse(w2.overlap)

    def test_memory_is_bounded_for_long_recordings(self):
        p = self.planner()
        speech = voiced(1.0)
        for second in range(600):  # ten minutes
            feed_planner(p, speech, chunk=1600)
            if second % 5 == 4:
                window, _ = p.propose()
                if window is not None:
                    p.commit(window)
        self.assertLessEqual(p.buffered_samples(), p.capacity + 1600)
        self.assertLessEqual(len(p.detector.intervals), 20)
        self.assertEqual(p.total, 600 * R)

    def test_interval_setting_is_clamped(self):
        self.assertEqual(st.settings_from_config({"streaming_stt_preview_interval_seconds": 1}).interval_seconds, 4.0)
        self.assertEqual(st.settings_from_config({"streaming_stt_preview_interval_seconds": 60}).interval_seconds, 6.0)
        self.assertEqual(st.settings_from_config({"streaming_stt_preview_interval_seconds": "x"}).interval_seconds, 5.0)
        self.assertEqual(st.PreviewSettings().window_seconds, 6.0)


# --------------------------------------------------------------------------
# Budget, rate limits, cooldown


class BudgetTests(unittest.TestCase):
    def budget(self, **kwargs):
        clock = FakeClock()
        return st.PreviewBudget(clock=clock, **kwargs), clock

    def grant(self, budget, seconds=6.0, outcome="ok", **kwargs):
        token, reason = budget.try_begin_preview(seconds)
        if token is not None:
            budget.end_preview(token, outcome, **kwargs)
        return reason

    def test_free_tier_constants(self):
        self.assertEqual(st.FREE_TIER_LIMITS, {
            "requests_per_minute": 20, "requests_per_day": 2000,
            "audio_seconds_per_hour": 7200, "audio_seconds_per_day": 28800,
        })
        self.assertEqual(st.MIN_BILLED_SECONDS, 10.0)

    def test_single_flight(self):
        budget, _ = self.budget()
        token, reason = budget.try_begin_preview(6.0)
        self.assertIsNone(reason)
        self.assertEqual(budget.try_begin_preview(6.0), (None, "in flight"))
        budget.end_preview(token, "ok")
        self.assertIsNone(self.grant(budget))

    def test_rpm_cap_leaves_headroom_for_final(self):
        budget, clock = self.budget()
        for _ in range(12):
            self.assertIsNone(self.grant(budget))
            clock.now += 1.0
        self.assertEqual(self.grant(budget), "rpm")
        clock.now += 60.0
        self.assertIsNone(self.grant(budget))
        self.assertLessEqual(budget.rpm_cap + 1, st.FREE_TIER_LIMITS["requests_per_minute"])

    def test_finals_count_and_block_previews_while_in_flight(self):
        budget, clock = self.budget()
        with budget.final_priority(30.0):
            self.assertEqual(self.grant(budget), "final in progress")
        self.assertIsNone(self.grant(budget))
        for _ in range(11):
            budget.record_final(5.0)
        self.assertEqual(self.grant(budget), "rpm")  # 1 final ctx + 11 finals + 1 preview

    def test_hourly_audio_cap_counts_minimum_billing(self):
        budget, clock = self.budget()
        cap = st.FREE_TIER_LIMITS["audio_seconds_per_hour"] * budget.share  # 2880 s
        sent = 0
        while self.grant(budget, seconds=2.0) is None:  # billed as 10 s each
            sent += 1
            clock.now += 6.0
            self.assertLess(sent, 1000)
        self.assertEqual(sent, int(cap // 10))
        self.assertEqual(self.grant(budget, seconds=2.0), "hourly audio")
        self.assertIn("hourly audio", st.PERMANENT_DENIALS)
        clock.now += 3600.0
        self.assertIsNone(self.grant(budget, seconds=2.0))

    def test_daily_caps(self):
        budget, clock = self.budget(limits=dict(st.FREE_TIER_LIMITS, requests_per_day=20))
        for _ in range(8):  # 40 % of 20
            self.assertIsNone(self.grant(budget))
            clock.now += 61.0
        self.assertEqual(self.grant(budget), "daily requests")
        clock.now += 86400.0
        self.assertIsNone(self.grant(budget))

        budget, clock = self.budget(limits=dict(st.FREE_TIER_LIMITS, audio_seconds_per_day=100,
                                                audio_seconds_per_hour=100000))
        for _ in range(4):
            self.assertIsNone(self.grant(budget))
            clock.now += 61.0
        self.assertEqual(self.grant(budget), "daily audio")

    def test_429_uses_retry_after_with_floor_and_escalates_to_disable(self):
        budget, clock = self.budget()
        self.assertIsNone(self.grant(budget, outcome="rate_limited", retry_after=7.0))
        self.assertEqual(self.grant(budget), "cooldown")
        self.assertAlmostEqual(budget.cooldown_remaining(), 60.0)
        clock.now += 61.0
        self.assertIsNone(self.grant(budget, outcome="rate_limited", retry_after=300.0))
        self.assertAlmostEqual(budget.cooldown_remaining(), 600.0)  # 300 s doubled
        clock.now += 601.0
        self.assertIsNone(self.grant(budget, outcome="rate_limited", retry_after=1.0))
        self.assertEqual(budget.disabled_reason(), "rate limited repeatedly")
        clock.now += 7200.0
        self.assertEqual(self.grant(budget), "disabled")

    def test_auth_failure_disables_previews(self):
        budget, _ = self.budget()
        self.grant(budget, outcome="auth")
        self.assertEqual(budget.disabled_reason(), "authentication failed")

    def test_timeout_cools_down_briefly(self):
        budget, clock = self.budget()
        self.grant(budget, outcome="timeout")
        self.assertEqual(self.grant(budget), "cooldown")
        clock.now += 10.5
        self.assertIsNone(self.grant(budget))

    def test_abandoned_request_cannot_block_forever(self):
        budget, clock = self.budget()
        token, _ = budget.try_begin_preview(6.0)
        self.assertEqual(budget.try_begin_preview(6.0), (None, "in flight"))
        clock.now += 31.0
        self.assertIsNotNone(budget.try_begin_preview(6.0)[0])

    def test_not_sent_is_refunded(self):
        budget, _ = self.budget()
        for _ in range(30):
            self.assertIsNone(self.grant(budget, outcome="not_sent"))
        self.assertEqual(budget.usage()["requests_minute"], 0)

    def test_low_remaining_daily_requests_header_yields_to_finals(self):
        budget, clock = self.budget()
        headers = {"X-RateLimit-Limit-Requests": "2000", "X-RateLimit-Remaining-Requests": "40",
                   "X-RateLimit-Reset-Requests": "2m30s"}
        self.grant(budget, headers=headers)
        self.assertEqual(self.grant(budget), "cooldown")
        self.assertAlmostEqual(budget.cooldown_remaining(), 150.0)
        budget2, _ = self.budget()
        self.grant(budget2, headers={"x-ratelimit-remaining-requests": "1500"})
        self.assertIsNone(self.grant(budget2))

    def test_parse_duration(self):
        cases = {"12": 12.0, "7.66s": 7.66, "2m59.56s": 179.56, "1h2m": 3720.0,
                 "250ms": 0.25, "": None, "soon": None, "-3": None, None: None}
        for value, expected in cases.items():
            result = st.parse_duration(value)
            if expected is None:
                self.assertIsNone(result, value)
            else:
                self.assertAlmostEqual(result, expected, msg=value)


# --------------------------------------------------------------------------
# Text reconciliation


class ReconcilerTests(unittest.TestCase):
    def test_overlap_deduplicates_shared_words(self):
        r = st.TextReconciler()
        r.add("We should meet on Monday at")
        r.add("Monday at noon to plan the launch.", overlap=True)
        self.assertEqual(r.text, "We should meet on Monday at noon to plan the launch.")

    def test_newer_window_fixes_boundary_punctuation(self):
        r = st.TextReconciler()
        r.add("I think the report is ready.")
        r.add("is ready, but the charts need work.", overlap=True)
        self.assertEqual(r.text, "I think the report is ready, but the charts need work.")

    def test_cut_off_word_is_completed_not_duplicated(self):
        r = st.TextReconciler()
        r.add("Please send the meet")
        r.add("the meeting notes to everyone.", overlap=True)
        self.assertEqual(r.text, "Please send the meeting notes to everyone.")

    def test_capitalized_window_start_matches(self):
        r = st.TextReconciler()
        r.add("we can start the review")
        r.add("The review takes an hour.", overlap=True)
        self.assertEqual(r.text, "we can start The review takes an hour.")

    def test_no_match_appends_without_boundary_duplicate(self):
        r = st.TextReconciler()
        r.add("first part ends here")
        r.add("here comes something new", overlap=True)
        self.assertEqual(r.text, "first part ends here comes something new")

    def test_lowercase_continuation_removes_edge_full_stop(self):
        r = st.TextReconciler()
        r.add("We walked to the.")
        r.add("park and sat down.", overlap=False)
        self.assertEqual(r.text, "We walked to the park and sat down.")

    def test_gap_is_marked_and_segments_never_merged(self):
        r = st.TextReconciler()
        r.add("alpha beta gamma")
        r.add("gamma delta", gap=True)
        self.assertEqual(r.text, "alpha beta gamma … gamma delta")

    def test_frozen_prefix_never_changes(self):
        r = st.TextReconciler(max_revise_words=4)
        r.add("one two three four five six seven eight")
        frozen = r.words[:r.frozen]
        r.add("seven eight nine ten", overlap=True)
        r.add("Two three four nine ten eleven", overlap=True)  # misaligned junk
        self.assertEqual(r.words[:len(frozen)], frozen)

    def test_empty_result_keeps_existing_text(self):
        r = st.TextReconciler()
        r.add("keep this")
        self.assertFalse(r.add("", overlap=True))
        self.assertEqual(r.text, "keep this")

    def test_text_is_bounded(self):
        r = st.TextReconciler(max_words=50)
        for k in range(40):
            r.add(f"w{k}a w{k}b w{k}c", overlap=False)
        self.assertLessEqual(len(r.words), 50)
        self.assertEqual(r.words[0], r.GAP)
        self.assertTrue(r.text.endswith("w39a w39b w39c"))


# --------------------------------------------------------------------------
# Response filtering and transport


class FilterTests(unittest.TestCase):
    def test_known_hallucinations_and_non_text_are_dropped(self):
        for text in ("Thank you.", " you ", "Thanks for watching!", "...", "1 2 3",
                     "", "the the the the the"):
            self.assertEqual(st.clean_preview_text({"text": text}), "", text)
        self.assertEqual(st.clean_preview_text({"text": " Hello  there. "}), "Hello there.")

    def test_segment_confidence_filters(self):
        result = {"text": "ignored", "segments": [
            {"text": " Real words", "no_speech_prob": 0.1, "avg_logprob": -0.2, "compression_ratio": 1.2},
            {"text": " ghost", "no_speech_prob": 0.9, "avg_logprob": -0.1, "compression_ratio": 1.0},
            {"text": " noise", "no_speech_prob": 0.7, "avg_logprob": -1.2, "compression_ratio": 1.0},
            {"text": " loop loop loop", "no_speech_prob": 0.0, "avg_logprob": -0.1, "compression_ratio": 3.1},
            {"text": " stay.", "no_speech_prob": 0.65, "avg_logprob": -0.3, "compression_ratio": 1.4},
        ]}
        self.assertEqual(st.clean_preview_text(result), "Real words stay.")

    def test_attribute_and_model_extra_results(self):
        obj = types.SimpleNamespace(text="Object text")
        self.assertEqual(st.clean_preview_text(obj), "Object text")
        extra = types.SimpleNamespace(text="x", model_extra={"segments": [{"text": "From extra"}]})
        self.assertEqual(st.clean_preview_text(extra), "From extra")


class Transcriptions:
    def __init__(self, reply=None, error=None):
        self.calls = []
        self.reply = reply if reply is not None else {"text": "hello"}
        self.error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.reply


class FakeGroqClient:
    def __init__(self, transcriptions=None, base_url="https://api.groq.com/openai/v1"):
        self.audio = types.SimpleNamespace(transcriptions=transcriptions or Transcriptions())
        self.base_url = base_url


class StatusError(Exception):
    def __init__(self, status, headers=None):
        super().__init__("provider said: secret words")
        self.status_code = status
        self.response = types.SimpleNamespace(status_code=status, headers=headers or {})


class APITimeoutError(Exception):
    pass


class TransportTests(unittest.TestCase):
    def test_wav_is_16k_mono_pcm16_and_resampled(self):
        for rate, seconds in ((16000, 1.0), (48000, 1.0), (44100, 0.5)):
            data = st.encode_wav(voiced(seconds, rate=rate), rate)
            with wave.open(io.BytesIO(data)) as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getframerate(), 16000)
                self.assertEqual(handle.getnframes(), int(round(seconds * 16000)))

    def test_request_shape_uses_selected_model_timeout_and_language(self):
        api = Transcriptions()
        client = FakeGroqClient(api)
        result, headers = st.groq_transcribe(client, "whisper-large-v3", b"RIFF", language="de", timeout=4.0)
        self.assertEqual(result, {"text": "hello"})
        self.assertIsNone(headers)
        call = api.calls[0]
        self.assertEqual(call["model"], "whisper-large-v3")
        self.assertEqual(call["timeout"], 4.0)
        self.assertEqual(call["language"], "de")
        self.assertEqual(call["response_format"], "verbose_json")
        self.assertEqual(call["temperature"], 0.0)
        self.assertEqual(call["file"], ("preview.wav", b"RIFF"))
        st.groq_transcribe(client, "m", b"x")
        self.assertNotIn("language", api.calls[1])

    def test_raw_response_headers_are_returned(self):
        class Raw:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return types.SimpleNamespace(headers={"x-ratelimit-remaining-requests": "9"},
                                             parse=lambda: {"text": "raw"})
        api = Transcriptions()
        api.with_raw_response = Raw()
        result, headers = st.groq_transcribe(FakeGroqClient(api), "m", b"x")
        self.assertEqual(result, {"text": "raw"})
        self.assertEqual(headers["x-ratelimit-remaining-requests"], "9")
        self.assertEqual(api.calls, [])

    def test_non_groq_or_insecure_endpoint_is_refused(self):
        for url in ("https://api.openai.com/v1", "http://api.groq.com/openai/v1",
                    "https://groq.com.evil.example/v1", "https://example.com"):
            api = Transcriptions()
            with self.assertRaises(st.PreviewUnavailable, msg=url):
                st.groq_transcribe(FakeGroqClient(api, base_url=url), "m", b"x")
            self.assertEqual(api.calls, [])
        self.assertTrue(st.is_groq_endpoint(FakeGroqClient()))

    def test_error_classification_keeps_no_text(self):
        self.assertEqual(st.classify_error(StatusError(429, {"retry-after": "12"})), ("rate_limited", 12.0))
        self.assertEqual(st.classify_error(StatusError(429, {"Retry-After": "x"})), ("rate_limited", None))
        self.assertEqual(st.classify_error(StatusError(401)), ("auth", None))
        self.assertEqual(st.classify_error(StatusError(503)), ("error", None))
        self.assertEqual(st.classify_error(APITimeoutError()), ("timeout", None))
        self.assertEqual(st.classify_error(TimeoutError()), ("timeout", None))
        self.assertEqual(st.classify_error(OSError("x")), ("error", None))


# --------------------------------------------------------------------------
# Provider gating and the absence of any paid route


def groq_config(**overrides):
    config = {"streaming_stt_enabled": True, "streaming_stt_provider": "groq-chunks",
              "use_groq": True, "groq_api_key": "gsk_test_SECRET"}
    config.update(overrides)
    return config


class GatingTests(unittest.TestCase):
    def test_default_config_is_off(self):
        from config import DEFAULT_CONFIG
        self.assertIs(DEFAULT_CONFIG["streaming_stt_enabled"], False)
        self.assertEqual(DEFAULT_CONFIG["streaming_stt_provider"], "groq-chunks")
        self.assertEqual(st.availability(dict(DEFAULT_CONFIG)), "disabled")
        provider = mock.Mock(side_effect=AssertionError("no client by default"))
        self.assertIsNone(st.create_session(dict(DEFAULT_CONFIG), client_provider=provider))
        provider.assert_not_called()

    def test_every_gate(self):
        self.assertIsNone(st.availability(groq_config()))
        self.assertEqual(st.availability(groq_config(streaming_stt_enabled="true")), "disabled")
        self.assertEqual(st.availability(groq_config(streaming_stt_provider="")), "no provider configured")
        self.assertEqual(st.availability(groq_config(use_groq=False)), "Groq dictation not enabled")
        self.assertEqual(st.availability(groq_config(groq_api_key="  ")), "no Groq API key configured")
        self.assertIsNone(st.create_session(groq_config(), client_provider=None))
        self.assertIsInstance(st.create_session(groq_config(), client_provider=lambda: None),
                              st.GroqChunkSession)

    def test_legacy_paid_provider_config_stays_off(self):
        legacy = groq_config(streaming_stt_provider="openai-realtime",
                             streaming_stt_api_key="sk-legacy", streaming_stt_billing_authorized=True,
                             streaming_stt_model="gpt-live-transcribe")
        self.assertEqual(st.availability(legacy), "unsupported provider")
        self.assertIsNone(st.create_session(legacy, client_provider=lambda: None))

    def test_process_disable_reported(self):
        with mock.patch.object(st._DEFAULT_BUDGET, "_disabled", "authentication failed"):
            self.assertEqual(st.availability(groq_config()), "preview disabled (authentication failed)")

    def test_no_paid_or_realtime_route_in_feature_sources(self):
        source = (ROOT / "streaming_stt.py").read_text().lower()
        for banned in ("api.openai.com", "wss://", "realtime", "websocket", "gpt-",
                       "billing_authorized", "streaming_stt_api_key", "import openai"):
            self.assertNotIn(banned, source)
        config_source = (ROOT / "config.py").read_text()
        for banned in ("streaming_stt_api_key", "billing_authorized", "gpt-live", "openai"):
            self.assertNotIn(banned, config_source)
        for qml in ("Settings.qml", "entry/Main.qml"):
            text = (ROOT / qml).read_text().lower()
            for banned in ("openai", "realtime", "billing", "streamingsttapikey",
                           "streamingsttmodel", "streaming_stt_model"):
                self.assertNotIn(banned, text, f"{qml}: {banned}")

    def test_old_config_with_obsolete_keys_still_loads(self):
        import tempfile
        import config as config_module
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"use_groq": True, "streaming_stt_provider": "openai-realtime",
                                        "streaming_stt_api_key": "sk-old", "groq_model": "whisper-large-v3"}))
            with mock.patch.object(config_module, "CONFIG_FILE", str(path)):
                loaded = config_module.load_config()
        self.assertEqual(loaded["groq_model"], "whisper-large-v3")
        self.assertEqual(loaded["streaming_stt_api_key"], "sk-old")  # preserved, ignored
        self.assertIs(loaded["streaming_stt_enabled"], False)
        self.assertEqual(st.availability(loaded), "disabled")


class WhisperEnginePreviewClientTests(unittest.TestCase):
    """Previews reuse the final's Groq client and never create one."""

    def engine(self, config):
        stubs = {}
        for name in ("faster_whisper", "groq", "soundfile"):
            try:
                __import__(name)
            except Exception:
                module = types.ModuleType(name)
                module.WhisperModel = mock.Mock(side_effect=AssertionError("local model"))
                module.Groq = mock.Mock(side_effect=AssertionError("client creation"))
                stubs[name] = module
        with mock.patch.dict(sys.modules, stubs):
            sys.modules.pop("whisper_engine", None)
            import whisper_engine
            engine = whisper_engine.WhisperEngine(config)
        sys.modules.pop("whisper_engine", None)
        return engine

    def test_preview_client_reuses_existing_final_client(self):
        engine = self.engine({"use_groq": True, "groq_api_key": " gsk_x ", "groq_model": "whisper-large-v3"})
        self.assertIsNone(engine.groq_preview_client())  # not set up yet: never created here
        client = object()
        engine.groq_client, engine.current_groq_key = client, "gsk_x"
        self.assertEqual(engine.groq_preview_client(), (client, "whisper-large-v3"))
        engine.current_groq_key = "gsk_other"
        self.assertIsNone(engine.groq_preview_client())

    def test_preview_client_requires_groq_mode(self):
        engine = self.engine({"use_groq": False, "groq_api_key": "gsk_x"})
        engine.groq_client, engine.current_groq_key = object(), "gsk_x"
        self.assertIsNone(engine.groq_preview_client())


# --------------------------------------------------------------------------
# Deterministic virtual-time benchmark

VOCAB = ("project", "meeting", "tomorrow", "morning", "review", "budget", "design",
         "please", "send", "notes", "team", "launch", "update", "schedule", "client",
         "report", "numbers", "quarter", "deadline", "draft", "feedback", "agenda",
         "after", "before", "during", "lunch", "call", "today", "friday", "monday",
         "the", "a", "we", "should", "can", "will", "and", "then", "with", "about",
         "for", "our", "this", "next", "week", "final", "version", "slides", "share")


def make_script(rng, seconds=60.0, long_pause_at=25.0, long_pause=12.0):
    """Ground-truth words with timings; one long silent pause mid-way."""
    words, t, sentence = [], 0.6, 0
    paused = False
    while t < seconds - 1.0:
        if not paused and t >= long_pause_at:
            t += long_pause
            paused = True
        length = rng.randint(5, 11)
        for k in range(length):
            duration = rng.uniform(0.24, 0.48)
            if t + duration > seconds - 0.5:
                break
            token = rng.choice(VOCAB)
            if k == 0:
                token = token.capitalize()
            if k == length - 1:
                token += "."
            elif k == length // 2 and rng.random() < 0.3:
                token += ","
            words.append((token, t, t + duration))
            t += duration + rng.uniform(0.04, 0.12)
        t += rng.uniform(0.45, 1.0)
        sentence += 1
    return words


def synthesize(script, seconds, seed):
    rng = np.random.default_rng(seed)
    audio = (rng.standard_normal(int(seconds * R)) * 0.0004).astype(np.float32)
    for _, start, end in script:
        burst = word_burst(end - start, amp=rng.uniform(0.05, 0.12), f0=rng.uniform(110, 190))
        i = int(start * R)
        audio[i:i + burst.size] += burst[:max(0, audio.size - i)]
    return audio


def fake_whisper(window, script, rng):
    """Simulated Whisper output for one window, with realistic edge effects."""
    ws, we = window.start / R, window.end / R
    out = []
    for token, start, end in script:
        covered = min(we, end) - max(ws, start)
        if covered <= 0:
            continue
        fraction = covered / (end - start)
        if fraction >= 0.6:
            pass
        elif fraction >= 0.25 and end > we:
            if rng.random() < 0.5:  # cut-off word at the right edge
                bare = token.rstrip(".,")
                token = bare[:max(2, int(len(bare) * fraction))]
        elif fraction >= 0.25 and rng.random() < 0.5:
            pass  # partially heard left-edge word kept
        else:
            continue
        if rng.random() < 0.04:
            token = rng.choice(VOCAB)  # recognition error
        out.append(token)
    if not out:
        return {"text": "Thank you.", "segments": [
            {"text": " Thank you.", "no_speech_prob": 0.9, "avg_logprob": -1.0, "compression_ratio": 1.0}]}
    out[0] = out[0][:1].upper() + out[0][1:]
    if not out[-1].endswith((".", ",")) and rng.random() < 0.7:
        out[-1] += "."  # window-edge full stop
    text = " " + " ".join(out)
    return {"text": text, "segments": [
        {"text": text, "no_speech_prob": 0.05, "avg_logprob": -0.3, "compression_ratio": 1.3}]}


def tokens(text):
    return [t for t in (re.sub(r"[^\w']+", "", w.lower()) for w in text.split()) if t]


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def run_synthetic_benchmark(seed=7, seconds=60.0, latency=(0.35, 1.1)):
    rng = random.Random(seed)
    script = make_script(rng, seconds)
    audio = synthesize(script, seconds, seed)
    clock = FakeClock(0.0)
    budget = st.PreviewBudget(clock=clock)
    core = st.PreviewCore(st.PreviewSettings(), R, budget=budget)
    step = R // 100
    pending, publishes, windows, naive, delivered = None, [], [], [], []
    revision_depth, previous_words = 0, []
    for i in range(0, audio.size, step):
        core.feed(audio[i:i + step])
        clock.now = (i + step) / R
        if pending is not None and clock.now >= pending[2]:
            window, token, _, result = pending
            text = core.finish_request(window, token, "ok", clock.now, result=result)
            pending = None
            delivered.append(window)
            naive.append(st.clean_preview_text(result))
            if text is not None:
                words = text.split()
                common = 0
                while common < min(len(words), len(previous_words)) and words[common] == previous_words[common]:
                    common += 1
                revision_depth = max(revision_depth, len(previous_words) - common)
                previous_words = words
                publishes.append((clock.now, window.end / R))
        if pending is None:
            plan = core.next_window(clock.now)
            if plan is not None:
                window, token = plan
                result = fake_whisper(window, script, rng)
                delay = rng.uniform(*latency) + 0.05 * window.duration
                pending = (window, token, clock.now + delay, result)
                windows.append((clock.now, window))
    # Score only what was delivered; a window still in flight when the
    # recording ends is discarded exactly like a stopped session's request.
    covered_until = max((w.end / R for w in delivered), default=0.0)
    reference = [w for w, s, e in script if (s + e) / 2 < covered_until]
    ref_tokens = tokens(" ".join(reference))
    preview_tokens = tokens(core.text)
    naive_tokens = tokens(" ".join(naive))
    starts = [t for t, _ in windows]
    peak_minute = max((sum(1 for u in starts if t <= u < t + 60) for t in starts), default=0)
    speech_spans = [(s, e) for _, s, e in script]
    silent_windows = sum(
        1 for _, w in windows
        if not any(min(w.end / R, e) > max(w.start / R, s) for s, e in speech_spans))
    duplicates = sum(1 for a, b in zip(preview_tokens, preview_tokens[1:]) if a == b) - \
        sum(1 for a, b in zip(ref_tokens, ref_tokens[1:]) if a == b)
    first_word = script[0][1]
    return {
        "seed": seed,
        "audio_seconds": seconds,
        "reference_words": len(ref_tokens),
        "requests": len(windows),
        "peak_requests_per_minute": peak_minute,
        "billed_audio_seconds": round(sum(max(10.0, w.duration) for _, w in windows), 1),
        "silent_windows_sent": silent_windows,
        "first_preview_latency_s": round(publishes[0][0] - first_word, 2) if publishes else None,
        "mean_update_interval_s": round(float(np.mean(np.diff([t for t, _ in publishes]))), 2)
        if len(publishes) > 1 else None,
        "mean_staleness_s": round(float(np.mean([t - end for t, end in publishes])), 2) if publishes else None,
        "preview_wer": round(edit_distance(preview_tokens, ref_tokens) / max(1, len(ref_tokens)), 3),
        "naive_concat_wer": round(edit_distance(naive_tokens, ref_tokens) / max(1, len(ref_tokens)), 3),
        "extra_adjacent_duplicates": duplicates,
        "max_revision_words": revision_depth,
        "silence_skips": core.stats["silence"],
    }


def benchmark_capture_overhead(chunks=20000):
    """Wall-clock cost of push_audio on the capture thread (no worker)."""
    session = st.GroqChunkSession(st.PreviewSettings(max_queue_chunks=chunks + 1),
                                  client_provider=lambda: None)
    chunk = voiced(0.01)
    started = time.perf_counter()
    for _ in range(chunks):
        session.push_audio(chunk)
    per_call = (time.perf_counter() - started) / chunks
    planner = st.WindowPlanner(st.PreviewSettings(), R)
    feed_planner(planner, voiced(10.0), chunk=160)
    started = time.perf_counter()
    for _ in range(20):
        window, _ = planner.propose()
        st.encode_wav(window.audio, R)
    prepare = (time.perf_counter() - started) / 20
    return {"push_audio_us": round(per_call * 1e6, 2), "window_and_wav_ms": round(prepare * 1e3, 2)}


class SyntheticBenchmarkTests(unittest.TestCase):
    SEEDS = (7, 11, 23, 42)

    def test_benchmark_alignment_latency_and_budget(self):
        for seed in self.SEEDS:
            report = run_synthetic_benchmark(seed)
            with self.subTest(seed=seed, report=report):
                self.assertGreater(report["reference_words"], 60)
                self.assertLessEqual(report["preview_wer"], 0.12)
                self.assertLess(report["preview_wer"], report["naive_concat_wer"])
                self.assertLessEqual(report["extra_adjacent_duplicates"], 1)
                self.assertLessEqual(report["max_revision_words"], 8)
                self.assertLessEqual(report["peak_requests_per_minute"], 12)
                self.assertEqual(report["silent_windows_sent"], 0)
                self.assertGreater(report["silence_skips"], 0)
                self.assertIsNotNone(report["first_preview_latency_s"])
                self.assertLessEqual(report["first_preview_latency_s"], 6.0)
                self.assertLessEqual(report["mean_update_interval_s"], 7.0)

    def test_benchmark_is_deterministic(self):
        self.assertEqual(run_synthetic_benchmark(5), run_synthetic_benchmark(5))

    def test_capture_path_is_cheap(self):
        overhead = benchmark_capture_overhead(5000)
        self.assertLess(overhead["push_audio_us"], 200.0)  # generous for slow CI


if __name__ == "__main__":
    if "--benchmark" in sys.argv:
        setUpModule()
        try:
            reports = [run_synthetic_benchmark(seed) for seed in SyntheticBenchmarkTests.SEEDS]
            print(json.dumps({"synthetic_mocked_benchmark": reports,
                              "capture_overhead": benchmark_capture_overhead()}, indent=2))
        finally:
            tearDownModule()
    else:
        unittest.main()
