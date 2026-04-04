# PatentQA QA-Core Full-Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `patentQA` from the current direct retrieval path to the approved staged QA core pipeline while keeping persistence, durable transcript handling, and caller-facing shell behavior on the existing patent-side implementation path.

**Architecture:** Reuse the `fastQA` staged execution skeleton only inside the QA core. Add patent-owned KB service, staged models, orchestrator, and stage adapters so the execution path becomes `PatentExecutor -> PatentKbService -> patent orchestrator/runtime -> compatible execution_result`. Keep `AskService`, `PatentResultBuilder`, `ChatPersistenceService`, and the current request/stream shell as boundary owners rather than redesigning them.

**Tech Stack:** Python, FastAPI, Pydantic, pytest, ChromaDB, httpx, Redis-compatible execution cache, `conda` environment `agent`

---

## Constraints And References

**Primary spec**
- Spec: [docs/2026-04-02-patentqa-fastqa-full-pipeline-migration-spec.md](/home/cqy/worktrees/highThinking/docs/2026-04-02-patentqa-fastqa-full-pipeline-migration-spec.md)

**QA-core-only boundary**
- Only the QA core path is in scope:
  - stage 1 planning
  - stage 2 dual-search retrieval
  - stage 2.5 reserved no-op boundary
  - stage 3 evidence assembly with table attachment
  - stage 4 synthesis
- The following remain on the current `patentQA` implementation path:
  - `AskService`
  - `PatentResultBuilder`
  - `ChatPersistenceService`
  - durable transcript ownership
  - request lifecycle and stream transport shell

**Reference-flow constraints from the approved spec**
- Stage 2 must follow patent dual-search semantics:
  - abstract DB coarse recall
  - extract ordered `patent_id` candidate set
  - chunk DB constrained localization under that candidate set
  - merge and dedup results across retrieval-plan items
- Stage 2.5 remains an explicit stage boundary but defaults to skipped/no-op in patent mode
- Stage 3 is the deterministic table-attachment boundary
- Stage 3 defaults to retrieval-result aggregation plus `*_tables.json`
- PDF fallback is optional and patent-side-config-controlled
- `source_ids` are extracted from patent metadata via `patent_id`, never via DOI logic

**Existing implementation references**
- Current patent shell:
  - [patent/server/services/ask_service.py](/home/cqy/worktrees/highThinking/patent/server/services/ask_service.py)
  - [patent/server/patent/result_builder.py](/home/cqy/worktrees/highThinking/patent/server/patent/result_builder.py)
  - [patent/server/services/chat_persistence.py](/home/cqy/worktrees/highThinking/patent/server/services/chat_persistence.py)
- Current patent QA core entry:
  - [patent/server/patent/executor.py](/home/cqy/worktrees/highThinking/patent/server/patent/executor.py)
  - [patent/server/patent/pipeline.py](/home/cqy/worktrees/highThinking/patent/server/patent/pipeline.py)
  - [patent/server/patent/runtime.py](/home/cqy/worktrees/highThinking/patent/server/patent/runtime.py)
  - [patent/server/patent/retrieval_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/retrieval_service.py)
- FastQA skeleton references:
  - [fastQA/app/modules/qa_kb/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/service.py)
  - [fastQA/app/modules/qa_kb/models.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/models.py)
  - [fastQA/app/modules/qa_kb/orchestrators/generation.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/orchestrators/generation.py)
  - [fastQA/app/services/conversation_context_builder.py](/home/cqy/worktrees/highThinking/fastQA/app/services/conversation_context_builder.py)

**Resource roots**
- Abstract vector DB: [resource/patentQA/vector_db_patent_abstracts](/home/cqy/worktrees/highThinking/resource/patentQA/vector_db_patent_abstracts)
- Chunk vector DB: [resource/patentQA/vector_db_patent_chunks](/home/cqy/worktrees/highThinking/resource/patentQA/vector_db_patent_chunks)
- Patent archive root: [resource/patentQA](/home/cqy/worktrees/highThinking/resource/patentQA)

**Recommended test invocation**
- `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/... -q`
- Concrete smoke command:
  - `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -q`

**Git command working directory**
- All `git add` and `git commit` commands below are written to run from repo root: [highThinking](/home/cqy/worktrees/highThinking)

---

## File Structure Map

### New QA-core files to create

- `patent/server/patent/kb_service.py`
  - Patent-owned QA core service boundary that mirrors `fastQA` structure but emits patent-compatible execution results.
- `patent/server/patent/models.py`
  - Patent staged runtime protocol, staged request/result dataclasses, and QA-core metadata objects.
- `patent/server/patent/orchestrators/generation.py`
  - Patent staged orchestrator for stage 1 / 2 / 2.5 / 3 / 4 execution.
- `patent/server/patent/stages/planning.py`
  - Stage 1 wrapper and validation for patent retrieval-plan output.
- `patent/server/patent/stages/retrieval.py`
  - Stage 2 dual-search wrapper plus stage 2.5 no-op wrapper.
- `patent/server/patent/stages/evidence_loading.py`
  - Stage 3 retrieval-result aggregation, table loading, and optional PDF fallback.
- `patent/server/patent/stages/synthesis.py`
  - Stage 4 synthesis wrapper that returns a shell-compatible execution payload.
- `patent/server/services/conversation_context_builder.py`
  - Patent-side normalized context builder that converts raw persistence context into stage prompt context.
- `patent/tests/test_conversation_context_builder.py`
- `patent/tests/test_patent_kb_service.py`
- `patent/tests/test_patent_generation_orchestrator.py`
- `patent/tests/test_patent_stage1_planning.py`
- `patent/tests/test_patent_stage3_evidence_loading.py`
- `patent/tests/test_patent_stage4_synthesis.py`

### Existing QA-core files to modify

- `patent/server/patent/executor.py`
  - Replace direct retrieval execution with KB service entry.
- `patent/server/patent/pipeline.py`
  - Stop acting as the primary QA core and instead provide compatibility helpers if still needed.
- `patent/server/patent/runtime.py`
  - Implement the patent staged runtime interface on top of current retrieval/original/archive resources.
- `patent/server/patent/retrieval_service.py`
  - Expose patent-native stage 2 helpers for abstract recall, candidate extraction, chunk localization, and merge/dedup.
- `patent/server/patent/retrieval_models.py`
  - Expand contracts to cover staged retrieval plan, retrieval payloads, source-id extraction, and stage 3 evidence inputs.
- `patent/server/patent/answering.py`
  - Support stage 4 synthesis prompt/result handling if current answer builder is too shallow.
- `patent/tests/test_patent_executor.py`
- `patent/tests/test_patent_retrieval_service.py`
- `patent/tests/test_runtime_controls.py`
- `patent/tests/fastapi_contract/test_ask_contract.py`

### Shell files intentionally left as boundary owners

- `patent/server/services/ask_service.py`
- `patent/server/patent/result_builder.py`
- `patent/server/services/chat_persistence.py`

The implementation may touch these only when needed to preserve integration, not to redesign persistence or stream ownership.

---

## Delivery Order

Implement in this order:

1. Patent QA-core models and normalized context boundary
2. Patent KB service and orchestrator skeleton
3. Stage 1 planning
4. Stage-level cache and singleflight boundaries
5. Stage 2 dual-search retrieval and `patent_id` source extraction
6. Stage 2.5 no-op boundary
7. Stage 3 evidence assembly and table attachment
8. Stage 4 synthesis and execution-result assembly
9. Production bootstrap wiring and shell-regression verification

This order keeps the QA core independently testable before it is wired back into the existing patent shell.

---

## Task 1: Add Patent QA-Core Models And Context Normalization Boundary

**Files:**
- Create: `patent/server/patent/models.py`
- Create: `patent/server/services/conversation_context_builder.py`
- Test: `patent/tests/test_conversation_context_builder.py`
- Test: `patent/tests/test_runtime_controls.py`

- [ ] **Step 1: Write failing tests for patent normalized conversation context**

Cover:
- raw `chat_history`, `summary`, and `conversation_state` normalize into:
  - `recent_turns_for_llm`
  - `summary_for_llm`
  - `conversation_state`
  - `source_selection`
- pending overlays are reflected only through the already-merged chat history
- runtime-facing context never depends on raw snapshot or overlay bookkeeping keys
- staged runtime model types allow:
  - `PatentRetrievalPlan`
  - patent `source_ids`
  - stage-timing metadata

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_conversation_context_builder.py tests/test_runtime_controls.py -q`
Expected: FAIL because the patent context builder and staged model contracts do not exist yet

- [ ] **Step 3: Implement the minimal staged model layer and patent context builder**

Implement:
- patent runtime protocol and staged dataclasses in `server/patent/models.py`
- patent-side normalized context builder modeled on `fastQA` but scoped to current patent request fields
- source-selection normalization that preserves the current `kb`-only patent contract
- explicit separation between raw persistence context and stage prompt context

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_conversation_context_builder.py tests/test_runtime_controls.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/models.py patent/server/services/conversation_context_builder.py patent/tests/test_conversation_context_builder.py patent/tests/test_runtime_controls.py
git commit -m "feat: add patent qa core models and context builder"
```

## Task 2: Add Patent KB Service And Staged Orchestrator Skeleton

**Files:**
- Create: `patent/server/patent/kb_service.py`
- Create: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/executor.py`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`
- Test: `patent/tests/test_patent_executor.py`

- [ ] **Step 1: Write failing tests for the QA-core service boundary**

Cover:
- `PatentKbService` runs a staged QA core and returns a shell-compatible `execution_result`
- orchestrator preserves stage order `1 -> 2 -> 2.5 -> 3 -> 4`
- orchestrator can represent stage 2.5 as skipped/no-op
- `PatentExecutor` delegates to KB service instead of calling `PatentRetrievalService.retrieve()` directly
- existing shell-facing executor tests keep passing for response shape expectations

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_kb_service.py tests/test_patent_generation_orchestrator.py tests/test_patent_executor.py -q`
Expected: FAIL because KB service and orchestrator modules do not exist and executor still uses the direct path

- [ ] **Step 3: Implement the minimal KB service and orchestrator skeleton**

Implement:
- KB service entrypoint that accepts `question`, runtime, and normalized context
- orchestrator skeleton with explicit stage hooks and timing capture
- compatibility result assembly that still returns `answer_text`, `steps`, `timings`, `references`, `reference_objects`, `reference_links`, `original_links`, `metadata`, and `route`
- executor delegation into KB service while preserving the existing `AskService` and `PatentResultBuilder` shell

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_kb_service.py tests/test_patent_generation_orchestrator.py tests/test_patent_executor.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/kb_service.py patent/server/patent/orchestrators/generation.py patent/server/patent/executor.py patent/tests/test_patent_kb_service.py patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_executor.py
git commit -m "feat: add patent kb service skeleton"
```

## Task 3: Implement Stage 1 Patent Planning

**Files:**
- Create: `patent/server/patent/stages/planning.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/models.py`
- Test: `patent/tests/test_patent_stage1_planning.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`

- [ ] **Step 1: Write failing tests for stage 1 planning output**

Cover:
- stage 1 returns `deep_answer`
- stage 1 returns a structured `retrieval_plan` with at least:
  - `question_type`
  - `analysis_axes`
  - `explicit_patent_ids`
  - `candidate_recall_queries`
  - `evidence_localization_queries`
  - `preferred_sections`
  - `filters`
- explicit patent ids in the question are surfaced into the retrieval plan
- empty or malformed stage 1 output is normalized into safe defaults instead of crashing later stages

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage1_planning.py tests/test_patent_generation_orchestrator.py -q`
Expected: FAIL because stage 1 planning is not yet implemented

- [ ] **Step 3: Implement stage 1 planning**

Implement:
- stage 1 wrapper in `server/patent/stages/planning.py`
- runtime method `stage1_pre_answer_and_planning`
- prompt/result normalization that is patent-analysis-oriented rather than paper-claim-oriented
- preservation of the current shell contract by storing stage 1 outputs only inside QA-core metadata/raw fields

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage1_planning.py tests/test_patent_generation_orchestrator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/stages/planning.py patent/server/patent/runtime.py patent/server/patent/models.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_generation_orchestrator.py
git commit -m "feat: add patent stage1 planning"
```

## Task 4: Add Stage-Level Cache And Singleflight Boundaries

**Files:**
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/services/execution_cache.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/models.py`
- Test: `patent/tests/test_execution_cache.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`

- [ ] **Step 1: Write failing tests for stage cache and singleflight boundaries**

Cover:
- stage 1 cache key composition
- stage 2 cache key composition
- stage 2.5 cache key composition for skipped/no-op stage identity
- stage 3 cache key composition including patent-side PDF-mode flag
- orchestrator reads cached stage results before recomputing
- singleflight boundaries exist for stage 1, stage 2, stage 2.5, and stage 3

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_execution_cache.py tests/test_patent_generation_orchestrator.py -q`
Expected: FAIL because stage-level cache families and singleflight hooks do not exist yet

- [ ] **Step 3: Implement cache and singleflight support**

Implement:
- stage cache key helpers in `server/patent/cache_keys.py`
- execution-cache helpers for stage 1/2/2.5/3 payloads in `server/services/execution_cache.py`
- orchestrator hooks that read/write stage cache payloads
- singleflight ownership around stage 1/2/2.5/3 execution boundaries

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_execution_cache.py tests/test_patent_generation_orchestrator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/cache_keys.py patent/server/services/execution_cache.py patent/server/patent/orchestrators/generation.py patent/server/patent/models.py patent/tests/test_execution_cache.py patent/tests/test_patent_generation_orchestrator.py
git commit -m "feat: add patent stage cache boundaries"
```

## Task 5: Implement Stage 2 Dual-Search Retrieval And Patent Source-ID Extraction

**Files:**
- Create: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server/patent/retrieval_models.py`
- Modify: `patent/server/patent/runtime.py`
- Test: `patent/tests/test_patent_retrieval_service.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`

- [ ] **Step 1: Write failing tests for stage 2 dual-search behavior**

Cover:
- abstract recall produces an ordered `patent_id` candidate set
- chunk localization is constrained by the recalled candidate set when available
- multi-plan-item retrieval results merge and dedup into one payload
- source ids are extracted from `patent_id` metadata, not DOI fields
- explicit id resolution, no-vector fallbacks, and current retrieval cache behavior still work

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_patent_generation_orchestrator.py -q`
Expected: FAIL because retrieval service is still shaped as a single direct retrieval path

- [ ] **Step 3: Implement stage 2 wrappers and retrieval-service helpers**

Implement:
- stage 2 wrapper in `server/patent/stages/retrieval.py`
- retrieval-service helper boundaries for:
  - abstract recall
  - candidate `patent_id` extraction
  - constrained chunk localization
  - result merge and dedup
  - source-id extraction
- runtime method `stage2_targeted_retrieval`
- patent-native retrieval payload that can be consumed by stage 3 without paper DOI assumptions

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_patent_generation_orchestrator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/stages/retrieval.py patent/server/patent/retrieval_service.py patent/server/patent/retrieval_models.py patent/server/patent/runtime.py patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_generation_orchestrator.py
git commit -m "feat: add patent dual-search retrieval stage"
```

## Task 6: Represent Stage 2.5 As A Reserved No-Op Boundary

**Files:**
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/runtime.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`

- [ ] **Step 1: Write failing tests for stage 2.5 skip semantics**

Cover:
- stage 2.5 runs as an explicit stage boundary in the orchestrator
- patent mode marks stage 2.5 as skipped/no-op
- retrieval payload passed into stage 3 is unchanged by default
- stage timings and step messages still record stage 2.5

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_generation_orchestrator.py -q`
Expected: FAIL because stage 2.5 semantics are not implemented explicitly

- [ ] **Step 3: Implement the no-op boundary**

Implement:
- stage 2.5 wrapper in `server/patent/stages/retrieval.py`
- runtime method `stage25_patent_evidence_expansion` as pass-through/skipped by default
- orchestrator step metadata and raw payload support for explicit skip semantics

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_generation_orchestrator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/stages/retrieval.py patent/server/patent/orchestrators/generation.py patent/server/patent/runtime.py patent/tests/test_patent_generation_orchestrator.py
git commit -m "feat: add patent stage25 no-op boundary"
```

## Task 7: Implement Stage 3 Evidence Assembly And Table Attachment

**Files:**
- Create: `patent/server/patent/stages/evidence_loading.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/archive_loader.py`
- Modify: `patent/server/patent/retrieval_models.py`
- Test: `patent/tests/test_patent_stage3_evidence_loading.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`

- [ ] **Step 1: Write failing tests for stage 3 evidence assembly**

Cover:
- retrieval results are aggregated by `patent_id`
- matched snippets remain bounded and deduplicated per patent
- `*_tables.json` is attached whenever present for a selected patent
- stage 3 defaults to not opening PDFs
- patent-side PDF fallback config enables local PDF loading without dropping table evidence

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage3_evidence_loading.py tests/test_patent_generation_orchestrator.py -q`
Expected: FAIL because stage 3 is not yet modeled as a dedicated evidence-assembly stage

- [ ] **Step 3: Implement stage 3 evidence loading**

Implement:
- stage 3 wrapper in `server/patent/stages/evidence_loading.py`
- runtime method `stage3_load_patent_evidence`
- default path that aggregates stage 2 snippets and appends tables from archive assets
- optional PDF fallback gate in runtime using a patent-side config flag
- patent evidence-bundle contract that stage 4 can synthesize from directly

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage3_evidence_loading.py tests/test_patent_generation_orchestrator.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/stages/evidence_loading.py patent/server/patent/runtime.py patent/server/patent/archive_loader.py patent/server/patent/retrieval_models.py patent/tests/test_patent_stage3_evidence_loading.py patent/tests/test_patent_generation_orchestrator.py
git commit -m "feat: add patent stage3 evidence assembly"
```

## Task 8: Implement Stage 4 Synthesis And Shell-Compatible Execution Result Assembly

**Files:**
- Create: `patent/server/patent/stages/synthesis.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/kb_service.py`
- Test: `patent/tests/test_patent_stage4_synthesis.py`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write failing tests for stage 4 synthesis and final execution_result compatibility**

Cover:
- stage 4 consumes:
  - user question
  - stage 1 `deep_answer`
  - stage 3 evidence bundle
  - retrieval metadata
  - normalized conversation context
- final execution result still exposes:
  - `answer_text`
  - `steps`
  - `timings`
  - `references`
  - `reference_objects`
  - `reference_links`
  - `original_links`
  - `metadata`
- current ask-contract tests still pass without changing the shell envelope

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage4_synthesis.py tests/test_patent_kb_service.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL because stage 4 synthesis is not yet integrated into the staged QA core

- [ ] **Step 3: Implement stage 4 synthesis and final result assembly**

Implement:
- stage 4 wrapper in `server/patent/stages/synthesis.py`
- runtime method `stage4_synthesis_with_patent_evidence`
- reuse or extend `PatentAnswerBuilder` for patent-evidence synthesis
- KB service final assembly into the shell-compatible execution-result dict consumed by `AskService` and `PatentResultBuilder`

- [ ] **Step 4: Re-run the targeted tests**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage4_synthesis.py tests/test_patent_kb_service.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/stages/synthesis.py patent/server/patent/answering.py patent/server/patent/orchestrators/generation.py patent/server/patent/kb_service.py patent/tests/test_patent_stage4_synthesis.py patent/tests/test_patent_kb_service.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: add patent stage4 synthesis"
```

## Task 9: Wire Production Bootstrap Into The Staged QA Core And Verify Shell Regressions

**Files:**
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/pipeline.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server_fastapi/app.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write or extend failing regression tests for shell preservation**

Cover:
- `AskService` can keep publishing through the existing patent shell
- executor now routes through the staged QA core
- durable context input remains patent-side and does not leak raw persistence internals into stages
- no persistence redesign is required for QA-core integration
- real app bootstrap constructs a `PatentExecutor` that can reach the staged QA core instead of only the direct `retrieval_service` path

- [ ] **Step 2: Run the regression slice and verify failures where expected**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_executor.py tests/test_patent_kb_service.py tests/test_chat_persistence.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS on unchanged shell invariants and FAIL only on missing staged integration pieces

- [ ] **Step 3: Finalize executor/runtime compatibility wiring and production bootstrap**

Implement:
- choose one explicit production bootstrap strategy and implement it end to end:
  - either `server_fastapi/app.py` constructs `PatentExecutor` with a staged KB service/runtime dependency
  - or `PatentExecutor` constructs the staged KB service from the runtime/retrieval dependencies it already receives
- `server_fastapi/app.py` no longer leaves production ask traffic on the old direct retrieval-only execution chain
- executor/runtime bootstrap chooses the staged QA core when retrieval resources are available
- pipeline module, if retained, becomes compatibility glue instead of the primary execution path
- shell-facing step messages and metadata align with the approved spec:
  - stage 1 planning
  - stage 2 dual-search retrieval
  - stage 2.5 skipped in patent mode
  - stage 3 table attachment
  - stage 4 synthesis

- [ ] **Step 4: Run the full patent QA-core verification slice**

Run: `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_conversation_context_builder.py tests/test_patent_kb_service.py tests/test_patent_generation_orchestrator.py tests/test_patent_stage1_planning.py tests/test_patent_retrieval_service.py tests/test_patent_stage3_evidence_loading.py tests/test_patent_stage4_synthesis.py tests/test_patent_executor.py tests/test_chat_persistence.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/executor.py patent/server/patent/pipeline.py patent/server/patent/runtime.py patent/server_fastapi/app.py patent/tests/test_patent_executor.py patent/tests/test_patent_kb_service.py patent/tests/test_chat_persistence.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_conversation_context_builder.py patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_stage3_evidence_loading.py patent/tests/test_patent_stage4_synthesis.py
git commit -m "feat: integrate patent qa core pipeline"
```

---

## Verification Checklist

- [ ] Stage 1 returns a patent retrieval plan, not paper-style retrieval claims
- [ ] Stage 2 uses abstract recall plus constrained chunk localization
- [ ] Stage 2 extracts `source_ids` from patent metadata via `patent_id`
- [ ] Stage 2.5 is explicit and skipped/no-op in patent mode
- [ ] Stage 3 attaches same-patent tables deterministically
- [ ] Stage 3 defaults to not reading PDFs
- [ ] Stage 4 synthesizes from the stage 3 evidence bundle
- [ ] `AskService` and `PatentResultBuilder` remain the shell boundary
- [ ] Persistence and durable transcript logic were not redesigned as part of this plan

---

## Handoff Notes

- Do not widen scope into gateway, public-service, or patent durable persistence redesign while executing this plan.
- If stage 2.5 and stage 3 look collapsible after implementation, treat that as a separate cleanup after the first increment lands and passes regression tests.
- If stage 4 cannot reuse the current answer builder cleanly, prefer a thin patent-specific adapter over changing shell contracts.
