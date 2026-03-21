# Public-Service Full Replacement Spec

## Objective

Make `public-service` the long-term standalone public backend behind `gateway`.

After replacement:
- `gateway` remains a thin layer for request routing, QA mode selection, and file-context-based decisioning
- `public-service` becomes the only owner of public capabilities
- `fastapi-version` no longer acts as the source of truth for public APIs or public infrastructure

## Target Responsibility Split

### gateway
- transparent proxy for public APIs
- QA intent analysis and mode routing
- file context lookup for QA routing
- no ownership of auth, conversations, uploads, quota, admin, or document public APIs

### public-service
- auth
- conversations and conversation persistence
- uploaded file metadata and download/delete flows
- PDF and Excel upload flows
- document public APIs such as preview, summary, translation, reference preview, literature content
- quota
- admin user management
- public health and operational endpoints

## Replacement Standard

`public-service` is considered a full replacement only when all of the following are true:
- frontend-visible public APIs are contract-compatible
- `gateway` can transparently forward public traffic without request/response rewriting
- runtime behavior no longer depends on `fastapi-version` assumptions
- public-service is independently deployable and operable

## Required Workstreams

### 1. API Contract Freeze

Freeze and verify for every public API:
- canonical path
- whether `/api/v1/*` compatibility is required
- request body shape
- response body shape
- auth requirement
- admin requirement
- error code and status code behavior
- content-disposition and content-type headers for file responses

Priority endpoints:
- `/api/v1/auth/*`
- `/api/v1/conversations/*`
- `/api/v1/upload_pdf`
- `/api/v1/upload_excel`
- `/api/v1/clear_pdf`
- `/api/v1/reference_preview`
- `/api/v1/literature_content`
- `/api/v1/translate`
- `/api/v1/kb_info`
- `/api/v1/refresh_kb`
- `/api/v1/clear_cache`
- `/api/v1/quota/*`
- `/api/admin/*`
- `/api/v1/view_pdf/{doi}`

### 2. Frontend Compatibility

Current gateway frontend already assumes stable contracts for:
- auth flows
- conversation list/detail/update
- file list metadata fields
- upload success payloads
- PDF preview URL behavior
- quota and admin pages
- reference preview request and response shapes

Known contract mismatch already identified:
- frontend sends `reference_preview` payload using `doi`
- `public-service` currently expects `doi_list` and `dois_text`

This type of mismatch must be solved in `public-service`, not in `gateway`.

### 3. Permission and Access Semantics

These rules must be explicitly decided and then implemented consistently in `public-service`:
- whether `view_pdf` requires login
- whether `kb_info` requires login or admin
- whether `refresh_kb` requires admin
- whether `clear_cache` requires admin
- whether `background_status` requires admin
- whether upload APIs require both auth and conversation binding

The key rule is: `gateway` should not encode public business permissions.

### 4. Runtime Independence

`public-service` still needs to be audited for remaining runtime coupling.

Special attention areas:
- `literature_content`
- `reference_preview`
- any endpoint that still relies on `runtime.agent`
- any endpoint that still behaves like a monolith-local control path rather than a service API

The goal is not just route compatibility. The goal is operational independence.

### 5. Operational Readiness

Before full cutover, `public-service` must be clear on:
- MySQL ownership and schema expectations
- Redis usage and cache semantics
- object storage and download strategy
- health/status semantics
- background worker semantics
- failure behavior when DB, Redis, or storage are unavailable

## Delivery Phases

### Phase P0: Replacement Readiness
- freeze public API contracts
- resolve request/response mismatches
- validate gateway passthrough for all public APIs
- validate frontend critical paths

### Phase P1: Independent Service Readiness
- remove or isolate remaining monolith runtime assumptions
- align auth and admin semantics
- align health, cache, and operational endpoints
- validate deployment as an independent service

### Phase P2: Cutover Hardening
- full regression testing
- observability and operational playbook
- rollback plan
- deprecation plan for old public capabilities in `fastapi-version`

## Main Risks

### Contract Drift
The biggest risk is not routing failure but subtle incompatibility:
- same endpoint name
- different request body
- different response payload
- different auth behavior
- different file-response headers

### Permission Drift
A route may technically work through `gateway` but still break product behavior if auth or admin checks differ.

### Hidden Runtime Coupling
Some public-service endpoints may still depend on historical runtime objects or startup assumptions that are not acceptable for a standalone service.

### Incomplete Operational Ownership
If health, cache, workers, or storage semantics remain ambiguous, the service may be routable but not reliably operable.

## Immediate Next Step

Before implementation continues, the following decisions must be confirmed by the user:
- external public API baseline
- `/api/v1` retention policy
- permission policy for operational endpoints
- document preview auth policy
- scope of runtime decoupling in this phase
