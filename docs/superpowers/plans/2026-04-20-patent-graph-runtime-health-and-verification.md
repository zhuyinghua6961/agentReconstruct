# Patent Graph Runtime Health And Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the new patent graph V2 controls and readiness state through runtime bootstrap and health surfaces, while preserving current service-availability semantics and adding a clear verification path for rollout.

**Architecture:** FastAPI bootstrap remains responsible for creating the patent graph client and recording component status. Health output should report graph enabled/ready/V2 state without turning graph degradation into a blanket service outage when staged QA still works. Verification should combine repo-local tests with an optional service-backed smoke pass.

**Tech Stack:** FastAPI app state, env-driven runtime bootstrap, health contracts, pytest, optional service-backed smoke checks

---

## Scope and ownership

This document owns:

- `patent/server_fastapi/app.py`
- `patent/server_fastapi/routers/health.py`
- health/runtime tests
- rollout verification checklist

This document does not own:

- `patent/config.py`
- `patent/config.shared.env.example`
- `patent/tests/test_patent_graph_kb_config.py`

## Prerequisites

Do not execute this document before these companion documents have landed:

- `docs/superpowers/plans/2026-04-20-patent-graph-core-contracts-and-routing.md`
- `docs/superpowers/plans/2026-04-20-patent-graph-schema-planning-and-query-builders.md`
- `docs/superpowers/plans/2026-04-20-patent-graph-execution-canonicalization-and-direct-rendering.md`
- `docs/superpowers/plans/2026-04-20-patent-graph-rag-context-and-stage-integration.md`

Specifically, this document assumes the core-routing plan has already added and tested:

- `PATENT_GRAPH_KB_V2_ENABLED`
- `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED`
- corresponding settings fields in `PatentGraphSettings`
- config coverage in `test_patent_graph_kb_config.py`

### Task 1: Extend runtime bootstrap metadata

**Files:**
- Modify: `patent/server_fastapi/app.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Write failing bootstrap/health tests**

Cover:

- graph V2 disabled state exposed
- graph V2 enabled state exposed
- graph RAG injection enabled state exposed
- graph client degraded state does not erase staged QA readiness

- [ ] **Step 2: Update FastAPI app bootstrap**

Record in component status or top-level metadata:

- graph enabled
- graph ready
- V2 enabled
- graph RAG injection enabled

Keep the existing direct Neo4j client bootstrap flow.
Consume the new flags from `app.state.settings.graph_kb`, rather than defining them here.

- [ ] **Step 3: Run health contract tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/fastapi_contract/test_health_contract.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server_fastapi/app.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat: expose patent graph v2 runtime state"
```

### Task 2: Tighten the health response contract

**Files:**
- Modify: `patent/server_fastapi/routers/health.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Add health assertions**

Cover:

- `patent_graph_kb_enabled`
- `patent_graph_kb_ready`
- V2 enabled signal
- RAG injection enabled signal
- durable health behavior unchanged for unrelated components

- [ ] **Step 2: Update health response payload**

Expose the new flags without changing:

- existing route readiness semantics
- file-route runtime gating semantics
- durable-mode behavior

- [ ] **Step 3: Re-run contract tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/fastapi_contract/test_health_contract.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server_fastapi/routers/health.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat: report patent graph v2 state in health"
```

### Task 3: Define verification gates

**Files:**
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`
- Optional docs-only verification notes in implementation PR description

- [ ] **Step 1: Lock repo-local verification commands**

Required test command set:

```bash
cd patent && PYTHONPATH=. pytest \
  tests/test_patent_graph_kb_config.py \
  tests/test_patent_graph_kb_classifier_v2.py \
  tests/test_patent_graph_kb_schema_registry.py \
  tests/test_patent_graph_kb_query_strategy.py \
  tests/test_patent_graph_kb_planner_v2.py \
  tests/test_patent_graph_kb_guardrail.py \
  tests/test_patent_graph_kb_executor_v2.py \
  tests/test_patent_graph_kb_canonicalizer.py \
  tests/test_patent_graph_kb_direct_renderer.py \
  tests/test_patent_graph_kb_rag_adapter.py \
  tests/test_patent_graph_kb_service_v2.py \
  tests/test_patent_graph_kb_stage1_cache_keys.py \
  tests/test_patent_stage1_graph_context.py \
  tests/test_patent_stage4_graph_context.py \
  tests/test_patent_answering_graph_context.py \
  tests/test_patent_kb_service.py \
  tests/test_patent_executor.py \
  tests/fastapi_contract/test_health_contract.py -q
```

- [ ] **Step 2: Define optional service-backed smoke checks**

When the local patent Neo4j instance is available:

- exact patent ID lookup
- IPC listing
- applicant listing
- one graph-for-RAG compare question

Keep these as manual or opt-in verification, not default unit tests.

- [ ] **Step 3: Commit**

```bash
git add patent/tests/fastapi_contract/test_health_contract.py
git commit -m "test: define patent graph v2 verification gates"
```
