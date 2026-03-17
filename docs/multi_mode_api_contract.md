# Multi-Mode API Contract

## Scope

This document freezes the external contract between:
- frontend
- gateway / BFF
- `fast` backend
- `thinking` backend
- `patent` backend

Important boundary:
- the current `fast` backend plays two roles at the same time
- it is both the `fast` QA backend and the shared public infrastructure backend

## Backend Roles

### 1. `fast` backend
Owns:
- `fast` mode QA
- auth
- conversations
- file upload / file list / file delete / file download
- PDF preview
- translate
- summarize
- health / kb info / quota / admin
- shared conversation and file persistence

### 2. `thinking` backend
Owns:
- `thinking` mode QA only

### 3. `patent` backend
Owns:
- `patent` mode QA only

### 4. Gateway / BFF
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
These always route to the `fast` backend, regardless of selected mode.

- `POST /api/auth/login`
- `POST /api/auth/register`
- `GET /api/auth/me`
- `POST /api/auth/password`
- `POST /api/auth/forgot-password/initiate`
- `POST /api/auth/forgot-password/verify`
- `GET /api/auth/security-questions`
- `POST /api/auth/security-questions`
- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `DELETE /api/conversations/{conversation_id}`
- `POST /api/conversations/{conversation_id}/messages`
- `GET /api/conversations/{conversation_id}/files`
- `GET /api/conversations/{conversation_id}/files/{file_id}`
- `GET /api/conversations/{conversation_id}/files/{file_id}/download`
- `DELETE /api/conversations/{conversation_id}/files/{file_id}`
- `POST /api/upload_pdf`
- `POST /api/upload_excel`
- `GET /api/view_pdf/{doi}`
- `HEAD /api/view_pdf/{doi}`
- `POST /api/translate`
- `POST /api/summarize_pdf/{doi}`
- `GET /api/extract_pdf_text/{doi}`
- `GET /api/check_pdf/{doi}`
- `GET /api/health`
- `GET /api/kb_info`

### Mode-routed QA routes
These are routed by mode.

- `POST /api/fast/ask`
- `POST /api/fast/ask_stream`
- `POST /api/thinking/ask`
- `POST /api/thinking/ask_stream`
- `POST /api/patent/ask`
- `POST /api/patent/ask_stream`

Compatibility rule:
- `POST /api/ask`
- `POST /api/ask_stream`

may remain as aliases to `fast` mode during transition.

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
  "requested_mode": "fast|thinking|patent, optional",
  "pdf_context": {
    "selected_ids": [1, 2],
    "newly_uploaded_ids": [3],
    "all_available_ids": [1, 2, 3],
    "last_focus_ids": [2],
    "last_turn_route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa"
  },
  "options": {},
  "mode": "optional, must match path if provided"
}
```

### Normalization rules
- `chat_history` max size: `20`
- `requested_mode` defaults to `fast` if omitted
- `mode` in body is optional
- if body `mode` exists, it must equal path `mode`
- auth token user identity overrides body `user_id`
- `pdf_context` is advisory context, not an instruction to force file QA

### Gateway -> Backend normalized execution body

```json
{
  "question": "string",
  "conversation_id": 123,
  "chat_history": [],
  "requested_mode": "thinking",
  "actual_mode": "thinking|fast|patent",
  "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "turn_mode": "kb_only|file_only|mixed",
  "allow_kb_verification": false,
  "used_files": [],
  "execution_files": [],
  "trace_id": "req_xxx",
  "options": {}
}
```

Rules:
- `requested_mode` is what frontend selected
- `actual_mode` is what gateway finally routed to
- if the turn is file-aware or mixed, gateway may override `actual_mode` to `fast`
- if the turn is plain QA, gateway should preserve `requested_mode`
- `used_files` and `execution_files` are gateway-resolved outputs, not frontend inputs

## Ask Success Response Contract

Applies to:
- `POST /api/{mode}/ask`

```json
{
  "success": true,
  "data": {
    "final_answer": "string",
    "timings": {},
    "metadata": {
      "requested_mode": "fast|thinking|patent",
      "actual_mode": "fast|thinking|patent",
      "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
      "mode": "fast|thinking|patent",
      "query_mode": "fast|thinking|patent|pdf_qa|tabular_qa|hybrid_qa",
      "conversation_id": "string|number|null"
    },
    "references": ["10.xxxx/..."],
    "pdf_links": [
      {"doi": "10.xxxx/...", "pdf_url": "/api/view_pdf/..."}
    ],
    "reference_links": [
      {"doi": "10.xxxx/...", "pdf_url": "/api/view_pdf/..."}
    ],
    "trace_id": "req_xxx"
  },
  "trace_id": "req_xxx"
}
```

## Ask Stream SSE Contract

Applies to:
- `POST /api/{mode}/ask_stream`

### Common envelope
Every SSE frame must be emitted as:

```text
data: {json}\n\n
```

Every event should include:
- `seq`
- `ts`

### Event types

#### 1. `metadata`

```json
{
  "type": "metadata",
  "requested_mode": "fast|thinking|patent",
  "actual_mode": "fast|thinking|patent",
  "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "mode": "fast|thinking|patent",
  "query_mode": "fast|thinking|patent|pdf_qa|tabular_qa|hybrid_qa",
  "trace_id": "req_xxx"
}
```

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
  "requested_mode": "fast|thinking|patent",
  "actual_mode": "fast|thinking|patent",
  "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "mode": "fast|thinking|patent",
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
- `NOT_IMPLEMENTED`
- `INTERNAL_ERROR`
- `INVALID_REQUEST`
- `FILE_SELECTION_CLARIFICATION_REQUIRED`

Clarification rule:
- if gateway cannot uniquely resolve a file-aware turn, it should return a gateway-level clarification error
- gateway should not forward that request to any backend

## Public File / PDF Contract

### PDF preview
- `GET /api/view_pdf/{doi}`
- `HEAD /api/view_pdf/{doi}`

Rules:
- always routed to `fast` backend
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

### Always to `fast` backend
- all public infrastructure routes listed above
- legacy `POST /api/ask`
- legacy `POST /api/ask_stream`

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
