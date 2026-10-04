from __future__ import annotations

import threading

from companion.browser_bridge import BrowserBridge
from companion.ipc import CompanionIPCServer
from companion.state import CompanionStatePublisher
from companion.wake_word import WakeWordDetector


class CompanionRuntime:
    def __init__(self, config: dict, transcriber, busy=None):
        self.config = config
        self.enabled = bool(config.get("companion_enabled", True))
        self.state = CompanionStatePublisher(enabled=self.enabled)
        self.ipc = CompanionIPCServer(self.state.command)
        self.browser = BrowserBridge(config)
        self.wake = WakeWordDetector(config, self._on_wake, transcriber=transcriber, busy=busy)
        self._idle_timer: threading.Timer | None = None

    def _on_wake(self, transcript: str, score: float) -> None:
        if not self.enabled:
            return
        self.state.set_state("wake")
        threading.Thread(target=self.browser.activate, name="hey-tabby-browser", daemon=True).start()
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(6.0, self._return_idle)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _return_idle(self) -> None:
        snapshot = self.state.snapshot()
        if snapshot.get("state") == "wake" and not snapshot.get("whiteboardVisible"):
            self.state.set_state("idle")

    def start(self) -> None:
        self.ipc.start()
        if self.enabled:
            self.wake.start()

    def stop(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self.wake.stop()
        self.ipc.stop()
