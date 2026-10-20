"""Hyprland temporary Return ownership, cleanup, and exception guards."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

from enter_intercept import EnterInterceptor, no_existing_unmodified_return, unmodified_return_installed


class Result:
    returncode = 0
    def __init__(self, stdout): self.stdout = stdout


class EnterInterceptTests(unittest.TestCase):
    def test_foreign_plain_return_prevents_registration(self):
        bindings = [
            {"modmask": 0, "key": "Return", "dispatcher": "__lua"},
            {"modmask": 64, "key": "Return", "dispatcher": "__lua"},
        ]
        with patch("enter_intercept.run_hypr", return_value=Result(json.dumps(bindings))):
            self.assertFalse(no_existing_unmodified_return())
        bindings.pop(0)
        with patch("enter_intercept.run_hypr", return_value=Result(json.dumps(bindings))):
            self.assertTrue(no_existing_unmodified_return())

    def test_arm_and_disarm_use_lua_and_private_owner_file(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict("enter_intercept.os.environ", {"XDG_RUNTIME_DIR": temp}):
                with patch("enter_intercept.no_existing_unmodified_return", return_value=True),                      patch("enter_intercept.lua_eval", return_value=True) as eval_lua,                      patch("enter_intercept.unmodified_return_installed", return_value=True),                      patch("enter_intercept.subprocess.Popen") as worker:
                    enter = EnterInterceptor()
                    self.assertTrue(enter.arm())
                    self.assertTrue(enter.armed)
                    self.assertTrue(enter.owner.exists())
                    self.assertEqual(enter.owner.stat().st_mode & 0o777, 0o600)
                    self.assertIn('hl.bind("Return",', eval_lua.call_args_list[0].args[0])
                    self.assertTrue(enter.disarm())
                    self.assertFalse(enter.owner.exists())
                    self.assertFalse(enter.armed)
                    self.assertEqual(eval_lua.call_args_list[-1].args[0], 'hl.unbind("Return")')
                    worker.assert_called_once()

    def test_bind_activation_must_be_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict("enter_intercept.os.environ", {"XDG_RUNTIME_DIR": temp}):
                with patch("enter_intercept.no_existing_unmodified_return", return_value=True),                      patch("enter_intercept.lua_eval", return_value=True) as evaluate,                      patch("enter_intercept.unmodified_return_installed", return_value=False):
                    hook = EnterInterceptor()
                    self.assertFalse(hook.arm())
                    self.assertFalse(hook.armed)
                    self.assertFalse(hook.owner.exists())
                    self.assertEqual(evaluate.call_args_list[-1].args[0], 'hl.unbind("Return")')

    def test_bind_lookup_verifies_dispatcher(self):
        bindings = [{"modmask": 0, "key": "Return", "dispatcher": "__lua"}]
        with patch("enter_intercept.run_hypr", return_value=Result(json.dumps(bindings))):
            self.assertTrue(unmodified_return_installed())
        bindings[0]["dispatcher"] = "pass"
        with patch("enter_intercept.run_hypr", return_value=Result(json.dumps(bindings))):
            self.assertFalse(unmodified_return_installed())

    def test_failed_disarm_never_synthesizes_enter(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict("enter_intercept.os.environ", {"XDG_RUNTIME_DIR": temp}):
                with patch("enter_intercept.no_existing_unmodified_return", return_value=True),                      patch("enter_intercept.lua_eval", side_effect=[True, False]),                      patch("enter_intercept.unmodified_return_installed", return_value=True),                      patch("enter_intercept.subprocess.Popen") as spawn:
                    hook = EnterInterceptor()
                    self.assertTrue(hook.arm())
                    self.assertFalse(hook.forward_return())
                    self.assertTrue(hook.armed)
                    spawn.assert_called_once()  # watchdog only, not wtype


if __name__ == "__main__":
    unittest.main()
