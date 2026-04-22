from __future__ import annotations

from types import SimpleNamespace

import httpx

from server.patent.upstream_transport import (
    build_patent_request_timeout,
    describe_patent_transport,
    record_patent_pool_timeout,
    record_patent_pool_wait,
)


def test_build_request_timeout_preserves_connect_read_write_and_pool_dimensions():
    shared_pool = SimpleNamespace(
        config=SimpleNamespace(
            connect_timeout_seconds=1.5,
            read_timeout_seconds=2.5,
            stream_read_timeout_seconds=9.5,
            write_timeout_seconds=3.5,
            pool_timeout_seconds=4.5,
        )
    )
    shared_client = SimpleNamespace(_patent_shared_pool=shared_pool)

    request_timeout = build_patent_request_timeout(
        http_client=shared_client,
        timeout_seconds=17.0,
    )
    stream_timeout = build_patent_request_timeout(
        http_client=shared_client,
        timeout_seconds=17.0,
        stream=True,
    )
    private_timeout = build_patent_request_timeout(
        http_client=SimpleNamespace(),
        timeout_seconds=11.0,
    )

    assert isinstance(request_timeout, httpx.Timeout)
    assert request_timeout.connect == 1.5
    assert request_timeout.read == 2.5
    assert request_timeout.write == 3.5
    assert request_timeout.pool == 4.5

    assert isinstance(stream_timeout, httpx.Timeout)
    assert stream_timeout.connect == 1.5
    assert stream_timeout.read == 9.5
    assert stream_timeout.write == 3.5
    assert stream_timeout.pool == 4.5

    assert isinstance(private_timeout, httpx.Timeout)
    assert private_timeout.connect == 11.0
    assert private_timeout.read == 11.0
    assert private_timeout.write == 11.0
    assert private_timeout.pool == 11.0


def test_build_request_timeout_can_override_shared_pool_dimensions_for_per_call_budget():
    shared_pool = SimpleNamespace(
        config=SimpleNamespace(
            connect_timeout_seconds=1.5,
            read_timeout_seconds=2.5,
            stream_read_timeout_seconds=9.5,
            write_timeout_seconds=3.5,
            pool_timeout_seconds=4.5,
        )
    )
    shared_client = SimpleNamespace(_patent_shared_pool=shared_pool)

    request_timeout = build_patent_request_timeout(
        http_client=shared_client,
        timeout_seconds=5.0,
        override_client_config=True,
    )

    assert isinstance(request_timeout, httpx.Timeout)
    assert request_timeout.connect == 5.0
    assert request_timeout.read == 5.0
    assert request_timeout.write == 5.0
    assert request_timeout.pool == 5.0


def test_transport_helper_records_pool_metrics_against_shared_provider():
    class _FakeSharedPool:
        def __init__(self) -> None:
            self.wait_calls: list[float] = []
            self.timeout_calls: list[float] = []

        def record_pool_wait(self, *, wait_ms: float) -> None:
            self.wait_calls.append(wait_ms)

        def record_pool_timeout(self, *, wait_ms: float) -> None:
            self.timeout_calls.append(wait_ms)

    shared_pool = _FakeSharedPool()
    shared_client = SimpleNamespace(_patent_shared_pool=shared_pool)

    record_patent_pool_wait(http_client=shared_client, wait_ms=12.5)
    record_patent_pool_timeout(http_client=shared_client, wait_ms=21.0)

    assert shared_pool.wait_calls == [12.5]
    assert shared_pool.timeout_calls == [21.0]


def test_transport_helper_reports_shared_vs_private_client_ownership():
    shared_pool = SimpleNamespace(
        snapshot=lambda: {
            "pool_owner": "app",
            "client_owner": "shared",
            "shared_client_id": "shared-123",
            "pid": 42,
            "bootstrap_source": "startup",
            "pool_timeout_count": 1,
            "pool_wait_ms": 18.0,
        }
    )
    shared_client = SimpleNamespace(_patent_shared_pool=shared_pool)
    private_client = SimpleNamespace()
    injected_client = SimpleNamespace()

    shared = describe_patent_transport(http_client=shared_client, owns_http_client=False)
    private = describe_patent_transport(http_client=private_client, owns_http_client=True)
    injected = describe_patent_transport(http_client=injected_client, owns_http_client=False)

    assert shared["pool_owner"] == "app"
    assert shared["client_owner"] == "shared"
    assert shared["shared_client_id"] == "shared-123"
    assert shared["pool_timeout_count"] == 1
    assert shared["pool_wait_ms"] == 18.0

    assert private["pool_owner"] == "client"
    assert private["client_owner"] == "private"
    assert private["shared_client_id"]
    assert private["bootstrap_source"] == "private_client"

    assert injected["pool_owner"] == "external"
    assert injected["client_owner"] == "shared"
    assert injected["shared_client_id"]
    assert injected["bootstrap_source"] == "injected_client"
