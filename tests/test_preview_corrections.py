"""Adversarial regressions. Synthetic audio, local SQLite and fake SDK only."""
import multiprocessing
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock
import numpy as np

import streaming_stt as st
from preview_quota import DurablePreviewBudget
from tests.test_groq_chunks import FakeClock, detected_speech, voiced, groq_config
from tests import test_groq_chunks as fixtures
from tests.test_streaming_stt import SessionTestCase, FakeTranscriptions, FakeClient, wait_until


_socket_guard = None


def setUpModule():
    global _socket_guard
    _socket_guard = mock.patch.object(socket.socket, 'connect',
                                      side_effect=AssertionError('offline tests only'))
    _socket_guard.start()


def tearDownModule():
    _socket_guard.stop()


def _reserve_process(path, now, results):
    b = DurablePreviewBudget(path, clock=lambda: now)
    token, reason = b.try_begin_preview(6)
    if token:
        b.end_preview(token, 'ok')
    results.put(reason)


class DurableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'quota.sqlite')
        self.clock = FakeClock()
        self.b = DurablePreviewBudget(self.path, clock=self.clock)
        self.assertIn('warming', self.b.disabled_reason())
        self.clock.now += 86401

    def test_disabled_previews_retain_accounting_and_nested_final_counts_once(self):
        with mock.patch.object(st, '_DEFAULT_BUDGET', self.b), mock.patch.object(st, '_BUDGET_PATH', self.path):
            self.assertEqual(st.availability({'streaming_stt_quota_ledger': self.path}), 'disabled')
            with st.final_priority(30):
                with st.final_priority(30):
                    pass
        restarted = DurablePreviewBudget(self.path, clock=self.clock)
        self.assertEqual(restarted.usage()['requests_day'], 1)
        self.assertEqual(restarted.usage()['audio_day'], 30)

    def test_accounting_fault_poisons_other_processes_and_restart(self):
        db = sqlite3.connect(self.path)
        db.execute('BEGIN IMMEDIATE')
        try:
            with self.b.final_priority(50):
                pass
        finally:
            db.close()
        restarted = DurablePreviewBudget(self.path, clock=self.clock)
        self.assertEqual(restarted.try_begin_preview(6), (None, 'disabled'))
        self.assertTrue(Path(self.path + '.unsafe').exists())

    def test_restart_retains_events_cooldown_and_strikes(self):
        token, reason = self.b.try_begin_preview(6)
        self.assertIsNone(reason)
        self.b.end_preview(token, 'rate_limited', retry_after=123)
        restarted = DurablePreviewBudget(self.path, clock=self.clock)
        self.assertEqual(restarted.usage()['audio_day'], 10)
        self.assertEqual(restarted.try_begin_preview(6)[1], 'cooldown')
        self.assertEqual(restarted.cooldown_remaining(), 123)
        self.clock.now += 124
        for _ in range(2):
            token, reason = restarted.try_begin_preview(6)
            self.assertIsNone(reason)
            restarted.end_preview(token, 'rate_limited')
            self.clock.now += 125
        self.assertEqual(DurablePreviewBudget(self.path, clock=self.clock).disabled_reason(),
                         'rate limited repeatedly')

    def test_concurrent_processes_cannot_oversubscribe(self):
        ctx = multiprocessing.get_context('spawn')
        results = ctx.Queue()
        workers = [ctx.Process(target=_reserve_process, args=(self.path, self.clock(), results)) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
            self.assertEqual(worker.exitcode, 0)
        outcomes = [results.get(timeout=2) for _ in workers]
        self.assertGreater(outcomes.count(None), 0)
        self.assertLessEqual(outcomes.count(None), 4)
        self.assertEqual(self.b.usage()['requests_day'], outcomes.count(None))

    def test_two_hosts_partition_and_explicit_final_reserve(self):
        for key, value in st.FREE_TIER_LIMITS.items():
            self.assertEqual(2 * self.b.limits[key], value)
        for _ in range(4):
            token, reason = self.b.try_begin_preview(6)
            self.assertIsNone(reason)
            self.b.end_preview(token, 'ok')
        self.assertEqual(self.b.try_begin_preview(6)[1], 'rpm')
        # Admission never rejects finals even beyond a host's whole allocation.
        with self.b.final_priority(20000):
            self.assertEqual(self.b.try_begin_preview(6)[1], 'final in progress')
        self.assertEqual(self.b.usage()['audio_day'], 20040)
        self.assertEqual(self.b.try_begin_preview(6)[1], 'rpm')

    def test_missing_corrupt_locked_or_clock_rollback_fail_closed_final_runs(self):
        for mode in ('corrupt', 'locked', 'rollback'):
            with self.subTest(mode=mode):
                if mode == 'corrupt':
                    candidate = str(Path(self.temp.name) / 'bad.sqlite')
                    Path(candidate).write_text('bad data')
                else:
                    candidate = self.path
                b = DurablePreviewBudget(candidate, clock=self.clock)
                if mode == 'rollback':
                    self.clock.now -= 86402
                db = None
                if mode == 'locked':
                    db = sqlite3.connect(candidate)
                    db.execute('BEGIN IMMEDIATE')
                try:
                    self.assertEqual(b.try_begin_preview(6), (None, 'disabled'))
                    with b.final_priority(30):
                        pass  # Full final is always executable.
                finally:
                    if db:
                        db.close()

    def test_final_feedback_and_audio_headers_survive_restart(self):
        with mock.patch.object(st, '_DEFAULT_BUDGET', self.b):
            error = type('RateLimit', (Exception,), {})()
            error.status_code = 429
            error.response = type('Response', (), {'headers': {
                'retry-after': '2m', 'x-ratelimit-remaining-audio-seconds': '0',
                'x-ratelimit-reset-audio-seconds': '1h'}})()
            st.observe_final_response(error=error)
        restarted = DurablePreviewBudget(self.path, clock=self.clock)
        self.assertEqual(restarted.cooldown_remaining(), 3600)
        self.assertEqual(restarted.try_begin_preview(6)[1], 'cooldown')


class SafetyTests(unittest.TestCase):
    def test_assertions_models_and_staleness_are_fail_closed(self):
        cases = ({'streaming_stt_free_tier_verified': False},
                 {'streaming_stt_free_tier_verified': 'true'},
                 {'streaming_stt_quota_partition_verified': False},
                 {'streaming_stt_verified_at': time.time() - 86401},
                 {'streaming_stt_verified_at': time.time() + 60},
                 {'streaming_stt_quota_ledger': ''}, {'groq_model': 'paid-model'})
        for override in cases:
            with self.subTest(override=override), mock.patch.object(st, 'configure_budget') as configure:
                self.assertIsNotNone(st.availability(groq_config(**override)))
                # Ledger setup is allowed even with previews disabled: finals
                # must remain accounted. No provider is created by availability.
                self.assertLessEqual(configure.call_count, 1)

    def test_transport_rejects_unknown_models(self):
        api = FakeTranscriptions()
        with self.assertRaises(st.PreviewUnavailable):
            st.groq_transcribe(FakeClient(api), 'unknown-model', b'RIFF')
        self.assertEqual(api.calls, [])

    def test_immediate_and_quiet_sustained_vowels(self):
        for amp in (0.1, 0.01, 0.004):
            for steady in (False, True):
                with self.subTest(amp=amp, steady=steady):
                    audio = voiced(3, amp=amp)
                    if steady:
                        t = np.arange(3 * 16000) / 16000
                        audio = (amp * (np.sin(2*np.pi*140*t) + .4*np.sin(2*np.pi*280*t))).astype(np.float32)
                    self.assertGreater(detected_speech(audio), 2.7)

    def test_energetic_colored_noise(self):
        noise = np.random.default_rng(77).normal(0, .1, 3 * 16000)
        colored = np.convolve(noise, np.ones(8)/8, mode='same').astype(np.float32)
        self.assertEqual(detected_speech(colored), 0)

    def test_intentional_boundary_repetitions(self):
        for overlap in (False, True):
            r = st.TextReconciler()
            r.add('No')
            r.add('no, do not send', overlap=overlap)
            self.assertEqual(r.text, 'No no, do not send')


class FinalTransportTests(unittest.TestCase):
    def test_final_model_payload_and_headers_preserved_without_preview_assertion(self):
        import types
        helper = fixtures.WhisperEnginePreviewClientTests()
        engine = helper.engine({'use_groq': True, 'groq_model': 'existing-batch-model'})
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(headers={'x-ratelimit-remaining-requests': '0'},
                                         parse=lambda: types.SimpleNamespace(text='Final text.'))
        api = types.SimpleNamespace(with_raw_response=types.SimpleNamespace(create=create))
        engine.groq_client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=api))
        budget = st.PreviewBudget()
        sf = types.SimpleNamespace(write=lambda buffer, *a, **kw: buffer.write(b'full-wav'))
        with mock.patch.dict(engine.transcribe.__globals__, sf=sf), mock.patch.object(engine, '_load_model'), \
                mock.patch.object(st, '_DEFAULT_BUDGET', budget):
            result = engine.transcribe(np.ones(32000, dtype=np.float32))
        self.assertEqual(result, 'Final text.')
        self.assertEqual(calls[0]['file'], ('audio.wav', b'full-wav'))
        self.assertEqual(calls[0]['model'], 'existing-batch-model')
        self.assertEqual(calls[0]['response_format'], 'json')
        self.assertEqual(budget.usage()['requests_day'], 1)
        self.assertGreater(budget.cooldown_remaining(), 0)

    def test_final_429_feeds_budget_without_blocking_batch_error_behavior(self):
        import types
        helper = fixtures.WhisperEnginePreviewClientTests()
        engine = helper.engine({'use_groq': True})
        error = type('RateLimit', (Exception,), {})()
        error.status_code = 429
        error.response = types.SimpleNamespace(headers={'retry-after': '120'})
        api = types.SimpleNamespace(create=mock.Mock(side_effect=error))
        engine.groq_client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=api))
        budget = st.PreviewBudget()
        sf = types.SimpleNamespace(write=lambda buffer, *a, **kw: buffer.write(b'full-wav'))
        with mock.patch.dict(engine.transcribe.__globals__, sf=sf), mock.patch.object(engine, '_load_model'), \
                mock.patch.object(st, '_DEFAULT_BUDGET', budget):
            self.assertEqual(engine.transcribe(np.ones(32000, dtype=np.float32)), '')
        self.assertGreater(budget.cooldown_remaining(), 119)
        self.assertEqual(budget.usage()['requests_day'], 1)


class RaceTests(SessionTestCase):
    def test_final_started_while_preview_encoding_revokes_reservation(self):
        entered, release = threading.Event(), threading.Event()
        original = st.encode_wav
        def encode(*args):
            entered.set()
            release.wait(3)
            return original(*args)
        s, api, partials, _, budget = self.make()
        with mock.patch.object(st, 'encode_wav', side_effect=encode):
            s.start()
            self.push(s, voiced(.6))
            self.assertTrue(entered.wait(2))
            with budget.final_priority(30):
                release.set()
                self.assertTrue(wait_until(lambda: not budget.in_flight()))
                self.assertEqual(api.calls, [])
            s.stop()
        self.assertEqual(partials, [])

    def test_stop_during_encoding_revokes_reservation(self):
        entered, release = threading.Event(), threading.Event()
        original = st.encode_wav
        def encode(*args):
            entered.set()
            release.wait(3)
            return original(*args)
        s, api, _, _, budget = self.make()
        with mock.patch.object(st, 'encode_wav', side_effect=encode):
            s.start()
            self.push(s, voiced(.6))
            self.assertTrue(entered.wait(2))
            s.stop()
            release.set()
            self.assertTrue(wait_until(lambda: not budget.in_flight()))
            self.assertEqual(api.calls, [])

    def test_slow_callback_never_blocks_stop(self):
        entered, release = threading.Event(), threading.Event()
        s, _, _, _, _ = self.make()
        def callback(text):
            entered.set()
            release.wait(3)
        s.set_partial_callback(callback)
        s.start()
        self.push(s, voiced(.6))
        self.assertTrue(entered.wait(2))
        stopped = threading.Event()
        thread = threading.Thread(target=lambda: (s.stop(), stopped.set()))
        thread.start()
        try:
            self.assertTrue(stopped.wait(.2))
        finally:
            release.set()
            thread.join(1)
