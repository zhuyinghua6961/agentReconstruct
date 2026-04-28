from __future__ import annotations

from typing import Any

from app.modules.graph_kb.client import build_legacy_template_query_plan
from app.modules.graph_kb.models import GraphQueryPath, GraphQueryPlanV2, SemanticDecision
from app.modules.graph_kb.query_templates import build_v1_query_paths
from app.modules.graph_kb.schema_registry import SchemaRegistry


def _tuple_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip().lower(),) if value.strip() else ()
    return tuple(str(item).strip().lower() for item in tuple(value or ()) if str(item or "").strip())


def _candidate_queries(paths: tuple[GraphQueryPath, ...]) -> list[dict[str, Any]]:
    return [path.as_candidate_query() for path in paths]


def _recipe_terms(slots: dict[str, Any], key: str) -> tuple[str, ...]:
    recipe_terms = slots.get("recipe_terms") if isinstance(slots.get("recipe_terms"), dict) else {}
    return _tuple_values(recipe_terms.get(key))


def _process_terms(slots: dict[str, Any]) -> tuple[str, ...]:
    process_terms = slots.get("process_terms") if isinstance(slots.get("process_terms"), dict) else {}
    values: list[str] = []
    for item in process_terms.values():
        values.extend(_tuple_values(item))
    return tuple(values)


def _terms_from_slots(slots: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("entities", "title_terms", "material_terms", "raw_material_terms"):
        values.extend(_tuple_values(slots.get(key)))
    return tuple(dict.fromkeys(values))


def _intent_and_template_slots(question: str, decision: SemanticDecision) -> tuple[str, dict[str, Any]]:
    slots = dict(decision.slots or {})
    legacy_template_plan = build_legacy_template_query_plan(question)
    doi = str(slots.get("doi") or "").strip()
    if doi:
        if str(slots.get("doi_intent") or "") == "expand":
            return "expand_doi_context", {"doi": doi}
        return "lookup_by_doi", {"doi": doi}
    if legacy_template_plan is not None:
        if legacy_template_plan.template_id == "list_by_raw_material":
            return "list_by_raw_material", {"raw_material_terms": (legacy_template_plan.params.get("material_name"),)}
        if legacy_template_plan.template_id == "list_by_material":
            return "list_by_title_or_material", {"terms": (legacy_template_plan.params.get("material_name"),)}
        if legacy_template_plan.template_id == "count_by_filter":
            return "count_by_structured_field", {
                "field": "raw_material.name",
                "terms": (legacy_template_plan.params.get("material_name"),),
            }

    carbon_source_terms = _recipe_terms(slots, "carbon_source")
    if decision.legacy_route == "community":
        return "community_find_by_term", {"terms": _terms_from_slots(slots)}
    if bool(slots.get("count_signal")) and carbon_source_terms:
        return "count_by_structured_field", {"field": "recipe.carbon_source", "carbon_source_terms": carbon_source_terms}
    if carbon_source_terms:
        return "list_by_carbon_source", {"carbon_source_terms": carbon_source_terms}

    process_terms = _process_terms(slots)
    if process_terms:
        return "list_by_process_method", {"process_terms": process_terms}

    property_field = str(slots.get("property_field") or "").strip()
    if property_field:
        return "numeric_property_query", {"property_field": property_field, "title_terms": _terms_from_slots(slots)}

    raw_material_terms = _tuple_values(slots.get("raw_material_terms"))
    if raw_material_terms:
        return "list_by_raw_material", {"raw_material_terms": raw_material_terms}

    terms = _terms_from_slots(slots)
    if terms:
        return "list_by_title_or_material", {"terms": terms}

    return "", {}


def _legacy_template_id(intent: str) -> str:
    return {
        "lookup_by_doi": "lookup_by_doi",
        "expand_doi_context": "expand_doi_context_by_doi",
        "list_by_title_or_material": "list_by_material",
        "list_by_raw_material": "list_by_raw_material",
        "count_by_structured_field": "count_by_filter",
    }.get(intent, "")


def build_graph_query_plan_v2(
    *,
    question: str,
    decision: SemanticDecision,
    schema_registry: SchemaRegistry,
) -> GraphQueryPlanV2 | None:
    _ = schema_registry
    if decision.mode == "skip_graph":
        return None

    intent, template_slots = _intent_and_template_slots(question, decision)
    if not intent:
        return None

    paths = build_v1_query_paths(intent=intent, slots=template_slots, limit=20)
    if not paths:
        return None

    legacy_template_plan = build_legacy_template_query_plan(question)
    legacy_template_id = legacy_template_plan.template_id if legacy_template_plan is not None else _legacy_template_id(intent)
    direct_paths = any(path.direct_answer_eligible for path in paths)
    strategy = "template" if legacy_template_plan is not None else ("parametric" if intent == "numeric_property_query" else "v1_template")

    return GraphQueryPlanV2(
        strategy=strategy,
        intent=intent,
        question=question,
        legacy_template_id=legacy_template_id,
        legacy_template_plan=legacy_template_plan,
        parametric_slots={
            "question": question,
            "slots": template_slots,
            "candidate_queries": _candidate_queries(paths),
            "direct_answer_eligible": direct_paths,
        },
        diagnostics={"legacy_route": decision.legacy_route, "route_family": decision.route_family or decision.legacy_route},
    )
