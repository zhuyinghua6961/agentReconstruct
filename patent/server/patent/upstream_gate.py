from __future__ import annotations

from contextlib import contextmanager
from threading import Condition
import os
import time
from types import SimpleNamespace
from typing import Any, Callable, Iterator


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


class PatentPlanningUpstreamGateCancelled(RuntimeError):
    """Raised when a planning upstream gate wait is cancelled."""


class _PlanningGateProxy:
    def __init__(
        self,
        *,
        gate: "PatentPlanningUpstreamGate",
        base_client: Any,
        trace_label: str,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        self._gate = gate
        self._base_client = base_client
        self._trace_label = trace_label
        self._should_cancel = should_cancel
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs) -> Any:
        with self._gate.enter(trace_label=self._trace_label, should_cancel=self._should_cancel):
            return self._base_client.chat.completions.create(**kwargs)


class PatentPlanningUpstreamGate:
    def __init__(
        self,
        *,
        name: str,
        limit: int,
        logger: Any | None = None,
        limit_provider: Callable[[], int] | None = None,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self.name = str(name or "planning")
        self.limit = max(1, int(limit))
        self.logger = logger
        self._limit_provider = limit_provider
        self._poll_interval_seconds = max(0.01, float(poll_interval_seconds or 0.1))
        self._condition = Condition()
        self._in_flight = 0

    @classmethod
    def from_env(
        cls,
        *,
        name: str = "planning",
        logger: Any | None = None,
        limit_provider: Callable[[], int] | None = None,
        poll_interval_seconds: float = 0.1,
    ) -> "PatentPlanningUpstreamGate | None":
        if not _env_flag("PATENT_PLANNING_UPSTREAM_GATE_ENABLED", False):
            return None
        return cls(
            name=name,
            limit=max(1, _env_int("PATENT_PLANNING_UPSTREAM_GATE_LIMIT", 1)),
            logger=logger,
            limit_provider=limit_provider,
            poll_interval_seconds=poll_interval_seconds,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        name: str = "planning",
        logger: Any | None = None,
        limit_provider: Callable[[], int] | None = None,
        poll_interval_seconds: float = 0.1,
    ) -> "PatentPlanningUpstreamGate | None":
        gate_settings = getattr(settings, "planning_upstream_gate", settings)
        if not bool(getattr(gate_settings, "enabled", False)):
            return None
        return cls(
            name=name,
            limit=max(1, int(getattr(gate_settings, "limit", 1) or 1)),
            logger=logger,
            limit_provider=limit_provider,
            poll_interval_seconds=poll_interval_seconds,
        )

    def _current_limit(self) -> int:
        if not callable(self._limit_provider):
            return self.limit
        try:
            dynamic_limit = int(self._limit_provider() or 0)
        except Exception:
            dynamic_limit = 0
        return max(0, min(self.limit, dynamic_limit))

    @contextmanager
    def enter(
        self,
        *,
        trace_label: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[None]:
        acquired = False
        effective_limit = 0
        started_at = time.monotonic()
        with self._condition:
            while True:
                if should_cancel is not None:
                    try:
                        if bool(should_cancel()):
                            raise PatentPlanningUpstreamGateCancelled(
                                f"{self.name} upstream gate wait cancelled"
                            )
                    except PatentPlanningUpstreamGateCancelled:
                        raise
                    except Exception:
                        pass
                effective_limit = self._current_limit()
                if effective_limit <= 0:
                    break
                if effective_limit > 0 and self._in_flight < effective_limit:
                    self._in_flight += 1
                    acquired = True
                    break
                self._condition.wait(timeout=self._poll_interval_seconds)

        if acquired and self.logger is not None:
            self.logger.info(
                "patent %s upstream gate wait_ms=%.2f trace_label=%s limit=%s",
                self.name,
                (time.monotonic() - started_at) * 1000.0,
                str(trace_label or ""),
                effective_limit,
            )
        try:
            yield
        finally:
            if not acquired:
                return
            with self._condition:
                self._in_flight = max(0, self._in_flight - 1)
                self._condition.notify_all()

    def proxy_client(
        self,
        *,
        base_client: Any | None,
        trace_label: str,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Any | None:
        if base_client is None:
            return None
        return _PlanningGateProxy(
            gate=self,
            base_client=base_client,
            trace_label=trace_label,
            should_cancel=should_cancel,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            in_flight = self._in_flight
        return {
            "name": self.name,
            "limit": self.limit,
            "effective_limit": self._current_limit(),
            "in_flight": in_flight,
        }
