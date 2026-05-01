# Richer Graph Direct Answer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich fastQA and patent graph direct answers with compact deterministic graph profiles while keeping direct-answer paths free of LLM calls.

**Architecture:** Extend only the graph query templates and direct renderers for existing direct-answer paths. Query templates first select and limit DOI/patent candidates, then expand bounded optional profile fields for those candidates; renderers format only non-empty fields and preserve existing references and fallback behavior. Routing, classifiers, staged RAG generation, graph-for-RAG payload contracts, and frontend rendering are out of scope.

**Tech Stack:** Python, FastAPI service modules, Neo4j Cypher templates, pytest.

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-05-01-richer-graph-direct-answer-design.md`
- fastQA graph renderer: `fastQA/app/modules/graph_kb/direct_renderer.py`
- fastQA graph templates: `fastQA/app/modules/graph_kb/query_templates.py`
- patent graph renderer: `patent/server/patent/graph_kb/direct_renderer.py`
- patent graph templates: `patent/server/patent/graph_kb/query_templates.py`

## Implementation Constraints

- Do not change graph classifiers, routing thresholds, or direct-answer vs graph-for-RAG decisions.
- Do not add LLM calls to direct-answer paths.
- Do not change public response shape for `references`, `reference_objects`, `query_mode`, `template_id`, `result_count`, or graph metadata.
- Do not commit during implementation unless the user explicitly asks.
- Keep unrelated untracked files untouched.
- Every broad list query must limit candidates before optional profile expansion. A final `LIMIT $limit` alone is not sufficient.
- Render at most 5 detailed profiles by default; keep references for all returned DOI/patent rows.

## File Structure

- Modify `fastQA/app/modules/graph_kb/direct_renderer.py`
  - Add deterministic value cleanup, truncation, compact-list formatting, and DOI profile rendering helpers.
  - Reuse existing `DirectAnswerResult` behavior and suspicious DOI filtering.
- Modify `fastQA/app/modules/graph_kb/query_templates.py`
  - Enrich direct list templates with bounded DOI profile fields.
  - Preserve existing path IDs, direct eligibility, params, and expected behavior.
- Modify `fastQA/tests/test_graph_kb_direct_renderer.py`
  - Add renderer tests for rich DOI profiles, dirty value cleanup, display caps, and reference preservation.
- Modify `fastQA/tests/test_graph_kb_query_templates.py`
  - Add query-shape tests proving candidate limiting happens before optional enrichment.
- Modify `fastQA/tests/test_graph_kb_service.py`
  - Add graph service regressions for direct returned mode and graph-for-RAG stability.
- Modify `fastQA/tests/test_fastqa_kb_graph_integration.py`
  - Strengthen router-level direct-answer tests proving staged generation is not invoked and emitted references remain compatible.
- Modify `patent/server/patent/graph_kb/direct_renderer.py`
  - Add deterministic value cleanup, truncation, compact-list formatting, rich patent listing rendering, and optional single-patent supplemental fields.
- Modify `patent/server/patent/graph_kb/query_templates.py`
  - Enrich broad patent listing templates with bounded profile fields after candidate limiting.
  - Add optional supplemental fields to single-patent lookup if needed and already available in graph.
- Modify `patent/tests/test_patent_graph_kb_direct_renderer.py`
  - Add renderer tests for rich patent profiles, missing-field fallback, caps, references, and single-patent supplemental output.
- Modify `patent/tests/test_patent_graph_kb_query_templates.py`
  - Add query-shape tests for bounded candidate-first Cypher on broad listing paths.
- Modify `patent/tests/test_patent_graph_kb_service_v2.py`
  - Add route-level regressions for direct handling and graph-for-RAG unchanged.
- Modify `patent/tests/test_patent_kb_service.py`
  - Strengthen no-staged-orchestrator behavior for graph direct answers if the current test does not cover the enriched path.

## Task 1: fastQA Direct Renderer Profiles

**Files:**
- Modify: `fastQA/tests/test_graph_kb_direct_renderer.py`
- Modify: `fastQA/app/modules/graph_kb/direct_renderer.py`

- [ ] **Step 1: Add a rich raw-material direct-renderer test**

Add a test with two DOI rows where the first row contains `matched_raw_materials`, `raw_materials`, `carbon_sources`, `carbon_contents`, `dopants`, `doping_elements`, `additives`, `preparation_methods`, `process_parameters`, `testing_items`, and `equipment`.

Assert:
- result is handled
- answer includes the existing overview heading
- answer includes DOI/title
- answer includes matched condition
- answer includes recipe/material context
- answer includes process/testing/equipment context
- references include every DOI returned by the bundle

Example assertion shape:

```python
assert "LiFePO4 powder" in result.answer
assert "solid-state synthesis" in result.answer
assert "Rate capability" in result.answer
assert result.references == ("10.1/a", "10.1/b")
```

- [ ] **Step 2: Add a display-cap/reference-preservation test**

Create 7 DOI rows, set all 7 in `doi_candidates` and `direct_render_dois`, and render `list_by_carbon_source`.

Assert:
- only the first 5 detailed profiles are rendered
- all 7 DOI references are preserved
- answer mentions there are additional matches when returned rows exceed displayed profiles

- [ ] **Step 3: Add dirty-value cleanup and truncation tests**

Use values containing `_null`, `null_null`, duplicated underscores, repeated whitespace, duplicates, and a very long fact string.

Assert:
- placeholders are not printed
- duplicate values are printed once
- long individual values are truncated
- clean prose remains readable

- [ ] **Step 4: Implement shared renderer helpers**

In `fastQA/app/modules/graph_kb/direct_renderer.py`, add helpers near existing `_clean_text` and `_clean_items`:

```python
_PROFILE_DISPLAY_LIMIT = 5
_LIST_ITEM_LIMIT = 3
_LONG_VALUE_LIMIT = 160

def _clean_graph_value(value: Any, *, limit: int = _LONG_VALUE_LIMIT) -> str:
    ...

def _dedupe_clean_items(values: Any, *, limit: int = _LIST_ITEM_LIMIT) -> list[str]:
    ...

def _format_compact_list(values: Any, *, limit: int = _LIST_ITEM_LIMIT) -> str:
    ...
```

Implementation requirements:
- accept scalar, list, tuple, and set inputs
- preserve order for list/tuple values
- remove placeholder fragments like `_null`, `null_null`, and duplicated underscores
- collapse whitespace
- omit empty placeholders such as `null`, `none`, `nan`, and `unknown` when they are standalone values
- truncate long values with a short suffix such as `...`

- [ ] **Step 5: Implement `_render_paper_profiles`**

Add a helper that receives `rows`, `references`, `heading_label`, `condition_label`, and `condition_key`.

Required output behavior:
- first section keeps the current overview style so existing tests remain compatible
- detailed profile count is capped at 5
- each profile starts with the same `### [n] title` shape currently used by `_render_list`
- bullets are included only when data exists
- supported bullets include DOI, matched condition, raw materials/recipe, method/parameters, testing/equipment
- recipe bullets should cover available `carbon_sources`, `carbon_contents`, `dopants`, `doping_elements`, and `additives`
- if a row has no enrichment fields, it still renders the DOI/title and matched condition like the old minimal answer

- [ ] **Step 6: Route existing list intents through the profile renderer**

Update existing branches for:
- `list_by_raw_material`
- `list_by_carbon_source`
- `list_by_process_method`
- `list_by_title_or_material`

Keep numeric direct-answer rendering unchanged.

- [ ] **Step 7: Run focused fastQA renderer tests**

Run:

```bash
cd fastQA && pytest tests/test_graph_kb_direct_renderer.py -q
```

Expected: all tests pass. If sandbox blocks pytest execution, rerun the same command with escalated permissions.

## Task 2: fastQA Candidate-First Enriched Query Templates

**Files:**
- Modify: `fastQA/tests/test_graph_kb_query_templates.py`
- Modify: `fastQA/app/modules/graph_kb/query_templates.py`

- [ ] **Step 1: Add query-shape tests for candidate-first limiting**

Add tests for:
- `list_by_title_or_material`
- `list_by_raw_material`
- `list_by_carbon_source`
- `list_by_process_method`

Assert each generated Cypher contains a candidate-limiting segment before optional enrichment. The assertion should check ordering, not only substring presence:

```python
candidate_limit = cypher.index("LIMIT $limit")
optional_profile = cypher.index("OPTIONAL MATCH", candidate_limit)
assert candidate_limit < optional_profile
```

If a query needs optional matches to determine the candidate match itself, assert that there is a second candidate boundary such as `WITH DISTINCT d` followed by `LIMIT $limit` before the profile-only optional matches.

- [ ] **Step 2: Add expected-column tests for profile fields**

For list-style direct paths, assert `expected_columns` includes relevant profile fields:
- `raw_materials`
- `carbon_sources`
- `carbon_contents`
- `dopants`
- `doping_elements`
- `additives`
- `preparation_methods`
- `process_parameters`
- `testing_items`
- `equipment`

Use path-specific expectations where not every path naturally returns every matched-condition field.

- [ ] **Step 3: Rewrite `list_by_raw_material` as candidate-first enrichment**

Target shape:

```cypher
MATCH (d:doi)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials)
OPTIONAL MATCH (d)-[:title]->(t:title)
WHERE any(term IN $terms WHERE toLower(coalesce(rm.name, '')) CONTAINS term)
WITH DISTINCT d, t, collect(DISTINCT rm.name)[0..3] AS matched_raw_materials
LIMIT $limit
OPTIONAL MATCH ...
RETURN d.name AS doi, t.name AS title, matched_raw_materials, ...
LIMIT $limit
```

Profile fields to add:
- `raw_materials`
- `carbon_sources`
- `carbon_contents`
- `dopants`
- `doping_elements`
- `additives`
- `preparation_methods`
- `process_parameters`
- `testing_items`
- `equipment`

- [ ] **Step 4: Rewrite `list_by_carbon_source` as candidate-first enrichment**

Keep current match semantics for carbon source terms.

Return:
- `carbon_sources` as matched and profile field
- `dopants`, `doping_elements`, and other recipe/process/testing/equipment profile fields

- [ ] **Step 5: Rewrite `list_by_process_method` as candidate-first enrichment**

Keep existing `target_terms` behavior and generic process-term filtering.

Return:
- `preparation_methods` as matched and profile field
- `raw_materials`
- `carbon_sources`
- `dopants`
- `doping_elements`
- `process_parameters`
- `testing_items`
- `equipment`

- [ ] **Step 6: Rewrite `list_by_title_or_material` as candidate-first enrichment**

Preserve matching across DOI, title, raw materials, and sample names.

After candidate limiting, expand recipe/process/testing/equipment fields for those DOI rows only, including `dopants` and `doping_elements` where graph data is present.

- [ ] **Step 7: Run focused fastQA template tests**

Run:

```bash
cd fastQA && pytest tests/test_graph_kb_query_templates.py -q
```

Expected: all tests pass. If sandbox blocks pytest execution, rerun with escalated permissions.

## Task 3: fastQA Route Regression

**Files:**
- Modify: `fastQA/tests/test_graph_kb_service.py`
- Modify: `fastQA/tests/test_fastqa_kb_graph_integration.py`

- [ ] **Step 1: Add direct-answer no-generation regression at graph service boundary**

Use the existing `route_graph_kb_v2` fake graph style. Add a direct list query such as `列出使用蔗糖作为碳源的文献`, return enriched rows, and assert:
- `routing_result.mode == "direct_answer"`
- `routing_result.direct_result.handled is True`
- enriched fields appear in answer
- `routing_result.rag_payload is None` on the returned direct result

Do not assert that the internal deterministic `build_graph_rag_payload(...)` helper was not called. Current fastQA service code builds that payload before direct rendering; the requirement is that direct mode does not expose a RAG payload and does not invoke staged LLM generation.

- [ ] **Step 2: Strengthen router-level no-staged-generation regression**

In `fastQA/tests/test_fastqa_kb_graph_integration.py`, extend an existing direct-answer test such as `test_sync_ask_uses_graph_direct_answer_when_mode_is_direct_answer` or add a sibling test.

Assert:
- graph direct answer is returned to the API payload
- staged runtime methods or generation facade are not called
- returned `references` remain compatible with the existing router payload
- if `reference_objects` are produced by the router from references, assert that behavior at the router payload boundary rather than inside `render_direct_answer`

- [ ] **Step 3: Preserve graph-for-RAG route behavior**

Extend or add a test for a hybrid/property question. Assert:
- `routing_result.mode == "graph_for_rag"`
- `stage4_fact_block` still contains graph facts
- no direct answer is returned

- [ ] **Step 4: Run focused fastQA service tests**

Run:

```bash
cd fastQA && pytest tests/test_graph_kb_service.py tests/test_fastqa_kb_graph_integration.py -q
```

Expected: all tests pass. If sandbox blocks pytest execution, rerun with escalated permissions.

## Task 4: patent Direct Renderer Profiles

**Files:**
- Modify: `patent/tests/test_patent_graph_kb_direct_renderer.py`
- Modify: `patent/server/patent/graph_kb/direct_renderer.py`

- [ ] **Step 1: Add rich patent listing renderer test**

Create a parametric `list_patents_by_material` plan and canonicalized rows containing:
- `patent_id`
- `title`
- `abstract`
- `application_date`
- `publication_date`
- `legal_status`
- `applicants`
- `inventors`
- `ipc_codes`
- `material_name`
- `material_roles`
- `process_steps`
- `problems`
- `solutions`
- `inventive_points`
- `performance_facts`
- `measurements`

Assert answer includes compact profile bullets for applicant/status/date, abstract, matched material, material/process highlights, problem/solution, inventive/performance, and measurements.

- [ ] **Step 2: Add missing-enrichment fallback test**

Use listing rows with only `patent_id`, `title`, and matched subject field. Assert the answer remains handled and still contains the old essential ID/title information.

- [ ] **Step 3: Add profile display cap/reference preservation test**

Create 7 patent listing rows.

Assert:
- first 5 detailed profiles are displayed
- all 7 patent IDs are present in `references`
- all 7 reference objects are returned
- answer mentions additional matches when rows exceed displayed profiles

- [ ] **Step 4: Add cleanup/truncation test**

Use dirty and long patent fields. Assert placeholders are removed, duplicates are deduped, and long abstract/fact values are truncated.

- [ ] **Step 5: Add single-patent supplemental output test**

For `lookup_patent_by_id`, add optional rows containing `problems`, `solutions`, `inventive_points`, `performance_facts`, and `measurements`.

Assert the current basic patent fields still render and a compact supplemental block appears only when fields are present.

- [ ] **Step 6: Implement patent renderer helpers**

In `patent/server/patent/graph_kb/direct_renderer.py`, add helpers near `_text`:

```python
_PATENT_PROFILE_DISPLAY_LIMIT = 5
_PATENT_LIST_ITEM_LIMIT = 3
_PATENT_ABSTRACT_LIMIT = 240
_PATENT_FACT_LIMIT = 160

def _clean_graph_value(value: Any, *, limit: int = _PATENT_FACT_LIMIT) -> str:
    ...

def _dedupe_clean_items(values: Any, *, limit: int = _PATENT_LIST_ITEM_LIMIT) -> list[str]:
    ...

def _format_compact_list(values: Any, *, limit: int = _PATENT_LIST_ITEM_LIMIT) -> str:
    ...
```

Field-specific caps:
- applicants/inventors: 3
- IPC: 5
- material/process: 5
- problems/solutions: 2
- inventive/performance/measurements: 3
- abstract: around 240 Chinese chars or equivalent simple character cap

- [ ] **Step 7: Enhance `_render_patent_listing`**

Keep `_reference_objects_from_rows` as the source of references.

New rendering behavior:
- header remains first line
- if `len(filtered_rows) > 5`, include a concise line saying only the first 5 graph summaries are shown
- each displayed profile starts with a stable patent ID/title line
- bullets are included only for non-empty fields
- fallback rows still render ID/title without requiring enrichment
- metadata includes existing `path_id` and `subject`; optional additions such as `profile_rows_shown` are allowed

- [ ] **Step 8: Enhance `lookup_patent_by_id` parametric renderer**

Append optional supplemental bullets for:
- problem/solution
- inventive/performance facts
- measurements

Do not require these fields for the answer to be handled.

- [ ] **Step 9: Run focused patent renderer tests**

Run:

```bash
cd patent && pytest tests/test_patent_graph_kb_direct_renderer.py -q
```

Expected: all tests pass. If sandbox blocks pytest execution, rerun with escalated permissions.

## Task 5: patent Candidate-First Enriched Query Templates

**Files:**
- Modify: `patent/tests/test_patent_graph_kb_query_templates.py`
- Modify: `patent/server/patent/graph_kb/query_templates.py`

- [ ] **Step 1: Add query-shape tests for broad listing paths**

For each path below, assert candidate limiting happens before profile expansion:
- `list_patents_by_material`
- `list_patents_by_material_role`
- `list_patents_by_process_term`
- `list_patents_by_applicant`
- `list_patents_by_inventor`
- `list_patents_by_agency`
- `list_patents_by_ipc_prefix`
- `list_patents_by_ipc_code_prefix`
- `list_patents_by_ipc_full_code`

The test must fail if the query only has a final `LIMIT $limit` after all optional matches.

- [ ] **Step 2: Add expected-column tests for patent profile fields**

Assert broad listing templates expose a compact profile shape:
- `abstract`
- `application_date`
- `publication_date`
- `legal_status`
- `applicants`
- `inventors`
- `ipc_codes`
- `material_roles`
- `process_steps`
- `problems`
- `solutions`
- `inventive_points`
- `performance_facts`
- `measurements`

Use path-specific matched fields such as `material_name`, `applicant_name`, `inventor_name`, `agency_name`, or `ipc_code`.

- [ ] **Step 3: Add optional single-patent supplemental column test**

For `lookup_patent_by_id`, assert expected columns include any supplemental fields added for the renderer.

- [ ] **Step 4: Rewrite broad ownership/classification listing templates**

For applicant, inventor, agency, and IPC listing paths, use this structure:

```cypher
MATCH ... // exact applicant/inventor/agency/IPC candidate match
WITH DISTINCT p, matched_value
LIMIT $limit
OPTIONAL MATCH (p)-[:HAS_APPLICANT]->(applicant:Organization)
WITH p, matched_value, collect(DISTINCT applicant.name)[0..3] AS applicants
OPTIONAL MATCH ...
RETURN p.patent_id AS patent_id, p.title AS title, ..., applicants, ...
LIMIT $limit
```

Preserve the existing exact-match semantics for applicant/inventor/agency and IPC prefix/full-code matching.
Preserve `p.stub AS stub` in templates that currently return it, because canonicalization and evidence-quality checks use stub signals.

- [ ] **Step 5: Rewrite broad material/process listing templates**

For `list_patents_by_material`, `list_patents_by_material_role`, and `list_patents_by_process_term`, use `WITH DISTINCT p, matched_value LIMIT $limit` before profile expansion.

Return bounded fields:
- material roles/options
- process steps/templates
- problems/solutions
- inventive points
- performance facts
- measurements

Preserve `p.stub AS stub` in these direct listing templates as well.

- [ ] **Step 6: Add supplemental fields to `lookup_patent_by_id` if needed**

If existing lookup rows do not include enough fields for the renderer supplemental block, add optional matches for:
- `ADDRESSES`
- `PROPOSES`
- `HAS_INVENTIVE_POINT`
- `HAS_PERFORMANCE_FACT`
- `HAS_EXPERIMENT_TABLE -> HAS_ROW -> HAS_MEASUREMENT`

Keep all collections capped.

- [ ] **Step 7: Run focused patent template tests**

Run:

```bash
cd patent && pytest tests/test_patent_graph_kb_query_templates.py -q
```

Expected: all tests pass. If sandbox blocks pytest execution, rerun with escalated permissions.

## Task 6: patent Route and KB-Service Regression

**Files:**
- Modify: `patent/tests/test_patent_graph_kb_service_v2.py`
- Modify: `patent/tests/test_patent_kb_service.py`

- [ ] **Step 1: Add route-level direct-answer enriched row regression**

In `test_patent_graph_kb_service_v2.py`, use the existing monkeypatch style to return a direct-answer plan, enriched canonical rows, and the real renderer where practical.

Assert:
- result mode is `direct_answer`
- direct result is handled
- enriched fields appear in answer
- no RAG payload is needed for handled direct answers

- [ ] **Step 2: Preserve direct fallback to graph-for-RAG**

Keep or extend the existing failed-render downgrade test. Assert a renderer failure still produces `graph_for_rag` and sets `direct_fallback_reason`.

- [ ] **Step 3: Preserve material attribute graph-for-RAG behavior**

In `test_patent_kb_service.py`, keep the existing material attribute test expectation:
- material attribute questions still inject graph-for-RAG context
- staged orchestrator is called for graph-for-RAG
- direct-answer enrichment does not pull these questions into direct answer

- [ ] **Step 4: Strengthen direct-answer no-orchestrator regression**

Extend `test_kb_service_returns_graph_result_before_staged_runtime` or add a sibling test using a material listing direct answer. Assert the failing staged orchestrator is not called when graph direct answer handles the request.

- [ ] **Step 5: Run focused patent service tests**

Run:

```bash
cd patent && pytest tests/test_patent_graph_kb_service_v2.py tests/test_patent_kb_service.py -q
```

Expected: all tests pass. If sandbox blocks pytest execution, rerun with escalated permissions.

## Task 7: Full Focused Verification

**Files:**
- No code edits unless failures reveal issues in files already listed above.

- [ ] **Step 1: Run all focused graph tests**

Run:

```bash
cd fastQA && pytest tests/test_graph_kb_direct_renderer.py tests/test_graph_kb_query_templates.py tests/test_graph_kb_service.py -q
cd patent && pytest tests/test_patent_graph_kb_direct_renderer.py tests/test_patent_graph_kb_query_templates.py tests/test_patent_graph_kb_service_v2.py tests/test_patent_kb_service.py -q
```

Expected: all tests pass. If sandbox blocks pytest execution, rerun each blocked command with escalated permissions.

- [ ] **Step 2: Run route-contract smoke tests**

Run:

```bash
cd fastQA && pytest tests/test_fastqa_kb_graph_integration.py -q
cd patent && pytest tests/test_patent_answering_graph_context.py tests/test_patent_stage1_graph_context.py tests/test_patent_stage4_graph_context.py -q
```

Expected: all tests pass, or any unrelated pre-existing failure is documented with exact failure output.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git diff -- fastQA/app/modules/graph_kb/direct_renderer.py fastQA/app/modules/graph_kb/query_templates.py patent/server/patent/graph_kb/direct_renderer.py patent/server/patent/graph_kb/query_templates.py
```

Check:
- no classifier/routing changes
- no LLM calls
- candidate limiting before enrichment
- collection caps in Cypher
- renderer caps in Python
- references are built from all returned rows

- [ ] **Step 4: Run code review loop**

After implementation and verification, open one `gpt-5.5` high-reasoning code-review subagent. Provide:
- this plan path
- the spec path
- `git diff`
- tests run and results

Ask for bugs, regressions, unbounded queries, direct-answer LLM leakage, reference-shape breakage, and missing tests. Apply valid findings, then reuse the same subagent for re-review until status is Approved.

## Acceptance Checklist

- [ ] fastQA list-style direct answers include useful graph context beyond DOI/title when fields are present.
- [ ] patent list-style direct answers include useful graph context beyond patent ID/title when fields are present.
- [ ] Direct-answer paths remain deterministic and do not call LLM or staged generation.
- [ ] Candidate limiting happens before optional enrichment in broad Cypher templates.
- [ ] Renderer output remains bounded, readable, and sparse-field tolerant.
- [ ] References/reference objects preserve all returned DOI/patent candidates.
- [ ] Direct-answer routing behavior is unchanged.
- [ ] Graph-for-RAG payload behavior is unchanged.
- [ ] Focused fastQA and patent tests pass.
