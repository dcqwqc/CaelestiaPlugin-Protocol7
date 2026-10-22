"""Deterministic release barriers. No provider, sockets, devices or home state."""
from concurrent.futures import Future
import io
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
import wave

import numpy as np
import streaming_stt as st
from preview_publication import PreviewPublication
from preview_quota import DurablePreviewBudget
from tests.test_streaming_stt import SessionTestCase, wait_until


class DispatchTests(SessionTestCase):
    def pause_at_boundary(self, action):
        session, api, partials, _, budget = self.make()
        entered, release = threading.Event(), threading.Event()
        original = budget.scheduler.dispatch
        def barrier(*args, **kw):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(*args, **kw)
        with mock.patch.object(budget.scheduler, 'dispatch', side_effect=barrier):
            session.start()
            from tests.test_groq_chunks import voiced
            self.push(session, voiced(.6))
            self.assertTrue(entered.wait(3))
            action(session, budget)
            release.set()
            self.assertTrue(wait_until(lambda: not budget.in_flight()))
            self.assertEqual(api.calls, [])
            self.assertEqual(partials, [])
        session.stop()

    def test_stop_paused_immediately_before_transport(self):
        self.pause_at_boundary(lambda session, budget: session.stop())

    def test_final_registration_and_completion_revoke_queued_transport(self):
        def final(session, budget):
            with budget.final_priority(30):
                pass
        self.pause_at_boundary(final)

    def test_final_after_stop_still_executes(self):
        session, _, _, _, budget = self.make()
        session.stop()
        with budget.final_priority(30):
            self.assertEqual(budget._finals_in_flight, 1)
        self.assertEqual(budget.usage()['audio_day'], 30)

    def test_already_dispatched_future_does_not_block_stop_or_final(self):
        session, _, _, _, budget = self.make()
        future = Future()
        calls = []
        def start(**kwargs):
            calls.append(kwargs)
            return future
        scheduler = budget.scheduler
        self.assertIs(scheduler.dispatch(session, scheduler.epoch, start, audio='fake'), future)
        session.stop()
        with budget.final_priority(30):
            with self.assertRaises(st.PreviewUnavailable):
                scheduler.dispatch(session, scheduler.epoch, start)
        future.set_result({'text': 'already sent cannot be retracted'})
        self.assertEqual(len(calls), 1)

    def test_contention_all_pending_epochs_revoked(self):
        for action in ('stop', 'final'):
            session, _, _, _, budget = self.make()
            barrier = threading.Barrier(17)
            release = threading.Event()
            calls = []
            epoch = budget.scheduler.epoch
            errors = []
            def contender():
                barrier.wait(timeout=3)
                release.wait(3)
                try:
                    budget.scheduler.dispatch(session, epoch, lambda: calls.append(True))
                except st.PreviewUnavailable:
                    errors.append(True)
            threads = [threading.Thread(target=contender) for _ in range(16)]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=3)
            if action == 'stop':
                session.stop()
            else:
                with budget.final_priority(30):
                    pass
            release.set()
            for thread in threads:
                thread.join(3)
                self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 16)
            self.assertEqual(calls, [])
            session.stop()

    def test_unsupported_synchronous_sdk_never_called(self):
        api = mock.Mock()
        client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=api))
        with self.assertRaises(st.PreviewUnavailable):
            st.groq_transcribe(client, st.DEFAULT_GROQ_MODEL, b'wav', dispatch=mock.Mock())
        api.create.assert_not_called()

    def test_stop_does_not_wait_for_core_worker(self):
        session, _, _, _, _ = self.make()
        done = threading.Event()
        with session._lock:
            thread = threading.Thread(target=lambda: (session.stop(), done.set()))
            thread.start()
            self.assertTrue(done.wait(.3))
        thread.join(1)


class AlignmentTests(unittest.TestCase):
    def test_adversarial_overlap(self):
        cases = [
            ('first part ends here', 'here comes something new', 'first part ends here comes something new'),
            ('the quick brown fox jumps', 'brown fox', 'the quick brown fox jumps'),
            ('No', 'no, do not send', 'No no, do not send'),
            ('No', 'no', 'No no'),
            ('No.', 'no, do not send', 'No. no, do not send'),
            ('please say no', 'no, no more', 'please say no no, no more'),
            ('今天我们去北京', '去北京看朋友', '今天我们去北京看朋友'),
            ('今天我们去北京', '我们去', '今天我们去北京'),
            ('今天我们去北京。', '北京，见朋友。', '今天我们去北京，见朋友。'),
            ('this ends here.', 'here, comes more.', 'this ends here, comes more.'),
        ]
        for previous, new, expected in cases:
            with self.subTest(previous=previous, new=new):
                r = st.TextReconciler()
                r.add(previous)
                r.add(new, overlap=True)
                self.assertEqual(r.text, expected)


class PositionTests(unittest.TestCase):
    def test_dropped_c_marks_gap_after_a_b_before_d(self):
        session = st.GroqChunkSession(st.PreviewSettings(max_queue_chunks=2))
        a, b, c, d = [np.full(16, n, dtype=np.float32) for n in range(4)]
        session.push_audio(a)
        session.push_audio(b)
        session.push_audio(c)
        chunks = session._drain(0)
        self.assertEqual([position for position, _ in chunks], [0, 16])
        planner = session._core.planner
        for position, chunk in chunks:
            self.assertEqual(position, session._consumed_position)
            planner.add(chunk)
            session._consumed_position = position + len(chunk)
        self.assertEqual(planner.buffered_samples(), 32)
        session.push_audio(d)
        position, chunk = session._drain(0)[0]
        self.assertEqual(position, 48)
        planner.discontinuity(position-session._consumed_position)
        planner.add(chunk)
        self.assertEqual(planner.total, 64)
        self.assertEqual(planner.buffered_samples(), 16)
        np.testing.assert_array_equal(np.concatenate(list(planner._chunks)), d)
        self.assertTrue(planner._lost_since_sent)

    def test_in_flight_old_window_cannot_clear_later_discontinuity(self):
        planner = st.WindowPlanner(st.PreviewSettings(), 16000)
        planner.add(np.zeros(160))
        window = st.Window(1, 0, 160, 16000, np.zeros(160))
        planner.discontinuity(16)
        planner.add(np.zeros(160))
        planner.commit(window, delivered=True)
        self.assertTrue(planner._lost_since_sent)


class CooldownTests(unittest.TestCase):
    def test_header_7200_survives_new_sessions_and_ledger_restart(self):
        now = [100000.0]
        with tempfile.TemporaryDirectory(prefix='protocol7-', dir='/tmp') as directory:
            path = str(Path(directory) / 'ledger.sqlite')
            budget = DurablePreviewBudget(path, clock=lambda: now[0])
            budget.disabled_reason()
            now[0] += 86401
            token, reason = budget.try_begin_preview(6)
            self.assertIsNone(reason)
            error = types.SimpleNamespace(status_code=429, response=types.SimpleNamespace(headers={'Retry-After': '7200'}))
            kind, retry = st.classify_error(error)
            self.assertEqual(retry, 7200)
            budget.end_preview(token, kind, retry_after=retry)
            now[0] += 3601
            restarted = DurablePreviewBudget(path, clock=lambda: now[0])
            self.assertEqual(restarted.cooldown_remaining(), 3599)
            for _ in range(2):
                session = st.GroqChunkSession(st.PreviewSettings(), budget=restarted)
                self.assertEqual(session._core.budget.try_begin_preview(6)[1], 'cooldown')
                session.stop()
            now[0] += 3599
            self.assertIsNotNone(restarted.try_begin_preview(6)[0])

    def test_direct_header_string_not_shortened(self):
        budget = st.PreviewBudget(clock=lambda: 100)
        token, _ = budget.try_begin_preview(6)
        budget.end_preview(token, 'rate_limited', retry_after='7200')
        self.assertEqual(budget.cooldown_remaining(), 7200)


class ResamplingTests(unittest.TestCase):
    @staticmethod
    def rms(rate, frequency):
        signal = .5*np.sin(2*np.pi*frequency*np.arange(rate)/rate)
        with wave.open(io.BytesIO(st.encode_wav(signal, rate)), 'rb') as handle:
            samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype='<i2') / 32767
        return np.sqrt(np.mean(samples[100:-100]**2))

    def test_alias_suppressed_and_speech_preserved(self):
        for rate in (48000, 44100):
            with self.subTest(rate=rate):
                speech = self.rms(rate, 1000)
                alias = self.rms(rate, 12000)
                self.assertGreater(speech, .34)
                self.assertLess(alias/speech, .001)  # at least 60 dB rejection


class PublicationTests(unittest.TestCase):
    def test_queued_consumer_rejects_old_generation(self):
        gate = PreviewPublication()
        old = types.SimpleNamespace(active=True)
        new = types.SimpleNamespace(active=True)
        displayed = []
        gate.bind(old)
        queued = lambda: gate.consume(old, 'obsolete', displayed.append)
        old.active = False
        gate.bind(new)
        gate.consume(new, 'current', displayed.append)
        self.assertFalse(queued())
        self.assertEqual(displayed, ['current'])

    def test_callback_paused_before_consumer_cannot_overwrite_new(self):
        gate = PreviewPublication()
        old, new = [types.SimpleNamespace(active=True) for _ in range(2)]
        gate.bind(old)
        entered, release = threading.Event(), threading.Event()
        displayed = []
        def callback():
            entered.set()
            release.wait(3)
            gate.consume(old, 'old', displayed.append)
        thread = threading.Thread(target=callback)
        thread.start()
        self.assertTrue(entered.wait(2))
        old.active = False
        gate.bind(new)
        gate.consume(new, 'new', displayed.append)
        release.set()
        thread.join(3)
        self.assertEqual(displayed, ['new'])

    def test_native_bridge_uses_identity_consumer(self):
        from native_bridge import NativeUIBridge
        bridge = NativeUIBridge.__new__(NativeUIBridge)
        bridge._preview_publication = PreviewPublication()
        bridge._state_lock = threading.Lock()
        bridge._state = {'liveText': ''}
        bridge._publish = mock.Mock()
        old, new = [types.SimpleNamespace(active=True) for _ in range(2)]
        bridge.bind_preview_session(old)
        bridge.bind_preview_session(new)
        bridge.publish_preview(new, 'current')
        bridge.publish_preview(old, 'obsolete')
        self.assertEqual(bridge._state['liveText'], 'current')

    def test_gtk_queued_call_rechecks_at_actual_consumption(self):
        # Exercise actual manager methods without importing a desktop toolkit.
        import ast
        source = Path('ui_linux.py').read_text()
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'UIManager')
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {'bind_preview_session', 'publish_preview'}]
        queue = []
        namespace = {'GLib': types.SimpleNamespace(idle_add=queue.append)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Consumer', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])), 'ui_linux.py', 'exec'), namespace)
        ui = namespace['Consumer']()
        ui._preview_publication = PreviewPublication()
        displayed = []
        ui.set_live_text = displayed.append
        old, new = [types.SimpleNamespace(active=True) for _ in range(2)]
        ui.bind_preview_session(old)
        ui.publish_preview(old, 'old queued')
        ui.bind_preview_session(new)
        ui.publish_preview(new, 'new queued')
        queue[1]()
        queue[0]()
        self.assertEqual(displayed, ['new queued'])

class WorkerRegressionTests(unittest.TestCase):
    def test_worker_applies_loss_at_sequence_position(self):
        session = st.GroqChunkSession(st.PreviewSettings(max_queue_chunks=2))
        chunks = [np.full(16, i, dtype=np.float32) for i in range(4)]
        for chunk in chunks[:3]:
            session.push_audio(chunk)
        first = session._drain(0)
        session.push_audio(chunks[3])
        second = session._drain(0)
        batches = iter([first, second])
        def drain(_timeout):
            try:
                return next(batches)
            except StopIteration:
                session.stop()
                return []
        events = []
        with mock.patch.object(session, '_drain', side_effect=drain), \
                mock.patch.object(session._core, 'feed', side_effect=lambda a: events.append(('feed', int(a[0])))), \
                mock.patch.object(session._core, 'discontinuity', side_effect=lambda n: events.append(('gap', n))), \
                mock.patch.object(session._core, 'next_window', return_value=None):
            session._run()
        self.assertEqual(events, [('feed', 0), ('feed', 1), ('gap', 16), ('feed', 3)])

    def test_resampling_six_second_worker_budget(self):
        for rate in (44100, 48000):
            started = time.perf_counter()
            result = st.encode_wav(np.zeros(rate*6, dtype=np.float32), rate)
            self.assertEqual(len(result), 44 + 6*16000*2)
            self.assertLess(time.perf_counter()-started, 1.0)

    def test_main_callback_in_flight_uses_scoped_consumer(self):
        from tests.test_streaming_integration import import_main_linux, FakeUI
        app_class = import_main_linux().Protocol7App
        app = app_class.__new__(app_class)
        app.is_active = True
        old, new = [types.SimpleNamespace(active=True) for _ in range(2)]
        app._stream_session = old
        app.ui_manager = FakeUI()
        app.ui_manager.bind_preview_session(old)
        entered, release = threading.Event(), threading.Event()
        consumer = app.ui_manager.publish_preview
        def delayed(session, text):
            entered.set()
            release.wait(3)
            consumer(session, text)
        with mock.patch.object(app.ui_manager, 'publish_preview', side_effect=delayed):
            thread = threading.Thread(target=lambda: app._on_stream_partial(old, 'old'))
            thread.start()
            self.assertTrue(entered.wait(2))
            app._stream_session = new
            app.ui_manager.bind_preview_session(new)
            consumer(new, 'new')
            release.set()
            thread.join(3)
        self.assertEqual(app.ui_manager.live, ['new'])
