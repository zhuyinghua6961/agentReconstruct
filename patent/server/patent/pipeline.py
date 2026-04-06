from __future__ import annotations

from typing import Any

from server.patent.retrieval_models import PatentRetrievalOutcome
from server.schemas.request_models import PatentAskRequest
from server.services.mode_profiles import PatentModeProfile, get_patent_mode_profile


def build_patent_reference_instruction(references: list[Any] | None) -> str:
    normalized = [str(item).strip() for item in list(references or []) if str(item).strip()]
    if not normalized:
        return ""
    return "引用知识库结论时仅可使用这些专利号：" + "、".join(normalized)


def build_retrieval_evidence_context(retrieval_outcome: PatentRetrievalOutcome) -> str:
    lines: list[str] = []
    for evidence in list(retrieval_outcome.evidences or [])[:3]:
        patent_id = str(evidence.canonical_patent_id or evidence.publication_number or "").strip()
        title = str(evidence.title or patent_id).strip()
        segments: list[str] = []
        if str(evidence.matched_section_label or "").strip() and str(evidence.matched_snippet or "").strip():
            segments.append(f"{str(evidence.matched_section_label).strip()}：{_truncate(evidence.matched_snippet, limit=180)}")
        elif str(evidence.matched_snippet or "").strip():
            segments.append(f"命中片段：{_truncate(evidence.matched_snippet, limit=180)}")
        if str(evidence.abstract_text or "").strip():
            segments.append(f"摘要：{_truncate(evidence.abstract_text, limit=180)}")
        if list(evidence.table_supplements or []):
            segments.append(f"表格：{_summarize_table(evidence.table_supplements[0])}")
        if not segments:
            segments.append("当前仅命中专利元数据。")
        lines.append(f"{patent_id}《{title}》；" + "；".join(segment for segment in segments if segment))
    return _truncate("\n".join(line for line in lines if line), limit=1200)


def build_stage3_evidence_context(stage3_payload: dict[str, Any] | None) -> str:
    evidences = [dict(item) for item in list(dict(stage3_payload or {}).get("evidences") or []) if isinstance(item, dict)]
    lines: list[str] = []
    for evidence in evidences[:3]:
        patent_id = str(evidence.get("canonical_patent_id") or dict(evidence.get("metadata") or {}).get("publication_number") or "").strip()
        title = str(evidence.get("title") or patent_id).strip()
        segments: list[str] = []
        matched_evidence = [dict(item) for item in list(evidence.get("matched_evidence") or []) if isinstance(item, dict)]
        for item in matched_evidence[:2]:
            label = str(item.get("section_label") or item.get("section_type") or "命中片段").strip()
            text = str(item.get("text") or "").strip()
            if text:
                segments.append(f"{label}：{_truncate(text, limit=180)}")
        if not matched_evidence and str(evidence.get("abstract_text") or "").strip():
            segments.append(f"摘要：{_truncate(str(evidence.get('abstract_text') or ''), limit=180)}")
        table_supplements = [dict(item) for item in list(evidence.get("table_supplements") or []) if isinstance(item, dict)]
        if table_supplements:
            segments.append(f"表格：{_summarize_table(table_supplements[0])}")
        if not segments:
            continue
        lines.append(f"{patent_id}《{title}》；" + "；".join(segment for segment in segments if segment))
    return _truncate("\n".join(line for line in lines if line), limit=1200)


def _summarize_table(table: Any) -> str:
    payload = dict(table or {})
    title = str(payload.get("table_title") or "表格证据").strip()
    columns = [str(item).strip() for item in list(payload.get("columns") or []) if str(item).strip()]
    rows = [dict(item) for item in list(payload.get("rows") or []) if isinstance(item, dict)]
    if not rows:
        return title
    first_row = rows[0]
    if columns:
        values = [f"{column}={str(first_row.get(column) or '').strip()}" for column in columns if str(first_row.get(column) or "").strip()]
    else:
        values = [f"{str(key).strip()}={str(value).strip()}" for key, value in first_row.items() if str(key).strip() and str(value).strip()]
    detail = "；".join(values[:4]).strip()
    return title if not detail else f"{title}（{detail}）"


def _truncate(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"



def build_stub_patent_result(
    *,
    request: PatentAskRequest,
    context: dict[str, Any] | None = None,
    profile: PatentModeProfile | None = None,
) -> dict[str, Any]:
    resolved_profile = profile or get_patent_mode_profile()
    _ = context or {}
    answer_text = f"Patent Phase 1 stub answer: {request.question}"
    return {
        "answer_text": answer_text,
        "requested_mode": resolved_profile.requested_mode,
        "actual_mode": resolved_profile.actual_mode,
        "route": resolved_profile.route,
        "query_mode": resolved_profile.query_mode,
        "steps": [
            {
                "step": "patent_stub",
                "title": "Patent Stub",
                "message": "Patent Phase 1 stub execution completed.",
                "status": "success",
            }
        ],
        "references": [],
        "reference_links": [],
        "pdf_links": [],
        "used_files": [],
        "file_selection": dict(request.file_selection or {}),
        "source_scope": request.source_scope,
        "timings": {
            "stub_total_ms": 1,
        },
    }


def build_retrieval_patent_result(
    *,
    request: PatentAskRequest,
    retrieval_outcome: PatentRetrievalOutcome,
    profile: PatentModeProfile | None = None,
) -> dict[str, Any]:
    resolved_profile = profile or get_patent_mode_profile()
    if retrieval_outcome.not_found:
        return {
            "answer_text": "Patent retrieval found no matching results.",
            "requested_mode": resolved_profile.requested_mode,
            "actual_mode": resolved_profile.actual_mode,
            "route": resolved_profile.route,
            "query_mode": resolved_profile.query_mode,
            "steps": [
                {
                    "step": "retrieval_not_found",
                    "title": "Patent Retrieval",
                    "message": "No matching patent was found by the no-vector retrieval pipeline.",
                    "status": "success",
                }
            ],
            "references": [],
            "reference_objects": [],
            "reference_links": [],
            "original_links": [],
            "used_files": [],
            "file_selection": dict(request.file_selection or {}),
            "source_scope": request.source_scope,
            "metadata": {
                "retrieval_backend": retrieval_outcome.retrieval_backend,
                "retrieval_version": retrieval_outcome.retrieval_version,
                "catalog_index_version": retrieval_outcome.catalog_index_version,
                "cache_hit": retrieval_outcome.cache_hit,
                "negative_cache_hit": retrieval_outcome.negative_cache_hit,
                "not_found": True,
            },
            "timings": dict(retrieval_outcome.timings),
        }

    return {
        "answer_text": str(retrieval_outcome.answer_text or (f"Patent retrieval answer: {retrieval_outcome.evidences[0].title}" if retrieval_outcome.evidences else "Patent retrieval answer")),
        "requested_mode": resolved_profile.requested_mode,
        "actual_mode": resolved_profile.actual_mode,
        "route": resolved_profile.route,
        "query_mode": resolved_profile.query_mode,
        "steps": [
            {
                "step": "retrieval_mvp",
                "title": "Patent Retrieval",
                "message": (
                    f"Resolved answer through {retrieval_outcome.retrieval_backend}; "
                    f"matched {len(retrieval_outcome.references)} patent(s) and "
                    f"loaded {sum(len(item.table_supplements) for item in retrieval_outcome.evidences)} table supplement(s)."
                ),
                "status": "success",
            }
        ],
        "references": list(retrieval_outcome.references),
        "reference_objects": list(retrieval_outcome.reference_objects),
        "reference_links": list(retrieval_outcome.reference_links),
        "original_links": list(retrieval_outcome.original_links),
        "used_files": [],
        "file_selection": dict(request.file_selection or {}),
        "source_scope": request.source_scope,
        "metadata": {
            "retrieval_backend": retrieval_outcome.retrieval_backend,
            "retrieval_version": retrieval_outcome.retrieval_version,
            "catalog_index_version": retrieval_outcome.catalog_index_version,
            "cache_hit": retrieval_outcome.cache_hit,
            "negative_cache_hit": retrieval_outcome.negative_cache_hit,
            "not_found": False,
            "kb_evidence_context": build_retrieval_evidence_context(retrieval_outcome),
            "kb_reference_instruction": build_patent_reference_instruction(retrieval_outcome.references),
        },
        "timings": dict(retrieval_outcome.timings),
    }
