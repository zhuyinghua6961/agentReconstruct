# Gateway + Public Service Remediation Spec

## Goal

Stabilize the current `gateway + public-service` deployment for production-like use without breaking the already aligned frontend contract.

This remediation targets four classes of problems found in review:

- large-file proxy performance risk
- multi-instance consistency gaps
- legacy local-state behavior
- residual runtime coupling in public document APIs

## Current State

What is already good:

- route surface is broadly aligned with the frontend
- replacement-oriented tests passed previously
- `/api/v1/*` compatibility is retained

What is still unsafe:

- `gateway` buffers upload and file/PDF responses in memory
- uploaded-file processing can still depend on the instance-local filesystem
- `refresh_kb` and `clear_cache` are instance-local only
- `clear_pdf/current_pdf_path` is legacy mutable process state
- `reference_preview` and `literature_content` are slower and more coupled than they should be

## Hard Constraints

- Do not break existing frontend API paths or payload shape unless the gateway contract is updated first.
- Do not regress authenticated PDF preview and conversation-bound upload behavior.
- Preserve current answer quality; performance work must not change QA semantics.
- Prefer eliminating instance-local semantics over documenting them.

## Remediation Priorities

## P0: Stop Production Risks

### P0.1 Gateway streaming proxy for multipart upload and binary/file responses

Problem:

- uploads currently read the full request body into memory
- binary responses currently read the full upstream body into memory

Required change:

- add a streaming forward path in `gateway`
- use it for `upload_pdf`, `upload_excel`, `view_pdf`, and conversation file download routes
- preserve response headers such as `Content-Type` and `Content-Disposition`

Acceptance:

- large uploads do not require whole-body buffering in the gateway
- PDF preview remains inline
- file download behavior matches direct upstream behavior

### P0.2 Make uploaded-file availability cluster-safe

Problem:

- upload success does not guarantee object storage success
- background parsing currently reads `local_path` directly

Required change:

- define object storage as the durable source of truth for uploaded files
- if upload mirror fails, either fail the request or mark the file unusable and do not enqueue processing
- worker execution must be able to hydrate a local temp copy from `storage_ref`

Acceptance:

- an uploaded file can be parsed or downloaded from any instance
- upload success implies cross-instance availability
- mirror failure no longer produces a false-success state

### P0.3 Remove misleading process-global PDF state from business semantics

Problem:

- `current_pdf_path` and `clear_pdf` are process-local
- actual upload semantics are now conversation-bound

Required change:

- deprecate `current_pdf_path` as a business source of truth
- keep `clear_pdf` only as a compatibility no-op or explicit frontend reset endpoint
- ensure no core behavior depends on this field

Acceptance:

- no business flow depends on process-global PDF state
- multi-instance behavior is not affected by calling `clear_pdf`

## P1: Fix Distributed Semantics

### P1.1 Re-scope admin cache and retrieval operations

Problem:

- `clear_cache` only clears one instance
- `refresh_kb` only refreshes one instance

Required change:

- make the API semantics explicit as cluster-level or per-instance
- if cluster-level is required, back them with Redis/pub-sub, version flags, or an orchestrated admin job
- if only per-instance is supported temporarily, expose this clearly in API and UI messaging

Acceptance:

- operators can predict the real effect of these endpoints
- no silent partial-refresh state across replicas

### P1.2 Eliminate remaining standalone coupling gaps in document APIs

Problem:

- `literature_content` and `reference_preview` still depend on in-process retrieval runtime objects

Required change:

- either keep retrieval as an explicit public-service dependency and formalize it
- or move metadata lookup behind a dedicated storage/index service contract

Acceptance:

- the public-service dependency model is explicit
- failure modes are controlled and testable

## P2: Performance and Query Quality

### P2.1 Parallelize `reference_preview`

Required change:

- batch or parallelize DOI metadata lookup
- batch object-storage existence checks where possible
- add concurrency limits to avoid overwhelming Neo4j/Chroma/MinIO

Acceptance:

- preview latency scales sub-linearly for typical DOI batches
- response order remains stable

### P2.2 Tighten `literature_content` DOI lookup

Required change:

- prefer exact DOI match over `CONTAINS`
- only fall back to looser matching with explicit guardrails
- review index strategy for graph lookup

Acceptance:

- lower false-match risk
- predictable query cost

## Design Decisions

### Uploaded file source of truth

- durable source: `storage_ref`
- local disk: ephemeral cache only
- queue/worker payload: carry `file_id` and fetch metadata, not trust stale local paths alone

### Gateway role

- gateway remains thin
- gateway handles transport concerns and route ownership
- gateway must not become a file persistence owner

### Compatibility policy

- keep existing route paths
- prefer internal semantic fixes over frontend rewrites
- add tests before removing compatibility-only behavior

## Validation Plan

### Functional

- upload PDF and Excel through gateway
- parse uploaded files on a different instance simulation
- preview PDF inline through gateway
- download conversation file through gateway
- call `clear_pdf` and verify no business-state regression

### Distributed

- simulate mirror failure and verify request/result semantics
- simulate worker execution on an instance without the original local file
- verify `refresh_kb` and `clear_cache` semantics are explicit

### Performance

- upload a large file and monitor gateway memory growth
- preview 10-30 DOI items and compare before/after latency
- stream PDF/file responses and confirm no full-buffer pause

### Regression

- retain existing contract tests
- add route-specific tests for streaming proxy behavior
- add failure-case tests for object storage unavailability

## Rollout Order

1. P0.1 gateway streaming proxy
2. P0.2 uploaded-file durability semantics
3. P0.3 remove business dependence on `current_pdf_path`
4. P1.1 cluster-safe admin semantics
5. P1.2 document API dependency formalization
6. P2.1 `reference_preview` optimization
7. P2.2 `literature_content` lookup tightening

## Non-Goals

- no QA-answer logic rewrite in this remediation
- no frontend feature redesign
- no v1 route removal in this phase
