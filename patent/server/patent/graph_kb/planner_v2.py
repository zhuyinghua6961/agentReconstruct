from __future__ import annotations

from server.patent.graph_kb.client import build_patent_parametric_query_candidates, plan_patent_graph_query
from server.patent.graph_kb.models import PatentGraphQueryPlanV2, PatentGraphSemanticDecision, PatentSchemaRegistry
from server.patent.graph_kb.query_strategy import select_patent_query_strategy


def build_patent_graph_query_plan_v2(
    *,
    question: str,
    decision: PatentGraphSemanticDecision,
    schema_registry: PatentSchemaRegistry,
) -> PatentGraphQueryPlanV2 | None:
    strategy = select_patent_query_strategy(question=question, decision=decision)
    if strategy is None:
        return None

    matched_rule = str(decision.diagnostics.get("matched_rule") or "")
    diagnostics = {
        "route_family": decision.route_family,
        "matched_rule": matched_rule,
        "strategy": strategy,
    }

    if strategy == "template":
        legacy_template_plan = plan_patent_graph_query(question)
        if legacy_template_plan is None:
            return None
        diagnostics["legacy_template_id"] = legacy_template_plan.template_id
        return PatentGraphQueryPlanV2(
            strategy="template",
            intent=legacy_template_plan.template_id,
            question=question,
            legacy_template_id=legacy_template_plan.template_id,
            legacy_template_plan=legacy_template_plan,
            diagnostics=diagnostics,
        )

    summary = schema_registry.summarize_for_planner(intent=decision.route_family)
    candidate_queries = build_patent_parametric_query_candidates(question)
    if not candidate_queries:
        return None

    diagnostics["candidate_path_ids"] = tuple(str(item.get("path_id") or "") for item in candidate_queries)
    return PatentGraphQueryPlanV2(
        strategy="parametric",
        intent=matched_rule or str(candidate_queries[0].get("path_id") or "parametric"),
        question=question,
        parametric_slots={
            "question": question,
            "allowed_labels": summary.allowed_labels,
            "allowed_relations": summary.allowed_relations,
            "candidate_queries": candidate_queries,
        },
        diagnostics=diagnostics,
    )
