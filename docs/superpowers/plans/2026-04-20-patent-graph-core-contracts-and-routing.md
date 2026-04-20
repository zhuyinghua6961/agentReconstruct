# Patent Graph Core Contracts And Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the patent graph V2 routing contracts, feature flags, classifier, and `kb_qa` service orchestration so patent graph can return `direct_answer`, `graph_for_rag`, or `skip_graph` without breaking current fallback behavior.

**Architecture:** Keep the existing `try_patent_graph_kb_answer(...)` path as the trusted legacy direct-answer shell, then layer a V2 router beside it. `PatentKbService` remains the integration boundary: it consumes the new routing result, returns direct graph answers immediately, injects graph payloads into context for staged QA, or falls through unchanged.

**Tech Stack:** Python dataclasses, patent `kb_service` preflight, env-based feature flags, deterministic graph routing, pytest

---

## Scope and ownership

This document owns:

- `patent/server/patent/graph_kb/models.py`
- `patent/server/patent/graph_kb/classifier_v2.py`
- `patent/server/patent/graph_kb/service.py`
- `patent/server/patent/cache_keys.py`
- `patent/server/patent/kb_service.py`
- `patent/server/patent/executor.py`
- `patent/config.py`
- `patent/config.shared.env.example`
- routing/config tests
- `patent/tests/test_patent_graph_kb_stage1_cache_keys.py`

This document does not own:

- schema registry and query builders
- guardrail/executor/canonicalizer/direct renderer
- stage prompt integration
- FastAPI health/runtime exposure

## Prerequisites

Do not execute the service-wiring tasks in this document until these companion documents have landed:

- `docs/superpowers/plans/2026-04-20-patent-graph-schema-planning-and-query-builders.md`
- `docs/superpowers/plans/2026-04-20-patent-graph-execution-canonicalization-and-direct-rendering.md`
- `docs/superpowers/plans/2026-04-20-patent-graph-rag-context-and-stage-integration.md`

In particular:

- `route_patent_graph_kb_v2(...)` depends on planner/executor/canonicalizer/direct renderer/rag adapter implementations from the other component documents
- the stage-1 cache isolation work owned here must land before V2 is enabled in any environment

### Task 1: Add V2 feature flags and routing contracts

**Files:**
- Modify: `patent/config.py`
- Modify: `patent/config.shared.env.example`
- Modify: `patent/server/patent/graph_kb/models.py`
- Modify: `patent/server/patent/cache_keys.py`
- Test: `patent/tests/test_patent_graph_kb_config.py`
- Test: `patent/tests/test_patent_graph_kb_stage1_cache_keys.py`

- [ ] **Step 1: Add config fields for V2 routing**

Add to `PatentGraphSettings`:

- `v2_enabled: bool`
- `rag_injection_enabled: bool`

Populate them from:

- `PATENT_GRAPH_KB_V2_ENABLED`
- `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED`

Both must default to `false`.

- [ ] **Step 2: Extend graph models with V2 routing dataclasses**

Add to `patent/server/patent/graph_kb/models.py`:

- `PatentGraphSemanticDecision`
- `PatentGraphRoutingResult`
- `PatentGraphConstraint`
- `PatentGraphQueryPlanV2`
- `PatentGraphRagPayload`
- `PatentGraphEvidenceBundle`
- `PatentDirectAnswerResult`
- `PatentGuardrailResult`
- `PatentExecutionTrace`
- `PatentRawExecutionResult`

Keep existing dataclasses untouched for legacy compatibility.

Define these fields explicitly in the owned contracts:

- `PatentGraphRagPayload.stage1_context_block`
- `PatentGraphRagPayload.stage2_patent_candidates`
- `PatentGraphRagPayload.stage2_constraints`
- `PatentGraphRagPayload.stage2_entity_hints`
- `PatentGraphRagPayload.stage4_fact_block`
- `PatentGraphRagPayload.stage4_graph_candidate_patent_ids`
- `PatentGraphRagPayload.cache_fingerprint`
- `PatentGraphRagPayload.diagnostics`
- `PatentGraphRoutingResult.diagnostics`
- `PatentGraphQueryPlanV2.diagnostics`
- `PatentGraphEvidenceBundle.diagnostics`

Also document the normalized injected context shape:

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

- [ ] **Step 3: Update config tests**

Add assertions that:

- `PATENT_GRAPH_KB_V2_ENABLED` defaults to `false`
- `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED` defaults to `false`
- env overrides populate the new settings fields

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_config.py -q
```

- [ ] **Step 4: Add stage-1 cache-key isolation tests and implementation**

Update `build_stage1_cache_fingerprint(...)` so graph payload changes produce different fingerprints.

Cover:

- same question + different graph payload => different fingerprint
- same normalized graph payload => stable fingerprint
- `conversation_context["graph_kb"]` normalization does not depend on volatile fields only

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_stage1_cache_keys.py -q
```

- [ ] **Step 5: Commit**

```bash
git add patent/config.py patent/config.shared.env.example patent/server/patent/graph_kb/models.py patent/server/patent/cache_keys.py patent/tests/test_patent_graph_kb_config.py patent/tests/test_patent_graph_kb_stage1_cache_keys.py
git commit -m "feat: add patent graph v2 routing contracts"
```

### Task 2: Implement the V2 patent classifier

**Files:**
- Create: `patent/server/patent/graph_kb/classifier_v2.py`
- Test: `patent/tests/test_patent_graph_kb_classifier_v2.py`

- [ ] **Step 1: Write classifier tests first**

Cover:

- patent ID -> `direct_answer` / `precise`
- IPC listing -> `direct_answer` / `precise`
- IPC subclass query -> graph-capable route
- applicant listing -> `direct_answer` / `precise`
- inventor query -> graph-capable route
- agency query -> graph-capable route
- multi-patent compare -> `graph_for_rag` / `hybrid`
- broad semantic question -> `skip_graph`
- file-context-heavy turn -> not `direct_answer`
- DOI question -> `skip_graph`

- [ ] **Step 2: Implement `classify_patent_graph_question_v2(...)`**

Preserve existing safe rules from `classifier.py`, then extend them to:

- recognize `precise`, `hybrid`, `semantic`
- produce `direct_answer`, `graph_for_rag`, `skip_graph`
- record `matched_rule`, `requires_context_resolution`, and anchor diagnostics

- [ ] **Step 3: Run the classifier test suite**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_classifier_v2.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/graph_kb/classifier_v2.py patent/tests/test_patent_graph_kb_classifier_v2.py
git commit -m "feat: add patent graph v2 classifier"
```

### Task 3: Add the V2 service entry point

**Files:**
- Modify: `patent/server/patent/graph_kb/service.py`
- Test: `patent/tests/test_patent_graph_kb_service_v2.py`

- [ ] **Step 1: Write service routing tests**

Cover:

- `skip_graph` returns a routing result with no direct answer
- `direct_answer` returns a `PatentGraphRoutingResult` carrying a handled graph result
- `graph_for_rag` returns a `PatentGraphRoutingResult` carrying a `PatentGraphRagPayload`
- direct-answer render failure downgrades to `graph_for_rag` when evidence is still useful

- [ ] **Step 2: Implement `route_patent_graph_kb_v2(...)`**

Wire the already-implemented modules delivered by the companion component docs:

- `classifier_v2`
- planner
- executor
- canonicalizer
- direct renderer
- rag adapter

Keep `try_patent_graph_kb_answer(...)` available for legacy path compatibility.

- [ ] **Step 3: Run service tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_service_v2.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/graph_kb/service.py patent/tests/test_patent_graph_kb_service_v2.py
git commit -m "feat: add patent graph v2 service router"
```

### Task 4: Integrate V2 routing into `PatentKbService`

**Files:**
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/executor.py`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/test_patent_executor.py`

- [ ] **Step 1: Add failing service integration tests**

Cover:

- V2 disabled -> current preflight behavior preserved
- V2 enabled + `direct_answer` -> direct graph return
- V2 enabled + `graph_for_rag` + rag injection enabled -> staged QA continues with enriched context
- V2 enabled + `graph_for_rag` + rag injection disabled -> staged QA continues without graph payload
- V2 enabled + `skip_graph` -> staged QA unchanged
- graph routing exception -> silent fallback

- [ ] **Step 2: Implement V2-aware preflight in `PatentKbService`**

Change `_try_graph_preflight(...)` to:

- branch on primitive constructor flags stored on the service instance, for example:
  - `_graph_kb_enabled`
  - `_graph_kb_v2_enabled`
  - `_graph_kb_rag_injection_enabled`
- call `route_patent_graph_kb_v2(...)` when enabled
- return direct answers immediately
- return an internal graph-payload result marker for `graph_for_rag`
- preserve legacy fallback semantics on error

- [ ] **Step 3: Pass the new controls through `PatentExecutor`**

Ensure the executor constructor and default `PatentKbService` wiring pass:

- `graph_kb_v2_enabled`
- `graph_kb_rag_injection_enabled`

- [ ] **Step 4: Run service integration tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_kb_service.py tests/test_patent_executor.py -q
```

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/kb_service.py patent/server/patent/executor.py patent/tests/test_patent_kb_service.py patent/tests/test_patent_executor.py
git commit -m "feat: integrate patent graph v2 routing into kb service"
```

### Task 5: Add downgrade metadata and compatibility assertions

**Files:**
- Modify: `patent/server/patent/kb_service.py`
- Test: `patent/tests/test_patent_kb_service.py`

- [ ] **Step 1: Record graph downgrade metadata**

When V2 selects `graph_for_rag`, record on both the success path and the downgrade path:

- `metadata["graph_kb"]`
- `metadata["graph_kb_mode"]`
- `metadata["graph_kb_strategy"]`
- `metadata["graph_kb_fingerprint"]`
- `metadata["graph_kb_downgrade_reason"]`

`metadata["graph_kb"]` should include bundle/routing diagnostics, not just flat mode fields.

- [ ] **Step 2: Assert compatibility with `PatentResultBuilder`**

Keep returned shapes compatible with:

- `references`
- `reference_objects`
- `query_mode`

- [ ] **Step 3: Re-run impacted tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_service_v2.py tests/test_patent_kb_service.py tests/test_patent_executor.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/kb_service.py patent/tests/test_patent_kb_service.py
git commit -m "test: lock patent graph v2 downgrade metadata"
```
