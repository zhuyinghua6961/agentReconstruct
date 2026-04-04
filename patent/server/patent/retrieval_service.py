from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from server.patent.models import PatentRetrievalClaim
from server.patent.original_models import OriginalRequest
from server.patent.original_service import build_original_viewer_uri
from server.patent.retrieval_models import (
    PatentCatalogRecord,
    PatentClaim,
    PatentDescriptionSnippet,
    PatentEvidence,
    PatentStage2RetrievalResult,
    PatentRetrievalOutcome,
    PatentTableSupplement,
)


_IDENTIFIER_RE = re.compile(r"\b(?=[A-Z0-9/.,-]*\d)[A-Z]{2}[A-Z0-9][A-Z0-9/.,-]{4,}[A-Z0-9]\b")
_IDENTIFIER_NORMALIZE_RE = re.compile(r"[^A-Z0-9]")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LOGGER = logging.getLogger("patent.retrieval")


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
        top_k_abstract_vector: int = 8,
        top_k_chunk_vector: int = 8,
        cache_ttl_seconds: int = 60,
        negative_ttl_seconds: int = 15,
        abstract_vector_search: Callable[[str, int], list[dict[str, Any]]] | None = None,
        chunk_vector_search: Callable[[str, list[str] | None, int], list[dict[str, Any]]] | None = None,
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
        self._vector_runtime_enabled = True
        self._table_loader = table_loader
        self._answer_builder = answer_builder
        self._archive_loader = archive_loader

    @property
    def retrieval_version(self) -> str:
        return self._retrieval_version

    @property
    def catalog_index_version(self) -> str:
        return self._catalog_index_version

    def retrieve(self, *, question: str, context: dict[str, Any] | None = None) -> PatentRetrievalOutcome:
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
                        )
                        _LOGGER.info("retrieve complete backend=%s refs=%s", outcome.retrieval_backend, outcome.references)
                        return outcome
            self._set_negative_patent_resolve(identifier, {"not_found": True})
            return self._build_not_found("exact_id", negative_cache_hit=False, timings=timings, started_at=started_at)

        retrieval_mode = "vector_hybrid" if self._vector_search_enabled() else "hybrid_no_vector"
        query_key = self._normalized_query_key(normalized_question=normalized_question, retrieval_mode=retrieval_mode)
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
            )
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
                )
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
                )
                self._set_retrieval_cache(query_key, self._cache_payload(outcome))
                _LOGGER.info("retrieve complete backend=%s refs=%s", outcome.retrieval_backend, outcome.references)
                return outcome

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
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        claims = self._coerce_retrieval_claims(retrieval_claims)
        if claims:
            return self._targeted_retrieve_from_claims(
                retrieval_claims=claims,
                user_question=user_question,
                query_generation_fn=query_generation_fn,
                context=context,
            )
        return self._targeted_retrieve_from_plan(
            retrieval_plan=retrieval_plan,
            user_question=user_question,
            context=context,
        )

    def _targeted_retrieve_from_plan(
        self,
        *,
        retrieval_plan: Any,
        user_question: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plan = self._coerce_retrieval_plan(retrieval_plan)
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
                )
                return self._stage2_payload_from_outcome(outcome)

        if self._vector_search_enabled():
            candidate_patent_ids = self._candidate_patent_ids_from_plan(plan, user_question=user_question)
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
                )
                payload = self._stage2_payload_from_outcome(outcome)
                metadata = dict(payload.get("metadata") or {})
                if candidate_patent_ids:
                    metadata["candidate_patent_ids"] = list(candidate_patent_ids)
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
                    )
                    payload = self._stage2_payload_from_outcome(outcome)
                    metadata = dict(payload.get("metadata") or {})
                    metadata["candidate_patent_ids"] = list(candidate_patent_ids)
                    metadata["retrieval_plan_queries"] = list(localization_queries)
                    metadata["localization_fallback"] = "archive_default_anchor"
                    payload["metadata"] = metadata
                    return payload

        fallback_question = explicit_patent_ids[0] if explicit_patent_ids else user_question
        return self._stage2_payload_from_outcome(self.retrieve(question=fallback_question, context=context))

    def _targeted_retrieve_from_claims(
        self,
        *,
        retrieval_claims: list[PatentRetrievalClaim],
        user_question: str,
        query_generation_fn: Callable[..., list[str]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._vector_search_enabled():
            return self._targeted_retrieve_from_plan(
                retrieval_plan=self._retrieval_plan_from_claims(retrieval_claims, user_question=user_question),
                user_question=user_question,
                context=context,
            )

        started_at = time.perf_counter()
        timings: dict[str, int] = {}
        generated_queries: list[str] = []
        candidate_patent_ids: list[str] = []
        all_matches: list[_MatchedReference] = []

        for claim in retrieval_claims:
            claim_queries = self._generate_claim_queries(
                user_question=user_question,
                retrieval_claim=claim,
                query_generation_fn=query_generation_fn,
            )
            if not claim_queries:
                continue
            for query in claim_queries:
                if query not in generated_queries:
                    generated_queries.append(query)
                abstract_hits = self._run_abstract_vector_search(query, self._top_k_abstract_vector)
                query_candidate_ids: list[str] = []
                query_matches: list[_MatchedReference] = []
                for hit in abstract_hits:
                    normalized = self._normalize_patent_id(
                        hit.get("patent_id") or hit.get("canonical_patent_id") or hit.get("json_stem")
                    )
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
                    if patent_id not in candidate_patent_ids:
                        candidate_patent_ids.append(patent_id)
                chunk_hits = self._run_chunk_vector_search(
                    query,
                    query_candidate_ids or None,
                    self._top_k_chunk_vector,
                )
                for hit in chunk_hits:
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
                all_matches.extend(self._dedupe_matches_by_prefix(query_matches))

        merged_matches = self._dedupe_matches_by_prefix(all_matches)
        if not merged_matches:
            fallback_plan = self._retrieval_plan_from_claims(retrieval_claims, user_question=user_question)
            return self._targeted_retrieve_from_plan(
                retrieval_plan=fallback_plan,
                user_question=user_question,
                context=context,
            )

        outcome = self._build_success(
            "vector_hybrid",
            merged_matches,
            question=user_question,
            context=context,
            cache_hit=False,
            timings=timings,
            started_at=started_at,
        )
        payload = self._stage2_payload_from_outcome(outcome, matches=merged_matches)
        metadata = dict(payload.get("metadata") or {})
        metadata["candidate_patent_ids"] = list(candidate_patent_ids)
        metadata["retrieval_plan_queries"] = list(generated_queries)
        payload["metadata"] = metadata
        payload["source_ids"] = self.extract_source_ids(payload)
        return payload

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
        if not resolved_candidate_ids:
            abstract_hits = self._run_abstract_vector_search(question, self._top_k_abstract_vector)
            for hit in abstract_hits:
                normalized = self._normalize_patent_id(hit.get("patent_id") or hit.get("canonical_patent_id"))
                if normalized and normalized not in resolved_candidate_ids:
                    resolved_candidate_ids.append(normalized)
        if not resolved_candidate_ids and force_backend == "exact_id":
            return []
        chunk_hits = self._run_chunk_vector_search(question, resolved_candidate_ids or None, self._top_k_chunk_vector)
        if not chunk_hits:
            return []

        grouped: dict[tuple[str, str, int | None, str | None, str], _MatchedReference] = {}
        for hit in chunk_hits:
            match = self._match_from_chunk_hit(hit)
            if match is None:
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
        answer_started_at = time.perf_counter()
        answer_text = self._build_answer_text(question=question, context=context, matches=matches, evidences=evidences)
        resolved_timings = dict(timings)
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
