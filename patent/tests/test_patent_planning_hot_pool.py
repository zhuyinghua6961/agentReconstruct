from __future__ import annotations

from datetime import datetime
import threading
import time

import httpx

from server.patent.planning_hot_pool import PatentPlanningHotPool, PatentPlanningHotPoolConfig
from server.patent.runtime import PatentPlanningClient


class _FakeHttpxModule:
    class Timeout:
        def __init__(self, *, connect, read, write, pool) -> None:
            self.connect = connect
            self.read = read
            self.write = write
            self.pool = pool

    class Limits:
        def __init__(self, *, max_connections, max_keepalive_connections, keepalive_expiry) -> None:
            self.max_connections = max_connections
            self.max_keepalive_connections = max_keepalive_connections
            self.keepalive_expiry = keepalive_expiry

    class Client:
        def __init__(self, *, timeout=None, limits=None, http2=False) -> None:
            self.timeout = timeout
            self.limits = limits
            self.http2 = http2
            self.closed = False
            self.calls: list[dict[str, object]] = []
            self.requests: list[httpx.Request] = []

        def build_request(self, method, url, *, headers=None, json=None, timeout=None, extensions=None):
            request = httpx.Request(str(method), str(url), headers=headers, json=json)
            request.extensions.update(dict(extensions or {}))
            if timeout is not None:
                request.extensions["timeout"] = timeout.as_dict()
            self.requests.append(request)
            return request

        def send(self, request, *, stream=False):
            self.calls.append(
                {
                    "request": request,
                    "stream": stream,
                    "timeout": dict(request.extensions.get("timeout") or {}),
                }
            )
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "warm ok"}}]},
            )

        def close(self) -> None:
            self.closed = True


class _FakeLaneClient:
    def __init__(self, *, http_client) -> None:
        self.http_client = http_client
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeWarmableLaneClient(_FakeLaneClient):
    def __init__(self, *, http_client) -> None:
        super().__init__(http_client=http_client)
        self.calls: list[dict[str, object]] = []
        self.chat = type(
            "_Chat",
            (),
            {
                "completions": type(
                    "_Completions",
                    (),
                    {"create": self._create},
                )()
            },
        )()

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return object()


def _config(**overrides) -> PatentPlanningHotPoolConfig:
    payload = {
        "enabled": True,
        "lane_count": 2,
        "connect_timeout_seconds": 1.0,
        "read_timeout_seconds": 2.0,
        "stream_read_timeout_seconds": 2.5,
        "write_timeout_seconds": 3.0,
        "pool_timeout_seconds": 4.0,
        "keepalive_expiry_seconds": 5.0,
        "warmup_enabled": False,
        "warm_interval_seconds": 60.0,
        "warm_timeout_seconds": 5.0,
        "warm_jitter_seconds": 0.0,
        "lane_degraded_after_seconds": 600.0,
        "warm_active_start_hour": 0,
        "warm_active_end_hour": 24,
    }
    payload.update(overrides)
    return PatentPlanningHotPoolConfig(**payload)


def test_patent_planning_hot_pool_creates_lane_local_clients():
    built_http_clients: list[object] = []

    def _build_lane_client(*, http_client):
        built_http_clients.append(http_client)
        return _FakeLaneClient(http_client=http_client)

    pool = PatentPlanningHotPool(
        config=_config(lane_count=2),
        lane_client_builder=_build_lane_client,
        httpx_module=_FakeHttpxModule,
    )

    snapshot = pool.snapshot()

    assert len(built_http_clients) == 2
    assert built_http_clients[0] is not built_http_clients[1]
    assert snapshot["total_lanes"] == 2
    assert snapshot["ready_lanes"] == 2
    pool.close()


def test_patent_planning_hot_pool_snapshot_exposes_lane_counts():
    pool = PatentPlanningHotPool(
        config=_config(lane_count=3),
        lane_client_builder=lambda *, http_client: _FakeLaneClient(http_client=http_client),
        httpx_module=_FakeHttpxModule,
    )

    snapshot = pool.snapshot()

    assert snapshot["enabled"] is True
    assert snapshot["total_lanes"] == 3
    assert snapshot["ready_lanes"] == 3
    assert snapshot["warming_lanes"] == 0
    assert snapshot["degraded_lanes"] == 0
    pool.close()


def test_patent_planning_hot_pool_constructor_closes_built_lanes_on_failure():
    built_http_clients: list[object] = []
    built_lane_clients: list[_FakeLaneClient] = []

    def _build_lane_client(*, http_client):
        built_http_clients.append(http_client)
        if len(built_http_clients) == 2:
            raise RuntimeError("lane boom")
        client = _FakeLaneClient(http_client=http_client)
        built_lane_clients.append(client)
        return client

    try:
        PatentPlanningHotPool(
            config=_config(lane_count=2),
            lane_client_builder=_build_lane_client,
            httpx_module=_FakeHttpxModule,
        )
    except RuntimeError as exc:
        assert "lane boom" in str(exc)
    else:
        raise AssertionError("expected lane boom")

    assert len(built_lane_clients) == 1
    assert built_lane_clients[0].closed is True
    assert built_http_clients[0].closed is True
    assert built_http_clients[1].closed is True


def test_patent_planning_hot_pool_close_closes_lane_clients():
    built_http_clients: list[object] = []
    built_lane_clients: list[_FakeLaneClient] = []

    def _build_lane_client(*, http_client):
        built_http_clients.append(http_client)
        client = _FakeLaneClient(http_client=http_client)
        built_lane_clients.append(client)
        return client

    pool = PatentPlanningHotPool(
        config=_config(lane_count=2),
        lane_client_builder=_build_lane_client,
        httpx_module=_FakeHttpxModule,
    )

    pool.close()
    pool.close()

    assert all(client.closed is True for client in built_lane_clients)
    assert all(http_client.closed is True for http_client in built_http_clients)


def test_patent_planning_hot_pool_bootstrap_warm_starts_immediately():
    warmed = threading.Event()

    def _warm_lane(*, lane, timeout_seconds, reason):
        assert timeout_seconds == 5.0
        assert reason == "bootstrap"
        lane.last_error_summary = ""
        warmed.set()

    pool = PatentPlanningHotPool(
        config=_config(lane_count=1, warmup_enabled=True),
        lane_client_builder=lambda *, http_client: _FakeLaneClient(http_client=http_client),
        httpx_module=_FakeHttpxModule,
        warm_lane_fn=_warm_lane,
    )

    assert warmed.wait(timeout=1.0) is True
    assert pool.snapshot()["last_any_warm_success_at"] != ""
    pool.close()


def test_patent_planning_hot_pool_keepalive_warm_updates_snapshot_fields():
    keepalive_warmed = threading.Event()
    warm_reasons: list[str] = []

    def _warm_lane(*, lane, timeout_seconds, reason):
        del lane, timeout_seconds
        warm_reasons.append(reason)
        if warm_reasons.count("keepalive") >= 1:
            keepalive_warmed.set()

    pool = PatentPlanningHotPool(
        config=_config(lane_count=1, warmup_enabled=True, warm_interval_seconds=1.0),
        lane_client_builder=lambda *, http_client: _FakeLaneClient(http_client=http_client),
        httpx_module=_FakeHttpxModule,
        warm_lane_fn=_warm_lane,
    )

    assert keepalive_warmed.wait(timeout=2.0) is True
    snapshot = pool.snapshot()
    assert snapshot["last_any_warm_success_at"] != ""
    assert snapshot["next_keepalive_at"] != ""
    pool.close()


def test_patent_planning_hot_pool_warm_window_is_honored():
    warm_reasons: list[str] = []

    def _warm_lane(*, lane, timeout_seconds, reason):
        del lane, timeout_seconds
        warm_reasons.append(reason)

    pool = PatentPlanningHotPool(
        config=_config(
            lane_count=1,
            warmup_enabled=True,
            warm_interval_seconds=1.0,
            warm_active_start_hour=8,
            warm_active_end_hour=18,
        ),
        lane_client_builder=lambda *, http_client: _FakeLaneClient(http_client=http_client),
        httpx_module=_FakeHttpxModule,
        warm_lane_fn=_warm_lane,
        now_fn=lambda: datetime(2026, 4, 22, 3, 0, 0),
    )

    time.sleep(0.2)

    assert warm_reasons == ["bootstrap"]
    pool.close()


def test_patent_planning_hot_pool_keepalive_warm_refreshes_degraded_lane():
    keepalive_warmed = threading.Event()

    def _warm_lane(*, lane, timeout_seconds, reason):
        del timeout_seconds
        if reason == "keepalive":
            keepalive_warmed.set()
        lane.last_error_summary = ""

    pool = PatentPlanningHotPool(
        config=_config(lane_count=1, warmup_enabled=True, warm_interval_seconds=1.0),
        lane_client_builder=lambda *, http_client: _FakeLaneClient(http_client=http_client),
        httpx_module=_FakeHttpxModule,
        warm_lane_fn=_warm_lane,
    )

    pool.mark_degraded(0, "stale")

    assert keepalive_warmed.wait(timeout=2.0) is True
    assert pool.snapshot()["ready_lanes"] == 1
    pool.close()


def test_patent_planning_hot_pool_shutdown_stops_scheduler_cleanly():
    warm_count = 0

    def _warm_lane(*, lane, timeout_seconds, reason):
        nonlocal warm_count
        del lane, timeout_seconds, reason
        warm_count += 1

    pool = PatentPlanningHotPool(
        config=_config(lane_count=1, warmup_enabled=True, warm_interval_seconds=1.0),
        lane_client_builder=lambda *, http_client: _FakeLaneClient(http_client=http_client),
        httpx_module=_FakeHttpxModule,
        warm_lane_fn=_warm_lane,
    )

    time.sleep(0.2)
    pool.close()
    count_after_close = warm_count
    time.sleep(0.2)

    assert warm_count == count_after_close


def test_patent_planning_hot_pool_default_warm_lane_performs_real_request():
    built_lane_clients: list[_FakeWarmableLaneClient] = []

    def _build_lane_client(*, http_client):
        client = _FakeWarmableLaneClient(http_client=http_client)
        built_lane_clients.append(client)
        return client

    pool = PatentPlanningHotPool(
        config=_config(lane_count=1),
        lane_client_builder=_build_lane_client,
        httpx_module=_FakeHttpxModule,
        warm_model="planner-model",
    )

    pool.warm_lane(0, reason="bootstrap")

    assert len(built_lane_clients) == 1
    assert built_lane_clients[0].calls == [
        {
            "model": "planner-model",
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": 0.0,
            "max_tokens": 1,
            "timeout_seconds": 5.0,
        }
    ]
    pool.close()


def test_patent_planning_hot_pool_default_warm_lane_uses_warm_timeout_on_wire():
    built_http_clients: list[_FakeHttpxModule.Client] = []

    def _build_lane_client(*, http_client):
        built_http_clients.append(http_client)
        return PatentPlanningClient(
            api_key="test-key",
            base_url="http://example.invalid",
            timeout_seconds=17.0,
            http_client=http_client,
        )

    pool = PatentPlanningHotPool(
        config=_config(lane_count=1),
        lane_client_builder=_build_lane_client,
        httpx_module=_FakeHttpxModule,
        warm_model="planner-model",
    )

    pool.warm_lane(0, reason="bootstrap")

    assert len(built_http_clients) == 1
    assert len(built_http_clients[0].calls) == 1
    timeout = built_http_clients[0].calls[0]["timeout"]
    assert timeout == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}
    pool.close()


def test_patent_planning_hot_pool_close_waits_for_in_flight_warm_before_closing_resources():
    built_http_clients: list[object] = []
    built_lane_clients: list[_FakeLaneClient] = []
    warm_started = threading.Event()
    allow_finish = threading.Event()
    close_finished = threading.Event()

    def _build_lane_client(*, http_client):
        built_http_clients.append(http_client)
        client = _FakeLaneClient(http_client=http_client)
        built_lane_clients.append(client)
        return client

    def _warm_lane(*, lane, timeout_seconds, reason):
        del lane, timeout_seconds, reason
        warm_started.set()
        assert allow_finish.wait(timeout=1.0) is True

    pool = PatentPlanningHotPool(
        config=_config(lane_count=1),
        lane_client_builder=_build_lane_client,
        httpx_module=_FakeHttpxModule,
        warm_lane_fn=_warm_lane,
    )

    warmer = threading.Thread(target=lambda: pool.warm_lane(0, reason="manual"))
    warmer.start()
    assert warm_started.wait(timeout=1.0) is True

    def _close_pool() -> None:
        pool.close()
        close_finished.set()

    closer = threading.Thread(target=_close_pool)
    closer.start()
    time.sleep(0.1)

    assert close_finished.is_set() is False
    assert built_lane_clients[0].closed is False
    assert built_http_clients[0].closed is False

    allow_finish.set()
    warmer.join(timeout=1.0)
    closer.join(timeout=1.0)

    assert close_finished.is_set() is True
    assert built_lane_clients[0].closed is True
    assert built_http_clients[0].closed is True
