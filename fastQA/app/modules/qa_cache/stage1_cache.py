from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from app.integrations.redis import RedisService
from app.modules.qa_cache.metrics import increment_cache_metric


def _qa_cache_epoch() -> str:
    return str(os.getenv("QA_CACHE_EPOCH", "0") or "0").strip() or "0"


def _stage1_cache_ttl_seconds() -> int:
    raw = str(os.getenv("QA_STAGE1_CACHE_TTL_SECONDS", "3600") or "3600").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 3600


def _normalize_question(question: str) -> str:
    return " ".join(str(question or "").split()).casefold()


def _question_hash(question: str) -> str:
    return hashlib.sha256(_normalize_question(question).encode("utf-8")).hexdigest()


def _canonicalize_context(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize_context(value[key]) for key in sorted(value.keys(), key=lambda item: str(item))}
    if isinstance(value, list):
        return [_canonicalize_context(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize_context(item) for item in value]
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_prompt_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _stage1_prompt_relevant_context(conversation_context: dict[str, Any] | None) -> dict[str, Any]:
    source = conversation_context or {}
    summary = source.get("summary_for_llm") if isinstance(source, dict) else {}
    turns = source.get("recent_turns_for_llm") if isinstance(source, dict) else []

    normalized_turns: list[dict[str, Any]] = []
    if isinstance(turns, list):
        for item in turns:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = _normalize_prompt_text(item.get("content"))
            if role not in {"user", "assistant"} or not content:
                continue
            normalized_turns.append({"role": role, "content": content})

    normalized_summary: dict[str, Any] = {}
    if isinstance(summary, dict):
        short_summary = _normalize_prompt_text(summary.get("short_summary"))
        if short_summary:
            normalized_summary["short_summary"] = short_summary
        open_threads = [str(item).strip() for item in list(summary.get("open_threads") or [])]
        open_threads = [item for item in open_threads if item]
        if open_threads:
            normalized_summary["open_threads"] = open_threads
        memory_facts = [str(item).strip() for item in list(summary.get("memory_facts") or [])]
        memory_facts = [item for item in memory_facts if item]
        if memory_facts:
            normalized_summary["memory_facts"] = memory_facts

    return {
        "recent_turns_for_llm": normalized_turns,
        "summary_for_llm": normalized_summary,
    }


def _conversation_context_hash(conversation_context: dict[str, Any] | None) -> str:
    canonical = _canonicalize_context(_stage1_prompt_relevant_context(conversation_context))
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _runtime_model_name(runtime: Any) -> str:
    raw = str(getattr(runtime, "model", "") or os.getenv("DASHSCOPE_MODEL", "unknown")).strip()
    return raw or "unknown"


def _runtime_prompt_version(runtime: Any) -> str:
    configured = str(os.getenv("QA_STAGE1_PROMPT_VERSION", "") or "").strip()
    if configured:
        return configured
    prompt = str(getattr(runtime, "stage1_prompt", "") or "").strip()
    context = ""
    get_context = getattr(runtime, "_get_vector_db_context_for_prompt", None)
    if callable(get_context):
        try:
            context = str(get_context() or "")
        except Exception:
            context = ""
    source = f"{prompt}\n{context}".strip()
    if not source:
        return "default"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def build_stage1_cache_key(
    *,
    redis_service: RedisService,
    runtime: Any,
    question: str,
    conversation_context: dict[str, Any] | None = None,
    route_hint: str = "kb_qa",
) -> str:
    return redis_service.key_factory.cache(
        "qa",
        "stage1",
        _qa_cache_epoch(),
        route_hint,
        _runtime_model_name(runtime),
        _runtime_prompt_version(runtime),
        _question_hash(question),
        _conversation_context_hash(conversation_context),
    )


def build_stage1_lock_key(
    *,
    redis_service: RedisService,
    runtime: Any,
    question: str,
    conversation_context: dict[str, Any] | None = None,
    route_hint: str = "kb_qa",
) -> str:
    return redis_service.key_factory.lock(
        "qa",
        "stage1",
        _qa_cache_epoch(),
        route_hint,
        _runtime_model_name(runtime),
        _runtime_prompt_version(runtime),
        _question_hash(question),
        _conversation_context_hash(conversation_context),
    )


def get_cached_stage1_result(
    *,
    redis_service: RedisService | None,
    runtime: Any,
    question: str,
    conversation_context: dict[str, Any] | None = None,
    route_hint: str = "kb_qa",
) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(
        build_stage1_cache_key(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            conversation_context=conversation_context,
            route_hint=route_hint,
        ),
        default=None,
    )
    if not isinstance(payload, dict):
        return None
    if payload.get("success") is not True:
        return None
    claims = payload.get("retrieval_claims") or []
    if not isinstance(claims, list):
        claims = []
    result = dict(payload)
    result["success"] = True
    result["deep_answer"] = str(payload.get("deep_answer") or "")
    result["retrieval_claims"] = claims
    return result


def cache_stage1_result(
    *,
    redis_service: RedisService | None,
    runtime: Any,
    question: str,
    stage1_result: dict[str, Any],
    conversation_context: dict[str, Any] | None = None,
    route_hint: str = "kb_qa",
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    if not isinstance(stage1_result, dict) or stage1_result.get("success") is not True:
        return False
    payload = dict(stage1_result)
    claims = payload.get("retrieval_claims") or []
    if not isinstance(claims, list):
        claims = []
    payload["success"] = True
    payload["deep_answer"] = str(payload.get("deep_answer") or "")
    payload["retrieval_claims"] = claims
    ok = redis_service.set_json(
        build_stage1_cache_key(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            conversation_context=conversation_context,
            route_hint=route_hint,
        ),
        payload,
        ttl_seconds=_stage1_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("stage1", "cache_write")
    return ok

