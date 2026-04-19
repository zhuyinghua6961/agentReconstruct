# fastQA graph_kb Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `fastQA` graph_kb from the current binary template fast-path into a three-mode graph routing and graph-for-rag evidence layer that remains compatible with the existing Neo4j field-bucket schema, preserves the current 5-template fallback behavior, and explicitly migrates reusable logic from the legacy `MaterialScienceAgent` / `CommanderAgent` pipeline instead of rewriting it from scratch.

**Architecture:** Keep the current legacy graph path working while introducing a parallel V2 stack: `classifier_v2 -> planner_v2 -> query_strategy -> guardrail/executor -> canonicalizer -> direct_renderer/rag_adapter`. `classifier_v2` is not a greenfield classifier; it is a Commander-compatible wrapper around the legacy routing chain (`HybridQueryAgent.is_hybrid_question -> precise keywords + numeric attributes -> community keywords -> semantic keywords -> graph non-numeric attributes + enumeration -> numeric-attribute-only precise route -> entity route -> default semantic`) that adds tri-state output. Route integration happens in `app/routers/qa.py`, and `graph_evidence` is carried as a top-level `QaKbRequest` field through `QaKbService`, the generation orchestrator, and the `GenerationRuntime` facade. Cache isolation and `graph_seeded_doi_fallback` are implemented as first-class behaviors rather than ad-hoc exceptions.

**Tech Stack:** FastAPI, Python dataclasses/protocols, existing Neo4j integration, `langchain_community.graphs.Neo4jGraph` bootstrap via `app/integrations/neo4j/client.py`, generation-driven RAG runtime, Redis-backed stage caches, pytest, legacy KG pipeline reference docs

**Spec:** `docs/graph_kb_upgrade_spec.md`

---

## Migration Principles

1. **MIGRATE before REPLACE**: any logic already proven in legacy `MaterialScienceAgent` / `CommanderAgent` must be inventoried first, then either migrated intact, wrapped, or explicitly replaced with justification.
2. **Commander-compatible classifier evolution**: `classifier_v2` wraps and extends the legacy route chain; it does not invent a new routing order before parity is established.
3. **Single Neo4j client choice**: fastQA V2 standardizes on `app/integrations/neo4j/client.py -> bootstrap_neo4j() -> Neo4jGraph` as the canonical connected client because:
   - it already matches current `NEO4J_URL / NEO4J_USERNAME / NEO4J_PASSWORD` runtime wiring
   - `app/core/runtime.py` and `tests/test_graph_kb_runtime.py` already verify this bootstrap path
   - existing `graph_kb/client.py` execution logic already knows how to use `graph.query(...)` and `graph._driver.execute_query(...)`
4. **Demote `app/modules/graph_kb/client.py` from “client” to “query/planning layer”**: V2 should not introduce a second connection bootstrap path. If low-level timeout/query helpers are needed, add them on top of the canonical `Neo4jGraph` object or in `app/integrations/neo4j/client.py`, not as a parallel client type.
5. **Classic pipeline retirement is gated, not implicit**: legacy `smart_query / query / hybrid_query / dual_hybrid_query` are retired only after explicit acceptance gates pass.

## Phase Mapping

- Phase 0: Task 0
- Phase 1: Task 1
- Phase 2: Task 2
- Phase 3: Task 3
- Phase 4: Tasks 4-6
- Phase 5: Task 7

## File Map

### New graph_kb modules

- Create: `app/modules/graph_kb/schema_registry.py`
- Create: `app/modules/graph_kb/classifier_v2.py`
- Create: `app/modules/graph_kb/planner_v2.py`
- Create: `app/modules/graph_kb/query_strategy.py`
- Create: `app/modules/graph_kb/guardrail.py`
- Create: `app/modules/graph_kb/executor_v2.py`
- Create: `app/modules/graph_kb/canonicalizer.py`
- Create: `app/modules/graph_kb/direct_renderer.py`
- Create: `app/modules/graph_kb/rag_adapter.py`

### Existing graph_kb modules to preserve/extend

- Modify: `app/modules/graph_kb/models.py`
- Modify: `app/modules/graph_kb/service.py`
- Modify: `app/modules/graph_kb/client.py`
- Modify: `app/modules/graph_kb/__init__.py`
- Modify: `app/integrations/neo4j/client.py`

### Route and QA pipeline integration

- Modify: `app/routers/qa.py`
- Modify: `app/core/config.py`
- Modify: `app/modules/qa_kb/models.py`
- Modify: `app/modules/qa_kb/service.py`
- Modify: `app/modules/qa_kb/stages/planning.py`
- Modify: `app/modules/qa_kb/stages/retrieval.py`
- Modify: `app/modules/qa_kb/stages/synthesis.py`
- Modify: `app/modules/qa_kb/orchestrators/generation.py`
- Modify: `app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `app/modules/generation_pipeline/stage1_planning.py`
- Modify: `app/modules/generation_pipeline/stage2_retrieval.py`
- Modify: `app/modules/generation_pipeline/synthesis_streaming.py`

### Cache and rollout integration

- Modify: `app/modules/qa_cache/stage1_cache.py`
- Modify: `app/modules/qa_cache/stage2_cache.py`
- Create: `docs/graph_kb_legacy_capability_inventory.md`
- Create: `docs/graph_kb_classic_pipeline_retirement.md`

### Tests

- Create: `tests/test_graph_kb_models.py`
- Create: `tests/test_graph_kb_schema_registry.py`
- Create: `tests/test_graph_kb_classifier_v2.py`
- Create: `tests/test_graph_kb_planner_v2.py`
- Create: `tests/test_graph_kb_guardrail.py`
- Create: `tests/test_graph_kb_executor_v2.py`
- Create: `tests/test_graph_kb_canonicalizer.py`
- Create: `tests/test_graph_kb_rag_adapter.py`
- Modify: `tests/test_graph_kb_service.py`
- Modify: `tests/test_graph_kb_client.py`
- Modify: `tests/test_fastqa_kb_graph_integration.py`
- Modify: `tests/test_generation_driven_rag_init.py`
- Modify: `tests/test_generation_stage1_planning.py`
- Modify: `tests/test_generation_stage2_retrieval.py`
- Modify: `tests/test_generation_stage4_synthesis.py`
- Modify: `tests/test_stage4_evidence_formatting.py`
- Modify: `tests/test_qa_kb_context_usage.py`
- Modify: `tests/test_qa_kb_models.py`
- Modify: `tests/test_qa_kb_service.py`
- Modify: `tests/test_qa_generation_orchestrator.py`
- Modify: `tests/test_qa_kb_service_runtime.py`
- Modify: `tests/test_qa_cache.py`
- Modify: `tests/test_qa_cache_stage1.py`
- Modify: `tests/test_qa_cache_stage2.py`

## Task 0: Capability Inventory & Migration

**Files:**
- Create: `docs/graph_kb_legacy_capability_inventory.md`
- Reference: `docs/legacy_kg_qa_pipeline.md`
- Reference: `docs/legacy_kg_qa_routing.md`
- Reference: legacy `main.py` containing `MaterialScienceAgent` if available outside the current worktree
- Reference: legacy `commander_agent.py` containing `CommanderAgent` if available outside the current worktree
- Reference: `app/integrations/neo4j/client.py`
- Reference: `app/modules/graph_kb/client.py`

- [ ] **Step 1: Locate and read the legacy source files before touching V2 design**

Run: `find .. -name 'commander_agent.py' -o -name 'main.py' | sed -n '1,80p'`
Expected: Either the legacy source files are found, or the implementer records that the current worktree only has the two legacy docs and must inventory from docs plus any external legacy checkout

- [ ] **Step 2: Write a migration inventory document with explicit `MIGRATE` / `REPLACE` tags**

The inventory must include at least these rows:

- `CommanderAgent.analyze_question` rule chain
- `HybridQueryAgent.is_hybrid_question`
- legacy precise keyword + numeric attribute routing
- legacy community keyword branch and its V2 mapping
- legacy semantic keyword priority
- legacy graph non-numeric attribute + enumeration routing
- legacy numeric-attribute-only `precise` routing
- legacy entity keyword fallback
- legacy `query` sequence: `_generate_cypher_query -> _validate_cypher_query -> _execute_cypher_query -> _synthesize_answer`
- legacy `hybrid_query` / `dual_hybrid_query` orchestration semantics
- legacy DOI direct-read semantics (`query_pdf_directly`) as a compatibility boundary
- current fastQA `graph_kb/client.py` template planner and execution helpers
- Neo4j connection choice:
  `app/integrations/neo4j/client.py -> bootstrap_neo4j() -> Neo4jGraph` = `MIGRATE`
  introducing a second parallel fastQA connection client = `REPLACE / do not add`

Use a table like:

```markdown
| Capability | Source | Status | Plan |
| --- | --- | --- | --- |
| Commander precise/hybrid/semantic rule order | commander_agent.py | MIGRATE | Wrap into classifier_v2 parity layer |
| Legacy smart_query monolithic entrypoint | main.py | REPLACE | Replace with router + qa_kb integration |
```

- [ ] **Step 3: Verify the inventory explicitly captures the Commander route chain and Neo4j client choice**

Run: `rg -n "CommanderAgent|MaterialScienceAgent|MIGRATE|REPLACE|Neo4jGraph|smart_query|query|hybrid_query|dual_hybrid_query" docs/graph_kb_legacy_capability_inventory.md`
Expected: The inventory contains route-chain rows, query-execution rows, and a single explicit Neo4j client decision

- [ ] **Step 4: Commit**

```bash
git add docs/graph_kb_legacy_capability_inventory.md
git commit -m "docs: add graph kb legacy capability inventory"
```

## Task 1: Add V2 Contracts and Schema Registry

**Files:**
- Create: `app/modules/graph_kb/schema_registry.py`
- Modify: `app/modules/graph_kb/models.py`
- Modify: `app/modules/graph_kb/__init__.py`
- Create: `tests/test_graph_kb_models.py`
- Create: `tests/test_graph_kb_schema_registry.py`

- [ ] **Step 1: Write failing tests for the new V2 contracts**

```python
def test_graph_rag_payload_has_stable_cache_fingerprint():
    payload = GraphRagPayload(
        stage1_context_block="doi:10.1000/test",
        stage2_doi_candidates=["10.1000/test"],
        stage2_constraints=[],
        stage2_entity_hints={},
        stage4_fact_block="fact block",
        cache_fingerprint="abc123",
    )
    assert payload.cache_fingerprint == "abc123"


def test_schema_registry_exposes_allowed_labels_and_field_specs():
    registry = build_default_schema_registry()
    summary = registry.summarize_for_planner(intent="doi_lookup")
    assert "doi" in summary.allowed_labels
    assert registry.get_field("paper.title") is not None
```

- [ ] **Step 2: Run the new model and registry tests to confirm they fail**

Run: `pytest tests/test_graph_kb_models.py tests/test_graph_kb_schema_registry.py -q`
Expected: FAIL with missing V2 models and missing schema registry implementation

- [ ] **Step 3: Implement the new data contracts in `models.py`**

```python
@dataclass(frozen=True)
class GraphRagPayload:
    stage1_context_block: str = ""
    stage2_doi_candidates: tuple[str, ...] = ()
    stage2_constraints: tuple[GraphConstraint, ...] = ()
    stage2_entity_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    stage4_fact_block: str = ""
    cache_fingerprint: str = "none"


@dataclass(frozen=True)
class GraphRoutingResult:
    mode: str
    direct_result: GraphKbExecutionResult | None = None
    rag_payload: GraphRagPayload | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4: Implement the default schema registry**

```python
def build_default_schema_registry() -> SchemaRegistry:
    return SchemaRegistry(
        fields={
            "paper.doi": LogicalFieldSpec(...),
            "paper.title": LogicalFieldSpec(...),
            "raw_material.name": LogicalFieldSpec(...),
            "process.method": LogicalFieldSpec(...),
        },
        allowed_labels=("doi", "title", "raw_materials", "process", "recipe", "equipment", "testing", "description"),
        allowed_relations=(...),
    )
```

- [ ] **Step 5: Re-run the contract and registry tests**

Run: `pytest tests/test_graph_kb_models.py tests/test_graph_kb_schema_registry.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/modules/graph_kb/models.py app/modules/graph_kb/schema_registry.py app/modules/graph_kb/__init__.py tests/test_graph_kb_models.py tests/test_graph_kb_schema_registry.py
git commit -m "feat: add graph kb v2 contracts and schema registry"
```

---

## Task 2: Build Commander-Compatible Classifier V2, Planner V2, and Query Strategy Selection

**Files:**
- Create: `app/modules/graph_kb/classifier_v2.py`
- Create: `app/modules/graph_kb/planner_v2.py`
- Create: `app/modules/graph_kb/query_strategy.py`
- Modify: `app/modules/graph_kb/client.py`
- Modify: `app/modules/graph_kb/service.py`
- Create: `tests/test_graph_kb_classifier_v2.py`
- Create: `tests/test_graph_kb_planner_v2.py`
- Modify: `tests/test_graph_kb_client.py`

- [ ] **Step 1: Write failing tests for Commander-parity routing and tri-state mapping**

```python
def test_classifier_v2_preserves_commander_numeric_precise_order():
    decision = classify_graph_question_v2("压实密度最高的LFP材料有哪些？", conversation_context={})
    assert decision.legacy_route == "precise"
    assert decision.mode in {"direct_answer", "graph_for_rag"}


def test_classifier_v2_preserves_legacy_community_branch_before_semantic_fallback():
    decision = classify_graph_question_v2("请分析该数据集里材料关系网络的机制关联", conversation_context={})
    assert decision.legacy_route == "community"
    assert decision.mode == "skip_graph"


def test_classifier_v2_preserves_semantic_keyword_priority_over_graph_enumeration():
    decision = classify_graph_question_v2("为什么 LFP 的循环性能更稳定？", conversation_context={})
    assert decision.legacy_route == "semantic"
    assert decision.mode == "graph_for_rag"


def test_classifier_v2_preserves_numeric_only_precise_route_before_entity_fallback():
    decision = classify_graph_question_v2("压实密度大于 2.4 的材料有哪些？", conversation_context={})
    assert decision.legacy_route == "precise"
    assert decision.diagnostics["matched_rule"] == "numeric_attribute_only"


def test_classifier_v2_preserves_entity_keyword_fallback():
    decision = classify_graph_question_v2("LFP 有哪些文献？", conversation_context={})
    assert decision.legacy_route == "precise"


def test_planner_v2_preserves_legacy_template_for_old_supported_queries():
    plan = build_graph_query_plan_v2(...)
    assert plan.strategy == "template"
    assert plan.legacy_template_id == "lookup_by_doi"
```

- [ ] **Step 2: Run the classifier/planner tests to confirm they fail**

Run: `pytest tests/test_graph_kb_classifier_v2.py tests/test_graph_kb_planner_v2.py tests/test_graph_kb_client.py -q`
Expected: FAIL with missing V2 classifier/planner/query strategy code

- [ ] **Step 3: Implement `classifier_v2.py` as a wrapper over the inventoried Commander rule chain**

```python
def classify_graph_question_v2(*, question: str, conversation_context: dict[str, Any] | None) -> SemanticDecision:
    if legacy_hybrid_rule(question):
        legacy_route = "hybrid"
    elif precise_keywords_and_numeric_attributes(question):
        legacy_route = "precise"
    elif community_keywords(question):
        legacy_route = "community"
    elif semantic_keywords(question):
        legacy_route = "semantic"
    elif graph_non_numeric_attributes_with_enumeration(question):
        legacy_route = "precise"
    elif numeric_attribute_only(question):
        legacy_route = "precise"
    elif entity_keywords(question):
        legacy_route = "precise"
    else:
        legacy_route = "semantic"

    return map_legacy_route_to_tri_state(
        legacy_route=legacy_route,
        question=question,
        conversation_context=conversation_context,
    )
```

Required mapping note:

- `legacy_route = "community"` maps to `skip_graph` in V2 until a real community-capability replacement exists, because the legacy runtime already degraded that branch to broad semantic search rather than graph execution
- `legacy_route = "precise"` may map to `direct_answer` or `graph_for_rag` depending on confidence and answerability
- `legacy_route in {"hybrid", "semantic"}` maps to `graph_for_rag` or `skip_graph` based on whether graph evidence is materially useful

- [ ] **Step 4: Implement planner and query strategy selection with legacy-template preservation and migration posture**

```python
def build_graph_query_plan_v2(...):
    if decision.mode == "skip_graph":
        return None
    if can_use_legacy_template(question):
        return GraphQueryPlanV2(strategy="template", legacy_template_id="lookup_by_doi", ...)
    if can_build_parametric_query(...):
        return GraphQueryPlanV2(strategy="parametric", ...)
    return GraphQueryPlanV2(strategy="llm_cypher", ...)
```

- [ ] **Step 5: Record any Commander rules intentionally not carried forward as `REPLACE` in the inventory**

Run: `rg -n "community|REPLACE" docs/graph_kb_legacy_capability_inventory.md`
Expected: Any legacy rule or branch not preserved verbatim is explicitly documented instead of silently dropped

- [ ] **Step 6: Keep old client/template functions callable as the fallback implementation**

Run: `pytest tests/test_graph_kb_client.py -q`
Expected: PASS with existing template behavior preserved

- [ ] **Step 7: Run the full Task 2 test set**

Run: `pytest tests/test_graph_kb_classifier_v2.py tests/test_graph_kb_planner_v2.py tests/test_graph_kb_client.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/modules/graph_kb/classifier_v2.py app/modules/graph_kb/planner_v2.py app/modules/graph_kb/query_strategy.py app/modules/graph_kb/client.py app/modules/graph_kb/service.py docs/graph_kb_legacy_capability_inventory.md tests/test_graph_kb_classifier_v2.py tests/test_graph_kb_planner_v2.py tests/test_graph_kb_client.py
git commit -m "feat: add graph kb v2 classifier and planner"
```

---

## Task 3: Add Guardrail, Safe Executor, Canonicalizer, and Direct Renderer

**Files:**
- Modify: `app/integrations/neo4j/client.py`
- Create: `app/modules/graph_kb/guardrail.py`
- Create: `app/modules/graph_kb/executor_v2.py`
- Create: `app/modules/graph_kb/canonicalizer.py`
- Create: `app/modules/graph_kb/direct_renderer.py`
- Modify: `app/modules/graph_kb/service.py`
- Create: `tests/test_graph_kb_guardrail.py`
- Create: `tests/test_graph_kb_executor_v2.py`
- Create: `tests/test_graph_kb_canonicalizer.py`
- Modify: `tests/test_graph_kb_service.py`

- [ ] **Step 1: Write failing tests for guardrail rejection, path fallback, canonical facts, direct rendering, and single-client execution**

```python
def test_guardrail_rejects_write_cypher():
    result = inspect_cypher("MATCH (n) DELETE n", registry=build_default_schema_registry())
    assert result.verdict == "reject"


def test_executor_tries_reverse_path_when_forward_path_is_empty():
    result = execute_prepared_query(..., max_path_attempts=2)
    assert result.trace.matched_path == "name.reverse"


def test_canonicalizer_extracts_fact_rows_into_graph_evidence_bundle():
    bundle = canonicalize_graph_rows(...)
    assert bundle.doi_candidates == ("10.1000/test",)
    assert bundle.facts


def test_executor_uses_bootstrapped_neo4jgraph_instead_of_second_client():
    neo4j_client = bootstrap_neo4j(...)
    result = execute_prepared_query(..., neo4j_client=neo4j_client)
    assert result.trace.strategy in {"template", "parametric", "llm_cypher"}
```

- [ ] **Step 2: Run the new tests**

Run: `pytest tests/test_graph_kb_guardrail.py tests/test_graph_kb_executor_v2.py tests/test_graph_kb_canonicalizer.py tests/test_graph_kb_service.py -q`
Expected: FAIL with missing guardrail/executor/canonicalizer/direct-renderer behaviors

- [ ] **Step 3: Implement the guardrail with read-only, whitelist, and limit checks**

```python
def inspect_cypher(*, cypher: str, registry: SchemaRegistry) -> GuardrailResult:
    if contains_write_clause(cypher) or contains_unapproved_procedure(cypher):
        return GuardrailResult(verdict="reject", issues=["write_clause"])
    return ensure_limit_and_whitelist(cypher, registry)
```

- [ ] **Step 4: Implement executor path fallback and canonicalizer extraction on top of the canonical `Neo4jGraph` bootstrap**

```python
def execute_prepared_query(...):
    for path in candidate_paths[:max_path_attempts]:
        rows = query_once(...)
        if rows:
            return RawExecutionResult(rows=rows, trace=ExecutionTrace(matched_path=path.path_id, ...))
    return RawExecutionResult(rows=[], trace=ExecutionTrace(fallback_reason="empty_result", ...))
```

- [ ] **Step 5: Update `app/integrations/neo4j/client.py` only if V2 needs helper behavior, but keep it as the sole connected client**

```python
def bootstrap_neo4j(...):
    graph = Neo4jGraph(...)
    return Neo4jBootstrapResult(graph=graph, ...)
```

Expected outcome:

- `app/integrations/neo4j/client.py` remains the only connection/bootstrap layer
- `app/modules/graph_kb/client.py` and `executor_v2.py` use `neo4j_client.graph` / `neo4j_client.graph._driver`
- no new `FastQaNeo4jClient` or equivalent parallel client type is introduced

- [ ] **Step 6: Implement direct rendering only for high-confidence low-ambiguity plans**

```python
def render_direct_answer(*, decision, plan, bundle):
    if decision.mode != "direct_answer" or not bundle.direct_answerable:
        return DirectAnswerResult(handled=False, ...)
    return render_from_slots(plan.legacy_template_id, bundle.render_slots)
```

- [ ] **Step 7: Run the Task 3 tests**

Run: `pytest tests/test_graph_kb_guardrail.py tests/test_graph_kb_executor_v2.py tests/test_graph_kb_canonicalizer.py tests/test_graph_kb_service.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/integrations/neo4j/client.py app/modules/graph_kb/guardrail.py app/modules/graph_kb/executor_v2.py app/modules/graph_kb/canonicalizer.py app/modules/graph_kb/direct_renderer.py app/modules/graph_kb/service.py tests/test_graph_kb_guardrail.py tests/test_graph_kb_executor_v2.py tests/test_graph_kb_canonicalizer.py tests/test_graph_kb_service.py
git commit -m "feat: add guarded graph execution and canonical rendering"
```

---

## Task 4: Integrate Graph V2 into `kb_qa` Routing and Feature Flags

**Files:**
- Modify: `app/routers/qa.py`
- Modify: `app/core/config.py`
- Modify: `app/modules/graph_kb/service.py`
- Modify: `tests/test_fastqa_kb_graph_integration.py`
- Modify: `tests/test_graph_kb_runtime.py`

- [ ] **Step 1: Write failing route tests for `direct_answer` and `skip_graph` routing**

```python
def test_sync_ask_uses_graph_direct_answer_when_mode_is_direct_answer():
    ...
    assert payload["query_mode"] == "graph_kb"


def test_sync_ask_skips_graph_v2_when_feature_flag_disabled():
    ...
    assert payload["query_mode"] == "生成驱动检索"


def test_sync_ask_goes_straight_to_generation_when_mode_is_skip_graph():
    ...
    assert payload["query_mode"] == "生成驱动检索"
```

- [ ] **Step 2: Run the route and runtime tests**

Run: `pytest tests/test_fastqa_kb_graph_integration.py tests/test_graph_kb_runtime.py -q`
Expected: FAIL because `qa.py` still only understands `GraphKbExecutionResult(handled=...)`

- [ ] **Step 3: Add V2 config flags and route selection logic**

```python
if settings.graph_kb_enabled and settings.graph_kb_v2_enabled:
    routing_result = run_graph_kb_v2(...)
    if routing_result.mode == "direct_answer":
        yield from _iter_graph_kb_events(...)
        return
```

- [ ] **Step 4: Keep `skip_graph` aligned with the approved spec**

```python
if routing_result.mode == "skip_graph":
    yield from qa_kb_service.iter_answer_events(...)
    return
```

- [ ] **Step 5: Re-run route tests**

Run: `pytest tests/test_fastqa_kb_graph_integration.py tests/test_graph_kb_runtime.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routers/qa.py app/core/config.py app/modules/graph_kb/service.py tests/test_fastqa_kb_graph_integration.py tests/test_graph_kb_runtime.py
git commit -m "feat: integrate graph kb v2 routing into kb_qa"
```

---

## Task 5: Implement Graph-to-RAG Adapter and Thread `graph_evidence` End-to-End

**Files:**
- Create: `app/modules/graph_kb/rag_adapter.py`
- Modify: `app/modules/qa_kb/models.py`
- Modify: `app/modules/qa_kb/service.py`
- Modify: `app/routers/qa.py`
- Modify: `app/modules/qa_kb/stages/planning.py`
- Modify: `app/modules/qa_kb/stages/retrieval.py`
- Modify: `app/modules/qa_kb/stages/synthesis.py`
- Modify: `app/modules/qa_kb/orchestrators/generation.py`
- Modify: `app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `app/modules/generation_pipeline/stage1_planning.py`
- Modify: `app/modules/generation_pipeline/stage2_retrieval.py`
- Modify: `app/modules/generation_pipeline/synthesis_streaming.py`
- Create: `tests/test_graph_kb_rag_adapter.py`
- Modify: `tests/test_fastqa_kb_graph_integration.py`
- Modify: `tests/test_generation_driven_rag_init.py`
- Modify: `tests/test_generation_stage1_planning.py`
- Modify: `tests/test_generation_stage2_retrieval.py`
- Modify: `tests/test_generation_stage4_synthesis.py`
- Modify: `tests/test_stage4_evidence_formatting.py`
- Modify: `tests/test_qa_kb_context_usage.py`
- Modify: `tests/test_qa_kb_models.py`
- Modify: `tests/test_qa_kb_service.py`
- Modify: `tests/test_qa_generation_orchestrator.py`
- Modify: `tests/test_qa_kb_service_runtime.py`

- [ ] **Step 1: Write failing tests for `GraphEvidenceBundle -> GraphRagPayload` and top-level `graph_evidence` plumbing**

```python
def test_rag_adapter_builds_cache_fingerprint_and_fact_blocks():
    payload = build_graph_rag_payload(bundle=sample_bundle(), ...)
    assert payload.cache_fingerprint
    assert payload.stage2_doi_candidates
    assert "structured fact" in payload.stage4_fact_block


def test_qakb_request_carries_graph_evidence_without_mutating_conversation_context():
    request = QaKbRequest(question="q", graph_evidence=GraphRagPayload(...))
    assert request.graph_evidence is not None


def test_stage1_runtime_receives_graph_context_argument():
    runtime = RecordingRuntime()
    service.run_generation_pipeline(...)
    assert runtime.stage1_graph_context == "doi:10.1000/test"


def test_stage4_runtime_receives_graph_fact_block():
    runtime = RecordingRuntime()
    service.run_generation_pipeline(...)
    assert "structured graph facts" in runtime.stage4_graph_fact_block


def test_sync_ask_passes_graph_payload_into_generation_when_mode_is_graph_for_rag():
    ...
    assert captured_request.graph_evidence is not None
```

- [ ] **Step 2: Run the QA pipeline tests**

Run: `pytest tests/test_graph_kb_rag_adapter.py tests/test_fastqa_kb_graph_integration.py tests/test_generation_driven_rag_init.py tests/test_generation_stage1_planning.py tests/test_generation_stage2_retrieval.py tests/test_generation_stage4_synthesis.py tests/test_stage4_evidence_formatting.py tests/test_qa_kb_context_usage.py tests/test_qa_kb_models.py tests/test_qa_kb_service.py tests/test_qa_generation_orchestrator.py tests/test_qa_kb_service_runtime.py -q`
Expected: FAIL because `rag_adapter.py`, `QaKbRequest.graph_evidence`, route forwarding, stage wrappers, and runtime facade do not yet support graph evidence

- [ ] **Step 3: Implement `rag_adapter.py` and produce stable `GraphRagPayload` objects**

```python
def build_graph_rag_payload(*, decision, plan, bundle) -> GraphRagPayload:
    return GraphRagPayload(
        stage1_context_block=render_stage1_context(bundle),
        stage2_doi_candidates=dedupe(bundle.doi_candidates)[:10],
        stage2_constraints=tuple(bundle.constraints_for_rag),
        stage2_entity_hints=render_entity_hints(bundle),
        stage4_fact_block=render_fact_block(bundle.facts),
        cache_fingerprint=hash_payload(...),
    )
```

- [ ] **Step 4: Add `graph_evidence` to `QaKbRequest` and forward it through `qa.py`, `QaKbService`, and the orchestrator**

```python
@dataclass(frozen=True)
class QaKbRequest:
    ...
    graph_evidence: GraphRagPayload | None = None


yield from self.iter_generation_answer_events(
    ...,
    graph_evidence=request.graph_evidence,
)
```

- [ ] **Step 5: Extend stage wrappers and the generation runtime facade signatures**

```python
def run(..., graph_evidence: GraphRagPayload | None = None):
    stage1_result = self._run_stage1(..., graph_evidence=graph_evidence)
    stage2_result = self._run_stage2(..., graph_evidence=graph_evidence)
    stage4_output = self.stage4.stream(..., graph_fact_block=graph_evidence.stage4_fact_block if graph_evidence else "")
```

- [ ] **Step 6: Update Stage1/Stage2/Stage4 implementation functions to consume the new payloads**

```python
user_content = f"{graph_context}\n\n{user_content}" if graph_context else user_content
constrained_query = merge_graph_hints_into_retrieval(...)
prompt = build_stage4_prompt(..., graph_fact_block=graph_fact_block)
```

- [ ] **Step 7: Gate graph-for-rag injection behind `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED`**

```python
if not settings.graph_kb_rag_injection_enabled:
    routing_result = GraphRoutingResult(mode="skip_graph", ...)
```

- [ ] **Step 8: Re-run the QA pipeline test set**

Run: `pytest tests/test_graph_kb_rag_adapter.py tests/test_fastqa_kb_graph_integration.py tests/test_generation_driven_rag_init.py tests/test_generation_stage1_planning.py tests/test_generation_stage2_retrieval.py tests/test_generation_stage4_synthesis.py tests/test_stage4_evidence_formatting.py tests/test_qa_kb_context_usage.py tests/test_qa_kb_models.py tests/test_qa_kb_service.py tests/test_qa_generation_orchestrator.py tests/test_qa_kb_service_runtime.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/modules/graph_kb/rag_adapter.py app/routers/qa.py app/modules/qa_kb/models.py app/modules/qa_kb/service.py app/modules/qa_kb/stages/planning.py app/modules/qa_kb/stages/retrieval.py app/modules/qa_kb/stages/synthesis.py app/modules/qa_kb/orchestrators/generation.py app/modules/generation_pipeline/generation_driven_rag_facade.py app/modules/generation_pipeline/stage1_planning.py app/modules/generation_pipeline/stage2_retrieval.py app/modules/generation_pipeline/synthesis_streaming.py tests/test_graph_kb_rag_adapter.py tests/test_fastqa_kb_graph_integration.py tests/test_generation_driven_rag_init.py tests/test_generation_stage1_planning.py tests/test_generation_stage2_retrieval.py tests/test_generation_stage4_synthesis.py tests/test_stage4_evidence_formatting.py tests/test_qa_kb_context_usage.py tests/test_qa_kb_models.py tests/test_qa_kb_service.py tests/test_qa_generation_orchestrator.py tests/test_qa_kb_service_runtime.py
git commit -m "feat: thread graph evidence through qa generation pipeline"
```

---

## Task 6: Add Cache Isolation, `graph_seeded_doi_fallback`, and Final Regression Coverage

**Files:**
- Modify: `app/modules/qa_cache/stage1_cache.py`
- Modify: `app/modules/qa_cache/stage2_cache.py`
- Modify: `app/modules/qa_kb/orchestrators/generation.py`
- Modify: `tests/test_qa_cache_stage1.py`
- Modify: `tests/test_qa_cache_stage2.py`
- Modify: `tests/test_qa_cache.py`
- Modify: `tests/test_qa_generation_orchestrator.py`
- Modify: `tests/test_fastqa_kb_graph_integration.py`

- [ ] **Step 1: Write failing cache and fallback tests**

```python
def test_stage1_cache_key_changes_when_graph_payload_changes():
    first = build_stage1_cache_key(..., graph_cache_fingerprint="none")
    second = build_stage1_cache_key(..., graph_cache_fingerprint="graph:abc")
    assert first != second


def test_stage2_cache_key_changes_when_graph_doi_candidates_change():
    first = build_stage2_cache_key(..., graph_cache_fingerprint="graph:a")
    second = build_stage2_cache_key(..., graph_cache_fingerprint="graph:b")
    assert first != second


def test_orchestrator_uses_graph_seeded_doi_fallback_when_stage2_has_no_doi():
    result = orchestrator.run(..., graph_evidence=GraphRagPayload(stage2_doi_candidates=("10.1000/test",), ...))
    assert result.raw["doi_source"] == "graph_seeded"
```

- [ ] **Step 2: Run cache and fallback tests**

Run: `pytest tests/test_qa_cache.py tests/test_qa_cache_stage1.py tests/test_qa_cache_stage2.py tests/test_qa_generation_orchestrator.py tests/test_fastqa_kb_graph_integration.py -q`
Expected: FAIL because cache keys ignore graph input and the orchestrator still exits early when Stage2 yields no DOI

- [ ] **Step 3: Add graph-aware cache key material and versioning**

```python
def build_stage1_cache_key(..., graph_cache_fingerprint: str = "none"):
    return redis_service.key_factory.cache(..., graph_cache_fingerprint)


def build_stage2_cache_key(..., graph_cache_fingerprint: str = "none"):
    return redis_service.key_factory.cache(..., graph_cache_fingerprint)
```

- [ ] **Step 4: Implement `graph_seeded_doi_fallback` in the orchestrator**

```python
if not dois and graph_evidence and graph_evidence.stage2_doi_candidates:
    dois = list(dedupe_preserve_order(graph_evidence.stage2_doi_candidates))[:top_n]
    doi_source = "graph_seeded"
```

- [ ] **Step 5: Run the cache/fallback tests again**

Run: `pytest tests/test_qa_cache.py tests/test_qa_cache_stage1.py tests/test_qa_cache_stage2.py tests/test_qa_generation_orchestrator.py tests/test_fastqa_kb_graph_integration.py -q`
Expected: PASS

- [ ] **Step 6: Run the focused end-to-end regression suite**

Run: `pytest tests/test_graph_kb_models.py tests/test_graph_kb_schema_registry.py tests/test_graph_kb_classifier_v2.py tests/test_graph_kb_planner_v2.py tests/test_graph_kb_guardrail.py tests/test_graph_kb_executor_v2.py tests/test_graph_kb_canonicalizer.py tests/test_graph_kb_rag_adapter.py tests/test_graph_kb_service.py tests/test_graph_kb_client.py tests/test_fastqa_kb_graph_integration.py tests/test_generation_driven_rag_init.py tests/test_generation_stage1_planning.py tests/test_generation_stage2_retrieval.py tests/test_generation_stage4_synthesis.py tests/test_stage4_evidence_formatting.py tests/test_qa_kb_context_usage.py tests/test_qa_kb_models.py tests/test_qa_kb_service.py tests/test_qa_generation_orchestrator.py tests/test_qa_kb_service_runtime.py tests/test_qa_cache.py tests/test_qa_cache_stage1.py tests/test_qa_cache_stage2.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/modules/qa_cache/stage1_cache.py app/modules/qa_cache/stage2_cache.py app/modules/qa_kb/orchestrators/generation.py tests/test_qa_cache.py tests/test_qa_cache_stage1.py tests/test_qa_cache_stage2.py tests/test_qa_generation_orchestrator.py tests/test_fastqa_kb_graph_integration.py
git commit -m "feat: add graph aware cache keys and seeded doi fallback"
```

---

## Task 7: Production Hardening, Observability, and Classic Pipeline Retirement Acceptance

**Files:**
- Create: `docs/graph_kb_classic_pipeline_retirement.md`
- Modify: `app/routers/qa.py`
- Modify: `app/modules/graph_kb/service.py`
- Modify: `app/services/file_route_service.py`
- Modify: `tests/test_fastqa_kb_graph_integration.py`

- [ ] **Step 1: Write failing tests for production metadata and migration diagnostics**

```python
def test_graph_kb_v2_metadata_exposes_pipeline_version_and_legacy_route_family():
    ...
    assert payload["metadata"]["graph_pipeline_version"] == "v2"
    assert payload["metadata"]["legacy_route_family"] in {"precise", "hybrid", "community", "semantic"}


def test_graph_kb_v2_metadata_exposes_neo4j_client_choice():
    ...
    assert payload["metadata"]["neo4j_client"] == "neo4jgraph"
```

- [ ] **Step 2: Run the hardening tests**

Run: `pytest tests/test_fastqa_kb_graph_integration.py -q`
Expected: FAIL because V2 metadata/diagnostics do not yet expose rollout and client-selection fields

- [ ] **Step 3: Add production diagnostics and observability fields**

Required metadata/log fields:

- `graph_pipeline_version = "v2"`
- `legacy_route_family`
- `tri_state_mode`
- `neo4j_client = "neo4jgraph"`
- `doi_source = "retrieval" | "graph_seeded" | "none"`
- `legacy_template_fallback_used`

- [ ] **Step 4: Write the classic-pipeline retirement acceptance document**

The document must include a table for at least:

- `MaterialScienceAgent.smart_query`
- `MaterialScienceAgent.query`
- `MaterialScienceAgent.hybrid_query`
- `MaterialScienceAgent.dual_hybrid_query`
- `MaterialScienceAgent.query_pdf_directly`
- `CommanderAgent.analyze_question`

For each row, include:

- current owner
- V2 replacement path
- parity test set
- acceptance gate
- rollout phase: `shadow`, `default-on`, `disabled-but-retained`, `eligible-for-removal`

The document must explicitly state:

1. classic methods are **not** deleted in the same change that ships V2
2. `query` / `hybrid_query` / `dual_hybrid_query` are retired only after parity and shadow acceptance
3. `smart_query` remains as a compatibility reference until all delegated paths have replacement coverage
4. `query_pdf_directly` has its own keep/retire decision and is not implicitly deleted with graph routing

- [ ] **Step 5: Verify the retirement document contains the Phase 5 subgoal**

Run: `rg -n "Classic Pipeline Retirement Acceptance|smart_query|query|hybrid_query|dual_hybrid_query|query_pdf_directly|shadow|default-on|eligible-for-removal" docs/graph_kb_classic_pipeline_retirement.md`
Expected: The document contains explicit migration gates and a gradual retirement plan instead of direct deletion

- [ ] **Step 6: Re-run the hardening tests**

Run: `pytest tests/test_fastqa_kb_graph_integration.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add docs/graph_kb_classic_pipeline_retirement.md app/routers/qa.py app/modules/graph_kb/service.py app/services/file_route_service.py tests/test_fastqa_kb_graph_integration.py
git commit -m "docs: add classic pipeline retirement acceptance plan"
```

---

## Final Verification Checklist

- [ ] Run: `pytest tests/test_graph_kb_models.py tests/test_graph_kb_schema_registry.py tests/test_graph_kb_classifier_v2.py tests/test_graph_kb_planner_v2.py tests/test_graph_kb_guardrail.py tests/test_graph_kb_executor_v2.py tests/test_graph_kb_canonicalizer.py tests/test_graph_kb_rag_adapter.py tests/test_graph_kb_service.py tests/test_graph_kb_client.py tests/test_fastqa_kb_graph_integration.py tests/test_generation_driven_rag_init.py tests/test_generation_stage1_planning.py tests/test_generation_stage2_retrieval.py tests/test_generation_stage4_synthesis.py tests/test_stage4_evidence_formatting.py tests/test_qa_kb_context_usage.py tests/test_qa_kb_models.py tests/test_qa_kb_service.py tests/test_qa_generation_orchestrator.py tests/test_qa_kb_service_runtime.py tests/test_qa_cache.py tests/test_qa_cache_stage1.py tests/test_qa_cache_stage2.py -q`
- [ ] Expected: all targeted graph_kb / qa_kb integration tests pass
- [ ] Verify legacy route behavior still works with `FASTQA_GRAPH_KB_V2_ENABLED=0`
- [ ] Verify `graph_for_rag` route still works with `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED=0` by falling back to the current generation path
- [ ] Verify cache invalidation works by changing `QA_STAGE1_GRAPH_CACHE_VERSION` or `QA_STAGE2_GRAPH_CACHE_VERSION`
- [ ] Verify `docs/graph_kb_legacy_capability_inventory.md` exists and every reused legacy capability is tagged `MIGRATE` or `REPLACE`
- [ ] Verify `docs/graph_kb_classic_pipeline_retirement.md` defines `Classic Pipeline Retirement Acceptance` gates for `smart_query / query / hybrid_query / dual_hybrid_query`
- [ ] Verify V2 runtime metadata identifies `neo4j_client = "neo4jgraph"` and no second connection client was introduced
