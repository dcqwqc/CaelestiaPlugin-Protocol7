import os
import stat
import tempfile
import threading
import unittest

from companion.ipc import CompanionIPCServer, send_command, socket_path
from companion.state import CompanionStatePublisher, initial_state, validate_command
from companion.wake_word import normalize_text, wake_match_score


class WakeWordTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_text("Hey, TABBY!"), "hey tabby")

    def test_exact_and_alias_match(self):
        self.assertEqual(wake_match_score("hey tabby"), 1.0)
        self.assertEqual(wake_match_score("Hey Tabi"), 1.0)
        self.assertEqual(wake_match_score("okay hey tabby can you open this"), 1.0)

    def test_unrelated_speech_is_rejected(self):
        self.assertLess(wake_match_score("maybe happy today"), 0.84)
        self.assertLess(wake_match_score("tabby"), 0.84)

    def test_uses_injected_protocol7_transcriber(self):
        import numpy as np
        from companion.wake_word import WakeWordDetector

        class FakeTranscriber:
            def __init__(self):
                self.calls = []

            def transcribe(self, audio, live=False, allow_local_fallback=True):
                self.calls.append((len(audio), live, allow_local_fallback))
                return "Hey Tabby"

        backend = FakeTranscriber()
        detector = WakeWordDetector(
            {"companion_wake_enabled": True},
            lambda text, score: None,
            transcriber=backend,
        )
        text = detector._transcribe(np.zeros(16000, dtype=np.float32))
        self.assertEqual(text, "Hey Tabby")
        self.assertEqual(backend.calls, [(16000, False, False)])


class CompanionVisibilityTests(unittest.TestCase):
    def test_companion_starts_unsummoned(self):
        self.assertFalse(initial_state()["summoned"])

    def test_summoned_latch_is_separate_from_state(self):
        state = CompanionStatePublisher(enabled=True)
        state.set_state("success")
        self.assertFalse(state.snapshot()["summoned"])
        state.set_summoned(True)
        self.assertTrue(state.snapshot()["summoned"])


class CompanionLifecycleTests(unittest.TestCase):
    class FakeState:
        def __init__(self, summoned=True):
            self.summoned = summoned

        def snapshot(self):
            return {"summoned": self.summoned, "state": "approval"}

        def command(self, command):
            return {"ok": True}

        def set_state(self, value):
            pass

    def test_idle_timeout_dismisses_stalled_companion(self):
        from companion.runtime import CompanionRuntime
        runtime = CompanionRuntime.__new__(CompanionRuntime)
        runtime._voice_active = False
        runtime._activation_in_progress = False
        runtime._hide_timer = None
        runtime.state = self.FakeState(True)
        dismissed = []
        runtime._return_idle = lambda: dismissed.append(True)
        runtime._idle_timeout()
        self.assertEqual(dismissed, [True])

    def test_idle_timeout_waits_for_voice_startup(self):
        from companion.runtime import CompanionRuntime
        runtime = CompanionRuntime.__new__(CompanionRuntime)
        runtime._voice_active = False
        runtime._activation_in_progress = True
        runtime._hide_timer = None
        runtime.state = self.FakeState(True)
        delays = []
        runtime._schedule_idle = lambda delay=3.0: delays.append(delay)
        runtime._idle_timeout()
        self.assertEqual(delays, [3.0])

    def test_closing_visible_login_surface_dismisses_tabby(self):
        from companion.runtime import CompanionRuntime

        class FakeVoice:
            def __init__(self):
                self.calls = 0

            def status(self):
                self.calls += 1
                if self.calls == 1:
                    return {"phase": "needs-login", "setupVisible": True, "loginVisible": True}
                return {"phase": "needs-login", "setupVisible": False, "loginVisible": False}

        runtime = CompanionRuntime.__new__(CompanionRuntime)
        runtime._monitor_stop = threading.Event()
        runtime._voice_active = False
        runtime.inactivity_timeout = 8.0
        runtime.state = self.FakeState(True)
        runtime.voice = FakeVoice()
        dismissed = []
        runtime._return_idle = lambda: dismissed.append(True)
        runtime._monitor_login()
        self.assertEqual(dismissed, [True])

    def test_successful_setup_closes_tabby_without_starting_voice(self):
        from companion.runtime import CompanionRuntime

        class FakeVoice:
            def __init__(self):
                self.hidden = 0
                self.activated = 0

            def status(self):
                return {"phase": "ready", "setupVisible": True, "loginVisible": True}

            def hide(self):
                self.hidden += 1

            def activate(self):
                self.activated += 1
                return {"ok": True, "phase": "active"}

        runtime = CompanionRuntime.__new__(CompanionRuntime)
        runtime._monitor_stop = threading.Event()
        runtime._voice_active = False
        runtime.inactivity_timeout = 8.0
        runtime.state = self.FakeState(True)
        runtime.voice = FakeVoice()
        dismissed = []
        runtime._return_idle = lambda: dismissed.append(True)
        runtime._monitor_login()
        self.assertEqual(dismissed, [True])
        self.assertEqual(runtime.voice.hidden, 1)
        self.assertEqual(runtime.voice.activated, 0)

    def test_inactive_voice_auto_hides_after_grace_period(self):
        from companion.runtime import CompanionRuntime

        class FakeVoice:
            def status(self):
                return {"phase": "ready", "active": False}

        runtime = CompanionRuntime.__new__(CompanionRuntime)
        runtime._monitor_stop = threading.Event()
        runtime._voice_active = True
        runtime.inactivity_timeout = 0.05
        runtime.voice = FakeVoice()
        dismissed = []
        runtime._return_idle = lambda: dismissed.append(True)
        runtime._monitor_voice()
        self.assertEqual(dismissed, [True])



class CommandValidationTests(unittest.TestCase):
    def test_rejects_unknown_commands(self):
        with self.assertRaises(ValueError):
            validate_command({"command": "shell", "cmd": "rm -rf /"})

    def test_bounds_shape_coordinates(self):
        clean = validate_command({"command": "shape", "kind": "rect", "x": -1, "y": 2, "w": 4, "h": -2})
        self.assertEqual(clean["x"], 0.0)
        self.assertEqual(clean["y"], 1.0)
        self.assertEqual(clean["w"], 1.0)
        self.assertEqual(clean["h"], 0.0)

    def test_text_is_bounded(self):
        clean = validate_command({"command": "text", "text": "x" * 800})
        self.assertEqual(len(clean["text"]), 500)


class IPCTests(unittest.TestCase):
    def test_local_socket_roundtrip_and_permissions(self):
        previous = os.environ.get("XDG_RUNTIME_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_RUNTIME_DIR"] = tmp
            state = CompanionStatePublisher(enabled=True)
            server = CompanionIPCServer(state.command)
            server.start()
            try:
                mode = stat.S_IMODE(os.stat(socket_path()).st_mode)
                self.assertEqual(mode, 0o600)
                result = send_command({"command": "text", "text": "hello"})
                self.assertTrue(result["ok"])
                self.assertTrue(result["state"]["whiteboardVisible"])
                self.assertEqual(result["state"]["items"][-1]["text"], "hello")
                rejected = send_command({"command": "shell", "cmd": "whoami"})
                self.assertFalse(rejected["ok"])
            finally:
                server.stop()
                if previous is None:
                    os.environ.pop("XDG_RUNTIME_DIR", None)
                else:
                    os.environ["XDG_RUNTIME_DIR"] = previous




class MCPToolSurfaceTests(unittest.TestCase):
    def test_expected_tools_are_registered(self):
        import asyncio
        from companion.mcp_server import mcp
        names = {tool.name for tool in asyncio.run(mcp.list_tools())}
        self.assertEqual(names, {
            "companion_set_state",
            "whiteboard_show",
            "whiteboard_hide",
            "whiteboard_clear",
            "whiteboard_write",
            "whiteboard_progress",
            "whiteboard_choice",
            "whiteboard_shape",
        })


if __name__ == "__main__":
    unittest.main()
