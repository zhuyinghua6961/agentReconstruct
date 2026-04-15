from __future__ import annotations

from collections import defaultdict
from typing import Any

from server.patent.graph_kb.models import PatentGraphKbQueryPlan


_DIRECT_TARGET_TEMPLATES = {
    "lookup_patent_by_id",
    "list_patent_process_steps",
    "list_patent_material_roles",
    "list_patent_experiment_tables",
    "list_patent_problem_solution",
    "list_patent_inventive_scope",
}
_LISTING_TEMPLATES = {"list_patents_by_ipc", "list_patents_by_applicant"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(values: Any) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for item in list(values or []):
        text = _text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _build_reference_object(*, patent_id: str, title: str = "") -> dict[str, Any]:
    return {
        "canonical_patent_id": patent_id,
        "patent_id": patent_id,
        "title": title,
        "source": "patent_graph",
    }


def _mark_render_empty(metadata: dict[str, Any], reason: str) -> tuple[str, tuple[str, ...], tuple[dict[str, Any], ...], dict[str, Any]]:
    metadata["render_empty_reason"] = reason
    return "", (), (), metadata


def _apply_stub_policy(plan: PatentGraphKbQueryPlan, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata = {"stub_filtered_count": 0}
    if not rows:
        return [], metadata

    if plan.template_id in _DIRECT_TARGET_TEMPLATES:
        if bool(rows[0].get("stub")):
            metadata["stub_fallback_reason"] = "stub_patent"
            return [], metadata
        return rows, metadata

    if plan.template_id == "list_patent_citations":
        filtered = [row for row in rows if not bool(row.get("cited_stub"))]
        metadata["stub_filtered_count"] = len(rows) - len(filtered)
        if not filtered:
            metadata["stub_fallback_reason"] = "stub_only_result"
        return filtered, metadata

    if plan.template_id in _LISTING_TEMPLATES:
        filtered = [row for row in rows if not bool(row.get("stub"))]
        metadata["stub_filtered_count"] = len(rows) - len(filtered)
        if not filtered:
            metadata["stub_fallback_reason"] = "stub_only_result"
        return filtered, metadata

    return rows, metadata


def render_patent_graph_answer(
    plan: PatentGraphKbQueryPlan,
    rows: list[dict[str, Any]],
) -> tuple[str, tuple[str, ...], tuple[dict[str, Any], ...], dict[str, Any]]:
    rows = [dict(item) for item in list(rows or []) if isinstance(item, dict)]
    rows, metadata = _apply_stub_policy(plan, rows)
    if not rows:
        return "", (), (), metadata

    if plan.template_id == "lookup_patent_by_id":
        row = rows[0]
        patent_id = _text(row.get("patent_id"))
        title = _text(row.get("title"))
        if not patent_id:
            return _mark_render_empty(metadata, "missing_patent_id")
        if not title:
            return _mark_render_empty(metadata, "missing_title")
        answer_lines = [
            f"专利 `{patent_id}` 的标题是《{title}》。",
        ]
        abstract = _text(row.get("abstract"))
        if abstract:
            answer_lines.append(f"摘要：{abstract}")
        applicants = _clean_list(row.get("applicants"))
        inventors = _clean_list(row.get("inventors"))
        ipc_codes = _clean_list(row.get("ipc_codes"))
        if applicants:
            answer_lines.append(f"申请人：{'；'.join(applicants)}")
        if inventors:
            answer_lines.append(f"发明人：{'；'.join(inventors)}")
        if ipc_codes:
            answer_lines.append(f"IPC：{'；'.join(ipc_codes)}")
        references = (patent_id,)
        return "\n".join(answer_lines), references, (_build_reference_object(patent_id=patent_id, title=title),), metadata

    if plan.template_id == "list_patent_process_steps":
        sorted_rows = sorted(rows, key=lambda item: (item.get("step_order") is None, item.get("step_order")))
        patent_id = _text(sorted_rows[0].get("patent_id"))
        lines = [f"专利 `{patent_id}` 的工艺步骤包括："]
        for row in sorted_rows:
            step_order = row.get("step_order")
            step_name = _text(row.get("step_name"))
            template = _text(row.get("step_template"))
            operation = _text(row.get("step_operation"))
            lines.append(f"- 步骤 {step_order}：{step_name or '未命名步骤'}")
            if template:
                lines.append(f"  模板：{template}")
            if operation:
                lines.append(f"  操作：{operation}")
        references = (patent_id,)
        return "\n".join(lines), references, (_build_reference_object(patent_id=patent_id),), metadata

    if plan.template_id == "list_patent_material_roles":
        patent_id = _text(rows[0].get("patent_id"))
        grouped: dict[str, list[str]] = defaultdict(list)
        role_meta: dict[str, str] = {}
        for row in rows:
            role_name = _text(row.get("role_name")) or "未命名角色"
            material_name = _text(row.get("material_name"))
            if material_name and material_name not in grouped[role_name]:
                grouped[role_name].append(material_name)
            bits = [item for item in (_text(row.get("role_ratio")), _text(row.get("role_note"))) if item]
            if bits and role_name not in role_meta:
                role_meta[role_name] = "；".join(bits)
        lines = [f"专利 `{patent_id}` 的原料角色包括："]
        for role_name, materials in grouped.items():
            suffix = f"（{role_meta[role_name]}）" if role_name in role_meta else ""
            lines.append(f"- {role_name}{suffix}：{'；'.join(materials) or '未记录候选材料'}")
        references = (patent_id,)
        return "\n".join(lines), references, (_build_reference_object(patent_id=patent_id),), metadata

    if plan.template_id == "list_patent_experiment_tables":
        patent_id = _text(rows[0].get("patent_id"))
        lines = [f"专利 `{patent_id}` 的实验表格和测量数据包括："]
        for row in rows:
            lines.append(
                f"- { _text(row.get('table_title')) or '未命名表格' } / { _text(row.get('row_label')) or '未命名行' }："
                f"{ _text(row.get('measurement_name')) or '未命名指标' } = { _text(row.get('measurement_value')) }"
                f"{ _text(row.get('measurement_unit')) }"
            )
        references = (patent_id,)
        return "\n".join(lines), references, (_build_reference_object(patent_id=patent_id),), metadata

    if plan.template_id == "list_patent_problem_solution":
        row = rows[0]
        patent_id = _text(row.get("patent_id"))
        if not patent_id:
            return _mark_render_empty(metadata, "missing_patent_id")
        problems = _clean_list(row.get("problem_texts"))
        solutions = _clean_list(row.get("solution_texts"))
        scenarios = _clean_list(row.get("scenario_texts"))
        if not (problems or solutions or scenarios):
            return _mark_render_empty(metadata, "missing_problem_solution_facts")
        lines = [f"专利 `{patent_id}` 的技术问题与方案如下："]
        if problems:
            lines.append(f"技术问题：{'；'.join(problems)}")
        if solutions:
            lines.append(f"技术方案：{'；'.join(solutions)}")
        if scenarios:
            lines.append(f"应用场景：{'；'.join(scenarios)}")
        references = (patent_id,)
        return "\n".join(lines), references, (_build_reference_object(patent_id=patent_id),), metadata

    if plan.template_id == "list_patent_inventive_scope":
        row = rows[0]
        patent_id = _text(row.get("patent_id"))
        if not patent_id:
            return _mark_render_empty(metadata, "missing_patent_id")
        sections = [
            ("发明点", _clean_list(row.get("inventive_point_texts"))),
            ("性能事实", _clean_list(row.get("performance_fact_texts"))),
            ("保护范围", _clean_list(row.get("protection_scope_texts"))),
            ("claim 步骤标签", _clean_list(row.get("claim_step_labels"))),
        ]
        if not any(values for _label, values in sections):
            return _mark_render_empty(metadata, "missing_inventive_scope_facts")
        lines = [f"专利 `{patent_id}` 的发明点和保护范围包括："]
        for label, values in sections:
            if values:
                lines.append(f"{label}：{'；'.join(values)}")
        references = (patent_id,)
        return "\n".join(lines), references, (_build_reference_object(patent_id=patent_id),), metadata

    if plan.template_id == "list_patent_citations":
        target_id = _text(rows[0].get("patent_id"))
        references = tuple(_text(row.get("cited_patent_id")) for row in rows if _text(row.get("cited_patent_id")))
        lines = [f"专利 `{target_id}` 引用了以下专利："]
        reference_objects = []
        for row in rows:
            cited_id = _text(row.get("cited_patent_id"))
            title = _text(row.get("cited_title"))
            if not cited_id:
                continue
            lines.append(f"- `{cited_id}`：{title or '未知标题'}")
            reference_objects.append(_build_reference_object(patent_id=cited_id, title=title))
        return "\n".join(lines), references, tuple(reference_objects), metadata

    if plan.template_id == "list_patents_by_ipc":
        ipc_code = _text(plan.params.get("ipc_code"))
        references = tuple(_text(row.get("patent_id")) for row in rows if _text(row.get("patent_id")))
        lines = [f"`{ipc_code}` 下的专利包括："]
        reference_objects = []
        for row in rows:
            patent_id = _text(row.get("patent_id"))
            title = _text(row.get("title"))
            if not patent_id:
                continue
            lines.append(f"- `{patent_id}`：{title or '未知标题'}")
            reference_objects.append(_build_reference_object(patent_id=patent_id, title=title))
        return "\n".join(lines), references, tuple(reference_objects), metadata

    if plan.template_id == "list_patents_by_applicant":
        organization_name = _text(plan.params.get("organization_name"))
        references = tuple(_text(row.get("patent_id")) for row in rows if _text(row.get("patent_id")))
        lines = [f"`{organization_name}` 名下的专利包括："]
        reference_objects = []
        for row in rows:
            patent_id = _text(row.get("patent_id"))
            title = _text(row.get("title"))
            if not patent_id:
                continue
            lines.append(f"- `{patent_id}`：{title or '未知标题'}")
            reference_objects.append(_build_reference_object(patent_id=patent_id, title=title))
        return "\n".join(lines), references, tuple(reference_objects), metadata

    return "", (), (), metadata
