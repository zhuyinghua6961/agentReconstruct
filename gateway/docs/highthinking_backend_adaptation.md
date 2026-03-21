# highThinking Backend Adaptation

## Role in Target Architecture

`highThinking` should adapt to the role:
- `thinking` QA execution backend

It may temporarily still expose public APIs, but those should not define the gateway protocol.

## Current Strengths

Already present:
- `/api/{mode}/ask`
- `/api/{mode}/ask_stream`
- JSON ask and streaming ask separation
- auth, conversation, document, quota, and admin subsets
- SSE with structured events and explicit `seq` / `ts`
- query-token auth compatibility for preview/download

## Current Gaps Against Gateway Standard

### 1. Request model is too narrow for canonical execution payload
Current request parsing only normalizes:
- `question`
- `mode`
- `user_id`
- `conversation_id`
- `chat_history`
- `options`

It does not yet consume gateway-standard execution fields such as:
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `allow_kb_verification`
- `execution_files`
- `used_files`

### 2. Response shape is still backend-local
Current non-stream ask response returns:
- `final_answer`
- `timings`
- `metadata.mode`
- `metadata.query_mode`
- `conversation_id`
- `references`
- `pdf_links`
- `reference_links`
- `trace_id`

Target state still needs stable gateway-facing semantics for:
- `requested_mode`
- `actual_mode`
- `route`
- `used_files`
- canonical `metadata`

### 3. Stream `done` payload is not yet gateway-complete
Current `done` includes:
- `final_answer`
- `references`
- `pdf_links`
- `reference_links`
- `trace_id`

But it does not yet consistently include:
- `route`
- `used_files`
- canonical `metadata`

### 4. Public capability coverage is incomplete
Compared with the public backend target, current `highThinking` lacks at least:
- `literature_content`
- `reference_preview`
- conversation title update

This is acceptable if `highThinking` is treated as execution-only for thinking mode.

## Adaptation Requirements

### Required first
1. Accept canonical gateway execution payload.
2. Treat gateway route decision as authoritative.
3. Preserve `trace_id` and stream order.
4. Normalize stream `done` and JSON ask payloads to gateway expectations.

### Required second
1. Decide whether legacy public endpoints remain exposed or are reduced over time.
2. Remove any remaining assumption that mode alone determines execution strategy.

## What Should Stay in highThinking

Should remain owned here:
- thinking-mode execution logic
- high-accuracy reasoning pipeline
- stream step emission
- response generation quality

Should not become its long-term responsibility:
- public infrastructure APIs as architecture owner
- literature helper APIs if those stay centralized in public backend
- raw frontend `pdf_context` interpretation
- mode arbitration across backends

## Suggested Adaptation Order

1. Extend request parsing to accept canonical gateway execution fields.
2. Make execution branch selection depend on canonical `route` / `turn_mode` where needed.
3. Normalize JSON ask and stream `done` outputs.
4. Treat missing public helper APIs as out-of-scope for the thinking backend role.
