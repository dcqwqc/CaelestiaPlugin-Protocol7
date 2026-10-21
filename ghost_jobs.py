"""Bounded background transcription jobs with target-aware insertion."""
from __future__ import annotations
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import threading
import time

from config import add_history
from ghost_wave import Target


@dataclass
class GhostJob:
    id: str
    target: object
    audio: object
    status: str = "queued"
    text: str = ""
    error: str = ""
    cancelled: bool = False
    focus_epoch: int | None = None
    non_enter_epoch: int | None = None
    attempt: int = 0
    send_queued: bool = False
    # Optional opt-in live session. Its verified final replaces the batch
    # request; anything else falls back to the retained full recording.
    stream: object = None
    created: float = field(default_factory=time.monotonic)

    def public(self):
        return {
            "id": self.id, "status": self.status,
            "target": self.target.as_public() if self.target else {},
            "preview": self.text[:100] if self.status == "ready" else "",
            "retryable": self.audio is not None and len(self.audio) >= 8000,
            "sendQueued": self.send_queued,
        }


class GhostJobs:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self._paste_lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self.job_timeout_seconds = 40.0
        self.ready_visible_seconds = 45.0
        self.error_visible_seconds = 15.0
        self.jobs = OrderedDict()
        # Single STT worker keeps on-device Whisper and Groq ordering safe,
        # while recording and mouse/keyboard remain completely independent.
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="protocol7-ghost")
        self.state_path = Path.home() / ".local/state/protocol-7/ghost-jobs.json"
        self._restore()
        # A recovered result must be discoverable briefly, not become a
        # permanent floating chip after a plugin/shell restart.
        for recovered in tuple(self.jobs.values()):
            self._archive_after(recovered.id, "ready", self.ready_visible_seconds)
        self._publish()

    def _restore(self):
        # Only completed, uninserted text survives a backend restart. Never
        # resume an expired window address and never auto-inject restored text.
        try:
            rows = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(rows, list):
            return
        for row in rows[-12:]:
            if not isinstance(row, dict):
                continue
            jid, message = row.get("id"), row.get("text")
            if not isinstance(jid, str) or len(jid) != 12 or not all(c in "0123456789abcdef" for c in jid):
                continue
            if not isinstance(message, str) or not message.strip() or len(message) > 20000:
                continue
            hint = row.get("target")
            target = None
            if isinstance(hint, dict):
                try:
                    target = Target(
                        address="", app=str(hint.get("app", ""))[:100],
                        monitor=str(hint.get("monitor", ""))[:100],
                        x=max(0, int(hint.get("x", 0))), y=max(0, int(hint.get("y", 0))),
                    )
                except (ValueError, TypeError, OverflowError):
                    target = None
            self.jobs[jid] = GhostJob(jid, target, None, status="ready", text=message)

    def _persist(self):
        # Snapshot/write/replace must serialize across simultaneous jobs.
        with self._persist_lock:
            with self.lock:
                rows = [
                    {"id": j.id, "text": j.text,
                     "target": j.target.as_public() if j.target else {}}
                    for j in self.jobs.values()
                    if j.status == "ready" and j.text
                ][-12:]
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.state_path.with_name(self.state_path.name + ".tmp")
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(rows, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.state_path)
            except OSError:
                pass

    def snapshot(self):
        with self.lock:
            active = [job for job in self.jobs.values()
                      if job.status not in ("inserted", "cancelled", "archived")]
            # Never let an older retry/ready pill hide a newly active job.
            priority = {"processing": 0, "queued": 0, "inserting": 1,
                        "ready": 2, "error": 3}
            active.sort(key=lambda j: (priority.get(j.status, 4), -j.created))
            return [job.public() for job in active]

    def _archive_after(self, job_id, expected_status, duration):
        timer = threading.Timer(duration, self._archive_if_still, args=(job_id, expected_status))
        timer.daemon = True
        timer.start()

    def _archive_if_still(self, job_id, expected_status):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.status != expected_status:
                return
            # Ready text was already added to transcription history and is
            # recoverable there; hide the floating chip instead of pinning it.
            job.status = "archived"
            job.audio = None
        self._publish()
        self._persist()

    def _publish(self):
        rows = self.snapshot()
        self.app.ui_manager.set_jobs(rows)
        hook = getattr(self.app, "enter_interceptor", None)
        if hook and not any(
            row["status"] in ("queued", "processing", "inserting", "ready")
            for row in rows
        ):
            hook.disarm()

    def enqueue(self, audio, target=None, focus_epoch=None, non_enter_epoch=None, stream=None):
        with self.lock:
            busy = sum(j.status in ("queued", "processing") for j in self.jobs.values())
            job = GhostJob(
                secrets.token_hex(6), target, audio,
                focus_epoch=focus_epoch, non_enter_epoch=non_enter_epoch,
                stream=stream,
            )
            if busy >= 4:
                job.status = "error"
                job.error = "Busy processing other recordings"
                job.stream = None
            self.jobs[job.id] = job
            # Do not keep entire audio arrays indefinitely after completion.
            while len(self.jobs) > 16:
                oldest_id, oldest = next(iter(self.jobs.items()))
                if oldest.status in ("queued", "processing"):
                    break
                self.jobs.pop(oldest_id)
        if job.status == "queued":
            hook = getattr(self.app, "enter_interceptor", None)
            if hook and target:
                hook.arm()
        self._publish()
        if job.status == "queued":
            self.executor.submit(self._run, job)
        else:
            if stream is not None:
                stream.cancel()
            self._archive_after(job.id, "error", self.error_visible_seconds)
        return job

    def _expire(self, job_id, attempt):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.cancelled or job.status != "processing" or job.attempt != attempt:
                return
            job.status = "error"
            job.error = "Transcription timed out; tap to retry"
        self._publish()
        self._archive_after(job.id, "error", self.error_visible_seconds)

    def _take_stream(self, job):
        with self.lock:
            stream, job.stream = job.stream, None
        return stream

    def _verified_stream_text(self, job):
        """Return a verified live transcript, or None to use batch STT.

        The stream stays attached to the job while finalizing so that cancel
        and Double-Control dismissal can abort it promptly.
        """
        with self.lock:
            stream = job.stream
        if stream is None:
            return None
        try:
            outcome = stream.finish()
        except Exception:
            stream.cancel()
            return None
        finally:
            with self.lock:
                job.stream = None
        text = (outcome.text or "").strip() if outcome.verified else ""
        if not text:
            return None
        # Keep the existing guard against digit-only non-speech output.
        guard = getattr(self.app.whisper_engine, "_suspicious_numeric_hallucination", None)
        try:
            if guard is not None and guard(text, job.audio):
                return None
        except Exception:
            pass
        return text

    def _run(self, job):
        with self.lock:
            if job.cancelled or job.status not in ("queued", "error"):
                stale = job.stream
                job.stream = None
                runnable = False
            else:
                runnable = True
                job.attempt += 1
                attempt = job.attempt
                job.status = "processing"
        if not runnable:
            if stale is not None:
                stale.cancel()
            return
        self._publish()
        timer = threading.Timer(self.job_timeout_seconds, self._expire, args=(job.id, attempt))
        timer.daemon = True
        timer.start()
        try:
            if job.audio is None or len(job.audio) < 8000:
                stream = self._take_stream(job)
                if stream is not None:
                    stream.cancel()
                raise ValueError("Recording was too short")
            result = self._verified_stream_text(job)
            if result is None:
                if job.cancelled:
                    # Never start a (possibly paid) batch request for work
                    # the user already dismissed.
                    return
                result = self.app.whisper_engine.transcribe(job.audio)
            if not result or not result.strip():
                raise ValueError("No speech recognized")
            result = self.app.llm_rewriter.rewrite(result)
            if not result or not result.strip():
                raise ValueError("Transcription returned empty text")
            with self.lock:
                if job.cancelled or job.attempt != attempt or job.status != "processing":
                    return
                # Commit history and ready status together. A dismiss arriving
                # here either wins (nothing saved/inserted) or waits for the
                # completed result and then cancels its visible pill.
                add_history(result.strip())
                job.text = result.strip()
                job.audio = None
                job.status = "ready"
            self._publish()
            self._persist()
            self._archive_after(job.id, "ready", self.ready_visible_seconds)
            # Never synthesize a window switch to deliver the result. If the
            # user changed windows, retain a visible ready-to-insert chip.
            if job.target and self._focus_unchanged(job) and not self.app.is_active and job.target.same_window_active():
                self.insert(job.id, require_target=True)
        except Exception as exc:
            with self.lock:
                if job.cancelled or job.attempt != attempt or job.status != "processing":
                    return
                job.status = "error"
                job.error = str(exc)[:180]
            self._publish()
            self._archive_after(job.id, "error", self.error_visible_seconds)
        finally:
            timer.cancel()

    def _focus_unchanged(self, job):
        listener = getattr(self.app, "hotkey", None)
        if job.non_enter_epoch is not None:
            read = getattr(listener, "non_enter_focus_epoch", None)
            if read is None or read() != job.non_enter_epoch:
                return False
            if job.send_queued:
                # A captured Return may arrive at the evdev listener before or
                # after its IPC command. That must never invalidate a queued
                # send: only non-Return focus actions may do so.
                return True
        return job.focus_epoch is None or (
            listener is not None and listener.focus_epoch() == job.focus_epoch
        )

    def insert(self, job_id, *, require_target=False):
        # Serialise complete text injections. A second job can finish while
        # the first is inserting; their keystrokes must never interleave.
        with self._paste_lock:
            with self.lock:
                job = self.jobs.get(job_id)
                if job is None or job.cancelled or job.status != "ready":
                    return {"ok": False, "error": "job not ready"}
                if require_target and (
                    self.app.is_active or not self._focus_unchanged(job)
                    or not job.target or not job.target.same_window_active()
                ):
                    return {"ok": False, "error": "target changed"}
                job.status = "inserting"
                text = job.text
            self._publish()
            self._persist()
            try:
                ok = self.app.paste_text(text)
                if ok is False:
                    raise RuntimeError("text insertion failed")
                # Wayland virtual-keyboard delivery is asynchronous: a wtype
                # success means events were submitted, not that the composer
                # has rendered them. Keep the wave visible while its input
                # queue drains. Do not synthesize queued Enter yet.
                time.sleep(min(0.78, max(0.38, len(text) * 0.0015)))
            except Exception as exc:
                with self.lock:
                    job.status = "ready"
                    job.error = str(exc)[:180]
                self._publish()
                self._persist()
                return {"ok": False, "error": "insertion failed"}
            with self.lock:
                # A send is valid only if the original focus survived paste.
                send = job.send_queued and not job.cancelled and job.target is not None
                send = bool(send and self._focus_unchanged(job) and
                            job.target.same_window_active())
                job.status = "inserted"
                job.send_queued = False
            self._publish()
            self._persist()
            if send:
                # Another background job might still have the global Return
                # trap armed. Temporarily release it *unconditionally* before
                # synthesizing Send, or the synthetic Enter could enqueue on
                # the wrong job instead of reaching the text application.
                hook = getattr(self.app, "enter_interceptor", None)
                if hook and not hook.disarm():
                    return {"ok": False, "inserted": True, "sent": False}
                sender = getattr(self.app, "send_enter", None)
                sent = bool(sender()) if sender else False
                # Keep the next job interceptible if it is still pending.
                if hook:
                    with self.lock:
                        still_pending = any(
                            j.status in ("queued", "processing", "ready", "inserting")
                            and not j.cancelled for j in self.jobs.values()
                        )
                    if still_pending:
                        hook.arm()
                return {"ok": bool(sent), "inserted": True, "sent": bool(sent)}
            return {"ok": True, "inserted": True, "sent": False}

    def dismiss_visible(self):
        """Dismiss floating work atomically; late STT results cannot return.

        Only a completed manual text insertion is retained; we cannot safely
        interrupt synthesized keystrokes midway through an insertion.
        """
        with self.lock:
            visible = [
                job for job in self.jobs.values()
                if job.status in ("queued", "processing", "ready", "error", "inserting")
            ]
            if not visible:
                return False
            streams = []
            for job in visible:
                job.cancelled = True
                job.status = "cancelled"
                job.audio = None
                if job.stream is not None:
                    streams.append(job.stream)
                    job.stream = None
        for stream in streams:
            stream.cancel()
        self._publish()
        self._persist()
        return True

    def queue_send(self):
        """Return was consumed by Hyprland. Never let it hit an empty composer.

        The request is bound to one original active window and only submitted
        if paste succeeds there; other apps receive their intercepted Return.
        """
        hook = getattr(self.app, "enter_interceptor", None)
        # The control socket must never synthesize Return unless Hyprland has
        # explicitly intercepted a physical Return for this live job.
        if hook is not None and not hook.armed:
            return {"ok": False, "send_queued": False, "error": "Return hook inactive"}
        dispatch_ready_id = None
        with self.lock:
            candidates = [
                j for j in self.jobs.values()
                if j.status in ("queued", "processing", "ready", "inserting") and not j.cancelled
            ]
            candidates.sort(key=lambda j: j.created, reverse=True)
            job = next((j for j in candidates if j.target and j.target.same_window_active()), None)
            if job is not None:
                listener = getattr(self.app, "hotkey", None)
                read = getattr(listener, "non_enter_focus_epoch", None)
                if job.non_enter_epoch is not None and (read is None or read() != job.non_enter_epoch):
                    job = None
                else:
                    job.send_queued = True
                    if listener:
                        # Enter itself increments the old focus epoch. Update
                        # just that counter, NOT the non-Enter focus guard.
                        job.focus_epoch = listener.focus_epoch()
                    if job.status == "ready":
                        dispatch_ready_id = job.id
                    self._publish()
        if job is not None:
            if dispatch_ready_id:
                self.executor.submit(self.insert, dispatch_ready_id, require_target=True)
            return {"ok": True, "send_queued": True}
        forwarded = bool(hook.forward_return()) if hook else False
        return {"ok": forwarded, "send_queued": False, "forwarded": forwarded}

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.cancelled or job.status == "inserted":
                return {"ok": False, "error": "unknown job"}
            job.cancelled = True
            job.status = "cancelled"
            job.audio = None
            stream, job.stream = job.stream, None
        if stream is not None:
            stream.cancel()
        self._publish()
        self._persist()
        return {"ok": True, "cancelled": True}

    def retry(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.cancelled or job.status != "error" or job.audio is None or len(job.audio) < 8000:
                return {"ok": False, "error": "job not retryable"}
            job.status = "queued"
            job.error = ""
        self._publish()
        self.executor.submit(self._run, job)
        return {"ok": True, "retried": True}

    def counts(self):
        with self.lock:
            values = list(self.jobs.values())
        return {
            "processing": sum(j.status in ("queued", "processing") for j in values),
            "ready": sum(j.status == "ready" for j in values),
        }
