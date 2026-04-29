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


def _candidate_for_path(plan: PatentGraphQueryPlanV2, path_id: str) -> dict[str, Any]:
    candidate_queries = list(plan.parametric_slots.get("candidate_queries") or [])
    normalized_path_id = _text(path_id)
    if normalized_path_id:
        for candidate in candidate_queries:
            if isinstance(candidate, dict) and _text(candidate.get("path_id")) == normalized_path_id:
                return dict(candidate)
    return dict(candidate_queries[0] or {}) if candidate_queries else {}


def _render_patent_listing(
    *,
    rows: list[dict[str, Any]],
    subject: str,
    header: str,
    path_id: str,
) -> PatentDirectAnswerResult:
    filtered_rows = [row for row in rows if _text(row.get("patent_id"))]
    if not filtered_rows:
        return PatentDirectAnswerResult(handled=False, metadata={"reason": "missing_patent_ids", "path_id": path_id})
    references, reference_objects = _reference_objects_from_rows(filtered_rows)
    lines = [header]
    for row in filtered_rows:
        patent_id = _text(row.get("patent_id"))
        title = _text(row.get("title")) or "未知标题"
        if patent_id:
            lines.append(f"- `{patent_id}`：{title}")
    if not references:
        return PatentDirectAnswerResult(handled=False, metadata={"reason": "missing_patent_ids", "path_id": path_id})
    return PatentDirectAnswerResult(
        handled=True,
        answer="\n".join(lines),
        references=references,
        reference_objects=reference_objects,
        metadata={"path_id": path_id, "subject": subject},
    )


def _render_parametric_answer(plan: PatentGraphQueryPlanV2, bundle: PatentGraphEvidenceBundle, rows: list[dict[str, Any]]) -> PatentDirectAnswerResult:
    path_id = _text(bundle.render_slots.get("path_id"))
    candidate = _candidate_for_path(plan, path_id)
    if not candidate:
        return PatentDirectAnswerResult(handled=False, metadata={"reason": "unsupported_parametric_path", "path_id": path_id})
    path_id = _text(candidate.get("path_id")) or path_id
    params = dict(candidate.get("params") or {})
    evidence_quality = dict(bundle.diagnostics.get("evidence_quality") or {})

    if path_id == "lookup_patent_by_id":
        row = dict(rows[0] or {})
        patent_id = _text(row.get("patent_id")) or _text(params.get("patent_id"))
        if not patent_id:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "missing_patent_id", "path_id": path_id})
        lines = [f"专利 `{patent_id}` 的图谱信息如下："]
        title = _text(row.get("title"))
        abstract = _text(row.get("abstract"))
        if title:
            lines.append(f"- 标题：{title}")
        if abstract:
            lines.append(f"- 摘要：{abstract}")
        for label, key in (("申请人", "applicants"), ("发明人", "inventors"), ("IPC", "ipc_codes")):
            values = row.get(key)
            if isinstance(values, (list, tuple)):
                text = "；".join(_text(item) for item in values if _text(item))
            else:
                text = _text(values)
            if text:
                lines.append(f"- {label}：{text}")
        for label, key in (("申请日", "application_date"), ("公开日", "publication_date"), ("法律状态", "legal_status")):
            value = _text(row.get(key))
            if value:
                lines.append(f"- {label}：{value}")
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=(patent_id,),
            reference_objects=(_build_reference_object(patent_id=patent_id, title=title),),
            metadata={"path_id": path_id},
        )

    if path_id == "list_patent_process_steps":
        patent_id = _text(rows[0].get("patent_id")) or _text(params.get("patent_id"))
        if not patent_id:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "missing_patent_id", "path_id": path_id})
        sorted_rows = sorted(rows, key=lambda item: (item.get("step_order") is None, item.get("step_order") or 9999, _text(item.get("step_name"))))
        lines = [f"专利 `{patent_id}` 的工艺步骤包括："]
        for row in sorted_rows:
            step_name = _text(row.get("step_name")) or _text(row.get("operation")) or "未命名步骤"
            step_order = row.get("step_order")
            prefix = f"步骤 {step_order}" if step_order not in (None, "") else "步骤"
            template = _text(row.get("step_template")) or _text(row.get("step_template_name"))
            detail = f"；模板：{template}" if template else ""
            lines.append(f"- {prefix}：{step_name}{detail}")
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=(patent_id,),
            reference_objects=(_build_reference_object(patent_id=patent_id, title=_text(rows[0].get("title"))),),
            metadata={"path_id": path_id, "truncated": bool(evidence_quality.get("truncated", False))},
        )

    if path_id == "list_patent_material_roles":
        patent_id = _text(rows[0].get("patent_id")) or _text(params.get("patent_id"))
        lines = [f"专利 `{patent_id}` 的原料角色包括："]
        for row in rows:
            role = _text(row.get("material_role")) or _text(row.get("material_role_type")) or "未命名角色"
            options = row.get("material_options")
            if isinstance(options, (list, tuple)):
                rendered_options = "；".join(_text(item) for item in options if _text(item))
            else:
                rendered_options = _text(options)
            lines.append(f"- {role}：{rendered_options or '未记录候选材料'}")
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=(patent_id,) if patent_id else (),
            reference_objects=(_build_reference_object(patent_id=patent_id),) if patent_id else (),
            metadata={"path_id": path_id},
        )

    if path_id in {"list_patent_problem_solution", "list_patent_inventive_scope"}:
        patent_id = _text(rows[0].get("patent_id")) or _text(params.get("patent_id"))
        facts: list[str] = []
        for row in rows:
            for key in ("problems", "solutions", "scenarios", "inventive_points", "performance_facts", "protection_scopes", "claim_step_labels"):
                value = row.get(key)
                if isinstance(value, (list, tuple)):
                    facts.extend(_text(item) for item in value if _text(item))
                elif _text(value):
                    facts.append(_text(value))
        if not facts:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "missing_facts", "path_id": path_id})
        lines = [f"专利 `{patent_id}` 的图谱结构化信息包括："]
        lines.extend(f"- {fact}" for fact in facts[:20])
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=(patent_id,) if patent_id else (),
            reference_objects=(_build_reference_object(patent_id=patent_id),) if patent_id else (),
            metadata={"path_id": path_id, "truncated": len(facts) > 20},
        )

    if path_id == "list_patent_experiment_tables":
        patent_id = _text(rows[0].get("patent_id")) or _text(params.get("patent_id"))
        lines = [f"专利 `{patent_id}` 的实验表格记录包括："]
        for row in rows:
            table_title = _text(row.get("table_title")) or "未命名表格"
            row_label = _text(row.get("row_label"))
            metric = _text(row.get("metric_key"))
            value = _text(row.get("value_raw"))
            unit = _text(row.get("unit_hint"))
            row_suffix = f"；样本：{row_label}" if row_label else ""
            metric_suffix = f"；{metric}={value}{unit}" if metric or value else ""
            lines.append(f"- {table_title}{row_suffix}{metric_suffix}")
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=(patent_id,) if patent_id else (),
            reference_objects=(_build_reference_object(patent_id=patent_id),) if patent_id else (),
            metadata={"path_id": path_id, "truncated": bool(evidence_quality.get("truncated", False))},
        )

    if path_id == "list_patent_citations":
        patent_id = _text(rows[0].get("patent_id")) or _text(params.get("patent_id"))
        filtered_rows = [row for row in rows if _text(row.get("cited_patent_id"))]
        if not filtered_rows:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "missing_citations", "path_id": path_id})
        lines = [f"专利 `{patent_id}` 引用的专利包括："]
        references = [patent_id] if patent_id else []
        reference_objects = [_build_reference_object(patent_id=patent_id, title=_text(rows[0].get("title")))] if patent_id else []
        for row in filtered_rows:
            cited_id = _text(row.get("cited_patent_id"))
            cited_title = _text(row.get("cited_title")) or "未知标题"
            lines.append(f"- `{cited_id}`：{cited_title}")
            if cited_id not in references:
                references.append(cited_id)
                reference_objects.append(_build_reference_object(patent_id=cited_id, title=cited_title))
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=tuple(references),
            reference_objects=tuple(reference_objects),
            metadata={"path_id": path_id},
        )

    if path_id in {
        "list_patents_by_applicant",
        "list_patents_by_inventor",
        "list_patents_by_agency",
        "list_patents_by_ipc_prefix",
        "list_patents_by_ipc_code_prefix",
        "list_patents_by_ipc_full_code",
        "list_patents_by_ipc_subclass",
        "list_patents_by_material",
        "list_patents_by_material_role",
        "list_patents_by_process_term",
    }:
        if path_id == "list_patents_by_inventor":
            subject = _text(rows[0].get("inventor_name")) or _text(params.get("inventor_name"))
            header = f"发明人 `{subject}` 关联的专利包括："
        elif path_id == "list_patents_by_agency":
            subject = _text(rows[0].get("agency_name")) or _text(params.get("agency_name"))
            header = f"代理机构 `{subject}` 名下的专利包括："
        elif path_id == "list_patents_by_applicant":
            subject = _text(rows[0].get("applicant_name")) or _text(params.get("applicant_name") or params.get("organization_name"))
            header = f"申请人 `{subject}` 名下的专利包括："
        elif path_id == "list_patents_by_ipc_prefix":
            subject = _text(rows[0].get("ipc_prefix")) or _text(params.get("ipc_prefix"))
            header = f"`{subject}` IPC 小类下的专利包括："
        elif path_id == "list_patents_by_ipc_code_prefix":
            subject = _text(params.get("ipc_code_prefix")) or _text(rows[0].get("ipc_code"))
            header = f"`{subject}` IPC 代码前缀下的专利包括："
        elif path_id in {"list_patents_by_ipc_full_code", "list_patents_by_ipc_subclass"}:
            subject = _text(rows[0].get("ipc_full_code") or rows[0].get("ipc_code") or rows[0].get("ipc_subclass")) or _text(
                params.get("ipc_full_code") or params.get("ipc_code") or params.get("ipc_subclass")
            )
            header = f"`{subject}` IPC 分类下的专利包括："
        elif path_id == "list_patents_by_material":
            subject = _text(rows[0].get("material_name")) or _text(params.get("material_term"))
            header = f"涉及材料 `{subject}` 的专利包括："
        elif path_id == "list_patents_by_material_role":
            subject = _text(rows[0].get("material_role") or rows[0].get("material_role_type")) or _text(params.get("material_role_term"))
            header = f"涉及材料角色 `{subject}` 的专利包括："
        else:
            subject = _text(rows[0].get("step_name") or rows[0].get("step_template")) or _text(params.get("process_term"))
            header = f"涉及工艺 `{subject}` 的专利包括："
        return _render_patent_listing(rows=rows, subject=subject, header=header, path_id=path_id)

    if path_id in {
        "count_patents_by_ipc",
        "count_patents_by_ipc_prefix",
        "count_patents_by_ipc_code_prefix",
        "count_patents_by_ipc_full_code",
        "count_patents_by_applicant",
        "count_patents_by_inventor",
        "count_patents_by_agency",
    }:
        row = dict(rows[0] or {})
        count = _text(row.get("patent_count")) or "0"
        if path_id in {"count_patents_by_ipc", "count_patents_by_ipc_prefix", "count_patents_by_ipc_code_prefix", "count_patents_by_ipc_full_code"}:
            subject = _text(row.get("ipc_code") or row.get("ipc_prefix") or row.get("ipc_code_prefix") or row.get("ipc_full_code")) or _text(
                params.get("ipc_code") or params.get("ipc_prefix") or params.get("ipc_code_prefix") or params.get("ipc_full_code")
            )
            answer = f"`{subject}` 对应的专利数量为 {count}。"
        elif path_id == "count_patents_by_applicant":
            subject = _text(row.get("applicant_name")) or _text(params.get("applicant_name") or params.get("organization_name"))
            answer = f"`{subject}` 名下的专利数量为 {count}。"
        elif path_id == "count_patents_by_inventor":
            subject = _text(row.get("inventor_name")) or _text(params.get("inventor_name"))
            answer = f"发明人 `{subject}` 关联的专利数量为 {count}。"
        else:
            subject = _text(row.get("agency_name")) or _text(params.get("agency_name"))
            answer = f"代理机构 `{subject}` 名下的专利数量为 {count}。"
        return PatentDirectAnswerResult(handled=True, answer=answer, references=(), reference_objects=(), metadata={"path_id": path_id})

    if path_id in {"rank_materials_by_frequency", "rank_processes_by_frequency"}:
        name_key = "material_name" if path_id == "rank_materials_by_frequency" else "process_name"
        label = "材料" if path_id == "rank_materials_by_frequency" else "工艺"
        sorted_rows = sorted(rows, key=lambda item: int(item.get("patent_count") or 0), reverse=True)
        lines = [f"图谱中出现频次较高的{label}包括："]
        references: list[str] = []
        reference_objects: list[dict[str, Any]] = []
        for row in sorted_rows:
            name = _text(row.get(name_key)) or "未命名"
            count = _text(row.get("patent_count")) or "0"
            sample_ids = [item for item in list(row.get("sample_patent_ids") or []) if _text(item)]
            for patent_id in sample_ids:
                if patent_id not in references:
                    references.append(_text(patent_id))
                    reference_objects.append(_build_reference_object(patent_id=_text(patent_id)))
            sample_suffix = f"；样例：{', '.join(f'`{item}`' for item in sample_ids[:3])}" if sample_ids else ""
            lines.append(f"- {name}：{count} 件专利{sample_suffix}")
        return PatentDirectAnswerResult(
            handled=True,
            answer="\n".join(lines),
            references=tuple(references),
            reference_objects=tuple(reference_objects),
            metadata={"path_id": path_id, "truncated": bool(evidence_quality.get("truncated", False))},
        )

    if path_id == "list_patent_atmospheres":
        filtered_rows = [row for row in rows if _text(row.get("atmosphere_options"))]
        if not filtered_rows:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "missing_atmosphere_facts", "path_id": path_id})
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
        filtered_rows = [row for row in rows if _text(row.get("insight_conclusion")) or _text(row.get("insight_type"))]
        if not filtered_rows:
            return PatentDirectAnswerResult(handled=False, metadata={"reason": "missing_embodiment_insights", "path_id": path_id})
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

    return _render_parametric_answer(plan, bundle, rows)
