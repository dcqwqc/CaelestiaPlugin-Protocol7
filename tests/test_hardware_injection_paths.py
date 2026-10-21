"""Every Protocol7 keyboard path must avoid wtype's corrupted transient keymap."""
import subprocess
import unittest
from unittest.mock import patch

from history_actions import type_text
from main_linux import Protocol7App
from enter_intercept import EnterInterceptor


class HardwareInjectionPathTests(unittest.TestCase):
    def test_history_ascii_uses_ydotool_and_stdin(self):
        with patch('history_actions.subprocess.run') as run:
            run.return_value.returncode = 0
            self.assertTrue(type_text('Replay 123 aBc'))
            self.assertEqual(run.call_args.args[0],
                ['ydotool', 'type', '-d', '3', '-H', '3', '-f', '-'])
            self.assertEqual(run.call_args.kwargs['input'], b'Replay 123 aBc')

    def test_history_unicode_preserves_accents_via_clipboard(self):
        with patch('history_actions.clipboard_paste', return_value=True) as clip, \
             patch('history_actions.subprocess.run') as run:
            self.assertTrue(type_text('Schöne Grüße'))
            clip.assert_called_once_with('Schöne Grüße')
            run.assert_not_called()

    def test_history_failed_hardware_does_not_fall_back_to_wtype(self):
        with patch('history_actions.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, 'ydotool')) as run:
            self.assertFalse(type_text('Replay this'))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], 'ydotool')

    def test_main_send_enter_uses_hardware_keycode(self):
        app = Protocol7App.__new__(Protocol7App)
        with patch('main_linux.subprocess.run') as run:
            run.return_value.returncode = 0
            self.assertTrue(app.send_enter())
            self.assertEqual(run.call_args.args[0], ['ydotool', 'key', '28:1', '28:0'])

    def test_forward_return_uses_hardware_and_reports_failure(self):
        hook = EnterInterceptor.__new__(EnterInterceptor)
        with patch.object(hook, 'disarm', return_value=True), \
             patch('enter_intercept.subprocess.run') as run:
            run.return_value.returncode = 0
            self.assertTrue(hook.forward_return())
            self.assertEqual(run.call_args.args[0], ['ydotool', 'key', '28:1', '28:0'])
            run.return_value.returncode = 1
            self.assertFalse(hook.forward_return())

if __name__ == '__main__':
    unittest.main()
