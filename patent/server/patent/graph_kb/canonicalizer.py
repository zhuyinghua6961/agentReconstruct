from __future__ import annotations

from typing import Any

from server.patent.graph_kb.models import PatentGraphConstraint, PatentGraphEvidenceBundle, PatentGraphQueryPlanV2
from server.patent.graph_kb.query_templates import get_patent_query_template
from server.patent.graph_kb.slots import extract_patent_graph_slots


_SAFE_DIRECT_PARAMETRIC_PATHS = {
    "lookup_patent_by_id",
    "list_patent_process_steps",
    "list_patent_material_roles",
    "list_patent_experiment_tables",
    "list_patent_problem_solution",
    "list_patent_inventive_scope",
    "list_patent_citations",
    "list_patents_by_inventor",
    "list_patents_by_agency",
    "list_patents_by_applicant",
    "list_patents_by_ipc_prefix",
    "list_patents_by_ipc_code_prefix",
    "list_patents_by_ipc_full_code",
    "count_patents_by_ipc_prefix",
    "count_patents_by_ipc_code_prefix",
    "count_patents_by_ipc_full_code",
    "count_patents_by_applicant",
    "count_patents_by_inventor",
    "count_patents_by_agency",
    "list_patent_atmospheres",
    "list_patent_embodiment_insights",
    "list_patents_by_material",
    "list_patents_by_material_role",
    "list_patents_by_process_term",
    "rank_materials_by_frequency",
    "rank_processes_by_frequency",
}
_GENERIC_ROW_KEYS = {"patent_id", "title", "abstract", "stub", "application_date", "publication_date", "legal_status"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _iter_clean_items(values: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    items: list[str] = []
    for item in list(values or []):
        text = _text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return tuple(items)


def _append_unique(target: list[str], value: Any) -> None:
    text = _text(value)
    if text and text not in target:
        target.append(text)


def _candidate_queries(plan: PatentGraphQueryPlanV2) -> list[dict[str, Any]]:
    return [dict(item or {}) for item in list(plan.parametric_slots.get("candidate_queries") or []) if isinstance(item, dict)]


def _infer_primary_path_id(plan: PatentGraphQueryPlanV2) -> str:
    candidate_queries = list(plan.parametric_slots.get("candidate_queries") or [])
    if candidate_queries:
        return str(candidate_queries[0].get("path_id") or "")
    return ""


def _select_candidate(plan: PatentGraphQueryPlanV2, matched_path: str = "") -> dict[str, Any]:
    candidates = _candidate_queries(plan)
    normalized_matched_path = _text(matched_path)
    if normalized_matched_path:
        for candidate in candidates:
            if _text(candidate.get("path_id")) == normalized_matched_path:
                return candidate
    return candidates[0] if candidates else {}


def _normalized_set(values: Any) -> set[str]:
    return {_text(item).lower() for item in list(values or []) if _text(item)}


def _slots_for_plan(plan: PatentGraphQueryPlanV2) -> dict[str, Any]:
    slots = dict(plan.parametric_slots.get("slots") or {})
    if slots or not _text(plan.question):
        return slots
    return extract_patent_graph_slots(plan.question).diagnostics()


def _material_constraint_terms(slots: dict[str, Any]) -> tuple[str, ...]:
    role_terms = _normalized_set(slots.get("material_role_terms"))
    process_terms = tuple(_text(item).lower() for item in tuple(slots.get("process_terms") or ()) if _text(item))
    atmosphere_terms = tuple(_text(item).lower() for item in tuple(slots.get("atmosphere_terms") or ()) if _text(item))
    material_terms: list[str] = []
    for value in tuple(slots.get("material_terms") or ()):
        text = _text(value)
        lowered = text.lower()
        if not text or lowered in role_terms:
            continue
        if any(term and term in lowered for term in process_terms):
            continue
        if any(term and term in lowered for term in atmosphere_terms):
            continue
        if any(term and term in lowered for term in role_terms):
            continue
        material_terms.append(text)
    return tuple(material_terms)


def _is_allowed_material_constraint(value: Any, slots: dict[str, Any]) -> bool:
    if not slots:
        return bool(_text(value))
    return _text(value) in set(_material_constraint_terms(slots))


def _append_constraint(
    constraints: list[PatentGraphConstraint],
    *,
    field: str,
    operator: str,
    value: Any,
) -> None:
    text = _text(value)
    if not text:
        return
    key = (field, operator, text)
    if any((item.field, item.operator, _text(item.value)) == key for item in constraints):
        return
    constraints.append(PatentGraphConstraint(field=field, operator=operator, value=text))


def _infer_selected_path_id(plan: PatentGraphQueryPlanV2, matched_path: str = "") -> str:
    if plan.strategy == "template":
        return _text(matched_path) or _text(plan.legacy_template_id or getattr(plan.legacy_template_plan, "template_id", ""))
    candidate = _select_candidate(plan, matched_path=matched_path)
    return _text(candidate.get("path_id"))


def _has_non_stub_rows(rows: tuple[dict[str, Any], ...]) -> bool:
    if not rows:
        return False
    for row in rows:
        if not bool(row.get("stub")) and not bool(row.get("cited_stub")):
            return True
    return False


def _has_requested_facet(rows: tuple[dict[str, Any], ...]) -> bool:
    for row in rows:
        for key, value in row.items():
            if key in _GENERIC_ROW_KEYS or key.endswith("_stub"):
                continue
            if value not in (None, "", [], (), {}):
                return True
    return False


def _has_textual_fact(rows: tuple[dict[str, Any], ...]) -> bool:
    for row in rows:
        for key, value in row.items():
            if key in {"stub", "cited_stub"}:
                continue
            if isinstance(value, (list, tuple)) and any(_text(item) for item in value):
                return True
            if _text(value):
                return True
    return False


def _evaluate_evidence_quality(plan: PatentGraphQueryPlanV2, rows: tuple[dict[str, Any], ...], *, path_id: str = "") -> dict[str, Any]:
    primary_path_id = _text(path_id) or _infer_primary_path_id(plan)
    has_rows = bool(rows)
    has_requested_facet = _has_requested_facet(rows)
    has_identifier = any(_text(row.get("patent_id") or row.get("cited_patent_id")) for row in rows)
    has_measurement_value = any(_text(row.get("value_raw") or row.get("measurement_value")) for row in rows)
    is_stub_only = has_rows and not _has_non_stub_rows(rows) and not has_requested_facet
    result_cap = 20
    template = get_patent_query_template(primary_path_id)
    if template is not None:
        result_cap = int(template.result_cap or result_cap)
    return {
        "has_rows": has_rows,
        "has_requested_facet": has_requested_facet,
        "has_textual_fact": _has_textual_fact(rows),
        "has_identifier": has_identifier,
        "is_bounded": len(rows) <= result_cap,
        "is_partial": has_rows and not has_requested_facet,
        "is_stub_only": is_stub_only,
        "has_measurement_value": has_measurement_value,
        "result_cap": result_cap,
        "truncated": len(rows) >= result_cap,
    }


def _build_fact(row: dict[str, Any]) -> str:
    fact_parts: list[str] = []
    for key in sorted(row):
        if key in {"stub", "cited_stub"}:
            continue
        value = row.get(key)
        if value in (None, "", [], (), {}):
            continue
        if isinstance(value, (list, tuple)):
            items = [item for item in _iter_clean_items(value) if item]
            if not items:
                continue
            fact_parts.append(f"{key}={'；'.join(items)}")
            continue
        text = _text(value)
        if text:
            fact_parts.append(f"{key}={text}")
    return "; ".join(fact_parts)


def _constraints_for_plan(plan: PatentGraphQueryPlanV2, *, matched_path: str = "") -> tuple[PatentGraphConstraint, ...]:
    constraints: list[PatentGraphConstraint] = []
    if plan.legacy_template_plan is not None:
        params = dict(plan.legacy_template_plan.params or {})
        template_id = str(plan.legacy_template_id or plan.legacy_template_plan.template_id or "")
        if template_id == "lookup_patent_by_id":
            _append_constraint(constraints, field="patent.id", operator="eq", value=params.get("patent_id"))
        if template_id == "list_patents_by_ipc":
            _append_constraint(constraints, field="ipc.code", operator="eq", value=params.get("ipc_code"))
        if template_id == "list_patents_by_applicant":
            _append_constraint(constraints, field="organization.applicant", operator="eq", value=params.get("organization_name"))
        return tuple(constraints)

    selected_candidate = _select_candidate(plan, matched_path=matched_path)
    selected_path_id = str(selected_candidate.get("path_id") or "")
    candidate_sources = [selected_candidate] if selected_candidate else []
    slots = _slots_for_plan(plan)
    if str(plan.diagnostics.get("matched_rule") or "") in {
        "combined_facet_listing_requires_rag",
        "material_process_synthesis_question",
    }:
        for candidate in _candidate_queries(plan):
            if candidate not in candidate_sources:
                candidate_sources.append(candidate)

    for candidate in candidate_sources:
        path_id = str(candidate.get("path_id") or "")
        params = dict(candidate.get("params") or {})
        if path_id in {"list_patents_by_inventor", "count_patents_by_inventor"}:
            _append_constraint(constraints, field="person.inventor", operator="eq", value=params.get("inventor_name"))
        elif path_id in {"list_patents_by_agency", "count_patents_by_agency"}:
            _append_constraint(constraints, field="organization.agency", operator="eq", value=params.get("agency_name"))
        elif path_id in {"list_patents_by_ipc_prefix", "count_patents_by_ipc_prefix"}:
            _append_constraint(constraints, field="ipc.subclass", operator="eq", value=params.get("ipc_prefix"))
        elif path_id in {"list_patents_by_ipc_code_prefix", "count_patents_by_ipc_code_prefix"}:
            _append_constraint(constraints, field="ipc.code", operator="starts_with", value=params.get("ipc_code_prefix"))
        elif path_id in {"list_patents_by_ipc_full_code", "count_patents_by_ipc_full_code"}:
            _append_constraint(constraints, field="ipc.code", operator="eq", value=params.get("ipc_full_code"))
        elif path_id in {"list_patents_by_applicant", "count_patents_by_applicant"}:
            _append_constraint(
                constraints,
                field="organization.applicant",
                operator="eq",
                value=params.get("applicant_name") or params.get("organization_name"),
            )
        elif path_id in {
            "lookup_patent_by_id",
            "list_patent_process_steps",
            "list_patent_material_roles",
            "list_patent_experiment_tables",
            "list_patent_problem_solution",
            "list_patent_inventive_scope",
            "list_patent_citations",
            "list_patent_atmospheres",
            "list_patent_embodiment_insights",
        }:
            _append_constraint(constraints, field="patent.id", operator="eq", value=params.get("patent_id"))
        elif path_id == "list_patents_by_material":
            if _is_allowed_material_constraint(params.get("material_term"), slots):
                _append_constraint(constraints, field="material.name", operator="contains", value=params.get("material_term"))
        elif path_id == "list_patents_by_material_role":
            _append_constraint(constraints, field="material.role", operator="contains", value=params.get("material_role_term"))
        elif path_id == "list_patents_by_process_term":
            _append_constraint(constraints, field="process.step", operator="contains", value=params.get("process_term"))

    if slots and selected_path_id in {"list_patents_by_material", "list_patents_by_material_role", "list_patents_by_process_term"}:
        for value in tuple(slots.get("material_role_terms") or ()):
            _append_constraint(constraints, field="material.role", operator="contains", value=value)
        for value in _material_constraint_terms(slots):
            _append_constraint(constraints, field="material.name", operator="contains", value=value)
        for value in tuple(slots.get("process_terms") or ()):
            _append_constraint(constraints, field="process.step", operator="contains", value=value)
        for value in tuple(slots.get("atmosphere_terms") or ()):
            _append_constraint(constraints, field="process.atmosphere", operator="contains", value=value)
    return tuple(constraints)


def canonicalize_patent_graph_rows(
    *,
    plan: PatentGraphQueryPlanV2,
    rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    matched_path: str = "",
) -> PatentGraphEvidenceBundle:
    normalized_rows = tuple(dict(item or {}) for item in list(rows or []) if isinstance(item, dict))

    patent_candidates: list[str] = []
    ipc_candidates: list[str] = []
    organization_candidates: list[str] = []
    inventor_candidates: list[str] = []
    facts: list[str] = []

    for row in normalized_rows:
        _append_unique(patent_candidates, row.get("patent_id"))
        _append_unique(patent_candidates, row.get("cited_patent_id"))
        _append_unique(ipc_candidates, row.get("ipc_match"))
        _append_unique(ipc_candidates, row.get("ipc_code"))
        _append_unique(ipc_candidates, row.get("ipc_subclass"))
        for item in _iter_clean_items(row.get("ipc_codes")):
            _append_unique(ipc_candidates, item)
        for item in _iter_clean_items(row.get("ipc_subclasses")):
            _append_unique(ipc_candidates, item)
        _append_unique(organization_candidates, row.get("applicant_name"))
        _append_unique(organization_candidates, row.get("agency_name"))
        for item in _iter_clean_items(row.get("applicants")):
            _append_unique(organization_candidates, item)
        for item in _iter_clean_items(row.get("agencies")):
            _append_unique(organization_candidates, item)
        _append_unique(inventor_candidates, row.get("inventor_name"))
        for item in _iter_clean_items(row.get("inventors")):
            _append_unique(inventor_candidates, item)

        fact = _build_fact(row)
        if fact:
            facts.append(fact)

    primary_path_id = _infer_primary_path_id(plan)
    selected_path_id = _infer_selected_path_id(plan, matched_path=matched_path)
    matched_path_id = _text(matched_path)
    direct_path_mismatch = bool(plan.strategy == "parametric" and matched_path_id and primary_path_id and selected_path_id != primary_path_id)
    evidence_quality = _evaluate_evidence_quality(plan, normalized_rows, path_id=selected_path_id)
    direct_answerable = bool(normalized_rows) and bool(evidence_quality["has_textual_fact"]) and not bool(evidence_quality["is_stub_only"]) and (
        (plan.strategy == "template" and bool(plan.legacy_template_id or plan.legacy_template_plan))
        or (
            plan.strategy == "parametric"
            and not direct_path_mismatch
            and selected_path_id in _SAFE_DIRECT_PARAMETRIC_PATHS
            and bool(evidence_quality["has_requested_facet"] or selected_path_id == "lookup_patent_by_id")
        )
    )

    diagnostics = dict(plan.diagnostics or {})
    diagnostics["row_count"] = len(normalized_rows)
    diagnostics["evidence_quality"] = evidence_quality
    if selected_path_id:
        diagnostics["path_id"] = selected_path_id
    if primary_path_id and primary_path_id != selected_path_id:
        diagnostics["primary_path_id"] = primary_path_id
    if matched_path_id:
        diagnostics["matched_path"] = matched_path_id
    if direct_path_mismatch:
        diagnostics["direct_downgrade_reason"] = "matched_fallback_path_differs_from_primary"

    return PatentGraphEvidenceBundle(
        patent_candidates=tuple(patent_candidates),
        ipc_candidates=tuple(ipc_candidates),
        organization_candidates=tuple(organization_candidates),
        inventor_candidates=tuple(inventor_candidates),
        facts=tuple(facts),
        render_slots={
            "rows": normalized_rows,
            "strategy": plan.strategy,
            "template_id": plan.legacy_template_id,
            "path_id": selected_path_id,
            "primary_path_id": primary_path_id,
            "matched_path": matched_path_id,
        },
        direct_answerable=direct_answerable,
        constraints_for_rag=_constraints_for_plan(plan, matched_path=matched_path),
        confidence=1.0 if direct_answerable else (0.7 if normalized_rows else 0.0),
        diagnostics=diagnostics,
    )
