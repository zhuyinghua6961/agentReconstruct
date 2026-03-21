# FastQA Gateway Alignment Spec

## Goal

Define the minimum `fastQA` surface required to sit behind `gateway` as the `fast` mode executor in Phase 1.

Phase 1 target is intentionally narrow:
- `fastQA` owns only fast `kb_qa` execution
- `gateway` owns mode routing, file intent, and public-service coordination

## Phase 1 Route Surface

`fastQA` Phase 1 should expose only:

- `POST /api/v1/ask`
- `POST /api/v1/ask_stream`
- `GET /api/v1/health`

Optional compatibility aliases may be added later, but they are not required to start the extraction.

## Gateway -> FastQA Request Contract

Gateway should send a normalized ask payload such as:

```json
{
  "question": "string",
  "chat_history": [],
  "requested_mode": "fast",
  "actual_mode": "fast",
  "trace_id": "req_xxx",
  "options": {}
}
```

Optional metadata fields allowed in Phase 1:
- `conversation_id`
- `route`
- `turn_mode`
- `allow_kb_verification`

Phase 1 policy:
- `fastQA` accepts plain KB-oriented requests
- `fastQA` may ignore unsupported file-oriented fields
- `fastQA` must not fetch conversation file metadata by itself
- `fastQA` must not treat missing file context as a reason to call public-service modules

## SSE Contract

`fastQA` must emit gateway-stable SSE frames:

- `metadata`
- `step`
- `content`
- `done`
- `error`

### Required fields

`metadata`:
- `type=metadata`
- `query_mode`
- `trace_id`

`step`:
- `type=step`
- `step`
- `status=processing|success|error`
- `message`

`content`:
- `type=content`
- `content`

`done`:
- `type=done`
- `references`
- `trace_id`
- optional: `timings`

`error`:
- `type=error`
- `error`
- `message`
- `trace_id`
- optional: `code`

## Responsibility Boundary

### Gateway owns
- `requested_mode -> actual_mode` decision
- file intent judgment
- ambiguous file clarification
- file-aware request routing away from Phase 1 `fastQA`
- conversation/public-service side effects

### FastQA owns
- execute `kb_qa`
- stream steps and content
- produce final references and done event

## Explicit Phase 1 Exclusions

Do not keep these responsibilities in `fastQA` Phase 1:
- file-context parsing as route authority
- PDF execution
- tabular execution
- hybrid execution
- auth/quota checks
- persistence side effects
- upload metadata lookup

## Acceptance

Phase 1 is acceptable when:
- gateway can call `POST /api/v1/ask_stream`
- plain KB turns execute without importing public-service modules
- SSE frame shape matches gateway/frontend expectations
- no conversation/upload/file-context modules are required to boot the service
