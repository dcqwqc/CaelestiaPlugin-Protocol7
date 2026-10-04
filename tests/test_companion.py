import os
import stat
import tempfile
import unittest

from companion.ipc import CompanionIPCServer, send_command, socket_path
from companion.state import CompanionStatePublisher, validate_command
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



class BrowserBridgeTests(unittest.TestCase):
    def test_disabled_bridge_never_acts(self):
        from companion.browser_bridge import BrowserBridge
        bridge = BrowserBridge({"companion_browser_bridge_enabled": False})
        result = bridge.activate()
        self.assertFalse(result["ok"])


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
