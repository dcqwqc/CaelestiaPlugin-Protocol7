import sys
import threading
import subprocess
from datetime import datetime

from config import load_config
from hotkey_linux import HotkeyListener
from audio import AudioRecorder
from native_bridge import NativeUIBridge
from control_ipc import ControlServer
from companion.wake_runtime import WakeRuntime
from ghost_jobs import GhostJobs
from enter_intercept import EnterInterceptor
from ghost_wave import capture_target, hypr_json


def log_debug(msg):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    log_path = "/tmp/protocol7_debug.log"
    try:
        with open(log_path, "a") as f:
            f.write(f"[{stamp}] [MAIN_LINUX] {msg}\n")
    except Exception:
        pass


def _clean_child_env():
    # Legacy releases preloaded gtk4-layer-shell into the main process. Keep
    # children clean even if an old launcher still supplied those variables.
    import os

    env = os.environ.copy()
    env.pop("LD_PRELOAD", None)
    env.pop("PROTOCOL7_PRELOADED", None)
    return env


class TrayIcon:
    def __init__(self, app):
        self.app = app
        self.process = None
        self.tray_log = None

    def start(self):
        if not self.app.config.get("show_tray", False):
            return

        import os

        app_dir = os.path.dirname(os.path.abspath(__file__))
        tray_path = os.path.join(app_dir, "tray.py")
        self.tray_log = open("/tmp/protocol7_tray.log", "w")
        self.process = subprocess.Popen(
            [sys.executable, tray_path, str(os.getpid())],
            cwd=app_dir,
            stdout=self.tray_log,
            stderr=subprocess.STDOUT,
            env=_clean_child_env(),
        )

    def stop(self):
        if self.process:
            self.process.terminate()
        if self.tray_log:
            self.tray_log.close()


class Protocol7App:
    def __init__(self):
        self.config = load_config()
        self.audio_recorder = AudioRecorder(device_id=self.config.get("input_device"))

        # Publish an idle native-UI state before importing the heavy inference
        # stacks. This lets Caelestia attach immediately after login/reload while
        # Whisper/LLM modules continue initialising behind it.
        self.ui_manager = NativeUIBridge(self.config, self.audio_recorder)

        from whisper_engine import WhisperEngine
        from llm_rewriter import LLMRewriter

        self.whisper_engine = WhisperEngine(self.config)
        # The always-on wake detector must not share Whisper's mutable model
        # and inference state with final dictation requests.
        self.wake_whisper_engine = WhisperEngine({**self.config, "use_groq": False, "cpu_threads": 2})
        self.llm_rewriter = LLMRewriter(self.config)
        self.tray = TrayIcon(self)

        # Preserve the existing decoupled wtype helper, but never give it any
        # stale GTK/layer-shell preload state.
        import os
        import psutil

        daemon_running = False
        for process in psutil.process_iter(["cmdline"]):
            try:
                cmd = process.info["cmdline"]
                if cmd and "wtype_daemon.py" in " ".join(cmd):
                    daemon_running = True
                    break
            except Exception:
                pass

        if not daemon_running:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            daemon_path = os.path.join(app_dir, "wtype_daemon.py")
            subprocess.Popen(
                [sys.executable, daemon_path],
                start_new_session=True,
                env=_clean_child_env(),
            )

        self.is_active = False
        self.is_processing = False
        self.cancel_requested = False
        self._dictation_generation = 0
        self.processing_timeout_seconds = 18.0
        self._live_stop = threading.Event()
        self._live_thread = None
        self._dictation_lock = threading.RLock()
        self.enter_interceptor = EnterInterceptor()
        self._ghost = GhostJobs(self)
        self._recording_target = None
        # Opt-in, display-only live preview for the current recording only
        # (None when disabled). It stops with the recording; the final always
        # transcribes the complete audio.
        self._stream_session = None
        self.control = ControlServer(self._handle_control_command)
        self.tabby_wake = WakeRuntime(
            self.config,
            transcriber=self.wake_whisper_engine,
            busy=lambda: bool(self.is_active or self.is_processing),
        )

        # KEY_LEFTCTRL = 29.
        self.hotkey = HotkeyListener(
            self.config.get("hotkey_keycode", 29),
            self.on_hotkey_trigger,
        )

    def _dictation_status(self):
        counts = self._ghost.counts() if hasattr(self, "_ghost") else {"processing": int(self.is_processing), "ready": 0}
        return {
            "ok": True,
            "active": bool(self.is_active),
            "processing": bool(counts["processing"]),
            "pending": counts["processing"],
            "ready": counts["ready"],
        }

    def _start_stream_session(self):
        """Attach an opt-in Groq preview session to the recording just started.

        Disabled or misconfigured installations keep the unchanged batch
        path; nothing here may fail the recording itself. Previews reuse the
        final dictation's Groq client and model (no other credential).
        """
        self._stream_session = None
        try:
            import streaming_stt

            reason = streaming_stt.availability(self.config)
            if reason is not None:
                if self.config.get("streaming_stt_enabled", False):
                    log_debug(f"Live preview inactive: {reason}")
                return
            session = streaming_stt.create_session(
                self.config,
                input_rate=getattr(self.audio_recorder, "actual_sample_rate", 16000),
                client_provider=getattr(self.whisper_engine, "groq_preview_client", None),
                log=lambda message: log_debug(f"[LIVE_STT] {message}"),
            )
            if session is None:
                return
            session.set_partial_callback(
                lambda text, live=session: self._on_stream_partial(live, text)
            )
            self._stream_session = session
            session.start()
            self.audio_recorder.attach_stream_listener(session.push_audio)
        except Exception as error:
            log_debug(f"Live preview unavailable ({type(error).__name__})")
            stale, self._stream_session = self._stream_session, None
            if stale is not None:
                self.audio_recorder.detach_stream_listener()
                stale.cancel()

    def _on_stream_partial(self, session, text):
        # Display only while recording: partials are never pasted, saved to
        # history or logged. Events from a finished session are ignored.
        if not self.is_active or self._stream_session is not session:
            return
        setter = getattr(self.ui_manager, "set_live_text", None)
        if setter is not None:
            try:
                setter(text)
            except Exception:
                pass

    def _toggle_ghost(self, source):
        # No cloud work happens here. Another Double-Ctrl while the detached
        # wave is visible dismisses it instead of starting a new recording.
        # Explicit "start" still permits back-to-back recordings on demand.
        with self._dictation_lock:
            if not self.is_active:
                if source != "control-start" and self._ghost.dismiss_visible():
                    self.ui_manager.hide()
                    log_debug(f"Ghost Wave dismissed via {source}; late result invalidated")
                    return self._dictation_status()
                # Capture the pointer at the hotkey press, before recorder
                # setup/UI rendering can shift timing or the user moves away.
                pointer_at_press = hypr_json("cursorpos")
                self.audio_recorder.start_recording()
                if not self.audio_recorder.is_recording:
                    self.ui_manager.hide()
                    return {"ok": False, "error": "microphone unavailable"}
                self.is_active = True
                self.ui_manager.show()
                self._recording_target = capture_target(pointer_at_press)
                # Chunks captured before this point are replayed on attach.
                self._start_stream_session()
                log_debug(f"Ghost recording started ({source})")
                return self._dictation_status()

            self.is_active = False
            stream, self._stream_session = self._stream_session, None
            audio = self.audio_recorder.stop_recording()
            if stream is not None:
                # Previews end with the recording (no request starts after
                # this) and never feed the final, which uses `audio` in full.
                stream.stop()
            target = self._recording_target
            self._recording_target = None
            # Publish the ghost before hiding the bottom recording chip:
            # ShellPanels can now animate its existing surface toward the
            # original window rather than flickering off and back on.
            if len(audio) < 8000:
                # A quick accidental toggle must not leave a permanent Retry
                # ghost floating beside the last application.
                if stream is not None:
                    stream.cancel()
                self.ui_manager.hide()
                log_debug("Ghost recording too short; closed without creating a job")
                return self._dictation_status()
            job = self._ghost.enqueue(
                audio, target, self.hotkey.focus_epoch(),
                getattr(self.hotkey, "non_enter_focus_epoch", lambda: None)(),
                stream=stream,
            )
            self.ui_manager.hide()
            if job is None:
                # Never discard a full recording silently on queue saturation.
                log_debug("Ghost queue full: recording could not be submitted")
                return {"ok": False, "error": "processing queue full"}
            log_debug(f"Ghost detached job={job.id}")
            return self._dictation_status()

    def _toggle_dictation(self, source="hotkey"):
        if hasattr(self, "_ghost"):
            return self._toggle_ghost(source)
        with self._dictation_lock:
            log_debug(f"DICTATION TOGGLE source={source}")

            if self.is_processing:
                log_debug("Dictation cancelled during processing")
                self.cancel_requested = True
                self._dictation_generation += 1
                self.is_processing = False
                self.ui_manager.hide()
                return self._dictation_status()

            if not self.is_active:
                log_debug("Dictation started")
                self._dictation_generation += 1
                self.is_active = True
                self.cancel_requested = False
                self.audio_recorder.start_recording()
                if not self.audio_recorder.is_recording:
                    self.is_active = False
                    log_debug("Input stream failed to start; aborting recording")
                    self.ui_manager.hide()
                    return {"ok": False, "active": False, "processing": False, "error": "microphone unavailable"}
                self.ui_manager.show()
                return self._dictation_status()

            log_debug("Dictation stopped")
            self.is_active = False
            self._live_stop.set()
            self.is_processing = True
            generation = self._dictation_generation
            self.ui_manager.set_processing_state()

            # An external request or inference library can occasionally stall.
            # Never leave the native HUD spinning forever; expired work is
            # invalidated so it cannot paste into a later dictation session.
            watchdog = threading.Timer(
                self.processing_timeout_seconds, self._expire_processing,
                args=(generation,),
            )
            watchdog.daemon = True
            watchdog.start()
            threading.Thread(target=self.process_audio, args=(generation,), daemon=True).start()
            return self._dictation_status()

    def _expire_processing(self, generation):
        with self._dictation_lock:
            if generation != self._dictation_generation or not self.is_processing:
                return
            log_debug(f"Transcription watchdog expired after {self.processing_timeout_seconds:.0f}s")
            self.cancel_requested = True
            self._dictation_generation += 1
            self.is_processing = False
            self.ui_manager.hide()

    def _handle_control_command(self, command):
        if ":" in command and hasattr(self, "_ghost"):
            action, job_id = command.split(":", 1)
            if action == "insert":
                return self._ghost.insert(job_id)
            if action == "cancel":
                return self._ghost.cancel(job_id)
            if action == "retry":
                return self._ghost.retry(job_id)
            return {"ok": False, "error": "unsupported action"}
        if command == "queue-send":
            return self._ghost.queue_send()
        if command == "status":
            with self._dictation_lock:
                return self._dictation_status()
        if command == "toggle":
            return self._toggle_dictation("control")
        if command == "start":
            with self._dictation_lock:
                if not self.is_active and not self.is_processing:
                    return self._toggle_dictation("control-start")
                return self._dictation_status()
        if command == "stop":
            with self._dictation_lock:
                if self.is_active:
                    return self._toggle_dictation("control-stop")
                if hasattr(self, "_ghost"):
                    if self._ghost.dismiss_visible():
                        self.ui_manager.hide()
                    return self._dictation_status()
                if self.is_processing:
                    return self._toggle_dictation("control-cancel")
                return self._dictation_status()
        return {"ok": False, "error": "unsupported command"}

    def send_enter(self):
        """Deliver exactly one Return *after* successful, settled text paste."""
        try:
            reply = subprocess.run(
                ["ydotool", "key", "28:1", "28:0"], capture_output=True,
                timeout=1.5, env=_clean_child_env(),
            )
            return reply.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def on_hotkey_trigger(self):
        return self._toggle_dictation("hotkey")

    def _start_live_preview(self):
        self._live_stop.clear()
        self._live_thread = threading.Thread(
            target=self._live_preview_loop,
            daemon=True,
        )
        self._live_thread.start()

    def _stop_live_preview(self):
        self._live_stop.set()
        if self._live_thread and self._live_thread.is_alive():
            self._live_thread.join(timeout=5)
        self._live_thread = None

    def _live_preview_loop(self):
        """Publish local partials only in explicit local Whisper mode."""
        if self.config.get("use_groq", False):
            # Groq is used for final dictation; do not show tiny.en hallucinations.
            return
        if self._live_stop.wait(1.2):
            return

        while not self._live_stop.is_set() and self.is_active:
            audio_data = self.audio_recorder.get_recording_snapshot()
            if len(audio_data) >= 8000:
                try:
                    text = self.whisper_engine.transcribe(audio_data, live=True)
                    if text and not self._live_stop.is_set():
                        self.ui_manager.set_live_text(text)
                except Exception as error:
                    log_debug(f"Live transcription failed: {error}")
            self._live_stop.wait(2.0)

    def process_audio(self, generation):
        import time
        import traceback

        start_time = time.time()

        try:
            self._stop_live_preview()
            audio_data = self.audio_recorder.stop_recording()
            audio_time = time.time()
            log_debug(
                f"Audio collected and resampled in {audio_time - start_time:.2f}s"
            )

            if len(audio_data) > 0:
                if generation != self._dictation_generation:
                    log_debug("Discarding cancelled audio before transcription")
                    return
                transcribe_start = time.time()
                log_debug("Starting final Whisper/Groq transcription")
                text = self.whisper_engine.transcribe(audio_data)
                transcribe_end = time.time()
                log_debug(
                    f"Whisper Transcription took {transcribe_end - transcribe_start:.2f}s"
                )

                if text:
                    log_debug(f"Transcribed: {text}")

                    llm_start = time.time()
                    clean_text = self.llm_rewriter.rewrite(text)
                    llm_end = time.time()
                    log_debug(
                        f"LLM Rewriting took {llm_end - llm_start:.2f}s"
                    )

                    if clean_text != text:
                        log_debug(f"Rewritten to: {clean_text}")

                    if self.cancel_requested or generation != self._dictation_generation:
                        log_debug("Discarding transcription: cancelled, timed out, or superseded")
                        return

                    # Double-Control is already released by the time final
                    # transcription finishes. Keep only a tiny guard instead of
                    # the old 400 ms pause so output begins almost immediately.
                    time.sleep(0.035)

                    if clean_text.strip():
                        from config import add_history

                        add_history(clean_text)
                        paste_start = time.time()
                        self.paste_text(clean_text)
                        log_debug(
                            f"Paste operation took {time.time() - paste_start:.2f}s"
                        )

                    log_debug(
                        f"Total Pipeline Execution Time: {time.time() - start_time:.2f}s"
                    )
                    return

                log_debug("No text transcribed.")
            else:
                log_debug("No audio data recorded.")

            log_debug(
                f"Pipeline Failed/Empty, Total Time: {time.time() - start_time:.2f}s"
            )

        except Exception as error:
            log_debug(f"Fatal error in audio processing pipeline: {error}")
            log_debug(traceback.format_exc())
        finally:
            with self._dictation_lock:
                if generation == self._dictation_generation:
                    self.is_processing = False
                    self.ui_manager.hide()

    def _focused_is_terminal(self):
        """Return True when the currently focused Hyprland window is a terminal.

        Terminal long-paste deliberately avoids synthetic Ctrl/Shift chords.
        Protocol7 uses Double-Control as its hotkey, and mixing the physical
        Control release with a virtual Ctrl+Shift+V could transiently form
        compositor shortcuts (for example Caelestia's Ctrl+Shift+Escape sysmon
        workspace binding). Plain text injection is slower but modifier-safe.
        """
        import json

        terminal_markers = (
            "ghostty", "foot", "alacritty", "wezterm", "konsole", "xterm", "org.wezfurlong.wezterm",
        )
        try:
            result = subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                capture_output=True,
                text=True,
                timeout=0.15,
                env=_clean_child_env(),
            )
            info = json.loads(result.stdout or "{}")
            app = f"{info.get('class', '')} {info.get('initialClass', '')}".lower()
            return any(marker in app for marker in terminal_markers)
        except Exception:
            return False

    def _focused_is_sumi(self):
        """Use hardware-equivalent key injection for SUMI's embedded editor."""
        import json
        try:
            result = subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                capture_output=True, text=True, timeout=0.3, env=_clean_child_env(),
            )
            return json.loads(result.stdout or "{}").get("class", "").lower() == "sumi"
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def _active_paste_command(self):
        """Use the stable hardware-equivalent uinput paste shortcut."""
        import os
        import shutil
        if shutil.which("ydotool"):
            sock = f"/run/user/{os.getuid()}/.ydotool_socket"
            if os.path.exists(sock):
                if self._focused_is_terminal():
                    # Terminals use Ctrl+Shift+V, not Ctrl+V.
                    return ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]
                return ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]
        # Never fall back to wtype: this compositor reinterprets its
        # per-call keymap as digits in XWayland/Electron applications.
        raise RuntimeError("Hardware keyboard injection unavailable (ydotoold not running)")

    def _fast_clipboard_paste(self, text, *, allow_terminal=False):
        """Paste a long transcript atomically, then restore the previous clipboard.

        Long virtual-keyboard strings are inherently O(characters) and were
        taking multiple seconds. Clipboard transfer makes the long path nearly
        constant-time while short utterances still keep the visible typing
        effect through wtype.
        """
        if self._focused_is_terminal() and not allow_terminal:
            return False

        env = _clean_child_env()
        import os
        env["YDOTOOL_SOCKET"] = f"/run/user/{os.getuid()}/.ydotool_socket"
        previous = None
        copied_ok = False

        try:
            types = subprocess.run(
                ["wl-paste", "--list-types"],
                capture_output=True,
                text=True,
                timeout=0.2,
                env=env,
            )
            available = [line.strip() for line in types.stdout.splitlines() if line.strip()]
            preferred = [
                "text/plain;charset=utf-8", "text/plain", "UTF8_STRING",
                "image/png", "image/jpeg",
            ]
            mime = next((m for m in preferred if m in available), available[0] if available else None)
            if mime:
                snap = subprocess.run(
                    ["wl-paste", "--type", mime],
                    capture_output=True,
                    timeout=0.25,
                    env=env,
                )
                if snap.returncode == 0:
                    previous = (mime, snap.stdout)
        except Exception:
            previous = None

        payload = text.replace(chr(13), " ").replace(chr(10), " ").encode("utf-8")
        try:
            copied = subprocess.run(
                ["wl-copy", "--sensitive", "--type", "text/plain;charset=utf-8"],
                input=payload,
                timeout=0.35,
                env=env,
            )
            if copied.returncode != 0:
                return False
            copied_ok = True

            # The compositor may accept virtual-keyboard events without delivering
            # them to SUMI's editor. ydotool uses the already running uinput daemon.
            subprocess.run(self._active_paste_command(), check=True, timeout=1.0, env=env)
            return True
        except Exception as error:
            log_debug(f"Clipboard paste failed: {type(error).__name__}")
            return False
        finally:
            # Qt/WebKit may fetch Wayland clipboard data asynchronously. Always
            # restore the existing selection, even if the key injection failed.
            if copied_ok and previous is not None:
                import time
                time.sleep(0.65 if allow_terminal else 0.22)
                mime, data = previous
                try:
                    subprocess.run(
                        ["wl-copy", "--sensitive", "--type", mime],
                        input=data, timeout=0.5, env=env,
                    )
                except Exception:
                    log_debug("Could not restore previous clipboard selection")

    def paste_text(self, text):
        clean = text.replace(chr(13), " ").replace(chr(10), " ")
        try:
            # Prefer real keycodes without touching the clipboard for ordinary
            # ASCII, regardless of length. Use text-only clipboard paste for
            # SUMI's embedded editor and Unicode unsupported by ydotool.
            if self._focused_is_sumi() and self._fast_clipboard_paste(clean):
                return True

            # ydotool emits regular kernel uinput keycodes, avoiding wtype's
            # transient Wayland keymap. Verified on this host: wtype produced
            # "5q2w" for alphabetic text; ydotool matched the entire phrase.
            # ydotool cannot represent arbitrary Unicode (umlauts, CJK,
            # emoji). Use an explicitly text-only clipboard transfer then.
            if not clean.isascii():
                log_debug("Text injection method=clipboard-unicode")
                return self._fast_clipboard_paste(clean, allow_terminal=True)

            safe_timeout = max(8.0, min(90.0, 5.0 + len(clean) * 0.03))
            log_debug(
                f"Text injection method=ydotool chars={len(clean)} "
                f"letters={sum(c.isalpha() for c in clean)} "
                f"digits={sum(c.isdigit() for c in clean)}"
            )
            subprocess.run(
                ["ydotool", "type", "-d", "3", "-H", "3", "-f", "-"],
                input=clean.encode("utf-8"),
                check=True,
                timeout=safe_timeout,
                env=_clean_child_env(),
            )
            return True
        except Exception as error:
            log_debug(f"Text injection failed ({type(error).__name__}): {error}")
            return False

    def run(self):
        log_debug("Starting Protocol-7 native Caelestia backend...")

        threading.Thread(
            target=self.whisper_engine._load_model,
            daemon=True,
        ).start()
        threading.Thread(
            target=self.llm_rewriter.load_model,
            daemon=True,
        ).start()
        # Loading the wake model on the first spoken phrase made the first
        # Hey Loom activation unusually slow. Warm it asynchronously now;
        # UI/control registration below never waits for its completion.
        if self.tabby_wake.enabled:
            threading.Thread(
                target=self.wake_whisper_engine._load_model,
                name="protocol7-wake-prewarm",
                daemon=True,
            ).start()

        self.control.start()
        self.tabby_wake.start()
        self.hotkey.start()
        self.tray.start()

        try:
            self.ui_manager.run()
        except KeyboardInterrupt:
            log_debug("Exiting...")
        finally:
            stream, self._stream_session = getattr(self, "_stream_session", None), None
            if stream is not None:
                stream.cancel()
            self.tabby_wake.stop()
            self.control.stop()
            self.hotkey.stop()
            self.tray.stop()
            if hasattr(self, "enter_interceptor"):
                self.enter_interceptor.disarm()
            self.ui_manager.quit()

    def quit(self):
        self.ui_manager.quit()

    def open_settings(self):
        subprocess.Popen([sys.executable, sys.argv[0], "--settings"])


if __name__ == "__main__":
    config = load_config()

    if "--settings" in sys.argv:
        from settings_ui_linux import run_settings

        run_settings(config)
        sys.exit(0)

    app = Protocol7App()
    app.run()
