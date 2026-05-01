from __future__ import annotations

from typing import Any

from app.modules.graph_kb.models import DirectAnswerResult, GraphEvidenceBundle, GraphQueryPlanV2, SemanticDecision


_PROFILE_DISPLAY_LIMIT = 5
_LIST_ITEM_LIMIT = 3
_LONG_VALUE_LIMIT = 160
_PLACEHOLDERS = {"null", "none", "nan", "unknown", "_null", "null_null"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_graph_value(value: Any, *, limit: int = _LONG_VALUE_LIMIT) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = text.replace("null_null", " ")
    text = text.replace("_null_", " ")
    text = text.replace("_null", " ")
    text = text.replace("null_", " ")
    text = text.replace("__", " ")
    text = text.replace("_", " ")
    text = " ".join(text.split()).strip(" ;,，。")
    if text.lower() in _PLACEHOLDERS:
        return ""
    if limit > 0 and len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _clean_identifier(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = text.replace("null_null", "")
    text = text.replace("_null_", "")
    text = text.replace("_null", "")
    text = text.replace("null_", "")
    return text.strip(" ;,，。")


def _dedupe_clean_items(values: Any, *, limit: int = _LIST_ITEM_LIMIT) -> list[str]:
    if isinstance(values, (list, tuple)):
        raw_items = list(values)
    elif isinstance(values, set):
        raw_items = list(values)
    elif values is None:
        raw_items = []
    else:
        raw_items = [values]
    items: list[str] = []
    for item in raw_items:
        text = _clean_graph_value(item)
        if not text or text in items:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _format_compact_list(values: Any, *, limit: int = _LIST_ITEM_LIMIT) -> str:
    return "；".join(_dedupe_clean_items(values, limit=limit))


def _clean_items(values: Any, *, limit: int) -> list[str]:
    items: list[str] = []
    for item in list(values or []):
        text = _clean_text(item)
        if not text or text in items:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _build_markdown(sections: list[list[str]]) -> str:
    blocks = ["\n".join(section).strip() for section in sections if section and "\n".join(section).strip()]
    return "\n\n".join(blocks).strip()


def _rows(bundle: GraphEvidenceBundle) -> list[dict[str, Any]]:
    return [dict(item or {}) for item in list(bundle.render_slots.get("rows") or []) if isinstance(item, dict)]


def _references(bundle: GraphEvidenceBundle) -> tuple[str, ...]:
    return tuple(bundle.direct_render_dois or bundle.doi_candidates or ())


def _numeric_rows_are_direct_safe(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    for row in rows:
        try:
            confidence = float(row.get("parser_confidence") or 0.0)
        except (TypeError, ValueError):
            return False
        if confidence < 0.8:
            return False
        if row.get("parsed_value") is None:
            return False
        if not _clean_text(row.get("parsed_unit")):
            return False
    return True


def _legacy_params(plan: GraphQueryPlanV2) -> dict[str, Any]:
    return dict((plan.legacy_template_plan.params if plan.legacy_template_plan is not None else {}) or {})


def _render_list(
    *,
    rows: list[dict[str, Any]],
    references: tuple[str, ...],
    heading_label: str,
    condition_label: str,
    condition_key: str,
) -> DirectAnswerResult:
    sections = [
        [
            "## 📚 文献概览",
            f"- 当前展示 {len(rows)} 篇相关文献",
            f"- 查询类型：{heading_label}",
        ],
        ["## 📖 相关文献"],
    ]
    for index, row in enumerate(rows, start=1):
        title = _clean_text(row.get("title")) or _clean_text(row.get("doi")) or "未知条目"
        sections[-1].append(f"### [{index}] {title}")
        doi = _clean_text(row.get("doi"))
        if doi:
            sections[-1].append(f"- DOI：{doi}")
        values = _clean_items(row.get(condition_key), limit=3)
        if not values:
            values = [_clean_text(row.get(condition_key))]
        values = [item for item in values if item]
        if values:
            sections[-1].append(f"- 命中条件：{condition_label} = {'；'.join(values)}")
    return DirectAnswerResult(handled=True, answer=_build_markdown(sections), references=references)


def _append_profile_bullet(
    lines: list[str],
    label: str,
    values: Any,
    *,
    limit: int = _LIST_ITEM_LIMIT,
    exclude: set[str] | None = None,
) -> None:
    excluded = set(exclude or set())
    items = [item for item in _dedupe_clean_items(values, limit=limit) if item not in excluded]
    text = "；".join(items)
    if text:
        lines.append(f"- {label}：{text}")


def _render_paper_profiles(
    *,
    rows: list[dict[str, Any]],
    references: tuple[str, ...],
    heading_label: str,
    condition_label: str,
    condition_key: str,
) -> DirectAnswerResult:
    displayed_rows = rows[:_PROFILE_DISPLAY_LIMIT]
    sections = [
        [
            "## 📚 文献概览",
            f"- 当前展示 {len(rows)} 篇相关文献",
            f"- 查询类型：{heading_label}",
        ],
        ["## 📖 相关文献"],
    ]
    if len(rows) > len(displayed_rows):
        sections[0].append(f"- 另有 {len(rows) - len(displayed_rows)} 条命中未展开，已保留在参考文献中")

    for index, row in enumerate(displayed_rows, start=1):
        title = _clean_graph_value(row.get("title")) or _clean_graph_value(row.get("doi")) or "未知条目"
        lines = [f"### [{index}] {title}"]
        doi = _clean_identifier(row.get("doi"))
        if doi:
            lines.append(f"- DOI：{doi}")
        matched_values = _dedupe_clean_items(row.get(condition_key), limit=3)
        if not matched_values:
            matched = _clean_graph_value(row.get(condition_key))
            matched_values = [matched] if matched else []
        if matched_values:
            lines.append(f"- 命中条件：{condition_label} = {'；'.join(matched_values)}")
        matched_set = set(matched_values)
        _append_profile_bullet(lines, "原料", row.get("raw_materials"), limit=3)
        recipe_values: list[str] = []
        for key in ("carbon_sources", "carbon_contents", "dopants", "doping_elements", "additives"):
            recipe_values.extend(_dedupe_clean_items(row.get(key), limit=3))
        _append_profile_bullet(lines, "配方", recipe_values, limit=8)
        _append_profile_bullet(lines, "制备方法", row.get("preparation_methods"), limit=3, exclude=matched_set)
        _append_profile_bullet(lines, "关键参数", row.get("process_parameters"), limit=3)
        _append_profile_bullet(lines, "测试/表征", row.get("testing_items"), limit=3)
        _append_profile_bullet(lines, "设备", row.get("equipment"), limit=3)
        sections[-1].extend(lines)

    return DirectAnswerResult(handled=True, answer=_build_markdown(sections), references=references)


def render_direct_answer(
    *,
    decision: SemanticDecision,
    plan: GraphQueryPlanV2,
    bundle: GraphEvidenceBundle,
) -> DirectAnswerResult:
    if decision.mode != "direct_answer":
        return DirectAnswerResult(handled=False, metadata={"reason": "not_direct_answer_mode"})

    rows = _rows(bundle)
    references = _references(bundle)
    template_id = str(plan.legacy_template_id or "")
    intent = str(plan.intent or template_id)
    legacy_params = _legacy_params(plan)

    if intent not in {"count_by_structured_field"} and not intent.startswith("community"):
        rows_with_doi = [row for row in rows if _clean_text(row.get("doi"))]
        if rows_with_doi and not references:
            return DirectAnswerResult(handled=False, metadata={"reason": "suspicious_doi"})
        if references:
            allowed = set(references)
            rows = [row for row in rows if not _clean_text(row.get("doi")) or _clean_text(row.get("doi")) in allowed]
            if rows_with_doi and not rows:
                return DirectAnswerResult(handled=False, metadata={"reason": "suspicious_doi"})

    if intent == "numeric_property_query":
        if not _numeric_rows_are_direct_safe(rows):
            return DirectAnswerResult(handled=False, metadata={"reason": "direct_renderer_unavailable"})

    if not rows and intent not in {"count_by_structured_field"}:
        return DirectAnswerResult(handled=False, metadata={"reason": "empty_rows"})

    if template_id == "lookup_by_doi" or intent == "lookup_by_doi":
        row = rows[0]
        doi = _clean_text(row.get("doi"))
        title = _clean_text(row.get("title")) or "未知标题"
        raw_materials = _clean_items(row.get("raw_materials"), limit=3)
        answer = f"文献 DOI {doi} 的标题为《{title}》。"
        if raw_materials:
            answer += f" 图谱里关联到的原料包括：{'；'.join(raw_materials[:3])}。"
        return DirectAnswerResult(handled=True, answer=answer, references=references, metadata={"template_id": template_id or intent})

    if template_id == "expand_doi_context_by_doi" or intent == "expand_doi_context":
        row = rows[0]
        sections: list[list[str]] = [
            [
                "## 📄 文献信息",
                f"- 标题：{_clean_text(row.get('title')) or '未知标题'}",
                f"- DOI：{_clean_text(row.get('doi')) or legacy_params.get('doi') or '未知 DOI'}",
            ]
        ]
        testing_items = _clean_items(row.get("testing_items"), limit=5)
        if testing_items:
            sections.append(["## 🔬 测试/表征", *[f"- {item}" for item in testing_items]])
        preparation_methods = _clean_items(row.get("preparation_methods") or row.get("value"), limit=5)
        if preparation_methods:
            sections.append(["## ⚙️ 制备/工艺", *[f"### {item}" for item in preparation_methods]])
        raw_materials = _clean_items(row.get("raw_materials"), limit=5)
        if raw_materials:
            sections.append(["## 🧪 原料", *[f"- {item}" for item in raw_materials]])
        return DirectAnswerResult(handled=True, answer=_build_markdown(sections), references=references, metadata={"template_id": template_id or intent})

    if template_id == "list_by_raw_material" or intent == "list_by_raw_material":
        return _render_paper_profiles(
            rows=rows,
            references=references,
            heading_label="按原料查文献",
            condition_label="原料",
            condition_key="matched_raw_materials",
        )

    if intent == "list_by_carbon_source":
        normalized_rows = []
        for row in rows:
            current = dict(row)
            if "carbon_source" in current and "carbon_sources" not in current:
                current["carbon_sources"] = [current["carbon_source"]]
            if "matched_carbon_sources" in current:
                current["carbon_sources"] = current.get("matched_carbon_sources")
            normalized_rows.append(current)
        return _render_paper_profiles(
            rows=normalized_rows,
            references=references,
            heading_label="按碳源查文献",
            condition_label="碳源",
            condition_key="carbon_sources",
        )

    if intent == "list_by_process_method":
        normalized_rows = []
        for row in rows:
            current = dict(row)
            if "matched_preparation_methods" in current:
                current["preparation_methods"] = current.get("matched_preparation_methods")
            normalized_rows.append(current)
        return _render_paper_profiles(
            rows=normalized_rows,
            references=references,
            heading_label="按工艺查文献",
            condition_label="工艺",
            condition_key="preparation_methods",
        )

    if template_id == "list_by_material" or intent == "list_by_title_or_material":
        return _render_paper_profiles(
            rows=rows,
            references=references,
            heading_label="按标题/材料查文献",
            condition_label="标题/材料",
            condition_key="raw_materials",
        )

    if template_id == "count_by_filter" or intent == "count_by_structured_field":
        count = bundle.render_slots.get("count")
        if count is None and rows:
            count = rows[0].get("count", 0)
        field_label = _clean_text(bundle.render_slots.get("field_label")) or "structured_field"
        term = _clean_text(bundle.render_slots.get("term"))
        prefix = f"{term} " if term else ""
        return DirectAnswerResult(handled=True, answer=f"{prefix}{field_label} 在当前图谱中的命中文献数量为 {count} 篇。", references=references)

    if intent == "numeric_property_query":
        sections = [
            [
                "## 📊 数值属性结果",
                f"- 当前展示 {len(rows)} 条图谱记录",
                "- 查询类型：数值属性",
            ],
            ["## 📖 相关记录"],
        ]
        for index, row in enumerate(rows, start=1):
            title = _clean_text(row.get("title")) or _clean_text(row.get("doi")) or "未知条目"
            sections[-1].append(f"### [{index}] {title}")
            doi = _clean_text(row.get("doi"))
            if doi:
                sections[-1].append(f"- DOI：{doi}")
            sample_name = _clean_text(row.get("sample_name"))
            if sample_name:
                sections[-1].append(f"- 样品：{sample_name}")
            original_value = _clean_text(row.get("original_value") or row.get("value"))
            if original_value:
                sections[-1].append(f"- 原始数值：{original_value}")
        return DirectAnswerResult(handled=True, answer=_build_markdown(sections), references=references)

    if intent.startswith("community"):
        label = _clean_text(bundle.render_slots.get("community_label")) or "相关文献聚类"
        title_items = [f"《{_clean_text(row.get('title'))}》" for row in rows if _clean_text(row.get("title"))]
        answer = f"{label} 的代表性文献包括：{'；'.join(title_items[:5])}。" if title_items else f"{label} 有可用于生成回答的图谱证据。"
        return DirectAnswerResult(handled=True, answer=answer, references=references)

    return DirectAnswerResult(handled=False, metadata={"reason": "direct_renderer_unavailable"})
