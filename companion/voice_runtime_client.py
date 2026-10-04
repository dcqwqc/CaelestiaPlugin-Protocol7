from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

MAX_REPLY = 16384


def socket_path() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "protocol7-chatgpt-voice.sock"


class VoiceRuntimeClient:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._process = None

    def _session_env(self) -> dict:
        env = os.environ.copy()
        if not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "show-environment"],
                    capture_output=True,
                    text=True,
                    timeout=0.6,
                )
                for line in result.stdout.splitlines():
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key in {"WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}:
                        env[key] = value
            except Exception:
                pass
        env["PROTOCOL7_CHATGPT_URL"] = str(self.config.get("companion_url", "https://chatgpt.com/")).strip() or "https://chatgpt.com/"
        return env

    def _runtime_script(self) -> Path:
        return Path(__file__).with_name("chatgpt_voice_runtime.py")

    def ensure_running(self, timeout: float = 6.0) -> bool:
        if self.request("ping", ensure=False, timeout=0.2).get("ok"):
            return True
        try:
            self._process = subprocess.Popen(
                [sys.executable, str(self._runtime_script())],
                env=self._session_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.request("ping", ensure=False, timeout=0.25).get("ok"):
                return True
            time.sleep(0.15)
        return False

    def request(self, command: str, ensure: bool = True, timeout: float = 4.5, payload: dict | None = None) -> dict:
        if ensure and not self.ensure_running():
            return {"ok": False, "result": "runtime-unavailable"}
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        try:
            client.connect(str(socket_path()))
            message = {"command": command}
            if payload:
                message.update(payload)
            client.sendall(json.dumps(message, separators=(",", ":")).encode("utf-8"))
            raw = client.recv(MAX_REPLY)
            if not raw:
                return {"ok": False, "result": "empty-reply"}
            result = json.loads(raw.decode("utf-8"))
            return result if isinstance(result, dict) else {"ok": False, "result": "invalid-reply"}
        except Exception as error:
            return {"ok": False, "result": "runtime-request-failed", "error": str(error)}
        finally:
            client.close()

    def activate(self) -> dict:
        if not self.ensure_running():
            return {"ok": False, "result": "runtime-unavailable"}
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            status = self.request("status", ensure=False, timeout=1.0)
            if status.get("loaded"):
                return self.request("activate", ensure=False, timeout=16.0)
            time.sleep(0.2)
        return {"ok": False, "result": "loading-timeout"}

    def status(self) -> dict:
        return self.request("status")

    def end(self) -> dict:
        return self.request("end")

    def show_login(self) -> dict:
        return self.request("show-login")

    def hide(self) -> dict:
        return self.request("hide")

    def set_debug(self, enabled: bool) -> dict:
        return self.request("set-debug", payload={"enabled": bool(enabled)})
