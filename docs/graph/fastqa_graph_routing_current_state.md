# fastQA Graph Routing Current State

> Status: exploratory notes. This document compares the current refactored code path with the desired four-route graph experience. It does not prescribe reverting to the old code shape.

## Target Experience

The legacy flow document describes a user-facing experience with distinct routes:

- `precise`: use Neo4j for structured filtering, listing, counts, rankings, and DOI expansion.
- `semantic`: use Chroma/vector retrieval for broad summaries, mechanisms, trends, and why/how questions.
- `hybrid`: use Neo4j to constrain or seed vector/PDF retrieval, then synthesize with RAG evidence.
- `community`: answer relationship/network/community questions from graph communities or broad community evidence.

The current codebase has some of the pieces, but the route boundaries are no longer expressed as the same old `smart_query` style entry point.

## Gateway Current Behavior

Gateway currently makes a coarse routing decision, not a graph-vs-vector decision.

Relevant files:

- `gateway/app/services/file_context_resolver.py`
- `gateway/app/services/route_decision.py`
- `gateway/app/routers/qa.py`

Current gateway route names:

- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

Key behavior:

- Gateway resolves file references, selected files, table/PDF focus, and mixed file+KB intent.
- If there is no file intent, gateway routes to `kb_qa`.
- `hybrid_qa` at gateway means file+KB or PDF+table mixing. It does not mean graph+vector hybrid.
- Gateway does not currently choose `precise`, `semantic`, `hybrid`, or `community` for the knowledge graph experience.

Implication: the four-route graph experience should probably live inside fastQA's `kb_qa` route, with gateway continuing to own file-vs-KB routing.

## fastQA Current KB Route

Relevant file:

- `fastQA/app/routers/qa.py`

For `route == "kb_qa"`, fastQA currently does:

1. Build conversation context.
2. If graph KB and graph KB V2 are enabled, call `route_graph_kb_v2(...)`.
3. If graph V2 returns `direct_answer`, stream the direct graph answer and stop.
4. If graph V2 returns `graph_for_rag`, attach `GraphRagPayload` when graph-to-RAG injection is enabled.
5. Fall through to generation-driven RAG.
6. If graph V2 is disabled but graph KB is enabled, try the older template graph path.

This means the current system already has a tri-state graph adapter:

| Current Mode | Meaning |
| --- | --- |
| `direct_answer` | Graph result is considered answerable directly. |
| `graph_for_rag` | Graph result becomes hints/evidence for RAG. |
| `skip_graph` | RAG proceeds without graph evidence. |

This tri-state is a useful refactored foundation for the four-route experience.

## Graph V2 Classifier

Relevant file:

- `fastQA/app/modules/graph_kb/classifier_v2.py`

Current classifier still uses legacy route names internally:

- `precise`
- `semantic`
- `hybrid`
- `community`

But it maps them into tri-state execution:

| Legacy Route Family | Current Tri-State Mapping |
| --- | --- |
| `precise` | `direct_answer` only if an old template plan exists; otherwise `graph_for_rag`. |
| `semantic` | `graph_for_rag` only if graph signals are present; otherwise `skip_graph`. |
| `hybrid` | `graph_for_rag`. |
| `community` | currently `skip_graph`. |

Important current gap:

- `community` is recognized but intentionally skipped.
- Numeric precise questions without old template support usually become graph-for-RAG, not true direct structured ranking/filtering.
- Entity detection is small and hard-coded.
- The route family is diagnostic metadata more than a complete user-facing route.

## Graph V2 Planner And Execution

Relevant files:

- `fastQA/app/modules/graph_kb/planner_v2.py`
- `fastQA/app/modules/graph_kb/query_strategy.py`
- `fastQA/app/modules/graph_kb/executor_v2.py`
- `fastQA/app/modules/graph_kb/guardrail.py`
- `fastQA/app/modules/graph_kb/schema_registry.py`

Current strategy selection:

| Strategy | Current Meaning |
| --- | --- |
| `template` | Use older hard-coded graph templates when available. |
| `parametric` | Use built-in candidate queries for precise numeric signals. |
| `llm_cypher` | Strategy name exists, but current planner still supplies built-in candidate queries; this is not a fully restored free-form LLM Cypher path. |

Current built-in candidate queries are broad search queries over:

- DOI/title
- raw materials
- sample names
- testing
- description
- preparation method

Current schema registry is much smaller than the actual graph:

- Covers DOI, title, raw materials, process method, equipment, testing, recipe, description.
- Does not cover performance fields, recipe subfields, process parameter subfields, or `louvainCommunityId`.

Implication: the current Graph V2 scaffold is structurally suitable, but its schema registry and query planner are not yet rich enough for the desired four-route behavior.

## Current Classifier Examples

Read-only local checks against `classify_graph_question_v2` and `build_graph_query_plan_v2` produced these examples:

| Question | Legacy Route Family | Tri-State Mode | Strategy | Notes |
| --- | --- | --- | --- | --- |
| `压实密度最高的LFP材料有哪些？` | `precise` | `graph_for_rag` | `parametric` | Recognizes precise numeric intent, but no direct density ranking answer. |
| `请总结LiFePO4的制备方法和测试表征` | `semantic` | `graph_for_rag` | `llm_cypher` | Graph hints may be attached, final path remains RAG. |
| `LiFePO4的关系网络和机制关联是什么？` | `community` | `skip_graph` | none | Community route is recognized but not executed against Neo4j. |
| `为什么碳包覆会影响LFP倍率性能？` | `semantic` | `graph_for_rag` | `llm_cypher` | Semantic question with graph/entity hints. |
| `列出使用蔗糖作为碳源的文献` | `precise` | `graph_for_rag` | `llm_cypher` | Should target `carbon_source`, but current candidate queries are generic. |
| `10.1021/jp1005692 这篇文献是什么？` | `precise` | `direct_answer` | `template` | DOI template still supports direct graph answer. |
| `放电容量超过150 mAh/g的LFP有哪些特点？` | `hybrid` | `graph_for_rag` | `llm_cypher` | Good target for graph filter + RAG synthesis, but current planner lacks numeric capacity parsing. |

The practical gap is not classification vocabulary; the names are already present. The gap is that most non-template routes do not yet have route-specific graph plans and renderers.

## Graph-To-RAG Injection

Relevant files:

- `fastQA/app/modules/graph_kb/rag_adapter.py`
- `fastQA/app/modules/qa_kb/stages/planning.py`
- `fastQA/app/modules/qa_kb/stages/retrieval.py`
- `fastQA/app/modules/qa_kb/stages/synthesis.py`
- `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`

Current injection points:

| Stage | Current Graph Payload Use |
| --- | --- |
| Stage1 planning | `stage1_context_block` is passed as `graph_context`. |
| Stage2 retrieval | DOI and entity hints are prefixed into retrieval query text. |
| Stage4 synthesis | `stage4_fact_block` is passed as supplemental structured graph facts. |

There is also a graph-seeded DOI fallback in the generation orchestrator: if Stage2 vector retrieval produces no DOI but graph evidence has DOI candidates, those graph DOI candidates can seed later PDF/MD loading.

Implication: the current hybrid path has the right insertion points. The main missing piece is richer, higher-confidence graph evidence.

## Current Vector Stores

Configured and observed:

- `resource/fastqa/vector_database`: Chroma collection `lfp_papers`, 1024 dimensions, about 34,726 embeddings.
- `resource/fastqa/vector_database_md`: Chroma collection `md_papers`, 1024 dimensions, about 686,266 embeddings.

Configured but not present in this worktree during inspection:

- `resource/fastqa/vector_database_pdf`
- `resource/fastqa/community_vector_database`

Current route implication:

- `semantic` should remain generation-driven RAG over Chroma.
- `hybrid` should use graph evidence to constrain or enrich Chroma queries and DOI/PDF/MD evidence loading.
- `community` should not require a separate community vector DB initially; Neo4j `louvainCommunityId` can seed representative DOI/title/material/method summaries, then normal Chroma can provide citation evidence.

## Gap Against Desired Four Routes

| Desired Route | Current Support | Gap |
| --- | --- | --- |
| `precise` | Partial. Template and broad graph queries exist. | Missing field-specific planners/parsers for performance, process, recipe, and ranked numeric answers. |
| `semantic` | Strong. Generation-driven RAG is the default KB path. | Needs cleaner handoff so semantic questions skip unnecessary graph work unless graph hints are useful. |
| `hybrid` | Partial. `GraphRagPayload` injection exists. | Graph candidate generation is too shallow and does not yet express structured constraints well. |
| `community` | Data exists via `louvainCommunityId`; classifier recognizes keywords. | Current V2 maps `community` to `skip_graph`; no community planner/renderer yet. |

## Feasibility Assessment

Recreating the four-route experience is feasible without reverting to the old code shape.

Recommended shape:

1. Keep gateway as file-vs-KB router.
2. Add a fastQA-internal `KnowledgeIntentRouter` or evolve `classifier_v2` so `kb_qa` can classify into `precise`, `semantic`, `hybrid`, and `community`.
3. Extend `schema_registry.py` to match the populated field-bucket schema.
4. Add route-specific graph planners:
   - precise field planner,
   - DOI/material/process/recipe planner,
   - community planner,
   - hybrid candidate planner.
5. Keep the current `direct_answer` / `graph_for_rag` / `skip_graph` tri-state as the execution contract.
6. Render direct graph answers only when confidence is high; otherwise pass structured evidence into RAG.

This would preserve the refactored service architecture while restoring the old user-visible route behavior.
