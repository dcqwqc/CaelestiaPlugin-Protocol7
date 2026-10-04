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
        self.webview_debug = bool(config.get("companion_webview_debug", False))
        self._external_busy = busy or (lambda: False)
        self._voice_active = False
        self._monitor_thread: threading.Thread | None = None
        self._login_monitor_thread: threading.Thread | None = None
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
        self.voice.hide()
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

    def _set_listening(self) -> None:
        self._voice_active = True
        self.voice.hide()
        self.state.command({"command": "clear"})
        self.state.set_state("listening")
        self._ensure_voice_monitor()

    def _show_login_state(self) -> None:
        self._voice_active = False
        self.state.set_state("approval")
        self.state.command({"command": "clear"})
        self.state.command({
            "command": "text",
            "title": "One-time sign in",
            "text": "Sign in once. Tabby will close this automatically and start Voice when the session is ready.",
        })
        self.voice.show_login()
        self._ensure_login_monitor()

    def _handle_activation_result(self, result: dict) -> bool:
        phase = str(result.get("phase", ""))
        outcome = str(result.get("result", ""))
        if result.get("ok") and (phase == "active" or outcome in {"active", "already-active"}):
            self._set_listening()
            return True
        if phase == "needs-login" or outcome == "needs-login":
            self._show_login_state()
            return True
        return False

    def _activate_voice(self) -> None:
        result = self.voice.activate()
        if self._handle_activation_result(result):
            return
        self.state.set_state("error")
        self._schedule_idle(3.0)

    def _ensure_login_monitor(self) -> None:
        if self._login_monitor_thread and self._login_monitor_thread.is_alive():
            return
        self._login_monitor_thread = threading.Thread(
            target=self._monitor_login,
            name="hey-tabby-login-monitor",
            daemon=True,
        )
        self._login_monitor_thread.start()

    def _monitor_login(self) -> None:
        # A first-time OAuth/password flow may involve several redirects or a
        # child WebKit window. Keep watching the *main* persistent ChatGPT
        # surface until it is both authenticated and has a Voice control.
        deadline = time.monotonic() + 300.0
        while not self._monitor_stop.is_set() and time.monotonic() < deadline:
            snapshot = self.state.snapshot()
            if not snapshot.get("summoned") or self._voice_active:
                return

            status = self.voice.status()
            phase = str(status.get("phase", ""))
            if phase == "needs-login" or phase in {"loading", "unavailable", ""}:
                self._monitor_stop.wait(0.6)
                continue

            if phase in {"ready", "active"}:
                # Auth is genuinely complete and the real Voice surface exists.
                self.voice.hide()
                self.state.command({"command": "clear"})
                self.state.set_state("wake")
                result = self.voice.activate()
                if self._handle_activation_result(result):
                    return
                self.state.set_state("error")
                self._schedule_idle(3.0)
                return

            self._monitor_stop.wait(0.6)

        if self.state.snapshot().get("summoned") and not self._voice_active:
            self.state.set_state("error")
            self._schedule_idle(3.0)

    def _ensure_voice_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor_voice,
            name="hey-tabby-voice-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_voice(self) -> None:
        inactive_polls = 0
        while not self._monitor_stop.is_set() and self._voice_active:
            status = self.voice.status()
            phase = str(status.get("phase", ""))
            if phase == "active":
                inactive_polls = 0
            elif phase == "needs-login":
                self._voice_active = False
                self._show_login_state()
                return
            else:
                inactive_polls += 1
                if inactive_polls >= 3:
                    self._return_idle()
                    return
            self._monitor_stop.wait(0.8)

    def start(self) -> None:
        self._monitor_stop.clear()
        self.ipc.start()
        # Sync the persistent ChatGPT runtime with the plugin debug toggle.
        # Enabling debug intentionally starts the runtime immediately so the
        # user can inspect it before saying the wake phrase.
        if self.webview_debug:
            self.voice.set_debug(True)
        else:
            # If an older runtime is already alive, hide it again. Do not
            # launch a new process just to apply the default hidden state.
            if self.voice.request("ping", ensure=False, timeout=0.2).get("ok"):
                self.voice.set_debug(False)
        if self.enabled:
            self.wake.start()

    def stop(self) -> None:
        self._monitor_stop.set()
        self._voice_active = False
        if self._hide_timer is not None:
            self._hide_timer.cancel()
        self.wake.stop()
        self.ipc.stop()
