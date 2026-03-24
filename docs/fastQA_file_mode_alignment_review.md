# fastQA File-Mode Alignment Review

## Scope

This document reviews current `fastQA` file-mode behavior against the legacy `fastapi-version` implementation.

In scope:

- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`
- request adaptation
- file-context routing
- stream/event contract
- direct test coverage related to the above

Out of scope:

- `kb_qa`
- `highThinkingQA`
- `gateway` implementation changes
- code changes of any kind

Baseline source of truth:

- legacy: `/home/cqy/worktrees/fastapi-version/backend`
- current: `/home/cqy/worktrees/highThinking/fastQA`

## Executive Summary

### Overall

Current `fastQA` is not uniformly incomplete. The three file-oriented branches are in three different states:

- `tabular_qa`: close to legacy core behavior
- `hybrid_qa`: core service logic is close, but active route behavior is not identical
- `pdf_qa`: module-level logic is mostly preserved, but live route semantics and surrounding runtime contract are not fully aligned

### Estimated Parity

These are engineering estimates based on code-path review, not runtime benchmarking:

| Area | Estimated parity | Notes |
|---|---:|---|
| `qa_tabular` core modules | 95%+ | planner / executor / renderer / loader are effectively unchanged |
| `hybrid_qa` service behavior | 85% | service is close, but active route wiring differs from legacy |
| `pdf_qa` module behavior | 80% | core service/streaming/engine are close |
| `pdf_qa` live route parity | 60-70% | missing conversation-backed file resolution and live KB verification |
| route + payload + SSE contract parity | 65-75% | transport and enrichment semantics changed materially |
| test coverage parity | 40-55% | current file-mode tests are materially thinner |

### Highest-Risk Gaps

1. Current live `pdf_qa` does not actually perform KB verification even when `allow_kb_verification=True`.
2. Current `hybrid_qa` active route wiring is not the same as legacy route wiring:
   legacy route path usually used PDF preview fallback only, current route path wires full PDF chunk extraction.
3. Current stream contract is richer in some places but no longer identical:
   `legacy_type="thinking"` is gone, synthetic `done` behavior changed, `/api/v1/ask` semantics changed.
4. Current test coverage is too thin to claim legacy-equivalent behavior for file QA.
5. Legacy `conversation_id`-driven file resolution moved to `gateway`; this is an architecture shift and should not be counted as a `fastQA` execution bug.

## Gateway Boundary Clarification

This repository is no longer using the legacy `fastapi-version` operating model for file-intent judgment.

In the current architecture, `gateway` is the owner of file-intent judgment and mode routing. That means:

- `gateway` fetches conversation-bound files before QA dispatch
- `gateway` resolves whether the turn is `kb_only`, `file_only`, or `mixed`
- `gateway` resolves whether the route is `kb_qa`, `pdf_qa`, `tabular_qa`, or `hybrid_qa`
- `gateway` decides `actual_mode`
- `gateway` returns clarification errors before forwarding if file selection is ambiguous
- QA backends receive already-normalized execution context and should primarily execute, not own top-level intent judgment

Current gateway evidence:

- file list lookup: [gateway/app/routers/qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L35)
- gateway file-context resolve: [gateway/app/services/file_context_resolver.py](/home/cqy/worktrees/highThinking/gateway/app/services/file_context_resolver.py#L84)
- gateway route decision: [gateway/app/services/route_decision.py](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py#L8)
- normalized upstream payload: [gateway/app/routers/qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L51)
- clarification short-circuit: [gateway/app/routers/qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L96) and [gateway/app/routers/qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L112)

This changes how parity should be judged:

- it is incorrect to require current `fastQA` to reproduce legacy `ask_gateway` ownership of file-intent judgment
- it is still correct to require current `fastQA` to execute the routed `pdf_qa / tabular_qa / hybrid_qa` branches correctly once the gateway has decided the route and provided `used_files`, `execution_files`, `turn_mode`, and `allow_kb_verification`
- any review item that depends on `conversation_id`-driven file lookup inside `fastQA` must be marked as an architecture shift, not automatically a bug

Therefore this document distinguishes between:

- `legacy single-service parity`: what old `fastapi-version` did inside one backend
- `current gateway-system parity`: what the new multi-service system should do with gateway-owned intent judgment

Unless otherwise stated, the gap analysis below uses `gateway-system parity` as the preferred target when discussing file-intent ownership, and uses `legacy single-service parity` only when discussing branch execution behavior inside `fastQA`.

## Source Map

### Legacy active path

Legacy file-mode behavior was not defined by module code alone. The active behavior was the combination of:

- `app/modules/ask_gateway/api.py`
- `app/modules/ask_gateway/service.py`
- `app/modules/ask_gateway/streaming.py`
- `app/modules/file_context/service.py`
- `app/modules/qa_pdf/*`
- `app/modules/qa_tabular/*`

### Current active path

Current file-mode behavior is defined by:

- `fastQA/app/routers/qa.py`
- `fastQA/app/services/request_adapter.py`
- `fastQA/app/services/file_routes.py`
- `fastQA/app/services/stream_contract.py`
- `fastQA/app/modules/file_context/service.py`
- `fastQA/app/modules/qa_pdf/*`
- `fastQA/app/modules/qa_tabular/*`

Important note:

- `fastQA/app/services/file_route_service.py` exists, but is not on the current active route path.
- `fastQA/app/modules/qa_pdf/*` and `fastQA/app/modules/qa_tabular/*` are only part of the story.
- Legacy parity must be judged against the real HTTP execution path, not just against copied module files.

## Legacy File-Mode Architecture

### Legacy request entry

Legacy entrypoint is the ask-gateway SSE route in [api.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/api.py#L27).

Key properties:

- `/api/v1/ask` and `/api/v1/ask_stream` both use the same SSE-oriented gateway path
- gateway owns auth/quota/slot handling
- gateway enriches request before dispatch
- gateway persists stream summaries after the stream completes

### Legacy file-context authority

The real legacy route authority is [file_context/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/file_context/service.py#L129).

That resolver handles:

- explicit file references like `#n`
- ordinal references
- deleted-file clarification
- latest/new upload preference
- last-focus reuse
- generic question fallback to KB
- `pdf_qa` / `tabular_qa` / `hybrid_qa` selection
- mixed-task `allow_kb_verification`
- `current_pdf_path` fallback

### Legacy dispatch split

Legacy dispatch happens in [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L530):

- `route_hint == "pdf_qa"` -> `_dispatch_pdf()`
- `route_hint in {"tabular_qa", "hybrid_qa"}` -> `_dispatch_tabular()`
- otherwise -> `_dispatch_kb()`

This matters because legacy parity is defined by `_dispatch_pdf()` and `_dispatch_tabular()`, not only by the copied `qa_pdf` and `qa_tabular` modules.

## PDF QA Review

### Legacy PDF QA End-to-End Path

Legacy active path:

1. HTTP request enters [ask_gateway/api.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/api.py#L27)
2. request is normalized in [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L157)
3. if `conversation_id` exists, uploaded files are fetched and passed to [file_context/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/file_context/service.py#L129)
4. `route_hint` may be rewritten to `pdf_qa`
5. `_dispatch_pdf()` executes in [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L396)
6. single-PDF eager text load happens through [ask_gateway/helpers.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/helpers.py#L185)
7. module routing happens in [qa_pdf/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/service.py#L329)
8. single-PDF streaming happens in [qa_pdf/streaming.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/streaming.py#L90)
9. prompt + truncation + LLM invocation happens in:
   [qa_pdf/prompting.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/prompting.py#L20),
   [qa_pdf/truncation.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/truncation.py#L136),
   [qa_pdf/engine.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/engine.py#L53)
10. stream event enrichment/persistence is applied by ask-gateway wrappers

### Current PDF QA End-to-End Path

Current active path:

1. HTTP request enters [fastQA/app/routers/qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L703)
2. payload is normalized by [request_adapter.py](/home/cqy/worktrees/highThinking/fastQA/app/services/request_adapter.py#L151)
3. route/file context is resolved in [fastQA/app/routers/qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L317) and [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L125)
4. `pdf_qa` dispatch calls [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L141)
5. single-PDF eager text load still happens through [file_qa_helpers.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_qa_helpers.py#L78)
6. module routing happens in [qa_pdf/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/service.py#L329)
7. single-PDF streaming happens in [qa_pdf/streaming.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/streaming.py#L90)
8. prompt + truncation + LLM invocation happens in:
   [qa_pdf/prompting.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/prompting.py#L20),
   [qa_pdf/truncation.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/truncation.py#L136),
   [qa_pdf/engine.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/engine.py#L53)
9. stream normalization and JSON aggregation are applied in [fastQA/app/routers/qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L421) and [stream_contract.py](/home/cqy/worktrees/highThinking/fastQA/app/services/stream_contract.py#L51)

### PDF QA File-by-File Parity

| Area | Legacy | Current | Status | Notes |
|---|---|---|---|---|
| extractor | [pdf_extractor.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/pdf_extractor.py#L48) | [pdf_extractor.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/pdf_extractor.py#L48) | preserved | same extractor and reference exclusion |
| truncation | [truncation.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/truncation.py#L136) | [truncation.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/truncation.py#L136) | preserved | same smart truncation strategy |
| prompting | [prompting.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/prompting.py#L20) | [prompting.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/prompting.py#L20) | preserved | prompt text is effectively the same |
| engine | [engine.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/engine.py#L53) | [engine.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/engine.py#L53) | mostly preserved | same logic, current uses dict messages instead of LangChain message objects |
| service | [service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/service.py#L329) | [service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/service.py#L329) | mostly preserved | only DOI regex was broadened |
| streaming | [streaming.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/streaming.py#L90) | [streaming.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/streaming.py#L90) | mostly preserved | same frame order; live KB verification is not wired the same |
| sidecar policy | [service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/service.py#L75) | [service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/service.py#L75) | preserved | same sidecar gate and health cache |
| web bindings | [web_bindings.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/web_bindings.py#L534) | [web_bindings.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/web_bindings.py#L21) | changed | current is heavily simplified |
| llm factory | [llm_factory.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/llm_factory.py#L57) | [llm_factory.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_pdf/llm_factory.py#L31) | changed | current removed dedicated Neo4j/legacy init path and simplified fallback |
| route wrapper | [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L396) | [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L141) | changed | live route semantics differ even when module code is similar |

### PDF QA Preserved Behavior

- single-PDF stream order is still `metadata -> thinking -> content* -> done`
- sidecar policy is effectively unchanged
- multi-PDF module behavior is preserved
- smart truncation is preserved
- PDF-only prompt constraints are preserved
- PDF text cache/singleflight logic is preserved through helper migration

### PDF QA Missing or Changed Behavior

#### High

1. Gateway now owns conversation-backed file resolution; `fastQA` no longer should.

Legacy single-service behavior:

- `ask_gateway` fetched files from `conversation_id`
- `ask_gateway` ran file-context resolution itself
- `ask_gateway` rewrote route and execution scope before dispatch

Current gateway-system behavior:

- `gateway` fetches files and resolves file intent first
- `gateway` forwards normalized `used_files`, `execution_files`, `turn_mode`, and `allow_kb_verification` to `fastQA`
- `fastQA` should be judged mainly on branch execution after that normalization

This is an architecture shift, not by itself a `fastQA` branch-execution defect.

Relevant files:

- legacy [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L157)
- legacy [file_context/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/file_context/service.py#L129)
- current gateway [gateway/app/routers/qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L35)
- current gateway [gateway/app/services/file_context_resolver.py](/home/cqy/worktrees/highThinking/gateway/app/services/file_context_resolver.py#L84)
- current `fastQA` [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L125)

2. Live PDF KB verification is effectively disabled.

Legacy behavior:

- `_dispatch_pdf()` passes `runtime.agent`
- PDF streaming can call `agent.smart_query()` when `allow_kb_verification=True`

Current behavior:

- `file_routes.py` passes `SimpleNamespace(llm=...)`
- no `smart_query()` is available on the active route path

Impact:

- `allow_kb_verification` may still be set by file-context logic
- but the active PDF path cannot execute the legacy KB verification branch

Relevant files:

- legacy [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L453)
- legacy [qa_pdf/streaming.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/streaming.py#L58)
- current [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L193)

#### Medium

3. Legacy answer sanitization was reduced.

Legacy sanitization lives in [ask_gateway/helpers.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/helpers.py#L15).

Current sanitization lives in [file_qa_helpers.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_qa_helpers.py#L16).

Current cleaner is materially simpler, so output text can drift even if model output is similar.

4. Legacy dedicated PDF-LLM policy and warmup are no longer present in the same form.

Legacy `web_bindings.py` contains:

- dedicated PDF-LLM selection
- transport policy
- warmup thread
- provider-specific timeout logic

Current `web_bindings.py` is a thin binder and delegates fallback creation to [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L84).

Impact:

- functionality remains usable
- latency and operator-level knobs are not identical

#### Low

5. Current reference output is richer than legacy.

Legacy `done` payload is basically DOI list + route.

Current router adds:

- `reference_objects`
- `reference_links`
- `pdf_links`
- `doi_locations`
- enriched `metadata`

This is not necessarily bad, but it is not parity.

### PDF QA Prompt and LLM Inputs

The old implementation is prompt-constrained and PDF-only by design.

Main files:

- [qa_pdf/prompting.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/prompting.py#L20)
- [qa_pdf/engine.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_pdf/engine.py#L53)

Old behavior:

- fixed system message forbids using general/pretrained knowledge
- optional KB verification block can be injected into prompt
- summary and non-summary prompts are separate
- final LLM input is `system + user(prompt)`

Current behavior is almost the same, except:

- current engine sends OpenAI-style dict messages instead of `SystemMessage/HumanMessage`
- current live route uses simplified web bindings and LLM bootstrap

### PDF QA Test Coverage Gap

Legacy PDF tests are broad:

- [test_qa_pdf.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_qa_pdf.py#L18)
- [test_pdf_cache.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_pdf_cache.py#L47)
- [test_ask_gateway.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_ask_gateway.py#L221)

They cover:

- bridge behavior
- truncation
- native/compatible adapter policy
- sidecar rules/health/fallback
- multi-PDF
- DOI direct path
- PDF cache
- gateway dispatch

Current PDF tests are much thinner:

- [test_qa_pdf_service.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_qa_pdf_service.py#L4)
- [test_qa_routes_file_modes.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_qa_routes_file_modes.py#L12)
- [test_request_adapter.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_request_adapter.py#L37)
- [test_file_context_service.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_file_context_service.py#L30)

Missing current tests versus legacy:

- sidecar policy/health/fallback
- multi-PDF
- DOI direct query
- direct PDF cache tests
- live mixed PDF + KB verification path

## Tabular QA Review

### Legacy Tabular QA End-to-End Path

Legacy active path:

1. request enters ask-gateway SSE route
2. gateway enriches request and determines `route_hint`
3. `_dispatch_tabular()` in [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L490) emits `dispatch`
4. it calls [qa_tabular/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/service.py#L186)
5. service loads workbook, profiles schema, plans intent, executes deterministic operation, then uses renderer/LLM for explanation
6. stream events are normalized/enriched by ask-gateway wrappers

### Current Tabular QA End-to-End Path

Current active path:

1. request enters [fastQA/app/routers/qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L703)
2. payload is normalized by [request_adapter.py](/home/cqy/worktrees/highThinking/fastQA/app/services/request_adapter.py#L151)
3. route is resolved to `tabular_qa` or `hybrid_qa`
4. router calls [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L232)
5. that calls [qa_tabular/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L207)
6. router normalizes and enriches stream output

### Tabular QA File-by-File Parity

Line-count and diff inspection show the core tabular stack is nearly unchanged:

| Module | Legacy | Current | Status |
|---|---|---|---|
| `workbook_loader.py` | [legacy](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/workbook_loader.py#L185) | [current](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py#L185) | identical |
| `schema_profiler.py` | [legacy](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/schema_profiler.py#L56) | [current](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/schema_profiler.py#L56) | identical |
| `planner.py` | [legacy](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/planner.py#L909) | [current](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/planner.py#L909) | identical |
| `executor.py` | [legacy](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/executor.py#L94) | [current](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/executor.py#L94) | identical |
| `renderer.py` | [legacy](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/renderer.py#L60) | [current](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/renderer.py#L60) | identical |
| `service.py` | [legacy](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/service.py#L186) | [current](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L207) | slightly changed |

Actual service differences are small:

1. current DOI regex is broader
2. current adds `_fallback_profile_for_workbook()` in
   [qa_tabular/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L164)

That fallback changes failure semantics:

- legacy would effectively skip a file if profiling failed during load loop
- current can keep going with a reduced profile

### Tabular QA Preserved Behavior

The deterministic core is preserved:

- workbook loading
- sheet/column profiling
- operation planning
- compare/multi-table logic
- execution operators
- result rendering
- LLM prompt structure

Legacy renderer prompt:

- uses deterministic execution result as source of truth
- asks the LLM only to express the computed result clearly

See:

- [renderer.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/renderer.py#L60)

### Tabular QA Test Coverage Gap

Legacy test coverage:

- [test_qa_tabular.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_qa_tabular.py#L6)

Covers:

- load bridge
- plan bridge
- execute bridge
- compare bridge
- render bridge
- standard tabular stream
- hybrid evidence behavior

Current test coverage:

- [test_qa_tabular_service.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_qa_tabular_service.py#L6)

Current direct tabular service test count is materially lower.

Conclusion:

- core logic parity is high
- confidence parity is lower than it should be because tests regressed sharply

## Hybrid QA Review

### What Legacy Hybrid Really Was

For the active `route_hint="hybrid_qa"` path, legacy did not use a separate orchestrator.

Active legacy route behavior:

- ask-gateway selects `route_hint="hybrid_qa"`
- `_dispatch_tabular()` still calls `qa_tabular_service.iter_answer_events(...)`
- hybrid mode is just `route_hint == "hybrid_qa"` inside tabular service

Relevant files:

- [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L490)
- [qa_tabular/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/service.py#L207)

### Hybrid-Specific Logic in Old Implementation

Old service behavior:

- collect ready table files
- collect ready PDF files
- execute deterministic table plan first
- build `pdf_evidence_context`
- ask LLM to answer with table result as authoritative source and PDF evidence only as explanation/verification

Key code:

- hybrid switch at [qa_tabular/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/service.py#L207)
- evidence formatting at [qa_tabular/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/service.py#L54)
- chunk retrieval code present at [qa_tabular/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/service.py#L115)
- hybrid prompt at [renderer.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_tabular/renderer.py#L71)

### Important Legacy Limitation

Although the service supports chunk retrieval through `extract_pdf_text_fn`, the active legacy route wrapper `_dispatch_tabular()` did not pass that argument.

That means:

- service capability existed
- active legacy route path usually fell back to PDF preview text instead of chunk-scored evidence

Relevant file:

- [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L509)

### Current Hybrid Behavior

Current active route wrapper does pass `extract_pdf_text_fn`:

- [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L264)

So current active hybrid behavior is actually stronger than legacy route behavior in this one place.

This is a real behavior change, not pure regression.

### Hybrid-Specific Logic Details

Current hybrid path:

- `route_hint == "hybrid_qa"` at
  [qa_tabular/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L228)
- PDF companions filtered from `used_files`
- `_retrieve_hybrid_evidence()` uses:
  - paragraph chunking
  - token overlap scoring
  - numeric overlap scoring
  - top-6 chunk retention
- chunk context rendered as `[E1] 文件#... chunk#... | DOI=...`
- if no chunks, fallback uses filename + `parsed_preview`

Key code:

- [qa_tabular/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L70)
- [qa_tabular/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L105)
- [qa_tabular/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L150)
- [qa_tabular/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L338)

### Hybrid Review Conclusion

Hybrid service parity is high at the module level.

But active-route parity is not exact:

- current route wrapper enables stronger PDF evidence extraction than legacy wrapper
- current request adapter is stricter than legacy
- current event normalization is richer than legacy

So current `hybrid_qa` is not a simple “missing old behavior” case.
It is a mixed state:

- service core mostly preserved
- active route contract changed

## Route, Payload, and Stream Contract Review

### Legacy Contract

Legacy contract characteristics:

- ask-gateway is SSE-first
- `/api/v1/ask` is effectively a streaming path
- gateway owns request enrichment
- gateway owns conversation-file lookup
- clarification may terminate without synthetic `done`
- normalized step events preserve `legacy_type="thinking"`

Key files:

- [ask_gateway/api.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/api.py#L27)
- [ask_gateway/service.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py#L157)
- [ask_gateway/streaming.py](/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/streaming.py#L51)

### Current Contract

Current `fastQA` contract characteristics:

- supports both JSON and SSE
- `AskRequest` is wider and gateway-oriented
- `fastQA` does not look up conversation files itself
- router enriches `done` with more metadata and links
- router emits synthetic `done` for more terminal cases
- `legacy_type="thinking"` is gone

Key files:

- [fastQA/app/routers/qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L421)
- [request_adapter.py](/home/cqy/worktrees/highThinking/fastQA/app/services/request_adapter.py#L151)
- [stream_contract.py](/home/cqy/worktrees/highThinking/fastQA/app/services/stream_contract.py#L51)

### Contract Mismatches That Matter

1. `/api/v1/ask` semantics differ because current system now has gateway-owned normalization and `fastQA` also supports JSON `/api/ask`
2. clarification/error terminal behavior differs because synthetic `done` is now common inside `fastQA`, while gateway clarification short-circuits earlier
3. `legacy_type="thinking"` is gone
4. file-selection authority now lives primarily in `gateway`, but `fastQA` still contains a second-pass resolver; this duplicated authority is a real system risk
5. current adapter may accept explicit file routes with only partial resolution context
6. legacy `conversation_id`-driven lookup moved from the QA backend into `gateway`; that is an architecture change, not automatically a defect

## Test Coverage Comparison

### Legacy Coverage

Legacy file-mode behavior is covered across:

- [test_qa_pdf.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_qa_pdf.py#L18)
- [test_pdf_cache.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_pdf_cache.py#L47)
- [test_qa_tabular.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_qa_tabular.py#L6)
- [test_ask_gateway.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_ask_gateway.py#L221)
- [test_file_context_resolver.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_file_context_resolver.py#L88)
- [test_file_context_sequence_scenarios.py](/home/cqy/worktrees/fastapi-version/backend/tests/test_file_context_sequence_scenarios.py#L61)

### Current Coverage

Current file-mode coverage is spread across:

- [test_qa_pdf_service.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_qa_pdf_service.py#L4)
- [test_qa_tabular_service.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_qa_tabular_service.py#L6)
- [test_qa_routes_file_modes.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_qa_routes_file_modes.py#L12)
- [test_file_context_service.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_file_context_service.py#L30)
- [test_request_adapter.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_request_adapter.py#L37)
- [test_stream_contract.py](/home/cqy/worktrees/highThinking/fastQA/tests/test_stream_contract.py#L4)

### Major Coverage Gaps

Current tests are missing legacy-equivalent coverage for:

- sidecar policy/health/fallback
- multi-PDF path
- DOI direct path
- direct PDF cache tests
- conversation-backed file resolution
- live PDF mixed verification path
- hybrid route dispatch coverage
- many file-context resolver edge cases
- exact event normalization parity

## Conclusion

### What Is Actually Close

- `qa_tabular` core deterministic stack
- `qa_pdf` prompt/truncation/streaming core
- sidecar module logic
- PDF text cache logic
- file-context algorithm itself once candidate files are already available

### What Is Not Actually Aligned

- current live `pdf_qa` route semantics
- current `fastQA` request/file-resolution contract
- current stream terminal/enrichment contract
- current test coverage confidence
- active hybrid route behavior versus legacy active wrapper behavior

### Final Judgment

If the question is:

- “Did we copy over most of the file-QA modules?”
  answer: yes

- “Is current `fastQA` file-mode behavior end-to-end the same as legacy `fastapi-version`?”
  answer: no

The main reason is not that the inner modules are universally broken.
The main reason is that the active route contract and surrounding runtime wiring changed:

- request adaptation changed
- conversation-backed file lookup moved out
- PDF KB verification live wiring changed
- hybrid route helper behavior changed
- stream/event contract changed
- tests no longer lock enough of the old behavior

## Recommended Follow-Up Audit Order

If this review is used as the basis for later repair work, the highest-value order is:

1. restore end-to-end PDF route parity
   - conversation-backed file resolution
   - live KB verification
   - output sanitization
2. freeze route/stream contract parity
   - metadata
   - step normalization
   - clarification/error termination
   - `/api/v1/ask` semantics
3. decide whether hybrid should match legacy route behavior exactly or keep the stronger current chunk-evidence path
4. rebuild file-mode regression coverage to legacy level
