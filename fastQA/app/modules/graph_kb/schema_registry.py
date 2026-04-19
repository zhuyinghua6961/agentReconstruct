from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LogicalFieldSpec:
    logical_name: str
    label: str
    relation_path: tuple[str, ...] = ()
    value_kind: str = "text"
    description: str = ""


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
            "process.method": LogicalFieldSpec(
                logical_name="process.method",
                label="preparation_method",
                relation_path=("process", "preparation_method"),
                description="Preparation or process method values.",
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
            "recipe",
            "equipment",
            "testing",
            "description",
        ),
        allowed_relations=(
            "title",
            "raw_materials",
            "process",
            "preparation_method",
            "recipe",
            "equipment",
            "testing",
            "description",
            "name",
        ),
    )
