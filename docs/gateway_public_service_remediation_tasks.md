# Gateway + Public Service Remediation Tasks

## P0

### T0.1 Add gateway streaming proxy path

Status:

- completed

Target:

- repo: `gateway`
- modules: proxy transport, public proxy router, proxy tests

- add request-stream forwarding for multipart upload routes
- add response-stream forwarding for PDF and file download routes
- preserve auth, trace, and binary response headers
- add tests for `upload_pdf`, `upload_excel`, `view_pdf`, and conversation file download

Done when:

- gateway no longer uses full-body buffering on those routes

### T0.2 Fix uploaded-file durability contract

Status:

- completed

Target:

- repo: `public-service`
- modules: upload API, storage service, upload processing worker, tests

- audit upload success conditions
- require durable mirror success or return explicit failure
- update worker flow to recover file content from `storage_ref`
- add tests for original-instance-local-file missing scenarios

Done when:

- upload success means cross-instance usable

### T0.3 Remove `current_pdf_path` from business semantics

Status:

- completed

Target:

- repo: `public-service`
- modules: runtime, upload API, compatibility tests

- trace all reads/writes of `current_pdf_path`
- convert `clear_pdf` to compatibility-safe behavior
- ensure uploads, downloads, and file QA do not depend on it
- add regression tests

Done when:

- no core flow uses process-global PDF state

## P1

### T1.1 Rework `clear_cache` semantics

Status:

- completed

Target:

- repo: `public-service`
- modules: system service, runtime coordination, admin/system tests

- decide cluster-wide vs explicit instance-local behavior
- implement the chosen coordination/versioning mechanism or harden API messaging
- add admin contract tests

### T1.2 Rework `refresh_kb` semantics

Status:

- completed

Target:

- repo: `public-service`
- modules: system service, retrieval bootstrap coordination, health/admin tests

- decide cluster-wide vs explicit instance-local behavior
- implement coordination or explicit degraded semantics
- add health/admin tests proving the real scope

### T1.3 Formalize public document dependency model

Status:

- completed

Target:

- repo: `public-service`
- modules: documents service, retrieval/runtime boundary, docs/tests

- decide whether retrieval runtime remains inside `public-service`
- if yes, document and health-check it explicitly
- if no, define and implement a metadata lookup abstraction
- add failure-mode tests

## P2

### T2.1 Optimize `reference_preview`

Status:

- completed

Target:

- repo: `public-service`
- modules: documents reference preview, storage lookup path, performance tests

- profile graph/chroma/storage calls
- parallelize or batch metadata lookup with bounded concurrency
- keep output order deterministic
- add latency-oriented regression tests

### T2.2 Tighten `literature_content` lookup

Status:

- completed

Target:

- repo: `public-service`
- modules: documents service, graph/chroma lookup path, regression tests

- replace weak DOI match path
- verify fallback behavior for legacy rows
- review graph/index assumptions
- add exact-match and false-match regression tests

## Test Matrix

### Transport

- large upload through gateway
- PDF inline preview through gateway
- binary download through gateway

### Durability

- upload with mirror success
- upload with mirror failure
- worker parse after local file removal

### Consistency

- `clear_cache` semantics under multi-instance deployment
- `refresh_kb` semantics under multi-instance deployment
- `clear_pdf` does not affect conversation-bound file flows

### Performance

- gateway memory profile on large upload/download
- `reference_preview` batch latency
- `literature_content` query latency and match accuracy

## Execution Order

1. `T0.1`
2. `T0.2`
3. `T0.3`
4. `T1.1`
5. `T1.2`
6. `T1.3`
7. `T2.1`
8. `T2.2`
