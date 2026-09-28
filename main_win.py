import sys
import threading
import subprocess
import os

from config import load_config
from hotkey_win import HotkeyListener
from audio import AudioRecorder
from whisper_engine import WhisperEngine
from ui_win import UIManager
from llm_rewriter import LLMRewriter
from datetime import datetime

def log_debug(msg):
    # Timestamped so a line can be placed against a run. Without this the
    # log says what happened but never when, and two interleaved pipelines
    # are indistinguishable from one.
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    log_path = os.path.join(os.environ.get("TEMP", "/tmp"), "protocol7_debug.log")
    try:
        with open(log_path, "a") as f:
            f.write(f"[{stamp}] [MAIN_WIN] {msg}\n")
    except:
        pass

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
        self.tray_log = open(os.path.join(APP_DIR, "tray_crash.log"), "w")
        self.process = subprocess.Popen([sys.executable, tray_path, str(os.getpid())], cwd=APP_DIR, stdout=self.tray_log, stderr=subprocess.STDOUT)

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
        
        self.is_active = False
        
        # Default hotkey ctrl if not set
        self.hotkey = HotkeyListener(self.config.get("hotkey_name", "ctrl").lower(), self.on_hotkey_trigger)
        
    def on_hotkey_trigger(self):
        log_debug("HOTKEY TRIGGERED")
        if not self.is_active:
            log_debug("Dictation started")
            self.is_active = True
            self.audio_recorder.start_recording()
            self.ui_manager.invoke_main_thread(self.ui_manager.show)
        else:
            log_debug("Dictation stopped")
            self.is_active = False
            self.ui_manager.invoke_main_thread(self.ui_manager.set_processing_state)
            threading.Thread(target=self.process_audio, daemon=True).start()

    def process_audio(self):
        import time
        import traceback
        start_time = time.time()
        
        try:
            audio_data = self.audio_recorder.stop_recording()
            audio_time = time.time()
            log_debug(f"Audio collected and resampled in {audio_time - start_time:.2f}s (Array length: {len(audio_data)})")
            
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
            log_debug(f"[ERROR] Fatal error in audio processing pipeline: {e}")
            log_debug(traceback.format_exc())
        finally:
            self.ui_manager.invoke_main_thread(self.ui_manager.hide)

    def paste_text(self, text):
        try:
            import keyboard
            keyboard.write(text, delay=0.01)
        except Exception as e:
            log_debug(f"Error pasting text with keyboard: {e}")

    def run(self):
        log_debug("Starting Protocol-7...")
        
        APP_DIR = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(APP_DIR, "app.pid"), "w") as f:
            f.write(str(os.getpid()))
            
        # Pre-load models in background
        threading.Thread(target=self.whisper_engine._load_model, daemon=True).start()
        threading.Thread(target=self.llm_rewriter.load_model, daemon=True).start()
        
        # Start hotkey and tray
        self.hotkey.start()
        self.tray.start()
        
        # Run UI main loop (blocks until app exits)
        try:
            self.ui_manager.run()
        except KeyboardInterrupt:
            log_debug("Exiting...")
        finally:
            self.hotkey.stop()
            self.tray.stop()
            
    def quit(self):
        self.ui_manager.root.quit()
        
    def open_settings(self):
        import subprocess
        subprocess.Popen([sys.executable, sys.argv[0], "--settings"])

if __name__ == "__main__":
    from config import load_config
    config = load_config()
    
    if "--settings" in sys.argv:
        from settings_ui_win import run_settings
        run_settings(config)
        sys.exit(0)
        
    app = Protocol7App()
    app.run()
