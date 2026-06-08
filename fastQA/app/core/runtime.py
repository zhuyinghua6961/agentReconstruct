from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from app.integrations.llm import (
    ChatHotLanePool,
    FastQASharedUpstreamHttpPool,
    RerankSessionPool,
    SharedHttpPoolConfig,
    SharedStage2UpstreamGate,
)
from app.integrations.llm.thinking import auth_headers
from app.integrations.redis import RedisService, build_redis_bindings

try:
    from app.integrations.neo4j.client import bootstrap_neo4j
except Exception:  # pragma: no cover
    bootstrap_neo4j = None


_LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _rerank_auth_mode() -> str:
    return str(os.getenv("RERANK_AUTH_MODE") or "bearer").strip()


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        raw = str(os.getenv(name, "") or "").strip()
        if raw:
            return raw
    return str(default or "").strip()


def _normalize_rerank_endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return value
    for suffix in ("/v1/rerank", "/rerank"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/rerank"


def _set_component_status(
    runtime: Any,
    component: str,
    *,
    status: str,
    detail: str = "",
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": str(status or "unknown"),
        "detail": str(detail or ""),
        "error": str(error or ""),
        "updated_at": _now_iso(),
    }
    if extra:
        payload.update(extra)
    runtime.component_status[component] = payload
    runtime.health_flags[component] = payload["status"]


def _set_stage2_hot_pool_component_defaults(runtime: Any) -> None:
    settings = getattr(runtime, "settings", None)
    generation_enabled = bool(getattr(settings, "generation_runtime_enabled", False))

    for component_name, enabled_attr, lanes_attr in (
        ("stage2_chat_hot_pool", "stage2_chat_hot_pool_enabled", "stage2_chat_hot_lane_count"),
        ("stage2_rerank_hot_pool", "stage2_rerank_hot_pool_enabled", "stage2_rerank_hot_lane_count"),
    ):
        pool_enabled = bool(getattr(settings, enabled_attr, False))
        total_lanes = int(getattr(settings, lanes_attr, 0) or 0)
        if generation_enabled and pool_enabled:
            status = "pending"
            detail = "stage2 hot pool not initialized yet"
        elif generation_enabled:
            status = "skipped"
            detail = "stage2 hot pool disabled by config"
        else:
            status = "skipped"
            detail = "generation runtime disabled"
        _set_component_status(
            runtime,
            component_name,
            status=status,
            detail=detail,
            extra={
                "enabled": bool(generation_enabled and pool_enabled),
                "ready": False,
                "total_lanes": total_lanes,
                "ready_lanes": 0,
                "warming_lanes": 0,
                "degraded_lanes": 0,
                "last_any_warm_success_at": "",
                "last_any_error_at": "",
                "last_error_summary": "",
                "next_keepalive_at": "",
            },
        )


def bootstrap_redis(runtime: Any) -> None:
    bindings = build_redis_bindings(settings=runtime.settings)
    runtime.redis_bindings = bindings
    runtime.redis_client = bindings.client
    runtime.redis_service = RedisService.from_prefix(
        client=bindings.client,
        key_prefix=str(bindings.key_prefix or runtime.settings.redis_key_prefix or "agentcode"),
    )

    status = "ok"
    if not bindings.enabled:
        status = "skipped"
    elif not bindings.available:
        status = "degraded"
    _set_component_status(
        runtime,
        "redis",
        status=status,
        detail=bindings.detail,
        error=bindings.error,
        extra={
            "enabled": bindings.enabled,
            "available": bindings.available,
            "library_available": bindings.library_available,
            "url": bindings.url,
            "key_prefix": bindings.key_prefix,
        },
    )


def generation_runtime_is_ready(runtime: Any) -> bool:
    if getattr(runtime, "generation_runtime", None) is None:
        return False
    status = dict(getattr(runtime, "component_status", {}).get("generation_runtime") or {})
    return str(status.get("status") or "").strip().lower() == "ok"


def _shared_llm_pool_enabled(runtime: Any | None = None) -> bool:
    settings = getattr(runtime, "settings", None) if runtime is not None else None
    configured = getattr(settings, "llm_http_shared_pool_enabled", None)
    if configured is not None:
        return bool(configured)
    return _env_bool("FASTQA_LLM_HTTP_SHARED_POOL_ENABLED", False)


def _shared_llm_pool_config(runtime: Any) -> SharedHttpPoolConfig:
    settings = getattr(runtime, "settings", None)
    if settings is None:
        return SharedHttpPoolConfig.from_env()
    try:
        return SharedHttpPoolConfig(
            connect_timeout_seconds=float(getattr(settings, "llm_http_connect_timeout_seconds")),
            read_timeout_seconds=float(getattr(settings, "llm_http_read_timeout_seconds")),
            stream_read_timeout_seconds=float(getattr(settings, "llm_http_stream_read_timeout_seconds")),
            write_timeout_seconds=float(getattr(settings, "llm_http_write_timeout_seconds")),
            pool_timeout_seconds=float(getattr(settings, "llm_http_pool_timeout_seconds")),
            keepalive_expiry_seconds=float(getattr(settings, "llm_http_keepalive_expiry_seconds")),
            max_connections=int(getattr(settings, "llm_http_max_connections")),
            max_keepalive_connections=int(getattr(settings, "llm_http_max_keepalive_connections")),
        )
    except Exception:
        return SharedHttpPoolConfig.from_env()


def _shared_llm_status_extra(
    *,
    runtime: Any,
    status: str,
    ready: bool,
    client_owner: str,
    bootstrap_source: str,
    shared_pool: Any | None = None,
) -> dict[str, Any]:
    config = _shared_llm_pool_config(runtime)
    snapshot = dict(getattr(shared_pool, "snapshot", lambda: {})() or {})
    return {
        "enabled": _shared_llm_pool_enabled(runtime),
        "ready": bool(ready),
        "pool_owner": "app",
        "client_owner": str(client_owner or "private"),
        "shared_client_id": snapshot.get("shared_client_id"),
        "pid": int(snapshot.get("pid") or os.getpid()),
        "bootstrap_source": str(snapshot.get("bootstrap_source") or bootstrap_source or "startup"),
        "pool_timeout_count": int(snapshot.get("pool_timeout_count") or 0),
        "pool_wait_ms": float(snapshot.get("pool_wait_ms") or 0.0),
        "max_connections": int(snapshot.get("max_connections") or config.max_connections),
        "max_keepalive_connections": int(
            snapshot.get("max_keepalive_connections") or config.max_keepalive_connections
        ),
        "keepalive_expiry_seconds": float(
            snapshot.get("keepalive_expiry_seconds") or config.keepalive_expiry_seconds
        ),
    }


def _set_shared_llm_pool_status(
    runtime: Any,
    *,
    status: str,
    detail: str,
    error: str = "",
    client_owner: str,
    bootstrap_source: str = "startup",
    shared_pool: Any | None = None,
) -> None:
    extra = _shared_llm_status_extra(
        runtime=runtime,
        status=status,
        ready=status == "ok",
        client_owner=client_owner,
        bootstrap_source=bootstrap_source,
        shared_pool=shared_pool,
    )
    _set_component_status(
        runtime,
        "shared_llm_pool",
        status=status,
        detail=detail,
        error=error,
        extra=extra,
    )
    logger = getattr(runtime, "logger", None)
    if logger is not None:
        logger.info(
            "fastqa shared llm pool status=%s ready=%s pool_owner=%s client_owner=%s shared_client_id=%s pid=%s bootstrap_source=%s pool_timeout_count=%s max_connections=%s max_keepalive_connections=%s keepalive_expiry_seconds=%s detail=%s error=%s",
            status,
            extra["ready"],
            extra["pool_owner"],
            extra["client_owner"],
            extra["shared_client_id"],
            extra["pid"],
            extra["bootstrap_source"],
            extra["pool_timeout_count"],
            extra["max_connections"],
            extra["max_keepalive_connections"],
            extra["keepalive_expiry_seconds"],
            detail,
            error,
        )


def _ensure_shared_llm_http_pool(runtime: Any) -> Any | None:
    existing = getattr(runtime, "shared_llm_http_pool", None)
    existing_client = getattr(existing, "client", None)
    if callable(existing_client) and existing_client() is not None:
        return existing
    pool = FastQASharedUpstreamHttpPool.from_env(bootstrap_source="startup")
    runtime.shared_llm_http_pool = pool
    return pool


def _pool_ready_lanes(pool: Any | None) -> int:
    return int(dict(getattr(pool, "snapshot", lambda: {})() or {}).get("ready_lanes") or 0)


def _build_shared_stage2_upstream_gate(
    *,
    name: str,
    configured_limit: int,
    pool_getter: Any,
    logger: Any | None,
) -> SharedStage2UpstreamGate | None:
    limit = max(0, int(configured_limit or 0))
    if limit <= 0:
        return None
    return SharedStage2UpstreamGate(
        name=name,
        limit=limit,
        logger=logger,
        limit_provider=lambda: _pool_ready_lanes(pool_getter()),
    )


def _set_stage2_chat_hot_pool_status(
    runtime: Any,
    *,
    status: str,
    detail: str,
    error: str = "",
    pool: Any | None = None,
) -> None:
    snapshot = dict(getattr(pool, "snapshot", lambda: {})() or {})
    total_lanes = int(
        snapshot.get("total_lanes")
        or getattr(getattr(runtime, "settings", None), "stage2_chat_hot_lane_count", 0)
        or 0
    )
    _set_component_status(
        runtime,
        "stage2_chat_hot_pool",
        status=status,
        detail=detail,
        error=error,
        extra={
            "enabled": bool(getattr(getattr(runtime, "settings", None), "stage2_chat_hot_pool_enabled", False)),
            "ready": int(snapshot.get("ready_lanes") or 0) > 0 and str(status).strip().lower() != "degraded",
            "total_lanes": total_lanes,
            "ready_lanes": int(snapshot.get("ready_lanes") or 0),
            "warming_lanes": int(snapshot.get("warming_lanes") or 0),
            "degraded_lanes": int(snapshot.get("degraded_lanes") or 0),
            "last_any_warm_success_at": str(snapshot.get("last_any_warm_success_at") or ""),
            "last_any_error_at": str(snapshot.get("last_any_error_at") or ""),
            "last_error_summary": str(snapshot.get("last_error_summary") or ""),
            "next_keepalive_at": str(snapshot.get("next_keepalive_at") or ""),
        },
    )


def _set_stage2_rerank_hot_pool_status(
    runtime: Any,
    *,
    status: str,
    detail: str,
    error: str = "",
    pool: Any | None = None,
) -> None:
    snapshot = dict(getattr(pool, "snapshot", lambda: {})() or {})
    total_lanes = int(
        snapshot.get("total_lanes")
        or getattr(getattr(runtime, "settings", None), "stage2_rerank_hot_lane_count", 0)
        or 0
    )
    _set_component_status(
        runtime,
        "stage2_rerank_hot_pool",
        status=status,
        detail=detail,
        error=error,
        extra={
            "enabled": bool(getattr(getattr(runtime, "settings", None), "stage2_rerank_hot_pool_enabled", False)),
            "ready": str(status).strip().lower() == "ok",
            "total_lanes": total_lanes,
            "ready_lanes": int(snapshot.get("ready_lanes") or 0),
            "warming_lanes": int(snapshot.get("warming_lanes") or 0),
            "degraded_lanes": int(snapshot.get("degraded_lanes") or 0),
            "last_any_warm_success_at": str(snapshot.get("last_any_warm_success_at") or ""),
            "last_any_error_at": str(snapshot.get("last_any_error_at") or ""),
            "last_error_summary": str(snapshot.get("last_error_summary") or ""),
            "next_keepalive_at": str(snapshot.get("next_keepalive_at") or ""),
        },
    )


def _warm_stage2_chat_lane(
    *,
    lane: Any,
    model: str,
    timeout_seconds: float,
    reason: str = "manual",
) -> None:
    from app.integrations.llm.thinking import LLM_STAGE_CONTROL, merge_extra_body, resolve_thinking_controls

    _ = reason
    client = getattr(lane, "client", None)
    if client is None:
        raise RuntimeError("chat lane client unavailable")
    controls = resolve_thinking_controls(
        stage=LLM_STAGE_CONTROL,
        max_tokens=1,
        stream=False,
    )
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "warm"}],
        temperature=0.0,
        max_tokens=controls.max_tokens,
        extra_body=merge_extra_body(None, controls),
        timeout=timeout_seconds,
    )


def _warm_stage2_rerank_lane(
    *,
    lane: Any,
    provider: str,
    api_key: str,
    model: str,
    base_url: str,
    timeout_seconds: float,
    reason: str = "manual",
) -> None:
    del provider
    session = getattr(lane, "session", None)
    if session is None:
        raise RuntimeError("rerank lane session unavailable")
    endpoint = _normalize_rerank_endpoint(base_url)
    if not endpoint or not str(model or "").strip():
        return
    headers = auth_headers(api_key, auth_mode=_rerank_auth_mode()) if api_key else {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "query": "warm",
        "documents": ["warm doc one", "warm doc two"],
        "top_n": 1,
    }
    started_at = time.perf_counter()
    auth_mode = _rerank_auth_mode()
    _LOGGER.info(
        "model_call start service=fastQA component=rerank_warmup model=%s endpoint=%s auth_mode=%s "
        "candidate_count=%s top_n=%s query_chars=%s timeout_seconds=%s key_present=%s reason=%s",
        str(model or "").strip(),
        endpoint,
        auth_mode,
        len(payload["documents"]),
        payload["top_n"],
        len(str(payload["query"])),
        timeout_seconds,
        bool(api_key),
        str(reason or ""),
    )
    response = None
    try:
        response = session.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        parse_json = getattr(response, "json", None)
        if callable(parse_json):
            parse_json()
    except Exception as exc:
        _LOGGER.warning(
            "model_call failed service=fastQA component=rerank_warmup model=%s endpoint=%s auth_mode=%s "
            "status_code=%s elapsed_ms=%.2f reason=warmup_failed error_type=%s",
            str(model or "").strip(),
            endpoint,
            auth_mode,
            getattr(response, "status_code", None),
            (time.perf_counter() - started_at) * 1000.0,
            type(exc).__name__,
        )
        raise
    _LOGGER.info(
        "model_call success service=fastQA component=rerank_warmup model=%s endpoint=%s auth_mode=%s "
        "status_code=%s elapsed_ms=%.2f",
        str(model or "").strip(),
        endpoint,
        auth_mode,
        getattr(response, "status_code", None),
        (time.perf_counter() - started_at) * 1000.0,
    )


def bootstrap_generation_runtime(runtime: Any) -> None:
    settings = runtime.settings
    close_generation_runtime(runtime, close_shared_pool=False)
    runtime.generation_runtime = None
    runtime.generation_runtime_ready = False
    if not hasattr(runtime, "shared_llm_http_pool"):
        runtime.shared_llm_http_pool = None
    if not hasattr(runtime, "stage2_chat_hot_pool"):
        runtime.stage2_chat_hot_pool = None
    if not hasattr(runtime, "stage2_rerank_hot_pool"):
        runtime.stage2_rerank_hot_pool = None
    if not hasattr(runtime, "stage2_chat_upstream_gate"):
        runtime.stage2_chat_upstream_gate = None
    if not hasattr(runtime, "stage2_rerank_upstream_gate"):
        runtime.stage2_rerank_upstream_gate = None
    _set_stage2_hot_pool_component_defaults(runtime)

    if not bool(getattr(settings, "generation_runtime_enabled", False)):
        _set_shared_llm_pool_status(
            runtime,
            status="skipped",
            detail="shared llm pool skipped because generation runtime is disabled",
            client_owner="private",
        )
        _set_component_status(
            runtime,
            "generation_runtime",
            status="skipped",
            detail="generation runtime disabled by config",
            extra={"enabled": False, "ready": False},
        )
        runtime.stage2_chat_upstream_gate = None
        runtime.stage2_rerank_upstream_gate = None
        return

    try:
        from app.modules.generation_pipeline.generation_driven_rag_facade import GenerationDrivenRAG
        from app.modules.generation_pipeline.runtime_bootstrap import resolve_generation_runtime_inputs

        resolved = resolve_generation_runtime_inputs(
            api_key=None,
            base_url=None,
            model=None,
            config=None,
        )
        if not str(resolved.base_url or "").strip():
            raise ValueError("LLM_BASE_URL is required")

        shared_http_client = None
        stage2_chat_hot_pool = None
        stage2_rerank_hot_pool = None
        if _shared_llm_pool_enabled(runtime):
            try:
                pool = _ensure_shared_llm_http_pool(runtime)
                if pool is not None:
                    shared_http_client = pool.client()
                    _set_shared_llm_pool_status(
                        runtime,
                        status="ok",
                        detail="shared llm pool initialized",
                        client_owner="shared",
                        shared_pool=pool,
                    )
            except Exception as exc:
                runtime.shared_llm_http_pool = None
                shared_http_client = None
                _set_shared_llm_pool_status(
                    runtime,
                    status="degraded",
                    detail="shared llm pool bootstrap failed; falling back to private client",
                    error=str(exc),
                    client_owner="private",
                )
        else:
            _set_shared_llm_pool_status(
                runtime,
                status="skipped",
                detail="shared llm pool disabled by config; using private app-owned client",
                client_owner="private",
            )

        if bool(getattr(settings, "stage2_chat_hot_pool_enabled", False)):
            try:
                transport_config = _shared_llm_pool_config(runtime)
                stage2_chat_hot_pool = ChatHotLanePool(
                    lane_count=int(getattr(settings, "stage2_chat_hot_lane_count", 0) or 0),
                    api_key=str(resolved.api_key or ""),
                    base_url=str(resolved.base_url or ""),
                    connect_timeout_seconds=transport_config.connect_timeout_seconds,
                    read_timeout_seconds=transport_config.read_timeout_seconds,
                    write_timeout_seconds=transport_config.write_timeout_seconds,
                    pool_timeout_seconds=transport_config.pool_timeout_seconds,
                    keepalive_expiry_seconds=float(
                        getattr(settings, "stage2_chat_hot_keepalive_expiry_seconds", transport_config.keepalive_expiry_seconds)
                    ),
                    logger=getattr(runtime, "logger", None),
                    warmup_enabled=bool(getattr(settings, "stage2_chat_warmup_enabled", True)),
                    warm_interval_seconds=float(getattr(settings, "stage2_chat_warm_interval_seconds", 300) or 300),
                    warm_timeout_seconds=float(getattr(settings, "stage2_chat_warm_timeout_seconds", 420.0) or 420.0),
                    warm_jitter_seconds=float(getattr(settings, "stage2_warm_jitter_seconds", 60) or 60),
                    lane_degraded_after_seconds=float(
                        getattr(settings, "stage2_lane_degraded_after_seconds", 900) or 900
                    ),
                    warm_active_start_hour=int(getattr(settings, "stage2_warm_active_start_hour", 0) or 0),
                    warm_active_end_hour=int(getattr(settings, "stage2_warm_active_end_hour", 24) or 24),
                    bootstrap_warm_max_parallel=int(
                        getattr(settings, "stage2_bootstrap_warm_max_parallel", 1) or 1
                    ),
                    bootstrap_warm_jitter_seconds=float(
                        getattr(settings, "stage2_bootstrap_warm_jitter_seconds", 30) or 30
                    ),
                    warm_lane_fn=lambda *, lane, timeout_seconds, reason="manual": _warm_stage2_chat_lane(
                        lane=lane,
                        model=str(resolved.model or ""),
                        timeout_seconds=float(timeout_seconds),
                        reason=reason,
                    ),
                )
                runtime.stage2_chat_hot_pool = stage2_chat_hot_pool
                runtime.stage2_chat_upstream_gate = _build_shared_stage2_upstream_gate(
                    name="chat",
                    configured_limit=int(getattr(settings, "stage2_chat_gate_max_in_flight", 0) or 0),
                    pool_getter=lambda: getattr(runtime, "stage2_chat_hot_pool", None),
                    logger=getattr(runtime, "logger", None),
                )
                _set_stage2_chat_hot_pool_status(
                    runtime,
                    status="pending",
                    detail="stage2 chat hot pool initialized",
                    pool=stage2_chat_hot_pool,
                )
            except Exception as exc:
                runtime.stage2_chat_hot_pool = None
                _set_stage2_chat_hot_pool_status(
                    runtime,
                    status="degraded",
                    detail="stage2 chat hot pool bootstrap failed",
                    error=str(exc),
                )
        else:
            runtime.stage2_chat_hot_pool = None
            runtime.stage2_chat_upstream_gate = None
            _set_stage2_chat_hot_pool_status(
                runtime,
                status="skipped",
                detail="stage2 chat hot pool disabled by config",
            )

        rerank_base_url = _first_env("RERANK_BASE_URL", "QA_RETRIEVAL_RERANK_BASE_URL")
        rerank_model = _first_env("RERANK_MODEL", "QA_RETRIEVAL_RERANK_MODEL")
        if bool(getattr(settings, "stage2_rerank_hot_pool_enabled", False)) and rerank_base_url and rerank_model:
            try:
                rerank_api_key = _first_env("RERANK_API_KEY", "QA_RETRIEVAL_RERANK_API_KEY")
                stage2_rerank_hot_pool = RerankSessionPool(
                    lane_count=int(getattr(settings, "stage2_rerank_hot_lane_count", 0) or 0),
                    logger=getattr(runtime, "logger", None),
                    warmup_enabled=bool(getattr(settings, "stage2_rerank_warmup_enabled", True)),
                    warm_interval_seconds=float(getattr(settings, "stage2_rerank_warm_interval_seconds", 300) or 300),
                    warm_timeout_seconds=float(getattr(settings, "stage2_rerank_warm_timeout_seconds", 420.0) or 420.0),
                    warm_jitter_seconds=float(getattr(settings, "stage2_warm_jitter_seconds", 60) or 60),
                    lane_degraded_after_seconds=float(
                        getattr(settings, "stage2_lane_degraded_after_seconds", 900) or 900
                    ),
                    warm_active_start_hour=int(getattr(settings, "stage2_warm_active_start_hour", 0) or 0),
                    warm_active_end_hour=int(getattr(settings, "stage2_warm_active_end_hour", 24) or 24),
                    bootstrap_warm_max_parallel=int(
                        getattr(settings, "stage2_bootstrap_warm_max_parallel", 1) or 1
                    ),
                    bootstrap_warm_jitter_seconds=float(
                        getattr(settings, "stage2_bootstrap_warm_jitter_seconds", 30) or 30
                    ),
                    warm_lane_fn=lambda *, lane, timeout_seconds, reason="manual": _warm_stage2_rerank_lane(
                        lane=lane,
                        provider="openai_compatible",
                        api_key=rerank_api_key,
                        model=rerank_model,
                        base_url=rerank_base_url,
                        timeout_seconds=float(timeout_seconds),
                        reason=reason,
                    ),
                )
                runtime.stage2_rerank_hot_pool = stage2_rerank_hot_pool
                runtime.stage2_rerank_upstream_gate = _build_shared_stage2_upstream_gate(
                    name="rerank",
                    configured_limit=int(getattr(settings, "stage2_rerank_gate_max_in_flight", 0) or 0),
                    pool_getter=lambda: getattr(runtime, "stage2_rerank_hot_pool", None),
                    logger=getattr(runtime, "logger", None),
                )
                _set_stage2_rerank_hot_pool_status(
                    runtime,
                    status="pending",
                    detail="stage2 rerank hot pool initialized",
                    pool=stage2_rerank_hot_pool,
                )
            except Exception as exc:
                runtime.stage2_rerank_hot_pool = None
                _set_stage2_rerank_hot_pool_status(
                    runtime,
                    status="degraded",
                    detail="stage2 rerank hot pool bootstrap failed",
                    error=str(exc),
                )
        else:
            runtime.stage2_rerank_hot_pool = None
            runtime.stage2_rerank_upstream_gate = None
            _set_stage2_rerank_hot_pool_status(
                runtime,
                status="skipped",
                detail="stage2 rerank hot pool disabled by config",
            )

        runtime.generation_runtime = GenerationDrivenRAG(
            http_client=shared_http_client,
            stage2_chat_hot_pool=stage2_chat_hot_pool,
            rerank_session_pool=stage2_rerank_hot_pool,
            stage2_chat_gate=getattr(runtime, "stage2_chat_upstream_gate", None),
            stage2_rerank_gate=getattr(runtime, "stage2_rerank_upstream_gate", None),
        )
        literature_expert = getattr(runtime.generation_runtime, "literature_expert", None)
        if getattr(literature_expert, "available", True) is False:
            detail = str(getattr(literature_expert, "availability_detail", "") or "literature expert unavailable")
            raise RuntimeError(detail)
        runtime.generation_runtime_ready = True
        _set_component_status(
            runtime,
            "generation_runtime",
            status="ok",
            detail="generation runtime initialized",
            extra={
                "enabled": True,
                "ready": True,
                "model": str(getattr(runtime.generation_runtime, "model", "") or ""),
                "base_url": str(getattr(runtime.generation_runtime, "base_url", "") or ""),
                "client_owner": "shared" if shared_http_client is not None else "private",
                "pool_owner": "app",
                "shared_client_id": (
                    getattr(getattr(runtime, "shared_llm_http_pool", None), "client_id", None)
                    if shared_http_client is not None
                    else None
                ),
                "bootstrap_source": "startup",
                "pid": os.getpid(),
            },
        )
    except Exception as exc:
        close_generation_runtime(runtime, close_shared_pool=False)
        _set_stage2_chat_hot_pool_status(
            runtime,
            status="degraded" if bool(getattr(settings, "stage2_chat_hot_pool_enabled", False)) else "skipped",
            detail="stage2 chat hot pool unavailable after generation runtime bootstrap failure"
            if bool(getattr(settings, "stage2_chat_hot_pool_enabled", False))
            else "stage2 chat hot pool disabled by config",
            error=str(exc) if bool(getattr(settings, "stage2_chat_hot_pool_enabled", False)) else "",
        )
        _set_stage2_rerank_hot_pool_status(
            runtime,
            status="degraded" if bool(getattr(settings, "stage2_rerank_hot_pool_enabled", False)) else "skipped",
            detail="stage2 rerank hot pool unavailable after generation runtime bootstrap failure"
            if bool(getattr(settings, "stage2_rerank_hot_pool_enabled", False))
            else "stage2 rerank hot pool disabled by config",
            error=str(exc) if bool(getattr(settings, "stage2_rerank_hot_pool_enabled", False)) else "",
        )
        if "shared_llm_pool" not in getattr(runtime, "component_status", {}):
            _set_shared_llm_pool_status(
                runtime,
                status="degraded" if _shared_llm_pool_enabled(runtime) else "skipped",
                detail="shared llm pool unavailable during generation runtime bootstrap"
                if _shared_llm_pool_enabled(runtime)
                else "shared llm pool disabled by config; using private app-owned client",
                error=str(exc) if _shared_llm_pool_enabled(runtime) else "",
                client_owner="private",
            )
        _set_component_status(
            runtime,
            "generation_runtime",
            status="degraded",
            detail="generation runtime unavailable",
            error=str(exc),
            extra={"enabled": True, "ready": False},
        )


def bootstrap_graph_kb(runtime: Any) -> None:
    settings = runtime.settings
    runtime.neo4j_client = None
    runtime.graph_kb_ready = False

    if not bool(getattr(settings, "graph_kb_enabled", True)):
        _set_component_status(
            runtime,
            "graph_kb",
            status="skipped",
            detail="graph kb disabled by config",
            extra={"enabled": False, "ready": False},
        )
        return

    neo4j_url = str(getattr(settings, "neo4j_url", "") or "").strip()
    if not neo4j_url:
        _set_component_status(
            runtime,
            "graph_kb",
            status="degraded",
            detail="graph kb enabled but NEO4J_URL is missing",
            extra={"enabled": True, "ready": False},
        )
        return

    if bootstrap_neo4j is None:
        _set_component_status(
            runtime,
            "graph_kb",
            status="degraded",
            detail="graph kb bootstrap unavailable",
            extra={"enabled": True, "ready": False},
        )
        return

    try:
        client = bootstrap_neo4j(
            url=neo4j_url,
            username=str(getattr(settings, "neo4j_username", "neo4j") or "neo4j").strip(),
            password=str(getattr(settings, "neo4j_password", "") or ""),
            logger=getattr(runtime, "logger", None),
        )
        runtime.neo4j_client = client
        available = bool(getattr(client, "available", False))
        degraded = bool(getattr(client, "degraded", False))
        runtime.graph_kb_ready = bool(available and not degraded)
        _set_component_status(
            runtime,
            "graph_kb",
            status="ok" if runtime.graph_kb_ready else "degraded",
            detail="graph kb initialized" if runtime.graph_kb_ready else "graph kb unavailable",
            error=str(getattr(client, "error", "") or ""),
            extra={"enabled": True, "ready": runtime.graph_kb_ready},
        )
    except Exception as exc:
        _set_component_status(
            runtime,
            "graph_kb",
            status="degraded",
            detail="graph kb unavailable",
            error=str(exc),
            extra={"enabled": True, "ready": False},
        )


def close_generation_runtime(runtime: Any, *, close_shared_pool: bool = True) -> None:
    generation_runtime = getattr(runtime, "generation_runtime", None)
    close = getattr(generation_runtime, "close", None)
    if callable(close):
        close()
    stage2_chat_hot_pool = getattr(runtime, "stage2_chat_hot_pool", None)
    close_chat_pool = getattr(stage2_chat_hot_pool, "close", None)
    if callable(close_chat_pool):
        close_chat_pool()
    stage2_rerank_hot_pool = getattr(runtime, "stage2_rerank_hot_pool", None)
    close_rerank_pool = getattr(stage2_rerank_hot_pool, "close", None)
    if callable(close_rerank_pool):
        close_rerank_pool()
    if close_shared_pool:
        shared_llm_http_pool = getattr(runtime, "shared_llm_http_pool", None)
        close_shared_pool_fn = getattr(shared_llm_http_pool, "close", None)
        if callable(close_shared_pool_fn):
            close_shared_pool_fn()
    runtime.generation_runtime = None
    runtime.generation_runtime_ready = False
    runtime.stage2_chat_hot_pool = None
    runtime.stage2_rerank_hot_pool = None
    runtime.stage2_chat_upstream_gate = None
    runtime.stage2_rerank_upstream_gate = None
    if close_shared_pool:
        runtime.shared_llm_http_pool = None


def close_graph_kb(runtime: Any) -> None:
    client = getattr(runtime, "neo4j_client", None)
    close = getattr(client, "close", None)
    if callable(close):
        close()
    runtime.neo4j_client = None
    runtime.graph_kb_ready = False


def close_redis(runtime: Any) -> None:
    client = getattr(runtime, "redis_client", None)
    close = getattr(client, "close", None)
    if callable(close):
        close()
    runtime.redis_client = None
    runtime.redis_service = None
    runtime.redis_bindings = None
