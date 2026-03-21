# Public-Service Full Replacement Detailed Spec

## 1. Goal

Establish `public-service` as the only standalone public backend behind `gateway`.

Final state:
- `gateway` owns only routing, QA mode decisioning, and file-context-based QA dispatch
- `public-service` owns all public capabilities
- `fastapi-version` no longer serves as the public API baseline

## 2. Confirmed Decisions

These decisions are fixed unless explicitly changed later:
- external public API baseline: `gateway` frontend's current real calls
- `/api/v1/*` policy: retain compatibility
- `view_pdf` policy: authentication is mandatory
- operational endpoint policy:
  - `kb_info`: admin
  - `refresh_kb`: admin
  - `clear_cache`: admin
  - `background_status`: admin
- `reference_preview` policy: `public-service` must accept current frontend payload while retaining existing compatibility fields
- upload policy: require authenticated conversation-bound upload
- runtime decoupling policy:
  - P0: contract-compatible replacement first
  - P1: remove remaining runtime coupling
- cutover policy: keep a rollback window instead of immediate hard deletion of old public capability paths

## 3. Architectural Boundary

### 3.1 gateway responsibilities
- route public API requests to `public-service`
- route QA requests to fast/thinking/patent backends
- inspect file context for QA routing only
- preserve request and response payloads for public APIs
- avoid embedding public business semantics

### 3.2 public-service responsibilities
- authentication and user identity
- conversations and persistence
- uploaded file metadata lifecycle
- upload and file download/delete/preview
- translation, summary, literature content, reference preview
- quota management
- admin operations
- health and operational endpoints

## 4. Replacement Acceptance Standard

`public-service` is a full replacement only if all items below are satisfied.

### 4.1 Contract compatibility
For each public endpoint:
- path is correct
- `/api/v1/*` compatibility exists where frontend depends on it
- request payload matches frontend usage
- response payload matches frontend expectations
- status code and error code behavior are stable
- auth and admin requirements are explicit and consistent
- file download and PDF preview headers are correct

### 4.2 Runtime independence
- public-service starts and serves public APIs without depending on `fastapi-version`
- public-service operational behavior is understandable in isolation
- hidden assumptions about monolith-local runtime objects are identified and either accepted temporarily or removed

### 4.3 Cutover readiness
- `gateway` can proxy all public routes to `public-service`
- frontend critical flows work without frontend-side compatibility hacks
- rollback to old public backend remains operational during rollout

## 5. Priority Public API Surface

### 5.1 Auth
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/register`
- `GET /api/v1/auth/me`
- `PUT /api/v1/auth/password`
- `POST /api/v1/auth/forgot-password/initiate`
- `POST /api/v1/auth/forgot-password/verify`
- `GET /api/v1/auth/security-questions`
- `PUT /api/v1/auth/security-questions`

### 5.2 Conversations
- `POST /api/v1/conversations`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`
- `PUT /api/v1/conversations/{conversation_id}/title`
- `POST /api/v1/conversations/{conversation_id}/messages`
- `DELETE /api/v1/conversations/{conversation_id}`
- `GET /api/v1/conversations/{conversation_id}/files`
- `GET /api/v1/conversations/{conversation_id}/files/{file_id}`
- `DELETE /api/v1/conversations/{conversation_id}/files/{file_id}`
- `GET /api/v1/conversations/{conversation_id}/files/{file_id}/download`

### 5.3 Uploads
- `POST /api/v1/upload_pdf`
- `POST /api/v1/upload_excel`
- `POST /api/v1/clear_pdf`

### 5.4 Documents
- `POST /api/v1/translate`
- `POST /api/v1/summarize_pdf/{doi}`
- `GET /api/v1/extract_pdf_text/{doi}`
- `GET /api/v1/check_pdf/{doi}`
- `GET /api/v1/view_pdf/{doi}`
- `HEAD /api/v1/view_pdf/{doi}`
- `GET /api/v1/literature_content`
- `POST /api/v1/reference_preview`

### 5.5 Operations and Governance
- `GET /api/v1/kb_info`
- `POST /api/v1/refresh_kb`
- `POST /api/v1/clear_cache`
- `GET /api/v1/background_status`
- `GET /api/v1/quota/my`
- `GET /api/v1/quota/configs`
- `POST /api/v1/quota/configs`
- `PUT /api/v1/quota/configs/{quota_type}`
- `GET /api/v1/quota/users/{user_id}`
- `POST /api/v1/quota/reset/{user_id}/{quota_type}`
- `/api/admin/*`

## 6. Known Contract Pressure Points

### 6.1 reference_preview request shape
Current gateway frontend sends:
```json
{
  "doi": ["10.1000/test"],
  "max_items": 5
}
```

`public-service` currently expects `doi_list` and `dois_text`.

Required resolution:
- support frontend `doi`
- continue supporting `doi_list`
- continue supporting `dois_text` where useful for compatibility

### 6.2 Upload return payloads
Frontend currently depends on these top-level fields after upload:
- `file_id`
- `filename`
- `filepath`
- `storage_ref`
- `parse_status`
- `index_status`
- `processing_stage`

These fields must remain stable.

### 6.3 Conversation file metadata
`gateway` QA routing and frontend both depend on stable file metadata.

Required stable fields:
- `file_id`
- `file_type`
- `file_name`
- `file_status`
- `parse_status`
- `index_status`
- `processing_stage`
- `file_meta`
- `file_no`
- `display_no`

### 6.4 PDF preview semantics
Current policy is fixed:
- `view_pdf` requires authentication
- token query compatibility may remain for browser-open flows if needed
- inline preview headers must be preserved

### 6.5 Operational endpoint permissions
These are admin-only by policy and should stay explicit.

## 7. Runtime Independence Scope

### 7.1 P0 scope
P0 does not require full removal of all runtime couplings.

P0 requires:
- API contract correctness
- gateway compatibility
- frontend compatibility
- deployability as an independent public service for public routes

### 7.2 P1 scope
P1 addresses deeper service independence issues, including:
- lingering `runtime.agent` dependencies in document endpoints
- monolith-style control surfaces that do not fit standalone service ownership
- operational semantics cleanup

## 8. Delivery Phases

### P0: Contract-Compatible Replacement
- fix request/response mismatches
- verify `/api/v1/*` compatibility
- verify gateway passthrough behavior
- run critical flow regression

### P1: Service Independence Hardening
- remove or isolate hidden runtime coupling
- normalize operational semantics
- tighten service ownership boundaries

### P2: Cutover and Hardening
- rollout readiness
- rollback plan verification
- production monitoring and alerting expectations
- deprecation plan for old public capability ownership

## 9. Test Strategy

### 9.1 Contract tests
For each priority endpoint:
- success path
- auth failure path where applicable
- validation failure path where applicable
- response shape assertions

### 9.2 Integration tests
At minimum:
- login
- create conversation
- upload PDF with conversation binding
- list files
- download file
- reference preview
- view PDF with auth
- quota and admin smoke checks

### 9.3 Gateway compatibility tests
- gateway forwards public APIs transparently
- gateway accepts `/api/v1/*` entrypoints
- gateway does not rewrite public request bodies

## 10. Current Execution Rule

Implementation priority from now on:
1. fix `public-service` contract mismatches against real frontend usage
2. keep `gateway` thin
3. do not move public business logic into `gateway`
4. treat `fastapi-version` as rollback reference, not design authority
