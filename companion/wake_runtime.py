from __future__ import annotations

import subprocess
from pathlib import Path

from companion.tabby_proxy import send_command
from companion.wake_word import WakeWordDetector


def _send_tabby_wake(transcript: str, score: float) -> bool:
    result=send_command({
        "command":"wake",
        "source":"protocol7",
        "transcript":str(transcript or "")[:300],
        "score":float(score),
    }, timeout=0.45)
    return bool(result.get("ok"))


class WakeRuntime:
    """Protocol7's only Tabby responsibility: recognize the wake phrase.

    Tabby owns all UI, ChatGPT/Zen state, text input, audio animation and
    lifecycle.  This runtime deliberately has no companion presentation state.
    """

    def __init__(self, config: dict, transcriber, busy=None):
        self.config = config
        self.enabled = bool(config.get("companion_wake_enabled", True))
        self._external_busy = busy or (lambda: False)
        self.wake = WakeWordDetector(
            config,
            self._on_wake,
            transcriber=transcriber,
            busy=lambda: bool(self._external_busy()),
        )

    def _on_wake(self, transcript: str, score: float) -> None:
        if not self.enabled:
            return
        if _send_tabby_wake(transcript, score):
            return
        # Recovery path if the Tabby backend is still starting/reloading.
        # Call Tabby's own CLI/socket control plane directly; Protocol7 still
        # owns no Tabby UI/runtime state.
        try:
            tabby_ctl = Path.home() / ".local/share/caelestia/plugins/tabby/tabbyctl.py"
            subprocess.Popen(
                ["/usr/bin/python3", str(tabby_ctl), "wake"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass

    def start(self) -> None:
        if self.enabled:
            self.wake.start()

    def stop(self) -> None:
        self.wake.stop()
