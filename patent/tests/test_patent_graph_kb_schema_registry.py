from __future__ import annotations

from server.patent.graph_kb.schema_registry import build_default_patent_schema_registry


def test_schema_registry_exposes_patent_native_fields_and_allowlists():
    registry = build_default_patent_schema_registry()

    assert registry.get_field("patent.id") is not None
    assert registry.get_field("ipc.code") is not None
    assert registry.get_field("ipc.subclass") is not None
    assert registry.get_field("organization.applicant") is not None
    assert registry.get_field("organization.agency") is not None
    assert registry.get_field("person.inventor") is not None
    assert registry.get_field("process.atmosphere") is not None
    assert registry.get_field("embodiment.insight") is not None

    assert "Patent" in registry.allowed_labels
    assert "Atmosphere" in registry.allowed_labels
    assert "EmbodimentInsight" in registry.allowed_labels
    assert "doi" not in registry.allowed_labels

    assert "NEXT_STEP" in registry.allowed_relations
    assert "USES_ATMOSPHERE" in registry.allowed_relations
    assert "HAS_EMBODIMENT_INSIGHT" in registry.allowed_relations
    assert "raw_materials" not in registry.allowed_relations


def test_schema_registry_summary_is_planner_facing():
    registry = build_default_patent_schema_registry()

    summary = registry.summarize_for_planner(intent="hybrid")

    assert summary.intent == "hybrid"
    assert summary.allowed_labels == registry.allowed_labels
    assert summary.allowed_relations == registry.allowed_relations
    assert "patent.id" in summary.fields
    assert "process.atmosphere" in summary.fields
    assert "embodiment.insight" in summary.fields
