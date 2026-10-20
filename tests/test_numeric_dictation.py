"""Regression tests for cloud number hallucinations and safe literal injection."""
import unittest
from unittest.mock import patch
import numpy as np
from whisper_engine import WhisperEngine


class NumericDictationTests(unittest.TestCase):
    def setUp(self):
        self.engine = WhisperEngine({"use_groq": True})
        self.audio = np.zeros(16000 * 2, dtype=np.float32)

    def test_silent_numeric_hallucination_is_rejected(self):
        self.assertTrue(self.engine._suspicious_numeric_hallucination("927. 1234", self.audio))

    def test_silent_nonnumeric_phrase_is_not_changed(self):
        self.assertFalse(self.engine._suspicious_numeric_hallucination("Please start the timer.", self.audio))

    def test_real_spoken_numbers_are_preserved(self):
        with patch('faster_whisper.vad.get_speech_timestamps', return_value=[{'start': 0, 'end': 16000}]):
            self.assertFalse(self.engine._suspicious_numeric_hallucination("2026-10-21", self.audio))

    def test_vad_failure_preserves_text(self):
        with patch('faster_whisper.vad.get_speech_timestamps', side_effect=RuntimeError('unavailable')):
            self.assertFalse(self.engine._suspicious_numeric_hallucination("12345", self.audio))

    def test_integer_transcription_no_voice_does_not_reach_editor(self):
        fake_groq = type('Client', (), {'audio': type('Audio', (), {'transcriptions': type('Transcription', (), {'create': lambda *args, **kwargs: type('R', (), {'text':'123 456'})()})()})()})()
        self.engine.groq_client = fake_groq
        with patch.object(self.engine, '_load_model'):
            self.assertEqual(self.engine.transcribe(self.audio), '')


if __name__ == '__main__':
    unittest.main()
