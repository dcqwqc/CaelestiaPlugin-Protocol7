from __future__ import annotations
import json, os, socket
from pathlib import Path
from typing import Any

MAX_REPLY=128*1024

def socket_path() -> Path:
    runtime=Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))
    return runtime/'tabby.sock'

def send_command(command: dict[str,Any], timeout: float=1.0) -> dict[str,Any]:
    client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); client.settimeout(timeout)
    try:
        client.connect(str(socket_path()))
        client.sendall(json.dumps(command,separators=(',',':'),ensure_ascii=False).encode())
        raw=client.recv(MAX_REPLY)
        return json.loads(raw.decode()) if raw else {'ok':False,'error':'empty reply'}
    except Exception as e:
        return {'ok':False,'error':str(e)}
    finally:
        client.close()
