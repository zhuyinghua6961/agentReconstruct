from __future__ import annotations

import time
import re
from typing import Any

from app.modules.graph_kb.classifier import classify_graph_kb_question
from app.modules.graph_kb.classifier_v2 import classify_graph_question_v2
from app.modules.graph_kb.client import execute_graph_kb_plan, plan_graph_kb_query
from app.modules.graph_kb.canonicalizer import canonicalize_graph_rows
from app.modules.graph_kb.direct_renderer import render_direct_answer
from app.modules.graph_kb.executor_v2 import execute_prepared_query
from app.modules.graph_kb.metadata import build_graph_route_metadata
from app.modules.graph_kb.rag_adapter import build_graph_rag_payload
from app.modules.graph_kb.models import GraphKbExecutionResult, GraphKbQueryPlan, GraphRoutingResult
from app.modules.graph_kb.planner_v2 import build_graph_query_plan_v2
from app.modules.graph_kb.schema_registry import build_default_schema_registry


_GRAPH_ROW_DOI_START_RE = re.compile(r"^10\.\d{1,9}[/_]", re.IGNORECASE)
_GRAPH_ROW_TRAILING_PUNCT_RE = re.compile(r"[.,;:]+$")
_GRAPH_NULL_TOKEN_RE = re.compile(r"(?i)(?:^|_)null(?=_|$)")
_GRAPH_WHITESPACE_RE = re.compile(r"\s+")
_GRAPH_FIELD_KEY_RE = re.compile(
    r"(?:^|_)(ball_powder_ratio|temperature|atmosphere|thickness|speed|time|method)(?=_)",
    re.IGNORECASE,
)

_GRAPH_PARAM_LABELS = {
    "time": "时间",
    "temperature": "温度",
    "speed": "转速",
    "ball_powder_ratio": "球粉比",
    "atmosphere": "气氛",
    "thickness": "厚度",
}
_GRAPH_PARAM_ORDER = ("time", "temperature", "speed", "ball_powder_ratio", "atmosphere", "thickness")


def _trim_unbalanced_trailing_parens(value: str) -> str:
    text = str(value or "")
    while text.endswith(")") and text.count("(") < text.count(")"):
        text = text[:-1]
    return text


def _normalize_graph_row_doi(value: Any) -> str:
    doi = str(value or "").strip()
    if not doi:
        return ""
    doi = _GRAPH_ROW_TRAILING_PUNCT_RE.sub("", doi)
    doi = _trim_unbalanced_trailing_parens(doi)
    if doi.startswith("10.") and "_" in doi and "/" not in doi:
        doi = doi.replace("_", "/", 1)
    lowered = doi.lower()
    if not _GRAPH_ROW_DOI_START_RE.match(doi):
        return ""
    if any(marker in lowered for marker in ("http://", "https://", "www.")):
        return ""
    if doi.endswith(("-", "/", "_", ".")):
        return ""
    return doi


def _sanitize_graph_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row or {})
        if "doi" not in payload:
            sanitized.append(payload)
            continue
        doi = _normalize_graph_row_doi(payload.get("doi"))
        if not doi:
            continue
        payload["doi"] = doi
        sanitized.append(payload)
    return sanitized


def _unique_references(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    seen: set[str] = set()
    refs: list[str] = []
    for row in rows:
        doi = _normalize_graph_row_doi(row.get("doi"))
        if not doi or doi in seen:
            continue
        seen.add(doi)
        refs.append(doi)
    return tuple(refs)


def _clean_items(values: Any, *, limit: int) -> list[str]:
    items: list[str] = []
    for item in list(values or []):
        text = _clean_graph_display_text(item)
        if not text or text in items:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _normalize_graph_field_source(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _GRAPH_NULL_TOKEN_RE.sub("_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip(" _;，；,.")


def _clean_graph_display_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _GRAPH_NULL_TOKEN_RE.sub("_", text)
    text = re.sub(r"_+", " ", text)
    text = _GRAPH_WHITESPACE_RE.sub(" ", text)
    return text.strip(" _")


def _clean_graph_field_value(value: Any) -> str:
    text = _normalize_graph_field_source(value)
    if not text:
        return ""
    text = text.replace("_", " ")
    text = _GRAPH_WHITESPACE_RE.sub(" ", text)
    return text.strip(" ")


def _format_graph_title(value: Any) -> str:
    text = _clean_graph_display_text(value)
    if text and text == text.lower():
        return text[:1].upper() + text[1:]
    return text


def _parse_graph_field_map(value: Any) -> tuple[dict[str, str], list[str]]:
    source = _normalize_graph_field_source(value)
    if not source:
        return {}, []

    matches = list(_GRAPH_FIELD_KEY_RE.finditer(source))
    if not matches:
        cleaned = _clean_graph_display_text(source)
        return {}, [cleaned] if cleaned else []

    parsed: dict[str, str] = {}
    leftovers: list[str] = []

    prefix = _clean_graph_field_value(source[: matches[0].start()])
    if prefix:
        leftovers.append(prefix)

    for index, match in enumerate(matches):
        key = str(match.group(1) or "").strip().lower()
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        value_text = _clean_graph_field_value(source[value_start:value_end])
        if value_text and key not in parsed:
            parsed[key] = value_text

    return parsed, leftovers


def _build_method_sections(values: Any, *, limit: int) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in list(values or []):
        field_map, leftovers = _parse_graph_field_map(raw)
        title = _format_graph_title(field_map.pop("method", ""))
        if not title:
            plain = _format_graph_title(raw)
            if not plain or plain in seen:
                continue
            sections.append({"title": plain, "params": []})
            seen.add(plain)
        else:
            params: list[str] = []
            for key in _GRAPH_PARAM_ORDER:
                value_text = field_map.get(key)
                if not value_text:
                    continue
                params.append(f"- {_GRAPH_PARAM_LABELS[key]}：{value_text}")
            for extra in leftovers:
                if extra:
                    params.append(f"- {extra}")
            key_text = f"{title}|{'|'.join(params)}"
            if key_text in seen:
                continue
            sections.append({"title": title, "params": params})
            seen.add(key_text)
        if len(sections) >= limit:
            break

    return sections


def _build_parameter_lines(values: Any, *, limit: int) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    for raw in list(values or []):
        field_map, leftovers = _parse_graph_field_map(raw)
        current: list[str] = []
        for key in _GRAPH_PARAM_ORDER:
            value_text = field_map.get(key)
            if not value_text:
                continue
            current.append(f"- {_GRAPH_PARAM_LABELS[key]}：{value_text}")
        for extra in leftovers:
            if extra:
                current.append(f"- {extra}")
        if not current:
            plain = _clean_graph_display_text(raw)
            if plain:
                current = [f"- {plain}"]
        for line in current:
            if line in seen:
                continue
            seen.add(line)
            lines.append(line)
            if len(lines) >= limit:
                return lines

    return lines


def _build_markdown(sections: list[list[str]]) -> str:
    blocks = ["\n".join(section).strip() for section in sections if section and "\n".join(section).strip()]
    return "\n\n".join(blocks).strip()


def render_graph_kb_answer(plan: GraphKbQueryPlan, rows: list[dict[str, Any]]) -> tuple[str, tuple[str, ...]]:
    rows = _sanitize_graph_rows(rows)
    if not rows:
        return "", ()

    references = _unique_references(rows)
    if plan.template_id == "lookup_by_doi":
        row = rows[0]
        raw_materials = _clean_items(row.get("raw_materials"), limit=3)
        answer = f"文献 DOI {row.get('doi') or plan.params.get('doi')} 的标题为《{_clean_graph_display_text(row.get('title')) or '未知标题'}》。"
        if raw_materials:
            answer += f" 图谱里关联到的原料包括：{'；'.join(raw_materials[:3])}。"
        return answer, references
    if plan.template_id == "expand_doi_context_by_doi":
        row = rows[0]
        title = _clean_graph_display_text(row.get("title")) or "未知标题"
        doi = str(row.get("doi") or plan.params.get("doi") or "").strip()
        sections: list[list[str]] = [
            [
                "## 📄 文献信息",
                f"- 标题：{title}",
                f"- DOI：{doi}",
            ]
        ]
        if bool(plan.params.get("include_testing")):
            testing_items = _clean_items(row.get("testing_items"), limit=3)
            if testing_items:
                sections.append(["## 🔬 测试/表征", *[f"- {item}" for item in testing_items]])
        if bool(plan.params.get("include_process")):
            process_section: list[str] = ["## ⚙️ 制备/工艺"]
            for method in _build_method_sections(row.get("preparation_methods"), limit=3):
                process_section.append(f"### {method['title']}")
                process_section.extend(list(method.get("params") or []))
            if len(process_section) > 1:
                sections.append(process_section)

            parameter_lines = _build_parameter_lines(row.get("process_parameters"), limit=6)
            if parameter_lines:
                sections.append(["## 📌 关键参数", *parameter_lines])
        if bool(plan.params.get("include_raw_materials")):
            raw_materials = _clean_items(row.get("raw_materials"), limit=3)
            if raw_materials:
                sections.append(["## 🧪 原料", *[f"- {item}" for item in raw_materials]])
        return _build_markdown(sections), references
    if plan.template_id == "list_by_material":
        material = str(plan.params.get("material_name") or "")
        items = [f"《{_clean_graph_display_text(row.get('title')) or row.get('doi') or '未知条目'}》" for row in rows]
        return f"关于 {material} 的图谱命中文献包括：{'；'.join(items)}。", references
    if plan.template_id == "list_by_raw_material":
        material = str(plan.params.get("material_name") or "")
        sections: list[list[str]] = [
            [
                "## 📚 文献概览",
                f"- 当前展示 {len(rows)} 篇相关文献",
                f"- 原料：{material}",
                "- 查询类型：按原料查文献",
            ],
            ["## 📖 相关文献"],
        ]
        list_section = sections[-1]
        for index, row in enumerate(rows, start=1):
            title = _clean_graph_display_text(row.get("title")) or str(row.get("doi") or "未知条目")
            matched_raw_materials = _clean_items(row.get("matched_raw_materials"), limit=2)
            list_section.append(f"### [{index}] {title}")
            list_section.append(f"- DOI：{row.get('doi') or '未知 DOI'}")
            if matched_raw_materials:
                list_section.append(f"- 命中条件：原料 = {'；'.join(matched_raw_materials)}")
        return _build_markdown(sections), references
    if plan.template_id == "count_by_filter":
        material = str(plan.params.get("material_name") or "")
        return f"{material} 在当前图谱中的命中文献数量为 {rows[0].get('count', 0)} 篇。", references
    return "", references


def try_graph_kb_answer(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 3000,
    generation_runtime: Any | None = None,
) -> GraphKbExecutionResult:
    _ = generation_runtime
    decision = classify_graph_kb_question(question, conversation_context=conversation_context or {})
    if decision.decision != "try_graph":
        return GraphKbExecutionResult(handled=False, fallback_reason=decision.reason)

    plan = plan_graph_kb_query(question)
    if plan is None:
        return GraphKbExecutionResult(handled=False, fallback_reason="no_plan")

    started = time.perf_counter()
    try:
        rows = execute_graph_kb_plan(
            plan,
            neo4j_client=neo4j_client,
            max_rows=max_rows,
            timeout_ms=int(timeout_ms or 0),
        )
    except TimeoutError:
        latency_ms = (time.perf_counter() - started) * 1000.0
        return GraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            result_count=0,
            latency_ms=latency_ms,
            fallback_reason="timeout",
        )
    latency_ms = (time.perf_counter() - started) * 1000.0
    if not rows:
        return GraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            result_count=0,
            latency_ms=latency_ms,
            fallback_reason="empty_result",
        )

    sanitized_rows = _sanitize_graph_rows(rows)
    answer, references = render_graph_kb_answer(plan, sanitized_rows)
    if not answer.strip():
        return GraphKbExecutionResult(
            handled=False,
            template_id=plan.template_id,
            result_count=len(sanitized_rows),
            latency_ms=latency_ms,
            fallback_reason="render_empty",
        )

    return GraphKbExecutionResult(
        handled=True,
        answer=answer,
        references=references,
        query_mode="graph_kb",
        template_id=plan.template_id,
        result_count=len(sanitized_rows),
        latency_ms=latency_ms,
        fallback_reason="",
    )


def route_graph_kb_v2(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 3000,
    generation_runtime: Any | None = None,
) -> GraphRoutingResult:
    decision = classify_graph_question_v2(question=question, conversation_context=conversation_context or {})
    plan = build_graph_query_plan_v2(
        question=question,
        decision=decision,
        schema_registry=build_default_schema_registry(),
    )
    diagnostics = dict(decision.diagnostics)
    route_family = str(decision.route_family or decision.legacy_route or "")
    diagnostics["legacy_route"] = decision.legacy_route
    diagnostics["legacy_route_family"] = route_family
    diagnostics["knowledge_route_family"] = route_family
    diagnostics["graph_pipeline_version"] = "v2"
    diagnostics["tri_state_mode"] = decision.mode
    diagnostics["neo4j_client"] = "neo4jgraph"
    diagnostics["doi_source"] = "none"
    diagnostics["legacy_template_fallback_used"] = False
    diagnostics["strategy"] = plan.strategy if plan is not None else ""
    diagnostics["graph_strategy"] = plan.strategy if plan is not None else ""
    diagnostics["graph_intent"] = plan.intent if plan is not None else ""
    diagnostics["graph_confidence"] = float(decision.confidence or 0.0)
    diagnostics["graph_direct_answer_eligible"] = bool(decision.direct_answer_eligible)
    diagnostics.update(
        build_graph_route_metadata(
            route_family=route_family,
            tri_state_mode=decision.mode,
            strategy=plan.strategy if plan is not None else "",
            intent=plan.intent if plan is not None else "",
            confidence=float(decision.confidence or 0.0),
            fallback_reason=str(decision.fallback_reason or ""),
            direct_answer_eligible=bool(decision.direct_answer_eligible),
            doi_source="none",
            graph_pipeline_version="v2",
        )
    )

    if decision.mode == "skip_graph" or plan is None:
        return GraphRoutingResult(mode="skip_graph", diagnostics=diagnostics)

    execution = execute_prepared_query(
        plan=plan,
        neo4j_client=neo4j_client,
        max_rows=max_rows,
        timeout_ms=timeout_ms,
        max_path_attempts=2,
    )
    diagnostics["matched_path"] = execution.trace.matched_path
    diagnostics["attempted_paths"] = execution.trace.attempted_paths
    diagnostics["guardrail_verdict"] = execution.trace.guardrail_verdict
    diagnostics["neo4j_client"] = execution.trace.neo4j_client
    if execution.trace.fallback_reason:
        diagnostics["fallback_reason"] = execution.trace.fallback_reason
        diagnostics["graph_fallback_reason"] = execution.trace.fallback_reason

    bundle = canonicalize_graph_rows(plan=plan, rows=execution.rows)
    result_count = len(tuple(bundle.render_slots.get("rows") or execution.rows or ()))
    diagnostics["graph_result_count"] = result_count
    diagnostics["graph_doi_candidates_count"] = len(tuple(bundle.doi_candidates or ()))
    diagnostics["graph_filtered_doi_count"] = int(bundle.diagnostics.get("filtered_doi_count") or 0)
    diagnostics["graph_suspicious_doi_count"] = int(bundle.diagnostics.get("suspicious_doi_count") or 0)
    rag_payload = build_graph_rag_payload(
        decision=decision,
        plan=plan,
        bundle=bundle,
    )
    if decision.mode == "direct_answer":
        direct = render_direct_answer(decision=decision, plan=plan, bundle=bundle)
        if direct.handled:
            direct_result = GraphKbExecutionResult(
                handled=True,
                answer=direct.answer,
                references=direct.references,
                query_mode="graph_kb",
                template_id=plan.legacy_template_id,
                result_count=result_count,
                fallback_reason="",
            )
            return GraphRoutingResult(mode="direct_answer", direct_result=direct_result, diagnostics=diagnostics)
        diagnostics["direct_fallback_reason"] = str(direct.metadata.get("reason") or execution.trace.fallback_reason or "render_unavailable")
        diagnostics["legacy_template_fallback_used"] = bool(plan.legacy_template_plan is not None)
        return GraphRoutingResult(mode="graph_for_rag", rag_payload=rag_payload, diagnostics=diagnostics)

    return GraphRoutingResult(mode=decision.mode, rag_payload=rag_payload, diagnostics=diagnostics)
