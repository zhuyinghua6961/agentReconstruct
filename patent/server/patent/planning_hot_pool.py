from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Lock, Thread
from types import SimpleNamespace
import time
from typing import Any, Callable, Iterator


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


@dataclass(frozen=True)
class PatentPlanningHotPoolConfig:
    enabled: bool
    lane_count: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    stream_read_timeout_seconds: float
    write_timeout_seconds: float
    pool_timeout_seconds: float
    keepalive_expiry_seconds: float
    warmup_enabled: bool = False
    warm_interval_seconds: float = 7200.0
    warm_timeout_seconds: float = 30.0
    warm_jitter_seconds: float = 0.0
    lane_degraded_after_seconds: float = 7200.0
    warm_active_start_hour: int = 8
    warm_active_end_hour: int = 18

    @classmethod
    def from_env(cls) -> "PatentPlanningHotPoolConfig":
        return cls(
            enabled=True,
            lane_count=max(1, _env_int("PATENT_PLANNING_HOT_POOL_LANE_COUNT", 2)),
            connect_timeout_seconds=_env_float("PATENT_LLM_HTTP_CONNECT_TIMEOUT_SECONDS", 15.0),
            read_timeout_seconds=_env_float("PATENT_LLM_HTTP_READ_TIMEOUT_SECONDS", 180.0),
            stream_read_timeout_seconds=_env_float("PATENT_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS", 600.0),
            write_timeout_seconds=_env_float("PATENT_LLM_HTTP_WRITE_TIMEOUT_SECONDS", 180.0),
            pool_timeout_seconds=_env_float("PATENT_LLM_HTTP_POOL_TIMEOUT_SECONDS", 30.0),
            keepalive_expiry_seconds=_env_float("PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", 120.0),
            warmup_enabled=False,
            warm_interval_seconds=7200.0,
            warm_timeout_seconds=30.0,
            warm_jitter_seconds=0.0,
            lane_degraded_after_seconds=max(
                1.0,
                _env_float("PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS", 7200.0),
            ),
            warm_active_start_hour=0,
            warm_active_end_hour=24,
        )

    @classmethod
    def from_settings(cls, settings: Any) -> "PatentPlanningHotPoolConfig":
        llm_http = getattr(settings, "llm_http", settings)
        planning = getattr(settings, "planning_hot_pool", settings)
        return cls(
            enabled=True,
            lane_count=max(1, int(getattr(planning, "lane_count", 2) or 2)),
            connect_timeout_seconds=float(getattr(llm_http, "connect_timeout_seconds", 15.0)),
            read_timeout_seconds=float(getattr(llm_http, "read_timeout_seconds", 180.0)),
            stream_read_timeout_seconds=float(getattr(llm_http, "stream_read_timeout_seconds", 600.0)),
            write_timeout_seconds=float(getattr(llm_http, "write_timeout_seconds", 180.0)),
            pool_timeout_seconds=float(getattr(llm_http, "pool_timeout_seconds", 30.0)),
            keepalive_expiry_seconds=float(getattr(llm_http, "keepalive_expiry_seconds", 120.0)),
            warmup_enabled=False,
            warm_interval_seconds=7200.0,
            warm_timeout_seconds=30.0,
            warm_jitter_seconds=0.0,
            lane_degraded_after_seconds=max(
                1.0,
                float(getattr(planning, "lane_degraded_after_seconds", 7200.0) or 7200.0),
            ),
            warm_active_start_hour=0,
            warm_active_end_hour=24,
        )


@dataclass
class PatentPlanningHotLane:
    lane_id: int
    http_client: Any
    client: Any | None = None
    state: str = "ready"
    in_flight: int = 0
    pool_timeout_count: int = 0
    last_pool_wait_ms: float = 0.0
    last_warm_success_at: str = ""
    last_warm_success_monotonic: float = 0.0
    last_error_at: str = ""
    last_error_summary: str = ""


class _LaneTransportObserver:
    def __init__(self, *, lane: PatentPlanningHotLane, config: PatentPlanningHotPoolConfig) -> None:
        self._lane = lane
        self.config = SimpleNamespace(
            connect_timeout_seconds=float(config.connect_timeout_seconds),
            read_timeout_seconds=float(config.read_timeout_seconds),
            stream_read_timeout_seconds=float(config.stream_read_timeout_seconds),
            write_timeout_seconds=float(config.write_timeout_seconds),
            pool_timeout_seconds=float(config.pool_timeout_seconds),
            keepalive_expiry_seconds=float(config.keepalive_expiry_seconds),
            max_connections=1,
            max_keepalive_connections=1,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "pool_owner": "app",
            "client_owner": "lane",
            "shared_client_id": f"planning-hot-lane-{self._lane.lane_id}-{id(self._lane.http_client):x}",
            "pid": os.getpid(),
            "bootstrap_source": "planning_hot_pool",
            "pool_timeout_count": self._lane.pool_timeout_count,
            "pool_wait_ms": self._lane.last_pool_wait_ms,
            "max_connections": 1,
            "max_keepalive_connections": 1,
            "keepalive_expiry_seconds": float(self.config.keepalive_expiry_seconds),
        }

    def record_pool_wait(self, *, wait_ms: float) -> None:
        self._lane.last_pool_wait_ms = max(0.0, float(wait_ms or 0.0))

    def record_pool_timeout(self, *, wait_ms: float) -> None:
        self._lane.pool_timeout_count += 1
        self._lane.last_pool_wait_ms = max(0.0, float(wait_ms or 0.0))


class _PlanningHotPoolProxy:
    def __init__(self, *, pool: "PatentPlanningHotPool", fallback_client: Any | None) -> None:
        self._pool = pool
        self._fallback_client = fallback_client
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs) -> Any:
        try:
            with self._pool.acquire() as client:
                return client.chat.completions.create(**kwargs)
        except LookupError:
            if self._fallback_client is None:
                raise
            return self._fallback_client.chat.completions.create(**kwargs)


class PatentPlanningHotPool:
    def __init__(
        self,
        *,
        config: PatentPlanningHotPoolConfig,
        lane_client_builder: Callable[..., Any],
        httpx_module: Any | None = None,
        logger: Any | None = None,
        warm_lane_fn: Callable[..., Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        warm_model: str = "",
    ) -> None:
        if httpx_module is None:
            import httpx as httpx_module

        self.config = config
        self.enabled = bool(config.enabled)
        self._httpx = httpx_module
        self._logger = logger
        self._lane_client_builder = lane_client_builder
        self._warm_lane_fn = warm_lane_fn
        self._now_fn = now_fn
        self._warm_model = str(warm_model or "").strip()
        self._closed = False
        self._lock = Lock()
        self._next_lane = 0
        self._warmup_enabled = bool(config.warmup_enabled)
        self._warm_interval_seconds = max(1.0, float(config.warm_interval_seconds or 0.0))
        self._warm_timeout_seconds = max(1.0, float(config.warm_timeout_seconds or 0.0))
        self._warm_jitter_seconds = max(0.0, float(config.warm_jitter_seconds or 0.0))
        self._lane_degraded_after_seconds = max(1.0, float(config.lane_degraded_after_seconds or 0.0))
        self._warm_active_start_hour = max(0, min(23, int(config.warm_active_start_hour or 0)))
        self._warm_active_end_hour = max(1, min(24, int(config.warm_active_end_hour or 24)))
        self._last_any_warm_success_at = ""
        self._last_any_error_at = ""
        self._last_error_summary = ""
        self._next_keepalive_at = ""
        self._stop_event = Event()
        self._scheduler_thread: Thread | None = None
        self._lanes: list[PatentPlanningHotLane] = []
        if not self.enabled:
            return

        built_lanes: list[PatentPlanningHotLane] = []
        try:
            for lane_id in range(max(1, int(config.lane_count))):
                http_client = self._build_lane_http_client()
                lane = PatentPlanningHotLane(lane_id=lane_id, http_client=http_client)
                try:
                    setattr(http_client, "_patent_shared_pool", _LaneTransportObserver(lane=lane, config=config))
                except Exception:
                    pass
                try:
                    lane.client = lane_client_builder(http_client=http_client)
                except Exception:
                    self._close_http_client(http_client)
                    raise
                if lane.client is None:
                    self._close_http_client(http_client)
                    raise RuntimeError("planning hot lane client builder returned no client")
                built_lanes.append(lane)
        except Exception:
            for lane in reversed(built_lanes):
                self._close_lane(lane)
            raise
        self._lanes = built_lanes
        if self._warmup_enabled and self._lanes:
            self.start()

    @classmethod
    def from_env(
        cls,
        *,
        lane_client_builder: Callable[..., Any],
        httpx_module: Any | None = None,
        logger: Any | None = None,
        warm_lane_fn: Callable[..., Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        warm_model: str = "",
    ) -> "PatentPlanningHotPool":
        return cls(
            config=PatentPlanningHotPoolConfig.from_env(),
            lane_client_builder=lane_client_builder,
            httpx_module=httpx_module,
            logger=logger,
            warm_lane_fn=warm_lane_fn,
            now_fn=now_fn,
            warm_model=warm_model,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        lane_client_builder: Callable[..., Any],
        httpx_module: Any | None = None,
        logger: Any | None = None,
        warm_lane_fn: Callable[..., Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        warm_model: str = "",
    ) -> "PatentPlanningHotPool":
        return cls(
            config=PatentPlanningHotPoolConfig.from_settings(settings),
            lane_client_builder=lane_client_builder,
            httpx_module=httpx_module,
            logger=logger,
            warm_lane_fn=warm_lane_fn,
            now_fn=now_fn,
            warm_model=warm_model,
        )

    def _build_lane_http_client(self) -> Any:
        timeout = self._httpx.Timeout(
            connect=float(self.config.connect_timeout_seconds),
            read=float(self.config.read_timeout_seconds),
            write=float(self.config.write_timeout_seconds),
            pool=float(self.config.pool_timeout_seconds),
        )
        limits = self._httpx.Limits(
            max_connections=1,
            max_keepalive_connections=1,
            keepalive_expiry=float(self.config.keepalive_expiry_seconds),
        )
        return self._httpx.Client(timeout=timeout, limits=limits, http2=False)

    @staticmethod
    def _close_http_client(http_client: Any | None) -> None:
        close = getattr(http_client, "close", None)
        if callable(close):
            close()

    @classmethod
    def _close_lane(cls, lane: PatentPlanningHotLane) -> None:
        close_client = getattr(lane.client, "close", None)
        if callable(close_client):
            close_client()
        cls._close_http_client(lane.http_client)

    def _now(self) -> datetime:
        current = self._now_fn() if callable(self._now_fn) else datetime.now(timezone.utc).astimezone()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone()

    def _now_iso(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def _is_warm_window_active(self, now: datetime) -> bool:
        start_hour = self._warm_active_start_hour
        end_hour = self._warm_active_end_hour
        if start_hour == 0 and end_hour == 24:
            return True
        if start_hour == end_hour:
            return True
        hour = int(now.hour)
        if start_hour < end_hour:
            return start_hour <= hour < end_hour
        return hour >= start_hour or hour < end_hour

    def _next_window_start(self, now: datetime) -> datetime:
        start_hour = self._warm_active_start_hour
        end_hour = self._warm_active_end_hour
        if start_hour == 0 and end_hour == 24:
            return now
        if self._is_warm_window_active(now):
            return now
        candidate = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        if now < candidate:
            return candidate
        return candidate + timedelta(days=1)

    def _cycle_jitter_seconds(self) -> float:
        if self._warm_jitter_seconds <= 0.0:
            return 0.0
        return self._warm_jitter_seconds * (float(os.getpid() % 7) / 6.0)

    def _refresh_stale_lanes_locked(self) -> None:
        now_monotonic = time.monotonic()
        for lane in self._lanes:
            if lane.state != "ready" or lane.in_flight != 0 or lane.last_warm_success_monotonic <= 0.0:
                continue
            if now_monotonic - lane.last_warm_success_monotonic <= self._lane_degraded_after_seconds:
                continue
            self._mark_degraded_locked(lane, "warm_expired")

    def _mark_ready_locked(self, lane: PatentPlanningHotLane) -> None:
        lane.state = "ready"
        lane.last_warm_success_at = self._now_iso()
        lane.last_warm_success_monotonic = time.monotonic()
        lane.last_error_at = ""
        lane.last_error_summary = ""
        self._last_any_warm_success_at = lane.last_warm_success_at
        self._last_error_summary = ""

    def _mark_degraded_locked(self, lane: PatentPlanningHotLane, error_summary: str) -> None:
        lane.state = "degraded"
        lane.last_error_at = self._now_iso()
        lane.last_error_summary = str(error_summary or "")
        self._last_any_error_at = lane.last_error_at
        self._last_error_summary = lane.last_error_summary

    def _default_warm_lane(self, lane: PatentPlanningHotLane) -> None:
        if not self._warm_model:
            raise RuntimeError("planning hot pool warm model is unavailable")
        create = getattr(getattr(getattr(lane.client, "chat", None), "completions", None), "create", None)
        if not callable(create):
            raise RuntimeError("planning hot pool lane client does not support chat completions")
        create(
            model=self._warm_model,
            messages=[{"role": "user", "content": "ping"}],
            temperature=0.0,
            max_tokens=1,
            timeout_seconds=self._warm_timeout_seconds,
        )

    def mark_degraded(self, lane_id: int, error_summary: str) -> None:
        with self._lock:
            lane = self._lanes[lane_id]
            self._mark_degraded_locked(lane, error_summary)

    def warm_lane(self, lane_id: int, *, reason: str) -> PatentPlanningHotLane:
        with self._lock:
            if self._closed:
                raise RuntimeError("planning hot pool is closed")
            lane = self._lanes[lane_id]
            if lane.in_flight != 0:
                return lane
            lane.state = "warming"
            lane.in_flight += 1
        try:
            if callable(self._warm_lane_fn):
                self._warm_lane_fn(
                    lane=lane,
                    timeout_seconds=self._warm_timeout_seconds,
                    reason=reason,
                )
            else:
                self._default_warm_lane(lane)
            with self._lock:
                self._mark_ready_locked(lane)
        except Exception as exc:
            with self._lock:
                self._mark_degraded_locked(lane, str(exc))
            if self._logger is not None:
                self._logger.warning("patent planning hot lane warm failed lane=%s reason=%s error=%s", lane_id, reason, exc)
        finally:
            with self._lock:
                if lane.in_flight > 0:
                    lane.in_flight -= 1
        return lane

    def _drain_lanes_when_idle(self) -> list[PatentPlanningHotLane]:
        while True:
            with self._lock:
                lanes = list(self._lanes)
                if all(lane.in_flight == 0 for lane in lanes):
                    self._lanes = []
                    return lanes
            time.sleep(0.01)

    def _bootstrap_warm(self) -> None:
        for lane_id in range(len(self._lanes)):
            if self._stop_event.is_set():
                return
            self.warm_lane(lane_id, reason="bootstrap")

    def _keepalive_loop(self) -> None:
        self._bootstrap_warm()
        while not self._stop_event.is_set():
            now = self._now()
            if not self._is_warm_window_active(now):
                next_keepalive = self._next_window_start(now)
                self._next_keepalive_at = next_keepalive.isoformat(timespec="seconds")
                wait_seconds = max(0.0, (next_keepalive - now).total_seconds())
                if self._stop_event.wait(wait_seconds):
                    return
                continue
            sleep_seconds = self._warm_interval_seconds + self._cycle_jitter_seconds()
            next_keepalive = now + timedelta(seconds=max(0.0, sleep_seconds))
            self._next_keepalive_at = next_keepalive.isoformat(timespec="seconds")
            if self._stop_event.wait(max(0.0, sleep_seconds)):
                return
            with self._lock:
                self._refresh_stale_lanes_locked()
            for lane_id in range(len(self._lanes)):
                if self._stop_event.is_set():
                    return
                self.warm_lane(lane_id, reason="keepalive")

    def start(self) -> None:
        if self._closed or not self._warmup_enabled or not self._lanes:
            return
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return
        self._stop_event.clear()
        self._scheduler_thread = Thread(
            target=self._keepalive_loop,
            name="patent-planning-hot-pool-warmup",
            daemon=True,
        )
        self._scheduler_thread.start()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_stale_lanes_locked()
            total_lanes = len(self._lanes)
            ready_lanes = sum(1 for lane in self._lanes if lane.state == "ready")
            warming_lanes = sum(1 for lane in self._lanes if lane.state == "warming")
            degraded_lanes = sum(1 for lane in self._lanes if lane.state == "degraded")
            busy_lanes = sum(1 for lane in self._lanes if lane.in_flight > 0)
        return {
            "enabled": self.enabled,
            "total_lanes": total_lanes,
            "ready_lanes": ready_lanes,
            "warming_lanes": warming_lanes,
            "degraded_lanes": degraded_lanes,
            "busy_lanes": busy_lanes,
            "warmup_enabled": self._warmup_enabled,
            "scheduler_running": bool(self._scheduler_thread is not None and self._scheduler_thread.is_alive()),
            "last_any_warm_success_at": self._last_any_warm_success_at,
            "last_any_error_at": self._last_any_error_at,
            "last_error_summary": self._last_error_summary,
            "next_keepalive_at": self._next_keepalive_at,
        }

    @contextmanager
    def acquire(self) -> Iterator[Any]:
        lane: PatentPlanningHotLane | None = None
        with self._lock:
            if not self.enabled or self._closed:
                raise LookupError("planning hot pool unavailable")
            ready_lanes = [item for item in self._lanes if item.state == "ready"]
            if not ready_lanes:
                raise LookupError("planning hot pool has no ready lanes")
            lane = ready_lanes[self._next_lane % len(ready_lanes)]
            self._next_lane = (self._next_lane + 1) % len(ready_lanes)
            lane.in_flight += 1
        try:
            yield lane.client
        finally:
            with self._lock:
                if lane is not None and lane.in_flight > 0:
                    lane.in_flight -= 1

    def proxy_client(self, *, fallback_client: Any | None = None) -> Any:
        return _PlanningHotPoolProxy(pool=self, fallback_client=fallback_client)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop_event.set()
        thread = self._scheduler_thread
        if thread is not None and thread.is_alive():
            thread.join()
        self._scheduler_thread = None
        lanes = self._drain_lanes_when_idle()
        for lane in reversed(lanes):
            self._close_lane(lane)
