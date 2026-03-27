from __future__ import annotations

from typing import Any, Callable, Iterator

from app.modules.generation_pipeline.doi_validation import extract_valid_dois


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

from app.modules.qa_kb.models import QaKbExecutionResult
from app.modules.storage.service import storage_service


def iter_text_chunks(text: str, *, chunk_size: int = 120) -> Iterator[str]:
    value = str(text or "")
    size = max(1, int(chunk_size))
    for index in range(0, len(value), size):
        yield value[index : index + size]


def normalize_reference_objects(values: Any) -> list[dict[str, Any]]:
    reference_objects: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return reference_objects
    for item in values:
        if isinstance(item, dict):
            payload = dict(item)
            doi_values = extract_valid_dois(str(payload.get("doi") or "").strip())
        elif isinstance(item, str):
            doi_values = extract_valid_dois(str(item).strip())
            payload = {}
        else:
            continue
        if not doi_values:
            continue
        for doi in doi_values:
            key = doi.lower()
            if key in seen:
                continue
            seen.add(key)
            cloned = dict(payload)
            cloned["doi"] = doi
            reference_objects.append(cloned)
    return reference_objects


def normalize_references(values: Any) -> list[str]:
    return [str(item.get("doi") or "").strip() for item in normalize_reference_objects(values)]


def build_doi_locations(values: Any) -> dict[str, list[dict[str, Any]]]:
    locations: dict[str, list[dict[str, Any]]] = {}
    for item in normalize_reference_objects(values):
        doi = str(item.get("doi") or "").strip()
        if not doi:
            continue
        page = _coerce_positive_int(item.get("page"))
        page_range = item.get("page_range")
        if page is None and isinstance(page_range, (list, tuple)) and page_range:
            page = _coerce_positive_int(page_range[0])
        location: dict[str, Any] = {}
        if page is not None:
            location["page"] = page
        section_name = str(item.get("section_name") or item.get("section") or "").strip()
        if section_name:
            location["section"] = section_name
        chunk_index = item.get("chunk_index")
        try:
            if chunk_index is not None:
                location["chunk_index"] = int(chunk_index)
        except (TypeError, ValueError):
            pass
        evidence_text = str(item.get("evidence_text") or item.get("sample_text") or "").strip()
        if evidence_text:
            location["source_text"] = evidence_text
            location["source_preview"] = evidence_text
        if not location:
            continue
        default_confidence = "page" if page else "section" if section_name else "chunk" if "chunk_index" in location else "evidence"
        confidence = str(item.get("locator_confidence") or default_confidence).strip()
        if confidence:
            location["confidence"] = confidence
        locations.setdefault(doi, []).append(location)
    return locations


def iter_result_events(
    *,
    result: QaKbExecutionResult,
    sse_event: Callable[[dict[str, Any]], Any],
    chunk_size: int = 120,
) -> Iterator[Any]:
    synthesis_result = result.raw.get("synthesis_result") if isinstance(result.raw, dict) else None
    reference_objects = normalize_reference_objects(
        synthesis_result.get("references") if isinstance(synthesis_result, dict) else [],
    )
    references = normalize_references(reference_objects)
    links = storage_service.build_pdf_links(references)
    metadata = {
        "type": "metadata",
        "query_mode": result.metadata.query_mode,
        "route": result.metadata.route,
        "pipeline_mode": result.metadata.pipeline_mode,
        "use_generation_driven": int(result.metadata.use_generation_driven),
        "stage_timings_ms": result.metadata.stage_timings_ms,
        "stage3_pdf_skipped": result.metadata.stage3_pdf_skipped,
        "stage3_pdf_skip_reason": result.metadata.stage3_pdf_skip_reason,
    }
    yield sse_event(metadata)
    for chunk in iter_text_chunks(result.final_answer, chunk_size=chunk_size):
        yield sse_event({"type": "content", "content": chunk})
    yield sse_event(
        {
            "type": "done",
            "query_mode": result.metadata.query_mode,
            "route": result.metadata.route,
            "doi_count": result.metadata.doi_count,
            "chunk_count": result.metadata.chunk_count,
            "source_count": result.metadata.source_count,
            "final_answer": result.final_answer,
            "references": references,
            "reference_objects": reference_objects,
            "reference_links": links,
            "pdf_links": links,
            "doi_locations": build_doi_locations(reference_objects),
            "timings": dict(result.metadata.stage_timings_ms or {}),
            "metadata": {
                "route": result.metadata.route,
                "query_mode": result.metadata.query_mode,
                "pipeline_mode": result.metadata.pipeline_mode,
            },
        }
    )
