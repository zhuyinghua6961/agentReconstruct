# Three-Service Integration Status

## Scope

This document tracks the real integration state of the current monorepo target:

- `gateway/`: single ingress and frontend-facing BFF
- `public-service/`: auth, conversation, upload, document, quota, admin, system
- `fastQA/`: fast-mode QA backend only
- `highThinkingQA/`: thinking-mode QA backend only

The `patent` backend is out of scope for this phase.

## Target Topology

```text
Frontend
  -> gateway
    -> public-service        (all public capabilities)
    -> fastQA                (fast ask / ask_stream only)
    -> highThinkingQA        (thinking ask / ask_stream only)
```

## Current Status

### 1. `gateway`

Progress: about `60%`.

What is already in place:
- canonical QA ingress routes:
  - `POST /api/{mode}/ask`
  - `POST /api/{mode}/ask_stream`
- legacy aliases:
  - `POST /api/ask`
  - `POST /api/ask_stream`
- gateway-side file-context resolution and route decision
- public route proxy surface for auth, conversation, uploads, documents, quota, admin, health
- SSE streaming proxy path is present and does not intentionally buffer to completion

What is still missing:
- real backend endpoint wiring and live smoke tests across all three services
- confirmation that conversation-file lookup is fed by `public-service` in the deployed path
- end-to-end verification that public proxy routes match current frontend usage without fallback to old services

### 2. `public-service`

Progress: about `75%`.

What is already in place:
- standalone FastAPI service structure
- modules for:
  - `system`
  - `auth`
  - `admin_users`
  - `quota`
  - `conversation`
  - `documents`
  - `uploads`
- health and lifespan wiring
- route surface largely matches the frontend/public backend expectation

What is still missing:
- final gateway-backed smoke tests for login, conversation, upload, file listing, PDF preview, translate, summarize
- confirmation that gateway forwards auth headers, multipart uploads, and inline PDF headers exactly as expected
- removal of remaining ownership overlap with `highThinkingQA`

### 3. `fastQA`

Progress: about `72%`.

What is already in place:
- standalone FastAPI service for fast-mode QA
- gateway-shaped ask contract
- `ask` and `ask_stream` mode aliases
- readiness gating and placeholder fallback control
- health/runtime lifecycle
- minimal local generation closure:
  - `stage1`
  - `stage2`
  - `stage3`
  - `stage4`
- minimal microscopic semantic search compatibility layer
- current regression status: `89 passed`

What is still missing:
- stable live HTTP smoke test with runtime enabled
- current enabled-mode bootstrap can now reach `ready`, but only after:
  - loading root workspace LLM config as env fallback
  - switching embedding to remote mode
  - pointing `VECTOR_DB_PATH` to the existing `/home/cqy/worktrees/fastapi-version/vector_database`
- this means fastQA is no longer blocked on missing API keys or embedding import failure
- remaining runtime issues are now resource-contract issues rather than core migration issues:
  - `resource/state/dev/fastQA/vector_database` still does not contain the required `lfp_papers` collection
  - `resource/state/dev/fastQA/vector_db_topic_index.json` is still missing
  - the current smoke path still depends on the legacy vector DB location for retrieval readiness
- answer quality still trails the source system because the migrated stage4 is intentionally minimal

### 4. `highThinkingQA`

Progress: about `40%` as a gateway-aligned thinking backend.

What is already in place:
- mature HTTP backend with:
  - `ask`
  - `ask_stream`
  - upload
  - auth
  - conversation
  - documents
  - quota
  - admin
- `/api/{mode}/ask` and `/api/{mode}/ask_stream` compatibility routes already exist

What is still missing:
- clear narrowing to `thinking QA only`
- alignment to the canonical gateway-owned normalized execution payload
- normalization of non-stream and SSE response shapes to match `fastQA`
- removal of duplicated public capability ownership once `public-service` becomes authoritative

## Main Integration Gaps

1. `fastQA` can now bootstrap to `ready`, but still depends on a temporary legacy vector DB path and has not completed live HTTP smoke validation.
2. `highThinkingQA` still carries public capabilities that should move behind `public-service`.
3. `gateway` needs live end-to-end validation against the colocated `public-service`, `fastQA`, and `highThinkingQA`.
4. The unified backend execution contract exists in docs, but `highThinkingQA` and `fastQA` are not yet fully behavior-identical.

## Recommended Execution Order

1. Make `fastQA` reach real `ready` state with live runtime bootstrap.
2. Run gateway -> public-service smoke tests for public capabilities.
3. Align `highThinkingQA` ask/ask_stream request and SSE/done contracts to the same canonical shape as `fastQA`.
4. Run gateway -> fastQA -> highThinkingQA end-to-end mode routing tests from one frontend.
5. Only after those four steps, move to front-end complete acceptance.

## Real Overall Estimate

For full frontend-backend integration of the three-service architecture, current progress is about `55%`.
