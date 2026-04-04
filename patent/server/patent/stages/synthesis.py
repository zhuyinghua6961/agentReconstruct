from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Callable

from server.patent.answering import (
    build_fallback_patent_answer,
    extract_cited_patent_ids,
    sanitize_patent_id_citations,
)
from server.patent.retrieval_models import (
    PatentDescriptionSnippet,
    PatentEvidence,
    PatentRetrievalOutcome,
    PatentSynthesisResult,
    PatentTableSupplement,
)

_LOGGER = logging.getLogger("patent.stage4")


def _normalize_source_ids(patent_evidence_bundle: dict[str, Any]) -> list[str]:
    normalized: list[str] = []
    for item in list(patent_evidence_bundle.get("source_ids") or []):
        text = str(item or "").strip().upper()
        if text and text not in normalized:
            normalized.append(text)
    if normalized:
        return normalized
    for item in list(patent_evidence_bundle.get("evidences") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("canonical_patent_id") or "").strip().upper()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _table_from_item(item: dict[str, Any]) -> PatentTableSupplement | None:
    rows: list[dict[str, str]] = []
    for row in list(item.get("rows") or []):
        if not isinstance(row, dict):
            continue
        rows.append({str(key): str(value) for key, value in row.items()})
    if not rows:
        return None
    return PatentTableSupplement(
        table_title=str(item.get("table_title") or "").strip(),
        columns=[str(value) for value in list(item.get("columns") or []) if str(value).strip()],
        rows=rows,
        source_image=str(item.get("source_image") or "").strip() or None,
    )


def _evidences_for_patent(
    *,
    patent_id: str,
    items: list[dict[str, Any]],
    reference_object: dict[str, Any] | None,
) -> list[PatentEvidence]:
    metadata_item = next((item for item in items if str(item.get("kind") or "") == "patent_metadata"), {})
    table_supplements = [
        table
        for item in items
        for table in [_table_from_item(item)]
        if str(item.get("kind") or "") == "table" and table is not None
    ]
    matched_items = [item for item in items if str(item.get("kind") or "") == "matched_snippet"]
    title = str(metadata_item.get("title") or reference_object.get("title") or patent_id).strip()
    abstract_text = str(metadata_item.get("abstract_text") or "").strip()
    publication_number = str(
        metadata_item.get("publication_number")
        or reference_object.get("publication_number")
        or patent_id
    ).strip().upper()
    application_number = str(reference_object.get("application_number") or "").strip() or None
    country = str(reference_object.get("country") or "").strip()
    kind_code = str(reference_object.get("kind_code") or "").strip()
    provider = str(reference_object.get("provider") or "patent_archive").strip()
    original_available = bool(reference_object.get("original_available", True))

    evidences: list[PatentEvidence] = []
    for item in matched_items:
        anchor = dict(item.get("anchor") or {})
        scores = dict(item.get("scores") or {})
        paragraph_id = str(anchor.get("paragraph_id") or "").strip() or None
        evidences.append(
            PatentEvidence(
                canonical_patent_id=patent_id,
                publication_number=publication_number,
                application_number=application_number,
                title=title,
                abstract_text=abstract_text,
                description_snippets=(
                    [PatentDescriptionSnippet(paragraph_id=paragraph_id, text=str(item.get("text") or "").strip())]
                    if paragraph_id is not None and str(item.get("text") or "").strip()
                    else []
                ),
                provider=provider,
                original_available=original_available,
                country=country,
                kind_code=kind_code,
                matched_section_type=str(item.get("section_type") or "").strip(),
                matched_section_label=str(item.get("section_label") or "").strip(),
                matched_snippet=str(item.get("text") or "").strip(),
                claim_number=anchor.get("claim_number"),
                paragraph_id=paragraph_id,
                table_supplements=list(table_supplements),
                abstract_score=scores.get("abstract_score"),
                chunk_score=scores.get("chunk_score"),
                metadata={"anchor": anchor},
            )
        )
    if evidences:
        return evidences
    return [
        PatentEvidence(
            canonical_patent_id=patent_id,
            publication_number=publication_number,
            application_number=application_number,
            title=title,
            abstract_text=abstract_text,
            provider=provider,
            original_available=original_available,
            country=country,
            kind_code=kind_code,
            table_supplements=list(table_supplements),
        )
    ]


def _retrieval_outcome_from_bundle(
    *,
    patent_evidence_bundle: dict[str, Any],
    retrieval_results: dict[str, Any] | None,
) -> PatentRetrievalOutcome:
    retrieval_payload = dict(retrieval_results or {})
    evidences: list[PatentEvidence] = []
    references: list[str] = []
    reference_objects: list[dict[str, object]] = []
    reference_links: list[dict[str, object]] = []
    original_links: list[dict[str, object]] = []
    structured_evidences = [dict(item) for item in list(patent_evidence_bundle.get("evidences") or []) if isinstance(item, dict)]
    if structured_evidences:
        for bundle in structured_evidences:
            patent_id = str(bundle.get("canonical_patent_id") or "").strip().upper()
            if not patent_id:
                continue
            if patent_id not in references:
                references.append(patent_id)
            reference_object = dict(bundle.get("reference_object") or {})
            if reference_object:
                reference_objects.append(reference_object)
            reference_link = dict(bundle.get("reference_link") or {})
            if reference_link:
                reference_links.append(reference_link)
            original_links.extend(
                dict(item)
                for item in list(bundle.get("original_links") or [])
                if isinstance(item, dict)
            )
            table_supplements = [
                _table_from_item(item)
                for item in list(bundle.get("table_supplements") or [])
                if isinstance(item, dict)
            ]
            resolved_tables = [item for item in table_supplements if item is not None]
            matched_items = [dict(item) for item in list(bundle.get("matched_evidence") or []) if isinstance(item, dict)]
            if matched_items:
                evidences.extend(
                    _evidences_for_patent(
                        patent_id=patent_id,
                        items=[
                            {
                                "kind": "patent_metadata",
                                "title": bundle.get("title"),
                                "abstract_text": bundle.get("abstract_text"),
                                "publication_number": dict(bundle.get("metadata") or {}).get("publication_number"),
                            },
                            *[
                                {
                                    "kind": "matched_snippet",
                                    "section_type": item.get("section_type"),
                                    "section_label": item.get("section_label"),
                                    "text": item.get("text"),
                                    "anchor": dict(item.get("anchor") or {}),
                                    "scores": dict(item.get("scores") or {}),
                                }
                                for item in matched_items
                            ],
                            *[
                                {
                                    "kind": "table",
                                    "table_title": table.table_title,
                                    "columns": list(table.columns),
                                    "rows": [dict(row) for row in table.rows],
                                    "source_image": table.source_image,
                                }
                                for table in resolved_tables
                            ],
                        ],
                        reference_object=reference_object,
                    )
                )
            else:
                evidences.append(
                    PatentEvidence(
                        canonical_patent_id=patent_id,
                        publication_number=str(dict(bundle.get("metadata") or {}).get("publication_number") or patent_id),
                        application_number=str(reference_object.get("application_number") or "").strip() or None,
                        title=str(bundle.get("title") or patent_id).strip(),
                        abstract_text=str(bundle.get("abstract_text") or "").strip(),
                        provider=str(reference_object.get("provider") or "patent_archive"),
                        original_available=bool(reference_object.get("original_available", True)),
                        country=str(reference_object.get("country") or ""),
                        kind_code=str(reference_object.get("kind_code") or ""),
                        table_supplements=resolved_tables,
                        abstract_score=dict(bundle.get("scores") or {}).get("abstract_score"),
                        chunk_score=dict(bundle.get("scores") or {}).get("chunk_score"),
                    )
                )
    else:
        evidence_by_patent_id = {
            str(key).strip().upper(): [dict(item) for item in list(value or []) if isinstance(item, dict)]
            for key, value in dict(patent_evidence_bundle.get("evidence_by_patent_id") or {}).items()
        }
        reference_object_by_patent_id = {
            str(item.get("canonical_patent_id") or "").strip().upper(): dict(item)
            for item in list(retrieval_payload.get("reference_objects") or [])
            if isinstance(item, dict) and str(item.get("canonical_patent_id") or "").strip()
        }
        references = list(retrieval_payload.get("references") or _normalize_source_ids(patent_evidence_bundle))
        reference_objects = [dict(item) for item in list(retrieval_payload.get("reference_objects") or []) if isinstance(item, dict)]
        reference_links = [dict(item) for item in list(retrieval_payload.get("reference_links") or []) if isinstance(item, dict)]
        original_links = [dict(item) for item in list(retrieval_payload.get("original_links") or []) if isinstance(item, dict)]
        for patent_id in _normalize_source_ids(patent_evidence_bundle):
            evidences.extend(
                _evidences_for_patent(
                    patent_id=patent_id,
                    items=evidence_by_patent_id.get(patent_id, []),
                    reference_object=reference_object_by_patent_id.get(patent_id, {}),
                )
            )
    return PatentRetrievalOutcome(
        retrieval_backend=str(dict(retrieval_payload.get("metadata") or {}).get("retrieval_backend") or "vector_hybrid"),
        retrieval_version=str(dict(retrieval_payload.get("metadata") or {}).get("retrieval_version") or ""),
        catalog_index_version=str(dict(retrieval_payload.get("metadata") or {}).get("catalog_index_version") or ""),
        references=references,
        reference_objects=reference_objects,
        reference_links=reference_links,
        original_links=original_links,
        evidences=evidences,
        timings=dict(retrieval_payload.get("timings") or {}),
    )


def _programmatic_repair_patent_citations(
    *,
    answer_text: str,
    retrieval_outcome: PatentRetrievalOutcome,
    allowed_patent_ids: list[str],
) -> str:
    text = str(answer_text or "").strip()
    if not text or not allowed_patent_ids:
        return text
    if extract_cited_patent_ids(text):
        return text

    normalized_allowed = [str(item).strip().upper() for item in allowed_patent_ids if str(item).strip()]
    chosen_patent_id = normalized_allowed[0]
    evidence_texts = {
        patent_id: " ".join(
            str(piece or "").strip()
            for evidence in list(retrieval_outcome.evidences)
            if str(evidence.canonical_patent_id or "").strip().upper() == patent_id
            for piece in (
                evidence.title,
                evidence.abstract_text,
                evidence.matched_section_label,
                evidence.matched_snippet,
            )
            if str(piece or "").strip()
        ).lower()
        for patent_id in normalized_allowed
    }
    lowered_answer = text.lower()
    scored = [
        (
            sum(1 for token in set(lowered_answer.split()) if token and token in evidence_texts.get(patent_id, "")),
            patent_id,
        )
        for patent_id in normalized_allowed
    ]
    scored.sort(reverse=True)
    if scored and scored[0][0] > 0:
        chosen_patent_id = scored[0][1]

    if text.endswith(("。", "！", "？", ".", "!", "?")):
        return f"{text} (patent_id={chosen_patent_id})"
    return f"{text} (patent_id={chosen_patent_id})"


def run_stage4_synthesis_with_patent_evidence(
    *,
    user_question: str,
    deep_answer: str,
    patent_evidence_bundle: dict[str, Any],
    retrieval_results: dict[str, Any] | None = None,
    answer_builder: Callable[..., str] | None = None,
    content_callback: Callable[[str], None] | None = None,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    retrieval_outcome = _retrieval_outcome_from_bundle(
        patent_evidence_bundle=patent_evidence_bundle,
        retrieval_results=retrieval_results,
    )
    allowed_patent_ids = _normalize_source_ids(patent_evidence_bundle) or [
        str(item).strip().upper()
        for item in list(retrieval_outcome.references)
        if str(item).strip()
    ]
    synthesis_context = dict(conversation_context or {})
    if deep_answer:
        synthesis_context["stage1_deep_answer"] = str(deep_answer)
    synthesis_context["allowed_patent_ids"] = list(allowed_patent_ids)
    synthesis_context["stage2_retrieval_results"] = dict(retrieval_results or {})
    citation_mode = "fallback"
    used_fallback_builder = False
    _LOGGER.info(
        "patent stage4 synthesis start allowed_patent_ids=%s evidence_count=%s references=%s answer_builder=%s deep_answer_chars=%s",
        allowed_patent_ids,
        len(list(retrieval_outcome.evidences)),
        len(list(retrieval_outcome.references)),
        callable(answer_builder),
        len(str(deep_answer or "")),
    )
    if callable(answer_builder):
        raw_answer = ""
        stream_builder = getattr(answer_builder, "stream", None)
        if callable(stream_builder):
            streamed_chunks: list[str] = []
            for chunk in stream_builder(
                question=user_question,
                retrieval_outcome=retrieval_outcome,
                context=synthesis_context,
            ):
                text = str(chunk or "")
                if not text:
                    continue
                streamed_chunks.append(text)
                if callable(content_callback):
                    content_callback(text)
            raw_answer = "".join(streamed_chunks).strip()
            if raw_answer:
                citation_mode = "answer_builder_stream"
        if not raw_answer:
            raw_answer = str(
                answer_builder(
                    question=user_question,
                    retrieval_outcome=retrieval_outcome,
                    context=synthesis_context,
                )
                or ""
            ).strip()
            citation_mode = "answer_builder"
    else:
        raw_answer = build_fallback_patent_answer(
            question=user_question,
            retrieval_outcome=retrieval_outcome,
            context=synthesis_context,
        ).strip()
        used_fallback_builder = True
        _LOGGER.warning("patent stage4 synthesis using fallback answer builder because no callable answer_builder is configured")
    final_answer, cited_patent_ids, invalid_cited_patent_ids = sanitize_patent_id_citations(
        raw_answer,
        allowed_patent_ids=allowed_patent_ids,
    )
    if allowed_patent_ids and not cited_patent_ids and final_answer:
        repaired_candidate = _programmatic_repair_patent_citations(
            answer_text=final_answer,
            retrieval_outcome=retrieval_outcome,
            allowed_patent_ids=allowed_patent_ids,
        )
        repaired_answer, repaired_cited_patent_ids, repaired_invalid_cited_patent_ids = sanitize_patent_id_citations(
            repaired_candidate,
            allowed_patent_ids=allowed_patent_ids,
        )
        if repaired_answer and repaired_cited_patent_ids:
            final_answer = repaired_answer
            cited_patent_ids = repaired_cited_patent_ids
            invalid_cited_patent_ids = list(dict.fromkeys([*invalid_cited_patent_ids, *repaired_invalid_cited_patent_ids]))
            citation_mode = "programmatic_repair"
    elif callable(answer_builder) and not used_fallback_builder:
        citation_mode = "answer_builder_validated"
    if used_fallback_builder and citation_mode == "fallback" and cited_patent_ids:
        citation_mode = "fallback_validated"
    metadata = dict(dict(retrieval_results or {}).get("metadata") or {})
    metadata.update(
        {
            "source_ids": _normalize_source_ids(patent_evidence_bundle),
            "allowed_patent_ids": list(allowed_patent_ids),
            "cited_patent_ids": list(cited_patent_ids or extract_cited_patent_ids(final_answer)),
            "invalid_cited_patent_ids": list(invalid_cited_patent_ids),
            "citation_format": "(patent_id=公开号)",
            "citation_mode": citation_mode,
            "evidence_patent_count": len(list(patent_evidence_bundle.get("evidences") or []))
            or len(dict(patent_evidence_bundle.get("evidence_by_patent_id") or {})),
            "matched_evidence_count": sum(
                1
                for bundle in list(patent_evidence_bundle.get("evidences") or [])
                for item in list((bundle or {}).get("matched_evidence") or [])
                if isinstance(bundle, dict) and isinstance(item, dict)
            ),
            "table_count": sum(
                1
                for bundle in list(patent_evidence_bundle.get("evidences") or [])
                for item in list((bundle or {}).get("table_supplements") or [])
                if isinstance(bundle, dict) and isinstance(item, dict)
            ),
        }
    )
    result = PatentSynthesisResult(
        success=bool(final_answer),
        final_answer=final_answer,
        references=list(retrieval_outcome.references),
        reference_objects=list(retrieval_outcome.reference_objects),
        reference_links=list(retrieval_outcome.reference_links),
        original_links=list(retrieval_outcome.original_links),
        metadata=metadata,
        answer_text=final_answer,
    )
    _LOGGER.info(
        "patent stage4 synthesis completed success=%s citation_mode=%s final_answer_chars=%s cited_patent_ids=%s invalid_cited_patent_ids=%s",
        bool(final_answer),
        citation_mode,
        len(final_answer),
        list(metadata.get("cited_patent_ids") or []),
        list(metadata.get("invalid_cited_patent_ids") or []),
    )
    return asdict(result)
