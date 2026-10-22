"""Opt-in, display-only live preview from overlapping Groq Whisper windows.

Provider ``groq-chunks`` periodically sends the most recent ~6 s of speech
(1 s overlapping the previous window) to the Groq Whisper client and model
already selected for final dictation. There is no separate credential,
provider account or billing route, and no other provider exists here.

Preview text is never trusted as the result: on stop the ghost job always
transcribes the complete original recording once, and a failed, throttled or
disabled preview can never prevent or delay that final request.

Threading: the PortAudio callback only ``put_nowait``s chunks. One worker
thread per recording drains them, runs voice-activity detection and plans
windows; at most one preview request is in flight per session, on its own
short-lived thread with a bounded timeout. Memory per recording is bounded
by the ring buffer (window + 1 s) and the speech-interval list.

Budgeting: runtime previews require fresh explicit provider/organization
assertions and a warmed durable two-host partition ledger. These are not
proof of account billing protection. See preview_quota.py and the release
blockers in docs/live-transcription.md. Groq's supplied free tier for Whisper
allows 20 requests/min, 2000/day,
7200 audio-seconds/hour and 28800/day per organization, shared with the
final dictation and any other device using the same key. Previews may use
only a fraction of that (counting the 10 s minimum billed length per
request), yield while any final is in flight, cool down on 429 using
``retry-after`` and stop for the recording on the first 429. API keys and
transcript text are never logged.
"""
from __future__ import annotations

import io
import math
import queue
import re
import threading
import time
import wave
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

import numpy as np

PROVIDER_GROQ_CHUNKS = "groq-chunks"
WIRE_RATE = 16000
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"
FREE_MODELS = frozenset({"whisper-large-v3", "whisper-large-v3-turbo"})

# Groq free-tier limits for whisper-large-v3 / -turbo (per organization).
FREE_TIER_LIMITS = {
    "requests_per_minute": 20,
    "requests_per_day": 2000,
    "audio_seconds_per_hour": 7200,
    "audio_seconds_per_day": 28800,
}
# Groq bills (and, conservatively, we assume rate-limits) each request as at
# least 10 s of audio, so a 6 s preview window costs 10 s of budget.
MIN_BILLED_SECONDS = 10.0

# Budget denials that cannot clear during one recording.
PERMANENT_DENIALS = frozenset({"disabled", "hourly audio", "daily audio", "daily requests"})


class PreviewUnavailable(RuntimeError):
    """No usable Groq client right now; nothing was sent."""


@dataclass(frozen=True)
class PreviewSettings:
    language: str = ""
    verification_deadline: float = 0.0
    interval_seconds: float = 5.0
    overlap_seconds: float = 1.0
    first_preview_seconds: float = 4.0
    min_window_speech_seconds: float = 0.6
    min_new_speech_seconds: float = 0.5
    request_timeout: float = 4.0
    max_failures: int = 2
    idle_timeout: float = 3.0
    max_session_seconds: float = 900.0
    max_requests: int = 120
    max_queue_chunks: int = 1024

    @property
    def window_seconds(self):
        return self.interval_seconds + self.overlap_seconds


def _clamp(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(high, max(low, number))


def availability(config):
    """Return None when previews may run, else a short non-secret reason."""
    # Keep final accounting alive even while previews are switched off.
    configured_path = config.get("streaming_stt_quota_ledger")
    if isinstance(configured_path, str) and configured_path.startswith("/"):
        configure_budget(configured_path)
    if config.get("streaming_stt_enabled", False) is not True:
        return "disabled"
    provider = str(config.get("streaming_stt_provider") or "").strip()
    if not provider:
        return "no provider configured"
    if provider != PROVIDER_GROQ_CHUNKS:
        return "unsupported provider"
    if config.get("use_groq", False) is not True:
        return "Groq dictation not enabled"
    if not str(config.get("groq_api_key") or "").strip():
        return "no Groq API key configured"
    if str(config.get("groq_model") or DEFAULT_GROQ_MODEL) not in FREE_MODELS:
        return "unsupported preview model"
    if config.get("streaming_stt_free_tier_verified") is not True:
        return "provider free-tier protection not verified"
    if config.get("streaming_stt_quota_partition_verified") is not True:
        return "organization quota partition not verified"
    verified = _number(config.get("streaming_stt_verified_at"), 0.0)
    if not 0 <= time.time() - verified < 86400:
        return "provider/quota verification stale"
    path = config.get("streaming_stt_quota_ledger")
    if not isinstance(path, str) or not path.startswith("/"):
        return "durable quota ledger required"
    reason = default_budget().disabled_reason()
    if reason:
        return f"preview disabled ({reason})"
    return None


def settings_from_config(config):
    language = ""
    if not config.get("auto_detect_language", True):
        language = str(config.get("language") or "").strip()
    return PreviewSettings(
        language=language,
        interval_seconds=_clamp(config.get("streaming_stt_preview_interval_seconds", 5.0), 4.0, 6.0, 5.0),
    )


def create_session(config, *, input_rate=16000, client_provider=None, on_partial=None,
                   log=None, budget=None):
    """Build an unstarted session, or None when previews are not opted in."""
    if availability(config) is not None or client_provider is None:
        return None
    return GroqChunkSession(
        replace(settings_from_config(config), verification_deadline=float(config["streaming_stt_verified_at"]) + 86400), input_rate=input_rate,
        client_provider=client_provider, budget=budget, on_partial=on_partial, log=log,
    )


# --------------------------------------------------------------------------
# Voice activity


class SpeechDetector:
    """Deterministic energy/zero-crossing voice detector.

    A 30 ms frame needs sufficient speech-band energy and periodicity,
    an absolute level and a low zero-crossing rate. No startup noise-floor
    calibration can absorb immediate speech. Dense runs >= ~210 ms reject
    clicks, key taps and brief bumps; confirmed runs bridge 200 ms pauses.
    """

    def __init__(self, rate, *, frame_seconds=0.03, abs_threshold_db=-60.0,
                 max_zcr=0.4, min_run_seconds=0.21, hangover_seconds=0.2):
        self.rate = int(rate)
        self.frame = max(1, int(round(self.rate * frame_seconds)))
        self.abs_threshold_db = abs_threshold_db
        self.max_zcr = max_zcr
        self.min_run = max(1, int(math.ceil(min_run_seconds / frame_seconds - 1e-9)))
        self.hangover = max(0, int(round(hangover_seconds / frame_seconds)))
        self.pre_gap = 3  # frames bridged before a run is confirmed (~90 ms)
        self.position = 0  # absolute sample index of the next unprocessed frame
        self.intervals = deque()  # confirmed [start, end) absolute sample spans
        self._carry = np.zeros(0, dtype=np.float32)
        self._run_start = None
        self._run_frames = 0
        self._gap = 0
        self._confirmed = False

    def process(self, audio):
        data = np.asarray(audio, dtype=np.float32).reshape(-1)
        if self._carry.size:
            data = np.concatenate([self._carry, data])
        count = data.size // self.frame
        if count:
            frames = data[:count * self.frame].reshape(count, self.frame)
            rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
            levels = 20.0 * np.log10(np.maximum(rms, 1e-9))
            signs = np.signbit(frames)
            zcr = np.mean(signs[:, 1:] != signs[:, :-1], axis=1)
            # Speech-band energy rejects mains hum; normalized periodicity
            # rejects energetic broadband/colored noise without learning initial
            # sustained vowels as background. This remains a preview heuristic.
            spectrum = np.abs(np.fft.rfft(frames, axis=1)) ** 2
            frequencies = np.fft.rfftfreq(self.frame, 1 / self.rate)
            band = spectrum[:, (frequencies >= 90) & (frequencies <= 4000)].sum(axis=1)
            fraction = band / np.maximum(spectrum.sum(axis=1), 1e-20)
            for frame, level, crossing, energy in zip(frames, levels, zcr, fraction):
                centered = frame.astype(np.float64) - frame.mean()
                corr = np.correlate(centered, centered, mode="full")[self.frame - 1:]
                low, high = max(1, self.rate // 400), min(self.frame - 1, self.rate // 80)
                periodic = max((corr[k] / max(np.sqrt(np.dot(centered[:-k], centered[:-k]) *
                            np.dot(centered[k:], centered[k:])), 1e-20)
                            for k in range(low, high + 1)), default=0) > 0.75
                self._frame(float(level), float(crossing), periodic and energy > 0.55)
        self._carry = data[count * self.frame:].copy()

    def _frame(self, level, crossing, voiced=True):
        start = self.position
        self.position += self.frame
        speech = voiced and level >= self.abs_threshold_db and crossing <= self.max_zcr
        if speech:
            if self._run_start is None:
                self._run_start, self._run_frames = start, 0
            self._run_frames += 1
            self._gap = 0
            if self._confirmed:
                self.intervals[-1][1] = self.position
            else:
                span = (self.position - self._run_start) // self.frame
                # Dense, sustained energy only: periodic key taps or clicks
                # bridged by short gaps never confirm a speech run.
                if self._run_frames >= self.min_run and self._run_frames >= 0.6 * span:
                    self._confirmed = True
                    self.intervals.append([self._run_start, self.position])
        elif self._run_start is not None:
            self._gap += 1
            if self._gap > (self.hangover if self._confirmed else self.pre_gap):
                self.reset_run()

    def reset_run(self):
        self._run_start, self._run_frames, self._gap, self._confirmed = None, 0, 0, False

    def skip(self, samples):
        """Account for samples that were lost before reaching the detector."""
        self._carry = np.zeros(0, dtype=np.float32)
        self.position += int(samples)
        self.reset_run()

    def speech_samples(self, start, end):
        total = 0
        for span_start, span_end in self.intervals:
            total += max(0, min(end, span_end) - max(start, span_start))
        return total

    def prune(self, before):
        while self.intervals and self.intervals[0][1] <= before and not (
                self._confirmed and len(self.intervals) == 1):
            self.intervals.popleft()


# --------------------------------------------------------------------------
# Window planning


@dataclass(frozen=True)
class Window:
    index: int
    start: int
    end: int
    rate: int
    audio: np.ndarray = field(repr=False, compare=False)
    overlap: bool = False
    gap: bool = False
    speech_seconds: float = 0.0

    @property
    def duration(self):
        return (self.end - self.start) / self.rate


class WindowPlanner:
    """Bounded ring buffer that proposes overlapping, voiced preview windows."""

    def __init__(self, settings, rate):
        self.rate = int(rate)
        seconds = lambda value: int(round(value * self.rate))
        self.window = seconds(settings.window_seconds)
        self.overlap = seconds(settings.overlap_seconds)
        self.interval = seconds(settings.interval_seconds)
        self.first = seconds(settings.first_preview_seconds)
        self.min_window_speech = seconds(settings.min_window_speech_seconds)
        self.min_new_speech = seconds(settings.min_new_speech_seconds)
        self.min_gap_speech = seconds(0.2)
        self.capacity = self.window + self.rate
        self.detector = SpeechDetector(self.rate)
        self.total = 0           # absolute samples received for this recording
        self.sent_end = 0        # end of the last window actually sent
        self.considered_end = 0  # audio before this was sent or judged silent
        self.windows = 0
        self._chunks = deque()
        self._buffered = 0
        self._buffer_start = 0
        self._lost_since_sent = False

    def add(self, chunk):
        data = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if not data.size:
            return
        self._chunks.append(data)
        self._buffered += data.size
        self.total += data.size
        self.detector.process(data)
        while self._chunks and self._buffered - self._chunks[0].size >= self.capacity:
            old = self._chunks.popleft()
            self._buffered -= old.size
            self._buffer_start += old.size
        self.detector.prune(self._buffer_start - self.window)

    def discontinuity(self, lost_samples=0):
        """Audio was dropped: never stitch a window across the hole."""
        self.detector.skip(lost_samples)
        self.total += int(lost_samples)
        self._chunks.clear()
        self._buffered = 0
        self._buffer_start = self.total
        self.considered_end = max(self.considered_end, self.total)
        self._lost_since_sent = True

    def buffered_samples(self):
        return self._buffered

    def propose(self):
        """Return (window, None) for a window worth sending, else (None, reason).

        Silent stretches are consumed here so that the same unvoiced audio is
        never reconsidered; a window always contains new voiced samples.
        """
        needed = self.first if self.windows == 0 else self.interval
        end = self.total
        if end - self.considered_end < needed:
            return None, "waiting"
        start = max(self._buffer_start, end - self.window, 0)
        new_speech = self.detector.speech_samples(self.considered_end, end)
        window_speech = self.detector.speech_samples(start, end)
        if new_speech < self.min_new_speech or window_speech < self.min_window_speech:
            self.considered_end = end
            return None, "silence"
        overlap = self.windows > 0 and start < self.sent_end and not self._lost_since_sent
        skipped = self.detector.speech_samples(self.sent_end, start) if self.windows else 0
        gap = self.windows > 0 and (self._lost_since_sent or skipped >= self.min_gap_speech)
        audio = np.concatenate(list(self._chunks))[start - self._buffer_start:end - self._buffer_start]
        return Window(self.windows + 1, start, end, self.rate, audio.copy(), overlap, gap,
                      window_speech / self.rate), None

    def commit(self, window, *, delivered=True):
        """Mark a window as sent; undelivered text forces a gap marker next."""
        self.windows = max(self.windows, window.index)
        self.sent_end = window.end
        self.considered_end = max(self.considered_end, window.end)
        self._lost_since_sent = not delivered


# --------------------------------------------------------------------------
# Text reconciliation


def _norm(word):
    return re.sub(r"[^\w']+", "", word.lower()).strip("'")


class TextReconciler:
    """Merge overlapping window transcripts into one stable preview line.

    Only the last ``max_revise_words`` words may ever change; everything
    before is frozen so displayed text never blinks. Overlapping windows are
    aligned on their shared words (the newer window's wording wins inside the
    overlap, fixing boundary punctuation and cut-off words); windows separated
    by unsent speech are joined with an ellipsis instead of being merged.
    """

    GAP = "…"

    def __init__(self, *, max_revise_words=8, max_words=300, search_words=10, max_head_skip=4):
        self.words = []
        self.frozen = 0
        self.max_revise = max_revise_words
        self.max_words = max_words
        self.search = search_words
        self.max_head_skip = max_head_skip

    @property
    def text(self):
        return " ".join(self.words)

    def add(self, text, *, overlap=False, gap=False):
        new = str(text or "").split()
        if not new:
            return False
        before = list(self.words)
        if not self.words:
            self.words = new
        elif gap:
            self.words = self.words + ([] if self.words[-1] == self.GAP else [self.GAP]) + new
        elif overlap:
            self._merge(new)
        else:
            self._append(new)
        self.frozen = max(self.frozen, len(self.words) - self.max_revise)
        if len(self.words) > self.max_words:
            drop = len(self.words) - self.max_words + 1
            self.words = [self.GAP] + self.words[drop:]
            self.frozen = max(1, self.frozen - drop + 1)
        return self.words != before

    def _merge(self, new):
        prev = self.words
        low = max(self.frozen, len(prev) - self.search)
        tail = [_norm(w) for w in prev[low:]]
        head = [_norm(w) for w in new[:self.search]]
        last = len(tail) - 1

        def same(a, b, cut):
            # The previous window's final word may have been cut off mid-word.
            if not a or not b:
                return False
            return a == b or (cut and min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))

        # Align a suffix of the previous text with the start of the new
        # window position by position, so one misrecognized word inside the
        # overlap does not break the match. The first aligned word must agree.
        best = None
        for i in range(len(tail)):
            for j in range(min(len(head), self.max_head_skip + 1)):
                if not same(tail[i], head[j], i == last):
                    continue
                pairs = [(i + m, j + m) for m in range(len(tail) - i) if j + m < len(head)]
                matches = sum(same(tail[a], head[b], a == last) for a, b in pairs)
                # One word alone cannot establish temporal alignment.
                if not (matches >= 2 and 2 * matches >= len(pairs)):
                    continue
                key = (matches, matches - len(pairs), -j, i)
                if best is None or key > best[0]:
                    best = (key, i, j)
        if best is None:
            self._append(new)
            return
        _, i, j = best
        self.words = prev[:low + i] + new[j:]

    def _append(self, new):
        if not new:
            return
        last = self.words[-1]
        # A lower-case continuation shows a window-edge full stop was not real.
        if new[0][:1].islower() and last.endswith(".") and not last.endswith("..") \
                and len(self.words) > self.frozen:
            self.words[-1] = last[:-1]
        self.words = self.words + new


# --------------------------------------------------------------------------
# Response filtering

_HALLUCINATIONS = frozenset({
    "you", "thank you", "thanks", "thank you so much", "thank you very much",
    "thanks for watching", "thank you for watching", "thanks for listening",
    "please subscribe", "like and subscribe", "bye", "bye bye", "goodbye", "okay",
    "subtitles by the amaraorg community", "amaraorg", "music", "applause",
    "silence", "uh", "um", "hmm", "so", "the end",
})


def _field(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get(name)
    return value


def _number(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def clean_preview_text(result):
    """Return displayable preview text, or "" for likely non-speech output."""
    segments = _field(result, "segments")
    if isinstance(segments, (list, tuple)) and segments:
        kept = []
        for segment in segments:
            no_speech = _number(_field(segment, "no_speech_prob"), 0.0)
            logprob = _number(_field(segment, "avg_logprob"), 0.0)
            ratio = _number(_field(segment, "compression_ratio"), 1.0)
            if no_speech > 0.85 or (no_speech > 0.6 and logprob < -0.7) or ratio > 2.4:
                continue
            kept.append(str(_field(segment, "text") or ""))
        text = " ".join(kept)
    else:
        text = str(_field(result, "text") or "")
    text = " ".join(text.split())
    if not re.search(r"[^\W\d_]", text):
        return ""  # punctuation, digits or nothing: never preview as speech
    normalized = " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())
    if normalized in _HALLUCINATIONS:
        return ""
    words = normalized.split()
    if len(words) >= 4 and max(words.count(w) for w in set(words)) / len(words) > 0.6:
        return ""  # "the the the the" style decoder loops
    return text


# --------------------------------------------------------------------------
# Groq transport


def encode_wav(audio, rate):
    """Mono 16 kHz PCM16 WAV bytes (resampled when the device rate differs)."""
    data = np.asarray(audio, dtype=np.float32).reshape(-1)
    if int(rate) != WIRE_RATE and data.size > 1:
        length = max(1, int(round(data.size * WIRE_RATE / int(rate))))
        data = np.interp(np.linspace(0, data.size - 1, length), np.arange(data.size), data)
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(WIRE_RATE)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def is_groq_endpoint(client):
    """Refuse any client whose base URL is not Groq over HTTPS."""
    base = getattr(client, "base_url", None)
    if base is None:
        return True  # injected test double without a transport
    parts = urlsplit(str(base))
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "groq.com" or host.endswith(".groq.com"))


def groq_transcribe(client, model, wav, *, language="", timeout=4.0):
    """One bounded Groq Whisper request; returns (result, headers or None)."""
    if model not in FREE_MODELS:
        raise PreviewUnavailable("unsupported preview model")
    if not is_groq_endpoint(client):
        raise PreviewUnavailable("client is not a Groq endpoint")
    kwargs = {
        "file": ("preview.wav", wav),
        "model": model,
        "response_format": "verbose_json",
        "temperature": 0.0,
        "timeout": timeout,
    }
    if language:
        kwargs["language"] = language
    api = client.audio.transcriptions
    raw = getattr(api, "with_raw_response", None)
    if raw is not None and callable(getattr(raw, "create", None)):
        response = raw.create(**kwargs)
        return response.parse(), getattr(response, "headers", None)
    return api.create(**kwargs), None


def _header(headers, name):
    if not headers:
        return None
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter(name)
        if value is not None:
            return str(value)
    try:
        items = headers.items()
    except AttributeError:
        return None
    for key, value in items:
        if str(key).lower() == name:
            return str(value)
    return None


def parse_duration(value):
    """Seconds from "12", "7.66s", "2m59.56s", "1h2m" or "250ms"; else None."""
    if value is None:
        return None
    text = str(value).strip().lower()
    try:
        number = float(text)
        return number if math.isfinite(number) and number >= 0 else None
    except ValueError:
        pass
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(n) * scale[u] for n, u in parts)


def classify_error(exc):
    """Map an SDK/transport exception to (kind, retry_after) without text."""
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    if status == 429:
        return "rate_limited", parse_duration(_header(getattr(response, "headers", None), "retry-after"))
    if status in (401, 403):
        return "auth", None
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "timeout", None
    return "error", None


# --------------------------------------------------------------------------
# Process-wide budget


class PreviewBudget:
    """Conservative, process-wide accounting of Groq requests and audio.

    Finals are recorded too and always win: previews never start while a
    final is in flight and only use a share of each free-tier limit.
    """

    def __init__(self, *, clock=time.monotonic, limits=None, share=0.4, rpm_cap=12,
                 min_billed=MIN_BILLED_SECONDS, max_strikes=3, header_reserve=0.05):
        self.clock = clock
        self.limits = dict(FREE_TIER_LIMITS if limits is None else limits)
        self.share = share
        self.rpm_cap = min(rpm_cap, self.limits["requests_per_minute"])
        self.min_billed = min_billed
        self.max_strikes = max_strikes
        self.header_reserve = header_reserve
        self._lock = threading.RLock()
        self._events = deque(maxlen=20000)  # (time, billed seconds, kind)
        self._strikes = deque()
        self._cooldown_until = 0.0
        self._disabled = ""
        self._preview_in_flight = None
        self._finals_in_flight = 0

    def billed(self, seconds):
        return max(self.min_billed, float(seconds))

    def _prune(self, now):
        while self._events and now - self._events[0][0] > 86400.0:
            self._events.popleft()
        while self._strikes and now - self._strikes[0] > 86400.0:
            self._strikes.popleft()

    def usage(self):
        with self._lock:
            now = self.clock()
            self._prune(now)
            return self._usage(now)

    def _usage(self, now):
        minute = sum(1 for t, _, _ in self._events if now - t < 60.0)
        hour_audio = sum(b for t, b, _ in self._events if now - t < 3600.0)
        day_audio = sum(b for _, b, _ in self._events)
        return {"requests_minute": minute, "requests_day": len(self._events),
                "audio_hour": hour_audio, "audio_day": day_audio}

    def disabled_reason(self):
        with self._lock:
            return self._disabled

    def dispatch_allowed(self):
        with self._lock:
            return not self._finals_in_flight and not self._disabled and self.clock() >= self._cooldown_until

    def in_flight(self):
        with self._lock:
            return self._preview_in_flight is not None

    def cooldown_remaining(self):
        with self._lock:
            return max(0.0, self._cooldown_until - self.clock())

    def try_begin_preview(self, seconds):
        """Reserve one preview request; returns a token or (None, reason)."""
        with self._lock:
            now = self.clock()
            if self._disabled:
                return None, "disabled"
            if now < self._cooldown_until:
                return None, "cooldown"
            if self._finals_in_flight:
                return None, "final in progress"
            if self._preview_in_flight is not None and now - self._preview_in_flight[0] < 30.0:
                # Single flight. A request abandoned far past its bounded
                # timeout cannot block previews for the rest of the process.
                return None, "in flight"
            self._prune(now)
            usage = self._usage(now)
            billed = self.billed(seconds)
            if usage["requests_minute"] + 1 > self.rpm_cap:
                return None, "rpm"
            if usage["requests_day"] + 1 > self.limits["requests_per_day"] * self.share:
                return None, "daily requests"
            if usage["audio_hour"] + billed > self.limits["audio_seconds_per_hour"] * self.share:
                return None, "hourly audio"
            if usage["audio_day"] + billed > self.limits["audio_seconds_per_day"] * self.share:
                return None, "daily audio"
            token = (now, billed, "preview")
            self._events.append(token)
            self._preview_in_flight = token
            return token, None

    def end_preview(self, token, outcome, *, retry_after=None, headers=None):
        with self._lock:
            now = self.clock()
            if self._preview_in_flight is token:
                self._preview_in_flight = None
            if outcome == "not_sent":
                try:
                    self._events.remove(token)
                except ValueError:
                    pass
                return
            if outcome == "rate_limited":
                self._apply_headers(now, headers)
                self._strikes.append(now)
                strikes = len(self._strikes)
                if strikes >= self.max_strikes:
                    self._disabled = "rate limited repeatedly"
                else:
                    wait = max(60.0, retry_after or 0.0) * (2 ** (strikes - 1))
                    self._cooldown_until = max(self._cooldown_until, now + min(3600.0, wait))
            elif outcome == "auth":
                self._disabled = "authentication failed"
            elif outcome in ("timeout", "error"):
                self._cooldown_until = max(self._cooldown_until, now + 10.0)
            elif outcome == "ok":
                self._apply_headers(now, headers)

    def _apply_headers(self, now, headers):
        for counter, ceiling in (("audio-seconds", self.limits["audio_seconds_per_hour"]),
                                 ("tokens", self.limits["audio_seconds_per_hour"])):
            left = _number(_header(headers, "x-ratelimit-remaining-" + counter), None)
            if left is not None and left <= max(60.0, ceiling * self.header_reserve):
                reset = parse_duration(_header(headers, "x-ratelimit-reset-" + counter))
                self._cooldown_until = max(self._cooldown_until, now + (reset or 3600.0))
        remaining = _number(_header(headers, "x-ratelimit-remaining-requests"), None)
        if remaining is None:
            return
        limit = _number(_header(headers, "x-ratelimit-limit-requests"), None) \
            or self.limits["requests_per_day"]
        if remaining <= max(20.0, limit * self.header_reserve):
            # The organization (maybe other devices) is near its daily cap:
            # leave the rest to final dictation until the counter resets.
            reset = parse_duration(_header(headers, "x-ratelimit-reset-requests"))
            self._cooldown_until = max(self._cooldown_until, now + (reset if reset else 3600.0))

    def record_final(self, seconds):
        with self._lock:
            self._events.append((self.clock(), self.billed(seconds), "final"))

    @contextmanager
    def final_priority(self, seconds):
        with self._lock:
            self._finals_in_flight += 1
            self._events.append((self.clock(), self.billed(seconds), "final"))
        try:
            yield
        finally:
            with self._lock:
                self._finals_in_flight -= 1


_DEFAULT_BUDGET = PreviewBudget()
_BUDGET_PATH = None


def configure_budget(path):
    """A single backend ledger; never open/reset it merely by importing."""
    global _DEFAULT_BUDGET, _BUDGET_PATH
    if _BUDGET_PATH != path:
        from preview_quota import DurablePreviewBudget
        _DEFAULT_BUDGET = DurablePreviewBudget(path)
        _BUDGET_PATH = path



def default_budget():
    return _DEFAULT_BUDGET


_FINAL_SCOPE = threading.local()


@contextmanager
def final_priority(audio_seconds):
    """Count one attempt even when the ghost job and engine both wrap it."""
    if getattr(_FINAL_SCOPE, "active", False):
        yield
        return
    _FINAL_SCOPE.active = True
    try:
        with _DEFAULT_BUDGET.final_priority(audio_seconds):
            yield
    finally:
        _FINAL_SCOPE.active = False


def observe_final_response(*, headers=None, error=None):
    """Best-effort accounting feedback must never change batch behavior."""
    try:
        outcome, retry = classify_error(error) if error is not None else ("ok", None)
        if error is not None:
            headers = getattr(getattr(error, "response", None), "headers", None)
        default_budget().end_preview(None, outcome, retry_after=retry, headers=headers)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Thread-free core (shared by the session and the deterministic benchmark)


class PreviewCore:
    """Scheduling, budgeting and reconciliation for one recording."""

    def __init__(self, settings, rate, *, budget=None, log=None):
        self.settings = settings
        self.planner = WindowPlanner(settings, rate)
        self.reconciler = TextReconciler()
        self.budget = budget or default_budget()
        self.disabled = ""
        self.failures = 0
        self.requests = 0
        self.next_check = 0.0
        self.stats = {"sent": 0, "ok": 0, "published": 0, "silence": 0, "filtered": 0,
                      "denied": 0, "failed": 0, "discarded": 0, "overflows": 0}
        self._log = log
        self._last_denial = ""

    @property
    def text(self):
        return self.reconciler.text

    def feed(self, chunk):
        self.planner.add(chunk)

    def discontinuity(self, lost_samples=0):
        self.stats["overflows"] += 1
        self.planner.discontinuity(lost_samples)

    def disable(self, reason):
        if not self.disabled:
            self.disabled = reason
            self.say(f"preview disabled for this recording ({reason})")

    def next_window(self, now):
        """Return a budget-reserved (window, token) to send, or None."""
        if self.disabled or now < self.next_check:
            return None
        if self.requests >= self.settings.max_requests:
            self.disable("request limit")
            return None
        window, reason = self.planner.propose()
        if window is None:
            if reason == "silence":
                self.stats["silence"] += 1
            return None
        token, denied = self.budget.try_begin_preview(window.duration)
        if token is None:
            self.stats["denied"] += 1
            if denied != self._last_denial:
                self._last_denial = denied
                self.say(f"preview deferred ({denied})")
            if denied in PERMANENT_DENIALS:
                self.disable(f"budget: {denied}")
            else:
                self.next_check = now + 0.25
            return None
        self._last_denial = ""
        # Single flight: the caller proposes nothing else until
        # finish_request() commits (or releases) this window.
        self.requests += 1
        self.stats["sent"] += 1
        return window, token

    def finish_request(self, window, token, outcome, now, *, result=None, headers=None,
                       retry_after=None, discard=False):
        """Account for a request; returns new preview text or None."""
        self.budget.end_preview(token, outcome, retry_after=retry_after, headers=headers)
        if outcome == "not_sent":
            # Nothing left the process: refund it and retry this audio later.
            self.requests = max(0, self.requests - 1)
            self.stats["sent"] = max(0, self.stats["sent"] - 1)
            if not discard:
                self.next_check = now + self.settings.interval_seconds / 2
            return None
        self.planner.commit(window, delivered=outcome == "ok" and not discard)
        if outcome != "ok":
            self.stats["failed"] += 1
            self.say(f"preview request failed ({outcome})")
            if outcome in ("rate_limited", "auth"):
                self.disable(outcome.replace("_", " "))
            else:
                self.failures += 1
                if self.failures >= self.settings.max_failures:
                    self.disable("repeated failures")
                else:
                    self.next_check = now + self.settings.interval_seconds
            return None
        self.failures = 0
        self.stats["ok"] += 1
        if discard:
            self.stats["discarded"] += 1
            return None
        text = clean_preview_text(result)
        if not text:
            self.stats["filtered"] += 1
            return None
        if not self.reconciler.add(text, overlap=window.overlap, gap=window.gap):
            return None
        self.stats["published"] += 1
        return self.reconciler.text

    def say(self, message):
        # Status only: never transcript text, audio or credentials.
        if self._log is not None:
            try:
                self._log(message)
            except Exception:
                pass


# --------------------------------------------------------------------------
# Threaded session


class GroqChunkSession:
    """Display-only live preview for exactly one recording."""

    def __init__(self, settings, *, input_rate=16000, client_provider=None, budget=None,
                 on_partial=None, log=None):
        self.settings = settings
        self.input_rate = int(input_rate) if input_rate else 16000
        self._provider = client_provider
        self._on_partial = on_partial
        self._log = log
        self._queue = queue.Queue(maxsize=max(1, int(settings.max_queue_chunks)))
        self._overflow_samples = 0
        # Guards core state only. Callbacks must never hold this lock;
        # consumers must reject an inactive/obsolete session at consumption.
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._stop_reason = ""
        self._generation = 0
        self._core = PreviewCore(settings, self.input_rate, budget=budget, log=log)
        self._thread = None
        self._request_thread = None

    def __repr__(self):
        return (f"<GroqChunkSession rate={self.input_rate} stopped={self._stopped.is_set()} "
                f"requests={self._core.requests}>")

    @property
    def active(self):
        return not self._stopped.is_set()

    @property
    def stop_reason(self):
        return self._stop_reason

    @property
    def preview_text(self):
        """Current preview; display only, never a dictation result."""
        with self._lock:
            return self._core.text

    def stats(self):
        with self._lock:
            return dict(self._core.stats, requests=self._core.requests,
                        disabled=self._core.disabled, buffered=self._core.planner.buffered_samples())

    def set_partial_callback(self, callback):
        with self._lock:
            self._on_partial = callback

    def start(self):
        if self._thread is None and not self._stopped.is_set():
            self._thread = threading.Thread(target=self._run, name="protocol7-preview", daemon=True)
            self._thread.start()

    def push_audio(self, chunk):
        """Queue one captured chunk. Runs on the PortAudio thread: never blocks."""
        if self._stopped.is_set():
            return
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._overflow_samples += len(chunk)
            except TypeError:
                self._overflow_samples += 1

    def stop(self, reason="stopped"):
        """End previews now. An in-flight request is abandoned, not awaited."""
        with self._lock:
            if self._stopped.is_set():
                return
            self._stop_reason = reason
            self._stopped.set()
            self._generation += 1
        self._core.say(f"preview stopped ({reason})")

    def cancel(self):
        self.stop("cancelled")

    def join(self, timeout=None):
        """Test helper: wait for the worker and any request thread."""
        for thread in (self._thread, self._request_thread):
            if thread is not None:
                thread.join(timeout)

    def _drain(self, timeout):
        chunks = []
        try:
            chunks.append(self._queue.get(timeout=timeout))
            while len(chunks) < 256:
                chunks.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        return chunks

    def _run(self):
        started = last_audio = time.monotonic()
        try:
            while not self._stopped.is_set():
                chunks = self._drain(0.05)
                now = time.monotonic()
                if chunks:
                    last_audio = now
                elif now - last_audio > self.settings.idle_timeout:
                    self.stop("idle")  # recorder gone: a dead session never calls Groq
                    break
                if now - started > self.settings.max_session_seconds:
                    self.stop("session limit")
                    break
                with self._lock:
                    if self._stopped.is_set():
                        break
                    lost, self._overflow_samples = self._overflow_samples, 0
                    if lost:
                        self._core.discontinuity(lost)
                    for chunk in chunks:
                        self._core.feed(chunk)
                    busy = self._request_thread is not None and self._request_thread.is_alive()
                    plan = None if busy else self._core.next_window(now)
                    if plan is not None:
                        self._request_thread = threading.Thread(
                            target=self._request, args=plan,
                            name="protocol7-preview-request", daemon=True,
                        )
                        self._request_thread.start()
        except Exception as exc:
            self._core.say(f"preview worker error ({type(exc).__name__})")
            self.stop("error")

    def _request(self, window, token):
        outcome, result, headers, retry_after = "not_sent", None, None, None
        try:
            try:
                info = self._provider() if self._provider is not None else None
            except Exception:
                info = None
            if not info:
                raise PreviewUnavailable("Groq client not ready")
            client, model = info
            wav = encode_wav(window.audio, window.rate)
            # Revoke queued windows after encoding. This is dispatch
            # admission, not a wire-level cancellation guarantee (see docs).
            # Atomic dispatch admission shares the scheduler with final
            # registration. Expensive encoding/client lookup happen before it.
            with self._lock:
                admitted = (not self._stopped.is_set() and self._core.budget.dispatch_allowed()
                            and (not self.settings.verification_deadline
                                 or time.time() < self.settings.verification_deadline))
            if admitted:
                result, headers = groq_transcribe(
                    client, model, wav, language=self.settings.language,
                    timeout=self.settings.request_timeout,
                )
                outcome = "ok"
        except PreviewUnavailable:
            outcome = "not_sent"
        except Exception as exc:
            outcome, retry_after = classify_error(exc)
            headers = getattr(getattr(exc, "response", None), "headers", None)
        with self._lock:
            text = self._core.finish_request(
                window, token, outcome, time.monotonic(), result=result, headers=headers,
                retry_after=retry_after, discard=self._stopped.is_set(),
            )
            if text is None or self._stopped.is_set() or self._on_partial is None:
                return
            callback = self._on_partial
            generation = self._generation
        # Never execute arbitrary/UI code while holding a lifecycle lock.
        # Consumers also check session identity and active at delivery time.
        if self.active and generation == self._generation:
            try:
                callback(text)
            except Exception:
                pass
