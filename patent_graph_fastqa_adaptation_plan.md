# Patent Graph QA Adaptation Plan

## Scope and inputs

This plan is based on:

- `/tmp/fastqa_doc1.md`
- `/tmp/fastqa_doc2.md`
- `patent/server/patent/graph_kb/`
- `fastQA/app/modules/graph_kb/`
- the existing read-only patent schema notes in `patent_graph_live_schema_report.md`

The goal is not to replace the current patent graph path in one step. The goal is to evolve it from a fixed-template shortcut into a fastQA V2-style graph routing and evidence subsystem that still respects the patent graph's much richer domain schema.

## 1. Current patent graph baseline

### 1.1 What exists today

The current patent graph path is a narrow, deterministic preflight:

1. `classifier.py` decides `try_graph` or `skip`
2. `client.py` picks one of 9 fixed Cypher templates
3. `neo4j_client.py` executes the hardcoded Cypher through the direct Neo4j Python driver
4. `rendering.py` turns rows into a final answer string
5. `service.py` either returns a handled graph answer or falls back to normal patent KB flow

### 1.2 What the current classifier recognizes

The current patent classifier is intentionally conservative:

- direct patent-id lookup
- patent process steps
- patent material roles
- patent experiment tables and measurements
- patent problem / solution / application scenario
- patent inventive scope / protection scope / claim labels
- patent citations
- IPC-based patent listing
- applicant-based patent listing

It explicitly skips:

- DOI questions
- broad semantic questions such as `为什么`, `趋势`, `对比分析`
- ambiguous follow-ups
- multi-patent questions
- file-context-heavy turns

### 1.3 What the current 9 templates cover

The 9 hardcoded templates are:

1. `lookup_patent_by_id`
2. `list_patent_process_steps`
3. `list_patent_material_roles`
4. `list_patent_experiment_tables`
5. `list_patent_problem_solution`
6. `list_patent_inventive_scope`
7. `list_patent_citations`
8. `list_patents_by_ipc`
9. `list_patents_by_applicant`

### 1.4 What the live patent graph contains beyond those templates

The live schema already supports more than the current code uses. In addition to the labels and edges touched by the 9 templates, the DB also contains:

- labels: `Atmosphere`, `EmbodimentInsight`
- relations: `NEXT_STEP`, `USES_ATMOSPHERE`, `HAS_EMBODIMENT_INSIGHT`, `CO_OCCURS_WITH`

That matters for the adaptation plan: the patent graph is not too small for a smarter graph QA workflow. The current code is simply underusing it.

### 1.5 Architectural gap versus fastQA reference flow

Compared with the fastQA workflow described in `/tmp/fastqa_doc1.md` and `/tmp/fastqa_doc2.md`, patent is missing:

- a unified graph entry point equivalent to `smart_query`
- route families like `precise` / `hybrid` / `semantic`
- tri-state output such as `direct_answer` / `graph_for_rag` / `skip_graph`
- a planner layer
- a schema registry / logical view
- a query strategy ladder beyond hardcoded templates
- a guardrail layer for non-template Cypher
- a graph evidence adapter for downstream generation

## 2. Target architecture

### 2.1 Recommended entry point

Add a single patent-native graph router entry point, for example:

- `route_patent_graph_v2(...)`

or, if compatibility with the fastQA vocabulary is useful:

- `smart_patent_query(...)`

This entry point should sit above the current `try_patent_graph_kb_answer(...)` logic and return one of three modes:

- `direct_answer`: the graph can answer directly with high confidence
- `graph_for_rag`: the graph has useful structured evidence, but generation should write the final answer
- `skip_graph`: graph is low-value or too risky for this question

### 2.2 Recommended component layout

The cleanest adaptation is to keep the current patent modules as the legacy trusted path and add V2-style layers around them:

- `classifier_v2_patent.py`
- `schema_registry_patent.py`
- `planner_v2_patent.py`
- `query_strategy_patent.py`
- `guardrail_patent.py`
- `executor_v2_patent.py`
- `canonicalizer_patent.py`
- `direct_renderer_patent.py`
- `rag_adapter_patent.py`

Keep the existing modules as compatibility layers:

- `classifier.py`: legacy binary fallback
- `client.py`: trusted template library
- `rendering.py`: legacy direct renderer
- `service.py`: compatibility shell during rollout

### 2.3 Design principle

Do not copy fastQA's graph logic literally. Reuse its architecture, not its schema assumptions.

fastQA V2 is built around a field-bucket DOI graph. Patent needs the same orchestration pattern, but the logical schema, canonicalization rules, query builders, and renderers must be patent-native.

## 3. Routing strategy for patent

### 3.1 Route families

Patent should use three route families at the classifier level:

- `precise`
- `hybrid`
- `semantic`

I do not recommend a separate `community` family in phase 1. Patent does not currently have a dedicated graph-community answering path. Questions about graph neighborhoods or relation patterns should map to `hybrid` for now.

### 3.2 Precise triggers

`precise` should fire when the question is anchored to a structured patent entity and asks for lookup, listing, filtering, aggregation, or bounded comparison.

High-confidence precise anchors:

- patent IDs: `CN...`, `US...`, `WO...`, `JP...`, `EP...`, `KR...`
- IPC codes: `A-H` class patterns such as `H01M4/13`
- explicit entity roles: `申请人`, `发明人`, `代理机构`, `IPC`, `分类号`
- explicit subgraphs: `工艺步骤`, `步骤`, `工艺`, `原料`, `材料角色`, `实验表格`, `实验数据`, `测量`, `技术问题`, `技术方案`, `应用场景`, `发明点`, `保护范围`, `claim`, `引用`

Precise intent verbs:

- `有哪些`
- `列出`
- `给出`
- `多少`
- `统计`
- `最高`
- `最低`
- `前 N`
- `包含`
- `属于`
- `由谁申请`
- `由谁发明`

Patent-specific precise examples:

- "CN123456789A 的工艺步骤是什么"
- "H01M4/13 下有哪些专利"
- "宁德时代有哪些专利"
- "张三有哪些专利"
- "CN... 引用了哪些专利"
- "某专利的技术问题和技术方案是什么"

### 3.3 Hybrid triggers

`hybrid` should fire when the question is still graph-anchored, but the final answer needs synthesis, explanation, comparison, or narrative summarization instead of a direct row rendering.

Hybrid indicators:

- graph anchor + `为什么`
- graph anchor + `如何`
- graph anchor + `优势`
- graph anchor + `改进`
- graph anchor + `对比`
- graph anchor + `差异`
- graph anchor + `趋势`
- graph anchor + `总结`
- graph anchor + `适用场景`
- graph anchor + multi-patent comparison

Important behavior change from current patent code:

- multi-patent questions should no longer default to `skip`
- they should usually become `hybrid`

Patent-specific hybrid examples:

- "比较 CN... 和 CN... 的工艺步骤差异"
- "总结 H01M4/13 相关专利的常见技术方案"
- "宁德时代相关专利的材料角色有什么共性"
- "某申请人的专利主要解决哪些问题"

### 3.4 Semantic triggers

`semantic` should fire when the question is broad, weakly anchored, or not meaningfully answerable from the graph.

Examples:

- broad domain questions with no patent / IPC / applicant / inventor / process / material anchor
- legal or policy questions not represented in the graph
- vague follow-ups with no recoverable entity anchor
- open-ended background questions like "锂电专利趋势如何"

Semantic does not always mean `skip_graph`.

Recommended mapping:

- weak graph value -> `skip_graph`
- some graph value but narrative answer needed -> `graph_for_rag`

### 3.5 Suggested routing table

| Route family | Typical signals | Output mode |
| --- | --- | --- |
| `precise` | patent ID, IPC code, applicant/inventor/agency name, listing/filter/count/lookup verbs | mostly `direct_answer` |
| `hybrid` | graph anchor plus compare/explain/summarize/trend reasoning | `graph_for_rag` |
| `semantic` | broad or weakly anchored question | `skip_graph` or low-confidence `graph_for_rag` |

## 4. NL -> Cypher -> Neo4j -> answer pipeline

### 4.1 Proposed pipeline

1. Normalize the question and inspect conversation context.
2. Extract structured anchors:
   - patent IDs
   - IPC codes
   - applicant names
   - inventor names
   - agency names
   - process/material/measurement/scope keywords
3. Run `classifier_v2_patent` and produce:
   - route family: `precise` / `hybrid` / `semantic`
   - output mode: `direct_answer` / `graph_for_rag` / `skip_graph`
   - diagnostics: matched rule, ambiguity, context dependency
4. Canonicalize entities:
   - normalize patent IDs to uppercase
   - normalize IPC codes
   - resolve organization/person aliases if available
   - tag query intent slots such as `target_patent_id`, `target_ipc`, `target_applicant`
5. Build a logical query plan in `planner_v2_patent`.
6. Select a query strategy in `query_strategy_patent`.
7. Guard and execute Cypher against Neo4j.
8. Canonicalize rows into a patent evidence bundle.
9. Either:
   - render a direct graph answer
   - or build a graph payload for downstream generation

### 4.2 Query strategy ladder

The strategy ladder should be:

1. `template`
   - use one of the existing 9 templates
   - highest trust
   - highest confidence for `direct_answer`
2. `parametric`
   - generate Cypher from approved slot patterns, not from free-form LLM output
   - preferred expansion path for patent
3. `llm_cypher`
   - optional later phase
   - only for read-only, guardrailed, schema-allowlisted, low-complexity cases
   - initially better suited for `graph_for_rag` than `direct_answer`

### 4.3 Guardrail requirements

Patent should adopt fastQA V2's guardrail idea, but with a patent schema allowlist:

- read-only only
- reject `CREATE`, `MERGE`, `DELETE`, `SET`, `REMOVE`, `DROP`, `CALL`
- enforce `LIMIT`
- enforce timeout
- allow only approved patent labels and relations
- reject uncontrolled variable-length traversals in phase 1
- prefer bounded path patterns over open graph exploration

### 4.4 Canonicalized output bundle

The patent equivalent of `GraphEvidenceBundle` should carry:

- `patent_candidates`
- `ipc_candidates`
- `organization_candidates`
- `inventor_candidates`
- `facts`
- `render_slots`
- `constraints_for_rag`
- `direct_answerable`
- `confidence`
- `diagnostics`

For patent, `constraints_for_rag` should focus on patent anchors rather than DOI anchors:

- `patent_id`
- `ipc_code`
- `organization_name`
- `inventor_name`
- `material_name`
- `metric_key`
- `application_date`
- `publication_date`

## 5. Handling patent-specific entities

### 5.1 Logical schema registry

Patent needs a logical schema registry that abstracts the raw labels and edges into stable query fields.

Recommended first-pass logical fields:

- `patent.id` -> `Patent.patent_id`
- `patent.title` -> `Patent.title`
- `patent.abstract` -> `Patent.abstract`
- `patent.application_date` -> `Patent.application_date`
- `patent.publication_date` -> `Patent.publication_date`
- `patent.type` -> `Patent.patent_type`
- `patent.status` -> `Patent.legal_status`
- `patent.stub` -> `Patent.stub`
- `ipc.code` -> `IPC.code`
- `ipc.subclass` -> `IPCPrefix.subclass`
- `organization.applicant` -> `Organization.name` via `HAS_APPLICANT`
- `organization.agency` -> `Organization.name` via `HAS_AGENCY`
- `person.inventor` -> `Person.name` via `HAS_INVENTOR`
- `process.step_name` -> `ProcessStep.name`
- `process.operation` -> `ProcessStep.operation`
- `process.template` -> `StepTemplate.label`
- `process.next_step` -> `NEXT_STEP`
- `process.atmosphere` -> `Atmosphere` via `USES_ATMOSPHERE`
- `material.role_name` -> `MaterialRole.type`
- `material.role_type` -> `MaterialRole.role`
- `material.ratio` -> `MaterialRole.ratio`
- `material.name` -> `Material.name`
- `material.type` -> `Material.material_type`
- `material.key` -> `Material.canonical_key`
- `experiment.table_title` -> `ExperimentTable.table_title`
- `experiment.row_label` -> `TableRow.sample_label`
- `measurement.metric` -> `Measurement.metric_key`
- `measurement.value` -> `Measurement.value_raw`
- `measurement.unit` -> `Measurement.unit_hint`
- `problem.text` -> `TechnicalProblem.text`
- `solution.text` -> `TechnicalSolution.text`
- `scenario.text` -> `ApplicationScenario.text`
- `inventive_point.text` -> `InventivePoint.text`
- `performance_fact.text` -> `PerformanceFact.text`
- `protection_scope.text` -> `ProtectionScope.text`
- `claim_step_label.name` -> `ClaimStepLabel.name`
- `citation.outgoing` -> `CITES_PATENT`
- `embodiment.insight` -> `EmbodimentInsight` via `HAS_EMBODIMENT_INSIGHT`

### 5.2 Entity handling recommendations

#### IPC codes

- treat IPC code detection as first-class, not just a regex side branch
- support exact code and subclass expansion
- add canonicalization for uppercase and slash formatting
- support questions like "某 IPC 下有哪些专利", "某 IPC 子类常见技术方案"

#### Applicants

- expand from exact `X 有哪些专利` to more general applicant queries
- support listing, counting, comparison, and hybrid summaries
- add organization alias normalization where possible

#### Inventors

- add parity with applicants
- support inventor-based listing and counting
- allow inventor plus IPC or date filtering later through parametric builders

#### Agencies

- current graph schema supports `HAS_AGENCY`, but current patent graph_kb does not expose it
- add agency as a first-class precise entity in phase 2

#### Process steps

- keep the current direct template for single-patent lookup
- add hybrid support for multi-patent process comparison
- use `NEXT_STEP` and `USES_ATMOSPHERE` later for richer process reasoning

#### Material roles

- keep current direct template
- add aggregation questions such as:
  - common material roles under an IPC
  - common materials for an applicant's patents
  - material-role differences across patents

#### Experiment and measurement data

- keep current per-patent table rendering
- add parametric filtering by measurement key and unit
- allow graph evidence to feed RAG for questions like "which patents report X metric"

#### Problem / solution / inventive scope

- keep the current fixed templates as the direct source of trusted facts
- use these nodes heavily in `graph_for_rag` for "why", "advantage", "application scenario", and "summary" questions

## 6. Evolution from 9 fixed templates

### 6.1 Phase 1: wrap, do not replace

Keep all 9 current templates unchanged and route through them whenever they match.

This gives the patent graph a safe `direct_answer` core immediately.

### 6.2 Phase 2: add parametric families

Add constrained query builders for the most obvious missing patent intents:

- `list_patents_by_inventor`
- `list_patents_by_agency`
- `list_patents_by_ipc_subclass`
- `count_patents_by_applicant`
- `count_patents_by_inventor`
- `compare_patents_process`
- `compare_patents_material_roles`
- `compare_patents_problem_solution`
- `list_patent_atmospheres`
- `list_patent_embodiment_insights`

These should not be raw LLM-generated Cypher. They should be slot-driven builders over approved label/relation paths.

### 6.3 Phase 3: add graph-for-RAG

Once classifier, planner, executor, and canonicalizer are stable:

- allow `hybrid` patent questions to return graph evidence bundles
- let downstream generation write the narrative answer
- use graph evidence to keep entity grounding stable

This is the phase where patent starts to resemble the fastQA reference architecture operationally.

### 6.4 Phase 4: optional constrained llm_cypher

Only after phase 1-3 are stable:

- allow a small `llm_cypher` escape hatch
- keep it read-only and heavily guardrailed
- use it first for `graph_for_rag`, not for high-confidence direct answers

For patent, most value will likely come from `template + parametric`, not from broad free-form Cypher generation.

## 7. Reuse from fastQA V2 vs patent-specific work

### 7.1 Reuse directly as architecture patterns

These ideas from fastQA V2 transfer well:

- tri-state routing: `direct_answer` / `graph_for_rag` / `skip_graph`
- classifier -> planner -> strategy -> guardrail -> executor -> canonicalizer -> renderer / adapter layering
- schema registry pattern
- execution trace and diagnostics pattern
- guardrailed fallback from direct answer to graph-for-RAG
- graph evidence adapter concept

### 7.2 Reuse carefully, not literally

These pieces are reusable as patterns but need patent-specific implementations:

- `classifier_v2`
- `planner_v2`
- `executor_v2`
- `direct_renderer`
- `rag_adapter`

The fastQA code assumes:

- a DOI-centric graph
- `Neo4jGraph`
- field-bucket schema
- DOI candidates as the main retrieval constraint

Patent instead needs:

- patent-id and IPC-centric routing
- `PatentNeo4jClient`
- explicit domain entities and relationship semantics
- patent candidates and structured fact blocks as the main retrieval constraint

### 7.3 Build new for patent

Patent-specific components that must be built fresh:

- patent schema registry
- patent entity extractor and canonicalizer
- patent parametric query builders
- patent direct renderer
- patent graph-to-RAG payload design
- patent route keywords and scoring rules
- patent stub-quality policy

## 8. Patent-specific quality and risk controls

### 8.1 Stub policy

The live DB contains many `stub` patents. The current patent renderer already treats stubs conservatively. Keep that policy.

Recommended rule:

- `direct_answer`: require non-stub or otherwise sufficiently complete records
- `graph_for_rag`: stubs may contribute weak evidence, but should be marked lower confidence

### 8.2 Follow-up handling

Current patent graph skips most follow-ups. The V2 version should be less binary:

- if context resolves a prior patent / applicant / IPC anchor, allow graph use
- if not resolvable, downgrade to `graph_for_rag` or `skip_graph`

### 8.3 Multi-patent handling

Current patent graph treats multiple patent IDs as a skip condition. That is too restrictive for the target architecture.

Recommended rule:

- single patent + bounded factual intent -> `direct_answer`
- multiple patents + compare / summarize intent -> `graph_for_rag`

## 9. Recommended rollout sequence

1. Add `classifier_v2_patent` and unified routing entry point, but keep execution on the existing 9 templates only.
2. Add `schema_registry_patent`, `planner_v2_patent`, and `query_strategy_patent`.
3. Add `guardrail_patent` and `executor_v2_patent` for parametric read-only queries.
4. Add `canonicalizer_patent` and `rag_adapter_patent` so `hybrid` questions can feed downstream generation.
5. Expand template coverage with inventor, agency, IPC subclass, and multi-patent comparison builders.
6. Only then consider a constrained `llm_cypher` layer.

## 10. Bottom line

The correct adaptation is evolutionary, not greenfield:

- keep the 9 patent templates as the trusted nucleus
- add a fastQA V2-style orchestration layer around them
- route patent questions into `direct_answer`, `graph_for_rag`, or `skip_graph`
- treat patent IDs, IPC codes, applicants, inventors, process steps, material roles, and scope/problem-solution nodes as first-class logical entities
- prefer parametric patent query builders over early free-form Cypher generation

That approach preserves what is already reliable in the patent graph while unlocking the main capability fastQA's reference workflow has and patent currently lacks: graph evidence that can participate in a broader QA pipeline instead of only producing fixed-template final answers.
