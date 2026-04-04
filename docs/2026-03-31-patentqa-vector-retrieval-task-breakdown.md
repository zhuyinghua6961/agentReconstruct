# PatentQA Vector Retrieval And Original-View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the `patentQA` patent-domain retrieval and original-view implementation defined by the updated [2026-03-30-patentqa-delivery-spec.md](/home/cqy/worktrees/highThinking/docs/2026-03-30-patentqa-delivery-spec.md) so the service can retrieve against the two patent vector DBs, build stable patent evidence/original links, and resolve original content from the checked-in patent archive root.

**Architecture:** Keep the `fastQA`-style outer skeleton (`AskService -> executor/pipeline -> result builder -> persistence`) but replace the core paper/DOI retrieval semantics with a patent-specific dual-vector flow. Use `resource/patentQA/vector_db_patent_abstracts` for patent-level recall, `resource/patentQA/vector_db_patent_chunks` for evidence recall, and `resource/patentQA/__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_` as the authoritative original-view source keyed by `canonical_patent_id`. The dual-vector path is the preferred path when both vector DBs are available, but the approved no-vector modes `metadata_lexical`, `fulltext_lexical`, and `hybrid_no_vector` remain required fallback behavior when vector resources are missing or unavailable.

**Tech Stack:** Python, FastAPI, Pydantic, ChromaDB, pytest, Redis, httpx, `conda` environment `agent`

---

## Constraints And References

**Primary spec**
- Spec: [docs/2026-03-30-patentqa-delivery-spec.md](/home/cqy/worktrees/highThinking/docs/2026-03-30-patentqa-delivery-spec.md)
- Relevant sections:
  - 8.5 current resource-layout constraints
  - 9.x internal ask pipeline
  - 10.5-10.7 dual patent vector retrieval
  - 11.10 original-view resource resolution
  - 13.4 dual-index retrieval cache key composition

**Resource roots**
- Abstract vector DB: [resource/patentQA/vector_db_patent_abstracts](/home/cqy/worktrees/highThinking/resource/patentQA/vector_db_patent_abstracts)
- Chunk vector DB: [resource/patentQA/vector_db_patent_chunks](/home/cqy/worktrees/highThinking/resource/patentQA/vector_db_patent_chunks)
- Original archive root: [resource/patentQA/__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_](/home/cqy/worktrees/highThinking/resource/patentQA/__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_)

**Delivery constraints**
- Only `patent/` is in write scope for implementation.
- Do not assume DOI-centric fields exist in patent retrieval outputs.
- `patent_dir` inside chunk metadata is diagnostic only; runtime original-view resolution must use `canonical_patent_id` under the checked-in archive root.
- Figure support is v1 section-only. Do not invent `figure_id`, `figure_name`, or other figure selectors.
- Durable transcript ownership remains in `public-service`; this plan only changes the patent-side retrieval/original behavior and the caller-facing payloads it emits.
- Caller-facing `viewer_uri` and `original_links` must always use the gateway path shape `/api/patent/original/{canonical_patent_id}?section=...`, never a `patentQA` local address and never a filesystem-derived path.

**Recommended test invocation**
- All pytest commands below run from [patent](/home/cqy/worktrees/highThinking/patent).
- All `git add` pathspecs below are also written for execution from [patent](/home/cqy/worktrees/highThinking/patent).
- Environment prerequisite on a clean `agent` env:
  - from [patent](/home/cqy/worktrees/highThinking/patent), run `conda run -n agent pip install -e .`
  - rerun the same command after any dependency change in `pyproject.toml`
- Use either:
  - `PYTHONPATH=. conda run -n agent pytest ... -q`
  - `bash scripts/test.sh ...`
- Example:
  - `cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py -q`

---

## File Structure Map

### Existing files to modify

- `patent/pyproject.toml`
  - Declare the Chroma dependency required by the vector readers.
- `patent/server/patent/retrieval_models.py`
  - Expand from no-vector MVP models into dual-vector patent recall/evidence models.
- `patent/server/patent/retrieval_service.py`
  - Replace lexical-first retrieval core with abstract/chunk vector recall, fusion, and internal evidence/original-locator packaging.
- `patent/server/patent/original_models.py`
  - Tighten request/response models around archive-root resolution and section-only figure semantics.
- `patent/server/patent/original_service.py`
  - Resolve structured content from the patent archive root by `canonical_patent_id`.
- `patent/server/patent/result_builder.py`
  - Normalize execution results into caller-facing reference/reference-link/original-link payloads.
- `patent/server/patent/cache_keys.py`
  - Add dual-index retrieval cache key components if not already present.
- `patent/server/services/execution_cache.py`
  - Support the retrieval/original cache payload shapes needed by the new services.
- `patent/server/services/ask_service.py`
  - Wire caller-facing sync/SSE ask responses to the updated retrieval and result contracts.
- `patent/server/services/chat_persistence.py`
  - Map patent execution results into authority user-write and assistant-accept payloads.
- `patent/server/services/conversation_authority_client.py`
  - Send patent-specific authority fields required for durable accept.
- `patent/server/schemas/authority_models.py`
  - Carry the patent-specific authority request/final-event schema.
- `patent/server/patent/pipeline.py`
  - Surface vector retrieval metadata and build internal execution payloads for downstream result/persistence adapters.
- `patent/server/patent/executor.py`
  - Orchestrate exact-id, abstract recall, chunk recall, fusion, and not-found fallback.
- `patent/server_fastapi/routers/original.py`
  - Enforce the updated original-view contract against the archive-root resolver.

### Existing tests to modify

- `patent/tests/test_patent_retrieval_service.py`
- `patent/tests/test_patent_executor.py`
- `patent/tests/test_original_service.py`
- `patent/tests/fastapi_contract/test_original_contract.py`
- `patent/tests/fastapi_contract/test_ask_contract.py`
- `patent/tests/test_execution_cache.py`
- `patent/tests/test_chat_persistence.py`
- `patent/tests/test_conversation_authority_client.py`

### New files to create

- `patent/server/patent/resource_registry.py`
  - Resolve the current patent resource roots and collection names from the checked-in repo layout.
- `patent/server/patent/identity_registry.py`
  - Resolve alternate patent identifiers into `canonical_patent_id` using the patent identity registry rules.
- `patent/server/patent/chroma_readers.py`
  - Thin, testable adapters for abstract/chunk Chroma collections.
- `patent/tests/test_resource_registry.py`
  - Resource root and collection resolution tests.
- `patent/tests/test_identity_registry.py`
  - Alternate-id resolution and canonicalization tests.

---

## Delivery Order

Implement in this order:

1. Patent vector dependency and resource-root configuration
2. Patent identity registry and canonicalization
3. Abstract vector recall adapter
4. Chunk vector recall adapter
5. Dual-vector fusion and evidence packaging
6. No-vector fallback preservation when vector resources are missing
7. Archive-root original-view resolver
8. Ask/executor integration and final response metadata
9. Patent-side authority persistence mapping
10. Regression and contract verification

This order keeps retrieval and original-view independently testable before ask-path integration.

## External Rollout Dependencies

The plan below only writes inside `patent/`, but execution is not production-ready unless these non-`patent/` handoffs are tracked explicitly:

- Gateway handoff:
  - canonical emitted viewer path from `patentQA` is `/api/patent/original/{canonical_patent_id}?section=...`
  - gateway must expose both `/api/patent/original/{canonical_patent_id}` and `/api/v1/patent/original/{canonical_patent_id}`
  - both gateway routes belong to the `document-proxy` route family
  - gateway must support `GET` and `HEAD`
  - upstream target is the `patentQA` original-view endpoint
  - passthrough semantics must preserve:
    - auth headers
    - redirect / html / json / text / streaming body
    - `Content-Type`, `Cache-Control`, `ETag` and equivalent cache/content headers
  - the proxy path does not participate in QA ask quota finalize logic
  - backend ownership is exception-routed to:
    - `X-Gateway-Backend: patent`
    - target backend = `patentQA`
- Public-service handoff:
  - patent-side authority payload changes in Task 9 are only half of the durable rollout
  - `public-service` still needs the allowlist/schema/materializer/replay work defined in the approved spec
  - this is an external dependency, not part of the `patent/` write scope
- Compatibility-route rollout blocker:
  - for `requested_mode=patent` with `turn_mode in {file_only, mixed}`, gateway must reroute to `fastQA`
  - gateway owns the authority-facing tuple rewrite to `requested_mode=fast` and `actual_mode=fast`
  - gateway also owns the caller-facing recovery of `requested_mode=patent` with `actual_mode=fast`
  - gateway must preserve provenance in `options.mode_origin.*`
  - `fastQA` must map inbound `options.mode_origin.*` into:
    - authority `context_hints.mode_origin_*`
    - authority `final_event.metadata.mode_origin`
  - `public-service` must durably materialize and replay the same provenance fields
  - compatibility-routed patent file/mixed turns are not rollout-ready until gateway rewrite, fastQA authority mapping, and public-service replay support are all shipped
  - this is a cross-service rollout dependency, not part of the `patent/` write scope

---

## Task 1: Declare Vector Dependency And Introduce Patent Resource Registry

**Files:**
- Modify: `patent/pyproject.toml`
- Create: `patent/server/patent/resource_registry.py`
- Test: `patent/tests/test_resource_registry.py`

- [ ] **Step 1: Define dependency ownership and write the failing tests for patent resource-root resolution**

Cover:
- `chromadb` dependency is declared in `patent/pyproject.toml`
- patent data resource resolution includes the `patent_identity_registry` dataset location
- abstract DB root resolves to `resource/patentQA/vector_db_patent_abstracts`
- chunk DB root resolves to `resource/patentQA/vector_db_patent_chunks`
- original archive root resolves to `resource/patentQA/__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_`
- collection names resolve to `patent_abstracts` and `patent_chunks`
- resolution is anchored to repo-root-relative checked-in paths, not generalized env overrides

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_resource_registry.py -q`
Expected: FAIL because the registry module does not exist yet

- [ ] **Step 3: Implement the minimal resource registry**

Implement:
- minimal `chromadb` dependency declaration in `patent/pyproject.toml`
- helper to resolve fixed checked-in resource paths under repo root
- identity-registry dataset resolver in the same checked-in patent data layout
- explicit collection names:
  - `patent_abstracts`
  - `patent_chunks`
- archive root resolver keyed by `canonical_patent_id`
- no config/env abstraction layer for interchangeable resource sources in this phase

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_resource_registry.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml server/patent/resource_registry.py tests/test_resource_registry.py
git commit -m "feat: add patent resource registry"
```

## Task 2: Add Patent Identity Registry And Canonicalization

**Files:**
- Create: `patent/server/patent/identity_registry.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Test: `patent/tests/test_identity_registry.py`
- Test: `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Write failing tests for alternate-id resolution and canonicalization**

Cover:
- `publication_number`, `patent_number`, and `application_number` inputs all resolve through the identity registry
- exact-id short-circuit returns only `canonical_patent_id`, never a pseudo id
- only unique active records are allowed to resolve
- inactive hits are rejected
- ambiguous matches fail with clarification/error semantics instead of inventing IDs
- vector candidates carrying raw `patent_id` are normalized into `canonical_patent_id` before evidence packaging
- no identifier bypasses the identity registry rules
- registry rows carry the minimum contract fields required by the spec, including `is_active`

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_identity_registry.py tests/test_patent_retrieval_service.py -q`
Expected: FAIL because explicit identity-registry ownership is not implemented yet

- [ ] **Step 3: Implement the identity registry module and retrieval integration**

Implement:
- identity-registry resolver module with canonicalization helpers
- loading/parsing of the `patent_identity_registry` dataset from the checked-in patent data layer
- exact-id lookup path bound to identity-registry resolution
- `patent_id -> canonical_patent_id` translation before evidence packaging
- active-only resolution and explicit error handling for missing, inactive, and ambiguous matches

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_identity_registry.py tests/test_patent_retrieval_service.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/patent/identity_registry.py server/patent/retrieval_service.py tests/test_identity_registry.py tests/test_patent_retrieval_service.py
git commit -m "feat: add patent identity canonicalization"
```

## Task 3: Add Abstract Vector Recall Adapter

**Files:**
- Create: `patent/server/patent/chroma_readers.py`
- Modify: `patent/server/patent/retrieval_models.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Test: `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Write failing tests for abstract patent-level recall**

Cover:
- abstract collection query returns `patent_id`, score, kind, source_json
- abstract recall maps patent IDs to canonical patent IDs
- abstract recall metadata is captured in retrieval metadata
- dual-index version participates in returned metadata

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py -q`
Expected: FAIL because only the current MVP retrieval path exists

- [ ] **Step 3: Implement the abstract Chroma reader and retrieval model additions**

Implement:
- abstract recall candidate model
- Chroma collection reader abstraction
- abstract recall path in retrieval service
- `retrieval_mode=abstract_vector` support

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py -q`
Expected: abstract recall tests PASS; chunk/fusion tests still pending

- [ ] **Step 5: Commit**

```bash
git add server/patent/chroma_readers.py server/patent/retrieval_models.py server/patent/retrieval_service.py tests/test_patent_retrieval_service.py
git commit -m "feat: add patent abstract vector recall"
```

## Task 4: Add Chunk Vector Recall Adapter

**Files:**
- Modify: `patent/server/patent/chroma_readers.py`
- Modify: `patent/server/patent/retrieval_models.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Test: `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Write failing tests for chunk recall**

Cover:
- chunk collection query returns `patent_id`, `source_file`, `chunk_index`, `json_stem`
- `source_file=权利要求.json` maps to `section_type=claim`
- `source_file=说明书.json` maps to `section_type=description`
- chunk recall supports candidate patent filtering or equivalent post-filter behavior

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py -q`
Expected: FAIL because chunk recall path is not implemented

- [ ] **Step 3: Implement the chunk recall path**

Implement:
- chunk recall candidate model
- source-file to section-type mapping
- section-only figure exclusion for v1 retrieval
- `retrieval_mode=chunk_vector` support

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py -q`
Expected: PASS for chunk recall behavior

- [ ] **Step 5: Commit**

```bash
git add server/patent/chroma_readers.py server/patent/retrieval_models.py server/patent/retrieval_service.py tests/test_patent_retrieval_service.py
git commit -m "feat: add patent chunk vector recall"
```

## Task 5: Implement Dual-Vector Fusion And Patent Evidence Packaging

**Files:**
- Modify: `patent/server/patent/retrieval_models.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/services/execution_cache.py`
- Test: `patent/tests/test_patent_retrieval_service.py`
- Test: `patent/tests/test_execution_cache.py`

- [ ] **Step 1: Write failing tests for hybrid fusion**

Cover:
- exact identifier still short-circuits to a single patent
- abstract topN + chunk topK fuse to `retrieval_mode=abstract_chunk_hybrid`
- chunk evidence packages to stable internal `PatentEvidence` / locator models
- `patent_dir` is ignored for runtime original resolution
- retrieval cache key includes abstract and chunk index versions
- internal fusion output is executor-ready but not yet responsible for caller-facing `references/reference_objects/reference_links/original_links`

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_execution_cache.py -q`
Expected: FAIL because fusion and dual-index cache semantics are incomplete

- [ ] **Step 3: Implement minimal fusion logic**

Implement:
- abstract/chunk score fusion
- question-intent weighting for claim vs description
- exact-id in-patent chunk restriction
- dual-index retrieval cache payload and key composition
- internal evidence/original locator packaging only; leave caller-facing `references/reference_objects/reference_links/original_links` assembly to Task 8 `result_builder`

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_execution_cache.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/patent/retrieval_models.py server/patent/retrieval_service.py server/patent/cache_keys.py server/services/execution_cache.py tests/test_patent_retrieval_service.py tests/test_execution_cache.py
git commit -m "feat: add patent dual-vector fusion"
```

## Task 6: Preserve No-Vector Fallback Modes

**Files:**
- Modify: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/pipeline.py`
- Test: `patent/tests/test_patent_retrieval_service.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write failing tests for vector-resource-missing fallback**

Cover:
- missing abstract DB falls back without crashing
- missing chunk DB falls back without crashing
- both vector DBs missing fall back to canonical no-vector modes
- returned `retrieval_backend` / `retrieval_mode` preserve approved values:
  - `metadata_lexical`
  - `fulltext_lexical`
  - `hybrid_no_vector`
- cache keys remain canonical for the no-vector path

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL because the current plan has not yet preserved spec-defined degradation behavior

- [ ] **Step 3: Implement minimal fallback preservation**

Implement:
- vector resource availability checks in retrieval orchestration
- fallback routing to existing no-vector retrieval stages
- canonical fallback metadata and cache-key composition
- no-vector result packaging compatible with later ask-path wiring

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/patent/retrieval_service.py server/patent/cache_keys.py server/patent/executor.py server/patent/pipeline.py tests/test_patent_retrieval_service.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: preserve patent no-vector fallback modes"
```

## Task 7: Resolve Original Content From Archive Root

**Files:**
- Modify: `patent/server/patent/original_models.py`
- Modify: `patent/server/patent/original_service.py`
- Modify: `patent/server_fastapi/routers/original.py`
- Test: `patent/tests/test_original_service.py`
- Test: `patent/tests/fastapi_contract/test_original_contract.py`

- [ ] **Step 1: Write failing tests for archive-root original-view resolution**

Cover:
- resolve by `canonical_patent_id` under the current archive root
- `claim` reads `权利要求.json`
- `description` reads `说明书.json`
- `abstract` reads `著录项目.json` or equivalent abstract source
- `figure` is section-only and resolves from archive-root figure assets (`摘要附图` / `全文附图`)
- `fulltext` prefers local PDF viewer and falls back to provider redirect
- source priority is fixed:
  - structured JSON
  - local PDF
  - provider redirect
- `viewer_uri` always uses the gateway contract path `/api/patent/original/{canonical_patent_id}?section=...`
- local `patentQA` router exposes both `/api/patent/original/{canonical_patent_id}` and `/api/v1/patent/original/{canonical_patent_id}`
- local `patentQA` router supports both `GET` and `HEAD`
- no `patentQA` local URL or filesystem path is ever returned to callers
- absolute `patent_dir` metadata is never trusted as file-open path

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_original_service.py tests/fastapi_contract/test_original_contract.py -q`
Expected: FAIL because original-view still relies on stub behavior

- [ ] **Step 3: Implement archive-root original resolver**

Implement:
- `canonical_patent_id -> current archive root` resolution
- section-specific source selection
- source priority enforcement:
  - structured JSON first for `claim`, `description`, `abstract`
  - archive-root figure assets (`摘要附图` / `全文附图`) for `figure`
  - local PDF second
  - provider redirect last
- claim/paragraph section-only fallback behavior
- figure section-only behavior
- `section=fulltext` behavior backed by local PDF or provider redirect
- router contract coverage for both `/api` and `/api/v1` original-view paths
- explicit `HEAD` semantics on the original-view routes
- gateway-relative `viewer_uri` generation only
- no filesystem open path derived from chunk metadata

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_original_service.py tests/fastapi_contract/test_original_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/patent/original_models.py server/patent/original_service.py server_fastapi/routers/original.py tests/test_original_service.py tests/fastapi_contract/test_original_contract.py
git commit -m "feat: resolve patent originals from archive root"
```

## Task 8: Integrate Retrieval Results Into Ask Path

**Files:**
- Modify: `patent/server/patent/pipeline.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/result_builder.py`
- Modify: `patent/server/services/ask_service.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write failing tests for caller-facing ask integration**

Cover:
- sync ask returns vector-backed `references/reference_objects/reference_links/original_links`
- SSE event family remains complete:
  - `metadata`
  - `thinking`
  - `step`
  - `content`
  - `error`
  - `done`
- sync response includes the full required caller-facing envelope:
  - `success`
  - `final_answer`
  - `query_mode`
  - `route`
  - `requested_mode`
  - `actual_mode`
  - `source_scope`
  - `timings`
  - `metadata`
  - `trace_id`
  - `used_files`
  - `file_selection`
- SSE `done` includes the full required envelope:
  - `final_answer`
  - `query_mode`
  - `route`
  - `requested_mode`
  - `actual_mode`
  - `source_scope`
  - `timings`
  - `references`
  - `reference_objects`
  - `reference_links`
  - `original_links`
  - `metadata`
  - `trace_id`
  - `used_files`
  - `file_selection`
- `metadata.retrieval_backend` and dual-index metadata surface caller-facing
- original links and `viewer_uri` always use gateway paths:
  - `/api/patent/original/{canonical_patent_id}?section=...`
- no caller-facing payload leaks `patentQA` local URLs or filesystem-derived paths
- no-vector fallback responses preserve the same caller-facing shape
- `references` remain unique canonical patent ids
- `reference_links` and `original_links` both satisfy the approved shape rules
- each referenced patent has at least one corresponding `original_link`
- `original_view` vs `provider_redirect` link shapes follow the approved contract invariants

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL because ask path still reflects the current MVP retrieval assumptions

- [ ] **Step 3: Implement ask-path integration**

Implement:
- executor wiring to the new retrieval service
- `result_builder` is the sole owner of caller-facing `references`, `reference_objects`, `reference_links`, and `original_links`
- pipeline result packaging for vector-backed evidence
- pipeline result packaging for no-vector fallback evidence
- full caller-facing sync/SSE contract fields:
  - `success` for sync
  - `final_answer`
  - `query_mode`
  - `route`
  - `requested_mode`
  - `actual_mode`
  - `source_scope`
  - `timings`
  - `metadata`
  - `trace_id`
  - `used_files`
  - `file_selection`
- full SSE event family preservation:
  - `metadata`
  - `thinking`
  - `step`
  - `content`
  - `error`
  - `done`
- response invariants for unique `references` and consistent original-link coverage
- response invariants for `reference_links` and `original_links` contract shape
- gateway-relative `original_links` / `viewer_uri` only

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/patent/pipeline.py server/patent/executor.py server/patent/result_builder.py server/services/ask_service.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: integrate patent vector retrieval into ask path"
```

## Task 9: Extend Patent Authority Persistence Mapping

**Files:**
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Modify: `patent/server/schemas/authority_models.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/test_conversation_authority_client.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write failing tests for patent-side durable authority mapping**

Cover:
- user-write carries `context_hints.mode_origin_*` when present
- assistant async outer envelope preserves:
  - `trace_id`
  - `source_service`
  - `route`
  - `requested_mode`
  - `actual_mode`
  - `idempotency_key`
- assistant accept sends:
  - `answer_text`
  - `steps`
  - `metadata`
  - `used_files=[]`
  - `timings`
  - authority-shaped `references(list[dict])`
  - `reference_objects`
  - `reference_links`
  - `original_links`
- caller-facing flat `references(list[str])` are adapted into authority `references(list[dict])`
- `reference_objects` remain a separate mapped field, not a substitute for authority `references`
- durable patent flow keeps the same canonical `canonical_patent_id` and gateway `viewer_uri`

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_chat_persistence.py tests/test_conversation_authority_client.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL because the updated retrieval/original payloads are not fully wired through the patent authority path yet

- [ ] **Step 3: Implement patent-side authority persistence mapping**

Implement:
- authority outbound schema support for patent final-event payload fields
- preservation of the authority async outer envelope:
  - `trace_id`
  - `source_service`
  - `route`
  - `requested_mode`
  - `actual_mode`
  - `idempotency_key`
- conversation authority client serialization of patent-specific final-event fields
- chat-persistence adapter mapping from execution result to authority final event
- preservation of the base authority `final_event` contract:
  - `answer_text`
  - `steps`
  - `metadata`
  - `references`
  - `reference_objects`
  - `reference_links`
  - `original_links`
  - `used_files=[]`
  - `timings`
- explicit note in code/docs that `public-service` rollout remains an external dependency

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_chat_persistence.py tests/test_conversation_authority_client.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/services/chat_persistence.py server/services/conversation_authority_client.py server/schemas/authority_models.py tests/test_chat_persistence.py tests/test_conversation_authority_client.py tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: extend patent authority persistence payloads"
```

## Task 10: Run Full Patent Regression And Release Verification

**Files:**
- No planned write-scope changes. If regressions fail, fix only files under `patent/`.

- [ ] **Step 1: Run the full relevant patent regression suite**

Run from [patent](/home/cqy/worktrees/highThinking/patent):

```bash
PYTHONPATH=. conda run -n agent pytest \
  tests/test_resource_registry.py \
  tests/test_identity_registry.py \
  tests/test_patent_retrieval_service.py \
  tests/test_patent_executor.py \
  tests/test_original_service.py \
  tests/test_execution_cache.py \
  tests/test_chat_persistence.py \
  tests/test_conversation_authority_client.py \
  tests/fastapi_contract/test_ask_contract.py \
  tests/fastapi_contract/test_original_contract.py -q
```

Expected: PASS

- [ ] **Step 2: Validate plan/spec conformance against the completed `patent/` changes**

Check:
- resource roots still point to checked-in `resource/patentQA/*`
- alternate identifiers still canonicalize through the identity registry before packaging
- original-view still honors `canonical_patent_id` and source priority
- ask contract still surfaces dual-index retrieval metadata and stable original links
- patent authority payloads still carry metadata plus reference/reference-link/original-link objects
- no writes escaped the `patent/` subtree

- [ ] **Step 3: Commit only if regression fixes were required inside `patent/`**

```bash
git add server server_fastapi tests pyproject.toml
git commit -m "fix: close patent regression gaps"
```

---

## Notes For Executors

- Do not reuse `fastQA` paper-domain `RetrievedChunk` or DOI-centric retrieval metadata.
- Do not trust vector DB `patent_dir` as a filesystem path; always resolve originals from the checked-in archive root.
- Do not add figure selectors in v1. Figure is section-only across request, evidence, original-view, and cache contracts.
- Keep TDD discipline: test first, verify failure, implement minimal code, rerun, then commit.
