# User Personnel Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 `public-service -> gateway -> frontend-vue` 用户体系增加“人员主档 + 账号绑定”能力，让用户必须绑定 `工号 + 姓名 + 校验码` 后才能正常使用系统，并让管理员在“用户管理”内维护人员表、导入人员、查看绑定账号、直接改绑/解绑。

**Architecture:** 后端新增独立 `personnel` 模块承载人员主档、导入、模板下载和绑定账号查询；账号当前绑定关系继续落在 `users.personnel_id`，由 `auth` 负责登录态和自助绑定，由 `admin_users` 负责管理员视角的绑定修复。前端沿用现有 profile 强制补全模型，不新增新路由；管理员界面在“用户管理”内部增加二级子 tab，并把人员管理面板拆成独立组件，避免继续膨胀 `AdminDashboard.vue`。

**Tech Stack:** FastAPI, Pydantic, MySQL, gateway public proxy, Vue 3 + Vite, node:test, pytest, `conda run -n agent`

---

## Requirements Snapshot

1. 人员主数据真源是新表 `personnel_records`，不是 `users` 表平铺字段。
2. `users` 仅新增 `personnel_id`，允许 `NULL`，表示当前账号未绑定人员。
3. 同一条人员记录允许绑定多个账号；一个账号同一时刻只能绑定一条人员记录。
4. `personnel_record_status` 只允许 `active | disabled`；`personnel_binding_status` 只允许 `unbound | bound_active | bound_disabled | bound_missing`。
5. `login` 与 `GET /api/auth/me` 的用户 payload 必须新增：
   - `personnel_id`
   - `employee_no`
   - `full_name`
   - `personnel_binding_status`
   - `require_personnel_setup`
6. 用户自助绑定与改绑统一走 `PUT /api/auth/personnel-binding`，都必须校验 `employee_no + full_name + verification_code`。
7. 管理员用户列表必须支持：
   - 查看用户当前人员信息
   - 绑定到某个启用中的人员
   - 解绑后让目标用户重新进入强制补全
8. 管理员“用户管理”内部新增二级 tab：
   - `账号列表`
   - `人员表`
9. 人员表必须支持：
   - 工号/姓名搜索
   - 状态筛选
   - 新增/编辑/启停用
   - 批量导入 Excel/CSV
   - 模板下载
   - 查看该人员绑定的全部账号
10. 人员停用后，不拒绝登录本身，但 `require_personnel_setup = true`，前端继续沿用现有 `/profile` 强制补全拦截模型。
11. 本 phase 不实现 `PERSONNEL_BINDING_ENFORCED` 灰度开关，默认强制开启。
12. 本 phase 的绑定/改绑/解绑/启停用审计先写结构化应用日志，不新增持久化审计表。

## Contract Snapshot

### Auth User Payload

```json
{
  "id": 12,
  "username": "alice",
  "role": "user",
  "user_type": 3,
  "personnel_id": 9,
  "employee_no": "T2024001",
  "full_name": "张三",
  "personnel_binding_status": "bound_active",
  "require_personnel_setup": false
}
```

### Admin User Row Payload

```json
{
  "id": 12,
  "username": "alice",
  "personnel_id": 9,
  "employee_no": "T2024001",
  "full_name": "张三",
  "personnel_binding_status": "bound_active",
  "personnel_display": "T2024001 / 张三"
}
```

### Personnel Record Payload

```json
{
  "id": 9,
  "employee_no": "T2024001",
  "full_name": "张三",
  "personnel_record_status": "active",
  "binding_count": 2
}
```

### Personnel Bindings Payload

```json
{
  "items": [
    {
      "id": 12,
      "username": "alice",
      "role": "user",
      "user_type": 3,
      "status": "active",
      "personnel_id": 9,
      "employee_no": "T2024001",
      "full_name": "张三",
      "personnel_binding_status": "bound_active"
    },
    {
      "id": 37,
      "username": "alice_lab",
      "role": "user",
      "user_type": 2,
      "status": "active",
      "personnel_id": 9,
      "employee_no": "T2024001",
      "full_name": "张三",
      "personnel_binding_status": "bound_active"
    }
  ]
}
```

## File Map

### Backend

- Create: `highThinkingQA/server/database/migrations/20260420_01_personnel_binding.sql`
- Create: `public-service/backend/app/modules/personnel/__init__.py`
- Create: `public-service/backend/app/modules/personnel/repository.py`
- Create: `public-service/backend/app/modules/personnel/service.py`
- Create: `public-service/backend/app/modules/personnel/schemas.py`
- Create: `public-service/backend/app/modules/personnel/api.py`
- Create: `public-service/backend/app/modules/personnel/import_service.py`
- Modify: `public-service/backend/app/main.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Create: `public-service/backend/tests/test_personnel_module.py`
- Modify: `public-service/backend/tests/test_auth_module.py`
- Modify: `public-service/backend/tests/test_admin_users_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

### Gateway

- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`

### Frontend

- Modify: `frontend-vue/src/services/auth.js`
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/views/Login.vue`
- Modify: `frontend-vue/src/router/profileSetup.js`
- Modify: `frontend-vue/src/router/profileSetup.test.js`
- Modify: `frontend-vue/src/views/UserProfile.vue`
- Modify: `frontend-vue/src/views/UserProfile.department-flow.test.js`
- Create: `frontend-vue/src/components/PersonnelManagementPanel.vue`
- Create: `frontend-vue/src/components/PersonnelLookupSelect.vue`
- Create: `frontend-vue/src/components/PersonnelBatchImportDialog.vue`
- Create: `frontend-vue/src/components/PersonnelImportResultDialog.vue`
- Create: `frontend-vue/src/components/PersonnelManagementPanel.structure.test.js`
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

## Task 1: 落数据库迁移和 repository 基础能力

**Files:**
- Create: `highThinkingQA/server/database/migrations/20260420_01_personnel_binding.sql`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Create: `public-service/backend/app/modules/personnel/repository.py`
- Create: `public-service/backend/tests/test_personnel_module.py`

- [ ] **Step 1: 先写 repository / migration 侧失败测试**

在 `test_personnel_module.py` 增加至少这些测试：

```python
def test_auth_repository_select_user_fields_include_personnel_id_when_present(): ...
def test_auth_repository_list_users_includes_personnel_id_when_present(): ...
def test_personnel_repository_maps_personnel_rows_and_binding_count(): ...
def test_personnel_repository_maps_binding_user_rows(): ...
```

关注点：
- `users.personnel_id` 出现在 `get_by_id / get_by_username / list_users` 的字段集中
- `personnel_records` 列表读取能带出 `binding_count`
- `GET /bindings` 对应的用户列表读取结构稳定

- [ ] **Step 2: 跑单测确认当前 schema 支撑不存在**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py -v
```

Expected:
- repository 字段或新模块相关测试失败

- [ ] **Step 3: 写 migration 和 repository**

`20260420_01_personnel_binding.sql` 要求：
- 创建 `personnel_records`
- 字段：
  - `id`
  - `employee_no VARCHAR(64) NOT NULL`
  - `full_name VARCHAR(64) NOT NULL`
  - `verification_code_hash VARCHAR(255) NOT NULL`
  - `status ENUM('active','disabled') NOT NULL DEFAULT 'active'`
  - `remarks VARCHAR(255) NULL`
  - `created_at`
  - `updated_at`
- 约束：
  - `employee_no` 唯一
  - `idx_personnel_records_status`
- 幂等扩展 `users.personnel_id`
- 给 `users.personnel_id` 加索引和外键 `fk_users_personnel`
- 外键使用 `ON DELETE SET NULL`，与现有用户外键风格一致

`auth/repository.py` 要求：
- `_select_user_fields()` 在列存在时包含 `personnel_id`
- `list_users()` 在列存在时包含 `personnel_id`
- 增加：

```python
def update_user_personnel(self, *, user_id: int, personnel_id: int | None) -> int: ...
```

`personnel/repository.py` 要求至少提供：
- `get_by_id()`
- `get_by_employee_no()`
- `list_personnel(...)`
- `count_personnel(...)`
- `create_personnel(...)`
- `update_personnel(...)`
- `update_personnel_status(...)`
- `list_bindings(personnel_id=...)`

不要在 repository 层做业务态判断，只做 SQL 和轻量 row mapping。

- [ ] **Step 4: 重跑 repository 相关测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py -k "repository or select_user_fields or list_users" -v
```

Expected:
- repository 基础测试通过

## Task 2: 实现人员表模块的管理、导入、模板和绑定列表

**Files:**
- Create: `public-service/backend/app/modules/personnel/__init__.py`
- Create: `public-service/backend/app/modules/personnel/service.py`
- Create: `public-service/backend/app/modules/personnel/schemas.py`
- Create: `public-service/backend/app/modules/personnel/api.py`
- Create: `public-service/backend/app/modules/personnel/import_service.py`
- Modify: `public-service/backend/app/main.py`
- Modify: `public-service/backend/tests/test_personnel_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: 写人员模块 API / service 失败测试**

在 `test_personnel_module.py` 增加：

```python
def test_personnel_service_list_supports_employee_name_and_status_filters(): ...
def test_personnel_service_create_hashes_verification_code(): ...
def test_personnel_service_update_can_reset_verification_code(): ...
def test_personnel_service_disable_updates_record_status(): ...
def test_personnel_service_list_bindings_returns_all_bound_accounts(): ...
def test_personnel_import_rejects_duplicate_employee_no_inside_file(): ...
def test_personnel_import_updates_existing_employee_no(): ...
def test_personnel_template_supports_csv_and_xlsx(): ...
def test_personnel_admin_routes_registered(): ...
```

在 `test_route_surface.py` 断言新增：

```text
/api/admin/personnel
/api/admin/personnel/{personnel_id}
/api/admin/personnel/{personnel_id}/status
/api/admin/personnel/{personnel_id}/bindings
/api/admin/personnel/batch-import
/api/admin/personnel/import-template
```

- [ ] **Step 2: 跑测试确认模块尚未实现**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py public-service/backend/tests/test_route_surface.py -k "personnel" -v
```

Expected:
- service / route surface 失败

- [ ] **Step 3: 实现 personnel service / schemas / api / import_service**

`schemas.py` 至少定义：

```python
class PersonnelCreateRequest(BaseModel): ...
class PersonnelUpdateRequest(BaseModel): ...
class PersonnelStatusUpdateRequest(BaseModel): ...
```

`service.py` 关键要求：
- 统一状态字段名使用 `personnel_record_status`
- 使用现有 PBKDF2 hash 风格存储 `verification_code_hash`
- 不回显旧校验码
- `list_personnel()` 支持 `employee_no` / `full_name` / `status` / `keyword` / `page` / `page_size`
- `keyword` 是前端选人下拉专用便捷参数，语义为 `employee_no LIKE ? OR full_name LIKE ?`，不替代显式工号/姓名筛选
- `list_bindings()` 返回全部绑定账号，不做分页首版优化
- 写操作统一打结构化日志：
  - `personnel_created`
  - `personnel_updated`
  - `personnel_status_changed`
  - `personnel_import_applied`

`import_service.py` 关键要求：
- 仅支持 `xlsx/csv`
- 模板字段固定：
  - `employee_no`
  - `full_name`
  - `verification_code`
  - `status`
  - `remarks`
- 同文件内重复 `employee_no` 直接整次导入失败
- 命中数据库已有 `employee_no` 时按覆盖更新处理
- 更新时必须覆盖：
  - `full_name`
  - `status`
  - `remarks`
  - `verification_code_hash`

`api.py` 关键要求：
- 路由前缀固定 `/api/admin/personnel`
- 复用与部门模块同类的 multipart 提取逻辑，不要再发明第三套上传解析

`main.py` 要求：
- 注册 `personnel_router`
- 根路径模块列表里加入 `personnel`

- [ ] **Step 4: 重跑人员模块测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py public-service/backend/tests/test_route_surface.py -v
```

Expected:
- 人员模块测试通过

## Task 3: 接入 auth，完成绑定状态计算和用户自助绑定

**Files:**
- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/tests/test_auth_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: 写 auth 失败测试，先固定 contract**

在 `test_auth_module.py` 增加：

```python
def test_auth_build_user_payload_exports_personnel_binding_fields(): ...
def test_login_returns_require_personnel_setup_for_unbound_user(): ...
def test_login_returns_bound_disabled_for_disabled_personnel(): ...
def test_get_me_returns_bound_active_personnel_payload(): ...
def test_auth_routes_include_personnel_binding_update(): ...
def test_update_personnel_binding_rejects_invalid_tuple_without_leaking_detail(): ...
def test_update_personnel_binding_rejects_disabled_personnel(): ...
def test_update_personnel_binding_allows_rebind_to_other_active_personnel(): ...
def test_admin_user_is_not_exempt_from_personnel_requirement(): ...
```

重点覆盖：
- `personnel_binding_status` 不再叫 `personnel_status`
- `require_personnel_setup` 对管理员同样生效
- 绑定失败时错误信息收口
- 登录不直接报错拒绝，而是返回需补全标记

同时在 `test_route_surface.py` 断言：

```text
/api/auth/personnel-binding
/api/v1/auth/personnel-binding
```

- [ ] **Step 2: 跑测试确认 auth contract 还没接上**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -k "personnel" -v
```

Expected:
- 新增 personnel 相关断言失败

- [ ] **Step 3: 在 auth 中实现 payload 扩展和自助绑定**

`schemas.py` 新增：

```python
class PersonnelBindingUpdateRequest(BaseModel):
    employee_no: str = Field(default="")
    full_name: str = Field(default="")
    verification_code: str = Field(default="")
```

`api.py` 新增：

```python
@router.put("/api/v1/auth/personnel-binding")
@router.put("/api/auth/personnel-binding")
def update_personnel_binding(...): ...
```

`service.py` 关键要求：
- 通过注入 `personnel_service` 或轻量 helper 统一计算：
  - `personnel_id`
  - `employee_no`
  - `full_name`
  - `personnel_binding_status`
  - `require_personnel_setup`
- `_build_user_payload()` 负责合并这些字段
- `login()` 的 `result.data.user` 与顶层 `require_personnel_setup` 都要同步携带人员信息
- 新增 `update_personnel_binding()`：
  - 校验三字段非空
  - 用 `employee_no + full_name + verification_code` 找目标人员
  - 不暴露“工号不存在 / 姓名不匹配 / 校验码错误”的细分原因
  - 若目标人员 `disabled`，返回 `PERSONNEL_DISABLED`
  - 成功后更新 `users.personnel_id`
  - 返回最新 `_build_user_payload(updated_user)`
- 绑定/改绑成功写结构化日志：
  - `personnel_bound_by_self`
  - `personnel_rebound_by_self`

这里不要新增全局后端拦截中间件；继续沿用当前“登录和 me 暴露 require flag，由前端路由守卫强制跳 /profile”的既有模型。

- [ ] **Step 4: 重跑 auth 人员绑定测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -v
```

Expected:
- auth 模块测试通过

## Task 4: 扩展 admin_users，支持用户列表展示人员信息和后台改绑/解绑

**Files:**
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/tests/test_admin_users_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: 写 admin_users 失败测试**

在 `test_admin_users_module.py` 增加：

```python
def test_list_users_includes_personnel_summary_fields(): ...
def test_admin_routes_include_user_personnel_binding_endpoints(): ...
def test_admin_bind_user_to_active_personnel(): ...
def test_admin_bind_rejects_disabled_personnel(): ...
def test_admin_unbind_user_sets_require_personnel_setup_again(): ...
def test_admin_bind_returns_404_for_missing_personnel(): ...
```

重点覆盖：
- 用户列表行里出现 `personnel_id / employee_no / full_name / personnel_binding_status / personnel_display`
- 管理员解绑后，用户重新回到 `unbound`

同时在 `test_route_surface.py` 断言：

```text
/api/admin/users/{user_id}/personnel-binding
```

- [ ] **Step 2: 跑测试确认后台修复链路还未存在**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_route_surface.py -k "personnel" -v
```

Expected:
- 新增 route / service 测试失败

- [ ] **Step 3: 实现 admin_users 人员绑定能力**

`schemas.py` 新增：

```python
class UserPersonnelBindingUpdateRequest(BaseModel):
    personnel_id: int = Field(gt=0)
```

`api.py` 新增：

```python
@router.put("/users/{user_id}/personnel-binding")
def update_user_personnel_binding(...): ...

@router.delete("/users/{user_id}/personnel-binding")
def clear_user_personnel_binding(...): ...
```

`service.py` 关键要求：
- `list_users()` 通过人员服务或 shared helper 组装人员摘要字段
- `update_user_personnel_binding()`：
  - 目标用户不存在 -> `USER_NOT_FOUND`
  - 目标人员不存在 -> `PERSONNEL_NOT_FOUND`
  - 目标人员停用 -> `PERSONNEL_DISABLED`
  - 成功后更新 `users.personnel_id`
  - 返回最新目标用户 payload
- `clear_user_personnel_binding()`：
  - `users.personnel_id = NULL`
  - 返回最新 payload，确保 `personnel_binding_status = unbound`
  - `require_personnel_setup = true`
- 两个后台写操作都写结构化日志：
  - `personnel_bound_by_admin`
  - `personnel_unbound_by_admin`

不要把“解绑”实现成 `PUT personnel_id=null` 的隐式语义；首版严格按独立 `DELETE` 落地，避免前后端误解。

- [ ] **Step 4: 重跑 admin_users 测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_route_surface.py -v
```

Expected:
- admin_users 模块测试通过

## Task 5: 补齐 gateway 代理和 public route table

**Files:**
- Modify: `gateway/app/routers/public_proxy.py`
- Modify: `gateway/app/services/route_table.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`

- [ ] **Step 1: 先补 gateway 断言**

新增路径断言：

```text
/api/auth/personnel-binding
/api/v1/auth/personnel-binding
/api/admin/personnel
/api/admin/personnel/{personnel_id}
/api/admin/personnel/{personnel_id}/status
/api/admin/personnel/{personnel_id}/bindings
/api/admin/personnel/batch-import
/api/admin/personnel/import-template
/api/admin/users/{user_id}/personnel-binding
```

同时补 method 断言：

```text
PUT /api/auth/personnel-binding
PUT /api/v1/auth/personnel-binding
GET|POST /api/admin/personnel
PUT /api/admin/personnel/{personnel_id}
PUT /api/admin/personnel/{personnel_id}/status
GET /api/admin/personnel/{personnel_id}/bindings
POST /api/admin/personnel/batch-import
GET /api/admin/personnel/import-template
PUT|DELETE /api/admin/users/{user_id}/personnel-binding
```

- [ ] **Step 2: 跑 gateway 测试确认 surface 未更新**

Run:

```bash
conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -k "personnel" -v
```

Expected:
- 新路径断言失败

- [ ] **Step 3: 更新 public_proxy 和 route_table**

`public_proxy.py` 的 `_ROUTE_SPECS` 增加：

```python
(_paths("/api/auth/personnel-binding"), ("PUT",)),
(_paths("/api/admin/personnel", include_v1=False), ("GET", "POST")),
(_paths("/api/admin/personnel/{personnel_id}", include_v1=False), ("PUT",)),
(_paths("/api/admin/personnel/{personnel_id}/status", include_v1=False), ("PUT",)),
(_paths("/api/admin/personnel/{personnel_id}/bindings", include_v1=False), ("GET",)),
(_paths("/api/admin/personnel/batch-import", include_v1=False), ("POST",)),
(_paths("/api/admin/personnel/import-template", include_v1=False), ("GET",)),
(_paths("/api/admin/users/{user_id}/personnel-binding", include_v1=False), ("PUT", "DELETE")),
```

`route_table.py` 的 `_PUBLIC_ROUTE_GROUPS` 同步增加相同路径组。

`test_public_proxy.py` 与 `test_route_table.py` 不只断言 path 存在，还要显式断言：

```python
"/api/admin/users/{user_id}/personnel-binding" in route.path
set(route.methods) == {"PUT", "DELETE"}
```

避免后续只保留了其中一个 method 仍然误过 path-only 测试。

- [ ] **Step 4: 重跑 gateway 测试**

Run:

```bash
conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v
```

Expected:
- gateway route surface 测试通过

## Task 6: 前端接入登录态字段、路由守卫和个人中心自助绑定

**Files:**
- Modify: `frontend-vue/src/services/auth.js`
- Modify: `frontend-vue/src/views/Login.vue`
- Modify: `frontend-vue/src/router/profileSetup.js`
- Modify: `frontend-vue/src/router/profileSetup.test.js`
- Modify: `frontend-vue/src/views/UserProfile.vue`
- Modify: `frontend-vue/src/views/UserProfile.department-flow.test.js`

- [ ] **Step 1: 先补前端 source-level 测试**

在 `profileSetup.test.js` 增加断言：

```javascript
test('hasRequiredProfileSetup blocks on personnel requirement for all roles', () => { ... })
test('buildRequiredProfilePath appends personnel query param', () => { ... })
test('mergeValidatedUser preserves require_personnel_setup for admin users', () => { ... })
```

在 `UserProfile.department-flow.test.js` 增加断言：

```javascript
test('Login persists personnel completion flags from auth payload', () => { ... })
test('UserProfile renders personnel binding section', () => { ... })
test('UserProfile keeps personnel binding errors scoped locally', () => { ... })
test('UserProfile wires authApi.updatePersonnelBinding into self-bind flow', () => { ... })
```

- [ ] **Step 2: 跑前端测试确认入口尚不存在**

Run:

```bash
node --test frontend-vue/src/router/profileSetup.test.js frontend-vue/src/views/UserProfile.department-flow.test.js
```

Expected:
- 新增 personnel 断言失败

- [ ] **Step 3: 实现 auth service、登录页、路由守卫和 UserProfile 人员绑定区**

`auth.js` 新增：

```javascript
async updatePersonnelBinding(employeeNo, fullName, verificationCode) { ... }
```

`Login.vue` 要求：
- `persistStoredUser()` 时写入：
  - `personnel_id`
  - `employee_no`
  - `full_name`
  - `personnel_binding_status`
  - `require_personnel_setup`
- 首次登录提示文案可追加“绑定人员信息”
- 进入 `/profile` 的 query 参数增加 `personnel=required`

`profileSetup.js` 要求：
- `hasRequiredProfileSetup()` 增加 `user?.require_personnel_setup`
- 不对管理员豁免人员要求
- `buildRequiredProfilePath()` 增加 `personnel=required`
- `mergeValidatedUser()` 规范化 `require_personnel_setup`

`UserProfile.vue` 要求：
- 新增独立状态：
  - `personnelError`
  - `personnelSuccess`
  - `forcePersonnelSetup`
  - `employeeNoInput`
  - `fullNameInput`
  - `verificationCodeInput`
  - `showPersonnelForm`
- 人员绑定区对所有用户显示，不做管理员豁免
- 当前绑定展示：
  - 未绑定 -> `未绑定`
  - `bound_disabled` -> 明确告警
  - 已绑定 -> 显示 `工号 / 姓名`
- 保存成功后：

```javascript
currentUser.value = { ...(currentUser.value || {}), ...(result.data || {}) }
syncStoredUser(result.data || {})
forcePersonnelSetup.value = Boolean(result.data?.require_personnel_setup)
```

- `hasPendingForcedSetup()` 纳入 `forcePersonnelSetup`
- 不要把 personnel 失败写进全局 `error` 或部门 `departmentError`
- 个人中心里部门树获取保持按需执行：只有部门区需要显示或用户手动展开时再拉，避免“只补人员时还冒出获取部门选项失败”

- [ ] **Step 4: 重跑前端测试**

Run:

```bash
node --test frontend-vue/src/router/profileSetup.test.js frontend-vue/src/views/UserProfile.department-flow.test.js
```

Expected:
- 人员强制补全和个人中心自助绑定相关测试通过

## Task 7: 搭建管理员“人员表”面板、搜索选择和导入能力

**Files:**
- Modify: `frontend-vue/src/services/admin.js`
- Create: `frontend-vue/src/components/PersonnelManagementPanel.vue`
- Create: `frontend-vue/src/components/PersonnelLookupSelect.vue`
- Create: `frontend-vue/src/components/PersonnelBatchImportDialog.vue`
- Create: `frontend-vue/src/components/PersonnelImportResultDialog.vue`
- Create: `frontend-vue/src/components/PersonnelManagementPanel.structure.test.js`

- [ ] **Step 1: 先补组件结构测试**

在 `PersonnelManagementPanel.structure.test.js` 增加断言：

```javascript
test('PersonnelManagementPanel renders account list filters and status filter', () => { ... })
test('PersonnelManagementPanel exposes create edit status and bindings actions', () => { ... })
test('PersonnelManagementPanel wires batch import and template download', () => { ... })
test('PersonnelManagementPanel lazy loads bindings when expanding a personnel row', () => { ... })
test('PersonnelLookupSelect supports keyword search and active-only selection', () => { ... })
```

- [ ] **Step 2: 跑测试确认组件尚不存在**

Run:

```bash
node --test frontend-vue/src/components/PersonnelManagementPanel.structure.test.js
```

Expected:
- 新组件或关键结构断言失败

- [ ] **Step 3: 在 admin service 与新组件实现人员表能力**

`admin.js` 至少新增：

```javascript
getPersonnel(params = {})
createPersonnel(payload)
updatePersonnel(personnelId, payload)
updatePersonnelStatus(personnelId, status)
getPersonnelBindings(personnelId)
batchImportPersonnel(file)
downloadPersonnelImportTemplate(format = 'xlsx')
bindUserPersonnel(userId, personnelId)
unbindUserPersonnel(userId)
```

`PersonnelManagementPanel.vue` 关键要求：
- 内部状态：
  - 搜索工号
  - 搜索姓名
  - 状态筛选
  - 列表 loading / error / success
  - 行展开状态
  - bindings loading / error / data
- 列表列：
  - 工号
  - 姓名
  - 状态
  - 绑定账号数
  - 更新时间
  - 操作
- 行展开时懒加载 `GET /api/admin/personnel/{personnel_id}/bindings`
- 支持新增、编辑、启停用
- 批量导入成功后刷新列表，并显示导入结果弹窗

`PersonnelLookupSelect.vue` 关键要求：
- 用于管理员在账号列表里给用户选择目标人员
- 远程调用 `getPersonnel({ keyword, status: 'active', page_size: 20 })`
- 选择项文案固定 `employee_no / full_name`
- 只允许选启用中的人员

`PersonnelBatchImportDialog.vue` 与 `PersonnelImportResultDialog.vue` 关键要求：
- 模板字段说明完整
- 支持 `xlsx/csv`
- 上传中禁止关闭
- 关闭时清空 file input

- [ ] **Step 4: 重跑组件测试**

Run:

```bash
node --test frontend-vue/src/components/PersonnelManagementPanel.structure.test.js
```

Expected:
- 人员面板与导入组件结构测试通过

## Task 8: 在 AdminDashboard 接入二级子 tab、用户列表人员列和改绑/解绑弹窗

**Files:**
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`
- Modify: `frontend-vue/src/services/admin.js`

- [ ] **Step 1: 先补 AdminDashboard 结构测试**

在 `AdminDashboard.department-management.test.js` 增加断言：

```javascript
test('AdminDashboard adds user management secondary tabs for accounts and personnel', () => { ... })
test('AdminDashboard mounts PersonnelManagementPanel under user management', () => { ... })
test('AdminDashboard user table shows personnel info column', () => { ... })
test('AdminDashboard wires admin bind and unbind personnel actions', () => { ... })
```

- [ ] **Step 2: 跑测试确认 dashboard 还没接好**

Run:

```bash
node --test frontend-vue/src/views/AdminDashboard.department-management.test.js
```

Expected:
- 新增人员管理断言失败

- [ ] **Step 3: 在 AdminDashboard 接线**

`AdminDashboard.vue` 关键要求：
- 在“用户管理”内部新增二级 tab，本地状态建议：

```javascript
const activeUserManagementTab = ref('accounts')
```

- `accounts` 子 tab 保留现有账号列表
- `personnel` 子 tab 渲染 `PersonnelManagementPanel`
- 账号列表新增 `人员信息` 列，显示：
  - `未绑定`
  - `employee_no / full_name`
  - 停用时加 `（已停用）`
- 新增“设置人员”弹窗：
  - 使用 `PersonnelLookupSelect`
  - 支持保存绑定
  - 支持解绑
  - 保存/解绑成功后 `await fetchUsers()`
- 管理员页自己的一级 tab 结构不变；不要把“人员表”做成新的一级顶栏

- [ ] **Step 4: 跑管理员前端测试和构建**

Run:

```bash
node --test frontend-vue/src/components/PersonnelManagementPanel.structure.test.js frontend-vue/src/views/AdminDashboard.department-management.test.js
```

Expected:
- 人员表和管理员接线测试通过

Run:

```bash
cd frontend-vue && npm run build
```

Expected:
- 构建成功

## Task 9: 做后端和网关全量回归

**Files:**
- No additional files; verification only

- [ ] **Step 1: 跑 public-service 后端测试集**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_admin_users_module.py public-service/backend/tests/test_route_surface.py -v
```

Expected:
- 全部通过

- [ ] **Step 2: 跑 gateway 相关测试集**

Run:

```bash
conda run -n agent pytest gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py -v
```

Expected:
- 全部通过

- [ ] **Step 3: 跑前端关键 source-level 测试**

Run:

```bash
node --test frontend-vue/src/router/profileSetup.test.js frontend-vue/src/views/UserProfile.department-flow.test.js frontend-vue/src/components/PersonnelManagementPanel.structure.test.js frontend-vue/src/views/AdminDashboard.department-management.test.js
```

Expected:
- 全部通过

- [ ] **Step 4: 记录上线注意事项**

在执行实现时补一段简短交付说明，至少包含：
- 需要先执行 `20260420_01_personnel_binding.sql`
- 上线后存量用户会因为 `users.personnel_id = NULL` 被标记为 `require_personnel_setup = true`
- 若误停用管理员所绑定人员，管理员账号也会被 profile 强制拦截
