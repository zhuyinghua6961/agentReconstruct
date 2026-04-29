# Patent Graph QA Parity Spec

> Status: draft, pending review.
>
> Date: 2026-04-29
>
> Scope: design the target patent graph QA behavior for the refactored `gateway` + `patent` backend, using the current patent graph data model and the fastQA graph restoration as the reference experience.
>
> This is a product and architecture specification. It is not an implementation plan and does not authorize code changes by itself.

## 1. Executive Summary

The patent backend already has the skeleton of a graph-aware KB pipeline:

```text
gateway route decision
  -> patent kb_qa
  -> patent graph preflight
  -> direct graph answer OR graph-for-RAG context OR normal vector/RAG
```

The current implementation is not yet equivalent to the fastQA graph experience. It has a tri-state contract, a Neo4j client, an allowlisted executor, canonicalization, direct rendering, and graph-to-RAG injection, but it is still shallow in three areas:

1. route understanding is mostly keyword/template based and misses many patent-specific intents;
2. query planning is spread across `client.py` and lacks a patent-specific template registry/slot layer like fastQA;
3. graph evidence mostly helps Stage1 and Stage4 prompts, but does not yet strongly constrain Stage2 retrieval and does not cover the richer patent graph schema.

The target is not to restore old code shape from `archive/oldCode`. The target is to reproduce the old user-visible experience inside the refactored architecture:

> Gateway decides file-vs-KB. Patent owns graph-vs-vector-vs-hybrid routing inside `kb_qa`. Patent graph V2 decides whether the question should be answered directly from Neo4j, converted into graph constraints for RAG, or skipped so the existing vector/RAG path remains unchanged.

For patent, the four-route experience should be adapted as:

| Internal route family | Patent meaning | Typical execution mode |
| --- | --- | --- |
| `precise` | Exact patent/entity/facet/list/count lookup from Neo4j. | `direct_answer` when evidence is complete; otherwise `graph_for_rag`. |
| `semantic` | Broad technical explanation, literature-style synthesis, or no reliable graph anchor. | `skip_graph`, preserving current vector/RAG. |
| `hybrid` | Graph supplies patent candidates, structured facts, constraints, or comparison rows; RAG synthesizes. | `graph_for_rag`. |
| `community` | Graph-backed landscape, relationship, cluster, trend, or actor/material/process network analysis. | `graph_for_rag`, with direct graph only for safe list/count sub-questions. |

The success condition is practical: patent graph answers should become useful and observable without disrupting existing patent vector database behavior.

## 2. Source Basis

### 2.1 Current Patent Code

The current patent graph path is represented by:

- `patent/server_fastapi/app.py`
- `patent/server/patent/executor.py`
- `patent/server/patent/kb_service.py`
- `patent/server/patent/graph_kb/models.py`
- `patent/server/patent/graph_kb/neo4j_client.py`
- `patent/server/patent/graph_kb/classifier.py`
- `patent/server/patent/graph_kb/classifier_v2.py`
- `patent/server/patent/graph_kb/client.py`
- `patent/server/patent/graph_kb/query_strategy.py`
- `patent/server/patent/graph_kb/planner_v2.py`
- `patent/server/patent/graph_kb/executor_v2.py`
- `patent/server/patent/graph_kb/guardrail.py`
- `patent/server/patent/graph_kb/schema_registry.py`
- `patent/server/patent/graph_kb/canonicalizer.py`
- `patent/server/patent/graph_kb/rag_adapter.py`
- `patent/server/patent/graph_kb/direct_renderer.py`
- `patent/server/patent/stages/planning.py`
- `patent/server/patent/stages/synthesis.py`
- `patent/server/patent/answering.py`
- `patent/server/patent/cache_keys.py`

### 2.2 Current Gateway Code

Gateway should remain a coarse route owner, not a graph domain owner:

- `gateway/app/services/route_decision.py`
- `gateway/app/services/file_context_resolver.py`
- `gateway/app/routers/qa.py`

Gateway currently routes user turns to modes such as `kb_qa`, `pdf_qa`, `tabular_qa`, and gateway-level `hybrid_qa`. It does not know patent graph labels, relationships, query templates, or graph/vector hybrid semantics. This is the desired boundary.

### 2.3 fastQA Reference

fastQA is the reference for graph orchestration patterns, not for patent schema:

- `fastQA/app/modules/graph_kb/slots.py`
- `fastQA/app/modules/graph_kb/query_templates.py`
- `fastQA/app/modules/graph_kb/metadata.py`
- `fastQA/app/modules/graph_kb/classifier_v2.py`
- `fastQA/app/modules/graph_kb/query_strategy.py`
- `fastQA/app/modules/graph_kb/planner_v2.py`
- `fastQA/app/modules/graph_kb/executor_v2.py`
- `fastQA/app/modules/graph_kb/canonicalizer.py`
- `fastQA/app/modules/graph_kb/rag_adapter.py`
- `fastQA/app/modules/graph_kb/direct_renderer.py`
- `fastQA/app/modules/graph_kb/service.py`

The patent implementation should borrow the shape:

- slot extraction;
- query template registry;
- detailed graph logs;
- metadata construction;
- deterministic tri-state routing;
- graph evidence payloads that are visible to planning, retrieval, and synthesis.

It must not copy fastQA schema assumptions such as DOI-centric paper nodes, material-science sample labels, or old paper graph terminology.

### 2.4 Legacy Behavior Reference

Old code in `archive/oldCode` is only a behavioral reference. The most relevant concepts are:

- a router choosing graph, literature/vector, community, or hybrid-like retrieval;
- exact graph answers for structured entity/list/count questions;
- vector/RAG for broad semantic questions;
- graph plus vector fusion for explanation/comparison questions;
- graph evidence used as constraints, not merely as a decorative context block.

The old modules should not be restored directly. Patent should stay inside the current service layout.

### 2.5 Existing Documentation

This spec builds on:

- `docs/audit/知识图谱问答流程.md`
- `docs/graph/2026-04-29-patent-graph-structure-analysis.md`
- `docs/graph/2026-04-28-fastqa-legacy-graph-parity-spec.md`
- `docs/graph/fastqa_four_route_graph_qa_spec.md`
- `docs/graph/fastqa_graph_parity_live_smoke.md`

## 3. Goals

### 3.1 Product Goals

1. Support patent graph direct answers for precise structured questions:
   - patent ID lookup;
   - process steps;
   - material roles and material options;
   - experiment tables and measurements;
   - technical problem, solution, and application scenario;
   - inventive points, performance facts, protection scope, and claim-step labels;
   - citations;
   - atmospheres;
   - embodiment insights;
   - applicant, inventor, agency, and IPC list/count questions.

2. Support graph-enhanced RAG for questions where graph evidence is useful but insufficient alone:
   - comparisons between patents;
   - why/how questions anchored to patent IDs, materials, processes, applicants, IPC classes, or graph facts;
   - process/performance/mechanism analysis;
   - technology landscape, actor, material, and process trend questions;
   - broad questions that can be narrowed by graph candidates but still require synthesis.

3. Preserve vector database behavior:
   - a question without reliable graph anchors should continue through the existing patent RAG path;
   - graph timeout, empty rows, guardrail rejection, or unsupported intent should degrade to vector/RAG instead of failing the request;
   - file routes should not be affected by patent graph changes.

4. Make graph behavior visible:
   - logs should show each graph pipeline step;
   - response metadata should indicate graph mode, route family, strategy, template/path, candidates, downgrade reason, and fingerprint;
   - tests should be able to prove whether a question took direct graph, graph-for-RAG, or vector-only path.

5. Use the real patent graph schema:
   - use `Patent`, `ProcessStep`, `StepTemplate`, `MaterialRole`, `Material`, `ExperimentTable`, `TableRow`, `Measurement`, `TechnicalProblem`, `TechnicalSolution`, `ApplicationScenario`, `InventivePoint`, `PerformanceFact`, `ProtectionScope`, `ClaimStepLabel`, `Atmosphere`, `EmbodimentInsight`, `IPC`, `IPCPrefix`, `Organization`, and `Person`;
   - avoid old paper labels such as `:doi`, `:Paper`, `:Article`, `:Sample`, `:recipe`, `:process`, `:testing`, `:name`, `:title`, `:__Chunk__`, and `:__Document__` for patent graph queries.
   - do not confuse stale labels with valid properties: `Patent.title`, `Organization.name`, `Person.name`, `Material.name`, and similar properties are valid and expected.

### 3.2 Architecture Goals

1. Keep gateway simple:
   - gateway decides whether the request is KB or file based;
   - gateway does not decide graph/vector/hybrid for patent KB;
   - gateway does not know Neo4j schema or patent graph templates.

2. Keep patent graph logic isolated:
   - graph classifier, slot extraction, query templates, executor, canonicalizer, renderer, and RAG adapter live under `patent/server/patent/graph_kb/`;
   - patent stage modules consume graph payloads through `conversation_context["graph_kb"]`, not by calling Neo4j directly.

3. Prefer deterministic graph planning:
   - no free-form LLM Cypher in this rollout;
   - all Cypher must be allowlisted through templates or prepared parametric strategies;
   - guardrail remains mandatory before execution.

4. Make direct-answer eligibility evidence based:
   - direct answers require usable rows for the requested facet;
   - `Patent.stub=true` must not by itself block direct answers when the requested graph relationships are populated;
   - empty, weak, mismatched, or overly broad graph data should become `graph_for_rag` or `skip_graph`.

5. Strengthen graph-to-RAG integration:
   - graph candidates should constrain or bias Stage2 retrieval when possible;
   - Stage1 should receive deterministic graph constraints and seeded claims;
   - Stage4 should receive compact structured fact blocks and must not invent graph facts.

## 4. Non-Goals

1. Do not restore `archive/oldCode` modules or old monolithic architecture.
2. Do not move graph classification into gateway.
3. Do not change public gateway route names.
4. Do not change file route semantics for `pdf_qa`, `tabular_qa`, or gateway-level `hybrid_qa`.
5. Do not require a Neo4j schema migration before delivering useful patent graph QA.
6. Do not commit credentials, Neo4j passwords, API keys, or secret env values.
7. Do not add free-form LLM-generated Cypher in this spec.
8. Do not make graph direct answers the default for every graph hit.
9. Do not remove or bypass existing patent vector retrieval, rerank, planning, or synthesis logic.
10. Do not rely on stale paper-like Neo4j labels that exist in the patent database with zero count. This restriction is about labels such as `:name` and `:title`, not legitimate `name` or `title` properties on patent-schema nodes.

## 5. Terminology

### 5.1 Gateway Route

Gateway route means the existing public route chosen before patent backend execution:

- `kb_qa`;
- `pdf_qa`;
- `tabular_qa`;
- gateway-level `hybrid_qa`.

Gateway-level `hybrid_qa` means file context mixing. It is not the same as graph+vector hybrid retrieval.

### 5.2 Patent Knowledge Route Family

Patent knowledge route family means the internal decision made inside patent `kb_qa`:

- `precise`;
- `semantic`;
- `hybrid`;
- `community`.

This route family should be visible in diagnostics and logs, but normal users should not need to pick it.

### 5.3 Graph Execution Mode

Graph execution mode means the tri-state result returned by patent graph V2:

- `direct_answer`: graph evidence is sufficient to return immediately;
- `graph_for_rag`: graph evidence should be injected into normal staged RAG;
- `skip_graph`: graph should not participate, and normal patent vector/RAG should proceed.

### 5.4 Patent Graph Evidence

Patent graph evidence means canonical evidence derived from Neo4j rows:

- candidate patent IDs;
- patent titles and metadata;
- applicant, inventor, agency, and IPC facts;
- process steps and step templates;
- material roles and material options;
- table rows and measurements;
- technical problem/solution/application scenario;
- inventive points, performance facts, protection scope, and claim-step labels;
- citations;
- atmospheres;
- embodiment insights;
- structured constraints and entity hints for downstream retrieval;
- compact fact blocks for final synthesis.

## 6. Current State

### 6.1 Runtime Wiring

Patent startup reads graph settings and, when enabled, bootstraps a Neo4j client. The app passes these dependencies into `PatentExecutor`, then into `PatentKbService`.

`PatentKbService.run(...)` calls graph preflight only when:

- `request.route == "kb_qa"`;
- graph KB is enabled;
- a Neo4j client exists.

When V2 is enabled, the service calls `route_patent_graph_kb_v2(...)` and handles the result:

| V2 result | Current behavior |
| --- | --- |
| `direct_answer` with handled result | Return graph answer immediately. |
| `graph_for_rag` with payload and RAG injection enabled | Write `conversation_context["graph_kb"]`, then run staged patent RAG. |
| `graph_for_rag` with injection disabled | Continue staged RAG and attach downgrade metadata. |
| `skip_graph` | Continue staged patent RAG normally. |
| exception or invalid result | Log warning and continue staged patent RAG. |

This is the correct high-level shape and should be preserved.

### 6.2 V2 Graph Pipeline

The current V2 flow is:

```text
question
  -> classify_patent_graph_question_v2
  -> build_patent_graph_query_plan_v2
  -> execute_patent_prepared_query
  -> canonicalize_patent_graph_rows
  -> build_patent_graph_rag_payload
  -> direct render OR graph_for_rag OR skip_graph
```

This should remain the primary graph pipeline. The spec requires improving the internals, not replacing this contract.

### 6.3 Current Strengths

Patent already has:

- graph settings and startup bootstrap;
- Neo4j degraded-state handling;
- tri-state graph routing;
- a V2 semantic decision model;
- an executor with guardrail;
- a schema registry;
- canonical graph evidence bundles;
- graph-to-RAG payload construction;
- direct answer renderer;
- graph metadata attachment to final response;
- Stage1 prompt awareness of graph context;
- Stage4 synthesis awareness of graph fact blocks;
- cache-key normalization for graph context.

These are good foundations and should be extended.

### 6.4 Current Gaps

1. Patent lacks `slots.py`.
   - Entity and intent extraction is duplicated across `classifier_v2.py` and `client.py`.
   - There is no shared normalized slot object for patent IDs, IPC codes, IPC prefixes, organizations, inventors, agencies, materials, process terms, metric names, comparison intent, count/list/ranking intent, or broad semantic intent.

2. Patent lacks `query_templates.py`.
   - Cypher and parametric path definitions are concentrated in `client.py`.
   - Strategy priority is harder to inspect and test than fastQA.
   - Query catalog coverage is incomplete relative to the real patent graph schema.

3. Strategy precedence is not reliable enough.
   - Generic patent lookup can win before more specific facet paths.
   - Example: atmosphere or embodiment questions can be classified as a patent lookup path instead of the specific relationship path.

4. Direct-answer policy is too blunt.
   - Some patents have `stub=true` but rich relationship data.
   - `stub=true` must not automatically force fallback when the requested facet has usable rows.

5. IPC handling is schema-mismatched.
   - Actual `IPCPrefix` grain is like `H01M`, not `H01M10`.
   - Full `IPC.code` contains values like `H01M10/0525`.
   - Queries for `H01M10` should likely match `IPC.code STARTS WITH "H01M10"` or be normalized into an appropriate prefix strategy, not match `IPCPrefix.subclass = "H01M10"`.

6. Broad graph-enhanced questions are underused.
   - Questions such as "为什么喷雾干燥能提升磷酸铁锂性能？" currently tend to skip graph.
   - The graph contains process/material/performance/embodiment evidence that can seed RAG even when no single patent ID is present.

7. Stage2 graph constraints are weak.
   - Graph payload reaches Stage1 and Stage4, but candidate patent IDs and structured constraints should more deterministically shape retrieval.
   - The system should not merely mention graph facts in a prompt if it can filter or bias retrieval by patent candidates.

8. Logging is not yet at fastQA parity.
   - Existing logs show high-level service completion.
   - They should show graph route start, classification, slots, plan, strategy, guardrail, query path attempts, row counts, canonical evidence quality, RAG payload shape, direct rendering, downgrade reason, and route end.

## 7. Real Patent Neo4j Data Shape

### 7.1 Populated Labels

The patent graph contains these important populated labels:

| Label | Observed role |
| --- | --- |
| `Patent` | Patent root node. |
| `ProcessStep` | Ordered process step instances for patents. |
| `StepTemplate` | Normalized process step type/template. |
| `MaterialRole` | Material role slot within a patent/process. |
| `Material` | Material option or substance node. |
| `ExperimentTable` | Patent experiment table. |
| `TableRow` | Experiment table row. |
| `Measurement` | Metric/value/unit evidence. |
| `TechnicalProblem` | Problem addressed by patent. |
| `TechnicalSolution` | Proposed solution. |
| `ApplicationScenario` | Application scenario. |
| `InventivePoint` | Inventive point. |
| `PerformanceFact` | Performance fact. |
| `ProtectionScope` | Protection scope item. |
| `ClaimStepLabel` | Claim step label. |
| `Atmosphere` | Atmosphere condition. |
| `EmbodimentInsight` | Embodiment-level insight. |
| `IPC` | Full IPC code. |
| `IPCPrefix` | Coarser IPC prefix/subclass. |
| `Organization` | Applicant and agency nodes. |
| `Person` | Inventor nodes. |

Labels such as `:doi`, `:Paper`, `:Article`, `:Sample`, `:recipe`, `:process`, `:testing`, `:name`, `:title`, `:__Chunk__`, and `:__Document__` are not useful for patent graph planning because their patent DB counts are zero or stale.

This label restriction must not be implemented as a ban on property names. Patent templates may and should use legitimate properties such as `p.title`, `org.name`, `person.name`, `material.name`, `ipc.code`, and `sub.subclass`.

### 7.2 Populated Relationships

The graph contains these major relationship paths:

| Relationship | Expected use |
| --- | --- |
| `HAS_PROCESS_STEP` | Patent to process steps. |
| `INSTANCE_OF` | Process step to normalized step template. |
| `NEXT_STEP` | Step ordering. |
| `HAS_MATERIAL_ROLE` | Patent to material role slots. |
| `OPTION_INCLUDES` | Material role to material options. |
| `HAS_EXPERIMENT_TABLE` | Patent to experiment table. |
| `HAS_ROW` | Experiment table to rows. |
| `HAS_MEASUREMENT` | Row to metric/value/unit measurements. |
| `ADDRESSES` | Patent to technical problem. |
| `PROPOSES` | Patent to technical solution. |
| `HAS_APPLICATION_SCENARIO` | Patent to application scenario. |
| `HAS_INVENTIVE_POINT` | Patent to inventive point. |
| `HAS_PERFORMANCE_FACT` | Patent to performance fact. |
| `PROTECTION_INCLUDES` | Patent to protection scope. |
| `CLAIM_INCLUDES_STEP` | Patent to claim-step label. |
| `CITES_PATENT` | Patent citation edge. |
| `CLASSIFIED_AS` | Patent to full IPC code. |
| `IN_IPC_SUBCLASS` | Patent to IPC prefix/subclass. |
| `HAS_APPLICANT` | Patent to applicant organization. |
| `HAS_AGENCY` | Patent to agency organization. |
| `HAS_INVENTOR` | Patent to inventor person. |
| `USES_ATMOSPHERE` | Patent/process to atmosphere condition. |
| `HAS_EMBODIMENT_INSIGHT` | Patent to embodiment insight. |

### 7.3 Size Implications

The graph is large enough that broad queries need bounded templates and deterministic limits:

- `Patent`: about 39.5k nodes;
- `Measurement`: about 698k nodes;
- `TableRow`: about 128k nodes;
- `InventivePoint`: about 128k nodes;
- `PerformanceFact`: about 92k nodes;
- `ProcessStep`: about 64k nodes;
- `MaterialRole`: about 60k nodes;
- `Person`: about 23.6k nodes;
- `Organization`: about 5.2k nodes.

Spec implication:

- exact ID/facet queries can be direct;
- broad metric/material/process queries must be capped, ranked, or converted to graph-for-RAG;
- count queries should use aggregation templates;
- landscape/community queries must not scan unbounded rows without limits.

### 7.4 Evidence Quality Example

`CN100355122C` has rich graph data:

- title and abstract;
- process steps such as precursor preparation, solid-liquid separation, drying, material mixing, calcining, cooling;
- material roles such as dopant source, pH regulator, reducing carbon, lithium source, oxidant, iron source, phosphorus source;
- problem, solution, scenario;
- inventive points, performance facts, protection scope, claim step labels;
- citations;
- atmosphere condition.

However, it may still carry `stub=true`.

Spec implication:

> `stub=true` is an attribute, not a verdict. Direct-answer eligibility must be based on whether the requested facet has enough usable rows and fields.

## 8. Target User-Visible Behavior

### 8.1 Direct Graph Answer

Direct graph answers should be used when all of the following are true:

1. the user asks for an exact structured fact/list/count;
2. the graph has a reliable anchor, such as patent ID, applicant, inventor, agency, IPC, material, process term, or metric;
3. the chosen template returns usable rows for the requested facet;
4. rendering can preserve values without requiring unsupported inference;
5. file context is absent or does not need to be synthesized with graph evidence.

Examples:

| Question | Expected route family | Expected graph mode | Notes |
| --- | --- | --- | --- |
| `CN100355122C 这件专利是什么？` | `precise` | `direct_answer` if patent metadata usable | `stub=true` alone must not block. |
| `CN100355122C 的工艺步骤是什么？` | `precise` | `direct_answer` | Ordered steps from `HAS_PROCESS_STEP` and `NEXT_STEP`/`position`. |
| `CN100355122C 使用了哪些原料？` | `precise` | `direct_answer` | Material roles plus material options. |
| `CN100355122C 的技术问题和技术方案是什么？` | `precise` | `direct_answer` | Problem/solution/scenario facts. |
| `CN100355122C 的发明点和保护范围是什么？` | `precise` | `direct_answer` | Inventive/protection/claim/performance facts. |
| `CN100355122C 的气氛条件是什么？` | `precise` | `direct_answer` | Specific atmosphere template must outrank generic lookup. |
| `CN101209823B 的实施例洞察是什么？` | `precise` | `direct_answer` if insight rows exist | Specific embodiment template. |
| `宁德时代新能源科技股份有限公司有哪些专利？` | `precise` | `direct_answer` | Applicant listing. |
| `发明人李长东有多少专利？` | `precise` | `direct_answer` | Inventor count. |
| `H01M 下有哪些专利？` | `precise` | `direct_answer` | IPCPrefix match. |
| `H01M10/0525 有多少专利？` | `precise` | `direct_answer` | Full IPC code match. |
| `H01M10 下有哪些专利？` | `precise` | `direct_answer` or `graph_for_rag` | Prefix match over `IPC.code`, not `IPCPrefix.subclass = H01M10`. |

### 8.2 Graph-for-RAG Hybrid

Graph-for-RAG should be used when graph evidence is relevant but final wording requires synthesis, comparison, explanation, or ranking.

Examples:

| Question | Expected route family | Expected graph mode | Notes |
| --- | --- | --- | --- |
| `比较 CN100355122C 和 CN100369314C 的工艺步骤差异` | `hybrid` | `graph_for_rag` | Graph supplies step rows per patent; synthesis explains differences. |
| `CN100355122C 为什么能提升大电流放电性能？` | `hybrid` | `graph_for_rag` | Patent ID anchors graph; RAG explains mechanism using graph facts and text. |
| `使用喷雾干燥的磷酸铁锂专利有哪些共性？` | `hybrid` or `community` | `graph_for_rag` | Graph finds candidate patents/processes; RAG synthesizes commonality. |
| `宁德时代在磷酸铁锂方面的工艺路线有什么特点？` | `community` | `graph_for_rag` | Applicant + material/process graph constraints. |
| `哪些材料角色经常和碳包覆工艺一起出现？` | `community` | `graph_for_rag` | Co-occurrence/network-style graph evidence. |
| `为什么喷雾干燥能提升磷酸铁锂性能？` | `hybrid` | `graph_for_rag` when graph has process/material/performance matches | No patent ID, but process/material/performance anchors exist. |

### 8.3 Vector-Only / Skip Graph

Skip graph should be used when:

- no graph anchor exists;
- question asks broad background knowledge and graph would add noise;
- query would require unsupported graph schema;
- DOI-only question appears, because patent graph is patent-ID centric;
- guardrail rejects a query;
- Neo4j is unavailable or times out and no reliable graph evidence was obtained.

Examples:

| Question | Expected route family | Expected graph mode | Notes |
| --- | --- | --- | --- |
| `磷酸铁锂正极材料的发展趋势是什么？` | `semantic` | `skip_graph` or `graph_for_rag` only if explicit graph landscape support is added | Default vector/RAG should remain safe. |
| `10.xxxx/xxxx 这篇文献是什么？` | `semantic` | `skip_graph` | Patent graph does not key by DOI. |
| `帮我总结一下刚上传的PDF` | gateway file route | no patent graph preflight | Gateway/file semantics preserved. |
| `这个表格里的数据说明什么？` | gateway table route | no patent graph preflight | Patent graph is out of scope. |

## 9. Target Data Flow

### 9.1 Gateway to Patent

The gateway should continue to:

1. inspect request/file context;
2. choose public route;
3. forward KB requests to patent as `kb_qa`;
4. forward file requests using existing file routes.

Gateway must not:

- inspect Neo4j schema;
- select patent graph templates;
- decide direct graph vs graph-for-RAG;
- inject patent graph constraints.

### 9.2 Patent KB Direct Graph Flow

Target direct flow:

```text
PatentKbService.run(kb_qa)
  -> route_patent_graph_kb_v2
    -> extract slots
    -> classify route family and graph mode
    -> select query template/strategy
    -> guardrail inspect Cypher
    -> execute Neo4j query
    -> canonicalize evidence
    -> evaluate direct-answer eligibility
    -> render direct answer
  -> return PatentGraphKbExecutionResult
```

Direct answer metadata should include:

- `graph_pipeline_version`;
- `graph_kb_mode=direct_answer`;
- `graph_kb_route_family`;
- `graph_kb_strategy`;
- `graph_kb_template_id` or `graph_kb_path_id`;
- `graph_kb_row_count`;
- `graph_kb_evidence_quality`;
- `graph_kb_direct_reason`;
- `graph_kb_fingerprint`;
- bounded references/reference objects when applicable.

### 9.3 Patent Graph-for-RAG Flow

Target hybrid flow:

```text
PatentKbService.run(kb_qa)
  -> route_patent_graph_kb_v2
    -> slots/classify/plan/execute/canonicalize
    -> build graph RAG payload
  -> conversation_context["graph_kb"] = payload
  -> PatentGenerationOrchestrator.run(...)
    -> Stage1 planning uses graph constraints and seeded claims
    -> Stage2 retrieval filters/biases by graph candidates where possible
    -> Stage4 synthesis uses graph fact block and retrieved text
  -> final response includes graph metadata
```

The payload should be compact and deterministic:

- `mode`;
- `route_family`;
- `strategy`;
- `template_id` or `path_id`;
- `stage1_context_block`;
- `stage1_seed_claims`;
- `stage2_patent_candidates`;
- `stage2_entity_hints`;
- `stage2_constraints`;
- `stage4_fact_block`;
- `source_graph_rows` or bounded row summary;
- `cache_fingerprint`.

### 9.4 Skip Graph Flow

Target skip flow:

```text
PatentKbService.run(kb_qa)
  -> route_patent_graph_kb_v2
  -> skip_graph with diagnostics
  -> existing staged patent RAG unchanged
```

Skip graph should still log diagnostics. It should not modify the RAG context.

## 10. Patent Slot Model

Patent should introduce a normalized slot extraction layer. This is a design requirement, not a file-level implementation order.

### 10.1 Entity Slots

| Slot | Examples | Notes |
| --- | --- | --- |
| `patent_ids` | `CN100355122C`, `US...` | Uppercase, deduplicated, preserve order. |
| `ipc_full_codes` | `H01M10/0525` | Match `IPC.code`. |
| `ipc_prefixes` | `H01M`, `C01B` | Match `IPCPrefix.subclass` or actual prefix property. |
| `ipc_code_prefixes` | `H01M10` | Match `IPC.code STARTS WITH`. |
| `applicant_names` | `宁德时代新能源科技股份有限公司` | Organization via `HAS_APPLICANT`. |
| `inventor_names` | `李长东` | Person via `HAS_INVENTOR`. |
| `agency_names` | patent代理机构 names | Organization via `HAS_AGENCY`. |
| `material_terms` | `磷酸铁锂`, `碳酸锂`, `葡萄糖` | Match `Material.name` and text fields. |
| `material_role_terms` | `锂源`, `铁源`, `碳源` | Match `MaterialRole.role` or similar property. |
| `process_terms` | `喷雾干燥`, `煅烧`, `混合` | Match `StepTemplate.name`, `ProcessStep.name`, text. |
| `metric_terms` | `放电容量`, `循环性能`, `倍率性能` | Match `Measurement.metric` and performance facts. |
| `atmosphere_terms` | `氮气`, `空气`, `惰性气氛` | Match `Atmosphere`. |

### 10.2 Intent Slots

| Slot | Meaning |
| --- | --- |
| `asks_lookup` | User asks what a patent/entity is. |
| `asks_list` | User asks "有哪些". |
| `asks_count` | User asks "多少/数量/统计". |
| `asks_compare` | User asks comparison/difference. |
| `asks_rank` | User asks top/best/most/common/highest. |
| `asks_process` | Process steps or route. |
| `asks_materials` | Raw materials, material roles, options. |
| `asks_experiment` | Experiment tables, rows, measurements. |
| `asks_problem_solution` | Technical problem, solution, scenario. |
| `asks_inventive_scope` | Inventive point, performance fact, protection scope, claims. |
| `asks_citation` | Citation/reference graph. |
| `asks_atmosphere` | Atmosphere conditions. |
| `asks_embodiment` | Embodiment insights. |
| `asks_why_how` | Explanation/mechanism. |
| `asks_trend_landscape` | Trend, landscape, cluster/community. |
| `asks_followup` | Pronoun or context-dependent turn. |
| `has_file_context` | Gateway/file context is attached. |

### 10.3 Slot Extraction Requirements

1. Extraction must be deterministic and unit-testable.
2. Regex patterns must avoid over-capturing Chinese organization names in inventor/agency queries.
3. Patent IDs must be deduplicated but preserve first appearance order.
4. IPC parsing must distinguish:
   - `H01M`;
   - `H01M10`;
   - `H01M10/0525`.
5. Slot extraction should not call Neo4j.
6. Slot extraction should feed classifier, planner, and metadata consistently.

### 10.4 Slot-to-Template Parameter Contract

The normalized slot model is intentionally plural because one question can contain multiple entities. Query templates may still declare singular required parameters when the template operates on one selected value.

Planner responsibilities:

1. select or expand plural slots into concrete template parameters;
2. preserve slot order when selecting the primary entity;
3. emit a deterministic plan per template invocation;
4. record the selected value in diagnostics;
5. reject or reroute when a singular template receives multiple incompatible slot values.

Required mappings:

| Normalized slot | Template parameter examples | Selection rule |
| --- | --- | --- |
| `patent_ids` | `patent_id`, `left_patent_id`, `right_patent_id`, `patent_ids` | Single-patent templates use the first ID only when exactly one ID is allowed; comparison templates use the ordered list. |
| `applicant_names` | `applicant_name`, `organization_name` | Use first exact applicant mention for list/count; multiple applicants should route to comparison/landscape or graph-for-RAG. |
| `inventor_names` | `inventor_name` | Use first inventor for list/count; multiple inventors require a multi-entity template. |
| `agency_names` | `agency_name` | Use first agency for list/count. |
| `ipc_prefixes` | `ipc_prefix` | Use for `IPCPrefix` templates such as `H01M`. |
| `ipc_code_prefixes` | `ipc_code_prefix` | Use for `IPC.code STARTS WITH` templates such as `H01M10`. |
| `ipc_full_codes` | `ipc_full_code`, `ipc_code` | Use for exact `IPC.code` templates such as `H01M10/0525`. |
| `material_terms` | `material_term` | Use selected material anchor for list/count/hybrid templates. |
| `process_terms` | `process_term` | Use selected process anchor for list/count/hybrid templates. |
| `metric_terms` | `metric_term` | Use selected metric anchor for measurement/performance templates. |

Tests should assert both sides of this contract: extracted plural slots and generated singular template parameters.

## 11. Route Classification Spec

### 11.1 Classification Inputs

Classifier should receive:

- question text;
- normalized slots;
- conversation context;
- file-context signal;
- optional runtime feature flags only for enabled/disabled capability, not for semantics.

### 11.2 Classification Output

Classifier should output:

- graph mode: `direct_answer`, `graph_for_rag`, or `skip_graph`;
- route family: `precise`, `semantic`, `hybrid`, or `community`;
- standalone flag;
- requires-context-resolution flag;
- matched rule;
- confidence or rule strength;
- diagnostics including slots and downgrade reason.

### 11.3 Classification Rules

Precise direct candidates:

- one patent ID plus exact facet intent;
- applicant/inventor/agency list/count;
- IPC list/count;
- material/process exact list with list/count/rank intent and bounded result shape;
- single-patent citations, atmosphere, embodiment, problem/solution, inventive/protection facets.

Hybrid candidates:

- multiple patent IDs with compare intent;
- patent ID plus why/how/advantage/mechanism intent;
- material/process/metric anchor plus why/how/commonality/trend intent;
- applicant/inventor/IPC anchor plus landscape/process/material/performance analysis intent;
- ambiguous follow-up with graph hint and previous graph context;
- direct candidate downgraded because file context is present.

Community candidates:

- co-occurrence questions;
- landscape/trend questions anchored by applicant, material, process, IPC, or metric;
- network questions such as "哪些材料角色经常和 X 一起出现";
- cluster-like questions that need grouped graph evidence plus RAG.

Semantic skip candidates:

- DOI-only questions;
- no graph anchor and no graph-relevant term;
- broad educational questions where graph would not constrain retrieval;
- file/table/PDF gateway routes;
- unsupported relation requests.

### 11.4 Downgrade Rules

A classifier may start with a direct candidate and later downgrade:

| Condition | Downgrade |
| --- | --- |
| File context present on a request that still reaches patent as `kb_qa` | `direct_answer` -> `graph_for_rag` when graph evidence is still relevant. |
| Gateway route is `pdf_qa`, `tabular_qa`, or gateway-level `hybrid_qa` | Do not run patent graph preflight. Preserve file route behavior. |
| Multiple patents and compare/synthesis intent | `direct_answer` -> `graph_for_rag`. |
| Rows exist but requested facet is partial | `direct_answer` -> `graph_for_rag`. |
| Empty rows | `direct_answer` -> `skip_graph` unless graph slots should still seed RAG. |
| Graph timeout or guardrail rejection | `skip_graph` with diagnostics. |
| Unsupported template but semantic graph anchors exist | `graph_for_rag` only if useful constraints can be formed; otherwise `skip_graph`. |

## 12. Query Template Catalog

Patent should have an explicit template registry comparable to fastQA. The registry should describe:

- path/template ID;
- supported route families;
- required slots;
- optional slots;
- graph mode eligibility;
- Cypher;
- row normalizer expectations;
- result cap;
- direct-answer renderer;
- graph-for-RAG adapter behavior.

### 12.1 Single Patent Facet Templates

| Template ID | Required slots | Main path | Direct eligible |
| --- | --- | --- | --- |
| `lookup_patent_by_id` | `patent_id` | `(p:Patent)` | Yes, if title/abstract/metadata usable. |
| `list_patent_process_steps` | `patent_id` | `Patent-HAS_PROCESS_STEP-ProcessStep-INSTANCE_OF-StepTemplate` | Yes. |
| `list_patent_material_roles` | `patent_id` | `Patent-HAS_MATERIAL_ROLE-MaterialRole-OPTION_INCLUDES-Material` | Yes. |
| `list_patent_experiment_tables` | `patent_id` | `Patent-HAS_EXPERIMENT_TABLE-ExperimentTable-HAS_ROW-TableRow-HAS_MEASUREMENT-Measurement` | Yes if bounded and table rows usable; otherwise graph-for-RAG. |
| `list_patent_problem_solution` | `patent_id` | `ADDRESSES`, `PROPOSES`, `HAS_APPLICATION_SCENARIO` | Yes. |
| `list_patent_inventive_scope` | `patent_id` | `HAS_INVENTIVE_POINT`, `HAS_PERFORMANCE_FACT`, `PROTECTION_INCLUDES`, `CLAIM_INCLUDES_STEP` | Yes. |
| `list_patent_citations` | `patent_id` | `CITES_PATENT` | Yes for bounded list. |
| `list_patent_atmospheres` | `patent_id` | `USES_ATMOSPHERE` | Yes. |
| `list_patent_embodiment_insights` | `patent_id` | `Patent-HAS_EMBODIMENT_INSIGHT-EmbodimentInsight` | Yes if rows exist. |

Specific facet templates must outrank `lookup_patent_by_id`.

### 12.2 Entity Listing and Count Templates

| Template ID | Required slots | Main path | Direct eligible |
| --- | --- | --- | --- |
| `list_patents_by_applicant` | `applicant_name` | `Patent-HAS_APPLICANT-Organization` | Yes. |
| `count_patents_by_applicant` | `applicant_name` | same | Yes. |
| `list_patents_by_inventor` | `inventor_name` | `Patent-HAS_INVENTOR-Person` | Yes. |
| `count_patents_by_inventor` | `inventor_name` | same | Yes. |
| `list_patents_by_agency` | `agency_name` | `Patent-HAS_AGENCY-Organization` | Yes. |
| `count_patents_by_agency` | `agency_name` | same | Yes. |
| `list_patents_by_ipc_prefix` | `ipc_prefix` | `Patent-IN_IPC_SUBCLASS-IPCPrefix` | Yes. |
| `list_patents_by_ipc_full_code` | `ipc_full_code` | `Patent-CLASSIFIED_AS-IPC` | Yes. |
| `list_patents_by_ipc_code_prefix` | `ipc_code_prefix` | `IPC.code STARTS WITH $prefix` | Yes or graph-for-RAG if too broad. |
| `count_patents_by_ipc_*` | IPC slot | matching IPC path | Yes. |

### 12.3 Material, Process, and Metric Templates

| Template ID | Required slots | Main path | Direct eligible |
| --- | --- | --- | --- |
| `list_patents_by_material` | `material_term` | `Patent-HAS_MATERIAL_ROLE-MaterialRole-OPTION_INCLUDES-Material` | Yes for list/count; hybrid for analysis. |
| `list_patents_by_material_role` | `material_role_term` | `MaterialRole` | Yes for list/count. |
| `list_patents_by_process_term` | `process_term` | `ProcessStep/StepTemplate` | Yes for list/count; hybrid for explanation. |
| `list_patents_by_metric` | `metric_term` | `Measurement` and `PerformanceFact` | Usually graph-for-RAG unless exact count/list. |
| `rank_materials_by_frequency` | material/process/IPC/applicant optional | grouped material counts | Direct only for pure ranking table; hybrid for explanation. |
| `rank_processes_by_frequency` | material/IPC/applicant optional | grouped step/template counts | Direct only for pure ranking table; hybrid for explanation. |

Broad list/rank direct-answer boundaries:

1. direct rendering is allowed only for bounded list/count/rank questions, not explanation questions;
2. default direct display should cap returned items to the configured graph max rows;
3. templates that can match more than the display cap must include total count or truncation metadata when feasible;
4. if the query would require scanning a very broad portion of `Measurement`, `ProcessStep`, or `MaterialRole` without a selective anchor, classify as `graph_for_rag` or `skip_graph` instead of direct;
5. ranking templates should return grouped rows with counts and representative patent IDs, not unbounded raw evidence rows;
6. response metadata must indicate truncation, cap, and whether the answer is a sample or complete list.

### 12.4 Comparison Templates

| Template ID | Required slots | Main path | Direct eligible |
| --- | --- | --- | --- |
| `compare_patents_process_steps` | 2+ patent IDs | process steps per patent | No, graph-for-RAG. |
| `compare_patents_material_roles` | 2+ patent IDs | material roles per patent | No, graph-for-RAG. |
| `compare_patents_problem_solution` | 2+ patent IDs | problem/solution/scenario | No, graph-for-RAG. |
| `compare_patents_performance_facts` | 2+ patent IDs | performance facts and measurements | No, graph-for-RAG. |
| `compare_patents_claim_scope` | 2+ patent IDs | protection and claim step labels | No, graph-for-RAG. |

Comparisons should return structured per-patent rows and let RAG/synthesis explain differences.

### 12.5 Community and Landscape Templates

| Template ID | Required slots | Main path | Direct eligible |
| --- | --- | --- | --- |
| `landscape_by_applicant_material` | applicant + material/process optional | patents grouped by process/material/IPC | No, graph-for-RAG. |
| `landscape_by_ipc_material` | IPC + material/process optional | patents grouped by applicant/process/material | No, graph-for-RAG. |
| `cooccurring_material_roles` | material/process/role | material role co-occurrence | Usually graph-for-RAG. |
| `cooccurring_process_steps` | material/process/IPC optional | process template co-occurrence/order | Usually graph-for-RAG. |
| `performance_by_process_term` | process + metric/material optional | process to performance facts/measurements | Graph-for-RAG. |
| `performance_by_material_term` | material + metric/process optional | material to performance facts/measurements | Graph-for-RAG. |

Community route in this patent graph does not require a separate community vector DB in the first version. It means graph-derived network/grouping evidence that helps RAG answer landscape questions.

## 13. Strategy Priority

Strategy selection should be deterministic and testable.

Priority order:

1. Safety skip:
   - empty question;
   - DOI-only unsupported question;
   - file route context where graph should not run.

2. Multi-patent comparison:
   - 2+ patent IDs plus compare/difference intent;
   - choose comparison template before any single-patent lookup.

3. Single-patent specific facet:
   - process/material/experiment/problem/inventive/citation/atmosphere/embodiment templates;
   - these must outrank generic `lookup_patent_by_id`.

4. Single-patent generic lookup:
   - only when no more specific facet intent exists.

5. Entity count:
   - applicant/inventor/agency/IPC/material/process count/statistics.

6. Entity list:
   - applicant/inventor/agency/IPC/material/process list.

7. Ranking/table:
   - top/common/highest/frequency questions.

8. Hybrid anchored explanation:
   - why/how/commonality/trend with graph anchors.

9. Community/landscape:
   - co-occurrence, actor/material/process landscape, grouped evidence.

10. Semantic skip:
   - no reliable graph path.

This priority specifically fixes the observed problem where `lookup_patent_by_id` can beat `list_patent_atmospheres` or `list_patent_embodiment_insights`.

## 14. Evidence Canonicalization

Canonicalization should convert raw Neo4j rows into a consistent evidence bundle:

- normalized patent IDs;
- candidate patent IDs for retrieval;
- source entity hints;
- direct renderer rows;
- constraints for RAG;
- structured facts;
- evidence quality diagnostics;
- direct-answer eligibility.

### 14.1 Evidence Quality

The bundle should evaluate quality per template:

| Quality signal | Meaning |
| --- | --- |
| `has_rows` | Neo4j returned at least one row. |
| `has_requested_facet` | Returned row contains the relationship/facet the user asked for. |
| `has_textual_fact` | Row has non-empty fact text/title/name/value. |
| `has_identifier` | Row has patent ID or entity ID needed for citation/candidate linkage. |
| `is_bounded` | Row count is within rendering limit. |
| `is_partial` | Some expected facets are missing but others exist. |
| `is_stub_only` | Only a stub patent node exists with no useful requested facet rows. |
| `has_measurement_value` | Metric row has value/unit/raw text when numeric facts are requested. |

### 14.2 Direct Eligibility Policy

Direct answer is allowed when:

- graph mode was classified as `direct_answer`;
- guardrail passed;
- execution did not time out;
- bundle has usable rows;
- requested facet is present;
- direct renderer supports the template/path;
- response can be rendered without inventing explanations.

Direct answer is not allowed when:

- query is comparative/explanatory/landscape;
- rows are empty;
- rows are unrelated to the requested facet;
- values are too partial for a direct answer;
- result is too broad to display directly;
- renderer cannot preserve important numeric/text values.

`stub=true` must be interpreted as:

- a warning for metadata-only lookup if no useful fields exist;
- not a blocker for relationship-backed facet answers when relationship rows are populated.

## 15. Graph-to-RAG Contract

### 15.1 Stage1 Planning

Stage1 should receive:

- concise graph context block;
- route family and graph mode;
- candidate patent IDs;
- entity hints;
- explicit constraints;
- seed claims/facts.

Stage1 should use this to:

- avoid redundant broad planning when exact graph candidates exist;
- produce retrieval claims aligned with graph evidence;
- preserve user intent for comparison/explanation;
- avoid fabricating facts not in graph or retrieved text.

### 15.2 Stage2 Retrieval

Stage2 should do more than read graph context text. It should use graph payload structurally when existing retrieval APIs allow it.

Required behavior:

- when candidate patent IDs exist, prefer or filter retrieval to those patents;
- when exact patent IDs are in the user question, include them as high-priority retrieval constraints;
- when applicant/inventor/IPC/material/process constraints exist, convert them into retrieval hints or filters if supported;
- when hard filtering is unavailable, use graph candidates as deterministic retrieval claims and ranking hints;
- if graph candidates produce no vector hits, fall back to normal retrieval and preserve downgrade metadata.

Stage2 must not:

- drop existing vector retrieval entirely for graph-for-RAG;
- return graph rows as if they were document chunks unless the response contract supports graph references;
- silently ignore graph candidates without metadata.

Stage2 acceptance is behavioral, not just prompt-level:

- tests must prove that `stage2_patent_candidates` or equivalent constraints are passed into retrieval filtering/biasing code when supported by the retrieval service;
- if hard filtering is not supported, tests must prove that candidate patents become deterministic retrieval claims or ranking hints;
- metadata must distinguish `filter_applied`, `bias_applied`, `hint_only`, and `fallback_no_vector_hits`;
- a graph-for-RAG request with graph candidates must not be considered complete if the implementation only appends graph facts to Stage1/Stage4 prompts and never influences Stage2 retrieval.

### 15.3 Stage4 Synthesis

Stage4 should receive:

- graph fact block;
- graph route/mode/fingerprint;
- retrieved text evidence;
- source IDs and references.

Stage4 should:

- use graph facts as structured context;
- use retrieved text for explanation and citation-rich synthesis;
- clearly avoid claiming unavailable measurements/details;
- preserve numeric values, units, and original raw values;
- describe graph-derived facts as graph evidence when needed.

## 16. Metadata and Logging

Patent graph logs should be detailed enough to diagnose routing without reading code.

### 16.1 Required Log Events

Use the existing patent logger hierarchy or a dedicated graph logger. Each event should include `trace_id` when available.

| Event | Required fields |
| --- | --- |
| `patent_graph.route_start` | question length, route, max rows, timeout. |
| `patent_graph.slots_done` | slot counts/types, not secrets. |
| `patent_graph.classify_done` | mode, route family, matched rule, confidence, downgrade. |
| `patent_graph.plan_done` | strategy, template/path, required slots, params keys. |
| `patent_graph.guardrail_done` | verdict, rejected reason if any. |
| `patent_graph.execute_attempt` | path ID, attempt index, limit, timeout. |
| `patent_graph.execute_done` | row count, latency, Neo4j client state. |
| `patent_graph.canonicalize_done` | candidate count, fact count, evidence quality, direct eligible. |
| `patent_graph.rag_payload_done` | stage1/stage2/stage4 payload presence and candidate counts. |
| `patent_graph.direct_render_done` | handled, template/path, references count, reason if not handled. |
| `patent_graph.route_end` | final graph mode, downgrade reason, total latency. |

### 16.2 Response Metadata

Final response metadata should include graph summary when graph was attempted:

- `graph_kb_attempted`;
- `graph_kb_mode`;
- `graph_kb_route_family`;
- `graph_kb_strategy`;
- `graph_kb_template_id` or `graph_kb_path_id`;
- `graph_kb_fingerprint`;
- `graph_kb_candidate_patent_ids`;
- `graph_kb_constraints`;
- `graph_kb_row_count`;
- `graph_kb_evidence_quality`;
- `graph_kb_downgrade_reason`;
- `graph_kb_error` only for non-sensitive failure summaries.

Metadata should not include:

- Neo4j password;
- API keys;
- raw secrets;
- unbounded raw row dumps.

## 17. Error Handling and Degradation

The graph path should fail open to existing patent RAG.

| Failure | Required behavior |
| --- | --- |
| Neo4j unavailable at startup | Mark graph component degraded; patent backend still starts. |
| Neo4j query timeout | Log timeout; return `skip_graph` or graph-for-RAG with partial payload only if safe. |
| Guardrail rejection | Skip graph; include diagnostic reason. |
| Empty rows | Skip graph unless slots still provide useful retrieval constraints. |
| Direct renderer unsupported | Downgrade to graph-for-RAG if payload exists. |
| Canonicalizer exception | Skip graph; do not fail whole KB request. |
| RAG injection disabled | Continue normal RAG; attach downgrade metadata. |
| Stage2 cannot filter by candidates | Use candidates as retrieval claims/hints and record fallback. |

No graph failure should break the existing vector database path for normal KB questions.

## 18. Caching

Graph context can affect Stage1/Stage2/Stage4 outputs and must be part of cache identity when present.

Requirements:

1. cache fingerprint should include graph mode, route family, template/path, constraints, candidate patent IDs, and fact block hash;
2. fingerprint should not include raw unbounded rows;
3. equivalent graph payloads should produce stable keys;
4. graph skip should not change existing vector-only cache behavior;
5. graph errors should not poison successful vector-only cache entries.

`patent/server/patent/cache_keys.py` already has graph context normalization. The spec requires keeping it aligned with any new graph payload fields.

## 19. Testing and Acceptance Criteria

### 19.1 Unit Tests

Required unit coverage:

1. slot extraction:
   - patent IDs;
   - applicant/inventor/agency;
   - `H01M`, `H01M10`, `H01M10/0525`;
   - material/process/metric terms;
   - count/list/compare/why/how/rank signals.
   - plural slot extraction and singular template parameter selection.

2. classifier:
   - direct precise single-patent facets;
   - specific facet outranks generic lookup;
   - process, material, problem/solution, inventive/scope, citation, atmosphere, and embodiment intents all outrank generic patent lookup;
   - multi-patent compare -> graph-for-RAG;
   - patent ID plus why/how -> graph-for-RAG;
   - DOI-only -> skip;
   - broad semantic without graph anchor -> skip;
   - file context on a `kb_qa` request downgrades direct to graph-for-RAG;
   - gateway file routes do not trigger patent graph preflight.

3. query templates:
   - every template has required slots;
   - generated Cypher uses allowed labels/relationships;
   - no stale paper labels in patent query catalog;
   - valid `name` and `title` properties are not rejected merely because stale `:name` and `:title` labels exist;
   - `HAS_EMBODIMENT_INSIGHT` is allowlisted and covered by embodiment templates;
   - IPC prefix/full/prefix-of-code templates behave differently.

4. query strategy:
   - precedence order is stable;
   - process/material/problem-solution/inventive-scope/citation/atmosphere/embodiment templates beat generic lookup;
   - empty unsupported paths skip safely.

5. guardrail:
   - read-only allowlisted queries pass;
   - writes, deletes, calls, unallowlisted labels, and risky queries fail.

6. canonicalizer:
   - `stub=true` plus populated facet rows can be direct-answerable;
   - stub-only metadata row is not direct-answerable for facet questions;
   - rows produce candidate patent IDs, constraints, facts, and diagnostics.

7. RAG adapter:
   - produces compact Stage1/Stage2/Stage4 fields;
   - includes candidate patent IDs;
   - stable fingerprint;
   - no unbounded raw row dump.

8. KB service integration:
   - direct graph returns without staged RAG;
   - graph-for-RAG injects context and continues staged RAG;
   - skip graph leaves context unchanged;
   - graph exception falls back to existing RAG.
   - gateway-through-patent tests prove `kb_qa` can trigger patent graph while `pdf_qa`, `tabular_qa`, and gateway-level `hybrid_qa` do not.

9. Stage2 retrieval integration:
   - graph candidate patent IDs reach retrieval filter/bias/hint inputs;
   - constraints for applicant, inventor, IPC, material, and process are converted into supported retrieval controls where available;
   - no vector hits after graph filtering causes a recorded fallback, not an empty final answer;
   - metadata records whether filter, bias, hint-only, or fallback behavior occurred.

### 19.2 Real Neo4j Smoke Tests

These are manual or integration tests against local Neo4j using the `agent` conda environment and approved credentials. They should not print secrets.

Minimum smoke matrix:

| Question | Expected |
| --- | --- |
| `CN100355122C 这件专利是什么？` | Direct graph if metadata fields usable; otherwise graph-for-RAG with explicit downgrade reason, not because `stub=true` alone. |
| `CN100355122C 的工艺步骤是什么？` | Direct graph with process steps. |
| `CN100355122C 使用了哪些原料？` | Direct graph with material roles/options. |
| `CN100355122C 的技术问题和技术方案是什么？` | Direct graph. |
| `CN100355122C 的发明点和保护范围是什么？` | Direct graph or graph-for-RAG if too long, with facts present. |
| `CN100355122C 的气氛条件是什么？` | Atmosphere template, not generic lookup. |
| `CN101209823B 的实施例洞察是什么？` | Embodiment template when rows exist. |
| `比较 CN100355122C 和 CN100369314C 的工艺步骤差异` | Graph-for-RAG with per-patent process evidence. |
| `宁德时代新能源科技股份有限公司有哪些专利？` | Direct graph applicant listing. |
| `发明人李长东有哪些专利？` | Direct graph inventor listing. |
| `H01M 下有哪些专利？` | IPCPrefix listing. |
| `H01M10 下有哪些专利？` | IPC.code prefix strategy, not empty IPCPrefix lookup. |
| `H01M10/0525 有多少专利？` | Full IPC count. |
| `H01M 有多少专利？` | IPCPrefix count. |
| `H01M10 有多少专利？` | IPC.code prefix count, not `IPCPrefix.subclass = H01M10`. |
| `为什么喷雾干燥能提升磷酸铁锂性能？` | Graph-for-RAG when process/material/performance graph evidence exists; otherwise vector-only with clear skip reason. |
| `10.xxxx/xxxx 这篇文献是什么？` | Skip graph. |

Gateway-through-patent smoke requirements:

| Gateway route shape | Expected patent graph behavior |
| --- | --- |
| KB question routed as `kb_qa` | Patent graph preflight may run. |
| Uploaded PDF question routed as `pdf_qa` | Patent graph preflight must not run. |
| Uploaded table question routed as `tabular_qa` | Patent graph preflight must not run. |
| Gateway-level file/KB mixed `hybrid_qa` | Patent graph preflight must not run unless a later spec explicitly changes file-route semantics. |

Graph-for-RAG smoke requirements:

| Scenario | Expected proof |
| --- | --- |
| Graph returns candidate patent IDs and retrieval supports filtering | Logs/metadata show filter or bias was applied. |
| Graph returns candidate patent IDs but retrieval has no matching vector hits | Logs/metadata show fallback and final answer uses normal retrieval instead of failing. |
| Graph returns only facts and no candidates | Metadata marks Stage2 as `hint_only` or equivalent. |

### 19.3 Log Acceptance

For any graph-attempted request, logs must show:

- route start;
- slot summary;
- classification decision;
- selected strategy/template;
- guardrail result;
- row count;
- canonical evidence quality;
- direct/render or RAG payload decision;
- final graph mode.

This is mandatory because graph routing failures are otherwise hard to debug from the frontend.

### 19.4 Regression Acceptance

Existing behavior must remain intact:

- vector-only patent KB questions still return through staged RAG;
- file routes remain unaffected;
- gateway route response shape remains compatible;
- graph disabled/degraded mode still works;
- cache key changes do not break vector-only cache reuse;
- no secret values appear in committed docs, logs, or test fixtures.

## 20. Rollout Requirements

The implementation should be delivered in phases, but this spec does not define task boundaries.

Recommended rollout order:

1. document and test slot extraction;
2. introduce query template registry and strategy precedence;
3. fix evidence quality and direct-answer policy;
4. expand single-patent and entity templates;
5. improve graph-for-RAG payload and Stage2 constraint usage;
6. add logging and metadata parity;
7. add community/landscape templates;
8. run real Neo4j smoke tests and gateway-through-patent tests.

Each phase should be independently testable and should not require disabling the existing vector path.

## 21. Open Questions

1. What exact property names on `EmbodimentInsight`, `Atmosphere`, `PerformanceFact`, and `Measurement` should be treated as primary display text across all rows?
2. Should direct graph answers expose graph references as patent IDs only, or also include relationship/facet references?
3. Does the current patent retrieval service support hard filtering by patent ID, applicant, IPC, or source ID, or should Stage2 initially use graph candidates as ranking hints?
4. For `H01M10`-style IPC prefixes, should the product expectation be exact `IPC.code STARTS WITH` matching or a broader subclass normalization?
5. How broad can community/landscape graph queries be before they need pagination or async handling?
6. Should graph fact blocks be shown in response metadata for debugging only, or also visible to users in some traces?
7. Should graph query logging be controlled by the existing `PATENT_GRAPH_KB_QUERY_LOGGING` flag, always-on structured info logs, or both?

## 22. Definition of Done

Patent graph QA reaches this spec when:

1. precise patent graph questions return direct answers for all supported single-patent and entity templates;
2. hybrid questions inject graph candidates/facts into staged RAG and final answers use them coherently;
3. broad vector questions still use existing vector/RAG without graph interference;
4. gateway remains schema-agnostic;
5. logs clearly show every graph pipeline step;
6. direct-answer eligibility is evidence-based and not blocked by `stub=true` alone;
7. IPC routing matches actual graph grain;
8. stale paper labels are absent from patent graph query templates;
9. tests cover classifier, slots, templates, strategy, canonicalizer, RAG adapter, and KB integration;
10. real Neo4j smoke tests cover direct, graph-for-RAG, and skip paths;
11. no secrets are committed or printed in docs.
