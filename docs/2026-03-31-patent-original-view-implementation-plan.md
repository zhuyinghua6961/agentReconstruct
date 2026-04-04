# Patent Original View MinIO + Public-Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the MinIO-backed patent original-view stack so `patentQA` emits stable patent viewer links, `public-service` serves patent original content from MinIO, and `gateway` proxies the public original-view routes to the new upstream.

**Architecture:** Keep patent-domain section/anchor resolution in `patentQA`, but move actual original serving into `public-service` documents APIs backed by MinIO objects under `patent/originals/{canonical_patent_id}/...`. `gateway` remains the only public entrypoint and continues to proxy document-view traffic, while rollout is gated by MinIO backfill, manifest generation, parity validation, and cache/version support before cutover.

**Tech Stack:** Python, FastAPI, Pydantic, MinIO, httpx, pytest, Redis, `conda` environment `agent`

---

## Constraints And References

**Primary spec**
- Spec: [docs/2026-03-31-patent-original-view-minio-public-service-spec.md](/home/cqy/worktrees/highThinking/docs/2026-03-31-patent-original-view-minio-public-service-spec.md)

**Related approved specs**
- Patent delivery spec: [docs/2026-03-30-patentqa-delivery-spec.md](/home/cqy/worktrees/highThinking/docs/2026-03-30-patentqa-delivery-spec.md)
- Patent retrieval/original-view task plan: [docs/2026-03-31-patentqa-vector-retrieval-task-breakdown.md](/home/cqy/worktrees/highThinking/docs/2026-03-31-patentqa-vector-retrieval-task-breakdown.md)

**Scope constraints**
- This plan spans four write areas:
  - `public-service/backend/`
  - `gateway/`
  - `patent/`
  - `scripts/`
- No implementation work in unrelated services.
- `patentQA` remains the owner of viewer-link generation and original anchor semantics.
- `public-service` becomes the owner of patent original-view HTTP serving and MinIO reads.
- `gateway` remains the only public route surface.

**Environment prerequisite**
- `public-service` tests run from [public-service/backend](/home/cqy/worktrees/highThinking/public-service/backend)
- `gateway` tests run from [gateway](/home/cqy/worktrees/highThinking/gateway)
- `patent` tests run from [patent](/home/cqy/worktrees/highThinking/patent)
- Current repo packaging facts:
  - `gateway/` and `patent/` each have `pyproject.toml`, so editable install is available when needed.
  - `public-service/backend/` does not currently expose its own `pyproject.toml` or `setup.py`; run tests there with `PYTHONPATH=.` instead of assuming `pip install -e .`.
- Before running Python tests in a clean environment:
  - `cd /home/cqy/worktrees/highThinking/gateway && conda run -n agent pip install -e .`
  - `cd /home/cqy/worktrees/highThinking/patent && conda run -n agent pip install -e .`
  - `cd /home/cqy/worktrees/highThinking/public-service/backend && PYTHONPATH=. conda run -n agent pytest tests/test_health.py -q`

---

## File Structure Map

### Public-service files to modify

- `public-service/backend/app/modules/documents/api.py`
  - Add patent original-view routes under `/api/patent/original/{canonical_patent_id}` and `/api/v1/patent/original/{canonical_patent_id}` with `GET`/`HEAD`.
- `public-service/backend/app/modules/documents/service.py`
  - Add patent original-view service methods for manifest loading, structured-content lookup, figure selection, PDF serving, and cache/version logic.
- `public-service/backend/app/modules/documents/schemas.py`
  - Add request/response models for patent original-view query parameters and JSON responses.
- `public-service/backend/app/modules/documents/cache.py`
  - Add or expose cache helpers for patent original-view cache entries keyed by `original_version`.
- `public-service/backend/app/modules/storage/service.py`
  - Add object-name helpers and MinIO fetch helpers for patent original assets.
- `public-service/backend/app/integrations/storage/minio.py`
  - Add object-stat/stream/read helpers needed for manifest JSON, figure object, and PDF reads.
- `public-service/backend/app/core/config.py`
  - Add patent-original MinIO prefix / optional tuning config if needed.

### Public-service tests to modify or create

- `public-service/backend/tests/test_documents_module.py`
  - Extend route and documents-service contract coverage for patent original-view.
- `public-service/backend/tests/test_route_surface.py`
  - Extend public route-surface assertions so the new patent original-view endpoints are explicitly registered.
- `public-service/backend/tests/test_config_independence.py`
  - Cover any new config fields if introduced.
- `public-service/backend/tests/test_live_public_service_integration.py`
  - Extend live MinIO integration coverage if the repo already uses it for storage validation.
- `public-service/backend/tests/test_patent_original_view_module.py`
  - New focused tests for manifest parsing, structured lookup, figure selection, and versioned cache behavior.

### Gateway files to modify

- `gateway/app/routers/public_proxy.py`
  - Add `/api/patent/original/{canonical_patent_id}` and `/api/v1/patent/original/{canonical_patent_id}` proxy routes to the public backend role.
- `gateway/app/services/route_table.py`
  - Register patent original-view routes under the public/document-proxy ownership set.

### Gateway tests to modify

- `gateway/tests/test_public_proxy.py`
  - Add patent original-view forwarding, `GET`/`HEAD`, streaming/header passthrough, and backend-header assertions.
- `gateway/tests/test_route_table.py`
  - Extend route-table assertions so the patent original-view paths are owned by the public route surface and do not overlap with QA routes.

### Patent files to modify

- `patent/server/patent/original_service.py`
  - Keep request parsing/anchor semantics but change `viewer_uri` generation to point at the public-service-backed public route.
- `patent/server/patent/retrieval_service.py`
  - Continue to emit `reference_links` / `original_links` using the new public route contract.
- `patent/tests/test_original_service.py`
  - Keep viewer URI generation tests aligned to the new source-of-truth.
- `patent/tests/test_patent_retrieval_service.py`
  - Verify generated `original_links` still satisfy the viewer contract.
- `patent/tests/test_patent_executor.py`
  - Verify final execution payload still uses the same public viewer path.
- `patent/tests/fastapi_contract/test_original_contract.py`
  - Rework the existing patent-local original-route contract so it becomes a compatibility test surface, not the source of truth for public serving semantics.
- `patent/tests/fastapi_contract/test_ask_contract.py`
  - Verify sync/SSE payloads still emit stable patent original-view links.

### New migration or support files to create

- `public-service/backend/app/modules/documents/patent_original_store.py`
  - Focused helpers for manifest loading, structured JSON lookup, figure selection, and version-aware fetch behavior.
- `public-service/backend/tests/fixtures/patent_original_store/`
  - Minimal manifest/JSON/figure/PDF fixtures for deterministic tests.
- `patent/server/patent/original_assets_tooling.py`
  - Domain-owned helpers for manifest generation, local corpus scanning, deterministic `original_version` calculation, and parity validation over the patent source corpus.
- `patent/tests/test_original_assets_tooling.py`
  - Focused tests for manifest generation inputs, object-key layout, and parity diagnostics.
- `scripts/patent_originals_backfill.py`
  - Thin CLI wrapper around patent tooling that backfills checked-in/local patent original assets into MinIO and generates `manifest.json`.
- `scripts/patent_originals_parity_check.py`
  - Thin CLI wrapper around patent tooling that validates MinIO corpus parity against the current local/archive source before cutover.

---

## Delivery Order

Separate implementation order from production cutover order. Code can land incrementally, but no environment may switch public serving ownership until the rollout gates are satisfied.

### Implementation Order

Implement in this order:

1. Public-service patent original-store models and MinIO helpers
2. Public-service patent original-view HTTP routes
3. Backfill and parity tooling
4. Gateway patent original-view proxy routes
5. Patent viewer-link generation and compatibility alignment
6. Cutover/readiness verification

This order keeps the serving path testable early, but still ensures the rollout gate work exists before any public cutover tasks are attempted.

### Production Cutover Order

Do not cut over in production until this order is satisfied:

1. MinIO backfill is complete
2. `manifest.json` exists for every target patent
3. local/archive corpus parity is green
4. `original_version`, `ETag`, and cache revalidation are verified in `public-service`
5. `gateway` upstream switches from patent-local serving to `public-service`
6. `patentQA` generated `viewer_uri` values are switched to the finalized explicit format contract

---

## Task 1: Add Public-Service Patent Original Store Core

**Files:**
- Create: `public-service/backend/app/modules/documents/patent_original_store.py`
- Modify: `public-service/backend/app/modules/storage/service.py`
- Modify: `public-service/backend/app/integrations/storage/minio.py`
- Modify: `public-service/backend/app/modules/documents/schemas.py`
- Test: `public-service/backend/tests/test_patent_original_view_module.py`

- [ ] **Step 1: Write failing tests for manifest and object lookup**

Cover:
- `manifest.json` loads from `patent/originals/{canonical_patent_id}/manifest.json`
- `original_version` is required and participates in cache metadata
- `claims.json` lookup by `claim_number`
- `description.json` lookup by `paragraph_id`
- section-level fallback when claim/paragraph anchor misses
- `figure` chooses `summary.primary_object` first, then `fulltext.primary_object`
- returned figure payload records `figure_source` and `served_object_key`
- `fulltext` resolves the configured PDF object key

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [public-service/backend](/home/cqy/worktrees/highThinking/public-service/backend): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_original_view_module.py -q`
Expected: FAIL because the patent original store module does not exist yet

- [ ] **Step 3: Implement the minimal store and MinIO read helpers**

Implement:
- manifest loader
- structured JSON parser helpers
- figure object selection helper
- `original_version` accessor
- MinIO object read/stat helpers required by the patent store

- [ ] **Step 4: Re-run the targeted tests**

Run from [public-service/backend](/home/cqy/worktrees/highThinking/public-service/backend): `PYTHONPATH=. conda run -n agent pytest tests/test_patent_original_view_module.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/documents/patent_original_store.py app/modules/storage/service.py app/integrations/storage/minio.py app/modules/documents/schemas.py tests/test_patent_original_view_module.py
git commit -m "feat: add patent original store core"
```

## Task 2: Add Public-Service Patent Original-View Routes

**Files:**
- Modify: `public-service/backend/app/modules/documents/api.py`
- Modify: `public-service/backend/app/modules/documents/service.py`
- Modify: `public-service/backend/app/modules/documents/cache.py`
- Modify: `public-service/backend/app/modules/documents/schemas.py`
- Test: `public-service/backend/tests/test_documents_module.py`
- Test: `public-service/backend/tests/test_route_surface.py`
- Test: `public-service/backend/tests/test_patent_original_view_module.py`

- [ ] **Step 1: Write failing tests for patent original-view HTTP contract**

Cover:
- `GET /api/patent/original/{canonical_patent_id}`
- `HEAD /api/patent/original/{canonical_patent_id}`
- `GET /api/v1/patent/original/{canonical_patent_id}`
- `HEAD /api/v1/patent/original/{canonical_patent_id}`
- route auth and quota behavior matches the existing documents family:
  - authenticated access path remains enforced
  - `file_view` quota is consumed on successful patent original views
  - query-token compatibility works for `GET` and `HEAD` if documents routes already permit it
- `claim / description / abstract / figure` return JSON/html/text according to request format
- `fulltext` returns inline PDF/stream when `fulltext/original.pdf` is available
- provider redirect remains a fallback-only path when the manifest says no local PDF is available
- `ETag` / `Cache-Control` derive from `original_version`
- `HEAD` shares lookup logic but returns no body
- error mapping for `PATENT_NOT_FOUND`, `ORIGINAL_NOT_AVAILABLE`, and `OBJECT_STORE_UNAVAILABLE`
- fallback behavior for section-level degradation when anchor misses
- explicit coverage for `ANCHOR_NOT_FOUND` only when strict-anchor mode is requested
- explicit coverage for `PROVIDER_REDIRECT_ONLY` when only redirect-style fallback is available

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [public-service/backend](/home/cqy/worktrees/highThinking/public-service/backend): `PYTHONPATH=. conda run -n agent pytest tests/test_documents_module.py tests/test_patent_original_view_module.py -q`
Expected: FAIL because the patent original-view routes and service methods do not exist yet

- [ ] **Step 3: Implement minimal patent original-view HTTP serving**

Implement:
- route registration in the documents API
- documents-service methods for patent original serving
- cache entries keyed by `canonical_patent_id + section + anchor + format + original_version`
- `GET` and `HEAD` parity
- content-type and cache-header propagation
- auth/quota behavior aligned with the existing documents viewer endpoints
- explicit default-format handling:
  - no-format requests to `public-service` follow the spec default (`json` for structured sections, stream/redirect for `fulltext`)
  - browser-facing compatibility is preserved by patent-generated links carrying an explicit `format`

- [ ] **Step 4: Re-run the targeted tests**

Run from [public-service/backend](/home/cqy/worktrees/highThinking/public-service/backend): `PYTHONPATH=. conda run -n agent pytest tests/test_documents_module.py tests/test_patent_original_view_module.py tests/test_route_surface.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/documents/api.py app/modules/documents/service.py app/modules/documents/cache.py app/modules/documents/schemas.py tests/test_documents_module.py tests/test_route_surface.py tests/test_patent_original_view_module.py
git commit -m "feat: add patent original view routes"
```

## Task 3: Add Gateway Patent Original Document Proxy

**Files:**
- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Test: `gateway/tests/test_public_proxy.py`
- Test: `gateway/tests/test_route_table.py`

**Execution gate:**
- Execute this task only after Task 5 backfill/parity tooling has produced the manifest and readiness evidence needed for cutover planning.
- Code may be prototyped earlier in a branch, but do not merge/deploy the proxy ownership change ahead of Task 5 outputs.

- [ ] **Step 1: Write failing tests for gateway patent original proxying**

Cover:
- `/api/patent/original/{canonical_patent_id}` forwards to the public backend
- `/api/v1/patent/original/{canonical_patent_id}` forwards to the public backend
- `GET` and `HEAD` are both forwarded
- auth headers and query params are preserved
- `json/html/text/pdf/redirect/stream` semantics are preserved
- `X-Gateway-Backend` remains `public`

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [gateway](/home/cqy/worktrees/highThinking/gateway): `PYTHONPATH=. conda run -n agent pytest tests/test_public_proxy.py tests/test_route_table.py -q`
Expected: FAIL because patent original-view routes are not in the public proxy surface yet

- [ ] **Step 3: Implement the minimal gateway route additions**

Implement:
- new public-proxy route entries
- route-table ownership entries
- streaming-route handling if patent fulltext responses use streamed bodies

- [ ] **Step 4: Re-run the targeted tests**

Run from [gateway](/home/cqy/worktrees/highThinking/gateway): `PYTHONPATH=. conda run -n agent pytest tests/test_public_proxy.py tests/test_route_table.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routers/public_proxy.py app/services/route_table.py tests/test_public_proxy.py tests/test_route_table.py
git commit -m "feat: proxy patent original routes through public backend"
```

## Task 4: Repoint Patent Viewer-Link Generation

**Files:**
- Modify: `patent/server/patent/original_service.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server_fastapi/routers/original.py`
- Test: `patent/tests/test_original_service.py`
- Test: `patent/tests/test_patent_retrieval_service.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/fastapi_contract/test_original_contract.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

**Execution gate:**
- Execute this task after Task 5 confirms the MinIO corpus, manifest schema, and parity checks are ready for cutover.
- This task is the contract-finalization step for browser-facing `viewer_uri`; it must not ship before the serving stack is actually ready.

- [ ] **Step 1: Write failing tests for explicit-format viewer links and patent-local compatibility**

Cover:
- `viewer_uri` contract remains gateway-relative but becomes explicit about `format` for browser-facing structured-section links
- `claim / description / abstract / figure` links emitted by patent payloads explicitly carry the finalized compatibility format
- `reference_links` and `original_links` remain stable after serving ownership moves
- ask sync / SSE `done` payloads do not change shape
- `patent/server_fastapi/routers/original.py` is treated as compatibility coverage only and no longer defines the production public-serving contract
- patent-local no-format behavior is either removed or documented as compatibility-only; it must not silently contradict the public-service cutover contract

- [ ] **Step 2: Run the targeted tests and capture the baseline**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_original_service.py tests/test_patent_retrieval_service.py tests/test_patent_executor.py tests/fastapi_contract/test_original_contract.py tests/fastapi_contract/test_ask_contract.py -q`
Expected:
- current tests may already PASS because gateway-relative `viewer_uri` is already in place
- after adding explicit-format and compatibility assertions, at least the contract delta tests should fail before implementation
- use that failure to drive the compatibility migration instead of assuming the whole task starts red

- [ ] **Step 3: Implement minimal patent-side link alignment**

Implement:
- stable gateway-relative viewer URI generation
- explicit `format` emission for structured-section viewer links so cutover does not change browser-visible behavior unexpectedly
- retrieval-service link generation unchanged in caller-facing shape
- remove any patent-side assumptions that it remains the public-serving owner
- keep `patent/server_fastapi/routers/original.py` only as compatibility or internal coverage until a later cleanup explicitly removes it

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_original_service.py tests/test_patent_retrieval_service.py tests/test_patent_executor.py tests/fastapi_contract/test_original_contract.py tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/patent/original_service.py server/patent/retrieval_service.py server_fastapi/routers/original.py tests/test_original_service.py tests/test_patent_retrieval_service.py tests/test_patent_executor.py tests/fastapi_contract/test_original_contract.py tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: repoint patent original viewer links"
```

## Task 5: Add Backfill And Parity Tooling

This task is the rollout gate for every later ownership change. In execution order, finish this task before Task 3 and Task 4 even though it appears later in the document.

**Files:**
- Create: `patent/server/patent/original_assets_tooling.py`
- Create: `patent/tests/test_original_assets_tooling.py`
- Create: `scripts/patent_originals_backfill.py`
- Create: `scripts/patent_originals_parity_check.py`

- [ ] **Step 1: Write failing tests for manifest generation and parity rules**

Cover:
- local archive inputs produce the expected object-key layout under `patent/originals/{canonical_patent_id}/...`
- generated manifests include `original_version`
- figure manifests include deterministic `primary_object`
- parity check catches missing structured JSON / figure / PDF objects

- [ ] **Step 2: Run the targeted tests and verify failures**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_original_assets_tooling.py -q`
Expected: FAIL because the reusable tooling module does not exist yet

- [ ] **Step 3: Implement minimal backfill and parity scripts**

Implement:
- reusable patent-side tooling module
- archive-to-MinIO upload plan
- manifest generation
- parity validation report
- deterministic `original_version` calculation
- provider redirect fallback support only if the rollout corpus contains patents without local `fulltext/original.pdf`

- [ ] **Step 4: Re-run the targeted tests**

Run from [patent](/home/cqy/worktrees/highThinking/patent): `PYTHONPATH=. conda run -n agent pytest tests/test_original_assets_tooling.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/patent/original_assets_tooling.py tests/test_original_assets_tooling.py ../scripts/patent_originals_backfill.py ../scripts/patent_originals_parity_check.py
git commit -m "feat: add patent originals backfill tooling"
```

## Task 6: Run Cross-Service Verification And Cutover Readiness Checks

**Files:**
- No planned code changes unless regressions fail. Fix only files within `public-service/backend/`, `gateway/`, or `patent/` if needed.

- [ ] **Step 1: Run public-service verification**

Run from [public-service/backend](/home/cqy/worktrees/highThinking/public-service/backend):

```bash
PYTHONPATH=. conda run -n agent pytest \
  tests/test_documents_module.py \
  tests/test_patent_original_view_module.py \
  tests/test_route_surface.py \
  tests/test_config_independence.py -q
```

Expected: PASS

- [ ] **Step 2: Run gateway verification**

Run from [gateway](/home/cqy/worktrees/highThinking/gateway):

```bash
PYTHONPATH=. conda run -n agent pytest tests/test_public_proxy.py tests/test_route_table.py -q
```

Expected: PASS

- [ ] **Step 3: Run patent verification**

Run from [patent](/home/cqy/worktrees/highThinking/patent):

```bash
PYTHONPATH=. conda run -n agent pytest \
  tests/test_original_service.py \
  tests/test_patent_retrieval_service.py \
  tests/test_patent_executor.py \
  tests/fastapi_contract/test_original_contract.py \
  tests/fastapi_contract/test_ask_contract.py -q
```

Expected: PASS

- [ ] **Step 4: Run backfill/parity dry-run and record cutover readiness**

Run:
- `conda run -n agent python /home/cqy/worktrees/highThinking/scripts/patent_originals_backfill.py --dry-run`
- `conda run -n agent python /home/cqy/worktrees/highThinking/scripts/patent_originals_parity_check.py`

Check:
- manifests exist for all target patents
- `original_version` is present
- `ETag` / cache behavior is enabled in public-service responses
- gateway routes are wired to the public backend path
- patent-generated browser-facing links carry an explicit `format`, so cutover does not change no-format semantics unexpectedly
- cutover can proceed without changing the gateway path family of `viewer_uri`
- if rollout scope includes patents without local PDF objects, provider redirect fallback is verified; otherwise explicitly record that the first cutover cohort is local-PDF-only

- [ ] **Step 5: Commit only if regression fixes were required**

```bash
git add public-service/backend gateway patent scripts
git commit -m "fix: close patent original view rollout gaps"
```

---

## Notes For Executors

- `patentQA` is not the public-serving owner anymore; do not keep patent-side HTTP serving as the source of truth for front-end original-view.
- `public-service` must not infer anchors from retrieval metadata; it only consumes explicit `section + claim_number + paragraph_id`.
- Backfill/parity logic belongs in a reusable patent-domain module; top-level `scripts/` should stay as thin operators' entrypoints, not the only place that owns business logic.
- Provider redirect fallback is a gated capability, not unconditional scope: require it before cutover only if the selected rollout corpus cannot guarantee local PDF availability.
- Do not ship cutover before MinIO backfill, manifest generation, parity checks, and `original_version`/`ETag` behavior are in place.
- Keep TDD discipline: failing test, verify failure, minimal implementation, verify pass, commit.
