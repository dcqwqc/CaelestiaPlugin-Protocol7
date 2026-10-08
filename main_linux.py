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
        self._live_stop = threading.Event()
        self._live_thread = None
        self._dictation_lock = threading.RLock()
        self.control = ControlServer(self._handle_control_command)
        self.tabby_wake = WakeRuntime(
            self.config,
            transcriber=self.whisper_engine,
            busy=lambda: bool(self.is_active or self.is_processing),
        )

        # KEY_LEFTCTRL = 29.
        self.hotkey = HotkeyListener(
            self.config.get("hotkey_keycode", 29),
            self.on_hotkey_trigger,
        )

    def _dictation_status(self):
        return {
            "ok": True,
            "active": bool(self.is_active),
            "processing": bool(self.is_processing),
        }

    def _toggle_dictation(self, source="hotkey"):
        with self._dictation_lock:
            log_debug(f"DICTATION TOGGLE source={source}")

            if self.is_processing:
                log_debug("Dictation cancelled during processing")
                self.cancel_requested = True
                self.is_processing = False
                self.ui_manager.hide()
                return self._dictation_status()

            if not self.is_active:
                log_debug("Dictation started")
                self.is_active = True
                self.cancel_requested = False
                self.audio_recorder.start_recording()
                self.ui_manager.show()
                return self._dictation_status()

            log_debug("Dictation stopped")
            self.is_active = False
            self._live_stop.set()
            self.is_processing = True
            self.ui_manager.set_processing_state()

            # Transcription and LLM work stay off the trigger thread.
            threading.Thread(target=self.process_audio, daemon=True).start()
            return self._dictation_status()

    def _handle_control_command(self, command):
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
                if self.is_processing:
                    return self._toggle_dictation("control-cancel")
                return self._dictation_status()
        return {"ok": False, "error": "unsupported command"}

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
        """Publish a best-effort partial transcript every two seconds."""
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

    def process_audio(self):
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
                transcribe_start = time.time()
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

                    if self.cancel_requested:
                        log_debug("Discarding transcription: cancelled by hotkey")
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

    def _active_paste_command(self):
        """Paste shortcut for ordinary GUI apps only."""
        return ["wtype", "-M", "ctrl", "v", "-m", "ctrl"]

    def _fast_clipboard_paste(self, text):
        """Paste a long transcript atomically, then restore the previous clipboard.

        Long virtual-keyboard strings are inherently O(characters) and were
        taking multiple seconds. Clipboard transfer makes the long path nearly
        constant-time while short utterances still keep the visible typing
        effect through wtype.
        """
        if self._focused_is_terminal():
            return False

        env = _clean_child_env()
        previous = None

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

            subprocess.run(self._active_paste_command(), check=True, timeout=0.5, env=env)

            # Give the focused client enough time to request the selection before
            # restoring the user's clipboard. This is still far below the old
            # multi-second character injection path.
            import time
            time.sleep(0.07)

            if previous is not None:
                mime, data = previous
                subprocess.run(
                    ["wl-copy", "--sensitive", "--type", mime],
                    input=data,
                    timeout=0.35,
                    env=env,
                )
            return True
        except Exception:
            return False

    def paste_text(self, text):
        clean = text.replace(chr(13), " ").replace(chr(10), " ")
        try:
            # Preserve the satisfying typing animation for short phrases. For
            # longer dictations use an atomic clipboard paste: this removes the
            # per-character cost that made a ~500-character utterance take ~3s.
            if len(clean) >= 64 and self._fast_clipboard_paste(clean):
                return

            safe_timeout = max(3.0, min(15.0, len(clean) * 0.02))
            if len(clean) >= 64 and self._focused_is_terminal():
                log_debug("Safe terminal output: plain wtype (no synthetic modifiers)")

            subprocess.run(
                ["wtype", "--", clean],
                check=True,
                timeout=safe_timeout,
                env=_clean_child_env(),
            )
        except Exception as error:
            log_debug(f"Error pasting text with wtype: {error}")

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

        self.control.start()
        self.tabby_wake.start()
        self.hotkey.start()
        self.tray.start()

        try:
            self.ui_manager.run()
        except KeyboardInterrupt:
            log_debug("Exiting...")
        finally:
            self.tabby_wake.stop()
            self.control.stop()
            self.hotkey.stop()
            self.tray.stop()
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
