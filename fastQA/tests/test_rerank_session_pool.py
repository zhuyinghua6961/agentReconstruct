from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event, Thread
import time

from app.integrations.llm.rerank_session_pool import RerankSessionPool


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _build_pool() -> RerankSessionPool:
    return RerankSessionPool(
        lane_count=3,
        session_factory=_FakeSession,
    )


def test_rerank_session_pool_builds_configured_lane_count():
    pool = _build_pool()

    assert pool.total_lanes == 3
    assert pool.snapshot()["total_lanes"] == 3


def test_rerank_session_pool_leases_ready_lane():
    pool = _build_pool()
    pool.mark_ready(0)

    with pool.lease_lane(trace_label="claim_1") as lane:
        assert lane is not None
        assert lane.session is not None
        assert lane.in_flight == 1
        assert pool.snapshot()["busy_lanes"] == 1

    assert pool.snapshot()["busy_lanes"] == 0


def test_rerank_session_pool_tracks_ready_and_degraded_state():
    pool = _build_pool()

    pool.mark_ready(0)
    pool.mark_degraded(1, "boom")
    snapshot = pool.snapshot()

    assert snapshot["ready_lanes"] == 1
    assert snapshot["degraded_lanes"] == 1
    assert snapshot["last_error_summary"] == "boom"


def test_rerank_session_pool_close_waits_for_warm_thread_before_closing_sessions():
    started = Event()
    release = Event()
    pool = RerankSessionPool(
        lane_count=1,
        session_factory=_FakeSession,
        warmup_enabled=True,
        bootstrap_warm_jitter_seconds=0.0,
        warm_lane_fn=lambda **kwargs: (started.set(), release.wait(1.0)),
    )

    assert started.wait(1.0) is True
    close_thread = Thread(target=pool.close)
    close_thread.start()
    time.sleep(0.05)

    assert pool._lanes[0].session.closed is False
    assert close_thread.is_alive() is True

    release.set()
    close_thread.join(1.0)

    assert close_thread.is_alive() is False
    assert pool._lanes[0].session.closed is True


def test_rerank_session_pool_defers_next_keepalive_to_next_active_window():
    now = datetime(2026, 4, 22, 20, 15, tzinfo=timezone(timedelta(hours=8)))
    pool = RerankSessionPool(
        lane_count=1,
        session_factory=_FakeSession,
        warmup_enabled=False,
        warm_interval_seconds=7200.0,
        warm_active_start_hour=8,
        warm_active_end_hour=18,
        now_fn=lambda: now,
    )

    scheduled = pool._next_keepalive_target(now=now, sleep_seconds=7200.0)

    assert scheduled.isoformat(timespec="seconds") == "2026-04-23T08:00:00+08:00"
