from __future__ import annotations

from typing import Any

from server.patent.graph_kb.models import PatentDirectAnswerResult, PatentGraphEvidenceBundle, PatentGraphKbQueryPlan, PatentGraphQueryPlanV2, PatentGraphSemanticDecision
from server.patent.graph_kb.rendering import render_patent_graph_answer


def _text(value: Any) -> str:
    return str(value or "").strip()


def _build_reference_object(*, patent_id: str, title: str = "") -> dict[str, Any]:
    return {
        "canonical_patent_id": patent_id,
        "patent_id": patent_id,
        "title": title,
        "source": "patent_graph",
    }


def _clean_unique(values: list[str]) -> tuple[str, ...]:
    items: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in items:
            items.append(text)
    return tuple(items)


def _reference_objects_from_rows(rows: list[dict[str, Any]]) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:
    references: list[str] = []
    reference_objects: list[dict[str, Any]] = []
    for row in rows:
        patent_id = _text(row.get("patent_id"))
        if not patent_id or patent_id in references:
            continue
        references.append(patent_id)
        reference_objects.append(_build_reference_object(patent_id=patent_id, title=_text(row.get("title"))))
    return tuple(references), tuple(reference_objects)


def _render_parametric_answer(plan: PatentGraphQueryPlanV2, rows: list[dict[str, Any]]) -> PatentDirectAnswerResult:
    path_id = _text((bundle_path_id := rows and "") or "")
    _ = bundle_path_id
    candidate_queries = list(plan.parametric_slots.get("candidate_queries") or [])
    if len(candidate_queries) != 1:
        return PatentDirectAnswerResult(handled=False, metadata={"reason": "unsupported_parametric_path"})
    candidate = dict(candidate_queries[0] or {})
    path_id = _text(candidate.get("path_id"))
    params = dict(candidate.get("params") or {})

    if path_id in {"list_patents_by_inventor", "list_patents_by_agency", "list_patents_by_ipc_subclass"}:
        filtered_rows = [row for row in rows if not bool(row.get("stub"))]
        if not filtered_rows:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "stub_only_result"})
        references, reference_objects = _reference_objects_from_rows(filtered_rows)
        if path_id == "list_patents_by_inventor":
            subject = _text(filtered_rows[0].get("inventor_name")) or _text(params.get("inventor_name"))
            header = f"发明人 `{subject}` 关联的专利包括："
        elif path_id == "list_patents_by_agency":
            subject = _text(filtered_rows[0].get("agency_name")) or _text(params.get("agency_name"))
            header = f"代理机构 `{subject}` 名下的专利包括："
        else:
            subject = _text(filtered_rows[0].get("ipc_subclass")) or _text(params.get("ipc_subclass"))
            header = f"`{subject}` 子类下的专利包括："
        lines = [header]
        for row in filtered_rows:
            patent_id = _text(row.get("patent_id"))
            title = _text(row.get("title")) or "未知标题"
            if patent_id:
                lines.append(f"- `{patent_id}`：{title}")
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=references,
            reference_objects=reference_objects,
            metadata={"path_id": path_id},
        )

    if path_id in {"count_patents_by_ipc", "count_patents_by_applicant", "count_patents_by_inventor"}:
        row = dict(rows[0] or {})
        count = _text(row.get("patent_count")) or "0"
        if path_id == "count_patents_by_ipc":
            subject = _text(row.get("ipc_code")) or _text(params.get("ipc_code"))
            answer = f"`{subject}` 对应的专利数量为 {count}。"
        elif path_id == "count_patents_by_applicant":
            subject = _text(row.get("applicant_name")) or _text(params.get("organization_name"))
            answer = f"`{subject}` 名下的专利数量为 {count}。"
        else:
            subject = _text(row.get("inventor_name")) or _text(params.get("inventor_name"))
            answer = f"发明人 `{subject}` 关联的专利数量为 {count}。"
        return PatentDirectAnswerResult(handled=True, answer=answer, references=(), reference_objects=(), metadata={"path_id": path_id})

    if path_id == "list_patent_atmospheres":
        filtered_rows = [row for row in rows if not bool(row.get("stub"))]
        if not filtered_rows:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "stub_only_result"})
        patent_id = _text(filtered_rows[0].get("patent_id")) or _text(params.get("patent_id"))
        lines = [f"专利 `{patent_id}` 的气氛条件包括："]
        for row in filtered_rows:
            options = _text(row.get("atmosphere_options")) or "未记录气氛选项"
            preferred = _text(row.get("atmosphere_preferred"))
            suffix = f"（preferred={preferred}）" if preferred else ""
            lines.append(f"- {options}{suffix}")
        reference = (patent_id,) if patent_id else ()
        reference_objects = (_build_reference_object(patent_id=patent_id),) if patent_id else ()
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=reference,
            reference_objects=reference_objects,
            metadata={"path_id": path_id},
        )

    if path_id == "list_patent_embodiment_insights":
        filtered_rows = [row for row in rows if not bool(row.get("stub"))]
        if not filtered_rows:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "stub_only_result"})
        patent_id = _text(filtered_rows[0].get("patent_id")) or _text(params.get("patent_id"))
        lines = [f"专利 `{patent_id}` 的实施例洞察包括："]
        for row in filtered_rows:
            conclusion = _text(row.get("insight_conclusion")) or "未记录洞察结论"
            insight_type = _text(row.get("insight_type"))
            suffix = f"（{insight_type}）" if insight_type else ""
            lines.append(f"- {conclusion}{suffix}")
        reference = (patent_id,) if patent_id else ()
        reference_objects = (_build_reference_object(patent_id=patent_id),) if patent_id else ()
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=reference,
            reference_objects=reference_objects,
            metadata={"path_id": path_id},
        )

    return PatentDirectAnswerResult(handled=False, metadata={"reason": "unsupported_parametric_path", "path_id": path_id})


def render_patent_direct_answer(
    *,
    decision: PatentGraphSemanticDecision,
    plan: PatentGraphQueryPlanV2,
    bundle: PatentGraphEvidenceBundle,
) -> PatentDirectAnswerResult:
    if decision.mode != "direct_answer" or not bundle.direct_answerable:
        return PatentDirectAnswerResult(handled=False, metadata={"reason": "not_direct_answerable"})

    rows = [dict(item or {}) for item in list(bundle.render_slots.get("rows") or []) if isinstance(item, dict)]
    if not rows:
        return PatentDirectAnswerResult(handled=False, metadata={"reason": "empty_rows"})

    if plan.strategy == "template":
        legacy_plan = plan.legacy_template_plan or PatentGraphKbQueryPlan(
            template_id=_text(plan.legacy_template_id),
            params={},
        )
        answer, references, reference_objects, metadata = render_patent_graph_answer(legacy_plan, rows)
        if not _text(answer):
            merged_metadata = dict(metadata)
            merged_metadata["reason"] = _text(metadata.get("render_empty_reason")) or _text(metadata.get("stub_fallback_reason")) or "render_empty"
            return PatentDirectAnswerResult(handled=False, metadata=merged_metadata)
        return PatentDirectAnswerResult(
            handled=True,
            answer=answer,
            references=_clean_unique(list(references)),
            reference_objects=tuple(reference_objects),
            metadata=dict(metadata),
        )

    return _render_parametric_answer(plan, rows)
