"""Regression tests for SUMI text insertion through Protocol7."""
import unittest
from unittest.mock import patch
from main_linux import Protocol7App


class SumiPasteTests(unittest.TestCase):
    def setUp(self):
        self.app = Protocol7App.__new__(Protocol7App)

    def test_sumi_prefers_uinput(self):
        with patch.object(Protocol7App, "_focused_is_terminal", return_value=False),              patch("shutil.which", return_value="/usr/bin/ydotool"),              patch("os.path.exists", return_value=True):
            self.assertEqual(self.app._active_paste_command(),
                             ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"])

    def test_other_apps_use_stable_hardware_paste(self):
        with patch.object(Protocol7App, "_focused_is_terminal", return_value=False),              patch("shutil.which", return_value="/usr/bin/ydotool"),              patch("os.path.exists", return_value=True):
            self.assertEqual(self.app._active_paste_command(),
                             ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"])

    def test_terminal_uses_control_shift_v(self):
        with patch.object(Protocol7App, "_focused_is_terminal", return_value=True),              patch("shutil.which", return_value="/usr/bin/ydotool"),              patch("os.path.exists", return_value=True):
            self.assertEqual(self.app._active_paste_command(),
                             ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"])

    def test_ydotool_unavailable_refuses_broken_wtype(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ydotoold"):
                self.app._active_paste_command()

    def test_long_codex_transcription_never_uses_clipboard(self):
        long_text = "A detailed Codex prompt that is long enough to trigger " * 12
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=False),              patch.object(Protocol7App, "_fast_clipboard_paste") as clipboard,              patch("main_linux.subprocess.run") as run:
            self.app.paste_text(long_text)
            clipboard.assert_not_called()
            self.assertEqual(run.call_args.args[0],
                ["ydotool", "type", "-d", "3", "-H", "3", "-f", "-"])
            self.assertEqual(run.call_args.kwargs["input"], long_text.encode("utf-8"))
            self.assertGreaterEqual(run.call_args.kwargs["timeout"], 8)

    def test_unicode_routes_to_safe_text_clipboard_instead_of_losing_accents(self):
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=False),              patch.object(Protocol7App, "_fast_clipboard_paste", return_value=True) as clipboard,              patch("main_linux.subprocess.run") as run:
            self.assertTrue(self.app.paste_text("Grüße aus München"))
            clipboard.assert_called_once_with("Grüße aus München", allow_terminal=True)
            run.assert_not_called()

    def test_failed_hardware_injection_does_not_silently_use_wtype(self):
        from subprocess import CalledProcessError
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=False),              patch("main_linux.subprocess.run", side_effect=CalledProcessError(1, 'ydotool')) as run:
            self.assertFalse(self.app.paste_text("This is a test"))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], "ydotool")

    def test_short_sumi_transcription_uses_clipboard(self):
        with patch.object(Protocol7App, "_focused_is_sumi", return_value=True),              patch.object(Protocol7App, "_fast_clipboard_paste", return_value=True) as paste:
            self.app.paste_text("short")
            paste.assert_called_once_with("short")


if __name__ == "__main__":
    unittest.main()
