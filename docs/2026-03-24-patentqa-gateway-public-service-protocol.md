# PatentQA Gateway / Public-Service Protocol

## Status

- Drafted from live code reading on 2026-03-24.
- This document separates three things clearly:
  - current code reality
  - Phase 1 compatibility rules
  - long-term target contract
- This document is intentionally protocol-focused. It does not define the internal retrieval, prompting, or ranking logic of `patentQA`.

## Audience

- gateway implementers
- `patentQA` backend implementers
- `public-service` conversation-authority implementers
- reviewers validating whether a rollout is safe

## Non-Goals

- This document does not change the current fact that file QA and mixed QA are owned by `fastQA`.
- This document does not claim that `fastQA`, `highThinkingQA`, and future `patentQA` are already protocol-aligned today.
- This document does not define a patent-native citation schema yet.
- This document does not define `patentQA`'s internal model or retrieval architecture.

## 1. Confirmed Current Behavior

### 1.1 Gateway topology and backend registry

Confirmed in code:

- `gateway` registers four backend roles:
  - `public`
  - `fast`
  - `thinking`
  - `patent`
- Backend base URLs come from:
  - `PUBLIC_BACKEND_BASE_URL`
  - `FAST_BACKEND_BASE_URL`
  - `THINKING_BACKEND_BASE_URL`
  - `PATENT_BACKEND_BASE_URL`

Implication:

- Gateway is already structurally able to route to a future `patent` backend.

Relevant refs:

- `gateway/app/core/config.py`
- `gateway/app/services/backend_registry.py`

### 1.2 Gateway route ownership

Confirmed in code:

- Public routes proxy to the `public` backend.
- QA routes are:
  - `POST /api/ask`
  - `POST /api/ask_stream`
  - `POST /api/{mode}/ask`
  - `POST /api/{mode}/ask_stream`
- Supported mode names already include:
  - `fast`
  - `thinking`
  - `patent`

Relevant refs:

- `gateway/app/services/route_table.py`
- `gateway/app/routers/public_proxy.py`
- `gateway/app/routers/qa.py`

### 1.3 Gateway ask normalization

Confirmed in code:

- Gateway accepts frontend ask payload fields such as:
  - `question`
  - `conversation_id`
  - `chat_history`
  - `requested_mode`
  - `pdf_context`
  - `options`
  - optional body `mode`
- Gateway forwards a normalized execution payload containing:
  - `question`
  - `conversation_id`
  - `chat_history`
  - `requested_mode`
  - `actual_mode`
  - `route`
  - `source_scope`
  - `turn_mode`
  - `kb_enabled`
  - `allow_kb_verification`
  - `used_files`
  - `execution_files`
  - `selected_file_ids`
  - `primary_file_id`
  - `file_selection`
  - `trace_id`
  - `options`

Relevant refs:

- `gateway/app/models/ask.py`
- `gateway/app/models/routing.py`
- `gateway/app/routers/qa.py`

### 1.4 Gateway route decision for file turns

Confirmed in code:

- If gateway decides the turn is `file_only` or `mixed`, it forces:
  - `actual_mode = fast`
- Current route names are:
  - `kb_qa`
  - `pdf_qa`
  - `tabular_qa`
  - `hybrid_qa`

Current architectural meaning:

- File-aware execution is currently owned by `fastQA`.
- `highThinkingQA` is currently a KB/text execution backend from gateway's point of view.
- `patentQA` should be treated the same way in Phase 1 unless and until file ownership changes intentionally.

Relevant refs:

- `gateway/app/services/file_context_resolver.py`
- `gateway/app/services/route_decision.py`
- `gateway/tests/test_qa_proxy.py`

### 1.5 Gateway depends on public-service for conversation file metadata

Confirmed in code:

- Gateway can use `GATEWAY_CONVERSATION_FILE_PROVIDER=public_http`.
- In that mode it loads file metadata from:
  - `GET /api/conversations/{conversation_id}/files`
- Authorization and trace headers are forwarded on that metadata read.

Implication:

- Gateway already treats `public-service` as the authority for conversation file inventory.

Relevant refs:

- `gateway/app/services/provider_factory.py`
- `gateway/app/providers/conversation_files/public_http.py`
- `gateway/app/services/conversation_files.py`

### 1.6 Current persistence split

Confirmed in code:

- Gateway directly persists user and assistant messages to `public-service` when:
  - `actual_mode != thinking`
- That means current gateway-side persistence applies to:
  - `fast`
  - future `patent`, unless gateway behavior changes
- Gateway direct persistence target is:
  - `POST /api/v1/conversations/{conversation_id}/messages`
- `fastQA` and `highThinkingQA` also already have a better authority-style persistence model:
  - write user turn
  - read context snapshot
  - submit assistant final event asynchronously

Implication:

- Today there are two persistence patterns in the repo:
  - Pattern A: gateway direct append to public conversation API
  - Pattern B: QA backend authority protocol to public-service internal API
- Pattern B is the better template for `patentQA`.

Relevant refs:

- `gateway/app/routers/qa.py`
- `gateway/app/services/conversation_persistence.py`
- `fastQA/app/services/conversation_authority_client.py`
- `fastQA/app/services/chat_persistence.py`
- `highThinkingQA/server/services/conversation_authority_client.py`
- `highThinkingQA/server/services/chat_persistence.py`

### 1.7 Public-service external conversation authority

Confirmed in code:

- Public conversation APIs exposed to gateway/frontend include:
  - create/list/detail/delete conversation
  - add message
  - list/get/download/delete files
- Conversation state is stored as:
  - relational metadata in MySQL
  - canonical per-conversation chat JSON
  - Redis-backed caches for conversation list/detail

Relevant refs:

- `public-service/backend/app/modules/conversation/api.py`
- `public-service/backend/app/modules/conversation/service.py`
- `public-service/backend/app/modules/conversation/json_store.py`
- `public-service/backend/app/modules/conversation/repository.py`

### 1.8 Public-service internal authority API

Confirmed in code:

- Internal endpoints already exist:
  - `POST /internal/conversations/{conversation_id}/messages/user`
  - `GET /internal/conversations/{conversation_id}/context-snapshot`
  - `POST /internal/conversations/{conversation_id}/messages/assistant-async`
- Internal auth headers are:
  - `X-Internal-Service-Name`
  - `X-Internal-Service-Token`
- Idempotency keys are:
  - user write: `{conversation_id}:{trace_id}:user`
  - assistant write: `{conversation_id}:{trace_id}:assistant`

Critical current limitation:

- Whitelist only allows:
  - `fastQA -> fast`
  - `highThinkingQA -> thinking`
- Schema literals currently allow only:
  - `source_service in {fastQA, highThinkingQA}`
  - `requested_mode/actual_mode in {fast, thinking}`

This is the main contract gap blocking `patentQA` today.

Relevant refs:

- `public-service/backend/app/modules/conversation/internal_api.py`
- `public-service/backend/app/modules/conversation/authority_schemas.py`

### 1.9 Public-service assistant async materialization

Confirmed in code:

- `assistant-async` does not immediately finalize the assistant message into the canonical transcript.
- Public-service first enqueues an async assistant task.
- A worker later materializes the final assistant turn into chat JSON and marks the task done/retry/dead.

Implication:

- `patentQA` does not need its own custom final-turn storage model.
- It can reuse the same eventual-consistency path already used by `highThinkingQA`.

Relevant refs:

- `public-service/backend/app/modules/conversation/service.py`
- `public-service/backend/app/modules/conversation/repository.py`
- `public-service/backend/app/modules/conversation/assistant_inbox.py`

### 1.10 Public-service durability and retry

Confirmed in code:

- Canonical chat JSON is stored locally first.
- Public-service mirrors it to object storage.
- If remote sync fails, an outbox worker retries it.

Implication:

- `public-service` should remain the durability owner for patent-mode chat persistence.

Relevant refs:

- `public-service/backend/app/modules/conversation/json_store.py`
- `public-service/backend/app/modules/conversation/outbox.py`
- `public-service/backend/app/modules/conversation/outbox_worker.py`

### 1.11 fastQA ingress limitation that affects patent Phase 1

Confirmed in code:

- `fastQA`'s request adapter currently requires:
  - `requested_mode = fast`
  - `actual_mode = fast`
- If either value is not `fast`, it raises `mode_not_supported` with message `fastQA only supports fast mode`.

Implication:

- Gateway cannot currently forward patent file turns to `fastQA` while preserving `requested_mode = patent`.
- During the compatibility phase, gateway must rewrite those forwarded turns into the exact fast-compatible ingress contract unless `fastQA` is changed first.

Relevant refs:

- `fastQA/app/services/request_adapter.py`
- `fastQA/tests/test_request_adapter.py`

## 2. Decision Summary

### 2.1 Final recommendations in one place

- Phase 1: `patentQA` handles only `kb_only` patent turns.
- Phase 1 file-aware or mixed patent turns still execute on `fastQA`.
- Final target persistence model: `patentQA -> public-service internal authority API`.
- Gateway direct persistence for `actual_mode = patent` must be disabled before patent authority persistence is enabled.
- Wrapped sync response and JSON SSE with `seq/ts` are target `patentQA` protocol choices.
- Patent-native citation semantics remain an open future expansion topic.

### 2.2 Mutually exclusive rollout states

Only one of these may be true for patent mode at a time.

State A: Compatibility state

- patent file turns are rewritten by gateway into the existing `fastQA` ingress contract
- gateway/public-service may still persist those compatibility turns using the old path
- requested patent mode may not yet be preserved in persisted metadata for those compatibility turns
- authority persistence for `actual_mode = patent` is not yet active

State B: Target state

- `patentQA` persists through the public-service internal authority API
- gateway direct persistence for `actual_mode = patent` is disabled
- requested and actual modes can both be intentionally preserved in patent-mode metadata

These states must not overlap.

## 3. Phase Model

### 3.1 Phase 1

Goal:

- ship patent text-mode execution without changing current file-QA ownership

Rules:

- `requested_mode = patent`, `turn_mode = kb_only`:
  - gateway routes to `patentQA`
- `requested_mode = patent`, `turn_mode in {file_only, mixed}`:
  - gateway routes to `fastQA`
- if gateway must forward a patent file turn to `fastQA` under current code reality, it must rewrite the forwarded payload to:
  - `requested_mode = fast`
  - `actual_mode = fast`

Audit consequence in Phase 1 compatibility:

- current compatibility forwarding cannot automatically preserve requested patent mode in the persisted metadata of those file turns
- the team must explicitly choose one of these temporary behaviors:
  - accept fast-compatible persisted metadata during the compatibility phase
  - extend gateway/public-service persistence so original requested patent mode is recorded separately during that phase

### 3.2 Phase 2

Goal:

- optionally let `patentQA` own some or all file-aware patent turns

Required changes before Phase 2:

- gateway route-decision rules must be revised intentionally
- `patentQA` must accept and correctly execute file-aware payloads
- patent-native file/citation semantics must be defined explicitly
- persistence and metadata rules must be updated to reflect the new ownership model

## 4. Gateway -> PatentQA Contract

### 4.1 Endpoint matrix

| Request shape | Gateway decision | Execution target | Notes |
| --- | --- | --- | --- |
| `requested_mode=patent`, `turn_mode=kb_only` | preserve patent | `patentQA` | Phase 1 target path |
| `requested_mode=patent`, `turn_mode=file_only` | override to fast | `fastQA` | current real ownership |
| `requested_mode=patent`, `turn_mode=mixed` | override to fast | `fastQA` | current real ownership |
| `requested_mode=thinking`, `turn_mode=kb_only` | preserve thinking | `highThinkingQA` | unchanged current path |
| any public route | proxy to public backend | `public-service` / current public backend | unchanged current path |

### 4.2 Request invariants for Phase 1 patent turns

For any request gateway sends to `patentQA` in Phase 1:

- `requested_mode` must be `patent`
- `actual_mode` must be `patent`
- `turn_mode` must be `kb_only`
- `route` must be `kb_qa`
- `source_scope` should be empty under current gateway logic for plain KB patent turns
- `kb_enabled` should follow the gateway-normalized value rather than a backend-invented default
- `used_files` should be empty
- `execution_files` should be empty
- `selected_file_ids` should normally be empty

If any of these invariants fail, `patentQA` should reject the request as a protocol mismatch instead of silently reinterpreting it.

### 4.3 Recommended normalized request body for Phase 1 patent turns

```json
{
  "question": "string",
  "conversation_id": 123,
  "chat_history": [],
  "requested_mode": "patent",
  "actual_mode": "patent",
  "route": "kb_qa",
  "source_scope": "",
  "turn_mode": "kb_only",
  "kb_enabled": false,
  "allow_kb_verification": false,
  "used_files": [],
  "execution_files": [],
  "selected_file_ids": [],
  "primary_file_id": null,
  "file_selection": {},
  "trace_id": "req_xxx",
  "options": {}
}
```

### 4.4 Trust boundary

`patentQA` should treat gateway as the authority for:

- route selection
- requested-mode to actual-mode decision
- file-intent resolution
- clarification vs execution decision
- normalized file payload fields

`patentQA` may still validate for safety that the received payload matches the agreed patent Phase 1 contract.

## 5. PatentQA -> Public-Service Authority Contract

### 5.1 Guarantees the authority protocol should provide

- idempotent user turn write
- idempotent assistant final event submission
- read-after-write context recovery through `context-snapshot`
- eventual assistant materialization through the public-service inbox worker
- public-service-owned durability and retry

### 5.2 Required public-service expansion checklist

Before `patentQA` can use the authority API, `public-service` must expand all of the following consistently:

- internal caller whitelist: `patentQA -> patent`
- authority request schema literals for `source_service`
- authority request schema literals for `requested_mode`
- authority request schema literals for `actual_mode`
- any admin or diagnostic surface that reports known authority callers

### 5.3 User turn write

Request:

- `POST /internal/conversations/{conversation_id}/messages/user`

Headers:

- `X-Internal-Service-Name: patentQA`
- `X-Internal-Service-Token: <shared internal token>`
- `X-Trace-Id: <trace_id>`

Body:

```json
{
  "conversation_id": 123,
  "user_id": 456,
  "trace_id": "req_xxx",
  "source_service": "patentQA",
  "route": "kb_qa",
  "requested_mode": "patent",
  "actual_mode": "patent",
  "idempotency_key": "123:req_xxx:user",
  "message": {
    "role": "user",
    "content": "..."
  },
  "context_hints": {
    "selected_file_ids": [],
    "last_turn_route_hint": "kb_qa"
  }
}
```

### 5.4 Context snapshot read

Request:

- `GET /internal/conversations/{conversation_id}/context-snapshot`

Query:

- `user_id`
- `trace_id`
- `source_service=patentQA`
- `route=kb_qa`
- `requested_mode=patent`
- `actual_mode=patent`

Expected response surface:

- `summary`
- `recent_turns`
- `conversation_state`
- `snapshot_version`

### 5.5 Assistant final event submit

Request:

- `POST /internal/conversations/{conversation_id}/messages/assistant-async`

Body:

```json
{
  "conversation_id": 123,
  "user_id": 456,
  "trace_id": "req_xxx",
  "source_service": "patentQA",
  "route": "kb_qa",
  "requested_mode": "patent",
  "actual_mode": "patent",
  "idempotency_key": "123:req_xxx:assistant",
  "final_event": {
    "done_seen": true,
    "answer_text": "...",
    "steps": [],
    "references": [],
    "used_files": [],
    "timings": {}
  }
}
```

### 5.6 Failure handling expectations

If patent authority calls fail:

- user-turn write failure should fail the request before execution in the target state
- context-snapshot read failure should fail the request before execution in the target state
- assistant async submission failure should be treated as a persistence failure, not silently ignored

The recommended production target is:

- user write is a hard precondition
- context read is a hard precondition
- assistant async submit is a hard postcondition

## 6. Persistence Sequence and Ownership

### 6.1 Sequence diagram

```text
Frontend
  -> Gateway
    -> resolve mode + file context
    -> if kb_only patent: forward to patentQA
      -> authority write user turn to public-service
      -> authority read context snapshot from public-service
      -> execute patent ask
      -> stream SSE back through gateway
      -> authority submit assistant final event
        -> public-service assistant inbox worker
          -> materialize canonical assistant turn into chat JSON
          -> refresh/invalidate caches
```

### 6.2 Ownership by stage

| Stage | Owner | Why |
| --- | --- | --- |
| file-intent decision | gateway | only component with frontend `pdf_context` contract |
| requested vs actual mode decision | gateway | routing authority boundary |
| conversation and file metadata authority | public-service | canonical persistence owner |
| patent execution | patentQA | mode-specific logic owner |
| transcript durability | public-service | canonical storage + retry owner |
| async assistant materialization | public-service worker | eventual consistency path already exists |

### 6.3 Required trace propagation

For every patent-mode turn in the target state:

- gateway must propagate the trace id to `patentQA`
- `patentQA` must reuse the same trace id in authority user write
- `patentQA` must reuse the same trace id in authority assistant async submit
- persisted assistant metadata should keep the trace id so gateway, patentQA, and public-service logs can be joined later

## 7. PatentQA Sync and SSE Contract

### 7.1 Important scope note

The following sections define target `patentQA` protocol choices.

They do not claim that current `fastQA` and `highThinkingQA` are already normalized to those same shapes.

### 7.2 Sync response target

Recommended `patentQA` sync response shape:

```json
{
  "success": true,
  "data": {
    "final_answer": "...",
    "timings": {},
    "metadata": {
      "requested_mode": "patent",
      "actual_mode": "patent",
      "route": "kb_qa",
      "mode": "patent",
      "query_mode": "patent",
      "conversation_id": 123
    },
    "references": [],
    "pdf_links": [],
    "reference_links": [],
    "trace_id": "req_xxx"
  },
  "trace_id": "req_xxx"
}
```

Why this target:

- it matches the frozen multi-mode contract better than `fastQA`'s flat response shape
- it avoids introducing a third response format
- current gateway assistant-summary parsing already tolerates wrapped payloads

### 7.3 SSE event target

Recommended event set:

- `metadata`
- `step`
- `content`
- `heartbeat`
- `done`
- `error`

Every SSE frame should still be standard `data: {json}\n\n`.

Every event should include:

- `seq`
- `ts`

### 7.4 Minimum field requirements by event type

`metadata` should include at minimum:

- `requested_mode`
- `actual_mode`
- `route`
- `query_mode`
- `trace_id`
- `seq`
- `ts`

`content` should include at minimum:

- `content`
- `seq`
- `ts`

`done` should include at minimum:

- `final_answer`
- `timings`
- `references`
- `trace_id`
- `seq`
- `ts`

`error` should include at minimum:

- `code`
- `error`
- `message`
- `trace_id`
- `seq`
- `ts`

### 7.5 Phase 1 compatibility fields vs future patent-native fields

In Phase 1, `patentQA` may emit literature-era compatibility fields so gateway and downstream consumers can reuse existing parsing logic.

Recommended minimum `done` event for Phase 1 compatibility:

```json
{
  "type": "done",
  "requested_mode": "patent",
  "actual_mode": "patent",
  "route": "kb_qa",
  "final_answer": "...",
  "timings": {},
  "references": [],
  "trace_id": "req_xxx",
  "used_files": []
}
```

Optional Phase 1 compatibility fields:

- `reference_links`
- `pdf_links`
- `file_selection`

These fields are compatibility fields only. They should not be treated as permanent patent-native requirements.

Long-term patent-native protocol expansion may replace or extend:

- DOI-based reference objects
- `pdf_links`
- literature-style `reference_links`

## 8. Gateway Persistence Rules for Patent Mode

### 8.1 Final target rule

- `patentQA` should persist through the public-service internal authority API.
- Gateway should not be the long-term persistence owner for patent-mode transcript writes.
- Gateway direct persistence for `actual_mode = patent` must be disabled before the target patent persistence model reaches production.

### 8.2 Compatibility-state rule

During the temporary compatibility phase for patent file turns:

- gateway may still need to use the existing non-thinking persistence path for fast-compatible file turns
- if that path is used, requested patent mode may not yet be preserved automatically in persisted metadata
- this limitation should be treated as an explicit compatibility-state compromise, not as the intended end state

## 9. Rollout Gates

Patent mode should not be declared ready for the target architecture until all of the following are true:

- gateway can route `kb_only` patent turns to `patentQA`
- public-service authority schemas and whitelist accept `patentQA/patent`
- gateway direct persistence for `actual_mode = patent` is disabled in the target state
- the team has explicitly chosen how Phase 1 patent file turns are persisted during the `fastQA` compatibility rewrite
- `patentQA` emits the agreed wrapped sync response and JSON SSE events
- at least one end-to-end test verifies:
  - user turn write
  - snapshot read
  - assistant async submit
  - final assistant materialization

## 10. Open Decisions The Team Must Make Explicitly

These are still design choices, not facts:

- whether Phase 1 patent file turns are allowed to lose requested patent mode in persisted metadata during the temporary compatibility rewrite
- whether gateway/public-service persistence should be extended so requested patent mode is recorded separately during that phase
- whether assistant async persistence failure should fail the browser request or be handled as an operational retry/error path
- what the patent-native citation object should be once the system stops borrowing literature-era compatibility fields
- whether `patentQA` should inherit `highThinkingQA`'s multi-turn context shaping exactly, or use a stricter patent-specific context budget
- whether Phase 2 should ever let `patentQA` own file-aware patent turns

## 11. Validation Notes

Validated by targeted tests run on 2026-03-24 in the `conda` `agent` environment:

- `gateway/tests/test_qa_proxy.py`
  - plain KB question to `/api/thinking/ask` stays on `thinking`
  - file question to `/api/thinking/ask` is rerouted to `fast`
- `highThinkingQA/tests/fastapi_migration/test_fastapi_ask_contract.py`
  - `/api/v1/patent/ask` and `/api/v1/patent/ask_stream` currently follow the `NOT_IMPLEMENTED` contract in `highThinkingQA`

These checks support the central conclusions of this document:

- file-aware turns are currently owned by `fastQA`
- `patent` is architecturally reserved but not yet implemented
- `highThinkingQA` already provides the better persistence pattern for future `patentQA`

## 12. Related Documents

- `docs/multi_mode_api_contract.md`
- `docs/multi_mode_gateway_architecture.md`
- `docs/resource_root_contract.md`
