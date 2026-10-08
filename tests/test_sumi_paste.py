"""Regression tests for SUMI text insertion through Protocol7."""
import unittest
from unittest.mock import patch
from main_linux import Protocol7App


class SumiPasteTests(unittest.TestCase):
    def setUp(self):
        self.app = Protocol7App.__new__(Protocol7App)

    def test_sumi_prefers_uinput(self):
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=True),              patch("shutil.which", return_value="/usr/bin/ydotool"),              patch("os.path.exists", return_value=True):
            self.assertEqual(self.app._active_paste_command(),
                             ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"])

    def test_other_apps_keep_wtype(self):
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=False):
            self.assertEqual(self.app._active_paste_command(),
                             ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"])

    def test_short_sumi_transcription_uses_clipboard(self):
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=True),              patch.object(Protocol7App, "_fast_clipboard_paste", return_value=True) as paste:
            self.app.paste_text("short")
            paste.assert_called_once_with("short")


if __name__ == "__main__":
    unittest.main()
