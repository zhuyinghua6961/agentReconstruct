# fastQA Graph Routing Current State

> Status: V1 implementation notes for the refactored fastQA graph path. Gateway still owns file-vs-KB routing; fastQA `kb_qa` owns the internal `precise` / `semantic` / `hybrid` / `community` knowledge route.

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

This tri-state remains the V1 execution contract for the four-route experience.

## Graph V2 Classifier

Relevant file:

- `fastQA/app/modules/graph_kb/classifier_v2.py`

Current classifier uses canonical route families internally:

- `precise`
- `semantic`
- `hybrid`
- `community`

But it maps them into tri-state execution:

| Legacy Route Family | Current Tri-State Mapping |
| --- | --- |
| `precise` | `direct_answer` for DOI and safe list/count plans, otherwise `graph_for_rag`. |
| `semantic` | `skip_graph` when no graph slots exist; graph slots may seed RAG. |
| `hybrid` | `graph_for_rag`. |
| `community` | `graph_for_rag`; simple representative/profile answers may be directly rendered when explicitly direct-answer eligible. |

Important current gap:

- DOI lookup and DOI expansion are prioritized before semantic keywords.
- Numeric precise questions without tested parser confidence remain graph-for-RAG rather than direct rankings.
- Route family, tri-state, strategy, intent, confidence, result count, and RAG injection status are emitted as graph metadata.

## Graph V2 Planner And Execution

Relevant files:

- `fastQA/app/modules/graph_kb/planner_v2.py`
- `fastQA/app/modules/graph_kb/query_strategy.py`
- `fastQA/app/modules/graph_kb/executor_v2.py`
- `fastQA/app/modules/graph_kb/guardrail.py`
- `fastQA/app/modules/graph_kb/schema_registry.py`

Current V1 planning:

| Strategy | Meaning |
| --- | --- |
| `template` | Use older proven legacy template execution when still available. |
| `v1_template` | Use explicit route-specific Cypher paths from `query_templates.py`. |
| `parametric` | Use explicit numeric-property V1 templates for graph-for-RAG evidence. |

V1 explicit query templates cover:

- DOI lookup and DOI context expansion
- title/material listing
- raw material listing
- carbon source listing and count
- process method listing
- numeric property graph evidence for capacity/density/conductivity-style fields
- community term lookup and representative/profile paths

The schema registry now covers the observed field-bucket graph:

- DOI, title, sample/material names, raw materials
- process method and process parameter child buckets
- recipe subfields such as carbon source and doping elements
- performance fields such as discharge capacity and compaction density
- `louvainCommunityId` community property

Free-form LLM Cypher is intentionally out of scope for V1. Guardrail accepts explicit allowlisted labels/relations and rejects unbounded or unallowlisted dynamic relationship shapes.

## Current Classifier Examples

Read-only local checks against `classify_graph_question_v2` and `build_graph_query_plan_v2` produced these examples:

| Question | Legacy Route Family | Tri-State Mode | Strategy | Notes |
| --- | --- | --- | --- | --- |
| `压实密度最高的LFP材料有哪些？` | `precise` | `graph_for_rag` | `parametric` | Numeric evidence is parsed/canonicalized for RAG; direct ranking remains gated. |
| `请总结LiFePO4的制备方法和测试表征` | `precise` | `graph_for_rag` | `v1_template` | Structured graph slots seed RAG. |
| `LiFePO4的关系网络和机制关联是什么？` | `community` | `graph_for_rag` | `v1_template` | Community route now executes graph evidence instead of unconditional skip. |
| `为什么碳包覆会影响LFP倍率性能？` | `hybrid` | `graph_for_rag` | `parametric`/`v1_template` | Graph evidence enriches vector synthesis. |
| `列出使用蔗糖作为碳源的文献` | `precise` | `graph_for_rag` or safe direct | `v1_template` | Targets explicit `recipe.carbon_source` path. |
| `10.1021/jp1005692 这篇文献是什么？` | `precise` | `direct_answer` | `template` | DOI template still supports direct graph answer. |
| `放电容量超过150 mAh/g的LFP有哪些特点？` | `hybrid` | `graph_for_rag` | `parametric` | Capacity rows preserve original text and parser output for synthesis. |

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

The hybrid/community paths now use these insertion points with richer graph evidence and route metadata.

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
| `precise` | V1 implemented for DOI, list, count, recipe/process paths. | Numeric direct ranking remains gated by parser confidence and tests. |
| `semantic` | Generation-driven RAG remains default. | Pure semantic no-slot questions skip graph. |
| `hybrid` | V1 graph-for-RAG evidence implemented. | Final synthesis still depends on Chroma/PDF evidence. |
| `community` | V1 graph-for-RAG community evidence implemented. | Mechanism/network explanations are synthesized by RAG, not directly rendered. |

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
