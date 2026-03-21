from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.integrations.redis import RedisService, build_redis_bindings


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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


def bootstrap_generation_runtime(runtime: Any) -> None:
    settings = runtime.settings
    runtime.generation_runtime = None
    runtime.generation_runtime_ready = False

    if not bool(getattr(settings, "generation_runtime_enabled", False)):
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

        runtime.generation_runtime = GenerationDrivenRAG()
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
            },
        )
    except Exception as exc:
        _set_component_status(
            runtime,
            "generation_runtime",
            status="degraded",
            detail="generation runtime unavailable",
            error=str(exc),
            extra={"enabled": True, "ready": False},
        )


def close_generation_runtime(runtime: Any) -> None:
    generation_runtime = getattr(runtime, "generation_runtime", None)
    close = getattr(generation_runtime, "close", None)
    if callable(close):
        close()
    runtime.generation_runtime = None
    runtime.generation_runtime_ready = False


def close_redis(runtime: Any) -> None:
    client = getattr(runtime, "redis_client", None)
    close = getattr(client, "close", None)
    if callable(close):
        close()
    runtime.redis_client = None
    runtime.redis_service = None
    runtime.redis_bindings = None
