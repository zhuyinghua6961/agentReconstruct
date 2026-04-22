from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event, Thread
import time

from app.integrations.llm.hot_lane_pool import ChatHotLanePool


class _FakeHttpClient:
    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeHttpxModule:
    class Timeout:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)

    class Limits:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)

    def Client(self, **kwargs):
        return _FakeHttpClient(**kwargs)


def _build_pool() -> ChatHotLanePool:
    return ChatHotLanePool(
        lane_count=3,
        api_key="test-key",
        base_url="https://example.com/v1",
        connect_timeout_seconds=15.0,
        read_timeout_seconds=180.0,
        write_timeout_seconds=180.0,
        pool_timeout_seconds=30.0,
        keepalive_expiry_seconds=1800.0,
        logger=None,
        httpx_module=_FakeHttpxModule(),
        client_builder=lambda **kwargs: {"lane_client": kwargs["http_client"]},
    )


def test_chat_hot_lane_pool_builds_configured_lane_count():
    pool = _build_pool()

    assert pool.total_lanes == 3
    assert pool.snapshot()["total_lanes"] == 3


def test_chat_hot_lane_pool_lease_is_exclusive():
    pool = _build_pool()
    pool.mark_ready(0)

    with pool.lease_lane(trace_label="claim_1") as lane:
        assert lane is not None
        assert lane.in_flight == 1
        assert lane.client is not None
        assert pool.snapshot()["busy_lanes"] == 1

    assert pool.snapshot()["busy_lanes"] == 0
    assert pool.snapshot()["ready_lanes"] == 1


def test_chat_hot_lane_pool_tracks_ready_and_degraded_state():
    pool = _build_pool()

    pool.mark_ready(0)
    pool.mark_degraded(1, "boom")
    snapshot = pool.snapshot()

    assert snapshot["ready_lanes"] == 1
    assert snapshot["degraded_lanes"] == 1
    assert snapshot["last_error_summary"] == "boom"


def test_chat_hot_lane_pool_close_waits_for_warm_thread_before_closing_clients():
    started = Event()
    release = Event()
    pool = ChatHotLanePool(
        lane_count=1,
        api_key="test-key",
        base_url="https://example.com/v1",
        connect_timeout_seconds=15.0,
        read_timeout_seconds=180.0,
        write_timeout_seconds=180.0,
        pool_timeout_seconds=30.0,
        keepalive_expiry_seconds=1800.0,
        logger=None,
        httpx_module=_FakeHttpxModule(),
        client_builder=lambda **kwargs: {"lane_client": kwargs["http_client"]},
        warmup_enabled=True,
        bootstrap_warm_jitter_seconds=0.0,
        warm_lane_fn=lambda **kwargs: (started.set(), release.wait(1.0)),
    )

    assert started.wait(1.0) is True
    close_thread = Thread(target=pool.close)
    close_thread.start()
    time.sleep(0.05)

    assert pool._lanes[0].http_client.closed is False
    assert close_thread.is_alive() is True

    release.set()
    close_thread.join(1.0)

    assert close_thread.is_alive() is False
    assert pool._lanes[0].http_client.closed is True


def test_chat_hot_lane_pool_defers_next_keepalive_to_next_active_window():
    now = datetime(2026, 4, 22, 20, 15, tzinfo=timezone(timedelta(hours=8)))
    pool = ChatHotLanePool(
        lane_count=1,
        api_key="test-key",
        base_url="https://example.com/v1",
        connect_timeout_seconds=15.0,
        read_timeout_seconds=180.0,
        write_timeout_seconds=180.0,
        pool_timeout_seconds=30.0,
        keepalive_expiry_seconds=1800.0,
        logger=None,
        httpx_module=_FakeHttpxModule(),
        client_builder=lambda **kwargs: {"lane_client": kwargs["http_client"]},
        warmup_enabled=False,
        warm_interval_seconds=7200.0,
        warm_active_start_hour=8,
        warm_active_end_hour=18,
        now_fn=lambda: now,
    )

    scheduled = pool._next_keepalive_target(now=now, sleep_seconds=7200.0)

    assert scheduled.isoformat(timespec="seconds") == "2026-04-23T08:00:00+08:00"


def test_chat_hot_lane_pool_bootstrap_warms_even_outside_active_window():
    started = Event()
    release = Event()
    reasons: list[str] = []
    now = datetime(2026, 4, 22, 21, 0, tzinfo=timezone(timedelta(hours=8)))
    pool = ChatHotLanePool(
        lane_count=1,
        api_key="test-key",
        base_url="https://example.com/v1",
        connect_timeout_seconds=15.0,
        read_timeout_seconds=180.0,
        write_timeout_seconds=180.0,
        pool_timeout_seconds=30.0,
        keepalive_expiry_seconds=1800.0,
        logger=None,
        httpx_module=_FakeHttpxModule(),
        client_builder=lambda **kwargs: {"lane_client": kwargs["http_client"]},
        warmup_enabled=True,
        warm_interval_seconds=7200.0,
        bootstrap_warm_jitter_seconds=0.0,
        warm_active_start_hour=8,
        warm_active_end_hour=18,
        now_fn=lambda: now,
        warm_lane_fn=lambda **kwargs: (reasons.append(str(kwargs["reason"])), started.set(), release.wait(1.0)),
    )

    assert started.wait(1.0) is True
    assert reasons == ["bootstrap"]

    release.set()
    pool.close()
