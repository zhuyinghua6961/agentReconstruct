from __future__ import annotations

import logging
import os

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


class PatentSharedUpstreamHttpProvider:
    def __init__(
        self,
        *,
        enabled: bool,
        keepalive_expiry_seconds: float,
        max_keepalive_connections: int,
        max_connections: int,
    ) -> None:
        self.enabled = bool(enabled)
        self.keepalive_expiry_seconds = float(keepalive_expiry_seconds)
        self.max_keepalive_connections = max(1, int(max_keepalive_connections))
        self.max_connections = max(1, int(max_connections))
        self._client: httpx.Client | None = None
        self._closed = False
        if self.enabled:
            self._client = httpx.Client(
                limits=httpx.Limits(
                    max_keepalive_connections=self.max_keepalive_connections,
                    max_connections=self.max_connections,
                    keepalive_expiry=self.keepalive_expiry_seconds,
                )
            )
        _LOGGER.info(
            "Patent shared upstream http provider initialized enabled=%s keepalive_expiry_seconds=%s max_keepalive_connections=%s max_connections=%s client_id=%s",
            self.enabled,
            self.keepalive_expiry_seconds,
            self.max_keepalive_connections,
            self.max_connections,
            hex(id(self._client)) if self._client is not None else "-",
        )

    @classmethod
    def from_env(cls) -> "PatentSharedUpstreamHttpProvider":
        return cls(
            enabled=_env_flag("PATENT_LLM_HTTP_SHARED_POOL_ENABLED", default=False),
            keepalive_expiry_seconds=_env_float("PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS", 120.0),
            max_keepalive_connections=_env_int("PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS", 20),
            max_connections=_env_int("PATENT_LLM_HTTP_MAX_CONNECTIONS", 100),
        )

    def client(self) -> httpx.Client | None:
        if self._closed:
            return None
        return self._client

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            self._client.close()
            self._client = None
