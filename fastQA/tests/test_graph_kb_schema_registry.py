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


def test_registry_covers_v1_field_bucket_schema():
    registry = build_default_schema_registry()

    for field in [
        "paper.doi",
        "paper.title",
        "material.sample_name",
        "raw_material.name",
        "process.method",
        "process.calcination",
        "process.milling",
        "process.sintering",
        "process.drying",
        "recipe.carbon_source",
        "recipe.doping_elements",
        "performance.discharge_capacity_child",
        "performance.compaction_density",
        "performance.tap_density",
        "performance.conductivity",
        "performance.cycling_stability",
        "performance.coulombic_efficiency",
        "recipe.carbon_content",
        "recipe.dopant",
        "recipe.additives",
        "recipe.ratios",
        "community.id",
    ]:
        assert registry.get_field(field) is not None


def test_registry_marks_direct_and_deferred_support_tiers():
    registry = build_default_schema_registry()

    carbon_source = registry.get_field("recipe.carbon_source")
    compaction_density = registry.get_field("performance.compaction_density")
    energy_density = registry.get_field("performance.energy_density")

    assert carbon_source is not None
    assert carbon_source.direct_answer_eligible is True
    assert getattr(carbon_source, "support_tier") == "direct-capable"
    assert compaction_density is not None
    assert compaction_density.direct_answer_eligible is False
    assert getattr(compaction_density, "numeric_parse_supported") is True
    assert energy_density is not None
    assert getattr(energy_density, "support_tier") == "deferred"


def test_registry_allowlist_contains_v1_labels_and_relations():
    registry = build_default_schema_registry()

    for label in ["carbon_source", "calcination", "milling", "discharge_capacity", "compaction_density"]:
        assert label in registry.allowed_labels

    for rel in ["carbon_source", "calcination", "milling", "discharge_capacity", "key_process_parameters"]:
        assert rel in registry.allowed_relations
