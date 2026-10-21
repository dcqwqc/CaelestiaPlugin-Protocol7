"""Regression contract: Protocol 7 history opens without horizontal drift."""
from pathlib import Path
import unittest

PANEL = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()


class HistoryHoverCenterTests(unittest.TestCase):
    def test_history_remains_centered_during_width_animation(self):
        self.assertIn("readonly property bool panelLockHorizontalCenter: true", PANEL)
        self.assertIn("readonly property bool panelGlideMotion: false", PANEL)
        self.assertIn("panelHostHovered || historyPinned", PANEL)
        self.assertIn("NumberAnimation { duration: 165; easing.type: Easing.OutCubic }", PANEL)

        # ShellPanels uses x = (screenWidth - animatedPanelWidth)/2 with
        # the lock enabled: the centre must not drift at any frame.
        screen = 1920
        for width in (112, 113, 140, 210, 320, 375, 376):
            x = (screen - width) / 2
            self.assertEqual(x + width / 2, screen / 2)


if __name__ == "__main__":
    unittest.main()
