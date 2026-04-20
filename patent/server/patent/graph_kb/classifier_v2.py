from __future__ import annotations

import re
from typing import Any

from server.patent.graph_kb.classifier import _contains_file_context, _extract_patent_ids, _normalize_question
from server.patent.graph_kb.client import build_patent_parametric_query_candidates, plan_patent_graph_query
from server.patent.graph_kb.models import PatentGraphSemanticDecision


_FOLLOWUP_HINTS = ("它", "这个", "那件", "上面", "前者", "后者")
_GRAPH_HINTS = (
    "工艺步骤",
    "步骤",
    "工艺",
    "原料",
    "材料角色",
    "实验表格",
    "实验数据",
    "性能数据",
    "测量",
    "技术问题",
    "技术方案",
    "方案",
    "应用场景",
    "发明点",
    "保护范围",
    "claim",
    "引用",
    "气氛",
    "洞察",
    "实施例",
)
_HYBRID_HINTS = ("为什么", "如何", "优势", "改进", "对比", "比较", "差异", "趋势", "总结", "共性", "适用场景")
_BROAD_HINTS = ("为什么", "如何评价", "趋势", "综述", "对比分析", "替代窗口")
_DOI_PATTERN = re.compile(r"10\.\d+/[A-Za-z0-9._\-()/]+", re.IGNORECASE)
_IPC_PATTERN = re.compile(r"\b([A-H][0-9]{2}[A-Z][0-9]+/[0-9A-Z]+)\b", re.IGNORECASE)
_IPC_SUBCLASS_PATTERN = re.compile(r"\b([A-H][0-9]{2}[A-Z][0-9]+)\b", re.IGNORECASE)


def _extract_ipc_codes(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    codes: list[str] = []
    for item in _IPC_PATTERN.findall(text):
        normalized = str(item or "").upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        codes.append(normalized)
    return tuple(codes)


def _extract_ipc_subclasses(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    subclasses: list[str] = []
    full_codes = _extract_ipc_codes(text)
    full_prefixes = {item.split("/", 1)[0] for item in full_codes}
    for item in _IPC_SUBCLASS_PATTERN.findall(text):
        normalized = str(item or "").upper()
        if not normalized or normalized in full_prefixes or normalized in seen:
            continue
        seen.add(normalized)
        subclasses.append(normalized)
    return tuple(subclasses)


def _has_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _find_parametric_candidate(path_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in candidates:
        if str(item.get("path_id") or "") == path_id:
            return item
    return None


def classify_patent_graph_question_v2(
    *,
    question: str,
    conversation_context: dict[str, Any] | None = None,
) -> PatentGraphSemanticDecision:
    text = _normalize_question(question)
    standalone = not bool(conversation_context)
    if not text:
        return PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics={"matched_rule": "no_graph_signal"},
        )

    patent_ids = _extract_patent_ids(text)
    ipc_codes = _extract_ipc_codes(text)
    ipc_subclasses = _extract_ipc_subclasses(text)
    legacy_template_plan = plan_patent_graph_query(text)
    parametric_candidates = build_patent_parametric_query_candidates(text)
    candidate_path_ids = tuple(str(item.get("path_id") or "") for item in parametric_candidates)

    diagnostics: dict[str, Any] = {
        "matched_rule": "",
        "legacy_template_id": legacy_template_plan.template_id if legacy_template_plan is not None else "",
        "patent_ids": patent_ids,
        "ipc_codes": ipc_codes,
        "ipc_subclasses": ipc_subclasses,
        "parametric_path_ids": candidate_path_ids,
    }

    if _DOI_PATTERN.search(text):
        diagnostics["matched_rule"] = "doi_not_supported"
        return PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=diagnostics,
        )

    has_graph_hint = _has_any(text, _GRAPH_HINTS)
    has_hybrid_hint = _has_any(text, _HYBRID_HINTS)
    structured_anchor = bool(patent_ids or ipc_codes or ipc_subclasses or legacy_template_plan or parametric_candidates)

    if not structured_anchor and _has_any(text, _FOLLOWUP_HINTS):
        diagnostics["matched_rule"] = "ambiguous_followup"
        diagnostics["override"] = "ambiguous_followup"
        return PatentGraphSemanticDecision(
            mode="graph_for_rag" if has_graph_hint else "skip_graph",
            route_family="hybrid" if has_graph_hint else "semantic",
            standalone=False,
            requires_context_resolution=True,
            diagnostics=diagnostics,
        )

    if len(patent_ids) > 1 and (has_graph_hint or has_hybrid_hint):
        diagnostics["matched_rule"] = "multi_patent_compare"
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=diagnostics,
        )
    elif structured_anchor and has_hybrid_hint:
        diagnostics["matched_rule"] = "hybrid_graph_anchor"
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=diagnostics,
        )
    elif legacy_template_plan is not None:
        if legacy_template_plan.template_id == "list_patents_by_applicant":
            diagnostics["organization_name"] = str(legacy_template_plan.params.get("organization_name") or "")
        diagnostics["matched_rule"] = "legacy_template"
        decision = PatentGraphSemanticDecision(
            mode="direct_answer",
            route_family="precise",
            standalone=standalone,
            diagnostics=diagnostics,
        )
    elif _find_parametric_candidate("list_patents_by_inventor", parametric_candidates) is not None:
        candidate = _find_parametric_candidate("list_patents_by_inventor", parametric_candidates)
        diagnostics["matched_rule"] = "inventor_listing"
        diagnostics["entity_kind"] = "inventor"
        diagnostics["inventor_name"] = str(candidate.get("params", {}).get("inventor_name") or "")
        decision = PatentGraphSemanticDecision(
            mode="direct_answer",
            route_family="precise",
            standalone=standalone,
            diagnostics=diagnostics,
        )
    elif _find_parametric_candidate("list_patents_by_agency", parametric_candidates) is not None:
        candidate = _find_parametric_candidate("list_patents_by_agency", parametric_candidates)
        diagnostics["matched_rule"] = "agency_listing"
        diagnostics["entity_kind"] = "agency"
        diagnostics["agency_name"] = str(candidate.get("params", {}).get("agency_name") or "")
        decision = PatentGraphSemanticDecision(
            mode="direct_answer",
            route_family="precise",
            standalone=standalone,
            diagnostics=diagnostics,
        )
    elif _find_parametric_candidate("list_patents_by_ipc_subclass", parametric_candidates) is not None:
        diagnostics["matched_rule"] = "ipc_subclass_listing"
        diagnostics["anchor_kind"] = "ipc_subclass"
        decision = PatentGraphSemanticDecision(
            mode="direct_answer",
            route_family="precise",
            standalone=standalone,
            diagnostics=diagnostics,
        )
    elif parametric_candidates:
        diagnostics["matched_rule"] = str(candidate_path_ids[0] or "parametric")
        decision = PatentGraphSemanticDecision(
            mode="direct_answer",
            route_family="precise",
            standalone=standalone,
            diagnostics=diagnostics,
        )
    elif _has_any(text, _BROAD_HINTS):
        diagnostics["matched_rule"] = "broad_semantic_question"
        decision = PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=diagnostics,
        )
    else:
        diagnostics["matched_rule"] = "no_graph_signal"
        decision = PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=diagnostics,
        )

    if _contains_file_context(conversation_context or {}):
        downgraded_mode = "graph_for_rag" if decision.mode == "direct_answer" else decision.mode
        downgraded_route = decision.route_family if downgraded_mode != "skip_graph" else "semantic"
        downgraded_diagnostics = dict(decision.diagnostics)
        downgraded_diagnostics["override"] = "file_context_present"
        return PatentGraphSemanticDecision(
            mode=downgraded_mode,
            route_family=downgraded_route,
            standalone=False,
            requires_context_resolution=True,
            diagnostics=downgraded_diagnostics,
        )

    return decision
