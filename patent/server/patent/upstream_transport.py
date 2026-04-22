from __future__ import annotations

import os
import time
from typing import Any

import httpx


def _shared_pool_from_client(http_client: Any | None) -> Any | None:
    if http_client is None:
        return None
    return getattr(http_client, "_patent_shared_pool", None)


def build_patent_request_timeout(
    *,
    http_client: Any | None,
    timeout_seconds: float,
    stream: bool = False,
    override_client_config: bool = False,
) -> httpx.Timeout:
    fallback_timeout_seconds = max(0.001, float(timeout_seconds))
    shared_pool = _shared_pool_from_client(http_client)
    config = getattr(shared_pool, "config", None)
    if config is not None and not override_client_config:
        read_timeout_seconds = getattr(
            config,
            "stream_read_timeout_seconds" if stream else "read_timeout_seconds",
            fallback_timeout_seconds,
        )
        return httpx.Timeout(
            connect=float(getattr(config, "connect_timeout_seconds", fallback_timeout_seconds)),
            read=float(read_timeout_seconds),
            write=float(getattr(config, "write_timeout_seconds", fallback_timeout_seconds)),
            pool=float(getattr(config, "pool_timeout_seconds", fallback_timeout_seconds)),
        )
    return httpx.Timeout(
        connect=fallback_timeout_seconds,
        read=fallback_timeout_seconds,
        write=fallback_timeout_seconds,
        pool=fallback_timeout_seconds,
    )


def describe_patent_transport(
    *,
    http_client: Any | None,
    owns_http_client: bool,
) -> dict[str, Any]:
    shared_pool = _shared_pool_from_client(http_client)
    if shared_pool is not None:
        snapshot = dict(getattr(shared_pool, "snapshot", lambda: {})() or {})
        snapshot.setdefault("pool_owner", "app")
        snapshot.setdefault("client_owner", "shared")
        snapshot.setdefault("shared_client_id", f"{id(http_client):x}" if http_client is not None else "")
        snapshot.setdefault("pid", os.getpid())
        snapshot.setdefault("bootstrap_source", "startup")
        snapshot.setdefault("pool_timeout_count", 0)
        snapshot.setdefault("pool_wait_ms", 0.0)
        return snapshot
    client_owner = "private" if owns_http_client else "shared"
    pool_owner = "client" if owns_http_client else "external"
    bootstrap_source = "private_client" if owns_http_client else "injected_client"
    return {
        "pool_owner": pool_owner,
        "client_owner": client_owner,
        "shared_client_id": f"{id(http_client):x}" if http_client is not None else "",
        "pid": os.getpid(),
        "bootstrap_source": bootstrap_source,
        "pool_timeout_count": 0,
        "pool_wait_ms": 0.0,
    }


def record_patent_pool_wait(*, http_client: Any | None, wait_ms: float) -> None:
    shared_pool = _shared_pool_from_client(http_client)
    record = getattr(shared_pool, "record_pool_wait", None)
    if callable(record):
        record(wait_ms=max(0.0, float(wait_ms or 0.0)))


def record_patent_pool_timeout(*, http_client: Any | None, wait_ms: float) -> None:
    shared_pool = _shared_pool_from_client(http_client)
    record = getattr(shared_pool, "record_pool_timeout", None)
    if callable(record):
        record(wait_ms=max(0.0, float(wait_ms or 0.0)))


def is_patent_pool_timeout(exc: Exception) -> bool:
    pool_timeout_cls = getattr(httpx, "PoolTimeout", None)
    if pool_timeout_cls is not None and isinstance(exc, pool_timeout_cls):
        return True
    return exc.__class__.__name__ == "PoolTimeout"


def record_patent_dispatch_success(*, http_client: Any | None, started_at: float) -> float:
    wait_ms = (time.perf_counter() - started_at) * 1000.0
    record_patent_pool_wait(http_client=http_client, wait_ms=wait_ms)
    return wait_ms


def record_patent_dispatch_error(*, http_client: Any | None, started_at: float, exc: Exception) -> None:
    if not is_patent_pool_timeout(exc):
        return
    record_patent_pool_timeout(
        http_client=http_client,
        wait_ms=(time.perf_counter() - started_at) * 1000.0,
    )
