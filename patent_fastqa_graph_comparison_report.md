# Patent graph_kb vs fastQA graph_kb

## Scope

This report compares the current patent graph implementation under `patent/server/patent/graph_kb/` with the current fastQA graph implementation under `fastQA/app/modules/graph_kb/`.

The comparison is based on:

- graph_kb source files
- runtime/router integration points
- config defaults
- tests that lock current behavior

It is a code-level comparison, not a live database inspection. Patent node and relationship inventories below are inferred from the Cypher templates in code.

## Executive summary

The patent graph_kb is a compact, closed-world, patent-specific query layer. It has 9 fixed templates, a direct Neo4j driver client, exact entity-oriented Cypher, strong stub filtering, and one outcome model: either return a final patent graph answer or fall back to the normal KB pipeline.

fastQA graph_kb is broader but less semantically precise in the current codebase. It contains:

- a legacy 5-template direct-answer path
- a V2 semantic routing layer with tri-state outcomes: `skip_graph`, `direct_answer`, `graph_for_rag`
- a schema registry, guardrail, canonicalizer, direct renderer, and graph-to-RAG adapter

The patent graph is narrower but structurally cleaner for its domain. fastQA V2 is architecturally more ambitious, but the current shipped implementation still executes only planner-supplied candidate Cypher and mainly acts as a graph evidence supplier for downstream generation, especially for non-template questions.

## 1. High-level architecture

### Patent

Main path:

1. `classifier.py`
2. `client.py` plan selection
3. `neo4j_client.py` query execution
4. `rendering.py`
5. `service.py` returns `PatentGraphKbExecutionResult`
6. `server/patent/kb_service.py` short-circuits normal `kb_qa` when handled

Characteristics:

- binary route decision: `try_graph` or `skip`
- no planner/executor split beyond template selection + query run
- no graph-to-RAG mode
- no guardrail layer, because all Cypher is hardcoded
- no dependency on generation runtime for graph answers

### fastQA

There are effectively two graph stacks:

- legacy stack:
  - `classifier.py`
  - `client.py`
  - `service.py::try_graph_kb_answer`
- V2 stack:
  - `classifier_v2.py`
  - `query_strategy.py`
  - `planner_v2.py`
  - `guardrail.py`
  - `executor_v2.py`
  - `canonicalizer.py`
  - `direct_renderer.py`
  - `rag_adapter.py`
  - `service.py::route_graph_kb_v2`

Router behavior in `fastQA/app/routers/qa.py`:

- if `graph_kb_enabled` and `graph_kb_v2_enabled`, it runs V2 first
- V2 may:
  - return a direct graph answer
  - attach `GraphRagPayload` to generation
  - skip graph and fall through to generation
- if V2 is disabled but graph is enabled, it falls back to legacy `try_graph_kb_answer`

### Architectural difference

Patent graph_kb is a deterministic query-answer subsystem.

fastQA graph_kb V2 is a routing and evidence subsystem. Its direct-answer branch only applies to legacy-template-compatible questions. For broader questions, V2 mostly produces graph hints for the generation pipeline rather than final graph answers.

## 2. Runtime and Neo4j bootstrap

### Patent

Config surface in `patent/config.py`:

- `PATENT_GRAPH_KB_ENABLED`
- `PATENT_NEO4J_URL`
- `PATENT_NEO4J_USERNAME`
- `PATENT_NEO4J_PASSWORD`
- `PATENT_NEO4J_DATABASE`
- `PATENT_GRAPH_KB_TIMEOUT_MS`
- `PATENT_GRAPH_KB_MAX_ROWS`
- `PATENT_GRAPH_KB_QUERY_LOGGING`

Default URL:

- `bolt://127.0.0.1:8687`

Bootstrap behavior:

- uses `neo4j.GraphDatabase.driver(...)` directly
- verifies connectivity
- probes the configured database with `RETURN 1`
- stores a direct driver in `PatentNeo4jClient`
- query execution always builds a `neo4j.Query` with timeout seconds

### fastQA

Graph feature flags live in `fastQA/app/core/config.py`:

- `FASTQA_GRAPH_KB_ENABLED`
- `FASTQA_GRAPH_KB_V2_ENABLED`
- `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED`
- `FASTQA_GRAPH_KB_TIMEOUT_MS`
- `FASTQA_GRAPH_KB_MAX_ROWS`
- `FASTQA_GRAPH_KB_QUERY_LOGGING`

Connection behavior in `fastQA/app/core/runtime.py`:

- graph bootstrap uses `NEO4J_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`
- the URL is not hardcoded in `Settings`
- `bootstrap_neo4j()` wraps `langchain_community.graphs.Neo4jGraph`
- bootstrap tries multiple constructor variants
- if APOC is unavailable, it can degrade after verifying base connectivity

### Port note

Patent default `8687` is confirmed in code and config example.

I did not find `7688` anywhere in the current `fastQA/` worktree. The current fastQA code expects `NEO4J_URL` from environment, and the current tests/bootstrap examples use `bolt://127.0.0.1:7687`.

### Runtime difference

Patent uses a direct Neo4j driver and does not depend on LangChain graph wrappers or APOC.

fastQA uses `Neo4jGraph` as its canonical graph client and carries compatibility logic for wrapper/APOC variability.

## 3. Query model and schema shape

### Patent graph schema shape

The patent graph is entity-oriented and domain-specific.

Observed node labels from templates:

- `Patent`
- `IPC`
- `IPCPrefix`
- `Organization`
- `Person`
- `ProcessStep`
- `StepTemplate`
- `MaterialRole`
- `Material`
- `ExperimentTable`
- `TableRow`
- `Measurement`
- `TechnicalProblem`
- `TechnicalSolution`
- `ApplicationScenario`
- `InventivePoint`
- `PerformanceFact`
- `ProtectionScope`
- `ClaimStepLabel`

Observed relationship types:

- `CLASSIFIED_AS`
- `IN_IPC_SUBCLASS`
- `HAS_APPLICANT`
- `HAS_AGENCY`
- `HAS_INVENTOR`
- `HAS_PROCESS_STEP`
- `INSTANCE_OF`
- `HAS_MATERIAL_ROLE`
- `OPTION_INCLUDES`
- `HAS_EXPERIMENT_TABLE`
- `HAS_ROW`
- `HAS_MEASUREMENT`
- `ADDRESSES`
- `PROPOSES`
- `HAS_APPLICATION_SCENARIO`
- `HAS_INVENTIVE_POINT`
- `HAS_PERFORMANCE_FACT`
- `PROTECTION_INCLUDES`
- `CLAIM_INCLUDES_STEP`
- `CITES_PATENT`

Important correction versus the earlier inference:

- applicant, inventor, and agency are not separate labels in the current Cypher; they are role-specific traversals to `Organization` or `Person`
- the IPC subclass node is queried as `IPCPrefix`, not `IPCSubclass`

### fastQA graph schema shape

fastQA is built around a DOI-centric field-bucket graph rather than a normalized domain entity graph.

Labels actually traversed by current query code:

- `doi`
- `title`
- `raw_materials`
- `process`
- `preparation_method`
- `testing`
- `description`
- `name`
- `key_process_parameters`

Relationship types actually traversed by current query code:

- `title`
- `raw_materials`
- `process`
- `preparation_method`
- `testing`
- `description`
- `name`
- `key_process_parameters`

Notable shape details:

- many traversals use unlabeled or same-labeled bucket hops such as `(d)-[:raw_materials]->(:raw_materials)-[:raw_materials]->(rm:raw_materials)`
- sample-name lookup uses `(s:name)-[:name]->(d)`
- process parameter traversal ends with an unconstrained target variable `kp`

Schema registry surface in V2 is slightly different:

- `schema_registry.py` additionally exposes logical fields for `recipe` and `equipment`
- current planner candidate queries do not actually search `recipe` or `equipment`
- current legacy template Cypher does use `key_process_parameters`, but that token is not part of the V2 schema registry allowlist

### Schema comparison

Patent:

- explicit domain entities
- relationship names encode business meaning
- query paths correspond to patent concepts directly

fastQA:

- storage-oriented buckets around DOI
- labels and relation names mostly duplicate field names
- many queries are text-match traversals over value buckets rather than entity joins

## 4. Template inventory and query intent

### Patent templates: 9

1. `lookup_patent_by_id`
2. `list_patent_process_steps`
3. `list_patent_material_roles`
4. `list_patent_experiment_tables`
5. `list_patent_problem_solution`
6. `list_patent_inventive_scope`
7. `list_patent_citations`
8. `list_patents_by_ipc`
9. `list_patents_by_applicant`

These are all explicit business queries over patent structure.

### fastQA legacy templates: 5

1. `lookup_by_doi`
2. `expand_doi_context_by_doi`
3. `list_by_material`
4. `list_by_raw_material`
5. `count_by_filter`

These are mostly DOI lookup or fuzzy literature/material listing queries.

### fastQA V2 candidate query families

For non-template V2 execution, the planner currently builds two generic candidate queries:

- `schema.primary`
- `schema.support`

Both are token-based search queries over title/raw-material/sample-name or title/testing/description/preparation-method fields.

### Query intent difference

Patent templates answer structural patent questions directly.

fastQA legacy templates answer simple literature lookup/listing questions directly.

fastQA V2 non-template paths do not directly encode most user semantics. They collect graph-adjacent evidence and pass it into generation.

## 5. Classifier behavior

### Patent classifier

`classify_patent_graph_kb_question()` is binary and conservative.

It will skip when:

- file context is present
- multiple patent IDs are present
- the question is an ambiguous follow-up
- the question contains a DOI
- the question is broad/semantic

It will try graph mainly for:

- one patent ID plus one of several domain-specific intent hints
- IPC patent listing
- applicant patent listing

### fastQA legacy classifier

`classify_graph_kb_question()` is also binary and regex-heavy, but its domain is literature/DOI/material questions.

It skips on:

- file context
- ambiguous follow-up
- broad semantic questions

It tries graph for:

- DOI lookup/context expansion
- literature listing/count
- raw-material listing

### fastQA V2 classifier

`classify_graph_question_v2()` returns `SemanticDecision` with:

- `skip_graph`
- `direct_answer`
- `graph_for_rag`

It preserves a legacy route family:

- `precise`
- `hybrid`
- `community`
- `semantic`

Important behavior differences from patent:

- file context does not force `skip`; it downgrades to `graph_for_rag`
- ambiguous follow-up also downgrades to `graph_for_rag`
- direct-answer mode only happens when the question also matches a legacy template plan

### Classifier comparison

Patent classifier answers a narrow question: "is this a structured patent graph question that I can answer right now?"

fastQA V2 classifier answers a broader question: "what role should graph evidence play in the full QA pipeline?"

## 6. Planning and query strategy

### Patent planning

`plan_patent_graph_query()` is a direct mirror of the classifier intent rules. It maps one question to one fixed template plus params.

There is no intermediate strategy layer.

### fastQA legacy planning

`plan_graph_kb_query()` maps a question to one of 5 fixed templates.

### fastQA V2 planning

V2 introduces:

- `query_strategy.py`
- `planner_v2.py`
- `schema_registry.py`

Strategy selection:

- `template` if the old legacy plan exists
- `parametric` for `precise` numeric questions
- `llm_cypher` otherwise

Current implementation caveat:

- both `parametric` and `llm_cypher` plans are executed from planner-supplied `candidate_queries`
- there is no current module that calls an LLM to synthesize Cypher at execution time
- `llm_cypher` is currently a strategy label, not a distinct runtime capability

Second caveat:

- numeric semantics are not actually expressed in the candidate Cypher
- for example, numeric ranking/filter questions route to `parametric`, but the planner still emits token-search queries over title/material/sample/testing/description/process text

### Planning comparison

Patent planning is simple but faithful to the question intent.

fastQA V2 planning is more extensible, but the current non-template strategies are still approximate retrieval plans rather than semantically exact graph query plans.

One additional implementation detail: both patent and fastQA legacy stacks duplicate routing logic between classifier and planner. fastQA V2 improves the separation, but still reuses the legacy template planner for direct-answer-compatible questions.

## 7. Execution behavior, guardrails, and timeouts

### Patent execution

`execute_patent_graph_plan()`:

- only runs against an available `PatentNeo4jClient`
- always uses hardcoded Cypher
- propagates `timeout_ms` into the Neo4j `Query`
- normalizes rows to dicts
- slices results to configured `max_rows`

There is no separate guardrail because the Cypher surface is fixed.

### fastQA legacy execution

`execute_graph_kb_plan()`:

- runs on the bootstrapped `Neo4jGraph`
- first tries direct driver execution with `neo4j.Query(timeout=...)`
- falls back to `graph.query(...)` or `graph.run(...)`
- sanitizes values when the graph wrapper supports it
- converts timeout-like Neo4j errors to `TimeoutError`

### fastQA V2 execution

`execute_prepared_query()` adds:

- execution traces
- path attempts
- guardrail checks for labels/relations/write clauses
- multi-candidate fallback

Important implementation detail:

- `timeout_ms` is ignored for V2 non-template execution (`executor_v2.py` assigns `_ = timeout_ms`)
- `_run_cypher_once()` uses `graph.query(...)` or raw `driver.execute_query(...)` without constructing a timed `neo4j.Query`
- template strategy still inherits timeout behavior through legacy execution
- parametric and `llm_cypher` strategies do not currently enforce per-query timeouts in the same way

### Guardrail comparison

Patent:

- safety comes from hardcoded Cypher only

fastQA V2:

- explicit read-only guardrail
- rejects `CREATE`, `MERGE`, `DELETE`, `SET`, `REMOVE`, `DROP`, `CALL`
- rejects labels/relations outside the schema allowlist
- appends `LIMIT 20` if missing

## 8. Rendering and answer packaging

### Patent rendering

`render_patent_graph_answer()` is template-specific and business-facing.

Characteristics:

- returns `answer`, `references`, `reference_objects`, and `metadata`
- builds patent reference objects with `patent_id`, `canonical_patent_id`, `title`, `source="patent_graph"`
- filters stub data aggressively
- can return render-empty with reasons such as missing title or missing facts
- uses plain Chinese text and bullet structure, without markdown section headers

The stub policy is especially important:

- direct target templates reject stub target patents entirely
- citation/listing templates filter stub rows and can force fallback when only stubs remain

### fastQA legacy rendering

`render_graph_kb_answer()` is also template-specific, but it is much more string-cleaning-oriented.

Characteristics:

- DOI normalization and malformed DOI filtering
- markdown-style sections with emoji headers for richer templates
- no structured reference object model, only DOI tuple
- heavy cleanup of dirty process strings like `_null_`, bucket artifacts, and embedded field names

### fastQA V2 rendering

V2 splits rendering into:

- `canonicalizer.py`
- `direct_renderer.py`
- `rag_adapter.py`

The canonicalizer produces:

- DOI candidates
- flat fact strings
- render slots
- `direct_answerable` flag

The direct renderer:

- only directly answers when `decision.mode == "direct_answer"` and `bundle.direct_answerable`
- mostly reuses the old template answer shapes
- otherwise can fall back to the first fact string

The RAG adapter produces:

- `stage1_context_block`
- `stage2_doi_candidates`
- `stage2_entity_hints`
- `stage4_fact_block`
- `cache_fingerprint`

### Rendering comparison

Patent renderer is domain-native and answer-oriented.

fastQA renderer is split between:

- direct UX for template-compatible questions
- machine-oriented evidence packaging for downstream generation

Patent also has a stronger reference model because the graph result itself owns the final cited objects. fastQA defers much of the user-facing citation experience to later router/generation layers.

## 9. Downstream integration

### Patent

Patent graph_kb integrates as a preflight in `server/patent/kb_service.py`.

If the graph result is handled:

- normal KB generation is skipped
- the response route remains `kb_qa`
- `query_mode` becomes `patent_graph_kb`
- a single graph step is reported

If not handled:

- it falls through to the staged runtime or retrieval fallback

There is no graph evidence injection into downstream stages.

### fastQA

fastQA V2 integrates in `app/routers/qa.py` before generation.

If mode is `direct_answer`:

- it returns immediately

If mode is `graph_for_rag` and injection is enabled:

- `GraphRagPayload` is attached to `QaKbRequest`
- Stage1 receives `graph_context`
- Stage2 merges graph DOI/entity hints into retrieval queries
- Stage2 can seed DOI fallback from graph evidence if retrieval yields none
- Stage4 receives `graph_fact_block`
- cache keys include the graph payload fingerprint

### Integration comparison

Patent graph is a standalone structured answer path.

fastQA graph V2 is partly a retrieval-control plane for the generation pipeline.

## 10. Concept mapping

Approximate conceptual overlaps:

| Patent graph concept | fastQA graph concept | Notes |
| --- | --- | --- |
| `Patent` | `doi` | primary document node |
| `ProcessStep` | `process` / `preparation_method` | patent is step-structured; fastQA is bucket/value-based |
| `MaterialRole` / `Material` | `raw_materials` | patent captures role semantics; fastQA mostly stores value lists |
| `ExperimentTable` / `Measurement` | `testing` | patent exposes table/row/measurement structure; fastQA stores flattened testing values |
| `TechnicalProblem` / `TechnicalSolution` / `ApplicationScenario` | no strong current equivalent | fastQA current graph templates do not model this layer |
| `InventivePoint` / `ProtectionScope` / `ClaimStepLabel` | no equivalent | patent has explicit invention/protection semantics |
| `Organization` / `Person` roles | no equivalent in current fastQA graph layer | fastQA graph is literature/material-centric |
| `description`, `recipe`, `equipment`, `name` | no direct current patent template equivalent | fastQA exposes some generic field buckets not used by patent graph |

## 11. Key findings

1. Patent graph_kb is structurally richer than fastQA graph_kb for its domain.
   It models explicit patent entities and meaningful relations instead of field buckets.

2. fastQA V2 is more architecturally advanced than patent graph_kb.
   It introduces routing, schema summaries, guardrails, execution traces, canonicalization, and graph-to-RAG integration.

3. fastQA V2 is not yet a true semantic Cypher planner in the current code.
   Non-template `parametric` and `llm_cypher` paths still execute prebuilt candidate queries from the planner.

4. Patent graph answers are more exact for supported questions.
   The templates align closely with the visible patent business questions.

5. fastQA V2 currently behaves more like graph-assisted retrieval than graph-native reasoning for many non-template questions.

6. Patent timeout handling is stronger and simpler on the main path.
   All patent queries run through a direct Neo4j `Query(timeout=...)`. fastQA V2 non-template execution currently does not enforce timeout in the same way.

7. Patent has a stronger null/stub data policy.
   Stub patents are explicitly filtered before answer rendering. fastQA focuses more on DOI sanitation and string cleanup than content-state filtering.

8. fastQA has stronger downstream interoperability.
   The `GraphRagPayload` is designed to influence stage1, stage2, stage4, and cache behavior. Patent graph data is not reused that way.

## 12. Practical takeaway

If the goal is direct structured patent QA, the patent graph_kb is the cleaner implementation.

If the goal is a graph layer that can participate in a larger generation pipeline, fastQA V2 is the more extensible architecture, but its current non-template execution is still closer to guarded graph evidence retrieval than to full semantic graph querying.

## 13. Suggested follow-up exploration

If you want the next pass, the highest-value follow-ups would be:

1. compare patent graph templates against the actual patent ingestion pipeline to recover the authoritative node/property contract
2. compare fastQA V2 design docs against implementation gaps line-by-line, especially around true `llm_cypher`, numeric constraints, and timeout enforcement
3. inspect whether the patent graph could benefit from a `graph_for_rag` mode similar to fastQA, or whether exact final-answer behavior is the better fit for patent QA
