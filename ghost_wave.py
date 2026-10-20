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


# The floating surface is fixed-size so its anchor never changes when the
# waveform changes between transcribing and queued-Send states.
GHOST_PILL_WIDTH = 112
GHOST_PILL_HEIGHT = 42
GHOST_SIDE_GAP = 7
GHOST_TOP_GAP = 5


def anchor_by_field_edge(
    x: float, y: float, w: float, h: float,
    window_x: float, window_width: float,
    monitor_x: float, monitor_y: float, monitor_width: float, monitor_height: float,
) -> tuple[int, int]:
    """Prefer left of an input. In narrow layouts float above its left edge.

    Return a monitor-local *pill centre*, never the top-left corner. The
    entire bubble stays inside the usable monitor, but is allowed outside the
    app's window on its left. Clamping to the *window* forced the pill far
    above the composer whenever the app was tiled narrow.
    """
    half_w = GHOST_PILL_WIDTH / 2
    half_h = GHOST_PILL_HEIGHT / 2
    # Caelestia reserves a small left rail. Do not clamp against the owning
    # application: a tiled ChatGPT window can have ample free screen space
    # immediately left of its composer.
    safe_left = monitor_x + min(64, monitor_width * .05) + 8
    safe_right = monitor_x + monitor_width - 8

    side_center_x = x - GHOST_SIDE_GAP - half_w
    if side_center_x - half_w >= safe_left:
        cx = side_center_x
        cy = y + min(h / 2, 33)
    else:
        # In a genuinely edge-aligned window, sit directly above its LEFT
        # edge, not several pill-widths farther into the text field.
        cx = x + half_w
        cy = y - GHOST_TOP_GAP - half_h

    cx = max(safe_left + half_w, min(safe_right - half_w, cx))
    cy = max(monitor_y + half_h + 8, min(monitor_y + monitor_height - half_h - 8, cy))
    return round(cx - monitor_x), round(cy - monitor_y)


def composer_fallback_anchor(active: dict, monitor: dict) -> tuple[int, int] | None:
    """Window-relative estimate for the bottom-docked ChatGPT desktop composer.

    On a wide window the composer has a maximum width and is centered.
    On a narrow window, the wave is above-left rather than hanging offscreen.
    """
    at = active.get("at")
    size = active.get("size")
    if not (
        isinstance(at, (list, tuple)) and isinstance(size, (list, tuple))
        and len(at) == 2 and len(size) == 2
    ):
        return None
    wx, wy = int(at[0]), int(at[1])
    ww, wh = int(size[0]), int(size[1])
    if ww < 350 or wh < 400:
        return None
    mx, my, mw, mh = logical_geometry(monitor)
    composer_width = min(890, max(1, ww - 110))
    composer_x = wx + (ww - composer_width) / 2
    composer_y = wy + wh - 114
    return anchor_by_field_edge(
        composer_x, composer_y, composer_width, 68,
        wx, ww, mx, my, mw, mh,
    )


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
        return mon, *anchor_by_field_edge(
            x, y, w, h, ax, aw, mx, my, mw, mh,
        )
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

    # Exact field geometry is always preferred. ChatGPT/Codex falls back to
    # the bottom-docked composer geometry instead of the drifting pointer.
    cls = str(active.get("class") or "").casefold()
    is_chatgpt_page = ("zen" in cls or "firefox" in cls) and "chatgpt" in str(active.get("title") or "").casefold()
    if cls in ("chatgpt", "com.openai.chatgpt") or is_chatgpt_page:
        mon = next(
            (m for m in monitors if isinstance(m, dict)
             and m.get("id") == active.get("monitor")),
            None,
        )
        anchor = composer_fallback_anchor(active, mon) if mon else None
        if anchor is not None:
            return Target(
                address=str(active["address"]),
                app=str(active.get("class") or ""),
                monitor=str(mon.get("name") or ""),
                x=anchor[0], y=anchor[1],
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
