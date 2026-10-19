"""Capture the original pointer anchor and safe application identity on Hyprland.

Cursor position is compositor-logical and global, monitor dimensions are
physical. Ghost Wave is drawn in monitor-local logical Caelestia coordinates.
Never activate an old window or inject into a different active window.
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
        # Never expose a window address/title through QML state.
        return {"monitor": self.monitor, "x": self.x, "y": self.y, "app": self.app}

    def same_window_active(self) -> bool:
        active = hypr_json("activewindow")
        return (
            bool(self.address) and isinstance(active, dict)
            and active.get("address") == self.address
            and active.get("class") == self.app
        )


def logical_geometry(monitor: dict) -> tuple[int, int, int, int]:
    try:
        scale = max(0.25, float(monitor.get("scale") or 1))
    except (ValueError, TypeError):
        scale = 1.
    width = max(1, round(float(monitor.get("width", 1920)) / scale))
    height = max(1, round(float(monitor.get("height", 1080)) / scale))
    return int(monitor.get("x", 0)), int(monitor.get("y", 0)), width, height


def capture_target(pointer_snapshot: object = None) -> Optional[Target]:
    """Take one *fixed* pointer snapshot at dictation start, not the textbox guess.

    Once detached, the target stays anchored to the pointer's original location
    while the user keeps moving the mouse independently.
    """
    active = hypr_json("activewindow")
    if not isinstance(active, dict) or not active.get("address"):
        return None

    monitors = hypr_json("monitors")
    cursor = pointer_snapshot if isinstance(pointer_snapshot, dict) else hypr_json("cursorpos")
    monitors = monitors if isinstance(monitors, list) else []
    valid_cursor = (
        isinstance(cursor, dict)
        and isinstance(cursor.get("x"), (int, float))
        and isinstance(cursor.get("y"), (int, float))
    )

    # A pointer can be on a *different* monitor than the active app. Anchor
    # location follows the pointer, while insertion safety follows the app.
    selected = None
    if valid_cursor:
        px, py = float(cursor["x"]), float(cursor["y"])
        for monitor in monitors:
            if not isinstance(monitor, dict):
                continue
            mx, my, mw, mh = logical_geometry(monitor)
            if mx <= px < mx + mw and my <= py < my + mh:
                selected = monitor
                break

    if selected is None:
        selected = next(
            (m for m in monitors if isinstance(m, dict) and m.get("id") == active.get("monitor")),
            {},
        )
    origin_x, origin_y, width, height = logical_geometry(selected)
    if valid_cursor:
        anchor_x = round(float(cursor["x"]) - origin_x)
        anchor_y = round(float(cursor["y"]) - origin_y)
    else:
        # Only when Hyprland cannot report a pointer: approximate the old
        # window-centered anchor rather than inventing screen coordinates.
        loc = active.get("at") or [origin_x, origin_y]
        size = active.get("size") or [0, 0]
        anchor_x = int(loc[0]) - origin_x + int(size[0]) // 2
        anchor_y = int(loc[1]) - origin_y + int(size[1]) // 2

    # Float the wave *above* the pointer so it never sits under the next click.
    # The panel host handles screen-rail offsets and final edge clamping.
    anchor_y -= 36

    return Target(
        address=str(active["address"]),
        app=str(active.get("class") or ""),
        monitor=str(selected.get("name") or ""),
        x=max(0, min(width - 1, anchor_x)),
        y=max(0, min(height - 1, anchor_y)),
        title=str(active.get("title") or "")[:100],
    )
