# Multi-Mode Gateway Architecture

## Goal

Support one frontend with three QA modes backed by three backend services:
- `fast`
- `thinking`
- `patent`

Keep non-QA capabilities implemented only once in the current `fast` backend, which also serves as the public infrastructure backend.

## Target Topology

```text
Frontend
  -> Gateway / BFF
    -> Fast backend (public infra + fast mode)
    -> Thinking QA backend
    -> Patent QA backend
    -> MQ / Worker cluster
```

## Design Principle

Split traffic into two categories:

1. Public capability traffic
- auth
- conversations
- file upload / download
- PDF preview
- translate
- summarize
- kb/system/quota/admin

2. Mode-specific QA traffic
- `ask`
- `ask_stream`

Public traffic always goes to the `fast` backend acting as the public infrastructure backend. Only QA traffic is routed by `mode`.

## Gateway Ownership Boundary

The gateway is the only component that should decide:
- whether a turn is plain QA or file-aware QA
- whether a turn is `kb_only`, `file_only`, or `mixed`
- whether a request must be clarified before execution
- which backend becomes the actual execution target

This means the gateway owns:
- `requested_mode` interpretation
- file-context resolution
- final backend routing

The backend should gradually stop owning duplicate file-intent resolution logic. The long-term target is:
- gateway performs light semantic routing
- backends execute only the route already chosen
- `fast` keeps public infrastructure and file-processing capabilities

Light semantic routing means:
- read conversation file metadata
- inspect question text and explicit file references
- reuse `selected_ids`, `last_focus_ids`, `last_turn_route`
- do not parse full PDF content in gateway
- do not run retrieval or LLM inference in gateway

## Recommended API Ownership

### `fast` backend as public infrastructure
- `POST /api/auth/login`
- `POST /api/auth/register`
- `GET /api/auth/me`
- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{id}`
- `DELETE /api/conversations/{id}`
- `POST /api/upload_pdf`
- `POST /api/upload_excel`
- `GET /api/conversations/{id}/files`
- `DELETE /api/conversations/{id}/files/{file_id}`
- `GET /api/view_pdf/{doi}`
- `POST /api/translate`
- `POST /api/summarize_pdf/{doi}`
- `GET /api/health`
- `GET /api/kb_info`

### Mode-routed QA backends
- `POST /api/fast/ask`
- `POST /api/fast/ask_stream`
- `POST /api/thinking/ask`
- `POST /api/thinking/ask_stream`
- `POST /api/patent/ask`
- `POST /api/patent/ask_stream`

## MQ Scope

MQ should handle asynchronous work only:
- file parsing
- OCR
- embedding / indexing
- long-running summary jobs
- async persistence / audit / notification

MQ should not replace the HTTP entrypoint for browser requests or SSE streaming.

## Shared Infrastructure Requirements

All QA backends must align on:
- token validation
- conversation and file identity
- MySQL metadata
- object storage access
- SSE event contract
- PDF / DOI reference semantics

Only the gateway should own the raw `pdf_context` contract from frontend. Mode backends should gradually consume a normalized execution payload instead of re-resolving raw file context independently.

## Performance Assessment

A lightweight gateway adds one HTTP hop, but this is usually negligible compared with LLM latency. Main risks are not raw latency but poor implementation:
- buffered SSE proxying
- duplicated file transfer
- inconsistent auth headers
- public backend becoming a bottleneck

## Recommended Rollout

### Phase 1
- add gateway
- keep all public APIs on the current `fast` backend
- route only `ask` and `ask_stream` by mode

### Phase 2
- align SSE and response contracts across all QA backends
- move file-intent and turn-mode resolution into gateway
- ensure all backends can access shared file and conversation context
- keep backend-side file resolver only as temporary compatibility fallback

### Phase 3
- introduce MQ for parsing, indexing, and other async jobs
- keep browser-facing APIs HTTP-based

### Phase 4
- optionally extract public capabilities from the current fast backend into a dedicated public service

## Risks

- protocol drift between three QA backends
- shared conversation state inconsistency
- file visibility mismatch across modes
- public backend becoming the single hot path for uploads and metadata
- SSE proxy misconfiguration causing delayed streaming
