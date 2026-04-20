# Patent Graph Rag Context And Stage Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the patent graph RAG payload adapter, stage-1 cache-key isolation, graph-aware degraded stage-1 fallback behavior, and stage-4 prompt/fallback integration without inserting Neo4j traversal into the staged patent runtime.

**Architecture:** `PatentGraphRagPayload` is built in the graph layer, injected into `conversation_context["graph_kb"]` by `PatentKbService`, then consumed by stage 1 planning and stage 4 synthesis. Stage 1 uses graph payload as a structured anchor and fallback seeding source. Stage 4 receives graph facts and a separate non-citable candidate list; only retrieval-backed `allowed_patent_ids` remain citable.

**Tech Stack:** Python dataclasses, staged patent QA context propagation, cache fingerprinting, prompt builders, pytest

---

## Scope and ownership

This document owns:

- `patent/server/patent/graph_kb/rag_adapter.py`
- `patent/server/patent/cache_keys.py`
- `patent/server/patent/stages/planning.py`
- `patent/server/patent/stages/synthesis.py`
- `patent/server/patent/answering.py`
- graph context and cache tests

This document does not own:

- `patent/server/patent/kb_service.py`

## Prerequisites

Do not execute this document before these companion documents have landed:

- `docs/superpowers/plans/2026-04-20-patent-graph-core-contracts-and-routing.md`
- `docs/superpowers/plans/2026-04-20-patent-graph-schema-planning-and-query-builders.md`

Specifically, this document assumes the core-routing plan has already added:

- `PatentGraphRagPayload` and related V2 graph contracts
- `kb_service.py` logic that injects the normalized `conversation_context["graph_kb"]` block
- staged graph metadata/degradation reporting on the `kb_service.py` path

### Task 1: Build the patent graph RAG adapter

**Files:**
- Create: `patent/server/patent/graph_kb/rag_adapter.py`
- Test: `patent/tests/test_patent_graph_kb_rag_adapter.py`

- [ ] **Step 1: Write RAG adapter tests**

Cover:

- stable payload fingerprinting
- stage-1 context block rendering
- stage-2 patent candidates and constraints
- stage-4 fact block
- explicit `stage4_graph_candidate_patent_ids`
- diagnostics propagation

- [ ] **Step 2: Implement `build_patent_graph_rag_payload(...)`**

Required outputs:

- `stage1_context_block`
- `stage2_patent_candidates`
- `stage2_constraints`
- `stage2_entity_hints`
- `stage4_fact_block`
- `stage4_graph_candidate_patent_ids`
- `cache_fingerprint`
- `diagnostics`

- [ ] **Step 3: Run the adapter tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_rag_adapter.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/graph_kb/rag_adapter.py patent/tests/test_patent_graph_kb_rag_adapter.py
git commit -m "feat: add patent graph rag payload adapter"
```

### Task 2: Isolate stage-1 cache keys for graph payloads

**Files:**
- Modify: `patent/server/patent/cache_keys.py`
- Test: `patent/tests/test_patent_graph_kb_stage1_cache_keys.py`

- [ ] **Step 1: Write cache-key tests**

Cover:

- same question + same conversational summary + different graph payloads => different stage-1 fingerprints
- same graph payload => stable fingerprint
- volatile graph diagnostics do not create accidental cache churn if the normalized design intentionally excludes them

- [ ] **Step 2: Implement graph-aware stage-1 fingerprinting**

Update `build_stage1_cache_fingerprint(...)` to include:

- normalized `conversation_context["graph_kb"]`
  or
- a normalized graph payload fingerprint plus other relevant graph context fields

Use the same normalization discipline as other cache-key helpers.

- [ ] **Step 3: Run cache-key tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_stage1_cache_keys.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/cache_keys.py patent/tests/test_patent_graph_kb_stage1_cache_keys.py
git commit -m "fix: isolate patent stage1 cache by graph payload"
```

### Task 3: Add graph-aware stage-1 planning support

**Files:**
- Modify: `patent/server/patent/stages/planning.py`
- Test: `patent/tests/test_patent_stage1_graph_context.py`

- [ ] **Step 1: Write stage-1 graph-context tests**

Cover:

- formatting of `conversation_context["graph_kb"]`
- graph payload included in prompt content
- planner-unavailable fallback seeds retrieval claims from graph candidates and hints
- JSON-parse-failed fallback seeds retrieval claims from graph candidates and hints
- planner-error fallback seeds retrieval claims from graph candidates and hints

- [ ] **Step 2: Implement graph context formatting**

Add a helper that renders:

- graph route/mode
- graph candidate patents
- graph entity hints
- graph constraints
- graph facts summary

- [ ] **Step 3: Implement degraded fallback seeding**

When stage-1 planning degrades:

- inspect `conversation_context["graph_kb"]`
- build a minimal retrieval plan and retrieval claims from graph anchors
- if graph payload is missing or unusable, fall back to current empty-plan behavior

- [ ] **Step 4: Run stage-1 tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_stage1_graph_context.py -q
```

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/stages/planning.py patent/tests/test_patent_stage1_graph_context.py
git commit -m "feat: add graph-aware patent stage1 planning context"
```

### Task 4: Wire graph context through stage 4 synthesis and answer building

**Files:**
- Modify: `patent/server/patent/stages/synthesis.py`
- Modify: `patent/server/patent/answering.py`
- Test: `patent/tests/test_patent_stage4_graph_context.py`
- Test: `patent/tests/test_patent_answering_graph_context.py`

- [ ] **Step 1: Write stage-4 context and answer-builder tests**

Cover:

- `synthesis.py` passes graph blocks into stage-4 context
- `answering.py` includes graph facts and graph candidate IDs in prompt context
- graph candidate IDs stay separate from retrieval-backed `allowed_patent_ids`
- fallback answer path sees graph context but does not fabricate citations from graph-only candidates

- [ ] **Step 2: Update `synthesis.py` context handoff**

Inject into stage-4 context:

- `graph_kb` payload block
- `graph_kb_mode`
- `graph_kb_fingerprint`

Do not alter stage-3 evidence loading.

- [ ] **Step 3: Update `answering.py` prompt and fallback builders**

Add graph context sections for:

- graph facts
- graph candidate patents
- graph diagnostics when helpful

Keep the citation contract strict:

- `allowed_patent_ids` remains retrieval-backed only
- `stage4_graph_candidate_patent_ids` remains non-citable grounding

- [ ] **Step 4: Run stage-4 tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest \
  tests/test_patent_stage4_graph_context.py \
  tests/test_patent_answering_graph_context.py \
  tests/test_patent_stage4_synthesis.py \
  tests/test_patent_kb_service.py -q
```

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/stages/synthesis.py patent/server/patent/answering.py patent/tests/test_patent_stage4_graph_context.py patent/tests/test_patent_answering_graph_context.py
git commit -m "feat: add patent graph context to stage4 answering"
```
