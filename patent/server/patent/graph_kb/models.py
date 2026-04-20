from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PatentGraphKbDecision:
    decision: str
    reason: str
    standalone: bool
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatentGraphKbQueryPlan:
    template_id: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentGraphKbExecutionResult:
    handled: bool
    answer: str = ""
    references: tuple[str, ...] = ()
    reference_objects: tuple[dict[str, Any], ...] = ()
    query_mode: str = "patent_graph_kb"
    template_id: str = ""
    result_count: int = 0
    latency_ms: float = 0.0
    fallback_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentGraphSemanticDecision:
    mode: str
    route_family: str
    standalone: bool = True
    requires_context_resolution: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentGraphConstraint:
    field: str
    operator: str
    value: Any


def _coerce_constraint(value: Any) -> PatentGraphConstraint:
    if isinstance(value, PatentGraphConstraint):
        return value
    if isinstance(value, dict):
        return PatentGraphConstraint(
            field=str(value.get("field") or ""),
            operator=str(value.get("operator") or ""),
            value=value.get("value"),
        )
    return PatentGraphConstraint(
        field=str(getattr(value, "field", "") or ""),
        operator=str(getattr(value, "operator", "") or ""),
        value=getattr(value, "value", None),
    )


@dataclass(frozen=True)
class PatentGraphRagPayload:
    stage1_context_block: str = ""
    stage2_patent_candidates: tuple[str, ...] = ()
    stage2_constraints: tuple[PatentGraphConstraint, ...] = ()
    stage2_entity_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    stage4_fact_block: str = ""
    stage4_graph_candidate_patent_ids: tuple[str, ...] = ()
    cache_fingerprint: str = "none"
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stage2_patent_candidates",
            tuple(str(item).strip() for item in tuple(self.stage2_patent_candidates or ()) if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "stage2_constraints",
            tuple(_coerce_constraint(item) for item in tuple(self.stage2_constraints or ())),
        )
        object.__setattr__(
            self,
            "stage2_entity_hints",
            {
                str(key): tuple(
                    str(item).strip()
                    for item in tuple(values or ())
                    if str(item or "").strip()
                )
                for key, values in dict(self.stage2_entity_hints or {}).items()
            },
        )
        object.__setattr__(
            self,
            "stage4_graph_candidate_patent_ids",
            tuple(
                str(item).strip()
                for item in tuple(self.stage4_graph_candidate_patent_ids or ())
                if str(item or "").strip()
            ),
        )


@dataclass(frozen=True)
class PatentGraphRoutingResult:
    mode: str
    direct_result: PatentGraphKbExecutionResult | None = None
    rag_payload: PatentGraphRagPayload | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentGraphQueryPlanV2:
    strategy: str
    intent: str = ""
    question: str = ""
    legacy_template_id: str = ""
    legacy_template_plan: PatentGraphKbQueryPlan | None = None
    parametric_slots: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentLogicalFieldSpec:
    logical_name: str
    label: str
    relation_path: tuple[str, ...] = ()
    property_name: str = ""
    value_kind: str = "text"
    description: str = ""


@dataclass(frozen=True)
class PatentSchemaSummary:
    intent: str
    allowed_labels: tuple[str, ...]
    allowed_relations: tuple[str, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class PatentSchemaRegistry:
    fields: dict[str, PatentLogicalFieldSpec] = field(default_factory=dict)
    allowed_labels: tuple[str, ...] = ()
    allowed_relations: tuple[str, ...] = ()

    def get_field(self, logical_name: str) -> PatentLogicalFieldSpec | None:
        return self.fields.get(str(logical_name or "").strip())

    def summarize_for_planner(self, *, intent: str) -> PatentSchemaSummary:
        return PatentSchemaSummary(
            intent=str(intent or "").strip() or "unknown",
            allowed_labels=self.allowed_labels,
            allowed_relations=self.allowed_relations,
            fields=tuple(sorted(self.fields)),
        )


@dataclass(frozen=True)
class PatentGuardrailResult:
    verdict: str
    issues: tuple[str, ...] = ()
    normalized_cypher: str = ""


@dataclass(frozen=True)
class PatentExecutionTrace:
    strategy: str
    matched_path: str = ""
    attempted_paths: tuple[str, ...] = ()
    fallback_reason: str = ""
    guardrail_verdict: str = ""
    neo4j_client: str = "patent_neo4j_driver"


@dataclass(frozen=True)
class PatentRawExecutionResult:
    rows: tuple[dict[str, Any], ...] = ()
    trace: PatentExecutionTrace = field(default_factory=lambda: PatentExecutionTrace(strategy=""))


@dataclass(frozen=True)
class PatentGraphEvidenceBundle:
    patent_candidates: tuple[str, ...] = ()
    ipc_candidates: tuple[str, ...] = ()
    organization_candidates: tuple[str, ...] = ()
    inventor_candidates: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    render_slots: dict[str, Any] = field(default_factory=dict)
    direct_answerable: bool = False
    constraints_for_rag: tuple[PatentGraphConstraint, ...] = ()
    confidence: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "patent_candidates",
            tuple(str(item).strip() for item in tuple(self.patent_candidates or ()) if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "ipc_candidates",
            tuple(str(item).strip() for item in tuple(self.ipc_candidates or ()) if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "organization_candidates",
            tuple(str(item).strip() for item in tuple(self.organization_candidates or ()) if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "inventor_candidates",
            tuple(str(item).strip() for item in tuple(self.inventor_candidates or ()) if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "facts",
            tuple(str(item).strip() for item in tuple(self.facts or ()) if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "constraints_for_rag",
            tuple(_coerce_constraint(item) for item in tuple(self.constraints_for_rag or ())),
        )


@dataclass(frozen=True)
class PatentDirectAnswerResult:
    handled: bool
    answer: str = ""
    references: tuple[str, ...] = ()
    reference_objects: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
