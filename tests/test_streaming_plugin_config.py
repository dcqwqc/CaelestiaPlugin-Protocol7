"""Contract checks for opt-in live streaming and native shell wiring."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StreamingPluginConfigTests(unittest.TestCase):
    def test_native_config_bridge_forwards_nonsecret_settings(self):
        source = (ROOT / "entry/Main.qml").read_text()
        for name in ("streaming_stt_enabled: settings.streamingSttEnabled",
                     "streaming_stt_provider: settings.streamingSttProvider",
                     "streaming_stt_model: settings.streamingSttModel",
                     "onStreamingSttEnabledChanged",
                     "onStreamingSttProviderChanged",
                     "onStreamingSttModelChanged"):
            self.assertIn(name, source)
        self.assertNotIn("streaming_stt_api_key: settings.", source)
        self.assertNotIn("streaming_stt_billing_authorized: settings.", source)

    def test_native_hover_panel_shows_received_live_text_without_expanding_pill(self):
        state = (ROOT / "services/ProtocolState.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()
        self.assertIn('liveText = String(message.liveText ?? "")', state)
        self.assertIn('text: P7.ProtocolState.liveText', panel)
        self.assertIn('visible: P7.ProtocolState.liveText.length > 0', panel)
        self.assertIn('implicitWidth: panelDetached ? 112 : (expanded ? 376 : 112)', panel)
        self.assertIn('implicitHeight: panelDetached ? 42 : (expanded ? 318 : 32)', panel)

    def test_billing_and_credential_not_exposed_in_settings(self):
        settings = (ROOT / "Settings.qml").read_text()
        self.assertNotIn('property string streamingSttApiKey', settings)
        from config import DEFAULT_CONFIG
        self.assertIs(DEFAULT_CONFIG['streaming_stt_enabled'], False)
        self.assertIs(DEFAULT_CONFIG['streaming_stt_billing_authorized'], False)

    def test_live_model_uses_plural_language_hints_and_manual_turn(self):
        import sys
        sys.path.insert(0, str(ROOT))
        import streaming_stt as st
        session = st.StreamingSession(st.StreamingSettings(api_key='dummy', language='de'))
        settings = session._session_update()['session']['audio']['input']
        self.assertEqual(settings['transcription']['languages'], ['de'])
        self.assertNotIn('language', settings['transcription'])
        self.assertIsNone(settings['turn_detection'])


if __name__ == '__main__':
    unittest.main()
