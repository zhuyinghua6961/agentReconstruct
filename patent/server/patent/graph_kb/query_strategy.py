from __future__ import annotations

from server.patent.graph_kb.client import build_patent_parametric_query_candidates, plan_patent_graph_query
from server.patent.graph_kb.models import PatentGraphSemanticDecision


def can_use_patent_legacy_template(question: str) -> bool:
    return plan_patent_graph_query(question) is not None


def can_build_patent_parametric_query(
    *,
    question: str,
    decision: PatentGraphSemanticDecision,
) -> bool:
    if decision.mode == "skip_graph":
        return False
    return bool(build_patent_parametric_query_candidates(question))


def select_patent_query_strategy(
    *,
    question: str,
    decision: PatentGraphSemanticDecision,
) -> str | None:
    if decision.mode == "skip_graph":
        return None
    if can_use_patent_legacy_template(question):
        return "template"
    if can_build_patent_parametric_query(question=question, decision=decision):
        return "parametric"
    return None
