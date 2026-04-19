from __future__ import annotations

from app.modules.graph_kb.schema_registry import build_default_schema_registry


def test_schema_registry_exposes_allowed_labels_and_field_specs():
    registry = build_default_schema_registry()

    summary = registry.summarize_for_planner(intent="doi_lookup")

    assert "doi" in summary.allowed_labels
    assert "recipe" in summary.allowed_labels
    assert registry.get_field("paper.title") is not None


def test_schema_registry_knows_process_and_equipment_logical_fields():
    registry = build_default_schema_registry()

    process_field = registry.get_field("process.method")
    equipment_field = registry.get_field("equipment.name")

    assert process_field is not None
    assert process_field.logical_name == "process.method"
    assert equipment_field is not None
    assert equipment_field.logical_name == "equipment.name"
