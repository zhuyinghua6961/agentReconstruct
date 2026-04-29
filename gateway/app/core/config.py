"""Environment-driven gateway settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.core.env_loader import load_workspace_env


load_workspace_env(override_existing=False)


_DEFAULT_MODE_BACKEND_ENDPOINTS = {
    'fast': 'http://127.0.0.1:8008',
    'thinking': 'http://127.0.0.1:8009',
    'patent': 'http://127.0.0.1:8010',
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, '1' if default else '0')).strip().lower()
    return raw in {'1', 'true', 'yes', 'on'}


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


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
class RedisSettings:
    enabled: bool
    url: str
    host: str
    port: int
    username: str
    password: str
    db: int
    key_prefix: str
    socket_connect_timeout_seconds: int
    socket_timeout_seconds: int


@dataclass(frozen=True)
class AdmissionSettings:
    enabled: bool
    runtime_role: str
    dispatcher_enabled: bool
    control_api_token: str
    poll_interval_seconds: int
    max_concurrent: int
    fast_or_patent_max_concurrent: int
    thinking_max_concurrent: int
    per_user_max_active: int
    thinking_min_slots: int
    queue_max_size: int
    queued_ttl_seconds: int
    post_admit_attach_ttl_seconds: int

    @property
    def is_admission_worker(self) -> bool:
        return self.runtime_role == "admission_worker"


@dataclass(frozen=True)
class RouteClassifierSettings:
    enabled: bool
    provider: str
    high_confidence_threshold: float
    medium_confidence_threshold: float


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
    redis: RedisSettings
    admission: AdmissionSettings
    route_classifier: RouteClassifierSettings
    patent_file_routes_enabled: bool = True
    refresh_survivable_qa_tasks_enabled: bool = False
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
        redis_enabled = _env_bool("REDIS_ENABLED", False)
        gateway_runtime_role = str(os.getenv("GATEWAY_RUNTIME_ROLE", "web") or "web").strip().lower() or "web"
        admission_enabled = _env_bool("GATEWAY_ADMISSION_ENABLED", False)
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
            redis=RedisSettings(
                enabled=redis_enabled,
                url=str(os.getenv("REDIS_URL", "") or "").strip(),
                host=str(os.getenv("REDIS_HOST", "127.0.0.1") or "127.0.0.1").strip(),
                port=_env_int("REDIS_PORT", 6379),
                username=str(os.getenv("REDIS_USERNAME", "") or "").strip(),
                password=str(os.getenv("REDIS_PASSWORD", "") or "").strip(),
                db=_env_int("REDIS_DB", 0),
                key_prefix=str(os.getenv("REDIS_KEY_PREFIX", "gateway") or "gateway").strip() or "gateway",
                socket_connect_timeout_seconds=_env_int("REDIS_SOCKET_CONNECT_TIMEOUT_SEC", 2),
                socket_timeout_seconds=_env_int("REDIS_SOCKET_TIMEOUT_SEC", 2),
            ),
            admission=AdmissionSettings(
                enabled=admission_enabled,
                runtime_role=gateway_runtime_role,
                dispatcher_enabled=_env_bool("GATEWAY_ADMISSION_DISPATCHER_ENABLED", admission_enabled),
                control_api_token=str(os.getenv("GATEWAY_ADMISSION_CONTROL_TOKEN", "") or "").strip(),
                poll_interval_seconds=max(1, _env_int("GATEWAY_ADMISSION_POLL_INTERVAL_SECONDS", 5)),
                max_concurrent=max(1, _env_int("INTERACTIVE_EXECUTION_MAX_CONCURRENT", 20)),
                fast_or_patent_max_concurrent=max(1, _env_int("INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT", 20)),
                thinking_max_concurrent=max(1, _env_int("INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT", 5)),
                per_user_max_active=max(1, _env_int("INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE", 5)),
                thinking_min_slots=max(0, _env_int("INTERACTIVE_EXECUTION_THINKING_MIN_SLOTS", 1)),
                queue_max_size=max(1, _env_int("INTERACTIVE_QUEUE_MAX_SIZE", 200)),
                queued_ttl_seconds=max(60, _env_int("INTERACTIVE_QUEUED_TTL_SECONDS", 900)),
                post_admit_attach_ttl_seconds=max(60, _env_int("INTERACTIVE_POST_ADMIT_ATTACH_TTL_SECONDS", 600)),
            ),
            route_classifier=RouteClassifierSettings(
                enabled=_env_bool("GATEWAY_ROUTE_CLASSIFIER_ENABLED", False),
                provider=str(os.getenv("GATEWAY_ROUTE_CLASSIFIER_PROVIDER", "noop") or "noop").strip().lower(),
                high_confidence_threshold=float(str(os.getenv("GATEWAY_ROUTE_CLASSIFIER_HIGH_CONFIDENCE", "0.8") or "0.8")),
                medium_confidence_threshold=float(str(os.getenv("GATEWAY_ROUTE_CLASSIFIER_MEDIUM_CONFIDENCE", "0.6") or "0.6")),
            ),
            patent_file_routes_enabled=_env_bool("GATEWAY_PATENT_FILE_ROUTES_ENABLED", True),
            refresh_survivable_qa_tasks_enabled=_env_bool("GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED", False),
            strict_backend_config=strict_backend_config,
            backend_config_warnings=backend_warnings,
        )
