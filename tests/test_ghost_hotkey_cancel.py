"""Regression: Double-Ctrl closes detached processing rather than re-recording."""
import threading
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from ghost_jobs import GhostJobs
from main_linux import Protocol7App


def wait_for(job, status, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job.status == status:
            return
        time.sleep(.01)
    raise AssertionError(f"{job.id} is {job.status}, not {status}")


class FakeUI:
    def __init__(self):
        self.jobs = []
        self.visible = False
    def set_jobs(self, jobs):
        self.jobs = jobs
    def show(self):
        self.visible = True
    def hide(self):
        self.visible = False


class FakeAudio:
    def __init__(self):
        self.is_recording = False
        self.starts = 0
        self.stops = 0
    def start_recording(self):
        self.starts += 1
        self.is_recording = True
    def stop_recording(self):
        self.stops += 1
        self.is_recording = False
        return np.ones(16000, dtype=np.float32)


class FakeHotkey:
    def focus_epoch(self):
        return 0


class HotkeyCancelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home_patch = patch("ghost_jobs.Path.home", return_value=Path(self.tmp.name))
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        # Avoid opening devices, registering hotkeys or starting a second daemon.
        self.app = Protocol7App.__new__(Protocol7App)
        self.app._dictation_lock = threading.RLock()
        self.app._recording_target = None
        self.app.is_active = False
        self.app.is_processing = False
        self.app.ui_manager = FakeUI()
        self.app.audio_recorder = FakeAudio()
        self.app.hotkey = FakeHotkey()
        self.app.pasted = []
        self.app.paste_text = self.app.pasted.append
        self.app.llm_rewriter = type("Rewriter", (), {"rewrite": lambda _, text: text})()
        self.app.whisper_engine = type("Whisper", (), {"transcribe": lambda _, data: "late transcript"})()
        self.app._ghost = GhostJobs(self.app)

    def test_double_ctrl_dismisses_loading_wave_and_discards_late_response(self):
        started, release = threading.Event(), threading.Event()
        def slow(audio):
            started.set()
            release.wait(2)
            return "late transcript"
        self.app.whisper_engine.transcribe = slow
        with patch("main_linux.hypr_json", return_value={}), patch("main_linux.capture_target", return_value=None), patch("ghost_jobs.add_history") as history:
            self.assertTrue(self.app.on_hotkey_trigger()["active"])
            self.assertTrue(self.app.on_hotkey_trigger()["processing"])
            self.assertTrue(started.wait(1))
            self.assertTrue(self.app.ui_manager.jobs)
            result = self.app.on_hotkey_trigger()
            self.assertFalse(result["active"])
            self.assertFalse(result["processing"])
            self.assertEqual(self.app.audio_recorder.starts, 1)
            self.assertEqual(self.app.audio_recorder.stops, 1)
            self.assertEqual(self.app.ui_manager.jobs, [])
            self.assertFalse(self.app.ui_manager.visible)
            release.set()
            time.sleep(.08)
            history.assert_not_called()
            self.assertEqual(self.app.pasted, [])

    def test_idle_hotkey_starts_again_after_cancel(self):
        with patch("main_linux.hypr_json", return_value={}), patch("main_linux.capture_target", return_value=None):
            self.app._ghost.enqueue(np.ones(16000, dtype=np.float32))
            self.app.on_hotkey_trigger()  # dismiss
            self.assertEqual(self.app.audio_recorder.starts, 0)
            result = self.app.on_hotkey_trigger()
            self.assertTrue(result["active"])
            self.assertEqual(self.app.audio_recorder.starts, 1)

    def test_ipc_stop_dismisses_background_job(self):
        started, release = threading.Event(), threading.Event()
        def slow(audio):
            started.set()
            release.wait(2)
            return "late transcript"
        self.app.whisper_engine.transcribe = slow
        with patch("ghost_jobs.add_history") as history:
            job = self.app._ghost.enqueue(np.ones(16000, dtype=np.float32))
            self.assertTrue(started.wait(1))
            self.assertEqual(self.app._handle_control_command("stop")["pending"], 0)
            self.assertTrue(job.cancelled)
            self.assertEqual(self.app.ui_manager.jobs, [])
            release.set()
            time.sleep(.08)
            history.assert_not_called()

    def test_explicit_ipc_start_can_record_while_another_job_processes(self):
        started, release = threading.Event(), threading.Event()
        def slow(audio):
            started.set()
            release.wait(2)
            return "late transcript"
        self.app.whisper_engine.transcribe = slow
        with patch("main_linux.hypr_json", return_value={}), patch("main_linux.capture_target", return_value=None):
            self.app._ghost.enqueue(np.ones(16000, dtype=np.float32))
            self.assertTrue(started.wait(1))
            result = self.app._handle_control_command("start")
            self.assertTrue(result["active"])
            self.assertEqual(self.app.audio_recorder.starts, 1)
            release.set()

    def test_cancel_all_floaters_including_ready_and_error(self):
        with patch("ghost_jobs.add_history"):
            ready = self.app._ghost.enqueue(np.ones(16000, dtype=np.float32))
            wait_for(ready, "ready")
            self.assertTrue(self.app._ghost.dismiss_visible())
            self.assertEqual(self.app._ghost.snapshot(), [])
            self.assertEqual(self.app._ghost.counts()["ready"], 0)
            self.assertFalse(self.app._ghost.dismiss_visible())


if __name__ == "__main__":
    unittest.main()
