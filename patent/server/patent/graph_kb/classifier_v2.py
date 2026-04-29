from __future__ import annotations

from typing import Any

from server.patent.graph_kb.classifier import _contains_file_context
from server.patent.graph_kb.models import PatentGraphSemanticDecision
from server.patent.graph_kb.query_templates import build_patent_template_candidates
from server.patent.graph_kb.slots import PatentGraphQuestionSlots, extract_patent_graph_slots


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
    elif slots.applicant_names and (slots.asks_trend_landscape or slots.process_terms or slots.material_terms) and not (slots.asks_count or slots.asks_list):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="community",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="community_landscape", candidates=candidates),
        )
    elif slots.asks_why_how and (slots.patent_ids or slots.process_terms or slots.material_terms or slots.metric_terms):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="hybrid_graph_anchor", candidates=candidates),
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
