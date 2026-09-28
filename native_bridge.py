import json
import sys
import threading
import time


class NativeUIBridge:
    """Thread-safe Protocol-7 state publisher consumed by Caelestia QML."""

    def __init__(self, config, audio_recorder):
        self.config = config
        self.audio_recorder = audio_recorder
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._state = {
            "visible": False,
            "processing": False,
            "level": 0.0,
            "liveText": "",
        }

        self._meter_thread = threading.Thread(
            target=self._meter_loop,
            name="protocol7-native-meter",
            daemon=True,
        )
        self._meter_thread.start()
        self._publish()

    def _snapshot(self):
        with self._state_lock:
            return dict(self._state)

    def _publish(self):
        payload = json.dumps(self._snapshot(), separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            try:
                sys.stdout.write(f"P7STATE {payload}\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                pass

    def _meter_loop(self):
        last_sent = -1.0
        last_publish = 0.0

        while not self._stop.wait(1 / 30):
            state = self._snapshot()
            if not state["visible"]:
                last_sent = -1.0
                continue

            level = 0.0 if state["processing"] else float(self.audio_recorder.get_volume_level())
            level = max(0.0, min(1.0, level))
            now = time.monotonic()

            # Caelestia interpolates bar heights, so ~30 Hz state delivery is
            # enough for a 120 Hz display without making stdout an audio stream.
            if abs(level - last_sent) < 0.012 and now - last_publish < 0.12:
                continue

            with self._state_lock:
                self._state["level"] = level
            last_sent = level
            last_publish = now
            self._publish()

    def show(self):
        with self._state_lock:
            self._state.update(visible=True, processing=False, level=0.0, liveText="")
        self._publish()

    def hide(self):
        with self._state_lock:
            self._state.update(visible=False, processing=False, level=0.0, liveText="")
        self._publish()

    def set_processing_state(self):
        with self._state_lock:
            self._state.update(visible=True, processing=True, level=0.0)
        self._publish()

    def set_live_text(self, text):
        with self._state_lock:
            self._state["liveText"] = (text or "").strip()
        self._publish()

    def run(self):
        try:
            while not self._stop.wait(1.0):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.quit()

    def quit(self):
        self._stop.set()
