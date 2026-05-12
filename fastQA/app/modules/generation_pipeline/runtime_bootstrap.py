from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.config import SERVICE_ASSET_ROOT, SERVICE_STATE_ROOT
from app.integrations.llm import SharedHttpPoolConfig, build_chat_completions_client
from app.integrations.llm.openai_compat import DEFAULT_LLM_COMPATIBLE_BASE_URL


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        raw = str(os.getenv(name, "") or "").strip()
        if raw:
            return raw
    return default


def _env_float(*names: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = _env_first(*names, default=str(default))
    try:
        value = float(raw)
    except Exception:
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _resolve_under_root(raw: str | None, *, root: Path, default: str) -> str:
    value = str(raw or default).strip() or default
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return str(candidate)


@dataclass
class GenerationRuntimeInputs:
    api_key: str
    base_url: str
    model: str
    embedding_model_type: str
    embedding_api_url: str
    embedding_model_path: str
    chroma_db_path: str


def resolve_generation_runtime_inputs(
    *,
    api_key: Optional[str],
    base_url: Optional[str],
    model: str | None,
    config: dict[str, Any] | None,
    state_root: str | Path | None = None,
    asset_root: str | Path | None = None,
) -> GenerationRuntimeInputs:
    embedding_model_type = None
    embedding_api_url = None
    embedding_model_path = None
    chroma_db_path = None

    if config:
        api_key = config.get("api_key") or api_key
        base_url = config.get("base_url") or base_url
        model = config.get("model") or model
        embedding_model_type = config.get("embedding_model_type")
        embedding_api_url = config.get("embedding_api_url")
        embedding_model_path = config.get("embedding_model_path")
        chroma_db_path = config.get("chroma_db_path")

    resolved_api_key = api_key or _env_first("LLM_API_KEY")
    resolved_base_url = base_url or _env_first(
        "LLM_BASE_URL",
        default=DEFAULT_LLM_COMPATIBLE_BASE_URL,
    )
    resolved_model = model or _env_first("LLM_MODEL", default="qwen-plus")

    if embedding_model_type is None:
        embedding_model_type = _env_first("EMBEDDING_MODEL_TYPE", default="local")
    if embedding_api_url is None:
        embedding_api_url = _env_first("EMBEDDING_API_URL", "EMBEDDING_BASE_URL")
    if embedding_model_path is None:
        embedding_model_path = _env_first("EMBEDDING_MODEL_PATH", default="models/bge_model")
    if chroma_db_path is None:
        chroma_db_path = _env_first("VECTOR_DB_PATH", default="vector_database")

    state_root_path = Path(state_root or SERVICE_STATE_ROOT).resolve()
    asset_root_path = Path(asset_root or SERVICE_ASSET_ROOT).resolve()

    if embedding_model_type != "remote":
        embedding_model_path = _resolve_under_root(embedding_model_path, root=asset_root_path, default="models/bge_model")
    chroma_db_path = _resolve_under_root(chroma_db_path, root=state_root_path, default="vector_database")

    return GenerationRuntimeInputs(
        api_key=resolved_api_key,
        base_url=resolved_base_url,
        model=resolved_model,
        embedding_model_type=str(embedding_model_type or "local"),
        embedding_api_url=str(embedding_api_url or ""),
        embedding_model_path=str(embedding_model_path or ""),
        chroma_db_path=str(chroma_db_path or ""),
    )


def build_openai_client(*, api_key: str, base_url: str, logger: Any | None = None, http_client: Any | None = None) -> Any:
    transport_config = SharedHttpPoolConfig.from_env()
    connect_timeout_seconds = transport_config.connect_timeout_seconds
    read_timeout_seconds = transport_config.read_timeout_seconds
    stream_read_timeout_seconds = transport_config.stream_read_timeout_seconds
    write_timeout_seconds = transport_config.write_timeout_seconds
    pool_timeout_seconds = transport_config.pool_timeout_seconds
    if logger is not None:
        logger.info(
            "Generation pipeline使用OpenAI-compatible协议: connect=%ss read=%ss write=%ss pool=%ss max_connections=%s max_keepalive_connections=%s keepalive_expiry_seconds=%s",
            connect_timeout_seconds,
            read_timeout_seconds,
            write_timeout_seconds,
            pool_timeout_seconds,
            transport_config.max_connections,
            transport_config.max_keepalive_connections,
            transport_config.keepalive_expiry_seconds,
        )
    return build_chat_completions_client(
        api_key=api_key,
        base_url=base_url,
        logger=logger,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        stream_read_timeout_seconds=stream_read_timeout_seconds,
        write_timeout_seconds=write_timeout_seconds,
        pool_timeout_seconds=pool_timeout_seconds,
        keepalive_expiry_seconds=transport_config.keepalive_expiry_seconds,
        max_connections=transport_config.max_connections,
        max_keepalive_connections=transport_config.max_keepalive_connections,
        http_client=http_client,
    )


def ensure_literature_expert(
    *,
    existing_expert: Any,
    expert_cls: Callable[..., Any],
    runtime_inputs: GenerationRuntimeInputs,
    logger: Any,
) -> Any:
    if existing_expert is not None:
        return existing_expert

    logger.info("初始化文献语义搜索专家")
    if runtime_inputs.embedding_model_type == "remote":
        logger.info("Embedding使用远程API: %s", runtime_inputs.embedding_api_url)
    else:
        logger.info("Embedding模型路径: %s", runtime_inputs.embedding_model_path)
    logger.info("向量数据库路径: %s", runtime_inputs.chroma_db_path)

    return expert_cls(
        model_path=runtime_inputs.embedding_model_path,
        db_path=runtime_inputs.chroma_db_path,
        embedding_model_type=runtime_inputs.embedding_model_type,
        embedding_api_url=runtime_inputs.embedding_api_url,
    )


def apply_default_doi_runtime_settings(target: Any) -> None:
    target.enable_programmatic_doi_insertion = True
    target.require_pdf_evidence_for_doi = True
    target.use_new_aligner = False
    target.insert_similarity_threshold = 0.70
    target.insert_seq_verify_threshold = 0.45
    target.insert_embed_verify_threshold = 0.45
    target.insert_vector_verify_threshold = 0.45
    target.seq_similarity_weight = 0.6
    target.vector_similarity_weight = 0.4
    target.max_seq_compare_chars = 1000
    target.strict_mode = True
    target.strict_action = "remove"
