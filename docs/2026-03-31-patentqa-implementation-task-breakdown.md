# PatentQA Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Phase 1 `patentQA` delivery defined in [2026-03-30-patentqa-delivery-spec.md](/home/cqy/worktrees/highThinking/docs/2026-03-30-patentqa-delivery-spec.md) so `patentQA` can serve ordinary patent QA, persist durable chats through `public-service`, support patent-original viewing, and preserve continuity for compatibility-routed file/mixed patent turns.

**Architecture:** Implement the normal `requested_mode=patent`, `actual_mode=patent`, `route=kb_qa`, `turn_mode=kb_only` path as a patent-owned caller-facing contract produced directly by `patentQA`. Keep durable transcript ownership in `public-service`, make `gateway` the contract owner only for compatibility-routed `patent -> fastQA` turns, and converge all Redis-backed transient coordination on a shared overlay and cache discipline.

**Tech Stack:** Python, FastAPI, Gunicorn, Pydantic, pytest, Redis, httpx, `conda` environment `agent`

---

## Constraints And References

**Primary spec and protocol references**
- Spec: [docs/2026-03-30-patentqa-delivery-spec.md](/home/cqy/worktrees/highThinking/docs/2026-03-30-patentqa-delivery-spec.md)
- Protocol baseline: [docs/2026-03-24-patentqa-gateway-public-service-protocol.md](/home/cqy/worktrees/highThinking/docs/2026-03-24-patentqa-gateway-public-service-protocol.md)
- Field contract baseline: [docs/2026-03-24-patentqa-field-contract.md](/home/cqy/worktrees/highThinking/docs/2026-03-24-patentqa-field-contract.md)
- QA module integration guide: [docs/2026-03-26-qa-module-integration-guide.md](/home/cqy/worktrees/highThinking/docs/2026-03-26-qa-module-integration-guide.md)

**Delivery constraints**
- Phase 1 `patentQA` only owns ordinary QA:
  - `requested_mode=patent`
  - `actual_mode=patent`
  - `route=kb_qa`
  - `turn_mode=kb_only`
- `file_only` and `mixed` patent turns remain compatibility-routed to `fastQA`
- Durable persistence truth source remains `public-service`
- Redis is only transient coordination/cache, not durable transcript storage
- Original-view public entrypoint must be gateway-facing and keyed by `canonical_patent_id`

**Recommended test invocation**
- `conda run -n agent pytest patent/tests/...`
- `conda run -n agent pytest public-service/backend/tests/...`
- `conda run -n agent pytest gateway/tests/...`
- `conda run -n agent pytest fastQA/tests/...`
- `conda run -n agent pytest highThinkingQA/tests/...`

---

## File Structure Map

### PatentQA

**Likely files to modify**
- `patent/server/schemas/request_models.py`
- `patent/server/schemas/response_models.py`
- `patent/server/schemas/authority_models.py`
- `patent/server/patent/result_builder.py`
- `patent/server/patent/pipeline.py`
- `patent/server/patent/executor.py`
- `patent/server/patent/cache_keys.py`
- `patent/server/services/ask_service.py`
- `patent/server/services/chat_persistence.py`
- `patent/server/services/conversation_authority_client.py`
- `patent/server/services/execution_cache.py`
- `patent/server_fastapi/app.py`
- `patent/server_fastapi/routers/ask.py`
- `patent/tests/fastapi_contract/test_ask_contract.py`
- `patent/tests/test_chat_persistence.py`
- `patent/tests/test_conversation_authority_client.py`
- `patent/tests/test_execution_cache.py`

**Likely files to create**
- `patent/server_fastapi/routers/original.py`
- `patent/server/patent/original_service.py`
- `patent/server/patent/original_models.py`
- `patent/server/patent/retrieval_service.py`
- `patent/server/patent/retrieval_models.py`
- `patent/tests/fastapi_contract/test_original_contract.py`
- `patent/tests/test_original_service.py`
- `patent/tests/test_patent_retrieval_service.py`

### Public-Service

**Likely files to modify**
- `public-service/backend/app/modules/conversation/authority_schemas.py`
- `public-service/backend/app/modules/conversation/internal_api.py`
- `public-service/backend/app/modules/conversation/service.py`
- `public-service/backend/tests/conftest.py`

**Likely files to create**
- `public-service/backend/tests/test_conversation_authority_patent.py`

### Gateway

**Likely files to modify**
- `gateway/app/routers/qa.py`
- `gateway/app/services/route_table.py`
- `gateway/app/main.py`

**Likely files to create**
- `gateway/app/routers/document_proxy.py`
- `gateway/tests/test_document_proxy.py`

### FastQA

**Likely files to modify**
- `fastQA/app/services/chat_persistence.py`
- `fastQA/app/services/conversation_authority_client.py`
- `fastQA/tests/test_chat_persistence.py`
- `fastQA/tests/test_conversation_authority_client.py`

### HighThinkingQA

**Likely files to modify**
- `highThinkingQA/server/services/chat_persistence.py`
- `highThinkingQA/server/services/redis_client.py`
- `highThinkingQA/tests/test_chat_persistence.py`

### Frontend

**Likely files to modify**
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/views/Home.vue`

**Likely files to create**
- `frontend-vue/src/utils/patentReferences.js`

---

## Delivery Order

Implement in this order:

1. `patentQA` normal caller-facing contract
2. `patentQA` original-view and retrieval MVP internals
3. `patentQA` durable authority write/read/accept expansion
4. `public-service` patent durable schema and replay
5. `gateway` original-view document proxy
6. `gateway` compatibility-route caller-facing rewrite
7. `fastQA` compatibility provenance write-through
8. shared overlay convergence across three QA backends
9. `frontend-vue` patent reference and original-link rendering
10. end-to-end verification and rollout gates

This order keeps the normal `patentQA` path deterministic before cross-service compatibility behavior is introduced.

---

## Task 1: Align PatentQA Normal Ask Contract To The Final Flat Caller-Facing Schema

**Files:**
- Modify: `patent/server/schemas/request_models.py`
- Modify: `patent/server/schemas/response_models.py`
- Modify: `patent/server/patent/result_builder.py`
- Modify: `patent/server_fastapi/routers/ask.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Add failing contract tests for the final sync and SSE shape**

Cover:
- flat sync success payload, not wrapped `data`
- `references=list[str]`
- `reference_objects=list[dict]`
- `reference_links=list[dict]`
- `original_links=list[dict]`
- `done` event includes `metadata`, `used_files`, `file_selection`

- [ ] **Step 2: Run the targeted contract tests and capture the failures**

Run: `conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL on wrapped sync shape and missing `original_links` / `done` fields

- [ ] **Step 3: Update request and response models to the final patent normal-turn contract**

Implement:
- strict normal-turn ingress invariants
- flat sync response schema
- sync/SSE `references/reference_objects/reference_links/original_links` typing separation
- sync/SSE `metadata` parity rules

- [ ] **Step 4: Update result builder and ask router emission**

Implement:
- sync body built directly in final caller-facing shape
- SSE `metadata` and `done` events emitted in final shape
- preserve `trace_id`, `requested_mode=patent`, `actual_mode=patent`, `source_scope=kb`

- [ ] **Step 5: Re-run targeted contract tests**

Run: `conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/schemas/request_models.py patent/server/schemas/response_models.py patent/server/patent/result_builder.py patent/server_fastapi/routers/ask.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: align patent normal ask contract"
```

## Task 2: Implement Patent Original-View Domain Models, Cache Keys, And FastAPI Endpoint

**Files:**
- Create: `patent/server_fastapi/routers/original.py`
- Create: `patent/server/patent/original_models.py`
- Create: `patent/server/patent/original_service.py`
- Modify: `patent/server_fastapi/routers/__init__.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/services/execution_cache.py`
- Test: `patent/tests/fastapi_contract/test_original_contract.py`
- Test: `patent/tests/test_original_service.py`
- Test: `patent/tests/test_execution_cache.py`

- [ ] **Step 1: Add failing tests for original-view route and cache anchor normalization**

Cover:
- `GET /api/patent/original/{canonical_patent_id}`
- `HEAD /api/patent/original/{canonical_patent_id}`
- `section=abstract|claim|description|figure|fulltext`
- anchor normalization:
  - `claim:<n>`
  - `paragraph:<id>`
  - `section:abstract`
  - `section:description`
  - `section:figure`
  - `fulltext`

- [ ] **Step 2: Run the targeted tests and verify failures**

Run: `conda run -n agent pytest patent/tests/fastapi_contract/test_original_contract.py patent/tests/test_original_service.py patent/tests/test_execution_cache.py -q`
Expected: FAIL because original-view route and cache family are incomplete

- [ ] **Step 3: Implement original-view models and service boundary**

Implement:
- original-view request/query parsing
- structured body vs redirect response models
- `viewer_uri` source-of-truth composition rules
- `original_version` participation in cache keys

- [ ] **Step 4: Implement patent-local original router**

Implement:
- route registration in `server_fastapi.routers.__init__`
- `GET` and `HEAD`
- pass-through capable response handling for `html/json/text/redirect`

- [ ] **Step 5: Re-run targeted original-view tests**

Run: `conda run -n agent pytest patent/tests/fastapi_contract/test_original_contract.py patent/tests/test_original_service.py patent/tests/test_execution_cache.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server_fastapi/routers/original.py patent/server/patent/original_models.py patent/server/patent/original_service.py patent/server_fastapi/routers/__init__.py patent/server/patent/cache_keys.py patent/server/services/execution_cache.py patent/tests/fastapi_contract/test_original_contract.py patent/tests/test_original_service.py patent/tests/test_execution_cache.py
git commit -m "feat: add patent original view contract"
```

## Task 3: Implement Patent Retrieval MVP Without Vector DB

**Files:**
- Create: `patent/server/patent/retrieval_models.py`
- Create: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server/patent/pipeline.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/services/execution_cache.py`
- Test: `patent/tests/test_patent_retrieval_service.py`
- Test: `patent/tests/test_patent_executor.py`

- [ ] **Step 1: Add failing tests for no-vector retrieval backend selection**

Cover:
- exact identifier resolve
- metadata lexical retrieval
- fulltext lexical retrieval
- `retrieval_backend`, `retrieval_version`, `catalog_index_version`
- retrieval cache hit/miss behavior
- negative cache behavior for not-found identifier and retrieval misses

- [ ] **Step 2: Run the retrieval tests and verify failures**

Run: `conda run -n agent pytest patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_executor.py -q`
Expected: FAIL because retrieval orchestration is still stubbed

- [ ] **Step 3: Implement retrieval models and MVP orchestration**

Implement:
- `patent_identity_registry` resolve path
- `patent_catalog_index` candidate generation path
- evidence packaging into `reference_objects`
- `original_links` derivation from evidence positions

- [ ] **Step 4: Wire retrieval and caches into pipeline/executor**

Implement:
- `normalized_request_key`
- `normalized_query_key`
- retrieval cache and negative cache writes only on successful stages

- [ ] **Step 5: Re-run retrieval tests**

Run: `conda run -n agent pytest patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_executor.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/retrieval_models.py patent/server/patent/retrieval_service.py patent/server/patent/pipeline.py patent/server/patent/executor.py patent/server/patent/cache_keys.py patent/server/services/execution_cache.py patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_executor.py
git commit -m "feat: add patent no-vector retrieval mvp"
```

## Task 4: Expand PatentQA Durable Authority Models, Client, And Persistence Flow

**Files:**
- Modify: `patent/server/schemas/authority_models.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/ask_service.py`
- Test: `patent/tests/test_conversation_authority_client.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Add failing tests for patent authority payload expansion**

Cover:
- `final_event.metadata`
- authority `references=list[dict]`
- `reference_objects`
- `reference_links`
- `original_links`
- durable success only after assistant accept success

- [ ] **Step 2: Run the authority and persistence tests**

Run: `conda run -n agent pytest patent/tests/test_conversation_authority_client.py patent/tests/test_chat_persistence.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL on missing authority fields and incomplete persistence mapping

- [ ] **Step 3: Expand authority schemas and HTTP client**

Implement:
- `AuthorityContextHints` future-proof field support
- `AuthorityAssistantFinalEvent.metadata`
- full patent evidence and original-link payload transmission

- [ ] **Step 4: Update durable ask flow**

Implement:
- result summary to authority final-event mapping
- accept-before-success enforcement for sync and stream
- cached result writes only after successful accept

- [ ] **Step 5: Re-run the targeted persistence tests**

Run: `conda run -n agent pytest patent/tests/test_conversation_authority_client.py patent/tests/test_chat_persistence.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/schemas/authority_models.py patent/server/services/conversation_authority_client.py patent/server/services/chat_persistence.py patent/server/services/ask_service.py patent/tests/test_conversation_authority_client.py patent/tests/test_chat_persistence.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: expand patent durable authority flow"
```

## Task 5: Roll Out Public-Service Patent Authority Schema, Materializer, And Replay

**Files:**
- Modify: `public-service/backend/app/modules/conversation/authority_schemas.py`
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Create: `public-service/backend/tests/test_conversation_authority_patent.py`

- [ ] **Step 1: Add failing public-service tests for patent authority acceptance**

Cover:
- allow `source_service=patentQA`
- allow `requested_mode=patent`, `actual_mode=patent`
- accept `final_event.metadata`
- accept `original_links`
- persist and replay `metadata.mode_origin.*`
- persist and replay assistant `original_links`

- [ ] **Step 2: Run the targeted public-service tests**

Run: `conda run -n agent pytest public-service/backend/tests/test_conversation_authority_patent.py -q`
Expected: FAIL because patent authority literals and replay fields are not yet supported

- [ ] **Step 3: Expand authority schemas and internal API validation**

Implement:
- source-service allowlist expansion
- patent mode literals
- user-write `context_hints.mode_origin_*`
- assistant `final_event.metadata` and `original_links`

- [ ] **Step 4: Update materialization and transcript/detail replay**

Implement:
- durable write-through of patent assistant evidence fields
- durable replay of `original_links`
- durable replay of `metadata.mode_origin.*`
- durable replay of user `context_hints.mode_origin_*`

- [ ] **Step 5: Re-run public-service tests**

Run: `conda run -n agent pytest public-service/backend/tests/test_conversation_authority_patent.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/conversation/authority_schemas.py public-service/backend/app/modules/conversation/internal_api.py public-service/backend/app/modules/conversation/service.py public-service/backend/tests/test_conversation_authority_patent.py
git commit -m "feat: add patent authority support to public-service"
```

## Task 6: Add Gateway Document-Proxy Family For Patent Original View

**Files:**
- Create: `gateway/app/routers/document_proxy.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/app/services/route_table.py`
- Create: `gateway/tests/test_document_proxy.py`

- [ ] **Step 1: Add failing gateway tests for patent original-view proxying**

Cover:
- `/api/patent/original/{canonical_patent_id}`
- `/api/v1/patent/original/{canonical_patent_id}`
- `GET` and `HEAD`
- `X-Gateway-Backend: patent`
- target backend is `patentQA`, not `public`
- preserve `Content-Type`, `Cache-Control`, `ETag`, redirect behavior

- [ ] **Step 2: Run the document-proxy tests**

Run: `conda run -n agent pytest gateway/tests/test_document_proxy.py gateway/tests/test_route_table.py -q`
Expected: FAIL because `document-proxy` family does not exist yet

- [ ] **Step 3: Implement the dedicated document-proxy router**

Implement:
- separate router family from existing `public_proxy`
- route ownership entries in `route_table`
- patent backend forwarding through proxy service

- [ ] **Step 4: Register router and preserve passthrough semantics**

Implement:
- gateway app router registration
- path and query passthrough
- auth and trace propagation
- no QA quota finalize coupling

- [ ] **Step 5: Re-run gateway document-proxy tests**

Run: `conda run -n agent pytest gateway/tests/test_document_proxy.py gateway/tests/test_route_table.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gateway/app/routers/document_proxy.py gateway/app/main.py gateway/app/services/route_table.py gateway/tests/test_document_proxy.py
git commit -m "feat: add patent document proxy routes"
```

## Task 7: Make Gateway The Caller-Facing Owner For Compatibility-Routed Patent File/Mixed Turns

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/tests/test_qa_proxy.py`

- [ ] **Step 1: Add failing gateway tests for compatibility-route rewrite**

Cover:
- upstream payload rewritten to `requested_mode=fast`, `actual_mode=fast`
- upstream `options.mode_origin.*` injected
- sync wrapped body rewritten back to caller-facing `requested_mode=patent`, `actual_mode=fast`
- SSE `metadata`, `error`, `done` frames rewritten with `metadata.mode_origin.*`

- [ ] **Step 2: Run the gateway compatibility tests**

Run: `conda run -n agent pytest gateway/tests/test_qa_proxy.py -q`
Expected: FAIL because gateway currently mostly passthroughs patent responses

- [ ] **Step 3: Implement compatibility upstream rewrite**

Implement:
- identify `requested_mode=patent` with `turn_mode in {file_only, mixed}`
- rewrite upstream mode tuple to `fast/fast`
- inject `options.mode_origin.requested_mode=patent`
- inject `options.mode_origin.execution_backend=fastQA`
- inject `options.mode_origin.compatibility_route=true`

- [ ] **Step 4: Implement caller-facing sync and SSE rewrite**

Implement:
- preserve upstream file/mixed response family
- rewrite top-level and nested mode fields
- inject or rewrite `metadata.mode_origin.*` in sync and SSE

- [ ] **Step 5: Re-run gateway compatibility tests**

Run: `conda run -n agent pytest gateway/tests/test_qa_proxy.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gateway/app/routers/qa.py gateway/tests/test_qa_proxy.py
git commit -m "feat: rewrite compatibility-routed patent responses"
```

## Task 8: Make FastQA Persist Compatibility Provenance To Public-Service

**Files:**
- Modify: `fastQA/app/services/chat_persistence.py`
- Modify: `fastQA/app/services/conversation_authority_client.py`
- Modify: `fastQA/tests/test_chat_persistence.py`
- Modify: `fastQA/tests/test_conversation_authority_client.py`

- [ ] **Step 1: Add failing fastQA tests for compatibility provenance mapping**

Cover:
- read inbound `options.mode_origin.*`
- map to user-write `context_hints.mode_origin_*`
- map to assistant `final_event.metadata.mode_origin`
- preserve existing fast file/mixed behavior when `mode_origin` is absent

- [ ] **Step 2: Run the fastQA persistence tests**

Run: `conda run -n agent pytest fastQA/tests/test_chat_persistence.py fastQA/tests/test_conversation_authority_client.py -q`
Expected: FAIL because fastQA authority payloads cannot yet transmit provenance metadata

- [ ] **Step 3: Expand fastQA authority client payloads**

Implement:
- optional `context_hints.mode_origin_*`
- optional assistant `metadata.mode_origin`
- backward compatibility for non-patent turns

- [ ] **Step 4: Update chat persistence mapping**

Implement:
- extract `mode_origin` from inbound payload options
- write-through on user write and assistant accept

- [ ] **Step 5: Re-run fastQA provenance tests**

Run: `conda run -n agent pytest fastQA/tests/test_chat_persistence.py fastQA/tests/test_conversation_authority_client.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/services/chat_persistence.py fastQA/app/services/conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_conversation_authority_client.py
git commit -m "feat: persist patent compatibility provenance in fastqa"
```

## Task 9: Converge Shared Pending Overlay Across PatentQA, FastQA, And HighThinkingQA

**Files:**
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/services/execution_cache.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `fastQA/app/services/pending_overlay.py`
- Modify: `fastQA/tests/test_chat_persistence.py`
- Modify: `highThinkingQA/server/services/chat_persistence.py`
- Modify: `highThinkingQA/server/services/redis_client.py`
- Modify: `patent/tests/test_execution_cache.py`
- Modify: `patent/tests/test_chat_persistence.py`
- Modify: `highThinkingQA/tests/test_chat_persistence.py`

- [ ] **Step 1: Add failing tests for shared overlay key family and convergence**

Cover:
- shared key family `pending:conversation:assistant:{user_id}:{conversation_id}`
- convergence by `last_assistant_trace_id`
- no duplicate overlay append
- `highThinkingQA` Redis-backed overlay replacing file-backed overlay

- [ ] **Step 2: Run overlay tests across the three services**

Run: `conda run -n agent pytest patent/tests/test_execution_cache.py patent/tests/test_chat_persistence.py highThinkingQA/tests/test_chat_persistence.py fastQA/tests/test_chat_persistence.py -q`
Expected: FAIL because current key families and storage backends diverge

- [ ] **Step 3: Align patent and fastQA shared overlay semantics**

Implement:
- patent key family migration
- patent cache helpers for shared overlay
- maintain current fastQA merge semantics while fixing family alignment

- [ ] **Step 4: Replace highThinking file-backed overlay with Redis-backed shared overlay**

Implement:
- Redis-backed overlay load/store/clear
- same convergence rules and TTL as the other QA backends

- [ ] **Step 5: Re-run overlay tests**

Run: `conda run -n agent pytest patent/tests/test_execution_cache.py patent/tests/test_chat_persistence.py highThinkingQA/tests/test_chat_persistence.py fastQA/tests/test_chat_persistence.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/cache_keys.py patent/server/services/execution_cache.py patent/server/services/chat_persistence.py fastQA/app/services/pending_overlay.py fastQA/tests/test_chat_persistence.py highThinkingQA/server/services/chat_persistence.py highThinkingQA/server/services/redis_client.py patent/tests/test_execution_cache.py patent/tests/test_chat_persistence.py highThinkingQA/tests/test_chat_persistence.py
git commit -m "feat: converge shared assistant overlay across qa services"
```

## Task 10: Frontend Patent Reference Rendering And Original-Link Jump Support

**Files:**
- Create: `frontend-vue/src/utils/patentReferences.js`
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/views/Home.vue`

- [ ] **Step 1: Add a failing frontend coverage target for patent references**

Cover:
- non-DOI `references=list[string]` must not be dropped
- `reference_objects` with `canonical_patent_id` must remain visible in normalized messages
- `reference_links` and `original_links` must both survive sync and stream finalization
- patent original jump uses gateway-facing `viewer_uri`

- [ ] **Step 2: Build the frontend and capture current incompatibilities**

Run: `cd frontend-vue && npm run build`
Expected: build may pass, but manual inspection should confirm current normalization still assumes DOI-centric references and stream finalization does not ingest `original_links`

- [ ] **Step 3: Implement patent-aware reference normalization helpers**

Implement:
- DOI and patent references handled as separate reference kinds
- `canonical_patent_id`-based display normalization
- extraction of `reference_links` and `original_links` from sync payloads and SSE `done`

- [ ] **Step 4: Wire patent-aware rendering into message hydration and streaming finalization**

Implement:
- `api.js` message normalization for patent references
- `Home.vue` stream `done` finalization for `original_links`
- stable click/jump behavior for gateway-facing patent `viewer_uri`

- [ ] **Step 5: Rebuild and manually verify patent jumps**

Run: `cd frontend-vue && npm run build`
Expected: PASS

Manual verification:
- sync patent answer displays patent references
- stream patent answer displays patent references after `done`
- clicking patent original link opens gateway patent original endpoint

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/utils/patentReferences.js frontend-vue/src/services/api.js frontend-vue/src/views/Home.vue
git commit -m "feat: add frontend patent reference support"
```

## Task 11: End-To-End Verification, Performance Budget Checks, And Rollout Gates

**Files:**
- Modify: `patent/README.md`
- Create: `docs/2026-03-31-patentqa-rollout-checklist.md`
- Create: `docs/2026-03-31-patentqa-verification-notes.md`

- [ ] **Step 1: Add a rollout checklist covering dependency order**

Cover:
- `patentQA` normal-turn rollout gates
- `public-service` schema rollout gate
- `gateway` document-proxy and compatibility-route gate
- `fastQA` provenance gate
- shared overlay migration gate

- [ ] **Step 2: Run the final targeted verification matrix**

Run:
- `conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_original_contract.py patent/tests/test_chat_persistence.py patent/tests/test_patent_executor.py -q`
- `conda run -n agent pytest public-service/backend/tests/test_conversation_authority_patent.py -q`
- `conda run -n agent pytest gateway/tests/test_document_proxy.py gateway/tests/test_qa_proxy.py -q`
- `conda run -n agent pytest fastQA/tests/test_chat_persistence.py fastQA/tests/test_conversation_authority_client.py -q`
- `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py -q`
- `cd frontend-vue && npm run build`
Expected: PASS

- [ ] **Step 3: Perform a manual end-to-end dry run**

Exercise:
- ordinary patent durable sync ask
- ordinary patent durable stream ask
- gateway original-view request
- compatibility-routed patent file/mixed turn
- replayed transcript and overlay continuity

- [ ] **Step 4: Document performance and fallback acceptance**

Record:
- cache hit/miss timings
- Redis unavailable behavior
- authority unavailable behavior
- provider redirect behavior for original-view

- [ ] **Step 5: Commit**

```bash
git add patent/README.md docs/2026-03-31-patentqa-rollout-checklist.md docs/2026-03-31-patentqa-verification-notes.md
git commit -m "docs: add patentqa rollout and verification notes"
```

---

## Parallelization Recommendation

After Task 1 and Task 4 freeze the normal `patentQA` contract, the following can run in parallel with disjoint write ownership:

- Worker A: Task 5 `public-service`
- Worker B: Task 6 and Task 7 `gateway`
- Worker C: Task 8 `fastQA`
- Worker D: Task 9 `highThinkingQA` and patent overlay alignment

Do not parallelize these together:

- Task 1 with Task 4
- Task 6 with Task 7 in separate workers unless router ownership is explicitly split
- Task 8 with any other worker editing `fastQA/app/services/conversation_authority_client.py`

---

## Review Checkpoints

Run review after each of these milestones:

1. after Task 1 to confirm the final patent normal-turn caller-facing contract is frozen correctly
2. after Task 4 and Task 5 together to confirm durable authority and transcript replay are aligned
3. after Task 6, Task 7, and Task 8 together to confirm compatibility-route and document-proxy behavior are aligned
4. after Task 9 and Task 10 to confirm shared overlay convergence and frontend jump behavior
5. after Task 11 to confirm rollout safety and final verification coverage

Reviewer prompt should always include:

- plan path: `docs/2026-03-31-patentqa-implementation-task-breakdown.md`
- spec path: `docs/2026-03-30-patentqa-delivery-spec.md`
- instruction to focus on blocker/major plan issues first

---

## Exit Criteria

This plan is complete only when all of the following are true:

- normal `patentQA` asks return the final flat sync/SSE contract from `patentQA`
- `public-service` durably accepts and replays patent evidence and original-link metadata
- gateway serves patent original-view through the dedicated `document-proxy` family
- compatibility-routed `patent -> fastQA` turns preserve durable provenance and caller-facing patent metadata
- shared assistant overlay semantics converge across `patentQA`, `fastQA`, and `highThinkingQA`
- the end-to-end verification matrix passes in the `agent` conda environment
