"""Centralized runtime settings for the highThinking service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from env_loader import (
    LEGACY_ENV_FILE,
    SECRET_ENV_FILE,
    SHARED_ENV_FILE,
    resolve_resource_root,
    resolve_service_root,
    load_workspace_env,
)


SERVICE_CODE = "HIGHTHINKINGQA"
PROJECT_ROOT = Path(__file__).resolve().parent
SERVICE_ROOT = PROJECT_ROOT
ENV_FILE = SHARED_ENV_FILE

load_workspace_env(override_existing=False)


RESOURCE_ROOT = resolve_resource_root()
SERVICE_CONFIG_ROOT = resolve_service_root("CONFIG")
SERVICE_STATE_ROOT = resolve_service_root("STATE")
SERVICE_RUNTIME_ROOT = resolve_service_root("RUNTIME")
SERVICE_ASSET_ROOT = resolve_service_root("ASSET")

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
    execution_target = _get_conversation_target(execution_key, "public_service")
    assistant_write_target = _get_conversation_target("CONVERSATION_ASSISTANT_WRITE_TARGET", "public_service")
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


def _resolve_relative_path(raw: str, *, base_dir: Path) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _resolve_path(name: str, default: str, *, base_dir: Path) -> str:
    raw = str(os.getenv(name, default) or default).strip() or default
    return str(_resolve_relative_path(raw, base_dir=base_dir))


def _resolve_state_path(name: str, default: str) -> str:
    return _resolve_path(name, default, base_dir=SERVICE_STATE_ROOT)


def _resolve_runtime_path(name: str, default: str) -> str:
    return _resolve_path(name, default, base_dir=SERVICE_RUNTIME_ROOT)


def _resolve_asset_path(name: str, default: str) -> str:
    raw = str(os.getenv(name, "") or "").strip()
    if raw:
        candidate = _resolve_relative_path(raw, base_dir=SERVICE_ASSET_ROOT)
        if candidate.exists() or Path(raw).expanduser().is_absolute():
            return str(candidate)
        legacy = _resolve_relative_path(raw, base_dir=PROJECT_ROOT)
        if legacy.exists():
            return str(legacy)
        return str(candidate)

    preferred = _resolve_relative_path(default, base_dir=SERVICE_ASSET_ROOT)
    if preferred.exists():
        return str(preferred)

    legacy = _resolve_relative_path(default, base_dir=PROJECT_ROOT)
    return str(legacy)


@dataclass(frozen=True)
class RuntimeSettings:
    dashscope_api_key: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_enable_thinking: bool
    decompose_model: str
    direct_answer_model: str
    sub_answer_model: str
    direct_answer_enable_thinking: bool
    decompose_enable_thinking: bool
    embedding_base_url: str
    embedding_model: str
    embedding_api_key: str
    embedding_dimensions: int
    ocr_base_url: str
    ocr_model: str
    ocr_api_key: str
    max_chunk_tokens: int
    semantic_chunk_min_tokens: int
    semantic_chunk_max_tokens: int
    tiktoken_encoding: str
    chroma_persist_dir: str
    chroma_collection_name: str
    ocr_concurrency: int
    ocr_max_concurrent_requests: int
    ocr_pages_per_batch: int
    ocr_max_retries: int
    ocr_retry_base: int
    embed_batch_size: int
    embed_api_rpm: int
    embed_api_tpm: int
    embed_concurrency: int
    embed_max_concurrent_requests: int
    embed_max_input_tokens: int
    embed_max_retries: int
    embed_queue_size: int
    retrieval_top_k: int
    retrieval_pipeline_batch_size: int
    num_sub_questions: int
    checker_model: str
    max_check_loops: int
    cache_dir: str
    parsed_markdown_cache_dir: str
    chat_json_base_dir: str
    papers_dir: str
    prompts_dir: str


@dataclass(frozen=True)
class HttpServiceSettings:
    app_env: str
    app_host: str
    app_port: int
    app_log_level: str
    upload_dir: str
    ask_stream_max_concurrent: int
    ask_executor_max_workers: int
    ask_timeout_seconds: int
    sse_heartbeat_seconds: int
    chat_persist_enabled: bool
    chat_persist_async: bool
    chat_persist_async_workers: int
    enable_cors: bool
    cors_origins: str
    runtime_root: str
    runtime_logs_dir: str


@dataclass(frozen=True)
class ConversationRolloutSettings:
    execution_authority_target: str
    execution_user_write_target: str
    execution_context_read_target: str
    assistant_write_target: str
    overlay_enabled: bool


@dataclass(frozen=True)
class GunicornSettings:
    bind_host: str
    bind_port: int
    worker_class: str
    workers: int
    threads: int
    timeout: int
    keepalive: int
    max_requests: int
    max_requests_jitter: int


def get_runtime_settings() -> RuntimeSettings:
    dashscope_api_key = str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()
    openai_api_key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()
    llm_api_key = str(os.getenv("LLM_API_KEY") or openai_api_key or dashscope_api_key or "").strip()
    embedding_api_key = str(os.getenv("EMBEDDING_API_KEY") or llm_api_key or "").strip()
    ocr_api_key = str(os.getenv("OCR_API_KEY") or llm_api_key or "").strip()

    return RuntimeSettings(
        dashscope_api_key=dashscope_api_key,
        llm_base_url=str(
            os.getenv("LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip(),
        llm_model=str(os.getenv("LLM_MODEL", "qwen3-max") or "qwen3-max").strip(),
        llm_api_key=llm_api_key,
        llm_enable_thinking=_get_bool("LLM_ENABLE_THINKING", True),
        decompose_model=str(os.getenv("DECOMPOSE_MODEL", os.getenv("LLM_MODEL", "qwen3-max")) or os.getenv("LLM_MODEL", "qwen3-max")).strip(),
        direct_answer_model=str(os.getenv("DIRECT_ANSWER_MODEL", os.getenv("LLM_MODEL", "qwen3-max")) or os.getenv("LLM_MODEL", "qwen3-max")).strip(),
        sub_answer_model=str(os.getenv("SUB_ANSWER_MODEL", os.getenv("LLM_MODEL", "qwen3-max")) or os.getenv("LLM_MODEL", "qwen3-max")).strip(),
        direct_answer_enable_thinking=_get_bool("DIRECT_ANSWER_ENABLE_THINKING", False),
        decompose_enable_thinking=_get_bool("DECOMPOSE_ENABLE_THINKING", False),
        embedding_base_url=str(
            os.getenv(
                "EMBEDDING_BASE_URL",
                os.getenv("OPENAI_BASE_URL")
                or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip(),
        embedding_model=str(os.getenv("EMBEDDING_MODEL", "text-embedding-v4") or "text-embedding-v4").strip(),
        embedding_api_key=embedding_api_key,
        embedding_dimensions=_get_int("EMBEDDING_DIMENSIONS", 2048, minimum=1),
        ocr_base_url=str(
            os.getenv(
                "OCR_BASE_URL",
                os.getenv("OPENAI_BASE_URL")
                or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).strip(),
        ocr_model=str(os.getenv("OCR_MODEL", "qwen-vl-ocr-2025-11-20") or "qwen-vl-ocr-2025-11-20").strip(),
        ocr_api_key=ocr_api_key,
        max_chunk_tokens=_get_int("MAX_CHUNK_TOKENS", 4000, minimum=1),
        semantic_chunk_min_tokens=_get_int("SEMANTIC_CHUNK_MIN_TOKENS", 2000, minimum=1),
        semantic_chunk_max_tokens=_get_int("SEMANTIC_CHUNK_MAX_TOKENS", 4000, minimum=1),
        tiktoken_encoding=str(os.getenv("TIKTOKEN_ENCODING", "cl100k_base") or "cl100k_base").strip(),
        chroma_persist_dir=_resolve_state_path("CHROMA_PERSIST_DIR", "vectordb"),
        chroma_collection_name=str(os.getenv("CHROMA_COLLECTION_NAME", "lfp_papers") or "lfp_papers").strip(),
        ocr_concurrency=_get_int("OCR_CONCURRENCY", 40, minimum=1),
        ocr_max_concurrent_requests=_get_int("OCR_MAX_CONCURRENT_REQUESTS", 40, minimum=1),
        ocr_pages_per_batch=_get_int("OCR_PAGES_PER_BATCH", 3, minimum=1),
        ocr_max_retries=_get_int("OCR_MAX_RETRIES", 5, minimum=0),
        ocr_retry_base=_get_int("OCR_RETRY_BASE", 3, minimum=1),
        embed_batch_size=_get_int("EMBED_BATCH_SIZE", 10, minimum=1),
        embed_api_rpm=_get_int("EMBED_API_RPM", 1800, minimum=1),
        embed_api_tpm=_get_int("EMBED_API_TPM", 1_200_000, minimum=1),
        embed_concurrency=_get_int("EMBED_CONCURRENCY", 2, minimum=1),
        embed_max_concurrent_requests=_get_int("EMBED_MAX_CONCURRENT_REQUESTS", 4, minimum=1),
        embed_max_input_tokens=_get_int("EMBED_MAX_INPUT_TOKENS", 8000, minimum=1),
        embed_max_retries=_get_int("EMBED_MAX_RETRIES", 5, minimum=0),
        embed_queue_size=_get_int("EMBED_QUEUE_SIZE", 200, minimum=1),
        retrieval_top_k=_get_int("RETRIEVAL_TOP_K", 3, minimum=1),
        retrieval_pipeline_batch_size=_get_int("RETRIEVAL_PIPELINE_BATCH_SIZE", 2, minimum=1),
        num_sub_questions=_get_int("NUM_SUB_QUESTIONS", 5, minimum=1),
        checker_model=str(os.getenv("CHECKER_MODEL", "qwen3.5-plus") or "qwen3.5-plus").strip(),
        max_check_loops=_get_int("MAX_CHECK_LOOPS", 2, minimum=0),
        cache_dir=_resolve_state_path("CACHE_DIR", "cache"),
        parsed_markdown_cache_dir=_resolve_state_path("PARSED_MARKDOWN_CACHE_DIR", "cache/parsed_markdown"),
        chat_json_base_dir=_resolve_state_path("CHAT_JSON_BASE_DIR", "data/conversations"),
        papers_dir=_resolve_state_path("PAPERS_DIR", "papers"),
        prompts_dir=_resolve_asset_path("PROMPTS_DIR", "prompts"),
    )


def get_http_service_settings() -> HttpServiceSettings:
    raw_port = str(os.getenv("HIGHTHINKINGQA_PORT") or os.getenv("APP_PORT") or "8008").strip()
    try:
        app_port = int(raw_port)
    except Exception:
        app_port = 8008
    app_port = max(1, min(65535, app_port))
    return HttpServiceSettings(
        app_env=str(os.getenv("APP_ENV", "dev") or "dev").strip(),
        app_host=str(os.getenv("HIGHTHINKINGQA_HOST") or os.getenv("APP_HOST") or "0.0.0.0").strip(),
        app_port=app_port,
        app_log_level=str(os.getenv("APP_LOG_LEVEL", "INFO") or "INFO").strip().upper(),
        upload_dir=_resolve_state_path("UPLOAD_DIR", "uploads"),
        ask_stream_max_concurrent=_get_int("ASK_STREAM_MAX_CONCURRENT", 5, minimum=1),
        ask_executor_max_workers=_get_int("ASK_EXECUTOR_MAX_WORKERS", 5, minimum=1),
        ask_timeout_seconds=_get_int("ASK_TIMEOUT_SECONDS", 1800, minimum=10),
        sse_heartbeat_seconds=_get_int("SSE_HEARTBEAT_SECONDS", 15, minimum=1),
        chat_persist_enabled=_get_bool("CHAT_PERSIST_ENABLED", True),
        chat_persist_async=_get_bool("CHAT_PERSIST_ASYNC", True),
        chat_persist_async_workers=_get_int("CHAT_PERSIST_ASYNC_WORKERS", 4, minimum=1),
        enable_cors=_get_bool("ENABLE_CORS", True),
        cors_origins=str(os.getenv("CORS_ORIGINS", "*") or "*").strip(),
        runtime_root=str(SERVICE_RUNTIME_ROOT),
        runtime_logs_dir=_resolve_runtime_path("APP_RUNTIME_LOGS_DIR", "logs"),
    )



def get_conversation_rollout_settings() -> ConversationRolloutSettings:
    app_env = str(os.getenv("APP_ENV", "dev") or "dev").strip()
    execution_authority_target, execution_user_write_target, execution_context_read_target, assistant_write_target, overlay_enabled = _resolve_conversation_rollout(app_env)
    return ConversationRolloutSettings(
        execution_authority_target=execution_authority_target,
        execution_user_write_target=execution_user_write_target,
        execution_context_read_target=execution_context_read_target,
        assistant_write_target=assistant_write_target,
        overlay_enabled=overlay_enabled,
    )


def get_gunicorn_settings() -> GunicornSettings:
    http_settings = get_http_service_settings()
    return GunicornSettings(
        bind_host=http_settings.app_host,
        bind_port=http_settings.app_port,
        worker_class=str(os.getenv("GUNICORN_WORKER_CLASS", "uvicorn.workers.UvicornWorker") or "uvicorn.workers.UvicornWorker").strip(),
        workers=_get_int("GUNICORN_WORKERS", 4, minimum=1),
        threads=_get_int("GUNICORN_THREADS", 8, minimum=1),
        timeout=_get_int("GUNICORN_TIMEOUT", 1800, minimum=30),
        keepalive=_get_int("GUNICORN_KEEPALIVE", 15, minimum=1),
        max_requests=_get_int("GUNICORN_MAX_REQUESTS", 1000, minimum=0),
        max_requests_jitter=_get_int("GUNICORN_MAX_REQUESTS_JITTER", 100, minimum=0),
    )


SETTINGS = get_runtime_settings()
HTTP_SETTINGS = get_http_service_settings()
CONVERSATION_ROLLOUT_SETTINGS = get_conversation_rollout_settings()
GUNICORN_SETTINGS = get_gunicorn_settings()

DASHSCOPE_API_KEY = SETTINGS.dashscope_api_key
LLM_BASE_URL = SETTINGS.llm_base_url
LLM_MODEL = SETTINGS.llm_model
LLM_API_KEY = SETTINGS.llm_api_key
LLM_ENABLE_THINKING = SETTINGS.llm_enable_thinking
DECOMPOSE_MODEL = SETTINGS.decompose_model
DIRECT_ANSWER_MODEL = SETTINGS.direct_answer_model
SUB_ANSWER_MODEL = SETTINGS.sub_answer_model
DIRECT_ANSWER_ENABLE_THINKING = SETTINGS.direct_answer_enable_thinking
DECOMPOSE_ENABLE_THINKING = SETTINGS.decompose_enable_thinking
EMBEDDING_BASE_URL = SETTINGS.embedding_base_url
EMBEDDING_MODEL = SETTINGS.embedding_model
EMBEDDING_API_KEY = SETTINGS.embedding_api_key
EMBEDDING_DIMENSIONS = SETTINGS.embedding_dimensions
OCR_BASE_URL = SETTINGS.ocr_base_url
OCR_MODEL = SETTINGS.ocr_model
OCR_API_KEY = SETTINGS.ocr_api_key
MAX_CHUNK_TOKENS = SETTINGS.max_chunk_tokens
SEMANTIC_CHUNK_MIN_TOKENS = SETTINGS.semantic_chunk_min_tokens
SEMANTIC_CHUNK_MAX_TOKENS = SETTINGS.semantic_chunk_max_tokens
TIKTOKEN_ENCODING = SETTINGS.tiktoken_encoding
CHROMA_PERSIST_DIR = SETTINGS.chroma_persist_dir
CHROMA_COLLECTION_NAME = SETTINGS.chroma_collection_name
OCR_CONCURRENCY = SETTINGS.ocr_concurrency
OCR_MAX_CONCURRENT_REQUESTS = SETTINGS.ocr_max_concurrent_requests
OCR_PAGES_PER_BATCH = SETTINGS.ocr_pages_per_batch
OCR_MAX_RETRIES = SETTINGS.ocr_max_retries
OCR_RETRY_BASE = SETTINGS.ocr_retry_base
EMBED_BATCH_SIZE = SETTINGS.embed_batch_size
EMBED_API_RPM = SETTINGS.embed_api_rpm
EMBED_API_TPM = SETTINGS.embed_api_tpm
EMBED_CONCURRENCY = SETTINGS.embed_concurrency
EMBED_MAX_CONCURRENT_REQUESTS = SETTINGS.embed_max_concurrent_requests
EMBED_MAX_INPUT_TOKENS = SETTINGS.embed_max_input_tokens
EMBED_MAX_RETRIES = SETTINGS.embed_max_retries
EMBED_QUEUE_SIZE = SETTINGS.embed_queue_size
RETRIEVAL_TOP_K = SETTINGS.retrieval_top_k
RETRIEVAL_PIPELINE_BATCH_SIZE = SETTINGS.retrieval_pipeline_batch_size
NUM_SUB_QUESTIONS = SETTINGS.num_sub_questions
CHECKER_MODEL = SETTINGS.checker_model
MAX_CHECK_LOOPS = SETTINGS.max_check_loops
CACHE_DIR = SETTINGS.cache_dir
PARSED_MARKDOWN_CACHE_DIR = SETTINGS.parsed_markdown_cache_dir
PAPERS_DIR = SETTINGS.papers_dir
PROMPTS_DIR = SETTINGS.prompts_dir
CHAT_JSON_BASE_DIR = SETTINGS.chat_json_base_dir
APP_ENV = HTTP_SETTINGS.app_env
APP_HOST = HTTP_SETTINGS.app_host
APP_PORT = HTTP_SETTINGS.app_port
APP_LOG_LEVEL = HTTP_SETTINGS.app_log_level
UPLOAD_DIR = HTTP_SETTINGS.upload_dir
ASK_STREAM_MAX_CONCURRENT = HTTP_SETTINGS.ask_stream_max_concurrent
ASK_EXECUTOR_MAX_WORKERS = HTTP_SETTINGS.ask_executor_max_workers
ASK_TIMEOUT_SECONDS = HTTP_SETTINGS.ask_timeout_seconds
SSE_HEARTBEAT_SECONDS = HTTP_SETTINGS.sse_heartbeat_seconds
CHAT_PERSIST_ENABLED = HTTP_SETTINGS.chat_persist_enabled
CHAT_PERSIST_ASYNC = HTTP_SETTINGS.chat_persist_async
CHAT_PERSIST_ASYNC_WORKERS = HTTP_SETTINGS.chat_persist_async_workers
ENABLE_CORS = HTTP_SETTINGS.enable_cors
CORS_ORIGINS = HTTP_SETTINGS.cors_origins
APP_RUNTIME_ROOT = HTTP_SETTINGS.runtime_root
APP_RUNTIME_LOGS_DIR = HTTP_SETTINGS.runtime_logs_dir
CONVERSATION_EXECUTION_AUTHORITY_TARGET = CONVERSATION_ROLLOUT_SETTINGS.execution_authority_target
CONVERSATION_EXECUTION_USER_WRITE_TARGET = CONVERSATION_ROLLOUT_SETTINGS.execution_user_write_target
CONVERSATION_EXECUTION_CONTEXT_READ_TARGET = CONVERSATION_ROLLOUT_SETTINGS.execution_context_read_target
CONVERSATION_ASSISTANT_WRITE_TARGET = CONVERSATION_ROLLOUT_SETTINGS.assistant_write_target
CONVERSATION_OVERLAY_ENABLED = CONVERSATION_ROLLOUT_SETTINGS.overlay_enabled
GUNICORN_BIND_HOST = GUNICORN_SETTINGS.bind_host
GUNICORN_BIND_PORT = GUNICORN_SETTINGS.bind_port
GUNICORN_WORKER_CLASS = GUNICORN_SETTINGS.worker_class
GUNICORN_WORKERS = GUNICORN_SETTINGS.workers
GUNICORN_THREADS = GUNICORN_SETTINGS.threads
GUNICORN_TIMEOUT = GUNICORN_SETTINGS.timeout
GUNICORN_KEEPALIVE = GUNICORN_SETTINGS.keepalive
GUNICORN_MAX_REQUESTS = GUNICORN_SETTINGS.max_requests
GUNICORN_MAX_REQUESTS_JITTER = GUNICORN_SETTINGS.max_requests_jitter
