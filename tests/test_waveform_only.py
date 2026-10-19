"""The native Protocol 7 panel is a waveform-only HUD in every state."""
from pathlib import Path
import unittest


class WaveformOnlyTests(unittest.TestCase):
    def test_no_hover_menu_tooltip_or_text_label(self):
        source = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        for blocked in ("Text {", "ToolTip", "HoverHandler", "historyPane",
                        "panelHostHovered", "expanded", 'text: "Working"',
                        'text: "Transcribing'):
            self.assertNotIn(blocked, source)

    def test_both_states_render_full_centered_wave(self):
        source = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        self.assertIn("anchors.centerIn: parent", source)
        self.assertIn("model: root.panelDetached ? 13 : 9", source)
        self.assertIn("height: root.panelDetached ? 30 : 20", source)

    def test_processing_never_grabs_pointer(self):
        source = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        self.assertIn(
            'readonly property bool panelInputEnabled: panelDetached && String(ghostJob?.status ?? "") === "ready"',
            source,
        )
