# Richer Graph Direct Answer Design

**Goal:** Make fastQA and patent graph direct answers more informative by using existing graph fields around matched DOI/patent nodes, while still avoiding LLM calls on direct-answer paths.

**Status:** Draft

---

## Background

Current graph direct-answer behavior is structurally correct but too sparse. When a query matches graph direct answer, the response often becomes a simple list:

- fastQA: DOI/title list for matched papers.
- patent: patent ID/title list for matched patents.

The graph contains substantially richer one-hop and two-hop context that can help users understand why each paper or patent matters. The direct-answer path should surface a compact, readable profile from graph data instead of only identifiers.

This feature must not introduce LLM generation. The output remains deterministic graph rendering.

## Evidence From Graph Exploration

### fastQA Literature Graph

Connection:

- `bolt://127.0.0.1:7688`
- database `neo4j`

Relevant labels and relation buckets include:

- DOI/title: `doi -> title`
- Materials: `doi -> raw_materials -> raw_materials`
- Recipe: `doi -> recipe -> carbon_source / carbon_content / dopant / doping_elements / additives / ratios`
- Process: `doi -> process -> preparation_method`
- Process parameters: `process -> key_process_parameters -> calcination / milling / drying / atmosphere / temperature / time / pressure`
- Testing/equipment: `doi -> testing -> testing`, `doi -> equipment -> name / model / instrument`
- Performance-related values: `discharge_capacity`, `conductivity`, `cycling_stability`, `coulombic_efficiency`, `particle_size`, `energy_density`, `power_density`

Sample DOI rows contain fields such as:

- title
- raw materials
- carbon source/content
- additives
- preparation method
- milling/drying parameters
- testing methods
- equipment

### patent Graph

Connection:

- `bolt://127.0.0.1:8687`
- database `neo4j`

Relevant patent graph context includes:

- Patent base fields: `patent_id`, `title`, `abstract`, `application_date`, `publication_date`, `legal_status`, `patent_type`, `ipc_main`
- Ownership/classification: `HAS_APPLICANT`, `HAS_INVENTOR`, `CLASSIFIED_AS`, `IN_IPC_SUBCLASS`
- Technical content: `ADDRESSES`, `PROPOSES`, `HAS_INVENTIVE_POINT`, `PROTECTION_INCLUDES`
- Materials/process: `HAS_MATERIAL_ROLE -> OPTION_INCLUDES`, `HAS_PROCESS_STEP -> INSTANCE_OF`
- Evidence/performance: `HAS_PERFORMANCE_FACT`, `USES_ATMOSPHERE`, `HAS_EXPERIMENT_TABLE -> HAS_ROW -> HAS_MEASUREMENT`
- Citations: `CITES_PATENT`

Sample material-query patent rows can include:

- abstract
- applicants/inventors/IPC/legal status/dates
- material roles
- process steps
- technical problem and solution
- inventive points
- performance facts
- measurement table snippets

## User Experience Target

For direct-answer graph results, the answer should still be fast and deterministic, but each result should provide a compact profile.

Instead of:

```text
涉及材料 `磷酸铁锂` 的专利包括：
- `CN...`：...
- `CN...`：...
```

Prefer:

```text
涉及材料 `磷酸铁锂` 的专利包括 20 件。以下展示前 5 件的图谱摘要：

### CN...：...
- 申请人：...
- 状态/日期：审中；公开日 ...
- 摘要：...
- 关键材料/工艺：main: 磷酸铁锂；喷雾干燥；热处理
- 技术问题/方案：...
- 性能/实验：电导率提升...；界面阻抗...
```

For fastQA DOI results, prefer:

```text
关于 LiFePO4 的图谱命中文献包括 20 篇。以下展示前 5 篇的图谱摘要：

### DOI ...
- 标题：...
- 原料/配方：...
- 制备方法：...
- 关键参数：...
- 测试/设备：...
```

## Scope

### In Scope

- Enrich existing graph direct-answer renderers.
- Extend direct-answer query templates to return useful graph context fields.
- Keep answer length bounded.
- Add deterministic formatting and tests.
- Preserve current `references`, `reference_objects`, metadata, and no-LLM behavior.

### Out of Scope

- Do not add LLM summarization to direct answer.
- Do not change graph routing/classifier decisions.
- Do not change staged RAG answers.
- Do not redesign frontend rendering.
- Do not change Neo4j data ingestion.
- Do not make graph queries unbounded or expensive.

## Design Principles

1. **Direct answer stays deterministic.** Use graph fields and fixed templates only.
2. **More context, not exhaustive dumps.** Show the most useful context per result, capped.
3. **Readable first.** Prefer compact bullets over raw JSON-like values.
4. **Stable references.** DOI and patent IDs remain the reference anchors.
5. **Graceful sparsity.** If a field is missing, omit it; do not print empty headings.
6. **Performance bounded.** Query limited result rows and per-row collections.

## fastQA Design

### Candidate Direct Paths to Enrich

Start with direct paths that currently produce sparse lists:

- `list_by_title_or_material`
- `list_by_raw_material`
- `list_by_carbon_source`
- `list_by_process_method`

Also improve if straightforward:

- `lookup_by_doi`
- `expand_doi_context_by_doi`

Numeric direct answers should remain conservative because they already have parser-confidence safety logic.

### Query Template Additions

For list-style paths, return a shared compact DOI profile shape where feasible:

- `doi`
- `title`
- matched condition field, such as `matched_raw_materials`, `carbon_sources`, or `preparation_methods`
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

Collection caps:

- per row, each list should cap at 3 values unless otherwise specified.
- direct result row count remains governed by existing `limit`.
- renderer should display detailed profiles for at most 5 rows by default, while references can still include all returned DOI candidates.

Candidate limiting must happen before optional enrichment. Do not match an unbounded set of DOI nodes and then expand optional profile fields before `LIMIT`. Use a two-stage shape:

1. Match and limit candidate DOI rows.
2. Expand only those candidate DOI rows into profile fields.

Example Cypher pattern for fastQA enrichment:

```cypher
MATCH ... // candidate DOI match
WITH DISTINCT d
LIMIT $limit
OPTIONAL MATCH (d)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source)
OPTIONAL MATCH (d)-[:recipe]->(:recipe)-[:carbon_content]->(cc:carbon_content)
OPTIONAL MATCH (d)-[:process]->(:process)-[:preparation_method]->(pm:preparation_method)
OPTIONAL MATCH (d)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[param_rel]->(param)
OPTIONAL MATCH (d)-[:testing]->(:testing)-[:testing]->(test:testing)
OPTIONAL MATCH (d)-[:equipment]->(:equipment)-[:name]->(eq:name)
RETURN
  collect(DISTINCT cs.name)[0..3] AS carbon_sources,
  collect(DISTINCT cc.name)[0..3] AS carbon_contents,
  collect(DISTINCT pm.name)[0..3] AS preparation_methods,
  collect(DISTINCT type(param_rel) + ':' + param.name)[0..6] AS process_parameters,
  collect(DISTINCT test.name)[0..3] AS testing_items,
  collect(DISTINCT eq.name)[0..3] AS equipment
```

### fastQA Rendering

Add a shared renderer helper in `fastQA/app/modules/graph_kb/direct_renderer.py`:

- `_render_paper_profiles(...)`
- `_format_compact_list(...)`
- `_clean_graph_value(...)` for values containing `_null_null`, repeated underscores, or JSON-like strings.

Each displayed DOI profile should include only non-empty bullets:

- DOI
- title
- matched condition
- raw materials / recipe
- method / process parameters
- testing / equipment

For list answers, include a top summary:

- matched count displayed
- detailed rows shown count
- if more rows exist, mention that references include additional matches.

## patent Design

### Candidate Direct Paths to Enrich

Start with listing paths that currently output only patent IDs and titles:

- `list_patents_by_material`
- `list_patents_by_material_role`
- `list_patents_by_process_term`
- `list_patents_by_applicant`
- `list_patents_by_inventor`
- `list_patents_by_agency`
- `list_patents_by_ipc_prefix`
- `list_patents_by_ipc_code_prefix`
- `list_patents_by_ipc_full_code`

Single-patent direct paths should also be enriched where data already exists:

- `lookup_patent_by_id`
- `list_patent_process_steps`
- `list_patent_material_roles`
- `list_patent_experiment_tables`
- `list_patent_problem_solution`
- `list_patent_inventive_scope`

### Query Template Additions

For patent listing paths, return a compact patent profile shape:

- `patent_id`
- `title`
- `abstract`
- `application_date`
- `publication_date`
- `legal_status`
- `applicants`
- `inventors`
- `ipc_codes`
- matched condition fields, such as `material_name`, `process_name`, `applicant_name`
- `material_roles`
- `process_steps`
- `problems`
- `solutions`
- `inventive_points`
- `performance_facts`
- `measurements`

Collection caps:

- `applicants`, `inventors`: 3
- `ipc_codes`: 5
- `material_roles`, `process_steps`: 5
- `problems`, `solutions`: 2
- `inventive_points`, `performance_facts`: 3
- `measurements`: 3

Candidate limiting must happen before optional profile expansion. Use `WITH DISTINCT p, matched_value LIMIT $limit` before joining applicants, inventors, IPC, materials, process steps, facts, and measurements. This prevents a broad material/applicant/IPC match from expanding every matching patent before truncation.

Preferred pattern:

```cypher
MATCH ... // matched patents
WITH DISTINCT p, matched_value
LIMIT $limit
OPTIONAL MATCH (p)-[:HAS_APPLICANT]->(app:Organization)
WITH p, matched_value, collect(DISTINCT app.name)[0..3] AS applicants
OPTIONAL MATCH ...
RETURN ...
LIMIT $limit
```

### patent Rendering

Enhance `_render_patent_listing(...)` in `patent/server/patent/graph_kb/direct_renderer.py` so listing paths render compact profiles instead of only IDs.

Each displayed patent profile should include only non-empty bullets:

- patent ID/title
- applicant/status/date
- abstract, truncated to a safe length
- matched condition
- material/process highlights
- technical problem/solution
- inventive/performance highlights
- measurement snippets

Detailed display should be capped at 5 patents by default. References should still include all returned patents.

Single-patent direct answer should keep current specific answer but may append a compact "图谱补充信息" block when available:

- applicant/inventor/IPC/status
- abstract
- problem/solution
- performance facts

## Formatting Rules

### Length Caps

- Detailed profiles: first 5 rows.
- Per bullet list: 3 values unless a field-specific cap above says otherwise.
- Abstract: truncate to around 180-240 Chinese chars or 350 English chars.
- Individual fact/value: truncate long strings to around 120-160 chars.
- Whole direct answer should remain comfortably readable in chat.

### Cleaning Rules

Need deterministic cleanup helpers:

- Remove `_null`, `null_null`, duplicated underscores.
- Collapse whitespace.
- Normalize obvious JSON-like dict strings only if easy and safe; otherwise display cleaned raw text.
- De-duplicate values preserving order.
- Omit empty values and placeholders.

## Metadata and References

Do not change public response shape.

Keep:

- `references`
- `reference_objects`
- `query_mode`
- `template_id`
- `result_count`
- graph metadata fields

Optional non-breaking metadata additions:

- `direct_answer_profile_rows_shown`
- `direct_answer_profile_fields`

These are advisory and not required for the first implementation.

## Performance and Safety

- All new Cypher must remain read-only.
- Avoid unbounded optional expansions.
- Use `WITH DISTINCT` to prevent row multiplication.
- Apply `LIMIT $limit` to matched candidate DOI/patent rows before optional profile expansion.
- Keep existing final `LIMIT $limit` only as an additional guard; it is not sufficient by itself.
- Cap every collection in Cypher.
- Renderer should cap display independently from query limit.
- If enrichment fields are missing, renderer should still produce the old minimal answer.

## Test Plan

### fastQA Tests

Add/update tests under:

- `fastQA/tests/test_graph_kb_query_templates.py`
- `fastQA/tests/test_graph_kb_direct_renderer.py`
- `fastQA/tests/test_graph_kb_service.py`

Test cases:

- `list_by_title_or_material` rows with raw materials, methods, testing produce richer profile bullets.
- `list_by_raw_material` includes matched raw material plus recipe/process context when present.
- `list_by_carbon_source` includes carbon source and preparation method.
- Empty enrichment fields still render a valid minimal DOI/title answer.
- References remain DOI list and are not truncated to displayed rows.
- Long/dirty values are cleaned and truncated.
- Direct-answer routing tests still return `mode == "direct_answer"` for representative direct queries.
- Graph-for-RAG route tests still return `mode == "graph_for_rag"` and preserve existing RAG payload fields.
- Direct-answer router tests prove generation runtime/stage methods are not called when graph direct answer is handled.
- Query-template tests assert candidate limiting happens before optional enrichment, either by checking `WITH DISTINCT ... LIMIT $limit` shape or by route-level fake graph tests that fail on unbounded expansion.

### patent Tests

Add/update tests under:

- `patent/tests/test_patent_graph_kb_query_templates.py`
- `patent/tests/test_patent_graph_kb_direct_renderer.py`
- `patent/tests/test_patent_graph_kb_service_v2.py`

Test cases:

- `list_patents_by_material` rich rows render applicant/status/abstract/material/process/performance bullets.
- Explicit patent listing references still include all patent IDs.
- Missing enrichment fields fall back to patent ID/title list.
- Single-patent lookup can include supplemental graph fields if available.
- Renderer caps displayed detailed profiles but preserves references.
- Query templates use `WITH DISTINCT` or equivalent bounded aggregation to avoid cross-product row explosion.
- Classifier tests confirm direct-answer vs graph-for-RAG routing decisions are unchanged for representative patent ID, material listing, material attribute, and file-context cases.
- Service tests confirm a handled graph direct answer still short-circuits before staged runtime/orchestrator calls.
- Existing graph-for-RAG injection tests still pass, proving staged RAG context behavior is unchanged.
- Query-template tests assert candidate limiting happens before optional enrichment for broad patent listing paths.

## Acceptance Criteria

- fastQA graph direct answers for list-style DOI matches include useful graph context beyond DOI/title when fields are present.
- patent graph direct answers for list-style patent matches include useful graph context beyond patent ID/title when fields are present.
- No LLM is called for direct-answer paths.
- Direct-answer routing behavior does not change.
- Existing graph-for-RAG behavior does not change.
- Responses remain bounded and deterministic.
- Existing references/reference objects remain compatible.
- Relevant fastQA and patent graph tests pass.

## Suggested Implementation Order

1. Add renderer helper tests with synthetic rows.
2. Implement deterministic cleaning/truncation/profile rendering helpers.
3. Extend patent direct renderer first, because patent graph has richer structured data and current list answers are sparse.
4. Extend patent query templates for listing paths with bounded profile fields.
5. Extend fastQA direct renderer.
6. Extend fastQA query templates for list paths with bounded profile fields.
7. Run focused graph tests for both services.

## Open Decisions

Resolved first-pass defaults:

- Show 5 detailed profiles by default for both fastQA and patent.
- Keep count-only answers concise; do not add sample profiles in this pass.
- Defer numeric direct-answer enrichment. Numeric direct answers have separate parser-confidence constraints and should not be mixed into this first implementation.
