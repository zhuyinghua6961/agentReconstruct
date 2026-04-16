# Department Batch Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add admin-only department dictionary batch import and template download, supporting `xlsx/csv`, updating existing department statuses by name while leaving departments absent from the import file unchanged.

**Architecture:** Add a dedicated `departments.import_service` in `public-service` for spreadsheet parsing, template generation, and row-by-row upsert into `primary_departments` / `secondary_departments`. Reuse the existing admin route and Vue admin panel patterns by adding `/api/admin/departments/batch-import` and `/api/admin/departments/import-template`, plus a focused import dialog under the existing `DepartmentManagementPanel`.

**Tech Stack:** FastAPI, existing departments repository/service, Python CSV + OOXML parsing, Vue 3 + Vite, existing `fetch`-based `adminApi`, pytest, `node:test`, Vite build validation

---

## Scope Note

This plan extends the existing department design at `docs/superpowers/specs/2026-04-16-user-department-management-design.md` with the approved addendum from this session:

1. Department batch import lives under the admin department-management entry.
2. Import supports both `xlsx` and `csv`.
3. Template download is required.
4. Same-name departments are updated in place.
5. Departments absent from the import file remain unchanged.
6. Template includes `primary_status` and `secondary_status`.

## Accepted Behavior

These rules are implementation requirements for this plan:

1. Template columns are exactly:
   - `primary_department_name`
   - `primary_status`
   - `secondary_department_name`
   - `secondary_status`
2. Each row represents one concrete primary/secondary pair.
3. All four columns are required per non-header row. Empty primary, empty secondary, or empty status fields fail that row.
4. Status values are limited to `active` and `disabled`.
5. Repeated rows for the same primary are allowed when they define different secondary departments, but `primary_status` must stay consistent across those rows within the same file.
6. An existing primary with the same name must have its stored status updated to the imported `primary_status`.
7. An existing secondary identified by `(primary_department_name, secondary_department_name)` must have its stored status updated to the imported `secondary_status`.
8. A new primary or secondary found in the import file must be created.
9. A primary or secondary that exists in MySQL but does not appear in the import file must remain unchanged.
10. Rename behavior is not part of batch import. A changed department name is treated as a new department; the old department remains because missing rows are preserved by design.
11. Exact duplicate rows in the same file may be marked `skipped`; conflicting duplicates in the same file must be marked `failed`.

## File Map

### Backend

- Create: `public-service/backend/app/core/spreadsheet.py`
  Responsibility: shared CSV/XLSX row loading and XLSX template generation for admin import services.
- Create: `public-service/backend/app/modules/departments/import_service.py`
  Responsibility: department import template generation, row validation, upsert logic, and result summary.
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
  Responsibility: switch existing user import service to the shared spreadsheet helpers without changing user-import behavior.
- Modify: `public-service/backend/app/modules/departments/api.py`
  Responsibility: expose `batch-import` and `import-template` routes under `/api/admin/departments`.
- Modify: `public-service/backend/app/modules/departments/service.py`
  Responsibility: extend status-code mapping so department import errors return useful HTTP statuses.
- Modify: `public-service/backend/app/modules/departments/repository.py`
  Responsibility: add any tiny helper needed by import flow only if existing `get_*_by_name` / `create_*` / `update_*_status` methods are insufficient.
- Create: `public-service/backend/tests/test_spreadsheet_helpers.py`
  Responsibility: lock shared CSV/XLSX parsing and XLSX template generation behavior.
- Modify: `public-service/backend/tests/test_departments_module.py`
  Responsibility: add department import route/template/import-service coverage.
- Modify: `public-service/backend/tests/test_admin_users_module.py`
  Responsibility: keep user-import regression coverage green after spreadsheet helper extraction.

### Gateway

- Modify: `gateway/app/routers/public_proxy.py`
  Responsibility: register the new department import/template routes in the gateway public proxy allowlist.
- Modify: `gateway/app/services/route_table.py`
  Responsibility: add the new department import/template patterns to the public route ownership table.
- Modify: `gateway/tests/test_public_proxy.py`
  Responsibility: verify the new proxy paths are registered and forwarded to `public-service`.
- Modify: `gateway/tests/test_route_table.py`
  Responsibility: lock the new department import/template patterns into the route table expectations.

### Frontend

- Create: `frontend-vue/src/components/DepartmentBatchImportDialog.vue`
  Responsibility: template download, file upload, and department-specific import instructions.
- Create: `frontend-vue/src/components/DepartmentImportResultDialog.vue`
  Responsibility: show row-level department import results with primary/secondary columns and failed-row CSV download.
- Modify: `frontend-vue/src/components/DepartmentManagementPanel.vue`
  Responsibility: add the batch-import entry, wire dialog open/close, refresh department tree after import, and surface result summaries.
- Modify: `frontend-vue/src/services/admin.js`
  Responsibility: add department template download and batch-import API methods; factor tiny shared blob-download helpers only if it reduces duplication without widening scope.
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`
  Responsibility: add structure-level coverage for the new department import entry and API wiring.

## Task 1: Extract Shared Spreadsheet Helpers

**Files:**
- Create: `public-service/backend/app/core/spreadsheet.py`
- Create: `public-service/backend/tests/test_spreadsheet_helpers.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Test: `public-service/backend/tests/test_spreadsheet_helpers.py`
- Regression: `public-service/backend/tests/test_admin_users_module.py`

- [ ] **Step 1: Write the failing spreadsheet helper tests**

```python
from app.core.spreadsheet import build_xlsx, load_rows


def test_load_rows_reads_utf8_sig_csv_headers_and_items():
    rows = load_rows(
        file_bytes=(
            b"\xef\xbb\xbfprimary_department_name,primary_status,secondary_department_name,secondary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active\n"
        ),
        ext="csv",
    )
    assert rows["columns"] == [
        "primary_department_name",
        "primary_status",
        "secondary_department_name",
        "secondary_status",
    ]
    assert rows["items"][0]["primary_department_name"] == "计算机学院"


def test_build_xlsx_and_load_rows_round_trip_headers_and_values():
    payload = build_xlsx(
        headers=["primary_department_name", "primary_status", "secondary_department_name", "secondary_status"],
        rows=[["计算机学院", "active", "软件工程系", "disabled"]],
        sheet_name="部门导入",
    )
    rows = load_rows(file_bytes=payload, ext="xlsx")
    assert rows["items"][0]["secondary_status"] == "disabled"
```

- [ ] **Step 2: Run helper tests to verify they fail**

Run from `public-service/backend`:

```bash
conda run -n agent pytest tests/test_spreadsheet_helpers.py -v
```

Expected: FAIL because `app.core.spreadsheet` does not exist yet.

- [ ] **Step 3: Implement shared spreadsheet helpers**

```python
def load_rows(*, file_bytes: bytes, ext: str) -> dict[str, Any]:
    if ext == "csv":
        return _load_csv_rows(file_bytes)
    if ext == "xlsx":
        return _load_xlsx_rows(file_bytes)
    raise ValueError("unsupported extension")


def build_xlsx(*, headers: list[str], rows: list[list[str]], sheet_name: str) -> bytes:
    ...
```

Implementation notes:

1. Move the existing CSV decode, CSV row parsing, XLSX ZIP/XML parsing, and workbook generation logic out of `admin_users/import_service.py`.
2. Keep helper API format identical to the current user-import expectations:
   - `{"columns": [...], "items": [{...}, ...]}`
3. Do not change the semantics of existing user import parsing in this task.

- [ ] **Step 4: Switch the user import service to the shared helpers**

```python
from app.core.spreadsheet import build_xlsx, load_rows


rows = load_rows(file_bytes=file_bytes, ext=ext)
content = build_xlsx(headers=headers, rows=rows, sheet_name="用户导入")
```

Implementation notes:

1. Keep `AdminUsersImportService.import_users()` response shape unchanged.
2. Keep `template_response()` filenames and sample rows unchanged.

- [ ] **Step 5: Run helper tests and user-import regression tests**

Run from `public-service/backend`:

```bash
conda run -n agent pytest tests/test_spreadsheet_helpers.py tests/test_admin_users_module.py -k "import or template" -v
```

Expected: PASS, proving the helper extraction did not break existing user import/template behavior.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/core/spreadsheet.py public-service/backend/app/modules/admin_users/import_service.py public-service/backend/tests/test_spreadsheet_helpers.py public-service/backend/tests/test_admin_users_module.py
git commit -m "refactor: share spreadsheet import helpers"
```

## Task 2: Add Department Batch Import Backend

**Files:**
- Create: `public-service/backend/app/modules/departments/import_service.py`
- Modify: `public-service/backend/app/modules/departments/api.py`
- Modify: `public-service/backend/app/modules/departments/service.py`
- Modify: `public-service/backend/app/modules/departments/repository.py`
- Modify: `public-service/backend/tests/test_departments_module.py`
- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`

- [ ] **Step 1: Write the failing route and import-service tests**

```python
def test_department_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/admin/departments/batch-import" in paths
    assert "/api/admin/departments/import-template" in paths
```

```python
def test_route_table_patterns_include_department_import_routes():
    expected = {
        "/api/admin/departments/batch-import",
        "/api/admin/departments/import-template",
    }
    assert expected.issubset(set(PUBLIC_ROUTE_PATTERNS))
```

```python
def test_department_import_template_contains_status_columns():
    response = department_import_service.template_response(fmt="csv")
    assert b"primary_status" in response.body
    assert b"secondary_status" in response.body
```

```python
def test_department_batch_import_route_contract(monkeypatch):
    monkeypatch.setattr(
        department_import_service,
        "import_departments",
        lambda **kwargs: {"success": True, "message": "导入完成", "data": kwargs},
    )
    request = _FakeRequest(
        body=(
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="departments.csv"\r\n'
            b"Content-Type: text/csv\r\n\r\n"
            b"primary_department_name,primary_status,secondary_department_name,secondary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active\n\r\n"
            b"--boundary--\r\n"
        ),
        content_type="multipart/form-data; boundary=boundary",
    )
    response = asyncio.run(
        department_api_module.batch_import_departments(
            request,
            AuthContext(user_id=1, role="admin", username="admin"),
        )
    )
    assert response.status_code == 200
```

```python
def test_department_import_updates_existing_statuses_and_preserves_omitted_rows():
    repo = FakeRepository(
        primary={
            "计算机学院": {"id": 1, "name": "计算机学院", "status": "disabled"},
            "化学学院": {"id": 2, "name": "化学学院", "status": "active"},
        },
        secondary={
            (1, "软件工程系"): {"id": 11, "primary_department_id": 1, "name": "软件工程系", "status": "disabled"},
            (2, "材料系"): {"id": 21, "primary_department_id": 2, "name": "材料系", "status": "active"},
        },
    )
    service = DepartmentImportService(repository=repo)
    result = service.import_departments(
        file_bytes=(
            b"primary_department_name,primary_status,secondary_department_name,secondary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active\n"
        ),
        filename="departments.csv",
    )
    assert result["success"] is True
    assert repo.primary["计算机学院"]["status"] == "active"
    assert repo.secondary[(2, "材料系")]["status"] == "active"  # omitted row remains unchanged
```

```python
def test_department_import_rejects_conflicting_primary_status_in_same_file():
    service = DepartmentImportService(repository=FakeRepository())
    result = service.import_departments(
        file_bytes=(
            b"primary_department_name,primary_status,secondary_department_name,secondary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,disabled,\xe4\xba\xba\xe5\xb7\xa5\xe6\x99\xba\xe8\x83\xbd\xe7\xb3\xbb,active\n"
        ),
        filename="departments.csv",
    )
    assert result["data"]["summary"]["failed"] == 1
```

- [ ] **Step 2: Run the new department tests to verify they fail**

Run from `public-service/backend`:

```bash
conda run -n agent pytest tests/test_departments_module.py -v
```

Expected: FAIL because the import service and new routes do not exist yet.

- [ ] **Step 3: Implement `DepartmentImportService`**

```python
class DepartmentImportService:
    REQUIRED_HEADERS = {
        "primary_department_name",
        "primary_status",
        "secondary_department_name",
        "secondary_status",
    }

    def import_departments(self, *, file_bytes: bytes, filename: str) -> dict[str, Any]:
        ...

    def template_response(self, *, fmt: str) -> Response | dict[str, Any]:
        ...
```

Implementation notes:

1. Reuse `app.core.spreadsheet.load_rows()` and `build_xlsx()`.
2. Normalize text with `strip()`.
3. Normalize statuses to lowercase and validate against `{"active", "disabled"}`.
4. Use file-local tracking maps so repeated primary names within one file must share the same `primary_status`.
5. Upsert order per row:
   - find/create primary by `primary_department_name`
   - update primary status if needed
   - find/create secondary by `(primary_id, secondary_department_name)`
   - update secondary status if needed
6. Preserve rows absent from the file by doing no delete/disable sweep at the end.
7. Return summary/details in the same top-level shape already used by user batch import:

```python
{
    "success": True,
    "message": "部门批量导入完成",
    "data": {
        "summary": {"total": 3, "success": 2, "failed": 1, "skipped": 0},
        "details": [
            {
                "row": 2,
                "primary_department_name": "计算机学院",
                "secondary_department_name": "软件工程系",
                "status": "success",
                "reason": "",
            }
        ],
        "duration": 0.12,
    },
}
```

- [ ] **Step 4: Expose the new admin routes**

```python
@router.post("/batch-import")
async def batch_import_departments(request: Request, context: AuthContext = Depends(require_admin_context)):
    ...


@router.get("/import-template")
def download_department_import_template(format: str = Query(default="xlsx"), _context: AuthContext = Depends(require_admin_context)):
    ...
```

Implementation notes:

1. Keep the route prefix under the existing departments router: `/api/admin/departments`.
2. Reuse the current multipart extraction logic shape from `admin_users/api.py`; copy the helper locally if that is the lowest-risk change for this feature.
3. Backend download filenames should be:
   - `department_import_template.csv`
   - `department_import_template.xlsx`

- [ ] **Step 5: Register the new routes in gateway**

```python
(_paths("/api/admin/departments/batch-import", include_v1=False), ("POST",)),
(_paths("/api/admin/departments/import-template", include_v1=False), ("GET",)),
```

Implementation notes:

1. Update both `gateway/app/routers/public_proxy.py` and `gateway/app/services/route_table.py`.
2. Add or extend tests so the gateway route table and registered router paths both include the new department import/template routes.
3. This step is required for the live stack because runtime traffic still flows through `gateway`.

- [ ] **Step 6: Extend department status-code mapping**

```python
if code in {
    "VALIDATION_ERROR",
    "INVALID_FILE_TYPE",
    "INVALID_FORMAT",
    "FILE_MISSING",
    "PRIMARY_DEPARTMENT_NAME_REQUIRED",
    "SECONDARY_DEPARTMENT_NAME_REQUIRED",
    "INVALID_PRIMARY_STATUS",
    "INVALID_SECONDARY_STATUS",
}:
    return 400
```

Implementation notes:

1. Keep import validation failures as `400`.
2. Keep repository or database failures mapped to `503` / `500` as today.

- [ ] **Step 7: Run focused backend and gateway tests**

Run from `public-service/backend`:

```bash
conda run -n agent pytest tests/test_departments_module.py tests/test_admin_users_module.py -k "department or import or template" -v
```

Run from `gateway`:

```bash
conda run -n agent pytest tests/test_public_proxy.py tests/test_route_table.py -k "department" -v
```

Expected: PASS, including regression that user-import template/download behavior still works and the new department import/template routes are reachable through gateway.

- [ ] **Step 8: Commit**

```bash
git add public-service/backend/app/modules/departments/import_service.py public-service/backend/app/modules/departments/api.py public-service/backend/app/modules/departments/service.py public-service/backend/app/modules/departments/repository.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_admin_users_module.py gateway/app/routers/public_proxy.py gateway/app/services/route_table.py gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py
git commit -m "feat: add department batch import backend"
```

## Task 3: Add Frontend Department Import APIs and Dialogs

**Files:**
- Create: `frontend-vue/src/components/DepartmentBatchImportDialog.vue`
- Create: `frontend-vue/src/components/DepartmentImportResultDialog.vue`
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

- [ ] **Step 1: Write the failing frontend structure tests**

```javascript
test('admin service exposes department import APIs', () => {
  assert.match(adminServiceSource, /batchImportDepartments/)
  assert.match(adminServiceSource, /downloadDepartmentImportTemplate/)
})

test('DepartmentBatchImportDialog documents status columns', () => {
  assert.match(dialogSource, /primary_status/)
  assert.match(dialogSource, /secondary_status/)
  assert.match(dialogSource, /active/)
  assert.match(dialogSource, /disabled/)
})
```

- [ ] **Step 2: Run the frontend structure tests to verify they fail**

Run from `frontend-vue`:

```bash
conda run -n agent npm test -- src/views/AdminDashboard.department-management.test.js
```

Expected: FAIL because the new department import symbols and UI copy do not exist yet.

- [ ] **Step 3: Add department import methods to `admin.js`**

```javascript
async function uploadFile(url, file, token) {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  })
  return safeJson(response)
}

export const adminApi = {
  async batchImportDepartments(file) {
    const token = readStoredToken()
    return uploadFile(`${API_BASE}/departments/batch-import`, file, token)
  },

  async downloadDepartmentImportTemplate(format = 'xlsx') {
    ...
  },
}
```

Implementation notes:

1. Keep user import methods working exactly as before.
2. If you factor a shared blob-download helper, update user template download to use it too; otherwise keep the change local.

- [ ] **Step 4: Create the department import dialog**

```vue
<DepartmentBatchImportDialog
  :show="showDepartmentImportDialog"
  @close="showDepartmentImportDialog = false"
  @import-success="handleDepartmentImportSuccess"
/>
```

Dialog requirements:

1. Match the current `BatchImportDialog.vue` flow:
   - download template
   - choose/drop file
   - start import
2. Use department-specific copy:
   - headers reference `primary_department_name`, `primary_status`, `secondary_department_name`, `secondary_status`
   - explain that same-name departments update status
   - explain that missing rows are not deleted or disabled
3. Accept only `.xlsx,.csv`.

- [ ] **Step 5: Create the department import result dialog**

```vue
<table>
  <thead>
    <tr>
      <th>行号</th>
      <th>一级部门</th>
      <th>一级状态</th>
      <th>二级部门</th>
      <th>二级状态</th>
      <th>结果</th>
      <th>消息</th>
    </tr>
  </thead>
</table>
```

Implementation notes:

1. Keep the summary cards and status filters similar to `ImportResultDialog.vue`.
2. Failed-row CSV download must include department-specific columns rather than `username/user_id`.
3. Do not genericize the existing `ImportResultDialog.vue` in this feature unless the dedicated dialog starts causing duplication that blocks readability.

- [ ] **Step 6: Run the updated frontend structure tests**

Run from `frontend-vue`:

```bash
conda run -n agent npm test -- src/views/AdminDashboard.department-management.test.js
```

Expected: PASS with service methods and dialog wiring visible in source.

- [ ] **Step 7: Commit**

```bash
git add frontend-vue/src/components/DepartmentBatchImportDialog.vue frontend-vue/src/components/DepartmentImportResultDialog.vue frontend-vue/src/services/admin.js frontend-vue/src/views/AdminDashboard.department-management.test.js
git commit -m "feat: add department import dialogs and api client"
```

## Task 4: Wire Department Import into the Existing Department Panel

**Files:**
- Modify: `frontend-vue/src/components/DepartmentManagementPanel.vue`
- Test: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

- [ ] **Step 1: Add a failing structure test for refresh/result wiring**

```javascript
test('DepartmentManagementPanel refreshes the dictionary after department import success', () => {
  assert.match(panelSource, /handleDepartmentImportSuccess/)
  assert.match(panelSource, /await fetchDepartmentTree\(\)/)
  assert.match(panelSource, /DepartmentImportResultDialog/)
})

test('DepartmentManagementPanel shows department batch import entry', () => {
  assert.match(panelSource, /批量导入部门/)
  assert.match(panelSource, /DepartmentBatchImportDialog/)
})
```

- [ ] **Step 2: Run the structure test to verify it fails**

Run from `frontend-vue`:

```bash
conda run -n agent npm test -- src/views/AdminDashboard.department-management.test.js
```

Expected: FAIL because the panel does not yet know about department import state/result.

- [ ] **Step 3: Add panel state and success flow**

```javascript
const showDepartmentImportDialog = ref(false)
const showDepartmentImportResultDialog = ref(false)
const departmentImportResult = ref(null)

async function handleDepartmentImportSuccess(result) {
  departmentImportResult.value = result
  showDepartmentImportResultDialog.value = true
  setSuccess(`部门导入完成：成功 ${result.summary.success} 条，失败 ${result.summary.failed} 条，跳过 ${result.summary.skipped} 条`)
  await fetchDepartmentTree()
  emit('updated')
}
```

Implementation notes:

1. Keep all department-import behavior inside `DepartmentManagementPanel.vue`; do not push it up into `AdminDashboard.vue`.
2. The existing `updated` emit should still fire so dependent admin-user department selectors can refresh.
3. The refresh path must happen after a successful import so the just-imported dictionary becomes immediately selectable.

- [ ] **Step 4: Add the import entry to the panel header**

```vue
<div class="panel-actions">
  <button class="refresh-btn" @click="fetchDepartmentTree">刷新</button>
  <button class="primary-btn" @click="showDepartmentImportDialog = true">批量导入部门</button>
</div>
```

Implementation notes:

1. Preserve the existing create/rename/status controls.
2. Keep the layout mobile-safe; avoid overflowing action buttons in the header.

- [ ] **Step 5: Run frontend tests and build**

Run from `frontend-vue`:

```bash
conda run -n agent npm test -- src/views/AdminDashboard.department-management.test.js
conda run -n agent npm run build
```

Expected: PASS for the structure tests and a successful Vite production build.

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/components/DepartmentManagementPanel.vue frontend-vue/src/views/AdminDashboard.department-management.test.js
git commit -m "feat: wire department batch import into admin panel"
```

## Task 5: End-to-End Verification

**Files:**
- Verify only: backend and frontend files touched above

- [ ] **Step 1: Run focused backend verification**

Run from `public-service/backend`:

```bash
conda run -n agent pytest tests/test_spreadsheet_helpers.py tests/test_departments_module.py tests/test_admin_users_module.py -v
```

Expected: PASS, including shared spreadsheet helper coverage, department import coverage, and user-import regression coverage.

- [ ] **Step 2: Run focused gateway verification**

Run from `gateway`:

```bash
conda run -n agent pytest tests/test_public_proxy.py tests/test_route_table.py -k "department" -v
```

Expected: PASS.

- [ ] **Step 3: Run focused frontend verification**

Run from `frontend-vue`:

```bash
conda run -n agent npm test -- src/views/AdminDashboard.department-management.test.js
conda run -n agent npm run build
```

Expected: PASS.

- [ ] **Step 4: Restart the backend stack and smoke-test the route surface**

Run from repo root:

```bash
bash scripts/stop_all.sh
bash scripts/start_all.sh
bash scripts/status_all.sh
```

Expected:

1. `public-service :8102` healthy
2. `gateway :8101` healthy
3. Department management page can:
   - download CSV/XLSX template
   - upload a valid file
   - refresh department list immediately after a successful import

- [ ] **Step 5: Manual smoke test checklist**

1. Download `department_import_template.csv`.
2. Confirm columns are `primary_department_name,primary_status,secondary_department_name,secondary_status`.
3. Import a file containing:
   - one brand-new department pair
   - one existing department pair with a status change
4. Verify in MySQL/admin UI that:
   - existing same-name rows changed status
   - new rows were created
   - omitted preexisting rows were untouched
5. Open user create/edit and personal department selector flows to confirm imported departments appear as expected.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/core/spreadsheet.py public-service/backend/app/modules/admin_users/import_service.py public-service/backend/app/modules/departments/import_service.py public-service/backend/app/modules/departments/api.py public-service/backend/app/modules/departments/service.py public-service/backend/app/modules/departments/repository.py public-service/backend/tests/test_spreadsheet_helpers.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_admin_users_module.py gateway/app/routers/public_proxy.py gateway/app/services/route_table.py gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py frontend-vue/src/components/DepartmentBatchImportDialog.vue frontend-vue/src/components/DepartmentImportResultDialog.vue frontend-vue/src/components/DepartmentManagementPanel.vue frontend-vue/src/services/admin.js frontend-vue/src/views/AdminDashboard.department-management.test.js
git commit -m "test: verify department batch import flow"
```

## Notes for the Implementer

1. No new database migration is required for this feature. It consumes the already-added `primary_departments` / `secondary_departments` schema.
2. Keep batch import strictly additive/updating. Do not add deletion, cleanup, or “sync to exact snapshot” behavior.
3. Do not widen scope into department export, rename mapping, or department-code support.
4. If the import-result UX starts pushing too much generic behavior into existing user import dialogs, prefer a dedicated department result dialog over a half-finished abstraction.
