from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from app.integrations.redis import RedisService
from app.modules.qa_cache.metrics import increment_cache_metric


def _qa_cache_epoch() -> str:
    return str(os.getenv("QA_CACHE_EPOCH", "0") or "0").strip() or "0"


def _kb_data_epoch() -> str:
    return str(os.getenv("KB_DATA_EPOCH", "0") or "0").strip() or "0"


def _stage2_retrieval_version() -> str:
    return str(os.getenv("QA_STAGE2_RETRIEVAL_VERSION", "1") or "1").strip() or "1"


def _stage2_cache_ttl_seconds() -> int:
    raw = str(os.getenv("QA_STAGE2_CACHE_TTL_SECONDS", "43200") or "43200").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 43200


def _normalize_question(question: str) -> str:
    return " ".join(str(question or "").split()).casefold()


def _question_hash(question: str) -> str:
    return hashlib.sha256(_normalize_question(question).encode("utf-8")).hexdigest()


def _normalize_claims(retrieval_claims: list[dict[str, Any]] | list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in retrieval_claims or []:
        if isinstance(item, dict):
            normalized.append(
                {
                    "claim": str(item.get("claim") or "").strip(),
                    "keywords": [str(value).strip() for value in list(item.get("keywords") or []) if str(value or "").strip()],
                    "preferred_sections": [
                        str(value).strip()
                        for value in list(item.get("preferred_sections") or item.get("preferred") or [])
                        if str(value or "").strip()
                    ],
                    "filters": item.get("filters") if isinstance(item.get("filters"), dict) else {},
                }
            )
        else:
            normalized.append(
                {
                    "claim": str(item or "").strip(),
                    "keywords": [],
                    "preferred_sections": [],
                    "filters": {},
                }
            )
    return normalized


def _claims_hash(retrieval_claims: list[dict[str, Any]] | list[Any]) -> str:
    payload = json.dumps(_normalize_claims(retrieval_claims), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _runtime_model_name(runtime: Any) -> str:
    raw = str(getattr(runtime, "model", "") or os.getenv("DASHSCOPE_MODEL", "unknown")).strip()
    return raw or "unknown"


def _flags_hash() -> str:
    payload = {
        "force_keyword_injection": str(os.getenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "1") or "1").strip(),
        "entity_lock_enabled": str(os.getenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "1") or "1").strip(),
        "use_rerank": str(os.getenv("QA_RETRIEVAL_RERANK_ENABLED", "1") or "1").strip(),
        "rerank_candidates": str(os.getenv("QA_RETRIEVAL_RERANK_CANDIDATES", "50") or "50").strip(),
        "rerank_provider": str(os.getenv("QA_RETRIEVAL_RERANK_PROVIDER", "dashscope") or "dashscope").strip(),
        "rerank_model": str(os.getenv("QA_RETRIEVAL_RERANK_MODEL", "qwen3-vl-rerank") or "qwen3-vl-rerank").strip(),
        "query_expansion_enabled": str(os.getenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "0") or "0").strip(),
        "query_expansion_model": str(os.getenv("QUERY_EXPANSION_MODEL", "qwen3-8b") or "qwen3-8b").strip(),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_stage2_cache_key(
    *,
    redis_service: RedisService,
    runtime: Any,
    question: str,
    retrieval_claims: list[dict[str, Any]] | list[Any],
    n_results_per_claim: int,
    route_hint: str = "kb_qa",
) -> str:
    return redis_service.key_factory.cache(
        "stage2",
        _qa_cache_epoch(),
        _kb_data_epoch(),
        _stage2_retrieval_version(),
        route_hint,
        _runtime_model_name(runtime),
        int(n_results_per_claim),
        _flags_hash(),
        _question_hash(question),
        _claims_hash(retrieval_claims),
    )


def build_stage2_lock_key(
    *,
    redis_service: RedisService,
    runtime: Any,
    question: str,
    retrieval_claims: list[dict[str, Any]] | list[Any],
    n_results_per_claim: int,
    route_hint: str = "kb_qa",
) -> str:
    return redis_service.key_factory.lock(
        "stage2",
        _qa_cache_epoch(),
        _kb_data_epoch(),
        _stage2_retrieval_version(),
        route_hint,
        _runtime_model_name(runtime),
        int(n_results_per_claim),
        _flags_hash(),
        _question_hash(question),
        _claims_hash(retrieval_claims),
    )


def get_cached_stage2_result(
    *,
    redis_service: RedisService | None,
    runtime: Any,
    question: str,
    retrieval_claims: list[dict[str, Any]] | list[Any],
    n_results_per_claim: int,
    route_hint: str = "kb_qa",
) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(
        build_stage2_cache_key(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            retrieval_claims=retrieval_claims,
            n_results_per_claim=n_results_per_claim,
            route_hint=route_hint,
        ),
        default=None,
    )
    if not isinstance(payload, dict):
        return None
    if payload.get("success") is not True:
        return None
    result = dict(payload)
    result["success"] = True
    result["documents"] = list(payload.get("documents") or [])
    result["metadatas"] = list(payload.get("metadatas") or [])
    result["distances"] = list(payload.get("distances") or [])
    result["claim_to_results"] = payload.get("claim_to_results") if isinstance(payload.get("claim_to_results"), dict) else {}
    result["unique_count"] = int(payload.get("unique_count") or len(result["documents"]))
    result["total_count"] = int(payload.get("total_count") or len(result["documents"]))
    return result


def cache_stage2_result(
    *,
    redis_service: RedisService | None,
    runtime: Any,
    question: str,
    retrieval_claims: list[dict[str, Any]] | list[Any],
    n_results_per_claim: int,
    stage2_result: dict[str, Any],
    route_hint: str = "kb_qa",
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    if not isinstance(stage2_result, dict) or stage2_result.get("success") is not True:
        return False
    payload = dict(stage2_result)
    payload["success"] = True
    payload["documents"] = list(stage2_result.get("documents") or [])
    payload["metadatas"] = list(stage2_result.get("metadatas") or [])
    payload["distances"] = list(stage2_result.get("distances") or [])
    payload["claim_to_results"] = (
        stage2_result.get("claim_to_results") if isinstance(stage2_result.get("claim_to_results"), dict) else {}
    )
    payload["unique_count"] = int(stage2_result.get("unique_count") or len(payload["documents"]))
    payload["total_count"] = int(stage2_result.get("total_count") or len(payload["documents"]))
    ok = redis_service.set_json(
        build_stage2_cache_key(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            retrieval_claims=retrieval_claims,
            n_results_per_claim=n_results_per_claim,
            route_hint=route_hint,
        ),
        payload,
        ttl_seconds=_stage2_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("stage2", "cache_write")
    return ok
