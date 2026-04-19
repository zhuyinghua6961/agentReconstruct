from __future__ import annotations

import re

from app.modules.graph_kb.client import build_legacy_template_query_plan
from app.modules.graph_kb.models import GraphQueryPlanV2, SemanticDecision
from app.modules.graph_kb.query_strategy import can_build_parametric_query, select_query_strategy
from app.modules.graph_kb.schema_registry import SchemaRegistry


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._+\-/]*|[\u4e00-\u9fff]{2,8}")
_STOPWORDS = {
    "请",
    "哪些",
    "有哪些",
    "什么",
    "为什么",
    "如何",
    "以及",
    "文献",
    "论文",
    "材料",
    "方法",
    "测试",
    "表征",
}

_PRIMARY_SEARCH_CYPHER = (
    "MATCH (d:doi) "
    "OPTIONAL MATCH (d)-[:title]->(t:title) "
    "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
    "OPTIONAL MATCH (s:name)-[:name]->(d) "
    "WITH d, t, collect(DISTINCT rm.name) AS raw_materials, collect(DISTINCT s.name) AS sample_names "
    "WHERE any(term IN $query_terms WHERE "
    "toLower(d.name) CONTAINS term OR "
    "toLower(coalesce(t.name, '')) CONTAINS term OR "
    "any(item IN raw_materials WHERE toLower(coalesce(item, '')) CONTAINS term) OR "
    "any(item IN sample_names WHERE toLower(coalesce(item, '')) CONTAINS term)) "
    "RETURN d.name AS doi, t.name AS title, raw_materials, sample_names LIMIT $limit"
)

_SUPPORT_SEARCH_CYPHER = (
    "MATCH (d:doi) "
    "OPTIONAL MATCH (d)-[:title]->(t:title) "
    "OPTIONAL MATCH (d)-[:testing]->(:testing)-[:testing]->(tv:testing) "
    "OPTIONAL MATCH (d)-[:description]->(:description)-[:description]->(dv:description) "
    "OPTIONAL MATCH (d)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method) "
    "WITH d, t, collect(DISTINCT tv.name) AS testing_items, collect(DISTINCT dv.name) AS description_items, collect(DISTINCT pm.name) AS preparation_methods "
    "WHERE any(term IN $query_terms WHERE "
    "toLower(coalesce(t.name, '')) CONTAINS term OR "
    "any(item IN testing_items WHERE toLower(coalesce(item, '')) CONTAINS term) OR "
    "any(item IN description_items WHERE toLower(coalesce(item, '')) CONTAINS term) OR "
    "any(item IN preparation_methods WHERE toLower(coalesce(item, '')) CONTAINS term)) "
    "RETURN d.name AS doi, t.name AS title, testing_items, description_items, preparation_methods LIMIT $limit"
)


def _extract_query_terms(question: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(str(question or "")):
        normalized = str(token or "").strip().lower()
        if not normalized or normalized in seen or normalized in _STOPWORDS:
            continue
        seen.add(normalized)
        terms.append(normalized)
        if len(terms) >= 8:
            break
    if terms:
        return terms
    fallback = str(question or "").strip().lower()
    return [fallback] if fallback else ["lfp"]


def _build_candidate_queries(question: str) -> list[dict[str, object]]:
    params = {
        "query_terms": _extract_query_terms(question),
        "limit": 20,
    }
    return [
        {"path_id": "schema.primary", "cypher": _PRIMARY_SEARCH_CYPHER, "params": params},
        {"path_id": "schema.support", "cypher": _SUPPORT_SEARCH_CYPHER, "params": params},
    ]


def build_graph_query_plan_v2(
    *,
    question: str,
    decision: SemanticDecision,
    schema_registry: SchemaRegistry,
) -> GraphQueryPlanV2 | None:
    strategy = select_query_strategy(question=question, decision=decision)
    if strategy is None:
        return None

    if strategy == "template":
        legacy_template_plan = build_legacy_template_query_plan(question)
        if legacy_template_plan is None:
            return None
        return GraphQueryPlanV2(
            strategy="template",
            intent=legacy_template_plan.template_id,
            question=question,
            legacy_template_id=legacy_template_plan.template_id,
            legacy_template_plan=legacy_template_plan,
            diagnostics={"legacy_route": decision.legacy_route},
        )

    summary = schema_registry.summarize_for_planner(intent=decision.legacy_route)
    if strategy == "parametric" and can_build_parametric_query(question=question, decision=decision):
        return GraphQueryPlanV2(
            strategy="parametric",
            intent="legacy_precise_parametric",
            question=question,
            parametric_slots={
                "question": question,
                "allowed_labels": summary.allowed_labels,
                "allowed_relations": summary.allowed_relations,
                "candidate_queries": _build_candidate_queries(question),
            },
            diagnostics={"legacy_route": decision.legacy_route},
        )

    return GraphQueryPlanV2(
        strategy="llm_cypher",
        intent="legacy_route_llm_cypher",
        question=question,
        parametric_slots={
            "question": question,
            "schema_fields": summary.fields,
            "allowed_labels": summary.allowed_labels,
            "allowed_relations": summary.allowed_relations,
            "candidate_queries": _build_candidate_queries(question),
        },
        diagnostics={"legacy_route": decision.legacy_route},
    )
