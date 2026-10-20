"""Cloud STT must have bounded latency and no hidden retry backoff."""
import unittest
from unittest.mock import patch


class GroqLatencyTests(unittest.TestCase):
    def test_groq_request_has_bounded_timeout_and_no_sdk_retries(self):
        from whisper_engine import WhisperEngine
        with patch("whisper_engine.Groq") as sdk:
            engine = WhisperEngine({"use_groq": True, "groq_api_key": "test-key"})
            engine._load_model()
            sdk.assert_called_once()
            self.assertEqual(sdk.call_args.kwargs["timeout"], 10.0)
            self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)

    def test_explicit_timeout_preference_still_respected(self):
        from whisper_engine import WhisperEngine
        with patch("whisper_engine.Groq") as sdk:
            engine = WhisperEngine({"use_groq": True, "groq_api_key": "test-key",
                                    "groq_timeout_seconds": 7.0})
            engine._load_model()
            self.assertEqual(sdk.call_args.kwargs["timeout"], 7.0)


if __name__ == "__main__":
    unittest.main()
