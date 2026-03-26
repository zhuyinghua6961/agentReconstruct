from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from app.integrations.redis import RedisService
from app.modules.qa_cache.metrics import increment_cache_metric


def _qa_cache_epoch() -> str:
    return str(os.getenv("QA_CACHE_EPOCH", "0") or "0").strip() or "0"


def _papers_epoch() -> str:
    raw = str(os.getenv("PAPERS_DATA_EPOCH", os.getenv("KB_DATA_EPOCH", "0")) or "0").strip()
    return raw or "0"


def _stage3_cache_ttl_seconds() -> int:
    raw = str(os.getenv("QA_STAGE3_CACHE_TTL_SECONDS", "1800") or "1800").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 1800


def _normalize_dois(dois: list[str] | list[Any]) -> list[str]:
    normalized = {str(item or "").strip().lower() for item in dois or [] if str(item or "").strip()}
    return sorted(normalized)


def _dois_hash(dois: list[str] | list[Any]) -> str:
    payload = json.dumps(_normalize_dois(dois), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_pdf_chunks(payload: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for doi, chunks in payload.items():
        doi_key = str(doi or "").strip()
        if not doi_key or not isinstance(chunks, list):
            continue
        normalized_chunks: list[dict[str, Any]] = []
        for chunk in chunks:
            if isinstance(chunk, dict):
                normalized_chunks.append(dict(chunk))
        normalized[doi_key] = normalized_chunks
    return normalized


def build_stage3_cache_key(
    *,
    redis_service: RedisService,
    dois: list[str] | list[Any],
    max_chunks_per_doi: int,
    route_hint: str = "kb_qa",
) -> str:
    return redis_service.key_factory.cache(
        "stage3",
        _qa_cache_epoch(),
        _papers_epoch(),
        route_hint,
        int(max_chunks_per_doi),
        _dois_hash(dois),
    )


def build_stage3_lock_key(
    *,
    redis_service: RedisService,
    dois: list[str] | list[Any],
    max_chunks_per_doi: int,
    route_hint: str = "kb_qa",
) -> str:
    return redis_service.key_factory.lock(
        "stage3",
        _qa_cache_epoch(),
        _papers_epoch(),
        route_hint,
        int(max_chunks_per_doi),
        _dois_hash(dois),
    )


def get_cached_stage3_result(
    *,
    redis_service: RedisService | None,
    dois: list[str] | list[Any],
    max_chunks_per_doi: int,
    route_hint: str = "kb_qa",
) -> dict[str, list[dict[str, Any]]] | None:
    if redis_service is None or not redis_service.available:
        return None
    payload = redis_service.get_json(
        build_stage3_cache_key(
            redis_service=redis_service,
            dois=dois,
            max_chunks_per_doi=max_chunks_per_doi,
            route_hint=route_hint,
        ),
        default=None,
    )
    if not isinstance(payload, dict):
        return None
    return _normalize_pdf_chunks(payload)


def cache_stage3_result(
    *,
    redis_service: RedisService | None,
    dois: list[str] | list[Any],
    max_chunks_per_doi: int,
    stage3_result: dict[str, list[dict[str, Any]]],
    route_hint: str = "kb_qa",
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    if not isinstance(stage3_result, dict):
        return False
    payload = _normalize_pdf_chunks(stage3_result)
    ok = redis_service.set_json(
        build_stage3_cache_key(
            redis_service=redis_service,
            dois=dois,
            max_chunks_per_doi=max_chunks_per_doi,
            route_hint=route_hint,
        ),
        payload,
        ttl_seconds=_stage3_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("stage3", "cache_write")
    return ok
