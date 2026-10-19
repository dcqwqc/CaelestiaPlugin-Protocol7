"""Regressions for slow or missed double-Control dictation hotkeys."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evdev import ecodes
from hotkey_linux import HotkeyListener


class FakeKeyboard:
    def __init__(self, events):
        self.events = events

    def read_loop(self):
        yield from self.events


def control_events():
    return [SimpleNamespace(type=ecodes.EV_KEY, code=ecodes.KEY_LEFTCTRL, value=v)
            for v in (1, 0, 1, 0)]


class DoubleControlTests(unittest.TestCase):
    def test_normal_deliberate_double_tap_is_accepted(self):
        triggered = []
        listener = HotkeyListener([29, 29], lambda: triggered.append(True))
        listener.running = True
        # Release-to-release gap is 0.42 sec; the old 0.28s limit missed it.
        with patch("hotkey_linux.time.monotonic", side_effect=[1.00, 1.08, 1.43, 1.50]):
            listener._listen_device(FakeKeyboard(control_events()))
        self.assertEqual(triggered, [True])

    def test_held_control_does_not_activate_dictation(self):
        triggered = []
        listener = HotkeyListener([29, 29], lambda: triggered.append(True))
        listener.running = True
        with patch("hotkey_linux.time.monotonic", side_effect=[1.0, 1.6, 1.76, 1.82]):
            listener._listen_device(FakeKeyboard(control_events()))
        self.assertEqual(triggered, [])


if __name__ == "__main__":
    unittest.main()
