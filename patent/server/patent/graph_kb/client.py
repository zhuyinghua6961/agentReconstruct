from __future__ import annotations

import re
from typing import Any

from server.patent.graph_kb.models import PatentGraphKbQueryPlan


_DOI_PATTERN = re.compile(r"10\.\d+/[A-Za-z0-9._\-()/]+", re.IGNORECASE)
_PATENT_ID_PATTERN = re.compile(r"\b((?:CN|US|WO|JP|EP|KR)[A-Z0-9]{6,})\b", re.IGNORECASE)
_IPC_PATTERN = re.compile(r"\b([A-H][0-9]{2}[A-Z][0-9]+/[0-9A-Z]+)\b", re.IGNORECASE)
_APPLICANT_LISTING_PATTERN = re.compile(r"^(?P<name>[\u4e00-\u9fffA-Za-z0-9()（）·\-.]+?)有哪些专利$")


def _normalize_question(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().rstrip("？?。.!！")


def _extract_patent_ids(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    patent_ids: list[str] = []
    for item in _PATENT_ID_PATTERN.findall(text):
        normalized = str(item or "").upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        patent_ids.append(normalized)
    return tuple(patent_ids)


def plan_patent_graph_query(question: str) -> PatentGraphKbQueryPlan | None:
    text = _normalize_question(question)
    if not text or _DOI_PATTERN.search(text):
        return None

    patent_ids = _extract_patent_ids(text)
    if len(patent_ids) > 1:
        return None
    if patent_ids:
        patent_id = patent_ids[0]
        if any(hint in text for hint in ("工艺步骤", "步骤", "工艺")):
            return PatentGraphKbQueryPlan("list_patent_process_steps", {"patent_id": patent_id})
        if any(hint in text for hint in ("原料", "材料角色")):
            return PatentGraphKbQueryPlan("list_patent_material_roles", {"patent_id": patent_id})
        if any(hint in text for hint in ("实验表格", "性能数据", "实验数据", "测量")):
            return PatentGraphKbQueryPlan("list_patent_experiment_tables", {"patent_id": patent_id})
        if any(hint in text for hint in ("技术问题", "方案", "应用场景")):
            return PatentGraphKbQueryPlan("list_patent_problem_solution", {"patent_id": patent_id})
        if any(hint in text for hint in ("发明点", "保护范围", "保护", "性能事实", "claim")):
            return PatentGraphKbQueryPlan("list_patent_inventive_scope", {"patent_id": patent_id})
        if "引用" in text:
            return PatentGraphKbQueryPlan("list_patent_citations", {"patent_id": patent_id})
        return PatentGraphKbQueryPlan("lookup_patent_by_id", {"patent_id": patent_id})

    ipc_match = _IPC_PATTERN.search(text)
    if ipc_match is not None and "专利" in text:
        return PatentGraphKbQueryPlan("list_patents_by_ipc", {"ipc_code": ipc_match.group(1).upper()})

    applicant_match = _APPLICANT_LISTING_PATTERN.fullmatch(text)
    if applicant_match is not None:
        return PatentGraphKbQueryPlan(
            "list_patents_by_applicant",
            {"organization_name": str(applicant_match.group("name") or "").strip()},
        )

    return None


def _cypher_and_params(plan: PatentGraphKbQueryPlan) -> tuple[str, dict[str, Any]]:
    params = dict(plan.params)
    if plan.template_id == "lookup_patent_by_id":
        return (
            "MATCH (p:Patent {patent_id: $patent_id}) "
            "OPTIONAL MATCH (p)-[:CLASSIFIED_AS]->(ipc:IPC) "
            "OPTIONAL MATCH (p)-[:IN_IPC_SUBCLASS]->(sub:IPCPrefix) "
            "OPTIONAL MATCH (p)-[:HAS_APPLICANT]->(applicant:Organization) "
            "OPTIONAL MATCH (p)-[:HAS_AGENCY]->(agency:Organization) "
            "OPTIONAL MATCH (p)-[:HAS_INVENTOR]->(inventor:Person) "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.title AS title, "
            "p.abstract AS abstract, "
            "p.application_date AS application_date, "
            "p.publication_date AS publication_date, "
            "p.ipc_main AS ipc_main, "
            "p.patent_type AS patent_type, "
            "p.legal_status AS legal_status, "
            "p.source_file AS source_file, "
            "p.stub AS stub, "
            "collect(DISTINCT ipc.code)[0..10] AS ipc_codes, "
            "collect(DISTINCT sub.subclass)[0..10] AS ipc_subclasses, "
            "collect(DISTINCT applicant.name)[0..10] AS applicants, "
            "collect(DISTINCT agency.name)[0..5] AS agencies, "
            "collect(DISTINCT inventor.name)[0..10] AS inventors "
            "LIMIT 1",
            params,
        )
    if plan.template_id == "list_patent_process_steps":
        return (
            "MATCH (p:Patent {patent_id: $patent_id})-[:HAS_PROCESS_STEP]->(step:ProcessStep) "
            "OPTIONAL MATCH (step)-[:INSTANCE_OF]->(template:StepTemplate) "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.stub AS stub, "
            "step.`order` AS step_order, "
            "step.name AS step_name, "
            "step.operation AS step_operation, "
            "step.params_json AS step_params_json, "
            "template.label AS step_template "
            "ORDER BY step.`order` ASC "
            "LIMIT 200",
            params,
        )
    if plan.template_id == "list_patent_material_roles":
        return (
            "MATCH (p:Patent {patent_id: $patent_id})-[:HAS_MATERIAL_ROLE]->(role:MaterialRole) "
            "OPTIONAL MATCH (role)-[:OPTION_INCLUDES]->(material:Material) "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.stub AS stub, "
            "role.type AS role_name, "
            "role.role AS role_type, "
            "role.ratio AS role_ratio, "
            "role.note AS role_note, "
            "material.name AS material_name, "
            "material.material_type AS material_type, "
            "material.canonical_key AS material_canonical_key "
            "LIMIT 300",
            params,
        )
    if plan.template_id == "list_patent_experiment_tables":
        return (
            "MATCH (p:Patent {patent_id: $patent_id})-[:HAS_EXPERIMENT_TABLE]->(table:ExperimentTable) "
            "OPTIONAL MATCH (table)-[:HAS_ROW]->(row:TableRow) "
            "OPTIONAL MATCH (row)-[:HAS_MEASUREMENT]->(measurement:Measurement) "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.stub AS stub, "
            "table.table_title AS table_title, "
            "coalesce(row.sample_label, toString(row.row_index)) AS row_label, "
            "measurement.metric_key AS measurement_name, "
            "measurement.value_raw AS measurement_value, "
            "measurement.unit_hint AS measurement_unit, "
            "row.process_note AS measurement_note "
            "ORDER BY table.table_index ASC, row.row_index ASC "
            "LIMIT 500",
            params,
        )
    if plan.template_id == "list_patent_problem_solution":
        return (
            "MATCH (p:Patent {patent_id: $patent_id}) "
            "OPTIONAL MATCH (p)-[:ADDRESSES]->(problem:TechnicalProblem) "
            "OPTIONAL MATCH (p)-[:PROPOSES]->(solution:TechnicalSolution) "
            "OPTIONAL MATCH (p)-[:HAS_APPLICATION_SCENARIO]->(scenario:ApplicationScenario) "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.stub AS stub, "
            "collect(DISTINCT problem.text) AS problem_texts, "
            "collect(DISTINCT solution.text) AS solution_texts, "
            "collect(DISTINCT scenario.text) AS scenario_texts "
            "LIMIT 1",
            params,
        )
    if plan.template_id == "list_patent_inventive_scope":
        return (
            "MATCH (p:Patent {patent_id: $patent_id}) "
            "OPTIONAL MATCH (p)-[:HAS_INVENTIVE_POINT]->(point:InventivePoint) "
            "OPTIONAL MATCH (p)-[:HAS_PERFORMANCE_FACT]->(fact:PerformanceFact) "
            "OPTIONAL MATCH (p)-[:PROTECTION_INCLUDES]->(scope:ProtectionScope) "
            "OPTIONAL MATCH (p)-[:CLAIM_INCLUDES_STEP]->(claim:ClaimStepLabel) "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.stub AS stub, "
            "collect(DISTINCT point.text) AS inventive_point_texts, "
            "collect(DISTINCT point.category) AS inventive_categories, "
            "collect(DISTINCT fact.text) AS performance_fact_texts, "
            "collect(DISTINCT fact.category) AS performance_categories, "
            "collect(DISTINCT scope.text) AS protection_scope_texts, "
            "collect(DISTINCT scope.kind) AS protection_kinds, "
            "collect(DISTINCT claim.name) AS claim_step_labels "
            "LIMIT 1",
            params,
        )
    if plan.template_id == "list_patent_citations":
        return (
            "MATCH (p:Patent {patent_id: $patent_id})-[:CITES_PATENT]->(cited:Patent) "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.stub AS stub, "
            "cited.patent_id AS cited_patent_id, "
            "cited.title AS cited_title, "
            "cited.publication_date AS cited_publication_date, "
            "cited.stub AS cited_stub "
            "LIMIT 200",
            params,
        )
    if plan.template_id == "list_patents_by_ipc":
        return (
            "MATCH (p:Patent) "
            "OPTIONAL MATCH (p)-[:CLASSIFIED_AS]->(ipc:IPC) "
            "OPTIONAL MATCH (p)-[:IN_IPC_SUBCLASS]->(sub:IPCPrefix) "
            "WITH p, collect(DISTINCT ipc.code) + collect(DISTINCT sub.subclass) AS ipc_values "
            "WITH p, [item IN ipc_values WHERE item = $ipc_code][0] AS ipc_match "
            "WHERE ipc_match IS NOT NULL "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.title AS title, "
            "p.application_date AS application_date, "
            "p.publication_date AS publication_date, "
            "ipc_match AS ipc_match, "
            "p.stub AS stub "
            "LIMIT 200",
            params,
        )
    if plan.template_id == "list_patents_by_applicant":
        return (
            "MATCH (p:Patent)-[:HAS_APPLICANT]->(org:Organization {name: $organization_name}) "
            "RETURN "
            "p.patent_id AS patent_id, "
            "p.title AS title, "
            "p.application_date AS application_date, "
            "p.publication_date AS publication_date, "
            "org.name AS applicant_name, "
            "p.stub AS stub "
            "LIMIT 200",
            params,
        )
    raise ValueError(f"unsupported template_id: {plan.template_id}")


def execute_patent_graph_plan(
    plan: PatentGraphKbQueryPlan,
    *,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    if not bool(getattr(neo4j_client, "available", False)):
        return []

    cypher, params = _cypher_and_params(plan)
    query = getattr(neo4j_client, "query", None)
    if not callable(query):
        return []

    try:
        rows = query(cypher, params, timeout_ms=int(timeout_ms or 0))
    except TypeError:
        rows = query(cypher, params)
    normalized = [dict(item) for item in list(rows or []) if isinstance(item, dict)]
    return normalized[: max(1, int(max_rows or 1))]
