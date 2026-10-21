#!/usr/bin/env python3
"""Insert a selected Protocol 7 transcription into the focused application.

The QML panel passes only an existing history timestamp, not arbitrary
commands or text. This helper does not modify or delete transcription history.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


HISTORY_PATH = Path.home() / ".config/protocol-7/history.json"


def selected_text(timestamp: str, path: Path = HISTORY_PATH) -> str:
    try:
        value = float(timestamp)
        if not math.isfinite(value):
            return ""
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(entries, list):
        return ""
    for entry in entries[:100]:
        if not isinstance(entry, dict):
            continue
        try:
            if float(entry.get("timestamp", -1)) == value:
                text = entry.get("text", "")
                return text if isinstance(text, str) else ""
        except (ValueError, TypeError):
            continue
    return ""


def child_env() -> dict:
    env = os.environ.copy()
    env.pop("LD_PRELOAD", None)
    env.pop("PROTOCOL7_PRELOADED", None)
    return env


def is_terminal() -> bool:
    try:
        p = subprocess.run(
            ["hyprctl", "activewindow", "-j"], capture_output=True, text=True,
            timeout=0.4, env=child_env(),
        )
        klass = str(json.loads(p.stdout or "{}").get("class", "")).lower()
        return any(name in klass for name in ("ghostty", "kitty", "foot", "alacritty", "wezterm", "konsole", "terminal"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def focused_is_sumi() -> bool:
    try:
        p = subprocess.run(
            ["hyprctl", "activewindow", "-j"], capture_output=True, text=True,
            timeout=0.4, env=child_env(),
        )
        return str(json.loads(p.stdout or "{}").get("class", "")).lower() == "sumi"
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def type_text(text: str) -> bool:
    """Use persistent kernel keyboard codes; never wtype's transient keymap."""
    if not text.isascii():
        return clipboard_paste(text)
    try:
        subprocess.run(
            ["ydotool", "type", "-d", "3", "-H", "3", "-f", "-"],
            input=text.encode("utf-8"), check=True,
            timeout=max(8, min(90, 5 + len(text) * 0.03)),
            env=child_env(),
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def clipboard_paste(text: str) -> bool:
    """Send Ctrl+V without permanently replacing the user's clipboard."""
    env = child_env()
    env["YDOTOOL_SOCKET"] = f"/run/user/{os.getuid()}/.ydotool_socket"
    previous = None
    changed = False
    try:
        formats = subprocess.run(
            ["wl-paste", "--list-types"], capture_output=True, text=True,
            timeout=0.35, env=env,
        )
        if formats.returncode == 0:
            available = formats.stdout.splitlines()
            preferred = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "image/png", "image/jpeg")
            mime = next((s for s in preferred if s in available), available[0] if available else None)
            if mime:
                old = subprocess.run(
                    ["wl-paste", "--type", mime], capture_output=True,
                    timeout=0.5, env=env,
                )
                if old.returncode == 0 and len(old.stdout) <= 10 * 1024 * 1024:
                    previous = (mime, old.stdout)
        copied = subprocess.run(
            ["wl-copy", "--sensitive", "--type", "text/plain;charset=utf-8"],
            input=text.encode("utf-8"), timeout=0.6, env=env,
        )
        if copied.returncode != 0:
            return False
        changed = True
        keys = (["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]
                if is_terminal() else ["29:1", "47:1", "47:0", "29:0"])
        result = subprocess.run(
            ["ydotool", "key", *keys],
            timeout=1.5, env=env,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if changed:
            # Consumers may read Wayland clipboard data asynchronously.
            time.sleep(0.25)
            try:
                if previous is None:
                    subprocess.run(["wl-copy", "--clear"], timeout=0.5, env=env)
                else:
                    subprocess.run(
                        ["wl-copy", "--sensitive", "--type", previous[0]],
                        input=previous[1], timeout=0.8, env=env,
                    )
            except (OSError, subprocess.SubprocessError):
                pass


def paste_timestamp(timestamp: str, path: Path = HISTORY_PATH) -> bool:
    text = selected_text(timestamp, path).replace("\r", " ").replace("\n", " ")
    if not text.strip():
        return False
    # Never race Codex or another asynchronous paste consumer by swapping the
    # clipboard back to its previous content (which may be an image).
    # The SUMI editor is the one known exception that requires Ctrl+V.
    if focused_is_sumi() and clipboard_paste(text):
        return True
    return type_text(text)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "paste":
        print("usage: history_actions.py paste <history-timestamp>", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if paste_timestamp(sys.argv[2]) else 1)
