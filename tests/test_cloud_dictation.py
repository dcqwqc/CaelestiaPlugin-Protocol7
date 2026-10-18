"""Cloud dictation fails closed and never publishes local numeric hallucinations."""
import unittest
from unittest.mock import patch
import numpy as np
from whisper_engine import WhisperEngine
from audio import AudioRecorder

class CloudDictationTests(unittest.TestCase):
    def test_cloud_without_key_never_loads_local(self):
        engine = WhisperEngine({"use_groq": True, "groq_api_key": ""})
        with patch("whisper_engine.WhisperModel", side_effect=AssertionError("local model")):
            self.assertEqual(engine.transcribe(np.ones(16000, dtype=np.float32)), "")

    def test_cloud_live_preview_never_loads_local(self):
        engine = WhisperEngine({"use_groq": True, "groq_api_key": "dummy"})
        with patch("whisper_engine.WhisperModel", side_effect=AssertionError("local model")):
            self.assertEqual(engine.transcribe(np.ones(16000, dtype=np.float32), live=True), "")

    def test_cloud_failure_does_not_run_local(self):
        engine = WhisperEngine({"use_groq": True, "groq_api_key": "dummy"})
        class FakeTranscription:
            def create(self, **kwargs):
                raise RuntimeError("test simulated network failure")
        class FakeClient:
            def __init__(self, **kwargs):
                self.audio = type("Audio", (), {"transcriptions": FakeTranscription()})()
        with patch("whisper_engine.Groq", FakeClient), patch("whisper_engine.WhisperModel", side_effect=AssertionError("local model")):
            self.assertEqual(engine.transcribe(np.ones(16000, dtype=np.float32)), "")

    def test_partial_48khz_resampled_to_16khz(self):
        recorder = AudioRecorder()
        recorder.actual_sample_rate = 48000
        recorder._recording_chunks = [np.ones(48000, dtype=np.float32)]
        output = recorder.get_recording_snapshot()
        self.assertEqual(len(output), 16000)
        self.assertAlmostEqual(float(output.mean()), 1.0, places=4)
