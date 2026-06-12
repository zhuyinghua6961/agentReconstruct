from __future__ import annotations

import threading
import logging
import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable

from server.patent.models import PatentRetrievalClaim
from server.patent.original_models import OriginalRequest
from server.patent.original_service import build_original_viewer_uri
from server.patent.retrieval_scoring import aggregate_patent_candidates, derive_patent_retrieval_intent
from server.patent.retrieval_validation import validate_patent_stage2_candidates
from server.patent.retrieval_models import (
    PatentCatalogRecord,
    PatentClaim,
    PatentDescriptionSnippet,
    PatentEvidence,
    PatentStage2RetrievalResult,
    PatentRetrievalOutcome,
    PatentTableSupplement,
)
from server.patent.stage2_controls import STAGE2_PAYLOAD_CONTRACT_VERSION, resolve_stage2_runtime_toggles


_IDENTIFIER_RE = re.compile(r"\b(?=[A-Z0-9/.,-]*\d)[A-Z]{2}[A-Z0-9][A-Z0-9/.,-]{4,}[A-Z0-9]\b")
_IDENTIFIER_NORMALIZE_RE = re.compile(r"[^A-Z0-9]")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LOGGER = logging.getLogger("patent.retrieval")


def _preview(value: Any, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _env_bool(raw: str | None, *, default: bool = False) -> bool:
    value = str(raw or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _stage2_diag_enabled() -> bool:
    return _env_bool(os.getenv("QA_STAGE2_DIAGNOSTIC_LOG"), default=True)


def _stage2_query_details_enabled() -> bool:
    return _stage2_diag_enabled() and _env_bool(os.getenv("QA_STAGE2_LOG_QUERY_DETAILS"), default=True)


def _stage2_hit_details_enabled() -> bool:
    return _stage2_diag_enabled() and _env_bool(os.getenv("QA_STAGE2_LOG_HIT_DETAILS"), default=True)


def _stage2_log_hit_max() -> int:
    return _env_int("QA_STAGE2_LOG_HIT_MAX", 5, minimum=0, maximum=50)


def _stage2_log_query_max_chars() -> int:
    return _env_int("QA_STAGE2_LOG_QUERY_MAX_CHARS", 1000, minimum=80, maximum=12000)


def _numeric_distances(values: list[Any]) -> list[float]:
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _explicit_patent_ids_from_user_question(user_question: str) -> list[str]:
    return [
        item
        for item in dict.fromkeys(_IDENTIFIER_RE.findall(str(user_question or "").upper()))
        if item
    ]


def _explicit_patent_ids_from_claims(retrieval_claims: list[Any]) -> list[str]:
    parts: list[str] = []
    for claim in list(retrieval_claims or []):
        filters = dict(getattr(claim, "filters", {}) or {}) if not isinstance(claim, dict) else dict(claim.get("filters") or {})
        if bool(filters.get("graph_seeded")):
            continue
        claim_text = getattr(claim, "claim", "") if not isinstance(claim, dict) else claim.get("claim", "")
        keywords = getattr(claim, "keywords", []) if not isinstance(claim, dict) else claim.get("keywords", [])
        parts.append(" ".join([str(claim_text or ""), *[str(keyword) for keyword in list(keywords or [])]]))
    return [
        item
        for item in dict.fromkeys(_IDENTIFIER_RE.findall(" ".join(parts).upper()))
        if item
    ]


def _explicit_patent_ids_for_hard_constraint(user_question: str, retrieval_claims: list[Any]) -> list[str]:
    return list(dict.fromkeys([
        *_explicit_patent_ids_from_user_question(user_question),
        *_explicit_patent_ids_from_claims(retrieval_claims),
    ]))


def _distance_summary(values: list[Any]) -> dict[str, Any]:
    nums = _numeric_distances(values)
    if not nums:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {"count": len(nums), "min": min(nums), "max": max(nums), "avg": sum(nums) / len(nums)}


def _query_encoding_diagnostics(text: str) -> dict[str, Any]:
    raw = str(text or "")
    normalized = " ".join(raw.split())
    lower = raw.lower()
    mojibake_patterns = ("Ã", "Â", "ä¸", "å", "æ", "\\u00")
    return {
        "chars": len(raw),
        "utf8_bytes": len(raw.encode("utf-8", errors="replace")),
        "non_ascii": sum(1 for ch in raw if ord(ch) > 127),
        "chinese_chars": sum(1 for ch in raw if "\u4e00" <= ch <= "\u9fff"),
        "control_chars": sum(1 for ch in raw if ord(ch) < 32 and ch not in "\r\n\t"),
        "has_replacement_char": "\ufffd" in raw,
        "has_mojibake_pattern": any(pattern.lower() in lower for pattern in mojibake_patterns),
        "normalized_changed": normalized != raw,
        "repr_preview": repr(raw[: _stage2_log_query_max_chars()]),
    }


def _hit_patent_id(hit: dict[str, Any]) -> str:
    return str(hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem") or "").strip()


def _hit_distance(hit: dict[str, Any]) -> Any:
    return hit.get("distance") if isinstance(hit, dict) else None


def _log_stage2_query_encoding(*, query: str, claim_text: str) -> None:
    if not _stage2_query_details_enabled():
        return
    diag = _query_encoding_diagnostics(query)
    _LOGGER.info(
        "Patent Stage2 query encoding diagnostic query_chars=%s utf8_bytes=%s non_ascii=%s chinese_chars=%s "
        "control_chars=%s has_replacement_char=%s has_mojibake_pattern=%s normalized_changed=%s "
        "claim=%s query_preview=%s repr_preview=%s",
        diag["chars"],
        diag["utf8_bytes"],
        diag["non_ascii"],
        diag["chinese_chars"],
        diag["control_chars"],
        _bool_text(bool(diag["has_replacement_char"])),
        _bool_text(bool(diag["has_mojibake_pattern"])),
        _bool_text(bool(diag["normalized_changed"])),
        _preview(claim_text, limit=180),
        _preview(query, limit=_stage2_log_query_max_chars()),
        diag["repr_preview"],
    )


def _log_stage2_vector_request(
    *,
    channel: str,
    query: str,
    top_k: int,
    candidate_patent_ids: list[str] | None = None,
) -> None:
    if not _stage2_query_details_enabled():
        return
    _LOGGER.info(
        "Patent Stage2 vector search request channel=%s top_k=%s candidate_filter_count=%s "
        "candidate_filter_sample=%s query_chars=%s query_preview=%s",
        channel,
        int(top_k),
        len(list(candidate_patent_ids or [])),
        list(candidate_patent_ids or [])[:8],
        len(str(query or "")),
        _preview(query, limit=_stage2_log_query_max_chars()),
    )


def _log_stage2_raw_hits(*, channel: str, query: str, hits: list[dict[str, Any]]) -> None:
    if not _stage2_hit_details_enabled():
        return
    distances = [_hit_distance(hit) for hit in hits]
    stats = _distance_summary(distances)
    _LOGGER.info(
        "Patent Stage2 %s hits diagnostic hit_count=%s distance_count=%s distance_min=%s "
        "distance_max=%s distance_avg=%s query_preview=%s",
        channel,
        len(hits),
        stats["count"],
        stats["min"],
        stats["max"],
        stats["avg"],
        _preview(query, limit=360),
    )
    for rank, hit in enumerate(hits[: _stage2_log_hit_max()], start=1):
        _LOGGER.info(
            "Patent Stage2 raw hit detail channel=%s rank=%s patent_id=%s distance=%s metadata_keys=%s doc_preview=%s",
            channel,
            rank,
            _hit_patent_id(hit),
            _hit_distance(hit),
            sorted(hit.keys())[:16],
            _preview(hit.get("document") or hit.get("snippet") or "", limit=360),
        )


def _normalize_question(question: str) -> str:
    return " ".join(str(question or "").strip().lower().split())


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(str(value or "").lower()) if token}


def _score_overlap(question_tokens: set[str], field_tokens: set[str]) -> float:
    if not question_tokens or not field_tokens:
        return 0.0
    return len(question_tokens & field_tokens) / float(len(question_tokens))


def _normalize_identifier(value: str) -> str:
    return _IDENTIFIER_NORMALIZE_RE.sub("", str(value or "").upper())


def _extract_identifier(question: str) -> str:
    match = _IDENTIFIER_RE.search(str(question or "").upper())
    return match.group(0) if match else ""


@dataclass(frozen=True)
class _MatchedReference:
    record: PatentCatalogRecord
    snippet_text: str
    section_type: str
    section_label: str
    claim_number: int | None
    paragraph_id: str | None
    abstract_score: float | None = None
    chunk_score: float | None = None
    metadata: dict[str, Any] | None = None


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_query_list(values: Any) -> list[str]:
    queries: list[str] = []
    for item in list(values or []):
        normalized = " ".join(str(item or "").split()).strip()
        if normalized and normalized not in queries:
            queries.append(normalized)
    return queries


def _normalize_patent_id_values(values: Any) -> list[str]:
    if isinstance(values, str):
        iterable = [values]
    else:
        iterable = list(values or [])
    normalized: list[str] = []
    for item in iterable:
        text = _normalize_identifier(str(item or ""))
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _document_prefix_key(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    for delimiter in ("。", ".", "!", "?", "；", ";", "\n"):
        if delimiter in normalized:
            prefix = normalized.split(delimiter, 1)[0].strip()
            if prefix:
                return prefix[:80]
    return normalized[:80]


def _document_first_200_key(text: str) -> str:
    return str(text or "")[:200]


def _reference_bundle(
    evidence: PatentEvidence,
    *,
    snippet_text: str,
    section_type: str,
    section_label: str,
    claim_number: int | None,
    paragraph_id: str | None,
) -> tuple[dict[str, object], dict[str, object] | None, dict[str, object] | None]:
    request = None
    viewer_uri = None
    if evidence.original_available:
        section = "abstract" if section_type == "abstract" else ("claim" if section_type == "claim" else "description")
        anchor = (
            f"claim:{claim_number}"
            if claim_number is not None
            else ("section:abstract" if section == "abstract" else f"paragraph:{paragraph_id}")
        )
        request = OriginalRequest(
            canonical_patent_id=evidence.canonical_patent_id,
            section=section,
            claim_number=claim_number,
            paragraph_id=paragraph_id,
            response_format="html",
            anchor=anchor,
        )
        viewer_uri = build_original_viewer_uri(request)
    reference_object = {
        "source_type": "patent",
        "canonical_patent_id": evidence.canonical_patent_id,
        "publication_number": evidence.publication_number,
        "application_number": evidence.application_number,
        "country": evidence.country,
        "kind_code": evidence.kind_code,
        "title": evidence.title,
        "section_type": section_type,
        "section_label": section_label,
        "anchor": {"claim_number": claim_number, "paragraph_id": paragraph_id},
        "snippet": snippet_text,
        "provider": evidence.provider,
        "original_available": evidence.original_available,
        "viewer_uri": viewer_uri,
        "table_supplement_count": len(evidence.table_supplements),
        "table_supplements": [
            {
                "table_title": item.table_title,
                "columns": list(item.columns),
                "rows": [dict(row) for row in item.rows],
                "source_image": item.source_image,
            }
            for item in evidence.table_supplements
        ],
        "scores": {
            "abstract_score": evidence.abstract_score,
            "chunk_score": evidence.chunk_score,
        },
    }
    if request is None:
        return reference_object, None, None
    reference_link = {
        "type": "original_view",
        "label": f"View {section_label}",
        "canonical_patent_id": evidence.canonical_patent_id,
        "viewer_uri": viewer_uri,
        "redirect_url": None,
    }
    original_link = {
        "type": "original_view",
        "label": f"View {section_label}",
        "canonical_patent_id": evidence.canonical_patent_id,
        "section": request.section,
        "claim_number": claim_number,
        "paragraph_id": paragraph_id,
        "viewer_uri": viewer_uri,
        "redirect_url": None,
    }
    return reference_object, reference_link, original_link


class PatentRetrievalService:
    def __init__(
        self,
        *,
        execution_cache: Any | None = None,
        identity_registry: dict[str, str | None] | None = None,
        catalog_records: list[PatentCatalogRecord] | None = None,
        retrieval_version: str = "retrieval-v1",
        catalog_index_version: str = "catalog-v1",
        top_k_metadata: int = 20,
        top_k_fulltext: int = 30,
        top_k_abstract_vector: int = 25,
        top_k_chunk_vector: int = 10,
        cache_ttl_seconds: int = 60,
        negative_ttl_seconds: int = 15,
        abstract_vector_search: Callable[[str, int], list[dict[str, Any]]] | None = None,
        chunk_vector_search: Callable[[str, list[str] | None, int], list[dict[str, Any]]] | None = None,
        query_expander: Callable[[str], str] | None = None,
        table_loader: Callable[[str], list[dict[str, Any]] | list[PatentTableSupplement]] | None = None,
        answer_builder: Callable[..., str] | None = None,
        archive_loader: Any | None = None,
    ) -> None:
        self._execution_cache = execution_cache
        self._identity_registry = self._build_identifier_registry(dict(identity_registry or {}))
        self._catalog_records = list(catalog_records or [])
        self._catalog_by_id = {record.canonical_patent_id: record for record in self._catalog_records}
        self._catalog_record_positions = {
            record.canonical_patent_id: index
            for index, record in enumerate(self._catalog_records)
        }
        self._catalog_identifier_index = self._build_catalog_identifier_index(self._catalog_records)
        self._retrieval_version = str(retrieval_version)
        self._catalog_index_version = str(catalog_index_version)
        self._top_k_metadata = max(1, int(top_k_metadata))
        self._top_k_fulltext = max(1, int(top_k_fulltext))
        self._top_k_abstract_vector = max(1, int(top_k_abstract_vector))
        self._top_k_chunk_vector = max(1, int(top_k_chunk_vector))
        self._cache_ttl_seconds = max(1, int(cache_ttl_seconds))
        self._negative_ttl_seconds = max(1, int(negative_ttl_seconds))
        self._abstract_vector_search = abstract_vector_search
        self._chunk_vector_search = chunk_vector_search
        self._query_expander = query_expander
        self._query_expander_instance: Any | None = None
        self._vector_runtime_enabled = True
        self._catalog_lock = threading.Lock()
        self._vector_runtime_lock = threading.Lock()
        self._table_loader = table_loader
        self._answer_builder = answer_builder
        self._archive_loader = archive_loader

    @property
    def retrieval_version(self) -> str:
        return self._retrieval_version

    @property
    def catalog_index_version(self) -> str:
        return self._catalog_index_version

    def retrieve(
        self,
        *,
        question: str,
        context: dict[str, Any] | None = None,
        include_answer_text: bool = True,
    ) -> PatentRetrievalOutcome:
        _LOGGER.info("retrieve start question_chars=%s vector_enabled=%s", len(str(question or "")), self._vector_search_enabled())
        started_at = time.perf_counter()
        timings: dict[str, int] = {}
        normalized_question = _normalize_question(question)
        identifier = _extract_identifier(question)
        if identifier:
            negative_hit = self._get_negative_patent_resolve(identifier)
            if negative_hit is not None:
                return self._build_not_found("exact_id", negative_cache_hit=True, timings=timings, started_at=started_at)
            canonical_patent_id = self._resolve_identifier(identifier)
            if canonical_patent_id:
                vector_started_at = time.perf_counter()
                vector_matches = self._vector_matches(
                    question=question,
                    candidate_patent_ids=[canonical_patent_id],
                    force_backend="exact_id",
                )
                timings["vector_search_ms"] = max(1, int((time.perf_counter() - vector_started_at) * 1000))
                if vector_matches:
                    outcome = self._build_success(
                        "exact_id",
                        vector_matches,
                        question=question,
                        context=context,
                        cache_hit=False,
                        timings=timings,
                        started_at=started_at,
                        include_answer_text=include_answer_text,
                    )
                    _LOGGER.info("retrieve complete backend=%s refs=%s", outcome.retrieval_backend, outcome.references)
                    return outcome
                record = self._ensure_catalog_record(canonical_patent_id)
                if record is not None:
                    match = self._default_match(record)
                    if match is not None:
                        outcome = self._build_success(
                            "exact_id",
                            [match],
                            question=question,
                            context=context,
                            cache_hit=False,
                            timings=timings,
                            started_at=started_at,
                            include_answer_text=include_answer_text,
                        )
                        _LOGGER.info("retrieve complete backend=%s refs=%s", outcome.retrieval_backend, outcome.references)
                        return outcome
            self._set_negative_patent_resolve(identifier, {"not_found": True})
            return self._build_not_found("exact_id", negative_cache_hit=False, timings=timings, started_at=started_at)

        retrieval_mode = "vector_hybrid" if self._vector_search_enabled() else "hybrid_no_vector"
        query_key = self._normalized_query_key(normalized_question=normalized_question, retrieval_mode=retrieval_mode)
        if include_answer_text:
            cached = self._get_retrieval_cache(query_key)
            if isinstance(cached, dict):
                return self._outcome_from_cache(cached, cache_hit=True)

            negative_hit = self._get_negative_retrieval(query_key)
            if negative_hit is not None:
                return self._build_not_found("metadata_lexical", negative_cache_hit=True, timings=timings, started_at=started_at)

        vector_started_at = time.perf_counter()
        vector_matches = self._vector_matches(question=question, candidate_patent_ids=None, force_backend="vector_hybrid")
        timings["vector_search_ms"] = max(1, int((time.perf_counter() - vector_started_at) * 1000))
        if vector_matches:
            outcome = self._build_success(
                "vector_hybrid",
                vector_matches,
                question=question,
                context=context,
                cache_hit=False,
                timings=timings,
                started_at=started_at,
                include_answer_text=include_answer_text,
            )
            if include_answer_text:
                self._set_retrieval_cache(query_key, self._cache_payload(outcome))
            _LOGGER.info("retrieve complete backend=%s refs=%s", outcome.retrieval_backend, outcome.references)
            return outcome

        metadata_started_at = time.perf_counter()
        metadata_candidates = self._metadata_candidates(normalized_question)
        timings["metadata_candidate_ms"] = max(1, int((time.perf_counter() - metadata_started_at) * 1000))
        if metadata_candidates:
            top_match, top_score = metadata_candidates[0]
            next_score = metadata_candidates[1][1] if len(metadata_candidates) > 1 else 0.0
            if top_score >= 0.35 and (len(metadata_candidates) == 1 or (top_score - next_score) >= 0.05):
                outcome = self._build_success(
                    "metadata_lexical",
                    [top_match],
                    question=question,
                    context=context,
                    cache_hit=False,
                    timings=timings,
                    started_at=started_at,
                    include_answer_text=include_answer_text,
                )
                if include_answer_text:
                    self._set_retrieval_cache(query_key, self._cache_payload(outcome))
                _LOGGER.info("retrieve complete backend=%s refs=%s", outcome.retrieval_backend, outcome.references)
                return outcome

        fulltext_started_at = time.perf_counter()
        fulltext_candidates = self._fulltext_candidates(normalized_question)
        timings["fulltext_candidate_ms"] = max(1, int((time.perf_counter() - fulltext_started_at) * 1000))
        if fulltext_candidates:
            top_match, top_score = fulltext_candidates[0]
            next_score = fulltext_candidates[1][1] if len(fulltext_candidates) > 1 else 0.0
            if top_score >= 0.20 and (len(fulltext_candidates) == 1 or (top_score - next_score) >= 0.03):
                outcome = self._build_success(
                    "fulltext_lexical",
                    [top_match],
                    question=question,
                    context=context,
                    cache_hit=False,
                    timings=timings,
                    started_at=started_at,
                    include_answer_text=include_answer_text,
                )
                if include_answer_text:
                    self._set_retrieval_cache(query_key, self._cache_payload(outcome))
                _LOGGER.info("retrieve complete backend=%s refs=%s", outcome.retrieval_backend, outcome.references)
                return outcome

        if include_answer_text:
            self._set_negative_retrieval(query_key, {"not_found": True})
        outcome = self._build_not_found("metadata_lexical", negative_cache_hit=False, timings=timings, started_at=started_at)
        _LOGGER.info("retrieve complete backend=%s refs=%s not_found=%s", outcome.retrieval_backend, outcome.references, outcome.not_found)
        return outcome

    def targeted_retrieve(
        self,
        *,
        retrieval_claims: list[PatentRetrievalClaim] | None = None,
        retrieval_plan: Any = None,
        user_question: str,
        query_generation_fn: Callable[..., list[str]] | None = None,
        frozen_claim_queries: list[list[str]] | None = None,
        parallel_workers: int = 1,
        should_cancel: Callable[[], bool] | None = None,
        active_stream_count: int | None = None,
        context: dict[str, Any] | None = None,
        rerank_fn: Callable[..., dict[str, Any]] | None = None,
        stage2_query_diagnostics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        claims = self._coerce_retrieval_claims(retrieval_claims)
        if claims:
            return self._targeted_retrieve_from_claims(
                retrieval_claims=claims,
                user_question=user_question,
                query_generation_fn=query_generation_fn,
                frozen_claim_queries=frozen_claim_queries,
                parallel_workers=parallel_workers,
                should_cancel=should_cancel,
                active_stream_count=active_stream_count,
                context=context,
                rerank_fn=rerank_fn,
                stage2_query_diagnostics=stage2_query_diagnostics,
            )
        return self._targeted_retrieve_from_plan(
            retrieval_plan=retrieval_plan,
            user_question=user_question,
            context=context,
        )

    def _graph_retrieval_controls(self, context: dict[str, Any] | None) -> dict[str, Any]:
        graph_kb = dict((context or {}).get("graph_kb") or {}) if isinstance(context, dict) else {}
        if not graph_kb:
            return {"candidate_patent_ids": [], "constraints": [], "entity_hints": {}, "behavior": "none"}
        candidate_patent_ids = _normalize_patent_id_values(graph_kb.get("stage2_patent_candidates"))
        constraints = [dict(item) for item in list(graph_kb.get("stage2_constraints") or []) if isinstance(item, dict)]
        entity_hints = {
            str(key): _normalize_query_list(values)
            for key, values in dict(graph_kb.get("stage2_entity_hints") or {}).items()
        }
        behavior = "filter_applied" if candidate_patent_ids else ("hint_only" if constraints or entity_hints or graph_kb.get("stage4_fact_block") else "none")
        return {
            "candidate_patent_ids": candidate_patent_ids,
            "constraints": constraints,
            "entity_hints": entity_hints,
            "behavior": behavior,
        }

    def _targeted_retrieve_from_claims_dual_search(
        self,
        *,
        retrieval_claims: list[PatentRetrievalClaim],
        user_question: str,
        query_generation_fn: Callable[..., list[str]] | None = None,
        frozen_claim_queries: list[list[str]] | None = None,
        parallel_workers: int = 1,
        should_cancel: Callable[[], bool] | None = None,
        context: dict[str, Any] | None = None,
        rerank_fn: Callable[..., dict[str, Any]] | None = None,
        stage2_query_diagnostics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        timings: dict[str, int] = {}
        graph_controls = self._graph_retrieval_controls(context)
        graph_candidate_patent_ids = list(graph_controls.get("candidate_patent_ids") or [])
        graph_candidate_keys = {_normalize_identifier(item) for item in graph_candidate_patent_ids}
        resolved_frozen_claim_queries = list(frozen_claim_queries or [])
        claim_jobs = list(enumerate(retrieval_claims))

        def _failed_claim_output(index: int, claim: PatentRetrievalClaim, exc: Exception) -> dict[str, Any]:
            _LOGGER.warning(
                "claim retrieval failed claim_index=%s claim=%s error=%s",
                int(index) + 1,
                str(claim.claim or "")[:120],
                exc,
            )
            return {
                "index": index,
                "generated_queries": [],
                "candidate_patent_ids": [],
                "matches": [],
                "ok": False,
            }

        def _process_claim(index: int, claim: PatentRetrievalClaim) -> dict[str, Any]:
            if index < len(resolved_frozen_claim_queries):
                claim_queries = _normalize_query_list(resolved_frozen_claim_queries[index])
            else:
                claim_queries = self._generate_claim_queries(
                    user_question=user_question,
                    retrieval_claim=claim,
                    query_generation_fn=query_generation_fn,
                )
            generated_queries: list[str] = []
            candidate_patent_ids: list[str] = []
            matches: list[_MatchedReference] = []
            for raw_query in claim_queries:
                query = self._prepare_stage2_dual_search_query(raw_query)
                if not query:
                    continue
                generated_queries.append(query)
                query_matches, query_candidate_ids = self._dual_vector_search_for_query(
                    query=query,
                    retrieval_claim=claim,
                    graph_candidate_patent_ids=graph_candidate_patent_ids,
                    graph_candidate_keys=graph_candidate_keys,
                    rerank_fn=rerank_fn,
                )
                for patent_id in query_candidate_ids:
                    if patent_id not in candidate_patent_ids:
                        candidate_patent_ids.append(patent_id)
                matches.extend(query_matches)
            return {
                "index": index,
                "generated_queries": generated_queries,
                "candidate_patent_ids": candidate_patent_ids,
                "matches": matches,
                "ok": True,
            }

        if callable(should_cancel) and should_cancel():
            return self._cancelled_stage2_payload()

        claim_outputs: list[dict[str, Any]] = []
        if len(claim_jobs) <= 1 or int(parallel_workers or 1) <= 1:
            for index, claim in claim_jobs:
                if callable(should_cancel) and should_cancel():
                    return self._cancelled_stage2_payload()
                try:
                    claim_outputs.append(_process_claim(index, claim))
                except Exception as exc:
                    claim_outputs.append(_failed_claim_output(index, claim, exc))
        else:
            max_workers = min(max(1, int(parallel_workers)), len(claim_jobs))
            cancelled = False
            executor = ThreadPoolExecutor(max_workers=max_workers)
            try:
                future_map = {executor.submit(_process_claim, index, claim): (index, claim) for index, claim in claim_jobs}
                pending = set(future_map)
                while pending:
                    if callable(should_cancel) and should_cancel():
                        cancelled = True
                        for future in pending:
                            future.cancel()
                        return self._cancelled_stage2_payload()
                    done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        try:
                            claim_outputs.append(future.result())
                        except Exception as exc:
                            index, claim = future_map[future]
                            claim_outputs.append(_failed_claim_output(index, claim, exc))
            finally:
                executor.shutdown(wait=not cancelled, cancel_futures=cancelled)

        generated_queries: list[str] = []
        candidate_patent_ids: list[str] = []
        all_matches: list[_MatchedReference] = []
        for output in sorted(claim_outputs, key=lambda item: int(item.get("index", 0))):
            if not output.get("ok"):
                continue
            for query in list(output.get("generated_queries") or []):
                if query not in generated_queries:
                    generated_queries.append(query)
            for patent_id in list(output.get("candidate_patent_ids") or []):
                if patent_id not in candidate_patent_ids:
                    candidate_patent_ids.append(patent_id)
            all_matches.extend(list(output.get("matches") or []))

        selected_matches = self._dedupe_matches_by_first_200_chars(all_matches)
        graph_fallback = False
        if not selected_matches and graph_candidate_patent_ids:
            selected_matches = [
                match
                for patent_id in graph_candidate_patent_ids
                for match in [self._default_match_for_patent(patent_id)]
                if match is not None
            ]
            graph_fallback = bool(selected_matches)
        if not selected_matches:
            fallback_plan = self._retrieval_plan_from_claims(retrieval_claims, user_question=user_question)
            if graph_candidate_patent_ids:
                payload = self._stage2_payload_from_outcome(
                    self._build_not_found(
                        "vector_hybrid",
                        negative_cache_hit=False,
                        timings=timings,
                        started_at=started_at,
                    )
                )
                metadata = dict(payload.get("metadata") or {})
                metadata["graph_stage2_behavior"] = "fallback_no_vector_hits"
                metadata["graph_candidate_patent_ids"] = list(graph_candidate_patent_ids)
                metadata["graph_constraints_applied"] = list(graph_controls.get("constraints") or [])
                metadata["candidate_patent_ids"] = list(graph_candidate_patent_ids)
                metadata["retrieval_plan_queries"] = list(generated_queries)
                payload["metadata"] = metadata
                return payload
            return self._targeted_retrieve_from_plan(
                retrieval_plan=fallback_plan,
                user_question=user_question,
                context=context,
            )

        outcome = self._build_success(
            "vector_hybrid",
            selected_matches,
            question=user_question,
            context=context,
            cache_hit=False,
            timings=timings,
            started_at=started_at,
            include_answer_text=False,
        )
        payload = self._stage2_payload_from_outcome(outcome, matches=selected_matches)
        metadata = dict(payload.get("metadata") or {})
        metadata["candidate_patent_ids"] = list(graph_candidate_patent_ids or candidate_patent_ids)
        metadata["retrieval_plan_queries"] = list(generated_queries)
        if stage2_query_diagnostics:
            metadata["stage2_query_diagnostics"] = list(stage2_query_diagnostics)
        if graph_candidate_patent_ids or graph_controls.get("behavior") == "hint_only":
            metadata["graph_stage2_behavior"] = "fallback_no_vector_hits" if graph_fallback else (
                "filter_applied" if graph_candidate_patent_ids else "hint_only"
            )
            if graph_candidate_patent_ids:
                metadata["graph_candidate_patent_ids"] = list(graph_candidate_patent_ids)
                metadata["graph_constraints_applied"] = list(graph_controls.get("constraints") or [])
            if graph_fallback:
                metadata["localization_fallback"] = "archive_default_anchor"
        payload["metadata"] = metadata
        _LOGGER.info(
            "patent stage2 dual-search retrieval summary raw_candidates=%s selected_sources=%s graph_behavior=%s source_ids=%s",
            len(candidate_patent_ids),
            [dict(match.metadata or {}).get("stage2_source") for match in selected_matches[:8]],
            str(metadata.get("graph_stage2_behavior") or "none"),
            list(payload.get("source_ids") or []),
        )
        if _stage2_diag_enabled():
            distances = list(payload.get("distances") or [])
            stats = _distance_summary(distances)
            _LOGGER.info(
                "Patent Stage2 diagnostic summary mode=dual_search generated_queries=%s raw_candidates=%s "
                "selected_sources=%s documents=%s distance_count=%s distance_min=%s distance_max=%s "
                "distance_avg=%s source_ids=%s graph_behavior=%s",
                len(generated_queries),
                len(candidate_patent_ids),
                len(list(payload.get("source_ids") or [])),
                len(list(payload.get("documents") or [])),
                stats["count"],
                stats["min"],
                stats["max"],
                stats["avg"],
                list(payload.get("source_ids") or []),
                str(metadata.get("graph_stage2_behavior") or "none"),
            )
        return payload

    def _targeted_retrieve_from_plan(
        self,
        *,
        retrieval_plan: Any,
        user_question: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plan = self._coerce_retrieval_plan(retrieval_plan)
        graph_controls = self._graph_retrieval_controls(context)
        started_at = time.perf_counter()
        timings: dict[str, int] = {}

        explicit_patent_ids = [
            self._normalize_patent_id(item)
            for item in list(plan.get("explicit_patent_ids") or [])
            if self._normalize_patent_id(item)
        ]
        explicit_patent_ids = list(dict.fromkeys(explicit_patent_ids))

        if explicit_patent_ids and not self._vector_search_enabled():
            matches = [match for patent_id in explicit_patent_ids for match in [self._default_match_for_patent(patent_id)] if match is not None]
            if matches:
                outcome = self._build_success(
                    "exact_id",
                    matches,
                    question=user_question,
                    context=context,
                    cache_hit=False,
                    timings=timings,
                    started_at=started_at,
                    include_answer_text=False,
                )
                return self._stage2_payload_from_outcome(outcome)

        if self._vector_search_enabled():
            candidate_patent_ids = self._candidate_patent_ids_from_plan(plan, user_question=user_question)
            graph_candidate_patent_ids = list(graph_controls.get("candidate_patent_ids") or [])
            if graph_candidate_patent_ids:
                candidate_patent_ids = graph_candidate_patent_ids
            localization_queries = self._localization_queries_from_plan(plan, user_question=user_question)
            matches_by_query = [
                self._vector_matches(
                    question=query,
                    candidate_patent_ids=candidate_patent_ids or explicit_patent_ids or None,
                    force_backend="vector_hybrid",
                )
                for query in localization_queries
            ]
            merged_matches = self._merge_targeted_matches(matches_by_query)
            if merged_matches:
                outcome = self._build_success(
                    "vector_hybrid",
                    merged_matches,
                    question=user_question,
                    context=context,
                    cache_hit=False,
                    timings=timings,
                    started_at=started_at,
                    include_answer_text=False,
                )
                payload = self._stage2_payload_from_outcome(outcome)
                metadata = dict(payload.get("metadata") or {})
                if candidate_patent_ids:
                    metadata["candidate_patent_ids"] = list(candidate_patent_ids)
                if graph_candidate_patent_ids:
                    metadata["graph_stage2_behavior"] = "filter_applied"
                    metadata["graph_candidate_patent_ids"] = list(graph_candidate_patent_ids)
                    metadata["graph_constraints_applied"] = list(graph_controls.get("constraints") or [])
                metadata["retrieval_plan_queries"] = list(localization_queries)
                payload["metadata"] = metadata
                return payload
            if candidate_patent_ids:
                fallback_matches = [
                    match
                    for patent_id in candidate_patent_ids
                    for match in [self._default_match_for_patent(patent_id)]
                    if match is not None
                ]
                if fallback_matches:
                    outcome = self._build_success(
                        "vector_hybrid",
                        fallback_matches,
                        question=user_question,
                        context=context,
                        cache_hit=False,
                        timings=timings,
                        started_at=started_at,
                        include_answer_text=False,
                    )
                    payload = self._stage2_payload_from_outcome(outcome)
                    metadata = dict(payload.get("metadata") or {})
                    metadata["candidate_patent_ids"] = list(candidate_patent_ids)
                    if graph_candidate_patent_ids:
                        metadata["graph_stage2_behavior"] = "fallback_no_vector_hits"
                        metadata["graph_candidate_patent_ids"] = list(graph_candidate_patent_ids)
                        metadata["graph_constraints_applied"] = list(graph_controls.get("constraints") or [])
                    metadata["retrieval_plan_queries"] = list(localization_queries)
                    metadata["localization_fallback"] = "archive_default_anchor"
                    payload["metadata"] = metadata
                    return payload

        if graph_controls.get("candidate_patent_ids"):
            payload = self._stage2_payload_from_outcome(
                self._build_not_found(
                    "vector_hybrid",
                    negative_cache_hit=False,
                    timings=timings,
                    started_at=started_at,
                )
            )
            metadata = dict(payload.get("metadata") or {})
            metadata["graph_stage2_behavior"] = "fallback_no_vector_hits"
            metadata["graph_candidate_patent_ids"] = list(graph_controls.get("candidate_patent_ids") or [])
            metadata["graph_constraints_applied"] = list(graph_controls.get("constraints") or [])
            metadata["candidate_patent_ids"] = list(graph_controls.get("candidate_patent_ids") or [])
            payload["metadata"] = metadata
            return payload

        fallback_question = explicit_patent_ids[0] if explicit_patent_ids else user_question
        payload = self._stage2_payload_from_outcome(
            self.retrieve(
                question=fallback_question,
                context=context,
                include_answer_text=False,
            )
        )
        if graph_controls.get("candidate_patent_ids") or graph_controls.get("behavior") == "hint_only":
            metadata = dict(payload.get("metadata") or {})
            metadata["graph_stage2_behavior"] = (
                "fallback_no_vector_hits" if graph_controls.get("candidate_patent_ids") else "hint_only"
            )
            metadata["graph_candidate_patent_ids"] = list(graph_controls.get("candidate_patent_ids") or [])
            metadata["graph_constraints_applied"] = list(graph_controls.get("constraints") or [])
            payload["metadata"] = metadata
        return payload

    def _targeted_retrieve_from_claims(
        self,
        *,
        retrieval_claims: list[PatentRetrievalClaim],
        user_question: str,
        query_generation_fn: Callable[..., list[str]] | None = None,
        frozen_claim_queries: list[list[str]] | None = None,
        parallel_workers: int = 1,
        should_cancel: Callable[[], bool] | None = None,
        active_stream_count: int | None = None,
        context: dict[str, Any] | None = None,
        rerank_fn: Callable[..., dict[str, Any]] | None = None,
        stage2_query_diagnostics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        del active_stream_count
        toggles = resolve_stage2_runtime_toggles()
        if callable(should_cancel) and should_cancel():
            return self._cancelled_stage2_payload()
        graph_controls = self._graph_retrieval_controls(context)
        c_enabled = toggles.convergence_enabled and toggles.c_patent_scoring_enabled
        _LOGGER.info(
            "patent stage2 convergence controls enabled=%s c_enabled=%s rerank=%s validation=%s "
            "global_chunk=%s table_metric=%s max_global_patents=%s graph_behavior=%s graph_candidates=%s claim_count=%s",
            bool(toggles.convergence_enabled),
            bool(c_enabled),
            bool(toggles.rerank_enabled),
            bool(toggles.validation_enabled),
            bool(toggles.c_global_chunk_recall_enabled),
            bool(toggles.c_table_metric_boost_enabled),
            int(toggles.max_global_patents),
            str(graph_controls.get("behavior") or "none"),
            len(list(graph_controls.get("candidate_patent_ids") or [])),
            len(list(retrieval_claims or [])),
        )
        if not self._vector_search_enabled():
            payload = self._targeted_retrieve_from_plan(
                retrieval_plan=self._retrieval_plan_from_claims(retrieval_claims, user_question=user_question),
                user_question=user_question,
                context=context,
            )
            return self._annotate_no_vector_convergence_payload(payload, toggles=toggles) if toggles.convergence_enabled else payload

        if not toggles.convergence_enabled:
            return self._targeted_retrieve_from_claims_dual_search(
                retrieval_claims=retrieval_claims,
                user_question=user_question,
                query_generation_fn=query_generation_fn,
                frozen_claim_queries=frozen_claim_queries,
                parallel_workers=parallel_workers,
                should_cancel=should_cancel,
                context=context,
                rerank_fn=rerank_fn,
                stage2_query_diagnostics=stage2_query_diagnostics,
            )

        started_at = time.perf_counter()
        timings: dict[str, int] = {}
        generated_queries: list[str] = []
        candidate_patent_ids: list[str] = []
        graph_candidate_patent_ids = list(graph_controls.get("candidate_patent_ids") or [])
        graph_candidate_keys = {_normalize_identifier(item) for item in graph_candidate_patent_ids}
        hard_graph_candidate_keys = set() if c_enabled else set(graph_candidate_keys)
        resolved_frozen_claim_queries = list(frozen_claim_queries or [])
        claim_jobs = list(enumerate(retrieval_claims))

        def _failed_claim_output(index: int, claim: PatentRetrievalClaim, exc: Exception) -> dict[str, Any]:
            _LOGGER.warning(
                "claim retrieval failed claim_index=%s claim=%s error=%s",
                int(index) + 1,
                str(claim.claim or "")[:120],
                exc,
            )
            return {
                "index": index,
                "generated_queries": [],
                "candidate_patent_ids": [],
                "per_query_matches": [],
                "ok": False,
            }

        def _process_claim(index: int, claim: PatentRetrievalClaim) -> dict[str, Any]:
            if index < len(resolved_frozen_claim_queries):
                claim_queries = _normalize_query_list(resolved_frozen_claim_queries[index])
            else:
                claim_queries = self._generate_claim_queries(
                    user_question=user_question,
                    retrieval_claim=claim,
                    query_generation_fn=query_generation_fn,
                )
            if not claim_queries:
                return {
                    "index": index,
                    "generated_queries": [],
                    "candidate_patent_ids": [],
                    "per_query_matches": [],
                    "ok": True,
                }
            claim_candidate_patent_ids: list[str] = []
            per_query_matches: list[list[_MatchedReference]] = []
            for query in claim_queries:
                abstract_hits = self._run_abstract_vector_search(query, self._top_k_abstract_vector)
                query_candidate_ids: list[str] = []
                query_matches: list[_MatchedReference] = []
                for hit in abstract_hits:
                    normalized = self._normalize_patent_id(
                        hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
                    )
                    if hard_graph_candidate_keys and _normalize_identifier(normalized) not in hard_graph_candidate_keys:
                        continue
                    if normalized and normalized not in query_candidate_ids:
                        query_candidate_ids.append(normalized)
                    abstract_match = self._match_from_abstract_hit(
                        hit,
                        generated_query=query,
                        retrieval_claim=claim,
                    )
                    if abstract_match is not None:
                        query_matches.append(abstract_match)
                for patent_id in query_candidate_ids:
                    if patent_id not in claim_candidate_patent_ids:
                        claim_candidate_patent_ids.append(patent_id)
                chunk_candidate_ids = query_candidate_ids or (
                    graph_candidate_patent_ids if graph_candidate_patent_ids and not c_enabled else None
                )
                chunk_hits = self._run_chunk_vector_search(
                    query,
                    chunk_candidate_ids,
                    self._top_k_chunk_vector,
                )
                for hit in chunk_hits:
                    normalized = self._normalize_patent_id(
                        hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
                    )
                    if hard_graph_candidate_keys and _normalize_identifier(normalized) not in hard_graph_candidate_keys:
                        continue
                    if normalized and normalized not in claim_candidate_patent_ids:
                        claim_candidate_patent_ids.append(normalized)
                    chunk_match = self._match_from_chunk_hit(
                        self._augment_stage2_hit_metadata(
                            hit,
                            stage2_source="chunk",
                            generated_query=query,
                            retrieval_claim=claim,
                        )
                    )
                    if chunk_match is not None:
                        query_matches.append(chunk_match)
                if c_enabled and toggles.c_global_chunk_recall_enabled:
                    global_chunk_hits = self._run_chunk_vector_search(query, None, self._top_k_chunk_vector)
                    for hit in global_chunk_hits:
                        normalized = self._normalize_patent_id(
                            hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
                        )
                        if normalized and normalized not in claim_candidate_patent_ids:
                            claim_candidate_patent_ids.append(normalized)
                        chunk_match = self._match_from_chunk_hit(
                            self._augment_stage2_hit_metadata(
                                hit,
                                stage2_source="chunk_vector_global",
                                generated_query=query,
                                retrieval_claim=claim,
                            )
                        )
                        if chunk_match is not None:
                            query_matches.append(chunk_match)
                deduped_query_matches = self._dedupe_matches_by_prefix(query_matches)
                _LOGGER.info(
                    "patent stage2 query retrieval diagnostics claim_index=%s query=%s abstract_hits=%s "
                    "chunk_hits=%s query_candidate_ids=%s query_matches=%s deduped_matches=%s hard_graph_filter=%s",
                    int(index) + 1,
                    _preview(query),
                    len(abstract_hits),
                    len(chunk_hits),
                    len(query_candidate_ids),
                    len(query_matches),
                    len(deduped_query_matches),
                    bool(hard_graph_candidate_keys),
                )
                per_query_matches.append(deduped_query_matches)
            _LOGGER.info(
                "patent stage2 claim retrieval completed claim_index=%s query_count=%s candidate_patents=%s match_groups=%s "
                "graph_filter=%s global_chunk=%s candidate_sample=%s",
                int(index) + 1,
                len(claim_queries),
                len(claim_candidate_patent_ids),
                len(per_query_matches),
                bool(hard_graph_candidate_keys),
                bool(c_enabled and toggles.c_global_chunk_recall_enabled),
                claim_candidate_patent_ids[:8],
            )
            return {
                "index": index,
                "generated_queries": list(claim_queries),
                "candidate_patent_ids": list(claim_candidate_patent_ids),
                "per_query_matches": per_query_matches,
                "ok": True,
            }

        claim_outputs: list[dict[str, Any]] = []
        if len(claim_jobs) <= 1 or int(parallel_workers or 1) <= 1:
            for index, claim in claim_jobs:
                if callable(should_cancel) and should_cancel():
                    return self._cancelled_stage2_payload()
                try:
                    claim_outputs.append(_process_claim(index, claim))
                except Exception as exc:
                    claim_outputs.append(_failed_claim_output(index, claim, exc))
        else:
            max_workers = min(max(1, int(parallel_workers)), len(claim_jobs))
            cancelled = False
            executor = ThreadPoolExecutor(max_workers=max_workers)
            try:
                future_map = {executor.submit(_process_claim, index, claim): (index, claim) for index, claim in claim_jobs}
                pending = set(future_map)
                while pending:
                    if callable(should_cancel) and should_cancel():
                        cancelled = True
                        for future in pending:
                            future.cancel()
                        return self._cancelled_stage2_payload()
                    done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        try:
                            claim_outputs.append(future.result())
                        except Exception as exc:
                            index, claim = future_map[future]
                            claim_outputs.append(_failed_claim_output(index, claim, exc))
            finally:
                executor.shutdown(wait=not cancelled, cancel_futures=cancelled)

        all_matches: list[_MatchedReference] = []
        for output in sorted(claim_outputs, key=lambda item: int(item.get("index", 0))):
            if not output.get("ok"):
                continue
            for query in list(output.get("generated_queries") or []):
                if query not in generated_queries:
                    generated_queries.append(query)
            for patent_id in list(output.get("candidate_patent_ids") or []):
                if patent_id not in candidate_patent_ids:
                    candidate_patent_ids.append(patent_id)
            for query_matches in list(output.get("per_query_matches") or []):
                all_matches.extend(list(query_matches or []))

        merged_matches = self._dedupe_matches_by_prefix(all_matches)
        _LOGGER.info(
            "patent stage2 merged candidate diagnostics claim_outputs=%s generated_queries=%s candidate_patents=%s "
            "all_matches=%s merged_matches=%s candidate_sample=%s",
            len(claim_outputs),
            len(generated_queries),
            len(candidate_patent_ids),
            len(all_matches),
            len(merged_matches),
            candidate_patent_ids[:10],
        )
        if not merged_matches:
            _LOGGER.warning(
                "patent stage2 no merged matches; falling back to plan retrieval claim_count=%s generated_queries=%s "
                "candidate_patents=%s user_question=%s",
                len(retrieval_claims),
                len(generated_queries),
                len(candidate_patent_ids),
                _preview(user_question),
            )
            fallback_plan = self._retrieval_plan_from_claims(retrieval_claims, user_question=user_question)
            payload = self._targeted_retrieve_from_plan(
                retrieval_plan=fallback_plan,
                user_question=user_question,
                context=context,
            )
            return self._annotate_no_vector_convergence_payload(payload, toggles=toggles) if toggles.convergence_enabled else payload

        if c_enabled and graph_candidate_patent_ids:
            seen_ids = {match.record.canonical_patent_id for match in merged_matches}
            for patent_id in graph_candidate_patent_ids:
                if patent_id in seen_ids:
                    continue
                graph_match = self._default_match_for_patent(patent_id)
                if graph_match is None:
                    continue
                merged_matches.append(
                    _MatchedReference(
                        record=graph_match.record,
                        snippet_text=graph_match.snippet_text,
                        section_type=graph_match.section_type,
                        section_label=graph_match.section_label,
                        claim_number=graph_match.claim_number,
                        paragraph_id=graph_match.paragraph_id,
                        abstract_score=graph_match.abstract_score,
                        chunk_score=graph_match.chunk_score,
                        metadata={
                            **dict(graph_match.metadata or {}),
                            "patent_id": graph_match.record.canonical_patent_id,
                            "stage2_source": "graph_candidate",
                            "stage2_channel": "graph_candidate",
                        },
                    )
                )

        raw_candidate_patent_ids = list(dict.fromkeys(match.record.canonical_patent_id for match in merged_matches))
        selected_matches = list(merged_matches)
        explicit_patent_ids = [
            self._normalize_patent_id(item)
            for item in _explicit_patent_ids_for_hard_constraint(user_question, retrieval_claims)
            if self._normalize_patent_id(item)
        ]
        explicit_id_fallback = False
        if toggles.convergence_enabled and explicit_patent_ids:
            explicit_set = set(explicit_patent_ids)
            selected_matches = [
                self._mark_explicit_id_match(match)
                for match in selected_matches
                if match.record.canonical_patent_id in explicit_set
            ]
            if not selected_matches:
                selected_matches = [
                    self._mark_explicit_id_match(match)
                    for patent_id in explicit_patent_ids
                    for match in [self._default_match_for_patent(patent_id)]
                    if match is not None
                ]
                explicit_id_fallback = bool(selected_matches)
        stage2_rerank_metadata = {"enabled": False, "applied": False, "fallback": False}
        if toggles.convergence_enabled:
            selected_matches, stage2_rerank_metadata = self._apply_stage2_rerank(
                matches=selected_matches,
                query=user_question,
                toggles=toggles,
                rerank_fn=rerank_fn,
            )

        validation_metadata: dict[str, Any] | None = None
        filtered_sample: list[dict[str, Any]] = []
        if toggles.convergence_enabled and toggles.validation_enabled and not c_enabled:
            validation_result = validate_patent_stage2_candidates(
                candidates=[self._candidate_from_match(match) for match in selected_matches],
                user_question=user_question,
                claim_text=" ".join(str(claim.claim or "") for claim in retrieval_claims),
                min_results=toggles.min_results_per_claim,
            )
            selected_keys = {
                (
                    dict(item.get("metadata") or {}).get("patent_id"),
                    item.get("document"),
                    dict(item.get("metadata") or {}).get("chunk_index"),
                    dict(item.get("metadata") or {}).get("section_type"),
                )
                for item in validation_result.selected
            }
            selected_matches = [
                match
                for match in selected_matches
                if (
                    match.record.canonical_patent_id,
                    match.snippet_text,
                    dict(match.metadata or {}).get("chunk_index"),
                    match.section_type,
                )
                in selected_keys
            ]
            validation_metadata = dict(validation_result.diagnostics)
            filtered_sample = [dict(item.get("metadata") or {}) for item in validation_result.filtered[:5]]
            _LOGGER.info(
                "patent stage2 validation completed before=%s after=%s filtered=%s fallback=%s",
                len(selected_keys) + len(validation_result.filtered),
                len(selected_matches),
                int(validation_metadata.get("filtered_count") or 0),
                bool(validation_metadata.get("validation_fallback")),
            )

        if c_enabled:
            selected_matches, patent_score_metadata = self._apply_c_patent_scoring(
                matches=selected_matches,
                retrieval_claims=retrieval_claims,
                user_question=user_question,
                graph_controls=graph_controls,
                toggles=toggles,
            )
        else:
            patent_score_metadata = []

        if toggles.convergence_enabled:
            selected_matches = self._limit_matches_to_patents(
                selected_matches,
                max_patents=toggles.max_global_patents,
            )
            _LOGGER.info(
                "patent stage2 payload contraction selected_patents=%s selected_matches=%s max_global_patents=%s",
                len({match.record.canonical_patent_id for match in selected_matches}),
                len(selected_matches),
                int(toggles.max_global_patents),
            )

        outcome = self._build_success(
            "vector_hybrid",
            selected_matches,
            question=user_question,
            context=context,
            cache_hit=False,
            timings=timings,
            started_at=started_at,
            include_answer_text=False,
        )
        payload = self._stage2_payload_from_outcome(outcome, matches=selected_matches)
        metadata = dict(payload.get("metadata") or {})
        metadata["candidate_patent_ids"] = list(graph_candidate_patent_ids or candidate_patent_ids)
        metadata["retrieval_plan_queries"] = list(generated_queries)
        if stage2_query_diagnostics:
            metadata["stage2_query_diagnostics"] = list(stage2_query_diagnostics)
        if graph_candidate_patent_ids or graph_controls.get("behavior") == "hint_only":
            metadata["graph_stage2_behavior"] = "filter_applied" if graph_candidate_patent_ids else "hint_only"
            if c_enabled and graph_candidate_patent_ids:
                metadata["graph_stage2_behavior"] = "seed_boost"
            metadata["graph_candidate_patent_ids"] = list(graph_candidate_patent_ids)
            metadata["graph_constraints_applied"] = list(graph_controls.get("constraints") or [])
        if toggles.convergence_enabled:
            metadata["stage2_raw_candidate_count"] = len(raw_candidate_patent_ids)
            metadata["stage2_raw_candidate_patent_ids"] = list(raw_candidate_patent_ids)
            metadata["stage2_payload_contract_version"] = STAGE2_PAYLOAD_CONTRACT_VERSION
            metadata["stage2_rerank"] = stage2_rerank_metadata
            if validation_metadata is not None:
                metadata["stage2_validation"] = validation_metadata
                metadata["stage2_filtered_out_sample"] = filtered_sample
            if patent_score_metadata:
                metadata["stage2_patent_scores"] = patent_score_metadata
                metadata["stage2_explicit_id_fallback"] = explicit_id_fallback or any(
                    "explicit_id_fallback" in list(item.get("reasons") or [])
                    for item in patent_score_metadata
                    if isinstance(item, dict)
                )
            elif explicit_id_fallback:
                metadata["stage2_explicit_id_fallback"] = True
        payload["metadata"] = metadata
        payload["source_ids"] = self.extract_source_ids(payload)
        _LOGGER.info(
            "patent stage2 retrieval summary convergence=%s c_enabled=%s raw_candidates=%s selected_sources=%s "
            "rerank_applied=%s rerank_fallback=%s validation_filtered=%s graph_behavior=%s explicit_fallback=%s source_ids=%s",
            bool(toggles.convergence_enabled),
            bool(c_enabled),
            len(raw_candidate_patent_ids),
            len(list(payload.get("source_ids") or [])),
            bool(stage2_rerank_metadata.get("applied")),
            bool(stage2_rerank_metadata.get("fallback")),
            int((validation_metadata or {}).get("filtered_count") or 0),
            str(metadata.get("graph_stage2_behavior") or "none"),
            bool(metadata.get("stage2_explicit_id_fallback")),
            list(payload.get("source_ids") or [])[:5],
        )
        if _stage2_diag_enabled():
            distances = list(payload.get("distances") or [])
            stats = _distance_summary(distances)
            _LOGGER.info(
                "Patent Stage2 diagnostic summary mode=convergence generated_queries=%s raw_candidates=%s "
                "selected_sources=%s documents=%s distance_count=%s distance_min=%s distance_max=%s "
                "distance_avg=%s rerank_applied=%s validation_filtered=%s source_ids=%s graph_behavior=%s",
                len(generated_queries),
                len(raw_candidate_patent_ids),
                len(list(payload.get("source_ids") or [])),
                len(list(payload.get("documents") or [])),
                stats["count"],
                stats["min"],
                stats["max"],
                stats["avg"],
                bool(stage2_rerank_metadata.get("applied")),
                int((validation_metadata or {}).get("filtered_count") or 0),
                list(payload.get("source_ids") or []),
                str(metadata.get("graph_stage2_behavior") or "none"),
            )
        return payload

    def _cancelled_stage2_payload(self) -> dict[str, Any]:
        return asdict(
            PatentStage2RetrievalResult(
                documents=[],
                metadatas=[],
                distances=[],
                references=[],
                reference_objects=[],
                reference_links=[],
                original_links=[],
                source_ids=[],
                metadata={"cancelled": True},
                cache_hit=False,
                negative_cache_hit=False,
                not_found=False,
                timings={},
            )
        )

    def extract_source_ids(self, retrieval_results: dict[str, Any]) -> list[str]:
        source_ids: list[str] = []
        for item in list(retrieval_results.get("metadatas") or []):
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_patent_id(
                item.get("patent_id") or item.get("canonical_patent_id") or item.get("json_stem")
            )
            if normalized and normalized not in source_ids:
                source_ids.append(normalized)
        if source_ids:
            return source_ids
        for item in list(retrieval_results.get("reference_objects") or []):
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            candidate = (
                item.get("patent_id")
                or item.get("canonical_patent_id")
                or (metadata.get("patent_id") if isinstance(metadata, dict) else "")
            )
            normalized = self._normalize_patent_id(candidate)
            if normalized and normalized not in source_ids:
                source_ids.append(normalized)
        if source_ids:
            return source_ids
        for item in list(retrieval_results.get("references") or []):
            normalized = self._normalize_patent_id(item)
            if normalized and normalized not in source_ids:
                source_ids.append(normalized)
        return source_ids

    def _resolve_identifier(self, raw_identifier: str) -> str:
        raw_key = str(raw_identifier or "").upper()
        normalized_key = _normalize_identifier(raw_identifier)
        resolved = self._identity_registry.get(raw_key) or self._identity_registry.get(normalized_key)
        if resolved:
            return str(resolved).strip()
        candidate = self._catalog_by_id.get(raw_key)
        if candidate is not None:
            return candidate.canonical_patent_id
        candidate = self._catalog_identifier_index.get(normalized_key)
        return candidate.canonical_patent_id if candidate is not None else ""

    def _coerce_retrieval_plan(self, retrieval_plan: Any) -> dict[str, Any]:
        if hasattr(retrieval_plan, "__dict__"):
            return dict(getattr(retrieval_plan, "__dict__", {}) or {})
        return dict(retrieval_plan or {})

    def _coerce_retrieval_claims(self, retrieval_claims: Any) -> list[PatentRetrievalClaim]:
        claims: list[PatentRetrievalClaim] = []
        for item in list(retrieval_claims or []):
            if isinstance(item, PatentRetrievalClaim):
                claims.append(item)
                continue
            if not isinstance(item, dict):
                continue
            claims.append(
                PatentRetrievalClaim(
                    claim=str(item.get("claim") or ""),
                    keywords=[str(value).strip() for value in list(item.get("keywords") or []) if str(value).strip()],
                    preferred_sections=[
                        str(value).strip()
                        for value in list(item.get("preferred_sections") or [])
                        if str(value).strip()
                    ],
                    filters=dict(item.get("filters") or {}) if isinstance(item.get("filters"), dict) else {},
                )
            )
        return [claim for claim in claims if _normalize_text(claim.claim)]

    def _retrieval_plan_from_claims(
        self,
        retrieval_claims: list[PatentRetrievalClaim],
        *,
        user_question: str,
    ) -> dict[str, Any]:
        candidate_recall_queries: list[str] = []
        preferred_sections: list[str] = []
        filters: dict[str, Any] = {}
        explicit_patent_ids = [
            self._normalize_patent_id(item)
            for item in _IDENTIFIER_RE.findall(
                " ".join(
                    [
                        str(user_question or ""),
                        *[
                            " ".join([str(claim.claim or ""), *[str(keyword) for keyword in list(claim.keywords or [])]])
                            for claim in retrieval_claims
                        ],
                    ]
                ).upper()
            )
            if self._normalize_patent_id(item)
        ]
        for claim in retrieval_claims:
            query = self._fallback_claim_query(user_question=user_question, retrieval_claim=claim)
            if query and query not in candidate_recall_queries:
                candidate_recall_queries.append(query)
            for section in claim.preferred_sections:
                normalized_section = str(section).strip()
                if normalized_section and normalized_section not in preferred_sections:
                    preferred_sections.append(normalized_section)
            for key, value in dict(claim.filters or {}).items():
                if key not in filters:
                    filters[key] = value
        return {
            "explicit_patent_ids": explicit_patent_ids,
            "candidate_recall_queries": candidate_recall_queries,
            "evidence_localization_queries": list(candidate_recall_queries),
            "preferred_sections": preferred_sections,
            "filters": filters,
        }

    def _candidate_patent_ids_from_plan(self, plan: dict[str, Any], *, user_question: str) -> list[str]:
        candidate_patent_ids = [
            self._normalize_patent_id(item)
            for item in list(plan.get("explicit_patent_ids") or [])
            if self._normalize_patent_id(item)
        ]
        if candidate_patent_ids:
            return list(dict.fromkeys(candidate_patent_ids))
        recall_queries = list(plan.get("candidate_recall_queries") or [])
        if not recall_queries:
            recall_queries = [user_question]
        if not self._vector_search_enabled():
            return list(dict.fromkeys(candidate_patent_ids))
        for query in recall_queries:
            for hit in self._run_abstract_vector_search(str(query), self._top_k_abstract_vector):
                normalized = self._normalize_patent_id(hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem"))
                if normalized and normalized not in candidate_patent_ids:
                    candidate_patent_ids.append(normalized)
        return candidate_patent_ids

    def _localization_queries_from_plan(self, plan: dict[str, Any], *, user_question: str) -> list[str]:
        queries = [
            str(item).strip()
            for item in list(plan.get("evidence_localization_queries") or plan.get("candidate_recall_queries") or [user_question])
            if str(item).strip()
        ]
        return list(dict.fromkeys(queries)) or [user_question]

    def _fallback_claim_query(self, *, user_question: str, retrieval_claim: PatentRetrievalClaim) -> str:
        parts = [_normalize_text(retrieval_claim.claim), *[_normalize_text(item) for item in retrieval_claim.keywords]]
        query = " ".join(part for part in parts if part).strip()
        return query or _normalize_text(user_question)

    def _generate_claim_queries(
        self,
        *,
        user_question: str,
        retrieval_claim: PatentRetrievalClaim,
        query_generation_fn: Callable[..., list[str]] | None = None,
    ) -> list[str]:
        if callable(query_generation_fn):
            generated = list(
                query_generation_fn(
                    user_question=user_question,
                    retrieval_claim=retrieval_claim,
                )
                or []
            )
            queries = [_normalize_text(item) for item in generated if _normalize_text(item)]
            if queries:
                return list(dict.fromkeys(queries))
        fallback_query = self._fallback_claim_query(user_question=user_question, retrieval_claim=retrieval_claim)
        return [fallback_query] if fallback_query else []

    def _prepare_stage2_dual_search_query(self, query: str) -> str:
        preprocessed = self._preprocess_retrieval_query(query)
        return self._expand_retrieval_query(preprocessed)

    def _dual_vector_search_for_query(
        self,
        *,
        query: str,
        retrieval_claim: PatentRetrievalClaim,
        graph_candidate_patent_ids: list[str],
        graph_candidate_keys: set[str],
        rerank_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> tuple[list[_MatchedReference], list[str]]:
        abstract_top_k = self._abstract_dual_search_top_k()
        chunk_top_k = self._chunk_dual_search_top_k()
        abstract_candidate_top_k = self._dual_search_vector_candidate_top_k(abstract_top_k, rerank_fn)
        chunk_candidate_top_k = self._dual_search_vector_candidate_top_k(chunk_top_k, rerank_fn)
        max_where_ids = self._chunk_dual_search_where_max_ids()
        _log_stage2_query_encoding(query=query, claim_text=str(retrieval_claim.claim or ""))
        _log_stage2_vector_request(channel="abstract", query=query, top_k=abstract_candidate_top_k)
        abstract_hits = self._run_abstract_vector_search(query, abstract_candidate_top_k)
        _log_stage2_raw_hits(channel="abstract", query=query, hits=abstract_hits)
        if graph_candidate_keys:
            abstract_hits = [
                hit
                for hit in abstract_hits
                if _normalize_identifier(
                    self._normalize_patent_id(
                        hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
                    )
                )
                in graph_candidate_keys
            ]
        abstract_hits = self._rerank_stage2_hits(
            query=query,
            hits=abstract_hits,
            top_n=abstract_top_k,
            rerank_fn=rerank_fn,
        )
        query_candidate_ids: list[str] = []
        matches: list[_MatchedReference] = []

        for hit in abstract_hits:
            normalized = self._normalize_patent_id(
                hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
            )
            if normalized and normalized not in query_candidate_ids:
                query_candidate_ids.append(normalized)
            abstract_match = self._match_from_abstract_hit(
                hit,
                generated_query=query,
                retrieval_claim=retrieval_claim,
            )
            if abstract_match is not None:
                matches.append(abstract_match)

        if graph_candidate_patent_ids:
            chunk_candidate_ids = list(graph_candidate_patent_ids[:max_where_ids])
        else:
            chunk_candidate_ids = list(query_candidate_ids[:max_where_ids]) if query_candidate_ids else None

        _log_stage2_vector_request(
            channel="chunk",
            query=query,
            top_k=chunk_candidate_top_k,
            candidate_patent_ids=chunk_candidate_ids,
        )
        chunk_hits = self._run_chunk_vector_search(query, chunk_candidate_ids, chunk_candidate_top_k)
        _log_stage2_raw_hits(channel="chunk", query=query, hits=chunk_hits)
        if graph_candidate_keys:
            chunk_hits = [
                hit
                for hit in chunk_hits
                if _normalize_identifier(
                    self._normalize_patent_id(
                        hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
                    )
                )
                in graph_candidate_keys
            ]
        chunk_hits = self._rerank_stage2_hits(
            query=query,
            hits=chunk_hits,
            top_n=chunk_top_k,
            rerank_fn=rerank_fn,
        )
        for hit in chunk_hits:
            normalized = self._normalize_patent_id(
                hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
            )
            if normalized and normalized not in query_candidate_ids:
                query_candidate_ids.append(normalized)
            chunk_match = self._match_from_chunk_hit(
                self._augment_stage2_hit_metadata(
                    hit,
                    stage2_source="chunk",
                    generated_query=query,
                    retrieval_claim=retrieval_claim,
                )
            )
            if chunk_match is not None:
                matches.append(chunk_match)

        return matches, list(dict.fromkeys([*query_candidate_ids, *graph_candidate_patent_ids]))

    def _rerank_stage2_hits(
        self,
        *,
        query: str,
        hits: list[dict[str, Any]],
        top_n: int,
        rerank_fn: Callable[..., dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if not hits or not callable(rerank_fn):
            return list(hits)
        candidates = list(hits[:50])
        documents = [str(hit.get("document") or "") for hit in candidates]
        metadatas = [dict(hit or {}) for hit in candidates]
        try:
            result = dict(
                rerank_fn(
                    query=query,
                    documents=documents,
                    metadatas=metadatas,
                    top_n=min(max(1, int(top_n)), len(candidates)),
                )
                or {}
            )
        except Exception:
            _LOGGER.warning("patent stage2 internal rerank failed; using vector order", exc_info=True)
            return list(hits)
        if bool(result.get("fallback")):
            return list(hits)
        selected: list[dict[str, Any]] = []
        used: set[int] = set()
        result_documents = list(result.get("documents") or [])
        result_metadatas = list(result.get("metadatas") or [])
        for result_index, metadata in enumerate(result_metadatas):
            result_document = result_documents[result_index] if result_index < len(result_documents) else None
            metadata_patent_id = self._normalize_patent_id(dict(metadata or {}).get("patent_id"))
            for index, hit in enumerate(candidates):
                if index in used:
                    continue
                hit_patent_id = self._normalize_patent_id(
                    hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
                )
                if metadata_patent_id and metadata_patent_id != hit_patent_id:
                    continue
                if result_document is not None and str(result_document) != str(hit.get("document") or ""):
                    continue
                selected.append(hit)
                used.add(index)
                break
        if not selected:
            for index in list(result.get("indices") or []):
                try:
                    selected.append(candidates[int(index)])
                except Exception:
                    continue
        return selected or list(hits)

    @staticmethod
    def _dual_search_vector_candidate_top_k(final_top_k: int, rerank_fn: Callable[..., dict[str, Any]] | None) -> int:
        if callable(rerank_fn):
            return 50
        return max(1, int(final_top_k))

    @staticmethod
    def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
        try:
            value = int(str(os.getenv(name, default)).strip())
        except Exception:
            value = int(default)
        return max(minimum, min(maximum, value))

    def _abstract_dual_search_top_k(self) -> int:
        configured = self._env_int("PATENT_ABSTRACT_SEARCH_TOPK", 25, minimum=1, maximum=500)
        return max(configured, int(self._top_k_abstract_vector), 8)

    def _chunk_dual_search_top_k(self) -> int:
        configured = self._env_int("PATENT_CHUNK_SEARCH_TOPK", 10, minimum=1, maximum=500)
        return max(configured, int(self._top_k_chunk_vector), 10)

    def _chunk_dual_search_where_max_ids(self) -> int:
        return self._env_int("PATENT_CHUNK_WHERE_MAX_IDS", 60, minimum=1, maximum=1000)

    def _expand_retrieval_query(self, query: str) -> str:
        if not query or not query.strip():
            return query
        if callable(self._query_expander):
            try:
                expanded = str(self._query_expander(query) or "").strip()
                return expanded or query
            except Exception:
                _LOGGER.warning("patent stage2 query expansion failed; using preprocessed query", exc_info=True)
                return query
        try:
            if self._query_expander_instance is None:
                from server.patent.query_expander import QueryExpander

                self._query_expander_instance = QueryExpander()
            expanded = str(self._query_expander_instance.expand(query) or "").strip()
            return expanded or query
        except Exception:
            _LOGGER.warning("patent stage2 query expansion unavailable; using preprocessed query", exc_info=True)
            return query

    def _preprocess_retrieval_query(self, query: str) -> str:
        if not query:
            return ""
        query = self._normalize_chemical_notation(query)
        synonyms = {
            "PEG": "聚乙二醇",
            "LiFePO4": "磷酸铁锂 LFP",
            "LFP": "LiFePO4 磷酸铁锂",
            "磷酸铁锂": "LiFePO4 LFP",
            "PVDF": "聚偏氟乙烯",
            "NMP": "N-甲基吡咯烷酮",
            "CMC": "羧甲基纤维素钠",
            "SBR": "丁苯橡胶",
            "SP": "导电炭黑 Super P",
            "Super P": "导电炭黑 SP",
            "聚乙二醇": "PEG",
            "聚偏氟乙烯": "PVDF",
            "N-甲基吡咯烷酮": "NMP",
            "羧甲基纤维素钠": "CMC",
            "丁苯橡胶": "SBR",
            "导电炭黑": "SP Super P",
            "压实密度": "compaction density",
            "振实密度": "tap density",
            "compaction density": "压实密度",
            "tap density": "振实密度",
        }
        extensions: list[str] = []
        for abbr, synonym in synonyms.items():
            pattern = r"\b" + re.escape(abbr) + r"\b"
            if re.search(pattern, query, flags=re.IGNORECASE):
                for item in synonym.split():
                    if item not in extensions:
                        extensions.append(item)
        if extensions:
            query = f"{query} {' '.join(extensions)}"
        query = re.sub(r"\(|\)|OR|AND|\"", "", query)
        query = re.sub(r"[;,.。；，、]", " ", query)
        cleaned_keywords: list[str] = []
        for keyword in query.split():
            keyword = keyword.strip()
            if not keyword or len(keyword) >= 20:
                continue
            keyword = re.sub(r"[^\w\u4e00-\u9fff°C°:/\\-]", "", keyword, flags=re.UNICODE)
            if keyword and keyword not in {"的", "和", "与", "或", "等", "中", "在", "于", "对", "由"}:
                cleaned_keywords.append(keyword)
        unique_keywords: list[str] = []
        seen: set[str] = set()
        for keyword in cleaned_keywords:
            if keyword in seen:
                continue
            seen.add(keyword)
            unique_keywords.append(keyword)
        return " ".join(unique_keywords[:15])

    @staticmethod
    def _normalize_chemical_notation(query: str) -> str:
        if not query:
            return query
        chemical_mappings = {
            "fe2p": "Fe2P",
            "fe2p2o7": "Fe2P2O7",
            "li4p2o7": "Li4P2O7",
            "fe2o3": "Fe2O3",
            "feo": "FeO",
            "fe3o4": "Fe3O4",
            "γ-fe2o3": "γ-Fe2O3",
            "α-fe2o3": "α-Fe2O3",
            "lifepo4": "LiFePO4",
            "lfp": "LFP",
            "li2co3": "Li2CO3",
            "nh4h2po4": "NH4H2PO4",
            "fec2o4": "FeC2O4",
        }
        result = query.lower()
        for lower_case, proper_case in chemical_mappings.items():
            result = re.sub(r"\b" + re.escape(lower_case) + r"\b", proper_case, result, flags=re.IGNORECASE)
        return result

    @staticmethod
    def _dedupe_matches_by_first_200_chars(matches: list[_MatchedReference]) -> list[_MatchedReference]:
        seen: set[str] = set()
        deduped: list[_MatchedReference] = []
        for match in matches:
            key = _document_first_200_key(match.snippet_text)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(match)
        return deduped

    def _augment_stage2_hit_metadata(
        self,
        hit: dict[str, Any],
        *,
        stage2_source: str,
        generated_query: str,
        retrieval_claim: PatentRetrievalClaim,
    ) -> dict[str, Any]:
        enriched = dict(hit or {})
        enriched["stage2_source"] = str(stage2_source)
        channel_by_source = {
            "abstract": "abstract_vector",
            "chunk": "chunk_vector_candidate",
        }
        enriched["stage2_channel"] = channel_by_source.get(str(stage2_source), str(stage2_source))
        enriched["generated_query"] = str(generated_query)
        enriched["claim_text"] = str(retrieval_claim.claim or "")
        enriched["claim_keywords"] = list(retrieval_claim.keywords or [])
        enriched["preferred_sections"] = list(retrieval_claim.preferred_sections or [])
        return enriched

    def _match_from_abstract_hit(
        self,
        hit: dict[str, Any],
        *,
        generated_query: str,
        retrieval_claim: PatentRetrievalClaim,
    ) -> _MatchedReference | None:
        enriched_hit = self._augment_stage2_hit_metadata(
            hit,
            stage2_source="abstract",
            generated_query=generated_query,
            retrieval_claim=retrieval_claim,
        )
        patent_id = self._normalize_patent_id(
            enriched_hit.get("patent_id") or enriched_hit.get("canonical_patent_id") or enriched_hit.get("json_stem")
        )
        if not patent_id:
            return None
        record = self._ensure_catalog_record(patent_id)
        if record is None:
            return None
        snippet_text = str(enriched_hit.get("document") or record.abstract_text or "").strip()
        if not snippet_text:
            return None
        return _MatchedReference(
            record=record,
            snippet_text=snippet_text,
            section_type="abstract",
            section_label="Abstract",
            claim_number=None,
            paragraph_id=None,
            abstract_score=self._distance_to_score(enriched_hit.get("abstract_score"), enriched_hit.get("distance")),
            chunk_score=None,
            metadata=enriched_hit,
        )

    def _dedupe_matches_by_prefix(self, matches: list[_MatchedReference]) -> list[_MatchedReference]:
        ordered = list(matches)
        ordered.sort(
            key=lambda item: (
                float(item.chunk_score or 0.0),
                float(item.abstract_score or 0.0),
                item.record.publication_date,
            ),
            reverse=True,
        )
        deduped: list[_MatchedReference] = []
        seen_prefixes: set[str] = set()
        for match in ordered:
            prefix_key = _document_prefix_key(match.snippet_text)
            if prefix_key and prefix_key in seen_prefixes:
                continue
            if prefix_key:
                seen_prefixes.add(prefix_key)
            deduped.append(match)
        return deduped

    def _merge_targeted_matches(self, match_groups: list[list[_MatchedReference]]) -> list[_MatchedReference]:
        merged: dict[tuple[str, str, int | None, str | None, str], _MatchedReference] = {}
        for matches in match_groups:
            for match in matches:
                key = (
                    match.record.canonical_patent_id,
                    match.section_type,
                    match.claim_number,
                    match.paragraph_id,
                    match.snippet_text,
                )
                current = merged.get(key)
                if current is None or float(match.chunk_score or 0.0) > float(current.chunk_score or 0.0):
                    merged[key] = match
        ordered = list(merged.values())
        ordered.sort(key=lambda item: (float(item.chunk_score or 0.0), float(item.abstract_score or 0.0), item.record.publication_date), reverse=True)
        return ordered

    def _candidate_from_match(self, match: _MatchedReference) -> dict[str, Any]:
        metadata = dict(match.metadata or {})
        metadata.setdefault("patent_id", match.record.canonical_patent_id)
        metadata.setdefault("section_type", match.section_type)
        return {
            "document": match.snippet_text,
            "metadata": metadata,
            "score": match.chunk_score if match.chunk_score is not None else match.abstract_score,
        }

    def _mark_explicit_id_match(self, match: _MatchedReference) -> _MatchedReference:
        metadata = dict(match.metadata or {})
        metadata["patent_id"] = match.record.canonical_patent_id
        metadata["section_type"] = match.section_type
        metadata["exact_id_match"] = True
        return replace(match, metadata=metadata)

    def _apply_stage2_rerank(
        self,
        *,
        matches: list[_MatchedReference],
        query: str,
        toggles: Any,
        rerank_fn: Callable[..., dict[str, Any]] | None,
    ) -> tuple[list[_MatchedReference], dict[str, Any]]:
        if not toggles.rerank_enabled:
            return list(matches), {"enabled": False, "applied": False, "fallback": False}
        if not callable(rerank_fn):
            _LOGGER.info(
                "patent stage2 rerank skipped provider=%s reason=no_callable candidates=%s",
                str(toggles.rerank_provider),
                len(matches),
            )
            return list(matches), {"enabled": True, "applied": False, "fallback": False, "provider": toggles.rerank_provider}
        candidates = list(matches)[: max(1, int(toggles.rerank_candidates))]
        documents = [match.snippet_text for match in candidates]
        metadatas = [self._candidate_from_match(match)["metadata"] for match in candidates]
        _LOGGER.info(
            "patent stage2 rerank start provider=%s model=%s candidates=%s top_n=%s",
            str(toggles.rerank_provider),
            str(toggles.rerank_model),
            len(candidates),
            max(1, int(toggles.rerank_top_patents)),
        )
        try:
            result = dict(
                rerank_fn(
                    query=query,
                    documents=documents,
                    metadatas=metadatas,
                    top_n=max(1, int(toggles.rerank_top_patents)),
                    provider=toggles.rerank_provider,
                    model=toggles.rerank_model,
                )
                or {}
            )
        except Exception:
            _LOGGER.warning(
                "patent stage2 rerank failed provider=%s candidates=%s",
                str(toggles.rerank_provider),
                len(candidates),
                exc_info=True,
            )
            return list(matches), {
                "enabled": True,
                "applied": False,
                "fallback": True,
                "fallback_reason": "request_failed",
            }
        if bool(result.get("fallback")):
            fallback_reason = str(result.get("fallback_reason") or "provider_fallback")
            provider = str(result.get("provider") or toggles.rerank_provider)
            _LOGGER.info(
                "patent stage2 rerank fallback reason=%s provider=%s candidates=%s",
                fallback_reason,
                provider,
                len(candidates),
            )
            return list(matches), {
                "enabled": True,
                "applied": False,
                "fallback": True,
                "fallback_reason": fallback_reason,
                "provider": provider,
            }
        selected: list[_MatchedReference] = []
        used_indexes: set[int] = set()
        result_metadatas = list(result.get("metadatas") or [])
        result_documents = list(result.get("documents") or [])
        for result_index, metadata in enumerate(result_metadatas):
            result_document = result_documents[result_index] if result_index < len(result_documents) else None
            metadata_patent_id = self._normalize_patent_id(dict(metadata or {}).get("patent_id"))
            for index, match in enumerate(candidates):
                if index in used_indexes:
                    continue
                if metadata_patent_id and metadata_patent_id != match.record.canonical_patent_id:
                    continue
                if result_document is not None and str(result_document) != match.snippet_text:
                    continue
                selected.append(match)
                used_indexes.add(index)
                break
        if not selected:
            for index in list(result.get("indices") or []):
                try:
                    selected.append(candidates[int(index)])
                except Exception:
                    continue
        if not selected:
            _LOGGER.info(
                "patent stage2 rerank fallback reason=empty_response provider=%s candidates=%s",
                str(result.get("provider") or toggles.rerank_provider),
                len(candidates),
            )
            return list(matches), {"enabled": True, "applied": False, "fallback": True, "fallback_reason": "empty_response"}
        selected = self._limit_matches_to_patents(selected, max_patents=max(1, int(toggles.rerank_top_patents)))
        _LOGGER.info(
            "patent stage2 rerank applied provider=%s selected=%s",
            str(result.get("provider") or toggles.rerank_provider),
            len(selected),
        )
        return selected, {
            "enabled": True,
            "applied": True,
            "fallback": False,
            "provider": str(result.get("provider") or toggles.rerank_provider),
        }

    def _limit_matches_to_patents(self, matches: list[_MatchedReference], *, max_patents: int) -> list[_MatchedReference]:
        selected: list[_MatchedReference] = []
        seen_patents: set[str] = set()
        for match in list(matches or []):
            patent_id = match.record.canonical_patent_id
            if patent_id in seen_patents:
                continue
            selected.append(match)
            seen_patents.add(patent_id)
            if len(seen_patents) >= max(1, int(max_patents)):
                break
        return selected

    def _apply_c_patent_scoring(
        self,
        *,
        matches: list[_MatchedReference],
        retrieval_claims: list[PatentRetrievalClaim],
        user_question: str,
        graph_controls: dict[str, Any],
        toggles: Any,
    ) -> tuple[list[_MatchedReference], list[dict[str, Any]]]:
        candidate_ids = list(dict.fromkeys(match.record.canonical_patent_id for match in matches))
        table_supplements_by_id: dict[str, list[dict[str, Any]]] = {}
        if toggles.c_table_metric_boost_enabled:
            for patent_id in candidate_ids:
                table_supplements_by_id[patent_id] = [
                    {
                        "table_title": item.table_title,
                        "columns": list(item.columns),
                        "rows": [dict(row) for row in item.rows],
                        "source_image": item.source_image,
                    }
                    for item in self._load_table_supplements(patent_id)
                ]
            _LOGGER.info(
                "patent stage2 c table supplements loaded candidate_patents=%s patents_with_tables=%s",
                len(candidate_ids),
                sum(1 for items in table_supplements_by_id.values() if items),
            )
        hits: list[dict[str, Any]] = []
        for match in matches:
            metadata = dict(match.metadata or {})
            if table_supplements_by_id.get(match.record.canonical_patent_id):
                metadata["table_supplements"] = table_supplements_by_id[match.record.canonical_patent_id]
            hits.append(
                {
                    "patent_id": match.record.canonical_patent_id,
                    "document": match.snippet_text,
                    "section_type": match.section_type,
                    "score": match.chunk_score if match.chunk_score is not None else match.abstract_score,
                    "channel": metadata.get("stage2_channel") or metadata.get("stage2_source") or "",
                    "metadata": metadata,
                }
            )
        intent = derive_patent_retrieval_intent(
            user_question=user_question,
            retrieval_claims=retrieval_claims,
            graph_context=graph_controls,
            explicit_patent_ids=_explicit_patent_ids_for_hard_constraint(user_question, retrieval_claims),
        )
        ranked = aggregate_patent_candidates(
            hits=hits,
            intent=intent,
            table_metric_boost_enabled=toggles.c_table_metric_boost_enabled,
        )
        _LOGGER.info(
            "patent stage2 c scoring completed candidates=%s ranked=%s explicit_ids=%s top_patents=%s",
            len(candidate_ids),
            len(ranked),
            len(intent.explicit_patent_ids),
            [item.patent_id for item in ranked[:5]],
        )
        match_by_patent: dict[str, _MatchedReference] = {}
        for match in matches:
            match_by_patent.setdefault(match.record.canonical_patent_id, match)
        fallback_score_items: list[dict[str, Any]] = []
        if intent.explicit_patent_ids and not ranked:
            selected_matches = []
            for patent_id in intent.explicit_patent_ids:
                fallback_match = self._default_match_for_patent(patent_id)
                if fallback_match is None:
                    continue
                selected_matches.append(fallback_match)
                fallback_score_items.append(
                    {
                        "patent_id": patent_id,
                        "score": 1.0,
                        "reasons": ["explicit_id_fallback"],
                    }
                )
            _LOGGER.info(
                "patent stage2 c explicit fallback selected=%s explicit_ids=%s",
                len(selected_matches),
                list(intent.explicit_patent_ids),
            )
            return selected_matches, fallback_score_items
        selected_matches = [match_by_patent[item.patent_id] for item in ranked if item.patent_id in match_by_patent]
        metadata = [
            {
                "patent_id": item.patent_id,
                "score": item.score,
                "reasons": list(item.reasons),
            }
            for item in ranked
        ]
        if intent.explicit_patent_ids:
            return selected_matches, metadata
        return selected_matches or list(matches), metadata

    def _annotate_no_vector_convergence_payload(self, payload: dict[str, Any], *, toggles: Any) -> dict[str, Any]:
        resolved = dict(payload or {})
        metadata = dict(resolved.get("metadata") or {})
        source_ids = list(resolved.get("source_ids") or self.extract_source_ids(resolved))
        metadata["stage2_validation"] = {
            "enabled": bool(toggles.validation_enabled),
            "validated_count": len(source_ids),
            "filtered_count": 0,
            "validation_fallback": False,
        }
        metadata["stage2_no_vector_fallback"] = True
        metadata["stage2_missing_vector_signal"] = "exact_id_or_archive_fallback"
        metadata["stage2_payload_contract_version"] = STAGE2_PAYLOAD_CONTRACT_VERSION
        for key in ("documents", "metadatas", "distances", "references", "reference_objects", "reference_links", "original_links", "source_ids"):
            resolved[key] = list(resolved.get(key) or [])
        resolved["source_ids"] = source_ids
        resolved["metadata"] = metadata
        _LOGGER.info(
            "patent stage2 no-vector fallback annotated source_ids=%s validation_enabled=%s",
            source_ids[:5],
            bool(toggles.validation_enabled),
        )
        return resolved

    def _default_match_for_patent(self, canonical_patent_id: str) -> _MatchedReference | None:
        record = self._ensure_catalog_record(canonical_patent_id)
        if record is None:
            return None
        return self._default_match(record)

    def _stage2_payload_from_outcome(
        self,
        outcome: PatentRetrievalOutcome,
        *,
        matches: list[_MatchedReference] | None = None,
    ) -> dict[str, Any]:
        resolved_matches = list(matches or [])
        payload = PatentStage2RetrievalResult(
            documents=[match.snippet_text for match in resolved_matches],
            metadatas=[dict(match.metadata or {}) for match in resolved_matches],
            distances=[self._distance_from_match(match) for match in resolved_matches],
            references=list(outcome.references),
            reference_objects=list(outcome.reference_objects),
            reference_links=list(outcome.reference_links),
            original_links=list(outcome.original_links),
            source_ids=self.extract_source_ids(
                {
                    "references": list(outcome.references),
                    "reference_objects": list(outcome.reference_objects),
                }
            ),
            metadata={
                "retrieval_backend": outcome.retrieval_backend,
                "retrieval_version": outcome.retrieval_version,
                "catalog_index_version": outcome.catalog_index_version,
            },
            cache_hit=bool(outcome.cache_hit),
            negative_cache_hit=bool(outcome.negative_cache_hit),
            not_found=bool(outcome.not_found),
            timings=dict(outcome.timings or {}),
        )
        return asdict(payload)

    @staticmethod
    def _distance_from_match(match: _MatchedReference) -> float | None:
        metadata = dict(match.metadata or {})
        distance = metadata.get("distance")
        if distance is not None:
            try:
                return float(distance)
            except Exception:
                return None
        if match.chunk_score is not None:
            return max(0.0, (1.0 / float(match.chunk_score)) - 1.0) if float(match.chunk_score) > 0 else None
        if match.abstract_score is not None:
            return max(0.0, (1.0 / float(match.abstract_score)) - 1.0) if float(match.abstract_score) > 0 else None
        return None

    @staticmethod
    def _build_identifier_registry(identity_registry: dict[str, str | None]) -> dict[str, str | None]:
        registry: dict[str, str | None] = {}
        for key, value in identity_registry.items():
            raw_key = str(key).upper()
            normalized_key = _normalize_identifier(key)
            registry[raw_key] = value
            if normalized_key:
                registry[normalized_key] = value
        return registry

    @staticmethod
    def _build_catalog_identifier_index(catalog_records: list[PatentCatalogRecord]) -> dict[str, PatentCatalogRecord]:
        index: dict[str, PatentCatalogRecord] = {}
        for record in catalog_records:
            for candidate in (record.canonical_patent_id, record.publication_number, record.application_number):
                normalized = _normalize_identifier(candidate or "")
                if normalized and normalized not in index:
                    index[normalized] = record
        return index

    def _vector_search_enabled(self) -> bool:
        return self._vector_runtime_enabled and callable(self._abstract_vector_search) and callable(self._chunk_vector_search)

    def _ensure_catalog_record(self, canonical_patent_id: str) -> PatentCatalogRecord | None:
        normalized = str(canonical_patent_id or "").strip().upper()
        record = self._catalog_by_id.get(normalized)
        if record is not None:
            return record
        if self._archive_loader is None:
            return None
        loader = getattr(self._archive_loader, "load_catalog_record", None)
        if not callable(loader):
            return None
        with self._catalog_lock:
            cached = self._catalog_by_id.get(normalized)
            if cached is not None:
                return cached
            record = loader(normalized)
            if isinstance(record, PatentCatalogRecord):
                self._catalog_by_id[normalized] = record
                self._catalog_identifier_index = self._build_catalog_identifier_index(list(self._catalog_by_id.values()))
                return record
            return None

    def _vector_matches(
        self,
        *,
        question: str,
        candidate_patent_ids: list[str] | None,
        force_backend: str,
    ) -> list[_MatchedReference]:
        if not self._vector_search_enabled():
            return []
        resolved_candidate_ids = [self._normalize_patent_id(item) for item in list(candidate_patent_ids or []) if self._normalize_patent_id(item)]
        has_candidate_filter = bool(resolved_candidate_ids)
        if not resolved_candidate_ids:
            abstract_hits = self._run_abstract_vector_search(question, self._top_k_abstract_vector)
            for hit in abstract_hits:
                normalized = self._normalize_patent_id(hit.get("patent_id") or hit.get("canonical_patent_id"))
                if normalized and normalized not in resolved_candidate_ids:
                    resolved_candidate_ids.append(normalized)
        if not resolved_candidate_ids and force_backend == "exact_id":
            return []
        resolved_candidate_keys = {_normalize_identifier(item) for item in resolved_candidate_ids}
        chunk_hits = self._run_chunk_vector_search(question, resolved_candidate_ids or None, self._top_k_chunk_vector)
        if not chunk_hits:
            return []

        grouped: dict[tuple[str, str, int | None, str | None, str], _MatchedReference] = {}
        for hit in chunk_hits:
            match = self._match_from_chunk_hit(hit)
            if match is None:
                continue
            if (has_candidate_filter or resolved_candidate_keys) and _normalize_identifier(match.record.canonical_patent_id) not in resolved_candidate_keys:
                continue
            key = (
                match.record.canonical_patent_id,
                match.section_type,
                match.claim_number,
                match.paragraph_id,
                match.snippet_text,
            )
            current = grouped.get(key)
            if current is None or float(match.chunk_score or 0.0) > float(current.chunk_score or 0.0):
                grouped[key] = match
        ordered = list(grouped.values())
        ordered.sort(key=lambda item: (float(item.chunk_score or 0.0), float(item.abstract_score or 0.0), item.record.publication_date), reverse=True)
        return ordered

    def _match_from_chunk_hit(self, hit: dict[str, Any]) -> _MatchedReference | None:
        patent_id = self._normalize_patent_id(hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem"))
        if not patent_id:
            return None
        record = self._ensure_catalog_record(patent_id)
        if record is None:
            return None
        source_file = str(hit.get("source_file") or "").strip()
        chunk_index = int(hit.get("chunk_index") or 0)
        snippet_text = str(hit.get("document") or hit.get("chroma:document") or "").strip()
        abstract_score = self._distance_to_score(hit.get("abstract_score"), hit.get("abstract_distance"))
        chunk_score = self._distance_to_score(hit.get("chunk_score"), hit.get("distance"))
        if "权利要求" in source_file:
            claim_number = chunk_index + 1 if chunk_index >= 0 else None
            return _MatchedReference(
                record=record,
                snippet_text=snippet_text,
                section_type="claim",
                section_label=f"Claim {claim_number or 1}",
                claim_number=claim_number or 1,
                paragraph_id=None,
                abstract_score=abstract_score,
                chunk_score=chunk_score,
                metadata=dict(hit or {}),
            )
        paragraph_id = f"p-{chunk_index + 1:03d}"
        return _MatchedReference(
            record=record,
            snippet_text=snippet_text,
            section_type="description",
            section_label=f"Paragraph {paragraph_id}",
            claim_number=None,
            paragraph_id=paragraph_id,
            abstract_score=abstract_score,
            chunk_score=chunk_score,
            metadata=dict(hit or {}),
        )

    def _normalize_patent_id(self, value: Any) -> str:
        raw = str(value or "").strip().upper()
        if not raw:
            return ""
        resolved = self._resolve_identifier(raw)
        if resolved:
            return resolved
        normalized = _normalize_identifier(raw)
        record = self._catalog_identifier_index.get(normalized)
        if record is not None:
            return record.canonical_patent_id
        return raw

    @staticmethod
    def _distance_to_score(score_value: Any, distance_value: Any) -> float | None:
        try:
            if score_value is not None:
                return float(score_value)
            if distance_value is not None:
                return 1.0 / (1.0 + max(float(distance_value), 0.0))
        except Exception:
            return None
        return None

    def _default_match(self, record: PatentCatalogRecord) -> _MatchedReference | None:
        record = self._hydrate_catalog_record(record)
        if record.claims:
            claim = record.claims[0]
            return _MatchedReference(
                record=record,
                snippet_text=claim.text,
                section_type="claim",
                section_label=f"Claim {claim.claim_number}",
                claim_number=claim.claim_number,
                paragraph_id=None,
            )
        if record.description_snippets:
            paragraph = record.description_snippets[0]
            return _MatchedReference(
                record=record,
                snippet_text=paragraph.text,
                section_type="description",
                section_label=f"Paragraph {paragraph.paragraph_id}",
                claim_number=None,
                paragraph_id=paragraph.paragraph_id,
            )
        return None

    def _metadata_candidates(self, normalized_question: str) -> list[tuple[_MatchedReference, float]]:
        question_tokens = _tokens(normalized_question)
        scored_records: list[tuple[PatentCatalogRecord, float]] = []
        for record in self._catalog_records:
            title_match = _score_overlap(question_tokens, _tokens(record.title))
            abstract_match = _score_overlap(question_tokens, _tokens(record.abstract_text))
            people_match = _score_overlap(question_tokens, _tokens(" ".join(record.applicant_names + record.inventor_names)))
            class_match = _score_overlap(question_tokens, _tokens(" ".join(record.ipc_codes + record.cpc_codes)))
            phrase_coverage = min(1.0, (title_match + abstract_match) / 2.0)
            metadata_score = (
                0.40 * title_match
                + 0.25 * abstract_match
                + 0.10 * people_match
                + 0.15 * class_match
                + 0.10 * phrase_coverage
            )
            if metadata_score > 0:
                scored_records.append((record, metadata_score))
        top_records = sorted(scored_records, key=lambda item: (item[1], item[0].publication_date), reverse=True)[: self._top_k_metadata]
        scored: list[tuple[_MatchedReference, float]] = []
        for record, score in top_records:
            match = self._default_match(record)
            if match is not None:
                scored.append((match, score))
        return scored

    def _fulltext_candidates(self, normalized_question: str) -> list[tuple[_MatchedReference, float]]:
        question_tokens = _tokens(normalized_question)
        scored: list[tuple[_MatchedReference, float]] = []
        for record in self._catalog_records:
            record = self._hydrate_catalog_record(record)
            best_match: _MatchedReference | None = None
            best_score = 0.0
            for claim in record.claims:
                claim_score = 0.60 * _score_overlap(question_tokens, _tokens(claim.text))
                if claim_score > best_score:
                    best_score = claim_score
                    best_match = _MatchedReference(
                        record=record,
                        snippet_text=claim.text,
                        section_type="claim",
                        section_label=f"Claim {claim.claim_number}",
                        claim_number=claim.claim_number,
                        paragraph_id=None,
                    )
            for paragraph in record.description_snippets:
                paragraph_score = 0.40 * _score_overlap(question_tokens, _tokens(paragraph.text))
                if paragraph_score > best_score:
                    best_score = paragraph_score
                    best_match = _MatchedReference(
                        record=record,
                        snippet_text=paragraph.text,
                        section_type="description",
                        section_label=f"Paragraph {paragraph.paragraph_id}",
                        claim_number=None,
                        paragraph_id=paragraph.paragraph_id,
                    )
            if best_match is not None and best_score > 0:
                scored.append((best_match, best_score))
        return sorted(scored, key=lambda item: (item[1], item[0].record.publication_date), reverse=True)[: self._top_k_fulltext]

    def _hydrate_catalog_record(self, record: PatentCatalogRecord) -> PatentCatalogRecord:
        if record.claims or record.description_snippets or self._archive_loader is None:
            return record
        claims_loader = getattr(self._archive_loader, "load_claims", None)
        description_loader = getattr(self._archive_loader, "load_description_snippets", None)
        claims = claims_loader(record.canonical_patent_id) if callable(claims_loader) else []
        paragraphs = description_loader(record.canonical_patent_id) if callable(description_loader) else []
        if not claims and not paragraphs:
            return record
        hydrated = PatentCatalogRecord(
            canonical_patent_id=record.canonical_patent_id,
            publication_number=record.publication_number,
            application_number=record.application_number,
            title=record.title,
            abstract_text=record.abstract_text,
            applicant_names=list(record.applicant_names),
            inventor_names=list(record.inventor_names),
            ipc_codes=list(record.ipc_codes),
            cpc_codes=list(record.cpc_codes),
            claims=list(claims),
            description_snippets=list(paragraphs),
            country=record.country,
            kind_code=record.kind_code,
            publication_date=record.publication_date,
            provider=record.provider,
            original_available=record.original_available,
        )
        self._catalog_by_id[record.canonical_patent_id] = hydrated
        position = self._catalog_record_positions.get(record.canonical_patent_id)
        if position is not None:
            self._catalog_records[position] = hydrated
        return hydrated

    def _run_abstract_vector_search(self, question: str, top_k: int) -> list[dict[str, Any]]:
        if not self._vector_search_enabled() or not callable(self._abstract_vector_search):
            return []
        try:
            return list(self._abstract_vector_search(question, top_k) or [])
        except Exception as exc:
            self._disable_vector_search(exc)
            return []

    def _run_chunk_vector_search(self, question: str, candidate_patent_ids: list[str] | None, top_k: int) -> list[dict[str, Any]]:
        if not self._vector_search_enabled() or not callable(self._chunk_vector_search):
            return []
        try:
            return list(self._chunk_vector_search(question, candidate_patent_ids, top_k) or [])
        except Exception as exc:
            self._disable_vector_search(exc)
            return []

    def _disable_vector_search(self, exc: Exception) -> None:
        with self._vector_runtime_lock:
            if not self._vector_runtime_enabled:
                return
            self._vector_runtime_enabled = False
        _LOGGER.warning("Patent vector retrieval failed; degrading to no-vector mode: %s", exc, exc_info=True)

    def _build_success(
        self,
        backend: str,
        matches: list[_MatchedReference],
        *,
        question: str,
        context: dict[str, Any] | None,
        cache_hit: bool,
        timings: dict[str, int],
        started_at: float,
        include_answer_text: bool = True,
    ) -> PatentRetrievalOutcome:
        if not matches:
            return self._build_not_found(backend, negative_cache_hit=False, timings=timings, started_at=started_at)
        evidences = [self._to_evidence(match) for match in matches]
        references = list(dict.fromkeys(evidence.canonical_patent_id for evidence in evidences))
        reference_objects: list[dict[str, object]] = []
        reference_links: list[dict[str, object]] = []
        original_links: list[dict[str, object]] = []
        for evidence, match in zip(evidences, matches):
            reference_object, reference_link, original_link = _reference_bundle(
                evidence,
                snippet_text=match.snippet_text,
                section_type=match.section_type,
                section_label=match.section_label,
                claim_number=match.claim_number,
                paragraph_id=match.paragraph_id,
            )
            reference_objects.append(reference_object)
            if reference_link is not None:
                reference_links.append(reference_link)
            if original_link is not None:
                original_links.append(original_link)
        resolved_timings = dict(timings)
        answer_text = ""
        if include_answer_text:
            answer_started_at = time.perf_counter()
            answer_text = self._build_answer_text(question=question, context=context, matches=matches, evidences=evidences)
            resolved_timings["answer_build_ms"] = max(1, int((time.perf_counter() - answer_started_at) * 1000))
        resolved_timings["retrieval_total_ms"] = max(1, int((time.perf_counter() - started_at) * 1000))
        return PatentRetrievalOutcome(
            retrieval_backend=backend,  # type: ignore[arg-type]
            retrieval_version=self._retrieval_version,
            catalog_index_version=self._catalog_index_version,
            references=references,
            reference_objects=reference_objects,
            reference_links=reference_links,
            original_links=original_links,
            evidences=evidences,
            answer_text=answer_text,
            cache_hit=cache_hit,
            negative_cache_hit=False,
            not_found=False,
            timings=resolved_timings,
        )

    def _build_not_found(
        self,
        backend: str,
        *,
        negative_cache_hit: bool,
        timings: dict[str, int],
        started_at: float,
    ) -> PatentRetrievalOutcome:
        resolved_timings = dict(timings)
        resolved_timings["retrieval_total_ms"] = max(1, int((time.perf_counter() - started_at) * 1000))
        return PatentRetrievalOutcome(
            retrieval_backend=backend,  # type: ignore[arg-type]
            retrieval_version=self._retrieval_version,
            catalog_index_version=self._catalog_index_version,
            references=[],
            reference_objects=[],
            reference_links=[],
            original_links=[],
            evidences=[],
            cache_hit=False,
            negative_cache_hit=negative_cache_hit,
            not_found=True,
            timings=resolved_timings,
        )

    def _to_evidence(self, match: _MatchedReference) -> PatentEvidence:
        record = match.record
        return PatentEvidence(
            canonical_patent_id=record.canonical_patent_id,
            publication_number=record.publication_number,
            application_number=record.application_number,
            title=record.title,
            abstract_text=record.abstract_text,
            claims=list(record.claims),
            description_snippets=list(record.description_snippets),
            provider=record.provider,
            original_available=record.original_available,
            country=record.country,
            kind_code=record.kind_code,
            publication_date=record.publication_date,
            matched_section_type=match.section_type,
            matched_section_label=match.section_label,
            matched_snippet=match.snippet_text,
            claim_number=match.claim_number,
            paragraph_id=match.paragraph_id,
            table_supplements=self._load_table_supplements(record.canonical_patent_id),
            abstract_score=match.abstract_score,
            chunk_score=match.chunk_score,
            metadata=dict(match.metadata or {}),
        )

    def _load_table_supplements(self, canonical_patent_id: str) -> list[PatentTableSupplement]:
        if not callable(self._table_loader):
            return []
        raw_tables = list(self._table_loader(canonical_patent_id) or [])
        supplements: list[PatentTableSupplement] = []
        for item in raw_tables:
            if isinstance(item, PatentTableSupplement):
                supplements.append(item)
                continue
            if not isinstance(item, dict):
                continue
            rows = []
            for row in list(item.get("rows") or []):
                if not isinstance(row, dict):
                    continue
                rows.append({str(key): str(value) for key, value in row.items()})
            if not rows:
                continue
            supplements.append(
                PatentTableSupplement(
                    table_title=str(item.get("table_title") or "").strip(),
                    columns=[str(value) for value in list(item.get("columns") or []) if str(value).strip()],
                    rows=rows,
                    source_image=str(item.get("source_image") or item.get("_source_image") or "").strip() or None,
                )
            )
        return supplements

    def _build_answer_text(
        self,
        *,
        question: str,
        context: dict[str, Any] | None,
        matches: list[_MatchedReference],
        evidences: list[PatentEvidence],
    ) -> str:
        if callable(self._answer_builder):
            outcome = PatentRetrievalOutcome(
                retrieval_backend="vector_hybrid" if self._vector_search_enabled() else "hybrid_no_vector",
                retrieval_version=self._retrieval_version,
                catalog_index_version=self._catalog_index_version,
                references=list(dict.fromkeys(item.canonical_patent_id for item in evidences)),
                reference_objects=[],
                reference_links=[],
                original_links=[],
                evidences=evidences,
            )
            try:
                answer = self._answer_builder(question=question, retrieval_outcome=outcome, context=context)
                if str(answer or "").strip():
                    return str(answer).strip()
            except Exception:
                pass
        title = matches[0].record.title if matches else "patent"
        snippet = matches[0].snippet_text if matches else ""
        if snippet:
            return f"Patent retrieval answer: {title}。核心证据显示：{snippet}"
        return f"Patent retrieval answer: {title}"

    def _normalized_query_key(self, *, normalized_question: str, retrieval_mode: str) -> str:
        return "|".join(
            [
                normalized_question,
                retrieval_mode,
                "country:*",
                "language:*",
                f"top_k:{self._top_k_metadata}",
                f"fulltext_top_k:{self._top_k_fulltext}",
                f"abstract_top_k:{self._top_k_abstract_vector}",
                f"chunk_top_k:{self._top_k_chunk_vector}",
                f"catalog:{self._catalog_index_version}",
                f"retrieval:{self._retrieval_version}",
            ]
        )

    def _cache_payload(self, outcome: PatentRetrievalOutcome) -> dict[str, Any]:
        return {
            "retrieval_backend": outcome.retrieval_backend,
            "retrieval_version": outcome.retrieval_version,
            "catalog_index_version": outcome.catalog_index_version,
            "references": list(outcome.references),
            "reference_objects": list(outcome.reference_objects),
            "reference_links": list(outcome.reference_links),
            "original_links": list(outcome.original_links),
            "evidences": [asdict(evidence) for evidence in outcome.evidences],
            "answer_text": outcome.answer_text,
            "not_found": bool(outcome.not_found),
            "timings": dict(outcome.timings),
        }

    def _outcome_from_cache(self, payload: dict[str, Any], *, cache_hit: bool) -> PatentRetrievalOutcome:
        evidences = [
            PatentEvidence(
                canonical_patent_id=str(item.get("canonical_patent_id") or ""),
                publication_number=str(item.get("publication_number") or ""),
                application_number=item.get("application_number"),
                title=str(item.get("title") or ""),
                abstract_text=str(item.get("abstract_text") or ""),
                claims=[PatentClaim(**claim) for claim in list(item.get("claims") or [])],
                description_snippets=[PatentDescriptionSnippet(**snippet) for snippet in list(item.get("description_snippets") or [])],
                table_supplements=[PatentTableSupplement(**table) for table in list(item.get("table_supplements") or [])],
                provider=str(item.get("provider") or "patent_source_x"),
                original_available=bool(item.get("original_available", True)),
                country=str(item.get("country") or ""),
                kind_code=str(item.get("kind_code") or ""),
                publication_date=str(item.get("publication_date") or ""),
                matched_section_type=str(item.get("matched_section_type") or ""),
                matched_section_label=str(item.get("matched_section_label") or ""),
                matched_snippet=str(item.get("matched_snippet") or ""),
                claim_number=item.get("claim_number"),
                paragraph_id=item.get("paragraph_id"),
                abstract_score=item.get("abstract_score"),
                chunk_score=item.get("chunk_score"),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in list(payload.get("evidences") or [])
            if isinstance(item, dict)
        ]
        return PatentRetrievalOutcome(
            retrieval_backend=str(payload.get("retrieval_backend") or "metadata_lexical"),  # type: ignore[arg-type]
            retrieval_version=str(payload.get("retrieval_version") or self._retrieval_version),
            catalog_index_version=str(payload.get("catalog_index_version") or self._catalog_index_version),
            references=list(payload.get("references") or []),
            reference_objects=[dict(item) for item in list(payload.get("reference_objects") or []) if isinstance(item, dict)],
            reference_links=[dict(item) for item in list(payload.get("reference_links") or []) if isinstance(item, dict)],
            original_links=[dict(item) for item in list(payload.get("original_links") or []) if isinstance(item, dict)],
            evidences=evidences,
            answer_text=str(payload.get("answer_text") or ""),
            cache_hit=cache_hit,
            negative_cache_hit=False,
            not_found=bool(payload.get("not_found")),
            timings=dict(payload.get("timings") or {}),
        )

    def _get_retrieval_cache(self, normalized_query_key: str) -> dict[str, Any] | None:
        if self._execution_cache is None:
            return None
        getter = getattr(self._execution_cache, "get_retrieval_cache", None)
        if not callable(getter):
            return None
        return getter(normalized_query_key=normalized_query_key)

    def _set_retrieval_cache(self, normalized_query_key: str, payload: dict[str, Any]) -> None:
        if self._execution_cache is None:
            return
        setter = getattr(self._execution_cache, "set_retrieval_cache", None)
        if callable(setter):
            setter(normalized_query_key=normalized_query_key, payload=payload, ttl_seconds=self._cache_ttl_seconds)

    def _get_negative_patent_resolve(self, raw_identifier: str) -> dict[str, Any] | None:
        if self._execution_cache is None:
            return None
        getter = getattr(self._execution_cache, "get_negative_patent_resolve", None)
        if callable(getter):
            return getter(raw_identifier=raw_identifier)
        return None

    def _set_negative_patent_resolve(self, raw_identifier: str, payload: dict[str, Any]) -> None:
        if self._execution_cache is None:
            return
        setter = getattr(self._execution_cache, "set_negative_patent_resolve", None)
        if callable(setter):
            setter(raw_identifier=raw_identifier, payload=payload, ttl_seconds=self._negative_ttl_seconds)

    def _get_negative_retrieval(self, normalized_query_key: str) -> dict[str, Any] | None:
        if self._execution_cache is None:
            return None
        getter = getattr(self._execution_cache, "get_negative_retrieval", None)
        if callable(getter):
            return getter(normalized_query_key=normalized_query_key)
        return None

    def _set_negative_retrieval(self, normalized_query_key: str, payload: dict[str, Any]) -> None:
        if self._execution_cache is None:
            return
        setter = getattr(self._execution_cache, "set_negative_retrieval", None)
        if callable(setter):
            setter(normalized_query_key=normalized_query_key, payload=payload, ttl_seconds=self._negative_ttl_seconds)
