# Patent File/Hybrid Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `patent` file/hybrid streaming spec so `pdf_qa` keeps real final-answer streaming, `tabular_qa` becomes protocol-honest, and `hybrid_qa` can emit gated source previews plus a final unified answer without modifying `fastQA` or `patent` ordinary non-file QA.

**Architecture:** Add a capability-gated streaming protocol extension inside the `patent` ask pipeline, then route file/hybrid chunks through lightweight wrappers that assign `final` versus `preview` semantics without changing the outer SSE envelope shape. Update the Vue client only for `patent` file/hybrid routes so it advertises capability, buckets preview streams by `content_stream_id`, and keeps the main answer panel reserved for `final` content.

**Tech Stack:** FastAPI, Pydantic, Python service callbacks, SSE, Vue 3, Pinia, existing `patent` file-route pipeline, existing `frontend-vue` streaming consumer

---

## Source Documents

- Spec: `docs/2026-04-13-patent-file-hybrid-streaming-spec.md`
- Current SSE router: `patent/server_fastapi/routers/ask.py`
- Current ask service: `patent/server/services/ask_service.py`
- Current result builder: `patent/server/patent/result_builder.py`
- Current response models: `patent/server/schemas/response_models.py`
- Current file executor: `patent/server/patent/executor.py`
- Current file routes: `patent/server/patent/file_routes.py`
- Current PDF path: `patent/server/patent/pdf_service.py`
- Current tabular path: `patent/server/patent/tabular_service.py`
- Current frontend stream sender: `frontend-vue/src/services/api.js`
- Current frontend stream consumer: `frontend-vue/src/views/Home.vue`

## Hard Constraints

1. Do not modify any file under `fastQA/`.
2. Do not change `patent` standalone `kb_qa` behavior.
3. Do not change `patent` ordinary non-file QA behavior.
4. Do not rely on old frontends ignoring new fields; preview events must be server-gated.
5. Cache-hit behavior must stay safe: final-answer replay only, no preview reconstruction.
6. Any test, backend restart, or end-to-end curl run must be executed with escalated permissions when implementation starts.

## File Map

### Backend Protocol Surface

- Modify: `patent/server_fastapi/routers/ask.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/patent/result_builder.py`
- Modify: `patent/server/schemas/response_models.py`

### Backend File/Hybrid Streaming

- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Create: `patent/server/patent/stream_events.py`

### Backend Tests

- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_pdf_service.py`
- Modify: `patent/tests/test_patent_tabular_service.py`
- Modify: `patent/tests/test_patent_kb_service.py`
- Create: `patent/tests/test_patent_stream_events.py`

### Frontend Capability And Rendering

- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Create: `frontend-vue/src/utils/patentStreaming.js`

### Frontend Tests

- Modify: `frontend-vue/src/services/api.structure.test.js`
- Modify: `frontend-vue/src/utils/streamingLifecycle.test.js`
- Modify: `frontend-vue/src/utils/recoverableTaskController.test.js`
- Modify: `frontend-vue/src/views/Home.structure.test.js`
- Create: `frontend-vue/src/utils/patentStreaming.test.js`

### Gateway Proxy

- Modify if needed: `gateway/app/routers/qa.py`
- Modify: `gateway/tests/test_qa_proxy.py`

## Verification Discipline

All implementation-time commands below must be run with escalated permissions.

Backend pytest pattern:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest ...
```

Frontend test pattern:

```bash
cd frontend-vue && npm test -- ...
```

Focused SSE curl verification:

```bash
curl -N -s -X POST http://127.0.0.1:8010/api/patent/ask_stream ...
```

## Task 1: Add Protocol Models And Capability Gate

**Files:**
- Modify: `patent/server_fastapi/routers/ask.py`
- Modify: `patent/server/patent/result_builder.py`
- Modify: `patent/server/schemas/response_models.py`
- Create: `patent/server/patent/stream_events.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Create: `patent/tests/test_patent_stream_events.py`

- [ ] **Step 1: Write failing protocol tests**

Cover:
- capability disabled requests never emit `preview`
- `ContentEvent` can carry `content_role`, `content_source`, `content_stream_id`, `content_phase`, `replace_stream`
- invalid combinations are rejected:
  - `preview` without `content_stream_id`
  - `preview` without `content_phase`
  - `delta` before `start`
- standalone non-file `patent` ask/ask_stream remains on the old content contract

- [ ] **Step 2: Run the protocol red tests**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_stream_events.py patent/tests/test_patent_kb_service.py -q
```

Expected:
- FAIL because the current content event model only supports plain `content`

- [ ] **Step 3: Implement the protocol envelope**

Implement:
- capability parsing in `patent/server_fastapi/routers/ask.py`
- a shared helper layer in `patent/server/patent/stream_events.py`
- response model extensions in `patent/server/schemas/response_models.py`
- content-event builder support in `patent/server/patent/result_builder.py`

Rules to encode:
- preview events are impossible unless capability is enabled
- final events default to `snapshot` only when `content_phase` is omitted
- standalone `kb_qa` never opts into preview

- [ ] **Step 4: Re-run protocol tests**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_stream_events.py patent/tests/test_patent_kb_service.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server_fastapi/routers/ask.py patent/server/patent/result_builder.py patent/server/schemas/response_models.py patent/server/patent/stream_events.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_stream_events.py
git add patent/tests/test_patent_kb_service.py
git commit -m "feat: add patent streaming capability gate and event protocol"
```

## Task 2: Normalize Final-Only Semantics For `pdf_qa`, `tabular_qa`, And Cache Hits

**Files:**
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/tests/test_patent_pdf_service.py`
- Modify: `patent/tests/test_patent_tabular_service.py`
- Modify: `patent/tests/test_patent_file_routes.py`

- [ ] **Step 1: Write failing final-semantics tests**

Cover:
- capability enabled `pdf_qa` only emits `final/pdf`
- capability enabled `tabular_qa` only emits `final/table`
- cache-hit replay only emits `final` `snapshot`
- capability disabled routes still keep legacy single-channel content behavior

- [ ] **Step 2: Run the red tests for non-hybrid routes**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_service.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_file_routes.py -q
```

Expected:
- FAIL because current services emit plain content and cache replay is untyped

- [ ] **Step 3: Implement final-only wrappers**

Implement:
- final-answer wrapper usage for `pdf_qa`
- final-answer wrapper usage for `tabular_qa`
- cache-hit replay converted to `final` `snapshot`
- keep `ask_service` backward-compatible when no typed metadata is present

- [ ] **Step 4: Re-run the non-hybrid route tests**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_service.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_file_routes.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/services/ask_service.py patent/server/patent/file_routes.py patent/server/patent/pdf_service.py patent/server/patent/tabular_service.py patent/tests/test_patent_pdf_service.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_file_routes.py
git commit -m "feat: normalize patent final-answer streaming semantics"
```

## Task 3: Implement Gated Preview Streaming For Hybrid File Routes

**Files:**
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_file_routes.py`

- [ ] **Step 1: Write failing hybrid preview tests**

Cover:
- capability enabled `pdf+table` emits `preview/pdf` and `preview/table` before `final/hybrid`
- capability enabled `pdf+kb` emits file-side preview and does not clear file callback
- no new preview is allowed after first final event
- every opened preview stream is closed by `end` or `snapshot`
- standalone `kb_qa` remains unchanged

- [ ] **Step 2: Run the hybrid red tests**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py -q
```

Expected:
- FAIL because hybrid routes currently null out child callbacks and only replay final text

- [ ] **Step 3: Implement hybrid preview wrappers and ordering**

Implement:
- replace current raw callback nulling with capability-aware preview wrappers
- keep capability disabled path on final-only replay
- enforce preview lifecycle closure before final start
- preserve standalone `kb_qa` by limiting changes to file-route and hybrid wrappers only

- [ ] **Step 4: Re-run hybrid tests**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/executor.py patent/server/patent/file_routes.py patent/server/patent/pdf_service.py patent/server/patent/tabular_service.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py
git commit -m "feat: add gated preview streaming for patent hybrid routes"
```

## Task 4: Update Frontend Capability Negotiation And Preview Rendering

**Files:**
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Create: `frontend-vue/src/utils/patentStreaming.js`
- Modify: `frontend-vue/src/services/api.structure.test.js`
- Modify: `frontend-vue/src/utils/streamingLifecycle.test.js`
- Modify: `frontend-vue/src/utils/recoverableTaskController.test.js`
- Modify: `frontend-vue/src/views/Home.structure.test.js`
- Create: `frontend-vue/src/utils/patentStreaming.test.js`

- [ ] **Step 1: Write failing frontend tests**

Cover:
- `patent` file/hybrid requests advertise capability
- legacy paths still work when capability is absent
- `preview` chunks are bucketed by `content_stream_id`
- `final` chunks alone update the main answer body
- `replace_stream` resets only the targeted preview buffer

- [ ] **Step 2: Run frontend red tests**

Run:
```bash
cd frontend-vue && npm test -- src/services/api.structure.test.js src/utils/streamingLifecycle.test.js src/utils/recoverableTaskController.test.js src/views/Home.structure.test.js src/utils/patentStreaming.test.js
```

Expected:
- FAIL because the frontend currently appends every `content` event into the same answer body

- [ ] **Step 3: Implement frontend capability and rendering split**

Implement:
- request-side capability flag/header in `frontend-vue/src/services/api.js`
- preview/final parsing helper in `frontend-vue/src/utils/patentStreaming.js`
- `Home.vue` consumption split:
  - `final` to main answer
  - `preview` to preview area only
- safe fallback to legacy behavior when capability is absent

- [ ] **Step 4: Re-run frontend tests**

Run:
```bash
cd frontend-vue && npm test -- src/services/api.structure.test.js src/utils/streamingLifecycle.test.js src/utils/recoverableTaskController.test.js src/views/Home.structure.test.js src/utils/patentStreaming.test.js
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/services/api.js frontend-vue/src/views/Home.vue frontend-vue/src/stores/chatStore.js frontend-vue/src/utils/recoverableTaskController.js frontend-vue/src/utils/patentStreaming.js frontend-vue/src/services/api.structure.test.js frontend-vue/src/utils/streamingLifecycle.test.js frontend-vue/src/utils/recoverableTaskController.test.js frontend-vue/src/views/Home.structure.test.js frontend-vue/src/utils/patentStreaming.test.js
git commit -m "feat(frontend): render patent preview and final streams separately"
```

## Task 5: End-To-End Verification And Rollout Guards

**Files:**
- Modify if needed: `patent/server/patent/executor.py`
- Modify if needed: `patent/server_fastapi/routers/ask.py`
- Modify if needed: `frontend-vue/src/services/api.js`
- Modify if needed: `gateway/app/routers/qa.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `gateway/tests/test_qa_proxy.py`

- [ ] **Step 1: Write failing end-to-end contract tests**

Cover:
- capability disabled `hybrid_qa` never emits preview
- capability enabled `hybrid_qa pdf+table` emits preview before final
- capability enabled cache-hit requests emit final `snapshot` only
- gateway proxy path preserves the new optional fields for `patent` stream events
- ordinary non-file `patent` ask/ask_stream through the backend contract remains unchanged

- [ ] **Step 2: Run backend contract tests**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py -q
```

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=gateway/.pytest_cache' TMPDIR=gateway/.tmp conda run -n agent pytest gateway/tests/test_qa_proxy.py -q
```

Expected:
- FAIL until the proxy and contract layers accept the new optional content fields

- [ ] **Step 3: Implement any remaining contract fixes**

Implement only the minimum needed to:
- preserve new optional fields through the stream
- keep capability disabled requests on the old single-answer UX
- avoid leaking preview events to non-upgraded clients

- [ ] **Step 4: Re-run contract tests and direct SSE curl checks**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py -q
```

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=gateway/.pytest_cache' TMPDIR=gateway/.tmp conda run -n agent pytest gateway/tests/test_qa_proxy.py -q
```

Then verify with two direct curls:
- capability disabled `hybrid_qa pdf+table`
- capability enabled `hybrid_qa pdf+table`

Expected:
- tests PASS
- disabled curl contains no `preview`
- enabled curl contains `preview` before `final`

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/executor.py patent/server_fastapi/routers/ask.py frontend-vue/src/services/api.js patent/tests/fastapi_contract/test_ask_contract.py gateway/tests/test_qa_proxy.py
git add gateway/app/routers/qa.py
git commit -m "test: lock patent streaming capability and proxy contract"
```

## Review Loop Requirement

After each task:

1. Request code review before moving on.
2. Fix all blocking review findings.
3. Re-run that task's target tests.
4. Only then continue.

## Final Verification Checklist

- [ ] `pdf_qa` capability enabled emits only `final/pdf`
- [ ] `tabular_qa` capability enabled emits only `final/table`
- [ ] `hybrid_qa pdf+table` capability enabled emits preview then final
- [ ] `hybrid_qa pdf+kb` capability enabled does not null out file preview
- [ ] cache hits emit final snapshot only
- [ ] capability disabled clients never receive preview
- [ ] standalone `patent kb_qa` behavior is unchanged
- [ ] ordinary non-file `patent` ask/ask_stream behavior is unchanged
- [ ] frontend main answer never appends preview text
