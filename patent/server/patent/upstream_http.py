from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from threading import Lock
from typing import Any

import httpx


_LOGGER = logging.getLogger("patent.upstream_http")


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
        value = int(raw)
    except Exception:
        return int(default)
    return value if value >= 1 else int(default)


@dataclass(frozen=True)
class PatentSharedUpstreamHttpConfig:
    enabled: bool
    connect_timeout_seconds: float
    read_timeout_seconds: float
    stream_read_timeout_seconds: float
    write_timeout_seconds: float
    pool_timeout_seconds: float
    keepalive_expiry_seconds: float
    max_keepalive_connections: int
    max_connections: int

    @classmethod
    def from_env(cls) -> "PatentSharedUpstreamHttpConfig":
        return cls(
            enabled=True,
            connect_timeout_seconds=_env_float("PATENT_LLM_HTTP_CONNECT_TIMEOUT_SECONDS", 15.0),
            read_timeout_seconds=_env_float("PATENT_LLM_HTTP_READ_TIMEOUT_SECONDS", 180.0),
            stream_read_timeout_seconds=_env_float("PATENT_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS", 600.0),
            write_timeout_seconds=_env_float("PATENT_LLM_HTTP_WRITE_TIMEOUT_SECONDS", 180.0),
            pool_timeout_seconds=_env_float("PATENT_LLM_HTTP_POOL_TIMEOUT_SECONDS", 30.0),
            keepalive_expiry_seconds=_env_float("PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", 120.0),
            max_keepalive_connections=_env_int("PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", 20),
            max_connections=_env_int("PATENT_LLM_HTTP_MAX_CONNECTIONS", 100),
        )

    @classmethod
    def from_settings(cls, settings: Any) -> "PatentSharedUpstreamHttpConfig":
        llm_http = getattr(settings, "llm_http", settings)
        return cls(
            enabled=True,
            connect_timeout_seconds=float(getattr(llm_http, "connect_timeout_seconds", 15.0)),
            read_timeout_seconds=float(getattr(llm_http, "read_timeout_seconds", 180.0)),
            stream_read_timeout_seconds=float(getattr(llm_http, "stream_read_timeout_seconds", 600.0)),
            write_timeout_seconds=float(getattr(llm_http, "write_timeout_seconds", 180.0)),
            pool_timeout_seconds=float(getattr(llm_http, "pool_timeout_seconds", 30.0)),
            keepalive_expiry_seconds=float(getattr(llm_http, "keepalive_expiry_seconds", 120.0)),
            max_keepalive_connections=int(getattr(llm_http, "max_keepalive_connections", 20)),
            max_connections=int(getattr(llm_http, "max_connections", 100)),
        )


class PatentSharedUpstreamHttpProvider:
    def __init__(
        self,
        *,
        config: PatentSharedUpstreamHttpConfig,
        bootstrap_source: str = "startup",
    ) -> None:
        self.config = config
        self.enabled = bool(config.enabled)
        self.connect_timeout_seconds = float(config.connect_timeout_seconds)
        self.read_timeout_seconds = float(config.read_timeout_seconds)
        self.stream_read_timeout_seconds = float(config.stream_read_timeout_seconds)
        self.write_timeout_seconds = float(config.write_timeout_seconds)
        self.pool_timeout_seconds = float(config.pool_timeout_seconds)
        self.keepalive_expiry_seconds = float(config.keepalive_expiry_seconds)
        self.max_keepalive_connections = max(1, int(config.max_keepalive_connections))
        self.max_connections = max(1, int(config.max_connections))
        self.bootstrap_source = str(bootstrap_source or "startup")
        self._client: httpx.Client | None = None
        self._closed = False
        self._pid = os.getpid()
        self._client_id = ""
        self._pool_timeout_count = 0
        self._last_pool_wait_ms = 0.0
        self._metrics_lock = Lock()
        if self.enabled:
            timeout = httpx.Timeout(
                connect=self.connect_timeout_seconds,
                read=self.read_timeout_seconds,
                write=self.write_timeout_seconds,
                pool=self.pool_timeout_seconds,
            )
            self._client = httpx.Client(
                timeout=timeout,
                limits=httpx.Limits(
                    max_keepalive_connections=self.max_keepalive_connections,
                    max_connections=self.max_connections,
                    keepalive_expiry=self.keepalive_expiry_seconds,
                )
            )
            self._client_id = f"{id(self._client):x}"
            try:
                setattr(self._client, "_patent_shared_pool", self)
            except Exception:
                pass
        _LOGGER.info(
            "Patent shared upstream http provider initialized enabled=%s keepalive_expiry_seconds=%s max_keepalive_connections=%s max_connections=%s client_id=%s bootstrap_source=%s",
            self.enabled,
            self.keepalive_expiry_seconds,
            self.max_keepalive_connections,
            self.max_connections,
            self._client_id or "-",
            self.bootstrap_source,
        )

    @classmethod
    def from_env(cls) -> "PatentSharedUpstreamHttpProvider":
        return cls(config=PatentSharedUpstreamHttpConfig.from_env())

    @classmethod
    def from_settings(cls, settings: Any) -> "PatentSharedUpstreamHttpProvider":
        return cls(config=PatentSharedUpstreamHttpConfig.from_settings(settings))

    def client(self) -> httpx.Client | None:
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
            "client_owner": "shared" if self.enabled else "disabled",
            "shared_client_id": self._client_id,
            "pid": self._pid,
            "bootstrap_source": self.bootstrap_source,
            "pool_timeout_count": self._pool_timeout_count,
            "pool_wait_ms": self._last_pool_wait_ms,
            "max_connections": self.max_connections,
            "max_keepalive_connections": self.max_keepalive_connections,
            "keepalive_expiry_seconds": self.keepalive_expiry_seconds,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            self._client.close()
            self._client = None
