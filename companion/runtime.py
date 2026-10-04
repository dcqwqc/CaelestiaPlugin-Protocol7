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
        try:
            timeout = float(config.get("companion_auto_hide_seconds", 5.0))
        except (TypeError, ValueError):
            timeout = 5.0
        self.inactivity_timeout = max(2.0, min(30.0, timeout))
        self._external_busy = busy or (lambda: False)
        self._voice_active = False
        self._activation_in_progress = False
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

    def _schedule_idle(self, delay: float | None = None) -> None:
        if delay is None:
            delay = self.inactivity_timeout
        if self._hide_timer is not None:
            self._hide_timer.cancel()
        self._hide_timer = threading.Timer(delay, self._idle_timeout)
        self._hide_timer.daemon = True
        self._hide_timer.start()

    def _idle_timeout(self) -> None:
        # Do not interpret real startup work as inactivity. If ChatGPT is still
        # transitioning, give it another short grace window; active Voice and
        # visible sign-in surfaces own their own lifecycle.
        if self._voice_active:
            self._cancel_idle_timer()
            return
        if self._activation_in_progress:
            self._hide_timer = None
            self._schedule_idle()
            return
        snapshot = self.state.snapshot()
        if not snapshot.get("summoned"):
            self._cancel_idle_timer()
            return
        self._return_idle()

    def _cancel_idle_timer(self) -> None:
        if self._hide_timer is not None:
            self._hide_timer.cancel()
            self._hide_timer = None

    def _return_idle(self) -> None:
        self._cancel_idle_timer()
        self._voice_active = False
        self.voice.hide()
        self.state.command({"command": "clear"})
        self.state.set_state("idle")
        self.state.set_summoned(False)

    def _on_wake(self, transcript: str, score: float) -> None:
        if not self.enabled:
            return
        self._cancel_idle_timer()
        self.state.set_summoned(True)
        self.state.set_state("wake")
        # If wake recognition succeeded but ChatGPT never transitions into a
        # meaningful setup/Voice state, do not leave the face hanging around.
        self._schedule_idle(self.inactivity_timeout)
        threading.Thread(target=self._activate_voice, name="hey-tabby-voice", daemon=True).start()

    def _set_listening(self) -> None:
        self._cancel_idle_timer()
        self._voice_active = True
        self.voice.hide()
        self.state.command({"command": "clear"})
        self.state.set_state("listening")
        self._ensure_voice_monitor()

    def _show_login_state(self) -> None:
        # A visible sign-in surface is active user work, not inactivity. The
        # login monitor owns timeout/close behavior while this state is open.
        self._cancel_idle_timer()
        self._voice_active = False
        self.state.set_state("approval")
        self.state.command({"command": "clear"})
        self.state.command({
            "command": "text",
            "title": "One-time sign in",
            "text": "Sign in once. When setup is complete, the window and Tabby will close. Say Hey Tabby again to start Voice.",
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
        self._activation_in_progress = True
        try:
            result = self.voice.activate()
            if self._handle_activation_result(result):
                return
            self.state.set_state("error")
            self._schedule_idle()
        finally:
            self._activation_in_progress = False

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
        # First-time auth can involve redirects and child windows. Keep the
        # companion around while the setup UI is genuinely visible, but if the
        # user closes that setup surface before Voice becomes active, dismiss
        # Tabby immediately instead of leaving an orphaned approval card.
        deadline = time.monotonic() + 300.0
        seen_setup_visible = False
        no_ui_since: float | None = None

        while not self._monitor_stop.is_set() and time.monotonic() < deadline:
            snapshot = self.state.snapshot()
            if not snapshot.get("summoned") or self._voice_active:
                return

            status = self.voice.status()
            phase = str(status.get("phase", ""))
            setup_visible = bool(status.get("setupVisible") or status.get("loginVisible"))

            if setup_visible:
                seen_setup_visible = True
                no_ui_since = None
            elif seen_setup_visible:
                # The one-time browser/setup window was open and is now gone.
                # Setup is a separate task: browser gone means Tabby goes too.
                # A future wake uses the now-persisted ChatGPT session.
                self._return_idle()
                return
            elif no_ui_since is None:
                no_ui_since = time.monotonic()

            if phase == "active":
                # If Voice was explicitly started from the visible Voice-engine tab,
                # there is a real live session and Tabby should represent it.
                self.voice.hide()
                self._set_listening()
                return

            if phase == "ready":
                # Authentication/setup succeeded. End setup cleanly instead of
                # unexpectedly starting a Voice call. The next wake phrase is
                # the normal hidden, already-authenticated launch.
                self.voice.hide()
                self._return_idle()
                return

            if phase == "needs-login":
                # Give the async GTK show call a short chance to materialize.
                # If no setup surface appears at all, auto-dismiss as stale.
                if no_ui_since is not None and time.monotonic() - no_ui_since >= self.inactivity_timeout:
                    self._return_idle()
                    return
            elif phase in {"loading", "unavailable", ""}:
                if no_ui_since is not None and time.monotonic() - no_ui_since >= self.inactivity_timeout:
                    self._return_idle()
                    return

            self._monitor_stop.wait(0.5)

        if self.state.snapshot().get("summoned") and not self._voice_active:
            self._return_idle()

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
        inactive_since: float | None = None
        while not self._monitor_stop.is_set() and self._voice_active:
            status = self.voice.status()
            phase = str(status.get("phase", ""))
            if phase == "active":
                inactive_since = None
            elif phase == "needs-login":
                self._voice_active = False
                self._show_login_state()
                return
            else:
                if inactive_since is None:
                    inactive_since = time.monotonic()
                elif time.monotonic() - inactive_since >= self.inactivity_timeout:
                    self._return_idle()
                    return
            self._monitor_stop.wait(0.5)

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
        self._activation_in_progress = False
        if self._hide_timer is not None:
            self._hide_timer.cancel()
        self.wake.stop()
        self.ipc.stop()
