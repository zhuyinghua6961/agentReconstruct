# Gateway Canonical Protocol Revision

## Goal

Define the gateway as the single source of truth for:
- frontend-facing API routes
- multi-mode QA routing
- file-context resolution
- clarification behavior
- backend execution payloads

This document does not describe what current backends happen to do. It defines what they must adapt to.

## Protocol Layers

### 1. External contract: frontend -> gateway

Canonical public routes:
- `/api/...`

Canonical QA routes:
- `POST /api/{mode}/ask`
- `POST /api/{mode}/ask_stream`

Allowed modes:
- `fast`
- `thinking`
- `patent`

Compatibility during migration:
- `/api/v1/...` aliases remain temporary gateway-owned compatibility routes
- browser-opened PDF preview and file download may carry `?token=...`

### 2. Internal contract: gateway -> backend

Gateway sends normalized execution requests. Backends are execution targets, not routing authorities.

Required fields:
- `question`
- `conversation_id`
- `chat_history`
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `allow_kb_verification`
- `execution_files`
- `trace_id`
- `options`

Compatibility fields during transition:
- `used_files`
- `mode`
- `pdf_context`
- `route_hint`
- `use_pdf`
- `pdf_path`
- `use_generation_driven`

## Routing Ownership

Gateway exclusively owns:
- path mode interpretation
- requested-mode to actual-mode override
- file-intent detection
- tabular/pdf/hybrid route selection
- clarification on ambiguous file references

Backends must not be the long-term owner of raw `pdf_context` parsing.

## Standard Decision Model

Gateway outputs:
- `route`: `kb_qa | pdf_qa | tabular_qa | hybrid_qa`
- `turn_mode`: `kb_only | file_only | mixed`
- `actual_mode`: `fast | thinking | patent`

Rules:
- plain QA keeps selected mode
- file-only QA routes to `fast`
- mixed QA routes to `fast`
- ambiguous singular file reference returns clarification before backend execution

## Standard Stream Contract

Standard event types:
- `metadata`
- `step`
- `content`
- `done`
- `error`

Compatibility event types:
- `thinking`
- `heartbeat`

Rules:
- `metadata` should appear first when available
- `done` and `error` are mutually exclusive terminal events
- no content after terminal event
- gateway must not buffer SSE to completion

## Standard Public Backend Boundary

Public backend role owns:
- `auth`
- `conversations`
- `uploaded file metadata`
- `file upload/delete/download/preview`
- `translate`
- `summarize_pdf`
- `literature_content`
- `reference_preview`
- `quota`
- `admin`
- `kb_info`
- `refresh_kb`
- `clear_cache`

Thinking and patent backends should not be required to own public capability APIs.

## Canonical Compatibility Rules

### Trace headers
Canonical header:
- `X-Trace-Id`

Accepted compatibility headers:
- `X-Trace-ID`
- `X-Request-ID`

### PDF links
Canonical URL form:
- `/api/view_pdf/{doi}`

Temporary compatibility form:
- `/api/v1/view_pdf/{doi}`

### Auth transport
Preferred:
- `Authorization: Bearer <token>`

Temporary compatibility:
- `?token=<token>` for browser-opened preview/download

## Required Documentation Split

The gateway docs should be maintained in three layers:
1. canonical gateway protocol
2. backend compatibility review
3. backend adaptation task list

## Immediate Protocol Revision Tasks

1. Separate current backend behavior from canonical gateway behavior in docs.
2. Mark all backend-specific legacy fields as compatibility-only.
3. Define a strict backend execution payload version owned by gateway.
4. Add a backend conformance checklist for `fast` and `thinking`.
5. Add a compatibility appendix for `/api/v1`, query-token auth, trace-header aliases, and heartbeat differences.
