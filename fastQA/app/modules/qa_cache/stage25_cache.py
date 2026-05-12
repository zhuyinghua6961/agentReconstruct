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


def _stage25_cache_ttl_seconds() -> int:
    raw = str(os.getenv("QA_STAGE25_CACHE_TTL_SECONDS", "43200") or "43200").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 43200


def _normalize_question(question: str) -> str:
    return " ".join(str(question or "").split()).casefold()


def _question_hash(question: str) -> str:
    return hashlib.sha256(_normalize_question(question).encode("utf-8")).hexdigest()


def _runtime_model_name(runtime: Any) -> str:
    raw = str(getattr(runtime, "model", "") or os.getenv("LLM_MODEL", "unknown")).strip()
    return raw or "unknown"


def _normalize_dois(dois: list[str] | list[Any]) -> list[str]:
    normalized = {str(item or "").strip().lower() for item in dois or [] if str(item or "").strip()}
    return sorted(normalized)


def _dois_hash(dois: list[str] | list[Any]) -> str:
    payload = json.dumps(_normalize_dois(dois), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stage25_flags_hash() -> str:
    payload = {
        "enabled": str(os.getenv("QA_STAGE25_MD_EXPANSION_ENABLED", "1") or "1").strip(),
        "max_dois": str(os.getenv("QA_STAGE25_MD_MAX_DOIS", "20") or "20").strip(),
        "chunks_per_doi": str(os.getenv("QA_STAGE25_MD_CHUNKS_PER_DOI", "5") or "5").strip(),
        "global_supplement_enabled": str(os.getenv("QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED", "1") or "1").strip(),
        "global_topk": str(os.getenv("QA_STAGE25_MD_GLOBAL_TOPK", "20") or "20").strip(),
        "global_max_new_dois": str(os.getenv("QA_STAGE25_MD_GLOBAL_MAX_NEW_DOIS", "5") or "5").strip(),
        "global_min_score": str(os.getenv("QA_STAGE25_MD_GLOBAL_MIN_SCORE", "0") or "0").strip(),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _json_signature(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_retrieval_results(retrieval_results: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(retrieval_results, dict):
        return {}
    documents = list(retrieval_results.get("documents") or [])
    metadatas = list(retrieval_results.get("metadatas") or [])
    distances = list(retrieval_results.get("distances") or [])
    claim_to_results = retrieval_results.get("claim_to_results") if isinstance(retrieval_results.get("claim_to_results"), dict) else {}
    return {
        "documents": documents,
        "metadatas": metadatas,
        "distances": distances,
        "claim_to_results": claim_to_results,
        "unique_count": int(retrieval_results.get("unique_count") or len(documents)),
        "total_count": int(retrieval_results.get("total_count") or len(documents)),
    }


def _retrieval_results_hash(retrieval_results: dict[str, Any]) -> str:
    return _json_signature(_normalize_retrieval_results(retrieval_results))


def _normalize_md_chunks_by_doi(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for doi, chunks in payload.items():
        doi_key = str(doi or "").strip()
        if not doi_key:
            continue
        if not isinstance(chunks, list):
            continue
        normalized_chunks: list[dict[str, Any]] = []
        for chunk in chunks:
            if isinstance(chunk, dict):
                normalized_chunks.append(dict(chunk))
        normalized[doi_key] = normalized_chunks
    return normalized


def _normalize_stage25_stats(payload: Any) -> dict[str, Any]:
    stats = dict(payload) if isinstance(payload, dict) else {}
    stats["hit_doi_count"] = int(stats.get("hit_doi_count") or 0)
    stats["total_md_chunks"] = int(stats.get("total_md_chunks") or 0)
    stats["fallback_reason"] = str(stats.get("fallback_reason") or "")
    return stats


def build_stage25_cache_key(
    *,
    redis_service: RedisService,
    runtime: Any,
    question: str,
    retrieval_results: dict[str, Any],
    dois: list[str] | list[Any],
    route_hint: str = "kb_qa",
) -> str:
    return redis_service.key_factory.cache(
        "stage25",
        _qa_cache_epoch(),
        _kb_data_epoch(),
        route_hint,
        _runtime_model_name(runtime),
        _stage25_flags_hash(),
        _question_hash(question),
        _dois_hash(dois),
        _retrieval_results_hash(retrieval_results),
    )


def build_stage25_lock_key(
    *,
    redis_service: RedisService,
    runtime: Any,
    question: str,
    retrieval_results: dict[str, Any],
    dois: list[str] | list[Any],
    route_hint: str = "kb_qa",
) -> str:
    return redis_service.key_factory.lock(
        "stage25",
        _qa_cache_epoch(),
        _kb_data_epoch(),
        route_hint,
        _runtime_model_name(runtime),
        _stage25_flags_hash(),
        _question_hash(question),
        _dois_hash(dois),
        _retrieval_results_hash(retrieval_results),
    )


def get_cached_stage25_result(
    *,
    redis_service: RedisService | None,
    runtime: Any,
    question: str,
    retrieval_results: dict[str, Any],
    dois: list[str] | list[Any],
    route_hint: str = "kb_qa",
) -> dict[str, Any] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(
        build_stage25_cache_key(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            retrieval_results=retrieval_results,
            dois=dois,
            route_hint=route_hint,
        ),
        default=None,
    )
    if not isinstance(payload, dict):
        return None
    return {
        "enabled": bool(payload.get("enabled")),
        "applied": bool(payload.get("applied")),
        "md_chunks_by_doi": _normalize_md_chunks_by_doi(payload.get("md_chunks_by_doi")),
        "stats": _normalize_stage25_stats(payload.get("stats")),
    }


def cache_stage25_result(
    *,
    redis_service: RedisService | None,
    runtime: Any,
    question: str,
    retrieval_results: dict[str, Any],
    dois: list[str] | list[Any],
    stage25_result: dict[str, Any],
    route_hint: str = "kb_qa",
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    if not isinstance(stage25_result, dict):
        return False
    payload = {
        "enabled": bool(stage25_result.get("enabled")),
        "applied": bool(stage25_result.get("applied")),
        "md_chunks_by_doi": _normalize_md_chunks_by_doi(stage25_result.get("md_chunks_by_doi")),
        "stats": _normalize_stage25_stats(stage25_result.get("stats")),
    }
    ok = redis_service.set_json(
        build_stage25_cache_key(
            redis_service=redis_service,
            runtime=runtime,
            question=question,
            retrieval_results=retrieval_results,
            dois=dois,
            route_hint=route_hint,
        ),
        payload,
        ttl_seconds=_stage25_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("stage25", "cache_write")
    return ok
