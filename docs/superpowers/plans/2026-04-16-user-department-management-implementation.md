# User Department Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add admin-maintained two-level department dictionaries, user department binding, forced department completion on login, and department-aware admin import/edit flows across `public-service`, `gateway`, and `frontend-vue`.

**Architecture:** Implement a new `departments` module in `public-service` as the source of truth for dictionary data, extend `auth` and `admin_users` to reference department IDs on `users`, expose the new route surface through `gateway`, and update the Vue frontend so `/profile` enforces department completion while the admin dashboard can maintain department dictionaries and assign departments to users. Keep the live `users.role` / `user_type` model intact and apply only additive MySQL schema changes.

**Tech Stack:** FastAPI, PyMySQL/MySQL, gateway public proxy routing, Vue 3 + Vite, existing `fetch`-based frontend services, pytest, frontend build validation

---

## File Map

### Backend: new module and schema changes

- Create: `public-service/backend/app/modules/departments/__init__.py`
- Create: `public-service/backend/app/modules/departments/api.py`
- Create: `public-service/backend/app/modules/departments/schemas.py`
- Create: `public-service/backend/app/modules/departments/service.py`
- Create: `public-service/backend/app/modules/departments/repository.py`
- Create: `public-service/backend/tests/test_departments_module.py`
- Modify: `public-service/backend/app/main.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Modify: `public-service/backend/tests/test_auth_module.py`
- Modify: `public-service/backend/tests/test_admin_users_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`
- Create: `highThinkingQA/server/database/migrations/20260416_01_user_departments.sql`

### Gateway

- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`

### Frontend

- Create: `frontend-vue/src/components/DepartmentSelector.vue`
- Create: `frontend-vue/src/components/DepartmentManagementPanel.vue`
- Create: `frontend-vue/src/services/departments.js`
- Modify: `frontend-vue/src/services/auth.js`
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/router/index.js`
- Modify: `frontend-vue/src/views/Login.vue`
- Modify: `frontend-vue/src/views/UserProfile.vue`
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/components/BatchImportDialog.vue`

## Task 1: Add Department Schema and Repository Foundations

**Files:**
- Create: `highThinkingQA/server/database/migrations/20260416_01_user_departments.sql`
- Create: `public-service/backend/app/modules/departments/repository.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Test: `public-service/backend/tests/test_departments_module.py`

- [ ] **Step 1: Write the failing repository and route-surface tests**

```python
def test_department_repository_reads_primary_and_secondary_rows():
    repo = DepartmentRepository(database=FakeDatabase(...))
    tree = repo.list_department_tree(include_disabled=True)
    assert tree[0]["primary_name"] == "计算机学院"
    assert tree[0]["secondary_items"][0]["name"] == "软件工程系"


def test_auth_repository_select_user_fields_include_department_columns_when_present():
    repo = AuthRepository(database=FakeDatabase(...))
    repo._columns_cache = {
        "id", "username", "password_hash", "role", "user_type", "status",
        "is_first_login", "must_set_security_questions",
        "primary_department_id", "secondary_department_id",
        "created_at", "updated_at",
    }
    fields = repo._select_user_fields(include_password=True)
    assert "primary_department_id" in fields
    assert "secondary_department_id" in fields


def test_department_schema_helpers_expect_unique_and_fk_structure():
    ddl = load_migration_sql("20260416_01_user_departments.sql")
    assert "UNIQUE" in ddl
    assert "FOREIGN KEY" in ddl
    assert "primary_department_id" in ddl
    assert "secondary_department_id" in ddl
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest public-service/backend/tests/test_departments_module.py -v`
Expected: FAIL because `DepartmentRepository` and department-aware auth repository behavior do not exist yet

- [ ] **Step 3: Add additive MySQL migration and repository scaffolding**

```sql
ALTER TABLE users
  ADD COLUMN primary_department_id BIGINT NULL,
  ADD COLUMN secondary_department_id BIGINT NULL;

CREATE TABLE primary_departments (...);
CREATE TABLE secondary_departments (...);
```

```python
class DepartmentRepository:
    def list_department_tree(self, *, include_disabled: bool) -> list[dict[str, Any]]:
        ...
```

- [ ] **Step 4: Extend `AuthRepository` field selection and user row helpers**

```python
if self.has_column("primary_department_id"):
    fields.append("primary_department_id")
if self.has_column("secondary_department_id"):
    fields.append("secondary_department_id")
```

- [ ] **Step 5: Run repository tests again**

Run: `pytest public-service/backend/tests/test_departments_module.py -v`
Expected: PASS for the new repository-level expectations

- [ ] **Step 6: Commit**

```bash
git add highThinkingQA/server/database/migrations/20260416_01_user_departments.sql public-service/backend/app/modules/departments/repository.py public-service/backend/app/modules/auth/repository.py public-service/backend/tests/test_departments_module.py
git commit -m "feat: add department schema foundations"
```

## Task 2: Build Public-Service Departments Module

**Files:**
- Create: `public-service/backend/app/modules/departments/__init__.py`
- Create: `public-service/backend/app/modules/departments/api.py`
- Create: `public-service/backend/app/modules/departments/schemas.py`
- Create: `public-service/backend/app/modules/departments/service.py`
- Modify: `public-service/backend/app/main.py`
- Modify: `public-service/backend/tests/test_departments_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: Write failing FastAPI contract tests for admin department routes**

```python
def test_department_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/admin/departments/tree" in paths
    assert "/api/admin/departments/primary" in paths
    assert "/api/admin/departments/secondary/{secondary_id}/status" in paths
```

```python
def test_admin_department_tree_contract(monkeypatch):
    monkeypatch.setattr(department_service, "get_admin_tree", lambda: {"success": True, "data": {"items": []}})
    response = client.get("/api/admin/departments/tree", headers=admin_headers())
    assert response.status_code == 200
```

```python
def test_admin_department_mutation_contracts(monkeypatch):
    monkeypatch.setattr(department_service, "create_primary", lambda **kwargs: {"success": True, "data": kwargs})
    monkeypatch.setattr(department_service, "rename_primary", lambda **kwargs: {"success": True, "data": kwargs})
    monkeypatch.setattr(department_service, "update_primary_status", lambda **kwargs: {"success": True, "data": kwargs})
    monkeypatch.setattr(department_service, "create_secondary", lambda **kwargs: {"success": True, "data": kwargs})
    monkeypatch.setattr(department_service, "rename_secondary", lambda **kwargs: {"success": True, "data": kwargs})
    monkeypatch.setattr(department_service, "update_secondary_status", lambda **kwargs: {"success": True, "data": kwargs})
    assert client.post("/api/admin/departments/primary", json={"name": "计算机学院"}, headers=admin_headers()).status_code == 201
    assert client.put("/api/admin/departments/primary/1", json={"name": "信息学院"}, headers=admin_headers()).status_code == 200
    assert client.put("/api/admin/departments/primary/1/status", json={"status": "disabled"}, headers=admin_headers()).status_code == 200
    assert client.post("/api/admin/departments/secondary", json={"primary_department_id": 1, "name": "软件工程系"}, headers=admin_headers()).status_code == 201
    assert client.put("/api/admin/departments/secondary/11", json={"name": "计算机系"}, headers=admin_headers()).status_code == 200
    assert client.put("/api/admin/departments/secondary/11/status", json={"status": "disabled"}, headers=admin_headers()).status_code == 200
```

```python
def test_department_effective_status_follows_disabled_primary(monkeypatch):
    monkeypatch.setattr(
        department_service,
        "get_admin_tree",
        lambda: {
            "success": True,
            "data": {
                "items": [
                    {
                        "id": 1,
                        "name": "计算机学院",
                        "status": "disabled",
                        "secondary_items": [{"id": 11, "name": "软件工程系", "status": "active", "effective_status": "disabled"}],
                    }
                ]
            },
        },
    )
    response = client.get("/api/admin/departments/tree", headers=admin_headers())
    assert response.json()["data"]["items"][0]["secondary_items"][0]["effective_status"] == "disabled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py -v`
Expected: FAIL because the module is not registered and routes do not exist

- [ ] **Step 3: Implement schemas, service layer, and admin route surface**

```python
router = APIRouter(prefix="/api/admin/departments", tags=["departments"])

@router.get("/tree")
def get_tree(...):
    ...

@router.post("/primary")
def create_primary(...):
    ...
```

- [ ] **Step 4: Register the new router in `public-service` app startup**

```python
from app.modules.departments.api import router as departments_router

DEFAULT_ROUTERS = (
    system_router,
    auth_router,
    admin_users_router,
    departments_router,
    ...
)
```

- [ ] **Step 5: Run the route and contract tests again**

Run: `pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py -v`
Expected: PASS with route surface and handler contracts in place

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/departments public-service/backend/app/main.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py
git commit -m "feat: add department dictionary module"
```

## Task 3: Extend Auth Payloads and Self-Service Department Completion

**Files:**
- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/tests/test_auth_module.py`

- [ ] **Step 1: Write failing auth tests for `require_department_setup` and self-service update**

```python
def test_login_route_exposes_department_flags(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "login", lambda *_: {
        "success": True,
        "data": {
            "token": "t",
            "user": {
                "id": 1,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "primary_department_id": None,
                "secondary_department_id": None,
            },
            "require_department_setup": True,
        },
        "require_department_setup": True,
    })
    response = auth_api_module.login(LoginRequest(username="alice", password="Secret123!"))
    assert _decode(response)["require_department_setup"] is True
```

```python
def test_me_route_exposes_department_fields(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "get_user_info",
        lambda user_id: {
            "success": True,
            "data": {
                "id": user_id,
                "username": "alice",
                "role": "user",
                "user_type": 3,
                "primary_department_id": 1,
                "primary_department_name": "计算机学院",
                "secondary_department_id": 11,
                "secondary_department_name": "软件工程系",
                "require_department_setup": False,
            },
        },
    )
    response = auth_api_module.me(AuthContext(user_id=7, role="user", username="alice"))
    assert _decode(response)["data"]["primary_department_name"] == "计算机学院"
```

```python
def test_auth_department_tree_contract(monkeypatch):
    monkeypatch.setattr(
        auth_service_module.auth_service,
        "get_selectable_department_tree",
        lambda **kwargs: {"success": True, "data": {"items": [{"id": 1, "name": "计算机学院", "secondary_items": []}]}},
    )
    response = auth_api_module.get_department_tree(AuthContext(user_id=9, role="user", username="bob"))
    assert response.status_code == 200
```

```python
def test_auth_department_update_contract(monkeypatch):
    monkeypatch.setattr(auth_service_module.auth_service, "update_department", lambda **kwargs: {"success": True})
    response = auth_api_module.update_department(..., AuthContext(user_id=9, role="user", username="bob"))
    assert response.status_code == 200
```

- [ ] **Step 2: Run auth tests to verify they fail**

Run: `pytest public-service/backend/tests/test_auth_module.py -v`
Expected: FAIL because `require_department_setup` and `PUT /api/auth/department` do not exist

- [ ] **Step 3: Implement department-aware user payload building**

```python
def _build_user_payload(self, user: dict[str, Any]) -> dict[str, Any]:
    ...
    require_department_setup = self._department_setup_required(user)
    return {
        ...,
        "primary_department_id": user.get("primary_department_id"),
        "primary_department_name": user.get("primary_department_name"),
        "secondary_department_id": user.get("secondary_department_id"),
        "secondary_department_name": user.get("secondary_department_name"),
        "require_department_setup": require_department_setup,
    }
```

- [ ] **Step 4: Add `GET /api/auth/departments/tree` and `PUT /api/auth/department`**

```python
@router.get("/api/auth/departments/tree")
def get_selectable_departments(...):
    ...

@router.put("/api/auth/department")
def update_department(...):
    ...
```

- [ ] **Step 5: Run auth tests again**

Run: `pytest public-service/backend/tests/test_auth_module.py -v`
Expected: PASS for new login/me payload and department update contract

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/auth/schemas.py public-service/backend/app/modules/auth/api.py public-service/backend/app/modules/auth/service.py public-service/backend/app/modules/auth/repository.py public-service/backend/tests/test_auth_module.py
git commit -m "feat: add auth department completion flow"
```

## Task 4: Extend Admin User Flows for Department Binding

**Files:**
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/tests/test_admin_users_module.py`

- [ ] **Step 1: Write failing admin-user tests for department-aware create/list/update**

```python
def test_admin_create_user_accepts_department_ids(monkeypatch):
    monkeypatch.setattr(admin_users_service, "create_user", lambda **kwargs: {"success": True, "data": kwargs})
    response = client.post(
        "/api/admin/users",
        json={
            "username": "bob",
            "password": "Pass123!",
            "user_type": "common",
            "primary_department_id": 1,
            "secondary_department_id": 11,
        },
    )
    assert response.status_code == 201
```

```python
def test_admin_update_user_department_contract(monkeypatch):
    monkeypatch.setattr(admin_users_service, "update_department", lambda **kwargs: {"success": True, "data": kwargs})
    response = client.put("/api/admin/users/7/department", json={"primary_department_id": 1, "secondary_department_id": 11})
    assert response.status_code == 200
```

- [ ] **Step 2: Run admin-user tests to verify they fail**

Run: `pytest public-service/backend/tests/test_admin_users_module.py -v`
Expected: FAIL because department-aware admin create/list/update contracts do not exist

- [ ] **Step 3: Extend schemas and API routes**

```python
class UserCreateRequest(BaseModel):
    ...
    primary_department_id: int | None = None
    secondary_department_id: int | None = None

class UserDepartmentUpdateRequest(BaseModel):
    primary_department_id: int | None = None
    secondary_department_id: int | None = None
```

- [ ] **Step 4: Implement service validation and repository updates**

```python
def update_department(self, *, target_user_id: int, primary_department_id: int | None, secondary_department_id: int | None) -> dict[str, Any]:
    ...
```

- [ ] **Step 5: Ensure list users returns department summary text and IDs**

```python
{
    "primary_department_id": ...,
    "primary_department_name": ...,
    "secondary_department_id": ...,
    "secondary_department_name": ...,
}
```

- [ ] **Step 6: Run admin-user tests again**

Run: `pytest public-service/backend/tests/test_admin_users_module.py -v`
Expected: PASS for create/list/update department flows

- [ ] **Step 7: Commit**

```bash
git add public-service/backend/app/modules/admin_users/schemas.py public-service/backend/app/modules/admin_users/api.py public-service/backend/app/modules/admin_users/service.py public-service/backend/app/modules/auth/repository.py public-service/backend/tests/test_admin_users_module.py
git commit -m "feat: add admin user department management"
```

## Task 5: Extend Batch Import Template and Department Validation

**Files:**
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Modify: `public-service/backend/tests/test_admin_users_module.py`

- [ ] **Step 1: Write failing import tests for department-name columns**

```python
def test_admin_import_template_contains_department_columns():
    response = admin_users_import_service.template_response(fmt="csv")
    assert b"primary_department_name" in response.body
    assert b"secondary_department_name" in response.body
```

```python
def test_admin_import_rejects_half_filled_department_columns(monkeypatch):
    csv_bytes = b"username,password,user_type,primary_department_name,secondary_department_name\\nuser1,Pass123!,common,计算机学院,\\n"
    result = admin_users_import_service.import_users(file_bytes=csv_bytes, filename="users.csv", actor_user_id=1)
    assert result["success"] is True
    assert result["data"]["summary"]["failed"] == 1
```

```python
def test_admin_import_rejects_unknown_primary_department(monkeypatch):
    ...
```

```python
def test_admin_import_rejects_unknown_secondary_department(monkeypatch):
    ...
```

```python
def test_admin_import_rejects_invalid_primary_secondary_relation(monkeypatch):
    ...
```

```python
def test_admin_import_rejects_disabled_department(monkeypatch):
    ...
```

- [ ] **Step 2: Run import tests to verify they fail**

Run: `pytest public-service/backend/tests/test_admin_users_module.py -v`
Expected: FAIL because import template and parser do not know department columns

- [ ] **Step 3: Update template output and row validation**

```python
headers = ["username", "password", "user_type", "primary_department_name", "secondary_department_name"]
```

```python
if bool(primary_name) ^ bool(secondary_name):
    details.append({"status": "failed", "reason": "部门信息必须同时填写一级和二级"})
```

- [ ] **Step 4: Implement exact-name dictionary resolution in the import service**

```python
department = department_service.resolve_by_names(
    primary_name=primary_name,
    secondary_name=secondary_name,
    active_only=True,
)
```

- [ ] **Step 5: Run import tests again**

Run: `pytest public-service/backend/tests/test_admin_users_module.py -v`
Expected: PASS for template, validation, and successful department name resolution

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/admin_users/import_service.py public-service/backend/tests/test_admin_users_module.py
git commit -m "feat: add department-aware user import"
```

## Task 6: Expose the New Route Surface Through Gateway

**Files:**
- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`

- [ ] **Step 1: Write failing gateway tests for the new public routes**

```python
def test_route_table_patterns_include_department_routes():
    assert "/api/auth/departments/tree" in PUBLIC_ROUTE_PATTERNS
    assert "/api/auth/department" in PUBLIC_ROUTE_PATTERNS
    assert "/api/admin/departments/tree" in PUBLIC_ROUTE_PATTERNS
    assert "/api/admin/departments/primary" in PUBLIC_ROUTE_PATTERNS
    assert "/api/admin/departments/primary/{primary_id}" in PUBLIC_ROUTE_PATTERNS
    assert "/api/admin/departments/primary/{primary_id}/status" in PUBLIC_ROUTE_PATTERNS
    assert "/api/admin/departments/secondary" in PUBLIC_ROUTE_PATTERNS
    assert "/api/admin/departments/secondary/{secondary_id}" in PUBLIC_ROUTE_PATTERNS
    assert "/api/admin/departments/secondary/{secondary_id}/status" in PUBLIC_ROUTE_PATTERNS
```

```python
@pytest.mark.parametrize(
    ("method", "path", "expected_path"),
    [
        ("GET", "/api/auth/departments/tree", "/api/auth/departments/tree"),
        ("PUT", "/api/auth/department", "/api/auth/department"),
        ("GET", "/api/admin/departments/tree", "/api/admin/departments/tree"),
        ("POST", "/api/admin/departments/primary", "/api/admin/departments/primary"),
        ("PUT", "/api/admin/departments/primary/1", "/api/admin/departments/primary/1"),
        ("PUT", "/api/admin/departments/primary/1/status", "/api/admin/departments/primary/1/status"),
        ("POST", "/api/admin/departments/secondary", "/api/admin/departments/secondary"),
        ("PUT", "/api/admin/departments/secondary/11", "/api/admin/departments/secondary/11"),
        ("PUT", "/api/admin/departments/secondary/11/status", "/api/admin/departments/secondary/11/status"),
    ],
)
def test_public_proxy_forwards_department_routes(method, path, expected_path):
    ...
```

- [ ] **Step 2: Run gateway tests to verify they fail**

Run: `pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v`
Expected: FAIL because the new department routes are not proxied yet

- [ ] **Step 3: Add department routes to route ownership and proxy specs**

```python
_paths("/api/auth/departments/tree"),
_paths("/api/auth/department"),
_paths("/api/admin/departments/tree", include_v1=False),
...
```

- [ ] **Step 4: Run the gateway tests again**

Run: `pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v`
Expected: PASS with the new route surface proxied to `public-service`

- [ ] **Step 5: Commit**

```bash
git add gateway/app/routers/public_proxy.py gateway/app/services/route_table.py gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py
git commit -m "feat: proxy department management routes"
```

## Task 7: Add Frontend Department Service and Forced Profile Flow

**Files:**
- Create: `frontend-vue/src/services/departments.js`
- Create: `frontend-vue/src/components/DepartmentSelector.vue`
- Modify: `frontend-vue/src/services/auth.js`
- Modify: `frontend-vue/src/router/index.js`
- Modify: `frontend-vue/src/views/Login.vue`
- Modify: `frontend-vue/src/views/UserProfile.vue`

- [ ] **Step 1: Add or update frontend structure tests for the new forced flow**

```javascript
test('router redirects users with require_department_setup to profile', () => {
  assert.match(source, /require_department_setup/)
  assert.match(source, /next\\('\\/profile\\?/)
})
```

```javascript
test('UserProfile renders department completion card', () => {
  assert.match(source, /部门信息/)
  assert.match(source, /DepartmentSelector/)
})
```

- [ ] **Step 2: Run the targeted frontend tests or source assertions**

Run: `npm test -- --runInBand`
Expected: FAIL, or if no dedicated runner exists yet, targeted source assertions should fail in the added tests

- [ ] **Step 3: Add department service and reusable selector**

```javascript
export const departmentApi = {
  async getSelectableTree() { ... },
  async updateMyDepartment(primaryDepartmentId, secondaryDepartmentId) { ... },
}
```

- [ ] **Step 4: Extend login/session persistence and route guards**

```javascript
require_department_setup: Boolean(result.require_department_setup || result.data?.require_department_setup)
```

- [ ] **Step 5: Add department card to `/profile` and enforce completion**

```vue
<DepartmentSelector
  :tree="departmentTree"
  :primary-id="selectedPrimaryDepartmentId"
  :secondary-id="selectedSecondaryDepartmentId"
  ...
/>
```

- [ ] **Step 6: Run frontend build verification**

Run: `cd frontend-vue && npm run build`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend-vue/src/services/departments.js frontend-vue/src/components/DepartmentSelector.vue frontend-vue/src/services/auth.js frontend-vue/src/router/index.js frontend-vue/src/views/Login.vue frontend-vue/src/views/UserProfile.vue
git commit -m "feat: enforce department completion in profile"
```

## Task 8: Extend Admin Dashboard for Department Management and User Assignment

**Files:**
- Create: `frontend-vue/src/components/DepartmentManagementPanel.vue`
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/components/BatchImportDialog.vue`
- Modify: `frontend-vue/src/components/DepartmentSelector.vue`

- [ ] **Step 1: Add failing source assertions or UI tests for admin department controls**

```javascript
test('AdminDashboard includes department management entry and user department column', () => {
  assert.match(source, /部门管理/)
  assert.match(source, /<th>部门<\\/th>/)
})
```

```javascript
test('BatchImportDialog documents department columns', () => {
  assert.match(source, /primary_department_name/)
  assert.match(source, /secondary_department_name/)
})
```

```javascript
test('AdminDashboard shows disabled bound departments with status label', () => {
  assert.match(source, /已停用/)
})
```

- [ ] **Step 2: Run the admin dashboard tests**

Run: `npm test -- --runInBand`
Expected: FAIL, or source assertions fail if that test strategy is used

- [ ] **Step 3: Extend admin service layer for department endpoints and user department update**

```javascript
async updateUserDepartment(userId, payload) { ... }
async getDepartmentTree() { ... }
async createPrimaryDepartment(name) { ... }
```

- [ ] **Step 4: Add admin dashboard page sections**

```vue
<DepartmentManagementPanel
  :department-tree="departmentTree"
  ...
/>
```

- [ ] **Step 5: Extend create-user, user-list, and batch-import UI**

```vue
<DepartmentSelector v-model:primary-id="newPrimaryDepartmentId" v-model:secondary-id="newSecondaryDepartmentId" />
```

- [ ] **Step 6: Run frontend build verification again**

Run: `cd frontend-vue && npm run build`
Expected: PASS with admin department UI integrated

- [ ] **Step 7: Commit**

```bash
git add frontend-vue/src/components/DepartmentManagementPanel.vue frontend-vue/src/services/admin.js frontend-vue/src/views/AdminDashboard.vue frontend-vue/src/components/BatchImportDialog.vue frontend-vue/src/components/DepartmentSelector.vue
git commit -m "feat: add admin department dictionary management"
```

## Task 9: Final Integrated Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-04-16-user-department-management-design.md` (only if implementation review reveals spec drift)
- Modify: `docs/superpowers/plans/2026-04-16-user-department-management-implementation.md` (check off completed steps during execution)

- [ ] **Step 1: Run the public-service backend test subset**

Run: `pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_route_surface.py -v`
Expected: PASS

- [ ] **Step 2: Run the gateway test subset**

Run: `pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v`
Expected: PASS

- [ ] **Step 3: Run the frontend production validation**

Run: `cd frontend-vue && npm run build`
Expected: PASS

- [ ] **Step 4: Smoke-check the migration SQL against the live schema assumptions**

Run: `mysql ... -e "SHOW CREATE TABLE users\\G"`
Run:

```bash
MYSQL_HOST=$(grep -hE '^MYSQL_HOST=' public-service/config.shared.env public-service/config.secret.env | tail -n1 | cut -d= -f2-) \
MYSQL_PORT=$(grep -hE '^MYSQL_PORT=' public-service/config.shared.env public-service/config.secret.env | tail -n1 | cut -d= -f2-) \
MYSQL_USER=$(grep -hE '^MYSQL_USER=' public-service/config.secret.env | tail -n1 | cut -d= -f2-) \
MYSQL_PASSWORD=$(grep -hE '^MYSQL_PASSWORD=' public-service/config.secret.env | tail -n1 | cut -d= -f2-) \
MYSQL_DATABASE=$(grep -hE '^MYSQL_DATABASE=' public-service/config.shared.env public-service/config.secret.env | tail -n1 | cut -d= -f2-) \
MYSQL_PWD="$MYSQL_PASSWORD" mysql -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" "$MYSQL_DATABASE" -e "SHOW CREATE TABLE users\\G"
```

Expected: Confirms the migration still matches the live additive schema assumptions documented in the spec

- [ ] **Step 5: Summarize rollout dependencies**

Run: `git diff --stat`
Expected: Shows backend, gateway, frontend, migration, and tests all included

- [ ] **Step 6: Commit**

```bash
git add public-service backend gateway frontend-vue docs/superpowers/plans/2026-04-16-user-department-management-implementation.md
git commit -m "feat: add user department management"
```
