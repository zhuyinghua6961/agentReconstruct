from __future__ import annotations

from typing import Any

from app.modules.graph_kb.community_labels import build_community_label
from app.modules.graph_kb.doi_quality import classify_doi_quality
from app.modules.graph_kb.models import GraphEvidenceBundle, GraphQueryPlanV2
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


def canonicalize_graph_rows(*, plan: GraphQueryPlanV2, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> GraphEvidenceBundle:
    source_rows = [dict(item or {}) for item in list(rows or []) if isinstance(item, dict)]
    canonical_rows: list[dict[str, Any]] = []
    doi_candidates: list[str] = []
    direct_render_dois: list[str] = []
    facts: list[str] = []
    hints: dict[str, list[str]] = {}
    suspicious_doi_count = 0
    invalid_doi_count = 0

    for row in source_rows:
        current = _parse_numeric_row(row) if plan.intent == "numeric_property_query" else dict(row)
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
        constraints_for_rag=(),
        diagnostics={
            "filtered_doi_count": len(direct_render_dois),
            "suspicious_doi_count": suspicious_doi_count,
            "invalid_doi_count": invalid_doi_count,
        },
        entity_hints={key: tuple(values[:5]) for key, values in hints.items() if values},
    )
