# Quota Management Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify quota management around `ask_query`, `file_qa`, `file_view`, and `doc_assist`, with `public-service` as the authority, `gateway` handling QA-chain quota orchestration, and success-only charging for both sync and streaming asks.

**Architecture:** Keep quota config, quota usage, exemption rules, and admin/user quota reads in `public-service`. Add a thin internal quota-grant HTTP contract so `gateway` can precheck and finalize QA quotas after it has already classified the turn as normal QA or file QA. Migrate visible frontend/admin quota types to the four canonical buckets, keep legacy quota names as compatibility aliases during rollout, and stop exposing retired quota types in the active UI.

**Tech Stack:** FastAPI, Vue 3, gateway proxy layer, Redis/MySQL-backed quota service, pytest, Vite

**Explicitly Out of Scope:** `patent` ask-chain quota integration remains outside this rollout and must not be implicitly mapped into the four canonical buckets.

---

## File Map

### Public-Service

- Modify: `public-service/backend/app/modules/quota/service.py`
- Modify: `public-service/backend/app/modules/quota/schemas.py`
- Modify: `public-service/backend/app/modules/quota/api.py`
- Modify: `public-service/backend/app/modules/quota/deps.py`
- Modify: `public-service/backend/app/modules/documents/api.py`
- Modify: `public-service/backend/app/modules/conversation/api.py`
- Modify: `public-service/backend/app/modules/uploads/api.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Test: `public-service/backend/tests/test_quota_module.py`
- Test: `public-service/backend/tests/test_documents_module.py`
- Test: `public-service/backend/tests/test_uploads_module.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Test: `public-service/backend/tests/test_route_surface.py`

### Gateway

- Create: `gateway/app/services/quota_proxy.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `gateway/tests/test_public_proxy.py`

### Frontend

- Modify: `frontend-vue/src/views/QuotaManagement.vue`
- Modify: `frontend-vue/src/services/quota.js`
- Modify: `frontend-vue/src/api/quota.js`
- Modify: `frontend-vue/src/views/UserProfile.vue`

### Docs

- Reference: `docs/superpowers/specs/2026-03-28-quota-management-design.md`
- Update after implementation: `docs/audit/2026-03-28-retrieval-and-quota-todo.md`

---

## Lock Decisions

1. `user_type = 1/2` exempt, `user_type = 3` limited. Do not use `role` as the quota exemption authority.
2. User-visible quota types are only:
   - `ask_query`
   - `file_qa`
   - `file_view`
   - `doc_assist`
3. `gateway` is the ask-chain quota orchestrator because it already knows:
   - requested mode vs actual mode
   - final route classification
   - whether the turn used file context
   - whether a stream finished with a valid `done` event
4. `public-service` remains the only quota authority and owns:
   - config read/write
   - precheck/finalize semantics
   - exemption logic
   - quota usage storage
5. Successful-result-only charging:
   - sync ask: count only on successful upstream response
   - stream ask: count only after a valid `done` event
   - upstream error / timeout / broken stream / clarification / quota denial: do not count
6. Legacy quota types remain accepted as compatibility aliases during rollout, but must stop appearing in the active admin/user UI.
7. `doc_assist` only counts authenticated user calls in this rollout; anonymous compatibility routes remain available but unmetered.
8. `file_upload` is retired from the user-visible quota model; `excel_upload` may remain as a temporary internal legacy quota only if admin import still needs throttling.
9. `patent` ask-chain remains out of scope for this rollout and should be documented/tested as excluded rather than silently bucketed.
10. For successful user-facing business results, quota finalize/increment failure defaults to soft warning plus observability, not request-level 5xx rewriting.

## Rollout Order

Execute in this order, even if some task numbers would otherwise suggest parallel editing:

1. Canonical contract and legacy alias layer
2. Internal grant contract plus route-surface security checks
3. Gateway ask-chain quota orchestration
4. Document/file endpoint remap plus upload legacy handling
5. Canonical UI visibility switch
6. Verification and rollout notes

Reason:

- Do not hide legacy types from UI before backend consumers stop depending on them.
- Do not expose internal grant endpoints, even transiently, through public route tables.

---

### Task 1: Freeze the Canonical Quota Contract and Alias Layer

**Files:**
- Modify: `public-service/backend/app/modules/quota/service.py`
- Test: `public-service/backend/tests/test_quota_module.py`
- Modify: `frontend-vue/src/views/QuotaManagement.vue`
- Modify: `frontend-vue/src/views/UserProfile.vue`

- [x] Add failing quota-service tests for canonical type normalization and alias mapping
- [x] Cover these alias expectations in tests:
  - `kb_qa`, `thinking_qa` -> `ask_query`
  - `pdf_qa`, `tabular_qa`, `hybrid_qa` -> `file_qa`
  - `pdf_summary`, `text_translate`, `reference_preview`, `literature_content`, `extract_pdf_text` -> `doc_assist`
  - `file_view` stays `file_view`
- [x] Implement a single quota-type normalization path in `quota/service.py` so `check_quota`, `increment_quota`, `get_user_quotas`, `create_config`, and `update_config` all agree
- [x] Extend the same normalization/visibility contract to `get_all_configs` so the active admin API also becomes canonical-authoritative
- [x] Extend the same canonical contract to `reset_user_quota` so admin reset operates on canonical types rather than raw legacy names
- [x] Keep legacy quota rows readable only through compatibility-safe paths, not through the default active admin/user quota APIs
- [x] Add focused assertions for admin-facing quota APIs:
  - `get_all_configs` returns canonical 4-bucket active view for the admin page
  - `create_config` and `update_config` accept/administer canonical types
  - `reset_user_quota` resolves canonical types instead of requiring raw legacy names
- [x] Update frontend preset options so the admin page only offers the four canonical quota types
- [x] Re-run the focused quota tests and confirm the contract is stable before moving to cross-service QA charging

### Task 2: Add an Internal Quota-Grant HTTP Contract in Public-Service

**Files:**
- Modify: `public-service/backend/app/modules/quota/schemas.py`
- Modify: `public-service/backend/app/modules/quota/api.py`
- Modify: `public-service/backend/app/modules/quota/deps.py`
- Modify: `public-service/backend/app/modules/quota/service.py`
- Test: `public-service/backend/tests/test_quota_module.py`
- Test: `public-service/backend/tests/test_route_surface.py`
- Test: `gateway/tests/test_public_proxy.py`

- [x] Add failing tests for a two-step internal QA quota flow:
  - `precheck` returns an internal grant token for a non-exempt user
  - `finalize(success=true)` increments exactly once
  - `finalize(success=false)` releases without increment
  - duplicate finalize on the same token is idempotent
  - exempt users return a no-op grant
- [x] Define request/response schemas for internal quota-grant endpoints
- [x] Implement a tokenized grant lifecycle backed by Redis or a persistent fallback:
  - precheck creates a short-lived grant record keyed by `grant_id`
  - finalize consumes the grant record and performs success-only increment
  - abort or failed finalize releases state without increment
- [x] Require an internal-only trust boundary for these endpoints
  - recommended: shared internal service secret or equivalent gateway-to-public auth
  - do not expose these endpoints through the public admin/user route surface
  - do not add these endpoints to `gateway` public proxy route tables
- [x] Add failing route-surface tests proving the new internal endpoints are absent from public route surfaces and absent from `gateway` public proxy exposure
- [x] Keep the existing in-process `require_quota` / `finalize_quota` helpers for non-gateway code paths that already live inside `public-service`
- [x] Re-run focused quota module tests and confirm no regression in existing admin/user quota APIs

### Task 3: Integrate QA Mainline Charging in Gateway

**Files:**
- Create: `gateway/app/services/quota_proxy.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `gateway/tests/test_route_decision.py`

- [x] Add failing gateway tests for quota classification and success-only finalize behavior
- [x] Cover sync ask cases:
  - plain thinking/fast question -> precheck `ask_query`
  - file-only or mixed question -> precheck `file_qa`
  - clarification-required request -> no finalize
  - upstream non-2xx response -> no finalize
  - upstream `200` with `success=false` -> no finalize
  - upstream `200` with non-empty `error` -> no finalize
  - `patent` path is explicitly excluded from this rollout rather than implicitly charged
  - finalize/increment failure after an otherwise successful sync ask returns success plus warning metadata, not 5xx
- [x] Cover stream ask cases:
  - valid `done` event -> finalize counted
  - no `done` event / upstream error / timeout -> finalize not counted
  - thinking-mode request that is rerouted to fast file QA still uses `file_qa`
  - finalize/increment failure after a successful stream `done` still completes the stream and surfaces a warning marker in the terminal metadata/done payload
- [x] Implement a small gateway quota client that calls the new `public-service` internal precheck/finalize endpoints
- [x] Keep route classification authority in the existing gateway route-decision pipeline; do not re-derive `ask_query` vs `file_qa` inside downstream QA backends
- [x] Define one gateway-side sync success predicate for upstream JSON responses:
  - `success=false` means failure
  - non-empty `error` means failure
  - non-JSON success responses require explicit allow-list behavior
- [x] Implement ask-chain finalize soft-warning behavior:
  - sync ask keeps the successful upstream payload and attaches a warning/count marker on finalize failure
  - stream ask keeps the successful stream completion and appends warning/count metadata on finalize failure
  - structured logs and metrics record the finalize failure without rewriting the business result to 5xx
- [x] Reuse the existing stream-observation pattern already present in conversation persistence so quota finalize sees the same `done`/failure boundary as message persistence
- [x] Re-run focused gateway tests and confirm the quota client never changes upstream payload routing semantics

### Task 4: Remap Public-Service Document and File Actions to Canonical Buckets

**Files:**
- Modify: `public-service/backend/app/modules/documents/api.py`
- Modify: `public-service/backend/app/modules/conversation/api.py`
- Modify: `public-service/backend/app/modules/uploads/api.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Test: `public-service/backend/tests/test_documents_module.py`
- Test: `public-service/backend/tests/test_uploads_module.py`
- Test: `public-service/backend/tests/test_conversation_module.py`

- [x] Add failing API tests for canonical bucket usage:
  - `view_pdf` and conversation file download use `file_view`
  - authenticated `summarize_pdf`, `translate`, `reference_preview`, `literature_content`, `extract_pdf_text` use `doc_assist`
  - anonymous compatibility calls for `reference_preview`, `literature_content`, `extract_pdf_text` remain callable but do not consume user quota
  - conversation-bound upload no longer consumes a user-visible upload quota
- [x] Update `documents/api.py` so all document-assist endpoints share the same quota bucket and finalize behavior
- [x] Make the authenticated-vs-anonymous behavior explicit in API code and tests; do not leave it implicit in dependency wiring
- [x] Lock `doc_assist` strictness for authenticated calls:
  - authenticated `summarize_pdf`, `translate`, `reference_preview`, `literature_content`, `extract_pdf_text` all use strict quota config behavior
  - missing, inactive, unavailable, or check-failed `doc_assist` config states must all fail closed for authenticated calls
  - anonymous compatibility calls remain outside the quota model and therefore outside strictness enforcement
- [x] Decide and implement the upload policy exactly once:
  - user-facing conversation upload: no visible quota bucket in this rollout
  - existing `file_upload` config/history remain readable only as legacy compatibility data during the transition
  - admin batch Excel import: keep internal behavior only if still needed, but do not surface it as a user-visible quota type
  - existing `excel_upload` config/history remain compatibility-only and do not appear in canonical UI
- [x] Keep `file_view` charging unchanged for PDF/original-file access paths
- [x] Replace finalize-failure hard errors on successful user-facing results with soft-warning behavior:
  - successful business payload remains successful
  - response metadata can expose `quota_counted=false` or equivalent warning marker
  - logs/metrics must record the finalize failure
- [x] Re-run focused documents/upload/conversation tests and confirm the canonical bucket mapping is stable

### Task 5: Align Admin and User Quota UI to the Canonical Model

**Files:**
- Modify: `frontend-vue/src/views/QuotaManagement.vue`
- Modify: `frontend-vue/src/services/quota.js`
- Modify: `frontend-vue/src/api/quota.js`
- Modify: `frontend-vue/src/views/UserProfile.vue`

- [x] Add the canonical type labels and remove retired presets from the visible admin create/edit surface
- [x] Switch frontend consumers to trust canonicalized backend `get_all_configs` / `get_user_quotas` responses after Tasks 3-4 are complete
- [x] Align the admin quota settings page end-to-end with the canonical contract:
  - create dialog only offers `ask_query` / `file_qa` / `file_view` / `doc_assist`
  - config list does not present legacy quota rows as active editable entries
  - edit and reset actions operate on canonical types returned by backend
- [x] Confirm user quota display reads and renders only canonical types from `public-service`
- [x] Show stable Chinese labels that match the product model:
  - `ask_query`: 普通问答
  - `file_qa`: 文件问答
  - `file_view`: 查看原文
  - `doc_assist`: 文档辅助
- [x] Keep frontend normalization tolerant of legacy data during rollout, but do not offer legacy types in new admin actions
- [x] Run `cd frontend-vue && npm run build` and confirm quota pages still compile

### Task 6: Verification, Rollout, and Review Closure

**Files:**
- Test: `public-service/backend/tests/test_quota_module.py`
- Test: `public-service/backend/tests/test_documents_module.py`
- Test: `public-service/backend/tests/test_uploads_module.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `frontend-vue` build
- Update after implementation: `docs/audit/2026-03-28-retrieval-and-quota-todo.md`

- [x] Run focused public-service quota tests
- [x] Run focused public-service documents/upload/conversation tests
- [x] Run focused gateway QA routing/quota tests
- [x] Run `cd frontend-vue && npm run build`
- [ ] Manual smoke-check after backend restart:
  - normal fast ask counts `ask_query`
  - normal thinking ask counts `ask_query`
  - PDF/table/hybrid ask counts `file_qa`
  - view original file/PDF counts `file_view`
  - translate/summarize/reference preview/extract text count `doc_assist`
  - admin and super user remain exempt
  - successful responses still return success when quota finalize fails, while exposing soft warning markers and logs
  - `patent` ask path remains outside this rollout and does not silently consume one of the four canonical buckets
- [ ] Update the quota todo/audit doc with rollout status, known gaps, and any deferred migration cleanup

---

## Review Checklist for the Implementer

- [ ] No quota exemption branch depends on `role`
- [ ] `gateway` is the only ask-chain component classifying `ask_query` vs `file_qa`
- [ ] `public-service` is still the only authority storing quota usage/configs
- [ ] Streaming ask is counted only on successful `done`
- [ ] Failed sync/stream requests do not increment quota
- [ ] Legacy quota names still resolve safely during rollout
- [ ] Retired quota types are not exposed in the active admin/user UI
- [ ] Existing `file_view` behavior does not regress

---

## Review Record

### Pass 1: Plan Self-Review

Issues checked:

1. Cross-service `precheck`/`finalize` feasibility
2. Stream-success-only charging boundary
3. Legacy quota-type compatibility during rollout
4. Upload quota retirement scope
5. Admin/user UI consistency with backend contract
6. Authenticated-vs-anonymous `doc_assist` behavior
7. Legacy upload quota transition
8. Internal endpoint exposure tests
9. `doc_assist` strictness consistency
10. Backend-authoritative canonical config listing
11. Backend-authoritative canonical reset behavior
12. `patent` route exclusion from current rollout
13. finalize-failure soft-warning behavior

Result:

- Cross-service quota cannot safely reuse the current in-process `QuotaGrant` object, so the plan explicitly introduces a tokenized internal grant contract in `public-service`.
- Stream charging is attached to the gateway stream observer, not request entry.
- Legacy type aliases are preserved during rollout so old quota rows and old callers do not immediately break.
- Upload quota is treated as retired from the user-visible model, but legacy `file_upload` / `excel_upload` data remains explicitly in compatibility scope until cleanup.
- `doc_assist` is defined as authenticated-user quota only; anonymous compatibility routes are intentionally left unmetered in this rollout.
- Authenticated `doc_assist` calls are locked to one strict policy instead of mixed fail-open/fail-closed behavior.
- Canonical 4-bucket visibility is backend-authoritative, so active admin/user APIs should not keep returning raw mixed legacy/canonical config rows.
- The same backend-authoritative canonical contract also covers admin reset, so the active reset path must resolve canonical quota types rather than relying on raw legacy names.
- `patent` is now explicitly excluded from this rollout so there is no silent undocumented bucket mapping.
- finalize/increment failure for already-successful user-facing results is explicitly planned as soft warning behavior with logs/metrics, rather than being left to current hard-fail code paths.
- The visibility switch to canonical-only UI happens after backend consumers are migrated, not before.
- Route-surface and proxy-surface tests are part of the plan so internal grant endpoints cannot be accidentally exposed.
- The task list covers backend authority, gateway orchestration, authenticated document-assist behavior, frontend visibility, tests, and manual smoke verification.

Open implementation caution:

- The internal quota-grant endpoints need a clear internal trust boundary. Do not ship them as general user/admin APIs.
