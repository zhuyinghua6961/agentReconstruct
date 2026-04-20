# Patent Graph Schema Planning And Query Builders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the patent logical schema registry, V2 planner, query-strategy layer, and the safe parametric query builders that extend the existing 9 fixed patent templates.

**Architecture:** The planner must stay patent-native. It should use a logical schema over real patent labels and relations, prefer existing fixed templates, then emit slot-driven parametric candidates for inventor, agency, IPC subclass, counting, and multi-patent comparison queries. It must not default to free-form Cypher generation.

**Tech Stack:** Python dataclasses, deterministic Cypher construction, read-only Neo4j access, pytest

---

## Scope and ownership

This document owns:

- `patent/server/patent/graph_kb/models.py` for schema/planner-specific types only
- `patent/server/patent/graph_kb/schema_registry.py`
- `patent/server/patent/graph_kb/query_strategy.py`
- `patent/server/patent/graph_kb/planner_v2.py`
- `patent/server/patent/graph_kb/client.py`
- planner/query-builder tests

## Prerequisites

Do not execute this document before the core V2 contract work in:

- `docs/superpowers/plans/2026-04-20-patent-graph-core-contracts-and-routing.md`

Specifically, Task 2 and Task 4 assume these prerequisite types already exist in `graph_kb/models.py`:

- `PatentGraphSemanticDecision`
- `PatentGraphQueryPlanV2`
- related routing/constraint dataclasses used by the planner and query-strategy layers

### Task 1: Build the patent schema registry

**Files:**
- Modify: `patent/server/patent/graph_kb/models.py`
- Create: `patent/server/patent/graph_kb/schema_registry.py`
- Test: `patent/tests/test_patent_graph_kb_schema_registry.py`

- [ ] **Step 1: Write schema registry tests first**

Cover:

- expected logical fields are present
- allowed labels match the patent graph, not fastQA’s DOI labels
- allowed relations include `NEXT_STEP`, `USES_ATMOSPHERE`, `HAS_EMBODIMENT_INSIGHT`
- planner summary exposes the right field/allowlist sets

- [ ] **Step 2: Implement `build_default_patent_schema_registry()`**

Add logical fields for:

- patent metadata
- IPC and IPC subclass
- applicant, agency, inventor
- process, atmosphere
- material role and material
- experiment / measurement
- problem / solution / scenario
- inventive point / performance fact / protection scope / claim label
- embodiment insight

Add the required schema-model contracts to `graph_kb/models.py`:

- `PatentLogicalFieldSpec`
- `PatentSchemaSummary`
- planner-facing schema registry dataclass or protocol support if needed

- [ ] **Step 3: Run the schema registry tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_schema_registry.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/graph_kb/models.py patent/server/patent/graph_kb/schema_registry.py patent/tests/test_patent_graph_kb_schema_registry.py
git commit -m "feat: add patent graph schema registry"
```

### Task 2: Add the query-strategy layer

**Files:**
- Create: `patent/server/patent/graph_kb/query_strategy.py`
- Test: `patent/tests/test_patent_graph_kb_query_strategy.py`

- [ ] **Step 1: Write strategy tests**

Cover:

- current 9-template questions -> `template`
- inventor/agency/IPC-subclass questions -> `parametric`
- multi-patent compare -> `parametric`
- broad semantic -> `None`
- reserved `llm_cypher` path stays disabled by default

- [ ] **Step 2: Implement strategy helpers**

Add:

- `can_use_patent_legacy_template(...)`
- `can_build_patent_parametric_query(...)`
- `select_patent_query_strategy(...)`

- [ ] **Step 3: Run strategy tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_query_strategy.py -q
```

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/graph_kb/query_strategy.py patent/tests/test_patent_graph_kb_query_strategy.py
git commit -m "feat: add patent graph query strategy layer"
```

### Task 3: Extend `client.py` with safe parametric builders

**Files:**
- Modify: `patent/server/patent/graph_kb/client.py`
- Test: `patent/tests/test_patent_graph_kb_planner_v2.py`

- [ ] **Step 1: Write failing planner/query-builder tests**

Cover:

- `list_patents_by_inventor`
- `list_patents_by_agency`
- `list_patents_by_ipc_subclass`
- `list_patent_atmospheres`
- `list_patent_embodiment_insights`
- `count_patents_by_ipc`
- `count_patents_by_applicant`
- `count_patents_by_inventor`
- compare-intent query families

- [ ] **Step 2: Add builder support in `client.py`**

Add explicit builder helpers that return:

- path IDs
- Cypher strings
- params

Keep builder output deterministic and alias-stable.

- [ ] **Step 3: Preserve the existing 9 fixed-template functions**

Do not rewrite legacy template logic. Build new parametric helpers alongside it.

- [ ] **Step 4: Re-run legacy client regression coverage**

Because this task modifies `client.py`, explicitly rerun the current legacy query-path tests as a regression guard.

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_client.py -q
```

- [ ] **Step 5: Run the planner/query-builder tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_planner_v2.py -q
```

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/graph_kb/client.py patent/tests/test_patent_graph_kb_client.py patent/tests/test_patent_graph_kb_planner_v2.py
git commit -m "feat: add patent graph parametric query builders"
```

### Task 4: Implement the planner

**Files:**
- Create: `patent/server/patent/graph_kb/planner_v2.py`
- Test: `patent/tests/test_patent_graph_kb_planner_v2.py`

- [ ] **Step 1: Expand planner tests**

Cover:

- legacy template question -> `PatentGraphQueryPlanV2(strategy="template")`
- inventor/agency/IPC-subclass/count question -> `strategy="parametric"`
- compare question -> parametric candidate set
- semantic/no-graph question -> `None`

- [ ] **Step 2: Implement `build_patent_graph_query_plan_v2(...)`**

Rules:

- template first
- parametric second
- no `llm_cypher` unless a future feature flag explicitly enables it

- [ ] **Step 3: Emit planner diagnostics**

Add diagnostics for:

- route family
- matched rule
- strategy
- legacy template ID when applicable
- candidate path IDs

- [ ] **Step 4: Run planner tests**

Run:

```bash
cd patent && PYTHONPATH=. pytest tests/test_patent_graph_kb_planner_v2.py -q
```

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/graph_kb/planner_v2.py patent/tests/test_patent_graph_kb_planner_v2.py
git commit -m "feat: add patent graph v2 planner"
```
