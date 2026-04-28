from __future__ import annotations

import re
from typing import Any

from app.modules.graph_kb.client import plan_graph_kb_query
from app.modules.graph_kb.models import SemanticDecision
from app.modules.graph_kb.slots import extract_graph_slots


_FILE_ROUTE_HINTS = {"pdf_qa", "tabular_qa", "hybrid_qa"}
_FOLLOWUP_HINTS = ("它", "这个", "那篇", "前者", "后者", "上面那个", "最高的是哪篇")
_SEMANTIC_KEYWORDS = ("如何", "为什么", "影响", "方法", "总结", "介绍", "趋势", "稳定", "重要")
_RANKING_PATTERN = re.compile(r"(?:前\s*\d+|排名前|top\s*\d+)", re.IGNORECASE)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_question(question: str) -> str:
    return _text(question).rstrip("？?。.!！")


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in hints)


def _has_file_context(conversation_context: dict[str, Any] | None) -> bool:
    context = conversation_context or {}
    state = context.get("conversation_state") if isinstance(context.get("conversation_state"), dict) else {}
    source_selection = context.get("source_selection") if isinstance(context.get("source_selection"), dict) else {}
    last_turn_route = _text(state.get("last_turn_route")).lower()
    source_scope = _text(source_selection.get("source_scope")).lower()
    selected_file_ids = list(source_selection.get("selected_file_ids") or [])
    used_files = list(source_selection.get("used_files") or [])
    execution_files = list(source_selection.get("execution_files") or [])
    return (
        last_turn_route in _FILE_ROUTE_HINTS
        or any(token in source_scope for token in ("pdf", "table"))
        or bool(selected_file_ids or used_files or execution_files)
    )


def _has_useful_graph_slots(slots: Any) -> bool:
    return bool(
        slots.doi
        or slots.entities
        or slots.title_terms
        or slots.material_terms
        or slots.raw_material_terms
        or slots.recipe_terms
        or slots.process_terms
        or slots.property_field
        or slots.community_signal
        or slots.count_signal
    )


def _decision(
    *,
    mode: str,
    legacy_route: str,
    matched_rule: str,
    standalone: bool,
    slots: Any,
    confidence: float,
    direct_answer_eligible: bool,
    fallback_reason: str = "",
) -> SemanticDecision:
    diagnostics = {
        "matched_rule": matched_rule,
        "legacy_template_id": (plan_graph_kb_query(slots.doi or "") or plan_graph_kb_query(str(slots.doi or ""))).template_id
        if slots.doi and plan_graph_kb_query(slots.doi or "") is not None
        else "",
        "slot_summary": slots.as_dict(),
    }
    return SemanticDecision(
        mode=mode,
        legacy_route=legacy_route,
        standalone=standalone,
        diagnostics=diagnostics,
        route_family=legacy_route,
        confidence=confidence,
        slots=slots.as_dict(),
        direct_answer_eligible=direct_answer_eligible,
        fallback_reason=fallback_reason,
    )


def _base_route_for_slots(question: str, slots: Any, *, standalone: bool) -> SemanticDecision:
    legacy_template_plan = plan_graph_kb_query(question)

    if slots.doi:
        if slots.analysis_signal and (slots.property_field or _contains_any(question, _SEMANTIC_KEYWORDS)):
            return _decision(
                mode="graph_for_rag",
                legacy_route="hybrid",
                matched_rule="doi_contextual_analysis",
                standalone=standalone,
                slots=slots,
                confidence=0.86,
                direct_answer_eligible=False,
            )
        direct_answer_eligible = legacy_template_plan is not None
        return _decision(
            mode="direct_answer" if direct_answer_eligible else "graph_for_rag",
            legacy_route="precise",
            matched_rule="doi_expand" if slots.doi_intent == "expand" else "doi_lookup",
            standalone=standalone,
            slots=slots,
            confidence=0.95,
            direct_answer_eligible=direct_answer_eligible,
        )
    if slots.community_signal:
        return _decision(
            mode="graph_for_rag",
            legacy_route="community",
            matched_rule="community_signal",
            standalone=standalone,
            slots=slots,
            confidence=0.85,
            direct_answer_eligible=False,
        )
    if slots.property_field and slots.analysis_signal:
        return _decision(
            mode="graph_for_rag",
            legacy_route="hybrid",
            matched_rule="hybrid_property_analysis",
            standalone=standalone,
            slots=slots,
            confidence=0.82,
            direct_answer_eligible=False,
        )
    if slots.property_field:
        matched_rule = "numeric_attribute_only"
        if slots.ranking or _RANKING_PATTERN.search(question):
            matched_rule = "numeric_ranking"
        return _decision(
            mode="direct_answer" if legacy_template_plan is not None else "graph_for_rag",
            legacy_route="precise",
            matched_rule=matched_rule,
            standalone=standalone,
            slots=slots,
            confidence=0.78,
            direct_answer_eligible=legacy_template_plan is not None,
        )
    if slots.count_signal or slots.enumeration_signal or slots.recipe_terms or slots.process_terms or slots.entities:
        return _decision(
            mode="direct_answer" if legacy_template_plan is not None else "graph_for_rag",
            legacy_route="precise",
            matched_rule="legacy_template_signal" if legacy_template_plan is not None else "graph_slot_signal",
            standalone=standalone,
            slots=slots,
            confidence=0.76,
            direct_answer_eligible=legacy_template_plan is not None,
        )
    if _contains_any(question, _SEMANTIC_KEYWORDS) and not _has_useful_graph_slots(slots):
        return _decision(
            mode="skip_graph",
            legacy_route="semantic",
            matched_rule="semantic_without_graph_slots",
            standalone=standalone,
            slots=slots,
            confidence=0.7,
            direct_answer_eligible=False,
            fallback_reason="no_useful_graph_slots",
        )
    return _decision(
        mode="skip_graph",
        legacy_route="semantic",
        matched_rule="default_semantic",
        standalone=standalone,
        slots=slots,
        confidence=0.55,
        direct_answer_eligible=False,
        fallback_reason="no_useful_graph_slots",
    )


def classify_graph_question_v2(*, question: str, conversation_context: dict[str, Any] | None = None) -> SemanticDecision:
    text = _normalized_question(question)
    standalone = not bool(conversation_context)
    slots = extract_graph_slots(text)
    decision = _base_route_for_slots(text, slots, standalone=standalone)

    if _has_file_context(conversation_context):
        diagnostics = dict(decision.diagnostics)
        diagnostics["override"] = "file_context_present"
        diagnostics["requires_context_resolution"] = True
        return SemanticDecision(
            mode="graph_for_rag",
            legacy_route=decision.legacy_route,
            standalone=False,
            diagnostics=diagnostics,
            route_family=decision.route_family,
            confidence=decision.confidence,
            slots=decision.slots,
            direct_answer_eligible=False,
            fallback_reason="file_context_present",
        )

    if any(hint in text for hint in _FOLLOWUP_HINTS):
        diagnostics = dict(decision.diagnostics)
        diagnostics["override"] = "ambiguous_followup"
        diagnostics["requires_context_resolution"] = True
        return SemanticDecision(
            mode="graph_for_rag",
            legacy_route=decision.legacy_route,
            standalone=False,
            diagnostics=diagnostics,
            route_family=decision.route_family,
            confidence=decision.confidence,
            slots=decision.slots,
            direct_answer_eligible=False,
            fallback_reason="requires_context_resolution",
        )

    return decision
