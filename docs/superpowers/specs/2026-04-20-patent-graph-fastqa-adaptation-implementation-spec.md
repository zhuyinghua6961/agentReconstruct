# Patent Graph FastQA Adaptation Implementation Spec

**Date:** 2026-04-20

## Scope

This spec translates [patent_graph_fastqa_adaptation_plan.md](/home/cqy/worktrees/highThinking/patent_graph_fastqa_adaptation_plan.md) into an implementation-ready design for the `patent` service.

It covers:

- component architecture and interfaces
- data models and schemas
- implementation steps with exact file paths
- test strategy
- rollout boundaries for direct-answer and graph-for-RAG support

It does not cover:

- frontend changes
- new public API routes
- live database writes
- free-form LLM Cypher generation in the first rollout

## Goals

1. Evolve `patent/server/patent/graph_kb/` from a fixed-template shortcut into a fastQA V2-style graph routing subsystem.
2. Preserve the current 9 trusted patent templates as the high-confidence `direct_answer` path.
3. Add a tri-state graph routing result:
   - `direct_answer`
   - `graph_for_rag`
   - `skip_graph`
4. Let graph evidence participate in staged `kb_qa` generation without inserting Neo4j traversal into stage 2, stage 3, or stage 4 internals.
5. Keep the existing patent staged QA path as the default fallback whenever graph confidence, graph readiness, or graph coverage is insufficient.

## Non-goals

1. Do not replace the patent staged QA pipeline with graph-only answering.
2. Do not port fastQA’s DOI-centric field-bucket schema assumptions into patent.
3. Do not add graph behavior to `pdf_qa`, `tabular_qa`, or `hybrid_qa`.
4. Do not enable unconstrained LLM-generated Cypher in the initial rollout.
5. Do not weaken the current `stub` filtering policy for direct graph answers.

## Existing constraints

### Patent-side constraints

- Graph integration currently lives at the `PatentKbService.run()` preflight boundary.
- `PatentExecutor.execute_with_progress()` delegates `kb_qa` to `PatentKbService.run()`.
- `PatentGenerationOrchestrator.run()` already accepts `conversation_context` and passes it into:
  - stage 1 planning
  - stage 4 synthesis
- The current patent graph client uses the direct Neo4j Python driver, not `Neo4jGraph`.
- The current graph package already has working:
  - fixed query planning
  - direct Neo4j execution
  - deterministic direct rendering
  - `stub` filtering

### FastQA-derived architectural constraints

The reusable fastQA V2 architectural ideas are:

- tri-state routing
- classifier -> planner -> strategy -> guardrail -> executor -> canonicalizer -> renderer / adapter layering
- planner uses a logical schema, not raw labels alone
- non-template Cypher must be read-only and allowlisted
- direct answers and graph-for-RAG are distinct outputs

### Patent graph schema constraints

The live graph schema already supports the labels and relations needed for a richer graph layer:

- entity labels: `Patent`, `IPC`, `IPCPrefix`, `Organization`, `Person`, `ProcessStep`, `StepTemplate`, `MaterialRole`, `Material`, `ExperimentTable`, `TableRow`, `Measurement`, `TechnicalProblem`, `TechnicalSolution`, `ApplicationScenario`, `InventivePoint`, `PerformanceFact`, `ProtectionScope`, `ClaimStepLabel`, `Atmosphere`, `EmbodimentInsight`
- key extra edges beyond the current 9 templates: `NEXT_STEP`, `USES_ATMOSPHERE`, `HAS_EMBODIMENT_INSIGHT`

The implementation must still respect current direct-answer quality boundaries:

- if a target patent is `stub = true`, direct-answer preflight must fall back
- list-style graph answers should filter `stub` results by default

## Architecture

### Top-level design

The target design is an evolutionary wrapper around the existing patent graph path, not a rewrite.

`kb_qa` graph processing should become:

1. classify question with a patent-native V2 classifier
2. build a patent graph query plan
3. execute a trusted template or a guardrailed parametric query
4. canonicalize graph evidence
5. either:
   - return a graph direct answer
   - or inject graph evidence into staged QA via `conversation_context`
   - or skip graph entirely

### Module layout

Add these modules under [`patent/server/patent/graph_kb/`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb):

- `classifier_v2.py`
- `schema_registry.py`
- `planner_v2.py`
- `query_strategy.py`
- `guardrail.py`
- `executor_v2.py`
- `canonicalizer.py`
- `direct_renderer.py`
- `rag_adapter.py`

Retain and reuse these existing modules:

- [`classifier.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/classifier.py)
- [`client.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/client.py)
- [`neo4j_client.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/neo4j_client.py)
- [`rendering.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/rendering.py)
- [`service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/service.py)
- [`models.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/models.py)

### Integration boundaries

The primary orchestration boundary remains [`patent/server/patent/kb_service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/kb_service.py).

The new routing flow should be:

1. `PatentKbService._try_graph_preflight()` calls `route_patent_graph_kb_v2(...)` when V2 is enabled.
2. `route_patent_graph_kb_v2(...)` returns `PatentGraphRoutingResult`.
3. `PatentKbService` handles the result as follows:
   - `direct_answer` -> return immediate graph result
   - `graph_for_rag` -> enrich `conversation_context` with a graph payload, then continue into staged QA
   - `skip_graph` -> continue into staged QA unchanged

No Neo4j traversal logic should be inserted into:

- [`patent/server/patent/orchestrators/generation.py`](/home/cqy/worktrees/highThinking/patent/server/patent/orchestrators/generation.py)
- [`patent/server/patent/runtime.py`](/home/cqy/worktrees/highThinking/patent/server/patent/runtime.py)

Those layers should only consume graph evidence that arrives through a standardized context block.

## Component interfaces

### 1. `classifier_v2.py`

Responsibility:

- classify the question into a route family and a tri-state mode
- preserve the safe parts of the current patent classifier
- expand support for multi-patent, inventor, agency, and graph-for-RAG cases

Required interface:

```python
def classify_patent_graph_question_v2(
    *,
    question: str,
    conversation_context: dict[str, Any] | None = None,
) -> PatentGraphSemanticDecision:
    ...
```

Expected behavior:

- `precise` questions with a trusted template or safe parametric path -> `direct_answer`
- graph-anchored compare / explain / summarize questions -> `graph_for_rag`
- broad or weakly anchored questions -> `skip_graph` or low-confidence `graph_for_rag`
- file-context-heavy turns should not hard-fail; they should normally downgrade to `graph_for_rag` or `skip_graph`
- multi-patent compare questions should map to `graph_for_rag`, not unconditional skip

### 2. `schema_registry.py`

Responsibility:

- define the patent logical schema
- expose allowed labels and relations for planning and guardrails
- map logical fields to graph traversal paths

Required interface:

```python
def build_default_patent_schema_registry() -> PatentSchemaRegistry:
    ...
```

```python
class PatentSchemaRegistry:
    fields: dict[str, PatentLogicalFieldSpec]
    allowed_labels: tuple[str, ...]
    allowed_relations: tuple[str, ...]

    def get_field(self, logical_name: str) -> PatentLogicalFieldSpec | None:
        ...

    def summarize_for_planner(self, *, intent: str) -> PatentSchemaSummary:
        ...
```

### 3. `planner_v2.py`

Responsibility:

- translate the semantic decision into a graph query plan
- decide whether the question should use:
  - `template`
  - `parametric`
  - reserved `llm_cypher`

Required interface:

```python
def build_patent_graph_query_plan_v2(
    *,
    question: str,
    decision: PatentGraphSemanticDecision,
    schema_registry: PatentSchemaRegistry,
) -> PatentGraphQueryPlanV2 | None:
    ...
```

Planner rules:

- first prefer the existing 9 fixed templates
- second prefer parametric builders over free-form generation
- do not emit `llm_cypher` in the initial rollout unless explicitly feature-flagged

### 4. `query_strategy.py`

Responsibility:

- decide whether a question can be answered by:
  - an existing fixed template
  - a safe parametric builder
  - later, an opt-in constrained LLM path

Required interface:

```python
def select_patent_query_strategy(
    *,
    question: str,
    decision: PatentGraphSemanticDecision,
) -> str | None:
    ...
```

```python
def can_build_patent_parametric_query(
    *,
    question: str,
    decision: PatentGraphSemanticDecision,
) -> bool:
    ...
```

### 5. `guardrail.py`

Responsibility:

- inspect non-template Cypher before execution
- reject write clauses and unapproved tokens
- enforce `LIMIT`

Required interface:

```python
def inspect_patent_cypher(
    *,
    cypher: str,
    registry: PatentSchemaRegistry,
) -> PatentGuardrailResult:
    ...
```

Guardrail rules:

- reject `CREATE`, `MERGE`, `DELETE`, `SET`, `REMOVE`, `DROP`, `CALL`
- reject labels not in the patent registry allowlist
- reject relations not in the patent registry allowlist
- auto-append a default `LIMIT` if missing
- keep phase-1 traversal bounded and readable

### 6. `executor_v2.py`

Responsibility:

- run the prepared query plan
- execute legacy templates via existing `client.py`
- execute parametric candidates via the direct Neo4j client
- return rows plus execution trace

Required interface:

```python
def execute_patent_prepared_query(
    *,
    plan: PatentGraphQueryPlanV2,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 0,
    max_path_attempts: int = 1,
    registry: PatentSchemaRegistry | None = None,
) -> PatentRawExecutionResult:
    ...
```

### 7. `canonicalizer.py`

Responsibility:

- normalize rows from either template or parametric execution
- derive evidence fields for rendering and graph-for-RAG
- separate direct-answerability from graph evidence usefulness

Required interface:

```python
def canonicalize_patent_graph_rows(
    *,
    plan: PatentGraphQueryPlanV2,
    rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> PatentGraphEvidenceBundle:
    ...
```

### 8. `direct_renderer.py`

Responsibility:

- generate direct graph answers from canonicalized bundles
- preserve the current deterministic patent rendering tone
- only handle high-confidence direct answers

Required interface:

```python
def render_patent_direct_answer(
    *,
    decision: PatentGraphSemanticDecision,
    plan: PatentGraphQueryPlanV2,
    bundle: PatentGraphEvidenceBundle,
) -> PatentDirectAnswerResult:
    ...
```

### 9. `rag_adapter.py`

Responsibility:

- shape graph evidence into a patent-native RAG payload
- provide stable fields that can be inserted into staged QA through `conversation_context`

Required interface:

```python
def build_patent_graph_rag_payload(
    *,
    decision: PatentGraphSemanticDecision,
    plan: PatentGraphQueryPlanV2,
    bundle: PatentGraphEvidenceBundle,
) -> PatentGraphRagPayload:
    ...
```

### 10. `service.py`

Responsibility:

- continue exposing the current compatibility shell
- add the new orchestration entry point

Required interfaces:

```python
def try_patent_graph_kb_answer(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int,
    generation_runtime: Any | None = None,
) -> PatentGraphKbExecutionResult:
    ...
```

```python
def route_patent_graph_kb_v2(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int = 3000,
    generation_runtime: Any | None = None,
) -> PatentGraphRoutingResult:
    ...
```

`try_patent_graph_kb_answer(...)` remains the legacy direct-answer compatibility path.

`route_patent_graph_kb_v2(...)` becomes the new primary router when the V2 feature flag is enabled.

## Data models and schemas

All new contracts should be defined in [`patent/server/patent/graph_kb/models.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/models.py) to keep graph-related types local to the patent graph package.

### Core route decision models

```python
@dataclass(frozen=True)
class PatentGraphSemanticDecision:
    mode: str  # direct_answer | graph_for_rag | skip_graph
    route_family: str  # precise | hybrid | semantic
    standalone: bool = True
    requires_context_resolution: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)
```

```python
@dataclass(frozen=True)
class PatentGraphRoutingResult:
    mode: str
    direct_result: PatentGraphKbExecutionResult | None = None
    rag_payload: PatentGraphRagPayload | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
```

### Schema registry models

```python
@dataclass(frozen=True)
class PatentLogicalFieldSpec:
    logical_name: str
    label: str
    relation_path: tuple[str, ...] = ()
    value_kind: str = "text"
    description: str = ""
```

```python
@dataclass(frozen=True)
class PatentSchemaSummary:
    intent: str
    allowed_labels: tuple[str, ...]
    allowed_relations: tuple[str, ...]
    fields: tuple[str, ...]
```

### Query planning models

```python
@dataclass(frozen=True)
class PatentGraphConstraint:
    field: str
    operator: str
    value: Any
```

```python
@dataclass(frozen=True)
class PatentGraphQueryPlanV2:
    strategy: str  # template | parametric | llm_cypher
    intent: str = ""
    question: str = ""
    legacy_template_id: str = ""
    legacy_template_plan: PatentGraphKbQueryPlan | None = None
    parametric_slots: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
```

### Execution and guardrail models

```python
@dataclass(frozen=True)
class PatentGuardrailResult:
    verdict: str  # allow | reject
    issues: tuple[str, ...] = ()
    normalized_cypher: str = ""
```

```python
@dataclass(frozen=True)
class PatentExecutionTrace:
    strategy: str
    matched_path: str = ""
    attempted_paths: tuple[str, ...] = ()
    fallback_reason: str = ""
    guardrail_verdict: str = ""
    neo4j_client: str = "patent_neo4j_driver"
```

```python
@dataclass(frozen=True)
class PatentRawExecutionResult:
    rows: tuple[dict[str, Any], ...] = ()
    trace: PatentExecutionTrace = field(default_factory=lambda: PatentExecutionTrace(strategy=""))
```

### Canonical evidence and direct answer models

```python
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
```

```python
@dataclass(frozen=True)
class PatentDirectAnswerResult:
    handled: bool
    answer: str = ""
    references: tuple[str, ...] = ()
    reference_objects: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Graph-for-RAG payload model

```python
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
```

### Context injection contract

The patent graph payload should be carried through `conversation_context`, not by widening every stage runtime method.

Required normalized shape:

```python
conversation_context["graph_kb"] = {
    "mode": "graph_for_rag",
    "cache_fingerprint": "...",
    "stage1_context_block": "...",
    "stage2_patent_candidates": [...],
    "stage2_constraints": [...],
    "stage2_entity_hints": {...},
    "stage4_fact_block": "...",
    "stage4_graph_candidate_patent_ids": [...],
    "diagnostics": {...},
}
```

This keeps stage integration narrow:

- `PatentKbService` owns injection
- stage 1 planning reads this block when building the planning prompt
- stage 4 synthesis reads this block when building the synthesis context
- `stage4_graph_candidate_patent_ids` is a non-citable grounding list; it is distinct from the retrieval-backed `allowed_patent_ids` citation whitelist

## Patent logical schema

### Required logical fields

The initial registry should expose at least:

- `patent.id`
- `patent.title`
- `patent.abstract`
- `patent.application_date`
- `patent.publication_date`
- `patent.type`
- `patent.status`
- `patent.stub`
- `ipc.code`
- `ipc.subclass`
- `organization.applicant`
- `organization.agency`
- `person.inventor`
- `process.step_name`
- `process.operation`
- `process.template`
- `process.atmosphere`
- `material.role_name`
- `material.role_type`
- `material.ratio`
- `material.name`
- `material.type`
- `experiment.table_title`
- `experiment.row_label`
- `measurement.metric`
- `measurement.value`
- `measurement.unit`
- `problem.text`
- `solution.text`
- `scenario.text`
- `inventive_point.text`
- `performance_fact.text`
- `protection_scope.text`
- `claim_step_label.name`
- `citation.outgoing`
- `embodiment.insight`

### Allowed labels

The phase-1 allowlist should include:

- `Patent`
- `IPC`
- `IPCPrefix`
- `Organization`
- `Person`
- `ProcessStep`
- `StepTemplate`
- `MaterialRole`
- `Material`
- `ExperimentTable`
- `TableRow`
- `Measurement`
- `TechnicalProblem`
- `TechnicalSolution`
- `ApplicationScenario`
- `InventivePoint`
- `PerformanceFact`
- `ProtectionScope`
- `ClaimStepLabel`
- `Atmosphere`
- `EmbodimentInsight`

### Allowed relations

The phase-1 allowlist should include:

- `CLASSIFIED_AS`
- `IN_IPC_SUBCLASS`
- `HAS_APPLICANT`
- `HAS_AGENCY`
- `HAS_INVENTOR`
- `HAS_PROCESS_STEP`
- `INSTANCE_OF`
- `NEXT_STEP`
- `USES_ATMOSPHERE`
- `HAS_MATERIAL_ROLE`
- `OPTION_INCLUDES`
- `HAS_EXPERIMENT_TABLE`
- `HAS_ROW`
- `HAS_MEASUREMENT`
- `ADDRESSES`
- `PROPOSES`
- `HAS_APPLICATION_SCENARIO`
- `HAS_INVENTIVE_POINT`
- `HAS_PERFORMANCE_FACT`
- `PROTECTION_INCLUDES`
- `CLAIM_INCLUDES_STEP`
- `CITES_PATENT`
- `HAS_EMBODIMENT_INSIGHT`

## Query strategy

### Strategy order

The planner must prefer strategies in this order:

1. existing fixed template
2. slot-driven parametric query
3. reserved `llm_cypher` behind a future feature flag

### Phase-1 template-backed direct-answer intents

The existing 9 templates remain the trusted direct-answer core:

1. `lookup_patent_by_id`
2. `list_patent_process_steps`
3. `list_patent_material_roles`
4. `list_patent_experiment_tables`
5. `list_patent_problem_solution`
6. `list_patent_inventive_scope`
7. `list_patent_citations`
8. `list_patents_by_ipc`
9. `list_patents_by_applicant`

### Phase-2 parametric families

Add safe parametric builders for:

- `list_patents_by_inventor`
- `list_patents_by_agency`
- `list_patents_by_ipc_subclass`
- `count_patents_by_ipc`
- `count_patents_by_applicant`
- `count_patents_by_inventor`
- `compare_patents_process_steps`
- `compare_patents_material_roles`
- `compare_patents_problem_solution`
- `list_patent_atmospheres`
- `list_patent_embodiment_insights`

These builders should live in [`patent/server/patent/graph_kb/client.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/client.py) or a closely related helper because that file already owns trusted query construction.

## Graph-for-RAG integration design

### Why context injection is the preferred boundary

The current patent staged QA pipeline already carries `conversation_context` into:

- stage 1 planning
- stage 4 synthesis

That makes `conversation_context["graph_kb"]` the lowest-risk integration boundary for graph-for-RAG.

This approach avoids:

- widening every runtime protocol method
- passing raw graph rows through multiple unrelated layers
- embedding Neo4j-specific logic inside stage 2 / stage 3 / stage 4 internals

### Stage 1 integration

Modify [`patent/server/patent/stages/planning.py`](/home/cqy/worktrees/highThinking/patent/server/patent/stages/planning.py) to render a graph section when `conversation_context["graph_kb"]` exists.

Required additions:

- a formatter for graph stage-1 context
- prompt guidance telling stage 1 that graph-provided patent IDs, IPC codes, organizations, inventors, and fact blocks are higher-confidence structured anchors
- preference for graph-suggested patent candidates when constructing retrieval claims
- a graph-aware fallback path for planner-unavailable, JSON-parse-failed, and planner-error cases

Required degraded behavior:

- when `conversation_context["graph_kb"]` exists and stage-1 planning degrades, `planning.py` must seed a minimal retrieval plan from graph candidates, entity hints, and constraints instead of returning the normal empty plan
- if both the planner and graph fallback seeding fail, `kb_service.py` must record graph degradation in metadata and continue with normal staged QA behavior rather than claiming active `graph_for_rag`

Required cache behavior:

- [`patent/server/patent/cache_keys.py`](/home/cqy/worktrees/highThinking/patent/server/patent/cache_keys.py) must include a normalized graph payload fingerprint or normalized `graph_kb` block in `build_stage1_cache_fingerprint(...)`
- stage-1 cache coverage must explicitly prove that two requests with different graph payloads cannot share the same cache entry

### Stage 4 integration

Modify both:

- [`patent/server/patent/stages/synthesis.py`](/home/cqy/worktrees/highThinking/patent/server/patent/stages/synthesis.py)
- [`patent/server/patent/answering.py`](/home/cqy/worktrees/highThinking/patent/server/patent/answering.py)

Required additions:

- structured graph facts
- non-citable graph candidate patent IDs as grounding hints
- a note distinguishing graph facts from retrieval evidence

Stage 4 must not treat graph facts as raw source-text evidence. They are structured support and grounding hints, not replacements for cited retrieval snippets.

Implementation note:

- `synthesis.py` owns the stage-4 context handoff and should place graph payload data into the synthesis context
- `answering.py` owns prompt construction and fallback answer generation, so it must be updated for graph facts to materially reach either the LLM prompt or the non-LLM fallback path
- the existing `allowed_patent_ids` contract in stage 4 must remain the retrieval-backed citation whitelist; graph candidate IDs must travel in a separate field such as `graph_candidate_patent_ids` and must not become citable unless the same patent also appears in retrieved evidence

### Metadata and observability

Graph-for-RAG execution should leave traces in:

- `execution_result["metadata"]["graph_kb"]`
- `execution_result["metadata"]["graph_kb_mode"]`
- `execution_result["metadata"]["graph_kb_strategy"]`
- `execution_result["metadata"]["graph_kb_fingerprint"]`

This metadata should be added in [`patent/server/patent/kb_service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/kb_service.py), not deep inside stage code.

## File map

### Create

- `patent/server/patent/graph_kb/classifier_v2.py`
- `patent/server/patent/graph_kb/schema_registry.py`
- `patent/server/patent/graph_kb/planner_v2.py`
- `patent/server/patent/graph_kb/query_strategy.py`
- `patent/server/patent/graph_kb/guardrail.py`
- `patent/server/patent/graph_kb/executor_v2.py`
- `patent/server/patent/graph_kb/canonicalizer.py`
- `patent/server/patent/graph_kb/direct_renderer.py`
- `patent/server/patent/graph_kb/rag_adapter.py`
- `patent/tests/test_patent_graph_kb_stage1_cache_keys.py`
- `patent/tests/test_patent_graph_kb_classifier_v2.py`
- `patent/tests/test_patent_graph_kb_schema_registry.py`
- `patent/tests/test_patent_graph_kb_query_strategy.py`
- `patent/tests/test_patent_graph_kb_planner_v2.py`
- `patent/tests/test_patent_graph_kb_guardrail.py`
- `patent/tests/test_patent_graph_kb_executor_v2.py`
- `patent/tests/test_patent_graph_kb_canonicalizer.py`
- `patent/tests/test_patent_graph_kb_direct_renderer.py`
- `patent/tests/test_patent_graph_kb_rag_adapter.py`
- `patent/tests/test_patent_graph_kb_service_v2.py`
- `patent/tests/test_patent_stage1_graph_context.py`
- `patent/tests/test_patent_stage4_graph_context.py`
- `patent/tests/test_patent_answering_graph_context.py`

### Modify

- [`patent/server/patent/graph_kb/models.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/models.py)
- [`patent/server/patent/graph_kb/client.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/client.py)
- [`patent/server/patent/graph_kb/service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/service.py)
- [`patent/server/patent/graph_kb/__init__.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/__init__.py)
- [`patent/server/patent/cache_keys.py`](/home/cqy/worktrees/highThinking/patent/server/patent/cache_keys.py)
- [`patent/server/patent/kb_service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/kb_service.py)
- [`patent/server/patent/executor.py`](/home/cqy/worktrees/highThinking/patent/server/patent/executor.py)
- [`patent/server/patent/stages/planning.py`](/home/cqy/worktrees/highThinking/patent/server/patent/stages/planning.py)
- [`patent/server/patent/stages/synthesis.py`](/home/cqy/worktrees/highThinking/patent/server/patent/stages/synthesis.py)
- [`patent/server/patent/answering.py`](/home/cqy/worktrees/highThinking/patent/server/patent/answering.py)
- [`patent/config.py`](/home/cqy/worktrees/highThinking/patent/config.py)
- [`patent/config.shared.env.example`](/home/cqy/worktrees/highThinking/patent/config.shared.env.example)
- [`patent/server_fastapi/app.py`](/home/cqy/worktrees/highThinking/patent/server_fastapi/app.py)
- [`patent/server_fastapi/routers/health.py`](/home/cqy/worktrees/highThinking/patent/server_fastapi/routers/health.py)
- [`patent/tests/test_patent_graph_kb_config.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_graph_kb_config.py)
- [`patent/tests/test_patent_kb_service.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_kb_service.py)
- [`patent/tests/test_patent_executor.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)
- [`patent/tests/fastapi_contract/test_health_contract.py`](/home/cqy/worktrees/highThinking/patent/tests/fastapi_contract/test_health_contract.py)

## Implementation sequence

### Phase 1: Core contracts and feature flags

Modify:

- [`patent/server/patent/graph_kb/models.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/models.py)
- [`patent/config.py`](/home/cqy/worktrees/highThinking/patent/config.py)
- [`patent/config.shared.env.example`](/home/cqy/worktrees/highThinking/patent/config.shared.env.example)
- [`patent/server/patent/cache_keys.py`](/home/cqy/worktrees/highThinking/patent/server/patent/cache_keys.py)

Add:

- tri-state routing models
- planner / execution / bundle / payload models
- feature flags:
  - `PATENT_GRAPH_KB_V2_ENABLED`
  - `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED`
- stage-1 cache fingerprint support for graph payloads

### Phase 2: Classifier, registry, and planner

Create:

- [`patent/server/patent/graph_kb/classifier_v2.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/classifier_v2.py)
- [`patent/server/patent/graph_kb/schema_registry.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/schema_registry.py)
- [`patent/server/patent/graph_kb/planner_v2.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/planner_v2.py)
- [`patent/server/patent/graph_kb/query_strategy.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/query_strategy.py)

Extend:

- [`patent/server/patent/graph_kb/client.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/client.py)

Work items:

- preserve the current 9-template matcher
- add inventor, agency, IPC-subclass, and comparison-safe parametric builders
- keep direct-answer matchers and graph-for-RAG matchers separate in diagnostics

### Phase 3: Guardrail and executor

Create:

- [`patent/server/patent/graph_kb/guardrail.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/guardrail.py)
- [`patent/server/patent/graph_kb/executor_v2.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/executor_v2.py)

Work items:

- reuse the direct driver-based `PatentNeo4jClient`
- keep template execution on the current code path
- only run guardrails on parametric candidates
- return structured trace data for observability

### Phase 4: Canonicalization and direct rendering

Create:

- [`patent/server/patent/graph_kb/canonicalizer.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/canonicalizer.py)
- [`patent/server/patent/graph_kb/direct_renderer.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/direct_renderer.py)

Keep:

- [`patent/server/patent/graph_kb/rendering.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/rendering.py)

Work items:

- migrate shared rendering logic only where it reduces duplication
- keep the current `stub` policy centralized
- ensure `reference_objects` remain compatible with `PatentResultBuilder`

### Phase 5: Graph-for-RAG adapter and service orchestration

Create:

- [`patent/server/patent/graph_kb/rag_adapter.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/rag_adapter.py)

Modify:

- [`patent/server/patent/graph_kb/service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/graph_kb/service.py)
- [`patent/server/patent/kb_service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/kb_service.py)
- [`patent/server/patent/executor.py`](/home/cqy/worktrees/highThinking/patent/server/patent/executor.py)

Work items:

- add `route_patent_graph_kb_v2(...)`
- preserve `try_patent_graph_kb_answer(...)` as legacy direct path
- make `PatentKbService._try_graph_preflight()` V2-aware
- inject the graph payload into `conversation_context`
- continue returning direct graph answers through the current response contract
- if graph-for-RAG is selected but graph payload injection cannot be honored safely, degrade explicitly to normal staged QA with metadata explaining the downgrade

### Phase 6: Stage integration

Modify:

- [`patent/server/patent/stages/planning.py`](/home/cqy/worktrees/highThinking/patent/server/patent/stages/planning.py)
- [`patent/server/patent/stages/synthesis.py`](/home/cqy/worktrees/highThinking/patent/server/patent/stages/synthesis.py)
- [`patent/server/patent/answering.py`](/home/cqy/worktrees/highThinking/patent/server/patent/answering.py)

Work items:

- render graph context in stage 1 prompt construction
- render graph fact blocks in stage 4 synthesis context
- pass graph fact blocks and separate non-citable graph candidate IDs into the final answer prompt path and fallback answer builder
- keep `allowed_patent_ids` retrieval-backed only; do not promote graph candidates into the citation whitelist
- include graph fingerprint and mode in metadata
- keep stage 2 / stage 3 retrieval behavior unchanged
- implement graph-aware degraded planning fallback so planner-unavailable and parse-failure cases can still seed retrieval from graph candidates when available

### Phase 7: Runtime and health wiring

Modify:

- [`patent/server_fastapi/app.py`](/home/cqy/worktrees/highThinking/patent/server_fastapi/app.py)
- [`patent/server_fastapi/routers/health.py`](/home/cqy/worktrees/highThinking/patent/server_fastapi/routers/health.py)

Work items:

- keep existing `patent_graph_kb` component reporting
- add V2 flag visibility to health metadata
- do not let graph degradation alone force non-durable health to fail when staged QA is otherwise ready

## Test strategy

### Unit tests

Create focused unit suites for each new layer:

- `test_patent_graph_kb_stage1_cache_keys.py`
- `test_patent_graph_kb_classifier_v2.py`
- `test_patent_graph_kb_schema_registry.py`
- `test_patent_graph_kb_query_strategy.py`
- `test_patent_graph_kb_planner_v2.py`
- `test_patent_graph_kb_guardrail.py`
- `test_patent_graph_kb_executor_v2.py`
- `test_patent_graph_kb_canonicalizer.py`
- `test_patent_graph_kb_direct_renderer.py`
- `test_patent_graph_kb_rag_adapter.py`
- `test_patent_graph_kb_service_v2.py`

Required coverage:

- exact patent ID routing
- IPC routing
- IPC subclass routing
- applicant / inventor routing
- multi-patent compare -> `graph_for_rag`
- broad semantic -> `skip_graph`
- file-context downgrade rules
- template planning parity with the current 9 templates
- parametric builder gating
- guardrail rejection on write clauses and unapproved labels
- `stub` direct-answer fallback behavior
- `graph_for_rag` payload shaping and fingerprint stability

### Patent service integration tests

Extend:

- [`patent/tests/test_patent_kb_service.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_kb_service.py)
- [`patent/tests/test_patent_executor.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

Required coverage:

- V2 disabled -> current graph behavior preserved
- V2 enabled + `direct_answer` -> immediate graph return
- V2 enabled + `graph_for_rag` + rag injection enabled -> staged QA continues with enriched context
- V2 enabled + `graph_for_rag` + rag injection disabled -> staged QA continues without graph context
- `skip_graph` -> staged QA path unchanged
- graph exceptions -> silent fallback

### Stage prompt integration tests

Create:

- [`patent/tests/test_patent_stage1_graph_context.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_stage1_graph_context.py)
- [`patent/tests/test_patent_stage4_graph_context.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_stage4_graph_context.py)
- [`patent/tests/test_patent_answering_graph_context.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_answering_graph_context.py)

Required coverage:

- stage 1 formatter includes graph patent candidates, entity hints, and context block
- stage 1 degraded fallback seeds retrieval claims from graph payload when the planner is unavailable or parse fails
- stage 4 context handoff includes graph fact block and separate non-citable graph candidate IDs
- answer-builder prompt and fallback answer generation include graph context without converting graph facts or graph-only candidates into fabricated citations
- cache fingerprint changes when graph payload changes

### Config and health tests

Extend:

- [`patent/tests/test_patent_graph_kb_config.py`](/home/cqy/worktrees/highThinking/patent/tests/test_patent_graph_kb_config.py)
- [`patent/tests/fastapi_contract/test_health_contract.py`](/home/cqy/worktrees/highThinking/patent/tests/fastapi_contract/test_health_contract.py)

Required coverage:

- new V2 flags default off
- health payload exposes graph enabled / ready / V2 state
- graph degradation does not independently break unrelated routes

### Service-backed smoke verification

Optional, non-default verification against the local patent Neo4j instance:

- exact patent lookup
- IPC listing
- applicant listing
- one graph-for-RAG compare question

This should remain outside the default unit-test path.

## Edge cases the implementation must handle

1. Multiple patent IDs in a compare question should not be hard-skipped if the question is graph-anchored.
2. Ambiguous follow-ups should not produce false direct answers.
3. Questions mentioning DOI should continue to skip patent graph handling.
4. Applicant and inventor names may collide with ordinary words; exact-match-only rules are too brittle.
5. Graph payloads must not poison stage caches across unrelated questions.
6. Parametric query builders must not emit open-ended traversals.
7. Empty graph results and `stub`-only results must both degrade safely.
8. `reference_objects` must stay aligned with `references`, or `PatentResultBuilder` will reject them.
9. `graph_for_rag` must never bypass the patent staged citation and evidence rules.
10. Health reporting must distinguish “graph unavailable” from “service unavailable”.
11. Planner-unavailable and JSON-parse-failed stage-1 cases must still have a defined graph-for-RAG downgrade path.
12. Graph candidate IDs must not leak into the stage-4 citation whitelist unless retrieval evidence independently includes them.

## Rollout recommendation

1. Ship V2 contracts and feature flags first, with V2 disabled by default.
2. Enable V2 direct-answer parity first.
3. Enable graph-for-RAG injection only after stage integration tests are green.
4. Add parametric builders incrementally, starting with inventor, agency, and compare-intent helpers.
5. Keep `llm_cypher` entirely disabled in the initial rollout.

## Acceptance criteria

1. Existing 9 direct templates still work unchanged when V2 is off.
2. With V2 on, the service can distinguish:
   - `direct_answer`
   - `graph_for_rag`
   - `skip_graph`
3. `graph_for_rag` enriches staged QA through context injection, not deep graph traversal in stage code.
4. New graph logic preserves patent-native identifiers and patent-native reference objects.
5. The implementation remains read-only and safe against accidental write queries.
