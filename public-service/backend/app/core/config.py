from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from app.core.env_loader import SERVICE_DIR, load_env


load_env(override_existing=False)

_DEFAULT_DATA_ROOT = SERVICE_DIR / "data" / "runtime"
_CONVERSATION_AUTHORITY_TARGETS = frozenset({"legacy", "public_service", "shadow_public_service"})
_PRODUCTION_APP_ENVS = frozenset({"prod", "production"})


def _get_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _get_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _get_optional_conversation_target(name: str) -> str | None:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return None
    if raw not in _CONVERSATION_AUTHORITY_TARGETS:
        raise ValueError(f"unsupported {name}: {raw}")
    return raw


def _get_conversation_target(name: str, default: str) -> str:
    return _get_optional_conversation_target(name) or default


def _get_bool_from_names(*, names: tuple[str, ...], default: bool) -> bool:
    for name in names:
        raw = str(os.getenv(name, "") or "").strip()
        if raw:
            normalized = raw.lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
    return bool(default)


def _resolve_conversation_rollout(app_env: str) -> tuple[str, str, str, str, bool]:
    execution_key = "CONVERSATION_EXECUTION_AUTHORITY_TARGET"
    execution_target = _get_conversation_target(execution_key, "legacy")
    assistant_write_target = _get_conversation_target("CONVERSATION_ASSISTANT_WRITE_TARGET", "legacy")
    user_write_target = _get_optional_conversation_target("CONVERSATION_USER_WRITE_TARGET")
    context_read_target = _get_optional_conversation_target("CONVERSATION_CONTEXT_READ_TARGET")
    if user_write_target is not None or context_read_target is not None:
        if user_write_target != context_read_target:
            if str(app_env or "").strip().lower() in _PRODUCTION_APP_ENVS:
                raise ValueError("split authority rollout is not allowed in production")
        elif not str(os.getenv(execution_key, "") or "").strip():
            execution_target = user_write_target or context_read_target or execution_target
    overlay_enabled = _get_bool_from_names(
        names=("CONVERSATION_OVERLAY_ENABLED", "CONVERSATION_OVERLAY_READWRITE_ENABLED"),
        default=False,
    )
    return (
        execution_target,
        execution_target,
        execution_target,
        assistant_write_target,
        overlay_enabled,
    )


def _resolve_data_root() -> Path:
    raw = str(os.getenv("PUBLIC_SERVICE_DATA_ROOT", str(_DEFAULT_DATA_ROOT)) or str(_DEFAULT_DATA_ROOT)).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    return path


def _resolve_under_root(raw: str | None, *, data_root: Path, default: str) -> Path:
    value = str(raw or default).strip() or default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (data_root / path).resolve()
    else:
        path = path.resolve()
    return path


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    debug: bool
    host: str
    port: int
    api_prefix: str
    docs_url: str
    openapi_url: str
    cors_origins: list[str]
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    redis_enabled: bool
    redis_url: str | None
    redis_host: str
    redis_port: int
    redis_username: str | None
    redis_password: str
    redis_db: int
    redis_key_prefix: str
    redis_socket_connect_timeout_sec: int
    redis_socket_timeout_sec: int
    minio_endpoint: str | None
    minio_access_key: str | None
    minio_secret_key: str | None
    minio_bucket: str
    minio_secure: bool
    minio_region: str | None
    neo4j_url: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str
    data_root: Path
    uploads_dir: Path
    papers_dir: Path
    chat_json_base_dir: Path
    vector_db_path: Path
    translation_cache_dir: Path
    logs_dir: Path
    local_storage_root: Path
    conversation_execution_authority_target: str
    conversation_execution_user_write_target: str
    conversation_execution_context_read_target: str
    conversation_assistant_write_target: str
    conversation_overlay_enabled: bool
    conversation_legacy_fallback_enabled: bool
    personnel_department_strict_source_enabled: bool

    @property
    def mysql_dsn(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}@"
            f"{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @property
    def resolved_redis_url(self) -> str:
        explicit = str(self.redis_url or "").strip()
        if explicit:
            return explicit
        auth = ""
        username = str(self.redis_username or "").strip()
        password = str(self.redis_password or "")
        if username:
            auth = quote(username, safe="")
            if password:
                auth += f":{quote(password, safe='')}"
            auth += "@"
        elif password:
            auth = f":{quote(password, safe='')}@"
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cors_allow_credentials(self) -> bool:
        return "*" not in {str(item or "").strip() for item in self.cors_origins}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    cors_raw = str(os.getenv("PUBLIC_SERVICE_CORS_ORIGINS", os.getenv("BACKEND_CORS_ORIGINS", "*")) or "*").strip()
    cors_origins = [item.strip() for item in cors_raw.split(",") if item.strip()] or ["*"]
    app_env = str(os.getenv("APP_ENV", "development") or "development").strip()
    conversation_execution_authority_target, conversation_execution_user_write_target, conversation_execution_context_read_target, conversation_assistant_write_target, conversation_overlay_enabled = _resolve_conversation_rollout(app_env)
    data_root = _resolve_data_root()
    uploads_dir = _resolve_under_root(os.getenv("UPLOAD_DIR"), data_root=data_root, default="uploads")
    papers_dir = _resolve_under_root(os.getenv("PAPERS_DIR"), data_root=data_root, default="papers")
    chat_json_base_dir = _resolve_under_root(
        os.getenv("CHAT_JSON_BASE_DIR"),
        data_root=data_root,
        default="data/conversations",
    )
    vector_db_path = _resolve_under_root(os.getenv("VECTOR_DB_PATH"), data_root=data_root, default="vector_database")
    translation_cache_dir = _resolve_under_root(
        os.getenv("TRANSLATION_CACHE_DIR"),
        data_root=data_root,
        default="translation_cache",
    )
    logs_dir = _resolve_under_root(os.getenv("PUBLIC_SERVICE_LOGS_DIR"), data_root=data_root, default="logs")
    local_storage_root = _resolve_under_root(
        os.getenv("LOCAL_STORAGE_ROOT"),
        data_root=data_root,
        default="storage",
    )
    return Settings(
        app_name=str(os.getenv("PUBLIC_SERVICE_APP_NAME", "agentCode Public Service") or "agentCode Public Service").strip(),
        app_env=app_env,
        debug=_get_bool("PUBLIC_SERVICE_DEBUG", False),
        host=str(os.getenv("PUBLIC_SERVICE_HOST", "0.0.0.0") or "0.0.0.0").strip(),
        port=_get_int("PUBLIC_SERVICE_PORT", 8102, minimum=1, maximum=65535),
        api_prefix=str(os.getenv("PUBLIC_SERVICE_API_PREFIX", "/api") or "/api").strip(),
        docs_url=str(os.getenv("PUBLIC_SERVICE_DOCS_URL", "/docs") or "/docs").strip(),
        openapi_url=str(os.getenv("PUBLIC_SERVICE_OPENAPI_URL", "/openapi.json") or "/openapi.json").strip(),
        cors_origins=cors_origins,
        mysql_host=str(os.getenv("MYSQL_HOST", "127.0.0.1") or "127.0.0.1").strip(),
        mysql_port=_get_int("MYSQL_PORT", 3306, minimum=1, maximum=65535),
        mysql_user=str(os.getenv("MYSQL_USER", "root") or "root").strip(),
        mysql_password=str(os.getenv("MYSQL_PASSWORD", "") or "").strip(),
        mysql_database=str(os.getenv("MYSQL_DATABASE", "agent_reconstruct") or "agent_reconstruct").strip(),
        redis_enabled=True,
        redis_url=(str(os.getenv("REDIS_URL", "") or "").strip() or None),
        redis_host=str(os.getenv("REDIS_HOST", "127.0.0.1") or "127.0.0.1").strip(),
        redis_port=_get_int("REDIS_PORT", 6379, minimum=1, maximum=65535),
        redis_username=(str(os.getenv("REDIS_USERNAME", "") or "").strip() or None),
        redis_password=str(os.getenv("REDIS_PASSWORD", "123456") or "123456"),
        redis_db=_get_int("REDIS_DB", 0, minimum=0, maximum=63),
        redis_key_prefix=str(os.getenv("REDIS_KEY_PREFIX", "agentcode") or "agentcode").strip() or "agentcode",
        redis_socket_connect_timeout_sec=_get_int("REDIS_SOCKET_CONNECT_TIMEOUT_SEC", 2, minimum=1, maximum=60),
        redis_socket_timeout_sec=_get_int("REDIS_SOCKET_TIMEOUT_SEC", 2, minimum=1, maximum=60),
        minio_endpoint=(str(os.getenv("MINIO_ENDPOINT", "") or "").strip() or None),
        minio_access_key=(str(os.getenv("MINIO_ACCESS_KEY", "") or "").strip() or None),
        minio_secret_key=(str(os.getenv("MINIO_SECRET_KEY", "") or "").strip() or None),
        minio_bucket=str(os.getenv("MINIO_BUCKET", "agentcode") or "agentcode").strip() or "agentcode",
        minio_secure=_get_bool("MINIO_SECURE", False),
        minio_region=(str(os.getenv("MINIO_REGION", "") or "").strip() or None),
        neo4j_url=str(os.getenv("PUBLIC_SERVICE_NEO4J_URL") or os.getenv("NEO4J_URL", "") or "").strip(),
        neo4j_username=str(
            os.getenv("PUBLIC_SERVICE_NEO4J_USERNAME") or os.getenv("NEO4J_USERNAME", "neo4j") or "neo4j"
        ).strip(),
        neo4j_password=str(os.getenv("PUBLIC_SERVICE_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD", "") or ""),
        neo4j_database=str(
            os.getenv("PUBLIC_SERVICE_NEO4J_DATABASE") or os.getenv("NEO4J_DATABASE", "neo4j") or "neo4j"
        ).strip()
        or "neo4j",
        data_root=data_root,
        uploads_dir=uploads_dir,
        papers_dir=papers_dir,
        chat_json_base_dir=chat_json_base_dir,
        vector_db_path=vector_db_path,
        translation_cache_dir=translation_cache_dir,
        logs_dir=logs_dir,
        local_storage_root=local_storage_root,
        conversation_execution_authority_target=conversation_execution_authority_target,
        conversation_execution_user_write_target=conversation_execution_user_write_target,
        conversation_execution_context_read_target=conversation_execution_context_read_target,
        conversation_assistant_write_target=conversation_assistant_write_target,
        conversation_overlay_enabled=conversation_overlay_enabled,
        conversation_legacy_fallback_enabled=_get_bool("PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK", False),
        personnel_department_strict_source_enabled=_get_bool("PERSONNEL_DEPARTMENT_STRICT_SOURCE_ENABLED", False),
    )
