from __future__ import annotations

from contextlib import contextmanager
from threading import Condition
import time
from typing import Any, Callable, Iterator


class Stage2UpstreamGateCancelled(RuntimeError):
    """Raised when a wait on the shared Stage2 gate is cancelled."""


class SharedStage2UpstreamGate:
    def __init__(
        self,
        *,
        name: str,
        limit: int,
        logger: Any | None,
        limit_provider: Callable[[], int] | None = None,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self.name = str(name or "")
        self.limit = max(1, int(limit))
        self.logger = logger
        self._limit_provider = limit_provider
        self._poll_interval_seconds = max(0.01, float(poll_interval_seconds or 0.1))
        self._condition = Condition()
        self._in_flight = 0

    def _current_limit(self, *, request_limit: int | None = None) -> int | None:
        dynamic_limit = self.limit
        if callable(self._limit_provider):
            try:
                dynamic_limit = int(self._limit_provider() or 0)
            except Exception:
                dynamic_limit = 0
        if dynamic_limit <= 0:
            return None
        effective_limit = min(self.limit, dynamic_limit)
        if request_limit is not None:
            request_cap = max(0, int(request_limit or 0))
            if request_cap <= 0:
                return None
            effective_limit = min(effective_limit, request_cap)
        return max(1, int(effective_limit))

    @contextmanager
    def enter(
        self,
        *,
        trace_label: str | None = None,
        request_limit: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[None]:
        acquired = False
        effective_limit: int | None = None
        started_at = time.monotonic()
        with self._condition:
            while True:
                if should_cancel is not None:
                    try:
                        if bool(should_cancel()):
                            raise Stage2UpstreamGateCancelled(f"stage2 {self.name} gate wait cancelled")
                    except Stage2UpstreamGateCancelled:
                        raise
                    except Exception:
                        pass
                effective_limit = self._current_limit(request_limit=request_limit)
                if effective_limit is None:
                    break
                if self._in_flight < effective_limit:
                    self._in_flight += 1
                    acquired = True
                    break
                self._condition.wait(timeout=self._poll_interval_seconds)

        if acquired and self.logger is not None:
            wait_ms = (time.monotonic() - started_at) * 1000.0
            self.logger.info(
                "stage2 %s gate wait_ms=%.2f trace_label=%s limit=%s",
                self.name,
                wait_ms,
                str(trace_label or ""),
                int(effective_limit or self.limit),
            )

        try:
            yield
        finally:
            if not acquired:
                return
            with self._condition:
                self._in_flight = max(0, self._in_flight - 1)
                self._condition.notify_all()


__all__ = ["SharedStage2UpstreamGate", "Stage2UpstreamGateCancelled"]
