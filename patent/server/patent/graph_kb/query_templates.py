from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.patent.graph_kb.slots import PatentGraphQuestionSlots


@dataclass(frozen=True)
class PatentGraphQueryTemplate:
    template_id: str
    cypher: str
    required_params: tuple[str, ...]
    optional_params: tuple[str, ...] = ()
    expected_columns: tuple[str, ...] = ()
    direct_answer_eligible: bool = False
    route_family: str = "precise"
    result_cap: int = 20


def _cypher(value: str) -> str:
    return " ".join(str(value or "").split())


def _template(
    template_id: str,
    cypher: str,
    required_params: tuple[str, ...],
    expected_columns: tuple[str, ...],
    *,
    direct: bool = False,
    route_family: str = "precise",
    result_cap: int = 20,
) -> PatentGraphQueryTemplate:
    return PatentGraphQueryTemplate(
        template_id=template_id,
        cypher=_cypher(cypher),
        required_params=required_params,
        expected_columns=expected_columns,
        direct_answer_eligible=direct,
        route_family=route_family,
        result_cap=result_cap,
    )


_PATENT_PROFILE_COLUMNS = (
    "abstract",
    "application_date",
    "publication_date",
    "legal_status",
    "applicants",
    "inventors",
    "ipc_codes",
    "material_roles",
    "process_steps",
    "problems",
    "solutions",
    "inventive_points",
    "performance_facts",
    "measurements",
)


def _listing_columns(matched_column: str) -> tuple[str, ...]:
    return ("patent_id", "title", matched_column, *_PATENT_PROFILE_COLUMNS, "stub")


def _listing_profile_return(matched_column: str) -> str:
    return f"""
        OPTIONAL MATCH (p)-[:HAS_APPLICANT]->(profile_applicant:Organization)
        WITH p, matched_values, collect(DISTINCT profile_applicant.name)[0..3] AS applicants
        OPTIONAL MATCH (p)-[:HAS_INVENTOR]->(profile_inventor:Person)
        WITH p, matched_values, applicants, collect(DISTINCT profile_inventor.name)[0..3] AS inventors
        OPTIONAL MATCH (p)-[:CLASSIFIED_AS]->(profile_ipc:IPC)
        WITH p, matched_values, applicants, inventors, collect(DISTINCT profile_ipc.code)[0..5] AS ipc_codes
        OPTIONAL MATCH (p)-[:HAS_MATERIAL_ROLE]->(profile_role:MaterialRole)
        OPTIONAL MATCH (profile_role)-[:OPTION_INCLUDES]->(profile_material:Material)
        WITH p, matched_values, applicants, inventors, ipc_codes,
             collect(DISTINCT CASE
                 WHEN profile_role IS NULL AND profile_material IS NULL THEN NULL
                 WHEN profile_material.name IS NULL THEN coalesce(profile_role.role, profile_role.type, '')
                 ELSE coalesce(profile_role.role, profile_role.type, '') + ': ' + profile_material.name
             END)[0..5] AS material_roles
        OPTIONAL MATCH (p)-[:HAS_PROCESS_STEP]->(profile_step:ProcessStep)
        OPTIONAL MATCH (profile_step)-[:INSTANCE_OF]->(profile_template:StepTemplate)
        WITH p, matched_values, applicants, inventors, ipc_codes, material_roles,
             collect(DISTINCT CASE
                 WHEN profile_step IS NULL AND profile_template IS NULL THEN NULL
                 ELSE coalesce(profile_step.name, profile_step.operation, profile_template.label, profile_template.name)
             END)[0..5] AS process_steps
        OPTIONAL MATCH (p)-[:ADDRESSES]->(profile_problem:TechnicalProblem)
        WITH p, matched_values, applicants, inventors, ipc_codes, material_roles, process_steps,
             collect(DISTINCT profile_problem.text)[0..2] AS problems
        OPTIONAL MATCH (p)-[:PROPOSES]->(profile_solution:TechnicalSolution)
        WITH p, matched_values, applicants, inventors, ipc_codes, material_roles, process_steps, problems,
             collect(DISTINCT profile_solution.text)[0..2] AS solutions
        OPTIONAL MATCH (p)-[:HAS_INVENTIVE_POINT]->(profile_point:InventivePoint)
        WITH p, matched_values, applicants, inventors, ipc_codes, material_roles, process_steps, problems, solutions,
             collect(DISTINCT profile_point.text)[0..3] AS inventive_points
        OPTIONAL MATCH (p)-[:HAS_PERFORMANCE_FACT]->(profile_fact:PerformanceFact)
        WITH p, matched_values, applicants, inventors, ipc_codes, material_roles, process_steps, problems, solutions, inventive_points,
             collect(DISTINCT profile_fact.text)[0..3] AS performance_facts
        OPTIONAL MATCH (p)-[:HAS_EXPERIMENT_TABLE]->(:ExperimentTable)-[:HAS_ROW]->(:TableRow)-[:HAS_MEASUREMENT]->(profile_measurement:Measurement)
        RETURN p.patent_id AS patent_id, p.title AS title, p.abstract AS abstract,
               p.application_date AS application_date, p.publication_date AS publication_date,
               p.legal_status AS legal_status, matched_values AS {matched_column},
               applicants, inventors, ipc_codes, material_roles, process_steps,
               problems, solutions, inventive_points, performance_facts,
               collect(DISTINCT CASE
                   WHEN profile_measurement IS NULL THEN NULL
                   ELSE coalesce(profile_measurement.metric_key, '') + ':' + coalesce(profile_measurement.value_raw, '') + coalesce(profile_measurement.unit_hint, '')
               END)[0..3] AS measurements,
               p.stub AS stub
        LIMIT $limit
    """


_TEMPLATES: tuple[PatentGraphQueryTemplate, ...] = (
    _template(
        "lookup_patent_by_id",
        """
        MATCH (p:Patent {patent_id: $patent_id})
        OPTIONAL MATCH (p)-[:HAS_APPLICANT]->(applicant:Organization)
        OPTIONAL MATCH (p)-[:HAS_INVENTOR]->(inventor:Person)
        OPTIONAL MATCH (p)-[:CLASSIFIED_AS]->(ipc:IPC)
        OPTIONAL MATCH (p)-[:ADDRESSES]->(problem:TechnicalProblem)
        OPTIONAL MATCH (p)-[:PROPOSES]->(solution:TechnicalSolution)
        OPTIONAL MATCH (p)-[:HAS_INVENTIVE_POINT]->(point:InventivePoint)
        OPTIONAL MATCH (p)-[:HAS_PERFORMANCE_FACT]->(fact:PerformanceFact)
        OPTIONAL MATCH (p)-[:HAS_EXPERIMENT_TABLE]->(:ExperimentTable)-[:HAS_ROW]->(:TableRow)-[:HAS_MEASUREMENT]->(measurement:Measurement)
        RETURN p.patent_id AS patent_id, p.title AS title, p.abstract AS abstract,
               p.application_date AS application_date, p.publication_date AS publication_date,
               p.legal_status AS legal_status, collect(DISTINCT applicant.name)[0..5] AS applicants,
               collect(DISTINCT inventor.name)[0..5] AS inventors, collect(DISTINCT ipc.code)[0..5] AS ipc_codes,
               collect(DISTINCT problem.text)[0..2] AS problems,
               collect(DISTINCT solution.text)[0..2] AS solutions,
               collect(DISTINCT point.text)[0..3] AS inventive_points,
               collect(DISTINCT fact.text)[0..3] AS performance_facts,
               collect(DISTINCT CASE
                   WHEN measurement IS NULL THEN NULL
                   ELSE coalesce(measurement.metric_key, '') + ':' + coalesce(measurement.value_raw, '') + coalesce(measurement.unit_hint, '')
               END)[0..3] AS measurements,
               p.stub AS stub
        LIMIT $limit
        """,
        ("patent_id",),
        (
            "patent_id",
            "title",
            "abstract",
            "applicants",
            "inventors",
            "ipc_codes",
            "problems",
            "solutions",
            "inventive_points",
            "performance_facts",
            "measurements",
            "stub",
        ),
        direct=True,
    ),
    _template(
        "list_patent_process_steps",
        """
        MATCH (p:Patent {patent_id: $patent_id})-[:HAS_PROCESS_STEP]->(step:ProcessStep)
        OPTIONAL MATCH (step)-[:INSTANCE_OF]->(template:StepTemplate)
        RETURN p.patent_id AS patent_id, p.title AS title,
               step.name AS step_name, step.operation AS operation,
               coalesce(step.position, step.order, step.sequence) AS step_order,
               template.label AS step_template, template.name AS step_template_name,
               p.stub AS stub
        ORDER BY coalesce(step.position, step.order, step.sequence, 9999), step.name
        LIMIT $limit
        """,
        ("patent_id",),
        ("patent_id", "title", "step_name", "operation", "step_order", "step_template", "stub"),
        direct=True,
    ),
    _template(
        "list_patent_material_roles",
        """
        MATCH (p:Patent {patent_id: $patent_id})-[:HAS_MATERIAL_ROLE]->(role:MaterialRole)
        OPTIONAL MATCH (role)-[:OPTION_INCLUDES]->(material:Material)
        RETURN p.patent_id AS patent_id, p.title AS title,
               role.role AS material_role, role.type AS material_role_type, role.ratio AS material_ratio,
               collect(DISTINCT material.name)[0..10] AS material_options,
               collect(DISTINCT material.material_type)[0..10] AS material_types,
               p.stub AS stub
        ORDER BY material_role, material_role_type
        LIMIT $limit
        """,
        ("patent_id",),
        ("patent_id", "title", "material_role", "material_options", "stub"),
        direct=True,
    ),
    _template(
        "list_patent_experiment_tables",
        """
        MATCH (p:Patent {patent_id: $patent_id})-[:HAS_EXPERIMENT_TABLE]->(table:ExperimentTable)
        OPTIONAL MATCH (table)-[:HAS_ROW]->(row:TableRow)
        OPTIONAL MATCH (row)-[:HAS_MEASUREMENT]->(measurement:Measurement)
        RETURN p.patent_id AS patent_id, p.title AS title,
               table.table_title AS table_title, row.sample_label AS row_label,
               measurement.metric_key AS metric_key, measurement.value_raw AS value_raw,
               measurement.unit_hint AS unit_hint, p.stub AS stub
        LIMIT $limit
        """,
        ("patent_id",),
        ("patent_id", "title", "table_title", "row_label", "metric_key", "value_raw", "stub"),
        direct=True,
        result_cap=50,
    ),
    _template(
        "list_patent_problem_solution",
        """
        MATCH (p:Patent {patent_id: $patent_id})
        OPTIONAL MATCH (p)-[:ADDRESSES]->(problem:TechnicalProblem)
        OPTIONAL MATCH (p)-[:PROPOSES]->(solution:TechnicalSolution)
        OPTIONAL MATCH (p)-[:HAS_APPLICATION_SCENARIO]->(scenario:ApplicationScenario)
        RETURN p.patent_id AS patent_id, p.title AS title,
               collect(DISTINCT problem.text)[0..10] AS problems,
               collect(DISTINCT solution.text)[0..10] AS solutions,
               collect(DISTINCT scenario.text)[0..10] AS scenarios,
               p.stub AS stub
        LIMIT $limit
        """,
        ("patent_id",),
        ("patent_id", "title", "problems", "solutions", "scenarios", "stub"),
        direct=True,
    ),
    _template(
        "list_patent_inventive_scope",
        """
        MATCH (p:Patent {patent_id: $patent_id})
        OPTIONAL MATCH (p)-[:HAS_INVENTIVE_POINT]->(point:InventivePoint)
        OPTIONAL MATCH (p)-[:HAS_PERFORMANCE_FACT]->(fact:PerformanceFact)
        OPTIONAL MATCH (p)-[:PROTECTION_INCLUDES]->(scope:ProtectionScope)
        OPTIONAL MATCH (p)-[:CLAIM_INCLUDES_STEP]->(claim:ClaimStepLabel)
        RETURN p.patent_id AS patent_id, p.title AS title,
               collect(DISTINCT point.text)[0..10] AS inventive_points,
               collect(DISTINCT fact.text)[0..10] AS performance_facts,
               collect(DISTINCT scope.text)[0..10] AS protection_scopes,
               collect(DISTINCT claim.name)[0..10] AS claim_step_labels,
               p.stub AS stub
        LIMIT $limit
        """,
        ("patent_id",),
        ("patent_id", "title", "inventive_points", "performance_facts", "protection_scopes", "claim_step_labels", "stub"),
        direct=True,
    ),
    _template(
        "list_patent_citations",
        """
        MATCH (p:Patent {patent_id: $patent_id})-[:CITES_PATENT]->(cited:Patent)
        RETURN p.patent_id AS patent_id, p.title AS title,
               cited.patent_id AS cited_patent_id, cited.title AS cited_title,
               cited.stub AS cited_stub, p.stub AS stub
        LIMIT $limit
        """,
        ("patent_id",),
        ("patent_id", "title", "cited_patent_id", "cited_title", "stub"),
        direct=True,
    ),
    _template(
        "list_patent_atmospheres",
        """
        MATCH (p:Patent {patent_id: $patent_id})-[:USES_ATMOSPHERE]->(atmosphere:Atmosphere)
        RETURN p.patent_id AS patent_id, p.title AS title,
               atmosphere.options AS atmosphere_options,
               atmosphere.preferred AS atmosphere_preferred,
               p.stub AS stub
        LIMIT $limit
        """,
        ("patent_id",),
        ("patent_id", "title", "atmosphere_options", "atmosphere_preferred", "stub"),
        direct=True,
    ),
    _template(
        "list_patent_embodiment_insights",
        """
        MATCH (p:Patent {patent_id: $patent_id})-[:HAS_EMBODIMENT_INSIGHT]->(insight:EmbodimentInsight)
        RETURN p.patent_id AS patent_id, p.title AS title,
               insight.conclusion AS insight_conclusion,
               insight.insight_type AS insight_type,
               p.stub AS stub
        LIMIT $limit
        """,
        ("patent_id",),
        ("patent_id", "title", "insight_conclusion", "insight_type", "stub"),
        direct=True,
    ),
    _template(
        "list_patents_by_applicant",
        """
        MATCH (p:Patent)-[:HAS_APPLICANT]->(org:Organization {name: $applicant_name})
        WITH p, collect(DISTINCT org.name)[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("applicant_name"),
        ("applicant_name",),
        _listing_columns("applicant_name"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "count_patents_by_applicant",
        "MATCH (p:Patent)-[:HAS_APPLICANT]->(org:Organization {name: $applicant_name}) RETURN org.name AS applicant_name, count(DISTINCT p) AS patent_count LIMIT 1",
        ("applicant_name",),
        ("applicant_name", "patent_count"),
        direct=True,
    ),
    _template(
        "list_patents_by_inventor",
        """
        MATCH (p:Patent)-[:HAS_INVENTOR]->(person:Person {name: $inventor_name})
        WITH p, collect(DISTINCT person.name)[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("inventor_name"),
        ("inventor_name",),
        _listing_columns("inventor_name"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "count_patents_by_inventor",
        "MATCH (p:Patent)-[:HAS_INVENTOR]->(person:Person {name: $inventor_name}) RETURN person.name AS inventor_name, count(DISTINCT p) AS patent_count LIMIT 1",
        ("inventor_name",),
        ("inventor_name", "patent_count"),
        direct=True,
    ),
    _template(
        "list_patents_by_agency",
        """
        MATCH (p:Patent)-[:HAS_AGENCY]->(agency:Organization {name: $agency_name})
        WITH p, collect(DISTINCT agency.name)[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("agency_name"),
        ("agency_name",),
        _listing_columns("agency_name"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "count_patents_by_agency",
        "MATCH (p:Patent)-[:HAS_AGENCY]->(agency:Organization {name: $agency_name}) RETURN agency.name AS agency_name, count(DISTINCT p) AS patent_count LIMIT 1",
        ("agency_name",),
        ("agency_name", "patent_count"),
        direct=True,
    ),
    _template(
        "list_patents_by_ipc_prefix",
        """
        MATCH (p:Patent)-[:IN_IPC_SUBCLASS]->(sub:IPCPrefix)
        WHERE sub.subclass = $ipc_prefix
        WITH p, collect(DISTINCT sub.subclass)[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("ipc_prefix"),
        ("ipc_prefix",),
        _listing_columns("ipc_prefix"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "count_patents_by_ipc_prefix",
        "MATCH (p:Patent)-[:IN_IPC_SUBCLASS]->(sub:IPCPrefix) WHERE sub.subclass = $ipc_prefix RETURN sub.subclass AS ipc_prefix, count(DISTINCT p) AS patent_count LIMIT 1",
        ("ipc_prefix",),
        ("ipc_prefix", "patent_count"),
        direct=True,
    ),
    _template(
        "list_patents_by_ipc_code_prefix",
        """
        MATCH (p:Patent)-[:CLASSIFIED_AS]->(ipc:IPC)
        WHERE ipc.code STARTS WITH $ipc_code_prefix
        WITH p, collect(DISTINCT ipc.code)[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("ipc_code"),
        ("ipc_code_prefix",),
        _listing_columns("ipc_code"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "count_patents_by_ipc_code_prefix",
        "MATCH (p:Patent)-[:CLASSIFIED_AS]->(ipc:IPC) WHERE ipc.code STARTS WITH $ipc_code_prefix RETURN $ipc_code_prefix AS ipc_code_prefix, count(DISTINCT p) AS patent_count LIMIT 1",
        ("ipc_code_prefix",),
        ("ipc_code_prefix", "patent_count"),
        direct=True,
    ),
    _template(
        "list_patents_by_ipc_full_code",
        """
        MATCH (p:Patent)-[:CLASSIFIED_AS]->(ipc:IPC)
        WHERE ipc.code = $ipc_full_code
        WITH p, collect(DISTINCT ipc.code)[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("ipc_code"),
        ("ipc_full_code",),
        _listing_columns("ipc_code"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "count_patents_by_ipc_full_code",
        "MATCH (p:Patent)-[:CLASSIFIED_AS]->(ipc:IPC) WHERE ipc.code = $ipc_full_code RETURN ipc.code AS ipc_full_code, count(DISTINCT p) AS patent_count LIMIT 1",
        ("ipc_full_code",),
        ("ipc_full_code", "patent_count"),
        direct=True,
    ),
    _template(
        "compare_patents_process_steps",
        "MATCH (p:Patent)-[:HAS_PROCESS_STEP]->(step:ProcessStep) WHERE p.patent_id IN $patent_ids OPTIONAL MATCH (step)-[:INSTANCE_OF]->(template:StepTemplate) RETURN p.patent_id AS patent_id, p.title AS title, step.name AS step_name, step.operation AS operation, coalesce(step.position, step.order, step.sequence) AS step_order, template.label AS step_template, p.stub AS stub ORDER BY patent_id, coalesce(step.position, step.order, step.sequence, 9999) LIMIT $limit",
        ("patent_ids",),
        ("patent_id", "title", "step_name", "operation", "step_template", "stub"),
        route_family="hybrid",
    ),
    _template(
        "compare_patents_material_roles",
        "MATCH (p:Patent)-[:HAS_MATERIAL_ROLE]->(role:MaterialRole) WHERE p.patent_id IN $patent_ids OPTIONAL MATCH (role)-[:OPTION_INCLUDES]->(material:Material) RETURN p.patent_id AS patent_id, p.title AS title, role.role AS material_role, collect(DISTINCT material.name)[0..10] AS material_options, p.stub AS stub LIMIT $limit",
        ("patent_ids",),
        ("patent_id", "title", "material_role", "material_options", "stub"),
        route_family="hybrid",
    ),
    _template(
        "compare_patents_problem_solution",
        "MATCH (p:Patent) WHERE p.patent_id IN $patent_ids OPTIONAL MATCH (p)-[:ADDRESSES]->(problem:TechnicalProblem) OPTIONAL MATCH (p)-[:PROPOSES]->(solution:TechnicalSolution) RETURN p.patent_id AS patent_id, p.title AS title, collect(DISTINCT problem.text)[0..10] AS problems, collect(DISTINCT solution.text)[0..10] AS solutions, p.stub AS stub LIMIT $limit",
        ("patent_ids",),
        ("patent_id", "title", "problems", "solutions", "stub"),
        route_family="hybrid",
    ),
    _template(
        "compare_patents_performance_facts",
        "MATCH (p:Patent) WHERE p.patent_id IN $patent_ids OPTIONAL MATCH (p)-[:HAS_PERFORMANCE_FACT]->(fact:PerformanceFact) OPTIONAL MATCH (p)-[:HAS_EXPERIMENT_TABLE]->(:ExperimentTable)-[:HAS_ROW]->(:TableRow)-[:HAS_MEASUREMENT]->(measurement:Measurement) RETURN p.patent_id AS patent_id, p.title AS title, collect(DISTINCT fact.text)[0..10] AS performance_facts, collect(DISTINCT measurement.metric_key + ':' + measurement.value_raw)[0..10] AS measurements, p.stub AS stub LIMIT $limit",
        ("patent_ids",),
        ("patent_id", "title", "performance_facts", "measurements", "stub"),
        route_family="hybrid",
    ),
    _template(
        "compare_patents_claim_scope",
        "MATCH (p:Patent) WHERE p.patent_id IN $patent_ids OPTIONAL MATCH (p)-[:PROTECTION_INCLUDES]->(scope:ProtectionScope) OPTIONAL MATCH (p)-[:CLAIM_INCLUDES_STEP]->(claim:ClaimStepLabel) RETURN p.patent_id AS patent_id, p.title AS title, collect(DISTINCT scope.text)[0..10] AS protection_scopes, collect(DISTINCT claim.name)[0..10] AS claim_step_labels, p.stub AS stub LIMIT $limit",
        ("patent_ids",),
        ("patent_id", "title", "protection_scopes", "claim_step_labels", "stub"),
        route_family="hybrid",
    ),
    _template(
        "list_patents_by_material",
        """
        MATCH (p:Patent)-[:HAS_MATERIAL_ROLE]->(:MaterialRole)-[:OPTION_INCLUDES]->(material:Material)
        WHERE toLower(material.name) CONTAINS toLower($material_term)
        WITH p, collect(DISTINCT material.name)[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("material_name"),
        ("material_term",),
        _listing_columns("material_name"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "list_patents_by_material_role",
        """
        MATCH (p:Patent)-[:HAS_MATERIAL_ROLE]->(role:MaterialRole)
        WHERE toLower(coalesce(role.role, role.type, '')) CONTAINS toLower($material_role_term)
        WITH p, collect(DISTINCT coalesce(role.role, role.type, ''))[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("material_role"),
        ("material_role_term",),
        _listing_columns("material_role"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "list_patents_by_process_term",
        """
        MATCH (p:Patent)-[:HAS_PROCESS_STEP]->(step:ProcessStep)
        OPTIONAL MATCH (step)-[:INSTANCE_OF]->(template:StepTemplate)
        WHERE toLower(coalesce(step.name, step.operation, template.label, template.name, '')) CONTAINS toLower($process_term)
        WITH p, collect(DISTINCT coalesce(step.name, step.operation, template.label, template.name, ''))[0..3] AS matched_values
        LIMIT $limit
        """
        + _listing_profile_return("step_name"),
        ("process_term",),
        _listing_columns("step_name"),
        direct=True,
        result_cap=100,
    ),
    _template(
        "performance_by_process_term",
        "MATCH (p:Patent)-[:HAS_PROCESS_STEP]->(step:ProcessStep) OPTIONAL MATCH (step)-[:INSTANCE_OF]->(template:StepTemplate) OPTIONAL MATCH (p)-[:HAS_PERFORMANCE_FACT]->(fact:PerformanceFact) WHERE toLower(coalesce(step.name, step.operation, template.label, template.name, '')) CONTAINS toLower($process_term) RETURN p.patent_id AS patent_id, p.title AS title, step.name AS step_name, template.label AS step_template, collect(DISTINCT fact.text)[0..10] AS performance_facts, p.stub AS stub LIMIT $limit",
        ("process_term",),
        ("patent_id", "title", "step_name", "step_template", "performance_facts", "stub"),
        route_family="hybrid",
    ),
    _template(
        "performance_by_material_term",
        "MATCH (p:Patent)-[:HAS_MATERIAL_ROLE]->(:MaterialRole)-[:OPTION_INCLUDES]->(material:Material) OPTIONAL MATCH (p)-[:HAS_PERFORMANCE_FACT]->(fact:PerformanceFact) WHERE toLower(material.name) CONTAINS toLower($material_term) RETURN p.patent_id AS patent_id, p.title AS title, material.name AS material_name, collect(DISTINCT fact.text)[0..10] AS performance_facts, p.stub AS stub LIMIT $limit",
        ("material_term",),
        ("patent_id", "title", "material_name", "performance_facts", "stub"),
        route_family="hybrid",
    ),
    _template(
        "rank_materials_by_frequency",
        "MATCH (p:Patent)-[:HAS_MATERIAL_ROLE]->(:MaterialRole)-[:OPTION_INCLUDES]->(material:Material) RETURN material.name AS material_name, count(DISTINCT p) AS patent_count, collect(DISTINCT p.patent_id)[0..5] AS sample_patent_ids LIMIT $limit",
        (),
        ("material_name", "patent_count", "sample_patent_ids"),
        direct=True,
        result_cap=50,
    ),
    _template(
        "rank_processes_by_frequency",
        "MATCH (p:Patent)-[:HAS_PROCESS_STEP]->(step:ProcessStep) OPTIONAL MATCH (step)-[:INSTANCE_OF]->(template:StepTemplate) WITH coalesce(template.label, template.name, step.name) AS process_name, p WHERE process_name IS NOT NULL RETURN process_name AS process_name, count(DISTINCT p) AS patent_count, collect(DISTINCT p.patent_id)[0..5] AS sample_patent_ids LIMIT $limit",
        (),
        ("process_name", "patent_count", "sample_patent_ids"),
        direct=True,
        result_cap=50,
    ),
)

_TEMPLATE_BY_ID = {item.template_id: item for item in _TEMPLATES}


def list_patent_query_templates() -> tuple[PatentGraphQueryTemplate, ...]:
    return _TEMPLATES


def get_patent_query_template(template_id: str) -> PatentGraphQueryTemplate | None:
    return _TEMPLATE_BY_ID.get(str(template_id or "").strip())


def _limit(template: PatentGraphQueryTemplate, value: int | None) -> int:
    try:
        parsed = int(value or template.result_cap)
    except (TypeError, ValueError):
        parsed = template.result_cap
    return max(1, min(parsed, int(template.result_cap or 20)))


def build_patent_template_candidate(template_id: str, params: dict[str, Any], *, limit: int) -> dict[str, Any] | None:
    template = get_patent_query_template(template_id)
    if template is None:
        return None
    resolved_params = {key: value for key, value in dict(params or {}).items() if value not in (None, "", [], (), {})}
    missing = [key for key in template.required_params if key not in resolved_params]
    if missing:
        return None
    resolved_params["limit"] = _limit(template, limit)
    return {
        "path_id": template.template_id,
        "template_id": template.template_id,
        "cypher": template.cypher,
        "params": resolved_params,
        "direct_answer_eligible": template.direct_answer_eligible,
        "expected_columns": template.expected_columns,
        "route_family": template.route_family,
        "result_cap": template.result_cap,
    }


def _append(candidates: list[dict[str, Any]], template_id: str, params: dict[str, Any], *, limit: int) -> None:
    candidate = build_patent_template_candidate(template_id, params, limit=limit)
    if candidate is not None:
        candidates.append(candidate)


def build_patent_template_candidates(slots: PatentGraphQuestionSlots, *, limit: int = 20) -> tuple[dict[str, Any], ...]:
    if slots.has_doi:
        return ()
    candidates: list[dict[str, Any]] = []

    if len(slots.patent_ids) >= 2 and slots.asks_compare:
        params = {"patent_ids": list(slots.patent_ids)}
        if slots.asks_process:
            _append(candidates, "compare_patents_process_steps", params, limit=limit)
        if slots.asks_materials:
            _append(candidates, "compare_patents_material_roles", params, limit=limit)
        if slots.asks_problem_solution:
            _append(candidates, "compare_patents_problem_solution", params, limit=limit)
        if slots.metric_terms or slots.asks_experiment:
            _append(candidates, "compare_patents_performance_facts", params, limit=limit)
        if slots.asks_inventive_scope:
            _append(candidates, "compare_patents_claim_scope", params, limit=limit)
        if not candidates:
            _append(candidates, "compare_patents_process_steps", params, limit=limit)
        return tuple(candidates)

    if len(slots.patent_ids) == 1:
        params = {"patent_id": slots.patent_ids[0]}
        if slots.asks_atmosphere:
            _append(candidates, "list_patent_atmospheres", params, limit=limit)
        if slots.asks_embodiment:
            _append(candidates, "list_patent_embodiment_insights", params, limit=limit)
        if slots.asks_process:
            _append(candidates, "list_patent_process_steps", params, limit=limit)
        if slots.asks_materials:
            _append(candidates, "list_patent_material_roles", params, limit=limit)
        if slots.asks_experiment or slots.metric_terms:
            _append(candidates, "list_patent_experiment_tables", params, limit=limit)
        if slots.asks_problem_solution:
            _append(candidates, "list_patent_problem_solution", params, limit=limit)
        if slots.asks_inventive_scope:
            _append(candidates, "list_patent_inventive_scope", params, limit=limit)
        if slots.asks_citation:
            _append(candidates, "list_patent_citations", params, limit=limit)
        _append(candidates, "lookup_patent_by_id", params, limit=limit)

    if slots.applicant_names:
        key = "count_patents_by_applicant" if slots.asks_count else "list_patents_by_applicant"
        _append(candidates, key, {"applicant_name": slots.applicant_names[0]}, limit=limit)
    if slots.inventor_names:
        key = "count_patents_by_inventor" if slots.asks_count else "list_patents_by_inventor"
        _append(candidates, key, {"inventor_name": slots.inventor_names[0]}, limit=limit)
    if slots.agency_names:
        key = "count_patents_by_agency" if slots.asks_count else "list_patents_by_agency"
        _append(candidates, key, {"agency_name": slots.agency_names[0]}, limit=limit)

    if slots.ipc_full_codes:
        key = "count_patents_by_ipc_full_code" if slots.asks_count else "list_patents_by_ipc_full_code"
        _append(candidates, key, {"ipc_full_code": slots.ipc_full_codes[0]}, limit=limit)
    if slots.ipc_code_prefixes:
        key = "count_patents_by_ipc_code_prefix" if slots.asks_count else "list_patents_by_ipc_code_prefix"
        _append(candidates, key, {"ipc_code_prefix": slots.ipc_code_prefixes[0]}, limit=limit)
    if slots.ipc_prefixes:
        key = "count_patents_by_ipc_prefix" if slots.asks_count else "list_patents_by_ipc_prefix"
        _append(candidates, key, {"ipc_prefix": slots.ipc_prefixes[0]}, limit=limit)

    if slots.asks_rank and slots.asks_materials:
        _append(candidates, "rank_materials_by_frequency", {}, limit=limit)
    if slots.asks_rank and slots.asks_process:
        _append(candidates, "rank_processes_by_frequency", {}, limit=limit)

    if slots.material_role_terms:
        _append(candidates, "list_patents_by_material_role", {"material_role_term": slots.material_role_terms[0]}, limit=limit)
    if slots.material_terms:
        key = "performance_by_material_term" if slots.asks_why_how or slots.asks_trend_landscape else "list_patents_by_material"
        _append(candidates, key, {"material_term": slots.material_terms[0]}, limit=limit)
    if slots.process_terms:
        key = "performance_by_process_term" if slots.asks_why_how or slots.asks_trend_landscape else "list_patents_by_process_term"
        _append(candidates, key, {"process_term": slots.process_terms[0]}, limit=limit)

    return tuple(candidates)
