"""Protocol7: recording is native; transcribing is an independent Wayland wave."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / "Panel.qml"
FLOATING = ROOT / "entry/FloatingWave.qml"
CONTROLLER = ROOT / "entry/Main.qml"


class WaveformOnlyTests(unittest.TestCase):
    def test_recording_remains_in_shell_with_history_hover(self):
        panel = RECORDING.read_text()
        self.assertIn("readonly property bool panelDetached: false", panel)
        self.assertIn("readonly property bool panelVisible: P7.ProtocolState.visible", panel)
        self.assertIn("readonly property int panelMotionDuration: 0", panel)
        self.assertIn("readonly property bool panelGlideMotion: false", panel)
        self.assertIn("id: historyPane", panel)
        self.assertIn("visible: root.expanded", panel)
        self.assertIn('text: "Dictation history"', panel)

    def test_floating_is_loaded_once_from_custom_entry(self):
        root = CONTROLLER.read_text()
        self.assertEqual(root.count("FloatingWave { id: floatingWave }"), 1)
        self.assertIn("ghostOverlayAttached=", root)
        self.assertIn("ghostOverlayActive=", root)

    def test_dedicated_overlay_bypasses_bottom_edge_mask(self):
        floating = FLOATING.read_text()
        self.assertIn("PanelWindow {", floating)
        self.assertIn("WlrLayershell.layer: WlrLayer.Overlay", floating)
        self.assertIn("exclusionMode: ExclusionMode.Ignore", floating)
        self.assertIn("WlrLayershell.keyboardFocus: WlrKeyboardFocus.None", floating)
        self.assertIn('namespace: "protocol7-ghost-wave"', floating)
        self.assertIn("anchors.top: true", floating)
        self.assertIn("anchors.left: true", floating)
        self.assertNotIn("offsetScale", floating)
        self.assertNotIn("glideProgress", floating)
        self.assertNotIn("Easing.InOutSine", floating)

    def test_no_movement_animation_or_reposition_during_fadeout(self):
        floating = FLOATING.read_text()
        self.assertIn("readonly property real localX: lockedX", floating)
        self.assertIn("readonly property real localY: lockedY", floating)
        self.assertIn("if (onMonitor && currentId !==", floating)
        self.assertIn("lockedX = Number(root.ghostJob?.target?.x", floating)
        self.assertIn("opacity: win.revealed ? 1 : 0", floating)
        self.assertIn("scale: win.revealed ? 1 : 0.91", floating)
        self.assertIn("duration: 180", floating)
        self.assertIn("duration: 220", floating)
        self.assertNotIn("Behavior on x", floating)
        self.assertNotIn("Behavior on y", floating)

    def test_clickthrough_until_result_needs_action(self):
        floating = FLOATING.read_text()
        self.assertIn("mask: Region {", floating)
        self.assertIn("width: win.canAct ? 112 : 0", floating)
        self.assertIn("height: win.canAct ? 42 : 0", floating)
        self.assertIn('String(root.ghostJob?.status ?? "") === "ready"', floating)
        self.assertIn('ghostJob?.retryable === true', floating)

    def test_waveform_always_without_status_text(self):
        floating = FLOATING.read_text()
        for blocked in ("ToolTip", 'text: "Working"', 'text: "Retry"',
                        'text: "Transcribing', "historyPane"):
            self.assertNotIn(blocked, floating)
        self.assertIn("model: root.waitingToSend ? 15 : 13", floating)
        self.assertIn("readonly property real queuedWave", floating)
        self.assertIn("Colours.palette.m3error", floating)

    def test_send_queue_and_history_remain_in_separate_components(self):
        floating = FLOATING.read_text()
        native = RECORDING.read_text()
        self.assertIn("ghostJob?.sendQueued === true", floating)
        self.assertIn("id: action", floating)
        self.assertIn("action.mode", floating)
        self.assertIn("id: pasteHistory", native)
        self.assertIn("root.selectedHistoryTimestamp", native)


if __name__ == "__main__":
    unittest.main()
