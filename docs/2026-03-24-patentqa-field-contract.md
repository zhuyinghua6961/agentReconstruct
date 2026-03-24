# PatentQA Field-Level Contract

## Status

- Companion document to `docs/2026-03-24-patentqa-gateway-public-service-protocol.md`
- Purpose: define the field-level contract in a form that implementation and review can execute against directly
- This document is intentionally stricter and more granular than the main protocol overview

## Scope

This document covers:

- `Frontend -> Gateway` ask inputs that materially affect patent routing
- `Gateway -> patentQA` normalized request fields
- `Gateway -> fastQA` compatibility rewrite for patent file turns in Phase 1
- `patentQA -> public-service` authority API payloads
- `patentQA` sync response target
- `patentQA` SSE event target
- persistence-related metadata expectations for the target state

This document does not define:

- patent-native retrieval schema
- patent-native citation schema
- internal prompt design
- internal storage schema inside `patentQA`

## Terms

- `requested_mode`: the mode the frontend or caller asked for
- `actual_mode`: the execution backend mode gateway finally chose
- `route`: gateway-selected execution route such as `kb_qa`, `pdf_qa`, `tabular_qa`, `hybrid_qa`
- `turn_mode`: gateway-selected turn category: `kb_only`, `file_only`, `mixed`
- `compatibility state`: temporary rollout state where patent file turns still execute on `fastQA`
- `target state`: long-term state where patent mode uses authority-based persistence and gateway no longer directly persists `actual_mode=patent`

## Contract Layers

### Layer 1: Frontend -> Gateway

This layer is still governed by the existing multi-mode frontend contract.

### Layer 2: Gateway -> Execution Backend

This is the key runtime normalization layer.

### Layer 3: patentQA -> public-service authority APIs

This is the target persistence layer for patent-mode ownership.

## 1. Frontend -> Gateway Inputs That Matter For Patent Routing

The frontend may send many fields, but the following are the ones that materially affect patent routing and should be considered part of the effective contract.

| Field | Type | Required | Current source of truth | Phase 1 behavior | Notes |
| --- | --- | --- | --- | --- | --- |
| `question` | `string` | yes | frontend request body | required | gateway and backend both depend on it |
| `conversation_id` | `string|number|null` | no | frontend request body | optional but strongly recommended for persistence | required for any durable chat ownership |
| `chat_history` | `array` | no | frontend request body | optional | gateway forwards normalized copy |
| `requested_mode` | `fast|thinking|patent` | no | frontend request body | `patent` allowed | defaults per existing gateway contract |
| `mode` | `fast|thinking|patent|null` | no | frontend request body | optional compat field | if present, must not contradict path mode |
| `pdf_context.selected_ids` | `int[]` | no | frontend request body | may affect route away from patent | gateway-only input |
| `pdf_context.newly_uploaded_ids` | `int[]` | no | frontend request body | may affect route away from patent | gateway-only input |
| `pdf_context.all_available_ids` | `int[]` | no | frontend request body | may affect route away from patent | gateway-only input |
| `pdf_context.last_focus_ids` | `int[]` | no | frontend request body | may affect route away from patent | gateway-only input |
| `pdf_context.last_turn_route` | `string` | no | frontend request body | may affect route away from patent | gateway-only input |
| `options` | `object` | no | frontend request body | passthrough | no patent-specific meaning yet |

### Phase 1 routing rule from these inputs

| Condition | Gateway result |
| --- | --- |
| `requested_mode=patent` and gateway resolves `turn_mode=kb_only` | route to `patentQA` |
| `requested_mode=patent` and gateway resolves `turn_mode=file_only` | route to `fastQA` |
| `requested_mode=patent` and gateway resolves `turn_mode=mixed` | route to `fastQA` |

## 2. Gateway -> PatentQA Normalized Request Contract

This section defines the target request contract for Phase 1 patent turns.

### 2.1 Phase 1 gating rule

For any request actually sent to `patentQA` in Phase 1:

- `requested_mode` must be `patent`
- `actual_mode` must be `patent`
- `turn_mode` must be `kb_only`
- `route` must be `kb_qa`
- file payloads must be empty unless a later protocol revision explicitly changes this

If these invariants are violated, `patentQA` should reject the request as a protocol mismatch.

### 2.2 Field table

| Field | Type | Required | Phase 1 expected value | Source | Consumer rule |
| --- | --- | --- | --- | --- | --- |
| `question` | `string` | yes | any non-empty string | gateway | execute against this question |
| `conversation_id` | `int|string|null` | no | optional | gateway | if absent, no durable conversation authority write |
| `chat_history` | `array<object>` | no | normalized list | gateway | optional context input |
| `requested_mode` | `string` | yes | `patent` | gateway | must validate exact value |
| `actual_mode` | `string` | yes | `patent` | gateway | must validate exact value |
| `route` | `string` | yes | `kb_qa` | gateway | must validate exact value in Phase 1 |
| `source_scope` | `string` | no | empty string under current gateway logic | gateway | should not be re-inferred by patentQA |
| `turn_mode` | `string` | yes | `kb_only` | gateway | must validate exact value in Phase 1 |
| `kb_enabled` | `bool` | yes | current gateway-normalized value; currently `false` for plain `kb_qa` with empty `source_scope` | gateway | do not invent backend default |
| `allow_kb_verification` | `bool` | yes | `false` in Phase 1 patent turns | gateway | validation-only in Phase 1 |
| `used_files` | `array<object>` | yes | `[]` | gateway | reject non-empty in Phase 1 |
| `execution_files` | `array<object>` | yes | `[]` | gateway | reject non-empty in Phase 1 |
| `selected_file_ids` | `array<int>` | yes | `[]` | gateway | reject or ignore only by explicit policy; prefer reject |
| `primary_file_id` | `int|null` | no | `null` | gateway | reject non-null in Phase 1 |
| `file_selection` | `object` | yes | `{}` | gateway | should be empty in Phase 1 |
| `trace_id` | `string` | yes | non-empty | gateway | must be propagated to authority APIs |
| `options` | `object` | yes | passthrough object | gateway | reserved extension point |

### 2.3 Canonical Phase 1 example

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

## 3. Gateway -> fastQA Compatibility Rewrite For Patent File Turns

This section is only for the temporary compatibility state.

### 3.1 Why this rewrite exists

Current `fastQA` ingress rejects any payload where:

- `requested_mode != fast`
- or `actual_mode != fast`

That means gateway cannot forward patent file turns to `fastQA` while preserving `requested_mode=patent` today.

### 3.2 Compatibility rewrite rule

| Original gateway interpretation | Forwarded compatibility payload to `fastQA` |
| --- | --- |
| `requested_mode=patent`, `actual_mode=fast`, `turn_mode=file_only` | rewrite to `requested_mode=fast`, `actual_mode=fast` |
| `requested_mode=patent`, `actual_mode=fast`, `turn_mode=mixed` | rewrite to `requested_mode=fast`, `actual_mode=fast` |

### 3.3 Fields that may change in compatibility rewrite

| Field | Long-term target | Temporary fastQA compatibility forwarding |
| --- | --- | --- |
| `requested_mode` | `patent` | `fast` |
| `actual_mode` | `fast` | `fast` |
| `route` | gateway-chosen file route | same |
| `turn_mode` | gateway-chosen file turn mode | same |
| `used_files` | gateway-chosen | same |
| `execution_files` | gateway-chosen | same |
| `trace_id` | same | same |

### 3.4 Audit consequence in compatibility state

During this compatibility rewrite:

- requested patent mode is not automatically preserved in the fastQA ingress payload
- current direct persistence path may therefore store only fast-compatible metadata for those turns
- if the team wants requested patent mode preserved during this phase, that must be added explicitly in gateway/public-service persistence logic

This is a design decision, not something the current code gives for free.

## 4. PatentQA -> Public-Service Authority API Contract

This is the target-state persistence contract.

### 4.1 User-turn write fields

Endpoint:

- `POST /internal/conversations/{conversation_id}/messages/user`

Headers:

| Header | Type | Required | Value |
| --- | --- | --- | --- |
| `X-Internal-Service-Name` | `string` | yes | `patentQA` |
| `X-Internal-Service-Token` | `string` | yes | configured shared internal token |
| `X-Trace-Id` | `string` | yes | same trace id received from gateway |

Body:

| Field | Type | Required | Expected value / rule |
| --- | --- | --- | --- |
| `conversation_id` | `int` | yes | positive integer |
| `user_id` | `int` | yes | positive integer |
| `trace_id` | `string` | yes | non-empty; must match request trace id |
| `source_service` | `string` | yes | `patentQA` |
| `route` | `string` | yes | `kb_qa` in Phase 1 |
| `requested_mode` | `string` | yes | `patent` |
| `actual_mode` | `string` | yes | `patent` |
| `idempotency_key` | `string` | yes | `{conversation_id}:{trace_id}:user` |
| `message.role` | `string` | yes | `user` |
| `message.content` | `string` | yes | original question text |
| `context_hints.selected_file_ids` | `int[]` | no | `[]` in Phase 1 |
| `context_hints.last_turn_route_hint` | `string|null` | no | `kb_qa` or empty |

### 4.2 Context snapshot read fields

Endpoint:

- `GET /internal/conversations/{conversation_id}/context-snapshot`

Query parameters:

| Field | Type | Required | Expected value / rule |
| --- | --- | --- | --- |
| `user_id` | `int` | yes | positive integer |
| `trace_id` | `string` | yes | same trace id |
| `source_service` | `string` | yes | `patentQA` |
| `route` | `string` | yes | `kb_qa` in Phase 1 |
| `requested_mode` | `string` | yes | `patent` |
| `actual_mode` | `string` | yes | `patent` |

Expected response fields used by patentQA:

| Field | Type | Required | Consumer use |
| --- | --- | --- | --- |
| `conversation_id` | `int` | yes | sanity check |
| `user_id` | `int` | yes | sanity check |
| `snapshot_version` | `int` | yes | diagnostics / optional concurrency awareness |
| `updated_at` | `datetime-string` | yes | diagnostics |
| `summary` | `object` | yes | context assembly |
| `recent_turns` | `array<object>` | yes | context assembly |
| `conversation_state` | `object` | yes | context assembly |

### 4.3 Assistant final-event submit fields

Endpoint:

- `POST /internal/conversations/{conversation_id}/messages/assistant-async`

Body:

| Field | Type | Required | Expected value / rule |
| --- | --- | --- | --- |
| `conversation_id` | `int` | yes | positive integer |
| `user_id` | `int` | yes | positive integer |
| `trace_id` | `string` | yes | same trace id |
| `source_service` | `string` | yes | `patentQA` |
| `route` | `string` | yes | `kb_qa` in Phase 1 |
| `requested_mode` | `string` | yes | `patent` |
| `actual_mode` | `string` | yes | `patent` |
| `idempotency_key` | `string` | yes | `{conversation_id}:{trace_id}:assistant` |
| `final_event.done_seen` | `bool` | yes | `true` |
| `final_event.answer_text` | `string` | yes | final assistant answer |
| `final_event.steps` | `array<object>` | no | optional reasoning/progress summary |
| `final_event.references` | `array<object>` | no | compatibility references in Phase 1 |
| `final_event.used_files` | `array<object>` | no | `[]` in Phase 1 |
| `final_event.timings` | `object` | no | timing metrics |

### 4.4 Hard preconditions and hard postconditions

| Stage | Rule |
| --- | --- |
| user-turn write | hard precondition in target state |
| context-snapshot read | hard precondition in target state |
| assistant final-event submit | hard postcondition in target state |

That means none of these failures should be silently ignored once patent mode reaches the target state.

## 5. PatentQA Sync Response Target

This is the target-state sync response contract for `patentQA`. It does not describe current `fastQA` reality.

### 5.1 Response envelope

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `success` | `bool` | yes | `true` for success response |
| `data` | `object` | yes | wrapped payload |
| `trace_id` | `string` | yes | same request trace id |

### 5.2 `data` fields

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `final_answer` | `string` | yes | final user-facing answer |
| `timings` | `object` | yes | execution timings |
| `metadata` | `object` | yes | mode/route metadata |
| `references` | `array` | yes | may be compatibility reference objects in Phase 1 |
| `pdf_links` | `array` | no | compatibility field in Phase 1 |
| `reference_links` | `array` | no | compatibility field in Phase 1 |
| `trace_id` | `string` | yes | duplicated in `data` for compatibility with existing patterns |

### 5.3 `metadata` fields

| Field | Type | Required | Expected Phase 1 value |
| --- | --- | --- | --- |
| `requested_mode` | `string` | yes | `patent` |
| `actual_mode` | `string` | yes | `patent` |
| `route` | `string` | yes | `kb_qa` |
| `mode` | `string` | yes | `patent` |
| `query_mode` | `string` | yes | `patent` |
| `conversation_id` | `int|string|null` | no | passthrough conversation identifier |

### 5.4 Canonical example

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

## 6. PatentQA SSE Target

This is the target-state SSE contract for `patentQA`. It does not claim the existing multi-backend SSE ecosystem is already unified.

### 6.1 Envelope rules

- every frame must still be normal SSE `data: {json}\n\n`
- every event should include:
  - `seq`
  - `ts`

### 6.2 Event set

| Event type | Required in target state | Notes |
| --- | --- | --- |
| `metadata` | yes | first protocol-bearing event |
| `step` | recommended | progress reporting |
| `content` | yes for streamed answer content | chunked output |
| `heartbeat` | recommended | JSON heartbeat, not comment heartbeat |
| `done` | yes | terminal success event |
| `error` | yes on failure | terminal failure event |

### 6.3 Field requirements by event type

#### `metadata`

| Field | Type | Required |
| --- | --- | --- |
| `type` | `string` | yes |
| `requested_mode` | `string` | yes |
| `actual_mode` | `string` | yes |
| `route` | `string` | yes |
| `query_mode` | `string` | yes |
| `trace_id` | `string` | yes |
| `seq` | `int` | yes |
| `ts` | `string` | yes |

#### `content`

| Field | Type | Required |
| --- | --- | --- |
| `type` | `string` | yes |
| `content` | `string` | yes |
| `seq` | `int` | yes |
| `ts` | `string` | yes |

#### `done`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `type` | `string` | yes | `done` |
| `final_answer` | `string` | yes | final answer |
| `timings` | `object` | yes | timings |
| `references` | `array` | yes | compatibility references in Phase 1 |
| `trace_id` | `string` | yes | request trace id |
| `seq` | `int` | yes | sequence number |
| `ts` | `string` | yes | timestamp |
| `used_files` | `array` | no | `[]` in Phase 1 |
| `reference_links` | `array` | no | compatibility field |
| `pdf_links` | `array` | no | compatibility field |
| `file_selection` | `object` | no | compatibility field |

#### `error`

| Field | Type | Required |
| --- | --- | --- |
| `type` | `string` | yes |
| `code` | `string` | yes |
| `error` | `string` | yes |
| `message` | `string` | yes |
| `trace_id` | `string` | yes |
| `seq` | `int` | yes |
| `ts` | `string` | yes |

### 6.4 Phase 1 compatibility fields vs patent-native future

Phase 1 compatibility fields may include:

- `reference_links`
- `pdf_links`
- DOI-style reference objects
- literature-style `reference_links`
- `file_selection`

These are compatibility fields only. They should not be treated as the final patent-native semantic model.

## 7. Gateway Persistence Rules For Patent Mode

### 7.1 Target-state rule

In the target state:

- `patentQA` persists through the public-service internal authority API
- gateway direct persistence for `actual_mode = patent` is disabled
- requested and actual modes can be intentionally preserved in patent-mode metadata

### 7.2 Compatibility-state rule

In the compatibility state:

- gateway may still use the existing non-thinking persistence path for fast-compatible file turns
- requested patent mode may not yet be preserved in persisted metadata unless explicit extra work is added

## 8. Rollout Gates

Patent mode should not be declared ready for the target architecture until all of the following are true:

- gateway can route `kb_only` patent turns to `patentQA`
- public-service authority schemas and whitelist accept `patentQA/patent`
- gateway direct persistence for `actual_mode = patent` is disabled in the target state
- the team has explicitly chosen how Phase 1 patent file turns are persisted during the `fastQA` compatibility rewrite
- `patentQA` emits the agreed wrapped sync response and JSON SSE events
- at least one end-to-end test verifies:
  - user-turn write
  - context-snapshot read
  - assistant async submit
  - assistant materialization into the canonical transcript

## 9. Open Decisions

These are still design decisions, not current facts:

- whether Phase 1 patent file turns are allowed to lose requested patent mode in persisted metadata during the temporary compatibility rewrite
- whether gateway/public-service persistence should be extended so requested patent mode is recorded separately during that phase
- whether assistant async persistence failure should fail the browser request or be handled as an operational retry/error path
- what the patent-native citation object should be once the system stops borrowing literature-era compatibility fields
- whether `patentQA` should inherit `highThinkingQA`'s multi-turn context shaping exactly, or use a stricter patent-specific context budget
- whether Phase 2 should ever let `patentQA` own file-aware patent turns

## 10. Validation Notes

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

## 11. Related Documents

- `docs/2026-03-24-patentqa-gateway-public-service-protocol.md`
- `docs/multi_mode_api_contract.md`
- `docs/multi_mode_gateway_architecture.md`
- `docs/resource_root_contract.md`
