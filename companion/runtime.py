from __future__ import annotations

import threading
import time

from companion.ipc import CompanionIPCServer
from companion.state import CompanionStatePublisher
from companion.voice_runtime_client import VoiceRuntimeClient
from companion.wake_word import WakeWordDetector


class CompanionRuntime:
    def __init__(self, config: dict, transcriber, busy=None):
        self.config = config
        self.enabled = bool(config.get("companion_enabled", True))
        self.state = CompanionStatePublisher(enabled=self.enabled)
        self.ipc = CompanionIPCServer(self.state.command)
        self.voice = VoiceRuntimeClient(config)
        self._external_busy = busy or (lambda: False)
        self._voice_active = False
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        self._hide_timer: threading.Timer | None = None
        self.wake = WakeWordDetector(
            config,
            self._on_wake,
            transcriber=transcriber,
            busy=lambda: bool(self._voice_active or self._external_busy()),
        )

    def _schedule_idle(self, delay: float = 3.0) -> None:
        if self._hide_timer is not None:
            self._hide_timer.cancel()
        self._hide_timer = threading.Timer(delay, self._return_idle)
        self._hide_timer.daemon = True
        self._hide_timer.start()

    def _return_idle(self) -> None:
        self._voice_active = False
        self.state.command({"command": "clear"})
        self.state.set_state("idle")
        self.state.set_summoned(False)

    def _on_wake(self, transcript: str, score: float) -> None:
        if not self.enabled:
            return
        if self._hide_timer is not None:
            self._hide_timer.cancel()
        self.state.set_summoned(True)
        self.state.set_state("wake")
        threading.Thread(target=self._activate_voice, name="hey-tabby-voice", daemon=True).start()

    def _activate_voice(self) -> None:
        result = self.voice.activate()
        outcome = str(result.get("result", ""))
        if result.get("ok") and outcome in {"clicked", "already-active"}:
            self._voice_active = True
            self.state.command({"command": "clear"})
            self.state.set_state("listening")
            self._ensure_monitor()
            return
        if outcome == "needs-login":
            self._voice_active = False
            self.state.set_state("approval")
            self.state.command({
                "command": "text",
                "title": "One-time sign in",
                "text": "Sign in to ChatGPT in the Tabby window. After that, it stays hidden.",
            })
            return
        if outcome == "loading":
            time.sleep(1.0)
            retry = self.voice.activate()
            retry_outcome = str(retry.get("result", ""))
            if retry.get("ok") and retry_outcome in {"clicked", "already-active"}:
                self._voice_active = True
                self.state.command({"command": "clear"})
                self.state.set_state("listening")
                self._ensure_monitor()
                return
            if retry_outcome == "needs-login":
                self.state.set_state("approval")
                return
        self.state.set_state("error")
        self._schedule_idle(3.0)

    def _ensure_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_voice, name="hey-tabby-voice-monitor", daemon=True)
        self._monitor_thread.start()

    def _monitor_voice(self) -> None:
        seen_active = False
        inactive_polls = 0
        grace_deadline = time.monotonic() + 12.0
        while not self._monitor_stop.is_set() and self._voice_active:
            result = self.voice.status()
            if result.get("loggedOut"):
                self._voice_active = False
                self.state.set_state("approval")
                return
            if result.get("active"):
                seen_active = True
                inactive_polls = 0
                if self.state.snapshot().get("state") in {"wake", "idle", "asleep"}:
                    self.state.set_state("listening")
            else:
                inactive_polls += 1
                if seen_active and inactive_polls >= 3:
                    self._return_idle()
                    return
                if not seen_active and time.monotonic() > grace_deadline:
                    self._return_idle()
                    return
            self._monitor_stop.wait(0.8)

    def start(self) -> None:
        self.ipc.start()
        if self.enabled:
            self.wake.start()

    def stop(self) -> None:
        self._monitor_stop.set()
        self._voice_active = False
        if self._hide_timer is not None:
            self._hide_timer.cancel()
        self.wake.stop()
        self.ipc.stop()
