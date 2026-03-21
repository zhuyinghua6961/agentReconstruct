# FastQA KB-Only Phase 1 Spec

## Goal

Build the first runnable `fastQA` extraction as a pure `kb_qa` backend.

This phase does not aim to preserve full `fastapi-version` ask behavior. It aims to preserve the core fast knowledge-base QA path behind `gateway` while keeping the extraction boundary small and explicit.

## Phase 1 Inclusion

Include only:

- FastAPI ask entrypoints for `ask` and `ask_stream`
- request adapter from gateway payload into `QaKbRequest`
- `qa_kb`
- `generation_pipeline`
- `retrieval`
- `qa_cache`
- required `integrations/*`
- prompt loading
- SSE framing
- runtime/config/logging bootstrapping

## Phase 1 Exclusion

Do not include:

- `ask_gateway`
- `ask_dispatch`
- auth
- quota
- conversation CRUD
- uploads API
- documents API
- admin APIs
- `qa_pdf`
- `qa_tabular`
- file-context resolution
- public-service persistence ownership

## Why KB-Only First

The current `fastapi-version` ask entrypoint mixes:

- file-context routing
- persistence side effects
- upload-aware execution
- PDF / tabular branches
- auth/quota hooks

Copying that whole path into `fastQA` would recreate the monolith boundary we are trying to remove.

## Required API Surface

Phase 1 must expose only:

- `POST /api/v1/ask`
- `POST /api/v1/ask_stream`
- `GET /api/v1/health`

The request body should accept the gateway-normalized ask payload, but Phase 1 may reject unsupported file-oriented or hybrid fields explicitly.

## Request Policy

Phase 1 accepts:

- plain question
- optional chat history if gateway provides it
- optional trace/request metadata

Phase 1 rejects or ignores:

- direct file execution hints
- upload-specific PDF/tabular routing hints
- public persistence directives

## Path Policy

All mutable paths must bind to the monorepo resource contract:

- `SERVICE_CONFIG_ROOT`
- `SERVICE_STATE_ROOT`
- `SERVICE_RUNTIME_ROOT`
- `SERVICE_ASSET_ROOT`

## Copy Order

1. `app/core`: env/config/logging/prompts/sse
2. `app/integrations`: llm / embedding / vector_db / redis / neo4j
3. `app/modules/retrieval`
4. `app/modules/storage/paper_storage.py`
5. `app/modules/qa_cache`
6. `app/modules/generation_pipeline`
7. `app/modules/qa_kb`
8. thin `ask` / `ask_stream` API layer
9. focused tests

## Acceptance

Phase 1 is complete when:

- `fastQA` starts independently
- `POST /api/v1/ask_stream` returns valid SSE
- core `kb_qa` answers run without importing public-service modules
- all runtime writes land under `resource/state/dev/fastQA` or `resource/runtime/dev/fastQA`
