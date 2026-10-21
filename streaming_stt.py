"""Opt-in incremental speech recognition while the user is speaking.

Groq Whisper on the complete recording stays the default and the fallback.
A live session exists only when the user explicitly enabled it, selected a
provider, supplied a key and separately authorized billing in config.json;
the optional websocket-client package must also be installed. A completed
file uploaded with ``stream=true`` is *not* live recognition and is never
treated as such here.

One I/O thread owns the transport. The PortAudio callback only enqueues
chunks, so a slow or failed connection can never stall capture. A final
transcript is returned as verified only when every captured chunk was sent on
one uninterrupted connection, the final commit was acknowledged and every
committed item completed; otherwise the caller transcribes the full recording
in batch. API keys and transcript text are never logged.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np

PROVIDER_OPENAI_REALTIME = "openai-realtime"
DEFAULT_MODEL = "gpt-live-transcribe"
DEFAULT_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
WIRE_RATE = 24000
COMMIT_EVENT_ID = "protocol7_final_commit"

EVENT_COMMITTED = "input_audio_buffer.committed"
EVENT_DELTA = "conversation.item.input_audio_transcription.delta"
EVENT_COMPLETED = "conversation.item.input_audio_transcription.completed"
EVENT_FAILED = "conversation.item.input_audio_transcription.failed"
ERROR_COMMIT_EMPTY = "input_audio_buffer_commit_empty"


class StreamingUnavailable(RuntimeError):
    """The optional live transport cannot be used on this installation."""


class TransportClosed(RuntimeError):
    """The live connection failed or was closed."""


@dataclass(frozen=True)
class StreamingSettings:
    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    language: str = ""
    url: str = DEFAULT_URL
    connect_timeout: float = 5.0
    finalize_timeout: float = 8.0
    # Quiet period after the last commit event before the final transcript is
    # declared complete; covers a server-VAD commit racing our own commit.
    settle_seconds: float = 0.3
    max_reconnects: int = 1
    reconnect_delay: float = 0.25
    max_queue_chunks: int = 8192


@dataclass(frozen=True)
class StreamingResult:
    verified: bool
    text: str = field(default="", repr=False)
    reason: str = ""


def _websocket_installed(find_spec=None):
    finder = find_spec or importlib.util.find_spec
    try:
        return finder("websocket") is not None
    except (ImportError, ValueError):
        return False


def availability(config, *, require_dependency=True, find_spec=None):
    """Return None when live recognition may run, else a non-secret reason."""
    if config.get("streaming_stt_enabled", False) is not True:
        return "disabled"
    provider = str(config.get("streaming_stt_provider") or "").strip()
    if not provider:
        return "no provider configured"
    if provider != PROVIDER_OPENAI_REALTIME:
        return "unsupported provider"
    if not str(config.get("streaming_stt_api_key") or "").strip():
        return "no API key configured"
    if config.get("streaming_stt_billing_authorized", False) is not True:
        return "billing not authorized"
    if require_dependency and not _websocket_installed(find_spec):
        return "websocket-client not installed"
    return None


def settings_from_config(config):
    try:
        finalize = float(config.get("streaming_stt_finalize_timeout_seconds", 8.0))
    except (TypeError, ValueError):
        finalize = 8.0
    language = ""
    if not config.get("auto_detect_language", True):
        language = str(config.get("language") or "").strip()
    return StreamingSettings(
        api_key=str(config.get("streaming_stt_api_key") or "").strip(),
        model=str(config.get("streaming_stt_model") or "").strip() or DEFAULT_MODEL,
        language=language,
        finalize_timeout=min(30.0, max(1.0, finalize)),
    )


def create_session(config, *, input_rate=16000, on_partial=None, log=None,
                   transport_factory=None, find_spec=None):
    """Build an unstarted session, or None when streaming is not opted in."""
    reason = availability(config, require_dependency=transport_factory is None,
                          find_spec=find_spec)
    if reason is not None:
        return None
    return StreamingSession(
        settings_from_config(config), input_rate=input_rate,
        transport_factory=transport_factory, on_partial=on_partial, log=log,
    )


class WebsocketTransport:
    """websocket-client transport, imported lazily so it stays optional."""

    def __init__(self, settings):
        try:
            import websocket
        except ImportError:
            raise StreamingUnavailable("websocket-client is not installed") from None
        if not hasattr(websocket, "create_connection"):
            raise StreamingUnavailable("incompatible websocket module")
        self._timeouts = (getattr(websocket, "WebSocketTimeoutException", TimeoutError), TimeoutError)
        try:
            self._ws = websocket.create_connection(
                settings.url,
                header=[f"Authorization: Bearer {settings.api_key}"],
                timeout=settings.connect_timeout,
                enable_multithread=True,
            )
        except Exception as exc:
            # Exception text may echo request details; keep only its type.
            raise TransportClosed(f"connect failed ({type(exc).__name__})") from None

    def send(self, text):
        try:
            self._ws.send(text)
        except Exception as exc:
            raise TransportClosed(f"send failed ({type(exc).__name__})") from None

    def recv(self, timeout):
        try:
            self._ws.settimeout(timeout)
            data = self._ws.recv()
        except self._timeouts:
            return None
        except Exception as exc:
            raise TransportClosed(f"receive failed ({type(exc).__name__})") from None
        if not data:
            raise TransportClosed("connection closed")
        return data

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass


def default_transport_factory(settings):
    return WebsocketTransport(settings)


class TranscriptState:
    """Live items keyed by provider item ID, kept in conversation order."""

    def __init__(self):
        self.order = []
        self.previous = {}
        self.deltas = {}
        self.finals = {}
        self.failed = set()
        # Slot for a new connection's first item (previous_item_id null).
        self.base = 0

    def _known(self, item_id):
        if item_id not in self.deltas:
            self.deltas[item_id] = ""
            self.order.append(item_id)

    def _place(self, item_id, placed):
        if item_id in placed:
            return
        placed.add(item_id)
        previous = self.previous[item_id]
        self.order.remove(item_id)
        if previous is None:
            self.order.insert(min(self.base, len(self.order)), item_id)
        elif previous in self.order:
            self.order.insert(self.order.index(previous) + 1, item_id)
        else:
            self.order.append(item_id)
        # Items committed earlier that named this one as their predecessor.
        for other, before in list(self.previous.items()):
            if before == item_id:
                self._place(other, placed)

    def commit(self, item_id, previous_id, has_previous):
        self._known(item_id)
        if has_previous:
            self.previous[item_id] = previous_id if isinstance(previous_id, str) and previous_id else None
            self._place(item_id, set())

    def delta(self, item_id, text):
        self._known(item_id)
        if item_id not in self.finals:
            self.deltas[item_id] += text

    def complete(self, item_id, transcript):
        self._known(item_id)
        self.finals[item_id] = transcript

    def fail(self, item_id):
        self._known(item_id)
        self.failed.add(item_id)

    def pending(self):
        return sum(1 for i in self.order if i not in self.finals and i not in self.failed)

    def text(self):
        parts = []
        for item_id in self.order:
            if item_id in self.failed:
                continue
            words = self.finals.get(item_id, self.deltas.get(item_id, "")).split()
            if words:
                parts.append(" ".join(words))
        return " ".join(parts)

    def drop_incomplete(self):
        """Forget items that a lost connection can never finish."""
        self.order = [i for i in self.order if i in self.finals]
        keep = set(self.order)
        self.deltas = {i: t for i, t in self.deltas.items() if i in keep}
        self.previous = {i: p for i, p in self.previous.items() if i in keep}
        self.failed &= keep
        self.base = len(self.order)


class StreamingSession:
    """One live recognition session for exactly one recording."""

    def __init__(self, settings, *, input_rate=16000, transport_factory=None,
                 on_partial=None, log=None):
        self.settings = settings
        self.input_rate = int(input_rate) if input_rate else 16000
        self._factory = transport_factory or default_transport_factory
        self._on_partial = on_partial
        self._log = log
        self._audio = queue.Queue(maxsize=max(1, int(settings.max_queue_chunks)))
        # Guards transcript state, flags and partial delivery: once cancel()
        # or finish() returns, no partial callback can still be in flight.
        self._lock = threading.RLock()
        self._finish = threading.Event()
        self._done = threading.Event()
        self._thread = None
        self._transport = None
        self._state = TranscriptState()
        self._closed = False
        self._cancelled = False
        self._result = None
        self._error = ""
        self._audio_dropped = False
        self._gap = False
        self._reconnects = 0
        self._in_samples = 0
        self._out_samples = 0
        self._commit_sent = False
        self._commit_acked = False
        self._last_commit = 0.0
        self._last_partial = ""

    def __repr__(self):
        return f"<StreamingSession model={self.settings.model!r} done={self._done.is_set()}>"

    def set_partial_callback(self, callback):
        with self._lock:
            self._on_partial = callback

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="protocol7-live-stt", daemon=True,
            )
            self._thread.start()

    def push_audio(self, chunk):
        """Queue one captured chunk. Called from PortAudio: never blocks."""
        if self._finish.is_set() or self._done.is_set():
            return
        try:
            self._audio.put_nowait(chunk)
        except queue.Full:
            # The live copy is now incomplete; the batch recording is not.
            self._audio_dropped = True

    def finish(self, timeout=None):
        """Stop accepting audio and wait for a verified final transcript."""
        with self._lock:
            if self._result is not None:
                return self._result
        self._finish.set()
        wait = self.settings.finalize_timeout if timeout is None else timeout
        completed = self._thread is not None and self._done.wait(max(0.0, wait))
        with self._lock:
            if self._result is None:
                self._closed = True
                text = self._state.text()
                reason = self._failure_locked(completed, text)
                self._result = StreamingResult(
                    reason is None, text if reason is None else "", reason or "verified",
                )
            result = self._result
        self._close_transport()
        self._say(f"finalize verified={result.verified} reason={result.reason}")
        return result

    def cancel(self):
        """Abandon the session; late provider events are ignored."""
        with self._lock:
            if self._result is None:
                self._cancelled = True
                self._result = StreamingResult(False, "", "cancelled")
                self._say("cancelled")
            self._closed = True
        self._finish.set()
        self._close_transport()

    def _failure_locked(self, completed, text):
        if self._cancelled:
            return "cancelled"
        if self._thread is None:
            return "not started"
        if not completed:
            return "timeout"
        if self._error:
            return self._error
        if self._audio_dropped:
            return "audio dropped"
        if self._gap:
            return "connection interrupted"
        if not self._commit_acked:
            return "final commit not acknowledged"
        if self._state.pending():
            return "transcription incomplete"
        if not text:
            return "empty transcript"
        return None

    def _run(self):
        try:
            if not self._open():
                return
            self._say("connected")
            while not self._closed:
                try:
                    sent = self._pump_audio()
                    if self._finish.is_set() and not sent and self._audio.empty():
                        if self._error or self._audio_dropped or self._gap:
                            return  # cannot verify; let the caller fall back now
                        if not self._commit_sent:
                            self._send_commit()
                        if self._finalized():
                            return
                    raw = self._transport.recv(0.005 if sent else 0.03)
                except TransportClosed:
                    if self._closed or not self._reconnect():
                        return
                    continue
                if raw is not None:
                    self._dispatch(raw)
        except Exception as exc:
            self._error = self._error or f"internal error ({type(exc).__name__})"
        finally:
            self._done.set()
            self._close_transport()

    def _open(self):
        try:
            transport = self._factory(self.settings)
        except Exception as exc:
            self._error = self._error or f"connect failed ({type(exc).__name__})"
            return False
        with self._lock:
            closed = self._closed
            if not closed:
                self._transport = transport
        if closed:
            try:
                transport.close()
            except Exception:
                pass
            return False
        try:
            transport.send(json.dumps(self._session_update()))
        except Exception as exc:
            self._error = self._error or f"session setup failed ({type(exc).__name__})"
            return False
        return True

    def _reconnect(self):
        if self._finish.is_set() or self._reconnects >= self.settings.max_reconnects:
            self._error = self._error or "connection lost"
            return False
        self._reconnects += 1
        with self._lock:
            # Uncommitted server-side audio died with the socket. Completed
            # text still serves as a preview, but the final must be batch.
            self._gap = True
            self._state.drop_incomplete()
        self._say("reconnecting")
        self._close_transport()
        if self._finish.wait(self.settings.reconnect_delay):
            self._error = self._error or "connection lost"
            return False
        return self._open()

    def _session_update(self):
        transcription = {"model": self.settings.model}
        if self.settings.language:
            transcription["language"] = self.settings.language
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": WIRE_RATE},
                        "transcription": transcription,
                        "turn_detection": {
                            "type": "server_vad", "threshold": 0.5,
                            "prefix_padding_ms": 300, "silence_duration_ms": 500,
                        },
                    },
                },
            },
        }

    def _pump_audio(self, max_chunks=64):
        chunks = []
        while len(chunks) < max_chunks:
            try:
                chunks.append(self._audio.get_nowait())
            except queue.Empty:
                break
        if not chunks:
            return False
        audio = np.concatenate([np.asarray(c, dtype=np.float32).reshape(-1) for c in chunks])
        payload = self._wire_bytes(audio)
        if payload:
            self._transport.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(payload).decode("ascii"),
            }))
        return True

    def _wire_bytes(self, audio):
        """Resample to 24 kHz PCM16, tracking position across batches."""
        start = self._in_samples
        self._in_samples += audio.size
        total = int(round(self._in_samples * WIRE_RATE / self.input_rate))
        count = total - self._out_samples
        if count <= 0 or audio.size == 0:
            return b""
        if self.input_rate == WIRE_RATE and count == audio.size:
            wire = audio
        else:
            positions = np.arange(self._out_samples, total) * (self.input_rate / WIRE_RATE) - start
            wire = np.interp(positions, np.arange(audio.size), audio)
        self._out_samples = total
        return (np.clip(wire, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    def _send_commit(self):
        self._commit_sent = True
        if self._out_samples == 0:
            # Nothing was ever appended, so there is no buffer to commit.
            with self._lock:
                self._commit_acked = True
            return
        self._transport.send(json.dumps({
            "type": "input_audio_buffer.commit", "event_id": COMMIT_EVENT_ID,
        }))

    def _finalized(self):
        with self._lock:
            return (
                self._commit_acked and self._state.pending() == 0
                and time.monotonic() - self._last_commit >= self.settings.settle_seconds
            )

    def _dispatch(self, raw):
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        item_id = event.get("item_id")
        with self._lock:
            if self._closed:
                return
            if etype == "error":
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                code = str(error.get("code") or error.get("type") or "unknown")[:64]
                if code == ERROR_COMMIT_EMPTY:
                    # Server VAD already committed everything we sent.
                    if self._commit_sent:
                        self._commit_acked = True
                        self._last_commit = time.monotonic()
                    return
                # Provider messages may quote input; keep only the code.
                if not self._error:
                    self._error = f"provider error ({code})"
                    self._say(self._error)
                return
            if not isinstance(item_id, str) or not item_id:
                return
            if etype == EVENT_COMMITTED:
                self._state.commit(item_id, event.get("previous_item_id"), "previous_item_id" in event)
                self._last_commit = time.monotonic()
                if self._commit_sent:
                    self._commit_acked = True
            elif etype == EVENT_DELTA:
                self._state.delta(item_id, str(event.get("delta") or ""))
            elif etype == EVENT_COMPLETED:
                self._state.complete(item_id, str(event.get("transcript") or ""))
            elif etype == EVENT_FAILED:
                self._state.fail(item_id)
                if not self._error:
                    self._error = "item transcription failed"
                    self._say(self._error)
            else:
                return
            self._publish_partial_locked()

    def _publish_partial_locked(self):
        text = self._state.text()
        if not text or text == self._last_partial:
            return
        self._last_partial = text
        if self._on_partial is not None:
            try:
                self._on_partial(text)
            except Exception:
                pass

    def _close_transport(self):
        transport = self._transport
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass

    def _say(self, message):
        # Status only: never transcript text or credentials.
        if self._log is not None:
            try:
                self._log(message)
            except Exception:
                pass
