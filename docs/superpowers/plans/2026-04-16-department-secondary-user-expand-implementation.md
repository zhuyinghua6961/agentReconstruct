# Department Secondary User Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add collapsible secondary department sections in admin department management, showing total bound-user counts while collapsed and lazily loading the full user list for that secondary department when expanded.

**Architecture:** Keep `GET /api/admin/departments/tree` as the structure API and extend each secondary node with a `user_count` aggregate computed in MySQL. Add a separate admin-only endpoint `GET /api/admin/departments/secondary/{secondary_id}/users` that returns all users bound to one secondary department with minimal fields, then update `DepartmentManagementPanel.vue` to cache and render that data per expanded node. No schema migration is needed for this feature because department foreign-key columns already exist from the earlier department rollout.

**Tech Stack:** FastAPI, existing `public-service` departments module, MySQL/PyMySQL, gateway public proxy routing, Vue 3 + Vite, existing `fetch`-based admin service, pytest, Node `--test`, frontend build validation

---

## File Map

### Backend: department tree count and per-secondary user query

- Modify: `public-service/backend/app/modules/departments/repository.py`
  Responsibility: extend the department tree SQL with per-secondary `user_count`, and add a focused query that returns all users for one secondary department without reusing the paginated admin user list path.
- Modify: `public-service/backend/app/modules/departments/service.py`
  Responsibility: map repository rows into stable admin response payloads, add `list_secondary_users()` orchestration, keep admin semantics as “show all bound users” including disabled users and users under disabled departments.
- Modify: `public-service/backend/app/modules/departments/api.py`
  Responsibility: expose `GET /api/admin/departments/secondary/{secondary_id}/users` under the existing admin departments router.
- Modify: `public-service/backend/tests/test_departments_module.py`
  Responsibility: lock repository aggregation behavior, new service/API contract, 404 handling, and response field minimalism.
- Modify: `public-service/backend/tests/test_route_surface.py`
  Responsibility: assert the new route is registered on the public-service app.

### Gateway: route exposure only

- Modify: `gateway/app/routers/public_proxy.py`
  Responsibility: proxy the new admin department-user route to `public-service`.
- Modify: `gateway/app/services/route_table.py`
  Responsibility: add the route pattern to public route ownership.
- Modify: `gateway/tests/test_public_proxy.py`
  Responsibility: verify proxy forwarding preserves path, method, and auth header for the new route.
- Modify: `gateway/tests/test_route_table.py`
  Responsibility: assert the new route is present in `PUBLIC_ROUTE_PATTERNS`.

### Frontend: secondary collapse, lazy load, inline user list

- Modify: `frontend-vue/src/services/admin.js`
  Responsibility: add the admin API method for fetching users by secondary department.
- Modify: `frontend-vue/src/components/DepartmentManagementPanel.vue`
  Responsibility: add secondary expand/collapse state, per-secondary loading/error/cache state, count display, inline user list rendering, and cache reset on tree refresh.
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`
  Responsibility: lock the source-level expectations for the new API usage and UI structure.

## Contract Notes

### 1. Extended admin department tree

`GET /api/admin/departments/tree`

Secondary items gain:

```json
{
  "id": 11,
  "name": "软件工程系",
  "status": "active",
  "effective_status": "active",
  "user_count": 23
}
```

Rules:

- `user_count` counts all users currently bound to `users.secondary_department_id = secondary.id`.
- The count includes disabled user accounts.
- The count is still returned when the primary or secondary department itself is disabled.
- The tree response still does not embed user lists.

### 2. New admin endpoint for one secondary department

`GET /api/admin/departments/secondary/{secondary_id}/users`

Success payload:

```json
{
  "success": true,
  "data": {
    "secondary_department_id": 11,
    "primary_department_id": 1,
    "primary_department_name": "计算机学院",
    "secondary_department_name": "软件工程系",
    "user_count": 2,
    "users": [
      {
        "id": 101,
        "username": "alice",
        "user_type": 3,
        "user_type_label": "普通用户",
        "status": "active"
      },
      {
        "id": 102,
        "username": "bob",
        "user_type": 2,
        "user_type_label": "超级用户",
        "status": "disabled"
      }
    ]
  }
}
```

Rules:

- Admin-only, same auth model as existing admin department routes.
- Return all bound users for that secondary department in one response; no pagination.
- If the secondary department does not exist, return `404` with `SECONDARY_DEPARTMENT_NOT_FOUND`.
- Do not return password hashes, security data, or quota data.

### 3. Frontend behavior

- Primary departments remain collapsible as they are now.
- Each secondary department becomes independently collapsible.
- Collapsed secondary rows show `user_count` such as `23 人`.
- Expanding a secondary row lazily calls the new endpoint on first open only.
- Loaded results are cached by `secondary_id` until `fetchDepartmentTree()` runs again or the component remounts.
- Expanded content shows only `用户名 / 用户类型 / 状态`, plus inline loading, empty, and retryable error states.
- No inline user operations are added in department management.

## Task 1: Extend Department Tree With Secondary User Counts

**Files:**
- Modify: `public-service/backend/app/modules/departments/repository.py`
- Modify: `public-service/backend/app/modules/departments/service.py`
- Test: `public-service/backend/tests/test_departments_module.py`

- [ ] **Step 1: Write the failing repository and service tests for `user_count`**

```python
def test_department_repository_includes_secondary_user_count():
    repo = DepartmentRepository(database=object())
    repo._execute_query = lambda query, params=(): [
        {
            "primary_id": 1,
            "primary_name": "计算机学院",
            "primary_status": "active",
            "secondary_id": 11,
            "secondary_name": "软件工程系",
            "secondary_status": "active",
            "secondary_user_count": 7,
        }
    ]

    tree = repo.list_department_tree(include_disabled=True)

    assert tree[0]["secondary_items"][0]["user_count"] == 7


def test_department_service_admin_tree_maps_secondary_user_count():
    service = DepartmentService(repository=FakeDepartmentRepository(...))
    result = service.get_admin_tree()
    assert result["data"]["items"][0]["secondary_items"][0]["user_count"] == 7
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `conda run -n agent pytest public-service/backend/tests/test_departments_module.py -k "user_count or department_tree" -v`
Expected: FAIL because the repository/service payload currently do not expose `user_count`

- [ ] **Step 3: Add aggregated count support in `DepartmentRepository.list_department_tree()`**

```python
SELECT
    p.id AS primary_id,
    p.name AS primary_name,
    p.status AS primary_status,
    s.id AS secondary_id,
    s.name AS secondary_name,
    s.status AS secondary_status,
    COALESCE(u.user_count, 0) AS secondary_user_count
FROM primary_departments p
LEFT JOIN secondary_departments s ON s.primary_department_id = p.id
LEFT JOIN (
    SELECT secondary_department_id, COUNT(*) AS user_count
    FROM users
    WHERE secondary_department_id IS NOT NULL
    GROUP BY secondary_department_id
) u ON u.secondary_department_id = s.id
```

- [ ] **Step 4: Map `secondary_user_count` to a stable `user_count` response field in `DepartmentService.get_admin_tree()`**

```python
secondary_items.append(
    {
        "id": int(secondary["id"]),
        "name": secondary["name"],
        "status": child_status,
        "effective_status": effective_status,
        "user_count": int(secondary.get("user_count") or 0),
    }
)
```

- [ ] **Step 5: Run the targeted tests again**

Run: `conda run -n agent pytest public-service/backend/tests/test_departments_module.py -k "user_count or department_tree" -v`
Expected: PASS with `user_count` included in repository and service payloads

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/departments/repository.py public-service/backend/app/modules/departments/service.py public-service/backend/tests/test_departments_module.py
git commit -m "feat: add secondary department user counts"
```

## Task 2: Add Admin Endpoint For Full User List By Secondary Department

**Files:**
- Modify: `public-service/backend/app/modules/departments/repository.py`
- Modify: `public-service/backend/app/modules/departments/service.py`
- Modify: `public-service/backend/app/modules/departments/api.py`
- Modify: `public-service/backend/tests/test_departments_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: Write failing repository, service, API, and route-surface tests**

```python
def test_public_route_surface_includes_secondary_department_user_route():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/admin/departments/secondary/{secondary_id}/users" in paths


def test_department_service_lists_all_users_for_secondary_department():
    service = DepartmentService(repository=FakeDepartmentRepository(...))
    result = service.list_secondary_users(secondary_id=11)
    assert result["success"] is True
    assert result["data"]["user_count"] == 2
    assert result["data"]["users"][0]["username"] == "alice"
    assert result["data"]["users"][0]["user_type_label"] == "普通用户"


def test_department_service_returns_404_when_secondary_missing():
    service = DepartmentService(repository=FakeDepartmentRepository(...))
    result = service.list_secondary_users(secondary_id=999)
    assert result["code"] == "SECONDARY_DEPARTMENT_NOT_FOUND"


def test_admin_secondary_users_route_contract(monkeypatch):
    monkeypatch.setattr(
        department_service.department_service,
        "list_secondary_users",
        lambda secondary_id: {"success": True, "data": {"secondary_department_id": secondary_id, "user_count": 0, "users": []}},
    )
    response = department_api_module.get_secondary_users(11, AuthContext(user_id=1, role="admin", username="admin"))
    assert response.status_code == 200
```

- [ ] **Step 2: Run the backend tests to verify they fail**

Run: `conda run -n agent pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py -v`
Expected: FAIL because the repository query, service method, and route do not exist yet

- [ ] **Step 3: Add repository and service support for full user listing**

```python
def list_users_by_secondary_department(self, *, secondary_id: int) -> list[dict[str, Any]]:
    return self._execute_query(
        """
        SELECT id, username, role, user_type, status
        FROM users
        WHERE secondary_department_id = %s
        ORDER BY username ASC, id ASC
        """,
        (int(secondary_id),),
    )
```

```python
def list_secondary_users(self, *, secondary_id: int) -> dict[str, Any]:
    secondary = self._repository.get_secondary_by_id(secondary_id)
    if not secondary:
        return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}
    primary = self._repository.get_primary_by_id(int(secondary["primary_department_id"]))
    rows = self._repository.list_users_by_secondary_department(secondary_id=secondary_id)
    return {"success": True, "data": {...}}
```

- [ ] **Step 4: Expose `GET /api/admin/departments/secondary/{secondary_id}/users` in `api.py`**

```python
@router.get("/secondary/{secondary_id}/users")
def get_secondary_users(
    secondary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(department_service.list_secondary_users(secondary_id=secondary_id), ok_status=200)
```

- [ ] **Step 5: Run the backend tests again**

Run: `conda run -n agent pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py -v`
Expected: PASS with correct contract, 404 behavior, and route registration

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/departments/repository.py public-service/backend/app/modules/departments/service.py public-service/backend/app/modules/departments/api.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py
git commit -m "feat: add secondary department user listing api"
```

## Task 3: Expose The New Route Through Gateway

**Files:**
- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`

- [ ] **Step 1: Write failing gateway tests for route ownership and proxy forwarding**

```python
def test_route_table_patterns_include_secondary_department_user_route():
    assert "/api/admin/departments/secondary/{secondary_id}/users" in set(PUBLIC_ROUTE_PATTERNS)
```

```python
(
    "GET",
    "/api/admin/departments/secondary/11/users",
    "/api/admin/departments/secondary/11/users",
    None,
    b"",
)
```

- [ ] **Step 2: Run the gateway tests to verify they fail**

Run: `conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v`
Expected: FAIL because the new path is not in the route table or proxy route specs

- [ ] **Step 3: Add the route to both gateway route tables**

```python
_paths("/api/admin/departments/secondary/{secondary_id}/users", include_v1=False)
```

- [ ] **Step 4: Run the gateway tests again**

Run: `conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v`
Expected: PASS with the route forwarded to the public backend

- [ ] **Step 5: Commit**

```bash
git add gateway/app/routers/public_proxy.py gateway/app/services/route_table.py gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py
git commit -m "feat: proxy secondary department user routes"
```

## Task 4: Implement Secondary Collapse And Lazy User Rendering In Admin UI

**Files:**
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/components/DepartmentManagementPanel.vue`
- Test: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

- [ ] **Step 1: Write failing frontend source tests for API usage and UI structure**

```javascript
test('admin service exposes secondary department user query api', () => {
  assert.match(adminServiceSource, /getSecondaryDepartmentUsers/)
  assert.match(adminServiceSource, /\\/departments\\/secondary\\/\\$\\{secondaryId\\}\\/users/)
})

test('DepartmentManagementPanel renders collapsible secondary sections with user counts', () => {
  assert.match(panelSource, /expandedSecondaryIds|expandedSecondaryMap/)
  assert.match(panelSource, /toggleSecondary/)
  assert.match(panelSource, /isSecondaryExpanded/)
  assert.match(panelSource, /secondary\\.user_count/)
  assert.match(panelSource, /人/)
})

test('DepartmentManagementPanel lazy loads secondary users with loading and error states', () => {
  assert.match(panelSource, /loadSecondaryUsers/)
  assert.match(panelSource, /secondaryUsersById/)
  assert.match(panelSource, /secondaryUsersLoadingById/)
  assert.match(panelSource, /secondaryUsersErrorById/)
  assert.match(panelSource, /暂无用户/)
  assert.match(panelSource, /重新加载|重试/)
})
```

- [ ] **Step 2: Run the frontend source tests to verify they fail**

Run: `node --test frontend-vue/src/views/AdminDashboard.department-management.test.js`
Expected: FAIL because the admin service and department panel do not yet contain secondary expand/lazy-load behavior

- [ ] **Step 3: Add the new admin API method and component state model**

```javascript
async getSecondaryDepartmentUsers(secondaryId) {
  const token = readStoredToken()
  return fetchWithErrorHandling(`${API_BASE}/departments/secondary/${secondaryId}/users`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
}
```

```javascript
const expandedSecondaryIds = ref([])
const secondaryUsersById = ref({})
const secondaryUsersLoadingById = ref({})
const secondaryUsersErrorById = ref({})
```

- [ ] **Step 4: Implement lazy-loading secondary expand/collapse behavior in `DepartmentManagementPanel.vue`**

```javascript
async function loadSecondaryUsers(secondaryId) {
  if (secondaryUsersById.value[secondaryId]) return
  secondaryUsersLoadingById.value = { ...secondaryUsersLoadingById.value, [secondaryId]: true }
  const result = await adminApi.getSecondaryDepartmentUsers(secondaryId)
  if (result.success) {
    secondaryUsersById.value = { ...secondaryUsersById.value, [secondaryId]: result.data?.users || [] }
    secondaryUsersErrorById.value = { ...secondaryUsersErrorById.value, [secondaryId]: '' }
  } else {
    secondaryUsersErrorById.value = { ...secondaryUsersErrorById.value, [secondaryId]: result.error || '获取用户列表失败' }
  }
  secondaryUsersLoadingById.value = { ...secondaryUsersLoadingById.value, [secondaryId]: false }
}
```

Implementation notes:

- Convert each secondary item header into a clickable summary row with its own collapse toggle.
- Show `secondary.user_count` while collapsed and while expanded.
- Render a simple inline list/table with columns `用户名 / 用户类型 / 状态`.
- Keep action buttons for rename/status on the secondary header, not inside the user list.
- Clear `expandedSecondaryIds`, `secondaryUsersById`, `secondaryUsersLoadingById`, and `secondaryUsersErrorById` whenever `fetchDepartmentTree()` succeeds so refreshed counts and lists cannot remain stale.

- [ ] **Step 5: Run frontend tests and build validation**

Run: `node --test frontend-vue/src/views/AdminDashboard.department-management.test.js`
Expected: PASS

Run: `cd frontend-vue && npm run build`
Expected: PASS with no template/script compile errors

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/services/admin.js frontend-vue/src/components/DepartmentManagementPanel.vue frontend-vue/src/views/AdminDashboard.department-management.test.js
git commit -m "feat: show department users in secondary collapses"
```

## Task 5: Final Cross-Service Verification

**Files:**
- Modify: none
- Test: `public-service/backend/tests/test_departments_module.py`
- Test: `public-service/backend/tests/test_route_surface.py`
- Test: `gateway/tests/test_route_table.py`
- Test: `gateway/tests/test_public_proxy.py`
- Test: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

- [ ] **Step 1: Run the backend department test suite**

Run: `conda run -n agent pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py -v`
Expected: PASS

- [ ] **Step 2: Run the gateway route test suite**

Run: `conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v`
Expected: PASS

- [ ] **Step 3: Run the frontend targeted tests and production build**

Run: `node --test frontend-vue/src/views/AdminDashboard.department-management.test.js`
Expected: PASS

Run: `cd frontend-vue && npm run build`
Expected: PASS

- [ ] **Step 4: Manual smoke checklist**

```text
1. 打开管理员后台 > 部门管理。
2. 展开一个一级部门，确认每个二级部门显示“X 人”。
3. 展开一个二级部门，确认首次请求后出现完整用户列表。
4. 收起再展开同一二级部门，确认不重复请求且列表立即复用缓存。
5. 点击刷新，确认人数和用户列表缓存都被清空并按最新数据重建。
6. 展开一个空部门，确认显示“暂无用户”。
7. 将后端接口模拟为失败，确认仅在展开内容区显示“获取用户列表失败/重试”，而不是污染全局表单错误。
```

- [ ] **Step 5: Commit verification-only changes if any**

```bash
git status
```

Expected: no unintended edits remain beyond the planned implementation files
