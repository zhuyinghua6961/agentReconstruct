# QA Failed Turn Persistence Design

> Scope: `gateway` + `fastQA` + `highThinkingQA` + `public-service`
> Status: draft
> Date: 2026-03-30

## 1. Background

Current behavior persists user turns early, but assistant turns are only persisted after a successful terminal `done` path.

This creates a broken conversation history for failed QA runs:

- user question is often persisted
- assistant failure is usually not persisted
- refresh can show a dangling user message with no visible execution result
- gateway-level failures and backend execution failures are not represented consistently

This spec defines the target behavior for failed QA persistence without changing the existing service ownership model:

- `gateway` stays a lightweight routing/quota/proxy layer
- `fastQA` and `highThinkingQA` remain the execution owners
- `public-service` remains the authority store for conversation history

## 2. Goals

### 2.1 Product Goals

- Preserve failed QA turns in conversation history when the request has entered backend execution.
- Avoid silent disappearance of failed assistant responses after refresh.
- Preserve partial answer content when a failure happens after some content has already been generated.
- Keep frontend rendering simple by using the same conversation message model for success and failure terminal states.

### 2.2 Engineering Goals

- Do not move persistence authority into `gateway`.
- Do not introduce token-by-token streaming persistence.
- Reuse current idempotency and authority-write patterns.
- Make terminal persistence semantics identical across `fastQA` and `highThinkingQA`.

### 2.3 Non-Goals

- This spec does not introduce gateway-owned conversation persistence.
- This spec does not require full pending-message streaming synchronization.
- This spec does not redesign frontend chat UX beyond displaying failed/canceled assistant turns.

## 3. Current-State Summary

### 3.1 Active Ownership

- `gateway`
  - resolves route
  - performs quota precheck/finalize
  - proxies requests to `fastQA` or `highThinkingQA`
  - does not actively persist ask-path messages today

- `fastQA`
  - persists user message before execution
  - loads conversation context from `public-service`
  - persists assistant summary only when `done_seen == true`

- `highThinkingQA`
  - persists user message before execution
  - loads conversation context from `public-service`
  - persists assistant summary only when `done_seen == true`

- `public-service`
  - accepts authority user writes
  - accepts assistant async final events with non-empty `answer_text`
  - materializes successful assistant messages with `status=done`

### 3.2 Current Failure Gap

Current assistant persistence is success-gated. Failed turns generally produce one of these outcomes:

1. nothing is persisted
2. only the user message is persisted
3. partial streamed answer exists on the frontend temporarily, but disappears after refresh

## 4. Design Principles

1. Persist by terminal state, not by token stream.
2. Preserve execution ownership in QA backends.
3. Use one assistant-turn protocol for success and failure.
4. Treat failed and canceled turns as first-class conversation history.
5. Guarantee one terminal assistant outcome per trace/idempotency key.

## 5. Target Behavior

## 5.1 Turn Lifecycle

For accepted QA requests, the persisted lifecycle becomes:

1. user turn persisted early
2. backend executes
3. backend emits exactly one terminal assistant event:
   - `done`
   - `failed`
   - `canceled`
4. `public-service` materializes exactly one terminal assistant message for that assistant idempotency key

## 5.2 Assistant Terminal States

Assistant messages must support:

- `status=done`
- `status=failed`
- `status=canceled`

Semantics:

- `done`: normal successful answer
- `failed`: execution ended with an error
- `canceled`: execution was aborted intentionally or due to client disconnect/cancel semantics

## 5.3 Partial Content Retention

If the backend has already produced answer content before failure:

- persist the partial answer text in assistant `content`
- persist the terminal status as `failed` or `canceled`

If no answer content exists:

- persist empty `content` or a minimal fallback content string depending on frontend rendering policy
- still persist structured failure metadata

Recommended phase-1 behavior:

- keep `content` as the actual partial text if present
- allow empty `content` for hard failures before first chunk
- rely on metadata for failure explanation

## 6. Failure Boundary Policy

## 6.1 Failure Classes

### A. Request Validation Failures

Examples:

- malformed request payload
- invalid mode
- invalid conversation id
- auth binding failure before execution admission

Policy:

- do not persist as conversation turn

Reason:

- request was not accepted into a backend execution lifecycle

### B. Gateway Pre-Execution Rejections

Examples:

- quota precheck reject
- conversation file provider unavailable
- file selection clarification required
- upstream backend unreachable before request acceptance

Phase-1 policy:

- do not persist as conversation turn

Reason:

- keep gateway lightweight
- avoid introducing gateway-owned terminal message semantics in phase 1

### C. Backend Pre-Execution Failures After User Write

Examples:

- authority context load failed
- runtime unavailable
- backend preparation failed after request acceptance

Policy:

- persist assistant terminal message with `status=failed`

Reason:

- once user message is written and request is accepted into backend execution, the turn must close explicitly

### D. Backend Execution Failures

Examples:

- retrieval/rerank failure
- LLM request timeout
- stream interruption
- citation validation timeout
- postprocess exception

Policy:

- persist assistant terminal message with `status=failed`
- persist partial content if available

### E. User/Client Cancellation

Examples:

- client disconnect with explicit cancel semantics
- backend-side cancellation path

Policy:

- persist assistant terminal message with `status=canceled`
- persist partial content if available

## 7. Authority Contract Changes

## 7.1 Problem With Current Contract

Current authority assistant API is effectively success-only because it requires a non-empty final answer event and writes `done` semantics.

That is insufficient for failed turn persistence.

## 7.2 Proposed Contract

Replace success-only assistant acceptance semantics with terminal assistant event semantics.

Recommended new internal authority endpoint:

- `POST /internal/conversations/{conversation_id}/messages/assistant-terminal-async`

Request contract:

```json
{
  "user_id": 123,
  "conversation_id": 456,
  "trace_id": "trace-abc",
  "source_service": "fastQA",
  "route": "kb_qa",
  "requested_mode": "fast",
  "actual_mode": "fast",
  "idempotency_key": "assistant:456:trace-abc",
  "final_event": {
    "terminal_status": "failed",
    "answer_text": "partial answer if any",
    "done_seen": false,
    "failure": {
      "stage": "llm_stream",
      "code": "UPSTREAM_TIMEOUT",
      "message": "model stream timed out",
      "retriable": true
    },
    "steps": [],
    "timings": {},
    "used_files": [],
    "references": [],
    "reference_objects": [],
    "reference_links": [],
    "pdf_links": [],
    "doi_locations": {}
  }
}
```

### 7.3 Terminal Status Rules

Allowed `terminal_status` values:

- `done`
- `failed`
- `canceled`

Field rules:

- `done`
  - `answer_text` should normally be non-empty
  - `done_seen=true`

- `failed`
  - `answer_text` may be empty or partial
  - `done_seen=false`
  - `failure` object should be present

- `canceled`
  - `answer_text` may be empty or partial
  - `done_seen=false`
  - `failure.code` may be omitted or mapped to a cancel code

## 8. Persisted Message Model

Assistant message shape in authority document:

```json
{
  "message_id": "m-123",
  "role": "assistant",
  "content": "partial or final answer",
  "created_at": "2026-03-30T12:00:00Z",
  "status": "failed",
  "metadata": {
    "trace_id": "trace-abc",
    "source_service": "fastQA",
    "route": "hybrid_qa",
    "requested_mode": "thinking",
    "actual_mode": "fast",
    "idempotency_key": "assistant:456:trace-abc",
    "done_seen": false,
    "terminal_status": "failed",
    "failure_stage": "llm_stream",
    "failure_code": "UPSTREAM_TIMEOUT",
    "failure_message": "model stream timed out",
    "retriable": true,
    "partial_content_chars": 821,
    "used_files": [],
    "references": [],
    "reference_objects": [],
    "reference_links": [],
    "pdf_links": [],
    "doi_locations": {},
    "steps": [],
    "timings": {}
  },
  "references": [],
  "reference_objects": [],
  "reference_links": [],
  "pdf_links": [],
  "doi_locations": {},
  "steps": [],
  "done_seen": false
}
```

## 9. Idempotency and State Convergence

## 9.1 Core Rule

Each assistant execution trace must converge to exactly one terminal persisted state.

### 9.2 Identity

Assistant idempotency key should remain trace-based:

- `assistant:{conversation_id}:{trace_id}`

Exact formatting may follow the existing authority client convention, but semantics must remain one assistant terminal key per execution trace.

### 9.3 Priority Rules

Recommended terminal priority:

- `done > failed > canceled`

Rules:

1. if `done` already exists, ignore later `failed/canceled`
2. if `failed` already exists, ignore later duplicate `failed`
3. if `failed` exists and later `done` arrives for the same key, converge to `done`
4. if `canceled` exists and later `failed` arrives, converge to `failed`

This protects against duplicate terminal events and race conditions.

## 10. Failure Stage Taxonomy

To support debugging, analytics, and future UX, backend services should emit normalized `failure_stage` values.

Recommended enum:

- `gateway_precheck`
- `authority_user_write`
- `authority_context_read`
- `route_resolution`
- `runtime_prepare`
- `retrieval`
- `rerank`
- `pdf_loading`
- `tabular_execution`
- `synthesis`
- `citation_validation`
- `llm_request`
- `llm_stream`
- `postprocess`
- `unknown`

Phase 1 does not require every current failure path to use a perfect enum value, but new persistence payloads must reserve this field.

## 11. Service Responsibilities

## 11.1 Gateway

Phase-1 responsibility:

- unchanged
- route request
- precheck/finalize quota
- resolve file context
- proxy to backend

Explicit non-responsibility in phase 1:

- do not persist failed ask turns
- do not create gateway-owned assistant terminal messages

## 11.2 fastQA

Required responsibility:

- continue early user persistence
- continue authority context loading
- collect terminal stream summary
- emit terminal assistant event on:
  - success
  - failure
  - cancel

Must preserve:

- partial assistant content
- route/query metadata
- used files
- references if available
- timings/steps if available

## 11.3 highThinkingQA

Required responsibility:

- same terminal persistence semantics as `fastQA`
- unify sync and stream ask paths around assistant terminal event reporting

## 11.4 public-service

Required responsibility:

- accept assistant terminal async events
- materialize `done/failed/canceled` assistant messages
- enforce idempotent convergence
- refresh conversation detail/list caches after terminal message materialization

## 12. Frontend Rendering Requirements

Frontend should treat failed and canceled assistant turns as normal conversation messages with extra status.

Recommended display:

- `done`: current normal rendering
- `failed`: show failure badge and message metadata
- `canceled`: show canceled badge

If assistant `content` is non-empty:

- render markdown normally

If assistant `content` is empty:

- render a minimal failure shell using metadata

Suggested metadata shown to users:

- status
- whether retriable
- failure stage
- failure message

## 13. Logging Requirements

## 13.1 QA Backend Logs

When emitting assistant terminal event, log:

- `trace_id`
- `conversation_id`
- `route`
- `terminal_status`
- `failure_stage`
- `failure_code`
- `partial_content_chars`

## 13.2 public-service Logs

When accepting/materializing terminal event, log:

- accepted terminal event
- idempotent duplicate hit
- terminal materialized
- ignored lower-priority terminal event

## 14. Rollout Strategy

## 14.1 Phase 1

- extend authority assistant API to terminal semantics
- update `fastQA` terminal persistence
- update `highThinkingQA` terminal persistence
- update frontend to render `failed/canceled` assistant messages

## 14.2 Phase 2

- evaluate whether gateway precheck failures should also be represented as conversation turns
- only do this if product requires full-chain failure journaling

## 15. Migration and Compatibility

Compatibility requirements:

- existing successful assistant persistence must continue to work
- old successful messages with `status=done` remain valid
- frontend must tolerate missing `terminal_status` on older history records

Migration note:

- no historical backfill is required
- this is forward-only behavior for new turns

## 16. Testing Matrix

### 16.1 fastQA

- sync success persists `assistant done`
- stream success persists `assistant done`
- stream failure after partial content persists `assistant failed` with partial content
- execution failure before first chunk persists `assistant failed` with empty or fallback content

### 16.2 highThinkingQA

- sync success persists `assistant done`
- stream success persists `assistant done`
- stream failure after partial content persists `assistant failed`
- cancel path persists `assistant canceled`

### 16.3 public-service

- terminal event `done` materializes correctly
- terminal event `failed` materializes correctly
- duplicate failed event is deduped
- failed followed by done converges correctly
- canceled followed by failed converges correctly

### 16.4 frontend

- failed assistant turn remains visible after refresh
- partial answer content remains visible after refresh
- failed/canceled messages sort correctly in conversation list/detail

## 17. Recommended Implementation Order

1. `public-service`: add assistant terminal event contract and materialization logic
2. `highThinkingQA`: emit terminal failed/canceled events
3. `fastQA`: emit terminal failed/canceled events
4. frontend: display `failed/canceled` terminal assistant messages
5. integration tests and log verification

## 18. Open Decisions

### Decision A: Should gateway precheck failures be saved into conversation history?

Recommendation:

- No in phase 1

Reason:

- avoids making gateway stateful
- keeps responsibility with execution backends

### Decision B: Should empty failed assistant content be allowed?

Recommendation:

- Yes

Reason:

- some failures happen before any content exists
- failure metadata is still valuable and should not block persistence

### Decision C: Should we create assistant `pending` messages before execution completes?

Recommendation:

- No in this phase

Reason:

- much higher complexity
- terminal-only persistence solves the current product gap

## 19. Acceptance Criteria

This design is considered implemented correctly when:

1. a backend-executed failed QA turn still appears in conversation history after refresh
2. partial answer text is preserved when available
3. successful turns still persist normally
4. duplicate terminal events do not create duplicate assistant messages
5. `fastQA` and `highThinkingQA` behave consistently
6. `gateway` does not become the conversation persistence owner
