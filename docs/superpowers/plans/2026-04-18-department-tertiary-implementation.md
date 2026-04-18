# Tertiary Department Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing two-level department system to a three-level model, requiring tertiary department selection for all new non-empty department writes while preserving legacy two-level users during the transition.

**Architecture:** Extend the existing `public-service` departments module instead of creating a parallel path. Add a new `tertiary_departments` dictionary table plus `users.tertiary_department_id`, move all new department validation to a strict primary-secondary-tertiary chain, and keep legacy users with only primary+secondary temporarily readable and usable. Expose the new route surface through `gateway`, upgrade the Vue selector and admin management panel to primary -> secondary -> tertiary, and move per-department user expansion from secondary nodes to tertiary nodes.

**Tech Stack:** FastAPI, PyMySQL/MySQL, existing `public-service` auth/admin/departments modules, gateway public proxy routing, Vue 3 + Vite, existing `fetch`-based frontend services, pytest, `node --test`, Vite build validation

---

## Scope Note

This plan extends the existing two-level department rollout and related follow-up plans:

- `docs/superpowers/specs/2026-04-16-user-department-management-design.md`
- `docs/superpowers/plans/2026-04-16-user-department-management-implementation.md`
- `docs/superpowers/plans/2026-04-16-department-batch-import-implementation.md`
- `docs/superpowers/plans/2026-04-16-department-secondary-user-expand-implementation.md`

It replaces the current terminal department node from “secondary department” to “tertiary department” for all new writes and all new selectors.

## Approved Behavior

These rules are implementation requirements for this plan:

1. New department bindings must be either fully empty or a valid three-level chain.
2. Legacy users already stored as valid primary+secondary with `tertiary_department_id = NULL` remain usable and are not forced to complete immediately.
3. Admin users are not forced to maintain their own department and must never be blocked by `require_department_setup`.
4. Admin create-user and batch-import flows may still leave all department fields empty.
5. Once a user or admin explicitly writes a non-empty department, the payload must include valid primary, secondary, and tertiary IDs.
6. Department selectors must support name-based search.
7. Secondary departments with no tertiary children remain visible but are not selectable for completion, and the UI must tell the user to contact an administrator.
8. Department management becomes `一级 -> 二级 -> 三级`.
9. User expansion in department management moves to tertiary nodes only.
10. Secondary nodes must show tertiary count and total user count.
11. During the compatibility window, secondary totals must continue counting legacy users who still have no tertiary binding.
12. The user-facing QA main screen is out of scope; only the admin interface and personal-center-related department flows change.

## Rollout Gate

This plan allows additive commits per task, but it does **not** allow arbitrary intermediate deployment.

Required release ordering:

1. Tasks 1-3 are additive schema/API work and may land first.
2. Tasks 4-5 introduce strict three-level write rejection for non-empty department writes.
3. Tasks 7-9 introduce the only supported three-level frontend flows.
4. Therefore, Tasks 4-5 must not be deployed to production unless Tasks 7-9 are deployed in the same release batch.
5. If a staged deploy is unavoidable, keep strict three-level write rejection disabled until the frontend rollout is complete, then flip the release gate in the final rollout window.

## File Map

### Database

- Create: `highThinkingQA/server/database/migrations/20260418_01_department_tertiary.sql`
  Responsibility: add `tertiary_departments`, add `users.tertiary_department_id`, indexes, foreign key, and idempotent guards matching the existing migration style.

### Public-Service Backend

- Modify: `public-service/backend/app/modules/departments/repository.py`
  Responsibility: extend tree queries to tertiary level, aggregate secondary and tertiary counts, add tertiary CRUD helpers, add tertiary user-list query, add the legacy-user query for secondary departments with `tertiary_department_id IS NULL`, and support ID/name resolution for three-level chains.
- Modify: `public-service/backend/app/modules/departments/service.py`
  Responsibility: centralize three-level validation, preserve legacy two-level reads, expose tertiary CRUD/user-list responses, and map tree payloads for admin/selectable/profile use cases.
- Modify: `public-service/backend/app/modules/departments/api.py`
  Responsibility: add tertiary CRUD/status routes and tertiary user-list route; keep existing router ownership intact.
- Modify: `public-service/backend/app/modules/departments/schemas.py`
  Responsibility: add tertiary request/response schemas and three-level selector payload models.
- Modify: `public-service/backend/app/modules/departments/import_service.py`
  Responsibility: extend department dictionary batch import/template from two levels to optional tertiary rows.
- Modify: `public-service/backend/app/modules/auth/repository.py`
  Responsibility: include `tertiary_department_id` in user selects/inserts/updates when the column exists.
- Modify: `public-service/backend/app/modules/auth/service.py`
  Responsibility: compute department completion state with legacy compatibility, expose tertiary fields in login/me responses, and enforce three-level self-service writes for non-admin users.
- Modify: `public-service/backend/app/modules/auth/schemas.py`
  Responsibility: extend auth payloads with `tertiary_department_id` and any new completion-state fields.
- Modify: `public-service/backend/app/modules/auth/api.py`
  Responsibility: accept three-level self-service department updates without changing the route path.
- Modify: `public-service/backend/app/modules/admin_users/service.py`
  Responsibility: validate admin create/update payloads with the new three-level rules and expose tertiary names/IDs in admin user responses.
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
  Responsibility: extend admin create/update department payloads to include `tertiary_department_id`.
- Modify: `public-service/backend/app/modules/admin_users/api.py`
  Responsibility: pass tertiary department IDs through create/update routes.
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
  Responsibility: extend user import parsing/template generation with `tertiary_department_name`.
- Modify: `public-service/backend/tests/test_departments_module.py`
  Responsibility: lock migration-aware repository logic, three-level tree payloads, tertiary CRUD routes, tertiary user-list contracts, and three-level import behavior.
- Modify: `public-service/backend/tests/test_auth_module.py`
  Responsibility: cover legacy compatibility, admin exemption, login/me payload shape, and self-service tertiary enforcement.
- Modify: `public-service/backend/tests/test_admin_users_module.py`
  Responsibility: cover admin create/update/import/template behavior with tertiary departments.
- Modify: `public-service/backend/tests/test_route_surface.py`
  Responsibility: assert the new tertiary routes are registered on the FastAPI app.

### Gateway

- Modify: `gateway/app/routers/public_proxy.py`
  Responsibility: forward the new tertiary CRUD/status/user-list routes and any updated import/template path.
- Modify: `gateway/app/services/route_table.py`
  Responsibility: register the new public-service route patterns.
- Modify: `gateway/tests/test_public_proxy.py`
  Responsibility: verify proxy forwarding for tertiary routes.
- Modify: `gateway/tests/test_route_table.py`
  Responsibility: lock the new route patterns into the route table.

### Frontend

- Modify: `frontend-vue/src/components/DepartmentSelector.vue`
  Responsibility: upgrade the selector from two selects to three selects, keep search, and prevent submission on secondary nodes without tertiary children.
- Modify: `frontend-vue/src/components/DepartmentManagementPanel.vue`
  Responsibility: render collapsible primary/secondary/tertiary nodes, show secondary aggregate counts, show tertiary user counts, and lazily expand tertiary user lists.
- Create: `frontend-vue/src/utils/departmentSelectorModel.js`
  Responsibility: hold pure three-level selector logic for search matches, full-path selection, unselectable-secondary handling, and selection reset rules so the critical behaviors can be unit-tested without Vue DOM tooling.
- Create: `frontend-vue/src/utils/departmentManagementTreeModel.js`
  Responsibility: map backend department tree payloads into frontend render nodes, including the synthetic third-level legacy-remediation leaf when a secondary department still has users without tertiary binding.
- Modify: `frontend-vue/src/services/departments.js`
  Responsibility: send tertiary IDs for self-service updates and preserve disabled current bindings across three levels.
- Modify: `frontend-vue/src/services/admin.js`
  Responsibility: send tertiary IDs for user create/update, add tertiary CRUD/status/user-list calls, and update import/template helpers.
- Modify: `frontend-vue/src/utils/departmentSecondaryUsersRuntime.js`
  Responsibility: either rename and generalize to terminal-node keys or replace with a new runtime that supports both real tertiary nodes and the synthetic legacy-remediation leaf while keeping the lazy-load cache behavior.
- Modify: `frontend-vue/src/views/UserProfile.vue`
  Responsibility: render tertiary department completion/editing in personal center, keep forced-intercept behavior scoped to the department section, and exempt admin users from forced completion.
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
  Responsibility: pass tertiary IDs through user create/edit flows and render full department path in user management.
- Modify: `frontend-vue/src/router/profileSetup.js`
  Responsibility: preserve the “admin users are never forced by department completion” rule in the actual redirect guard used by the app shell.
- Modify: `frontend-vue/src/services/departments.test.js`
  Responsibility: lock three-level tree preservation and request payload behavior.
- Create: `frontend-vue/src/utils/departmentSelectorModel.test.js`
  Responsibility: behavior-test search result generation, full-path selection, and the unselectable-secondary rule.
- Create: `frontend-vue/src/utils/departmentManagementTreeModel.test.js`
  Responsibility: behavior-test synthetic legacy-remediation leaf generation and secondary summary metadata.
- Modify: `frontend-vue/src/utils/departmentSecondaryUsersRuntime.test.js`
  Responsibility: update runtime expectations for terminal-node keys, including synthetic legacy-remediation nodes.
- Modify: `frontend-vue/src/router/profileSetup.test.js`
  Responsibility: lock the redirect-guard rule that admin users are never blocked for missing department.
- Modify: `frontend-vue/src/views/UserProfile.department-flow.test.js`
  Responsibility: lock tertiary selector usage and forced-profile flow for non-admin users only.
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`
  Responsibility: lock tertiary tree rendering, counts, synthetic legacy-remediation leaf rendering, and lazy user expansion wiring.

## Data Contract Notes

### 1. New dictionary table

`tertiary_departments`

Required columns:

- `id bigint auto_increment`
- `secondary_department_id bigint not null`
- `name varchar(...) not null`
- `status enum('active','disabled') not null default 'active'`
- `created_at`
- `updated_at`

Required constraints:

- `UNIQUE KEY uq_tertiary_departments_secondary_name (secondary_department_id, name)`
- `KEY idx_tertiary_departments_secondary (secondary_department_id)`
- `FOREIGN KEY (secondary_department_id) REFERENCES secondary_departments(id) ON DELETE CASCADE`

### 2. Users table extension

Add:

- `tertiary_department_id bigint null`
- `KEY idx_users_tertiary_department_id (tertiary_department_id)`
- `FOREIGN KEY fk_users_tertiary_department (tertiary_department_id) REFERENCES tertiary_departments(id) ON DELETE SET NULL`

Do not backfill existing users. Existing primary+secondary bindings remain unchanged.

### 3. Valid binding states

Backend service logic must classify a user into one of these stable states:

1. `empty`: all three department IDs are null.
2. `legacy_two_level_complete`: primary+secondary valid, tertiary null.
3. `complete`: primary+secondary+tertiary all valid and consistent.
4. `invalid_partial`: any other combination, including broken foreign-key chains after dictionary changes.

Required behavior:

1. `empty` means `require_department_setup = true` for non-admin users.
2. `legacy_two_level_complete` means `require_department_setup = false` during this transition window.
3. `complete` means `require_department_setup = false`.
4. `invalid_partial` means `require_department_setup = true` for non-admin users.
5. Admin users always get `require_department_setup = false`.

### 4. Updated selectable tree

`GET /api/auth/departments/tree`

Expected structure:

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": 1,
        "name": "计算机学院",
        "secondary_items": [
          {
            "id": 11,
            "name": "软件工程系",
            "selectable": false,
            "disabled_reason": "暂无三级部门，请联系管理员维护",
            "tertiary_items": [
              {
                "id": 111,
                "name": "软件工程教研室"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

Rules:

1. Only active primary, secondary, and tertiary nodes belong in the selectable tree.
2. A secondary with zero active tertiary children still appears so the user can understand the structure.
3. Such a secondary is not a valid terminal choice.
4. Search results in the frontend must resolve to full tertiary paths only.

### 5. Updated admin tree

`GET /api/admin/departments/tree`

Expected secondary payload shape:

```json
{
  "id": 11,
  "name": "软件工程系",
  "status": "active",
  "effective_status": "active",
  "tertiary_count": 3,
  "user_count": 27,
  "legacy_user_count": 2,
  "tertiary_items": [
    {
      "id": 111,
      "name": "软件工程教研室",
      "status": "active",
      "effective_status": "active",
      "user_count": 10
    }
  ]
}
```

Rules:

1. `secondary.user_count` counts all users with that `secondary_department_id`, including tertiary-bound users and legacy users with `tertiary_department_id IS NULL`.
2. `secondary.legacy_user_count` counts only users with `secondary_department_id = secondary.id AND tertiary_department_id IS NULL`.
3. `tertiary.user_count` counts only users bound directly to that tertiary department.
4. Tertiary user lists are never embedded in the tree response.

### 6. Legacy-user remediation path under each secondary department

The approved UI rule is still “user expansion lives at the third level,” but legacy users with `tertiary_department_id = NULL` do not belong to a real tertiary department. The plan therefore needs an explicit compatibility path.

Required compatibility behavior:

1. When `secondary.legacy_user_count > 0`, the frontend renders a synthetic third-level leaf under that secondary department.
2. Suggested label: `未补全三级部门用户`.
3. The synthetic leaf is not stored in the dictionary and is not selectable anywhere outside admin management.
4. Expanding that synthetic leaf loads only users with `secondary_department_id = secondary.id AND tertiary_department_id IS NULL`.

Required route:

- `GET /api/admin/departments/secondary/{secondary_id}/legacy-users`

### 7. New admin tertiary routes

Required new routes:

- `POST /api/admin/departments/tertiary`
- `PUT /api/admin/departments/tertiary/{tertiary_id}`
- `PUT /api/admin/departments/tertiary/{tertiary_id}/status`
- `GET /api/admin/departments/tertiary/{tertiary_id}/users`
- `GET /api/admin/departments/secondary/{secondary_id}/legacy-users`

Recommended compatibility decision:

Keep `GET /api/admin/departments/secondary/{secondary_id}/users` temporarily if it already exists, but remove all frontend usage from it. This keeps the rollout additive and avoids unnecessary backend breakage during the transition.

### 8. Updated user import columns

Template columns become:

- `username`
- `password`
- `user_type`
- `primary_department_name`
- `secondary_department_name`
- `tertiary_department_name`

Rules:

1. All three department name columns may be empty together.
2. If any department name column is filled, all three are required.
3. Resolution uses active dictionary nodes only.

### 9. Updated department import columns

Template columns become:

- `primary_department_name`
- `primary_status`
- `secondary_department_name`
- `secondary_status`
- `tertiary_department_name`
- `tertiary_status`

Rules:

1. Each row always requires primary and secondary fields.
2. Tertiary fields may both be empty to import only the primary+secondary skeleton.
3. If one tertiary field is empty and the other is not, the row fails.
4. Existing rows are updated in place by scoped name uniqueness.
5. Departments missing from the file remain unchanged.

## Task 1: Add Tertiary Schema Foundation

**Files:**

- Create: `highThinkingQA/server/database/migrations/20260418_01_department_tertiary.sql`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/app/modules/departments/repository.py`
- Modify: `public-service/backend/tests/test_auth_module.py`
- Test: `public-service/backend/tests/test_departments_module.py`

- [ ] **Step 1: Write the failing migration and repository tests**

```python
def test_tertiary_department_migration_adds_table_and_user_column():
    ddl = load_migration_sql("20260418_01_department_tertiary.sql")
    assert "CREATE TABLE tertiary_departments" in ddl
    assert "tertiary_department_id" in ddl
    assert "uq_tertiary_departments_secondary_name" in ddl
    assert "fk_users_tertiary_department" in ddl


def test_auth_repository_select_fields_include_tertiary_department_id():
    repo = AuthRepository(database=FakeDatabase(...))
    repo._columns_cache = {
        "id", "username", "password_hash", "role", "user_type", "status",
        "is_first_login", "must_set_security_questions",
        "primary_department_id", "secondary_department_id", "tertiary_department_id",
    }
    fields = repo._select_user_fields(include_password=True)
    assert "tertiary_department_id" in fields


def test_auth_repository_update_user_department_writes_tertiary_column():
    repo = AuthRepository(database=FakeDatabase(...))
    repo._columns_cache = {"primary_department_id", "secondary_department_id", "tertiary_department_id"}
    captured = {}

    def fake_execute(query, params=()):
        captured["query"] = query
        captured["params"] = params
        return 1

    repo._execute_write = fake_execute
    repo.update_user_department(
        user_id=101,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )

    assert "tertiary_department_id" in captured["query"]
    assert captured["params"] == (1, 11, 111, 101)


def test_department_repository_detects_tertiary_user_column():
    repo = DepartmentRepository(database=FakeDatabase(...))
    repo._user_columns_cache = {"secondary_department_id", "tertiary_department_id"}
    assert repo.has_user_column("tertiary_department_id") is True
```

- [ ] **Step 2: Run the targeted backend tests and confirm they fail**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_auth_module.py -k "tertiary or migration or update_user_department" -v
```

Expected: FAIL because the migration and tertiary-aware repository logic do not exist yet.

- [ ] **Step 3: Add the additive MySQL migration**

Implementation requirements:

1. Follow the same idempotent `information_schema` + prepared-statement style already used in `20260416_01_user_departments.sql`.
2. Add `tertiary_departments`.
3. Add `users.tertiary_department_id`.
4. Add index and foreign key guards.
5. Do not rewrite or backfill existing user rows.

Representative DDL shape:

```sql
CREATE TABLE IF NOT EXISTS tertiary_departments (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    secondary_department_id BIGINT NOT NULL,
    name VARCHAR(128) NOT NULL,
    status ENUM('active', 'disabled') NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_tertiary_departments_secondary_name (secondary_department_id, name),
    KEY idx_tertiary_departments_secondary (secondary_department_id),
    CONSTRAINT fk_tertiary_departments_secondary
        FOREIGN KEY (secondary_department_id) REFERENCES secondary_departments(id)
        ON DELETE CASCADE
);
```

- [ ] **Step 4: Expose `tertiary_department_id` in repository column helpers**

```python
if self.has_column("tertiary_department_id"):
    fields.append("tertiary_department_id")
```

```python
if self.has_column("tertiary_department_id"):
    columns.append("tertiary_department_id")
    values.append(tertiary_department_id)
```

```python
def update_user_department(
    self,
    *,
    user_id: int,
    primary_department_id: int | None,
    secondary_department_id: int | None,
    tertiary_department_id: int | None,
) -> int:
    ...
```

- [ ] **Step 5: Run the targeted tests again**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_auth_module.py -k "tertiary or migration or update_user_department" -v
```

Expected: PASS for the migration-aware repository expectations.

- [ ] **Step 6: Commit**

```bash
git add highThinkingQA/server/database/migrations/20260418_01_department_tertiary.sql public-service/backend/app/modules/auth/repository.py public-service/backend/app/modules/departments/repository.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_auth_module.py
git commit -m "feat: add tertiary department schema foundation"
```

## Task 2: Upgrade Department Repository and Service to Three Levels

**Files:**

- Modify: `public-service/backend/app/modules/departments/repository.py`
- Modify: `public-service/backend/app/modules/departments/service.py`
- Modify: `public-service/backend/app/modules/departments/schemas.py`
- Test: `public-service/backend/tests/test_departments_module.py`

- [ ] **Step 1: Write failing tests for three-level trees, counts, and compatibility**

```python
def test_department_repository_admin_tree_includes_secondary_and_tertiary_counts():
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
            "secondary_legacy_user_count": 2,
            "tertiary_id": 111,
            "tertiary_name": "软件工程教研室",
            "tertiary_status": "active",
            "tertiary_user_count": 5,
        }
    ]
    tree = repo.list_department_tree(include_disabled=True)
    assert tree[0]["secondary_items"][0]["user_count"] == 7
    assert tree[0]["secondary_items"][0]["legacy_user_count"] == 2
    assert tree[0]["secondary_items"][0]["tertiary_items"][0]["user_count"] == 5


def test_department_service_selectable_tree_keeps_secondary_without_tertiary_but_marks_unselectable():
    service = DepartmentService(repository=FakeDepartmentRepository(...))
    result = service.get_selectable_tree()
    secondary = result["data"]["items"][0]["secondary_items"][0]
    assert secondary["selectable"] is False
    assert "暂无三级部门" in secondary["disabled_reason"]


def test_department_service_describes_legacy_two_level_user_without_forcing_completion():
    service = DepartmentService(repository=FakeDepartmentRepository(...))
    result = service.describe_user_department(
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=None,
    )
    assert result["data"]["department_completion_level"] == "legacy_two_level_complete"
    assert result["data"]["require_department_setup"] is False
```

- [ ] **Step 2: Run the targeted tests and confirm they fail**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py -k "selectable_tree or legacy_two_level or tertiary_count" -v
```

Expected: FAIL because the departments module still assumes secondary is the terminal node.

- [ ] **Step 3: Extend `DepartmentRepository` SQL and helper methods**

Required repository changes:

1. Join `secondary_departments` to `tertiary_departments`.
2. Aggregate `secondary_user_count`.
3. Aggregate `secondary_legacy_user_count`.
4. Aggregate `tertiary_user_count`.
5. Add `get_tertiary_by_id()`.
6. Add `get_tertiary_by_name()`.
7. Add `create_tertiary()`, `update_tertiary_name()`, `update_tertiary_status()`.
8. Add `list_users_by_tertiary_department()`.
9. Add `list_legacy_users_by_secondary_department()` for `secondary_department_id = ? AND tertiary_department_id IS NULL`.

Representative SQL shape:

```sql
SELECT
    p.id AS primary_id,
    p.name AS primary_name,
    p.status AS primary_status,
    s.id AS secondary_id,
    s.name AS secondary_name,
    s.status AS secondary_status,
    COALESCE(su.user_count, 0) AS secondary_user_count,
    COALESCE(sl.legacy_user_count, 0) AS secondary_legacy_user_count,
    t.id AS tertiary_id,
    t.name AS tertiary_name,
    t.status AS tertiary_status,
    COALESCE(tu.user_count, 0) AS tertiary_user_count
FROM primary_departments p
LEFT JOIN secondary_departments s ON s.primary_department_id = p.id
LEFT JOIN tertiary_departments t ON t.secondary_department_id = s.id
LEFT JOIN (...) su ON su.secondary_department_id = s.id
LEFT JOIN (...) sl ON sl.secondary_department_id = s.id
LEFT JOIN (...) tu ON tu.tertiary_department_id = t.id
ORDER BY p.id, s.id, t.id
```

Representative legacy-user query:

```sql
SELECT id, username, role, user_type, status
FROM users
WHERE secondary_department_id = %s
  AND tertiary_department_id IS NULL
ORDER BY username ASC, id ASC
```

- [ ] **Step 4: Refactor `DepartmentService` into explicit three-level validation helpers**

Introduce a single validation path instead of scattered two-level checks:

```python
def validate_department_selection(
    self,
    *,
    primary_department_id: int | None,
    secondary_department_id: int | None,
    tertiary_department_id: int | None,
    active_only: bool,
    allow_empty: bool,
    allow_legacy_two_level: bool,
) -> dict[str, Any]:
    ...
```

Required service behavior:

1. Preserve `legacy_two_level_complete` for reads.
2. Return full three-level names and IDs when tertiary exists.
3. Return a stable `department_completion_level`.
4. Return `secondary.selectable = false` with a clear reason when no active tertiary exists.
5. Keep disabled-current-binding preservation data usable by the frontend.

- [ ] **Step 5: Run the targeted tests again**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py -k "selectable_tree or legacy_two_level or tertiary_count" -v
```

Expected: PASS with three-level tree mapping and compatibility logic in place.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/departments/repository.py public-service/backend/app/modules/departments/service.py public-service/backend/app/modules/departments/schemas.py public-service/backend/tests/test_departments_module.py
git commit -m "feat: upgrade departments service to tertiary model"
```

## Task 3: Add Tertiary CRUD and Tertiary User-List Routes

**Files:**

- Modify: `public-service/backend/app/modules/departments/api.py`
- Modify: `public-service/backend/app/modules/departments/service.py`
- Modify: `public-service/backend/app/modules/departments/schemas.py`
- Modify: `public-service/backend/tests/test_departments_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`
- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`

- [ ] **Step 1: Write failing route-surface and contract tests**

```python
def test_department_routes_include_tertiary_crud_and_user_list():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/admin/departments/tertiary" in paths
    assert "/api/admin/departments/tertiary/{tertiary_id}" in paths
    assert "/api/admin/departments/tertiary/{tertiary_id}/status" in paths
    assert "/api/admin/departments/tertiary/{tertiary_id}/users" in paths


def test_gateway_route_table_includes_tertiary_department_routes():
    assert "/api/admin/departments/tertiary/{tertiary_id}/users" in PUBLIC_ROUTE_PATTERNS
    assert "/api/admin/departments/secondary/{secondary_id}/legacy-users" in PUBLIC_ROUTE_PATTERNS
```

```python
def test_tertiary_user_list_contract(monkeypatch):
    monkeypatch.setattr(
        department_service_module.department_service,
        "list_tertiary_users",
        lambda tertiary_id: {"success": True, "data": {"tertiary_department_id": tertiary_id, "user_count": 0, "users": []}},
    )
    response = client.get("/api/admin/departments/tertiary/111/users", headers=admin_headers())
    assert response.status_code == 200


def test_gateway_forwards_secondary_legacy_user_route():
    response = client.get("/api/admin/departments/secondary/11/legacy-users", headers=admin_headers())
    assert response.status_code in {200, 401, 403}


def test_secondary_legacy_user_list_contract(monkeypatch):
    monkeypatch.setattr(
        department_service_module.department_service,
        "list_secondary_legacy_users",
        lambda secondary_id: {"success": True, "data": {"secondary_department_id": secondary_id, "user_count": 0, "users": []}},
    )
    response = client.get("/api/admin/departments/secondary/11/legacy-users", headers=admin_headers())
    assert response.status_code == 200
```

- [ ] **Step 2: Run backend and gateway tests to confirm they fail**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py -k "tertiary or legacy_users" -v
```

Expected: FAIL because the new routes do not exist yet.

- [ ] **Step 3: Implement tertiary route surface**

Representative API shape:

```python
@router.post("/tertiary")
def create_tertiary(payload: CreateTertiaryRequest, auth=Depends(require_admin_auth)):
    ...


@router.put("/tertiary/{tertiary_id}/status")
def update_tertiary_status(tertiary_id: int, payload: UpdateDepartmentStatusRequest, auth=Depends(require_admin_auth)):
    ...


@router.get("/tertiary/{tertiary_id}/users")
def get_tertiary_users(tertiary_id: int, auth=Depends(require_admin_auth)):
    ...


@router.get("/secondary/{secondary_id}/legacy-users")
def get_secondary_legacy_users(secondary_id: int, auth=Depends(require_admin_auth)):
    ...
```

Representative gateway additions:

```python
"/api/admin/departments/tertiary",
"/api/admin/departments/tertiary/{tertiary_id}",
"/api/admin/departments/tertiary/{tertiary_id}/status",
"/api/admin/departments/tertiary/{tertiary_id}/users",
"/api/admin/departments/secondary/{secondary_id}/legacy-users",
```

- [ ] **Step 4: Add service support for tertiary mutations and user listing**

Required service behavior:

1. Validate `secondary_department_id` before creating a tertiary node.
2. Enforce unique tertiary names within one secondary department.
3. Compute `effective_status` using primary + secondary + tertiary status.
4. Return `404` with `TERTIARY_DEPARTMENT_NOT_FOUND` when needed.
5. Expose `list_secondary_legacy_users()` for the synthetic legacy-remediation leaf under each secondary department.

- [ ] **Step 5: Run the backend and gateway tests again**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py -k "tertiary or legacy_users" -v
```

Expected: PASS with the new route surface registered and forwarded.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/departments/api.py public-service/backend/app/modules/departments/service.py public-service/backend/app/modules/departments/schemas.py public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_route_surface.py gateway/app/routers/public_proxy.py gateway/app/services/route_table.py gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py
git commit -m "feat: add tertiary department routes"
```

## Task 4: Extend Auth Department Semantics With Legacy Compatibility

**Files:**

- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/tests/test_auth_module.py`

- [ ] **Step 1: Write failing auth tests for login/me and self-service update**

```python
def test_login_payload_exposes_tertiary_department_fields_for_complete_user():
    result = auth_service.login(username="alice", password="Pass123!")
    assert result["data"]["user"]["tertiary_department_id"] == 111
    assert result["data"]["user"]["department_completion_level"] == "complete"


def test_me_payload_does_not_force_legacy_two_level_user():
    result = auth_service.me(token_payload_for_user(...))
    assert result["data"]["require_department_setup"] is False
    assert result["data"]["department_completion_level"] == "legacy_two_level_complete"


def test_me_payload_never_forces_admin_department_setup():
    result = auth_service.me(token_payload_for_admin(...))
    assert result["data"]["require_department_setup"] is False


def test_update_my_department_requires_tertiary_for_non_empty_write():
    result = auth_service.update_my_department(
        user_id=101,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=None,
    )
    assert result["success"] is False
    assert result["code"] == "DEPARTMENT_REQUIRED"
```

- [ ] **Step 2: Run targeted auth tests and confirm they fail**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py -k "department_setup or tertiary_department or legacy_two_level" -v
```

Expected: FAIL because auth still treats secondary as the terminal node.

- [ ] **Step 3: Extend auth payloads and completion-state logic**

Required response additions:

```python
{
    "primary_department_id": ...,
    "secondary_department_id": ...,
    "tertiary_department_id": ...,
    "primary_department_name": ...,
    "secondary_department_name": ...,
    "tertiary_department_name": ...,
    "department_completion_level": "empty|legacy_two_level_complete|complete|invalid_partial",
    "require_department_setup": False,
}
```

Implementation rules:

1. Admin users bypass department interception.
2. Non-admin `empty` and `invalid_partial` states force `/profile`.
3. Non-admin `legacy_two_level_complete` stays usable.
4. Self-service update route accepts full empty or full three-level chain only.

- [ ] **Step 4: Keep department error handling isolated to the department flow**

Implementation note:

Do not wire department-fetch failure into unrelated password/security-question state. The personal center should keep independent error state for:

1. password update
2. security-question update
3. department dictionary fetch / department save

- [ ] **Step 5: Run the targeted auth tests again**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py -k "department_setup or tertiary_department or legacy_two_level" -v
```

Expected: PASS with legacy-compatible read logic and strict new-write validation.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/auth/schemas.py public-service/backend/app/modules/auth/service.py public-service/backend/app/modules/auth/api.py public-service/backend/tests/test_auth_module.py
git commit -m "feat: add tertiary-aware auth department flow"
```

## Task 5: Extend Admin User Create, Edit, and User Import to Tertiary

**Files:**

- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Modify: `public-service/backend/tests/test_admin_users_module.py`

- [ ] **Step 1: Write failing admin-user tests**

```python
def test_admin_create_user_accepts_empty_or_full_three_level_department_only():
    empty_result = admin_users_service.create_user(
        username="user1",
        password="Pass123!",
        user_type="common",
        primary_department_id=None,
        secondary_department_id=None,
        tertiary_department_id=None,
    )
    assert empty_result["success"] is True

    bad_result = admin_users_service.create_user(
        username="user2",
        password="Pass123!",
        user_type="common",
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=None,
    )
    assert bad_result["code"] == "DEPARTMENT_REQUIRED"


def test_admin_update_user_department_requires_full_three_level_chain():
    result = admin_users_service.update_user_department(
        user_id=101,
        primary_department_id=1,
        secondary_department_id=11,
        tertiary_department_id=111,
    )
    assert result["success"] is True


def test_user_import_requires_tertiary_when_any_department_name_is_present():
    result = admin_users_import_service.import_users(
        file_bytes=build_csv_bytes(...),
        filename="users.csv",
        actor_user_id=1,
    )
    assert result["data"]["details"][0]["status"] == "failed"
    assert "三级部门" in result["data"]["details"][0]["reason"]
```

- [ ] **Step 2: Run the targeted admin-user tests and confirm they fail**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py -k "tertiary or import_users or update_user_department" -v
```

Expected: FAIL because admin-user flows still validate only two department levels.

- [ ] **Step 3: Extend admin create/update schemas and service validation**

Required request additions:

```python
class AdminUserCreateRequest(BaseModel):
    ...
    tertiary_department_id: int | None = None
```

Required service rule:

```python
department_data = department_service.validate_department_selection(
    primary_department_id=primary_department_id,
    secondary_department_id=secondary_department_id,
    tertiary_department_id=tertiary_department_id,
    active_only=True,
    allow_empty=True,
    allow_legacy_two_level=False,
)
```

- [ ] **Step 4: Extend user batch import parsing and template generation**

Template headers:

```python
headers = [
    "username",
    "password",
    "user_type",
    "primary_department_name",
    "secondary_department_name",
    "tertiary_department_name",
]
```

Validation rules:

1. blank all three department-name columns: allowed
2. only some filled: fail row
3. all three filled but unresolved or disabled: fail row

- [ ] **Step 5: Run the targeted admin-user tests again**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py -k "tertiary or import_users or update_user_department" -v
```

Expected: PASS with tertiary-aware admin create/update/import behavior.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/admin_users/schemas.py public-service/backend/app/modules/admin_users/service.py public-service/backend/app/modules/admin_users/api.py public-service/backend/app/modules/admin_users/import_service.py public-service/backend/tests/test_admin_users_module.py
git commit -m "feat: extend admin user flows to tertiary departments"
```

## Task 6: Extend Department Dictionary Batch Import to Optional Tertiary Rows

**Files:**

- Modify: `public-service/backend/app/modules/departments/import_service.py`
- Modify: `public-service/backend/app/modules/departments/api.py`
- Modify: `public-service/backend/app/modules/departments/service.py`
- Modify: `public-service/backend/tests/test_departments_module.py`

- [ ] **Step 1: Write failing department-import tests for tertiary columns**

```python
def test_department_import_template_includes_tertiary_columns():
    response = department_import_service.template_response(fmt="csv")
    assert b"tertiary_department_name" in response.body
    assert b"tertiary_status" in response.body


def test_department_import_allows_secondary_without_tertiary_when_both_tertiary_columns_empty():
    result = department_import_service.import_departments(
        file_bytes=build_csv_bytes(...),
        filename="departments.csv",
        actor_user_id=1,
    )
    assert result["success"] is True


def test_department_import_rejects_half_filled_tertiary_columns():
    result = department_import_service.import_departments(
        file_bytes=build_csv_bytes(...),
        filename="departments.csv",
        actor_user_id=1,
    )
    assert result["data"]["details"][0]["status"] == "failed"
    assert "三级部门" in result["data"]["details"][0]["reason"]
```

- [ ] **Step 2: Run the targeted import tests and confirm they fail**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py -k "batch_import or import_template or tertiary_status" -v
```

Expected: FAIL because department import is still hard-coded to two levels.

- [ ] **Step 3: Extend the import service to tertiary-aware row handling**

Implementation rules:

1. Always resolve/upsert primary first.
2. Always resolve/upsert secondary second.
3. Only resolve/upsert tertiary when `tertiary_department_name` and `tertiary_status` are both non-empty.
4. Keep the existing “same-name update in place, absent rows unchanged” behavior.

Representative control flow:

```python
primary_id = self._upsert_primary(...)
secondary_id = self._upsert_secondary(primary_department_id=primary_id, ...)
if tertiary_name and tertiary_status:
    tertiary_id = self._upsert_tertiary(secondary_department_id=secondary_id, ...)
```

- [ ] **Step 4: Keep route contracts stable while updating template download**

Do not change the route paths:

- `POST /api/admin/departments/batch-import`
- `GET /api/admin/departments/import-template`

Only extend their data contract.

- [ ] **Step 5: Run the targeted import tests again**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py -k "batch_import or import_template or tertiary_status" -v
```

Expected: PASS with tertiary columns supported.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/departments/import_service.py public-service/backend/app/modules/departments/api.py public-service/backend/app/modules/departments/service.py public-service/backend/tests/test_departments_module.py
git commit -m "feat: extend department import to tertiary model"
```

## Task 7: Upgrade Frontend Department Services and Selector to Three Levels

**Files:**

- Modify: `frontend-vue/src/components/DepartmentSelector.vue`
- Create: `frontend-vue/src/utils/departmentSelectorModel.js`
- Create: `frontend-vue/src/utils/departmentSelectorModel.test.js`
- Modify: `frontend-vue/src/services/departments.js`
- Modify: `frontend-vue/src/services/departments.test.js`

- [ ] **Step 1: Write failing frontend behavior tests for three-level selection**

```javascript
test('buildSearchMatches returns full tertiary paths only', () => {
  const matches = buildSearchMatches(tree, '软件')
  assert.deepEqual(matches.map(item => item.path), [
    '计算机学院 / 软件工程系 / 软件工程教研室',
  ])
})

test('buildSecondarySelectionState keeps secondary without tertiary visible but unselectable', () => {
  const state = buildSecondarySelectionState(tree[0].secondary_items[0])
  assert.equal(state.selectable, false)
  assert.match(state.disabledReason, /暂无三级部门/)
})

test('selectSearchMatch fills all three ids from one full-path result', () => {
  const selected = selectSearchMatch({
    primaryId: 1,
    secondaryId: 11,
    tertiaryId: 111,
  })
  assert.deepEqual(selected, {
    primaryId: 1,
    secondaryId: 11,
    tertiaryId: 111,
  })
})

test('mergePreservedDepartmentTree preserves disabled tertiary binding', () => {
  const merged = mergePreservedDepartmentTree([], {
    primary_department_id: 1,
    secondary_department_id: 11,
    tertiary_department_id: 111,
    primary_department_name: '计算机学院',
    secondary_department_name: '软件工程系',
    tertiary_department_name: '软件工程教研室',
    department_effective_status: 'disabled',
  })
  assert.equal(merged[0].secondary_items[0].tertiary_items[0].id, 111)
})
```

- [ ] **Step 2: Run the targeted frontend tests and confirm they fail**

Run:

```bash
cd frontend-vue && node --test src/utils/departmentSelectorModel.test.js src/services/departments.test.js
```

Expected: FAIL because the selector and tree-merging logic still only support two levels.

- [ ] **Step 3: Extract selector behavior into a pure model module and refactor `DepartmentSelector.vue` to primary + secondary + tertiary**

Required component contract:

```vue
<DepartmentSelector
  :tree="departmentTree"
  :primary-id="selectedPrimaryDepartmentId"
  :secondary-id="selectedSecondaryDepartmentId"
  :tertiary-id="selectedTertiaryDepartmentId"
  @update:primary-id="..."
  @update:secondary-id="..."
  @update:tertiary-id="..."
/>
```

Required UX rules:

1. Search across all three names.
2. Search results render full `一级 / 二级 / 三级` paths.
3. Clicking a search result fills all three selects.
4. If a secondary has no tertiary nodes, render a clear hint and block tertiary submission.

- [ ] **Step 4: Extend `departments.js` for three-level preservation and update payloads**

Representative request body:

```javascript
body: JSON.stringify({
  primary_department_id: primaryDepartmentId,
  secondary_department_id: secondaryDepartmentId,
  tertiary_department_id: tertiaryDepartmentId,
})
```

Representative preservation shape:

```javascript
{
  id: secondaryDepartmentId,
  name: `${secondaryDepartmentName}${disabledSuffix}`,
  tertiary_items: [
    {
      id: tertiaryDepartmentId,
      name: `${tertiaryDepartmentName}${disabledSuffix}`,
    },
  ],
}
```

- [ ] **Step 5: Run the targeted frontend tests again**

Run:

```bash
cd frontend-vue && node --test src/utils/departmentSelectorModel.test.js src/services/departments.test.js
```

Expected: PASS with three-level selector service behavior in place.

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/components/DepartmentSelector.vue frontend-vue/src/utils/departmentSelectorModel.js frontend-vue/src/utils/departmentSelectorModel.test.js frontend-vue/src/services/departments.js frontend-vue/src/services/departments.test.js
git commit -m "feat: upgrade department selector to tertiary model"
```

## Task 8: Upgrade User Profile and Admin User Management Frontend Flows

**Files:**

- Modify: `frontend-vue/src/views/UserProfile.vue`
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/router/profileSetup.js`
- Modify: `frontend-vue/src/router/profileSetup.test.js`
- Modify: `frontend-vue/src/views/UserProfile.department-flow.test.js`

- [ ] **Step 1: Write failing frontend tests for profile/admin tertiary flows**

```javascript
test('UserProfile uses tertiary department selector state', () => {
  assert.match(profileSource, /selectedTertiaryDepartmentId/)
  assert.match(profileSource, /require_department_setup/)
})

test('AdminDashboard sends tertiary_department_id in create and edit flows', () => {
  assert.match(adminSource, /tertiary_department_id/)
  assert.match(adminServiceSource, /getTertiaryDepartmentUsers/)
})

test('profileSetup never forces admins for department completion', async () => {
  assert.equal(
    hasRequiredProfileSetup({ role: 'admin', user_type: 1, require_department_setup: true }),
    false,
  )
  assert.equal(
    buildRequiredProfilePath({ role: 'admin', user_type: 1, require_department_setup: true }),
    '/profile',
  )
})
```

- [ ] **Step 2: Run the targeted frontend tests and confirm they fail**

Run:

```bash
cd frontend-vue && node --test src/router/profileSetup.test.js src/views/UserProfile.department-flow.test.js src/views/AdminDashboard.department-management.test.js
```

Expected: FAIL because the views still use only primary+secondary state.

- [ ] **Step 3: Update `UserProfile.vue` to support tertiary completion without cross-contaminating other forms**

Required UI behavior:

1. Non-admin users with `require_department_setup = true` remain hard-blocked on `/profile`.
2. Admin users keep the profile page but are not forced to complete department.
3. Department fetch/save failure messages stay inside the department section only.
4. Legacy two-level users can still open profile normally and optionally upgrade to a tertiary binding.

- [ ] **Step 4: Update `AdminDashboard.vue` and `admin.js` for tertiary fields**

Required API additions:

```javascript
tertiary_department_id: department.tertiary_department_id ?? null
```

Required UI behavior:

1. Admin create-user form uses the three-level selector.
2. Admin edit-user department form uses the three-level selector.
3. Department display shows `一级 / 二级 / 三级` when present and falls back to two levels for legacy users.
4. `profileSetup.js` keeps ignoring `require_department_setup` for admin users even if a stale payload says otherwise.

- [ ] **Step 5: Run the targeted frontend tests again**

Run:

```bash
cd frontend-vue && node --test src/router/profileSetup.test.js src/views/UserProfile.department-flow.test.js src/views/AdminDashboard.department-management.test.js
```

Expected: PASS with tertiary-aware profile and admin user-management flows.

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/views/UserProfile.vue frontend-vue/src/views/AdminDashboard.vue frontend-vue/src/services/admin.js frontend-vue/src/router/profileSetup.js frontend-vue/src/router/profileSetup.test.js frontend-vue/src/views/UserProfile.department-flow.test.js
git commit -m "feat: update profile and admin user flows for tertiary departments"
```

## Task 9: Upgrade Department Management UI to Tertiary Expansion

**Files:**

- Modify: `frontend-vue/src/components/DepartmentManagementPanel.vue`
- Create: `frontend-vue/src/utils/departmentManagementTreeModel.js`
- Create: `frontend-vue/src/utils/departmentManagementTreeModel.test.js`
- Modify: `frontend-vue/src/utils/departmentSecondaryUsersRuntime.js`
- Modify: `frontend-vue/src/utils/departmentSecondaryUsersRuntime.test.js`
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

- [ ] **Step 1: Write failing frontend behavior tests for tertiary management**

```javascript
test('buildDepartmentRenderTree inserts synthetic legacy-remediation leaf when legacy users exist', () => {
  const nodes = buildDepartmentRenderTree([
    {
      id: 11,
      name: '软件工程系',
      tertiary_count: 1,
      user_count: 3,
      legacy_user_count: 2,
      tertiary_items: [{ id: 111, name: '软件工程教研室', user_count: 1 }],
    },
  ])
  assert.equal(nodes[0].children[0].nodeType, 'legacy_pending')
  assert.equal(nodes[0].children[0].userCount, 2)
})

test('department users runtime supports stable string keys for synthetic legacy nodes', async () => {
  const calls = []
  const runtime = createDepartmentUsersRuntime({
    requestUsers: async (nodeKey) => {
      calls.push(nodeKey)
      return { success: true, data: { users: [] } }
    },
  })
  await runtime.toggle('legacy-secondary-11')
  assert.deepEqual(calls, ['legacy-secondary-11'])
})
```

- [ ] **Step 2: Run the targeted frontend tests and confirm they fail**

Run:

```bash
cd frontend-vue && node --test src/utils/departmentManagementTreeModel.test.js src/utils/departmentSecondaryUsersRuntime.test.js src/views/AdminDashboard.department-management.test.js
```

Expected: FAIL because the panel still expands user lists on secondary nodes.

- [ ] **Step 3: Refactor the lazy-load runtime from secondary-based to tertiary-based**

Recommended refactor:

1. Rename the factory to a neutral name like `createDepartmentUsersRuntime`.
2. Cache by stable terminal node key instead of numeric-only IDs.
3. Keep the existing `expandedIds`, `usersById`, `loadingById`, `errorById`, `reset`, `load`, and `toggle` shape so the component refactor stays small.

Representative API:

```javascript
const departmentUsersRuntime = createDepartmentUsersRuntime({
  requestUsers: (tertiaryId) => adminApi.getTertiaryDepartmentUsers(tertiaryId),
})
```

For the synthetic legacy-remediation leaf:

```javascript
const departmentUsersRuntime = createDepartmentUsersRuntime({
  requestUsers: (nodeKey) => nodeKey.startsWith('legacy-secondary-')
    ? adminApi.getSecondaryLegacyDepartmentUsers(extractSecondaryId(nodeKey))
    : adminApi.getTertiaryDepartmentUsers(Number(nodeKey)),
})
```

- [ ] **Step 4: Rebuild `DepartmentManagementPanel.vue` to primary -> secondary -> tertiary**

Required rendering rules:

1. Primary nodes remain collapsible.
2. Secondary nodes remain collapsible.
3. Secondary summary shows `N 个三级部门 / M 人`.
4. When `legacy_user_count > 0`, show a note like `其中 2 人未补全三级`.
5. Render a synthetic third-level leaf labeled `未补全三级部门用户` when `legacy_user_count > 0`.
6. Tertiary nodes are individually collapsible.
7. Tertiary summary shows direct user count.
8. Expanding a real tertiary node lazy-loads `/api/admin/departments/tertiary/{tertiary_id}/users`; expanding the synthetic leaf lazy-loads `/api/admin/departments/secondary/{secondary_id}/legacy-users`.
9. Add tertiary create/rename/status actions in the panel.

- [ ] **Step 5: Run the targeted frontend tests again**

Run:

```bash
cd frontend-vue && node --test src/utils/departmentManagementTreeModel.test.js src/utils/departmentSecondaryUsersRuntime.test.js src/views/AdminDashboard.department-management.test.js
```

Expected: PASS with tertiary expansion and tertiary user loading in place.

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/components/DepartmentManagementPanel.vue frontend-vue/src/utils/departmentManagementTreeModel.js frontend-vue/src/utils/departmentManagementTreeModel.test.js frontend-vue/src/utils/departmentSecondaryUsersRuntime.js frontend-vue/src/utils/departmentSecondaryUsersRuntime.test.js frontend-vue/src/views/AdminDashboard.department-management.test.js
git commit -m "feat: upgrade department management to tertiary tree"
```

## Task 10: Full Regression Verification

**Files:**

- No new product files; verification only.

- [ ] **Step 1: Run public-service backend regression suites**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_route_surface.py -v
```

Expected: PASS.

- [ ] **Step 2: Run gateway regression suites**

Run:

```bash
conda run -n agent pytest gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py -v
```

Expected: PASS.

- [ ] **Step 3: Run frontend source-level tests**

Run:

```bash
cd frontend-vue && node --test src/utils/departmentSelectorModel.test.js src/utils/departmentManagementTreeModel.test.js src/services/departments.test.js src/utils/departmentSecondaryUsersRuntime.test.js src/router/profileSetup.test.js src/views/UserProfile.department-flow.test.js src/views/AdminDashboard.department-management.test.js
```

Expected: PASS.

- [ ] **Step 4: Run frontend production build**

Run:

```bash
cd frontend-vue && npm run build
```

Expected: PASS with no new build errors.

- [ ] **Step 5: Verify the rollout gate before any deployment**

Required release check:

1. Do not deploy Tasks 4-5 without Tasks 7-9.
2. Confirm the target release batch contains both strict backend validation and the new three-level frontend selectors.
3. If this cannot be guaranteed, keep strict three-level write rejection disabled until the frontend release is ready.

- [ ] **Step 6: Smoke-check the migration and runtime locally**

Run after applying the migration in the target user-space MySQL environment:

```bash
conda run -n agent pytest public-service/backend/tests/test_departments_module.py -k "tertiary and import" -v
```

Manual smoke checks:

1. Admin department panel can create tertiary departments.
2. Admin create-user can bind a tertiary department.
3. User batch import accepts blank-all or full three-level departments only.
4. Legacy two-level user can still log in and use the system.
5. Empty-department non-admin user is forced to `/profile`.
6. Admin user is never forced to `/profile` for department completion.
7. Secondary departments with legacy users show a synthetic `未补全三级部门用户` leaf and expanding it reveals the affected accounts.

- [ ] **Step 7: Commit verification snapshot**

```bash
git add .
git commit -m "test: verify tertiary department upgrade"
```

## Risks and Guardrails

1. Do not silently reinterpret a secondary-only binding as valid for new writes.
2. Do not backfill `users.tertiary_department_id` with guessed values.
3. Do not hide secondary nodes that currently have no tertiary children; users need to see why they cannot complete selection.
4. Do not lose visibility of legacy users in department management; secondary totals must still account for them.
5. Do not wire department-fetch errors into password/security-question UI.
6. Do not change unrelated admin dashboard tabs or the user QA main screen as part of this rollout.
