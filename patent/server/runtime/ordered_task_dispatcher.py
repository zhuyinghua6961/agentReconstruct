from __future__ import annotations

from anyio import CapacityLimiter
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock


@dataclass
class StreamSlotLease:
    _dispatcher: "OrderedTaskDispatcher"
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._dispatcher._release_stream_slot()

    def __enter__(self) -> "StreamSlotLease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class OrderedTaskDispatcher:
    def __init__(self, *, stream_max_concurrent: int, ask_executor_max_workers: int) -> None:
        self._stream_capacity = max(1, int(stream_max_concurrent))
        self._ask_executor_max_workers = max(1, int(ask_executor_max_workers))
        self._ask_limiter = CapacityLimiter(total_tokens=self._ask_executor_max_workers)
        self._stream_slots = BoundedSemaphore(value=self._stream_capacity)
        self._lock = Lock()
        self._inflight_streams = 0

    def try_acquire_stream_slot(self) -> StreamSlotLease | None:
        acquired = self._stream_slots.acquire(blocking=False)
        if not acquired:
            return None
        with self._lock:
            self._inflight_streams += 1
        return StreamSlotLease(self)

    def runtime_state(self) -> dict[str, int | bool]:
        with self._lock:
            available = self._stream_capacity - self._inflight_streams
        return {
            "ready": True,
            "stream_slots_capacity": self._stream_capacity,
            "stream_slots_available": max(0, int(available)),
            "ask_executor_max_workers": self._ask_executor_max_workers,
        }

    @property
    def ask_limiter(self) -> CapacityLimiter:
        return self._ask_limiter

    def _release_stream_slot(self) -> None:
        should_release = False
        with self._lock:
            if self._inflight_streams > 0:
                self._inflight_streams -= 1
                should_release = True
        if should_release:
            self._stream_slots.release()
