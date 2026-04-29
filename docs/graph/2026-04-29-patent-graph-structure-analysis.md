# Patent Graph Structure Analysis

> Date: 2026-04-29
> Scope: read-only analysis of the current `patent` graph configuration, runtime graph pipeline, and Neo4j data shape. No code changes are included in this document.
> Security note: local secrets were observed only to confirm connectivity. Passwords, API keys, and tokens are intentionally omitted.

## 1. Why This Document Exists

The immediate product goal is to make the `patent` graph-related experience approach the effect recently restored in `fastQA`: questions should be able to route to graph-only direct answers, normal vector/RAG answers, or graph-seeded hybrid answers without breaking existing vector database behavior.

This document records the current patent-side reality before implementation:

- where patent graph connection settings come from;
- whether the patent Neo4j graph is reachable;
- what labels, relationships, and properties are actually present in the patent graph;
- how patent currently routes a KB question through graph, vector/RAG, or a mixed graph-for-RAG path;
- what is already close to fastQA and what still needs improvement.

The document does not propose code edits line-by-line. It is an evidence base for the later patent graph parity spec.

## 2. Configuration Sources

Patent configuration is split across shared config, local overrides, and Python settings.

### 2.1 Shared Resource Config

The deployed shared config file is:

- `resource/config/services/patent/config.shared.env`

Current observation:

- it contains patent runtime, Redis, durable mode, embedding, OpenAI, LLM HTTP pool, and planning hot-pool settings;
- it does not currently declare the graph-specific `PATENT_GRAPH_KB_*` and `PATENT_NEO4J_*` settings.

This is a documentation/configuration gap: graph settings exist in code and local `.env`, but the resource shared config does not make the graph capability visible to operators.

### 2.2 Example Config

The example file is:

- `patent/config.shared.env.example`

This example does document graph settings:

- `PATENT_GRAPH_KB_ENABLED=<deprecated-disable-value>`
- `PATENT_GRAPH_KB_V2_ENABLED=false`
- `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED=false`
- `PATENT_GRAPH_KB_TIMEOUT_MS=3000`
- `PATENT_GRAPH_KB_MAX_ROWS=20`
- `PATENT_GRAPH_KB_QUERY_LOGGING=false`
- `PATENT_NEO4J_URL=bolt://127.0.0.1:8687`
- `PATENT_NEO4J_DATABASE=neo4j`
- `PATENT_NEO4J_USERNAME=neo4j`
- `PATENT_NEO4J_PASSWORD`: local secret placeholder

### 2.3 Local Patent `.env`

The local patent `.env` contains real graph credentials and enables graph features. Secrets are not repeated here.

Effective non-secret observations from the local override:

- graph KB is enabled;
- graph KB v2 is enabled;
- graph-to-RAG injection is enabled;
- Neo4j endpoint is the local Bolt endpoint on port `8687`;
- Neo4j database is `neo4j`;
- Neo4j username is configured;
- Neo4j password is configured.

### 2.4 Python Settings

The Python config lives in:

- `patent/config.py`

Relevant shape:

- `PatentGraphSettings` defines `enabled`, `v2_enabled`, `rag_injection_enabled`, `neo4j_url`, `neo4j_username`, `neo4j_password`, `neo4j_database`, `timeout_ms`, `max_rows`, and `query_logging`.
- Defaults in `get_settings()` keep graph disabled unless environment overrides enable it.
- Default Neo4j URL is `bolt://127.0.0.1:8687`.
- Default Neo4j database is `neo4j`.
- Default query timeout is `3000 ms`.
- Default max rows is `20`.
- `query_logging` is present as a setting, but the current graph service path does not appear to use it as a comprehensive per-step trace switch.

## 3. Connectivity Result

Read-only Neo4j connectivity was verified from the `agent` conda environment using the configured local endpoint and configured credentials.

Observed result:

- Neo4j Python driver is available in the `agent` environment.
- Bolt connectivity succeeds.
- `RETURN 1 AS ok` succeeds against database `neo4j`.
- The database contains a large populated patent graph.

No credential values should be copied into docs or logs beyond local operational debugging.

## 4. Current Patent Runtime Wiring

Patent graph support is not an external sidecar. It is wired into the patent backend startup and the patent KB service.

### 4.1 Application Bootstrap

Relevant file:

- `patent/server_fastapi/app.py`

Startup behavior:

1. The app reads `app.state.settings.graph_kb`.
2. If graph KB is enabled, startup calls `bootstrap_patent_neo4j_client(...)`.
3. The bootstrap client verifies Neo4j connectivity and records component status under `patent_graph_kb`.
4. The app creates `PatentExecutor` with graph services and settings:
   - legacy graph service: `try_patent_graph_kb_answer`;
   - v2 graph router: `route_patent_graph_kb_v2`;
   - Neo4j client;
   - `graph_kb_enabled`;
   - `graph_kb_v2_enabled`;
   - `graph_kb_rag_injection_enabled`;
   - `graph_kb_max_rows`;
   - `graph_kb_timeout_ms`.

### 4.2 Neo4j Client

Relevant file:

- `patent/server/patent/graph_kb/neo4j_client.py`

Behavior:

- `bootstrap_patent_neo4j_client(...)` creates a Neo4j driver, verifies connectivity, probes the configured database, and returns a `PatentNeo4jClient`.
- If import/connectivity/probe fails, the client is marked degraded instead of crashing the whole backend.
- `PatentNeo4jClient.query(...)` uses Neo4j `Query` with a per-query timeout derived from `timeout_ms`.
- Query rows are normalized into `list[dict]`.
- Neo4j timeout errors are converted to Python `TimeoutError`.

This is operationally sane, but the default `3000 ms` timeout may be tight for broad graph questions over this graph size.

### 4.3 Patent Executor

Relevant file:

- `patent/server/patent/executor.py`

Behavior:

- `PatentExecutor` receives graph dependencies from FastAPI startup.
- It constructs `PatentKbService` with those graph dependencies.
- It dispatches file routes (`pdf_qa`, `tabular_qa`, `hybrid_qa`) separately from KB route.
- For non-file routes, it dispatches into the KB service.

Important implication:

- Gateway/file-route `hybrid_qa` is not the same thing as graph+vector hybrid.
- The graph-vs-vector-vs-hybrid decision currently happens inside the patent KB route, not in gateway.

### 4.4 Patent KB Graph Preflight

Relevant file:

- `patent/server/patent/kb_service.py`

Current execution model:

1. `PatentKbService.run(...)` only attempts graph preflight when `request.route == "kb_qa"`.
2. If graph is disabled or the Neo4j client is missing, it goes directly to the normal staged generation/RAG flow.
3. If v2 graph is enabled, it calls `route_patent_graph_kb_v2(...)`.
4. If v2 returns `direct_answer`, the KB service returns the graph answer directly.
5. If v2 returns `graph_for_rag`:
   - when graph-to-RAG injection is enabled, it writes `conversation_context["graph_kb"]`;
   - when injection is disabled, it records downgrade metadata and continues without graph context.
6. If v2 returns `skip_graph`, it proceeds through the normal staged generation/RAG flow.
7. If v2 is disabled, it falls back to the older template graph service when configured.

This already matches the same core tri-state contract used by the fastQA graph path:

| Patent mode | Meaning |
| --- | --- |
| `direct_answer` | Neo4j evidence is considered safe to render directly and return without vector/RAG synthesis. |
| `graph_for_rag` | Neo4j evidence is converted into structured hints/facts and injected into the normal RAG pipeline. |
| `skip_graph` | The question continues through the normal patent staged generation/vector path without graph context. |

## 5. Current Patent Graph Module Layout

Graph code lives under:

- `patent/server/patent/graph_kb/`

Important files:

| File | Role |
| --- | --- |
| `models.py` | Dataclasses/contracts for decisions, plans, raw execution, evidence bundles, RAG payloads, and direct answers. |
| `neo4j_client.py` | Neo4j bootstrap, health/degraded state, query execution, timeout normalization. |
| `classifier.py` | Older rule classifier for legacy template graph path. |
| `classifier_v2.py` | Current tri-state classifier for direct graph, graph-for-RAG, or skip graph. |
| `client.py` | Legacy Cypher template planner/executor and parametric query candidate builder. |
| `planner_v2.py` | v2 plan builder that chooses template or parametric strategy. |
| `query_strategy.py` | Strategy selection logic. |
| `executor_v2.py` | Guarded execution for v2 plans. |
| `guardrail.py` | Cypher guardrail against non-read, unallowlisted, or risky query shapes. |
| `schema_registry.py` | Allowlisted labels, relationships, and logical fields. |
| `canonicalizer.py` | Converts raw rows into graph evidence bundle, facts, candidates, constraints, and direct-answer eligibility. |
| `rag_adapter.py` | Converts evidence bundle into Stage1/Stage2/Stage4 graph payload. |
| `direct_renderer.py` | Renders v2 direct answers. |
| `rendering.py` | Renders legacy template direct answers. |
| `service.py` | Public graph service entry points: legacy direct service and v2 tri-state router. |

## 6. Current V2 Graph Pipeline

Current patent graph v2 flow is:

```text
question
  -> classify_patent_graph_question_v2
  -> build_patent_graph_query_plan_v2
  -> execute_patent_prepared_query
  -> canonicalize_patent_graph_rows
  -> build_patent_graph_rag_payload
  -> direct render OR graph_for_rag OR skip_graph
```

### 6.1 Classification

Relevant file:

- `patent/server/patent/graph_kb/classifier_v2.py`

Classifier characteristics:

- It is rule-based, not LLM-based.
- It extracts patent IDs from the question.
- It recognizes graph-specific hints such as process steps, raw materials, experimental tables, performance data, technical problems, solutions, application scenarios, invention points, protection scope, citations, atmospheres, and embodiments.
- It recognizes hybrid/explanatory hints such as why/how/advantage/improvement/comparison/difference/trend/summary/commonality/applicability.
- It routes DOI-style questions to `skip_graph` because this patent graph is patent-ID based, not DOI based.
- It downgrades questions with file context from direct graph answers into graph-for-RAG when needed.
- It uses three modes:
  - `direct_answer`;
  - `graph_for_rag`;
  - `skip_graph`.

Important behavioral examples:

| Question shape | Likely mode | Reason |
| --- | --- | --- |
| `CN100355122C 的工艺步骤是什么？` | `direct_answer` | Patent ID + explicit structured process intent can be answered from graph rows. |
| `CN100355122C 用了哪些原料？` | `direct_answer` | Patent ID + material-role path. |
| `CN100355122C 的技术问题和技术方案是什么？` | `direct_answer` | Patent ID + problem/solution path. |
| `比较 CN... 和 CN... 的工艺路线差异` | `graph_for_rag` | Multi-patent compare usually needs graph evidence plus synthesis. |
| `为什么这种工艺能提高性能？` | `skip_graph` or `graph_for_rag` if anchored | Broad why/how without graph anchor should stay vector/RAG; anchored variants can use graph hints. |
| `10.xxxx/... 这篇专利是什么？` | `skip_graph` | DOI is not the patent graph key. |

### 6.2 Planning

Relevant files:

- `patent/server/patent/graph_kb/planner_v2.py`
- `patent/server/patent/graph_kb/query_strategy.py`
- `patent/server/patent/graph_kb/client.py`

Planner characteristics:

- The planner is rule/template/parametric based.
- It does not currently use an LLM planner.
- Strategy selection prefers a legacy template plan when one exists.
- If no legacy template exists but parametric query candidates exist, it chooses `parametric`.
- If neither exists, planning returns no executable graph plan and the route becomes `skip_graph`.

Supported legacy template paths include:

| Template | Capability |
| --- | --- |
| `lookup_patent_by_id` | Patent basic metadata lookup by patent ID. |
| `list_patent_process_steps` | Process step listing for one patent. |
| `list_patent_material_roles` | Material role and candidate material listing for one patent. |
| `list_patent_experiment_tables` | Experimental table, row, and measurement listing for one patent. |
| `list_patent_problem_solution` | Technical problem, solution, and application scenario for one patent. |
| `list_patent_inventive_scope` | Inventive points, performance facts, protection scope, and claim step labels for one patent. |
| `list_patent_citations` | Outgoing patent citations. |
| `list_patents_by_ipc` | Patent listing by IPC code. |
| `list_patents_by_applicant` | Patent listing by applicant organization. |

Supported parametric paths include:

| Parametric path | Capability |
| --- | --- |
| `list_patents_by_inventor` | List patents by inventor. |
| `list_patents_by_agency` | List patents by agency. |
| `list_patents_by_ipc_subclass` | List patents by IPC subclass. |
| `count_patents_by_ipc` | Count patents by IPC code. |
| `count_patents_by_applicant` | Count patents by applicant. |
| `count_patents_by_inventor` | Count patents by inventor. |
| `compare_patents_process_steps` | Compare process steps across multiple patent IDs. |
| `compare_patents_material_roles` | Compare material roles across multiple patent IDs. |
| `compare_patents_problem_solution` | Compare problem/solution across multiple patent IDs. |
| `list_patent_atmospheres` | List atmosphere conditions for a patent. |
| `list_patent_embodiment_insights` | List embodiment insights for a patent. |

### 6.3 Execution And Guardrail

Relevant files:

- `patent/server/patent/graph_kb/executor_v2.py`
- `patent/server/patent/graph_kb/guardrail.py`
- `patent/server/patent/graph_kb/schema_registry.py`

Execution characteristics:

- v2 executes prepared/template Cypher only.
- It inspects planned Cypher before execution.
- The schema registry allowlists known labels and relationships.
- It records a lightweight execution trace.
- It limits rows through configured `max_rows`.
- It uses the Neo4j client timeout.

This is similar in shape to fastQA's safer v2 graph path: no arbitrary free-form Cypher generation is used in the current observed path.

### 6.4 Canonicalization

Relevant file:

- `patent/server/patent/graph_kb/canonicalizer.py`

Canonicalization output includes:

- patent candidates;
- IPC candidates;
- organization candidates;
- inventor candidates;
- graph facts;
- constraints;
- render slots;
- direct-answer eligibility;
- diagnostics.

Current direct-answer eligibility is conservative:

- legacy template results can be direct answerable when rows are usable;
- safe parametric paths such as list/count by inventor, agency, IPC, applicant, atmosphere, and embodiment insights can be direct answerable;
- compare/hybrid-like paths generally go through graph-for-RAG.

### 6.5 RAG Payload

Relevant file:

- `patent/server/patent/graph_kb/rag_adapter.py`

The graph payload contains stage-specific fields:

| Payload field | Purpose |
| --- | --- |
| `mode` | `graph_for_rag` or related graph mode. |
| `cache_fingerprint` | Stable graph-context fingerprint for cache keys. |
| `stage1_context_block` | Concise graph context injected into Stage1 planning prompt/context. |
| `stage2_patent_candidates` | Patent IDs that can seed retrieval. |
| `stage2_constraints` | Structured constraints from graph execution. |
| `stage2_entity_hints` | Entity hints such as patents, IPCs, organizations, inventors. |
| `stage4_fact_block` | Structured graph facts used during final synthesis. |
| `stage4_graph_candidate_patent_ids` | Candidate patent IDs exposed to Stage4 synthesis. |
| `diagnostics` | Graph routing/execution diagnostics. |

## 7. Stage Integration

Patent graph-for-RAG injection is already connected to the staged patent pipeline.

### 7.1 Stage1 Planning

Relevant file:

- `patent/server/patent/stages/planning.py`

Behavior:

- `conversation_context["graph_kb"]` is formatted into the Stage1 context.
- Graph mode, patent candidates, entity hints, constraints, and graph facts can be exposed to planning.
- If graph context exists, retrieval claims can be seeded from graph candidates/hints/facts.
- If normal planner output is unavailable or malformed, graph-seeded claims still provide a fallback retrieval seed.

### 7.2 Cache Keys

Relevant file:

- `patent/server/patent/cache_keys.py`

Behavior:

- Graph context is normalized into cache keys.
- The graph fingerprint helps prevent reusing stale Stage1/Stage2 results across different graph evidence.

### 7.3 Stage4 Synthesis And Answer Builder

Relevant files:

- `patent/server/patent/stages/synthesis.py`
- `patent/server/patent/answering.py`

Behavior:

- Stage4 receives `graph_kb` context when injected.
- Stage4 copies graph mode and graph fingerprint into synthesis context and metadata.
- The answer-building layer reads graph facts and graph candidate patent IDs and can incorporate them into the final evidence context.

## 8. Observed Neo4j Schema

The patent graph is a normalized patent-domain graph, not a field-bucket graph like the inspected fastQA Neo4j database.

The dominant pattern is:

```cypher
(:Patent)-[:CLASSIFIED_AS]->(:IPC)
(:Patent)-[:IN_IPC_SUBCLASS]->(:IPCPrefix)
(:Patent)-[:HAS_APPLICANT]->(:Organization)
(:Patent)-[:HAS_AGENCY]->(:Organization)
(:Patent)-[:HAS_INVENTOR]->(:Person)
(:Patent)-[:HAS_PROCESS_STEP]->(:ProcessStep)
(:ProcessStep)-[:INSTANCE_OF]->(:StepTemplate)
(:ProcessStep)-[:NEXT_STEP]->(:ProcessStep)
(:Patent)-[:HAS_MATERIAL_ROLE]->(:MaterialRole)
(:MaterialRole)-[:OPTION_INCLUDES]->(:Material)
(:Patent)-[:HAS_EXPERIMENT_TABLE]->(:ExperimentTable)
(:ExperimentTable)-[:HAS_ROW]->(:TableRow)
(:TableRow)-[:HAS_MEASUREMENT]->(:Measurement)
(:Patent)-[:ADDRESSES]->(:TechnicalProblem)
(:Patent)-[:PROPOSES]->(:TechnicalSolution)
(:Patent)-[:HAS_APPLICATION_SCENARIO]->(:ApplicationScenario)
(:Patent)-[:HAS_INVENTIVE_POINT]->(:InventivePoint)
(:Patent)-[:HAS_PERFORMANCE_FACT]->(:PerformanceFact)
(:Patent)-[:PROTECTION_INCLUDES]->(:ProtectionScope)
(:Patent)-[:CLAIM_INCLUDES_STEP]->(:ClaimStepLabel)
(:Patent)-[:CITES_PATENT]->(:Patent)
(:Patent)-[:USES_ATMOSPHERE]->(:Atmosphere)
(:Patent)-[:HAS_EMBODIMENT_INSIGHT]->(:EmbodimentInsight)
```

There is also a large co-occurrence layer:

```cypher
(:Material)-[:CO_OCCURS_WITH]->(:StepTemplate)
```

This co-occurrence layer is currently visible in the graph but is not a first-class query family in the observed patent v2 planner.

## 9. Observed Node Counts

Approximate label counts from read-only Neo4j inspection:

| Label | Count |
| --- | ---: |
| `Measurement` | 698,092 |
| `TableRow` | 128,310 |
| `InventivePoint` | 128,003 |
| `PerformanceFact` | 92,418 |
| `ProcessStep` | 64,091 |
| `MaterialRole` | 60,670 |
| `StepTemplate` | 59,538 |
| `ClaimStepLabel` | 46,327 |
| `Patent` | 39,517 |
| `ProtectionScope` | 38,107 |
| `Material` | 37,705 |
| `Person` | 23,661 |
| `ExperimentTable` | 15,365 |
| `Atmosphere` | 13,530 |
| `TechnicalSolution` | 12,489 |
| `TechnicalProblem` | 12,488 |
| `ApplicationScenario` | 12,487 |
| `Organization` | 5,206 |
| `IPC` | 2,956 |
| `EmbodimentInsight` | 1,550 |
| `IPCPrefix` | 189 |

Interpretation:

- The graph is large enough to support patent-level structured QA.
- The largest evidence source is measurement/table data.
- Process, material-role, inventive-point, performance-fact, and claim/protection-scope data are heavily populated.
- Organization/inventor/IPC paths are suitable for listing/counting/filtering.
- The graph appears especially strong for battery/materials-style patent analysis where process, raw materials, measurements, and performance facts matter.

## 10. Observed Relationship Counts

Approximate relationship counts:

| Relationship | Count |
| --- | ---: |
| `CO_OCCURS_WITH` | 1,014,076 |
| `HAS_MEASUREMENT` | 698,092 |
| `OPTION_INCLUDES` | 205,366 |
| `HAS_ROW` | 128,310 |
| `HAS_INVENTIVE_POINT` | 128,003 |
| `HAS_PERFORMANCE_FACT` | 92,418 |
| `HAS_PROCESS_STEP` | 64,091 |
| `INSTANCE_OF` | 64,091 |
| `HAS_MATERIAL_ROLE` | 60,670 |
| `HAS_INVENTOR` | 60,055 |
| `CLASSIFIED_AS` | 59,171 |
| `NEXT_STEP` | 51,809 |
| `CITES_PATENT` | 48,377 |
| `CLAIM_INCLUDES_STEP` | 46,327 |
| `PROTECTION_INCLUDES` | 38,107 |
| `IN_IPC_SUBCLASS` | 20,268 |
| `HAS_APPLICANT` | 15,842 |
| `HAS_EXPERIMENT_TABLE` | 15,365 |
| `USES_ATMOSPHERE` | 13,530 |
| `PROPOSES` | 12,489 |
| `ADDRESSES` | 12,488 |
| `HAS_APPLICATION_SCENARIO` | 12,487 |
| `HAS_AGENCY` | 12,345 |
| `HAS_EMBODIMENT_INSIGHT` | 1,550 |

Interpretation:

- Measurement/table paths dominate and are likely valuable for evidence extraction, but can be expensive if queried broadly.
- `CO_OCCURS_WITH` is the largest relation and could support graph exploration, process-material association, or recommendation-like questions, but it needs careful query bounds.
- `NEXT_STEP` gives enough density to support ordered process-chain answers.
- Citation paths exist and can support direct citation listing and possibly citation-neighborhood hybrid retrieval.

## 11. Observed Properties

Common property keys include:

- graph identity/normalization: `gid`, `_labels`, `canonical_key`, `stub`;
- patent metadata: `patent_id`, `title`, `abstract`, `application_date`, `publication_date`, `patent_type`, `legal_status`, `ipc_main`, `source_file`;
- process: `name`, `operation`, `order`, `params_json`, `preferred`, `template_key`, `process_note`;
- material/role: `role`, `type`, `label`, `ratio`, `material_type`, `note`, `options`;
- tables/measurements: `table_index`, `table_title`, `columns_json`, `row_index`, `sample_label`, `metric_key`, `value_raw`, `unit_hint`;
- text facts: `text`, `category`, `conclusion`, `insight_type`;
- IPC: `code`, `subclass`.

Representative observed node shapes:

- `Patent` node has patent ID, title, abstract, patent type, legal status, dates, IPC main code, and source file.
- `ProcessStep` has name, operation, order, preferred values, and structured params JSON.
- `MaterialRole` has role/type/ratio/note and connects to candidate `Material` nodes.
- `ExperimentTable` and `TableRow` carry table title, row labels, and measurement links.
- `Measurement` carries metric key, raw value, and unit hint.
- `Atmosphere` carries options and preferred value.

## 12. Schema Registry Coverage

The code-level schema registry already matches the observed capitalized patent graph model.

Allowed labels include:

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
- `Atmosphere`
- `EmbodimentInsight`

Allowed relationships include:

- `CLASSIFIED_AS`
- `IN_IPC_SUBCLASS`
- `HAS_APPLICANT`
- `HAS_AGENCY`
- `HAS_INVENTOR`
- `HAS_PROCESS_STEP`
- `INSTANCE_OF`
- `NEXT_STEP`
- `USES_ATMOSPHERE`
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
- `HAS_EMBODIMENT_INSIGHT`

Important note:

- The same Neo4j database also appears to contain constraints/indexes for unrelated or older graph labels. The patent runtime should continue targeting the capitalized patent-domain labels above unless a later ingestion/schema review proves otherwise.

## 13. Current Direct Rendering Coverage

Direct rendering is split between legacy template rendering and v2 parametric rendering.

### 13.1 Legacy Template Direct Rendering

Relevant file:

- `patent/server/patent/graph_kb/rendering.py`

Current direct rendered answer families:

- patent metadata lookup;
- process step listing;
- material role listing;
- experimental table and measurement listing;
- technical problem/solution/application scenario;
- inventive point/performance fact/protection scope/claim step labels;
- outgoing citations;
- patents by IPC;
- patents by applicant.

The renderer filters stub-only rows for direct answers to avoid presenting incomplete patent nodes as authoritative answers.

### 13.2 Parametric Direct Rendering

Relevant file:

- `patent/server/patent/graph_kb/direct_renderer.py`

Current direct rendered parametric paths:

- list patents by inventor;
- list patents by agency;
- list patents by IPC subclass;
- count patents by IPC;
- count patents by applicant;
- count patents by inventor;
- list patent atmospheres;
- list patent embodiment insights.

Unsupported parametric paths fall back from direct rendering when they are not safe or not implemented.

## 14. Comparison With fastQA Graph Experience

Patent is already closer to fastQA's current architecture than expected. It has:

- a graph v2 module;
- startup health/degraded state;
- a Neo4j client abstraction;
- a tri-state graph router;
- direct answer rendering;
- graph-for-RAG payload construction;
- Stage1, cache, and Stage4 graph context integration;
- guardrails and a schema registry.

The main gap is not the existence of the graph path. The gap is breadth, observability, and route expressiveness.

### 14.1 What Is Already Similar

| Capability | fastQA current shape | patent current shape |
| --- | --- | --- |
| Service-level graph enable flags | Present | Present |
| Graph v2 enabled flag | Present | Present |
| Graph-to-RAG injection flag | Present | Present |
| Neo4j bootstrap/degraded status | Present | Present |
| Internal tri-state routing | Present | Present |
| Direct graph answers | Present | Present |
| Graph-for-RAG injection | Present | Present |
| Schema registry/guardrail | Present | Present |
| Stage1/Stage4 graph context | Present | Present |

### 14.2 What Is Weaker Than fastQA After Recent Work

| Gap | Current patent state | Impact |
| --- | --- | --- |
| Detailed graph logs | Patent logs graph at executor/health level, but not every classify/plan/execute/canonicalize/RAG step with rich diagnostics. | Hard to confirm route correctness from backend logs during live testing. |
| Four-route language | Patent has tri-state modes but does not expose fastQA-style `precise` / `semantic` / `hybrid` / `community` route-family behavior as clearly. | Product experience is harder to reason about and test by scenario. |
| Planner breadth | Planner is template/parametric only; no broader graph query families for co-occurrence, citation neighborhoods, measurement ranking, or process-material association. | Many graph-rich patent questions still skip graph or only use narrow evidence. |
| Measurement analytics | Graph has 698k measurement nodes, but current planner mainly exposes experiment tables by patent, not broad metric filtering/ranking. | Questions like “哪些专利的容量最高/性能最好/指标超过阈值” are not fully covered. |
| Co-occurrence graph | Graph has over 1M `CO_OCCURS_WITH` relations, but current planner does not appear to use them. | Association/network/community-like patent questions cannot use the strongest relationship layer. |
| Citation graph | Direct outgoing citation list exists, but citation-neighborhood or influence-style hybrid paths are not first-class. | Citation relationship questions remain narrow. |
| Config visibility | `resource/config/services/patent/config.shared.env` lacks graph env declarations. | Runtime graph state may depend on local `.env` and be invisible to shared deployment config. |
| Timeout budget | Default graph timeout is 3 seconds. | Broad queries over large measurement/co-occurrence paths may degrade or timeout. |

## 15. Current Ability By Question Type

### 15.1 Strong Direct-Graph Cases

These are the best-covered cases today:

- “`CN100355122C` 的标题/摘要/申请人/发明人/IPC 是什么？”
- “`CN100355122C` 的工艺步骤是什么？”
- “`CN100355122C` 用了哪些原料/材料角色？”
- “`CN100355122C` 的实验表格和测量数据有哪些？”
- “`CN100355122C` 解决了什么技术问题，提出了什么技术方案？”
- “`CN100355122C` 的发明点和保护范围是什么？”
- “`CN100355122C` 引用了哪些专利？”
- “某个 IPC/申请人/发明人下面有哪些专利？”
- “某个 IPC/申请人/发明人下面有多少专利？”
- “`CN100355122C` 的气氛条件有哪些？”
- “`CN100355122C` 的实施例洞察有哪些？”

### 15.2 Strong Graph-for-RAG Cases

These should usually use graph evidence but still need synthesis:

- “比较 `CN...` 和 `CN...` 的工艺步骤差异。”
- “比较 `CN...` 和 `CN...` 的原料体系差异。”
- “比较 `CN...` 和 `CN...` 分别解决的技术问题和方案。”
- “结合图谱总结某个专利族/IPC/申请人的技术路线。”
- “某个专利的发明点和性能事实能说明什么优势？”
- “某个工艺为什么可能改善性能？” if anchored by patent ID, IPC, applicant, material, or process.

### 15.3 Current Skip-Graph / Vector-First Cases

These should normally remain vector/RAG-first unless a graph anchor can be extracted:

- broad “为什么/如何评价/趋势/综述” questions without patent ID or structured entity;
- DOI-based questions;
- pure literature-style semantic questions;
- questions requiring full-text legal interpretation beyond graph facts;
- questions requiring user-selected PDF/table content.

This preserves the existing vector database behavior: graph is a preflight/augmentation layer, not a replacement for semantic retrieval.

## 16. Feasibility Assessment For “Patent Like fastQA”

It is feasible to make patent behave much more like the fastQA graph experience without reverting to old code shape.

The recommended direction is:

1. Keep gateway responsible for coarse file-vs-KB routing.
2. Keep patent `kb_qa` responsible for internal graph/vector/hybrid decisions.
3. Preserve the current tri-state execution contract:
   - `direct_answer`;
   - `graph_for_rag`;
   - `skip_graph`.
4. Add an explicit route-family concept for patent, analogous to fastQA:
   - `precise`: structured patent graph lookup/list/count/filter/ranking;
   - `semantic`: normal patent vector/RAG path;
   - `hybrid`: graph-constrained patent RAG;
   - `community` or `network`: citation, co-occurrence, process-material association, applicant/inventor/IPC neighborhoods.
5. Expand planner/query families around the graph's actual strengths:
   - measurement metric filtering/ranking over `Measurement`;
   - process chain exploration over `ProcessStep` and `NEXT_STEP`;
   - material-role and material-template associations;
   - `CO_OCCURS_WITH` association queries;
   - citation neighborhood queries;
   - applicant/inventor/agency/IPC portfolio summaries;
   - patent comparison families that return normalized facts for synthesis.
6. Add detailed logs at every graph step so live gateway tests can show:
   - route family;
   - tri-state mode;
   - strategy;
   - plan/template/path ID;
   - Cypher family, not secret params;
   - row count;
   - canonical facts/candidates count;
   - direct-render vs RAG downgrade reason;
   - graph injection status.

## 17. Implementation Risks To Keep In Mind Later

No code was changed for this analysis, but future implementation should be careful about:

- not disturbing patent vector retrieval, Stage1 planning, Stage2 evidence loading, or Stage4 synthesis;
- keeping graph direct answers conservative;
- never running unbounded measurement/co-occurrence queries;
- preserving stub filtering in direct answers;
- avoiding arbitrary LLM-generated Cypher unless guarded extremely tightly;
- redacting graph credentials in logs;
- using query timeouts and `LIMIT` consistently;
- ensuring cache fingerprints change when graph evidence changes;
- making gateway tests cover file routes separately from graph KB routes.

## 18. Summary

Patent already has a real Neo4j-backed graph QA path. It is not an empty shell:

- the graph is connected and populated;
- the code has v2 classify/plan/execute/canonicalize/RAG/direct-render stages;
- direct graph answers and graph-for-RAG injection both exist;
- the graph schema is rich enough for much broader capabilities than currently exposed.

The main work needed to reach fastQA-like effect is to broaden patent graph query families, add explicit route-family semantics, strengthen observability, and expose graph settings clearly in deployment config. The existing vector/RAG path can remain the default semantic fallback, so graph improvements do not need to interfere with current vector database behavior.
