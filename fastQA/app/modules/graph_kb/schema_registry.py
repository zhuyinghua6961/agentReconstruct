from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LogicalFieldSpec:
    logical_name: str
    label: str
    relation_path: tuple[str, ...] = ()
    property_name: str = "name"
    value_kind: str = "text"
    description: str = ""
    direct_answer_eligible: bool = False
    rag_eligible: bool = True
    numeric_parse_supported: bool = False
    support_tier: str = "graph_for_rag"
    default_limit: int = 20


@dataclass(frozen=True)
class SchemaSummary:
    intent: str
    allowed_labels: tuple[str, ...]
    allowed_relations: tuple[str, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class SchemaRegistry:
    fields: dict[str, LogicalFieldSpec] = field(default_factory=dict)
    allowed_labels: tuple[str, ...] = ()
    allowed_relations: tuple[str, ...] = ()

    def get_field(self, logical_name: str) -> LogicalFieldSpec | None:
        return self.fields.get(str(logical_name or "").strip())

    def summarize_for_planner(self, *, intent: str) -> SchemaSummary:
        return SchemaSummary(
            intent=str(intent or "").strip() or "unknown",
            allowed_labels=self.allowed_labels,
            allowed_relations=self.allowed_relations,
            fields=tuple(sorted(self.fields)),
        )


def build_default_schema_registry() -> SchemaRegistry:
    return SchemaRegistry(
        fields={
            "paper.doi": LogicalFieldSpec(
                logical_name="paper.doi",
                label="doi",
                description="Primary DOI node in the legacy field-bucket graph.",
                support_tier="direct-capable",
            ),
            "paper.title": LogicalFieldSpec(
                logical_name="paper.title",
                label="title",
                relation_path=("title",),
                description="Title bucket connected from DOI nodes.",
            ),
            "raw_material.name": LogicalFieldSpec(
                logical_name="raw_material.name",
                label="raw_materials",
                relation_path=("raw_materials", "raw_materials"),
                description="Raw material values linked through the raw_materials bucket.",
            ),
            "material.sample_name": LogicalFieldSpec(
                logical_name="material.sample_name",
                label="name",
                relation_path=("name",),
                description="Material or sample name nodes linked to DOI nodes.",
            ),
            "process.method": LogicalFieldSpec(
                logical_name="process.method",
                label="preparation_method",
                relation_path=("process", "preparation_method"),
                description="Preparation or process method values.",
            ),
            "process.calcination": LogicalFieldSpec(
                logical_name="process.calcination",
                label="calcination",
                relation_path=("process", "key_process_parameters", "calcination"),
                description="Calcination parameter values under process key parameter buckets.",
            ),
            "process.milling": LogicalFieldSpec(
                logical_name="process.milling",
                label="milling",
                relation_path=("process", "key_process_parameters", "milling"),
                description="Milling parameter values under process key parameter buckets.",
            ),
            "process.sintering": LogicalFieldSpec(
                logical_name="process.sintering",
                label="sintering",
                relation_path=("process", "key_process_parameters", "sintering"),
                description="Sintering parameter values under process key parameter buckets.",
            ),
            "process.drying": LogicalFieldSpec(
                logical_name="process.drying",
                label="drying",
                relation_path=("process", "key_process_parameters", "drying"),
                description="Drying parameter values under process key parameter buckets.",
            ),
            "equipment.name": LogicalFieldSpec(
                logical_name="equipment.name",
                label="equipment",
                relation_path=("equipment", "equipment"),
                description="Equipment bucket values available for later graph evidence use.",
            ),
            "testing.name": LogicalFieldSpec(
                logical_name="testing.name",
                label="testing",
                relation_path=("testing", "testing"),
                description="Testing or characterization values attached to a DOI.",
            ),
            "recipe.name": LogicalFieldSpec(
                logical_name="recipe.name",
                label="recipe",
                relation_path=("recipe", "recipe"),
                description="Recipe or composition values stored in the graph.",
            ),
            "recipe.carbon_source": LogicalFieldSpec(
                logical_name="recipe.carbon_source",
                label="carbon_source",
                relation_path=("recipe", "carbon_source"),
                description="Carbon source values linked through the recipe bucket.",
                direct_answer_eligible=True,
                support_tier="direct-capable",
            ),
            "recipe.carbon_content": LogicalFieldSpec(
                logical_name="recipe.carbon_content",
                label="carbon_content",
                relation_path=("recipe", "carbon_content"),
                description="Carbon content values linked through the recipe bucket.",
            ),
            "recipe.dopant": LogicalFieldSpec(
                logical_name="recipe.dopant",
                label="dopant",
                relation_path=("recipe", "dopant"),
                description="Dopant values linked through the recipe bucket.",
            ),
            "recipe.doping_elements": LogicalFieldSpec(
                logical_name="recipe.doping_elements",
                label="doping_elements",
                relation_path=("recipe", "doping_elements"),
                description="Doping element values linked through the recipe bucket.",
            ),
            "recipe.additives": LogicalFieldSpec(
                logical_name="recipe.additives",
                label="additives",
                relation_path=("recipe", "additives"),
                description="Additive values linked through the recipe bucket when present.",
            ),
            "recipe.ratios": LogicalFieldSpec(
                logical_name="recipe.ratios",
                label="ratios",
                relation_path=("recipe", "ratios"),
                description="Recipe ratio values linked through the recipe bucket when present.",
            ),
            "performance.discharge_capacity_child": LogicalFieldSpec(
                logical_name="performance.discharge_capacity_child",
                label="discharge_capacity",
                relation_path=("name", "discharge_capacity", "discharge_capacity"),
                value_kind="numeric_text",
                description="Child discharge capacity values under sample-name capacity buckets.",
                numeric_parse_supported=True,
            ),
            "performance.compaction_density": LogicalFieldSpec(
                logical_name="performance.compaction_density",
                label="compaction_density",
                relation_path=("name", "compaction_density"),
                value_kind="numeric_text",
                description="Compaction density values linked from sample-name nodes.",
                numeric_parse_supported=True,
            ),
            "performance.tap_density": LogicalFieldSpec(
                logical_name="performance.tap_density",
                label="tap_density",
                relation_path=("name", "tap_density"),
                value_kind="numeric_text",
                description="Tap density values linked from sample-name nodes when present.",
                numeric_parse_supported=True,
            ),
            "performance.conductivity": LogicalFieldSpec(
                logical_name="performance.conductivity",
                label="conductivity",
                relation_path=("name", "conductivity"),
                value_kind="numeric_text",
                description="Conductivity values linked from sample-name nodes when present.",
                numeric_parse_supported=True,
            ),
            "performance.cycling_stability": LogicalFieldSpec(
                logical_name="performance.cycling_stability",
                label="cycling_stability",
                relation_path=("name", "cycling_stability"),
                value_kind="numeric_text",
                description="Cycling stability values linked from sample-name nodes.",
                numeric_parse_supported=True,
            ),
            "performance.coulombic_efficiency": LogicalFieldSpec(
                logical_name="performance.coulombic_efficiency",
                label="coulombic_efficiency",
                relation_path=("name", "coulombic_efficiency"),
                value_kind="numeric_text",
                description="Coulombic efficiency values linked from sample-name nodes when present.",
                numeric_parse_supported=True,
            ),
            "performance.energy_density": LogicalFieldSpec(
                logical_name="performance.energy_density",
                label="energy_density",
                relation_path=("name", "energy_density"),
                value_kind="numeric_text",
                description="Deferred until live schema coverage is verified.",
                support_tier="deferred",
            ),
            "performance.power_density": LogicalFieldSpec(
                logical_name="performance.power_density",
                label="power_density",
                relation_path=("name", "power_density"),
                value_kind="numeric_text",
                description="Deferred until live schema coverage is verified.",
                support_tier="deferred",
            ),
            "performance.surface_area": LogicalFieldSpec(
                logical_name="performance.surface_area",
                label="surface_area",
                relation_path=("name", "surface_area"),
                value_kind="numeric_text",
                description="Deferred until live schema coverage is verified.",
                support_tier="deferred",
            ),
            "community.id": LogicalFieldSpec(
                logical_name="community.id",
                label="",
                property_name="louvainCommunityId",
                value_kind="integer",
                description="Louvain community identifier stored as a node property.",
                direct_answer_eligible=True,
            ),
            "description.name": LogicalFieldSpec(
                logical_name="description.name",
                label="description",
                relation_path=("description", "description"),
                description="Description bucket values available as structured graph evidence.",
            ),
        },
        allowed_labels=(
            "doi",
            "name",
            "title",
            "raw_materials",
            "process",
            "preparation_method",
            "key_process_parameters",
            "calcination",
            "milling",
            "sintering",
            "drying",
            "recipe",
            "carbon_source",
            "carbon_content",
            "dopant",
            "doping_elements",
            "additives",
            "ratios",
            "equipment",
            "testing",
            "description",
            "discharge_capacity",
            "compaction_density",
            "tap_density",
            "conductivity",
            "cycling_stability",
            "coulombic_efficiency",
        ),
        allowed_relations=(
            "title",
            "raw_materials",
            "process",
            "preparation_method",
            "key_process_parameters",
            "calcination",
            "milling",
            "sintering",
            "drying",
            "recipe",
            "carbon_source",
            "carbon_content",
            "dopant",
            "doping_elements",
            "additives",
            "ratios",
            "equipment",
            "testing",
            "description",
            "name",
            "discharge_capacity",
            "compaction_density",
            "tap_density",
            "conductivity",
            "cycling_stability",
            "coulombic_efficiency",
        ),
    )
