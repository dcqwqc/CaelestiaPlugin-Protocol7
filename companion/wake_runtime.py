from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from companion.tabby_proxy import send_command
from companion.wake_word import WakeWordDetector


def _send_tabby_command(command: str, transcript: str, score: float) -> bool:
    result=send_command({
        "command":str(command),
        "source":"protocol7",
        "transcript":str(transcript or "")[:300],
        "score":float(score),
    }, timeout=0.45)
    return bool(result.get("ok"))


def _send_tabby_wake(transcript: str, score: float) -> bool:
    # Use the exact same newer open variant as Tabby's physical hotkey.
    return _send_tabby_command("summon", transcript, score)


def _send_tabby_close(transcript: str, score: float) -> bool:
    return _send_tabby_command("close", transcript, score)



def _audit_voice_event(event: str, **fields) -> None:
    try:
        path=Path.home()/'.cache/protocol-7/tabby-voice-events.jsonl'
        path.parent.mkdir(parents=True,exist_ok=True)
        payload={"ts":time.time(),"event":str(event)}
        payload.update({k:v for k,v in fields.items() if v is not None})
        with path.open('a',encoding='utf-8') as f:
            f.write(json.dumps(payload,ensure_ascii=False) + chr(10))
    except Exception:
        pass


class WakeRuntime:
    """Protocol7's only Tabby responsibility: recognize the wake phrase.

    Tabby owns all UI, ChatGPT/Zen state, text input, audio animation and
    lifecycle.  This runtime deliberately has no companion presentation state.
    """

    @staticmethod
    def _tabby_status() -> dict:
        try:
            result=send_command({"command":"status","source":"protocol7-wake"}, timeout=0.16)
            state=result.get("state") or {}
            return state if isinstance(state,dict) else {}
        except Exception:
            return {}

    @classmethod
    def _tabby_active(cls) -> bool:
        return bool(cls._tabby_status().get("summoned"))

    def __init__(self, config: dict, transcriber, busy=None):
        self.config = config
        self.enabled = bool(config.get("companion_wake_enabled", True))
        self._external_busy = busy or (lambda: False)
        self.wake = WakeWordDetector(
            config,
            self._on_wake,
            transcriber=transcriber,
            busy=lambda: bool(self._external_busy()),
            on_close=self._on_close,
            companion_active=self._tabby_active,
            companion_state=lambda: str(self._tabby_status().get("state") or ""),
        )

    def _fallback_cli(self, command: str) -> None:
        # Recovery path if the Tabby backend is still starting/reloading.
        # Call Tabby's own CLI/socket control plane directly; Protocol7 still
        # owns no Tabby UI/runtime state.
        try:
            tabby_ctl = Path.home() / ".local/share/caelestia/plugins/loom/loomctl.py"
            subprocess.Popen(
                ["/usr/bin/python3", str(tabby_ctl), str(command)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass

    def _on_wake(self, transcript: str, score: float) -> None:
        if not self.enabled:
            return
        _audit_voice_event("wake",transcript=str(transcript)[:200],score=float(score))
        if not _send_tabby_wake(transcript, score):
            self._fallback_cli("summon")

    def _on_close(self, transcript: str, score: float, origin_state: str | None = None) -> None:
        if not self.enabled:
            return
        state=self._tabby_status()
        mode=str(state.get("state") or "")
        origin=str(origin_state or mode)
        if not state.get("summoned"):
            _audit_voice_event("close-rejected-inactive",transcript=str(transcript)[:200],score=float(score),state=mode,origin_state=origin)
            return
        safe_states={"listening","idle","success"}
        # Both when the clip STARTED and when Whisper FINISHED must be safe. This
        # blocks stale ChatGPT speaker audio that was queued during `speaking` and
        # only transcribed after the UI had already returned to `listening`.
        if origin not in safe_states:
            _audit_voice_event("close-rejected-origin",transcript=str(transcript)[:200],score=float(score),state=mode,origin_state=origin)
            return
        if mode not in safe_states:
            _audit_voice_event("close-rejected-busy",transcript=str(transcript)[:200],score=float(score),state=mode,origin_state=origin)
            return
        _audit_voice_event("close-accepted",transcript=str(transcript)[:200],score=float(score),state=mode,origin_state=origin)
        if not _send_tabby_close(transcript, score):
            self._fallback_cli("close")

    def start(self) -> None:
        if self.enabled:
            self.wake.start()

    def stop(self) -> None:
        self.wake.stop()
