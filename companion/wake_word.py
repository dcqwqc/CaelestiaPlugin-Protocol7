from __future__ import annotations

import difflib
import queue
import re
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd


def normalize_text(text: str) -> str:
    text = str(text or "").lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def wake_match_score(text: str, phrase: str = "Hey Tabby") -> float:
    heard = normalize_text(text)
    target = normalize_text(phrase)
    if not heard or not target:
        return 0.0
    aliases = {target}
    if target == "hey tabby":
        aliases.update({"hey tabi", "hey tabby", "hey tabbie", "hey tabbyy"})
    for alias in aliases:
        if alias in heard:
            return 1.0
    tokens = heard.split()
    best = 0.0
    target_words = max(1, len(target.split()))
    for size in range(max(1, target_words - 1), min(len(tokens), target_words + 1) + 1):
        for index in range(0, len(tokens) - size + 1):
            fragment = " ".join(tokens[index:index + size])
            for alias in aliases:
                best = max(best, difflib.SequenceMatcher(None, fragment, alias).ratio())
    return best


class WakeWordDetector:
    """Local-only, speech-gated V0.1 wake detector using faster-whisper."""

    def __init__(self, config: dict, on_wake: Callable[[str, float], None], busy: Callable[[], bool] | None = None):
        self.enabled = bool(config.get("companion_wake_enabled", True))
        self.phrase = str(config.get("companion_wake_phrase", "Hey Tabby")).strip() or "Hey Tabby"
        self.threshold = max(0.65, min(0.98, float(config.get("companion_wake_threshold", 0.84))))
        self.cooldown = max(1.0, min(30.0, float(config.get("companion_wake_cooldown_seconds", 4.0))))
        self.device = config.get("input_device")
        self.model_name = str(config.get("companion_wake_model", "tiny.en"))
        self.on_wake = on_wake
        self.busy = busy or (lambda: False)
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=96)
        self._stop = threading.Event()
        self._stream = None
        self._thread = None
        self._model = None
        self._last_wake = 0.0
        self._sample_rate = 16000
        self._speech_rms = 0.012

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        try:
            self._stream = sd.InputStream(samplerate=self._sample_rate, device=self.device, channels=1, blocksize=1600, dtype="float32", callback=self._audio_callback)
            self._stream.start()
        except Exception:
            self._stream = None
            return
        self._thread = threading.Thread(target=self._worker, name="hey-tabby-wake", daemon=True)
        self._thread.start()

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if self._stop.is_set() or self.busy():
            return
        chunk = np.asarray(indata[:, 0], dtype=np.float32).copy()
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(chunk)
            except queue.Empty:
                pass

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return self._model

    def _transcribe(self, audio: np.ndarray) -> str:
        model = self._ensure_model()
        segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True, condition_on_previous_text=False)
        return "".join(segment.text for segment in segments).strip()

    def _worker(self) -> None:
        active: list[np.ndarray] = []
        speaking = False
        silent_chunks = 0
        speech_chunks = 0
        while not self._stop.is_set():
            try:
                chunk = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if self.busy():
                active.clear()
                speaking = False
                silent_chunks = speech_chunks = 0
                continue
            rms = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
            is_speech = rms >= self._speech_rms
            if is_speech:
                speaking = True
                silent_chunks = 0
                speech_chunks += 1
                active.append(chunk)
            elif speaking:
                silent_chunks += 1
                active.append(chunk)

            duration = len(active) * 0.1
            should_finish = speaking and ((silent_chunks >= 6 and speech_chunks >= 3) or duration >= 3.2)
            if not should_finish:
                continue
            audio = np.concatenate(active).astype(np.float32) if active else np.array([], dtype=np.float32)
            active.clear()
            speaking = False
            silent_chunks = speech_chunks = 0
            if len(audio) < int(self._sample_rate * 0.35):
                continue
            try:
                text = self._transcribe(audio)
                score = wake_match_score(text, self.phrase)
                now = time.monotonic()
                if score >= self.threshold and now - self._last_wake >= self.cooldown:
                    self._last_wake = now
                    self.on_wake(text, score)
            except Exception:
                continue

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
