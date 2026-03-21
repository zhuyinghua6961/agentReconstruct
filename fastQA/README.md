# FastQA

Independent `fast` QA backend workspace inside the single-repository layout.

## Status Notice

This service is not functionally complete yet.
It is still in migration and alignment work, and must not be treated as a finished production-equivalent replacement for the legacy fastapi-version implementation.

## Current Phase

This directory now has a runnable phase-1 prep baseline:

- `app/`
- `scripts/`
- `tests/`
- `pyproject.toml`
- `app/integrations/redis/`
- `app/modules/qa_cache/`

The real `kb_qa` execution closure has not been extracted yet. Current runtime only exposes the gateway-facing contract, Redis/bootstrap health wiring, and cache helpers.

Source baseline:
- `/home/cqy/worktrees/fastapi-version/backend`

## Planned Role

Own only:
- fast-mode QA execution
- fast `kb_qa` answer execution
- gateway-normalized ask contract

Do not own long-term:
- auth
- conversations
- uploads
- documents
- quota
- admin
- frontend

## Required Phase-1 API Surface

- `POST /api/fast/ask`
- `POST /api/fast/ask_stream`
- `POST /api/v1/fast/ask`
- `POST /api/v1/fast/ask_stream`
- `GET /api/health`

Current skeleton behavior:

- `GET /api/health` is live
- Redis health is surfaced through `/api/health`
- ask routes return explicit `FASTQA_NOT_READY` placeholders until real `kb_qa` extraction lands
- unsupported file / hybrid routes are rejected explicitly in the router adapter
- `qa_cache` and singleflight helpers are ready for the real pipeline

## Current Verification

- `conda run -n agent pytest tests -q`
- latest result: `39 passed`

The HTTP contract tests use direct route/SSE consumption instead of `httpx.ASGITransport` because the current local test stack hangs even on a minimal FastAPI app. This is a test-harness constraint, not a `fastQA` business-path finding.

## Current Open Risks

- concurrency limit is still per-process, not service-wide across multiple Gunicorn workers
- disconnect cancellation is not yet wired into a real execution runtime
- real `generation_pipeline` / retrieval / LLM transport are not migrated yet

## Required Input Boundary

`gateway` must send normalized execution payloads. `fastQA` should not be the authority for:
- file-intent parsing
- ambiguous file clarification
- conversation file metadata lookup

## References

- [fastQA_gateway_alignment_spec.md](/home/cqy/worktrees/highThinking/docs/fastQA_gateway_alignment_spec.md)
- [fastQA_migration_tasks.md](/home/cqy/worktrees/highThinking/docs/fastQA_migration_tasks.md)
- [fastQA_source_mapping.md](/home/cqy/worktrees/highThinking/docs/fastQA_source_mapping.md)
