from __future__ import annotations

from dataclasses import dataclass
from threading import BoundedSemaphore, Lock


@dataclass
class AskSlotSnapshot:
    active: int
    limit: int


class AskConcurrencyLimiter:
    def __init__(self, *, max_concurrent: int) -> None:
        self._limit = max(1, int(max_concurrent))
        self._sem = BoundedSemaphore(self._limit)
        self._lock = Lock()
        self._active = 0

    @property
    def limit(self) -> int:
        return self._limit

    def try_acquire(self) -> bool:
        acquired = self._sem.acquire(blocking=False)
        if not acquired:
            return False
        with self._lock:
            self._active += 1
        return True

    def release(self) -> None:
        released = False
        with self._lock:
            if self._active > 0:
                self._active -= 1
                released = True
        if released:
            self._sem.release()

    def snapshot(self) -> AskSlotSnapshot:
        with self._lock:
            return AskSlotSnapshot(active=self._active, limit=self._limit)
