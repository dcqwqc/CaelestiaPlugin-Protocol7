#!/usr/bin/env python3
"""Small local control channel for the Protocol 7 backend.

The socket lives in XDG_RUNTIME_DIR, is mode 0600, accepts only a tiny fixed
command vocabulary, and never carries dictated text or configuration secrets.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Callable

MAX_COMMAND = 64
MAX_REPLY = 4096
COMMANDS = {"toggle", "start", "stop", "status"}


def control_socket_path() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "protocol7-control.sock"


class ControlServer:
    def __init__(self, handler: Callable[[str], dict]):
        self.handler = handler
        self.path = control_socket_path()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        server.listen(4)
        server.settimeout(0.25)
        self._socket = server
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve,
            name="protocol7-control",
            daemon=True,
        )
        self._thread.start()

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with conn:
                try:
                    payload = conn.recv(MAX_COMMAND + 1)
                    if not payload or len(payload) > MAX_COMMAND:
                        reply = {"ok": False, "error": "invalid command"}
                    else:
                        command = payload.decode("ascii", errors="strict").strip()
                        if command not in COMMANDS:
                            reply = {"ok": False, "error": "unsupported command"}
                        else:
                            reply = self.handler(command)
                    encoded = json.dumps(reply, separators=(",", ":")).encode("utf-8")
                    conn.sendall(encoded[:MAX_REPLY])
                except (UnicodeError, ValueError, OSError):
                    try:
                        conn.sendall(b'{"ok":false,"error":"control failure"}')
                    except OSError:
                        pass

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def call_control(command: str, timeout: float = 0.5) -> dict:
    if command not in COMMANDS:
        return {"ok": False, "error": "unsupported command"}
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(control_socket_path()))
        client.sendall((command + "\n").encode("ascii"))
        payload = client.recv(MAX_REPLY)
        if not payload:
            return {"ok": False, "error": "empty reply"}
        result = json.loads(payload.decode("utf-8"))
        return result if isinstance(result, dict) else {"ok": False, "error": "bad reply"}
    except (OSError, ValueError, UnicodeError) as error:
        return {"ok": False, "error": str(error)}
    finally:
        client.close()


def main() -> int:
    import sys

    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print("usage: control_ipc.py toggle|start|stop|status", file=sys.stderr)
        return 2
    result = call_control(sys.argv[1])
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
