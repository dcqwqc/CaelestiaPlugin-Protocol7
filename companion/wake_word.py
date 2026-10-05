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


def _phrase_aliases(phrase: str) -> set[str]:
    target = normalize_text(phrase)
    aliases = {target} if target else set()
    if target == "hey tabby":
        aliases.update({"hey tabi", "hey tabby", "hey tabbie", "hey tabbyy"})
    elif target == "bye tabby":
        aliases.update({"bye tabi", "bye tabby", "bye tabbie", "by tabby", "goodbye tabby"})
    return aliases


def close_match_score(text: str, phrase: str = "Bye Tabby") -> float:
    """Strict close matcher: the close phrase must be the utterance itself.

    Unlike the wake phrase, destructive close matching must never succeed merely
    because an alias appears somewhere inside a longer conversational transcript.
    """
    heard = normalize_text(text)
    aliases = _phrase_aliases(phrase)
    if not heard or not aliases:
        return 0.0
    if heard in aliases:
        return 1.0
    heard_words = heard.split()
    best = 0.0
    for alias in aliases:
        alias_words = alias.split()
        # A fuzzy close is only allowed when Whisper produced the same number of
        # words. Extra conversational words make the utterance non-destructive.
        if len(heard_words) != len(alias_words):
            continue
        best = max(best, difflib.SequenceMatcher(None, heard, alias).ratio())
    return best


def wake_match_score(text: str, phrase: str = "Hey Tabby") -> float:
    heard = normalize_text(text)
    target = normalize_text(phrase)
    if not heard or not target:
        return 0.0
    aliases = _phrase_aliases(phrase)
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


def companion_phrase_match(
    text: str,
    wake_phrase: str = "Hey Tabby",
    close_phrase: str = "Bye Tabby",
) -> tuple[str, float]:
    """Return the single best Tabby voice action for a transcript."""
    wake_score = wake_match_score(text, wake_phrase)
    close_score = close_match_score(text, close_phrase) if normalize_text(close_phrase) else 0.0
    if close_score > wake_score:
        return "close", close_score
    return "wake", wake_score


def companion_phrase_action(
    text: str,
    wake_phrase: str = "Hey Tabby",
    close_phrase: str = "Bye Tabby",
    companion_active: bool = False,
) -> tuple[str, float]:
    if companion_active:
        return "close", close_match_score(text, close_phrase) if normalize_text(close_phrase) else 0.0
    return companion_phrase_match(text, wake_phrase, close_phrase)


class WakeWordDetector:
    """Speech-gated wake detector using Protocol 7's configured STT backend."""

    def __init__(
        self,
        config: dict,
        on_wake: Callable[[str, float], None],
        transcriber,
        busy: Callable[[], bool] | None = None,
        on_close: Callable[..., None] | None = None,
        companion_active: Callable[[], bool] | None = None,
        companion_state: Callable[[], str] | None = None,
    ):
        self.enabled = bool(config.get("companion_wake_enabled", True))
        self.phrase = str(config.get("companion_wake_phrase", "Hey Tabby")).strip() or "Hey Tabby"
        self.close_phrase = str(config.get("companion_close_phrase", "Bye Tabby")).strip()
        self.threshold = max(0.65, min(0.98, float(config.get("companion_wake_threshold", 0.84))))
        self.close_threshold = max(0.92, self.threshold)
        self.cooldown = max(1.0, min(30.0, float(config.get("companion_wake_cooldown_seconds", 4.0))))
        self.device = config.get("input_device")
        self.on_wake = on_wake
        self.on_close = on_close
        self.transcriber = transcriber
        self.busy = busy or (lambda: False)
        self.companion_active = companion_active or (lambda: False)
        self.companion_state = companion_state or (lambda: "")
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=32)
        self._transcribe_queue: queue.Queue[tuple[np.ndarray, str]] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._stream = None
        self._thread = None
        self._transcribe_thread = None
        self._last_trigger = {"wake": 0.0, "close": 0.0}
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
        self._thread = threading.Thread(target=self._worker, name="hey-tabby-vad", daemon=True)
        self._transcribe_thread = threading.Thread(target=self._transcriber_worker, name="hey-tabby-stt", daemon=True)
        self._thread.start()
        self._transcribe_thread.start()

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

    def _transcribe(self, audio: np.ndarray) -> str:
        # Use the exact same shared WhisperEngine instance as Protocol 7
        # dictation. That means the wake path automatically follows the
        # Wake-word checks are frequent and latency-sensitive. Route them through
        # Protocol 7's local live path so they cannot saturate or rate-limit the
        # cloud Whisper backend used for final dictation.
        return str(self.transcriber.transcribe(audio, live=True, allow_local_fallback=True) or "").strip()

    def _worker(self) -> None:
        active: list[np.ndarray] = []
        speaking = False
        silent_chunks = 0
        speech_chunks = 0
        speech_origin_state = ""
        while not self._stop.is_set():
            try:
                chunk = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if self.busy():
                active.clear()
                speaking = False
                silent_chunks = speech_chunks = 0
                speech_origin_state = ""
                continue
            rms = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
            is_speech = rms >= self._speech_rms
            if is_speech:
                if not speaking:
                    try:
                        speech_origin_state = str(self.companion_state() or "")
                    except Exception:
                        speech_origin_state = ""
                speaking = True
                silent_chunks = 0
                speech_chunks += 1
                active.append(chunk)
            elif speaking:
                silent_chunks += 1
                active.append(chunk)

            duration = len(active) * 0.1
            # Keep the detector realtime even while ChatGPT is speaking through
            # the laptop speakers. A short clip is enough for Hey/Bye Tabby and
            # prevents multi-second assistant-audio backlogs.
            should_finish = speaking and ((silent_chunks >= 4 and speech_chunks >= 3) or duration >= 1.8)
            if not should_finish:
                continue
            audio = np.concatenate(active).astype(np.float32) if active else np.array([], dtype=np.float32)
            origin_state = speech_origin_state
            active.clear()
            speaking = False
            silent_chunks = speech_chunks = 0
            speech_origin_state = ""
            if len(audio) < int(self._sample_rate * 0.5):
                continue
            # STT runs independently. Keep at most one pending clip; if Whisper
            # is slower than realtime, replace stale pending speech with the
            # newest segment so a spoken close phrase is never buried in backlog.
            try:
                self._transcribe_queue.put_nowait((audio, origin_state))
            except queue.Full:
                try:
                    self._transcribe_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._transcribe_queue.put_nowait((audio, origin_state))
                except queue.Full:
                    pass

    def _transcriber_worker(self) -> None:
        while not self._stop.is_set():
            try:
                audio, origin_state = self._transcribe_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                text = self._transcribe(audio)
                active_now = bool(self.companion_active())
                action, score = companion_phrase_action(
                    text, self.phrase, self.close_phrase, companion_active=active_now
                )
                now = time.monotonic()
                threshold = self.close_threshold if action == "close" else self.threshold
                if score < threshold or now - self._last_trigger[action] < self.cooldown:
                    continue
                self._last_trigger[action] = now
                if action == "close" and self.on_close is not None:
                    try:
                        self.on_close(text, score, origin_state)
                    except TypeError:
                        self.on_close(text, score)
                elif action == "wake":
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
        if self._transcribe_thread and self._transcribe_thread.is_alive():
            self._transcribe_thread.join(timeout=1.0)
        self._thread = None
        self._transcribe_thread = None
