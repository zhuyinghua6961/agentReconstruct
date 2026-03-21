# FastQA Migration Checklist

## Goal

Extract the QA execution closure from `/home/cqy/worktrees/fastapi-version` into `fastQA/` while keeping it as an independent backend service behind `gateway`.

This is a service extraction, not a copy of the whole `fastapi-version` backend.

## Phase 1 Target Ownership

`fastQA` Phase 1 should own only fast-mode `kb_qa` execution:

- `ask`
- `ask_stream`
- gateway-normalized request adaptation
- QA retrieval / synthesis / rerank / cache / streaming helpers

## Out Of Scope For Phase 1

Do not bring these into `fastQA` Phase 1:

- `ask_gateway`
- `ask_dispatch`
- auth
- conversation CRUD
- uploads
- documents
- admin
- quota
- `qa_pdf`
- `qa_tabular`
- `file_context`
- public storage authority

## Minimum Extraction Boundary

Must preserve together:

- ask route contract
- SSE event contract
- KB QA branch
- execution-time cache / retrieval / rerank dependencies
- runtime config needed by the above

## Gateway Compatibility

Phase 1 must stay compatible with `gateway` on:

- `POST /api/v1/ask`
- `POST /api/v1/ask_stream`
- current SSE event names and answer assembly behavior
- explicit rejection or ignore policy for unsupported file-oriented payload fields

Current status:

- preserved now: `metadata`, `step`, `content`, `done`, `error`
- preserved now: `thinking -> step` normalization through `legacy_type=thinking`
- preserved now: busy path emits SSE `error + done`, not raw JSON
- preserved now: file / hybrid payloads are rejected explicitly
- pending: real answer content, references, timings from migrated `kb_qa`

## Resource Contract

All mutable paths must move under `resource/state/dev/fastQA` or `resource/runtime/dev/fastQA`.

At minimum rebind:

- `PAPERS_DIR`
- `CHAT_JSON_BASE_DIR`
- `VECTOR_DB_*`
- `JSON_*`
- `PDF_CHUNKS_DIR`
- `TRANSLATION_CACHE_DIR`
- runtime logs / pid files

## Main Risks

- high: current fast ask path mixes QA execution with persistence and file-context routing
- high: `QA_QUERY_PIPELINE_MODE=legacy` would pull in a much larger legacy dependency graph
- high: SSE contract drift will break gateway passthrough
- high: service-wide concurrency is not solved yet; limiter is still per-process
- high: disconnect cancellation is only router-local until a real runtime consumes `should_cancel`
- medium: config surface is much larger than `highThinkingQA`
- medium: local model / embedding path assumptions may still point outside the monorepo
- medium: first-token latency may regress if the migrated LLM transport still creates a fresh `httpx.Client` per request
