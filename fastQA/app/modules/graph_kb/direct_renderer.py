from __future__ import annotations

from typing import Any

from app.modules.graph_kb.models import DirectAnswerResult, GraphEvidenceBundle, GraphQueryPlanV2, SemanticDecision


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


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


def render_direct_answer(
    *,
    decision: SemanticDecision,
    plan: GraphQueryPlanV2,
    bundle: GraphEvidenceBundle,
) -> DirectAnswerResult:
    if decision.mode != "direct_answer" or not bundle.direct_answerable:
        return DirectAnswerResult(handled=False, metadata={"reason": "not_direct_answerable"})

    rows = list(bundle.render_slots.get("rows") or [])
    if not rows:
        return DirectAnswerResult(handled=False, metadata={"reason": "empty_rows"})

    row = dict(rows[0] or {})
    template_id = str(plan.legacy_template_id or "")
    references = bundle.doi_candidates
    legacy_params = dict((plan.legacy_template_plan.params if plan.legacy_template_plan is not None else {}) or {})
    if template_id == "lookup_by_doi":
        doi = _clean_text(row.get("doi"))
        title = _clean_text(row.get("title")) or "未知标题"
        raw_materials = [_clean_text(item) for item in list(row.get("raw_materials") or []) if _clean_text(item)]
        answer = f"文献 DOI {doi} 的标题为《{title}》。"
        if raw_materials:
            answer += f" 图谱里关联到的原料包括：{'；'.join(raw_materials[:3])}。"
        return DirectAnswerResult(handled=True, answer=answer, references=references, metadata={"template_id": template_id})
    if template_id == "expand_doi_context_by_doi":
        sections: list[list[str]] = [
            [
                "## 📄 文献信息",
                f"- 标题：{_clean_text(row.get('title')) or '未知标题'}",
                f"- DOI：{_clean_text(row.get('doi')) or legacy_params.get('doi') or '未知 DOI'}",
            ]
        ]
        if bool(legacy_params.get("include_testing")):
            testing_items = _clean_items(row.get("testing_items"), limit=5)
            if testing_items:
                sections.append(["## 🔬 测试/表征", *[f"- {item}" for item in testing_items]])
        if bool(legacy_params.get("include_process")):
            preparation_methods = _clean_items(row.get("preparation_methods"), limit=3)
            if preparation_methods:
                process_section = ["## ⚙️ 制备/工艺"]
                for method in preparation_methods:
                    process_section.append(f"### {method}")
                sections.append(process_section)
            process_parameters = _clean_items(row.get("process_parameters"), limit=6)
            if process_parameters:
                sections.append(["## 📌 关键参数", *[f"- {item}" for item in process_parameters]])
        if bool(legacy_params.get("include_raw_materials")):
            raw_materials = _clean_items(row.get("raw_materials"), limit=5)
            if raw_materials:
                sections.append(["## 🧪 原料", *[f"- {item}" for item in raw_materials]])
        return DirectAnswerResult(
            handled=True,
            answer=_build_markdown(sections),
            references=references,
            metadata={"template_id": template_id},
        )
    if template_id == "list_by_material":
        material = str(legacy_params.get("material_name") or "")
        items = [
            f"《{_clean_text(item.get('title')) or _clean_text(item.get('doi')) or '未知条目'}》"
            for item in rows
            if isinstance(item, dict)
        ]
        return DirectAnswerResult(
            handled=True,
            answer=f"关于 {material} 的图谱命中文献包括：{'；'.join(items)}。",
            references=references,
            metadata={"template_id": template_id},
        )
    if template_id == "list_by_raw_material":
        material = str(legacy_params.get("material_name") or "")
        sections = [
            [
                "## 📚 文献概览",
                f"- 当前展示 {len(rows)} 篇相关文献",
                f"- 原料：{material}",
                "- 查询类型：按原料查文献",
            ],
            ["## 📖 相关文献"],
        ]
        list_section = sections[-1]
        for index, item in enumerate(rows, start=1):
            current = dict(item or {})
            title = _clean_text(current.get("title")) or _clean_text(current.get("doi")) or "未知条目"
            list_section.append(f"### [{index}] {title}")
            list_section.append(f"- DOI：{_clean_text(current.get('doi')) or '未知 DOI'}")
            matched_raw_materials = _clean_items(current.get("matched_raw_materials"), limit=3)
            if matched_raw_materials:
                list_section.append(f"- 命中条件：原料 = {'；'.join(matched_raw_materials)}")
        return DirectAnswerResult(
            handled=True,
            answer=_build_markdown(sections),
            references=references,
            metadata={"template_id": template_id},
        )
    if template_id == "count_by_filter":
        count = row.get("count", 0)
        material = str(legacy_params.get("material_name") or "")
        answer = f"{material} 在当前图谱中的命中文献数量为 {count} 篇。" if material else f"图谱命中数量为 {count}。"
        return DirectAnswerResult(handled=True, answer=answer, references=references, metadata={"template_id": template_id})

    return DirectAnswerResult(
        handled=True,
        answer=bundle.facts[0] if bundle.facts else "",
        references=references,
        metadata={"template_id": template_id or "fact_fallback"},
    )
