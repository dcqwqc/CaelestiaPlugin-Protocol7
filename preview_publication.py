"""Identity-aware publication at the actual UI consumer, including queued work."""
import threading


class PreviewPublication:
    def __init__(self):
        self._lock = threading.RLock()
        self._session = None

    def bind(self, session):
        with self._lock:
            self._session = session

    def consume(self, session, text, publish):
        # The validity check and UI mutation are a single critical section.
        # Bind/invalidate uses this same lock, never the request callback lock.
        with self._lock:
            if self._session is not session or not session.active:
                return False
            publish(text)
            return True
