from __future__ import annotations

from typing import Any

from server.patent.graph_kb.classifier import _contains_file_context
from server.patent.graph_kb.models import PatentGraphSemanticDecision
from server.patent.graph_kb.query_templates import build_patent_template_candidates
from server.patent.graph_kb.slots import PatentGraphQuestionSlots, extract_patent_graph_slots


_PATENT_DOMAIN_OBJECT_HINTS = ("专利", "申请", "授权", "公开", "件", "项")
_SYNTHESIS_CONDITION_HINTS = (
    "通常",
    "一般",
    "常用",
    "常见",
    "优选",
    "推荐",
    "需要",
    "应",
    "是否",
    "能否",
    "可以",
    "用什么",
    "哪种",
    "什么气氛",
    "什么条件",
)
_SYNTHESIS_VALUE_HINTS = ("温度", "配比", "比例", "范围", "是多少")
_SYNTHESIS_EFFECT_HINTS = ("作用", "目的", "原因", "为什么")
_SYNTHESIS_FACET_HINTS = ("保护气氛", "烧结气氛", "原料", "锂源", "碳源")
_SINGLE_FACET_LISTING_PATHS = {
    "list_patents_by_material",
    "list_patents_by_material_role",
    "list_patents_by_process_term",
}


def _matched_rule_for_single_patent(slots: PatentGraphQuestionSlots) -> str:
    if slots.asks_atmosphere:
        return "single_patent_atmosphere"
    if slots.asks_embodiment:
        return "single_patent_embodiment"
    if slots.asks_process:
        return "single_patent_process"
    if slots.asks_materials:
        return "single_patent_materials"
    if slots.asks_problem_solution:
        return "single_patent_problem_solution"
    if slots.asks_inventive_scope:
        return "single_patent_inventive_scope"
    if slots.asks_citation:
        return "single_patent_citation"
    if slots.asks_experiment or slots.metric_terms:
        return "single_patent_experiment"
    return "patent_lookup"


def _diagnostics(slots: PatentGraphQuestionSlots, *, matched_rule: str, candidates: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    payload = slots.diagnostics()
    payload.update(
        {
            "matched_rule": matched_rule,
            "patent_ids": slots.patent_ids,
            "ipc_codes": slots.ipc_full_codes,
            "ipc_subclasses": slots.ipc_code_prefixes,
            "parametric_path_ids": tuple(str(item.get("path_id") or "") for item in candidates),
            "candidate_path_ids": tuple(str(item.get("path_id") or "") for item in candidates),
        }
    )
    if slots.applicant_names:
        payload["applicant_name"] = slots.applicant_names[0]
        payload["organization_name"] = slots.applicant_names[0]
    if slots.inventor_names:
        payload["inventor_name"] = slots.inventor_names[0]
    if slots.agency_names:
        payload["agency_name"] = slots.agency_names[0]
    return payload


def _is_explicit_patent_listing_or_count(slots: PatentGraphQuestionSlots) -> bool:
    return any(hint in slots.normalized_question for hint in _PATENT_DOMAIN_OBJECT_HINTS) and bool(slots.asks_list or slots.asks_count)


def _is_material_attribute_question(slots: PatentGraphQuestionSlots) -> bool:
    return bool(slots.material_terms and slots.asks_attribute_value)


def _is_analytical_relation_question(slots: PatentGraphQuestionSlots) -> bool:
    return bool(slots.asks_analytical_relation)


def _has_material_process_candidate(slots: PatentGraphQuestionSlots) -> bool:
    return bool(slots.material_terms or slots.material_role_terms or slots.process_terms or slots.atmosphere_terms)


def _has_precise_entity_anchor(slots: PatentGraphQuestionSlots) -> bool:
    return bool(
        slots.patent_ids
        or slots.applicant_names
        or slots.inventor_names
        or slots.agency_names
        or slots.ipc_full_codes
        or slots.ipc_code_prefixes
        or slots.ipc_prefixes
    )


def _is_unsupported_material_process_count(slots: PatentGraphQuestionSlots) -> bool:
    return bool(slots.asks_count and _has_material_process_candidate(slots) and not _has_precise_entity_anchor(slots))


def _is_material_process_synthesis_question(slots: PatentGraphQuestionSlots) -> bool:
    text = slots.normalized_question
    if not _has_material_process_candidate(slots):
        return False
    if slots.patent_ids:
        return False
    if _is_explicit_patent_listing_or_count(slots):
        return False
    if slots.asks_rank:
        return False
    return bool(
        slots.asks_atmosphere
        or slots.asks_attribute_value
        or slots.asks_why_how
        or any(hint in text for hint in _SYNTHESIS_CONDITION_HINTS)
        or any(hint in text for hint in _SYNTHESIS_VALUE_HINTS)
        or any(hint in text for hint in _SYNTHESIS_EFFECT_HINTS)
        or any(hint in text for hint in _SYNTHESIS_FACET_HINTS)
        or ("还是" in text and bool(slots.atmosphere_terms or slots.process_terms))
    )


def _normalized_term_set(values: tuple[str, ...]) -> set[str]:
    return {str(item or "").strip().lower() for item in values if str(item or "").strip()}


def _is_combined_facet_listing_that_current_template_drops(
    slots: PatentGraphQuestionSlots,
    candidates: tuple[dict[str, Any], ...],
) -> bool:
    if not _is_explicit_patent_listing_or_count(slots):
        return False
    if slots.patent_ids or not candidates:
        return False

    primary_path = str(candidates[0].get("path_id") or "")
    if primary_path not in _SINGLE_FACET_LISTING_PATHS:
        return False

    dropped_constraints = 0
    if bool(slots.atmosphere_terms or slots.asks_atmosphere):
        dropped_constraints += 1
    if slots.process_terms and primary_path != "list_patents_by_process_term":
        dropped_constraints += 1
    if slots.material_role_terms and primary_path != "list_patents_by_material_role":
        dropped_constraints += 1

    material_role_terms = _normalized_term_set(slots.material_role_terms)
    non_role_material_terms = tuple(
        item
        for item in slots.material_terms
        if str(item or "").strip().lower() not in material_role_terms
    )
    if non_role_material_terms and primary_path != "list_patents_by_material":
        dropped_constraints += 1

    return dropped_constraints > 0


def classify_patent_graph_question_v2(
    *,
    question: str,
    conversation_context: dict[str, Any] | None = None,
) -> PatentGraphSemanticDecision:
    slots = extract_patent_graph_slots(question)
    standalone = not bool(conversation_context)
    candidates = build_patent_template_candidates(slots, limit=20)

    if not slots.normalized_question:
        return PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="no_graph_signal", candidates=candidates),
        )

    if slots.has_doi:
        return PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="doi_not_supported", candidates=candidates),
        )

    if not (
        slots.patent_ids
        or slots.ipc_prefixes
        or slots.ipc_code_prefixes
        or slots.ipc_full_codes
        or slots.applicant_names
        or slots.inventor_names
        or slots.agency_names
        or slots.material_terms
        or slots.material_role_terms
        or slots.process_terms
        or slots.metric_terms
        or candidates
    ):
        if slots.asks_followup and (slots.asks_process or slots.asks_materials or slots.asks_atmosphere or slots.asks_embodiment):
            diagnostics = _diagnostics(slots, matched_rule="ambiguous_followup", candidates=candidates)
            diagnostics["override"] = "ambiguous_followup"
            return PatentGraphSemanticDecision(
                mode="graph_for_rag",
                route_family="hybrid",
                standalone=False,
                requires_context_resolution=True,
                diagnostics=diagnostics,
            )
        matched_rule = "broad_semantic_question" if (slots.asks_why_how or slots.asks_trend_landscape) else "no_graph_signal"
        return PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule=matched_rule, candidates=candidates),
        )

    if len(slots.patent_ids) > 1 and slots.asks_compare:
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="multi_patent_compare", candidates=candidates),
        )
    elif (
        _is_analytical_relation_question(slots)
        and not slots.patent_ids
        and not _is_explicit_patent_listing_or_count(slots)
    ):
        decision = PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="analytical_relation_question", candidates=candidates),
        )
    elif slots.applicant_names and (slots.asks_trend_landscape or slots.process_terms or slots.material_terms) and not (slots.asks_count or slots.asks_list):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="community",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="community_landscape", candidates=candidates),
        )
    elif _is_material_attribute_question(slots) and not slots.patent_ids and not _is_explicit_patent_listing_or_count(slots):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="material_attribute_graph_anchor", candidates=candidates),
        )
    elif slots.asks_why_how and (slots.patent_ids or slots.process_terms or slots.material_terms or slots.metric_terms):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="hybrid_graph_anchor", candidates=candidates),
        )
    elif _is_unsupported_material_process_count(slots):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="unsupported_material_process_count", candidates=candidates),
        )
    elif _is_material_process_synthesis_question(slots):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="material_process_synthesis_question", candidates=candidates),
        )
    elif _is_combined_facet_listing_that_current_template_drops(slots, candidates):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="combined_facet_listing_requires_rag", candidates=candidates),
        )
    elif len(slots.patent_ids) == 1:
        decision = PatentGraphSemanticDecision(
            mode="direct_answer",
            route_family="precise",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule=_matched_rule_for_single_patent(slots), candidates=candidates),
        )
    elif slots.applicant_names:
        decision = PatentGraphSemanticDecision(
            mode="direct_answer",
            route_family="precise",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="applicant_count" if slots.asks_count else "applicant_listing", candidates=candidates),
        )
    elif slots.inventor_names:
        diagnostics = _diagnostics(slots, matched_rule="inventor_count" if slots.asks_count else "inventor_listing", candidates=candidates)
        diagnostics["entity_kind"] = "inventor"
        decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise", standalone=standalone, diagnostics=diagnostics)
    elif slots.agency_names:
        diagnostics = _diagnostics(slots, matched_rule="agency_count" if slots.asks_count else "agency_listing", candidates=candidates)
        diagnostics["entity_kind"] = "agency"
        decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise", standalone=standalone, diagnostics=diagnostics)
    elif slots.ipc_full_codes:
        decision = PatentGraphSemanticDecision(
            mode="direct_answer",
            route_family="precise",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="ipc_full_code_count" if slots.asks_count else "ipc_full_code_listing", candidates=candidates),
        )
    elif slots.ipc_code_prefixes:
        diagnostics = _diagnostics(slots, matched_rule="ipc_code_prefix_count" if slots.asks_count else "ipc_code_prefix_listing", candidates=candidates)
        diagnostics["anchor_kind"] = "ipc_code_prefix"
        decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise", standalone=standalone, diagnostics=diagnostics)
    elif slots.ipc_prefixes:
        diagnostics = _diagnostics(slots, matched_rule="ipc_prefix_count" if slots.asks_count else "ipc_prefix_listing", candidates=candidates)
        diagnostics["anchor_kind"] = "ipc_prefix"
        decision = PatentGraphSemanticDecision(mode="direct_answer", route_family="precise", standalone=standalone, diagnostics=diagnostics)
    elif candidates:
        route_family = "hybrid" if any(not bool(item.get("direct_answer_eligible")) for item in candidates) else "precise"
        mode = "graph_for_rag" if route_family == "hybrid" else "direct_answer"
        decision = PatentGraphSemanticDecision(
            mode=mode,
            route_family=route_family,
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule=str(candidates[0].get("path_id") or "parametric"), candidates=candidates),
        )
    else:
        decision = PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="no_graph_signal", candidates=candidates),
        )

    if _contains_file_context(conversation_context or {}):
        downgraded_mode = "graph_for_rag" if decision.mode == "direct_answer" else decision.mode
        diagnostics = dict(decision.diagnostics)
        diagnostics["override"] = "file_context_present"
        return PatentGraphSemanticDecision(
            mode=downgraded_mode,
            route_family=decision.route_family,
            standalone=False,
            requires_context_resolution=True,
            diagnostics=diagnostics,
        )

    return decision
