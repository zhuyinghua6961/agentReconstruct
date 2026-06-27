from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from server.patent.original_minio_loader import PatentOriginalMinioLoader
from server.patent.retrieval_models import PatentCatalogRecord
from server.patent.retrieval_service import (
    PatentRetrievalService,
    _extract_identifier,
    _normalize_question,
)
from server.patent.browse_query import resolve_query_type
from server.patent.browse_rerank import apply_patent_browse_rerank, patent_browse_rerank_candidates
from server.patent.browse_search_cache import build_patent_search_cache_key
from server.patent.runtime import PatentRuntime

QueryType = Literal["auto", "patent_id", "topic"]
SourcesType = Literal["abstract", "chunk", "both"]

_LOGGER = logging.getLogger("patent.browse_search")


def _preview_query(query: str, *, max_chars: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(query or "").strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _preview_cache_key(cache_key: str) -> str:
    parts = str(cache_key or "").split(":")
    if len(parts) >= 2:
        return ":".join(parts[:2] + parts[-2:])
    return str(cache_key or "")


def _distance_to_score(score_value: Any, distance_value: Any) -> float | None:
    try:
        if score_value is not None:
            return float(score_value)
        if distance_value is not None:
            return 1.0 / (1.0 + max(float(distance_value), 0.0))
    except Exception:
        return None
    return None


@dataclass
class _PatentHitAgg:
    canonical_patent_id: str
    match_score: float
    match_source: str
    snippet: str


class PatentBrowseSearchService:
    def __init__(
        self,
        *,
        runtime: PatentRuntime | None,
        minio_loader: PatentOriginalMinioLoader | None = None,
        cache_getter: Any | None = None,
        cache_setter: Any | None = None,
    ) -> None:
        self._runtime = runtime
        self._minio_loader = minio_loader
        self._cache_get = cache_getter
        self._cache_set = cache_setter

    def search(
        self,
        *,
        query: str,
        query_type: str = "auto",
        sources: str = "both",
        limit: int = 20,
    ) -> tuple[dict[str, Any], int]:
        started_at = time.perf_counter()
        clean_query = str(query or "").strip()
        if not clean_query:
            _LOGGER.info("patent_search rejected reason=empty_query")
            return (
                {
                    "items": [],
                    "count": 0,
                    "error": "缺少查询参数",
                },
                200,
            )

        resolved_type = resolve_query_type(query=clean_query, query_type=query_type)
        normalized_sources = self._normalize_sources(sources)
        bounded_limit = max(1, min(int(limit or 20), 50))
        _LOGGER.info(
            "patent_search start query=%r query_type=%s resolved_type=%s sources=%s limit=%s",
            _preview_query(clean_query),
            query_type,
            resolved_type,
            normalized_sources,
            bounded_limit,
        )

        if callable(self._cache_get):
            cache_key = build_patent_search_cache_key(
                query=clean_query,
                query_type=resolved_type,
                sources=normalized_sources,
                limit=bounded_limit,
            )
            cached = self._cache_get(cache_key)
            if isinstance(cached, dict):
                payload = dict(cached)
                cache_meta = dict(payload.get("cache_meta") or {})
                payload["cache_meta"] = {"hit": True, "cached_at": cache_meta.get("cached_at")}
                _LOGGER.info(
                    "patent_search cache_hit cache_key=%s count=%s backend=%s elapsed_ms=%.2f",
                    _preview_cache_key(cache_key),
                    payload.get("count", 0),
                    payload.get("retrieval_backend", ""),
                    (time.perf_counter() - started_at) * 1000.0,
                )
                return payload, 200
            _LOGGER.info(
                "patent_search cache_miss cache_key=%s",
                _preview_cache_key(cache_key),
            )

        retrieval = self._retrieval_service()
        if retrieval is None:
            _LOGGER.error(
                "patent_search failed stage=runtime query=%r resolved_type=%s code=RETRIEVAL_RUNTIME_UNAVAILABLE",
                _preview_query(clean_query),
                resolved_type,
            )
            return (
                {
                    "items": [],
                    "count": 0,
                    "query": clean_query,
                    "query_type_detected": resolved_type,
                    "code": "RETRIEVAL_RUNTIME_UNAVAILABLE",
                    "error": "专利检索运行时不可用",
                },
                200,
            )

        search_started_at = time.perf_counter()
        if resolved_type == "patent_id":
            payload = self._search_by_patent_id(retrieval=retrieval, query=clean_query, limit=bounded_limit)
            payload["rerank"] = {"enabled": False, "applied": False, "fallback": False}
        else:
            payload = self._search_by_topic(
                retrieval=retrieval,
                query=clean_query,
                sources=normalized_sources,
                limit=bounded_limit,
            )

        payload["query"] = clean_query
        payload["query_type_detected"] = resolved_type
        payload["sources"] = self._sources_used(normalized_sources, payload.get("retrieval_backend", ""))

        if payload.get("code") in {"EMBEDDING_UNAVAILABLE", "RETRIEVAL_RUNTIME_UNAVAILABLE"}:
            if not str(payload.get("error") or "").strip():
                from server.utils.user_errors import user_message_for_code

                payload["error"] = user_message_for_code(str(payload.get("code") or ""))
            _LOGGER.error(
                "patent_search failed stage=retrieval query=%r resolved_type=%s code=%s backend=%s elapsed_ms=%.2f",
                _preview_query(clean_query),
                resolved_type,
                payload.get("code"),
                payload.get("retrieval_backend", ""),
                (time.perf_counter() - search_started_at) * 1000.0,
            )
            return payload, 200

        if callable(self._cache_set):
            cache_key = build_patent_search_cache_key(
                query=clean_query,
                query_type=resolved_type,
                sources=normalized_sources,
                limit=bounded_limit,
            )
            to_cache = dict(payload)
            to_cache.pop("cache_meta", None)
            self._cache_set(cache_key, to_cache)

        payload["cache_meta"] = {"hit": False}
        rerank_meta = dict(payload.get("rerank") or {})
        _LOGGER.info(
            "patent_search done query=%r resolved_type=%s backend=%s count=%s "
            "rerank_enabled=%s rerank_applied=%s rerank_fallback=%s cache_hit=false elapsed_ms=%.2f",
            _preview_query(clean_query),
            resolved_type,
            payload.get("retrieval_backend", ""),
            payload.get("count", 0),
            rerank_meta.get("enabled"),
            rerank_meta.get("applied"),
            rerank_meta.get("fallback"),
            (time.perf_counter() - started_at) * 1000.0,
        )
        return payload, 200

    def _retrieval_service(self) -> PatentRetrievalService | None:
        runtime = self._runtime
        if runtime is None:
            return None
        service = getattr(runtime, "retrieval_service", None)
        return service if service is not None else None

    def _normalize_sources(self, sources: str) -> SourcesType:
        value = str(sources or "both").strip().lower()
        if value in {"abstract", "chunk", "both"}:
            return value  # type: ignore[return-value]
        return "both"

    def _sources_used(self, sources: SourcesType, backend: str) -> list[str]:
        if backend == "exact_id":
            return ["patent_catalog"]
        if backend == "metadata_lexical":
            return ["patent_catalog_lexical"]
        used: list[str] = []
        if sources in {"abstract", "both"}:
            used.append("patent_abstracts")
        if sources in {"chunk", "both"}:
            used.append("patent_chunks")
        return used

    def _search_by_patent_id(
        self,
        *,
        retrieval: PatentRetrievalService,
        query: str,
        limit: int,
    ) -> dict[str, Any]:
        identifier = _extract_identifier(query) or str(query or "").strip().upper()
        canonical_patent_id = retrieval._resolve_identifier(identifier)  # noqa: SLF001
        if not canonical_patent_id:
            _LOGGER.info(
                "patent_search exact_id miss query=%r identifier=%r reason=unresolved_identifier",
                _preview_query(query),
                identifier,
            )
            return {
                "items": [],
                "count": 0,
                "retrieval_backend": "exact_id",
            }
        record = retrieval._ensure_catalog_record(canonical_patent_id)  # noqa: SLF001
        if record is None:
            _LOGGER.warning(
                "patent_search exact_id miss query=%r canonical_patent_id=%s reason=catalog_record_missing",
                _preview_query(query),
                canonical_patent_id,
            )
            return {
                "items": [],
                "count": 0,
                "retrieval_backend": "exact_id",
            }
        item = self._build_item(
            record=record,
            match_score=1.0,
            match_source="patent_catalog",
            snippet=str(record.abstract_text or "")[:400],
        )
        return {
            "items": [item][:limit],
            "count": 1,
            "retrieval_backend": "exact_id",
        }

    def _search_by_topic(
        self,
        *,
        retrieval: PatentRetrievalService,
        query: str,
        sources: SourcesType,
        limit: int,
    ) -> dict[str, Any]:
        aggregated: dict[str, _PatentHitAgg] = {}
        backend = "vector_hybrid"
        vector_enabled = retrieval._vector_search_enabled()  # noqa: SLF001
        _LOGGER.info(
            "patent_search topic stage=init query=%r sources=%s vector_enabled=%s limit=%s",
            _preview_query(query),
            sources,
            vector_enabled,
            limit,
        )

        if vector_enabled:
            if sources in {"abstract", "both"}:
                abstract_hits = retrieval._run_abstract_vector_search(query, max(limit * 3, 25))  # noqa: SLF001
                self._merge_abstract_hits(aggregated, abstract_hits)
                _LOGGER.info(
                    "patent_search topic stage=abstract_vector query=%r raw_hits=%s aggregated=%s",
                    _preview_query(query),
                    len(list(abstract_hits or [])),
                    len(aggregated),
                )
            if sources in {"chunk", "both"}:
                candidate_ids = list(aggregated.keys())
                if not candidate_ids and sources == "chunk":
                    warm_hits = retrieval._run_abstract_vector_search(query, max(limit * 3, 25))  # noqa: SLF001
                    candidate_ids = [
                        retrieval._normalize_patent_id(item.get("patent_id") or item.get("canonical_patent_id"))  # noqa: SLF001
                        for item in warm_hits
                    ]
                    candidate_ids = [item for item in candidate_ids if item]
                    _LOGGER.info(
                        "patent_search topic stage=chunk_warm query=%r warm_hits=%s warm_ids=%s",
                        _preview_query(query),
                        len(list(warm_hits or [])),
                        len(candidate_ids),
                    )
                chunk_hits = retrieval._run_chunk_vector_search(  # noqa: SLF001
                    query,
                    candidate_ids or None,
                    max(limit * 4, 30),
                )
                self._merge_chunk_hits(aggregated, chunk_hits)
                _LOGGER.info(
                    "patent_search topic stage=chunk_vector query=%r raw_hits=%s aggregated=%s candidate_ids=%s",
                    _preview_query(query),
                    len(list(chunk_hits or [])),
                    len(aggregated),
                    len(candidate_ids),
                )
        else:
            backend = "metadata_lexical"
            _LOGGER.warning(
                "patent_search topic fallback=metadata_lexical query=%r reason=vector_search_disabled",
                _preview_query(query),
            )
            return self._search_lexical(retrieval=retrieval, query=query, limit=limit, backend=backend)

        if not aggregated:
            _LOGGER.warning(
                "patent_search topic fallback=metadata_lexical query=%r reason=no_vector_hits",
                _preview_query(query),
            )
            return self._search_lexical(retrieval=retrieval, query=query, limit=limit, backend="metadata_lexical")

        candidate_limit = patent_browse_rerank_candidates(limit=limit)
        ordered = sorted(aggregated.values(), key=lambda item: item.match_score, reverse=True)[:candidate_limit]
        items = []
        skipped_catalog = 0
        for hit in ordered:
            record = retrieval._ensure_catalog_record(hit.canonical_patent_id)  # noqa: SLF001
            if record is None:
                skipped_catalog += 1
                _LOGGER.warning(
                    "patent_search topic enrich_skip patent_id=%s reason=catalog_record_missing",
                    hit.canonical_patent_id,
                )
                continue
            items.append(
                self._build_item(
                    record=record,
                    match_score=hit.match_score,
                    match_source=hit.match_source,
                    snippet=hit.snippet,
                )
            )
        _LOGGER.info(
            "patent_search topic stage=enrich query=%r candidate_limit=%s enriched=%s skipped_catalog=%s",
            _preview_query(query),
            candidate_limit,
            len(items),
            skipped_catalog,
        )
        items, rerank_meta = apply_patent_browse_rerank(
            query=query,
            items=items,
            limit=limit,
            logger=_LOGGER,
            context="topic_vector",
        )
        return {
            "items": items,
            "count": len(items),
            "retrieval_backend": backend,
            "rerank": rerank_meta,
        }

    def _search_lexical(
        self,
        *,
        retrieval: PatentRetrievalService,
        query: str,
        limit: int,
        backend: str,
    ) -> dict[str, Any]:
        normalized = _normalize_question(query)
        candidates = retrieval._metadata_candidates(normalized)  # noqa: SLF001
        _LOGGER.info(
            "patent_search lexical stage=candidates query=%r backend=%s candidate_count=%s",
            _preview_query(query),
            backend,
            len(list(candidates or [])),
        )
        items = []
        candidate_limit = patent_browse_rerank_candidates(limit=limit)
        for match, score in candidates[:candidate_limit]:
            items.append(
                self._build_item(
                    record=match.record,
                    match_score=float(score),
                    match_source="patent_catalog_lexical",
                    snippet=str(match.snippet_text or match.record.abstract_text or "")[:400],
                )
            )
        items, rerank_meta = apply_patent_browse_rerank(
            query=query,
            items=items,
            limit=limit,
            logger=_LOGGER,
            context=f"lexical:{backend}",
        )
        return {
            "items": items,
            "count": len(items),
            "retrieval_backend": backend,
            "rerank": rerank_meta,
        }

    def _merge_abstract_hits(self, aggregated: dict[str, _PatentHitAgg], hits: list[dict[str, Any]]) -> None:
        for hit in list(hits or []):
            patent_id = str(hit.get("canonical_patent_id") or hit.get("patent_id") or hit.get("json_stem") or "").strip().upper()
            if not patent_id:
                continue
            score = _distance_to_score(hit.get("abstract_score"), hit.get("distance"))
            if score is None:
                continue
            snippet = str(hit.get("document") or "").strip()[:400]
            current = aggregated.get(patent_id)
            if current is None or score > current.match_score:
                aggregated[patent_id] = _PatentHitAgg(
                    canonical_patent_id=patent_id,
                    match_score=score,
                    match_source="patent_abstracts",
                    snippet=snippet,
                )

    def _merge_chunk_hits(self, aggregated: dict[str, _PatentHitAgg], hits: list[dict[str, Any]]) -> None:
        for hit in list(hits or []):
            patent_id = str(hit.get("canonical_patent_id") or hit.get("patent_id") or hit.get("json_stem") or "").strip().upper()
            if not patent_id:
                continue
            score = _distance_to_score(hit.get("chunk_score"), hit.get("distance"))
            if score is None:
                continue
            snippet = str(hit.get("document") or "").strip()[:400]
            current = aggregated.get(patent_id)
            if current is None or score > current.match_score:
                aggregated[patent_id] = _PatentHitAgg(
                    canonical_patent_id=patent_id,
                    match_score=score,
                    match_source="patent_chunks",
                    snippet=snippet,
                )
            elif current is not None and score == current.match_score and not current.snippet:
                current.snippet = snippet

    def _build_item(
        self,
        *,
        record: PatentCatalogRecord,
        match_score: float,
        match_source: str,
        snippet: str,
    ) -> dict[str, Any]:
        patent_id = str(record.canonical_patent_id or record.publication_number or "").strip().upper()
        original_available = bool(record.original_available)
        has_pdf = original_available
        if self._minio_loader is not None and patent_id:
            manifest = self._minio_loader.load_manifest(patent_id)
            if isinstance(manifest, dict):
                availability = dict(manifest.get("availability") or {})
                if "fulltext_pdf" in availability:
                    has_pdf = bool(availability.get("fulltext_pdf"))
                    original_available = has_pdf or bool(availability.get("abstract"))
        applicants = ", ".join(str(item).strip() for item in list(record.applicant_names or []) if str(item).strip())
        return {
            "canonical_patent_id": patent_id,
            "publication_number": str(record.publication_number or patent_id),
            "application_number": record.application_number,
            "title": str(record.title or patent_id),
            "abstract": str(record.abstract_text or ""),
            "applicants": applicants,
            "publication_date": str(record.publication_date or ""),
            "country": str(record.country or ""),
            "kind_code": str(record.kind_code or ""),
            "ipc_codes": list(record.ipc_codes or []),
            "cpc_codes": list(record.cpc_codes or []),
            "original_available": original_available,
            "has_pdf": has_pdf,
            "original_url": f"/api/patent/original/{patent_id}?section=fulltext",
            "match_source": match_source,
            "match_score": round(float(match_score), 6),
            "match_mode": "semantic" if match_source.startswith("patent_") else "lexical",
            "snippet": snippet,
        }


def resolve_minio_loader(runtime: PatentRuntime | None) -> PatentOriginalMinioLoader | None:
    if runtime is None:
        return None
    for resource in list(getattr(runtime, "resources", []) or []):
        if isinstance(resource, PatentOriginalMinioLoader):
            return resource
    return None


def build_patent_browse_search_service(runtime: PatentRuntime | None) -> PatentBrowseSearchService:
    from server.patent.browse_search_cache import (
        get_patent_search_cache,
        set_patent_search_cache,
    )

    return PatentBrowseSearchService(
        runtime=runtime,
        minio_loader=resolve_minio_loader(runtime),
        cache_getter=get_patent_search_cache,
        cache_setter=set_patent_search_cache,
    )
