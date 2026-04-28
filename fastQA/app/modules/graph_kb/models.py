from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GraphRouteFamily(str, Enum):
    PRECISE = "precise"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    COMMUNITY = "community"


class GraphExecutionMode(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    GRAPH_FOR_RAG = "graph_for_rag"
    SKIP_GRAPH = "skip_graph"


@dataclass(frozen=True)
class GraphKbDecision:
    decision: str
    reason: str
    standalone: bool
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphKbQueryPlan:
    template_id: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphKbExecutionResult:
    handled: bool
    answer: str = ""
    references: tuple[str, ...] = ()
    query_mode: str = "graph_kb"
    template_id: str = ""
    result_count: int = 0
    latency_ms: float = 0.0
    fallback_reason: str = ""


@dataclass(frozen=True)
class GraphConstraint:
    field: str
    operator: str
    value: Any


def _coerce_constraint(value: Any) -> GraphConstraint:
    if isinstance(value, GraphConstraint):
        return value
    if isinstance(value, dict):
        return GraphConstraint(
            field=str(value.get("field") or ""),
            operator=str(value.get("operator") or ""),
            value=value.get("value"),
        )
    return GraphConstraint(
        field=str(getattr(value, "field", "") or ""),
        operator=str(getattr(value, "operator", "") or ""),
        value=getattr(value, "value", None),
    )


@dataclass(frozen=True)
class GraphRagPayload:
    stage1_context_block: str = ""
    stage2_doi_candidates: tuple[str, ...] = ()
    stage2_constraints: tuple[GraphConstraint, ...] = ()
    stage2_entity_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    stage4_fact_block: str = ""
    cache_fingerprint: str = "none"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stage2_doi_candidates",
            tuple(
                str(item).strip()
                for item in tuple(self.stage2_doi_candidates or ())
                if str(item or "").strip()
            ),
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


@dataclass(frozen=True)
class GraphRoutingResult:
    mode: str
    direct_result: GraphKbExecutionResult | None = None
    rag_payload: GraphRagPayload | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticDecision:
    mode: str
    legacy_route: str
    standalone: bool = True
    diagnostics: dict[str, Any] = field(default_factory=dict)
    route_family: str = ""
    confidence: float = 0.0
    slots: dict[str, Any] = field(default_factory=dict)
    direct_answer_eligible: bool = False
    fallback_reason: str = ""


@dataclass(frozen=True)
class GraphQuestionSlots:
    doi: str = ""
    doi_intent: str = ""
    entities: tuple[str, ...] = ()
    title_terms: tuple[str, ...] = ()
    material_terms: tuple[str, ...] = ()
    raw_material_terms: tuple[str, ...] = ()
    recipe_terms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    process_terms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    property_field: str = ""
    operator: str = ""
    threshold: float | None = None
    unit: str = ""
    ranking: str = ""
    limit: int | None = None
    community_signal: bool = False
    analysis_signal: bool = False
    enumeration_signal: bool = False
    count_signal: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "doi": self.doi,
            "doi_intent": self.doi_intent,
            "entities": self.entities,
            "title_terms": self.title_terms,
            "material_terms": self.material_terms,
            "raw_material_terms": self.raw_material_terms,
            "recipe_terms": self.recipe_terms,
            "process_terms": self.process_terms,
            "property_field": self.property_field,
            "operator": self.operator,
            "threshold": self.threshold,
            "unit": self.unit,
            "ranking": self.ranking,
            "limit": self.limit,
            "community_signal": self.community_signal,
            "analysis_signal": self.analysis_signal,
            "enumeration_signal": self.enumeration_signal,
            "count_signal": self.count_signal,
        }


@dataclass(frozen=True)
class GraphQueryPath:
    path_id: str
    cypher: str
    params: dict[str, Any] = field(default_factory=dict)
    expected_columns: tuple[str, ...] = ()
    direct_answer_eligible: bool = False

    def as_candidate_query(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "cypher": self.cypher,
            "params": dict(self.params),
            "expected_columns": self.expected_columns,
            "direct_answer_eligible": self.direct_answer_eligible,
        }


@dataclass(frozen=True)
class GraphQueryPlanV2:
    strategy: str
    intent: str = ""
    question: str = ""
    legacy_template_id: str = ""
    legacy_template_plan: GraphKbQueryPlan | None = None
    parametric_slots: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardrailResult:
    verdict: str
    issues: tuple[str, ...] = ()
    normalized_cypher: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"


@dataclass(frozen=True)
class ExecutionTrace:
    strategy: str
    matched_path: str = ""
    attempted_paths: tuple[str, ...] = ()
    fallback_reason: str = ""
    guardrail_verdict: str = ""
    neo4j_client: str = "neo4jgraph"


@dataclass(frozen=True)
class RawExecutionResult:
    rows: tuple[dict[str, Any], ...] = ()
    trace: ExecutionTrace = field(default_factory=lambda: ExecutionTrace(strategy=""))


@dataclass(frozen=True)
class GraphEvidenceBundle:
    doi_candidates: tuple[str, ...] = ()
    direct_render_dois: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    render_slots: dict[str, Any] = field(default_factory=dict)
    direct_answerable: bool = False
    constraints_for_rag: tuple[GraphConstraint, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
    entity_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectAnswerResult:
    handled: bool
    answer: str = ""
    references: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
