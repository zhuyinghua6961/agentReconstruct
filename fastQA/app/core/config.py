from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from app.core.env_loader import (
    LEGACY_ENV_FILE as LEGACY_CONFIG_ENV_FILE,
    SECRET_ENV_FILE as SECRET_CONFIG_ENV_FILE,
    SHARED_ENV_FILE as SHARED_CONFIG_ENV_FILE,
    load_workspace_env,
    resolve_resource_root,
    resolve_service_root,
)


BASE_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = Path(__file__).resolve().parents[3]
ENV_FILE = SHARED_CONFIG_ENV_FILE
SECRET_ENV_FILE = SECRET_CONFIG_ENV_FILE
LEGACY_ENV_FILE = LEGACY_CONFIG_ENV_FILE
RESOURCE_ROOT = resolve_resource_root()
SERVICE_CONFIG_ROOT = resolve_service_root("CONFIG")
SERVICE_STATE_ROOT = resolve_service_root("STATE")
SERVICE_RUNTIME_ROOT = resolve_service_root("RUNTIME")
SERVICE_ASSET_ROOT = resolve_service_root("ASSET")

load_workspace_env(override_existing=False)

_CONVERSATION_AUTHORITY_TARGETS = frozenset({"legacy", "public_service", "shadow_public_service"})
_PRODUCTION_APP_ENVS = frozenset({"prod", "production"})
logger = logging.getLogger(__name__)


def _get_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _get_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except Exception:
        if raw and raw != str(default):
            logger.warning("invalid int env %s=%r; using default %s", name, raw, default)
        value = int(default)
    original = value
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    if raw and raw != str(default) and value != original:
        logger.warning("out-of-range int env %s=%r; clamped to %s", name, raw, value)
    return value


def _get_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = float(raw)
    except Exception:
        if raw and raw != str(default):
            logger.warning("invalid float env %s=%r; using default %s", name, raw, default)
        value = float(default)
    original = value
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    if raw and raw != str(default) and value != original:
        logger.warning("out-of-range float env %s=%r; clamped to %s", name, raw, value)
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
        if not raw:
            continue
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


def _resolve_under_root(raw: str | None, *, root: Path, default: str) -> Path:
    value = str(raw or default).strip() or default
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


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
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    minio_region: str | None
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
    generation_runtime_enabled: bool
    graph_kb_enabled: bool
    graph_kb_v2_enabled: bool
    graph_kb_rag_injection_enabled: bool
    graph_kb_timeout_ms: int
    graph_kb_max_rows: int
    graph_kb_query_logging: bool
    allow_placeholder_fallback: bool
    file_context_fallback_enabled: bool
    ask_stream_max_concurrent: int
    sse_heartbeat_sec: int
    llm_http_shared_pool_enabled: bool
    llm_http_connect_timeout_seconds: float
    llm_http_read_timeout_seconds: float
    llm_http_stream_read_timeout_seconds: float
    llm_http_write_timeout_seconds: float
    llm_http_keepalive_expiry_seconds: float
    llm_http_max_connections: int
    llm_http_max_keepalive_connections: int
    llm_http_pool_timeout_seconds: float
    stage2_chat_hot_pool_enabled: bool
    stage2_rerank_hot_pool_enabled: bool
    stage2_chat_hot_lane_count: int
    stage2_rerank_hot_lane_count: int
    stage2_chat_warmup_enabled: bool
    stage2_rerank_warmup_enabled: bool
    stage2_chat_warm_interval_seconds: int
    stage2_rerank_warm_interval_seconds: int
    stage2_chat_hot_keepalive_expiry_seconds: float
    stage2_chat_warm_timeout_seconds: float
    stage2_rerank_warm_timeout_seconds: float
    stage2_bootstrap_warm_max_parallel: int
    stage2_bootstrap_warm_jitter_seconds: int
    stage2_chat_gate_max_in_flight: int
    stage2_rerank_gate_max_in_flight: int
    stage2_warm_jitter_seconds: int
    stage2_lane_degraded_after_seconds: int
    stage2_warm_active_start_hour: int
    stage2_warm_active_end_hour: int
    chat_persist_enabled: bool
    chat_persist_async: bool
    conversation_execution_authority_target: str
    conversation_execution_user_write_target: str
    conversation_execution_context_read_target: str
    conversation_assistant_write_target: str
    conversation_overlay_enabled: bool
    vector_db_path: Path
    vector_db_summary_path: Path
    vector_db_pdf_path: Path
    vector_db_community_path: Path
    vector_db_md_path: Path
    topic_index_path: Path
    json_dir: Path
    json_normalized_dir: Path
    papers_dir: Path
    pdf_chunks_dir: Path
    json_summary_dir: Path
    translation_cache_dir: Path
    chat_json_base_dir: Path
    prompts_dir: Path
    logs_dir: Path

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    from app.integrations.llm import SharedHttpPoolConfig

    cors_raw = str(os.getenv("BACKEND_CORS_ORIGINS", "*") or "*").strip()
    cors_origins = [item.strip() for item in cors_raw.split(",") if item.strip()] or ["*"]
    fastapi_host = str(os.getenv("FASTAPI_HOST", os.getenv("BACKEND_HOST", "0.0.0.0")) or "0.0.0.0").strip()
    app_env = str(os.getenv("APP_ENV", "development") or "development").strip()
    raw_fastapi_port = str(os.getenv("FASTAPI_PORT", os.getenv("BACKEND_PORT", "8012")) or "8012").strip()
    try:
        fastapi_port_default = int(raw_fastapi_port)
    except Exception:
        fastapi_port_default = 8012

    conversation_execution_authority_target, conversation_execution_user_write_target, conversation_execution_context_read_target, conversation_assistant_write_target, conversation_overlay_enabled = _resolve_conversation_rollout(app_env)

    state_root = Path(SERVICE_STATE_ROOT)
    runtime_root = Path(SERVICE_RUNTIME_ROOT)
    asset_root = Path(SERVICE_ASSET_ROOT)
    shared_http_pool_config = SharedHttpPoolConfig.from_env()

    return Settings(
        app_name=str(os.getenv("FASTAPI_APP_NAME", "fastQA FastAPI") or "fastQA FastAPI").strip(),
        app_env=app_env,
        debug=_get_bool("FASTAPI_DEBUG", False),
        host=fastapi_host or "0.0.0.0",
        port=_get_int("FASTAPI_PORT", fastapi_port_default, minimum=1, maximum=65535),
        api_prefix=str(os.getenv("FASTAPI_API_PREFIX", "/api") or "/api").strip(),
        docs_url=str(os.getenv("FASTAPI_DOCS_URL", "/docs") or "/docs").strip(),
        openapi_url=str(os.getenv("FASTAPI_OPENAPI_URL", "/openapi.json") or "/openapi.json").strip(),
        cors_origins=cors_origins,
        mysql_host=str(os.getenv("MYSQL_HOST", "127.0.0.1") or "127.0.0.1").strip(),
        mysql_port=_get_int("MYSQL_PORT", 3306, minimum=1, maximum=65535),
        mysql_user=str(os.getenv("MYSQL_USER", "root") or "root").strip(),
        mysql_password=str(os.getenv("MYSQL_PASSWORD", "") or "").strip(),
        mysql_database=str(os.getenv("MYSQL_DATABASE", "agent_reconstruct") or "agent_reconstruct").strip(),
        minio_endpoint=str(os.getenv("MINIO_ENDPOINT", "") or "").strip(),
        minio_access_key=str(os.getenv("MINIO_ACCESS_KEY", "") or "").strip(),
        minio_secret_key=str(os.getenv("MINIO_SECRET_KEY", "") or "").strip(),
        minio_bucket=str(os.getenv("MINIO_BUCKET", "agentcode") or "agentcode").strip() or "agentcode",
        minio_secure=_get_bool("MINIO_SECURE", False),
        minio_region=(str(os.getenv("MINIO_REGION", "") or "").strip() or None),
        redis_enabled=_get_bool("REDIS_ENABLED", False),
        redis_url=(str(os.getenv("REDIS_URL", "") or "").strip() or None),
        redis_host=str(os.getenv("REDIS_HOST", "127.0.0.1") or "127.0.0.1").strip(),
        redis_port=_get_int("REDIS_PORT", 6379, minimum=1, maximum=65535),
        redis_username=(str(os.getenv("REDIS_USERNAME", "") or "").strip() or None),
        redis_password=str(os.getenv("REDIS_PASSWORD", "123456") or "123456"),
        redis_db=_get_int("REDIS_DB", 0, minimum=0, maximum=63),
        redis_key_prefix=str(os.getenv("REDIS_KEY_PREFIX", "fastqa") or "fastqa").strip() or "fastqa",
        redis_socket_connect_timeout_sec=_get_int("REDIS_SOCKET_CONNECT_TIMEOUT_SEC", 2, minimum=1, maximum=60),
        redis_socket_timeout_sec=_get_int("REDIS_SOCKET_TIMEOUT_SEC", 2, minimum=1, maximum=60),
        generation_runtime_enabled=_get_bool("FASTQA_GENERATION_RUNTIME_ENABLED", False),
        graph_kb_enabled=_get_bool("FASTQA_GRAPH_KB_ENABLED", False),
        graph_kb_v2_enabled=_get_bool("FASTQA_GRAPH_KB_V2_ENABLED", False),
        graph_kb_rag_injection_enabled=_get_bool("FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED", False),
        graph_kb_timeout_ms=_get_int("FASTQA_GRAPH_KB_TIMEOUT_MS", 3000, minimum=100, maximum=60000),
        graph_kb_max_rows=_get_int("FASTQA_GRAPH_KB_MAX_ROWS", 20, minimum=1, maximum=200),
        graph_kb_query_logging=_get_bool("FASTQA_GRAPH_KB_QUERY_LOGGING", False),
        allow_placeholder_fallback=_get_bool("FASTQA_ALLOW_PLACEHOLDER_FALLBACK", True),
        file_context_fallback_enabled=_get_bool("FASTQA_ENABLE_FILE_CONTEXT_FALLBACK", True),
        ask_stream_max_concurrent=_get_int("ASK_STREAM_MAX_CONCURRENT", 20, minimum=1, maximum=500),
        sse_heartbeat_sec=_get_int(
            "SSE_HEARTBEAT_SEC",
            _get_int("SSE_HEARTBEAT_SECONDS", 15, minimum=5, maximum=120),
            minimum=5,
            maximum=120,
        ),
        llm_http_shared_pool_enabled=_get_bool("FASTQA_LLM_HTTP_SHARED_POOL_ENABLED", False),
        llm_http_connect_timeout_seconds=shared_http_pool_config.connect_timeout_seconds,
        llm_http_read_timeout_seconds=shared_http_pool_config.read_timeout_seconds,
        llm_http_stream_read_timeout_seconds=_get_float(
            "FASTQA_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS",
            600.0,
            minimum=5.0,
            maximum=7200.0,
        ),
        llm_http_write_timeout_seconds=shared_http_pool_config.write_timeout_seconds,
        llm_http_keepalive_expiry_seconds=shared_http_pool_config.keepalive_expiry_seconds,
        llm_http_max_connections=shared_http_pool_config.max_connections,
        llm_http_max_keepalive_connections=shared_http_pool_config.max_keepalive_connections,
        llm_http_pool_timeout_seconds=shared_http_pool_config.pool_timeout_seconds,
        stage2_chat_hot_pool_enabled=_get_bool("FASTQA_STAGE2_CHAT_HOT_POOL_ENABLED", True),
        stage2_rerank_hot_pool_enabled=_get_bool("FASTQA_STAGE2_RERANK_HOT_POOL_ENABLED", True),
        stage2_chat_hot_lane_count=_get_int("FASTQA_STAGE2_CHAT_HOT_LANE_COUNT", 3, minimum=0, maximum=16),
        stage2_rerank_hot_lane_count=_get_int("FASTQA_STAGE2_RERANK_HOT_LANE_COUNT", 3, minimum=0, maximum=16),
        stage2_chat_warmup_enabled=_get_bool("FASTQA_STAGE2_CHAT_WARMUP_ENABLED", True),
        stage2_rerank_warmup_enabled=_get_bool("FASTQA_STAGE2_RERANK_WARMUP_ENABLED", True),
        stage2_chat_warm_interval_seconds=_get_int(
            "FASTQA_STAGE2_CHAT_WARM_INTERVAL_SECONDS", 300, minimum=30, maximum=7200
        ),
        stage2_rerank_warm_interval_seconds=_get_int(
            "FASTQA_STAGE2_RERANK_WARM_INTERVAL_SECONDS", 300, minimum=30, maximum=7200
        ),
        stage2_chat_hot_keepalive_expiry_seconds=_get_float(
            "FASTQA_STAGE2_CHAT_HOT_KEEPALIVE_EXPIRY_SECONDS", 1800.0, minimum=60.0, maximum=7200.0
        ),
        stage2_chat_warm_timeout_seconds=_get_float(
            "FASTQA_STAGE2_CHAT_WARM_TIMEOUT_SECONDS", 420.0, minimum=420.0, maximum=1800.0
        ),
        stage2_rerank_warm_timeout_seconds=_get_float(
            "FASTQA_STAGE2_RERANK_WARM_TIMEOUT_SECONDS", 420.0, minimum=420.0, maximum=1800.0
        ),
        stage2_bootstrap_warm_max_parallel=_get_int(
            "FASTQA_STAGE2_BOOTSTRAP_WARM_MAX_PARALLEL", 1, minimum=1, maximum=16
        ),
        stage2_bootstrap_warm_jitter_seconds=_get_int(
            "FASTQA_STAGE2_BOOTSTRAP_WARM_JITTER_SECONDS", 30, minimum=0, maximum=600
        ),
        stage2_chat_gate_max_in_flight=_get_int(
            "FASTQA_STAGE2_CHAT_GATE_MAX_IN_FLIGHT", 3, minimum=0, maximum=16
        ),
        stage2_rerank_gate_max_in_flight=_get_int(
            "FASTQA_STAGE2_RERANK_GATE_MAX_IN_FLIGHT", 3, minimum=0, maximum=16
        ),
        stage2_warm_jitter_seconds=_get_int("FASTQA_STAGE2_WARM_JITTER_SECONDS", 60, minimum=0, maximum=600),
        stage2_lane_degraded_after_seconds=_get_int(
            "FASTQA_STAGE2_LANE_DEGRADED_AFTER_SECONDS", 900, minimum=60, maximum=86400
        ),
        stage2_warm_active_start_hour=_get_int(
            "FASTQA_STAGE2_WARM_ACTIVE_START_HOUR", 0, minimum=0, maximum=23
        ),
        stage2_warm_active_end_hour=_get_int(
            "FASTQA_STAGE2_WARM_ACTIVE_END_HOUR", 24, minimum=1, maximum=24
        ),
        chat_persist_enabled=_get_bool("CHAT_PERSIST_ENABLED", True),
        chat_persist_async=_get_bool("CHAT_PERSIST_ASYNC", True),
        conversation_execution_authority_target=conversation_execution_authority_target,
        conversation_execution_user_write_target=conversation_execution_user_write_target,
        conversation_execution_context_read_target=conversation_execution_context_read_target,
        conversation_assistant_write_target=conversation_assistant_write_target,
        conversation_overlay_enabled=conversation_overlay_enabled,
        vector_db_path=_resolve_under_root(os.getenv("VECTOR_DB_PATH"), root=state_root, default="vector_database"),
        vector_db_summary_path=_resolve_under_root(os.getenv("VECTOR_DB_SUMMARY_PATH"), root=state_root, default="vector_database"),
        vector_db_pdf_path=_resolve_under_root(os.getenv("VECTOR_DB_PDF_PATH"), root=state_root, default="vector_database_pdf"),
        vector_db_community_path=_resolve_under_root(os.getenv("VECTOR_DB_COMMUNITY_PATH"), root=state_root, default="community_vector_database"),
        vector_db_md_path=_resolve_under_root(os.getenv("VECTOR_DB_MD_PATH"), root=state_root, default="vector_database_md"),
        topic_index_path=_resolve_under_root(os.getenv("TOPIC_INDEX_PATH"), root=state_root, default="vector_db_topic_index.json"),
        json_dir=_resolve_under_root(os.getenv("JSON_DIR"), root=state_root, default="json"),
        json_normalized_dir=_resolve_under_root(os.getenv("JSON_NORMALIZED_DIR"), root=state_root, default="json_normalized"),
        papers_dir=_resolve_under_root(os.getenv("PAPERS_DIR"), root=state_root, default="papers"),
        pdf_chunks_dir=_resolve_under_root(os.getenv("PDF_CHUNKS_DIR"), root=state_root, default="pdf_chunks"),
        json_summary_dir=_resolve_under_root(os.getenv("JSON_SUMMARY_DIR"), root=state_root, default="json_summary"),
        translation_cache_dir=_resolve_under_root(os.getenv("TRANSLATION_CACHE_DIR"), root=state_root, default="translation_cache"),
        chat_json_base_dir=_resolve_under_root(os.getenv("CHAT_JSON_BASE_DIR"), root=state_root, default="data/conversations"),
        prompts_dir=_resolve_under_root(os.getenv("MATERIAL_AGENT_PROMPTS_DIR"), root=asset_root, default="prompts"),
        logs_dir=_resolve_under_root(os.getenv("FASTQA_LOGS_DIR"), root=runtime_root, default="logs"),
    )
