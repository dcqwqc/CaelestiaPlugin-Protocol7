"""Non-blocking dictation target and bounded background-job metadata.

Hyprland exposes window identity and bounds, NOT a reliable cross-application
text-caret identity. Never activate an old window or inject text across windows.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import subprocess
from typing import Optional


def hypr_json(*args: str) -> object:
    try:
        result = subprocess.run(
            ["hyprctl", *args, "-j"], capture_output=True, text=True,
            timeout=0.35, check=True,
        )
        return json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


@dataclass(frozen=True)
class Target:
    address: str
    app: str
    monitor: str
    x: int
    y: int
    title: str = ""

    def as_public(self) -> dict:
        return {"monitor": self.monitor, "x": self.x, "y": self.y, "app": self.app}

    def same_window_active(self) -> bool:
        active = hypr_json("activewindow")
        return (
            bool(self.address) and isinstance(active, dict)
            and active.get("address") == self.address
            and active.get("class") == self.app
        )


def capture_target() -> Optional[Target]:
    active = hypr_json("activewindow")
    if not isinstance(active, dict) or not active.get("address"):
        return None
    monitors = hypr_json("monitors")
    matched = None
    if isinstance(monitors, list):
        for monitor in monitors:
            if monitor.get("id") == active.get("monitor"):
                matched = monitor
                break
    location = active.get("at") or [0, 0]
    size = active.get("size") or [0, 0]
    if not isinstance(location, list) or len(location) < 2:
        location = [0, 0]
    if not isinstance(size, list) or len(size) < 2:
        size = [0, 0]
    monitor = matched or {}
    # Approximate textbox location for generic apps: lower-midpoint.
    # The backend NEVER claims that this is the precise caret.
    x = int(location[0]) - int(monitor.get("x", 0)) + max(0, int(size[0]) // 2)
    y = int(location[1]) - int(monitor.get("y", 0)) + max(0, int(size[1]) - 80)
    width = int(monitor.get("width", 1920))
    height = int(monitor.get("height", 1080))
    return Target(
        address=str(active["address"]), app=str(active.get("class") or ""),
        monitor=str(monitor.get("name") or ""),
        x=max(65, min(width - 65, x)),
        y=max(55, min(height - 55, y)),
        title=str(active.get("title") or "")[:100],
    )
