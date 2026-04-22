from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from threading import Event, Lock, Thread
import time
from typing import Any, Callable, Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class RerankSessionLane:
    lane_id: int
    session: Any
    state: str = "cold"
    last_warm_success_at: str = ""
    last_error_at: str = ""
    last_error_summary: str = ""
    in_flight: int = 0
    consecutive_failures: int = 0
    last_warm_success_monotonic: float = 0.0


class RerankSessionPool:
    def __init__(
        self,
        *,
        lane_count: int,
        session_factory: Callable[[], Any] | None = None,
        logger: Any | None = None,
        warmup_enabled: bool = False,
        warm_interval_seconds: float = 300.0,
        warm_timeout_seconds: float = 420.0,
        warm_jitter_seconds: float = 60.0,
        lane_degraded_after_seconds: float = 900.0,
        warm_active_start_hour: int = 0,
        warm_active_end_hour: int = 24,
        bootstrap_warm_max_parallel: int = 1,
        bootstrap_warm_jitter_seconds: float = 30.0,
        warm_lane_fn: Callable[..., Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if session_factory is None:
            import requests

            session_factory = requests.Session

        self._logger = logger
        self._session_factory = session_factory
        self._lock = Lock()
        self._next_index = 0
        self._closed = False
        self._last_error_summary = ""
        self._last_any_warm_success_at = ""
        self._last_any_error_at = ""
        self._next_keepalive_at = ""
        self._warmup_enabled = bool(warmup_enabled)
        self._warm_interval_seconds = max(1.0, float(warm_interval_seconds or 0.0))
        self._warm_timeout_seconds = max(1.0, float(warm_timeout_seconds or 0.0))
        self._warm_jitter_seconds = max(0.0, float(warm_jitter_seconds or 0.0))
        self._lane_degraded_after_seconds = max(1.0, float(lane_degraded_after_seconds or 0.0))
        self._warm_active_start_hour = max(0, min(23, int(warm_active_start_hour or 0)))
        self._warm_active_end_hour = max(1, min(24, int(warm_active_end_hour or 24)))
        self._bootstrap_warm_max_parallel = max(1, int(bootstrap_warm_max_parallel or 1))
        self._bootstrap_warm_jitter_seconds = max(0.0, float(bootstrap_warm_jitter_seconds or 0.0))
        self._warm_lane_fn = warm_lane_fn
        self._now_fn = now_fn
        self._cycle_index = 0
        self._stop_event = Event()
        self._scheduler_thread: Thread | None = None
        self._lanes: list[RerankSessionLane] = [
            RerankSessionLane(lane_id=lane_id, session=self._session_factory())
            for lane_id in range(max(0, int(lane_count)))
        ]
        if self._warmup_enabled and self._lanes:
            self.start()

    @property
    def total_lanes(self) -> int:
        return len(self._lanes)

    @staticmethod
    def _lane_jitter_seconds(*, lane_id: int, total_lanes: int, jitter_seconds: float) -> float:
        if jitter_seconds <= 0.0 or total_lanes <= 1:
            return 0.0
        return float(lane_id) * (float(jitter_seconds) / float(max(total_lanes - 1, 1)))

    def _cycle_jitter_seconds(self) -> float:
        if self._warm_jitter_seconds <= 0.0:
            return 0.0
        self._cycle_index += 1
        bucket = (os.getpid() + self._cycle_index) % 7
        return self._warm_jitter_seconds * (float(bucket) / 6.0)

    def _now(self) -> datetime:
        current = self._now_fn() if callable(self._now_fn) else datetime.now(timezone.utc).astimezone()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone()

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

    def _next_keepalive_target(self, *, now: datetime, sleep_seconds: float) -> datetime:
        candidate = now + timedelta(seconds=max(0.0, float(sleep_seconds or 0.0)))
        if self._is_warm_window_active(candidate):
            return candidate
        return self._next_window_start(candidate)

    def _mark_ready_locked(self, lane: RerankSessionLane) -> None:
        lane.state = "ready"
        lane.last_warm_success_at = _now_iso()
        lane.last_warm_success_monotonic = time.monotonic()
        lane.last_error_at = ""
        lane.last_error_summary = ""
        lane.consecutive_failures = 0
        self._last_any_warm_success_at = lane.last_warm_success_at

    def mark_ready(self, lane_id: int) -> None:
        with self._lock:
            self._mark_ready_locked(self._lanes[lane_id])

    def _mark_degraded_locked(self, lane: RerankSessionLane, error_summary: str) -> None:
        lane.state = "degraded"
        lane.last_error_at = _now_iso()
        lane.last_error_summary = str(error_summary or "")
        lane.consecutive_failures += 1
        self._last_error_summary = lane.last_error_summary
        self._last_any_error_at = lane.last_error_at

    def mark_degraded(self, lane_id: int, error_summary: str) -> None:
        with self._lock:
            self._mark_degraded_locked(self._lanes[lane_id], error_summary)

    def abort_lane(self, lane_id: int, *, error_summary: str = "cancelled") -> None:
        with self._lock:
            lane = self._lanes[lane_id]
            close = getattr(lane.session, "close", None)
            if callable(close):
                close()
            lane.session = self._session_factory()
            self._mark_degraded_locked(lane, error_summary)

    def warm_lane(self, lane_id: int, *, reason: str = "manual") -> RerankSessionLane:
        lane = self._lanes[lane_id]
        with self._lock:
            if self._closed or lane.in_flight != 0 or lane.state == "warming":
                return lane
            lane.state = "warming"
        try:
            if callable(self._warm_lane_fn):
                self._warm_lane_fn(
                    lane=lane,
                    timeout_seconds=self._warm_timeout_seconds,
                    reason=reason,
                )
            with self._lock:
                self._mark_ready_locked(lane)
            if self._logger is not None:
                self._logger.info("stage2 rerank lane warm success lane=%s reason=%s", lane_id, reason)
        except Exception as exc:
            with self._lock:
                self._mark_degraded_locked(lane, str(exc))
            if self._logger is not None:
                self._logger.warning("stage2 rerank lane warm failed lane=%s reason=%s error=%s", lane_id, reason, exc)
        return lane

    def _refresh_stale_lanes_locked(self) -> None:
        now = time.monotonic()
        for lane in self._lanes:
            if lane.state != "ready" or lane.in_flight != 0 or lane.last_warm_success_monotonic <= 0.0:
                continue
            if now - lane.last_warm_success_monotonic <= self._lane_degraded_after_seconds:
                continue
            self._mark_degraded_locked(lane, "warm_expired")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_stale_lanes_locked()
            ready_lanes = sum(1 for lane in self._lanes if lane.state == "ready")
            warming_lanes = sum(1 for lane in self._lanes if lane.state == "warming")
            degraded_lanes = sum(1 for lane in self._lanes if lane.state == "degraded")
            busy_lanes = sum(1 for lane in self._lanes if lane.in_flight > 0)
            return {
                "total_lanes": len(self._lanes),
                "ready_lanes": ready_lanes,
                "warming_lanes": warming_lanes,
                "degraded_lanes": degraded_lanes,
                "busy_lanes": busy_lanes,
                "last_any_warm_success_at": self._last_any_warm_success_at,
                "last_any_error_at": self._last_any_error_at,
                "last_error_summary": self._last_error_summary,
                "next_keepalive_at": self._next_keepalive_at,
                "closed": self._closed,
            }

    def _bootstrap_warm_loop(self) -> None:
        max_workers = max(1, self._bootstrap_warm_max_parallel)
        active_threads: list[Thread] = []

        def _spawn(lane_id: int) -> Thread:
            thread = Thread(
                target=self._bootstrap_warm_lane,
                args=(lane_id,),
                name=f"stage2-rerank-hot-lane-{lane_id}",
                daemon=True,
            )
            thread.start()
            return thread

        for lane_id in range(len(self._lanes)):
            if self._stop_event.is_set():
                break
            active_threads = [thread for thread in active_threads if thread.is_alive()]
            while len(active_threads) >= max_workers and not self._stop_event.is_set():
                active_threads[0].join(timeout=0.1)
                active_threads = [thread for thread in active_threads if thread.is_alive()]
            active_threads.append(_spawn(lane_id))
        for thread in active_threads:
            thread.join()

    def _bootstrap_warm_lane(self, lane_id: int) -> None:
        delay_seconds = self._lane_jitter_seconds(
            lane_id=lane_id,
            total_lanes=len(self._lanes),
            jitter_seconds=self._bootstrap_warm_jitter_seconds,
        )
        if delay_seconds > 0.0 and self._stop_event.wait(delay_seconds):
            return
        self.warm_lane(lane_id, reason="bootstrap")

    def _run_scheduler(self) -> None:
        if self._logger is not None:
            self._logger.info(
                "stage2 rerank hot pool bootstrap started lanes=%s warm_interval_seconds=%s",
                len(self._lanes),
                self._warm_interval_seconds,
            )
        self._bootstrap_warm_loop()
        while not self._stop_event.is_set():
            now = self._now()
            sleep_seconds = self._warm_interval_seconds + self._cycle_jitter_seconds()
            next_keepalive = self._next_keepalive_target(now=now, sleep_seconds=sleep_seconds)
            self._next_keepalive_at = next_keepalive.isoformat(timespec="seconds")
            wait_seconds = max(0.0, (next_keepalive - now).total_seconds())
            if wait_seconds > 0.0 and self._stop_event.wait(wait_seconds):
                break
            if not self._is_warm_window_active(self._now()):
                continue
            with self._lock:
                self._refresh_stale_lanes_locked()
                idle_lane_ids = [lane.lane_id for lane in self._lanes if lane.in_flight == 0]
            for lane_id in idle_lane_ids:
                if self._stop_event.is_set():
                    return
                self.warm_lane(lane_id, reason="keepalive")

    def start(self) -> None:
        if self._closed or not self._warmup_enabled or not self._lanes:
            return
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return
        self._scheduler_thread = Thread(
            target=self._run_scheduler,
            name="stage2-rerank-hot-pool",
            daemon=True,
        )
        self._scheduler_thread.start()

    @contextmanager
    def lease_lane(self, *, trace_label: str | None = None) -> Iterator[RerankSessionLane | None]:
        lane: RerankSessionLane | None = None
        with self._lock:
            if self._closed:
                yield None
                return
            total = len(self._lanes)
            for offset in range(total):
                candidate = self._lanes[(self._next_index + offset) % total]
                if candidate.state != "ready" or candidate.in_flight != 0:
                    continue
                candidate.in_flight = 1
                lane = candidate
                self._next_index = (candidate.lane_id + 1) % total if total else 0
                break
        try:
            _ = trace_label
            yield lane
        finally:
            if lane is None:
                return
            with self._lock:
                lane.in_flight = 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        thread = self._scheduler_thread
        if thread is not None and thread.is_alive():
            thread.join()
        for lane in self._lanes:
            close = getattr(lane.session, "close", None)
            if callable(close):
                close()


__all__ = ["RerankSessionLane", "RerankSessionPool"]
