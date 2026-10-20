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
        self.assertEqual(root.count("P7Entry.FloatingWave { id: floatingWave }"), 1)
        self.assertIn("import dcqwqc.protocol7.entry as P7Entry", root)
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
        self.assertIn("width: win.canAct ? root.pillWidth : 0", floating)
        self.assertIn("height: win.canAct ? root.pillHeight : 0", floating)
        self.assertIn("x: root.shadowInset", floating)
        self.assertIn("y: root.shadowInset", floating)
        self.assertIn('String(root.ghostJob?.status ?? "") === "ready"', floating)
        self.assertIn('ghostJob?.retryable === true', floating)

    def test_waveform_always_without_status_text(self):
        floating = FLOATING.read_text()
        for blocked in ("ToolTip", 'text: "Working"', 'text: "Retry"',
                        'text: "Transcribing', "historyPane"):
            self.assertNotIn(blocked, floating)
        self.assertIn("model: root.waitingToSend ? 15 : 13", floating)
        self.assertIn("readonly property real rollingWave", floating)
        self.assertIn("readonly property real waveDrift", floating)
        self.assertIn("Colours.palette.m3error", floating)

    def test_small_organic_wave_and_native_caelestia_elevation(self):
        floating = FLOATING.read_text()
        elevation = (Path.home() / ".config/quickshell/caelestia/components/effects/Elevation.qml").read_text()

        # Smaller actual pill, centred inside symmetric transparent margins:
        # changing size must not alter target centre or click-through status.
        self.assertIn("readonly property int pillWidth: 102", floating)
        self.assertIn("readonly property int pillHeight: 38", floating)
        self.assertIn("readonly property int shadowInset: 16", floating)
        self.assertIn("implicitWidth: root.pillWidth + root.shadowInset * 2", floating)
        self.assertIn("implicitHeight: root.pillHeight + root.shadowInset * 2", floating)
        self.assertIn("anchors.centerIn: parent", floating)

        # Actual shell Elevation, not a pretend dark outer rectangle.
        self.assertIn("import qs.components.effects", floating)
        self.assertIn("Elevation {", floating)
        self.assertIn("anchors.fill: surface", floating)
        self.assertIn("radius: surface.radius", floating)
        self.assertIn("level: 3", floating)
        self.assertIn("Qt.alpha(Colours.palette.m3outlineVariant, 0.22)", floating)
        self.assertIn("RectangularShadow {", elevation)
        self.assertIn("Colours.palette.m3shadow", elevation)
        self.assertNotIn('color: "#1a000000"', floating)

        # Bar texture and outline are smooth and subtle, not moving the overlay.
        self.assertIn("0.7 * Math.sin(root.phase * 0.21)", floating)
        self.assertIn("rollingWave *", floating)
        self.assertIn("root.phase += root.waitingToSend ? 0.31 : 0.24", floating)
        self.assertIn("width: 3", floating)
        self.assertIn("height: 26", floating)
        self.assertNotIn("Behavior on x", floating)
        self.assertNotIn("Behavior on y", floating)

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
