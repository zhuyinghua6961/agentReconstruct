# Patent Graph QA Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build patent graph QA parity so `patent` can internally route KB questions to direct graph answers, graph-enhanced RAG, or vector-only RAG while preserving existing gateway/file/vector behavior.

**Architecture:** Gateway remains a file-vs-KB router and does not learn Neo4j schema. Patent owns graph/vector/hybrid decisions inside `kb_qa` through the existing `route_patent_graph_kb_v2` tri-state contract. The implementation adds patent-specific slots, query templates, stronger strategy precedence, evidence-quality direct-answer policy, Stage2 retrieval use of graph candidates, and full graph-link logging/metadata.

**Tech Stack:** Python, FastAPI patent backend, Neo4j Python driver, pytest, existing patent staged RAG modules, existing patent graph V2 modules.

**Spec:** `docs/graph/2026-04-29-patent-graph-qa-parity-spec.md`

**Commit Policy:** Do not commit during task execution unless the user explicitly asks. The steps below use verification checkpoints instead of commit checkpoints.

---

## 1. Scope Boundaries

In scope:

- `patent/server/patent/graph_kb/`
- `patent/server/patent/kb_service.py`
- `patent/server/patent/stages/planning.py`
- `patent/server/patent/stages/retrieval.py`
- `patent/server/patent/retrieval_service.py`
- `patent/server/patent/cache_keys.py`
- patent tests under `patent/tests/`

Out of scope:

- changing gateway public route names;
- moving graph/vector routing into `gateway`;
- changing `pdf_qa`, `tabular_qa`, or gateway-level `hybrid_qa` semantics;
- restoring `archive/oldCode` code shape;
- adding free-form LLM-generated Cypher;
- committing or printing secrets.

## 2. File Structure

Create:

- `patent/server/patent/graph_kb/slots.py`
  - Deterministic patent slot extraction: patent IDs, IPC variants, applicants, inventors, agencies, material/process/metric terms, and intent flags.

- `patent/server/patent/graph_kb/query_templates.py`
  - Patent query template registry with allowlisted Cypher paths and template metadata.

- `patent/server/patent/graph_kb/metadata.py`
  - Graph route metadata builder so direct, graph-for-RAG, downgrade, and skip paths expose consistent diagnostics.

- `patent/tests/test_patent_graph_kb_slots.py`
  - Unit tests for slot extraction and plural-slot normalization.

- `patent/tests/test_patent_graph_kb_query_templates.py`
  - Unit tests for template registry, allowed labels/relationships, stale-label protection, IPC behavior, caps, and slot-to-param mapping.

- `patent/tests/test_patent_graph_kb_metadata.py`
  - Unit tests for graph metadata shape and redaction.

Modify:

- `patent/server/patent/graph_kb/models.py`
  - Add dataclasses for slots/template paths if needed, or minimally extend existing `PatentGraphQueryPlanV2`, `PatentGraphRagPayload`, and diagnostics fields.

- `patent/server/patent/graph_kb/classifier_v2.py`
  - Consume normalized slots instead of re-parsing independently.

- `patent/server/patent/graph_kb/query_strategy.py`
  - Enforce deterministic strategy priority from the spec.

- `patent/server/patent/graph_kb/planner_v2.py`
  - Build query plans from slots and template registry; preserve legacy-compatible plan fields during transition.

- `patent/server/patent/graph_kb/client.py`
  - Retain legacy execution helpers while moving new path definitions into `query_templates.py`; do not expand ad hoc Cypher here.

- `patent/server/patent/graph_kb/executor_v2.py`
  - Execute registry paths with guardrail; emit execution trace diagnostics.

- `patent/server/patent/graph_kb/schema_registry.py`
  - Keep schema aligned with real patent labels/relationships and embodiment relationship.

- `patent/server/patent/graph_kb/canonicalizer.py`
  - Implement evidence-quality direct-answer policy and graph constraints.

- `patent/server/patent/graph_kb/rag_adapter.py`
  - Build richer Stage1/Stage2/Stage4 graph payload and stable fingerprints.

- `patent/server/patent/graph_kb/direct_renderer.py`
  - Render new direct templates with truncation/cap metadata.

- `patent/server/patent/graph_kb/service.py`
  - Add detailed route logs and metadata while preserving tri-state behavior.

- `patent/server/patent/kb_service.py`
  - Preserve `kb_qa`-only graph preflight, attach graph metadata, and avoid file route interference.

- `patent/server/patent/stages/planning.py`
  - Preserve graph seeded claims and include new graph fields.

- `patent/server/patent/stages/retrieval.py`
  - Surface graph retrieval behavior metadata from Stage2.

- `patent/server/patent/orchestrators/generation.py`
  - Pass graph-bearing `conversation_context` into Stage2 and include graph Stage2 signature in Stage2 cache identity.

- `patent/server/patent/runtime.py`
  - Accept Stage2 `conversation_context` and forward it to `run_stage2_targeted_retrieval`.

- `patent/server/patent/models.py`
  - Update `PatentGenerationRuntime` Protocol for Stage2 context propagation.

- `patent/server/patent/retrieval_service.py`
  - Apply graph candidate patent IDs as filter/bias/hints where supported.

- `patent/server/patent/cache_keys.py`
  - Include new graph payload fields in normalized cache identity without raw row dumps.

Test files to extend:

- `patent/tests/test_patent_graph_kb_classifier_v2.py`
- `patent/tests/test_patent_graph_kb_query_strategy.py`
- `patent/tests/test_patent_graph_kb_planner_v2.py`
- `patent/tests/test_patent_graph_kb_executor_v2.py`
- `patent/tests/test_patent_graph_kb_guardrail.py`
- `patent/tests/test_patent_graph_kb_schema_registry.py`
- `patent/tests/test_patent_graph_kb_canonicalizer.py`
- `patent/tests/test_patent_graph_kb_rag_adapter.py`
- `patent/tests/test_patent_graph_kb_direct_renderer.py`
- `patent/tests/test_patent_graph_kb_service_v2.py`
- `patent/tests/test_patent_kb_service.py`
- `patent/tests/test_patent_stage1_graph_context.py`
- `patent/tests/test_patent_stage4_graph_context.py`
- `patent/tests/test_patent_retrieval_service.py`
- `patent/tests/test_patent_graph_kb_stage1_cache_keys.py`
- `patent/tests/test_patent_file_routes.py`

## 3. Implementation Tasks

### Task 1: Patent Slot Extraction

**Files:**

- Create: `patent/server/patent/graph_kb/slots.py`
- Modify: `patent/server/patent/graph_kb/models.py`
- Test: `patent/tests/test_patent_graph_kb_slots.py`

**Purpose:** Provide a single deterministic source for patent entity and intent extraction so classifier, planner, templates, and metadata do not parse the same question differently.

- [ ] **Step 1: Add failing tests for entity slots**

Test cases:

```python
def test_extracts_patent_ids_preserving_order_and_deduping():
    slots = extract_patent_graph_slots("比较 CN100355122C 和 cn100355122c 以及 CN100369314C")
    assert slots.patent_ids == ("CN100355122C", "CN100369314C")

def test_distinguishes_ipc_grains():
    slots = extract_patent_graph_slots("H01M H01M10 H01M10/0525 下有哪些专利")
    assert slots.ipc_prefixes == ("H01M",)
    assert slots.ipc_code_prefixes == ("H01M10",)
    assert slots.ipc_full_codes == ("H01M10/0525",)
```

Run:

```bash
cd patent && pytest tests/test_patent_graph_kb_slots.py -q
```

Expected: fails because `slots.py` does not exist.

- [ ] **Step 2: Add failing tests for organization/person/agency slots**

Test cases:

```python
def test_extracts_applicant_without_stealing_inventor_prefix():
    assert extract_patent_graph_slots("宁德时代新能源科技股份有限公司有哪些专利").applicant_names == ("宁德时代新能源科技股份有限公司",)
    assert extract_patent_graph_slots("发明人李长东有哪些专利").inventor_names == ("李长东",)
    assert extract_patent_graph_slots("代理机构北京三聚阳光知识产权代理有限公司有哪些专利").agency_names == ("北京三聚阳光知识产权代理有限公司",)
```

- [ ] **Step 3: Add failing tests for patent technical slots and intent flags**

Test cases:

```python
def test_extracts_process_material_metric_and_intents():
    slots = extract_patent_graph_slots("为什么喷雾干燥能提升磷酸铁锂倍率性能？")
    assert "喷雾干燥" in slots.process_terms
    assert "磷酸铁锂" in slots.material_terms
    assert "倍率性能" in slots.metric_terms
    assert slots.asks_why_how is True
```

- [ ] **Step 4: Implement `PatentGraphQuestionSlots` dataclass**

Add a frozen dataclass either in `models.py` or `slots.py`. Keep it explicit and serializable:

```python
@dataclass(frozen=True)
class PatentGraphQuestionSlots:
    normalized_question: str
    patent_ids: tuple[str, ...] = ()
    ipc_prefixes: tuple[str, ...] = ()
    ipc_code_prefixes: tuple[str, ...] = ()
    ipc_full_codes: tuple[str, ...] = ()
    applicant_names: tuple[str, ...] = ()
    inventor_names: tuple[str, ...] = ()
    agency_names: tuple[str, ...] = ()
    material_terms: tuple[str, ...] = ()
    material_role_terms: tuple[str, ...] = ()
    process_terms: tuple[str, ...] = ()
    metric_terms: tuple[str, ...] = ()
    atmosphere_terms: tuple[str, ...] = ()
    asks_lookup: bool = False
    asks_list: bool = False
    asks_count: bool = False
    asks_compare: bool = False
    asks_rank: bool = False
    asks_process: bool = False
    asks_materials: bool = False
    asks_experiment: bool = False
    asks_problem_solution: bool = False
    asks_inventive_scope: bool = False
    asks_citation: bool = False
    asks_atmosphere: bool = False
    asks_embodiment: bool = False
    asks_why_how: bool = False
    asks_trend_landscape: bool = False
    asks_followup: bool = False
    has_doi: bool = False
```

- [ ] **Step 5: Implement `extract_patent_graph_slots(question)`**

Rules:

- normalize whitespace;
- uppercase patent IDs;
- dedupe while preserving order;
- classify `H01M` as `ipc_prefixes`;
- classify `H01M10` as `ipc_code_prefixes`;
- classify `H01M10/0525` as `ipc_full_codes`;
- parse applicant/inventor/agency with anchored regexes;
- identify terms by curated patent dictionaries, not broad arbitrary noun extraction;
- set intent booleans from deterministic hint lists.

- [ ] **Step 6: Run slot tests**

Run:

```bash
cd patent && pytest tests/test_patent_graph_kb_slots.py -q
```

Expected: pass.

### Task 2: Patent Query Template Registry

**Files:**

- Create: `patent/server/patent/graph_kb/query_templates.py`
- Modify: `patent/server/patent/graph_kb/models.py`
- Modify: `patent/server/patent/graph_kb/schema_registry.py`
- Test: `patent/tests/test_patent_graph_kb_query_templates.py`
- Test: `patent/tests/test_patent_graph_kb_schema_registry.py`
- Test: `patent/tests/test_patent_graph_kb_guardrail.py`

**Purpose:** Move new patent Cypher paths into an inspectable registry so strategy, planner, guardrail, and tests can reason over templates without adding more ad hoc Cypher to `client.py`.

- [ ] **Step 1: Add failing tests for template coverage**

Expected registry IDs:

```python
{
    "lookup_patent_by_id",
    "list_patent_process_steps",
    "list_patent_material_roles",
    "list_patent_experiment_tables",
    "list_patent_problem_solution",
    "list_patent_inventive_scope",
    "list_patent_citations",
    "list_patent_atmospheres",
    "list_patent_embodiment_insights",
    "list_patents_by_applicant",
    "count_patents_by_applicant",
    "list_patents_by_inventor",
    "count_patents_by_inventor",
    "list_patents_by_agency",
    "count_patents_by_agency",
    "list_patents_by_ipc_prefix",
    "count_patents_by_ipc_prefix",
    "list_patents_by_ipc_code_prefix",
    "count_patents_by_ipc_code_prefix",
    "list_patents_by_ipc_full_code",
    "count_patents_by_ipc_full_code",
    "compare_patents_process_steps",
    "compare_patents_material_roles",
    "compare_patents_problem_solution",
    "compare_patents_performance_facts",
    "compare_patents_claim_scope",
    "list_patents_by_material",
    "list_patents_by_material_role",
    "list_patents_by_process_term",
    "performance_by_process_term",
    "performance_by_material_term",
    "rank_materials_by_frequency",
    "rank_processes_by_frequency",
}
```

- [ ] **Step 2: Add failing stale-label tests**

Test requirements:

- no template Cypher contains stale labels like `:doi`, `:Paper`, `:Article`, `:Sample`, `:recipe`, `:process`, `:testing`, `:name`, `:title`, `:__Chunk__`, `:__Document__`;
- valid properties such as `.name` and `.title` are allowed.

- [ ] **Step 3: Add failing IPC template tests**

Assertions:

```python
assert template("list_patents_by_ipc_prefix").cypher contains "IPCPrefix"
assert template("list_patents_by_ipc_code_prefix").cypher contains "STARTS WITH"
assert template("list_patents_by_ipc_full_code").cypher contains "ipc.code ="
```

- [ ] **Step 4: Add query path dataclass**

Use a compact dataclass compatible with existing executor needs:

```python
@dataclass(frozen=True)
class PatentGraphQueryTemplate:
    template_id: str
    cypher: str
    required_params: tuple[str, ...]
    optional_params: tuple[str, ...] = ()
    expected_columns: tuple[str, ...] = ()
    direct_answer_eligible: bool = False
    route_family: str = "precise"
    result_cap: int = 20
```

If adding to `models.py` would make imports cleaner, define there and import into `query_templates.py`.

- [ ] **Step 5: Implement registry helpers**

Required functions:

```python
def get_patent_query_template(template_id: str) -> PatentGraphQueryTemplate | None: ...
def list_patent_query_templates() -> tuple[PatentGraphQueryTemplate, ...]: ...
def build_patent_template_candidate(template_id: str, params: dict[str, Any], *, limit: int) -> dict[str, Any] | None: ...
def build_patent_template_candidates(slots: PatentGraphQuestionSlots, *, limit: int) -> tuple[dict[str, Any], ...]: ...
```

Candidate dicts should match executor expectations:

```python
{
    "path_id": template.template_id,
    "template_id": template.template_id,
    "cypher": normalized_cypher,
    "params": {...},
    "direct_answer_eligible": template.direct_answer_eligible,
    "expected_columns": template.expected_columns,
}
```

- [ ] **Step 6: Implement single-patent facet templates**

Templates must cover:

- `lookup_patent_by_id`;
- `list_patent_process_steps`;
- `list_patent_material_roles`;
- `list_patent_experiment_tables`;
- `list_patent_problem_solution`;
- `list_patent_inventive_scope`;
- `list_patent_citations`;
- `list_patent_atmospheres`;
- `list_patent_embodiment_insights`.

Requirements:

- use `Patent-HAS_EMBODIMENT_INSIGHT-EmbodimentInsight` for embodiment;
- return `p.patent_id AS patent_id` and title when possible;
- include `p.stub AS stub`;
- order process steps deterministically using available order/position fields and `NEXT_STEP` only if current data supports it safely;
- cap rows with `$limit`.

- [ ] **Step 7: Implement entity/list/count templates**

Templates must cover applicant, inventor, agency, IPC prefix, IPC code prefix, and IPC full code.

IPC behavior:

- `H01M` -> `IPCPrefix.subclass`;
- `H01M10` -> `IPC.code STARTS WITH $ipc_code_prefix`;
- `H01M10/0525` -> `IPC.code = $ipc_full_code`.

- [ ] **Step 8: Implement comparison and hybrid/community templates**

Templates must return bounded graph evidence for graph-for-RAG, not direct answers:

- process comparison;
- material-role comparison;
- problem/solution comparison;
- performance-fact comparison;
- claim/protection comparison;
- material/process candidate lists;
- process/material performance evidence;
- co-occurrence/landscape grouped rows where selective anchors exist.

- [ ] **Step 9: Add mandatory material/process/rank boundary tests**

Add tests that enforce:

- `list_patents_by_material_role` exists and uses `MaterialRole`;
- `rank_materials_by_frequency` exists, returns grouped counts, and is direct only for pure bounded ranking questions;
- `rank_processes_by_frequency` exists, returns grouped counts, and is direct only for pure bounded ranking questions;
- material/process count/list/rank templates expose caps/truncation metadata fields;
- analysis questions using the same material/process terms choose graph-for-RAG rather than direct answer.

- [ ] **Step 10: Update schema registry tests**

Ensure:

- `HAS_EMBODIMENT_INSIGHT` is allowed;
- `EmbodimentInsight` is allowed;
- valid `name`/`title` properties remain in field specs;
- stale labels are not allowed labels.

- [ ] **Step 11: Run template and guardrail tests**

Run:

```bash
cd patent && pytest \
  tests/test_patent_graph_kb_query_templates.py \
  tests/test_patent_graph_kb_schema_registry.py \
  tests/test_patent_graph_kb_guardrail.py -q
```

Expected: pass.

### Task 3: Classifier, Strategy, and Planner Wiring

**Files:**

- Modify: `patent/server/patent/graph_kb/classifier_v2.py`
- Modify: `patent/server/patent/graph_kb/query_strategy.py`
- Modify: `patent/server/patent/graph_kb/planner_v2.py`
- Modify: `patent/server/patent/graph_kb/client.py`
- Test: `patent/tests/test_patent_graph_kb_classifier_v2.py`
- Test: `patent/tests/test_patent_graph_kb_query_strategy.py`
- Test: `patent/tests/test_patent_graph_kb_planner_v2.py`

**Purpose:** Make routing deterministic and patent-specific, with specific facets outranking generic lookup and graph/vector/hybrid choices matching the spec.

- [ ] **Step 1: Add failing classifier tests for direct precise facets**

Cases:

- `CN100355122C 的工艺步骤是什么？` -> `direct_answer`, `precise`;
- `CN100355122C 使用了哪些原料？` -> `direct_answer`, `precise`;
- `CN100355122C 的技术问题和技术方案是什么？` -> `direct_answer`, `precise`;
- `CN100355122C 的发明点和保护范围是什么？` -> `direct_answer`, `precise`;
- `CN100355122C 的气氛条件是什么？` -> `direct_answer`, `precise`;
- `CN101209823B 的实施例洞察是什么？` -> `direct_answer`, `precise`.

- [ ] **Step 2: Add failing classifier tests for hybrid/community/skip**

Cases:

- `比较 CN100355122C 和 CN100369314C 的工艺步骤差异` -> `graph_for_rag`, `hybrid`;
- `CN100355122C 为什么能提升大电流放电性能？` -> `graph_for_rag`, `hybrid`;
- `为什么喷雾干燥能提升磷酸铁锂性能？` -> `graph_for_rag`, `hybrid` when slots contain process/material/metric anchors;
- `宁德时代在磷酸铁锂方面的工艺路线有什么特点？` -> `graph_for_rag`, `community`;
- `10.xxxx/xxxx 这篇文献是什么？` -> `skip_graph`, `semantic`.

- [ ] **Step 3: Add failing strategy precedence tests**

Assertions:

- atmosphere beats generic lookup;
- embodiment beats generic lookup;
- process/material/problem-solution/inventive-scope/citation all beat generic lookup;
- comparison beats single-patent lookup when 2+ patent IDs exist;
- IPC prefix/full/code-prefix pick distinct strategies.
- material/process list/count/rank questions pick bounded direct-capable registry templates;
- material/process why/how/landscape questions with the same terms pick graph-for-RAG templates.

- [ ] **Step 4: Update classifier to consume slots**

Implementation notes:

- call `extract_patent_graph_slots(question)` once;
- include slot summary in diagnostics;
- keep `_contains_file_context` behavior, but only downgrade direct to graph-for-RAG for `kb_qa` context cases;
- do not classify gateway file routes here. `PatentKbService` already gates graph preflight by `request.route == "kb_qa"`.

- [ ] **Step 5: Update strategy selection**

Replace current `template`-first rule with spec priority:

1. skip safety;
2. multi-patent comparison;
3. single-patent specific facet;
4. single-patent generic lookup;
5. entity count;
6. entity list;
7. rank/table;
8. hybrid anchored explanation;
9. community/landscape;
10. semantic skip.

Return a strategy that can be executed by the template registry. Keep legacy `template` support only for existing tests or fallback paths, not as the highest-priority path.

- [ ] **Step 6: Update planner to build registry candidates**

Planner requirements:

- accept `decision`;
- reuse slots from decision diagnostics when present;
- call `build_patent_template_candidates(slots, limit=...)`;
- store candidate queries in `parametric_slots["candidate_queries"]`;
- set `intent` to chosen path/template ID;
- set `legacy_template_id` only when using the old legacy template path;
- include `selected_template_id`, `candidate_path_ids`, and selected params in diagnostics.

- [ ] **Step 7: Keep `client.py` backward-compatible**

Do not delete legacy helpers. Existing tests may still import:

- `plan_patent_graph_query`;
- `build_patent_parametric_query_candidates`;
- `execute_patent_graph_plan`.

Adjust `build_patent_parametric_query_candidates` to delegate to `query_templates.py` where feasible, or leave legacy paths and make planner prefer registry candidates.

- [ ] **Step 8: Run classifier/strategy/planner tests**

Run:

```bash
cd patent && pytest \
  tests/test_patent_graph_kb_slots.py \
  tests/test_patent_graph_kb_query_templates.py \
  tests/test_patent_graph_kb_classifier_v2.py \
  tests/test_patent_graph_kb_query_strategy.py \
  tests/test_patent_graph_kb_planner_v2.py -q
```

Expected: pass.

### Task 4: Executor Guardrail and Graph Link Logging

**Files:**

- Modify: `patent/server/patent/graph_kb/executor_v2.py`
- Modify: `patent/server/patent/graph_kb/service.py`
- Create: `patent/server/patent/graph_kb/metadata.py`
- Test: `patent/tests/test_patent_graph_kb_executor_v2.py`
- Test: `patent/tests/test_patent_graph_kb_service_v2.py`
- Test: `patent/tests/test_patent_graph_kb_metadata.py`

**Purpose:** Make graph route decisions visible in logs and response metadata while keeping graph failures fail-open.

- [ ] **Step 1: Add failing metadata builder tests**

Expected metadata keys:

- `graph_pipeline_version`;
- `graph_kb_attempted`;
- `graph_kb_mode`;
- `graph_kb_route_family`;
- `graph_kb_strategy`;
- `graph_kb_template_id`;
- `graph_kb_path_id`;
- `graph_kb_fingerprint`;
- `graph_kb_row_count`;
- `graph_kb_evidence_quality`;
- `graph_kb_downgrade_reason`;
- `graph_kb_stage2_behavior`.

Ensure no secrets or raw row dumps appear.

- [ ] **Step 2: Implement metadata builder**

Add helpers:

```python
def build_patent_graph_route_metadata(...): ...
def summarize_patent_graph_slots(slots: PatentGraphQuestionSlots) -> dict[str, Any]: ...
```

Keep summaries bounded:

- counts and selected values only;
- no raw unbounded rows;
- no credentials.

- [ ] **Step 3: Add executor trace tests**

Mock candidate queries and Neo4j client to verify:

- guardrail rejection records `guardrail_reject`;
- timeout records `timeout`;
- empty rows records `empty_result`;
- successful path records `matched_path`;
- executor respects `max_path_attempts`;
- query rows are capped by `max_rows`.

- [ ] **Step 4: Add route log tests using `caplog`**

Verify graph-attempted requests log:

- `patent_graph.route_start`;
- `patent_graph.slots_done`;
- `patent_graph.classify_done`;
- `patent_graph.plan_done`;
- `patent_graph.guardrail_done` or executor equivalent;
- `patent_graph.execute_done`;
- `patent_graph.canonicalize_done`;
- `patent_graph.rag_payload_done`;
- `patent_graph.direct_render_done`;
- `patent_graph.route_end`.

- [ ] **Step 5: Implement structured logs**

Use `logging.getLogger("patent.graph_kb")` or existing module loggers. Keep log messages stable and grep-friendly:

```python
_LOGGER.info(
    "patent_graph.classify_done mode=%s route_family=%s matched_rule=%s trace=%s",
    decision.mode,
    decision.route_family,
    matched_rule,
    trace_id,
)
```

If `trace_id` is not currently passed to `route_patent_graph_kb_v2`, add an optional `trace_id: str = ""` parameter and pass it from `PatentKbService`.

- [ ] **Step 6: Run executor/service metadata tests**

Run:

```bash
cd patent && pytest \
  tests/test_patent_graph_kb_executor_v2.py \
  tests/test_patent_graph_kb_service_v2.py \
  tests/test_patent_graph_kb_metadata.py -q
```

Expected: pass.

### Task 5: Evidence Quality and Direct-Answer Policy

**Files:**

- Modify: `patent/server/patent/graph_kb/canonicalizer.py`
- Modify: `patent/server/patent/graph_kb/direct_renderer.py`
- Test: `patent/tests/test_patent_graph_kb_canonicalizer.py`
- Test: `patent/tests/test_patent_graph_kb_direct_renderer.py`

**Purpose:** Stop treating `stub=true` as an automatic direct-answer blocker and evaluate whether the requested facet actually has usable rows.

- [ ] **Step 1: Add failing canonicalizer tests for `stub=true` with populated facet rows**

Examples:

```python
def test_stub_true_process_rows_can_be_direct_answerable():
    bundle = canonicalize_patent_graph_rows(
        plan=plan_for("list_patent_process_steps"),
        rows=[{"patent_id": "CN100355122C", "stub": True, "step_name": "干燥"}],
    )
    assert bundle.direct_answerable is True
    assert bundle.diagnostics["evidence_quality"]["has_requested_facet"] is True
```

- [ ] **Step 2: Add failing tests for stub-only rows**

Example:

```python
def test_stub_only_lookup_not_direct_for_facet_question():
    bundle = canonicalize_patent_graph_rows(
        plan=plan_for("list_patent_process_steps"),
        rows=[{"patent_id": "CN100355122C", "stub": True, "title": "一种提高...的方法"}],
    )
    assert bundle.direct_answerable is False
    assert bundle.diagnostics["evidence_quality"]["is_stub_only"] is True
```

- [ ] **Step 3: Add failing tests for broad/truncated rows**

Requirements:

- if row count exceeds display cap, metadata marks truncation;
- direct eligible only for list/count/rank templates explicitly marked direct;
- comparison/hybrid/community templates are not direct-answerable.

- [ ] **Step 4: Implement evidence-quality helper**

Add internal function:

```python
def _evaluate_evidence_quality(plan: PatentGraphQueryPlanV2, rows: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    ...
```

Required fields:

- `has_rows`;
- `has_requested_facet`;
- `has_textual_fact`;
- `has_identifier`;
- `is_bounded`;
- `is_partial`;
- `is_stub_only`;
- `has_measurement_value`.

- [ ] **Step 5: Use template/path direct eligibility**

Direct-answer logic should require:

- decision mode is direct;
- rows exist;
- requested facet exists;
- template/path is direct eligible;
- renderer supports the path;
- result is bounded or explicitly truncated.

Do not require `stub == False` if the requested relationship/facet fields are populated.

- [ ] **Step 6: Update direct renderer for new paths**

Renderer must support:

- process steps;
- material roles;
- experiment tables/measurements;
- problem/solution/scenario;
- inventive/performance/protection/claim;
- citations;
- atmospheres;
- embodiment insights;
- entity list/count;
- IPC list/count.

Rendering requirements:

- preserve original values;
- include patent IDs in references when available;
- include truncation/cap metadata;
- return `handled=False` with a specific reason instead of empty text.

- [ ] **Step 7: Run canonicalizer and renderer tests**

Run:

```bash
cd patent && pytest \
  tests/test_patent_graph_kb_canonicalizer.py \
  tests/test_patent_graph_kb_direct_renderer.py -q
```

Expected: pass.

### Task 6: Graph RAG Payload, Cache, and Stage1 Context

**Files:**

- Modify: `patent/server/patent/graph_kb/rag_adapter.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/stages/planning.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/cache_keys.py`
- Test: `patent/tests/test_patent_graph_kb_rag_adapter.py`
- Test: `patent/tests/test_patent_stage1_graph_context.py`
- Test: `patent/tests/test_patent_graph_kb_stage1_cache_keys.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`
- Test: `patent/tests/test_patent_kb_service.py`

**Purpose:** Make graph-for-RAG payload deterministic, compact, cache-safe, and useful to Stage1.

- [ ] **Step 1: Add failing RAG adapter tests for enriched payload**

Assert payload contains:

- `mode`;
- `route_family`;
- `strategy`;
- `template_id` or `path_id`;
- `stage1_context_block`;
- `stage1_seed_claims` if added to model;
- `stage2_patent_candidates`;
- `stage2_entity_hints`;
- `stage2_constraints`;
- `stage4_fact_block`;
- `cache_fingerprint`;
- diagnostics with row count/evidence quality.

- [ ] **Step 2: Add failing graph cache-key tests**

Requirements:

- graph payload changes cache key when candidate patents or constraints change;
- graph skip does not change vector-only cache behavior;
- raw unbounded rows are not part of cache identity;
- fingerprint is stable for equivalent payloads.

- [ ] **Step 3: Add failing Stage2 cache fingerprint tests**

Current `build_stage2_cache_fingerprint` does not receive graph context. Add tests proving:

- different `conversation_context["graph_kb"]["stage2_patent_candidates"]` values produce different Stage2 fingerprints;
- different `stage2_constraints` values produce different Stage2 fingerprints;
- volatile graph diagnostics do not affect Stage2 fingerprints;
- absence of `graph_kb` keeps the existing vector-only fingerprint stable.

Expected target API can be either:

```python
build_stage2_cache_fingerprint(..., conversation_context=conversation_context)
```

or:

```python
build_stage2_cache_fingerprint(..., graph_stage2_signature=signature)
```

The selected implementation must be explicit and covered by orchestrator tests.

- [ ] **Step 4: Extend `PatentGraphRagPayload` if needed**

Only add fields that have consumers or tests:

- `route_family`;
- `strategy`;
- `template_id`;
- `path_id`;
- `stage1_seed_claims`;
- `stage2_retrieval_behavior` metadata placeholder.

Keep backward compatibility in `__post_init__`.

- [ ] **Step 5: Update `build_patent_graph_rag_payload`**

Build:

- compact stage1 block;
- bounded facts;
- deduped patent candidates;
- constraints from canonicalizer;
- entity hints for IPC, organizations, inventors, materials, processes, metrics;
- fingerprint from stable serialized fields.

- [ ] **Step 6: Update `PatentKbService._payload_to_conversation_context`**

Pass through new fields with bounded values. Include:

- `route_family`;
- `strategy`;
- `template_id`;
- `path_id`;
- `cache_fingerprint`;
- `stage2_retrieval_behavior` when later populated.

- [ ] **Step 7: Update Stage1 seeded claims**

In `patent/server/patent/stages/planning.py`, ensure `_seed_retrieval_claims_from_graph` includes:

- candidate patent IDs as keywords;
- IPC/applicant/inventor/material/process/metric hints;
- first graph fact as claim context;
- `filters={"graph_seeded": True, "graph_candidate_patent_ids": [...]}`.

- [ ] **Step 8: Update Stage2 cache fingerprint wiring**

Implementation requirements:

- add a stable graph Stage2 signature from `conversation_context["graph_kb"]`;
- include candidate patent IDs, constraints, entity hints, route family, strategy, template/path, and cache fingerprint;
- exclude volatile diagnostics and raw row dumps;
- call the updated `build_stage2_cache_fingerprint` from `PatentGenerationOrchestrator.run(...)`;
- add tests in `patent/tests/test_patent_generation_orchestrator.py` proving the orchestrator passes graph context/signature into Stage2 cache fingerprint.

- [ ] **Step 9: Run payload/cache/stage1 tests**

Run:

```bash
cd patent && pytest \
  tests/test_patent_graph_kb_rag_adapter.py \
  tests/test_patent_stage1_graph_context.py \
  tests/test_patent_graph_kb_stage1_cache_keys.py \
  tests/test_patent_generation_orchestrator.py \
  tests/test_patent_kb_service.py -q
```

Expected: pass.

### Task 7: Stage2 Retrieval Uses Graph Candidates

**Files:**

- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/models.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server/patent/stages/planning.py`
- Test: `patent/tests/test_patent_retrieval_service.py`
- Test: `patent/tests/test_patent_stage1_graph_context.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`
- Test: `patent/tests/test_patent_kb_service.py`

**Purpose:** Prove graph-for-RAG is not only prompt decoration. Candidate patent IDs and graph constraints must affect Stage2 retrieval as filter, bias, deterministic hints, or recorded fallback.

- [ ] **Step 1: Add failing orchestrator/runtime context propagation tests**

Current Stage2 does not receive `conversation_context`. Add tests proving:

- `PatentGenerationOrchestrator.run(..., conversation_context=...)` passes the same graph-bearing context into `runtime.stage2_targeted_retrieval(...)`;
- `PatentGenerationRuntime` Protocol allows `conversation_context`;
- `PatentRuntime.stage2_targeted_retrieval(..., conversation_context=...)` forwards it to `run_stage2_targeted_retrieval(..., context=conversation_context)`;
- existing fake runtimes that do not accept the new keyword are either updated or called through `_callable_accepts_keyword`-style compatibility so old tests remain stable.

Example target assertion:

```python
assert captured_stage2_context["graph_kb"]["stage2_patent_candidates"] == ["CN100355122C"]
```

- [ ] **Step 2: Update Stage2 method signatures**

Implementation requirements:

- update `PatentGenerationRuntime.stage2_targeted_retrieval` Protocol in `models.py`;
- update concrete runtime in `runtime.py`;
- update orchestrator call in `orchestrators/generation.py`;
- pass `conversation_context=conversation_context` into `runtime.stage2_targeted_retrieval`;
- pass `context=conversation_context` into `run_stage2_targeted_retrieval`;
- preserve `should_cancel=None` and `active_stream_count=None` behavior.

- [ ] **Step 3: Add failing retrieval-service tests for candidate filters**

Use fake vector search functions and a context:

```python
context = {
    "graph_kb": {
        "stage2_patent_candidates": ["CN100355122C", "CN100369314C"],
        "stage2_constraints": [{"field": "patent.id", "operator": "in", "value": ["CN100355122C", "CN100369314C"]}],
    }
}
```

Assert vector search receives candidate IDs or retrieval metadata records why it could not.

- [ ] **Step 4: Add failing tests for no-hit fallback**

When graph candidate filtering yields no vector hits:

- fallback to default patent anchors or normal retrieval;
- metadata includes `graph_stage2_behavior="fallback_no_vector_hits"`;
- final payload is not empty solely because graph candidates missed.

- [ ] **Step 5: Add failing tests for hint-only behavior**

When graph payload has facts but no candidate patent IDs:

- retrieval uses generated claims/hints;
- metadata includes `graph_stage2_behavior="hint_only"`.

- [ ] **Step 6: Implement graph controls extraction**

Add internal helper in `retrieval_service.py` or `stages/retrieval.py`:

```python
def _graph_retrieval_controls(context: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "candidate_patent_ids": (...),
        "constraints": (...),
        "entity_hints": {...},
        "behavior": "filter_applied" | "bias_applied" | "hint_only" | "none",
    }
```

- [ ] **Step 7: Apply controls in targeted retrieval**

Preferred behavior:

- if candidate patent IDs exist, pass them into `_vector_matches(..., candidate_patent_ids=...)`;
- preserve existing exact-ID behavior;
- if hard candidate filtering is unavailable in a code path, add candidates to generated claim keywords and metadata;
- fallback to default patent anchors or normal retrieval when candidate-filtered vector search misses.

- [ ] **Step 8: Surface metadata**

Stage2 retrieval result metadata should include:

- `graph_stage2_behavior`;
- `graph_candidate_patent_ids`;
- `graph_constraints_applied`;
- `graph_fallback_reason` when fallback occurs.

- [ ] **Step 9: Preserve final metadata path**

Implementation requirements:

- Stage2 result metadata must retain `graph_stage2_behavior`;
- orchestrator raw metadata must include the Stage2 graph behavior;
- `PatentKbService._apply_graph_metadata` must merge graph route metadata without overwriting actual Stage2 behavior;
- final response metadata must expose one of `filter_applied`, `bias_applied`, `hint_only`, or `fallback_no_vector_hits` when graph-for-RAG reached Stage2.

- [ ] **Step 10: Run Stage2 tests**

Run:

```bash
cd patent && pytest \
  tests/test_patent_retrieval_service.py \
  tests/test_patent_stage1_graph_context.py \
  tests/test_patent_generation_orchestrator.py \
  tests/test_patent_kb_service.py -q
```

Expected: pass.

### Task 8: End-to-End Graph Service Integration

**Files:**

- Modify: `patent/server/patent/graph_kb/service.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/server/patent/stages/synthesis.py`
- Test: `patent/tests/test_patent_graph_kb_service_v2.py`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/test_patent_answering_graph_context.py`
- Test: `patent/tests/test_patent_stage4_graph_context.py`
- Test: `patent/tests/test_patent_stage4_synthesis.py`

**Purpose:** Ensure the tri-state graph path returns direct answers, injects graph-for-RAG payloads, or skips graph without disrupting existing patent KB behavior.

- [ ] **Step 1: Add failing direct-answer integration tests**

Mock Neo4j rows for:

- process steps;
- material roles;
- problem/solution;
- atmosphere;
- embodiment;
- applicant/inventor/IPC list/count.

Assert:

- `route_patent_graph_kb_v2` returns `direct_answer`;
- `PatentKbService.run` returns without invoking staged runtime;
- metadata includes graph route family, mode, path/template, evidence quality.

- [ ] **Step 2: Add failing graph-for-RAG tests**

Cases:

- multi-patent compare;
- patent ID plus why/how;
- process/material performance explanation.

Assert:

- routing mode is `graph_for_rag`;
- `conversation_context["graph_kb"]` is injected;
- staged runtime receives graph context;
- final execution metadata includes graph fingerprint and Stage2 behavior.

- [ ] **Step 3: Add failing skip/fail-open tests**

Cases:

- DOI-only;
- no graph signal;
- Neo4j unavailable;
- guardrail rejection;
- timeout;
- empty rows.

Assert existing staged RAG still runs or fallback result is returned according to current service behavior.

- [ ] **Step 4: Enforce `kb_qa`-only graph preflight**

Tests:

- `kb_qa` can call graph;
- `pdf_qa` does not call graph;
- `tabular_qa` does not call graph;
- gateway-level `hybrid_qa` does not call graph.

Do not modify gateway route semantics unless a test reveals patent receives wrong route data.

- [ ] **Step 5: Ensure Stage4 graph facts remain bounded**

Tests should verify:

- graph fact block appears in synthesis context;
- graph candidate patent IDs appear in final context metadata;
- long graph facts are truncated/bounded;
- Stage4 does not require graph for vector-only questions.

- [ ] **Step 6: Run integration tests**

Run:

```bash
cd patent && pytest \
  tests/test_patent_graph_kb_service_v2.py \
  tests/test_patent_kb_service.py \
  tests/test_patent_answering_graph_context.py \
  tests/test_patent_stage4_graph_context.py \
  tests/test_patent_stage4_synthesis.py \
  tests/test_patent_file_routes.py -q
```

Expected: pass.

### Task 9: Real Neo4j Smoke Harness and Manual Verification Matrix

**Files:**

- Create or modify: `docs/graph/patent_graph_parity_live_smoke.md`
- Optional create: `patent/tests/test_patent_graph_kb_live_smoke.py` with skip-by-default marker if the repo has an existing live-test convention.

**Purpose:** Document and optionally automate real local Neo4j smoke tests without committing credentials.

- [ ] **Step 1: Write smoke doc skeleton**

Include:

- prerequisites: `conda run -n agent ...`;
- use resource/shared config and secret env locally, but do not print secrets;
- expected logs to inspect;
- exact question matrix from the spec;
- expected mode for each question.

- [ ] **Step 2: Add optional skipped live tests if appropriate**

Only if existing test conventions allow:

```python
@pytest.mark.live_neo4j
@pytest.mark.skipif(not os.getenv("PATENT_LIVE_NEO4J"), reason="live Neo4j disabled")
def test_live_patent_process_steps(...):
    ...
```

If no convention exists, keep live smoke as documentation only.

- [ ] **Step 3: Smoke matrix**

Required questions:

- `CN100355122C 这件专利是什么？`
- `CN100355122C 的工艺步骤是什么？`
- `CN100355122C 使用了哪些原料？`
- `CN100355122C 的技术问题和技术方案是什么？`
- `CN100355122C 的发明点和保护范围是什么？`
- `CN100355122C 的气氛条件是什么？`
- `CN101209823B 的实施例洞察是什么？`
- `比较 CN100355122C 和 CN100369314C 的工艺步骤差异`
- `宁德时代新能源科技股份有限公司有哪些专利？`
- `发明人李长东有哪些专利？`
- `H01M 下有哪些专利？`
- `H01M 有多少专利？`
- `H01M10 下有哪些专利？`
- `H01M10 有多少专利？`
- `H01M10/0525 有多少专利？`
- `为什么喷雾干燥能提升磷酸铁锂性能？`
- `10.xxxx/xxxx 这篇文献是什么？`

- [ ] **Step 4: Run local unit suite before live smoke**

Run:

```bash
cd patent && pytest tests/test_patent_graph_kb_*.py tests/test_patent_kb_service.py tests/test_patent_retrieval_service.py -q
```

Expected: pass.

- [ ] **Step 5: Run live smoke only with approved local credentials**

If a local script/test exists, run it through `conda run -n agent`. If it requires network/socket access and sandbox blocks it, request escalation immediately instead of retrying repeatedly.

Expected:

- direct cases return direct graph or graph-for-RAG with explicit downgrade not caused by `stub=true` alone;
- graph-for-RAG cases include graph candidates/facts and Stage2 behavior metadata;
- skip cases preserve vector/RAG path;
- logs show all required graph events.

### Task 10: Regression Suite and Final Code Review

**Files:**

- No new production files expected.
- Review all files changed by Tasks 1-9.

**Purpose:** Verify graph parity work did not break existing patent vector/file behavior and prepare for review.

- [ ] **Step 1: Run focused graph suite**

Run:

```bash
cd patent && pytest \
  tests/test_patent_graph_kb_slots.py \
  tests/test_patent_graph_kb_query_templates.py \
  tests/test_patent_graph_kb_metadata.py \
  tests/test_patent_graph_kb_classifier_v2.py \
  tests/test_patent_graph_kb_query_strategy.py \
  tests/test_patent_graph_kb_planner_v2.py \
  tests/test_patent_graph_kb_executor_v2.py \
  tests/test_patent_graph_kb_guardrail.py \
  tests/test_patent_graph_kb_schema_registry.py \
  tests/test_patent_graph_kb_canonicalizer.py \
  tests/test_patent_graph_kb_rag_adapter.py \
  tests/test_patent_graph_kb_direct_renderer.py \
  tests/test_patent_graph_kb_service_v2.py -q
```

Expected: pass.

- [ ] **Step 2: Run patent KB and retrieval regression suite**

Run:

```bash
cd patent && pytest \
  tests/test_patent_kb_service.py \
  tests/test_patent_retrieval_service.py \
  tests/test_patent_generation_orchestrator.py \
  tests/test_patent_stage1_graph_context.py \
  tests/test_patent_stage4_graph_context.py \
  tests/test_patent_answering_graph_context.py \
  tests/test_patent_graph_kb_stage1_cache_keys.py \
  tests/test_patent_file_routes.py -q
```

Expected: pass.

- [ ] **Step 3: Run broader patent tests if time permits**

Run:

```bash
cd patent && pytest -q
```

Expected: pass. If this is too slow or environment-dependent, record the exact failure/blocker and run the focused suites.

- [ ] **Step 4: Inspect worktree**

Run:

```bash
git status --short
git diff --stat
```

Expected:

- no secrets;
- no unrelated changes;
- `CLAUDE.md` and `部门分级.xlsx` remain untouched unless the user separately requested otherwise.

- [ ] **Step 5: Request code review**

Open a fresh `gpt-5.5` high subagent for code review with:

- spec path;
- plan path;
- changed file list;
- test commands and results;
- explicit review focus:
  - graph/vector/file route regression;
  - direct-answer evidence quality;
  - IPC behavior;
  - Stage2 actually uses graph candidates;
  - no stale paper labels;
  - no secret exposure;
  - logging/metadata completeness.

- [ ] **Step 6: Apply review feedback and re-review until pass**

For each reviewer issue:

- verify against code before changing;
- fix blocking and important items;
- rerun focused tests;
- reuse the same reviewer subagent for re-review;
- stop only after reviewer returns PASS or after surfacing an unresolved technical disagreement to the user.

## 4. Task Dependency Order

Implement in this order:

1. Task 1: Patent Slot Extraction
2. Task 2: Patent Query Template Registry
3. Task 3: Classifier, Strategy, and Planner Wiring
4. Task 4: Executor Guardrail and Graph Link Logging
5. Task 5: Evidence Quality and Direct-Answer Policy
6. Task 6: Graph RAG Payload, Cache, and Stage1 Context
7. Task 7: Stage2 Retrieval Uses Graph Candidates
8. Task 8: End-to-End Graph Service Integration
9. Task 9: Real Neo4j Smoke Harness and Manual Verification Matrix
10. Task 10: Regression Suite and Final Code Review

The order is intentional:

- slots must exist before templates and classifier can share one contract;
- templates must exist before planner/strategy can pick stable paths;
- routing must be stable before executor/canonicalizer behavior is meaningful;
- canonical evidence must exist before direct rendering and RAG payload can be trusted;
- graph payload must exist before Stage2 can use candidates structurally;
- integration and smoke tests should come after individual components are covered.

## 5. Acceptance Checklist

- [ ] Patent graph direct answers work for single-patent structured facets.
- [ ] Applicant/inventor/agency/IPC list and count questions work.
- [ ] `H01M`, `H01M10`, and `H01M10/0525` route to distinct IPC strategies.
- [ ] `stub=true` no longer blocks direct answers when requested facet rows are usable.
- [ ] Specific facet templates outrank generic patent lookup.
- [ ] Multi-patent comparisons route to graph-for-RAG.
- [ ] Anchored why/how questions route to graph-for-RAG.
- [ ] Stage2 retrieval applies or records graph filter/bias/hint/fallback behavior.
- [ ] Broad vector-only questions still use existing patent RAG.
- [ ] `pdf_qa`, `tabular_qa`, and gateway-level `hybrid_qa` do not trigger patent graph preflight.
- [ ] Logs show route start, slots, classification, planning, guardrail, execution, canonicalization, RAG payload, direct rendering, and route end.
- [ ] Metadata includes graph mode, route family, strategy, path/template, row count, evidence quality, fingerprint, and Stage2 behavior.
- [ ] Stale paper labels are not used as Neo4j labels in patent templates.
- [ ] Valid `name` and `title` properties remain allowed.
- [ ] No secrets are committed or printed.
