from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import logging
import re
from dataclasses import asdict
from typing import Any, Callable

from server.patent.pdf_service import PatentPdfService
from server.patent.retrieval_models import (
    PatentCatalogRecord,
    PatentEvidenceBundle,
    PatentMatchedEvidence,
    PatentStage3EvidenceResult,
    PatentTableSupplement,
)

_WHITESPACE_RE = re.compile(r"\s+")
_LOGGER = logging.getLogger("patent.stage3")


def _normalize_patent_id(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _document_prefix_key(value: Any, limit: int = 32) -> str:
    normalized = _normalize_text(value)
    return normalized[:limit]


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _distance_to_score(distance: Any) -> float | None:
    resolved = _coerce_float(distance)
    if resolved is None:
        return None
    if resolved < 0:
        return None
    return round(1.0 / (1.0 + resolved), 6)


def _section_label_from_metadata(metadata: dict[str, Any]) -> str:
    label = str(metadata.get("section_label") or "").strip()
    if label:
        return label
    claim_number = metadata.get("claim_number")
    paragraph_id = str(metadata.get("paragraph_id") or "").strip()
    section_type = str(metadata.get("section_type") or metadata.get("stage2_source") or "chunk").strip()
    if claim_number not in (None, ""):
        return f"Claim {claim_number}"
    if paragraph_id:
        return f"Paragraph {paragraph_id}"
    if section_type.lower() == "abstract":
        return "Abstract"
    return section_type.title() or "Matched Evidence"


def _scores_from_metadata(metadata: dict[str, Any], *, distance: Any) -> dict[str, float | None]:
    stage2_source = str(metadata.get("stage2_source") or metadata.get("section_type") or "").strip().lower()
    abstract_score = _coerce_float(metadata.get("abstract_score"))
    chunk_score = _coerce_float(metadata.get("chunk_score"))
    fallback_score = _distance_to_score(distance if distance is not None else metadata.get("distance"))
    if stage2_source == "abstract" and abstract_score is None:
        abstract_score = fallback_score
    if stage2_source != "abstract" and chunk_score is None:
        chunk_score = fallback_score
    return {
        "abstract_score": abstract_score,
        "chunk_score": chunk_score,
    }


def _anchor_from_metadata(metadata: dict[str, Any]) -> dict[str, object]:
    anchor: dict[str, object] = {}
    if metadata.get("claim_number") not in (None, ""):
        anchor["claim_number"] = metadata.get("claim_number")
    if str(metadata.get("paragraph_id") or "").strip():
        anchor["paragraph_id"] = str(metadata.get("paragraph_id")).strip()
    if str(metadata.get("stage2_source") or "").strip():
        anchor["stage2_source"] = str(metadata.get("stage2_source")).strip()
    generated_query = str(metadata.get("generated_query") or "").strip()
    if generated_query:
        anchor["generated_query"] = generated_query
    return anchor


def _metadata_patent_id(metadata: dict[str, Any]) -> str:
    return _normalize_patent_id(
        metadata.get("patent_id") or metadata.get("canonical_patent_id") or metadata.get("json_stem")
    )


def _build_retrieval_matched_evidence(
    *,
    retrieval_results: dict[str, Any],
    patent_id: str,
    max_snippets_per_patent: int,
) -> list[PatentMatchedEvidence]:
    documents = list(retrieval_results.get("documents") or [])
    metadatas = list(retrieval_results.get("metadatas") or [])
    distances = list(retrieval_results.get("distances") or [])
    matched: list[PatentMatchedEvidence] = []
    seen_prefixes: set[str] = set()

    total = max(len(documents), len(metadatas))
    for index in range(total):
        metadata = dict(metadatas[index] or {}) if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
        if _metadata_patent_id(metadata) != patent_id:
            continue
        text = str(documents[index] if index < len(documents) else "").strip()
        if not text:
            continue
        prefix = _document_prefix_key(text)
        if prefix and prefix in seen_prefixes:
            continue
        if len(matched) >= max_snippets_per_patent:
            break
        if prefix:
            seen_prefixes.add(prefix)
        matched.append(
            PatentMatchedEvidence(
                section_type=str(metadata.get("section_type") or metadata.get("stage2_source") or "chunk").strip(),
                section_label=_section_label_from_metadata(metadata),
                text=text,
                anchor=_anchor_from_metadata(metadata),
                scores=_scores_from_metadata(
                    metadata,
                    distance=distances[index] if index < len(distances) else None,
                ),
            )
        )
    return matched


def _build_legacy_matched_evidence(
    *,
    retrieval_results: dict[str, Any],
    patent_id: str,
    max_snippets_per_patent: int,
) -> list[PatentMatchedEvidence]:
    seen_prefixes: set[str] = set()
    matched: list[PatentMatchedEvidence] = []
    for item in list(retrieval_results.get("reference_objects") or []):
        if not isinstance(item, dict):
            continue
        candidate_patent_id = _normalize_patent_id(item.get("canonical_patent_id") or item.get("patent_id"))
        if candidate_patent_id != patent_id:
            continue
        text = str(item.get("snippet") or "").strip()
        if not text:
            continue
        prefix = _document_prefix_key(text)
        if prefix and prefix in seen_prefixes:
            continue
        if len(matched) >= max_snippets_per_patent:
            break
        if prefix:
            seen_prefixes.add(prefix)
        anchor = dict(item.get("anchor") or {})
        matched.append(
            PatentMatchedEvidence(
                section_type=str(item.get("section_type") or "").strip(),
                section_label=str(item.get("section_label") or "").strip(),
                text=text,
                anchor=anchor,
                scores={
                    "abstract_score": _coerce_float(dict(item.get("scores") or {}).get("abstract_score")),
                    "chunk_score": _coerce_float(dict(item.get("scores") or {}).get("chunk_score")),
                },
            )
        )
    return matched


def _table_columns(table: PatentTableSupplement) -> list[str]:
    if table.columns:
        return [str(value) for value in table.columns if str(value).strip()]
    derived: list[str] = []
    for row in list(table.rows):
        for key in row.keys():
            text = str(key).strip()
            if text and text not in derived:
                derived.append(text)
    return derived


def _table_to_markdown(table: PatentTableSupplement) -> str:
    columns = _table_columns(table)
    title = str(table.table_title or "表格证据").strip()
    if not columns:
        return f"### {title}"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    rows = [
        "| " + " | ".join(str(row.get(column) or "") for column in columns) + " |"
        for row in list(table.rows)
    ]
    return "\n".join([f"### {title}", "", header, divider, *rows]).strip()


def _build_table_matched_evidence(table_supplements: list[PatentTableSupplement]) -> list[PatentMatchedEvidence]:
    matched: list[PatentMatchedEvidence] = []
    for index, table in enumerate(table_supplements, start=1):
        matched.append(
            PatentMatchedEvidence(
                section_type="table",
                section_label=str(table.table_title or f"Table {index}").strip(),
                text=_table_to_markdown(table),
                anchor={"source_image": table.source_image} if table.source_image else {},
                scores={},
            )
        )
    return matched


def _split_pdf_text_into_paragraphs(text: str, *, max_chunks: int) -> list[str]:
    chunks: list[str] = []
    for piece in re.split(r"\n\s*\n+", str(text or "").strip()):
        normalized = _normalize_text(piece)
        if not normalized:
            continue
        chunks.append(normalized)
        if len(chunks) >= max(1, int(max_chunks)):
            break
    return chunks


def _load_pdf_matched_evidence(
    *,
    patent_id: str,
    pdf_loader: Callable[[str], dict[str, Any] | None] | None,
    pdf_text_extractor: Callable[[str], str] | None,
    max_chunks_per_patent: int,
) -> tuple[dict[str, object], list[PatentMatchedEvidence]]:
    metadata: dict[str, object] = {}
    matched: list[PatentMatchedEvidence] = []
    if not callable(pdf_loader):
        return metadata, matched
    pdf_document = pdf_loader(patent_id)
    if not isinstance(pdf_document, dict):
        return metadata, matched

    metadata["pdf_document"] = {
        "path": str(pdf_document.get("path") or ""),
        "filename": str(pdf_document.get("filename") or ""),
        "size_bytes": int(pdf_document.get("size_bytes") or 0),
    }
    pdf_path = str(pdf_document.get("path") or "").strip()
    if not pdf_path or not callable(pdf_text_extractor):
        return metadata, matched

    pdf_text = str(pdf_text_extractor(pdf_path) or "").strip()
    for index, paragraph in enumerate(_split_pdf_text_into_paragraphs(pdf_text, max_chunks=max_chunks_per_patent), start=1):
        matched.append(
            PatentMatchedEvidence(
                section_type="pdf_paragraph",
                section_label=f"PDF Paragraph {index}",
                text=paragraph,
                anchor={"pdf_chunk_index": index},
                scores={},
            )
        )
    return metadata, matched


def _cancelled_stage3_payload(*, force_pdf: bool) -> dict[str, Any]:
    return asdict(
        PatentStage3EvidenceResult(
            source_ids=[],
            evidences=[],
            metadata={"force_pdf": bool(force_pdf), "cancelled": True},
        )
    )


def run_stage3_load_patent_evidence(
    *,
    retrieval_results: dict[str, Any],
    source_ids: list[str],
    catalog_loader: Callable[[str], PatentCatalogRecord] | None = None,
    table_loader: Callable[[str], list[PatentTableSupplement]] | None = None,
    pdf_loader: Callable[[str], dict[str, Any] | None] | None = None,
    pdf_text_extractor: Callable[[str], str] | None = None,
    force_pdf: bool = False,
    max_snippets_per_patent: int = 3,
    parallel_workers: int = 1,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    normalized_source_ids = [
        _normalize_patent_id(item)
        for item in list(source_ids or [])
        if _normalize_patent_id(item)
    ]
    if not normalized_source_ids:
        for item in list(retrieval_results.get("metadatas") or []):
            if not isinstance(item, dict):
                continue
            patent_id = _metadata_patent_id(item)
            if patent_id and patent_id not in normalized_source_ids:
                normalized_source_ids.append(patent_id)
    _LOGGER.info(
        "patent stage3 evidence loading start source_id_count=%s parallel_workers=%s force_pdf=%s source_ids=%s retrieval_documents=%s retrieval_reference_objects=%s",
        len(normalized_source_ids),
        max(1, int(parallel_workers or 1)),
        bool(force_pdf),
        normalized_source_ids,
        len(list(retrieval_results.get("documents") or [])),
        len(list(retrieval_results.get("reference_objects") or [])),
    )
    if callable(should_cancel) and should_cancel():
        return _cancelled_stage3_payload(force_pdf=force_pdf)

    resolved_pdf_text_extractor = pdf_text_extractor or (
        lambda pdf_path: PatentPdfService._extract_pdf_text(pdf_path, max_pages=10)
    )
    documents = list(retrieval_results.get("documents") or [])
    metadatas = list(retrieval_results.get("metadatas") or [])
    distances = list(retrieval_results.get("distances") or [])
    retrieval_rows_by_patent_id: dict[str, list[dict[str, Any]]] = {}
    for index in range(max(len(documents), len(metadatas))):
        metadata = dict(metadatas[index] or {}) if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
        patent_id = _metadata_patent_id(metadata)
        if not patent_id:
            continue
        retrieval_rows_by_patent_id.setdefault(patent_id, []).append(
            {
                "document": str(documents[index] if index < len(documents) else "").strip(),
                "metadata": metadata,
                "distance": distances[index] if index < len(distances) else None,
            }
        )

    reference_object_by_patent_id: dict[str, dict[str, Any]] = {}
    for item in list(retrieval_results.get("reference_objects") or []):
        if not isinstance(item, dict):
            continue
        patent_id = _normalize_patent_id(item.get("canonical_patent_id") or item.get("patent_id"))
        if patent_id and patent_id not in reference_object_by_patent_id:
            reference_object_by_patent_id[patent_id] = dict(item)

    reference_link_by_patent_id: dict[str, dict[str, Any]] = {}
    for item in list(retrieval_results.get("reference_links") or []):
        if not isinstance(item, dict):
            continue
        patent_id = _normalize_patent_id(item.get("canonical_patent_id"))
        if patent_id and patent_id not in reference_link_by_patent_id:
            reference_link_by_patent_id[patent_id] = dict(item)

    original_links_by_patent_id: dict[str, list[dict[str, Any]]] = {}
    for item in list(retrieval_results.get("original_links") or []):
        if not isinstance(item, dict):
            continue
        patent_id = _normalize_patent_id(item.get("canonical_patent_id"))
        if patent_id:
            original_links_by_patent_id.setdefault(patent_id, []).append(dict(item))

    def _failed_patent_output(index: int, patent_id: str, exc: Exception) -> dict[str, Any]:
        _LOGGER.warning(
            "patent stage3 evidence loading failed patent_id=%s source_index=%s error=%s",
            patent_id,
            int(index) + 1,
            exc,
        )
        return {"index": index, "patent_id": patent_id, "bundle": None, "ok": False}

    def _build_patent_bundle(index: int, patent_id: str) -> dict[str, Any]:
        catalog_record = catalog_loader(patent_id) if callable(catalog_loader) else None
        reference_object = dict(reference_object_by_patent_id.get(patent_id) or {})
        reference_link = dict(reference_link_by_patent_id[patent_id]) if patent_id in reference_link_by_patent_id else None
        original_links = [dict(item) for item in list(original_links_by_patent_id.get(patent_id) or [])]
        retrieval_rows = list(retrieval_rows_by_patent_id.get(patent_id) or [])
        patent_retrieval_results = {
            "documents": [str(item.get("document") or "") for item in retrieval_rows],
            "metadatas": [dict(item.get("metadata") or {}) for item in retrieval_rows],
            "distances": [item.get("distance") for item in retrieval_rows],
            "reference_objects": [reference_object] if reference_object else [],
            "reference_links": [reference_link] if reference_link else [],
            "original_links": list(original_links),
        }

        matched_evidence = _build_retrieval_matched_evidence(
            retrieval_results=patent_retrieval_results,
            patent_id=patent_id,
            max_snippets_per_patent=max_snippets_per_patent,
        )
        if not matched_evidence:
            matched_evidence = _build_legacy_matched_evidence(
                retrieval_results=patent_retrieval_results,
                patent_id=patent_id,
                max_snippets_per_patent=max_snippets_per_patent,
            )

        table_supplements: list[PatentTableSupplement] = []
        if callable(table_loader):
            for table in list(table_loader(patent_id) or []):
                if isinstance(table, PatentTableSupplement):
                    table_supplements.append(table)

        metadata: dict[str, object] = {}
        if force_pdf:
            pdf_metadata, pdf_matched_evidence = _load_pdf_matched_evidence(
                patent_id=patent_id,
                pdf_loader=pdf_loader,
                pdf_text_extractor=resolved_pdf_text_extractor,
                max_chunks_per_patent=max_snippets_per_patent,
            )
            metadata.update(pdf_metadata)
            matched_evidence.extend(pdf_matched_evidence)

        matched_evidence.extend(_build_table_matched_evidence(table_supplements))
        _LOGGER.info(
            "patent stage3 evidence bundle patent_id=%s matched_evidence=%s tables=%s pdf_loaded=%s",
            patent_id,
            len(matched_evidence),
            len(table_supplements),
            "pdf_document" in metadata,
        )

        publication_number = ""
        title = patent_id
        abstract_text = ""
        if isinstance(catalog_record, PatentCatalogRecord):
            publication_number = catalog_record.publication_number
            title = catalog_record.title or patent_id
            abstract_text = catalog_record.abstract_text
        elif reference_object:
            publication_number = str(reference_object.get("publication_number") or patent_id).strip().upper()
            title = str(reference_object.get("title") or patent_id).strip()
        metadata["publication_number"] = publication_number or patent_id

        score_candidates = [dict(item.scores) for item in matched_evidence if isinstance(item, PatentMatchedEvidence)]
        evidence_bundle = PatentEvidenceBundle(
            canonical_patent_id=patent_id,
            title=title,
            abstract_text=abstract_text,
            matched_evidence=matched_evidence,
            table_supplements=table_supplements,
            reference_object=reference_object,
            reference_link=reference_link,
            original_links=original_links,
            scores={
                "abstract_score": max(
                    (
                        float(item.get("abstract_score") or 0.0)
                        for item in score_candidates
                        if item.get("abstract_score") is not None
                    ),
                    default=0.0,
                )
                or None,
                "chunk_score": max(
                    (
                        float(item.get("chunk_score") or 0.0)
                        for item in score_candidates
                        if item.get("chunk_score") is not None
                    ),
                    default=0.0,
                )
                or None,
            },
            metadata=metadata,
        )
        return {"index": index, "patent_id": patent_id, "bundle": evidence_bundle, "ok": True}

    patent_jobs = list(enumerate(normalized_source_ids))
    evidence_outputs: list[dict[str, Any]] = []
    if len(patent_jobs) <= 1 or int(parallel_workers or 1) <= 1:
        for index, patent_id in patent_jobs:
            if callable(should_cancel) and should_cancel():
                return _cancelled_stage3_payload(force_pdf=force_pdf)
            try:
                evidence_outputs.append(_build_patent_bundle(index, patent_id))
            except Exception as exc:
                evidence_outputs.append(_failed_patent_output(index, patent_id, exc))
    else:
        max_workers = min(max(1, int(parallel_workers)), len(patent_jobs))
        cancelled = False
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            future_map = {executor.submit(_build_patent_bundle, index, patent_id): (index, patent_id) for index, patent_id in patent_jobs}
            pending = set(future_map)
            while pending:
                if callable(should_cancel) and should_cancel():
                    cancelled = True
                    for future in pending:
                        future.cancel()
                    return _cancelled_stage3_payload(force_pdf=force_pdf)
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        evidence_outputs.append(future.result())
                    except Exception as exc:
                        index, patent_id = future_map[future]
                        evidence_outputs.append(_failed_patent_output(index, patent_id, exc))
        finally:
            executor.shutdown(wait=not cancelled, cancel_futures=cancelled)

    successful_outputs = [
        item
        for item in sorted(evidence_outputs, key=lambda value: int(value.get("index", 0)))
        if item.get("ok") and isinstance(item.get("bundle"), PatentEvidenceBundle)
    ]
    successful_source_ids = [str(item.get("patent_id") or "") for item in successful_outputs if str(item.get("patent_id") or "")]
    evidences = [item["bundle"] for item in successful_outputs]
    failed_patent_count = len(normalized_source_ids) - len(successful_source_ids)

    if not evidences and failed_patent_count > 0:
        raise RuntimeError("stage3 evidence loading produced no successful patent bundles")

    bundle = PatentStage3EvidenceResult(
        source_ids=successful_source_ids,
        evidences=evidences,
        metadata={"force_pdf": bool(force_pdf)},
    )
    _LOGGER.info(
        "patent stage3 evidence loading completed source_count=%s evidence_bundle_count=%s failed_patent_count=%s",
        len(successful_source_ids),
        len(evidences),
        failed_patent_count,
    )
    return asdict(bundle)
