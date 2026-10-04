from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Callable, Any

MAX_PAYLOAD = 16 * 1024
MAX_REPLY = 64 * 1024


def socket_path() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "protocol7-companion.sock"


class CompanionIPCServer:
    def __init__(self, handler: Callable[[Any], dict]):
        self.handler = handler
        self.path = socket_path()
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
        server.listen(8)
        server.settimeout(0.25)
        self._socket = server
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="hey-tabby-ipc", daemon=True)
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
                    conn.settimeout(0.4)
                    payload = conn.recv(MAX_PAYLOAD + 1)
                    if not payload or len(payload) > MAX_PAYLOAD:
                        result = {"ok": False, "error": "invalid payload size"}
                    else:
                        command = json.loads(payload.decode("utf-8"))
                        result = self.handler(command)
                    reply = json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                    conn.sendall(reply[:MAX_REPLY])
                except (json.JSONDecodeError, UnicodeError, OSError, ValueError):
                    try:
                        conn.sendall(b'{"ok":false,"error":"invalid request"}')
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


def send_command(command: dict, timeout: float = 0.6) -> dict:
    payload = json.dumps(command, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        return {"ok": False, "error": "payload too large"}
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path()))
        client.sendall(payload)
        result = client.recv(MAX_REPLY)
        if not result:
            return {"ok": False, "error": "empty reply"}
        decoded = json.loads(result.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {"ok": False, "error": "invalid reply"}
    except (OSError, ValueError, UnicodeError) as error:
        return {"ok": False, "error": str(error)}
    finally:
        client.close()
