from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        raw = str(os.getenv(name, "") or "").strip()
        if raw:
            return raw
    return default


def _env_float(*names: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    selected_name = None
    raw = str(default)
    for name in names:
        candidate = str(os.getenv(name, "") or "").strip()
        if candidate:
            selected_name = name
            raw = candidate
            break
    try:
        value = float(raw)
    except Exception:
        if selected_name is not None:
            logger.warning("invalid float env %s=%r; using default %s", selected_name, raw, default)
        value = float(default)
    original = value
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    if selected_name is not None and value != original:
        logger.warning(
            "out-of-range float env %s=%r; clamped to %s",
            selected_name,
            raw,
            value,
        )
    return value


def _env_int(*names: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    selected_name = None
    raw = str(default)
    for name in names:
        candidate = str(os.getenv(name, "") or "").strip()
        if candidate:
            selected_name = name
            raw = candidate
            break
    try:
        value = int(raw)
    except Exception:
        if selected_name is not None:
            logger.warning("invalid int env %s=%r; using default %s", selected_name, raw, default)
        value = int(default)
    original = value
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    if selected_name is not None and value != original:
        logger.warning(
            "out-of-range int env %s=%r; clamped to %s",
            selected_name,
            raw,
            value,
        )
    return value


@dataclass(frozen=True)
class SharedHttpPoolConfig:
    connect_timeout_seconds: float
    read_timeout_seconds: float
    stream_read_timeout_seconds: float
    write_timeout_seconds: float
    pool_timeout_seconds: float
    keepalive_expiry_seconds: float
    max_connections: int
    max_keepalive_connections: int

    @classmethod
    def from_env(cls) -> "SharedHttpPoolConfig":
        return cls(
            connect_timeout_seconds=_env_float(
                "FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS",
                "LLM_CONNECT_TIMEOUT_SECONDS",
                "OPENAI_CONNECT_TIMEOUT_SECONDS",
                "DASHSCOPE_CONNECT_TIMEOUT_SECONDS",
                default=15.0,
                minimum=1.0,
                maximum=300.0,
            ),
            read_timeout_seconds=_env_float(
                "FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS",
                "LLM_READ_TIMEOUT_SECONDS",
                "OPENAI_READ_TIMEOUT_SECONDS",
                "DASHSCOPE_READ_TIMEOUT_SECONDS",
                default=180.0,
                minimum=5.0,
                maximum=1800.0,
            ),
            stream_read_timeout_seconds=_env_float(
                "FASTQA_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS",
                "LLM_STREAM_READ_TIMEOUT_SECONDS",
                default=600.0,
                minimum=5.0,
                maximum=7200.0,
            ),
            write_timeout_seconds=_env_float(
                "FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS",
                "LLM_WRITE_TIMEOUT_SECONDS",
                "OPENAI_WRITE_TIMEOUT_SECONDS",
                "DASHSCOPE_WRITE_TIMEOUT_SECONDS",
                default=180.0,
                minimum=5.0,
                maximum=1800.0,
            ),
            pool_timeout_seconds=_env_float(
                "FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS",
                "LLM_POOL_TIMEOUT_SECONDS",
                "OPENAI_POOL_TIMEOUT_SECONDS",
                "DASHSCOPE_POOL_TIMEOUT_SECONDS",
                default=30.0,
                minimum=1.0,
                maximum=300.0,
            ),
            keepalive_expiry_seconds=_env_float(
                "FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS",
                "LLM_KEEPALIVE_EXPIRY_SECONDS",
                default=90.0,
                minimum=1.0,
                maximum=3600.0,
            ),
            max_connections=_env_int(
                "FASTQA_LLM_HTTP_MAX_CONNECTIONS",
                "LLM_MAX_CONNECTIONS",
                default=160,
                minimum=1,
                maximum=2048,
            ),
            max_keepalive_connections=_env_int(
                "FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS",
                "LLM_MAX_KEEPALIVE_CONNECTIONS",
                default=64,
                minimum=1,
                maximum=2048,
            ),
        )


class FastQASharedUpstreamHttpPool:
    def __init__(
        self,
        *,
        http_client: Any,
        config: SharedHttpPoolConfig,
        bootstrap_source: str = "startup",
    ) -> None:
        self._client = http_client
        self._config = config
        self._closed = False
        self._bootstrap_source = str(bootstrap_source or "startup")
        self._pid = os.getpid()
        self._client_id = f"{id(http_client):x}"
        self._pool_timeout_count = 0
        self._last_pool_wait_ms = 0.0
        self._metrics_lock = Lock()
        try:
            setattr(http_client, "_fastqa_shared_pool", self)
        except Exception:
            pass

    @classmethod
    def from_env(
        cls,
        *,
        httpx_module: Any | None = None,
        bootstrap_source: str = "startup",
    ) -> "FastQASharedUpstreamHttpPool":
        if httpx_module is None:
            import httpx as httpx_module

        config = SharedHttpPoolConfig.from_env()
        timeout = httpx_module.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.write_timeout_seconds,
            pool=config.pool_timeout_seconds,
        )
        limits = httpx_module.Limits(
            max_connections=config.max_connections,
            max_keepalive_connections=config.max_keepalive_connections,
            keepalive_expiry=config.keepalive_expiry_seconds,
        )
        client = httpx_module.Client(timeout=timeout, limits=limits, http2=False)
        return cls(http_client=client, config=config, bootstrap_source=bootstrap_source)

    @property
    def config(self) -> SharedHttpPoolConfig:
        return self._config

    @property
    def bootstrap_source(self) -> str:
        return self._bootstrap_source

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def pool_timeout_count(self) -> int:
        return self._pool_timeout_count

    @property
    def last_pool_wait_ms(self) -> float:
        return self._last_pool_wait_ms

    def client(self) -> Any | None:
        if self._closed:
            return None
        return self._client

    def record_pool_wait(self, *, wait_ms: float) -> None:
        with self._metrics_lock:
            self._last_pool_wait_ms = max(0.0, float(wait_ms or 0.0))

    def record_pool_timeout(self, *, wait_ms: float) -> None:
        with self._metrics_lock:
            self._pool_timeout_count += 1
            self._last_pool_wait_ms = max(0.0, float(wait_ms or 0.0))

    def snapshot(self) -> dict[str, Any]:
        return {
            "pool_owner": "app",
            "client_owner": "shared",
            "shared_client_id": self.client_id,
            "pid": self.pid,
            "bootstrap_source": self.bootstrap_source,
            "pool_timeout_count": self.pool_timeout_count,
            "pool_wait_ms": self.last_pool_wait_ms,
            "max_connections": self.config.max_connections,
            "max_keepalive_connections": self.config.max_keepalive_connections,
            "keepalive_expiry_seconds": self.config.keepalive_expiry_seconds,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
