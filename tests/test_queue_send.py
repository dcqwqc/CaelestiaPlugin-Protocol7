"""Enter must never be delivered before successful dictation insertion."""
import threading
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np

from ghost_jobs import GhostJobs
from ghost_wave import Target
from control_ipc import valid_command


class UI:
    def __init__(self): self.jobs = []
    def set_jobs(self, jobs): self.jobs = jobs


class Enter:
    def __init__(self): self.armed = False; self.arm_calls = 0; self.forwards = 0; self.disarms = 0
    def arm(self): self.arm_calls += 1; self.armed = True; return True
    def disarm(self): self.disarms += 1; self.armed = False; return True
    def forward_return(self): self.forwards += 1; self.armed = False; return True


class Hotkey:
    def __init__(self): self.count=10; self.non_enter=3
    def focus_epoch(self): return self.count
    def non_enter_focus_epoch(self): return self.non_enter


class App:
    def __init__(self):
        self.ui_manager = UI()
        self.enter_interceptor = Enter()
        self.hotkey = Hotkey()
        self.is_active = False
        self.pastes = []
        self.sends = []
        self.whisper_engine = type("Whisper", (), {"transcribe": lambda _, audio: "a transcript"})()
        self.llm_rewriter = type("Rewrite", (), {"rewrite": lambda _, text: text})()
    def paste_text(self, text): self.pastes.append(text); return True
    def send_enter(self):
        self.sends.append(("Return", len(self.pastes), self.enter_interceptor.armed))
        return True


def until(condition, timeout=2):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if condition(): return
        time.sleep(.01)
    raise AssertionError("condition did not become true")


class QueueSendTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch("ghost_jobs.Path.home", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = App()
        self.manager = GhostJobs(self.app)
        self.target = Target("0x123", "zen", "eDP-1", 200, 300)

    def test_enter_during_processing_waits_for_paste(self):
        processing, resume = threading.Event(), threading.Event()
        def transcribe(_):
            processing.set()
            resume.wait(2)
            return "dictated and then sent"
        self.app.whisper_engine.transcribe = transcribe
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 10, 3)
            self.assertTrue(processing.wait(1))
            self.assertTrue(self.app.enter_interceptor.armed)
            self.app.hotkey.count += 1
            self.assertTrue(self.manager.queue_send()["send_queued"])
            self.assertTrue(self.manager.snapshot()[0]["sendQueued"])
            self.assertEqual(self.app.sends, [])
            self.assertEqual(self.app.pastes, [])
            resume.set()
            until(lambda: job.status == "inserted")
            until(lambda: len(self.app.sends) == 1)
            self.assertEqual(self.app.pastes, ["dictated and then sent"])
            self.assertEqual(self.app.sends, [("Return", 1, False)])
            self.assertFalse(self.app.enter_interceptor.armed)

    def test_other_pending_job_cannot_recapture_synthetic_enter(self):
        processing, release = threading.Event(), threading.Event()
        calls = [0]
        def transcribe(_):
            calls[0] += 1
            if calls[0] == 2:
                processing.set()
                release.wait(2)
            return "text"
        self.app.whisper_engine.transcribe = transcribe
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            first = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 10, 3)
            self.app.hotkey.count += 1
            self.manager.queue_send()
            # Another job is allowed while the first is inserting.
            second = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 11, 3)
            self.assertTrue(processing.wait(2))
            until(lambda: first.status == "inserted")
            until(lambda: bool(self.app.sends))
            self.assertEqual(self.app.sends[0][2], False)
            self.assertTrue(self.app.enter_interceptor.armed)
            release.set()

    def test_enter_listener_event_after_queue_command_is_not_focus_change(self):
        started, release = threading.Event(), threading.Event()
        def transcribe(_):
            started.set()
            release.wait(2)
            return "go!"
        self.app.whisper_engine.transcribe = transcribe
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 10, 3)
            self.assertTrue(started.wait(1))
            # IPC can run before evdev's focus counter observes the key.
            self.assertTrue(self.manager.queue_send()["send_queued"])
            self.app.hotkey.count = 11
            release.set()
            until(lambda: bool(self.app.sends))
            self.assertEqual(len(self.app.pastes), 1)
            self.assertEqual(self.app.sends[0][1:], (1, False))
            self.assertEqual(job.status, "inserted")

    def test_actual_focus_change_after_queued_enter_blocks_auto_send(self):
        started, release = threading.Event(), threading.Event()
        def transcribe(_):
            started.set()
            release.wait(2)
            return "do not send"
        self.app.whisper_engine.transcribe = transcribe
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 10, 3)
            self.assertTrue(started.wait(1))
            self.manager.queue_send()
            self.app.hotkey.non_enter += 1  # user clicked another textbox
            release.set()
            until(lambda: job.status == "ready")
            self.assertEqual(self.app.pastes, [])
            self.assertEqual(self.app.sends, [])

    def test_enter_during_insertion_queues_single_send(self):
        entered, resume = threading.Event(), threading.Event()
        def slow_paste(text):
            entered.set()
            resume.wait(2)
            self.app.pastes.append(text)
            return True
        self.app.paste_text = slow_paste
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 10, 3)
            self.assertTrue(entered.wait(1))
            self.assertEqual(job.status, "inserting")
            self.app.hotkey.count += 1
            self.manager.queue_send()
            self.assertTrue(job.send_queued)
            self.assertEqual(self.app.sends, [])
            resume.set()
            until(lambda: bool(self.app.sends))
            self.assertEqual(len(self.app.sends), 1)

    def test_moved_text_field_wont_be_sent(self):
        entered, resume = threading.Event(), threading.Event()
        def slow(_):
            entered.set()
            resume.wait(2)
            return "text"
        self.app.whisper_engine.transcribe = slow
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 10, 3)
            self.assertTrue(entered.wait(1))
            self.app.hotkey.non_enter = 4
            self.app.hotkey.count = 12
            result = self.manager.queue_send()
            self.assertTrue(result["forwarded"])
            self.assertEqual(self.app.enter_interceptor.forwards, 1)
            resume.set()
            until(lambda: job.status == "ready")
            self.assertEqual(self.app.sends, [])
            self.assertEqual(self.app.pastes, [])

    def test_failed_paste_never_sends(self):
        self.app.paste_text = lambda text: False
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 10, 3)
            self.app.hotkey.count = 11
            self.assertTrue(self.manager.queue_send()["send_queued"])
            until(lambda: job.status == "ready")
            self.assertEqual(self.app.sends, [])

    def test_cancelled_queued_send_never_pastes_or_sends(self):
        event, resume = threading.Event(), threading.Event()
        def slow(_):
            event.set()
            resume.wait(2)
            return "late text"
        self.app.whisper_engine.transcribe = slow
        with patch.object(Target, "same_window_active", return_value=True), patch("ghost_jobs.add_history"):
            job = self.manager.enqueue(np.ones(16000, dtype=np.float32), self.target, 10, 3)
            self.assertTrue(event.wait(1))
            self.app.hotkey.count += 1
            self.manager.queue_send()
            self.manager.cancel(job.id)
            self.assertFalse(self.app.enter_interceptor.armed)
            resume.set()
            time.sleep(.08)
            self.assertEqual(self.app.pastes, [])
            self.assertEqual(self.app.sends, [])

    def test_ipc_command_is_allowlisted(self):
        self.assertTrue(valid_command("queue-send"))
        self.assertFalse(valid_command("queue-send;touch /tmp/bad"))

if __name__ == "__main__":
    unittest.main()
