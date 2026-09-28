import sys
import threading
import subprocess
from gi.repository import GLib

from config import load_config
from hotkey_linux import HotkeyListener
from audio import AudioRecorder
from whisper_engine import WhisperEngine
from ui_linux import UIManager
from llm_rewriter import LLMRewriter
from datetime import datetime

def log_debug(msg):
    # Timestamped so a line can be placed against a run. Without this the
    # log says what happened but never when, and two interleaved pipelines
    # are indistinguishable from one.
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    log_path = "/tmp/protocol7_debug.log"
    try:
        with open(log_path, "a") as f:
            f.write(f"[{stamp}] [MAIN_LINUX] {msg}\n")
    except:
        pass

def _env_without_layer_shell():
    """A child environment with the layer-shell preload removed.

    The main process forces gtk4-layer-shell in via LD_PRELOAD so the overlay
    is reliably a layer surface. Children inherit that, and the preload hooks
    GDK in processes that never initialise GTK the same way -- the tray dies on
    `gdk_display_manager_get() was called before gtk_init()`. Only the process
    that draws the overlay wants it.
    """
    import os
    env = os.environ.copy()
    env.pop("LD_PRELOAD", None)
    env.pop("PROTOCOL7_PRELOADED", None)
    return env


class TrayIcon:
    def __init__(self, app):
        self.app = app
        self.process = None

    def start(self):
        if not self.app.config.get("show_tray", True):
            return
        import subprocess
        import sys
        import os
        APP_DIR = os.path.dirname(os.path.abspath(__file__))
        tray_path = os.path.join(APP_DIR, "tray.py")
        self.tray_log = open("/tmp/protocol7_tray.log", "w")
        self.process = subprocess.Popen([sys.executable, tray_path, str(os.getpid())], cwd=APP_DIR, stdout=self.tray_log, stderr=subprocess.STDOUT, env=_env_without_layer_shell())

    def stop(self):
        if self.process:
            self.process.terminate()

class Protocol7App:
    def __init__(self):
        self.config = load_config()
        self.audio_recorder = AudioRecorder(device_id=self.config.get("input_device"))
        self.whisper_engine = WhisperEngine(self.config)
        self.llm_rewriter = LLMRewriter(self.config)
        self.ui_manager = UIManager(self.config, self.audio_recorder)
        self.tray = TrayIcon(self)
        
        # Ensure decoupled wtype daemon is running to prevent OSD layout spam entirely across restarts
        import os, subprocess
        import psutil
        daemon_running = False
        for p in psutil.process_iter(['cmdline']):
            try:
                cmd = p.info['cmdline']
                if cmd and 'wtype_daemon.py' in ' '.join(cmd):
                    daemon_running = True
                    break
            except: pass
        if not daemon_running:
            import sys
            app_dir = os.path.dirname(os.path.abspath(__file__))
            daemon_path = os.path.join(app_dir, 'wtype_daemon.py')
            subprocess.Popen([sys.executable, daemon_path], start_new_session=True, env=_env_without_layer_shell())
            import time
            time.sleep(0.2)
            
        self.is_active = False
        # Set while the transcription pipeline is running, so the hotkey can
        # tell "start a new dictation" apart from "abandon the one in flight".
        self.is_processing = False
        self.cancel_requested = False
        self._live_stop = threading.Event()
        self._live_thread = None

        # KEY_LEFTCTRL is 29
        self.hotkey = HotkeyListener(self.config.get("hotkey_keycode", 29), self.on_hotkey_trigger)
        
    def on_hotkey_trigger(self):
        log_debug("HOTKEY TRIGGERED")

        # A press while the pipeline is still working means "drop it", not
        # "start another one". Without this the overlay never closes on that
        # press: is_active is already False by then, so the trigger fell through
        # to starting a fresh dictation and the panel stayed up for that instead.
        if self.is_processing:
            log_debug("Dictation cancelled during processing")
            self.cancel_requested = True
            self.is_processing = False
            import gi
            from gi.repository import GLib
            GLib.idle_add(self.ui_manager.hide)
            return

        if not self.is_active:
            # Start dictation
            log_debug("Dictation started")
            self.is_active = True
            self.cancel_requested = False
            self.audio_recorder.start_recording()
            self._start_live_preview()
            
            # Show UI on main thread safely
            import gi
            gi.require_version('GLib', '2.0')
            from gi.repository import GLib
            # GLib.idle_add(self.ui_manager.show) # User requested to remove the overlay
        else:
            # Stop dictation
            log_debug("Dictation stopped")
            self.is_active = False
            # Signal the partial worker now; process_audio joins it off the
            # hotkey listener thread before the final inference begins.
            self._live_stop.set()
            
            # Update UI state to processing
            self.is_processing = True
            import gi
            from gi.repository import GLib
            GLib.idle_add(self.ui_manager.set_processing_state)
            
            # Process in background so we don't block GTK main loop
            threading.Thread(target=self.process_audio, daemon=True).start()

    def _start_live_preview(self):
        self._live_stop.clear()
        self._live_thread = threading.Thread(target=self._live_preview_loop, daemon=True)
        self._live_thread.start()

    def _stop_live_preview(self):
        self._live_stop.set()
        if self._live_thread and self._live_thread.is_alive():
            # Never run a final and partial transcription against the same
            # CTranslate2 model concurrently. The join is bounded so releasing
            # the hotkey cannot freeze the UI if a backend misbehaves.
            self._live_thread.join(timeout=5)
        self._live_thread = None

    def _live_preview_loop(self):
        """Publish a best-effort partial transcript every two seconds."""
        import time
        # Let enough speech accumulate for Whisper to produce useful words.
        if self._live_stop.wait(1.2):
            return
        while not self._live_stop.is_set() and self.is_active:
            audio_data = self.audio_recorder.get_recording_snapshot()
            if len(audio_data) >= 8000:
                try:
                    text = self.whisper_engine.transcribe(audio_data, live=True)
                    if text and not self._live_stop.is_set():
                        GLib.idle_add(self.ui_manager.set_live_text, text)
                except Exception as error:
                    log_debug(f"Live transcription failed: {error}")
            # The preview is deliberately sampled, not streamed token by
            # token: this keeps CPU use bounded on machines without CUDA.
            self._live_stop.wait(2.0)

    def process_audio(self):
        import time
        import traceback
        import gi
        from gi.repository import GLib
        start_time = time.time()
        
        try:
            self._stop_live_preview()
            audio_data = self.audio_recorder.stop_recording()
            audio_time = time.time()
            log_debug(f"Audio collected and resampled in {audio_time - start_time:.2f}s")
            
            if len(audio_data) > 0:
                transcribe_start = time.time()
                text = self.whisper_engine.transcribe(audio_data)
                transcribe_end = time.time()
                log_debug(f"Whisper Transcription took {transcribe_end - transcribe_start:.2f}s")
                
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

                    # Wait 400ms to ensure the user has physically released the keys before pasting.
                    # The UI stays in the 'processing' state during this time so the user knows it's not done yet.
                    time.sleep(0.4)
                    
                    if clean_text.strip():
                        from config import add_history
                        add_history(clean_text)
                        
                        paste_start = time.time()
                        self.paste_text(clean_text)
                        log_debug(f"Paste operation took {time.time() - paste_start:.2f}s")
                    
                    log_debug(f"Total Pipeline Execution Time: {time.time() - start_time:.2f}s")
                    return
                else:
                    log_debug("No text transcribed.")
            else:
                log_debug("No audio data recorded.")
                
            log_debug(f"Pipeline Failed/Empty, Total Time: {time.time() - start_time:.2f}s")
            
        except Exception as e:
            log_debug(f"Fatal error in audio processing pipeline: {e}")
            log_debug(traceback.format_exc())
        finally:
            self.is_processing = False
            # ALWAYS hide the UI, no matter what happens, to prevent infinite loading animation!
            GLib.idle_add(self.ui_manager.hide)

    def paste_text(self, text):
        try:
            # Run wtype for this utterance rather than feeding a long-lived
            # stdin/FIFO daemon. wtype emits its virtual-keyboard events when
            # its input ends; a persistent pipe can therefore leave text
            # queued forever and keep the overlay in its loading state.
            import subprocess
            subprocess.run(
                ["wtype", text.replace("\r", " ").replace("\n", " ")],
                check=True,
                env=_env_without_layer_shell(),
            )
        except Exception as e:
            log_debug(f"Error pasting text with wtype: {e}")

    def run(self):
        log_debug("Starting Protocol-7...")
        
        # Pre-load models in background
        threading.Thread(target=self.whisper_engine._load_model, daemon=True).start()
        threading.Thread(target=self.llm_rewriter.load_model, daemon=True).start()
        
        # Start hotkey and tray
        self.hotkey.start()
        self.tray.start()
        
        # Run GTK main loop (blocks until app exits)
        try:
            self.ui_manager.run()
        except KeyboardInterrupt:
            log_debug("Exiting...")
        finally:
            self.hotkey.stop()
            self.tray.stop()
            
    def quit(self):
        self.ui_manager.app.quit()
        
    def open_settings(self):
        import subprocess
        subprocess.Popen([sys.executable, sys.argv[0], "--settings"])

if __name__ == "__main__":
    from config import load_config
    config = load_config()
    
    if "--settings" in sys.argv:
        from settings_ui_linux import run_settings
        run_settings(config)
        sys.exit(0)
        
    app = Protocol7App()
    app.run()
