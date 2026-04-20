# Patent Graph Execution Canonicalization And Direct Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the execution, guardrail, canonicalization, and deterministic direct-rendering layers needed by patent graph V2 while preserving the current direct-driver Neo4j access model and `stub` quality controls.

**Architecture:** Template execution stays on the trusted current path. Parametric execution goes through a patent allowlist guardrail and a V2 executor that records traces. Canonicalization normalizes query rows into patent-native evidence bundles, and direct rendering consumes only high-confidence bundles that remain compatible with `PatentResultBuilder`.

**Tech Stack:** Python dataclasses, Neo4j Python driver, deterministic renderers, pytest

---

## Scope and ownership

This document owns:

- `patent/server/patent/graph_kb/guardrail.py`
- `patent/server/patent/graph_kb/executor_v2.py`
- `patent/server/patent/graph_kb/canonicalizer.py`
- `patent/server/patent/graph_kb/direct_renderer.py`
- optional cleanup in `patent/server/patent/graph_kb/rendering.py`
- execution/rendering tests

## Prerequisites

Do not execute this document before these companion documents have landed:

- `docs/superpowers/plans/2026-04-20-patent-graph-core-contracts-and-routing.md`
- `docs/superpowers/plans/2026-04-20-patent-graph-schema-planning-and-query-builders.md`

This component consumes:

- V2 graph dataclasses from `graph_kb/models.py`
- schema registry allowlists
- planner/query-plan outputs

### Task 1: Add the patent Cypher guardrail

**Files:**
- Create: `patent/server/patent/graph_kb/guardrail.py`
- Test: `patent/tests/test_patent_graph_kb_guardrail.py`

- [ ] **Step 1: Write guardrail tests first**

Cover:

- write clauses rejected
- unapproved labels rejected
- unapproved relations rejected
- missing `LIMIT` auto-normalized
- valid patent labels/relations allowed

- [ ] **Step 2: Implement `inspect_patent_cypher(...)`**

Use the patent schema registry allowlist. Do not borrow fastQA label/relation allowlists.

- [ ] **Step 3: Run guardrail tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_guardrail.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/graph_kb/guardrail.py patent/tests/test_patent_graph_kb_guardrail.py
git commit -m "feat: add patent graph cypher guardrail"
```

### Task 2: Build the V2 executor

**Files:**
- Create: `patent/server/patent/graph_kb/executor_v2.py`
- Test: `patent/tests/test_patent_graph_kb_executor_v2.py`

- [ ] **Step 1: Write executor tests**

Cover:

- legacy template plan uses current `execute_patent_graph_plan(...)`
- parametric plan uses guardrail plus driver query execution
- empty candidate set -> empty result with trace
- guardrail rejection -> trace records rejection
- multi-candidate parametric plans honor `max_path_attempts`
- `attempted_paths` and `matched_path` are populated correctly
- timeout and unavailable-client paths degrade safely

- [ ] **Step 2: Implement `execute_patent_prepared_query(...)`**

Requirements:

- template path delegates to current client execution
- parametric path uses guardrailed candidate queries
- return `PatentRawExecutionResult` with `PatentExecutionTrace`

- [ ] **Step 3: Run executor tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_executor_v2.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/graph_kb/executor_v2.py patent/tests/test_patent_graph_kb_executor_v2.py
git commit -m "feat: add patent graph v2 executor"
```

### Task 3: Canonicalize graph rows into patent evidence bundles

**Files:**
- Create: `patent/server/patent/graph_kb/canonicalizer.py`
- Test: `patent/tests/test_patent_graph_kb_canonicalizer.py`

- [ ] **Step 1: Write canonicalizer tests**

Cover:

- patent candidates are deduplicated
- IPC / organization / inventor candidates are extracted
- facts are stable and deterministic
- `direct_answerable` is true only for safe direct-answer cases
- bundle diagnostics are preserved

- [ ] **Step 2: Implement `canonicalize_patent_graph_rows(...)`**

Populate:

- patent candidates
- IPC candidates
- organization / inventor candidates
- facts
- render slots
- direct-answerability
- constraints for RAG
- diagnostics

- [ ] **Step 3: Run canonicalizer tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_canonicalizer.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/graph_kb/canonicalizer.py patent/tests/test_patent_graph_kb_canonicalizer.py
git commit -m "feat: add patent graph evidence canonicalizer"
```

### Task 4: Add the direct renderer

**Files:**
- Create: `patent/server/patent/graph_kb/direct_renderer.py`
- Optionally modify: `patent/server/patent/graph_kb/rendering.py`
- Test: `patent/tests/test_patent_graph_kb_direct_renderer.py`

- [ ] **Step 1: Write direct-renderer tests**

Cover:

- direct-answerable bundle returns a handled result
- unsupported bundle returns `handled=False`
- `reference_objects` stay aligned with `references`
- `stub`-only bundles do not produce direct answers

- [ ] **Step 2: Implement `render_patent_direct_answer(...)`**

Reuse current rendering semantics where practical, but keep V2 routing logic separate from legacy rendering helpers.

- [ ] **Step 3: Reconcile legacy rendering reuse**

If logic is shared between `rendering.py` and `direct_renderer.py`, extract small helper functions. Do not break the current legacy path.

- [ ] **Step 4: Re-run legacy renderer regression coverage when `rendering.py` changes**

If `rendering.py` is touched, explicitly rerun the current service-level regression tests that protect the legacy rendering path.

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_service.py -q
```

- [ ] **Step 5: Run rendering tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_direct_renderer.py -q
```

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/graph_kb/direct_renderer.py patent/server/patent/graph_kb/rendering.py patent/tests/test_patent_graph_kb_direct_renderer.py
git commit -m "feat: add patent graph direct renderer"
```
