from __future__ import annotations

from app.modules.graph_kb.guardrail import inspect_cypher
from app.modules.graph_kb.schema_registry import build_default_schema_registry


def test_guardrail_rejects_write_cypher():
    result = inspect_cypher(cypher="MATCH (n) DELETE n", registry=build_default_schema_registry())

    assert result.verdict == "reject"
    assert "write_clause" in result.issues


def test_guardrail_rejects_disallowed_label():
    result = inspect_cypher(
        cypher="MATCH (d:forbidden) RETURN d",
        registry=build_default_schema_registry(),
    )

    assert result.verdict == "reject"
    assert "label_not_allowed" in result.issues


def test_guardrail_keeps_parameterized_limit_without_appending_second_limit():
    result = inspect_cypher(
        cypher="MATCH (d:doi) RETURN d.name AS doi LIMIT $limit",
        registry=build_default_schema_registry(),
    )

    assert result.verdict == "allow"
    assert result.normalized_cypher.endswith("LIMIT $limit")
    assert "LIMIT $limit LIMIT 20" not in result.normalized_cypher
