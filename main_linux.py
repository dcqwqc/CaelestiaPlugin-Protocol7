import sys
import threading
import subprocess
from datetime import datetime

from config import load_config
from hotkey_linux import HotkeyListener
from audio import AudioRecorder
from whisper_engine import WhisperEngine
from native_bridge import NativeUIBridge
from llm_rewriter import LLMRewriter


def log_debug(msg):
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    try:
        with open("/tmp/protocol7_debug.log", "a") as f:
            f.write(f"[{stamp}] [MAIN_LINUX] {msg}\n")
    except Exception:
        pass


def _clean_child_env():
    """Never leak the legacy gtk4-layer-shell preload into child processes."""
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
        # The Caelestia plugin sets this false. Keep True as the standalone
        # fallback so running Protocol7 outside the shell retains old behaviour.
        if not self.app.config.get("show_tray", True):
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
        self.whisper_engine = WhisperEngine(self.config)
        self.llm_rewriter = LLMRewriter(self.config)
        self.ui_manager = NativeUIBridge(self.config, self.audio_recorder)
        self.tray = TrayIcon(self)

        # Preserve the existing decoupled wtype helper, but do not give it any
        # stale GTK/layer-shell environment from older Protocol7 launchers.
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

        self.hotkey = HotkeyListener(
            self.config.get("hotkey_keycode", 29),
            self.on_hotkey_trigger,
        )

    def on_hotkey_trigger(self):
        log_debug("HOTKEY TRIGGERED")

        if self.is_processing:
            log_debug("Dictation cancelled during processing")
            self.cancel_requested = True
            self.is_processing = False
            self.ui_manager.hide()
            return

        if not self.is_active:
            log_debug("Dictation started")
            self.is_active = True
            self.cancel_requested = False
            self.audio_recorder.start_recording()
            self.ui_manager.show()
            return

        log_debug("Dictation stopped")
        self.is_active = False
        self._live_stop.set()
        self.is_processing = True
        self.ui_manager.set_processing_state()
        threading.Thread(target=self.process_audio, daemon=True).start()

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
                    log_debug(f"LLM Rewriting took {llm_end - llm_start:.2f}s")

                    if clean_text != text:
                        log_debug(f"Rewritten to: {clean_text}")

                    if self.cancel_requested:
                        log_debug("Discarding transcription: cancelled by hotkey")
                        return

                    # Let the physical modifier keys come up before wtype injects
                    # the result into the previously focused application.
                    time.sleep(0.4)

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

    def paste_text(self, text):
        try:
            subprocess.run(
                ["wtype", text.replace("\r", " ").replace("\n", " ")],
                check=True,
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

        self.hotkey.start()
        self.tray.start()

        try:
            self.ui_manager.run()
        except KeyboardInterrupt:
            log_debug("Exiting...")
        finally:
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
