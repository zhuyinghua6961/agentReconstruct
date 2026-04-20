from __future__ import annotations

from server.patent.graph_kb.guardrail import inspect_patent_cypher
from server.patent.graph_kb.schema_registry import build_default_patent_schema_registry


def test_guardrail_rejects_write_clauses():
    registry = build_default_patent_schema_registry()

    result = inspect_patent_cypher(
        cypher="MATCH (p:Patent) SET p.title = 'x' RETURN p",
        registry=registry,
    )

    assert result.verdict == "reject"
    assert result.issues == ("write_clause",)


def test_guardrail_rejects_unapproved_labels():
    registry = build_default_patent_schema_registry()

    result = inspect_patent_cypher(
        cypher="MATCH (x:UnknownLabel) RETURN x LIMIT 5",
        registry=registry,
    )

    assert result.verdict == "reject"
    assert "label_not_allowed" in result.issues


def test_guardrail_rejects_unapproved_relations():
    registry = build_default_patent_schema_registry()

    result = inspect_patent_cypher(
        cypher="MATCH (p:Patent)-[:UNKNOWN_REL]->(o:Organization) RETURN p LIMIT 5",
        registry=registry,
    )

    assert result.verdict == "reject"
    assert "relation_not_allowed" in result.issues


def test_guardrail_appends_default_limit_when_missing():
    registry = build_default_patent_schema_registry()

    result = inspect_patent_cypher(
        cypher="MATCH (p:Patent)-[:HAS_INVENTOR]->(person:Person) RETURN p.patent_id AS patent_id",
        registry=registry,
    )

    assert result.verdict == "allow"
    assert result.normalized_cypher.endswith("LIMIT 200")


def test_guardrail_allows_valid_patent_labels_and_relations():
    registry = build_default_patent_schema_registry()

    result = inspect_patent_cypher(
        cypher=(
            "MATCH (p:Patent)-[:HAS_PROCESS_STEP]->(step:ProcessStep) "
            "OPTIONAL MATCH (step)-[:INSTANCE_OF]->(template:StepTemplate) "
            "RETURN p.patent_id AS patent_id, template.label AS step_template LIMIT 20"
        ),
        registry=registry,
    )

    assert result.verdict == "allow"
    assert result.issues == ()
