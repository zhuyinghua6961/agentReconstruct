# Classic Pipeline Retirement Acceptance

## Purpose

This document defines the acceptance gates for retiring the legacy knowledge-graph pipeline after `graph_kb` V2 reaches production readiness.

`Classic Pipeline Retirement Acceptance` is a Phase 5 hardening goal, not a code-deletion trigger. V2 can ship before any classic method is removed.

## Non-Deletion Rules

1. `MaterialScienceAgent.smart_query`, `query`, `hybrid_query`, and `dual_hybrid_query` are not deleted in the same change that ships V2.
2. `query`, `hybrid_query`, and `dual_hybrid_query` move to retirement only after parity checks and a successful `shadow` phase.
3. `smart_query` remains as a compatibility reference until every delegated path has a verified V2 replacement.
4. `query_pdf_directly` has an independent keep/retire decision and is not implicitly deleted with graph routing changes.

## Rollout Phases

| Phase | Meaning |
| --- | --- |
| `shadow` | V2 runs for diagnostics/parity, but classic path remains the user-visible authority |
| `default-on` | V2 is the default path for eligible traffic, with classic fallback still available |
| `disabled-but-retained` | Classic entrypoint is no longer used in normal traffic, but code remains callable for rollback |
| `eligible-for-removal` | Parity, rollback, and operational acceptance are complete; deletion can be planned separately |

## Retirement Matrix

| Legacy Capability | Current Owner | V2 Replacement Path | Parity Test Set | Acceptance Gate | Rollout Phase |
| --- | --- | --- | --- | --- | --- |
| `MaterialScienceAgent.smart_query` | legacy material agent shim / file-route compatibility layer | `app/routers/qa.py` route dispatch + `qa_kb_service.iter_answer_events()` + graph V2 preflight | `tests/test_fastqa_kb_graph_integration.py`, end-to-end `kb_qa` smoke tests, feature-flag rollback check | All delegated `kb_qa` branches route through V2 or documented fallback, with `FASTQA_GRAPH_KB_V2_ENABLED=0` rollback verified | `disabled-but-retained` |
| `MaterialScienceAgent.query` | legacy graph direct-answer path | `route_graph_kb_v2()` direct-answer mode + `template/parametric/llm_cypher` strategy ladder | `tests/test_graph_kb_service.py`, `tests/test_graph_kb_client.py`, template-regression cases | Direct-answer parity on legacy templates, guardrail pass rate acceptable, no second Neo4j client introduced | `default-on` |
| `MaterialScienceAgent.hybrid_query` | legacy hybrid orchestration | `route_graph_kb_v2(mode=graph_for_rag)` + `GraphRagPayload` injection + generation pipeline | `tests/test_graph_kb_rag_adapter.py`, `tests/test_qa_generation_orchestrator.py`, `tests/test_generation_stage2_retrieval.py` | Graph evidence reaches Stage1/2/4, graph-seeded DOI fallback works, cache isolation verified | `default-on` |
| `MaterialScienceAgent.dual_hybrid_query` | legacy graph + vector + original-text orchestration | same V2 `graph_for_rag` path, with future original-text adapters added behind current interfaces | Task 5/6 regression suite plus future PDF/MD expansion parity tests | Existing graph-for-rag path stable without requiring Chroma/PDF extras; future extensions remain additive | `shadow` |
| `MaterialScienceAgent.query_pdf_directly` | PDF direct-read compatibility shim in file route service | keep current file-route PDF path; evaluate separately from graph routing | `pdf_qa` smoke tests, `query_pdf_directly` compatibility checks | Explicit product decision recorded: keep as permanent compatibility path or replace with new PDF-only runtime | `disabled-but-retained` |
| `CommanderAgent.analyze_question` | legacy routing rule chain | `classifier_v2` wrapper around the same rule order with tri-state output | `tests/test_graph_kb_classifier_v2.py`, legacy routing doc review, regression prompts for precise/hybrid/community/semantic | Legacy rule order preserved, tri-state mapping reviewed, shadow metrics confirm no unacceptable drift | `default-on` |

## Acceptance Checklist

Before any row moves to `eligible-for-removal`, all of the following must be true:

- Feature flags confirm V2 can be disabled cleanly: `FASTQA_GRAPH_KB_V2_ENABLED=0`.
- `graph_for_rag` still falls back safely when `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED=0`.
- Production metadata identifies `graph_pipeline_version = v2` and `neo4j_client = neo4jgraph`.
- Shadow comparisons show no unresolved correctness regression for the specific legacy capability.
- Rollback instructions are documented and tested.
- The code owner explicitly signs off on removal in a later, separate change.

## Recommended Retirement Order

1. Move `CommanderAgent.analyze_question` parity to `default-on`.
2. Move `query` and `hybrid_query` to `default-on`, then `disabled-but-retained`.
3. Keep `smart_query` as the top-level compatibility reference until all delegated branches have replacement coverage.
4. Decide `query_pdf_directly` separately based on file-route product needs.
5. Only after all above are stable should any capability become `eligible-for-removal`.
