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

    def test_long_codex_transcription_never_uses_clipboard(self):
        long_text = "A detailed Codex prompt that is long enough to trigger " * 12
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=False),              patch.object(Protocol7App, "_fast_clipboard_paste") as clipboard,              patch("main_linux.subprocess.run") as run:
            self.app.paste_text(long_text)
            clipboard.assert_not_called()
            self.assertEqual(run.call_args.args[0], ["wtype", "--", long_text])
            self.assertGreaterEqual(run.call_args.kwargs["timeout"], 8)

    def test_short_sumi_transcription_uses_clipboard(self):
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=True),              patch.object(Protocol7App, "_fast_clipboard_paste", return_value=True) as paste:
            self.app.paste_text("short")
            paste.assert_called_once_with("short")


if __name__ == "__main__":
    unittest.main()
