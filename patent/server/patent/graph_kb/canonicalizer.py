from __future__ import annotations

from typing import Any

from server.patent.graph_kb.models import PatentGraphConstraint, PatentGraphEvidenceBundle, PatentGraphQueryPlanV2


_SAFE_DIRECT_PARAMETRIC_PATHS = {
    "list_patents_by_inventor",
    "list_patents_by_agency",
    "list_patents_by_ipc_subclass",
    "count_patents_by_ipc",
    "count_patents_by_applicant",
    "count_patents_by_inventor",
    "list_patent_atmospheres",
    "list_patent_embodiment_insights",
}


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


def _infer_primary_path_id(plan: PatentGraphQueryPlanV2) -> str:
    candidate_queries = list(plan.parametric_slots.get("candidate_queries") or [])
    if len(candidate_queries) == 1:
        return str(candidate_queries[0].get("path_id") or "")
    return ""


def _has_usable_rows(rows: tuple[dict[str, Any], ...]) -> bool:
    if not rows:
        return False
    for row in rows:
        if not bool(row.get("stub")) and not bool(row.get("cited_stub")):
            return True
    return False


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


def _constraints_for_plan(plan: PatentGraphQueryPlanV2) -> tuple[PatentGraphConstraint, ...]:
    constraints: list[PatentGraphConstraint] = []
    if plan.legacy_template_plan is not None:
        params = dict(plan.legacy_template_plan.params or {})
        template_id = str(plan.legacy_template_id or plan.legacy_template_plan.template_id or "")
        if template_id == "lookup_patent_by_id" and _text(params.get("patent_id")):
            constraints.append(PatentGraphConstraint(field="patent.id", operator="eq", value=_text(params.get("patent_id"))))
        if template_id == "list_patents_by_ipc" and _text(params.get("ipc_code")):
            constraints.append(PatentGraphConstraint(field="ipc.code", operator="eq", value=_text(params.get("ipc_code"))))
        if template_id == "list_patents_by_applicant" and _text(params.get("organization_name")):
            constraints.append(
                PatentGraphConstraint(field="organization.applicant", operator="eq", value=_text(params.get("organization_name")))
            )
        return tuple(constraints)

    candidate_queries = list(plan.parametric_slots.get("candidate_queries") or [])
    if len(candidate_queries) != 1:
        return ()
    candidate = dict(candidate_queries[0] or {})
    path_id = str(candidate.get("path_id") or "")
    params = dict(candidate.get("params") or {})
    if path_id in {"list_patents_by_inventor", "count_patents_by_inventor"} and _text(params.get("inventor_name")):
        constraints.append(PatentGraphConstraint(field="person.inventor", operator="eq", value=_text(params.get("inventor_name"))))
    elif path_id == "list_patents_by_agency" and _text(params.get("agency_name")):
        constraints.append(PatentGraphConstraint(field="organization.agency", operator="eq", value=_text(params.get("agency_name"))))
    elif path_id == "list_patents_by_ipc_subclass" and _text(params.get("ipc_subclass")):
        constraints.append(PatentGraphConstraint(field="ipc.subclass", operator="eq", value=_text(params.get("ipc_subclass"))))
    elif path_id == "count_patents_by_ipc" and _text(params.get("ipc_code")):
        constraints.append(PatentGraphConstraint(field="ipc.code", operator="eq", value=_text(params.get("ipc_code"))))
    elif path_id == "count_patents_by_applicant" and _text(params.get("organization_name")):
        constraints.append(
            PatentGraphConstraint(field="organization.applicant", operator="eq", value=_text(params.get("organization_name")))
        )
    elif path_id in {"list_patent_atmospheres", "list_patent_embodiment_insights"} and _text(params.get("patent_id")):
        constraints.append(PatentGraphConstraint(field="patent.id", operator="eq", value=_text(params.get("patent_id"))))
    return tuple(constraints)


def canonicalize_patent_graph_rows(
    *,
    plan: PatentGraphQueryPlanV2,
    rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
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
    direct_answerable = bool(normalized_rows) and _has_usable_rows(normalized_rows) and (
        (plan.strategy == "template" and bool(plan.legacy_template_id or plan.legacy_template_plan))
        or (plan.strategy == "parametric" and primary_path_id in _SAFE_DIRECT_PARAMETRIC_PATHS)
    )

    diagnostics = dict(plan.diagnostics or {})
    diagnostics["row_count"] = len(normalized_rows)
    if primary_path_id:
        diagnostics["path_id"] = primary_path_id

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
            "path_id": primary_path_id,
        },
        direct_answerable=direct_answerable,
        constraints_for_rag=_constraints_for_plan(plan),
        confidence=1.0 if direct_answerable else (0.7 if normalized_rows else 0.0),
        diagnostics=diagnostics,
    )
