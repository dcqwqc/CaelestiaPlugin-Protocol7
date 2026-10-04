from __future__ import annotations

from typing import Literal

from mcp.server import MCPServer

from companion.ipc import send_command

mcp = MCPServer("hey-tabby-companion")


@mcp.tool()
def companion_set_state(state: Literal[
    "asleep", "idle", "wake", "listening", "thinking",
    "tool", "speaking", "approval", "success", "error"
]) -> dict:
    """Set the small companion face/activity state."""
    return send_command({"command": "state", "value": state})


@mcp.tool()
def whiteboard_show() -> dict:
    """Show the companion whiteboard without adding content."""
    return send_command({"command": "show"})


@mcp.tool()
def whiteboard_hide() -> dict:
    """Hide the ephemeral companion whiteboard."""
    return send_command({"command": "hide"})


@mcp.tool()
def whiteboard_clear() -> dict:
    """Clear and hide all ephemeral whiteboard content."""
    return send_command({"command": "clear"})


@mcp.tool()
def whiteboard_write(text: str, title: str = "") -> dict:
    """Append a short text block to the companion whiteboard."""
    return send_command({"command": "text", "text": text, "title": title})


@mcp.tool()
def whiteboard_progress(value: float, label: str = "") -> dict:
    """Append a progress indicator. value is normalized from 0.0 to 1.0."""
    return send_command({"command": "progress", "value": value, "label": label})


@mcp.tool()
def whiteboard_choice(label: str, options: list[str]) -> dict:
    """Show a compact choice/question row with up to six options."""
    return send_command({"command": "choice", "label": label, "options": options})


@mcp.tool()
def whiteboard_shape(
    kind: Literal["line", "arrow", "rect", "circle"],
    x: float,
    y: float,
    w: float,
    h: float,
    label: str = "",
) -> dict:
    """Draw a simple shape using normalized 0..1 whiteboard coordinates."""
    return send_command({
        "command": "shape",
        "kind": kind,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "label": label,
    })


if __name__ == "__main__":
    mcp.run()
