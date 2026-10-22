"""Contract checks for the opt-in Groq live preview and native shell wiring."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROPERTY = re.compile(r"^\s*property\s+(\w+)\s+(\w+)\s*:\s*(.+?)\s*$", re.M)
META = re.compile(r"SettingMeta on (\w+) \{(.*?)\n    \}", re.S)
# Value types each SettingMeta input type can edit without coercion.
INPUT_TYPES = {"Switch": {"bool"}, "SpinBox": {"int", "real"},
               "SplitButton": {"string"}, "TextField": {"string"}}


def settings_properties():
    text = (ROOT / "Settings.qml").read_text()
    return {name: (kind, value) for kind, name, value in PROPERTY.findall(text)}, text


class StreamingPluginConfigTests(unittest.TestCase):
    def test_native_config_bridge_forwards_only_preview_switch_and_provider(self):
        source = (ROOT / "entry/Main.qml").read_text()
        for name in ("streaming_stt_enabled: settings.streamingSttEnabled",
                     "streaming_stt_provider: settings.streamingSttProvider",
                     "onStreamingSttEnabledChanged",
                     "onStreamingSttProviderChanged",
                     "groq_api_key: settings.groqApiKey"):
            self.assertIn(name, source)
        for removed in ("streaming_stt_model", "streamingSttModel", "streaming_stt_api_key",
                        "streaming_stt_billing_authorized"):
            self.assertNotIn(removed, source)

    def test_native_hover_panel_shows_received_live_text_without_expanding_pill(self):
        state = (ROOT / "services/ProtocolState.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()
        self.assertIn('liveText = String(message.liveText ?? "")', state)
        self.assertIn('text: P7.ProtocolState.liveText', panel)
        self.assertIn('visible: P7.ProtocolState.liveText.length > 0', panel)
        self.assertIn('implicitWidth: panelDetached ? 112 : (expanded ? 376 : 112)', panel)
        self.assertIn('implicitHeight: panelDetached ? 42 : (expanded ? 318 : 32)', panel)

    def test_preview_settings_are_relevant_and_default_off(self):
        props, text = settings_properties()
        self.assertEqual(props["streamingSttEnabled"], ("bool", "false"))
        self.assertEqual(props["streamingSttProvider"], ("string", '"groq-chunks"'))
        meta = dict(META.findall(text))["streamingSttProvider"]
        self.assertIn("SettingMeta.SplitButton", meta)
        self.assertIn('options: ["groq-chunks"]', meta)
        for removed in ("streamingSttApiKey", "streamingSttModel", "streamingSttBillingAuthorized"):
            self.assertNotIn(removed, props)
        from config import DEFAULT_CONFIG
        self.assertIs(DEFAULT_CONFIG["streaming_stt_enabled"], False)
        self.assertEqual(DEFAULT_CONFIG["streaming_stt_provider"], "groq-chunks")
        for removed in ("streaming_stt_api_key", "streaming_stt_billing_authorized", "streaming_stt_model"):
            self.assertNotIn(removed, DEFAULT_CONFIG)

    def test_setting_meta_input_types_match_property_types(self):
        props, text = settings_properties()
        metas = META.findall(text)
        self.assertGreater(len(metas), 10)
        for name, body in metas:
            kind = props[name][0]
            input_type = re.search(r"inputType: SettingMeta\.(\w+)", body).group(1)
            self.assertIn(kind, INPUT_TYPES[input_type], f"{name}: {kind} edited by {input_type}")
            options = re.search(r"options: \[(.*?)\]", body)
            if input_type == "SplitButton":
                self.assertIsNotNone(options, name)
                values = re.findall(r'"([^"]*)"', options.group(1))
                self.assertIn(props[name][1].strip('"'), values, f"{name} default not an option")

    def test_main_qml_references_only_existing_settings(self):
        props, _ = settings_properties()
        source = (ROOT / "entry/Main.qml").read_text()
        for name in set(re.findall(r"settings\.(\w+)", source)):
            self.assertIn(name, props, f"Main.qml reads missing setting {name}")
        for handler in re.findall(r"function on(\w+)Changed\(\)", source):
            name = handler[0].lower() + handler[1:]
            self.assertIn(name, props, f"Main.qml handles missing setting {name}")

    def test_language_hint_only_when_auto_detect_is_off(self):
        import streaming_stt as st
        self.assertEqual(st.settings_from_config({"auto_detect_language": True, "language": "de"}).language, "")
        self.assertEqual(st.settings_from_config({"auto_detect_language": False, "language": "de"}).language, "de")


if __name__ == "__main__":
    unittest.main()
