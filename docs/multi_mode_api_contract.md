# Multi-Mode API Contract

## Scope

This document freezes the external contract between:
- frontend
- gateway / BFF
- `public-service`
- `fastQA` backend
- `highThinkingQA` backend
- `patent` backend

Important boundary:
- `gateway` owns request routing and normalization
- `public-service` owns shared public capabilities and persistence
- QA backends own mode-specific execution only

## Backend Roles

### 1. `public-service`
Owns:
- auth
- conversations
- file upload / file list / file delete / file download
- PDF preview
- translate
- summarize
- health / kb info / quota / admin
- shared conversation and file persistence

### 2. `fastQA` backend
Owns:
- `fast` mode QA
- file-aware QA execution for routed file turns

### 3. `highThinkingQA` backend
Owns:
- `thinking` mode QA only

### 4. `patent` backend
Owns:
- `patent` mode QA only

### 5. Gateway / BFF
Owns:
- request routing
- auth passthrough
- optional trace / audit headers
- SSE passthrough without buffering
- file-context resolution
- requested-mode to actual-backend decision
- clarification response for ambiguous file turns

## External Route Contract

### Public infrastructure routes
These are exposed through `gateway` and routed to `public-service`, regardless of selected mode.

This list is intended to reflect the current public surface and should be kept in sync with `gateway/app/services/route_table.py`.

- `POST /api/auth/login`
- `POST /api/auth/register`
- `GET /api/auth/me`
- `POST|PUT /api/auth/password`
- `POST /api/auth/forgot-password/initiate`
- `POST /api/auth/forgot-password/verify`
- `GET|POST|PUT /api/auth/security-questions`
- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `DELETE /api/conversations/{conversation_id}`
- `PUT /api/conversations/{conversation_id}/title`
- `POST /api/conversations/{conversation_id}/messages`
- `GET /api/conversations/{conversation_id}/files`
- `GET /api/conversations/{conversation_id}/files/{file_id}`
- `GET /api/conversations/{conversation_id}/files/{file_id}/download`
- `DELETE /api/conversations/{conversation_id}/files/{file_id}`
- `POST /api/upload_pdf`
- `POST /api/upload_excel`
- `POST /api/clear_pdf`
- `GET /api/view_pdf/{doi}`
- `HEAD /api/view_pdf/{doi}`
- `POST /api/translate`
- `POST /api/summarize_pdf/{doi}`
- `GET /api/extract_pdf_text/{doi}`
- `GET /api/check_pdf/{doi}`
- `GET /api/health`
- `GET /api/kb_info`
- `POST /api/refresh_kb`
- `POST /api/clear_cache`
- `GET /api/background_status`
- `GET /api/literature_content`
- `POST /api/reference_preview`
- `GET /api/quota/my`
- `GET|POST /api/quota/configs`
- `PUT /api/quota/configs/{quota_type}`
- `GET /api/quota/users/{user_id}`
- `POST /api/quota/reset/{user_id}/{quota_type}`
- `GET|POST /api/admin/users`
- `DELETE /api/admin/users/{user_id}`
- `GET|PUT /api/admin/users/{user_id}/password`
- `PUT /api/admin/users/{user_id}/status`
- `PUT /api/admin/users/{user_id}/type`
- `POST /api/admin/users/batch-delete`
- `POST /api/admin/users/batch-type`
- `POST /api/admin/users/batch-import`
- `GET /api/admin/users/import-template`

### Mode-routed QA routes
These are routed by mode.

- `POST /api/fast/ask`
- `POST /api/fast/ask_stream`
- `POST /api/thinking/ask`
- `POST /api/thinking/ask_stream`
- `POST /api/patent/ask`
- `POST /api/patent/ask_stream`

Current boundary:
- gateway currently exposes only mode-scoped QA endpoints for the canonical path
- legacy unscoped `/api/ask` and `/api/ask_stream` are not part of the current gateway public contract

## Ask Request Contract

Applies to:
- `POST /api/{mode}/ask`
- `POST /api/{mode}/ask_stream`

Important layering:
- frontend sends raw request to gateway
- gateway sends normalized execution request to backend
- backend should not be the long-term owner of raw frontend file-context parsing

### Frontend -> Gateway request body

```json
{
  "question": "string, required, max 4000 chars",
  "conversation_id": "string|number, optional",
  "chat_history": [
    {"role": "user|assistant|system", "content": "string"}
  ],
  "requested_mode": "fast|thinking|patent, required",
  "pdf_context": {
    "selected_ids": [1, 2],
    "newly_uploaded_ids": [3],
    "all_available_ids": [1, 2, 3],
    "last_focus_ids": [2],
    "last_turn_route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa"
  },
  "options": {}
}
```

### Normalization rules
- `chat_history` max size: `20`
- `requested_mode` is required
- auth token user identity overrides body `user_id`
- `pdf_context` is advisory context, not an instruction to force file QA
- extra body fields outside the request model are ignored by gateway

### Gateway -> Backend normalized execution body

```json
{
  "question": "string",
  "conversation_id": 123,
  "chat_history": [],
  "requested_mode": "thinking",
  "actual_mode": "thinking|fast|patent",
  "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "source_scope": "kb|pdf|table|pdf+kb|table+kb|pdf+table|pdf+table+kb",
  "turn_mode": "kb_only|file_only|mixed",
  "allow_kb_verification": false,
  "kb_enabled": false,
  "used_files": [],
  "execution_files": [],
  "selected_file_ids": [1, 2],
  "primary_file_id": 1,
  "file_selection": {},
  "route_reasons": ["NO_FILE_INTENT", "FALLBACK_TO_KB"],
  "route_confidence": 1.0,
  "classifier_used": false,
  "needs_clarification": false,
  "trace_id": "req_xxx",
  "options": {}
}
```

Rules:
- `requested_mode` is what frontend selected
- `actual_mode` is what gateway finally routed to
- if the turn is file-aware or mixed, gateway may override `actual_mode` to `fast`
- if the turn is plain QA, gateway should preserve `requested_mode`
- `used_files` is telemetry only, not downstream-owned route input
- `execution_files` is the executable file set chosen by gateway
- for file routes, `route`, `source_scope`, and `turn_mode` are explicit frozen contract fields
- downstream backends must reject missing or inconsistent file-route contract fields instead of inferring them locally

## Ask Success Response Contract

Applies to:
- `POST /api/{mode}/ask`

```json
{
  "success": true,
  "final_answer": "string",
  "timings": {},
  "metadata": {
    "requested_mode": "fast|thinking|patent",
    "actual_mode": "fast|thinking|patent",
    "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
    "query_mode": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
    "source_scope": "kb|pdf|table|pdf+kb|table+kb|pdf+table|pdf+table+kb",
    "source_usage": {
      "pdf_used": false,
      "table_used": false,
      "kb_used": true
    }
  },
  "references": ["10.xxxx/..."],
  "pdf_links": [
    {"doi": "10.xxxx/...", "pdf_url": "/api/view_pdf/..."}
  ],
  "reference_links": [
    {"doi": "10.xxxx/...", "pdf_url": "/api/view_pdf/..."}
  ],
  "trace_id": "req_xxx"
}
```

Important:
- ordinary successful QA responses do not currently expose the full frozen route explainability contract to frontend
- `turn_mode / selected_file_ids / file_selection / route_reasons / route_confidence / classifier_used` are guaranteed in the normalized gateway -> backend execution request
- those fields are frontend-visible today only in gateway-generated clarification / file-status short-circuit responses

## Ask Stream SSE Contract

Applies to:
- `POST /api/{mode}/ask_stream`

### Common envelope
Every SSE frame must be emitted as:

```text
data: {json}\n\n
```

### Event types

#### 1. Normal execution `metadata`

```json
{
  "type": "metadata",
  "requested_mode": "fast|thinking|patent",
  "actual_mode": "fast|thinking|patent",
  "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "source_scope": "kb|pdf|table|pdf+kb|table+kb|pdf+table|pdf+table+kb",
  "source_usage": {
    "pdf_used": false,
    "table_used": false,
    "kb_used": true
  },
  "query_mode": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "trace_id": "req_xxx"
}
```

#### 1.1 Gateway short-circuit `metadata`

When gateway returns clarification or file-status without forwarding to a backend, the `metadata` frame may additionally include route-context fields such as:
- `selected_file_ids`
- `strategy`
- `file_selection`
- `route_reasons`
- `route_confidence`
- `classifier_used`
- `needs_clarification`
- `clarify_candidates` for clarification only

Note:
- clarification short-circuit metadata includes `needs_clarification` and `clarify_candidates`
- file-status short-circuit metadata does not currently include `turn_mode`, `mode`, or `query_mode`

#### 2. `step`

```json
{
  "type": "step",
  "step": "step1|step2|step3|step4|step5_check|step5_revise|...",
  "message": "string",
  "status": "processing|success|error",
  "data": {}
}
```

#### 3. `content`

```json
{
  "type": "content",
  "content": "string chunk"
}
```

Rules:
- content must stream before `done`
- DOI citations in streamed content must already be adapted to frontend-compatible form
- current canonical citation output form is `[DOI: 10.xxxx/...]`

#### 4. `done`

```json
{
  "type": "done",
  "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "source_scope": "kb|pdf|table|pdf+kb|table+kb|pdf+table|pdf+table+kb",
  "final_answer": "string",
  "timings": {},
  "references": ["10.xxxx/..."],
  "used_files": [],
  "file_selection": {},
  "pdf_links": [
    {"doi": "10.xxxx/...", "pdf_url": "/api/view_pdf/..."}
  ],
  "reference_links": [
    {"doi": "10.xxxx/...", "pdf_url": "/api/view_pdf/..."}
  ],
  "metadata": {
    "requested_mode": "fast|thinking|patent",
    "actual_mode": "fast|thinking|patent",
    "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
    "query_mode": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
    "source_scope": "kb|pdf|table|pdf+kb|table+kb|pdf+table|pdf+table+kb",
    "source_usage": {
      "pdf_used": false,
      "table_used": false,
      "kb_used": true
    }
  },
  "trace_id": "req_xxx"
}
```

#### 5. `error`

```json
{
  "type": "error",
  "code": "ERROR_CODE",
  "error": "machine_readable_error",
  "message": "human_readable_message",
  "retriable": true,
  "trace_id": "req_xxx"
}
```

#### 6. `heartbeat`
Optional passthrough keepalive event.

```json
{
  "type": "heartbeat",
  "trace_id": "req_xxx"
}
```

## Error Contract

The following codes are frozen for gateway-facing QA APIs:
- `TOKEN_MISSING`
- `TOKEN_INVALID`
- `MODE_NOT_SUPPORTED`
- `MODE_MISMATCH`
- `ASK_STREAM_BUSY`
- `UPSTREAM_TIMEOUT`
- `UPSTREAM_ERROR`
- `UPSTREAM_STREAM_UNAVAILABLE`
- `CONVERSATION_FILE_PROVIDER_UNAVAILABLE`
- `NOT_IMPLEMENTED`
- `INTERNAL_ERROR`
- `INVALID_REQUEST`
- `FILE_SELECTION_CLARIFICATION_REQUIRED`
- `FILE_NOT_READY`
- `FILE_PROCESSING_FAILED`
- `FILE_NOT_FOUND`

Clarification rule:
- if gateway cannot uniquely resolve a file-aware turn, it should return a gateway-level clarification error
- gateway should not forward that request to any backend

File status rule:
- if gateway resolves a file-aware turn but the target file is not executable yet, gateway must return a gateway-level status response
- gateway should not silently fall back to another route
- sync path returns HTTP JSON error
- stream path emits `metadata` first, then `error`
- in stream short-circuit mode, the `error` frame itself carries only `code / error / message / retriable / trace_id`
- route context is carried by the preceding `metadata` frame, not duplicated into the `error` frame

Frontend rendering rule:
- clarification and file-status responses must be rendered as readable assistant messages
- route context from `metadata` should be preserved for both live stream and persisted history replay

## Public File / PDF Contract

### PDF preview
- `GET /api/view_pdf/{doi}`
- `HEAD /api/view_pdf/{doi}`

Rules:
- routed through `gateway` to `public-service`
- response must use `Content-Disposition: inline`
- frontend must treat this as preview, not download

## Shared State Contract

The following are shared across all modes:
- auth token format
- user identity
- conversation identity
- uploaded file identity
- DOI preview route
- persistent message schema

This means:
- files uploaded through public routes must be readable by all permitted QA backends
- mode switching must not create separate user/session worlds

## Gateway Routing Rules

### Always to `public-service`
- all public infrastructure routes listed above

### Routed by mode
- `/api/fast/ask*` -> `fast` backend
- `/api/thinking/ask*` -> `thinking` backend
- `/api/patent/ask*` -> `patent` backend

### Actual routing decision rules

1. If request is plain QA:
- preserve `requested_mode`
- route to selected mode backend

2. If request is file-aware QA:
- override `actual_mode` to `fast`
- route to `fast` backend

3. If request is mixed file + KB QA:
- short term: override `actual_mode` to `fast`
- route to `fast` backend

4. If file selection is ambiguous:
- do not forward
- return `FILE_SELECTION_CLARIFICATION_REQUIRED`

5. Presence of `selected_ids` alone must not force file routing:
- selected files are only candidate context
- file route requires file intent or file-specific focus in question

## Non-Goals

The gateway does not:
- own file parsing logic
- run retrieval or model inference
- replace MQ for async jobs
- maintain independent conversation storage
