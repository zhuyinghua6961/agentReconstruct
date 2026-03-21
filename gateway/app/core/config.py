"""Environment-driven gateway settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


_DEFAULT_MODE_BACKEND_ENDPOINTS = {
    'fast': 'http://127.0.0.1:8008',
    'thinking': 'http://127.0.0.1:8009',
    'patent': 'http://127.0.0.1:8010',
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, '1' if default else '0')).strip().lower()
    return raw in {'1', 'true', 'yes', 'on'}


def _backend_config_warnings(*, fast: str, thinking: str, patent: str) -> tuple[str, ...]:
    warnings: list[str] = []
    current = {'fast': fast, 'thinking': thinking, 'patent': patent}
    for name, value in current.items():
        if value == _DEFAULT_MODE_BACKEND_ENDPOINTS[name]:
            warnings.append(f'{name}_backend_uses_default_placeholder')
    return tuple(warnings)


@dataclass(frozen=True)
class BackendEndpoints:
    public: str
    fast: str
    thinking: str
    patent: str


@dataclass(frozen=True)
class GatewaySettings:
    app_name: str
    environment: str
    debug: bool
    host: str
    port: int
    request_timeout_seconds: int
    sse_timeout_seconds: int
    conversation_file_provider: str
    endpoints: BackendEndpoints
    strict_backend_config: bool = False
    backend_config_warnings: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        debug = _env_bool("GATEWAY_DEBUG", False)
        fast_base_url = str(os.getenv("FAST_BACKEND_BASE_URL", "http://127.0.0.1:8008") or "http://127.0.0.1:8008").rstrip("/")
        public_base_url = str(os.getenv("PUBLIC_BACKEND_BASE_URL", "http://127.0.0.1:8102") or "http://127.0.0.1:8102").rstrip("/")
        thinking_base_url = str(os.getenv("THINKING_BACKEND_BASE_URL", "http://127.0.0.1:8009") or "http://127.0.0.1:8009").rstrip("/")
        patent_base_url = str(os.getenv("PATENT_BACKEND_BASE_URL", "http://127.0.0.1:8010") or "http://127.0.0.1:8010").rstrip("/")
        strict_backend_config = _env_bool("GATEWAY_STRICT_BACKEND_CONFIG", False)
        backend_warnings = _backend_config_warnings(
            fast=fast_base_url,
            thinking=thinking_base_url,
            patent=patent_base_url,
        )
        return cls(
            app_name=str(os.getenv("GATEWAY_APP_NAME", "multi-mode-gateway") or "multi-mode-gateway"),
            environment=str(os.getenv("GATEWAY_ENV", "dev") or "dev"),
            debug=debug,
            host=str(os.getenv("GATEWAY_HOST", "0.0.0.0") or "0.0.0.0"),
            port=int(str(os.getenv("GATEWAY_PORT", "8101") or "8101")),
            request_timeout_seconds=int(str(os.getenv("GATEWAY_REQUEST_TIMEOUT_SECONDS", "30") or "30")),
            sse_timeout_seconds=int(str(os.getenv("GATEWAY_SSE_TIMEOUT_SECONDS", "600") or "600")),
            conversation_file_provider=str(os.getenv("GATEWAY_CONVERSATION_FILE_PROVIDER", "noop") or "noop").strip().lower(),
            endpoints=BackendEndpoints(
                public=public_base_url,
                fast=fast_base_url,
                thinking=thinking_base_url,
                patent=patent_base_url,
            ),
            strict_backend_config=strict_backend_config,
            backend_config_warnings=backend_warnings,
        )
