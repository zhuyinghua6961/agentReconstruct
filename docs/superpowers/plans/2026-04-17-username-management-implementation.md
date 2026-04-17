# Username Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持普通用户和超级用户在个人中心自助修改自己的用户名，支持管理员在用户管理里修改非管理员用户的用户名，同时保持管理员用户名不可自改、不可被改。

**Architecture:** 后端沿用现有 `public-service -> gateway -> frontend-vue` 链路，在 `auth` 和 `admin_users` 两个入口分别暴露自改和代改接口，但把用户名规则收敛到一套共享校验逻辑里，避免注册、管理员建用户、后续改名规则继续分叉。前端复用现有个人中心表单卡片和管理员 modal 交互模式，只补最小必要 UI、缓存同步和测试。

**Tech Stack:** FastAPI, Pydantic, MySQL repository layer, gateway public proxy, Vue 3 + Vite, node:test, pytest

---

## Requirements Snapshot

1. 管理员用户名不允许修改。
2. 普通用户和超级用户都允许在个人中心修改自己的用户名。
3. 管理员允许修改其他非管理员用户的用户名。
4. 目标用户如果是管理员，任何人都不允许修改其用户名。
5. 用户名规则沿用当前限制：
   - 长度 `3-50`
   - 不允许以 `admin` 开头，不区分大小写
   - 必须唯一
   - 不新增字符集限制
6. 改用户名不要求输入当前密码。
7. 自助改名成功后不强制重新登录，但前端必须同步本地缓存用户信息。

## File Map

### Backend

- Modify: `public-service/backend/app/modules/auth/repository.py`
  - 增加 `update_username()` repository 能力。
- Modify: `public-service/backend/app/modules/auth/service.py`
  - 收敛用户名规则校验；新增“当前用户修改用户名”服务方法；让 `register()` 复用同一套规则。
- Modify: `public-service/backend/app/modules/auth/schemas.py`
  - 增加用户名更新请求体。
- Modify: `public-service/backend/app/modules/auth/api.py`
  - 暴露 `PUT /api/auth/username` 与 `/api/v1/auth/username`。
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
  - 增加管理员修改用户名请求体。
- Modify: `public-service/backend/app/modules/admin_users/service.py`
  - 增加管理员修改目标用户用户名的服务方法，并让管理员建用户也复用共享用户名规则。
- Modify: `public-service/backend/app/modules/admin_users/api.py`
  - 暴露 `PUT /api/admin/users/{user_id}/username`。
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
  - 让批量导入用户名校验复用共享规则，不再保留第三套重复判断。
- Modify: `public-service/backend/tests/test_auth_module.py`
  - 补 auth 路由、服务、自助改名、register 规则复用测试。
- Modify: `public-service/backend/tests/test_admin_users_module.py`
  - 补管理员修改用户名 API/服务测试，以及导入链路对共享用户名规则的覆盖。
- Modify: `public-service/backend/tests/test_route_surface.py`
  - 补 public surface 断言。

### Gateway

- Modify: `gateway/app/routers/public_proxy.py`
  - 将新增 auth/admin 用户名接口加入代理路由集合。
- Modify: `gateway/app/services/route_table.py`
  - 将新增路径加入 public route ownership table。
- Modify: `gateway/tests/test_public_proxy.py`
  - 补代理路径断言。
- Modify: `gateway/tests/test_route_table.py`
  - 补路由表断言。

### Frontend

- Modify: `frontend-vue/src/services/auth.js`
  - 增加 `updateUsername()`。
- Modify: `frontend-vue/src/services/admin.js`
  - 增加 `updateUserUsername()`。
- Modify: `frontend-vue/src/views/UserProfile.vue`
  - 新增“用户名”编辑卡片，仅非管理员显示编辑入口；成功后更新 `currentUser` 与 `persistStoredUser()`。
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
  - 用户管理增加“修改用户名”按钮和 modal；管理员行不显示该按钮。
- Modify: `frontend-vue/src/views/UserProfile.department-flow.test.js`
  - 扩展个人中心源文件级断言，覆盖用户名自助修改入口。
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`
  - 扩展管理员用户管理断言，覆盖用户名修改入口和 admin 行保护。

## Task 1: 收敛后端用户名规则与 repository 更新能力

**Files:**
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/tests/test_auth_module.py`

- [ ] **Step 1: 先写 auth service 侧失败测试，固定共享规则**

在 `test_auth_module.py` 增加这些测试：

```python
def test_auth_service_rejects_username_shorter_than_3(): ...
def test_auth_service_rejects_username_with_admin_prefix_case_insensitive(): ...
def test_auth_service_rejects_duplicate_username_when_owner_differs(): ...
def test_auth_service_accepts_same_username_as_noop_for_same_user(): ...
```

关注点：
- 长度统一变成 `3-50`
- `AdminFoo` 也要被拒绝
- 自己提交原用户名视为成功/no-op

- [ ] **Step 2: 跑单测确认当前实现不满足新约束**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py -k "username" -v
```

Expected:
- 至少出现与新测试对应的失败

- [ ] **Step 3: 在 auth service 中提取共享用户名规则**

在 `auth/service.py` 增加单一来源的方法，建议形态：

```python
def validate_username_candidate(
    self,
    *,
    username: str,
    owner_user_id: int | None = None,
) -> dict[str, Any]:
    ...
```

行为要求：
- `strip()`
- 空值 / 长度越界返回 `VALIDATION_ERROR`
- `admin` 前缀返回 `USERNAME_INVALID`
- 查询重名时，若命中的用户 `id != owner_user_id`，返回 `USERNAME_EXISTS`
- 成功时返回标准化后的 `username`

同时把 `register()` 的用户名校验改成复用这套逻辑，消除现在 `3-64` 与管理员侧 `3-50` 的分叉。
管理员侧 `create_user()` / `import_service.py` 的共享校验接入放到 Task 3 统一处理，避免把 admin 改动提前塞进 auth 任务边界。

- [ ] **Step 4: 在 repository 中补 `update_username()`**

在 `auth/repository.py` 增加：

```python
def update_username(self, *, user_id: int, username: str) -> int:
    return self._execute_update(
        """
        UPDATE users
        SET username = %s
        WHERE id = %s
        """,
        (username, user_id),
    )
```

不要顺手改 schema，不涉及数据库迁移。

- [ ] **Step 5: 重跑 auth 用户名相关测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py -k "username or register" -v
```

Expected:
- 新增用户名规则测试通过
- register 相关测试仍通过

## Task 2: 新增用户自助修改用户名接口

**Files:**
- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/tests/test_auth_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: 写 auth API / service 失败测试**

在 `test_auth_module.py` 增加：

```python
def test_auth_routes_registered_include_username_update(): ...
def test_auth_api_update_username_contract(monkeypatch): ...
def test_auth_service_update_username_rejects_admin_self_service(): ...
def test_auth_service_update_username_updates_non_admin_user(): ...
def test_auth_service_update_username_returns_user_not_found(): ...
```

再在 `test_route_surface.py` 断言：

```python
"/api/auth/username"
"/api/v1/auth/username"
```

- [ ] **Step 2: 跑测试确认新增接口尚未实现**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -k "username" -v
```

Expected:
- route 或 service contract 失败

- [ ] **Step 3: 实现 schema + route + service**

实现要求：

```python
class UsernameUpdateRequest(BaseModel):
    username: str = Field(default="")
```

```python
@router.put("/api/auth/username")
@router.put("/api/v1/auth/username")
def update_username(...): ...
```

```python
def update_username(self, *, user_id: int, username: str) -> dict[str, Any]:
    user = self._repo.get_by_id(user_id)
    if not user:
        return USER_NOT_FOUND
    if self._is_admin_user(user):
        return PERMISSION_DENIED
    validation = self.validate_username_candidate(username=username, owner_user_id=user_id)
    ...
```

返回要求：
- 成功时返回 `_build_user_payload(updated_user)`，让前端直接同步缓存
- 管理员自助改名返回 `PERMISSION_DENIED`
- 若 repository `update_username()` 返回 0，但数据库里用户名已经是目标值，则视为成功/no-op
- 若校验后到写库前发生并发重名，唯一键冲突也要被收敛成 `USERNAME_EXISTS`，不能直接冒泡成 500

同时把 `status_code_for()` 补齐 `USERNAME_INVALID`、`PERMISSION_DENIED` 到正确状态码映射。

- [ ] **Step 4: 重跑 auth 模块测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -v
```

Expected:
- 全部通过

## Task 3: 新增管理员修改非管理员用户名接口

**Files:**
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Modify: `public-service/backend/tests/test_admin_users_module.py`

- [ ] **Step 1: 写 admin_users 失败测试**

在 `test_admin_users_module.py` 增加：

```python
def test_admin_routes_include_update_username(): ...
def test_admin_api_update_user_username_contract(monkeypatch): ...
def test_admin_api_update_user_username_returns_403_for_permission_denied(monkeypatch): ...
def test_admin_service_update_username_rejects_target_admin(): ...
def test_admin_service_update_username_updates_common_or_super_user(): ...
def test_admin_service_update_username_returns_user_not_found(): ...
def test_admin_service_update_username_accepts_same_username_as_noop(): ...
def test_admin_import_rejects_case_insensitive_admin_prefix_via_shared_rules(): ...
```

重点覆盖：
- 目标管理员账号时拒绝
- 超级用户可被改名
- 成功返回最新 `username`

- [ ] **Step 2: 跑测试确认接口尚未实现**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py -k "username" -v
```

Expected:
- API/service 测试失败

- [ ] **Step 3: 实现 admin schema + service + route**

建议新增：

```python
class UserUsernameUpdateRequest(BaseModel):
    username: str = Field(default="")
```

```python
@router.put("/users/{user_id}/username")
def update_user_username(...): ...
```

服务逻辑：
- `admin_users_service.status_code_for()` 需要把本接口产生的 `PERMISSION_DENIED` 映射为 `403`
- 读目标用户
- 若不存在返回 `USER_NOT_FOUND`
- 若目标是管理员，返回 `PERMISSION_DENIED`
- 调 `auth_service.validate_username_candidate(username=..., owner_user_id=target_user_id)`
- 调 repository `update_username()`
- 若 repository 返回 `0`，但库里用户名已经是目标值，则视为成功/no-op
- 成功返回 `{id, username, role, user_type}` 或更完整的目标用户信息
- 若写库时撞上唯一键并发冲突，转成 `USERNAME_EXISTS`

这里不要新造第二套用户名规则，也不要在 `admin_users` 内复制 `startswith("admin")` 逻辑。

`import_service.py` 的处理要求：
- `create_user()` 的用户名校验也要切到共享入口，admin 侧不要再保留独立规则分支
- 不再手写 `startswith("admin")` / 长度范围这类重复分支
- 逐行调用共享用户名规则，或统一委托到已复用共享规则的创建路径
- 保持现有导入结果结构不变：`success / failed / skipped`
- 现有导入测试要继续通过，再补一条大小写混合 `admin` 前缀校验，证明导入链路和其它入口一致

- [ ] **Step 4: 重跑 admin_users 测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py -v
```

Expected:
- 全部通过

## Task 4: 补 gateway 代理和 route table 暴露面

**Files:**
- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`

- [ ] **Step 1: 先补 gateway 断言**

新增断言路径：

```text
/api/auth/username
/api/v1/auth/username
/api/admin/users/{user_id}/username
```

- [ ] **Step 2: 跑 gateway 测试确认 surface 未更新**

Run:

```bash
conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -k "username" -v
```

Expected:
- 新增路径断言失败

- [ ] **Step 3: 更新 proxy route specs 与 route table**

在 `public_proxy.py` 的 `_ROUTE_SPECS` 增加：

```python
(_paths("/api/auth/username"), ("PUT",)),
(_paths("/api/admin/users/{user_id}/username", include_v1=False), ("PUT",)),
```

在 `route_table.py` 的 `_PUBLIC_ROUTE_GROUPS` 增加同路径。

- [ ] **Step 4: 重跑 gateway 测试**

Run:

```bash
conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v
```

Expected:
- 全部通过

## Task 5: 个人中心增加自助修改用户名

**Files:**
- Modify: `frontend-vue/src/services/auth.js`
- Modify: `frontend-vue/src/views/UserProfile.vue`
- Modify: `frontend-vue/src/views/UserProfile.department-flow.test.js`

- [ ] **Step 1: 先补前端源文件级测试**

在 `UserProfile.department-flow.test.js` 增加断言：

```javascript
test('UserProfile exposes username edit flow for non-admin users', () => {
  assert.match(profileSource, /修改用户名/)
  assert.match(profileSource, /authApi\.updateUsername/)
  assert.match(profileSource, /function isAdminIdentity|const isAdminIdentity/)
  assert.match(profileSource, /user_type === 1|role === 'admin'/)
  assert.match(profileSource, /syncStoredUser\(/)
})
```

- [ ] **Step 2: 跑前端测试确认入口不存在**

Run:

```bash
node --test frontend-vue/src/views/UserProfile.department-flow.test.js
```

Expected:
- 新增断言失败

- [ ] **Step 3: 在 auth service 和 UserProfile 实现最小 UI**

`auth.js` 新增：

```javascript
async updateUsername(username) {
  const token = readStoredToken()
  const response = await fetch(`${API_BASE}/username`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({ username })
  })
  ...
}
```

`UserProfile.vue` 实现要点：
- 增加 `isAdminIdentity(user)`，与现有管理员后台保持同一判定口径
- 新增 `showUsernameForm`、`usernameInput`、`usernameError`、`usernameSuccess`
- `fetchCurrentUser()` 成功后初始化 `usernameInput`
- 非管理员显示“用户名”编辑卡片；管理员只读展示
- 成功后：

```javascript
currentUser.value = { ...(currentUser.value || {}), ...(result.data || {}) }
syncStoredUser(result.data || {})
showUsernameForm.value = false
```

- 不要把用户名改名失败错误写进部门 error 区域
- 不要影响现有首次登录强制流

- [ ] **Step 4: 重跑前端测试**

Run:

```bash
node --test frontend-vue/src/views/UserProfile.department-flow.test.js
```

Expected:
- 通过

## Task 6: 管理员用户管理增加“修改用户名”入口

**Files:**
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

- [ ] **Step 1: 先补管理员视图测试**

在 `AdminDashboard.department-management.test.js` 增加断言：

```javascript
test('AdminDashboard wires username editing into user management flows', () => {
  assert.match(adminSource, /updateUserUsername/)
  assert.match(adminSource, /修改用户名/)
  assert.match(adminSource, /!isAdminIdentity\\(user\\)/)
})
```

- [ ] **Step 2: 跑测试确认管理员侧入口尚不存在**

Run:

```bash
node --test frontend-vue/src/views/AdminDashboard.department-management.test.js
```

Expected:
- 新增断言失败

- [ ] **Step 3: 在 admin service 和 AdminDashboard 实现 modal 流**

`admin.js` 新增：

```javascript
async updateUserUsername(userId, username) {
  ...
  return fetchWithErrorHandling(`${API_BASE}/users/${userId}/username`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ username })
  })
}
```

`AdminDashboard.vue` 实现要点：
- 增加 `showUsernameModal`、`editUsernameValue`
- `openUsernameModal(user)` 时注入当前用户名
- 行操作里仅在 `!isAdminIdentity(user)` 时显示“修改用户名”
- 提交成功后：
  - 关闭 modal
  - `success.value = ...`
  - `await fetchUsers()`
- 不要求管理员能修改自己；管理员自己的名称也不出现在任何可编辑入口里

- [ ] **Step 4: 重跑前端管理员测试**

Run:

```bash
node --test frontend-vue/src/views/AdminDashboard.department-management.test.js
```

Expected:
- 通过

## Task 7: 做整体验证

**Files:**
- No code changes required unless verification fails

- [ ] **Step 1: 跑 public-service 相关测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_route_surface.py -v
```

Expected:
- 全部通过

- [ ] **Step 2: 跑 gateway 相关测试**

Run:

```bash
conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v
```

Expected:
- 全部通过

- [ ] **Step 3: 跑前端相关测试**

Run:

```bash
node --test frontend-vue/src/views/UserProfile.department-flow.test.js frontend-vue/src/views/AdminDashboard.department-management.test.js
```

Expected:
- 全部通过

- [ ] **Step 4: 跑前端构建**

Run:

```bash
npm run build
```

Workdir:

```text
frontend-vue
```

Expected:
- Vite build 成功

- [ ] **Step 5: 做最小手工链路验证**

Manual checklist:

```text
1. 用普通用户或超级用户登录，进入 /profile，修改自己的用户名。
2. 修改成功后留在当前页，确认基本信息卡片立即显示新用户名。
3. 刷新 /profile，确认新用户名仍然存在，证明前端缓存与后端状态已同步，无需重新登录。
4. 用管理员进入 /admin，确认管理员自己的界面没有“修改用户名”入口。
5. 在管理员用户管理里修改一个非管理员用户的用户名，刷新列表后确认展示新值。
6. 若强行请求修改管理员目标用户用户名，后端应返回 403 / PERMISSION_DENIED。
```

Expected:
- 自助改名和管理员代改都可用
- 管理员自改与管理员目标改名都被正确拒绝
- 缓存同步行为符合需求

## Notes For Implementers

1. 这是纯用户名能力，不涉及数据库 schema 变更，也不要顺手改用户表长度定义。
2. `gateway` 的 auth context 依赖 `/api/v1/auth/me` 实时回读 username，所以改名后不需要强制重发 token。
3. 前端本地 `agentcode.auth.user.v1` / `user` 双份缓存都要通过现有 `persistStoredUser()` 统一更新。
4. 个人中心和管理员后台都已经有全局 `error/success` 提示，不要再引入新的 toast 体系。
5. 如果需要提高可复用性，优先把用户名规则收在 `auth/service.py`，不要在多个 service 里复制判断分支。
6. `admin_users/service.py` 里当前已有用户名长度和 `admin` 前缀校验；实施时应删除重复分支，改为调用共享校验入口，而不是“保留旧逻辑再补新接口”。
