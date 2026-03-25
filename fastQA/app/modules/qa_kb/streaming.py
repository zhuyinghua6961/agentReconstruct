from __future__ import annotations

from typing import Any, Callable, Iterator

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
            doi = str(payload.get("doi") or "").strip()
        elif isinstance(item, str):
            doi = str(item).strip()
            payload = {"doi": doi}
        else:
            continue
        if not doi:
            continue
        key = doi.lower()
        if key in seen:
            continue
        seen.add(key)
        payload["doi"] = doi
        reference_objects.append(payload)
    return reference_objects


def normalize_references(values: Any) -> list[str]:
    return [str(item.get("doi") or "").strip() for item in normalize_reference_objects(values)]


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
            "doi_locations": [],
            "timings": dict(result.metadata.stage_timings_ms or {}),
            "metadata": {
                "route": result.metadata.route,
                "query_mode": result.metadata.query_mode,
                "pipeline_mode": result.metadata.pipeline_mode,
            },
        }
    )
