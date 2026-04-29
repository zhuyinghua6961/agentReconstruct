# fastQA Legacy Graph Parity Spec

> Status: draft, pending review.
>
> Date: 2026-04-28
>
> Scope: restore the legacy user-visible graph QA capability inside the refactored `gateway` + `fastQA` architecture, without restoring the old monolithic `archive/oldCode` code shape.
>
> This is a product and architecture specification. It is not an implementation plan and does not authorize code changes by itself.

## 1. Executive Summary

The legacy graph experience in `archive/oldCode` was stronger than a simple Neo4j shortcut. It combined:

1. intent routing,
2. Neo4j exact graph querying,
3. graph result rendering,
4. graph-to-PDF enrichment,
5. graph-to-vector hybrid retrieval,
6. graph two-stage process expansion,
7. broad semantic/community analysis,
8. DOI-first local source lookup,
9. answer synthesis guardrails.

The current refactored fastQA already has important foundations:

- gateway owns file-vs-KB routing;
- fastQA owns KB execution;
- graph V2 already uses `precise / semantic / hybrid / community` route vocabulary;
- graph V2 already has `direct_answer / graph_for_rag / skip_graph` execution states;
- generation-driven RAG already accepts graph context at planning, retrieval, and synthesis stages.

The main gap is capability depth. Current fastQA graph V2 has the skeleton and several V1 templates, but it does not yet reproduce the legacy experience where graph evidence can drive exact answers, constrain retrieval, expand into process/recipe evidence, and enrich final answers with source text.

The target design is:

> Gateway decides whether a turn is file-based or KB-based. fastQA decides which KB evidence route to use. Within `kb_qa`, fastQA routes to one of four internal knowledge routes: `precise`, `semantic`, `hybrid`, or `community`. Each route may produce a direct graph answer, graph evidence for RAG, or skip graph safely.

This preserves the refactored architecture while rebuilding the older user experience.

## 2. Source Basis

### 2.1 Legacy Code Sources

The old behavior was reconstructed from these read-only files:

- `archive/oldCode/router_expert.py`
- `archive/oldCode/commander_agent.py`
- `archive/oldCode/main.py`
- `archive/oldCode/system_prompt.txt`
- `archive/oldCode/synthesis_prompt.txt`
- `archive/oldCode/synthesis_prompt_v3.txt`
- `archive/oldCode/hybrid_query_agent.py`
- `archive/oldCode/neo4j_two_stage_optimizer.py`
- `archive/oldCode/dual_retrieval_agent.py`
- `archive/oldCode/community_expert.py`
- `archive/oldCode/vectorize_communities.py`
- `archive/oldCode/microscopic_expert.py`
- `archive/oldCode/md_expert.py`
- `archive/oldCode/two_hop_rag.py`
- `archive/oldCode/enhanced_two_hop_rag.py`
- `archive/oldCode/generation_driven_rag.py`
- `archive/oldCode/integrated_agent.py`

### 2.2 Current Code Sources

The current fastQA and gateway behavior is represented by:

- `gateway/app/services/file_context_resolver.py`
- `gateway/app/services/route_decision.py`
- `gateway/app/routers/qa.py`
- `fastQA/app/routers/qa.py`
- `fastQA/app/modules/graph_kb/classifier_v2.py`
- `fastQA/app/modules/graph_kb/planner_v2.py`
- `fastQA/app/modules/graph_kb/query_strategy.py`
- `fastQA/app/modules/graph_kb/query_templates.py`
- `fastQA/app/modules/graph_kb/executor_v2.py`
- `fastQA/app/modules/graph_kb/guardrail.py`
- `fastQA/app/modules/graph_kb/schema_registry.py`
- `fastQA/app/modules/graph_kb/canonicalizer.py`
- `fastQA/app/modules/graph_kb/direct_renderer.py`
- `fastQA/app/modules/graph_kb/rag_adapter.py`
- `fastQA/app/modules/graph_kb/service.py`
- `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- `fastQA/app/modules/qa_kb/stages/planning.py`
- `fastQA/app/modules/qa_kb/stages/retrieval.py`
- `fastQA/app/modules/qa_kb/stages/pdf_loading.py`
- `fastQA/app/modules/qa_kb/stages/synthesis.py`
- `fastQA/app/modules/generation_pipeline/stage1_planning.py`
- `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
- `fastQA/app/modules/generation_pipeline/md_expansion.py`
- `fastQA/app/modules/generation_pipeline/pdf_pipeline.py`
- `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`

### 2.3 Existing Related Docs

This spec extends and supersedes the capability target described in:

- `docs/audit/知识图谱问答流程.md`
- `docs/graph/fastqa_four_route_graph_qa_spec.md`
- `docs/graph/fastqa_graph_routing_current_state.md`
- `docs/graph/fastqa_graph_query_patterns.md`
- `docs/graph/fastqa_neo4j_schema_observations.md`
- `docs/superpowers/specs/2026-04-20-patent-graph-fastqa-adaptation-implementation-spec.md`
- `docs/superpowers/plans/2026-04-28-fastqa-four-route-graph-qa.md`

## 3. Goals

### 3.1 Product Goals

1. Restore the legacy four-route KB experience:
   - `precise`: graph-first exact structured answers.
   - `semantic`: vector/RAG-first broad literature answers.
   - `hybrid`: graph-constrained or graph-seeded RAG.
   - `community`: graph community/network evidence plus RAG synthesis.

2. Preserve existing user-facing gateway behavior:
   - no file turn regressions;
   - no change to `pdf_qa`, `tabular_qa`, or gateway-level `hybrid_qa`;
   - no new public route names required for normal users.

3. Improve graph-driven answer quality:
   - use graph evidence when it is strong;
   - use vector/PDF/MD evidence when graph alone is insufficient;
   - avoid direct graph answers for weak, broad, or ambiguous graph evidence.

4. Rebuild legacy hybrid capability:
   - graph exact filtering first;
   - second-stage process/recipe expansion from graph when possible;
   - graph DOI candidates seed Chroma/PDF/MD retrieval.

5. Keep hallucination resistance:
   - no fabricated DOI;
   - no fabricated numeric values;
   - no direct answers from empty or low-confidence graph data;
   - preserve original graph value text when numeric parsing is used.

### 3.2 Architecture Goals

1. Keep graph classification inside fastQA `kb_qa`, not gateway.
2. Keep graph logic in `fastQA/app/modules/graph_kb/`.
3. Keep generation-driven RAG as the final synthesis authority for broad, hybrid, and community answers.
4. Keep graph-to-RAG integration through explicit payloads, not hidden Neo4j calls inside stage internals.
5. Prefer deterministic route-specific planners and allowlisted Cypher over free-form LLM Cypher.
6. Make each route testable independently from real Neo4j through mocked graph clients.

## 4. Non-Goals

1. Do not restore `archive/oldCode/main.py` or `MaterialScienceAgent.smart_query`.
2. Do not move graph-vs-vector routing into gateway.
3. Do not add graph behavior to file-only PDF/table routes.
4. Do not require free-form LLM-generated Cypher in the first production-ready rollout.
5. Do not require a Neo4j schema migration before restoring V1 parity.
6. Do not rebuild the old `community_vector_database` before community graph evidence can work.
7. Do not make graph direct answers the default for every graph hit.
8. Do not bypass the existing generation-driven RAG citation and PDF/MD evidence pipeline.
9. Do not commit Neo4j credentials or resource config secrets into docs or code.

### 4.1 Hard Scope Boundaries

For this specification, these boundaries are mandatory:

1. Only `kb_qa` internal knowledge routing is in scope.
2. Gateway-level `hybrid_qa` is out of scope for graph enhancement in this round.
3. A gateway `hybrid_qa` turn may include KB context, but this spec does not require fastQA to run the four-route graph router inside file-mixed turns.
4. Free-form LLM Cypher is out of scope unless a later spec explicitly enables it behind a feature flag and stronger validation.
5. The old `community_summaries` vector database is out of scope for V1.
6. Graph-only DOI are not citable by default.
7. File route behavior must remain unchanged.

## 5. Terminology

### 5.1 Gateway Route

Gateway route means the existing public routing category:

- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

Gateway `hybrid_qa` means file mixing, such as PDF+KB or table+KB. It does not mean graph+vector hybrid retrieval.

This specification never changes the meaning of gateway `hybrid_qa`. The word `hybrid` without the `gateway` qualifier refers only to the fastQA-internal knowledge route family.

### 5.2 Knowledge Route Family

Knowledge route family means the fastQA-internal KB route:

- `precise`
- `semantic`
- `hybrid`
- `community`

These route families should be visible in logs, diagnostics, tests, and debug metadata, but normal users should not need to choose them.

### 5.3 Graph Execution Mode

Graph execution mode means the tri-state output of graph V2:

- `direct_answer`: graph evidence is sufficient and safe to answer immediately.
- `graph_for_rag`: graph evidence is useful but final answer should be synthesized by RAG.
- `skip_graph`: graph is not useful or not safe for this turn.

### 5.4 Graph Evidence

Graph evidence means canonical structured facts extracted from Neo4j, including:

- DOI candidates;
- titles;
- material/sample names;
- recipe fields;
- process fields;
- raw material fields;
- numeric property rows;
- community IDs and representative items;
- constraints and entity hints for vector retrieval;
- stage 4 structured fact block for synthesis.

## 6. Legacy Capability Reconstruction

### 6.1 Legacy Router Layer

The legacy system had two router generations.

#### 6.1.1 `RouterExpert`

`archive/oldCode/router_expert.py` used an LLM JSON router and a keyword fallback. It chose among:

- `neo4j`
- `literature`
- `community`

Routing behavior:

- exact numeric/attribute/list questions went to Neo4j;
- literature search and broad paper discovery went to vector search;
- mechanism/relationship/trend questions went to community or broad semantic;
- fallback generally preferred literature search when unsure.

#### 6.1.2 `CommanderAgent`

`archive/oldCode/commander_agent.py` is the more important later router. It used rule-first logic:

1. hybrid detection first;
2. precise keyword plus Neo4j numeric attribute -> precise graph;
3. community keywords -> community/broad semantic;
4. semantic keywords -> semantic vector;
5. graph attribute plus listing/filter intent -> precise graph;
6. numeric attribute alone -> precise graph;
7. entity keyword -> precise graph;
8. default -> semantic vector.

It also had a broad-vs-precise judge. That judge controlled semantic retrieval size and answer synthesis mode.

### 6.2 Legacy Direct Graph Query

`archive/oldCode/main.py` performed direct graph queries through:

1. `_generate_cypher_query(question)`;
2. `_validate_cypher_query(cypher)`;
3. `_execute_cypher_query(cypher)`;
4. `_synthesize_answer(question, raw_data)`.

The legacy Cypher generation prompt understood a broad field-bucket schema:

- `tap_density`;
- `compaction_density`;
- `energy_density`;
- `power_density`;
- `discharge_capacity`;
- `conductivity`;
- `coulombic_efficiency`;
- `cycling_stability`;
- `particle_size`;
- `morphology`;
- `surface_area`;
- `doi`;
- `title`;
- `preparation_method`;
- `process`;
- `process_steps`;
- `raw_materials`;
- `calcination`;
- `sintering`;
- `annealing`;
- `drying`;
- `milling`;
- `temperature`;
- `time`;
- `atmosphere`;
- `pressure`;
- `carbon_source`;
- `carbon_content`;
- `dopant`;
- `doping_elements`;
- ratios and additives.

The old system was flexible because the LLM could generate many Cypher shapes, but it was risky. The refactored target should reproduce the capability through tested planners and allowlisted query templates before considering constrained LLM Cypher.

### 6.3 Legacy Graph Answer Synthesis

Legacy graph synthesis was not a plain row renderer. It:

- parsed DOI from explicit `doi` columns;
- parsed DOI from `material_name` suffixes;
- loaded local PDFs when DOI was available;
- injected PDF text into the graph answer prompt;
- applied material-type filtering after graph results;
- preserved actual values from graph rows;
- forbade fabricated names, values, and DOI.

This is one major reason the old graph answers felt richer than current direct graph answers.

### 6.4 Legacy DOI Behavior

`MaterialScienceAgent.smart_query()` prioritized DOI before routing:

- if the question contained a DOI, it routed directly to `query_pdf_directly`;
- PDF direct query loaded local PDF text and answered from that source;
- it did not primarily use Neo4j for DOI metadata in that path.

Current fastQA may answer DOI metadata from graph. That behavior is useful, but it does not fully match the old experience. Target behavior should combine both:

- use graph for DOI title/context expansion when safe;
- also seed PDF/MD loading for DOI-specific content questions;
- avoid sending DOI questions to file routes unless gateway explicitly resolved uploaded files.

### 6.5 Legacy Hybrid Query

`archive/oldCode/hybrid_query_agent.py` detected questions with:

- a precise filter signal, such as `>`, `<`, `大于`, `小于`, `最高`, `最低`;
- an analysis signal, such as `特点`, `工艺`, `趋势`, `比较`, `关系`.

It decomposed the question into:

- phase 1: exact graph filter;
- phase 2: semantic or process analysis.

`MaterialScienceAgent.hybrid_query()` then:

1. ran phase 1 with graph exact query;
2. if phase 2 was process-like, used `Neo4jTwoStageOptimizer`;
3. queried `name -> doi -> process/recipe` paths;
4. synthesized phase 1 and phase 2 graph data;
5. otherwise used LLM analysis over phase 1 rows.

### 6.6 Legacy Dual Retrieval

`archive/oldCode/dual_retrieval_agent.py` added graph+vector hybrid retrieval:

- Path 1: Neo4j exact filtering;
- Path 2: ChromaDB semantic retrieval;
- fusion: material rows, DOI overlap, vector papers, graph counts;
- synthesis: compare graph structured data with semantic literature summaries.

This is the closest old-code analog to the desired fastQA `hybrid` route.

### 6.7 Legacy Community Route

The old code had a `CommunityExpert` backed by Chroma collection `community_summaries`, built from community summary files. It classified community queries into technical analysis, material performance, process method, data quality, and comprehensive query types.

However, in the inspected legacy `MaterialScienceAgent`, the community expert was disabled and community route fell back to broad semantic search. This means:

- the old target experience included community summaries;
- the old snapshot did not fully run community summaries by default;
- current fastQA should not block community route restoration on a community vector DB.

V1 should use Neo4j `louvainCommunityId` community evidence, then hand off to RAG for synthesis.

### 6.8 Legacy Two-Hop Evidence Binding

The old two-hop RAG files show another important behavior:

1. first hop retrieves DOI from summary vector DB;
2. second hop searches PDF chunks or MD full text for evidence;
3. claims are mapped to evidence;
4. DOI citations are accepted, downgraded, or rejected based on evidence quality.

`generation_driven_rag.py` later incorporated this style:

- Stage 1: pre-answer and retrieval claims;
- Stage 2: targeted vector retrieval;
- Stage 2.5: optional MD full-text expansion;
- Stage 3: PDF or patent chunk loading;
- Stage 4: synthesis with DOI whitelist and post-verification.

Target fastQA graph parity should use the existing refactored generation pipeline rather than rebuilding this old code.

## 7. Current FastQA Capability Baseline

### 7.1 Gateway Baseline

Gateway already does the correct coarse routing job:

- resolves selected files;
- detects PDF/table/file references;
- normalizes route/source scope;
- forwards execution payloads to fastQA.

Gateway should not inspect Neo4j schema, Chroma collections, graph route families, or community IDs.

### 7.2 fastQA Graph V2 Baseline

Current graph V2 already has:

- `classifier_v2.py` route families;
- `planner_v2.py`;
- `query_strategy.py`;
- `query_templates.py`;
- `guardrail.py`;
- `executor_v2.py`;
- `canonicalizer.py`;
- `direct_renderer.py`;
- `rag_adapter.py`;
- `service.py`;
- graph route metadata;
- DOI quality filtering;
- value parsers;
- community labels module.

The current shape is correct. The parity work should deepen and harden the behavior, not replace the architecture.

### 7.3 Current Execution Contract

Current `route_graph_kb_v2()` returns:

- `GraphRoutingResult(mode="direct_answer", direct_result=...)`;
- `GraphRoutingResult(mode="graph_for_rag", rag_payload=...)`;
- `GraphRoutingResult(mode="skip_graph")`.

This must remain the contract.

### 7.4 Current RAG Injection Points

Graph evidence can already enter:

- Stage 1 planning through `stage1_context_block`;
- Stage 2 retrieval through DOI candidates, entity hints, and constraints;
- Stage 4 synthesis through `stage4_fact_block`;
- DOI fallback when vector retrieval misses but graph has DOI candidates.

This is enough to implement legacy-style graph-enhanced RAG without adding Neo4j calls to RAG internals.

## 8. Target User Experience

### 8.1 General Behavior

Users ask normal KB questions. The system internally chooses:

- graph direct answer;
- graph-enhanced RAG;
- pure RAG.

The answer should expose sources naturally:

- DOI when available;
- title when available;
- graph-derived values when relevant;
- PDF/MD evidence when RAG synthesis uses source text.

The system should not say "I used route X" unless debug metadata or logs are inspected.

### 8.2 Precise Route Experience

Use `precise` for bounded structured questions:

- DOI lookup;
- title lookup;
- material/sample listing;
- raw material listing;
- recipe listing;
- process/method listing;
- count questions;
- supported property value questions;
- supported ranking/filtering questions.

Examples:

- `10.1021/jp1005692 这篇文献是什么？`
- `列出使用蔗糖作为碳源的文献`
- `使用葡萄糖作为碳源的文献有哪些？`
- `LiFePO4 的制备方法有哪些？`
- `哪些文献涉及 solid-state synthesis？`
- `压实密度最高的 LFP 材料有哪些？`
- `放电容量超过 150 mAh/g 的材料有哪些？`
- `统计使用 sucrose 作为碳源的文献数量`

Expected behavior:

- Graph is attempted first.
- Direct answer is allowed for:
  - DOI lookup/expansion;
  - safe list/count queries;
  - supported direct process/recipe/material lists;
  - numeric ranking/filtering only when parser confidence is high and tests cover the field.
- If graph rows exist but direct rendering is unsafe, pass graph rows to RAG.
- If graph rows are empty, skip direct answer and continue RAG.

### 8.3 Semantic Route Experience

Use `semantic` for broad explanation or literature synthesis where graph constraints are weak:

- `为什么碳包覆会影响 LFP 倍率性能？`
- `LFP 的低温性能问题有哪些改善方法？`
- `总结 Fe2P 杂相的形成机制`
- `磷酸铁锂过充性能研究有哪些趋势？`

Expected behavior:

- Chroma/RAG remains primary.
- Graph is skipped when no useful graph slots exist.
- If useful graph terms are present, graph may contribute hints, but it must not dominate the answer.
- Broad synthesis should still use PDF/MD evidence and DOI whitelist logic.

### 8.4 Hybrid Route Experience

Use `hybrid` when a question combines graph-structured filtering with analysis:

- `放电容量超过150 mAh/g的LFP材料，它们的制备工艺有什么共同点？`
- `压实密度最高的前10个样品，它们的碳源有什么规律？`
- `使用葡萄糖作为碳源的文献中，哪些工艺参数影响容量？`
- `振实密度大于2.8的材料，制备工艺特点是什么？`

Expected behavior:

1. Extract graph filter or anchor.
2. Query Neo4j for candidate DOI/material rows.
3. If phase 2 asks about process/recipe/equipment/testing:
   - expand candidates through graph process/recipe/equipment/test paths;
   - build a graph fact block;
   - seed vector/PDF/MD retrieval with candidate DOI and terms.
4. Let generation-driven RAG synthesize final answer using graph facts plus source evidence.
5. If graph candidate set is too large, cap and rank candidates deterministically.
6. If graph candidate set is empty, fallback to semantic RAG and record fallback metadata.

### 8.5 Community Route Experience

Use `community` for relationship, network, cluster, mechanism association, and community questions:

- `LiFePO4 的关系网络和机制关联是什么？`
- `碳源、烧结工艺和容量之间有哪些社区关联？`
- `哪些文献和高容量 LFP 属于同一类研究主题？`
- `按社区总结 LFP 制备路线和性能关系`

Expected behavior:

- Use Neo4j community IDs when available.
- Retrieve representative DOI/title/material/method/property rows per community.
- Build deterministic community labels from representative terms.
- Use RAG to synthesize mechanism/network interpretation.
- Direct answer is allowed only for simple representative-list or profile questions.
- Do not require old `community_summaries` Chroma DB for V1.
- If community IDs are absent or too sparse in the live graph, the route must record `fallback_reason=community_id_unavailable` and continue semantic RAG. It must not fabricate community evidence from unrelated rows.

## 9. Target Architecture

### 9.1 High-Level Flow

```text
Gateway
  |
  | normalized ask payload
  v
fastQA /api/fast/ask or ask_stream
  |
  | if route != kb_qa -> existing file route behavior; no graph four-route requirement in this spec
  |
  | if route == kb_qa
  v
Conversation context builder
  |
  v
Graph V2 router
  |
  +-- direct_answer -> stream graph direct answer and finish
  |
  +-- graph_for_rag -> attach GraphRagPayload -> generation-driven RAG
  |
  +-- skip_graph -> generation-driven RAG
```

### 9.2 Graph V2 Internal Flow

```text
classify_graph_question_v2
  -> extract_graph_slots
  -> build_graph_query_plan_v2
  -> select query strategy
  -> build route-specific prepared queries
  -> guardrail validate Cypher
  -> execute prepared queries
  -> canonicalize rows
  -> evaluate direct-answer eligibility
  -> direct_renderer OR rag_adapter
```

### 9.3 No Gateway Graph Routing

Gateway must not add fields like:

- `knowledge_route_family`;
- `graph_route`;
- `graph_intent`;
- `neo4j_strategy`.

If diagnostics need to be exposed, fastQA can return metadata in the response stream or final payload.

### 9.4 Route Metadata Contract

Every graph V2 attempt should emit diagnostics:

- `graph_attempted`;
- `graph_enabled`;
- `graph_ready`;
- `graph_route_family`;
- `graph_execution_mode`;
- `classifier_confidence`;
- `matched_rule`;
- `strategy`;
- `intent`;
- `template_id`;
- `matched_path`;
- `attempted_paths`;
- `result_count`;
- `doi_candidate_count`;
- `direct_answer_eligible`;
- `fallback_reason`;
- `graph_rag_injected`;
- `latency_ms`.

Canonical response/debug keys should be:

| Canonical Key | Meaning | Compatibility Note |
| --- | --- | --- |
| `graph_route_family` | one of `precise`, `semantic`, `hybrid`, `community` | Existing internal `legacy_route_family` and `knowledge_route_family` may remain as aliases during migration. |
| `graph_execution_mode` | one of `direct_answer`, `graph_for_rag`, `skip_graph` | Existing `execution_mode` and `tri_state_mode` may remain as aliases during migration. |
| `graph_strategy` | selected query strategy | Existing `strategy` can mirror this value. |
| `graph_fallback_reason` | why graph skipped or downgraded | Existing `fallback_reason` can mirror this value. |
| `graph_rag_injected` | whether a `GraphRagPayload` was passed to RAG | Existing `rag_injection_enabled` can mirror this value. |

Metadata is for logs/tests/debugging. It should not be required by frontend for normal rendering.

## 10. Component Requirements

### 10.1 `classifier_v2.py`

Responsibilities:

- classify KB questions into route families;
- choose initial graph execution mode;
- extract route diagnostics;
- respect file-context ambiguity by downgrading direct answers.

Required behavior:

1. DOI detection must happen before semantic keywords.
2. Hybrid detection must happen before simple precise property routing.
3. Community signals must map to `community`, not pure semantic.
4. Semantic no-slot questions should `skip_graph`.
5. File-context-present turns must not produce direct graph answers.
6. Ambiguous follow-ups should downgrade to `graph_for_rag`.

Classifier should consider:

- DOI;
- title terms;
- material terms;
- raw material terms;
- recipe terms;
- process terms;
- property field;
- operator/threshold;
- ranking/limit;
- analysis signals;
- community signals;
- count/list signals.

### 10.2 `slots.py`

Slot extraction must support at least:

- DOI pattern normalization;
- material aliases:
  - `LFP`;
  - `LiFePO4`;
  - `磷酸铁锂`;
  - `lithium iron phosphate`;
- numeric properties:
  - discharge capacity;
  - compaction density;
  - tap density;
  - conductivity;
  - coulombic efficiency;
  - cycling stability;
  - particle size;
  - surface area;
  - energy density;
  - power density;
- recipe fields:
  - carbon source;
  - carbon content;
  - dopant;
  - doping elements;
  - additives;
  - ratios;
- process fields:
  - preparation method;
  - process;
  - calcination;
  - sintering;
  - drying;
  - milling;
  - atmosphere;
  - pressure;
  - time;
  - temperature;
  - process steps;
  - key process parameters;
- community terms:
  - network;
  - relation;
  - community;
  - cluster;
  - mechanism association;
  - relationship map.

Numeric slot extraction must preserve:

- operator;
- threshold;
- unit text if present;
- ranking direction;
- requested limit.

### 10.3 `schema_registry.py`

The schema registry must describe the populated field-bucket graph, not an ideal future ontology.

Required logical groups:

- document:
  - DOI;
  - title;
  - community ID;
- material/sample:
  - name;
  - material terms;
- raw materials;
- recipe;
- process;
- process parameter child buckets;
- equipment/testing;
- performance properties;
- community.

Each logical field spec should include:

- logical name;
- display name;
- source label;
- source relationship path;
- output columns;
- whether it can support direct answer;
- whether numeric parsing is supported;
- whether it can seed RAG;
- allowed relations;
- default limit.

#### 10.3.1 Current Schema Support Matrix

Implementation plans must treat this matrix as the initial support contract. A field may move to a stronger tier only after tests and, where needed, live schema observation confirm the path.

| Logical Area | Observed / Expected Current Path | V1 Support Tier | Direct Eligibility | Test Requirement |
| --- | --- | --- | --- | --- |
| DOI metadata | `(:doi {name})-[:title]->(:title)` | direct-capable | yes for lookup/profile | mocked rows plus one opt-in live smoke |
| DOI context | `(:doi)-[:name|raw_materials|process|recipe|testing|equipment]->(...)` | graph_for_rag plus limited direct profile | direct only for bounded profile | mocked explicit bucket rows |
| title search | `(:doi)-[:title]->(:title)` | graph_for_rag, direct list when exact/bounded | conditional | mocked title rows |
| material/sample name | `(:doi)-[:name]->(:name)` and property edges from `(:name)` | graph_for_rag, direct list when bounded | conditional | mocked DOI/material rows; verify direction |
| raw materials | `(:doi)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(:raw_materials)` | direct-capable list/count and graph_for_rag | yes for list/count | mocked nested bucket rows |
| preparation method | `(:doi)-[:process]->(:process)-[:preparation_method]->(:preparation_method)` | direct-capable list/profile and graph_for_rag | yes for bounded list | mocked process rows |
| process parameters | `(:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[:calcination|milling|sintering|drying]->(...)` | graph_for_rag; direct profile for DOI-specific bounded rows | conditional | mocked explicit relation rows |
| generic `temperature/time/pressure/atmosphere` | legacy prompt names; current graph may encode under operation-specific nodes | graph_for_rag only until path verified | no by default | parser/path tests before promotion |
| recipe carbon source | `(:doi)-[:recipe]->(:recipe)-[:carbon_source]->(...)` | direct-capable list/count and graph_for_rag | yes for list/count | mocked recipe rows |
| carbon content | `(:doi)-[:recipe]->(:recipe)-[:carbon_content]->(...)` if present | graph_for_rag until live path coverage verified | no by default | path existence test before direct |
| dopant / doping elements | `(:doi)-[:recipe]->(:recipe)-[:dopant|doping_elements]->(...)` if present | graph_for_rag until live path coverage verified | no by default | path existence and normalization tests |
| ratios/additives | `(:doi)-[:recipe]->(:recipe)-[:Li_Fe_ratio|Fe_P_ratio|other_ratios|additives]->(...)` if present | graph_for_rag | no by default | mocked rows; live smoke optional |
| discharge capacity | `(:name)-[:discharge_capacity]->(:discharge_capacity)` and possible nested child values | graph_for_rag; direct after parser promotion | no by default | parser fixtures for direct promotion |
| compaction density | `(:name)-[:compaction_density]->(:compaction_density)` | graph_for_rag; direct after parser promotion | no by default | parser/unit fixtures |
| tap density | `(:name)-[:tap_density]->(:tap_density)` if present | graph_for_rag; direct after parser promotion | no by default | parser/unit fixtures |
| conductivity | `(:name)-[:conductivity]->(:conductivity)` if present | graph_for_rag until path verified | no by default | path and parser fixtures |
| cycling stability | `(:name)-[:cycling_stability]->(:cycling_stability)` | graph_for_rag | no by default | mocked rows |
| coulombic efficiency | `(:name)-[:coulombic_efficiency]->(:coulombic_efficiency)` if present | graph_for_rag until path verified | no by default | path and parser fixtures |
| particle size | `(:name)-[:particle_size]->(:particle_size)` if present | graph_for_rag | no by default | mocked rows |
| morphology | legacy prompt field; current path must be live-verified | deferred unless observed | no | live schema check before template |
| surface area | legacy prompt field; current path must be live-verified | deferred unless observed | no | live schema check before template |
| energy density | legacy prompt field; current path must be live-verified | deferred unless observed | no | live schema check before template |
| power density | legacy prompt field; current path must be live-verified | deferred unless observed | no | live schema check before template |
| equipment | `(:doi)-[:equipment]->(:equipment)-[...]` if present | graph_for_rag, mainly DOI expansion | no by default | mocked explicit rows |
| testing | `(:doi)-[:testing]->(:testing)-[:testing]->(:testing)` if present | graph_for_rag, mainly DOI expansion | no by default | mocked nested rows |
| community ID | `louvainCommunityId` on populated nodes when available | graph_for_rag; direct representative profile when available | conditional | mocked community rows; live smoke conditional |

Support tier definitions:

- `direct-capable`: implementation must include a renderer path for safe bounded fixture rows.
- `graph_for_rag`: implementation must produce structured evidence but not direct user answers.
- `deferred`: implementation must not create production templates until schema evidence is confirmed.
- `conditional`: direct answer is allowed only for specific fixture-proven shapes.

### 10.4 `query_strategy.py`

Strategy selection should distinguish:

- `template`: legacy trusted template remains valid;
- `v1_template`: explicit route-specific query template;
- `parametric`: safe property or field query built from slots;
- `multi_stage`: phase 1 plus expansion paths;
- `community`: community-specific graph evidence query;
- `skip`: no graph attempt.

Free-form `llm_cypher` must remain disabled by default.

### 10.5 `query_templates.py`

Templates should cover:

#### DOI

- lookup by DOI;
- DOI context expansion;
- DOI process/recipe/equipment/testing expansion.

#### Document And Entity

- title search;
- material/sample search;
- raw material search;
- DOI by material term;
- DOI by title term.

#### Recipe

- carbon source list;
- carbon source count;
- carbon content list;
- dopant/doping elements list;
- ratio/additive list.

#### Process

- preparation method list;
- process list;
- key process parameter list;
- calcination parameter list;
- sintering/drying/milling parameter list.

#### Performance

- discharge capacity evidence;
- compaction density evidence;
- tap density evidence;
- conductivity evidence;
- cycling stability evidence;
- coulombic efficiency evidence;
- particle size/morphology/surface area evidence.

#### Hybrid

- candidate DOI/material rows from phase 1;
- process expansion by DOI;
- recipe expansion by DOI;
- performance expansion by DOI;
- title/material context by DOI.

#### Community

- community representative DOI/title/material rows;
- community representative process/recipe rows;
- community property distribution rows;
- community overlap rows for selected terms.

### 10.6 `guardrail.py`

Guardrail must reject:

- write clauses:
  - `CREATE`;
  - `MERGE`;
  - `DELETE`;
  - `DETACH`;
  - `SET`;
  - `REMOVE`;
  - `DROP`;
  - `LOAD CSV`;
  - `CALL dbms`;
- no-limit queries;
- unknown labels;
- unknown relationships;
- dynamic relationship execution for direct-answer paths;
- user-provided Cypher;
- multi-statement queries;
- comment-injection suspicious text.

Guardrail must allow:

- read-only `MATCH`;
- `OPTIONAL MATCH`;
- `WITH`;
- `WHERE`;
- `RETURN`;
- `ORDER BY`;
- `LIMIT`;
- application-owned parameter placeholders.

### 10.7 `executor_v2.py`

Executor must:

- run only prepared guarded queries;
- preserve attempted path diagnostics;
- return timeout/fallback reason without crashing KB answer;
- support multiple path attempts for a route;
- cap row counts;
- never stream partial graph rows directly to users.

### 10.8 `canonicalizer.py`

Canonicalization must:

- normalize DOI safely;
- drop suspicious DOI rows from direct answer;
- preserve original field value text;
- parse numeric values separately from original text;
- de-duplicate DOI/title/material rows;
- build `GraphEvidenceBundle`;
- identify direct-render DOI references;
- build `constraints_for_rag`;
- build `entity_hints`.

### 10.9 `value_parsers.py`

Numeric parsers must:

- parse values from messy graph strings;
- preserve units;
- reject incompatible units for direct ranking;
- support unit families rather than string equality only;
- expose confidence;
- expose original text.

Direct numeric ranking/filtering requires:

- parse success;
- compatible unit;
- confidence above threshold;
- enough rows;
- deterministic sort/filter;
- test coverage for that property field.

### 10.10 `direct_renderer.py`

Direct renderer must:

- render only direct-answer-eligible bundles;
- return `handled=False` if evidence is weak;
- never invent totals beyond returned rows;
- clearly label counts as returned/observed when capped;
- include DOI/title where available;
- preserve original values;
- avoid broad causal claims.

Allowed direct answer families:

- DOI metadata/context;
- simple list;
- simple count;
- simple field profile;
- tested numeric ranking/filter;
- simple community representative list/profile.

Disallowed direct answer families:

- mechanism explanation;
- trend analysis;
- broad comparison;
- community causal interpretation;
- graph evidence with no DOI/title support;
- numeric ranking with low parser confidence.

### 10.11 `rag_adapter.py`

RAG adapter must build:

- `stage1_context_block`;
- `stage2_doi_candidates`;
- `stage2_constraints`;
- `stage2_entity_hints`;
- `stage4_fact_block`;
- stable `cache_fingerprint`.

The graph context should be concise and structured:

- route family;
- graph intent;
- graph facts;
- DOI candidates;
- constraints;
- warnings/fallback if graph is partial.

Graph context must not force RAG to cite DOI unless source evidence is later loaded.

### 10.12 `qa_kb` And Generation Integration

Generation integration must:

- use graph context in Stage 1 planning;
- inject DOI/entity hints into Stage 2 retrieval query construction;
- use graph DOI candidates as fallback only when vector retrieval finds no DOI;
- pass graph fact block into Stage 4 synthesis as supplemental structured facts;
- preserve citation whitelist behavior;
- preserve PDF/MD evidence priority;
- mark graph-only facts separately from source-text facts when needed.

## 11. Route-Specific Detailed Requirements

### 11.1 Precise Route

#### 11.1.1 Classification

Classify as `precise` when:

- DOI is present and no broad analysis signal dominates;
- user asks "有哪些", "列出", "统计", "数量", "最高", "最低", "大于", "小于";
- graph property, recipe, process, raw material, material, or title slots are present;
- user asks for exact metadata or graph-structured facts.

Bounded process/list questions such as `LiFePO4 的制备方法有哪些？` should be graph-aware `precise` questions in the target architecture. The old `CommanderAgent` could sometimes bias `方法/如何` questions toward semantic retrieval; that old routing weakness should not be reproduced when the question is clearly asking for an enumerable graph-backed process list.

#### 11.1.2 Planning

Planner should choose:

- DOI template for DOI lookup/expansion;
- list/count templates for recipe/process/material;
- parametric templates for supported numeric properties;
- graph-for-RAG fallback for unsupported exact fields.

#### 11.1.3 Execution

Direct answer if:

- rows are non-empty;
- DOI quality passes;
- direct renderer supports the intent;
- numeric parsing confidence passes if numeric;
- row count is below direct answer cap or can be summarized safely.

Graph-for-RAG if:

- rows are useful but too complex;
- numeric parse confidence is insufficient;
- direct renderer declines.

Skip graph if:

- no route slots;
- graph unavailable;
- guardrail rejects all candidate paths.

#### 11.1.4 Examples And Expected Internal Outcomes

| Question | Route | Classifier Mode | Execution Fixtures |
| --- | --- | --- | --- |
| `10.1021/jp1005692 这篇文献是什么？` | precise | direct_answer | safe DOI metadata rows -> direct; malformed DOI rows -> direct renderer unhandled and RAG fallback with DOI quality metadata |
| `列出使用蔗糖作为碳源的文献` | precise | direct_answer | safe bounded recipe rows -> direct; unsafe/large rows -> graph_for_rag |
| `使用 glucose 的文献有多少篇？` | precise | direct_answer | exact count row -> direct; list-only capped rows -> returned-count wording |
| `压实密度最高的 LFP 材料有哪些？` | precise | graph_for_rag | direct promotion only after parser-backed ranking task |
| `放电容量超过150 mAh/g的材料有哪些？` | precise | graph_for_rag | direct promotion only after capacity parser-backed ranking task |

### 11.2 Semantic Route

#### 11.2.1 Classification

Classify as `semantic` when:

- broad explanation question;
- no useful graph slots;
- user asks why/how/trend/review without graph anchor;
- vector retrieval is clearly the right source.

#### 11.2.2 Planning

Planner should usually skip graph. If slots exist but are weak:

- produce graph hints only if cheap and safe;
- do not force graph evidence.

#### 11.2.3 Examples

| Question | Route | Classifier Mode | Execution Fixtures |
| --- | --- | --- | --- |
| `为什么碳包覆会影响倍率性能？` | semantic | skip_graph | pure RAG unless slot extractor finds a concrete graph anchor in a separate hybrid test. |
| `LFP 低温性能有哪些改善方向？` | semantic | skip_graph | RAG primary. |
| `总结 Fe2P 杂相形成机制` | semantic | skip_graph | graph hint promotion requires a separate fixture with explicit stored Fe2P graph terms. |

### 11.3 Hybrid Route

#### 11.3.1 Classification

Classify as `hybrid` when:

- question has exact filter/ranking plus analysis;
- question asks features/commonality/trend/relationship of filtered graph rows;
- DOI/material/process/property slots and analysis signals coexist.

#### 11.3.2 Planning

Hybrid planner should produce:

1. candidate query:
   - structured filter or anchor;
   - returns DOI/material/title/property rows.
2. expansion queries:
   - process;
   - recipe;
   - performance;
   - raw materials;
   - equipment/testing when useful.
3. RAG constraints:
   - DOI candidates;
   - terms from phase 2;
   - entity hints.

#### 11.3.3 Execution

Hybrid route should normally use `graph_for_rag`.

Direct answer is only acceptable when:

- the question is actually a simple precise list;
- no synthesis/analysis is requested.

#### 11.3.4 Examples

| Question | Route | Mode | Expected Graph Work |
| --- | --- | --- | --- |
| `放电容量超过150 mAh/g的LFP材料，它们的制备工艺有什么共同点？` | hybrid | graph_for_rag | capacity candidates -> process/recipe expansion -> RAG |
| `压实密度最高的前10个样品，它们的碳源有什么规律？` | hybrid | graph_for_rag | density ranking candidates -> recipe carbon source -> RAG |
| `使用葡萄糖作为碳源的文献中，哪些工艺参数影响容量？` | hybrid | graph_for_rag | carbon source candidates -> process/performance expansion -> RAG |

### 11.4 Community Route

#### 11.4.1 Classification

Classify as `community` when:

- user asks relationship/network/community/cluster;
- user asks association among materials/process/performance;
- user asks "共同研究主题", "关系网络", "社区", "机制关联".

#### 11.4.2 Planning

Community planner should:

- find relevant DOI/material/title rows by terms;
- collect community IDs;
- fetch representatives within communities;
- collect representative process/recipe/property rows;
- build community label candidates.

#### 11.4.3 Execution

Use `graph_for_rag` by default.

Direct answer only for:

- `这个 DOI 属于哪个社区？`
- `列出与 X 同社区的代表文献`
- `这个社区有哪些代表材料/方法？`

#### 11.4.4 Examples

| Question | Route | Mode | Expected Graph Work |
| --- | --- | --- | --- |
| `LiFePO4 的关系网络和机制关联是什么？` | community | graph_for_rag | term -> community representatives -> RAG |
| `按社区总结 LFP 制备路线和性能关系` | community | graph_for_rag | communities -> representative process/property evidence |
| `10.xxxx 这篇文献所在社区有哪些代表文献？` | community | direct_answer | safe DOI -> community -> representative rows; missing community ID -> semantic RAG fallback |

## 12. DOI And Source Evidence Policy

### 12.1 DOI Priority

DOI questions should be handled with a two-part policy:

1. graph metadata lookup is safe and useful;
2. content-specific DOI questions should seed PDF/MD evidence loading.

Examples:

- `10.xxxx 这篇文献是什么？`
  - graph direct answer can provide title/context.
- `10.xxxx 这篇文献的实验条件是什么？`
  - graph can provide context, but final answer should use PDF/MD if available.
- `10.xxxx 中的容量数据是多少？`
  - graph rows plus source text should be preferred.

### 12.2 DOI Quality

A DOI should be excluded from direct references if:

- it does not match strict DOI syntax;
- it appears truncated;
- it contains URL/UI corruption;
- it has no title and no useful facts;
- it appears to be generated from malformed material text.

### 12.3 Citation Policy

Graph DOI candidates are not automatically citable source evidence.

RAG synthesis can cite DOI only when:

- DOI is in retrieval/PDF/MD evidence whitelist, or
- the configured citation policy allows graph-only DOI citations and the answer clearly labels them as graph records.

Default should continue to rely on PDF/MD/vector evidence for citations.

Direct graph answers may display DOI as graph record metadata in the answer body, but graph-only DOI must not be promoted into the frontend citation/reference list unless a separate source-evidence whitelist path has admitted that DOI. In other words:

- graph direct answer metadata: allowed to show `DOI: 10.xxxx` as part of the graph record;
- frontend/source citation reference: allowed only after PDF/MD/vector evidence whitelist admits the DOI;
- graph-only DOI with no source text: must remain metadata, not source citation.

### 12.4 Graph DOI To PDF/MD Binding Scenarios

The graph-to-source evidence bridge must be tested with deterministic fixtures. These scenarios define required behavior.

| Scenario | Stage 2 Vector Result | Graph DOI Candidate | Local Source State | Required Behavior | Citation Whitelist |
| --- | --- | --- | --- | --- | --- |
| graph DOI + vector miss + PDF exists | no DOI from Chroma | valid DOI from graph payload | PDF chunks load in Stage 3 | use graph DOI as fallback source ID; load PDF; Stage 4 may cite DOI if PDF evidence supports the claim | DOI enters whitelist from loaded PDF chunks |
| graph DOI + vector miss + only MD exists | no DOI from Chroma | valid DOI from graph payload | PDF missing; MD chunks available in Stage 2.5/MD expansion | use graph DOI to attempt MD expansion; Stage 4 may cite DOI if MD evidence is injected as source text | DOI enters whitelist from MD evidence |
| graph DOI + no local source | no DOI from Chroma | valid DOI from graph payload | PDF and MD both missing | graph facts may appear as uncited structured context; final answer must not cite DOI as source-text evidence by default | DOI must not enter source whitelist |
| malformed graph DOI | no DOI from Chroma | DOI fails quality filter | any | drop DOI before source loading; record `graph_fallback_reason=malformed_doi`; continue normal RAG | DOI must not enter whitelist |
| graph DOI + vector DOI overlap | Chroma returns same DOI | same valid DOI from graph | source text available or unavailable | dedupe DOI; prefer vector/source evidence path; graph facts can enrich Stage 4 | whitelist follows source evidence availability |

Operational requirements:

1. Stage 2 may prefix graph DOI/entity hints into retrieval queries, but graph DOI fallback should only activate when vector retrieval has no usable DOI or when the question is DOI-specific.
2. Stage 2.5 MD expansion must accept graph-seeded DOI candidates through the same structure used for vector-derived DOI candidates.
3. Stage 3 PDF loading must record whether each source ID came from vector retrieval, graph fallback, or explicit DOI question.
4. Stage 4 must distinguish `graph_facts` from `source_evidence`. Concrete claims with DOI citations must be backed by source evidence unless graph-only citation is explicitly enabled.
5. If no source text exists, answers may say that the graph contains structured metadata for the DOI, but must not imply that PDF/MD evidence was consulted.

## 13. Numeric Property Policy

### 13.1 Supported Numeric Fields

V1 should prioritize:

- discharge capacity;
- compaction density;
- tap density;
- conductivity;
- cycling stability;
- coulombic efficiency.

Additional fields can be graph-for-RAG only until parsers are tested.

### 13.2 Numeric Parsing Requirements

For every parsed value:

- retain original text;
- extract numeric value;
- extract unit text;
- infer unit family;
- compute confidence;
- record parse warnings.

### 13.3 Direct Ranking Requirements

Direct numeric ranking requires:

- enough parsed rows;
- compatible unit family;
- confidence threshold;
- deterministic handling of ties;
- explicit cap and returned-count note;
- tests for representative messy values.

If these conditions are not met, the route must become `graph_for_rag`.

## 14. Count Query Policy

Count direct answers must distinguish exact graph counts from capped returned counts.

Rules:

1. A query that uses `count(DISTINCT ...) AS count` may render an exact graph count if the Cypher counts before applying display row caps.
2. A query that returns rows with `LIMIT` may only say "returned N rows" or "observed N returned rows", not "there are N total".
3. If both count and sample rows are needed, use separate count and sample query paths or an explicit query that returns both exact count and capped examples.
4. Count answers must include the counted entity type, such as DOI, material/sample, or graph records.
5. Empty count result means zero only if the count query itself executed successfully and returned `0`; empty row result from a list query is not proof of absence.

## 15. Graph-To-RAG Evidence Contract

### 15.1 Stage 1 Context

The Stage 1 context block should include:

- route family;
- graph intent;
- concise graph findings;
- DOI candidate count;
- key entities;
- constraints.

It should not include long raw row dumps.

### 15.2 Stage 2 Retrieval

Stage 2 should use graph payload to:

- boost DOI candidates when available;
- prefix entity hints into retrieval queries;
- include structured constraints in query text;
- use graph DOI fallback when vector retrieval returns no DOI.

It should not:

- filter vector retrieval so aggressively that relevant papers outside graph are impossible to find;
- cite graph DOI without source text verification.

### 15.3 Stage 4 Synthesis

Stage 4 should receive:

- graph structured fact block;
- source evidence;
- DOI whitelist;
- warnings for graph-only facts.

Prompting should distinguish:

- graph facts;
- vector summary evidence;
- PDF/MD text evidence.

The answer should prefer source text for concrete claims and use graph facts as structured context.

## 16. Error Handling And Fallbacks

### 16.1 Graph Unavailable

If Neo4j is not ready:

- log `graph_ready=false`;
- return `skip_graph`;
- continue normal RAG;
- do not fail the user question.

### 16.2 Guardrail Rejection

If every graph query is rejected:

- log guardrail issues;
- return `skip_graph` when no safe graph evidence was produced;
- return `graph_for_rag` only when an earlier safe query in the same route already produced canonicalized evidence and a later optional expansion was rejected;
- continue RAG.

### 16.3 Empty Graph Results

If graph returns no rows:

- direct answer is forbidden;
- hybrid/community should continue RAG with a note in metadata;
- answer should not claim graph proved absence unless a count query explicitly supports it.

### 16.4 Partial Graph Results

If graph rows are partial:

- include available facts;
- do not fill missing fields;
- RAG can supplement from source evidence;
- metadata should include partial-data warning.

### 16.5 Vector Retrieval Empty But Graph Has DOI

If vector retrieval returns no DOI but graph has DOI candidates:

- use graph DOI candidates as fallback seeds for PDF/MD loading if configured;
- mark fallback source as graph-seeded;
- preserve citation whitelist logic.

### 16.6 Community ID Unavailable

If a community route question is classified correctly but no usable community ID is available:

- record `graph_route_family=community`;
- record `graph_execution_mode=skip_graph` when no other safe graph evidence exists;
- record `graph_execution_mode=graph_for_rag` only if non-community graph evidence exists and can help RAG without pretending to be community evidence;
- record `graph_fallback_reason=community_id_unavailable`;
- continue semantic RAG;
- do not fabricate community labels or community representative rows.

## 17. Observability

### 17.1 Logs

Every KB turn should log:

- trace ID;
- gateway route;
- graph enabled/disabled;
- graph route family;
- graph execution mode;
- strategy/path;
- result counts;
- fallback reason;
- graph-to-RAG injection status;
- final KB path.

### 17.2 Response Metadata

Final payload metadata should be sufficient for tests and debug:

- `graph_route_family`;
- `graph_execution_mode`;
- `graph_strategy`;
- `graph_result_count`;
- `graph_rag_injected`;
- `graph_fallback_reason`.

This metadata must be additive and should not break frontend rendering.

### 17.3 Test Fixtures

Tests should not depend on the live graph for most behavior. Use mocked executor rows for:

- direct rendering;
- graph-for-RAG payload;
- fallback behavior;
- empty result behavior;
- guardrail rejection.

Live graph smoke tests can remain separate and opt-in.

## 18. Rollout Plan At Spec Level

### 18.1 Phase A: Stabilize Route Semantics

Deliver:

- expanded classifier coverage;
- route examples in tests;
- metadata consistency;
- no graph execution behavior regressions.

Acceptance:

- all example questions classify into expected route family and execution mode;
- gateway tests unchanged.

### 18.2 Phase B: Expand Precise Graph Coverage

Deliver:

- route-specific templates for DOI/material/title/raw material/recipe/process;
- direct renderer for safe list/count/profile;
- DOI quality filtering;
- graph-for-RAG fallback for unsafe direct answers.

Acceptance:

- DOI questions work;
- carbon source list/count works;
- process method list works;
- direct answer never appears for empty rows.

### 18.3 Phase C: Numeric Evidence And Gated Direct Ranking

Deliver:

- value parser coverage;
- numeric graph evidence payload;
- direct ranking only for parser-backed tested fields.

Acceptance:

- capacity/density questions produce useful graph evidence;
- unsupported direct numeric ranking falls back to graph-for-RAG;
- original values are preserved.

### 18.4 Phase D: Hybrid Two-Stage Graph Expansion

Deliver:

- phase 1 candidate graph query;
- DOI/material candidate cap;
- process/recipe/performance expansion by DOI;
- graph-seeded retrieval terms;
- graph fact block synthesis support.

Acceptance:

- filtered-analysis examples become graph-for-RAG;
- graph DOI candidates appear in retrieval metadata;
- no direct graph answer for analysis questions.

### 18.5 Phase E: Community Graph Evidence

Deliver:

- community term lookup;
- representative DOI/title/material rows;
- deterministic community labels;
- graph-for-RAG community fact blocks.

Acceptance:

- mocked unit tests: community route no longer unconditional skip when fixture rows contain usable community IDs;
- mocked unit tests: representative list/profile questions render direct answer when fixture rows are safe;
- mocked unit tests: missing community ID records `graph_fallback_reason=community_id_unavailable` and continues semantic RAG;
- live smoke tests: community evidence executes only if the live graph has usable community IDs;
- mechanism/network questions use RAG synthesis.

### 18.6 Phase F: DOI Source Enrichment Parity

Deliver:

- DOI questions can use graph metadata plus PDF/MD source loading;
- graph DOI fallback integrates with existing PDF/MD pipeline;
- citation policy remains strict.

Acceptance:

- DOI metadata question answers from graph;
- DOI content question gets source evidence when available;
- fabricated DOI are stripped or prevented.

## 19. Acceptance Test Matrix

### 19.0 User Acceptance Scenarios

These are user-visible smoke scenarios for manual or live verification. They complement unit tests and should be checked after implementation phases that touch the relevant route.

| Scenario | Example Question | Expected User-Visible Behavior | Internal Route Expectation |
| --- | --- | --- | --- |
| DOI metadata | `10.1021/jp1005692 这篇文献是什么？` | Returns graph-backed title/context without invoking file route. | `kb_qa` -> `precise` -> direct graph answer if DOI row safe. |
| DOI content with source text | `10.1021/jp1005692 这篇文献的实验条件是什么？` | Uses graph DOI as anchor and answers from PDF/MD evidence if available; citations only when source text is loaded. | `kb_qa` -> `hybrid` -> graph_for_rag with graph-seeded DOI. |
| Carbon source list | `列出使用蔗糖作为碳源的文献` | Gives bounded DOI/title list or graph-seeded RAG if result is too large. | `kb_qa` -> `precise`. |
| Carbon source count | `使用 glucose 的文献有多少篇？` | Reports exact graph count only from count query; otherwise uses returned-count wording. | `kb_qa` -> `precise` -> direct count. |
| Numeric hybrid | `压实密度最高的前10个样品，它们的碳源有什么规律？` | Does not invent ranking; uses graph numeric candidates and RAG synthesis with source evidence. | `kb_qa` -> `hybrid` -> graph_for_rag. |
| Community fallback | `LFP 的关系网络和机制关联是什么？` | Uses community graph evidence when available; otherwise falls back to semantic RAG without fake community claims. | `kb_qa` -> `community`; fallback reason if no community ID. |
| Semantic no-slot | `LFP 低温性能有哪些改善方向？` | Preserves current vector/RAG behavior. | `kb_qa` -> `semantic` -> skip_graph. |
| File route no regression | selected PDF/table question | Existing PDF/table/file-hybrid behavior is unchanged. | gateway route is not `kb_qa`; no graph four-route requirement. |

### 19.1 Classifier Tests

Classifier tests must assert one route and one initial mode. Execution downgrade behavior belongs in executor/renderer/end-to-end tests, not classifier tests.

| Question | Expected Route | Expected Initial Mode |
| --- | --- | --- |
| `10.1021/jp1005692 这篇文献是什么？` | precise | direct_answer |
| `10.1021/jp1005692 这篇文献的实验条件是什么？` | hybrid | graph_for_rag |
| `列出使用蔗糖作为碳源的文献` | precise | direct_answer |
| `使用 glucose 的文献有多少篇？` | precise | direct_answer |
| `LiFePO4 的制备方法有哪些？` | precise | graph_for_rag |
| `放电容量超过150 mAh/g的LFP有哪些特点？` | hybrid | graph_for_rag |
| `压实密度最高的前10个样品，它们的碳源有什么规律？` | hybrid | graph_for_rag |
| `为什么碳包覆会影响倍率性能？` | semantic | skip_graph |
| `使用葡萄糖作为碳源的文献中，哪些工艺参数影响容量？` | hybrid | graph_for_rag |
| `LFP 的关系网络和机制关联是什么？` | community | graph_for_rag |
| `按社区总结 LFP 制备路线和性能关系` | community | graph_for_rag |

### 19.1.1 Execution Fixture Matrix

Execution tests must split result-dependent behavior into separate fixtures.

| Fixture | Route | Initial Mode | Required Final Graph Result |
| --- | --- | --- | --- |
| DOI lookup rows with valid DOI/title | precise | direct_answer | `direct_answer` handled |
| DOI lookup rows with malformed DOI | precise | direct_answer | no direct answer; fallback metadata records DOI quality failure |
| carbon source list rows under cap | precise | direct_answer | `direct_answer` handled |
| carbon source rows above direct cap | precise | direct_answer | `graph_for_rag` with capped facts |
| exact count row | precise | direct_answer | direct exact count wording |
| list rows only with `LIMIT` | precise | direct_answer | returned-count wording, not total count |
| numeric rows with parser confidence below threshold | precise | graph_for_rag | `graph_for_rag`; original values preserved |
| hybrid candidate rows plus process expansion rows | hybrid | graph_for_rag | graph payload includes DOI candidates and process facts |
| hybrid candidate query empty | hybrid | graph_for_rag | continue RAG with `graph_fallback_reason=empty_graph_results` |
| community rows with usable community IDs | community | graph_for_rag | graph payload includes representatives and labels |
| community rows without community IDs | community | graph_for_rag | semantic RAG fallback with `community_id_unavailable` |
| graph unavailable | any | any | `skip_graph`; normal RAG continues |

### 19.2 Planner Tests

Planner tests should verify:

- DOI questions choose DOI template;
- carbon source questions choose recipe template;
- process method questions choose process template;
- numeric questions choose parametric evidence path;
- hybrid questions produce candidate plus expansion plan;
- community questions choose community plan;
- semantic no-slot questions produce no plan.

### 19.3 Guardrail Tests

Guardrail tests should verify:

- read-only allowlisted templates pass;
- write clauses fail;
- no-limit queries fail;
- unknown labels fail;
- unknown relationships fail;
- multi-statement queries fail;
- dynamic relationship direct-answer queries fail.

### 19.4 Canonicalizer Tests

Canonicalizer tests should verify:

- DOI normalization;
- suspicious DOI filtering;
- duplicate row collapsing;
- numeric original-value preservation;
- entity hints extraction;
- graph constraints extraction;
- direct-answer eligibility flags.

### 19.5 Direct Renderer Tests

Renderer tests should verify:

- DOI lookup rendering;
- recipe list rendering;
- process list rendering;
- count rendering;
- numeric rendering only when parser confidence passes;
- empty rows return unhandled;
- analysis/community interpretation returns unhandled.

### 19.6 RAG Adapter Tests

RAG adapter tests should verify:

- stage 1 context contains concise graph facts;
- stage 2 DOI candidates are stable and deduped;
- constraints are serializable;
- stage 4 fact block distinguishes graph facts;
- cache fingerprint changes with evidence.

### 19.7 End-To-End FastQA Tests

Mocked end-to-end tests should verify:

- `direct_answer` stops before RAG;
- `graph_for_rag` continues into generation pipeline;
- `skip_graph` behaves exactly like current RAG;
- graph unavailable does not fail answer;
- file routes bypass graph direct answers;
- metadata is emitted consistently for sync and stream paths.

### 19.8 Live Smoke Tests

Opt-in live tests should verify against actual services:

- gateway login/session works;
- `kb_qa` DOI question routes to graph;
- precise carbon/process question uses graph metadata;
- hybrid question uses graph-for-RAG;
- semantic question still uses vector;
- community question executes graph evidence;
- logs match route expectations.

Live smoke tests may require elevated execution and should not be required for unit CI.

## 20. Backward Compatibility

### 20.1 Gateway Compatibility

No changes should be required to gateway public route names.

The following must remain true:

- no file intent -> `kb_qa`;
- selected PDF -> `pdf_qa`;
- selected table -> `tabular_qa`;
- file+KB -> gateway `hybrid_qa`;
- thinking file route can still route to fastQA as currently designed;
- patent mode behavior remains unchanged unless separately planned.

### 20.2 fastQA Compatibility

The following must remain true:

- `qa_kb_service` remains the main KB generation path;
- graph V2 disabled means current RAG behavior;
- graph errors fallback to RAG;
- cache behavior remains stable unless graph payload fingerprint changes intentionally;
- response schema remains additive.

### 20.3 Existing Graph Templates

Existing trusted templates should be preserved where tests prove they match the current graph. Templates with wrong path direction or weak assumptions should be replaced, not patched blindly.

## 21. Security And Safety

1. Never expose Neo4j credentials in logs, docs, response metadata, or errors.
2. Never execute user-provided Cypher.
3. Keep graph queries read-only.
4. Keep query parameters separated from Cypher strings.
5. Enforce row limits.
6. Enforce timeouts.
7. Treat graph evidence as untrusted until canonicalized.
8. Treat graph DOI as candidates, not proof of source evidence.
9. Preserve existing auth and gateway forwarding contracts.

## 22. Risks

### 22.1 Over-Routing To Graph

Risk: broad semantic questions get forced into graph evidence and produce shallow answers.

Mitigation:

- semantic no-slot -> `skip_graph`;
- graph direct answer only when renderer supports intent;
- hybrid/community use RAG synthesis.

### 22.2 Numeric Misinterpretation

Risk: graph value strings contain mixed units or noisy text.

Mitigation:

- parse in application code;
- preserve original value;
- require parser confidence;
- fallback to RAG when uncertain.

### 22.3 Community Overclaiming

Risk: community IDs are used to make causal claims that graph does not prove.

Mitigation:

- community direct answer limited to representative lists/profiles;
- mechanism interpretation always RAG-synthesized;
- prompt labels community evidence as graph association.

### 22.4 DOI Hallucination

Risk: graph/RAG synthesis introduces DOI not in evidence.

Mitigation:

- DOI quality filtering;
- existing citation whitelist;
- graph DOI candidates not automatically citable;
- post-synthesis DOI validation remains enabled.

### 22.5 Regression In Existing Vector QA

Risk: graph preflight delays or distorts normal RAG.

Mitigation:

- strict timeouts;
- skip graph for semantic no-slot;
- graph_for_rag payload concise;
- feature flags and metadata.

## 23. Open Decisions

### 23.1 Free-Form LLM Cypher

Recommendation: defer.

Reason:

- old LLM Cypher gave breadth but increased risk;
- current architecture has better route-specific planner seams;
- free-form Cypher needs a stronger validator and live schema feedback loop.

### 23.2 Graph-Only DOI Citation

Recommendation: do not enable by default.

Reason:

- old system enriched graph answers with PDF when possible;
- current RAG citation pipeline is stricter;
- graph DOI should seed evidence loading, not bypass evidence.

### 23.3 Community Vector DB

Recommendation: defer.

Reason:

- old snapshot had community expert disabled;
- Neo4j community IDs can provide V1 community evidence;
- normal vector/PDF/MD pipeline can synthesize final explanations.

### 23.4 Direct Numeric Ranking Scope

Recommendation: start with graph-for-RAG for most numeric questions, then promote fields to direct answer one by one after parser tests.

Reason:

- value strings are messy;
- wrong numeric ranking is worse than RAG synthesis with original evidence.

## 24. Definition Of Done

The graph parity work is done when:

1. all four route families are classified and observable;
2. `precise` route can safely direct-answer DOI/list/count/profile cases;
3. `semantic` route preserves current vector/RAG behavior;
4. `hybrid` route uses graph candidate and expansion evidence for RAG;
5. `community` route uses Neo4j community evidence instead of unconditional skip;
6. graph-to-RAG payload influences Stage 1, Stage 2, and Stage 4;
7. graph direct answers are gated by canonicalizer and renderer;
8. graph failures never fail normal KB QA;
9. gateway file route behavior is unchanged;
10. unit tests cover classifier, planner, guardrail, canonicalizer, renderer, RAG adapter, and fastQA route integration;
11. live smoke tests demonstrate route behavior on real services.

## 25. Implementation Planning Notes

The implementation plan should be split into tasks with this rough order:

1. classifier and slot coverage;
2. schema registry and route-specific query templates;
3. guardrail and executor hardening;
4. canonicalizer and DOI/numeric quality;
5. direct renderer expansion;
6. RAG adapter enrichment;
7. hybrid two-stage graph expansion;
8. community graph evidence;
9. DOI source enrichment;
10. end-to-end tests and live smoke tests.

The plan should not ask one worker to modify all graph files at once. Each task should own a narrow write set and include failing tests first.
