# Admin Batch Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable batch delete and batch user-type change for the active admin chain in `public-service`, proxy them through `gateway`, and expose them in the canonical frontend admin dashboard.

**Architecture:** Keep admin ownership in `public-service`. Add two batch endpoints that return partial-success summaries plus detail rows. Extend the frontend admin table with row selection and batch actions, and reuse the existing result dialog shape for operation feedback.

**Tech Stack:** FastAPI, Vue 3, gateway public proxy, pytest, Vite

---

### Task 1: Public-Service Batch Admin API

**Files:**
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Test: `public-service/backend/tests/test_admin_users_module.py`

- [ ] Add failing route-surface and contract tests for `batch-delete` and `batch-type`
- [ ] Run the focused test file and verify the new assertions fail
- [ ] Implement request schemas, service methods, and API routes with partial-success summaries/details
- [ ] Re-run the focused test file and verify it passes

### Task 2: Gateway Public Proxy Surface

**Files:**
- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Test: `gateway/tests/test_public_proxy.py`

- [ ] Add failing proxy tests for the two new admin endpoints
- [ ] Run the focused proxy tests and verify failure
- [ ] Add the new paths to the public proxy route surface
- [ ] Re-run the focused proxy tests and verify pass

### Task 3: Frontend Admin Dashboard Batch Actions

**Files:**
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/components/ImportResultDialog.vue`

- [ ] Add frontend service methods for batch delete and batch type change
- [ ] Add admin table row selection and batch action controls
- [ ] Show partial-success detail results in the existing result dialog shape
- [ ] Run frontend build to verify the admin page still compiles

### Task 4: Verification

**Files:**
- Test: `public-service/backend/tests/test_admin_users_module.py`
- Test: `gateway/tests/test_public_proxy.py`
- Test: `frontend-vue` build

- [ ] Run focused backend admin tests
- [ ] Run focused gateway proxy tests
- [ ] Run `cd frontend-vue && npm run build`
- [ ] Review changed files and summarize behavior/risks
