from __future__ import annotations

import re
from typing import Any

from app.modules.graph_kb.models import GraphKbQueryPlan

_DOI_PATTERN = re.compile(r"(10\.\d+/[A-Za-z0-9._\-()/]+)", re.IGNORECASE)
_LIST_PATTERN = re.compile(r"^(?:请)?有哪些关于(?P<material>[A-Za-z0-9\u4e00-\u9fff\-+/().]+)的(?:文献|论文)$")
_COUNT_PATTERN = re.compile(r"^(?:请)?(?P<material>[A-Za-z0-9\u4e00-\u9fff\-+/().]+)有多少篇(?:文献|论文)$")
_RAW_MATERIAL_PATTERNS = (
    re.compile(r"^(?:请)?(?:有哪些|哪些)(?:使用|用了|以)(?P<material>[A-Za-z0-9\u4e00-\u9fff\-+/().]+?)(?:作为)?原料的(?:文献|论文)$"),
    re.compile(r"^(?:请)?(?:有哪些|哪些)(?:文献|论文)(?:使用|用了|以)(?P<material>[A-Za-z0-9\u4e00-\u9fff\-+/().]+?)(?:作为)?原料$"),
)
_PROPERTY_FILTER_HINTS = (
    "压实密度",
    "比容量",
    "电压",
    "容量",
    "倍率",
    "循环",
    "大于",
    "小于",
    "高于",
    "低于",
    "超过",
    "最高",
    "最低",
)
_RANKING_PATTERN = re.compile(r"(?:前\s*\d+|排名前|top\s*\d+)", re.IGNORECASE)
_DOI_TESTING_HINTS = ("测试", "实验", "表征")
_DOI_PROCESS_HINTS = ("工艺", "制备", "方法", "流程", "步骤")


def _normalized_question(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().rstrip("？?。.!！")


def _looks_like_property_filter(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).strip()
    return any(hint in normalized for hint in _PROPERTY_FILTER_HINTS) or bool(_RANKING_PATTERN.search(normalized))


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _extract_raw_material_query(text: str) -> str:
    for pattern in _RAW_MATERIAL_PATTERNS:
        match = pattern.fullmatch(text)
        if match is not None:
            return " ".join(str(match.group("material") or "").split()).strip()
    return ""


def plan_graph_kb_query(question: str) -> GraphKbQueryPlan | None:
    text = _normalized_question(question)
    if not text:
        return None

    doi_match = _DOI_PATTERN.search(text)
    if doi_match:
        include_testing = _contains_any(text, _DOI_TESTING_HINTS)
        include_process = _contains_any(text, _DOI_PROCESS_HINTS)
        if include_testing or include_process:
            return GraphKbQueryPlan(
                template_id="expand_doi_context_by_doi",
                params={
                    "doi": doi_match.group(1),
                    "include_testing": include_testing,
                    "include_process": include_process,
                },
            )
        return GraphKbQueryPlan(template_id="lookup_by_doi", params={"doi": doi_match.group(1)})

    raw_material_name = _extract_raw_material_query(text)
    if raw_material_name:
        if _looks_like_property_filter(raw_material_name):
            return None
        return GraphKbQueryPlan(
            template_id="list_by_raw_material",
            params={"material_name": raw_material_name},
        )

    list_match = _LIST_PATTERN.fullmatch(text)
    if list_match:
        material_name = list_match.group("material")
        if _looks_like_property_filter(material_name):
            return None
        return GraphKbQueryPlan(
            template_id="list_by_material",
            params={"material_name": material_name},
        )

    count_match = _COUNT_PATTERN.fullmatch(text)
    if count_match:
        material_name = count_match.group("material")
        if _looks_like_property_filter(material_name):
            return None
        return GraphKbQueryPlan(
            template_id="count_by_filter",
            params={"material_name": material_name},
        )

    return None


def build_legacy_template_query_plan(question: str) -> GraphKbQueryPlan | None:
    return plan_graph_kb_query(question)


def _cypher_and_params(plan: GraphKbQueryPlan) -> tuple[str, dict[str, Any]]:
    params = dict(plan.params)
    if plan.template_id == "lookup_by_doi":
        return (
            "MATCH (d:doi {name: $doi}) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "RETURN d.name AS doi, t.name AS title, collect(DISTINCT rm.name)[0..5] AS raw_materials LIMIT 1",
            params,
        )
    if plan.template_id == "expand_doi_context_by_doi":
        return (
            "MATCH (d:doi {name: $doi}) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "WITH d, head(collect(DISTINCT t.name)) AS title "
            "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "WITH d, title, collect(DISTINCT rm.name)[0..5] AS raw_materials "
            "OPTIONAL MATCH (d)-[:testing]->(:testing)-[:testing]->(tv:testing) "
            "WITH d, title, raw_materials, collect(DISTINCT tv.name)[0..5] AS testing_items "
            "OPTIONAL MATCH (d)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method) "
            "WITH d, title, raw_materials, testing_items, collect(DISTINCT pm.name)[0..3] AS preparation_methods "
            "OPTIONAL MATCH (d)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[r]->(kp) "
            "RETURN d.name AS doi, title, raw_materials, testing_items, preparation_methods, collect(DISTINCT kp.name)[0..5] AS process_parameters LIMIT 1",
            params,
        )
    if plan.template_id == "list_by_material":
        return (
            "MATCH (d:doi) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "OPTIONAL MATCH (s:name)-[:name]->(d) "
            "WITH d, t, collect(DISTINCT rm.name) AS raw_materials, collect(DISTINCT s.name) AS sample_names "
            "WHERE toLower(d.name) CONTAINS toLower($material_name) "
            "OR toLower(coalesce(t.name, '')) CONTAINS toLower($material_name) "
            "OR any(item IN raw_materials WHERE toLower(coalesce(item, '')) CONTAINS toLower($material_name)) "
            "OR any(item IN sample_names WHERE toLower(coalesce(item, '')) CONTAINS toLower($material_name)) "
            "RETURN d.name AS doi, t.name AS title, raw_materials, sample_names LIMIT 50",
            params,
        )
    if plan.template_id == "list_by_raw_material":
        return (
            "MATCH (d:doi) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "WITH d, t, collect(DISTINCT rm.name) AS raw_materials "
            "WITH d, t, [item IN raw_materials WHERE toLower(coalesce(item, '')) CONTAINS toLower($material_name)][0..3] AS matched_raw_materials "
            "WHERE size(matched_raw_materials) > 0 "
            "RETURN d.name AS doi, t.name AS title, matched_raw_materials LIMIT 50",
            params,
        )
    if plan.template_id == "count_by_filter":
        return (
            "MATCH (d:doi) "
            "OPTIONAL MATCH (d)-[:title]->(t:title) "
            "OPTIONAL MATCH (d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials) "
            "OPTIONAL MATCH (s:name)-[:name]->(d) "
            "WITH d, t, collect(DISTINCT rm.name) AS raw_materials, collect(DISTINCT s.name) AS sample_names "
            "WHERE toLower(d.name) CONTAINS toLower($material_name) "
            "OR toLower(coalesce(t.name, '')) CONTAINS toLower($material_name) "
            "OR any(item IN raw_materials WHERE toLower(coalesce(item, '')) CONTAINS toLower($material_name)) "
            "OR any(item IN sample_names WHERE toLower(coalesce(item, '')) CONTAINS toLower($material_name)) "
            "RETURN count(DISTINCT d) AS count",
            params,
        )
    raise ValueError(f"unsupported template_id: {plan.template_id}")


def _is_neo4j_implicit_transaction_fallback(exc: BaseException) -> bool:
    code = str(getattr(exc, "code", "") or "")
    message = str(getattr(exc, "message", "") or str(exc) or "")
    return (
        (
            code in {
                "Neo.DatabaseError.Statement.ExecutionFailed",
                "Neo.DatabaseError.Transaction.TransactionStartFailed",
            }
            and "in an implicit transaction" in message
        )
        or (
            code == "Neo.ClientError.Statement.SemanticError"
            and (
                "in an open transaction is not possible" in message
                or "tried to execute in an explicit transaction" in message
            )
        )
    )


def _is_timeout_error(exc: BaseException) -> bool:
    code = str(getattr(exc, "code", "") or "").lower()
    message = str(getattr(exc, "message", "") or str(exc) or "").lower()
    return "timeout" in code or "timed out" in message or "timeout" in message


def _timeout_error_message(exc: BaseException) -> str:
    return str(getattr(exc, "message", "") or str(exc) or "graph query timed out")


def _sanitize_rows(graph: Any, rows: list[Any]) -> list[Any]:
    if not bool(getattr(graph, "sanitize", False)):
        return rows
    try:
        from langchain_community.graphs.neo4j_graph import value_sanitize
    except Exception:  # pragma: no cover
        return rows
    return [value_sanitize(item) for item in rows]


def _query_graph_with_timeout(*, graph: Any, cypher: str, params: dict[str, Any], timeout_ms: int) -> list[dict[str, Any]] | None:
    driver = getattr(graph, "_driver", None)
    if driver is None or int(timeout_ms or 0) <= 0:
        return None
    try:
        from neo4j import Query
        from neo4j.exceptions import Neo4jError
    except Exception:  # pragma: no cover
        return None

    query = Query(text=cypher, timeout=float(timeout_ms) / 1000.0)
    database = getattr(graph, "_database", None)

    def _normalize(rows: list[Any]) -> list[dict[str, Any]]:
        normalized = []
        for item in _sanitize_rows(graph, rows):
            if isinstance(item, dict):
                normalized.append(dict(item))
            elif hasattr(item, "data"):
                data = item.data()
                if isinstance(data, dict):
                    normalized.append(dict(data))
        return normalized

    try:
        rows, _, _ = driver.execute_query(query, database_=database, parameters_=params)
        return _normalize(list(rows or []))
    except Exception as exc:
        if _is_timeout_error(exc):
            raise TimeoutError(_timeout_error_message(exc)) from exc
        if not isinstance(exc, Neo4jError) or not _is_neo4j_implicit_transaction_fallback(exc):
            raise

    try:
        with driver.session(database=database) as session:
            rows = session.run(query, params)
            return _normalize(list(rows or []))
    except Exception as exc:
        if _is_timeout_error(exc):
            raise TimeoutError(_timeout_error_message(exc)) from exc
        raise


def execute_graph_kb_plan(
    plan: GraphKbQueryPlan,
    *,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 0,
) -> list[dict[str, Any]]:
    graph = getattr(neo4j_client, "graph", None)
    if graph is None or not bool(getattr(neo4j_client, "available", False)):
        return []

    cypher, params = _cypher_and_params(plan)
    rows = _query_graph_with_timeout(graph=graph, cypher=cypher, params=params, timeout_ms=int(timeout_ms or 0))
    if rows is None:
        try:
            if hasattr(graph, "query"):
                rows = graph.query(cypher, params)
            else:
                result = graph.run(cypher, **params)
                rows = result.data() if hasattr(result, "data") else result
        except Exception as exc:
            if _is_timeout_error(exc):
                raise TimeoutError(_timeout_error_message(exc)) from exc
            raise
    normalized = [dict(item) for item in list(rows or []) if isinstance(item, dict)]
    return normalized[: max(1, int(max_rows or 1))]
