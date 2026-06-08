# Import Template Chinese Columns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the admin batch import templates show Chinese column headers while keeping backend import parsing compatible with both Chinese and legacy English column names.

**Architecture:** Add one small shared column-alias helper in the public-service backend and let the three import services resolve template fields through it. Keep each module responsible for its own business validation and template generation. Update the Vue import dialogs so the user-facing template descriptions match the new Chinese headers.

**Tech Stack:** Python 3.11, FastAPI, pytest, Vue 3, Vite

---

### Task 1: Add shared column alias resolution for import templates

**Files:**
- Create: `public-service/backend/app/core/import_columns.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Modify: `public-service/backend/app/modules/departments/import_service.py`
- Modify: `public-service/backend/app/modules/personnel/import_service.py`

- [ ] **Step 1: Write the failing test**

Add or update backend tests so Chinese template headers are expected, while legacy English column names still parse successfully.

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_personnel_module.py -q`

Expected: failures on template header assertions and any missing alias handling.

- [ ] **Step 3: Write minimal implementation**

Use the shared helper to resolve required and optional column aliases in the three import services. Keep error messages clear and local to each service.

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command.

Expected: all targeted backend tests pass.

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/core/import_columns.py public-service/backend/app/modules/admin_users/import_service.py public-service/backend/app/modules/departments/import_service.py public-service/backend/app/modules/personnel/import_service.py public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_personnel_module.py
git commit -m "fix: localize batch import templates"
```

### Task 2: Update frontend batch import dialogs to show Chinese template columns

**Files:**
- Modify: `frontend-vue/src/components/BatchImportDialog.vue`
- Modify: `frontend-vue/src/components/DepartmentBatchImportDialog.vue`
- Modify: `frontend-vue/src/components/PersonnelBatchImportDialog.vue`
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

- [ ] **Step 1: Write the failing test**

Update the structure test to assert the dialogs now document Chinese column names instead of English keys.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend-vue && npm test -- AdminDashboard.department-management.test.js`

Expected: assertions fail until the dialog copy is updated.

- [ ] **Step 3: Write minimal implementation**

Update the hints and bullet text so the templates describe the Chinese headers and the compatibility note remains accurate.

- [ ] **Step 4: Run test to verify it passes**

Run the same frontend test command.

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/components/BatchImportDialog.vue frontend-vue/src/components/DepartmentBatchImportDialog.vue frontend-vue/src/components/PersonnelBatchImportDialog.vue frontend-vue/src/views/AdminDashboard.department-management.test.js
git commit -m "fix: localize import dialog copy"
```

### Task 3: Verify full affected test surface

**Files:**
- Run tests only

- [ ] **Step 1: Run backend tests**

Run: `conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_personnel_module.py -q`

- [ ] **Step 2: Run frontend build or targeted test**

Run: `cd frontend-vue && npm run build`

- [ ] **Step 3: Confirm results and note any follow-up**

Check that no unrelated files were modified and that the import templates still accept legacy column names.
