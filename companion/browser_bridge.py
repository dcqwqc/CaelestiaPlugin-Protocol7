from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


class BrowserBridge:
    """Thin, disposable launcher/focus layer around the real ChatGPT Voice UI.

    A future Zen WebExtension/native-messaging helper can implement exact tab
    selection and Voice-button activation. This class intentionally does not
    scrape ChatGPT DOM/content and never clicks fixed screen coordinates.
    """

    def __init__(self, config: dict):
        self.enabled = bool(config.get("companion_browser_bridge_enabled", True))
        self.url = str(config.get("companion_url", "https://chatgpt.com/")).strip() or "https://chatgpt.com/"
        self.helper = Path(os.path.expanduser(str(config.get("companion_browser_helper", "~/.local/bin/hey-tabby-browser-bridge"))))
        self._opened_once = False
        self.extension_shortcut = str(config.get("companion_browser_extension_shortcut", "ctrl+alt+shift+v")).lower()

    @staticmethod
    def _zen_clients() -> list[dict]:
        try:
            result = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=0.4)
            clients = json.loads(result.stdout or "[]")
            return [c for c in clients if "zen" in f"{c.get('class', '')} {c.get('initialClass', '')} {c.get('title', '')}".lower()]
        except Exception:
            return []

    @staticmethod
    def _focus(client: dict) -> bool:
        address = str(client.get("address", "")).strip()
        if not address:
            return False
        try:
            return subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{address}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.4).returncode == 0
        except Exception:
            return False

    def _trigger_extension_shortcut(self) -> bool:
        """Send the dedicated WebExtension command after Zen has focus."""
        if self.extension_shortcut != "ctrl+alt+shift+v":
            return False
        wtype = shutil.which("wtype")
        if not wtype:
            return False
        try:
            result = subprocess.run(
                [wtype, "-M", "ctrl", "-M", "alt", "-M", "shift", "v",
                 "-m", "shift", "-m", "alt", "-m", "ctrl"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=0.6,
            )
            return result.returncode == 0
        except Exception:
            return False

    def activate(self) -> dict:
        if not self.enabled:
            return {"ok": False, "reason": "browser bridge disabled"}

        if self.helper.is_file() and os.access(self.helper, os.X_OK):
            try:
                result = subprocess.run([str(self.helper), "activate", "--url", self.url], capture_output=True, text=True, timeout=2.0)
                if result.returncode == 0:
                    return {"ok": True, "mode": "extension-helper", "voiceRequested": True}
            except Exception:
                pass

        clients = self._zen_clients()
        focused = self._focus(clients[0]) if clients else False

        # First activation opens the configured Companion URL if necessary.
        if not self._opened_once:
            launcher = shutil.which("zen-browser") or shutil.which("zen") or shutil.which("xdg-open")
            if launcher:
                try:
                    subprocess.Popen([launcher, self.url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    self._opened_once = True
                    time.sleep(0.8)
                    clients = self._zen_clients()
                    focused = self._focus(clients[0]) if clients else focused
                except Exception:
                    pass

        shortcut = self._trigger_extension_shortcut() if focused else False
        return {
            "ok": bool(focused or self._opened_once),
            "mode": "zen-webextension-shortcut" if shortcut else ("focus-only" if focused else "open-url"),
            "voiceRequested": shortcut,
        }
