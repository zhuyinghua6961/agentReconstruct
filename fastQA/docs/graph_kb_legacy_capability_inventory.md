# fastQA graph_kb Legacy Capability Inventory

## Source Availability

- Legacy source lookup result:
  - `commander_agent.py`: not present in the current worktree
  - legacy `main.py` with `MaterialScienceAgent`: not present in the current worktree
- Inventory basis:
  - `docs/legacy_kg_qa_pipeline.md`
  - `docs/legacy_kg_qa_routing.md`
  - `app/services/file_route_service.py`
  - `app/modules/graph_kb/client.py`
  - `app/integrations/neo4j/client.py`

## Capability Table

| Capability | Source | Status | Plan |
| --- | --- | --- | --- |
| `CommanderAgent.analyze_question` route chain | `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Preserve route order in `classifier_v2`: `HybridQueryAgent.is_hybrid_question -> precise keywords + numeric attributes -> community keywords -> semantic keywords -> graph non-numeric attributes + enumeration -> numeric-only precise -> entity fallback -> default semantic`, then map to tri-state |
| `HybridQueryAgent.is_hybrid_question` | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Keep as the highest-priority graph-capable rule and expose it through `classifier_v2` diagnostics / parity tests |
| Legacy precise keyword + numeric attribute routing | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Reuse as the first `precise` branch in `classifier_v2`; feed planner/executor instead of directly dropping to monolithic `query()` |
| Legacy community keyword branch | `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Preserve branch recognition in `classifier_v2`, but map it to V2 `skip_graph` because legacy runtime already downgraded `community` to broad semantic search rather than graph execution |
| Legacy semantic keyword priority | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Preserve priority above graph enumeration routing; use it to produce `graph_for_rag` or `skip_graph` depending on whether graph evidence is materially useful |
| Legacy graph non-numeric attribute + enumeration routing | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Carry into `classifier_v2` and planner so list/filter questions can still route to graph execution |
| Legacy numeric-attribute-only `precise` routing | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Keep as a distinct rule before entity fallback; record `matched_rule = "numeric_attribute_only"` in diagnostics |
| Legacy entity keyword fallback | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Preserve as the last graph-biased fallback before default semantic |
| Legacy `query` sequence: `_generate_cypher_query -> _validate_cypher_query -> _execute_cypher_query -> _synthesize_answer` | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Split into V2 `planner_v2 -> query_strategy -> guardrail -> executor_v2 -> canonicalizer -> direct_renderer/rag_adapter`, preserving read-only Cypher generation + validation + execution semantics |
| Legacy `hybrid_query` semantics | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Preserve the “graph filter first, semantic analysis second” concept, but express outputs as `GraphRagPayload` instead of direct monolithic answer synthesis |
| Legacy `dual_hybrid_query` semantics | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md` | `MIGRATE` | Preserve the dual-path idea of graph + semantic retrieval, but adapt it to the existing generation pipeline as `graph_for_rag` rather than restoring the full old runtime |
| Legacy DOI direct read boundary: `query_pdf_directly` | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md`, `app/services/file_route_service.py` | `MIGRATE` | Keep as an explicit compatibility boundary; do not delete implicitly during graph routing rollout |
| Legacy `smart_query` monolithic entrypoint | `docs/legacy_kg_qa_pipeline.md`, `docs/legacy_kg_qa_routing.md`, `app/services/file_route_service.py` | `REPLACE` | Replace with `app/routers/qa.py` + `graph_kb.service` + `qa_kb` integration, while retaining compatibility shells and retirement gates |
| Current fastQA regex/binary classifier (`try_graph` / `skip`) | `app/modules/graph_kb/classifier.py` | `REPLACE` | Replace with Commander-compatible `classifier_v2`; keep old classifier callable behind feature flags until V2 rollout is accepted |
| Current fastQA hardcoded template planner | `app/modules/graph_kb/client.py` | `MIGRATE` | Keep current 5 templates as fallback capability inside V2 `query_strategy` and planner selection |
| Current fastQA graph answer rendering | `app/modules/graph_kb/service.py` | `MIGRATE` | Reuse normalization / rendering helpers where useful, but move structured evidence shaping into `canonicalizer.py` and `direct_renderer.py` |
| Current Neo4j connection bootstrap: `app/integrations/neo4j/client.py -> bootstrap_neo4j() -> Neo4jGraph` | `app/integrations/neo4j/client.py`, `app/core/runtime.py` | `MIGRATE` | Keep as the single canonical connected client; all V2 execution must receive this bootstrap result and operate through `graph.query(...)` / `graph._driver.execute_query(...)` |
| Introduce a second parallel fastQA Neo4j connection client | Proposed future change only | `REPLACE` | Do not add; keep `app/modules/graph_kb/client.py` focused on planning/execution helpers, not connection bootstrap |

## Migration Notes

- The current worktree does not contain the original legacy `MaterialScienceAgent` or `CommanderAgent` source files, so parity work must anchor on the two legacy docs plus the current fastQA compatibility shells.
- The old `community` branch must be preserved as a recognized route even though its runtime behavior degrades to broad semantic search.
- The current 5 hardcoded graph templates remain a required fallback during V2 rollout.
- The canonical Neo4j client is `Neo4jGraph` via `bootstrap_neo4j()` using `NEO4J_URL`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD`.
