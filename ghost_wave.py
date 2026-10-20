"""Capture the original pointer anchor and safe application identity on Hyprland.

Cursor position is compositor-logical and global, monitor dimensions are
physical. Ghost Wave is drawn in monitor-local logical Caelestia coordinates.
Never activate an old window or inject into a different active window.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
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
    source: str = "pointer"

    def as_public(self) -> dict:
        # Never expose a window address/title through QML state.
        return {"monitor": self.monitor, "x": self.x, "y": self.y, "app": self.app,
                "source": self.source}

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


def focused_rect(active: dict) -> tuple[int, int, int, int] | None:
    """A short bounded AT-SPI geometry read; never reads input content."""
    script = Path(__file__).with_name("focused_field.py")
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(active.get("class") or ""),
             str(active.get("pid") or 0)],
            capture_output=True, text=True, timeout=0.6, check=True,
        )
        rect = json.loads(result.stdout.strip())
        if (
            isinstance(rect, list) and len(rect) == 4
            and all(type(v) is int for v in rect)
            and 12 <= rect[2] <= 12000 and 12 <= rect[3] <= 12000
        ):
            return tuple(rect)
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    return None


def field_anchor(rect: tuple[int, int, int, int], active: dict, monitors: list[dict]):
    """Normalize AT-SPI screen bounds against compositor-logical coordinates.

    Some Wayland AT-SPI providers return pixels and others logical units.
    Only accept a rect whose center clearly lies inside the active window.
    """
    ax, ay = active.get("at") or [0, 0]
    aw, ah = active.get("size") or [0, 0]
    mon = next((m for m in monitors if m.get("id") == active.get("monitor")), None)
    if not mon or aw <= 0 or ah <= 0:
        return None
    mx, my, mw, mh = logical_geometry(mon)
    scale = float(mon.get("scale") or 1)
    rx, ry, rw, rh = rect
    # Normalized rectangle candidates: logical global; pixel coords relative
    # to this monitor with compositor origin; physical global.
    variants = [
        (rx, ry, rw, rh),
        (mx + (rx - mx) / scale, my + (ry - my) / scale, rw / scale, rh / scale),
        (rx / scale, ry / scale, rw / scale, rh / scale),
    ]
    for x, y, w, h in variants:
        cx = x + w / 2
        if not (ax - 8 <= cx <= ax + aw + 8):
            continue
        if not (ay - 8 <= y <= ay + ah + 8):
            continue
        if w > aw + 25 or h > ah + 25:
            continue
        # A whole-page/whole-terminal accessible is not a focused text input.
        if h > ah * .85 and w > aw * .75:
            continue
        # Position the pill's *bottom* 12px above the field's top edge.
        return mon, round(cx - mx), round(y - my - 33)
    return None


def capture_target(pointer_snapshot: object = None) -> Optional[Target]:
    """Capture the exact focused field when available, else pointer fallback.

    A captured target remains fixed after dictation starts.
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

    # Accessibility is the primary source: exact focused editable geometry.
    # The cursor snapshot is only a fallback when accessibility isn't exposed.
    rect = focused_rect(active)
    anchor = field_anchor(rect, active, monitors) if rect else None
    if anchor:
        selected, x, y = anchor
        origin_x, origin_y, width, height = logical_geometry(selected)
        return Target(
            address=str(active["address"]),
            app=str(active.get("class") or ""),
            monitor=str(selected.get("name") or ""),
            x=max(0, min(width - 1, x)),
            y=max(0, min(height - 1, y)),
            title=str(active.get("title") or "")[:100],
            source="focused-field",
        )

    # The ChatGPT/Codex desktop app does not currently expose its composer
    # via AT-SPI. Its input is bottom-docked, NOT underneath the pointer.
    # Use the window bottom as a deliberate app-specific fallback. The wave's
    # centre sits ~20px above the composer top; keep it in the left third,
    # where the writing area begins (rather than the Send button).
    cls = str(active.get("class") or "").casefold()
    if cls in ("chatgpt", "com.openai.chatgpt"):
        mon = next(
            (m for m in monitors if isinstance(m, dict) and m.get("id") == active.get("monitor")),
            None,
        )
        at, size = active.get("at"), active.get("size")
        if mon and isinstance(at, list) and isinstance(size, list) and len(at) == len(size) == 2:
            origin_x, origin_y, width, height = logical_geometry(mon)
            win_x, win_y = int(at[0]), int(at[1])
            win_w, win_h = int(size[0]), int(size[1])
            if win_w >= 350 and win_h >= 400:
                return Target(
                    address=str(active["address"]),
                    app=str(active.get("class") or ""),
                    monitor=str(mon.get("name") or ""),
                    x=max(0, min(width - 1, round(win_x - origin_x + win_w * .26))),
                    y=max(0, min(height - 1, round(win_y - origin_y + win_h - 146))),
                    title=str(active.get("title") or "")[:100],
                    source="composer-fallback",
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
