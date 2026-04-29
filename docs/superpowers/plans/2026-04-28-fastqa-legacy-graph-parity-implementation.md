# fastQA Legacy Graph Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the legacy four-route graph QA experience inside the refactored fastQA `kb_qa` pipeline while preserving the existing gateway route semantics and vector/PDF/MD RAG behavior.

**Architecture:** Gateway continues to decide only public file-vs-KB routes. fastQA owns graph route family classification (`precise`, `semantic`, `hybrid`, `community`), prepared Neo4j execution, graph direct rendering, and graph-to-RAG payload injection. Graph evidence remains additive: direct answers are allowed only for bounded safe graph facts, while broad, hybrid, community, weak, or source-dependent answers flow through the generation-driven RAG pipeline.

**Tech Stack:** Python 3, FastAPI, Neo4j prepared Cypher templates, dataclass contracts, pytest, existing fastQA generation pipeline, existing Chroma/PDF/MD retrieval integration.

---

## Execution Constraints

- Do not commit during task execution unless the user explicitly asks for commits.
- Do not modify `CLAUDE.md` or `部门分级.xlsx`.
- Do not restore files from `archive/oldCode`; use old code only as logic reference.
- Do not add gateway-level graph routing. Gateway `hybrid_qa` remains file-mixing only.
- Do not add free-form LLM Cypher in this implementation.
- Do not make graph-only DOI frontend/source citations unless PDF/MD/vector evidence admits those DOI through the existing source evidence path.
- Prefer mocked graph clients for unit tests. Live Neo4j smoke tests must be opt-in and skipped by default.
- If a required test is blocked by sandbox restrictions, rerun it with escalation instead of repeatedly retrying in the sandbox.

## Source Requirements

- Spec: `docs/graph/2026-04-28-fastqa-legacy-graph-parity-spec.md`
- Current graph modules: `fastQA/app/modules/graph_kb/`
- Current KB orchestration: `fastQA/app/modules/qa_kb/`
- Current generation pipeline: `fastQA/app/modules/generation_pipeline/`
- Current fastQA route integration: `fastQA/app/routers/qa.py`
- Gateway route behavior to preserve: `gateway/app/services/route_decision.py`, `gateway/app/services/route_classifier.py`

## File Responsibility Map

Modify these files only as needed:

- `fastQA/app/modules/graph_kb/models.py`: graph route/result dataclasses and additive fields.
- `fastQA/app/modules/graph_kb/metadata.py`: canonical metadata keys and compatibility aliases.
- `fastQA/app/modules/graph_kb/slots.py`: deterministic slot extraction for graph fields and route signals.
- `fastQA/app/modules/graph_kb/classifier_v2.py`: four-route classifier and tri-state mode selection.
- `fastQA/app/modules/graph_kb/schema_registry.py`: observed graph schema support matrix and direct/RAG eligibility metadata.
- `fastQA/app/modules/graph_kb/query_strategy.py`: strategy constants or helpers if current module needs expansion.
- `fastQA/app/modules/graph_kb/query_templates.py`: allowlisted prepared Cypher paths.
- `fastQA/app/modules/graph_kb/planner_v2.py`: route-specific intent and query path planning.
- `fastQA/app/modules/graph_kb/guardrail.py`: Cypher safety validation.
- `fastQA/app/modules/graph_kb/executor_v2.py`: multiple prepared path execution and diagnostics.
- `fastQA/app/modules/graph_kb/value_parsers.py`: parser confidence and unit-family support.
- `fastQA/app/modules/graph_kb/doi_quality.py`: DOI quality policy if current checks need tightening.
- `fastQA/app/modules/graph_kb/canonicalizer.py`: rows to `GraphEvidenceBundle`.
- `fastQA/app/modules/graph_kb/direct_renderer.py`: safe direct graph answers.
- `fastQA/app/modules/graph_kb/rag_adapter.py`: graph payload for Stage 1/2/4.
- `fastQA/app/modules/graph_kb/service.py`: graph V2 orchestration and downgrade/fallback behavior.
- `fastQA/app/modules/qa_kb/models.py`: graph evidence fields only if the payload contract expands.
- `fastQA/app/modules/qa_kb/orchestrators/generation.py`: graph DOI fallback and source provenance metadata.
- `fastQA/app/modules/qa_kb/stages/planning.py`: Stage 1 graph context forwarding if needed.
- `fastQA/app/modules/qa_kb/stages/retrieval.py`: Stage 2 graph evidence forwarding if needed.
- `fastQA/app/modules/qa_kb/stages/synthesis.py`: Stage 4 graph fact forwarding if needed.
- `fastQA/app/modules/generation_pipeline/stage1_planning.py`: Stage 1 prompt/runtime graph context handling if the underlying generation runtime does not already consume `graph_context`.
- `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`: graph hint query merging and graph-seeded DOI policy.
- `fastQA/app/modules/generation_pipeline/md_expansion.py`: graph-seeded DOI compatibility if missing.
- `fastQA/app/modules/generation_pipeline/pdf_pipeline.py`: source provenance support if needed.
- `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`: graph-fact vs source-evidence prompt and citation guardrails.
- `fastQA/app/routers/qa.py`: metadata exposure and graph-to-generation wiring.

Test files to create or expand:

- `fastQA/tests/test_graph_kb_slots.py`
- `fastQA/tests/test_graph_kb_classifier_v2.py`
- `fastQA/tests/test_graph_kb_models.py`
- `fastQA/tests/test_graph_kb_metadata.py`
- `fastQA/tests/test_graph_kb_schema_registry.py`
- `fastQA/tests/test_graph_kb_query_templates.py`
- `fastQA/tests/test_graph_kb_planner_v2.py`
- `fastQA/tests/test_graph_kb_guardrail.py`
- `fastQA/tests/test_graph_kb_executor_v2.py`
- `fastQA/tests/test_graph_kb_doi_quality.py`
- `fastQA/tests/test_graph_kb_value_parsers.py`
- `fastQA/tests/test_graph_kb_canonicalizer.py`
- `fastQA/tests/test_graph_kb_direct_renderer.py`
- `fastQA/tests/test_graph_kb_rag_adapter.py`
- `fastQA/tests/test_graph_kb_service.py`
- `fastQA/tests/test_fastqa_kb_graph_integration.py`
- `fastQA/tests/test_generation_stage1_planning.py`
- `fastQA/tests/test_generation_stage2_retrieval.py`
- `fastQA/tests/test_generation_md_expansion.py`
- `fastQA/tests/test_generation_pdf_pipeline.py`
- `fastQA/tests/test_generation_stage4_synthesis.py`
- `fastQA/tests/test_qa_generation_orchestrator.py`
- `fastQA/tests/test_qa_kb_service.py`
- `fastQA/tests/test_qa_routes_file_modes.py`
- `gateway/tests/test_route_decision.py`
- `gateway/tests/test_route_classifier.py`

Optional docs:

- `docs/graph/fastqa_graph_parity_live_smoke.md`: opt-in live Neo4j/API smoke procedure and sample questions.

## Target Contracts

### Route Contract

Every `kb_qa` graph attempt must resolve to:

- route family: `precise`, `semantic`, `hybrid`, or `community`;
- graph execution mode: `direct_answer`, `graph_for_rag`, or `skip_graph`;
- graph strategy: `template`, `v1_template`, `parametric`, `multi_stage`, `community`, or `skip`;
- explicit fallback reason when graph cannot produce direct or RAG evidence.

### Metadata Contract

Use canonical keys and keep current aliases during migration:

- `graph_route_family`
- `graph_execution_mode`
- `graph_strategy`
- `graph_intent`
- `graph_result_count`
- `graph_doi_candidates_count`
- `graph_filtered_doi_count`
- `graph_suspicious_doi_count`
- `graph_direct_answer_eligible`
- `graph_rag_injected`
- `graph_fallback_reason`
- `neo4j_client`

Compatibility aliases may remain:

- `knowledge_route_family`
- `legacy_route_family`
- `tri_state_mode`
- `graph_rag_injection_enabled`

### Direct Answer Contract

Direct graph answers are allowed only for:

- DOI metadata/profile;
- bounded list queries;
- exact count queries;
- bounded process/recipe/material field profiles;
- numeric ranking/filtering after parser confidence and unit compatibility pass;
- simple community representative/profile rows.

Direct graph answers are forbidden for:

- mechanism explanation;
- trends;
- broad comparison;
- community causal interpretation;
- graph rows with no trustworthy DOI/title support;
- numeric ranking/filtering with low parser confidence;
- graph-only source citations.

### RAG Contract

`GraphRagPayload` must remain explicit and cache-fingerprintable:

- `stage1_context_block`: concise route, intent, facts, constraints, and DOI count.
- `stage2_doi_candidates`: quality-filtered graph DOI candidates.
- `stage2_constraints`: structured constraints for retrieval query construction.
- `stage2_entity_hints`: title/material/raw-material/process/recipe/community hints.
- `stage4_fact_block`: graph facts clearly labeled as supplemental graph facts.
- `cache_fingerprint`: stable across equivalent graph payload content.

## Acceptance Matrix

Classifier acceptance examples:

| Question | Expected Route Family | Expected Mode |
| --- | --- | --- |
| `10.1021/jp1005692 这篇文献是什么？` | `precise` | `direct_answer` |
| `10.1021/jp1005692 这篇文献的实验条件是什么？` | `hybrid` | `graph_for_rag` |
| `列出使用蔗糖作为碳源的文献` | `precise` | `direct_answer` |
| `使用 glucose 的文献有多少篇？` | `precise` | `direct_answer` |
| `LiFePO4 的制备方法有哪些？` | `precise` | `graph_for_rag` |
| `放电容量超过150 mAh/g的LFP有哪些特点？` | `hybrid` | `graph_for_rag` |
| `压实密度最高的前10个样品，它们的碳源有什么规律？` | `hybrid` | `graph_for_rag` |
| `为什么碳包覆会影响倍率性能？` | `semantic` | `skip_graph` |
| `使用葡萄糖作为碳源的文献中，哪些工艺参数影响容量？` | `hybrid` | `graph_for_rag` |
| `LFP 的关系网络和机制关联是什么？` | `community` | `graph_for_rag` |
| `按社区总结 LFP 制备路线和性能关系` | `community` | `graph_for_rag` |

Execution fixture acceptance examples:

- valid DOI lookup rows render direct metadata.
- malformed DOI rows are excluded from direct references and downgrade safely.
- carbon source list under cap renders direct list.
- carbon source list over cap downgrades or states returned-count wording.
- exact count query renders exact count only from `count(DISTINCT ...)`.
- numeric parser low confidence downgrades to `graph_for_rag`.
- hybrid candidate rows expand into process/recipe facts and RAG DOI candidates.
- hybrid empty graph results fall back to semantic RAG with metadata.
- community rows with community IDs build labels and graph facts.
- community rows without IDs record `community_id_unavailable` and continue RAG.
- graph unavailable never fails the user request.

---

### Task 1: Baseline Verification And Non-Regression Harness

**Files:**
- Modify: no production files.
- Test: existing tests under `fastQA/tests/` and `gateway/tests/`.
- Optional Create: `docs/graph/fastqa_graph_parity_live_smoke.md`

- [ ] **Step 1: Capture baseline status**

Run:

```bash
git status --short
```

Expected: only known untracked files are present unless user has added more work. Do not touch unrelated files.

- [ ] **Step 2: Run focused current graph tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_classifier_v2.py fastQA/tests/test_graph_kb_query_templates.py fastQA/tests/test_graph_kb_service.py -q
```

Expected: current failures, if any, are recorded before implementation. If sandbox blocks the run, rerun with escalation.

- [ ] **Step 3: Run gateway route guard tests**

Run:

```bash
conda run -n agent pytest gateway/tests/test_route_decision.py gateway/tests/test_route_classifier.py -q
```

Expected: pass or record unrelated baseline failures. These tests protect the boundary that gateway does not become the graph router.

- [ ] **Step 4: Add or update live smoke documentation**

Create `docs/graph/fastqa_graph_parity_live_smoke.md` only if it does not exist. Include:

```md
# fastQA Graph Parity Live Smoke

This document is opt-in. Unit tests must not depend on live Neo4j.

## Preconditions

- Backend stack started with `bash scripts/start_all.sh`
- fastQA graph flags enabled
- Neo4j reachable from resource configuration
- Test account available

## Questions

- 10.1021/jp1005692 这篇文献是什么？
- 10.1021/jp1005692 这篇文献的实验条件是什么？
- 列出使用蔗糖作为碳源的文献
- 使用 glucose 的文献有多少篇？
- 放电容量超过150 mAh/g的LFP有哪些特点？
- LFP 的关系网络和机制关联是什么？
```

- [ ] **Step 5: Verify no business code changed in Task 1**

Run:

```bash
git diff --stat
```

Expected: either no diff or docs-only diff.

### Task 2: Metadata And Dataclass Contract

**Files:**
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Modify: `fastQA/app/modules/graph_kb/metadata.py`
- Modify: `fastQA/app/routers/qa.py`
- Test: `fastQA/tests/test_graph_kb_models.py`
- Test: `fastQA/tests/test_graph_kb_metadata.py`
- Test: `fastQA/tests/test_fastqa_kb_graph_integration.py`

- [ ] **Step 1: Write failing metadata tests**

Add tests requiring `build_graph_route_metadata()` to emit canonical keys and compatibility aliases.

Expected assertions:

```python
metadata = build_graph_route_metadata(
    route_family="hybrid",
    tri_state_mode="graph_for_rag",
    strategy="multi_stage",
    intent="hybrid_property_process",
    rag_injection_enabled=True,
)
assert metadata["graph_route_family"] == "hybrid"
assert metadata["graph_execution_mode"] == "graph_for_rag"
assert metadata["graph_rag_injected"] is True
assert metadata["knowledge_route_family"] == "hybrid"
assert metadata["legacy_route_family"] == "hybrid"
assert metadata["tri_state_mode"] == "graph_for_rag"
```

- [ ] **Step 2: Run metadata tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_metadata.py -q
```

Expected: fail on missing canonical keys before implementation.

- [ ] **Step 3: Implement canonical metadata aliases**

Update `fastQA/app/modules/graph_kb/metadata.py` so the function emits both canonical and current compatibility keys.

Implementation shape:

```python
payload = {
    "graph_pipeline_version": graph_pipeline_version,
    "graph_route_family": route,
    "knowledge_route_family": route,
    "legacy_route_family": route,
    "graph_execution_mode": tri_state_mode,
    "tri_state_mode": tri_state_mode,
    "graph_strategy": strategy,
    "graph_intent": intent,
    "graph_fallback_reason": fallback_reason,
    "doi_source": doi_source,
}
if rag_injection_enabled is not None:
    payload["graph_rag_injected"] = bool(rag_injection_enabled)
    payload["graph_rag_injection_enabled"] = bool(rag_injection_enabled)
```

- [ ] **Step 4: Preserve fastQA router metadata merging**

Update `_graph_v2_metadata()` in `fastQA/app/routers/qa.py` to pass through canonical values and keep existing aliases in `metadata`, `done`, and graph direct-answer events.

- [ ] **Step 5: Run metadata and integration tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_metadata.py fastQA/tests/test_fastqa_kb_graph_integration.py -q
```

Expected: pass.

### Task 3: Slot Extraction Coverage

**Files:**
- Modify: `fastQA/app/modules/graph_kb/slots.py`
- Modify: `fastQA/app/modules/graph_kb/models.py` only if new slot fields are needed.
- Test: `fastQA/tests/test_graph_kb_slots.py`

- [ ] **Step 1: Add table-driven slot tests**

Add a parameterized matrix covering DOI, aliases, recipe, process, numeric fields, ranking, units, and community signals.

Example cases:

```python
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("LiFePO4 的制备方法有哪些？", {"entities": {"lifepo4"}, "process_key": "method"}),
        ("lithium iron phosphate 使用 glucose 的文献有多少篇？", {"entities": {"lifepo4"}, "carbon_source": {"glucose"}, "count_signal": True}),
        ("放电容量超过150 mAh/g的LFP有哪些特点？", {"property_field": "discharge_capacity", "operator": ">", "threshold": 150, "unit": "mAh/g"}),
        ("压实密度最高的前10个样品", {"property_field": "compaction_density", "ranking": "top", "limit": 10}),
        ("碳含量为5%的样品有哪些？", {"recipe_key": "carbon_content", "operator": "=", "threshold": 5, "unit": "%"}),
        ("按社区总结 LFP 制备路线和性能关系", {"community_signal": True}),
    ],
)
def test_extract_graph_slots_matrix(question, expected):
    slots = extract_graph_slots(question)
    ...
```

- [ ] **Step 2: Run slot tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_slots.py -q
```

Expected: fail for unsupported aliases/fields.

- [ ] **Step 3: Expand alias dictionaries and keyword maps**

Add deterministic support for:

- material aliases: `lfp`, `lifepo4`, `li fe po4` if present, `磷酸铁锂`, `lithium iron phosphate`;
- numeric properties: discharge capacity, compaction density, tap density, conductivity, coulombic efficiency, cycling stability, particle size, surface area, energy density, power density;
- recipe fields: carbon source/content, dopant, doping elements, additives, ratios;
- process fields: method, process, calcination, sintering, drying, milling, atmosphere, pressure, time, temperature, process steps;
- community terms: relationship network, community, cluster, mechanism association.

- [ ] **Step 4: Preserve existing simple extraction behavior**

Check that existing tests for DOI, carbon source, and numeric capacity still pass.

- [ ] **Step 5: Run slot tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_slots.py -q
```

Expected: pass.

### Task 4: Four-Route Classifier Semantics

**Files:**
- Modify: `fastQA/app/modules/graph_kb/classifier_v2.py`
- Test: `fastQA/tests/test_graph_kb_classifier_v2.py`

- [ ] **Step 1: Replace or expand classifier matrix tests**

Add explicit tests for the acceptance matrix in this plan and the spec. The tests must assert both route family and mode.

Example:

```python
@pytest.mark.parametrize(
    ("question", "route", "mode"),
    [
        ("10.1021/jp1005692 这篇文献是什么？", "precise", "direct_answer"),
        ("10.1021/jp1005692 这篇文献的实验条件是什么？", "hybrid", "graph_for_rag"),
        ("为什么碳包覆会影响倍率性能？", "semantic", "skip_graph"),
        ("按社区总结 LFP 制备路线和性能关系", "community", "graph_for_rag"),
    ],
)
def test_classifier_v2_acceptance_matrix(question, route, mode):
    decision = classify_graph_question_v2(question=question, conversation_context={})
    assert decision.legacy_route == route
    assert decision.route_family == route
    assert decision.mode == mode
```

- [ ] **Step 2: Add downgrade tests**

Add tests for:

- file context downgrades direct answers to `graph_for_rag`;
- ambiguous follow-up downgrades to `graph_for_rag`;
- no-slot semantic questions skip graph;
- DOI + content analysis routes to `hybrid`.

- [ ] **Step 3: Run classifier tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_classifier_v2.py -q
```

Expected: fail on current differences.

- [ ] **Step 4: Implement rule order**

Update `_base_route_for_slots()` with this order:

1. DOI + content/experiment/process/capacity/detail analysis -> `hybrid`, `graph_for_rag`.
2. DOI metadata/profile -> `precise`, `direct_answer`.
3. Community signal -> `community`, `graph_for_rag`.
4. Structured filter/ranking + analysis -> `hybrid`, `graph_for_rag`.
5. Recipe/material list/count slots -> `precise`; classifier may choose `direct_answer` only for fixture-proven list/count families such as carbon-source list/count and DOI metadata.
6. Process/method enumerable questions such as `LiFePO4 的制备方法有哪些？` -> `precise`, `graph_for_rag` at classifier time. Any later safe direct process/profile answer must be an execution/renderer decision tested outside classifier semantics.
7. Numeric property without analysis -> `precise`, usually `graph_for_rag` until direct numeric promotion.
8. Semantic keywords without useful slots -> `semantic`, `skip_graph`.
9. Default -> `semantic`, `skip_graph`.

- [ ] **Step 5: Run classifier tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_classifier_v2.py -q
```

Expected: pass.

### Task 5: Schema Registry And Strategy Support Matrix

**Files:**
- Modify: `fastQA/app/modules/graph_kb/schema_registry.py`
- Modify: `fastQA/app/modules/graph_kb/query_strategy.py`
- Test: `fastQA/tests/test_graph_kb_schema_registry.py`
- Test: `fastQA/tests/test_graph_kb_planner_v2.py`

- [ ] **Step 1: Add schema support tests**

Assert that registry fields include:

- DOI metadata/context;
- raw materials;
- preparation method;
- process parameters;
- recipe carbon source/content/dopant/doping elements;
- discharge capacity;
- compaction density;
- tap density;
- conductivity;
- cycling stability;
- coulombic efficiency;
- community ID.

Expected field metadata:

```python
field = registry.get_field("recipe.carbon_source")
assert field.direct_answer_eligible is True
assert field.rag_eligible is True
assert field.relation_path == ("recipe", "carbon_source")
```

- [ ] **Step 2: Add strategy tests**

Expected planner strategies:

- DOI lookup -> `v1_template` or trusted `template`.
- carbon source list/count -> `v1_template`.
- numeric property -> `parametric`.
- hybrid property + analysis -> `multi_stage`.
- community -> `community`.
- semantic skip -> no plan.

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_schema_registry.py fastQA/tests/test_graph_kb_planner_v2.py -q
```

Expected: fail where registry/strategy lacks fields.

- [ ] **Step 4: Expand `LogicalFieldSpec` only if needed**

If existing fields are insufficient, add optional dataclass fields with defaults:

```python
display_name: str = ""
output_columns: tuple[str, ...] = ()
numeric_parse_supported: bool = False
support_tier: str = "graph_for_rag"
default_limit: int = 20
```

Keep defaults backward compatible.

- [ ] **Step 5: Populate registry conservatively**

Use the support tiers from the spec:

- direct-capable: DOI lookup/profile, raw material list/count, carbon source list/count, bounded preparation method list;
- graph_for_rag: DOI context, numeric fields before parser promotion, process parameters, dopant/doping elements, equipment/testing, community;
- deferred: morphology/surface area/energy density/power density unless verified.

- [ ] **Step 6: Implement strategy helpers if needed**

`query_strategy.py` should expose named strategy values or a small helper. Do not add behavior that duplicates planner logic unless tests need a reusable selector.

- [ ] **Step 7: Run schema and planner tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_schema_registry.py fastQA/tests/test_graph_kb_planner_v2.py -q
```

Expected: pass.

### Task 6: Prepared Query Template Coverage

**Files:**
- Modify: `fastQA/app/modules/graph_kb/query_templates.py`
- Modify: `fastQA/app/modules/graph_kb/planner_v2.py`
- Test: `fastQA/tests/test_graph_kb_query_templates.py`
- Test: `fastQA/tests/test_graph_kb_planner_v2.py`

- [ ] **Step 1: Add template tests by intent**

Test `build_v1_query_paths()` for:

- `lookup_by_doi`;
- `expand_doi_context`;
- `list_by_carbon_source`;
- `count_by_structured_field`;
- `list_by_process_method`;
- `numeric_property_query`;
- `hybrid_property_candidates`;
- `hybrid_expand_by_doi`;
- `community_find_by_term`;
- `community_profile`.

Each test must assert:

- non-empty candidate paths;
- expected `path_id`;
- expected params;
- expected output columns;
- direct eligibility only on safe templates;
- Cypher contains `$limit` and no user-interpolated string.

- [ ] **Step 2: Run template tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_query_templates.py fastQA/tests/test_graph_kb_planner_v2.py -q
```

Expected: fail for missing hybrid/community/process fields.

- [ ] **Step 3: Add precise query paths**

Implement or expand:

- DOI profile with title and bounded context;
- raw material list/count;
- carbon source list/count;
- preparation method list;
- process parameter evidence;
- recipe field evidence;
- numeric property evidence for supported fields.

Use `GraphQueryPath` and `_path()` helpers only. Keep all values parameterized.

- [ ] **Step 4: Protect deferred fields**

Do not add production query templates or planner paths for deferred fields unless a separate live/schema evidence task promotes them with tests.

Deferred fields for this implementation:

- morphology;
- surface area;
- energy density;
- power density.

Slot extraction may recognize these terms so classification can choose the right route family, but planner behavior must downgrade them to generic graph-for-RAG hints or skip graph. It must not generate unverified Neo4j paths for these fields.

- [ ] **Step 5: Add hybrid query paths**

Add query builders that support:

- candidate DOI/material rows from numeric property filters/rankings;
- expansion by DOI for process;
- expansion by DOI for recipe;
- expansion by DOI for performance values;
- expansion by DOI for title/material/raw materials.

Recommended intent names:

```python
"hybrid_property_candidates"
"hybrid_recipe_candidates"
"hybrid_expand_by_doi"
```

If one intent returns multiple paths, order paths from most specific to fallback.

- [ ] **Step 6: Add community query paths**

Add query builders that support:

- term -> community IDs;
- community representative DOI/title/material/method rows;
- community profile by community ID;
- community fallback term rows without pretending they are community evidence.

- [ ] **Step 7: Update planner intent selection**

Planner must return:

- `multi_stage` for hybrid;
- `community` for community;
- `parametric` for numeric property evidence;
- `v1_template` for deterministic list/count/profile templates;
- no plan for `skip_graph`.

- [ ] **Step 8: Run template and planner tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_query_templates.py fastQA/tests/test_graph_kb_planner_v2.py -q
```

Expected: pass.

### Task 7: Guardrail And Executor Hardening

**Files:**
- Modify: `fastQA/app/modules/graph_kb/guardrail.py`
- Modify: `fastQA/app/modules/graph_kb/executor_v2.py`
- Test: `fastQA/tests/test_graph_kb_guardrail.py`
- Test: `fastQA/tests/test_graph_kb_executor_v2.py`

- [ ] **Step 1: Add guardrail rejection tests**

Add tests for:

- `CREATE`, `MERGE`, `DELETE`, `DETACH DELETE`, `SET`, `REMOVE`, `DROP`, `LOAD CSV`, `CALL dbms`;
- semicolon multi-statement;
- line/block comments in generated Cypher;
- unknown labels;
- unknown relations;
- unbounded dynamic relationship patterns;
- missing limit normalization.

Example:

```python
result = inspect_cypher(cypher="MATCH (n) DETACH DELETE n", registry=registry)
assert result.verdict == "reject"
assert "write_clause" in result.issues
```

- [ ] **Step 2: Add executor path diagnostics tests**

Use fake graph clients to verify:

- graph unavailable -> `neo4j_unavailable`;
- first path empty, second path rows -> attempted paths include both;
- guardrail rejected all paths -> `guardrail_reject`;
- max rows cap applies;
- timeout argument is accepted and surfaced if runtime supports it.

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_guardrail.py fastQA/tests/test_graph_kb_executor_v2.py -q
```

Expected: fail for currently missing checks.

- [ ] **Step 4: Implement guardrail checks**

Use regex checks for:

```python
_WRITE_CLAUSE_RE = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV)\b", re.I)
_DISALLOWED_CALL_RE = re.compile(r"\bCALL\s+dbms\b", re.I)
_COMMENT_RE = re.compile(r"(--|//|/\*)")
_SEMICOLON_RE = re.compile(r";")
```

Keep read-only clauses allowed.

- [ ] **Step 5: Fix executor indentation and diagnostics if needed**

Ensure the returned `RawExecutionResult` for a matched path has correctly indented fields and complete trace:

```python
return RawExecutionResult(
    rows=rows[:max_rows],
    trace=ExecutionTrace(
        strategy=plan.strategy,
        matched_path=path_id,
        attempted_paths=tuple(attempted_paths),
        fallback_reason="",
        guardrail_verdict=inspected.verdict,
        neo4j_client="neo4jgraph",
    ),
)
```

- [ ] **Step 6: Run guardrail and executor tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_guardrail.py fastQA/tests/test_graph_kb_executor_v2.py -q
```

Expected: pass.

### Task 8: Canonicalizer, DOI Quality, Numeric Parser, And Constraints

**Files:**
- Modify: `fastQA/app/modules/graph_kb/canonicalizer.py`
- Modify: `fastQA/app/modules/graph_kb/doi_quality.py`
- Modify: `fastQA/app/modules/graph_kb/value_parsers.py`
- Modify: `fastQA/app/modules/graph_kb/models.py` if `GraphConstraint` needs richer metadata.
- Test: `fastQA/tests/test_graph_kb_canonicalizer.py`
- Test: `fastQA/tests/test_graph_kb_doi_quality.py`
- Test: `fastQA/tests/test_graph_kb_value_parsers.py`

- [ ] **Step 1: Add DOI quality tests**

Cover:

- valid DOI with slash;
- DOI with underscore that can normalize safely;
- trailing punctuation;
- truncated DOI;
- URL-corrupted DOI;
- empty/null DOI;
- suspicious DOI from material text.

- [ ] **Step 2: Add numeric parser tests**

Cover messy values:

```python
"155 mAh g-1"
"about 150 mAh/g"
"2.41 g cm-3"
"95% after 100 cycles"
"1.2e-3 S/cm"
"high capacity"
```

Expected:

- parsed numeric value only when confidence is high enough;
- original text preserved;
- unit family recorded or inferable;
- incompatible/no-unit rows are not direct-ranking eligible.

- [ ] **Step 3: Add canonicalizer tests**

Assert:

- DOI candidates contain only valid DOI;
- suspicious DOI count increments;
- original numeric value remains in row;
- parsed numeric fields are separate;
- entity hints include titles/materials/raw materials/carbon sources/process methods;
- `constraints_for_rag` includes structured filters for numeric and recipe hybrid plans;
- community labels are built only from community rows.

- [ ] **Step 4: Run tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_doi_quality.py fastQA/tests/test_graph_kb_value_parsers.py fastQA/tests/test_graph_kb_canonicalizer.py -q
```

Expected: fail for missing parser/constraint behavior.

- [ ] **Step 5: Implement conservative DOI quality and parser policy**

Do not over-normalize corrupted DOI. A DOI can be displayed or used for source lookup only when quality is `valid`.

Numeric parsers must return confidence. Direct numeric use later requires high confidence, compatible unit family, and non-empty DOI/title support.

- [ ] **Step 6: Build `constraints_for_rag`**

For graph-for-RAG bundles, add constraints such as:

```python
GraphConstraint(field="performance.discharge_capacity", operator=">", value=150)
GraphConstraint(field="recipe.carbon_source", operator="contains", value="glucose")
```

These are retrieval hints, not hard filters.

- [ ] **Step 7: Run canonicalizer/parser tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_doi_quality.py fastQA/tests/test_graph_kb_value_parsers.py fastQA/tests/test_graph_kb_canonicalizer.py -q
```

Expected: pass.

### Task 9: Direct Renderer Safety And User Output

**Files:**
- Modify: `fastQA/app/modules/graph_kb/direct_renderer.py`
- Test: `fastQA/tests/test_graph_kb_direct_renderer.py`

- [ ] **Step 1: Add direct renderer tests**

Cover:

- DOI lookup direct answer;
- DOI context profile direct answer;
- carbon source list under cap;
- carbon source count exact;
- process method bounded list;
- count row with exact `count`;
- list-only count wording is not exact total;
- numeric rows low parser confidence decline direct;
- numeric ranking high confidence direct only after policy passes;
- malformed DOI rows decline direct references;
- community representative profile direct only for simple profile/list intents.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_direct_renderer.py -q
```

Expected: fail where renderers are missing.

- [ ] **Step 3: Implement safe renderers**

Keep outputs factual and bounded:

- display DOI/title as graph record metadata;
- show returned row count and cap note when applicable;
- preserve original numeric value text;
- do not write causal/mechanism conclusions;
- decline when direct evidence is suspicious or ambiguous.

- [ ] **Step 4: Add direct decline metadata**

Return `handled=False` with metadata reasons such as:

- `empty_rows`;
- `suspicious_doi`;
- `low_numeric_confidence`;
- `unsupported_direct_intent`;
- `too_many_rows`;
- `community_id_unavailable`.

- [ ] **Step 5: Run direct renderer tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_direct_renderer.py -q
```

Expected: pass.

### Task 10: RAG Adapter Payload Enrichment

**Files:**
- Modify: `fastQA/app/modules/graph_kb/rag_adapter.py`
- Test: `fastQA/tests/test_graph_kb_rag_adapter.py`

- [ ] **Step 1: Add payload tests**

Assert that graph-for-RAG bundles produce:

- route family and intent in `stage1_context_block`;
- quality-filtered DOI candidates;
- constraints in payload;
- entity hints from canonicalizer;
- fact block with graph facts and warning that graph facts are supplemental;
- stable cache fingerprint changes when graph facts or candidates change.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_rag_adapter.py -q
```

Expected: fail for missing constraints/warnings if not present.

- [ ] **Step 3: Implement concise graph context**

Preferred Stage 1 format:

```text
graph_route_family: hybrid
graph_execution_mode: graph_for_rag
graph_intent: hybrid_property_process
graph_doi_candidates_count: 8
graph_constraints:
- performance.discharge_capacity > 150
graph_facts:
- DOI=...; sample_name=...; original_value=...
```

Do not dump long raw rows.

- [ ] **Step 4: Implement Stage 4 supplemental fact wording**

Fact block should make source policy clear:

```text
Graph structured facts (supplemental; cite DOI only if source evidence is loaded):
- doi=...; title=...; carbon_sources=...
```

- [ ] **Step 5: Run RAG adapter tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_rag_adapter.py -q
```

Expected: pass.

### Task 11: Graph Service Orchestration

**Files:**
- Modify: `fastQA/app/modules/graph_kb/service.py`
- Test: `fastQA/tests/test_graph_kb_service.py`

- [ ] **Step 1: Add service mode tests**

Use monkeypatch/fake graph clients to verify:

- direct answer returns `GraphRoutingResult(mode="direct_answer")`;
- direct renderer decline downgrades to `graph_for_rag`;
- graph unavailable returns `skip_graph` with metadata;
- guardrail rejection returns `skip_graph`;
- empty hybrid returns `graph_for_rag` or `skip_graph` according to evidence availability and records fallback;
- community missing ID records `community_id_unavailable`;
- `graph_rag_injected` is false until router attaches payload.

- [ ] **Step 2: Run service tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_service.py -q
```

Expected: fail for missing fallback metadata and mode transitions.

- [ ] **Step 3: Implement orchestration fallbacks**

Rules:

- `skip_graph` if classifier says skip or planner returns no plan.
- `skip_graph` if Neo4j unavailable and no graph evidence exists.
- `graph_for_rag` if graph evidence exists but direct rendering declines.
- `graph_for_rag` for hybrid/community by default when bundle has useful facts or DOI candidates.
- preserve `direct_answer` only when direct renderer handles safely.

- [ ] **Step 4: Add latency and attempted path diagnostics**

`diagnostics` should include:

- `graph_attempted`;
- `graph_ready`;
- `matched_path`;
- `attempted_paths`;
- `guardrail_verdict`;
- `graph_result_count`;
- `graph_doi_candidates_count`;
- `graph_fallback_reason`;
- `latency_ms` if easy to capture.

- [ ] **Step 5: Run service tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_service.py -q
```

Expected: pass.

### Task 12: Generation Pipeline Graph-To-Source Bridge

**Files:**
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Modify: `fastQA/app/modules/qa_kb/models.py` only if provenance fields are needed.
- Modify: `fastQA/app/modules/qa_kb/stages/planning.py`
- Modify: `fastQA/app/modules/generation_pipeline/stage1_planning.py`
- Modify: `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
- Modify: `fastQA/app/modules/generation_pipeline/md_expansion.py`
- Modify: `fastQA/app/modules/generation_pipeline/pdf_pipeline.py`
- Modify: `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
- Test: `fastQA/tests/test_generation_stage1_planning.py`
- Test: `fastQA/tests/test_qa_generation_orchestrator.py`
- Test: `fastQA/tests/test_generation_stage2_retrieval.py`
- Test: `fastQA/tests/test_generation_md_expansion.py`
- Test: `fastQA/tests/test_generation_pdf_pipeline.py`
- Test: `fastQA/tests/test_generation_stage4_synthesis.py`

- [ ] **Step 1: Add Stage 1 graph context tests**

Stage 1 tests must prove `GraphRagPayload.stage1_context_block` reaches the planning prompt/runtime and changes the planning input without hiding the original user question.

Cover both integration layers:

- `fastQA/app/modules/qa_kb/stages/planning.py` forwards `graph_context` when the runtime supports it.
- `fastQA/app/modules/generation_pipeline/stage1_planning.py` includes graph context in the Stage 1 prompt if the concrete generation runtime builds prompts there.
- no graph context is passed when `graph_evidence` is `None`.
- cache fingerprints already handled by the orchestrator still separate graph-context and no-graph Stage 1 outputs.

Example assertion:

```python
runtime.stage1_pre_answer_and_planning.assert_called_once()
_, kwargs = runtime.stage1_pre_answer_and_planning.call_args
assert "graph_context" in kwargs
assert "graph_route_family: hybrid" in kwargs["graph_context"]
```

- [ ] **Step 2: Add graph DOI fallback tests**

Orchestrator tests must cover:

- vector DOI exists -> do not replace with graph DOI;
- vector miss + valid graph DOI + PDF chunks -> use graph-seeded DOI and `doi_source="graph_seeded"`;
- vector miss + valid graph DOI + only MD chunks exist -> use graph-seeded DOI for MD expansion, merge MD chunks into source evidence, and allow citations only from MD-backed evidence;
- vector miss + valid graph DOI + no source chunks -> no source citation allowed by Stage 4;
- malformed graph DOI does not reach PDF/MD loading;
- DOI-specific question may use graph DOI fallback even when vector retrieval is weak.

- [ ] **Step 3: Add Stage 2 graph hint tests**

`merge_graph_hints_into_retrieval()` should:

- prefix DOI candidates and entity hints;
- avoid duplicate hints already in query;
- avoid adding more than configured cap;
- preserve preprocessing behavior.

- [ ] **Step 4: Add Stage 4 citation tests**

`iter_stage4_synthesis_with_pdf_chunks()` should:

- include graph fact block as supplemental context;
- still remove invalid DOI citations not present in `pdf_chunks`;
- build references only from source chunks;
- not cite graph-only DOI when no source chunks exist.

- [ ] **Step 5: Run generation tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_generation_md_expansion.py fastQA/tests/test_generation_pdf_pipeline.py fastQA/tests/test_generation_stage4_synthesis.py -q
```

Expected: fail where graph-source bridge is incomplete.

- [ ] **Step 6: Implement Stage 1 graph context forwarding**

Keep Stage 1 graph context concise. It must supplement planning, not replace the user question.

If `Stage1Planner` already forwards `graph_context`, verify it with tests and only modify the concrete generation pipeline prompt builder if the runtime ignores that kwarg.

- [ ] **Step 7: Implement source provenance minimally**

If current metadata is enough, avoid new dataclasses. If not, carry provenance in raw dict fields:

```python
raw["doi_source"] = "retrieval" | "graph_seeded" | "none"
raw["graph_seeded_dois"] = [...]
```

Do not change frontend response shape unless tests require additive metadata.

- [ ] **Step 8: Tighten graph DOI fallback**

Use graph DOI candidates only when:

- vector retrieval extracts no DOI; or
- the original question contains an explicit DOI and the candidate matches it.

Before source loading, DOI candidates must already have passed DOI quality in graph canonicalizer.

- [ ] **Step 9: Preserve citation whitelist**

Do not add graph DOI to references directly. Stage 4 reference building remains based on `pdf_chunks` or MD chunks merged into `pdf_chunks`.

- [ ] **Step 10: Run generation tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_generation_md_expansion.py fastQA/tests/test_generation_pdf_pipeline.py fastQA/tests/test_generation_stage4_synthesis.py -q
```

Expected: pass.

### Task 13: fastQA Router Integration And Gateway Boundary Tests

**Files:**
- Modify: `fastQA/app/routers/qa.py`
- Test: `fastQA/tests/test_fastqa_kb_graph_integration.py`
- Test: `fastQA/tests/test_qa_routes_file_modes.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `gateway/tests/test_route_classifier.py`

- [ ] **Step 1: Add fastQA API integration tests**

Cover:

- direct graph answer short-circuits generation;
- graph-for-RAG attaches payload only when graph RAG injection flag is enabled;
- graph-for-RAG without injection falls through to generation without payload;
- skip graph falls through to generation;
- metadata events include canonical keys;
- final done metadata merges graph metadata without overwriting generation metadata incorrectly.

- [ ] **Step 2: Add route non-regression tests**

Ensure:

- `pdf_qa`, `tabular_qa`, and gateway-level `hybrid_qa` behavior is unchanged;
- gateway does not emit graph route family fields;
- fastQA only runs four-route graph router for `kb_qa`.

- [ ] **Step 3: Run integration tests to verify failure**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_fastqa_kb_graph_integration.py fastQA/tests/test_qa_routes_file_modes.py gateway/tests/test_route_decision.py gateway/tests/test_route_classifier.py -q
```

Expected: fail only where new metadata/payload behavior is missing.

- [ ] **Step 4: Implement additive router metadata**

Update `_graph_v2_metadata()` and `_merge_graph_v2_event()` to expose canonical keys and aliases.

When graph RAG payload is attached, set:

```python
graph_v2_metadata["graph_rag_injected"] = True
graph_v2_metadata["graph_rag_injection_enabled"] = True
```

When graph evidence exists but injection flag is off, set:

```python
graph_v2_metadata["graph_rag_injected"] = False
graph_v2_metadata["graph_rag_injection_enabled"] = False
```

- [ ] **Step 5: Run integration and gateway boundary tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_fastqa_kb_graph_integration.py fastQA/tests/test_qa_routes_file_modes.py gateway/tests/test_route_decision.py gateway/tests/test_route_classifier.py -q
```

Expected: pass.

### Task 14: End-To-End Fixture Matrix

**Files:**
- Test: `fastQA/tests/test_fastqa_kb_graph_integration.py`
- Test: `fastQA/tests/test_graph_kb_service.py`
- Test: `fastQA/tests/test_qa_kb_service.py`
- Optional Modify: test helper modules only if existing helpers are insufficient.

- [ ] **Step 1: Create fake graph result fixtures**

Use small row sets for:

- DOI valid metadata;
- DOI malformed metadata;
- carbon source list;
- carbon source exact count;
- process profile;
- numeric high confidence rows;
- numeric low confidence rows;
- hybrid capacity candidates;
- hybrid expansion facts;
- community rows with `community_id`;
- community rows without `community_id`;
- graph unavailable.

- [ ] **Step 2: Add end-to-end graph route tests**

Each acceptance matrix question should be tested from `route_graph_kb_v2()` through direct result or `GraphRagPayload`.

Expected checks:

- route family;
- execution mode;
- strategy;
- intent;
- direct answer handled or declined;
- payload candidates/facts;
- fallback reason when applicable.

- [ ] **Step 3: Add API-level stream metadata tests**

For at least one direct answer and one graph-for-RAG answer, verify stream events include metadata and no frontend-breaking fields.

- [ ] **Step 4: Run end-to-end fixture tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py fastQA/tests/test_qa_kb_service.py -q
```

Expected: pass.

### Task 15: Full Verification

**Files:**
- No production modifications unless failures reveal defects.

- [ ] **Step 1: Run all graph tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_graph_kb_*.py -q
```

Expected: pass.

- [ ] **Step 2: Run generation and KB integration tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_generation_md_expansion.py fastQA/tests/test_generation_pdf_pipeline.py fastQA/tests/test_generation_stage4_synthesis.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_qa_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py -q
```

Expected: pass.

- [ ] **Step 3: Run route boundary tests**

Run:

```bash
conda run -n agent pytest fastQA/tests/test_qa_routes_file_modes.py gateway/tests/test_route_decision.py gateway/tests/test_route_classifier.py -q
```

Expected: pass.

- [ ] **Step 4: Run a broader fastQA suite if time permits**

Run:

```bash
conda run -n agent pytest fastQA/tests -q
```

Expected: pass or documented unrelated baseline failures.

- [ ] **Step 5: Inspect diff**

Run:

```bash
git diff --stat
git diff -- fastQA/app/modules/graph_kb fastQA/app/modules/qa_kb fastQA/app/modules/generation_pipeline fastQA/app/routers/qa.py fastQA/tests gateway/tests docs
```

Expected: changes are scoped to this plan and do not touch unrelated files.

### Task 16: Code Review Handoff After Implementation

**Files:**
- No direct modifications in this task unless review feedback requires fixes.

- [ ] **Step 1: Open a 5.5 high code-review subagent**

Reviewer context must include:

- this implementation plan path;
- spec path;
- summary of implemented tasks;
- test commands and outcomes;
- git diff target scope.

- [ ] **Step 2: Require explicit reviewer verdict**

Reviewer must return one of:

- `APPROVED`;
- `CHANGES_REQUESTED`.

Review focus:

- route semantics match spec;
- gateway boundary preserved;
- direct answers are safe;
- graph-only DOI citation policy is not violated;
- vector/PDF/MD RAG logic is not regressed;
- tests cover acceptance matrix and fallbacks.

- [ ] **Step 3: Fix review findings**

Use `superpowers:receiving-code-review` before applying feedback. Fix all critical and important issues. If feedback is wrong, document the technical reason and ask reviewer to reassess the specific point.

- [ ] **Step 4: Reuse the same reviewer for re-review**

Send only:

- changes made since previous review;
- test reruns;
- remaining questions if any.

Repeat until reviewer returns `APPROVED`.

- [ ] **Step 5: Final verification before claiming completion**

Use `superpowers:verification-before-completion`. Do not claim complete without test output from the focused suites.

## Implementation Order Summary

1. Baseline and docs.
2. Metadata contract.
3. Slot extraction.
4. Classifier route semantics.
5. Schema registry and strategy support.
6. Query templates and planner.
7. Guardrail and executor.
8. Canonicalizer, DOI, numeric parser, and constraints.
9. Direct renderer.
10. RAG adapter.
11. Graph service orchestration.
12. Generation graph-to-source bridge.
13. fastQA router and gateway boundary tests.
14. End-to-end fixture matrix.
15. Full verification.
16. 5.5 high code review and re-review loop.

## Out-Of-Scope For This Plan

- Rebuilding `community_summaries` vector database.
- Adding LLM-generated Cypher.
- Changing gateway public route names.
- Running graph router for file-only PDF/table routes.
- Frontend UI changes.
- Neo4j schema migration.
