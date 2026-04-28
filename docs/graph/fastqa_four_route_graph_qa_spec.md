# fastQA Four-Route Graph QA Spec

> Status: draft for review. This is a product and architecture specification, not an implementation plan.
>
> Scope: restore the user-visible four-route knowledge graph QA experience inside the refactored fastQA/gateway architecture, without reverting to the legacy code shape.
>
> Non-goal: do not expose Neo4j credentials, do not change production code from this document alone, and do not require the old `MaterialScienceAgent.smart_query` implementation to be restored.

## 1. Purpose

The current fastQA system has been refactored away from the old monolithic agent flow. The old flow described a useful user experience:

- precise graph answers from Neo4j,
- semantic answers from vector/RAG,
- hybrid graph-constrained RAG answers,
- community/network answers from graph community structure.

The goal of this spec is to define how to recreate that experience in the current architecture.

The core design principle is:

> Gateway decides whether the turn is file-based or KB-based. fastQA decides which KB evidence route to use: `precise`, `semantic`, `hybrid`, or `community`.

This preserves the refactored service boundaries while restoring the legacy route semantics at the user level.

## 2. Source Documents And Observations

This spec is based on local code and read-only Neo4j/Chroma inspection.

Relevant docs:

- `docs/audit/知识图谱问答流程.md`
- `docs/graph/fastqa_neo4j_schema_observations.md`
- `docs/graph/fastqa_graph_query_patterns.md`
- `docs/graph/fastqa_graph_routing_current_state.md`

Relevant current code areas:

- Gateway file/KB routing:
  - `gateway/app/services/file_context_resolver.py`
  - `gateway/app/services/route_decision.py`
  - `gateway/app/routers/qa.py`
- fastQA route dispatch:
  - `fastQA/app/routers/qa.py`
- fastQA graph KB:
  - `fastQA/app/modules/graph_kb/classifier_v2.py`
  - `fastQA/app/modules/graph_kb/planner_v2.py`
  - `fastQA/app/modules/graph_kb/query_strategy.py`
  - `fastQA/app/modules/graph_kb/executor_v2.py`
  - `fastQA/app/modules/graph_kb/guardrail.py`
  - `fastQA/app/modules/graph_kb/schema_registry.py`
  - `fastQA/app/modules/graph_kb/canonicalizer.py`
  - `fastQA/app/modules/graph_kb/rag_adapter.py`
  - `fastQA/app/modules/graph_kb/direct_renderer.py`
  - `fastQA/app/modules/graph_kb/service.py`
- fastQA generation-driven RAG:
  - `fastQA/app/modules/qa_kb/service.py`
  - `fastQA/app/modules/qa_kb/orchestrators/generation.py`
  - `fastQA/app/modules/qa_kb/stages/planning.py`
  - `fastQA/app/modules/qa_kb/stages/retrieval.py`
  - `fastQA/app/modules/qa_kb/stages/synthesis.py`
  - `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
  - `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`

## 3. Current Reality Summary

### 3.1 Gateway

Gateway currently routes among:

- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

In gateway, `hybrid_qa` means file mixing such as PDF+KB, table+KB, or PDF+table. It does not mean graph+vector hybrid retrieval.

Gateway should remain the owner of:

- uploaded file reference resolution,
- selected file scope,
- PDF/table route choice,
- mixed file+KB intent,
- conversation file context,
- forwarding normalized payloads to fastQA.

Gateway should not become the owner of graph route classification. Adding graph route classification to gateway would couple gateway to fastQA's Neo4j schema and Chroma internals, and it would blur the current service boundary.

### 3.2 fastQA KB Route

For `kb_qa`, fastQA currently:

1. Builds conversation context.
2. Attempts Graph KB V2 when enabled.
3. Returns direct graph answer if Graph KB V2 returns `direct_answer`.
4. Attaches `GraphRagPayload` if Graph KB V2 returns `graph_for_rag`.
5. Falls through to generation-driven RAG.

This already gives the desired internal execution contract:

- direct graph answer,
- graph-enhanced RAG,
- pure RAG.

The gap is that Graph KB V2's route planning and schema coverage are not yet rich enough.

### 3.3 Graph V2

Graph V2 currently has route family names:

- `precise`
- `semantic`
- `hybrid`
- `community`

But those names are not yet complete execution routes.

Current mapping:

| Route Family | Current Execution |
| --- | --- |
| `precise` | direct only for legacy templates; otherwise graph-for-RAG |
| `semantic` | graph-for-RAG if graph signals exist, otherwise skip |
| `hybrid` | graph-for-RAG |
| `community` | skip graph |

This means the current code has the vocabulary but not the full behavior.

Important implementation caution:

- Current generic candidate queries and legacy templates are scaffolding, not a reliable production baseline for all four routes.
- Some current query fragments do not match the observed graph direction. For example, observed material/sample linkage is primarily `(:doi)-[:name]->(:name)`, while current generic search code contains a sample-name fragment shaped like `OPTIONAL MATCH (s:name)-[:name]->(d)`.
- Implementation planning must treat the key graph query baseline as a rewrite/replace effort for route-specific paths, not a low-risk extension of every existing generic query.
- Existing template behavior can be preserved where tests prove it still matches the graph, especially DOI lookup/expansion. Other generic candidate queries must be revalidated against live schema observations before promotion to direct-answer eligibility.

### 3.4 Neo4j

The populated Neo4j graph is a field-bucket schema:

```cypher
(:doi)-[:title]->(:title)
(:doi)-[:name]->(:name)
(:doi)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(:raw_materials)
(:doi)-[:process]->(:process)-[:preparation_method]->(:preparation_method)
(:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[:calcination|milling|sintering|drying]->(...)
(:doi)-[:recipe]->(:recipe)-[:carbon_source|carbon_content|dopant|doping_elements|...]->(...)
(:doi)-[:testing]->(:testing)-[:testing]->(:testing)
(:doi)-[:equipment]->(:equipment)-[:model|parameters|structure|instrument]->(...)
(:name)-[:discharge_capacity]->(:discharge_capacity)
(:discharge_capacity)-[:discharge_capacity]->(:discharge_capacity)
(:name)-[:cycling_stability]->(:cycling_stability)
(:name)-[:compaction_density]->(:compaction_density)
```

The graph has enough coverage for a first four-route version, especially:

- DOI lookup,
- DOI expansion,
- material/sample names,
- raw materials,
- recipe fields,
- process methods and process parameters,
- equipment/test fields,
- performance fields,
- `louvainCommunityId` community grouping.

There are no observed Neo4j full-text or Neo4j vector indexes. Neo4j should be used for structured graph evidence, not semantic retrieval.

### 3.5 Chroma

Observed Chroma stores:

| Store | Collection | Embeddings | Metadata |
| --- | --- | ---: | --- |
| Summary/vector store | `lfp_papers` | about 34,726 | `doi`, `title`, `source_file`, `chunk_id`, `data_quality`, `chroma:document` |
| MD/full-text store | `md_papers` | about 686,266 | `document_name`, `filename`, `chunk_id`, `is_full_document`, `chroma:document` |

Semantic retrieval should remain Chroma/RAG based.

Graph-to-RAG handoff should pass:

- DOI candidates,
- entity hints,
- structured fact blocks,
- optional constraints,
- diagnostics and route metadata.

## 4. User-Visible Route Semantics

The final system should expose the following user-visible behavior through normal `kb_qa` answers.

The user should not need to know route names. Route names are for diagnostics, telemetry, tests, and internal behavior.

### 4.0 Version Scope

The first implementation version must restore the four-route experience conservatively.

V1 in scope:

| Area | V1 Requirement |
| --- | --- |
| Route classifier | Deterministically classify `precise`, `semantic`, `hybrid`, and `community` inside fastQA `kb_qa`. |
| Execution contract | Preserve `direct_answer`, `graph_for_rag`, and `skip_graph` tri-state. |
| Precise direct answers | Support high-confidence direct answers for DOI lookup/expansion and simple structured listing/count routes. |
| Precise graph-for-RAG | Support graph evidence for recipe/process/material/performance questions when direct rendering is not safe. |
| Numeric questions | Extract numeric slots and produce graph evidence; direct numeric ranking is limited to parser-backed fields only and is not required for V1 acceptance. |
| Hybrid | Use graph evidence to seed/enrich generation-driven RAG. |
| Semantic | Keep Chroma/RAG as primary path; skip graph unless useful graph slots are extracted. |
| Community | Community route must no longer unconditional `skip_graph`; V1 must produce community graph evidence for RAG and may render direct representative lists only for simple list/profile questions. |
| Community labels | Generate simple deterministic labels from representative titles/materials/methods; sophisticated LLM naming is deferred. |
| Guardrails | Support only explicitly allowlisted, prebuilt query path shapes. |
| Existing gateway/file QA | Preserve current behavior. |

Deferred beyond V1:

| Area | Deferred Item |
| --- | --- |
| Full numeric direct answer coverage | Broad numeric sorting/filtering across every performance field. |
| LLM-generated Cypher | Free-form LLM Cypher generation and execution. |
| Neo4j full-text/vector indexes | Adding and depending on new graph indexes. |
| Normalized ontology migration | Populating or depending on `Article`, `Material`, `Process`, `Step`, `Equipment`, `__Chunk__`, etc. |
| Community vector DB | Rebuilding or depending on `community_vector_database`. |
| Advanced community explanation | Causal/mechanistic community conclusions without RAG evidence. |
| Perfect entity canonicalization | Full ontology-grade normalization of all materials and recipe entities. |

V1 acceptance should be judged against the in-scope table, not against the maximum future capability described in this spec.

### 4.1 `precise`

Use `precise` when the user asks for structured, bounded, or directly enumerable facts that Neo4j can answer with high confidence.

Examples:

- "10.1021/jp1005692 这篇文献是什么？"
- "列出使用蔗糖作为碳源的文献"
- "哪些文献使用 LiFePO4 作为正极材料？"
- "LiFePO4 的制备方法有哪些？"
- "压实密度最高的 LFP 材料有哪些？"
- "放电容量超过 150 mAh/g 的 LFP 材料有哪些？"
- "统计使用 sucrose 作为碳源的文献数量"

Expected behavior:

- Use Neo4j first.
- Return direct graph answer only when:
  - query intent maps to supported structured path,
  - result count is non-empty,
  - DOI quality is acceptable for direct display,
  - values can be rendered safely,
  - ranking/filtering is backed by field parser confidence.
- If direct answer confidence is insufficient, fall back to graph-for-RAG, not to a misleading graph answer.
- In V1, if parser-backed ranking is not implemented for a field, return graph-for-RAG evidence rather than a direct ranked answer.

Direct answer style:

- concise,
- structured,
- include DOI/title/material/method/value rows where useful,
- state that values come from the knowledge graph when needed,
- preserve original value text for parsed numeric fields,
- avoid claiming more precision than the source supports.

### 4.2 `semantic`

Use `semantic` when the user asks broad, explanatory, mechanism, trend, or synthesis questions.

Examples:

- "为什么碳包覆会影响 LFP 倍率性能？"
- "总结 LiFePO4 的主要改性策略"
- "LFP 制备方法对性能有什么影响？"
- "介绍固相法和水热法的差异"

Expected behavior:

- Primary retrieval is Chroma/RAG.
- Graph is optional.
- If the question contains clear graph entities such as `LiFePO4`, `carbon_source`, `dopant`, or known process terms, graph evidence may be attached as hints.
- If no useful graph signal exists, skip graph quickly.

Answer style:

- synthesize across retrieved literature,
- cite DOI/PDF/MD evidence through existing RAG citation flow,
- do not present raw graph rows as the main answer unless they help structure the synthesis.

### 4.3 `hybrid`

Use `hybrid` when the user asks for both structured filtering and explanatory synthesis.

Examples:

- "放电容量超过 150 mAh/g 的 LFP 有哪些特点？"
- "找出使用 sucrose 碳源的 LiFePO4 文献，并总结这些工艺的共性"
- "压实密度较高的材料通常采用什么工艺？"
- "比较水热法和固相法制备的 LFP 在循环稳定性上的差异"

Expected behavior:

1. Use Neo4j to identify candidate DOI/material/process/property rows.
2. Canonicalize graph rows into DOI candidates, entity hints, constraints, and fact blocks.
3. Use Chroma/PDF/MD retrieval to obtain citation-bearing evidence.
4. Use LLM synthesis to answer the analytical part.

Hybrid should be the default when:

- there is a numeric or categorical filter plus an explanation/comparison request,
- there is a structured entity condition plus a "总结/分析/特点/趋势/为什么" request,
- graph direct answer would be too shallow but graph evidence can reduce retrieval noise.

Answer style:

- first describe the structured selection criteria,
- then synthesize common patterns,
- include representative DOI evidence,
- distinguish graph-derived facts from RAG-derived interpretation when necessary.

### 4.4 `community`

Use `community` when the user asks about relationship networks, communities, mechanism associations, or graph clusters.

Examples:

- "LiFePO4 的关系网络和机制关联是什么？"
- "哪些材料和 LFP 在同一图谱社区？"
- "围绕 LiFePO4 的主要研究社区有哪些？"
- "碳源、制备方法和容量之间有什么社区关联？"

Expected behavior:

1. Identify target term or entity.
2. Find relevant `louvainCommunityId` communities from title/material/process/raw material matches.
3. Extract community representatives:
   - DOI/title,
   - material/sample names,
   - preparation methods,
   - raw materials,
   - recipe fields,
   - performance field examples.
4. Generate a human-readable community label.
5. Answer in terms of concepts, not raw community IDs.
6. Use Chroma/RAG for citation support if the answer makes explanatory claims.

Raw community IDs may appear only in diagnostics, not as the main user-facing concept.

Community direct answer should be possible for:

- "同社区文献有哪些？"
- "这个社区的代表方法有哪些？"
- "这个社区主要覆盖哪些性能字段？"

Community graph-for-RAG should be used for:

- mechanisms,
- causal explanations,
- broad literature synthesis,
- cross-community comparison.

For V1, community direct answer is optional except for simple representative list/profile questions where the graph rows are high-confidence. The required V1 behavior is that community questions produce community graph evidence and no longer unconditional `skip_graph`.

## 5. Routing Requirements

### 5.1 Route Ownership

Gateway route ownership:

- file intent,
- selected file IDs,
- PDF/table/mixed route,
- `source_scope`,
- conversation file context.

fastQA route ownership:

- knowledge intent classification,
- graph vs vector vs hybrid vs community,
- graph evidence planning,
- graph-to-RAG handoff,
- direct graph answer rendering.

### 5.2 Internal Route Contract

The fastQA graph route result should keep the current tri-state execution contract:

```text
KnowledgeRouteFamily -> ExecutionMode

precise   -> direct_answer | graph_for_rag
semantic  -> skip_graph | graph_for_rag
hybrid    -> graph_for_rag | direct_answer for pure structured subcases only
community -> direct_answer | graph_for_rag
```

This contract keeps user-visible route semantics separate from execution safety.

### 5.3 Classifier Requirements

The classifier must output:

- route family:
  - `precise`
  - `semantic`
  - `hybrid`
  - `community`
- execution preference:
  - `direct_answer`
  - `graph_for_rag`
  - `skip_graph`
- confidence score,
- matched rule IDs,
- extracted slots,
- reason for downgrade if direct answer is not allowed.

Minimum extracted slots:

- DOI,
- material/entity terms,
- title terms,
- raw material terms,
- recipe field terms,
- process/method terms,
- property field names,
- numeric operator,
- numeric threshold,
- ranking direction,
- limit/top-k,
- community/network signal,
- analysis/synthesis signal.

Classifier must be deterministic for obvious cases. LLM-based classification can be added later, but the first implementation should rely on rule and slot extraction for predictability.

### 5.4 Route Priority

Recommended priority:

1. Explicit DOI lookup or DOI expansion.
2. File route already chosen by gateway; do not override inside graph router.
3. Community/network keywords.
4. Hybrid signals: structured filter plus analysis/synthesis.
5. Precise signals: DOI, list/count/filter/rank, numeric property, recipe/process field, entity enumeration.
6. Semantic signals: why/how/summary/trend/mechanism without strong structured filter.
7. Default semantic.

Important nuance:

- Community should be checked before generic semantic keywords. "机制关联" and "关系网络" must not be swallowed by semantic "为什么/影响/机制" rules.
- Hybrid should outrank precise when the user asks for both filtering and explanation.
- Semantic should skip graph if no useful graph slots are extracted.

## 6. Graph Schema Requirements

### 6.1 Registry Coverage

The schema registry must represent the actual populated graph, not the empty normalized ontology labels.

Required field families:

Document:

- `paper.doi`: label `doi`, property `name`
- `paper.title`: label `title`, relation `title`

Material/sample:

- `material.sample_name`: label `name`, relation `name`
- `raw_material.name`: label `raw_materials`, relation path `raw_materials/raw_materials`

Process:

- `process.method`: label `preparation_method`, relation path `process/preparation_method`
- `process.step_name`: label `step_name`, relation path `process/step_name`
- `process.calcination`: label `calcination`, relation path `process/key_process_parameters/calcination`
- `process.milling`: label `milling`, relation path `process/key_process_parameters/milling`
- `process.sintering`: label `sintering`, relation path `process/key_process_parameters/sintering`
- `process.drying`: label `drying`, relation path `process/key_process_parameters/drying`
- `process.other_parameters`: label `other_parameters`, relation path `process/key_process_parameters/other_parameters`

Recipe:

- `recipe.carbon_source`: label `carbon_source`, relation path `recipe/carbon_source`
- `recipe.carbon_content`: label `carbon_content`, relation path `recipe/carbon_content`
- `recipe.additives`: label `additives`, relation path `recipe/additives`
- `recipe.dopant`: label `dopant`, relation path `recipe/dopant`
- `recipe.doping_elements`: label `doping_elements`, relation path `recipe/doping_elements`
- `recipe.li_fe_ratio`: label `Li_Fe_ratio`, relation path `recipe/Li_Fe_ratio`
- `recipe.fe_p_ratio`: label `Fe_P_ratio`, relation path `recipe/Fe_P_ratio`
- `recipe.other_ratios`: label `other_ratios`, relation path `recipe/other_ratios`

Testing/equipment:

- `testing.name`: label `testing`, relation path `testing/testing`
- `equipment.name`: label `name`, relation path `equipment/name`
- `equipment.model`: label `model`, relation path `equipment/model`
- `equipment.parameters`: label `parameters`, relation path `equipment/parameters`
- `equipment.structure`: label `structure`, relation path `equipment/structure`
- `equipment.instrument`: label `instrument`, relation path `equipment/instrument`

Performance:

- `performance.discharge_capacity`: label `discharge_capacity`, relation path `name/discharge_capacity`
- `performance.discharge_capacity_child`: label `discharge_capacity`, relation path `name/discharge_capacity/discharge_capacity`
- `performance.cycling_stability`: label `cycling_stability`, relation path `name/cycling_stability`
- `performance.compaction_density`: label `compaction_density`, relation path `name/compaction_density`
- `performance.tap_density`: label `tap_density`, relation path `name/tap_density`
- `performance.conductivity`: label `conductivity`, relation path `name/conductivity`
- `performance.coulombic_efficiency`: label `coulombic_efficiency`, relation path `name/coulombic_efficiency`
- `performance.particle_size`: label `particle_size`, relation path `name/particle_size`
- `performance.surface_area`: label `surface_area`, relation path `name/surface_area`
- `performance.energy_density`: label `energy_density`, relation path `name/energy_density`
- `performance.power_density`: label `power_density`, relation path `name/power_density`

Community:

- `community.id`: node property `louvainCommunityId`

### 6.2 Allowlist Requirements

Guardrail allowlists must include all labels and relationships needed by the registry.

Allowed labels must include:

- `doi`
- `title`
- `name`
- `raw_materials`
- `process`
- `preparation_method`
- `process_steps`
- `step_name`
- `key_process_parameters`
- `calcination`
- `milling`
- `sintering`
- `drying`
- `other_parameters`
- `recipe`
- `carbon_source`
- `carbon_content`
- `additives`
- `dopant`
- `doping_elements`
- `Li_Fe_ratio`
- `Fe_P_ratio`
- `other_ratios`
- `testing`
- `equipment`
- `model`
- `parameters`
- `structure`
- `instrument`
- `description`
- `morphology`
- all selected performance labels.

Allowed relationships must include:

- `title`
- `name`
- `raw_materials`
- `process`
- `preparation_method`
- `process_steps`
- `step_name`
- `key_process_parameters`
- `calcination`
- `milling`
- `sintering`
- `drying`
- `other_parameters`
- `recipe`
- `carbon_source`
- `carbon_content`
- `additives`
- `dopant`
- `doping_elements`
- `Li_Fe_ratio`
- `Fe_P_ratio`
- `other_ratios`
- `testing`
- `equipment`
- `model`
- `parameters`
- `structure`
- `instrument`
- `description`
- `morphology`
- selected performance relationships.

### 6.3 Empty Ontology Labels

The following labels exist in constraints/indexes but have no observed nodes:

- `Article`
- `Entity`
- `Material`
- `Process`
- `Step`
- `Equipment`
- `__Document__`
- `__Chunk__`
- `__Entity__`

The first implementation must not depend on these labels.

## 7. Planner Requirements

### 7.1 General Planner Contract

Every graph plan should include:

- route family,
- execution mode preference,
- strategy,
- intent,
- extracted slots,
- candidate query paths,
- expected row schema,
- row limit,
- timeout,
- confidence,
- downgrade/fallback policy,
- diagnostics.

Every query path should be:

- read-only,
- parameterized,
- bounded by `LIMIT`,
- guardrail-inspectable,
- tied to schema registry field names,
- clear about whether it is direct-answer eligible or graph-for-RAG only.

V1 production query paths should be prebuilt route-specific patterns. Existing generic candidate queries may remain as fallback evidence paths, but they must not be treated as direct-answer eligible until their direction, row schema, and guardrail behavior are tested.

### 7.2 Precise Planner

The precise planner must support these initial intents:

1. DOI lookup.
2. DOI context expansion.
3. Title/material listing.
4. Raw material listing.
5. Carbon source listing.
6. Preparation method listing.
7. Process parameter listing.
8. Performance value listing.
9. Count by structured field.
10. Top-k or threshold filter for selected numeric fields.

Direct-answer eligible fields for first version:

- DOI lookup,
- DOI context expansion,
- title/material listing,
- raw material listing,
- carbon source listing,
- preparation method listing,
- simple count,
- selected performance values when parser confidence is sufficient.

Numeric direct-answer fields should start small:

- `compaction_density`,
- `tap_density`,
- `discharge_capacity`,
- `cycling_stability`,
- `conductivity`.

The planner must not pretend all numeric fields are equally reliable.

For V1:

- direct numeric ranking/filtering is allowed only after a field-specific parser test proves confidence for that field;
- otherwise numeric precise questions should produce graph evidence and fall through to RAG;
- acceptance does not require direct numeric ranking for every numeric route example.

### 7.3 Semantic Planner

Semantic planner should normally return `skip_graph`.

It may return `graph_for_rag` when:

- material/process/recipe/performance slots are extracted,
- graph query can cheaply produce DOI/entity/fact hints,
- no high-risk substring scan is required over a huge label without a limit.

Semantic planner must not generate direct graph answers.

### 7.4 Hybrid Planner

Hybrid planner must produce graph evidence, not final answers, unless the question is actually precise.

Hybrid graph evidence should include:

- DOI candidates,
- matching titles,
- materials,
- methods,
- raw materials,
- recipe fields,
- performance values,
- graph facts formatted for Stage4.

Hybrid should preserve constraints:

- selected property field,
- operator,
- threshold,
- units,
- graph row confidence,
- original text values.

Graph filtering may be approximate in v1 if values require parser normalization, but the system must label that confidence internally and avoid overclaiming.

### 7.5 Community Planner

Community planner must support:

1. Find relevant communities by target term.
2. Profile community labels.
3. Extract representative DOI/title rows.
4. Extract representative material/sample names.
5. Extract representative raw materials.
6. Extract representative process methods.
7. Extract performance field profile.

Community direct answer is eligible for:

- representative literature,
- representative materials,
- representative methods,
- profile summaries.

Community graph-for-RAG is required for:

- mechanism explanation,
- why/how questions,
- cross-community interpretation,
- conclusions that need paper evidence.

For V1, community planner must at least support graph-for-RAG evidence extraction. Direct community representative rendering can be implemented for simple list/profile routes, but mechanism and association questions should go through RAG synthesis.

## 8. Canonicalization Requirements

Raw graph rows must be canonicalized before direct rendering or RAG injection.

### 8.1 DOI Validation

DOI candidates should be classified:

- valid,
- suspicious,
- invalid.

Suspicious signals:

- does not match strict DOI pattern,
- truncated publisher prefix,
- unusually many titles,
- obvious text glued to DOI,
- no title and sparse evidence,
- known corrupted forms such as `10.1007/s12598-`.

Direct answers should exclude invalid DOI rows and usually exclude suspicious rows.

Graph-for-RAG may keep suspicious rows only when:

- they are not used as citation anchors,
- they are marked low-confidence,
- they do not dominate the candidate set.

### 8.2 Entity Canonicalization

Entity canonicalization should cover:

- `LFP`,
- `LiFePO4`,
- `LiFePO₄`,
- `lithium iron phosphate`,
- common casing variants,
- suffix cleanup such as `_null_null`,
- DOI suffix removal from material node names where safe.

Canonicalization must preserve original text.

### 8.3 Field Value Parsing

Parsers must return:

- original text,
- parsed numeric value when available,
- unit,
- context such as cycles/rate/temperature/pressure,
- confidence,
- parse warnings.

Initial parsers:

- density parser:
  - `g/cm³`,
  - `g cm⁻³`,
  - `g cm−3`,
  - percentage of theoretical density.
- capacity parser:
  - `mAh g⁻¹`,
  - `mA h g⁻¹`,
  - strings like `0.5C_initial_141.2 mA h g⁻¹`.
- retention/cycling parser:
  - `% retention`,
  - cycles,
  - rate/current.
- conductivity parser:
  - scientific notation,
  - `S cm⁻¹`,
  - `S/cm`.
- percent parser:
  - coulombic efficiency,
  - retention.

If parsing fails, the row may still be used as text evidence but must not be used for numeric sorting/filtering.

### 8.4 Serialized Field Parsing

Fields such as `additives` and `doping_elements` may contain serialized dictionary-like strings.

Parser should:

- split multiple dictionary-like segments,
- extract names,
- types,
- content,
- function/purpose,
- element,
- form,
- preserve original string when parsing is partial.

The first implementation may render these as structured text if parsing is reliable, otherwise as original text snippets.

## 9. Direct Answer Requirements

Direct graph answers must satisfy:

- answer is based on graph rows,
- route diagnostics identify graph route family,
- no unsupported claims,
- result count is shown or inferable,
- references include DOI/title when available,
- suspicious DOI rows are filtered or marked,
- no raw Cypher is exposed to user by default,
- no raw community ID is used as a semantic label.

Direct answer should not be used when:

- route is semantic,
- community answer asks for mechanism explanation,
- numeric comparison lacks parser confidence,
- result rows are mostly placeholders,
- graph evidence is too sparse,
- DOI candidates are mostly suspicious.

## 10. Graph-to-RAG Requirements

`GraphRagPayload` should be the core handoff object.

Required payload fields:

- `stage1_context_block`,
- `stage2_doi_candidates`,
- `stage2_entity_hints`,
- `stage2_constraints`,
- `stage4_fact_block`,
- `cache_fingerprint`.

Recommended extensions:

- route family,
- evidence confidence,
- suspicious DOI count,
- filtered DOI count,
- graph query strategy,
- community labels,
- canonicalized constraints.

Stage behavior:

- Stage1 receives compact route/fact context.
- Stage2 retrieval prefixes DOI/entity hints into queries.
- Stage2 should avoid making the query too long with low-value graph rows.
- Stage3 can use graph DOI fallback only when vector retrieval fails to produce DOI.
- Stage4 receives structured facts as supplemental evidence and must not cite graph facts as PDF evidence unless backed by retrieval.

## 11. Observability Requirements

Metadata should expose route behavior for debugging and tests.

Metadata fields:

- `graph_pipeline_version`,
- `knowledge_route_family`,
- `tri_state_mode`,
- `graph_strategy`,
- `graph_intent`,
- `graph_result_count`,
- `graph_confidence`,
- `graph_doi_candidates_count`,
- `graph_filtered_doi_count`,
- `graph_suspicious_doi_count`,
- `graph_fallback_reason`,
- `graph_direct_answer_eligible`,
- `graph_rag_injection_enabled`,
- `doi_source`.

User-facing stream steps should be stable:

For direct graph answer:

- identify graph intent,
- execute graph query,
- summarize graph results.

For graph-for-RAG:

- identify graph evidence,
- attach graph hints,
- run RAG retrieval,
- synthesize final answer.

For skip graph:

- normal RAG steps only, unless diagnostics are requested.

## 12. Configuration Requirements

Existing config flags should remain meaningful:

- `FASTQA_GRAPH_KB_ENABLED`
- `FASTQA_GRAPH_KB_V2_ENABLED`
- `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED`
- `FASTQA_GRAPH_KB_TIMEOUT_MS`
- `FASTQA_GRAPH_KB_MAX_ROWS`
- `FASTQA_GRAPH_KB_QUERY_LOGGING`

New config flags may be added if needed:

- `FASTQA_KB_INTENT_ROUTER_ENABLED`
- `FASTQA_GRAPH_COMMUNITY_ROUTE_ENABLED`
- `FASTQA_GRAPH_PRECISE_NUMERIC_ENABLED`
- `FASTQA_GRAPH_DIRECT_ANSWER_MIN_CONFIDENCE`
- `FASTQA_GRAPH_MAX_DOI_CANDIDATES`
- `FASTQA_GRAPH_ALLOW_SUSPICIOUS_DOI_FOR_RAG`

Default behavior should be conservative:

- direct answers only for high-confidence graph plans,
- graph-for-RAG for medium confidence,
- skip graph for low confidence.

## 13. Safety And Guardrails

Graph execution must remain read-only.

Guardrail requirements:

- reject write clauses,
- reject procedure calls unless explicitly allowlisted,
- reject unknown labels and relationships,
- enforce `LIMIT`,
- enforce timeout,
- cap result rows,
- reject multi-statement Cypher,
- parameterize user values,
- never interpolate raw user strings into Cypher.

LLM-generated Cypher is not required for v1. If retained as a strategy name, it must still execute only prebuilt or guarded query paths unless a separate reviewed LLM Cypher safety design is added.

### 13.1 V1 Production Query Shape Policy

V1 production graph queries should use explicit relationship paths wherever practical.

Allowed V1 direct-answer query shapes:

- exact DOI lookup:
  - `MATCH (d:doi {name: $doi})`
- one-hop DOI expansion:
  - `MATCH (d:doi {name: $doi})-[r]->(x)` only if `r` is inspected against allowlisted relationship types before rendering
- explicit title/material/raw-material paths:
  - `(:doi)-[:title]->(:title)`
  - `(:doi)-[:name]->(:name)`
  - `(:doi)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(:raw_materials)`
- explicit recipe paths:
  - `(:doi)-[:recipe]->(:recipe)-[:carbon_source]->(:carbon_source)`
  - equivalent explicit paths for approved recipe fields
- explicit process paths:
  - `(:doi)-[:process]->(:process)-[:preparation_method]->(:preparation_method)`
  - `(:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[:calcination]->(:calcination)`
  - `(:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[:milling]->(:milling)`
  - `(:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[:sintering]->(:sintering)`
  - `(:doi)-[:process]->(:process)-[:key_process_parameters]->(:key_process_parameters)-[:drying]->(:drying)`
- explicit performance paths:
  - `(:doi)-[:name]->(:name)-[:compaction_density]->(:compaction_density)`
  - `(:doi)-[:name]->(:name)-[:discharge_capacity]->(:discharge_capacity)-[:discharge_capacity]->(:discharge_capacity)`
  - equivalent explicit paths for approved performance fields
- community lookup using node property:
  - `MATCH (n {louvainCommunityId: $cid})` only for bounded profiling queries with label aggregation and strict `LIMIT`

Dynamic relationship filtering with `type(r) IN [...]` is not automatically direct-answer eligible in V1. It is allowed only when:

- every candidate relationship is in the schema registry allowlist,
- the guardrail explicitly supports this pattern,
- tests cover both allowed and rejected relationship values,
- result rendering preserves field identity.

Community profiling queries that use label aggregation or dynamic node matching must be treated as graph-for-RAG evidence unless a direct renderer test covers the exact shape.

Implementation planning must include guardrail upgrades if any new query shape cannot be safely inspected by the current guardrail.

## 14. Testing Requirements

### 14.1 Unit Tests

Classifier tests:

- DOI lookup -> `precise/direct_answer`.
- carbon source listing -> `precise/graph_for_rag` or direct if planner supports it.
- density top-k -> `precise`.
- capacity threshold plus analysis -> `hybrid`.
- mechanism question without structured slot -> `semantic/skip_graph`.
- community/network question -> `community`, not semantic.
- file context present -> no direct graph answer that ignores file context.

Planner tests:

- precise DOI plan has template or equivalent path.
- carbon source plan targets `recipe/carbon_source`.
- process method plan targets `process/preparation_method`.
- capacity plan uses two-hop capacity path when needed.
- community plan includes community lookup and representative extraction paths.
- semantic no-slot plan returns no graph plan.

Guardrail tests:

- all new labels/relationships are allowlisted.
- write clauses are rejected.
- missing limit is rejected or repaired.
- unknown label is rejected.

Canonicalizer tests:

- DOI validation and filtering.
- material suffix cleanup.
- carbon source variants.
- density parser.
- capacity parser.
- cycling retention parser.
- conductivity parser.
- serialized additives/doping parser.

RAG adapter tests:

- DOI candidates dedupe and cap.
- entity hints include route-specific fields.
- stage4 fact block is compact.
- cache fingerprint changes when graph evidence changes.

Direct renderer tests:

- DOI direct answer.
- carbon source listing.
- process method listing.
- numeric direct answer with parsed values.
- direct answer refusal/downgrade for low parser confidence.
- community representative answer without raw ID as main label.

### 14.2 Integration Tests

Use fake Neo4j client rows where possible.

Integration scenarios:

- `kb_qa` direct DOI answer returns graph events and stops before RAG.
- `kb_qa` hybrid attaches graph payload and continues RAG.
- `community` attaches community evidence or returns direct representative summary.
- graph exception falls back to generation-driven RAG.
- graph timeout falls back to generation-driven RAG.
- disabled graph flags preserve current RAG behavior.

### 14.3 Optional Live Smoke Tests

Live Neo4j tests should be opt-in only.

Examples:

- `10.1039/c4ra15767b` DOI expansion.
- `sucrose` carbon source listing.
- `LiFePO4` community representatives.
- title-constrained two-hop discharge capacity.

These tests should not be required in default CI unless the local Neo4j service is guaranteed.

## 15. Acceptance Criteria

### 15.1 Route Behavior

The following behaviors must pass:

| User Question | Expected Internal Family | Expected Execution |
| --- | --- | --- |
| `10.1021/jp1005692 这篇文献是什么？` | `precise` | direct graph answer if data exists |
| `列出使用蔗糖作为碳源的文献` | `precise` | carbon-source graph evidence; direct or graph-for-RAG depending renderer confidence |
| `压实密度最高的LFP材料有哪些？` | `precise` | graph field planner; V1 may return graph-for-RAG unless parser-backed direct ranking is implemented |
| `放电容量超过150 mAh/g的LFP有哪些特点？` | `hybrid` | graph seed plus RAG synthesis |
| `为什么碳包覆会影响LFP倍率性能？` | `semantic` | RAG primary, graph optional |
| `LiFePO4的关系网络和机制关联是什么？` | `community` | community graph evidence plus RAG synthesis, not unconditional skip graph |

### 15.2 Backward Compatibility

Existing behavior must remain:

- gateway file routing unchanged,
- `pdf_qa` unchanged,
- `tabular_qa` unchanged,
- gateway `hybrid_qa` semantics unchanged,
- generation-driven RAG remains default fallback,
- graph disabled flags still skip graph cleanly,
- legacy graph template behavior either preserved or replaced by equivalent tests.

### 15.3 Performance

Expected constraints:

- graph routing should usually finish within `FASTQA_GRAPH_KB_TIMEOUT_MS`.
- no unbounded graph queries.
- graph evidence row count capped by `FASTQA_GRAPH_KB_MAX_ROWS`.
- Chroma retrieval should not receive unbounded graph prefixes.
- community summaries should cap representative extraction per category.

### 15.4 Quality

Answers should:

- avoid raw community IDs as user-facing concepts,
- avoid raw placeholder nodes when better child values exist,
- preserve original numeric strings,
- not overclaim numeric ranking when parsing is uncertain,
- cite RAG/PDF/MD evidence for explanatory claims,
- expose route metadata for debugging.

### 15.5 V1 Completion Boundary

V1 is complete when:

- all four route families are classified and observable in metadata,
- `community` no longer maps to unconditional `skip_graph`,
- DOI and simple structured list/count routes can direct-answer when high-confidence,
- numeric and complex community questions can safely downgrade to graph-for-RAG,
- route-specific graph evidence reaches RAG stages,
- no direct answer relies on unparsed placeholders or unsafe dynamic query paths.

V1 is not required to:

- direct-answer all numeric ranking questions,
- direct-answer all community mechanism questions,
- implement LLM Cypher,
- create new graph indexes.

## 16. Proposed Implementation Phases

This section is directional. The detailed implementation plan will be written separately after this spec is approved.

### Phase 1: Route Contract And Schema Registry

Goal:

- make route family and tri-state behavior explicit,
- expand schema registry to reflect current graph.

Expected output:

- expanded fields,
- allowlist updates,
- classifier route metadata,
- tests for routing examples.

### Phase 2: Precise Planner Foundation

Goal:

- add field-specific graph planners for DOI, title/material, raw material, carbon source, process method, and basic performance fields.

Expected output:

- deterministic query paths,
- canonical row shapes,
- direct renderer support for simple list/count answers.

### Phase 3: Numeric Parsing And Direct Answer Safety

Goal:

- make selected numeric questions safe for direct graph answers or controlled graph-for-RAG downgrades.

Expected output:

- density/capacity/retention/conductivity parsers,
- parser confidence,
- ranking/threshold tests,
- direct numeric answers only for parser-backed fields selected for V1; other numeric routes stay graph-for-RAG.

### Phase 4: Hybrid Evidence Enrichment

Goal:

- improve GraphRagPayload so graph evidence materially improves Stage1/Stage2/Stage4.

Expected output:

- DOI/entity/fact hints,
- constraints,
- graph-seeded fallback,
- tests around retrieval query merging and synthesis fact blocks.

### Phase 5: Community Route

Goal:

- make community route first-class.

Expected output:

- community planner,
- representative extraction,
- deterministic representative labels,
- direct representative summaries only for simple list/profile questions when renderer tests cover the row shape,
- graph-for-RAG for mechanism/community explanation.

### Phase 6: Observability And Rollout Controls

Goal:

- make behavior debuggable and safe to deploy gradually.

Expected output:

- metadata,
- config toggles,
- logging,
- fallback metrics,
- compatibility tests.

## 17. Open Decisions

These should be resolved during implementation planning:

1. For V1, which specific numeric fields should be promoted from graph-for-RAG to direct ranking after parser tests pass?
2. Should community representative list/profile direct answers be enabled in the first implementation batch or after graph-for-RAG community evidence lands?
3. Should we add Neo4j full-text indexes later for high-frequency substring search, or keep semantic matching in Chroma?
4. Should `community_vector_database` be regenerated, or should community route use Neo4j communities plus existing Chroma stores?
5. Should route metadata be returned only in `metadata`, or also as visible stream steps?
6. What threshold should exclude suspicious DOI rows from graph-for-RAG candidate seeding?

## 18. Non-Goals

This spec does not require:

- restoring legacy monolithic files,
- making gateway understand Neo4j schema,
- using LLM-generated Cypher in v1,
- populating empty normalized ontology labels,
- changing file QA behavior,
- adding new Neo4j indexes before the first route implementation,
- changing Chroma ingestion.

## 19. Definition Of Done

The feature is done when:

- the six acceptance route examples behave as specified,
- all new graph planner paths are guarded and tested,
- direct answers are only used for high-confidence graph cases,
- community route no longer maps to unconditional `skip_graph`,
- graph-for-RAG payload materially includes route-specific DOI/entity/fact evidence,
- existing gateway/file/RAG tests still pass,
- docs describe the final route behavior and fallback rules.
