# Personnel Department Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把非管理员账号的部门真源切换到 `personnel_records`，要求管理员在新增/导入人员时必须填写完整三级部门，并让注册、人员绑定、管理员代绑、人员改部门都自动同步账号部门缓存，同时移除所有账号级直接改部门入口。

**Architecture:** 后端在 `personnel_records` 上新增三级部门引用，并把 `users` 上的三级部门字段降级为同步缓存；新增一个严格真源开关控制迁移期 fallback，避免存量账号在回填前被提前拦截。前端把部门维护入口统一收敛到“人员表”，注册页、个人中心和管理员用户管理全部移除账号级部门输入，只保留只读展示和阻断提示。

**Tech Stack:** FastAPI, Pydantic, MySQL, Vue 3 + Vite, node:test, pytest, `conda run -n agent`

---

## Requirements Snapshot

1. `personnel_records` 成为非管理员账号部门的唯一业务真源。
2. `users.primary_department_id / secondary_department_id / tertiary_department_id` 保留为同步缓存，不再作为真源。
3. 管理员新增人员、编辑人员、批量导入人员时必须填写完整、启用中的三级部门。
4. 同一 `personnel` 绑定的全部账号必须共享同一套部门。
5. 注册不再提交部门字段，部门从人员信息自动带出。
6. 用户个人中心不再允许修改部门。
7. 管理员用户管理不再允许直接修改账号部门；新增用户和导入用户也不再录入部门。
8. 用户自助绑定/改绑人员、管理员代绑人员时，必须同步账号部门缓存。
9. 管理员修改人员部门时，必须同步所有绑定账号的部门缓存。
10. 管理员账号继续豁免 `require_department_setup` / `require_personnel_setup`。
11. `/api/*` 与 `/api/v1/*` 的 register / auth department contract 必须保持一致。
12. 需要迁移期 fallback，确保存量人员部门尚未回填时不会提前阻断老账号。

## File Map

### Backend Data / Runtime

- Create: `highThinkingQA/server/database/migrations/20260420_02_personnel_department_source.sql`
  - 给 `personnel_records` 增加三级部门字段、索引和外键。
- Modify: `public-service/backend/app/core/config.py`
  - 增加 `personnel_department_strict_source_enabled` 开关。
- Modify: `public-service/backend/app/modules/auth/repository.py`
  - 增加批量同步 / 清空用户部门缓存的方法。
- Modify: `public-service/backend/app/modules/personnel/repository.py`
  - 读写人员三级部门、列出回填候选、支持导入同步。
- Create: `public-service/backend/app/modules/personnel/backfill_service.py`
  - 负责存量人员部门回填预览、冲突报告和可应用写入。
- Create: `scripts/personnel_department_backfill.py`
  - 迁移期 dry-run / apply 脚本入口。

### Backend Personnel / Auth / Admin

- Modify: `public-service/backend/app/modules/personnel/schemas.py`
- Modify: `public-service/backend/app/modules/personnel/service.py`
- Modify: `public-service/backend/app/modules/personnel/api.py`
- Modify: `public-service/backend/app/modules/personnel/import_service.py`
- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`

### Backend Tests

- Modify: `public-service/backend/tests/test_personnel_module.py`
- Modify: `public-service/backend/tests/test_auth_module.py`
- Modify: `public-service/backend/tests/test_admin_users_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

### Frontend

- Create: `frontend-vue/src/components/PersonnelEditorDialog.vue`
  - 人员新增/编辑表单，承载 `DepartmentSelector`。
- Modify: `frontend-vue/src/components/PersonnelManagementPanel.vue`
- Modify: `frontend-vue/src/components/PersonnelManagementPanel.structure.test.js`
- Modify: `frontend-vue/src/components/PersonnelImportResultDialog.vue`
- Modify: `frontend-vue/src/services/admin.js`
- Modify: `frontend-vue/src/services/auth.js`
- Modify: `frontend-vue/src/services/auth.register.test.js`
- Modify: `frontend-vue/src/views/Register.vue`
- Modify: `frontend-vue/src/views/Register.structure.test.js`
- Modify: `frontend-vue/src/views/UserProfile.vue`
- Modify: `frontend-vue/src/views/UserProfile.department-flow.test.js`
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`

### Gateway

- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`
- No code changes expected: `gateway/app/routers/public_proxy.py`
- No code changes expected: `gateway/app/services/route_table.py`

---

### Task 1: 落迁移、严格真源开关和 repository 基础能力

**Files:**
- Create: `highThinkingQA/server/database/migrations/20260420_02_personnel_department_source.sql`
- Modify: `public-service/backend/app/core/config.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/app/modules/personnel/repository.py`
- Test: `public-service/backend/tests/test_personnel_module.py`

- [ ] **Step 1: 先写 migration / settings / repository 侧失败测试**

在 `public-service/backend/tests/test_personnel_module.py` 增加至少这些测试：

```python
def test_personnel_department_migration_adds_three_department_columns_and_fks(): ...

def test_settings_expose_personnel_department_strict_source_flag(monkeypatch): ...

def test_auth_repository_can_sync_all_bound_user_departments_for_personnel(): ...

def test_auth_repository_can_clear_single_user_department_cache(): ...

def test_personnel_repository_lists_bound_department_triplets_for_backfill(): ...
```

关注点：

1. 新 migration 明确新增 `personnel_records.primary_department_id / secondary_department_id / tertiary_department_id`
2. `Settings` 明确暴露 `personnel_department_strict_source_enabled`
3. `AuthRepository` 有明确的方法：

```python
def sync_departments_for_personnel(...): ...
def clear_user_department_cache(...): ...
```

4. `PersonnelRepository` 能读取某个 `personnel_id` 绑定账号上的候选部门组合，用于回填预览

- [ ] **Step 2: 跑测试确认当前能力不存在**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py -k "strict_source or backfill or sync_all_bound or department_migration" -v
```

Expected:

1. migration 文件不存在
2. settings 没有严格真源开关
3. repository 不具备批量同步 / 清空缓存 / 回填候选读取能力

- [ ] **Step 3: 实现 migration、settings 和 repository**

`20260420_02_personnel_department_source.sql` 要求：

1. 幂等新增 `personnel_records.primary_department_id`
2. 幂等新增 `personnel_records.secondary_department_id`
3. 幂等新增 `personnel_records.tertiary_department_id`
4. 为三列新增索引
5. 分别挂接到三级部门表外键

`config.py` 要求：

```python
personnel_department_strict_source_enabled: bool
```

环境变量建议：

```text
PERSONNEL_DEPARTMENT_STRICT_SOURCE_ENABLED=0
```

`auth/repository.py` 至少新增：

```python
def sync_departments_for_personnel(
    self,
    *,
    personnel_id: int,
    primary_department_id: int | None,
    secondary_department_id: int | None,
    tertiary_department_id: int | None,
) -> int: ...

def clear_user_department_cache(self, *, user_id: int) -> int: ...
```

`personnel/repository.py` 至少新增：

```python
def list_bound_department_candidates(self, *, personnel_id: int) -> list[dict[str, Any]]: ...
```

实现要求：

1. repository 只做 SQL 和 row mapping，不塞业务规则
2. `sync_departments_for_personnel()` 必须批量更新同一 `personnel_id` 名下全部账号
3. `clear_user_department_cache()` 只清空目标账号三列，不动 `personnel_id`

- [ ] **Step 4: 重跑 repository 相关测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py -k "strict_source or backfill or sync_all_bound or department_migration" -v
```

Expected:

1. migration / settings / repository 测试通过

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/database/migrations/20260420_02_personnel_department_source.sql \
  public-service/backend/app/core/config.py \
  public-service/backend/app/modules/auth/repository.py \
  public-service/backend/app/modules/personnel/repository.py \
  public-service/backend/tests/test_personnel_module.py
git commit -m "feat: add personnel department storage primitives"
```

### Task 2: 实现人员表部门字段、导入校验、账号同步和回填服务

**Files:**
- Modify: `public-service/backend/app/modules/personnel/schemas.py`
- Modify: `public-service/backend/app/modules/personnel/service.py`
- Modify: `public-service/backend/app/modules/personnel/api.py`
- Modify: `public-service/backend/app/modules/personnel/import_service.py`
- Create: `public-service/backend/app/modules/personnel/backfill_service.py`
- Create: `scripts/personnel_department_backfill.py`
- Test: `public-service/backend/tests/test_personnel_module.py`

- [ ] **Step 1: 先写 personnel service / import / backfill 失败测试**

在 `public-service/backend/tests/test_personnel_module.py` 增加至少这些测试：

```python
def test_create_personnel_requires_complete_three_level_department(): ...

def test_update_personnel_syncs_all_bound_users_when_department_changes(): ...

def test_import_personnel_requires_department_name_columns_and_syncs_existing_personnel(): ...

def test_personnel_payload_includes_department_display_fields(): ...

def test_backfill_service_reports_synced_missing_and_conflicting_personnel(): ...

def test_backfill_service_apply_updates_only_synced_items(): ...

def test_personnel_department_backfill_cli_reports_summary_and_exit_code(): ...
```

关注点：

1. create / update payload 带三级部门 ID
2. import 模板新增三级部门名称列
3. import upsert 时如果更新了人员部门，需要同步绑定账号
4. backfill 服务至少区分：
   - `synced`
   - `missing_department`
   - `conflicting_departments`
5. `apply()` 只允许写 `synced` 项，不能碰 `missing/conflicting`
6. CLI 的 `--dry-run / --apply` summary 与 exit code 必须可预测

- [ ] **Step 2: 跑测试确认当前 personnel 模块不满足新契约**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py -k "create_personnel_requires_complete_three_level_department or syncs_all_bound_users or import_personnel_requires_department_name_columns or backfill_service" -v
```

Expected:

1. schema 里没有部门字段
2. import 模板和解析还不认识部门列
3. 人员改部门不会同步账号
4. 不存在 backfill service

- [ ] **Step 3: 扩展 personnel schema / service / import / backfill**

`schemas.py` 目标：

```python
class PersonnelCreateRequest(BaseModel):
    employee_no: str
    full_name: str
    verification_code: str
    primary_department_id: int | None
    secondary_department_id: int | None
    tertiary_department_id: int | None
    status: Literal["active", "disabled"] | str = "active"
    remarks: str | None = None
```

`service.py` 目标：

1. create / update 都复用 `departments.validate_department_selection(... allow_empty=False, allow_legacy_two_level=False)`
2. `_build_personnel_payload()` 带出 `department_display` 等字段
3. 部门变化后调用 `AuthRepository.sync_departments_for_personnel(...)`

关键 helper 建议：

```python
def _validate_personnel_department(...): ...
def _sync_bound_user_departments(...): ...
def describe_personnel_department(...): ...
```

`import_service.py` 目标：

1. `REQUIRED_COLUMNS` 加入 `primary_department_name / secondary_department_name / tertiary_department_name`
2. 每行导入先用 `department_service.resolve_by_names(... allow_legacy_two_level=False)` 解析
3. 对 upsert 的每条人员，如果部门被修改，则同步绑定账号
4. 模板示例行改成完整三级部门

`backfill_service.py` 目标：

```python
class PersonnelDepartmentBackfillService:
    def preview(self) -> dict[str, Any]: ...
    def apply(self) -> dict[str, Any]: ...
```

规则：

1. 单一唯一完整部门组合 -> 自动写入 personnel
2. 无完整组合 -> 记 missing
3. 多组冲突 -> 记 conflict
4. apply 只写入可自动确定的记录，不猜测冲突项

`scripts/personnel_department_backfill.py` 目标：

1. `--dry-run` 输出 summary
2. `--apply` 真正执行可回填写入
3. 非 0 冲突时返回非 0 exit code，方便发布前检查

- [ ] **Step 4: 重跑 personnel 模块测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_personnel_module.py -v
```

Expected:

1. personnel 新增/编辑/导入/回填相关测试通过

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/personnel/schemas.py \
  public-service/backend/app/modules/personnel/service.py \
  public-service/backend/app/modules/personnel/api.py \
  public-service/backend/app/modules/personnel/import_service.py \
  public-service/backend/app/modules/personnel/backfill_service.py \
  scripts/personnel_department_backfill.py \
  public-service/backend/tests/test_personnel_module.py
git commit -m "feat: manage departments on personnel records"
```

### Task 3: 改 auth 读模型、注册契约、自助绑定同步和旧部门接口封禁

**Files:**
- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/tests/test_auth_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: 先写 auth 失败测试**

在 `public-service/backend/tests/test_auth_module.py` 增加至少这些测试：

```python
def test_register_contract_drops_department_fields_and_uses_personnel_department(): ...

def test_register_rejects_personnel_without_complete_department(): ...

def test_self_personnel_binding_syncs_department_cache(): ...

def test_self_personnel_binding_rejects_personnel_without_complete_department(): ...

def test_login_uses_legacy_department_fallback_when_strict_flag_disabled(): ...

def test_legacy_department_fallback_only_uses_existing_complete_user_cache(): ...

def test_login_requires_department_setup_when_strict_flag_enabled_and_personnel_department_missing(): ...

def test_admin_login_and_me_remain_exempt_from_personnel_department_rules(): ...

def test_update_department_is_rejected_for_api_and_v1_routes(): ...
```

在 `public-service/backend/tests/test_route_surface.py` 锁定：

1. `/api/auth/register` 与 `/api/v1/auth/register` 同时存在
2. `/api/auth/department` 与 `/api/v1/auth/department` 同时存在
3. 两条 route 的契约变更必须同步

- [ ] **Step 2: 跑 auth 测试确认当前行为仍是旧逻辑**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -k "register or department or personnel_binding or strict_flag" -v
```

Expected:

1. register 仍要求部门字段
2. 绑定人员不会同步部门
3. `PUT /auth/department` 仍能成功写库
4. strict flag 相关读模型行为不存在

- [ ] **Step 3: 改 auth schema / api / service**

`auth/schemas.py`：

1. 从 `RegisterRequest` 移除三级部门字段
2. `DepartmentUpdateRequest` 保留结构体，供旧路由返回明确拒绝错误

`auth/api.py`：

1. `/api/auth/register` 与 `/api/v1/auth/register` 都只向 service 传：
   - `username`
   - `password`
   - `employee_no`
   - `full_name`
   - `verification_code`
   - `security_questions`
2. `/api/auth/department` 与 `/api/v1/auth/department` 都走统一 rejection branch

`auth/service.py`：

建议新增 helper：

```python
def _resolve_effective_department_payload(self, *, user: dict[str, Any]) -> dict[str, Any]: ...
def _personnel_department_payload(self, *, personnel_id: int | None) -> dict[str, Any]: ...
def _build_department_payload_with_fallback(self, *, user: dict[str, Any]) -> dict[str, Any]: ...
def _may_use_legacy_department_fallback(self, *, user: dict[str, Any], personnel_department: dict[str, Any]) -> bool: ...
```

实现要求：

1. 新注册账号从人员记录读取部门写入 `create_registered_user(...)`
2. `update_personnel_binding()` 成功后同步用户部门缓存
3. `login()` / `get_user_info()` 对非管理员账号使用：
   - strict flag `false` 时：允许存量 fallback
   - strict flag `true` 时：按 personnel 严格判定
4. fallback 判定必须写死为“已有完整 `users` 部门缓存但绑定人员主档暂缺部门”的迁移态，不能做成 `strict=false` 就对全部非管理员统一回退
5. 新注册账号、新绑定/改绑账号永远不能走 fallback；如果目标人员缺少完整部门，注册/绑定必须直接失败
6. 管理员账号在 strict/fallback 两种模式下都继续豁免部门与人员阻断
7. `update_department()` 返回：

```python
{"success": False, "error": "部门由人员信息维护，请联系管理员或修改绑定人员", "code": "DEPARTMENT_MANAGED_BY_PERSONNEL"}
```

8. 两条 auth register route 和两条 auth department route 行为完全一致

- [ ] **Step 4: 重跑 auth 测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -v
```

Expected:

1. register / me / login / self-bind / old endpoint rejection 全部通过

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/auth/schemas.py \
  public-service/backend/app/modules/auth/api.py \
  public-service/backend/app/modules/auth/service.py \
  public-service/backend/tests/test_auth_module.py \
  public-service/backend/tests/test_route_surface.py
git commit -m "feat: source auth departments from personnel"
```

### Task 4: 改 admin_users 后端，移除账号级部门写入口并收口用户导入

**Files:**
- Modify: `public-service/backend/app/modules/admin_users/schemas.py`
- Modify: `public-service/backend/app/modules/admin_users/api.py`
- Modify: `public-service/backend/app/modules/admin_users/service.py`
- Modify: `public-service/backend/app/modules/admin_users/import_service.py`
- Test: `public-service/backend/tests/test_admin_users_module.py`

- [ ] **Step 1: 先写 admin_users 失败测试**

在 `public-service/backend/tests/test_admin_users_module.py` 增加至少这些测试：

```python
def test_admin_create_user_no_longer_accepts_department_fields(): ...

def test_admin_update_department_is_rejected_with_department_managed_by_personnel(): ...

def test_admin_bind_personnel_syncs_department_cache(): ...

def test_admin_unbind_personnel_clears_department_cache(): ...

def test_user_import_template_drops_department_columns(): ...
```

关注点：

1. 新增用户只收 `username / password / user_type`
2. 旧的账号级部门更新接口明确拒绝
3. 代绑 / 解绑人员后部门缓存跟着同步或清空
4. 用户导入模板不再暴露部门列

- [ ] **Step 2: 跑 admin_users 测试确认当前行为仍旧**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py -k "department_managed_by_personnel or bind_personnel or import_template" -v
```

Expected:

1. create/import 仍接受部门
2. admin 仍可直接改用户部门
3. 代绑/解绑人员还不联动部门缓存

- [ ] **Step 3: 改 admin_users schema / service / import**

`schemas.py`：

1. 从 `UserCreateRequest` 移除三级部门字段
2. `UserDepartmentUpdateRequest` 保留，仅用于旧入口 rejection

`service.py`：

1. `create_user()` 不再解析部门
2. `update_user_personnel_binding()` 成功后调用 `AuthRepository.sync_departments_for_personnel(...)`
3. `clear_user_personnel_binding()` 成功后调用 `clear_user_department_cache(...)`
4. `update_department()` 改成统一返回 `DEPARTMENT_MANAGED_BY_PERSONNEL`

`import_service.py`：

1. 模板移除 `primary_department_name / secondary_department_name / tertiary_department_name`
2. 导入逻辑不再解析部门列
3. 仍维持 username/password/user_type 批量导入

- [ ] **Step 4: 重跑 admin_users 测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_admin_users_module.py -v
```

Expected:

1. 用户管理后端全部跟新模型一致

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/admin_users/schemas.py \
  public-service/backend/app/modules/admin_users/api.py \
  public-service/backend/app/modules/admin_users/service.py \
  public-service/backend/app/modules/admin_users/import_service.py \
  public-service/backend/tests/test_admin_users_module.py
git commit -m "feat: remove account-level department writes from admin users"
```

### Task 5: 改人员管理前端，增加部门表单和导入展示

**Files:**
- Create: `frontend-vue/src/components/PersonnelEditorDialog.vue`
- Modify: `frontend-vue/src/components/PersonnelManagementPanel.vue`
- Modify: `frontend-vue/src/components/PersonnelImportResultDialog.vue`
- Modify: `frontend-vue/src/components/PersonnelManagementPanel.structure.test.js`
- Modify: `frontend-vue/src/services/admin.js`

- [ ] **Step 1: 先写前端结构失败测试**

在 `frontend-vue/src/components/PersonnelManagementPanel.structure.test.js` 增加至少这些断言：

```js
test('PersonnelEditorDialog reuses DepartmentSelector with searchable department selection', () => ...)
test('PersonnelManagementPanel submits primary secondary tertiary department ids on create/update', () => ...)
test('PersonnelManagementPanel shows personnel department display in list rows', () => ...)
test('PersonnelImportResultDialog renders imported department columns', () => ...)
```

- [ ] **Step 2: 跑结构测试确认当前 prompt UI 不满足需求**

Run:

```bash
cd frontend-vue && node --test src/components/PersonnelManagementPanel.structure.test.js
```

Expected:

1. 当前没有 `PersonnelEditorDialog`
2. 当前 create/edit 还依赖 `window.prompt`
3. 当前导入结果没有部门字段展示

- [ ] **Step 3: 实现人员管理前端**

`PersonnelEditorDialog.vue` 要求：

1. 字段：
   - `employee_no`（新增时可编辑，编辑时只读）
   - `full_name`
   - `verification_code`
   - `status`
   - `remarks`
   - `DepartmentSelector`
2. 使用现有搜索能力选择三级部门
3. 输出 payload：

```js
{
  employee_no,
  full_name,
  verification_code,
  status,
  remarks,
  primary_department_id,
  secondary_department_id,
  tertiary_department_id,
}
```

`PersonnelManagementPanel.vue` 要求：

1. 去掉 `window.prompt` 新增/编辑
2. 用 dialog 打开 create / edit
3. 列表增加 `department_display`
4. 成功创建/编辑后 refresh + emit updated

`PersonnelImportResultDialog.vue` 要求：

1. 结果表格显示三级部门名称或 `department_display`
2. 失败原因里能看出部门解析失败

`admin.js` 要求：

1. `createPersonnel(payload)` / `updatePersonnel(payload)` 原样透传部门 ID
2. 保持现有导入 / 模板下载 API

- [ ] **Step 4: 重跑前端结构测试**

Run:

```bash
cd frontend-vue && node --test src/components/PersonnelManagementPanel.structure.test.js
```

Expected:

1. 人员管理结构测试通过

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/components/PersonnelEditorDialog.vue \
  frontend-vue/src/components/PersonnelManagementPanel.vue \
  frontend-vue/src/components/PersonnelImportResultDialog.vue \
  frontend-vue/src/components/PersonnelManagementPanel.structure.test.js \
  frontend-vue/src/services/admin.js
git commit -m "feat: manage personnel departments from admin panel"
```

### Task 6: 改注册页、个人中心、管理员用户管理前端，移除账号级部门入口

**Files:**
- Modify: `frontend-vue/src/views/Register.vue`
- Modify: `frontend-vue/src/views/Register.structure.test.js`
- Modify: `frontend-vue/src/services/auth.js`
- Modify: `frontend-vue/src/services/auth.register.test.js`
- Modify: `frontend-vue/src/views/UserProfile.vue`
- Modify: `frontend-vue/src/views/UserProfile.department-flow.test.js`
- Modify: `frontend-vue/src/views/AdminDashboard.vue`
- Modify: `frontend-vue/src/views/AdminDashboard.department-management.test.js`
- Modify: `frontend-vue/src/services/admin.js`

- [ ] **Step 1: 先写前端失败测试**

更新以下测试：

```js
// Register.structure.test.js
test('Register no longer renders department section or DepartmentSelector', () => ...)

// auth.register.test.js
test('authApi.register posts username password personnel and security questions only', async () => ...)

// UserProfile.department-flow.test.js
test('UserProfile renders department as read-only and shows admin-contact warning when require_department_setup=true', () => ...)

// AdminDashboard.department-management.test.js
test('AdminDashboard removes account-level department edit UI and create-user department selector', () => ...)
```

- [ ] **Step 2: 跑前端测试确认当前页面仍保留旧入口**

Run:

```bash
cd frontend-vue && node --test \
  src/views/Register.structure.test.js \
  src/services/auth.register.test.js \
  src/views/UserProfile.department-flow.test.js \
  src/views/AdminDashboard.department-management.test.js
```

Expected:

1. Register 仍带部门区块
2. UserProfile 仍带部门编辑按钮和保存逻辑
3. AdminDashboard 仍带账号级部门表单

- [ ] **Step 3: 实现前端去入口和只读阻断**

`Register.vue`：

1. 删除部门状态、`fetchDepartmentTree()`、`DepartmentSelector`
2. 调用 `authApi.register()` 时不再传三级部门字段
3. 文案改成“账号、人员校验、安全问题”

`services/auth.js`：

1. `register(payload)` 发给后端的 body 不再包含任何 `*_department_id`

`UserProfile.vue`：

1. 保留部门展示卡片，但改成只读
2. 删除部门编辑表单和保存按钮
3. 当 `require_department_setup === true` 时显示：

```text
当前绑定人员的部门信息未配置完成，请联系管理员在人员表中维护后再继续使用。
```

`AdminDashboard.vue`：

1. 新增用户弹窗删除部门字段
2. 删除“修改用户部门”入口
3. 用户列表的部门只读展示仍保留

`services/admin.js`：

1. 账号级部门写接口保留但前端不再调用
2. 用户创建 / 批量导入的 payload 与模板下载文案同步去部门化

- [ ] **Step 4: 重跑前端测试并构建**

Run:

```bash
cd frontend-vue && node --test \
  src/views/Register.structure.test.js \
  src/services/auth.register.test.js \
  src/views/UserProfile.department-flow.test.js \
  src/views/AdminDashboard.department-management.test.js
```

Run:

```bash
cd frontend-vue && npm run build
```

Expected:

1. 前端结构测试全部通过
2. Vite build 成功

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/views/Register.vue \
  frontend-vue/src/views/Register.structure.test.js \
  frontend-vue/src/services/auth.js \
  frontend-vue/src/services/auth.register.test.js \
  frontend-vue/src/views/UserProfile.vue \
  frontend-vue/src/views/UserProfile.department-flow.test.js \
  frontend-vue/src/views/AdminDashboard.vue \
  frontend-vue/src/views/AdminDashboard.department-management.test.js \
  frontend-vue/src/services/admin.js
git commit -m "feat: remove account-level department editing from frontend"
```

### Task 7: 锁定 gateway parity、跑回填 dry-run 和全量验证

**Files:**
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_route_table.py`
- Verify: `scripts/personnel_department_backfill.py`

- [ ] **Step 1: 先补 gateway / rollout 验证测试**

在 gateway tests 中明确锁定：

```python
def test_auth_register_routes_keep_api_and_v1_parity(): ...
def test_auth_department_routes_keep_api_and_v1_parity_even_when_rejected(): ...
```

这些测试不要求新增路由，只要求已有 route surface 在语义上仍同步存在。

- [ ] **Step 2: 跑 gateway 测试**

Run:

```bash
conda run -n agent pytest gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py -v
```

Expected:

1. gateway route surface 测试通过

- [ ] **Step 3: 跑 backfill dry-run**

Run:

```bash
conda run -n agent python scripts/personnel_department_backfill.py --dry-run
```

Expected:

1. 输出 `synced / missing / conflicting` summary
2. 如果 `conflicting > 0`，退出码非 0

- [ ] **Step 4: 跑最终验证**

Run:

```bash
conda run -n agent pytest \
  public-service/backend/tests/test_personnel_module.py \
  public-service/backend/tests/test_auth_module.py \
  public-service/backend/tests/test_admin_users_module.py \
  public-service/backend/tests/test_route_surface.py \
  gateway/tests/test_public_proxy.py \
  gateway/tests/test_route_table.py -v
```

Run:

```bash
cd frontend-vue && npm run build
```

Expected:

1. 后端测试通过
2. gateway 测试通过
3. 前端 build 成功
4. dry-run 报告可用于发布前人工检查

- [ ] **Step 5: Commit**

```bash
git add gateway/tests/test_public_proxy.py \
  gateway/tests/test_route_table.py
git commit -m "test: lock personnel department source rollout parity"
```

---

## Rollout Notes

1. 首次部署时保持 `PERSONNEL_DEPARTMENT_STRICT_SOURCE_ENABLED=0`。
2. 先让新增/编辑/导入人员、注册、绑定链路都按新模型写对。
3. 执行 `scripts/personnel_department_backfill.py --dry-run`，处理 `missing / conflicting` 记录。
4. 确认冲突清零后，再执行 `--apply`。
5. 前端去掉旧入口和后端封禁旧入口必须同一阶段发布。
6. 最后再把 `PERSONNEL_DEPARTMENT_STRICT_SOURCE_ENABLED=1`，开启严格真源判定。

## Review Checklist

1. 人员表是否成了唯一部门写入口
2. 用户表部门字段是否只由同步逻辑写入
3. 注册、自助绑定、管理员代绑是否都同步部门缓存
4. 人员改部门是否同步全部绑定账号
5. 旧的账号级部门入口是否前后端一起收口
6. `/api/*` 与 `/api/v1/*` 契约是否保持一致
7. 迁移期 fallback 是否只对存量账号生效
8. backfill 报告是否能区分 synced / missing / conflicting
