from __future__ import annotations

import copy
import json
import sys
import threading
from typing import Any

VALID_STATES = {"asleep", "idle", "wake", "listening", "thinking", "tool", "speaking", "approval", "success", "error"}
VALID_COMMANDS = {"status", "state", "show", "hide", "clear", "text", "progress", "choice", "shape"}
VALID_SHAPES = {"line", "arrow", "rect", "circle"}
MAX_ITEMS = 32
MAX_TEXT = 500


def _bounded_text(value: Any, limit: int = MAX_TEXT) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _unit(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def initial_state(enabled: bool = True) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "summoned": False,
        "state": "idle",
        "whiteboardVisible": False,
        "items": [],
        "sequence": 0,
    }


def validate_command(command: Any) -> dict[str, Any]:
    if not isinstance(command, dict):
        raise ValueError("command must be an object")
    kind = str(command.get("command", "")).strip().lower()
    if kind not in VALID_COMMANDS:
        raise ValueError("unsupported command")
    clean: dict[str, Any] = {"command": kind}

    if kind == "state":
        value = str(command.get("value", "")).strip().lower()
        if value not in VALID_STATES:
            raise ValueError("invalid state")
        clean["value"] = value
    elif kind == "text":
        text = _bounded_text(command.get("text"))
        if not text:
            raise ValueError("text is required")
        clean["text"] = text
        clean["title"] = _bounded_text(command.get("title"), 120)
    elif kind == "progress":
        clean["label"] = _bounded_text(command.get("label"), 160)
        clean["value"] = _unit(command.get("value"))
    elif kind == "choice":
        label = _bounded_text(command.get("label"), 180)
        if not label:
            raise ValueError("choice label is required")
        clean["label"] = label
        options = command.get("options", [])
        if not isinstance(options, list):
            raise ValueError("choice options must be a list")
        clean["options"] = [_bounded_text(option, 120) for option in options[:6] if _bounded_text(option, 120)]
    elif kind == "shape":
        shape = str(command.get("kind", "")).strip().lower()
        if shape not in VALID_SHAPES:
            raise ValueError("invalid shape")
        clean.update({
            "kind": shape,
            "x": _unit(command.get("x")),
            "y": _unit(command.get("y")),
            "w": _unit(command.get("w"), 0.25),
            "h": _unit(command.get("h"), 0.25),
            "label": _bounded_text(command.get("label"), 100),
        })
    return clean


def apply_command(state: dict[str, Any], command: Any) -> dict[str, Any]:
    clean = validate_command(command)
    kind = clean["command"]
    if kind == "status":
        return state
    if kind == "state":
        state["state"] = clean["value"]
    elif kind == "show":
        state["whiteboardVisible"] = True
    elif kind == "hide":
        state["whiteboardVisible"] = False
    elif kind == "clear":
        state["items"] = []
        state["whiteboardVisible"] = False
    else:
        item = {"type": kind}
        item.update({k: v for k, v in clean.items() if k != "command"})
        items = list(state.get("items", []))
        items.append(item)
        state["items"] = items[-MAX_ITEMS:]
        state["whiteboardVisible"] = True
    state["sequence"] = int(state.get("sequence", 0)) + 1
    return state


class CompanionStatePublisher:
    def __init__(self, enabled: bool = True):
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._state = initial_state(enabled)
        self.publish()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def publish(self) -> None:
        payload = json.dumps(self.snapshot(), separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            try:
                sys.stdout.write(f"COMPANIONSTATE {payload}\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                pass

    def command(self, command: Any) -> dict[str, Any]:
        try:
            with self._lock:
                clean = validate_command(command)
                if clean["command"] != "status":
                    apply_command(self._state, clean)
                    changed = True
                else:
                    changed = False
                snapshot = copy.deepcopy(self._state)
            if changed:
                self.publish()
            return {"ok": True, "state": snapshot}
        except ValueError as error:
            return {"ok": False, "error": str(error)}

    def set_state(self, value: str) -> None:
        self.command({"command": "state", "value": value})

    def set_summoned(self, value: bool) -> None:
        with self._lock:
            value = bool(value)
            if bool(self._state.get("summoned")) == value:
                return
            self._state["summoned"] = value
            self._state["sequence"] = int(self._state.get("sequence", 0)) + 1
        self.publish()
