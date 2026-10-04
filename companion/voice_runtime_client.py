from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from configparser import ConfigParser
from pathlib import Path


class VoiceRuntimeClient:
    """Control the dedicated hidden Zen/Firefox ChatGPT Voice tab.

    WebKitGTK's GStreamer WebRTC backend is not reliable enough for ChatGPT
    Voice on Mirai.  The Zen bridge runs inside the already-authenticated Zen
    profile and exposes only tiny command/state JSON files in that profile.
    No conversation text is read or written by this client.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.debug = bool(self.config.get("companion_webview_debug", False))
        self._lock = threading.Lock()
        self._last_seq = 0
        self._profile = self._find_zen_profile()
        self._command_path = self._profile / "tabby-bridge-command.json"
        self._state_path = self._profile / "tabby-bridge-state.json"

    @staticmethod
    def _find_zen_profile() -> Path:
        root = Path.home() / ".var/app/app.zen_browser.zen/.zen"
        ini = root / "profiles.ini"
        cfg = ConfigParser()
        cfg.read(ini)
        for section in cfg.sections():
            if section.startswith("Install") and cfg.has_option(section, "Default"):
                return root / cfg.get(section, "Default")
        for section in cfg.sections():
            if section.startswith("Profile") and cfg.get(section, "Default", fallback="0") == "1":
                return root / cfg.get(section, "Path")
        for section in cfg.sections():
            if section.startswith("Profile") and cfg.has_option(section, "Path"):
                return root / cfg.get(section, "Path")
        # Known current profile fallback.  Keeping this here is safer than
        # falling back to WebKit and reintroducing the WebRTC crash path.
        return root / "3bes0gjg.Default (release)"

    def _session_env(self) -> dict:
        env = os.environ.copy()
        if not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "show-environment"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                )
                for line in result.stdout.splitlines():
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key in {"WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}:
                        env[key] = value
            except Exception:
                pass
        # Mirai's graphical session currently exposes :1 / wayland-1. These
        # defaults only apply when systemd's user environment is incomplete.
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DISPLAY", ":1")
        env.setdefault("WAYLAND_DISPLAY", "wayland-1")
        return env

    @staticmethod
    def _zen_running() -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "/app/zen/zen --name app.zen_browser.zen"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=0.5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _read_state(self) -> dict:
        try:
            value = json.loads(self._state_path.read_text())
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _write_command(self, payload: dict) -> None:
        self._profile.mkdir(parents=True, exist_ok=True)
        temp = self._command_path.with_name(f"{self._command_path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, separators=(",", ":")))
        temp.replace(self._command_path)

    def _bridge_live(self) -> bool:
        state = self._read_state()
        return bool(self._zen_running() and state.get("bridgeLoaded") and str(state.get("version", "")).startswith("0.2."))

    def ensure_running(self, timeout: float = 8.0) -> bool:
        # A real status roundtrip proves both Zen and the privileged bridge are alive.
        probe = self._bridge_request("status", timeout=1.0, ensure=False)
        if probe.get("ok"):
            return True

        if not self._zen_running():
            try:
                subprocess.Popen(
                    ["flatpak", "run", "app.zen_browser.zen"],
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
            probe = self._bridge_request("status", timeout=0.8, ensure=False)
            if probe.get("ok"):
                return True
            time.sleep(0.2)
        return False

    def _normalize(self, raw: dict) -> dict:
        result = dict(raw or {})
        active = bool(result.get("active"))
        logged_out = bool(result.get("loggedOut"))
        ready = bool(result.get("ready"))
        if active:
            phase = "active"
        elif logged_out or result.get("result") == "needs-login":
            phase = "needs-login"
        elif ready:
            phase = "ready"
        elif result.get("ok"):
            phase = "loading"
        else:
            phase = "unavailable"
        result.update({
            "loaded": True,
            "phase": phase,
            "active": active,
            "loggedOut": logged_out,
            "authenticated": not logged_out,
            "voiceReady": ready,
            "debugVisible": bool(result.get("debugVisible", self.debug)),
            "loginVisible": bool(logged_out and result.get("debugVisible", self.debug)),
            "setupVisible": bool(logged_out and result.get("debugVisible", self.debug)),
            "url": result.get("href", "https://chatgpt.com/"),
        })
        return result

    def _bridge_request(
        self,
        command: str,
        *,
        timeout: float = 5.0,
        ensure: bool = True,
        extra: dict | None = None,
    ) -> dict:
        if ensure and not self.ensure_running():
            return {"ok": False, "result": "zen-bridge-unavailable", "phase": "unavailable"}
        if not self._profile.exists():
            return {"ok": False, "result": "zen-profile-unavailable", "phase": "unavailable"}

        with self._lock:
            seq = max(int(time.time() * 1000), self._last_seq + 1)
            self._last_seq = seq
            payload = {"seq": seq, "command": command}
            if extra:
                payload.update(extra)
            try:
                self._write_command(payload)
            except Exception as error:
                return {"ok": False, "result": "bridge-command-write-failed", "error": str(error)}

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                state = self._read_state()
                if state.get("seq") == seq:
                    return self._normalize(state)
                time.sleep(0.08)
            return {"ok": False, "result": "zen-bridge-timeout", "phase": "unavailable"}

    def request(self, command: str, ensure: bool = True, timeout: float = 4.5, payload: dict | None = None) -> dict:
        if command == "ping":
            if not self._bridge_live():
                return {"ok": False, "result": "zen-bridge-unavailable"}
            return {"ok": True, "result": "ready", "loaded": True}
        if command == "set-debug":
            enabled = bool((payload or {}).get("enabled", False))
            self.debug = enabled
            return self._bridge_request("show" if enabled else "hide", timeout=timeout, ensure=ensure)
        if command == "show-login":
            self.debug = True
            return self._bridge_request("show", timeout=timeout, ensure=ensure)
        if command == "hide":
            return self._bridge_request("hide", timeout=timeout, ensure=ensure)
        if command == "activate":
            return self._bridge_request("activate", timeout=timeout, ensure=ensure, extra={"debug": self.debug})
        if command == "end":
            return self._bridge_request("end", timeout=timeout, ensure=ensure, extra={"debug": self.debug})
        return self._bridge_request("status", timeout=timeout, ensure=ensure)

    def activate(self) -> dict:
        if not self.ensure_running():
            return {"ok": False, "result": "zen-bridge-unavailable", "phase": "unavailable"}
        return self.request("activate", ensure=False, timeout=16.0)

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
