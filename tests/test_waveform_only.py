"""The native Protocol 7 panel is a waveform-only HUD in every state."""
from pathlib import Path
import unittest


class WaveformOnlyTests(unittest.TestCase):
    def test_no_hover_menu_tooltip_or_text_label(self):
        source = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        for blocked in ("ToolTip", 'text: "Working"', 'text: "Transcribing',
                        'text: "Processing"', 'text: "Retry"'):
            self.assertNotIn(blocked, source)

    def test_both_states_render_full_centered_wave(self):
        source = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        self.assertIn("anchors.horizontalCenter: parent.horizontalCenter", source)
        self.assertIn("model: root.waitingToSend ? 15 : (root.panelDetached ? 13 : 9)", source)
        self.assertIn("height: root.panelDetached ? 30 : 20", source)

    def test_processing_never_grabs_pointer(self):
        source = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        self.assertIn(
            'readonly property bool panelInputEnabled: !panelDetached || String(ghostJob?.status ?? "") === "ready"',
            source,
        )

    def test_hover_history_only_recording(self):
        source = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        self.assertIn("readonly property bool expanded: !panelDetached && panelVisible && (panelHostHovered || historyPinned)", source)
        self.assertIn("id: historyPane", source)
        self.assertIn("visible: root.expanded", source)
        self.assertIn('text: "Dictation history"', source)

    def test_send_queued_wave_is_organic_not_high_frequency_spikes(self):
        source = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        self.assertIn("root.waitingToSend ? 0.38", source)
        self.assertIn("Math.sin(root.phase * 0.34", source)
        self.assertIn("root.waitingToSend ? 65 : 26", source)
        self.assertNotIn("Math.pow(Math.max(0, Math.sin(root.phase + index * 1.08)), 2.5)", source)
