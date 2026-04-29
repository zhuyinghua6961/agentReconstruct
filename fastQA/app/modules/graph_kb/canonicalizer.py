from __future__ import annotations

from typing import Any

from app.modules.graph_kb.community_labels import build_community_label
from app.modules.graph_kb.doi_quality import classify_doi_quality
from app.modules.graph_kb.models import GraphConstraint, GraphEvidenceBundle, GraphQueryPlanV2
from app.modules.graph_kb.value_parsers import parse_capacity, parse_conductivity, parse_density, parse_retention


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _append_hint(hints: dict[str, list[str]], key: str, value: Any) -> None:
    if isinstance(value, (list, tuple)):
        for item in value:
            _append_hint(hints, key, item)
        return
    text = _clean(value)
    if text and text not in hints.setdefault(key, []):
        hints[key].append(text)


def _fact_from_row(row: dict[str, Any]) -> str:
    return "; ".join(f"{key}={value}" for key, value in row.items() if value not in (None, "", [], ()))


def _row_doi_values(row: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    doi = _clean(row.get("doi"))
    if doi:
        values.append(doi)
    for item in list(row.get("dois") or []):
        text = _clean(item)
        if text:
            values.append(text)
    return tuple(values)


def _parse_numeric_row(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("value") or row.get("capacity") or row.get("density") or row.get("conductivity") or row.get("retention")
    original = _clean(value)
    if not original:
        return row
    parsed = parse_capacity(original)
    if parsed.value is None:
        parsed = parse_density(original)
    if parsed.value is None:
        parsed = parse_conductivity(original)
    if parsed.value is None:
        parsed = parse_retention(original)
    enriched = dict(row)
    enriched["original_value"] = original
    if parsed.value is not None:
        enriched["parsed_value"] = parsed.value
        enriched["parsed_unit"] = parsed.unit
        enriched["parser_confidence"] = parsed.confidence
    return enriched


def _constraints_from_plan(plan: GraphQueryPlanV2) -> tuple[GraphConstraint, ...]:
    slots = plan.parametric_slots.get("slots") if isinstance(plan.parametric_slots.get("slots"), dict) else {}
    constraints: list[GraphConstraint] = []
    property_field = _clean(slots.get("property_field"))
    operator = _clean(slots.get("operator"))
    threshold = slots.get("threshold")
    if property_field and operator and threshold is not None:
        constraints.append(GraphConstraint(field=f"performance.{property_field}", operator=operator, value=threshold))

    recipe_terms = slots.get("recipe_terms") if isinstance(slots.get("recipe_terms"), dict) else {}
    for field, values in dict(recipe_terms or {}).items():
        for value in tuple(values or ()):
            text = _clean(value)
            if text:
                constraints.append(GraphConstraint(field=f"recipe.{field}", operator="contains", value=text))
    return tuple(constraints)


def _numeric_slots(plan: GraphQueryPlanV2) -> dict[str, Any]:
    return plan.parametric_slots.get("slots") if isinstance(plan.parametric_slots.get("slots"), dict) else {}


def _passes_numeric_operator(row: dict[str, Any], *, operator: str, threshold: Any) -> bool:
    if not operator or threshold is None:
        return True
    parsed_value = row.get("parsed_value")
    if parsed_value is None:
        numeric_source = row.get("original_value") or row.get("value") or row.get("capacity") or row.get("density") or row.get("conductivity") or row.get("retention")
        return not bool(_clean(numeric_source))
    try:
        left = float(parsed_value)
        right = float(threshold)
    except (TypeError, ValueError):
        return False
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "=":
        return left == right
    return True


def _has_numeric_source(row: dict[str, Any]) -> bool:
    if row.get("parsed_value") is not None:
        return True
    return any(_clean(row.get(key)) for key in ("original_value", "value", "capacity", "density", "conductivity", "retention"))


def _apply_numeric_policy(*, plan: GraphQueryPlanV2, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if plan.intent not in {"numeric_property_query", "hybrid_property_analysis"}:
        return rows
    slots = _numeric_slots(plan)
    operator = _clean(slots.get("operator"))
    threshold = slots.get("threshold")
    numeric_rows = [row for row in rows if _has_numeric_source(row)]
    expansion_rows = [row for row in rows if not _has_numeric_source(row)]
    filtered = [row for row in numeric_rows if _passes_numeric_operator(row, operator=operator, threshold=threshold)]
    ranking = _clean(slots.get("ranking"))
    limit = slots.get("limit")
    if ranking == "top":
        numeric_rows = sorted(
            [row for row in filtered if row.get("parsed_value") is not None],
            key=lambda row: float(row.get("parsed_value") or 0.0),
            reverse=True,
        )
        unparsed_numeric_rows = [row for row in filtered if row.get("parsed_value") is None]
        filtered = numeric_rows + unparsed_numeric_rows
    if limit is not None:
        try:
            parsed_limit = max(1, int(limit))
            numeric_rows = _dedupe_numeric_rows_for_limit(
                [row for row in filtered if row.get("parsed_value") is not None]
            )[:parsed_limit]
            unparsed_numeric_rows = [row for row in filtered if row.get("parsed_value") is None]
            filtered = numeric_rows + unparsed_numeric_rows
        except (TypeError, ValueError):
            pass
    if plan.intent == "hybrid_property_analysis":
        allowed_dois = {doi for row in filtered for doi in _row_doi_values(row)}
        expansion_rows = [row for row in expansion_rows if any(doi in allowed_dois for doi in _row_doi_values(row))]
    if plan.intent == "numeric_property_query":
        return filtered + expansion_rows
    return filtered + expansion_rows


def _dedupe_numeric_rows_for_limit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        doi_values = _row_doi_values(row)
        key = doi_values or (_clean(row.get("title")), _clean(row.get("sample_name")), _clean(row.get("original_value") or row.get("value")))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def canonicalize_graph_rows(*, plan: GraphQueryPlanV2, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> GraphEvidenceBundle:
    source_rows = [dict(item or {}) for item in list(rows or []) if isinstance(item, dict)]
    canonical_rows: list[dict[str, Any]] = []
    doi_candidates: list[str] = []
    direct_render_dois: list[str] = []
    facts: list[str] = []
    hints: dict[str, list[str]] = {}
    suspicious_doi_count = 0
    invalid_doi_count = 0

    prepared_rows = [
        _parse_numeric_row(row) if plan.intent in {"numeric_property_query", "hybrid_property_analysis"} else dict(row)
        for row in source_rows
    ]
    prepared_rows = _apply_numeric_policy(plan=plan, rows=prepared_rows)

    for current in prepared_rows:
        for doi in _row_doi_values(current):
            quality = classify_doi_quality(doi)
            if quality.status == "valid":
                if doi not in doi_candidates:
                    doi_candidates.append(doi)
                if doi not in direct_render_dois:
                    direct_render_dois.append(doi)
            elif quality.status == "suspicious":
                suspicious_doi_count += 1
            else:
                invalid_doi_count += 1
        canonical_rows.append(current)
        fact = _fact_from_row(current)
        if fact:
            facts.append(fact)

        _append_hint(hints, "titles", current.get("title"))
        _append_hint(hints, "titles", current.get("titles"))
        _append_hint(hints, "materials", current.get("sample_name"))
        _append_hint(hints, "raw_materials", current.get("raw_materials"))
        _append_hint(hints, "raw_materials", current.get("matched_raw_materials"))
        _append_hint(hints, "materials", current.get("materials"))
        _append_hint(hints, "carbon_sources", current.get("carbon_source"))
        _append_hint(hints, "carbon_sources", current.get("carbon_sources"))
        _append_hint(hints, "process_methods", current.get("preparation_methods"))
        if plan.intent == "numeric_property_query":
            _append_hint(hints, "performance_fields", current.get("parsed_unit") or current.get("value") or current.get("capacity"))

    render_slots: dict[str, Any] = {
        "rows": tuple(canonical_rows),
        "template_id": plan.legacy_template_id,
        "intent": plan.intent,
    }
    direct_answerable = bool(canonical_rows) and (
        plan.strategy == "template" or bool(plan.parametric_slots.get("direct_answer_eligible"))
    )

    if plan.intent == "count_by_structured_field" and canonical_rows:
        first = canonical_rows[0]
        render_slots["count"] = int(first.get("count") or 0)
        render_slots["field_label"] = _clean(first.get("field_label")) or "carbon_source"
        render_slots["term"] = _clean(first.get("term")) or _clean((plan.parametric_slots.get("slots") or {}).get("term"))
        render_slots["direct_answerable"] = render_slots["field_label"] in {"carbon_source", "raw_material", "process_method"}
        direct_answerable = bool(render_slots["direct_answerable"])

    if plan.intent.startswith("community"):
        titles = _dedupe([_clean(item) for row in canonical_rows for item in ([row.get("title")] + list(row.get("titles") or []))])
        materials = _dedupe(
            [
                _clean(item)
                for row in canonical_rows
                for item in ([row.get("sample_name") or row.get("material")] + list(row.get("materials") or []))
            ]
        )
        methods = _dedupe([_clean(item) for row in canonical_rows for item in list(row.get("preparation_methods") or [])])
        label = build_community_label(
            community_id=(canonical_rows[0].get("community_id") if canonical_rows else None),
            titles=titles,
            materials=materials,
            methods=methods,
        )
        render_slots["community_label"] = label
        hints.setdefault("community_labels", []).append(label)

    return GraphEvidenceBundle(
        doi_candidates=tuple(doi_candidates),
        direct_render_dois=tuple(direct_render_dois),
        facts=tuple(facts),
        render_slots=render_slots,
        direct_answerable=direct_answerable,
        constraints_for_rag=_constraints_from_plan(plan),
        diagnostics={
            "filtered_doi_count": len(direct_render_dois),
            "suspicious_doi_count": suspicious_doi_count,
            "invalid_doi_count": invalid_doi_count,
        },
        entity_hints={key: tuple(values[:5]) for key, values in hints.items() if values},
    )
