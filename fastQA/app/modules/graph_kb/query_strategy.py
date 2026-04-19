from __future__ import annotations

from app.modules.graph_kb.client import build_legacy_template_query_plan
from app.modules.graph_kb.models import SemanticDecision


_NUMERIC_HINTS = ("压实密度", "比容量", "容量", "电压", "倍率", "循环", "粒径", "放电容量")
_PRECISE_HINTS = ("大于", "小于", "高于", "低于", "超过", "最高", "最低", "top")


def can_use_legacy_template(question: str) -> bool:
    return build_legacy_template_query_plan(question) is not None


def can_build_parametric_query(*, question: str, decision: SemanticDecision) -> bool:
    text = str(question or "")
    return decision.legacy_route == "precise" and any(hint in text for hint in _NUMERIC_HINTS) and any(hint in text.lower() for hint in _PRECISE_HINTS)


def select_query_strategy(*, question: str, decision: SemanticDecision) -> str | None:
    if decision.mode == "skip_graph":
        return None
    if can_use_legacy_template(question):
        return "template"
    if can_build_parametric_query(question=question, decision=decision):
        return "parametric"
    return "llm_cypher"
