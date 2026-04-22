from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.integrations.llm import FastQASharedUpstreamHttpPool, SharedHttpPoolConfig
from app.integrations.redis import RedisService, build_redis_bindings

try:
    from app.integrations.neo4j.client import bootstrap_neo4j
except Exception:  # pragma: no cover
    bootstrap_neo4j = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


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


def bootstrap_generation_runtime(runtime: Any) -> None:
    settings = runtime.settings
    runtime.generation_runtime = None
    runtime.generation_runtime_ready = False
    if not hasattr(runtime, "shared_llm_http_pool"):
        runtime.shared_llm_http_pool = None

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
        if not str(resolved.api_key or "").strip():
            raise ValueError("OPENAI_API_KEY/DASHSCOPE_API_KEY is required")
        if not str(resolved.base_url or "").strip():
            raise ValueError("OPENAI_BASE_URL/DASHSCOPE_BASE_URL is required")

        shared_http_client = None
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

        runtime.generation_runtime = GenerationDrivenRAG(http_client=shared_http_client)
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

    if not bool(getattr(settings, "graph_kb_enabled", False)):
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
        neo4j_url = str(__import__("os").getenv("NEO4J_URL", "") or "").strip()
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
            username=str(__import__("os").getenv("NEO4J_USERNAME", "neo4j") or "neo4j").strip(),
            password=str(__import__("os").getenv("NEO4J_PASSWORD", "password") or "password"),
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


def close_generation_runtime(runtime: Any) -> None:
    generation_runtime = getattr(runtime, "generation_runtime", None)
    close = getattr(generation_runtime, "close", None)
    if callable(close):
        close()
    shared_llm_http_pool = getattr(runtime, "shared_llm_http_pool", None)
    close_shared_pool = getattr(shared_llm_http_pool, "close", None)
    if callable(close_shared_pool):
        close_shared_pool()
    runtime.generation_runtime = None
    runtime.generation_runtime_ready = False
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
