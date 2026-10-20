"""Temporary, crash-recoverable Hyprland Return interception during dictation.

Only installed when Protocol7 has an active queued/processing Ghost Wave.
The hook routes physical Return to a local 0600 IPC socket. A detached
watchdog releases the keybinding if the backend unexpectedly dies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time


def run_hypr(*args):
    try:
        return subprocess.run(
            ["hyprctl", *args], capture_output=True, text=True,
            timeout=1.5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def lua_eval(script: str) -> bool:
    result = run_hypr("eval", script)
    return result is not None and result.returncode == 0 and result.stdout.strip() == "ok"


def no_existing_unmodified_return() -> bool:
    result = run_hypr("binds", "-j")
    if result is None or result.returncode:
        return False
    try:
        bindings = json.loads(result.stdout)
        return not any(
            item.get("modmask") == 0
            and str(item.get("key", "")).casefold() in ("return", "enter", "kp_enter")
            for item in bindings
        )
    except (ValueError, TypeError):
        return False


def unmodified_return_installed() -> bool:
    """Confirm the compositor accepted our unmodified Return binding."""
    result = run_hypr("binds", "-j")
    if result is None or result.returncode != 0:
        return False
    try:
        return any(
            item.get("modmask") == 0
            and str(item.get("key", "")).casefold() == "return"
            and item.get("dispatcher") == "__lua"
            for item in json.loads(result.stdout)
        )
    except (ValueError, TypeError):
        return False


class EnterInterceptor:
    def __init__(self):
        self.lock = threading.RLock()
        self.armed = False
        self.token = secrets.token_hex(8)
        self.owner = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "protocol7-return-owner.json"
        # A stale owner implies the old process died. Never remove a binding
        # owned by a *live* foreign process.
        self._cleanup_stale()

    def _cleanup_stale(self):
        try:
            old = json.loads(self.owner.read_text())
            pid = int(old["pid"])
            if pid > 1:
                try:
                    os.kill(pid, 0)
                    return
                except ProcessLookupError:
                    pass
            lua_eval('hl.unbind("Return")')
            self.owner.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def arm(self) -> bool:
        with self.lock:
            if self.armed:
                return True
            if not no_existing_unmodified_return():
                return False
            helper = Path(__file__).with_name("control_ipc.py")
            command = f"{sys.executable} {helper} queue-send"
            script = f'hl.bind("Return", hl.dsp.exec_cmd({json.dumps(command)}))'
            if not lua_eval(script):
                return False
            if not unmodified_return_installed():
                lua_eval('hl.unbind("Return")')
                return False
            try:
                descriptor = os.open(self.owner, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({"pid": os.getpid(), "token": self.token}, stream)
                subprocess.Popen(
                    [sys.executable, str(Path(__file__)), "watch", self.token, str(os.getpid())],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                lua_eval('hl.unbind("Return")')
                self.owner.unlink(missing_ok=True)
                return False
            self.armed = True
            return True

    def disarm(self) -> bool:
        with self.lock:
            if not self.armed:
                return True
            try:
                data = json.loads(self.owner.read_text())
                if data.get("token") != self.token:
                    return False
            except (OSError, ValueError):
                return False
            ok = lua_eval('hl.unbind("Return")')
            if ok:
                self.armed = False
                self.owner.unlink(missing_ok=True)
            # On failure, retain armed=True. Do not report a safe keyboard
            # state while Hyprland is still swallowing physical Returns.
            return ok

    def forward_return(self):
        """Disarm *before* forwarding to another focused app."""
        if not self.disarm():
            return False
        try:
            subprocess.Popen(
                ["wtype", "-k", "Return"], env=os.environ.copy(),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            return True
        except OSError:
            return False


def watchdog(token: str, parent_pid: int):
    owner = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "protocol7-return-owner.json"
    # Keep the crash monitor alive as long as the owning backend is alive;
    # the previous fixed 50-second deadline silently removed Return for an
    # otherwise valid, still processing dictation.
    while True:
        time.sleep(.15)
        try:
            saved = json.loads(owner.read_text())
            if saved.get("token") != token:
                return
        except (OSError, ValueError):
            return
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            break
    try:
        saved = json.loads(owner.read_text())
        if saved.get("token") != token:
            return
        if lua_eval('hl.unbind("Return")'):
            owner.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "watch":
    watchdog(sys.argv[2], int(sys.argv[3]))
