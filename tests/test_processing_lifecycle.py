import sys
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_linux import Protocol7App


class FakeRecorder:
    is_recording = False

    def start_recording(self):
        self.is_recording = True

    def stop_recording(self):
        import numpy as np
        self.is_recording = False
        return np.ones(16000, dtype="float32") * 0.05


class FakeUI:
    def __init__(self):
        self.events = []

    def show(self): self.events.append("show")
    def hide(self): self.events.append("hide")
    def set_processing_state(self): self.events.append("processing")


class ProcessingTests(unittest.TestCase):
    def setup_app(self):
        app = Protocol7App.__new__(Protocol7App)
        app.is_active = False
        app.is_processing = False
        app.cancel_requested = False
        app._dictation_generation = 0
        app._dictation_lock = threading.RLock()
        app._live_stop = threading.Event()
        app._live_thread = None
        app.processing_timeout_seconds = 0.06
        app.audio_recorder = FakeRecorder()
        app.ui_manager = FakeUI()
        app.llm_rewriter = type("Rewriter", (), {"rewrite": lambda self, x: x})()
        app.pasted = []
        app.paste_text = lambda text: app.pasted.append(text)
        return app

    def test_stalled_transcription_recovers_and_suppresses_late_text(self):
        app = self.setup_app()
        blocking = threading.Event()
        entered = threading.Event()
        def transcribe(audio):
            entered.set()
            blocking.wait(0.35)
            return "should never paste"
        app.whisper_engine = type("Engine", (), {"transcribe": lambda self, a: transcribe(a)})()
        self.assertTrue(app._toggle_dictation()["active"])
        self.assertTrue(app._toggle_dictation()["processing"])
        self.assertTrue(entered.wait(1))
        time.sleep(0.12)
        self.assertFalse(app.is_processing)
        self.assertEqual(app.ui_manager.events[-1], "hide")
        blocking.set()
        time.sleep(0.08)
        self.assertEqual(app.pasted, [])
        self.assertTrue(app._toggle_dictation()["active"])
        self.assertEqual(app.ui_manager.events[-1], "show")

    def test_recording_failure_does_not_show_spinner(self):
        app = self.setup_app()
        app.audio_recorder.start_recording = lambda: None
        result = app._toggle_dictation()
        self.assertFalse(result["ok"])
        self.assertFalse(result["active"])
        self.assertFalse(result["processing"])


if __name__ == "__main__":
    unittest.main()
