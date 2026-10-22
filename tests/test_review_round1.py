"""Production SDK selection with mocked TLS; every actual socket stays blocked."""
from contextlib import contextmanager
import json
import io
import multiprocessing
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from unittest import mock

from groq import Groq
import numpy as np
import streaming_stt as st
from preview_quota import DurablePreviewBudget
from tests.test_streaming_stt import SessionTestCase


class ProductionTransportTests(SessionTestCase):
    @contextmanager
    def transport(self, *, block_response=False, send_effect=None):
        import preview_transport as pt
        sent = []
        response_entered, response_release = threading.Event(), threading.Event()
        def send(data):
            sent.append(data)
            if send_effect:
                return send_effect(data)
            return len(data)
        sock = mock.Mock()
        sock.send.side_effect = send
        body = json.dumps({'text': 'production selected partial', 'words': []}).encode()
        wire_response = (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                         b'Retry-After: 7200\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        connection = mock.Mock(sock=sock)
        def makefile(*args, **kwargs):
            response_entered.set()
            if block_response:
                self.assertTrue(response_release.wait(3))
            return io.BytesIO(wire_response)
        sock.makefile.side_effect = makefile
        client = Groq(api_key='offline-test-only', max_retries=0)
        try:
            with mock.patch.object(pt.http.client, 'HTTPSConnection', return_value=connection):
                yield client, sent, response_entered, response_release, connection
        finally:
            response_release.set()
            client.close()

    def run_request(self, session, budget, client):
        window = st.Window(1, 0, 8000, 16000, np.zeros(8000, dtype=np.float32))
        token, reason = budget.try_begin_preview(.5)
        self.assertIsNone(reason)
        session._provider = lambda: (client, st.DEFAULT_GROQ_MODEL)
        return window, token, budget.scheduler.ticket()

    def test_actual_sdk_selected_path_sends_and_publishes(self):
        session, _, partials, _, budget = self.make()
        with self.transport() as (client, sent, _, _, connection):
            session._request(*self.run_request(session, budget, client))
        self.assertEqual(partials, ['production selected partial'])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].split(b'\r\n', 1)[0], b'POST /openai/v1/audio/transcriptions HTTP/1.1')
        self.assertIn(b'name="timestamp_granularities[]"', sent[0])
        self.assertIn(b'preview.wav', sent[0])
        connection.close.assert_called_once()
        session.stop()

    def test_engine_supplier_selects_real_sdk_transport(self):
        from tests.test_groq_chunks import WhisperEnginePreviewClientTests
        session, _, partials, _, budget = self.make()
        engine = WhisperEnginePreviewClientTests().engine({'use_groq': True,
                    'groq_api_key': 'offline-test-only', 'groq_model': st.DEFAULT_GROQ_MODEL})
        with self.transport() as (client, sent, _, _, _):
            plan = self.run_request(session, budget, client)
            engine.groq_client = client
            engine.current_groq_key = 'offline-test-only'
            session._provider = engine.groq_preview_client
            session._request(*plan)
            self.assertEqual(len(sent), 1)
            self.assertEqual(partials, ['production selected partial'])
        session.stop()

    def test_production_multithread_dispatch_contention(self):
        session, _, partials, _, budget = self.make()
        entered = threading.Barrier(9)
        release = threading.Event()
        dispatch = budget.scheduler.dispatch
        def barrier(*args, **kwargs):
            entered.wait(3)
            self.assertTrue(release.wait(3))
            return dispatch(*args, **kwargs)
        with self.transport() as (client, sent, _, _, _), mock.patch.object(budget.scheduler, 'dispatch', side_effect=barrier):
            plan = self.run_request(session, budget, client)
            threads = [threading.Thread(target=session._request, args=plan) for _ in range(8)]
            for thread in threads:
                thread.start()
            entered.wait(3)
            with budget.final_priority(30):
                pass
            release.set()
            for thread in threads:
                thread.join(3)
                self.assertFalse(thread.is_alive())
            self.assertEqual(sent, [])
            self.assertEqual(partials, [])
        session.stop()

    def test_production_boundary_stop_and_completed_final(self):
        for action in ('stop', 'final'):
            session, _, partials, _, budget = self.make()
            entered, release = threading.Event(), threading.Event()
            dispatch = budget.scheduler.dispatch
            def barrier(*args, **kwargs):
                entered.set()
                self.assertTrue(release.wait(3))
                return dispatch(*args, **kwargs)
            with self.transport() as (client, sent, _, _, _), mock.patch.object(budget.scheduler, 'dispatch', side_effect=barrier):
                plan = self.run_request(session, budget, client)
                thread = threading.Thread(target=session._request, args=plan)
                thread.start()
                self.assertTrue(entered.wait(3))
                if action == 'stop':
                    session.stop()
                else:
                    with budget.final_priority(30):
                        pass
                release.set()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(sent, [])
                self.assertEqual(partials, [])
                self.assertEqual(budget.usage()['requests_day'], int(action == 'final'))
            session.stop()

    def test_production_session_shared_final_at_http_boundary(self):
        with tempfile.TemporaryDirectory(prefix='protocol7-http-ledger-', dir='/tmp') as directory:
            now = [100000.0]
            path = str(Path(directory) / 'ledger')
            a = DurablePreviewBudget(path, clock=lambda: now[0])
            a.disabled_reason()
            now[0] += 86401
            b = DurablePreviewBudget(path, clock=lambda: now[0])
            partials = []
            session = st.GroqChunkSession(st.PreviewSettings(), budget=a, on_partial=partials.append)
            dispatch = a.scheduler.dispatch
            def register_then_dispatch(*args, **kwargs):
                with b.final_priority(30):
                    pass
                return dispatch(*args, **kwargs)
            with self.transport() as (client, sent, _, _, _), mock.patch.object(a.scheduler, 'dispatch', side_effect=register_then_dispatch):
                session._request(*self.run_request(session, a, client))
                self.assertEqual(sent, [])
                self.assertEqual(partials, [])
                self.assertEqual(a.usage()['requests_day'], 1)
            session.stop()

    def test_production_inflight_response_does_not_block_stop_final(self):
        session, _, partials, _, budget = self.make()
        with self.transport(block_response=True) as (client, sent, entered, release, _):
            thread = threading.Thread(target=session._request, args=self.run_request(session, budget, client))
            thread.start()
            self.assertTrue(entered.wait(3))
            session.stop()
            with budget.final_priority(30):
                self.assertEqual(len(sent), 1)
            release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(partials, [])
            self.assertEqual(budget.usage()['requests_day'], 2)

    def test_not_ready_first_write_rechecks_revocation(self):
        session, _, _, _, budget = self.make()
        import preview_transport as pt
        with self.transport() as (client, sent, _, _, connection):
            # Stop during TLS setup: no transcription payload write may follow.
            connection.connect.side_effect = session.stop
            session._request(*self.run_request(session, budget, client))
            self.assertEqual(sent, [])

    def test_tls_partial_write_is_already_inflight(self):
        session, _, partials, _, budget = self.make()
        import preview_transport as pt
        attempts = [0]
        # Calling stop from *inside* the scheduler would deadlock; use a
        # readiness wait after the first attempted write to register stop.
        def would_block(data):
            attempts[0] += 1
            if attempts[0] == 1:
                raise ssl.SSLWantWriteError()
            return len(data)
        with self.transport(send_effect=would_block) as (client, sent, _, _, _), mock.patch.object(pt.select, 'select', side_effect=lambda *a: session.stop()):
            session._request(*self.run_request(session, budget, client))
        self.assertEqual(len(sent), 2)
        self.assertEqual(partials, [])
        self.assertEqual(budget.usage()['requests_day'], 1)


def _register_final_in_process(path):
    with DurablePreviewBudget(path).final_priority(30):
        pass


class SharedDispatchTests(unittest.TestCase):
    def test_independent_instances_final_revokes_admitted_request_even_after_completion(self):
        now = [100000.0]
        with tempfile.TemporaryDirectory(prefix='protocol7-shared-', dir='/tmp') as directory:
            path = str(Path(directory) / 'ledger')
            a = DurablePreviewBudget(path, clock=lambda: now[0])
            a.disabled_reason()
            now[0] += 86401
            b = DurablePreviewBudget(path, clock=lambda: now[0])
            session = st.GroqChunkSession(st.PreviewSettings(), budget=a)
            epoch = a.scheduler.ticket()
            self.assertTrue(a.dispatch_allowed())
            with b.final_priority(30):
                for _ in range(2):
                    with self.assertRaises(st.PreviewUnavailable):
                        a.scheduler.dispatch(session, epoch, self.fail)
            with self.assertRaises(st.PreviewUnavailable):
                a.scheduler.dispatch(session, epoch, self.fail)
            calls = []
            a.scheduler.dispatch(session, a.scheduler.ticket(), lambda: calls.append(True))
            self.assertEqual(calls, [True])
            session.stop()

    def test_transport_readiness_is_not_a_ledger_fault(self):
        with tempfile.TemporaryDirectory(prefix='protocol7-readiness-', dir='/tmp') as directory:
            budget = DurablePreviewBudget(str(Path(directory) / 'ledger'))
            session = st.GroqChunkSession(st.PreviewSettings(), budget=budget)
            def start():
                raise ssl.SSLWantWriteError()
            with self.assertRaises(ssl.SSLWantWriteError):
                budget.scheduler.dispatch(session, budget.scheduler.ticket(), start)
            self.assertFalse(budget._fault)
            self.assertFalse(Path(budget._unsafe_path).exists())

    def test_ledger_failure_after_send_is_never_not_sent(self):
        with tempfile.TemporaryDirectory(prefix='protocol7-commit-', dir='/tmp') as directory:
            budget = DurablePreviewBudget(str(Path(directory) / 'ledger'))
            session = st.GroqChunkSession(st.PreviewSettings(), budget=budget)
            epoch = budget.scheduler.ticket()
            lock = budget._lock
            @contextmanager
            def fail_commit():
                with lock:
                    yield
                raise OSError('offline injected commit failure')
            calls = []
            with mock.patch.object(budget, '_lock', fail_commit()):
                with self.assertRaises(RuntimeError):
                    budget.scheduler.dispatch(session, epoch, lambda: calls.append(True))
            self.assertEqual(calls, [True])
            self.assertTrue(budget._fault)

    def test_other_process_completed_final_revokes_ticket(self):
        with tempfile.TemporaryDirectory(prefix='protocol7-process-', dir='/tmp') as directory:
            path = str(Path(directory) / 'ledger')
            budget = DurablePreviewBudget(path)
            session = st.GroqChunkSession(st.PreviewSettings(), budget=budget)
            epoch = budget.scheduler.ticket()
            worker = multiprocessing.get_context('spawn').Process(target=_register_final_in_process, args=(path,))
            worker.start()
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
            with self.assertRaises(st.PreviewUnavailable):
                budget.scheduler.dispatch(session, epoch, self.fail)
            self.assertEqual(budget.usage()['requests_day'], 1)

    def test_shared_scheduler_transport_and_final_have_one_order(self):
        with tempfile.TemporaryDirectory(prefix='protocol7-order-', dir='/tmp') as directory:
            path = str(Path(directory) / 'ledger')
            a, b = DurablePreviewBudget(path), DurablePreviewBudget(path)
            session = st.GroqChunkSession(st.PreviewSettings(), budget=a)
            epoch = a.scheduler.ticket()
            calls = []
            a.scheduler.dispatch(session, epoch, lambda: calls.append('transport'))
            with b.final_priority(30):
                calls.append('final')
                with self.assertRaises(st.PreviewUnavailable):
                    a.scheduler.dispatch(session, a.scheduler.ticket(), lambda: calls.append('late'))
            self.assertEqual(calls, ['transport', 'final'])


class TemporalAlignmentTests(unittest.TestCase):
    def test_shared_no_and_yes_are_not_duplicated(self):
        for a, b, expected in [('please say no', 'no more', 'please say no more'),
                                ('we said yes', 'yes to that', 'we said yes to that')]:
            r = st.TextReconciler()
            r.add(a)
            r.add(b, overlap=True)
            self.assertEqual(r.text, expected)

    def test_temporal_evidence_preserves_actual_repetition(self):
        for word in ('no', 'yes', 'here'):
            for shared in (False, True):
                r = st.TextReconciler()
                r.add('please say ' + word)
                r.add(word + ', more', overlap=True, boundary_shared=shared)
                expected = 'please say ' + (word + ' ' if not shared else '') + word + ', more'
                self.assertEqual(r.text, expected)

    def test_core_uses_absolute_word_times(self):
        for new_start, expected in [(0.0, 'please say no more'), (.6, 'please say no no more')]:
            core = st.PreviewCore(st.PreviewSettings(), 16000, budget=st.PreviewBudget())
            for index, start, text, words in [
                (1, 0, 'please say no', [{'word': 'please', 'start': 0, 'end': .2}, {'word': 'no', 'start': .8, 'end': 1}]),
                (2, 12800, 'no more', [{'word': 'no', 'start': new_start, 'end': new_start + .2}, {'word': 'more', 'start': .9, 'end': 1.1}])]:
                window = st.Window(index, start, start+32000, 16000, np.zeros(32000), overlap=index > 1)
                token, _ = core.budget.try_begin_preview(2)
                core.finish_request(window, token, 'ok', 0, result={'text': text, 'words': words})
            self.assertEqual(core.text, expected)


class RetryAfterHTTPDateTests(unittest.TestCase):
    """Actual RFC-7231 HTTP-date must not be replaced by a 60s fallback."""

    def test_numeric_and_http_date_have_equivalent_7200_second_cooldown(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        import types
        instant = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
        epoch = instant.timestamp()
        http_date = format_datetime(instant + timedelta(hours=2), usegmt=True)
        for header in ('7200', http_date):
            with self.subTest(header=header):
                error = types.SimpleNamespace(status_code=429,
                    response=types.SimpleNamespace(headers={'Retry-After': header}))
                with mock.patch.object(st.time, 'time', return_value=epoch):
                    kind, wait = st.classify_error(error)
                self.assertEqual(kind, 'rate_limited')
                self.assertAlmostEqual(wait, 7200.0, delta=1.0)
                clock=[1000.0]
                budget=st.PreviewBudget(clock=lambda:clock[0])
                token, denied=budget.try_begin_preview(5)
                self.assertIsNone(denied)
                budget.end_preview(token, kind, retry_after=wait)
                self.assertAlmostEqual(budget.cooldown_remaining(),7200.0,delta=1.0)

    def test_http_date_durable_cooldown_survives_recreation(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        import types
        instant=datetime(2026,10,22,12,0,0,tzinfo=timezone.utc)
        wall=instant.timestamp()
        future=format_datetime(instant+timedelta(hours=2),usegmt=True)
        err=types.SimpleNamespace(status_code=429,
            response=types.SimpleNamespace(headers={'Retry-After':future}))
        with mock.patch.object(st.time,'time',return_value=wall):
            outcome,wait=st.classify_error(err)
        with tempfile.TemporaryDirectory(prefix='p7-http-date-',dir='/tmp') as directory:
            path=str(Path(directory)/'ledger')
            clock=[wall]
            budget=DurablePreviewBudget(path,clock=lambda:clock[0])
            budget.disabled_reason()  # seed the durable ledger
            clock[0]+=86401  # conservative 24h warmup
            token,denied=budget.try_begin_preview(6)
            self.assertIsNone(denied)
            budget.end_preview(token,outcome,retry_after=wait)
            clock[0]+=1
            restarted=DurablePreviewBudget(path,clock=lambda:clock[0])
            self.assertGreaterEqual(restarted.cooldown_remaining(),7198)
            self.assertEqual(restarted.try_begin_preview(6)[1],'disabled' if restarted.disabled_reason() else 'cooldown')

    def test_malformed_and_past_http_dates(self):
        from datetime import datetime,timedelta,timezone
        from email.utils import format_datetime
        old=format_datetime(datetime(2026,10,22,12,0,tzinfo=timezone.utc)-timedelta(hours=1),usegmt=True)
        self.assertEqual(st.parse_retry_after(old,now=datetime(2026,10,22,12,0,tzinfo=timezone.utc).timestamp()),0)
        self.assertIsNone(st.parse_retry_after('not-a-date'))
        self.assertIsNone(st.parse_retry_after('Thu, 22 Oct 2026 12:00:00'))
