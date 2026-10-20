"""Measure ChatGPT's visible composer without inspecting text content.

This is a geometry-only fallback for apps that expose no focused editable
AT-SPI node. A transient crop of the active window's lower area is decoded in
memory; no image, pixel data or displayed text is written to disk or logged.

The detector needs TWO independent shapes: the persistent sidebar/chat divider
and the upper/lower borders of the bottom composer. Low confidence returns
None so the caller can use its established fallback.
"""
from __future__ import annotations

from io import BytesIO
import subprocess
import time

_last_capture_failure = float("-inf")

import numpy as np
from PIL import Image


def detect_rect(gray: np.ndarray, *, window_x: int, crop_y: int,
                logical_width: int, logical_height: int) -> tuple[int, int, int, int] | None:
    """Return global logical (x,y,width,height), measured from screen pixels."""
    if gray.ndim != 2:
        return None
    h, w = gray.shape
    if min(w, h) < 140 or logical_width <= 0 or logical_height <= 0:
        return None
    sx = w / logical_width
    sy = h / logical_height
    if not (0.5 <= sx <= 4 and 0.5 <= sy <= 4 and 0.8 <= sx/sy <= 1.2):
        return None
    data = gray.astype(np.float32, copy=False)

    # A real pane separator persists across most of the upper crop, unlike
    # text glyphs, icons and individual card borders. Use the 40th percentile.
    upper = data[:max(80, int(h * .65))]
    xdiff = np.abs(upper[:, 2:] - upper[:, :-2])
    persistent = np.percentile(xdiff, 40, axis=0)
    low, high = int(w * .26), int(w * .86)
    if high <= low:
        return None
    split_x = int(np.argmax(persistent[low:high]) + low + 1)
    if persistent[split_x - 1] < 6.0:
        return None

    # Look for two long horizontal rounded-rectangle edges near the bottom
    # of the *chat* pane, not the sidebar's independent footer.
    box_l = min(w - 60, split_x + max(20, round(27 * sx)))
    box_r = max(box_l + 30, w - max(20, round(28 * sx)))
    if box_r - box_l < w * .15:
        return None
    diff = np.abs(data[2:, box_l:box_r] - data[:-2, box_l:box_r])
    edge = np.percentile(diff, 50, axis=1)

    candidates = []
    for y in range(int(h * .5), min(h - 7, len(edge) - 1)):
        if edge[y] < 7:
            continue
        if edge[y] < edge[y - 1] or edge[y] < edge[y + 1]:
            continue
        candidates.append((y + 1, float(edge[y])))
    # A standard composer is 36–88 logical px high.
    min_gap, max_gap = round(35 * sy), round(90 * sy)
    pairs = [
        (score1 + score2, top, bottom)
        for top, score1 in candidates
        for bottom, score2 in candidates
        if min_gap <= bottom - top <= max_gap
        and bottom > h * .70
        and bottom < h - 9
    ]
    if not pairs:
        return None
    _, top, bottom = max(pairs, key=lambda pair: pair[0])

    # Refine left edge from the side of the composer itself when detectable.
    # The 40th percentile suppresses short labels and mouse pointer artifacts.
    side_top = min(h - 1, top + round(10 * sy))
    side_bottom = max(side_top + 1, bottom - round(10 * sy))
    span_l = min(w - 3, split_x + max(8, round(9 * sx)))
    span_r = min(w - 2, split_x + round(65 * sx))
    if side_bottom > side_top and span_r > span_l:
        side = np.abs(data[side_top:side_bottom, 2:] - data[side_top:side_bottom, :-2])
        side_persistent = np.percentile(side, 40, axis=0)
        side_x = int(np.argmax(side_persistent[span_l:span_r]) + span_l + 1)
        if side_persistent[side_x - 1] >= 3.0:
            left = side_x
        else:
            left = split_x + round(17 * sx)
    else:
        left = split_x + round(17 * sx)

    # Reject implausible "composers"; a divider alone is insufficient.
    if left < split_x + 6 or left >= w - 150:
        return None
    logical_x = round(window_x + left / sx)
    logical_y = round(crop_y + top / sy)
    logical_w = round((w - left - 12 * sx) / sx)
    logical_h = round((bottom - top) / sy)
    if not (100 <= logical_w <= logical_width and 35 <= logical_h <= 90):
        return None
    return logical_x, logical_y, logical_w, logical_h


def capture_composer(active: dict) -> tuple[int, int, int, int] | None:
    """Read an in-memory crop once per hotkey press, without OCR or saving."""
    global _last_capture_failure
    if time.monotonic() - _last_capture_failure < 90:
        return None
    pos, size = active.get("at"), active.get("size")
    if not (isinstance(pos, (list, tuple)) and isinstance(size, (list, tuple))
            and len(pos) == 2 and len(size) == 2):
        return None
    wx, wy = int(pos[0]), int(pos[1])
    ww, wh = int(size[0]), int(size[1])
    if ww < 500 or wh < 450:
        return None
    crop_h = min(330, wh)
    crop_y = wy + wh - crop_h
    try:
        result = subprocess.run(
            ["grim", "-g", f"{wx},{crop_y} {ww}x{crop_h}", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=0.42, check=True,
        )
        if not result.stdout or len(result.stdout) > 6_000_000:
            return None
        with Image.open(BytesIO(result.stdout)) as image:
            gray = np.asarray(image.convert("L"))
        return detect_rect(gray, window_x=wx, crop_y=crop_y,
                           logical_width=ww, logical_height=crop_h)
    except (OSError, ValueError, subprocess.SubprocessError):
        _last_capture_failure = time.monotonic()
        return None
