"""Offline durable preview quota ledger; no network or permanent service.

The two allowed producers each own HALF of every organization limit. The
operator must verify that there are no other producers and renew that
assertion daily. A new ledger warms up for 24 hours, recording finals, before
allowing previews. Finals always proceed even if accounting fails; such a
failure disables previews for this process. This is not a billing guarantee.
"""
from collections import deque
from contextlib import contextmanager
import json
import os
import sqlite3
import threading
import time

from streaming_stt import PreviewBudget, FREE_TIER_LIMITS, DispatchScheduler, PreviewUnavailable


class _LedgerLock:
    """Serialize read/modify/write across processes; bounded SQLite waits."""

    def __init__(self, owner, path):
        self.owner, self.path = owner, path
        self.local = threading.RLock()
        self.depth = 0
        self.connection = None

    def __enter__(self):
        self.local.acquire()
        if self.depth:
            self.depth += 1
            return self
        try:
            db = sqlite3.connect(self.path, timeout=0.025)
            self.connection = db
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
            row = db.execute("SELECT state FROM ledger WHERE id=1").fetchone()
            owner = self.owner
            now = owner.clock()
            if row:
                state = json.loads(row[0])
                if state['version'] != 1 or now < state['last_seen']:
                    raise ValueError('unknown ledger version or clock rollback')
                owner._events = deque((tuple(e) for e in state['events']), maxlen=20000)
                owner._strikes = deque(state['strikes'])
                owner._cooldown_until = state['cooldown']
                owner._disabled = state['disabled']
                owner._first_seen = state['first_seen']
                owner._finals_in_flight = state['finals']
                owner._dispatch_epoch = state.get('dispatch_epoch', 0)
            else:
                owner._first_seen = now
            self.depth = 1
            return self
        except BaseException:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            self.local.release()
            raise

    def __exit__(self, typ, value, traceback):
        self.depth -= 1
        try:
            if self.depth == 0:
                db, self.connection = self.connection, None
                try:
                    if typ is None:
                        o = self.owner
                        o._prune(o.clock())
                        state = dict(version=1, events=list(o._events), strikes=list(o._strikes),
                                     cooldown=o._cooldown_until, disabled=o._disabled,
                                     first_seen=o._first_seen, last_seen=o.clock(), finals=o._finals_in_flight, dispatch_epoch=o._dispatch_epoch)
                        db.execute("INSERT OR REPLACE INTO ledger VALUES (1, ?)", (json.dumps(state),))
                        db.commit()
                finally:
                    db.close()
        finally:
            self.local.release()


class LedgerDispatchScheduler(DispatchScheduler):
    """SQLite transaction orders final registration and first-byte transport.

    Lock order is ledger -> local scheduler. Stop only takes the local lock;
    no blocking network operation is permitted inside dispatch's callable.
    """
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def ticket(self):
        try:
            with self.owner._lock:
                return (super().ticket(), self.owner._dispatch_epoch)
        except Exception:
            self.owner._mark_fault()
            return None

    def dispatch(self, session, epoch, start, **kwargs):
        called, transport_error = False, None
        def initiate(**request):
            nonlocal called, transport_error
            called = True
            try:
                return start(**request)
            except Exception as exc:
                # Readiness/HTTP failures are not ledger failures. Commit the
                # ordering transaction, then propagate outside fault handling.
                transport_error = exc
        try:
            with self.owner._lock:
                o = self.owner
                if (epoch is None or not isinstance(epoch, tuple) or
                        epoch[1] != o._dispatch_epoch or o._finals_in_flight or
                        o._fault or os.path.exists(o._unsafe_path) or
                        o._disabled or o.clock() < o._cooldown_until):
                    raise PreviewUnavailable("shared dispatch revoked")
                result = super().dispatch(session, epoch[0], initiate, **kwargs)
        except PreviewUnavailable:
            raise
        except Exception as exc:
            self.owner._mark_fault()
            if called:
                # A write may already have escaped before commit failed.
                # Never classify it as definitely unsent or refund its quota.
                raise RuntimeError("ledger failed after transport initiation") from exc
            raise PreviewUnavailable("shared dispatch unavailable") from exc
        if transport_error is not None:
            raise transport_error
        return result


class DurablePreviewBudget(PreviewBudget):
    def __init__(self, path, *, clock=time.time):
        # Preview usage <= 40% of this host's half, leaving 60% for finals.
        super().__init__(clock=clock, limits={k: v / 2 for k, v in FREE_TIER_LIMITS.items()}, rpm_cap=4)
        self._dispatch_epoch = 0
        self.scheduler = LedgerDispatchScheduler(self)
        self._first_seen = clock()
        self._fault = False
        self._unsafe_path = path + ".unsafe"
        self._lock = _LedgerLock(self, path)

    def _mark_fault(self):
        self._fault = True
        # Separate durable poison flag also covers a final whose ledger was
        # busy. All processes must then stop previewing; do not lose its cost.
        try:
            fd = os.open(self._unsafe_path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        except OSError:
            pass

    def dispatch_allowed(self):
        if self.disabled_reason():
            return False
        try:
            return super().dispatch_allowed()
        except Exception:
            self._mark_fault()
            return False

    def disabled_reason(self):
        if self._fault or os.path.exists(self._unsafe_path):
            return 'quota ledger unavailable'
        try:
            with self._lock:
                if self.clock() - self._first_seen < 86400:
                    return 'quota history warming (24 hours)'
                return self._disabled
        except Exception:
            self._mark_fault()
            return 'quota ledger unavailable'

    def try_begin_preview(self, seconds):
        if self.disabled_reason():
            return None, 'disabled'
        try:
            return super().try_begin_preview(seconds)
        except Exception:
            self._mark_fault()
            return None, 'disabled'

    def end_preview(self, *args, **kwargs):
        try:
            return super().end_preview(*args, **kwargs)
        except Exception:
            self._mark_fault()

    @contextmanager
    def final_priority(self, seconds):
        # Never hold a ledger lock while transcribing, nor prevent the final
        # when another process owns the ledger or the disk is unavailable.
        self.scheduler.revoke(final_delta=1)
        registered = False
        try:
            with self._lock:
                self._dispatch_epoch += 1
                self._finals_in_flight += 1
                self._events.append((self.clock(), self.billed(seconds), 'final'))
                registered = True
        except Exception:
            self._mark_fault()
        try:
            yield
        finally:
            self.scheduler.revoke(final_delta=-1)
            if registered:
                try:
                    with self._lock:
                        self._finals_in_flight = max(0, self._finals_in_flight - 1)
                except Exception:
                    self._mark_fault()
