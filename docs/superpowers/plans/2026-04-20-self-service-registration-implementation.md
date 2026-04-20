# Self-Service Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为当前 `frontend-vue -> gateway -> public-service` 体系新增可上线的用户自助注册流程，使用户在独立 `/register` 页面一次性完成账号、三级部门、人员绑定和安全问题设置，注册成功后自动登录进入系统，并且不再触发首次登录/资料补全拦截。

**Architecture:** 后端继续沿用现有 `/api/auth/register` 与 `/api/v1/auth/register` 路径，但把注册请求体扩成完整资料提交，并在 `auth` 模块内新增专用的原子化注册写入路径，统一完成用户创建、部门写入、人员绑定、安全问题写入和 token 签发。前端新增独立 `Register.vue` 页面，复用现有 `DepartmentSelector` 和安全问题交互模型；登录页只增加入口，路由守卫把 `/register` 视为游客页，同时保证已完成资料的注册用户不会再被拦到 `/profile`。

**Tech Stack:** FastAPI, Pydantic, MySQL repository layer, gateway public proxy verification, Vue 3 + Vite, node:test, pytest, `conda run -n agent`

---

## Requirements Snapshot

1. 登录页新增“注册账号”入口，跳转独立 `/register` 页面。
2. 注册页必须一次性收集：
   - `username`
   - `password`
   - `confirm_password`
   - `primary_department_id`
   - `secondary_department_id`
   - `tertiary_department_id`
   - `employee_no`
   - `full_name`
   - `verification_code`
   - `security_questions[1..3]`
3. 注册密码规则必须复用当前非管理员改密码规则：
   - 长度至少 8
   - 数字 / 小写 / 大写 / 特殊符号四类中至少三类
4. 注册部门选择必须是完整三级部门，且三层都必须有效并启用。
5. 注册人员绑定必须复用当前 `employee_no + full_name + verification_code` 校验，并要求人员状态为 `active`。
6. 注册成功创建的账号必须是：
   - `role='user'`
   - `user_type=2`
   - `status='active'`
   - `is_first_login=false`
   - `must_set_security_questions=false`
7. 注册成功后用户状态必须满足：
   - `has_security_questions=true`
   - `require_security_questions_setup=false`
   - `require_department_setup=false`
   - `require_personnel_setup=false`
8. 注册成功返回的 `data.user` 必须已经是完整可用态，至少包含：
   - `id`
   - `username`
   - `role='user'`
   - `user_type=2`
   - `primary_department_id`
   - `primary_department_name`
   - `secondary_department_id`
   - `secondary_department_name`
   - `tertiary_department_id`
   - `tertiary_department_name`
   - `department_completion_level='complete'`
   - `require_department_setup=false`
   - `personnel_id`
   - `employee_no`
   - `full_name`
   - `personnel_binding_status='bound_active'`
   - `require_personnel_setup=false`
   - `has_security_questions=true`
   - `require_security_questions_setup=false`
   - `is_first_login=false`
9. 注册成功后直接返回 token 并自动登录跳转 `/`。
10. 注册失败不得留下半成品账号；用户创建、部门写入、人员绑定、安全问题写入必须原子化。
11. `/api/auth/register` 与 `/api/v1/auth/register` 必须保持相同请求体和相同行为。

## File Map

### Backend

- Modify: `public-service/backend/app/modules/auth/schemas.py`
  - 扩展 `RegisterRequest` 为完整注册请求体。
- Modify: `public-service/backend/app/modules/auth/api.py`
  - 让 `/api/auth/register` 与 `/api/v1/auth/register` 都走完整注册入参。
- Modify: `public-service/backend/app/modules/auth/service.py`
  - 重写注册逻辑，复用用户名、密码、部门、人员、安全问题校验，并返回“资料完整态”的登录 payload。
- Modify: `public-service/backend/app/modules/auth/repository.py`
  - 新增专用的原子化注册写入方法，避免多次独立提交造成半成品账号。
- Modify: `public-service/backend/tests/test_auth_module.py`
  - 覆盖注册契约、成功态、失败态、原子化写入路径。
- Modify: `public-service/backend/tests/test_route_surface.py`
  - 明确锁定 register public route surface。

### Gateway

- No code changes expected: `gateway/app/routers/public_proxy.py`
- No code changes expected: `gateway/app/services/route_table.py`

说明：
- 当前 gateway 已经代理 `/api/auth/register`，实现阶段只需要跑现有 gateway 测试确认没有被语义变更波及。

### Frontend Canonical Flow

- Modify: `frontend-vue/src/services/auth.js`
  - 扩展 `authApi.register()` 为完整注册 payload，并统一错误处理。
- Modify: `frontend-vue/src/views/Login.vue`
  - 增加“注册账号”入口。
- Modify: `frontend-vue/src/router/index.js`
  - 新增 `/register` 路由和 guest-only 跳转逻辑。
- Create: `frontend-vue/src/views/Register.vue`
  - 实现完整注册页面与自动登录流程。
- Create: `frontend-vue/src/views/Register.structure.test.js`
  - 锁定注册页字段、组件复用和提交流程结构。
- Create: `frontend-vue/src/services/auth.register.test.js`
  - 锁定 register 请求 payload 和错误处理。
- Create: `frontend-vue/src/router/register-route.test.js`
  - 锁定 `/register` 路由、登录页入口和已登录重定向规则。

### Frontend Compatibility Surface

- Modify: `frontend-vue/src/api/auth.js`
  - 把 `registerAuth()` 从旧的 `(username, password)` 签名对齐为完整 payload。
- Modify: `frontend-vue/src/features/auth/composables/useAuthSession.js`
  - 让辅助 composable 的 register 调用契约与 canonical `auth.js` 一致，避免保留过时签名。

## Task 1: 扩展后端注册请求契约与路由测试

**Files:**
- Modify: `public-service/backend/app/modules/auth/schemas.py`
- Modify: `public-service/backend/app/modules/auth/api.py`
- Modify: `public-service/backend/tests/test_auth_module.py`
- Modify: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: 先写 register route / contract 失败测试**

在 `public-service/backend/tests/test_auth_module.py` 增加至少这些测试：

```python
def test_auth_routes_registered_include_register_variants(): ...

def test_register_route_complete_profile_contract(monkeypatch):
    def fake_register(**kwargs):
        assert kwargs["username"] == "alice"
        assert kwargs["primary_department_id"] == 1
        assert kwargs["secondary_department_id"] == 11
        assert kwargs["tertiary_department_id"] == 111
        assert kwargs["employee_no"] == "T2024001"
        assert kwargs["full_name"] == "张三"
        assert kwargs["verification_code"] == "ABC123"
        assert kwargs["security_questions"] == [
            {"question": "我最喜欢的水果是什么？", "answer": "苹果"}
        ]
        return {
            "success": True,
            "message": "register_success",
            "data": {
                "token": "token-2",
                "user": {
                    "id": 2,
                    "username": "alice",
                    "role": "user",
                    "user_type": 2,
                    "primary_department_id": 1,
                    "primary_department_name": "计算机学院",
                    "secondary_department_id": 11,
                    "secondary_department_name": "软件工程系",
                    "tertiary_department_id": 111,
                    "tertiary_department_name": "软件工程教研室",
                    "department_completion_level": "complete",
                    "require_department_setup": False,
                    "personnel_id": 501,
                    "employee_no": "T2024001",
                    "full_name": "张三",
                    "personnel_binding_status": "bound_active",
                    "require_personnel_setup": False,
                    "has_security_questions": True,
                    "require_security_questions_setup": False,
                    "is_first_login": False,
                },
                "is_first_login": False,
                "has_security_questions": True,
                "require_security_questions_setup": False,
                "require_department_setup": False,
                "require_personnel_setup": False,
            },
        }
```

并补一条 route 层响应断言：`response.json()["data"]["user"]` 至少包含上述完整字段，不接受只返回 `{id, username, role, user_type}` 的精简 user。

同时在 `public-service/backend/tests/test_route_surface.py` 加一条断言，把以下路径加入 `expected`：

```python
"/api/auth/register"
"/api/v1/auth/register"
```

- [ ] **Step 2: 跑测试确认当前 register 契约还不满足**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -k "register" -v
```

Expected:
- `RegisterRequest` 缺少扩展字段
- `auth_api_module.register()` 仍然只把 `username`、`password` 传给 service
- 现有返回契约仍是首次登录补全态

- [ ] **Step 3: 扩展 schema 与 api 层**

在 `auth/schemas.py` 中把 `RegisterRequest` 改成完整形态：

```python
class RegisterRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")
    primary_department_id: int | None = Field(default=None)
    secondary_department_id: int | None = Field(default=None)
    tertiary_department_id: int | None = Field(default=None)
    employee_no: str = Field(default="")
    full_name: str = Field(default="")
    verification_code: str = Field(default="")
    security_questions: list[SecurityQuestionItem] = Field(default_factory=list)
```

在 `auth/api.py` 中让两条注册路由都按 named args 调用 service：

```python
def register(payload: RegisterRequest):
    return _respond(
        auth_service_module.auth_service.register(
            username=payload.username,
            password=payload.password,
            primary_department_id=payload.primary_department_id,
            secondary_department_id=payload.secondary_department_id,
            tertiary_department_id=payload.tertiary_department_id,
            employee_no=payload.employee_no,
            full_name=payload.full_name,
            verification_code=payload.verification_code,
            security_questions=[item.model_dump() for item in payload.security_questions],
        ),
        ok_status=201,
    )
```

- [ ] **Step 4: 重跑 register route / contract 测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -k "register" -v
```

Expected:
- route 与请求契约测试通过
- service 层的成功/失败业务测试仍然会在后续 task 中继续红

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/auth/schemas.py \
  public-service/backend/app/modules/auth/api.py \
  public-service/backend/tests/test_auth_module.py \
  public-service/backend/tests/test_route_surface.py
git commit -m "test: expand auth register contract coverage"
```

## Task 2: 实现后端原子化注册服务

**Files:**
- Modify: `public-service/backend/app/modules/auth/service.py`
- Modify: `public-service/backend/app/modules/auth/repository.py`
- Modify: `public-service/backend/tests/test_auth_module.py`

- [ ] **Step 1: 先写 auth service / repository 失败测试**

在 `public-service/backend/tests/test_auth_module.py` 增加至少这些测试：

```python
def test_auth_service_register_creates_super_user_with_completed_profile(): ...
def test_auth_service_register_rejects_incomplete_department_selection(): ...
def test_auth_service_register_rejects_invalid_personnel_identity(): ...
def test_auth_service_register_rejects_disabled_personnel(): ...
def test_auth_service_register_requires_1_to_3_security_questions(): ...
def test_auth_service_register_returns_username_exists_on_duplicate(): ...
def test_auth_service_register_uses_atomic_repository_path(monkeypatch): ...
def test_auth_repository_create_registered_user_rolls_back_on_security_question_failure(monkeypatch): ...
```

关键断言：

1. 成功注册返回的 `user.role == 'user'`
2. 成功注册返回的 `user.user_type == 2`
3. 成功注册返回的 `user.primary_department_id / name`、`user.secondary_department_id / name`、`user.tertiary_department_id / name` 都存在且与校验后的部门一致
4. 成功注册返回的 `user.department_completion_level == 'complete'`
5. 成功注册返回的 `user.require_department_setup is False`
6. 成功注册返回的 `user.personnel_id`、`user.employee_no`、`user.full_name` 与校验通过的人员记录一致
7. 成功注册返回的 `user.personnel_binding_status == 'bound_active'`
8. 成功注册返回的 `user.require_personnel_setup is False`
9. 成功注册返回的 `user.has_security_questions is True`
10. 成功注册返回的 `user.require_security_questions_setup is False`
11. 成功注册返回的 `user.is_first_login is False`
12. 顶层返回仍包含 `token`
13. 顶层返回的 `require_security_questions_setup / require_department_setup / require_personnel_setup / is_first_login` 也都为完整态
14. 顶层返回中明确不包含遗留字段 `require_password_change`
15. 注册必须通过单一 repository 原子化写入入口，不能继续串行调用多次会独立提交的旧方法
16. 仓储层在安全问题写入失败时必须整单回滚，`users` 表中不能残留新用户

- [ ] **Step 2: 跑测试确认现有注册逻辑仍是首次登录补全态**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py -k "register" -v
```

Expected:
- 现有 `register()` 仍返回 `is_first_login=True`
- 仍创建 `user_type=3`
- 不校验部门 / 人员 / 安全问题

- [ ] **Step 3: 在 service 层重构 register 逻辑**

在 `auth/service.py` 中把 `register()` 扩成完整签名：

```python
def register(
    self,
    *,
    username: str,
    password: str,
    primary_department_id: int | None,
    secondary_department_id: int | None,
    tertiary_department_id: int | None,
    employee_no: str,
    full_name: str,
    verification_code: str,
    security_questions: list[dict[str, Any]],
) -> dict[str, Any]:
    ...
```

实现要求：

1. 复用现有 `validate_username_candidate()`
2. 复用现有 `_validate_password_strength(password, role="user")`
3. 复用现有 `departments.validate_department_selection(... allow_empty=False, allow_legacy_two_level=False, require_active=True)`
4. 复用现有 `personnel.verify_personnel_identity()`
5. 不要直接调用现有 `set_security_questions()` 完成注册，因为它会走独立 repository 写入；应提取一个仅负责校验/规范化问题列表的内部 helper，例如：

```python
def _normalize_security_question_items(self, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ...
```

6. 成功创建用户时要求：

```python
role="user"
user_type=2
status="active"
is_first_login=False
must_set_security_questions=False
```

7. 成功返回的 payload 不能只返回最小 user 信息，必须显式组装完整可用态：

```python
{
    "success": True,
    "message": "register_success",
    "data": {
        "token": token,
        "user": {
            "id": user_id,
            "username": username,
            "role": "user",
            "user_type": 2,
            "primary_department_id": primary_department["id"],
            "primary_department_name": primary_department["name"],
            "secondary_department_id": secondary_department["id"],
            "secondary_department_name": secondary_department["name"],
            "tertiary_department_id": tertiary_department["id"],
            "tertiary_department_name": tertiary_department["name"],
            "department_completion_level": "complete",
            "require_department_setup": False,
            "personnel_id": personnel["id"],
            "employee_no": personnel["employee_no"],
            "full_name": personnel["full_name"],
            "personnel_binding_status": "bound_active",
            "require_personnel_setup": False,
            "has_security_questions": True,
            "require_security_questions_setup": False,
            "is_first_login": False,
        },
        "is_first_login": False,
        "has_security_questions": True,
        "require_security_questions_setup": False,
        "require_department_setup": False,
        "require_personnel_setup": False,
    },
}
```

8. 这个返回结构要与当前登录成功返回结构保持兼容，直接可供前端自动登录使用。
9. 外层返回体中不要继续带出遗留的 `require_password_change` 字段；测试里要显式断言该字段不存在。

- [ ] **Step 4: 在 repository 层增加单一原子化写入入口**

在 `auth/repository.py` 新增专用方法，不要把所有现有细粒度写入方法硬塞进事务上下文里复用；首版直接增加一个面向注册的事务化入口即可：

```python
def create_registered_user(
    self,
    *,
    username: str,
    password_hash: str,
    primary_department_id: int,
    secondary_department_id: int,
    tertiary_department_id: int,
    personnel_id: int,
    security_question_items: list[dict[str, Any]],
    user_type: int = 2,
) -> int:
    ...
```

事务内至少做这些写入：

1. `INSERT users (...)`
   - `role='user'`
   - `user_type=2`
   - `status='active'`
   - `is_first_login=0`
   - `must_set_security_questions=0`
   - 写入三级部门和 `personnel_id`
2. `INSERT password_history (...)`
3. `INSERT user_security_questions (...)` 1-3 行

实现约束：

1. 一旦安全问题写入失败，用户 insert 必须回滚
2. 唯一键冲突必须继续收敛成 `USERNAME_EXISTS`
3. 为事务回滚写一个明确测试点：模拟 `user_security_questions` 插入异常后，重新查询 `users.username` 必须查不到刚创建的用户名
4. 不要修改 `create_user()` 旧语义，避免影响管理员建用户与现有流程

- [ ] **Step 5: 重跑后端注册测试**

Run:

```bash
conda run -n agent pytest public-service/backend/tests/test_auth_module.py -k "register" -v
```

Expected:
- 注册成功态、失败态、原子写入路径测试通过

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/auth/service.py \
  public-service/backend/app/modules/auth/repository.py \
  public-service/backend/tests/test_auth_module.py
git commit -m "feat: implement atomic self-service registration"
```

## Task 3: 落前端 canonical 注册页与自动登录流程

**Files:**
- Modify: `frontend-vue/src/services/auth.js`
- Modify: `frontend-vue/src/views/Login.vue`
- Modify: `frontend-vue/src/router/index.js`
- Create: `frontend-vue/src/views/Register.vue`
- Create: `frontend-vue/src/views/Register.structure.test.js`
- Create: `frontend-vue/src/services/auth.register.test.js`
- Create: `frontend-vue/src/router/register-route.test.js`

- [ ] **Step 1: 先写前端失败测试**

创建 `frontend-vue/src/services/auth.register.test.js`，至少覆盖：

```javascript
test('authApi.register posts the complete self-service registration payload', async () => { ... })
test('authApi.register omits confirmPassword from the backend request body', async () => { ... })
test('authApi.register converts non-json error responses into structured failure payload', async () => { ... })
```

创建 `frontend-vue/src/views/Register.structure.test.js`，至少覆盖：

```javascript
test('Register renders account department personnel and security question sections', () => { ... })
test('Register reuses DepartmentSelector and authApi.register', () => { ... })
test('Register includes password confirmation and preset security question workflow', () => { ... })
```

创建 `frontend-vue/src/router/register-route.test.js`，至少覆盖：

```javascript
test('router includes /register route', () => { ... })
test('Login shows a register account entry that routes to /register', () => { ... })
test('router treats /register as guest-facing and redirects authenticated users away', () => { ... })
test('router validates token for /register via the same branch as /login before redirecting', async () => { ... })
```

- [ ] **Step 2: 跑测试确认当前前端没有注册页面**

Run:

```bash
cd frontend-vue
node --test src/services/auth.register.test.js src/views/Register.structure.test.js src/router/register-route.test.js
```

Expected:
- `/register` 页面和路由相关断言失败
- `authApi.register()` 仍只发送 `username/password`

- [ ] **Step 3: 实现 canonical 前端注册页**

在 `frontend-vue/src/services/auth.js` 中把注册接口改成完整 payload：

```javascript
async register(payload) {
  const submitPayload = {
    username: payload?.username ?? '',
    password: payload?.password ?? '',
    primary_department_id: payload?.primary_department_id ?? null,
    secondary_department_id: payload?.secondary_department_id ?? null,
    tertiary_department_id: payload?.tertiary_department_id ?? null,
    employee_no: payload?.employee_no ?? '',
    full_name: payload?.full_name ?? '',
    verification_code: payload?.verification_code ?? '',
    security_questions: Array.isArray(payload?.security_questions) ? payload.security_questions : [],
  }
  return fetchWithErrorHandling(`${API_BASE}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(submitPayload),
  })
}
```

这里的关键约束是：`confirmPassword` / `confirm_password` 只用于前端本地校验，禁止下发给后端；测试要显式锁定这一点。

在 `Register.vue` 中实现：

1. 账号区块：
   - `username`
   - `password`
   - `confirmPassword`
2. 部门区块：
   - 复用 `DepartmentSelector`
   - `allow-empty=false`
3. 人员区块：
   - `employeeNoInput`
   - `fullNameInput`
   - `verificationCodeInput`
4. 安全问题区块：
   - 复用当前个人中心的预置问题列表模型
   - 至少 1 个，最多 3 个
5. 提交前的最小前端校验：
   - 用户名非空
   - 密码非空
   - `password === confirmPassword`
   - 三级部门齐全
   - 工号/姓名/校验码齐全
   - 安全问题数量在 1-3 且每项有题目和答案
6. 提交成功后：
   - 保存 `token` 到 `token` 与 `agentcode.auth.token.v1`
   - 用 `persistStoredUser()` 保存用户对象
   - 直接 `window.location.href = '/'`

在 `router/index.js` 中：

1. 新增 `/register`
2. 不要只在 `/register` 的后置跳转逻辑里补特判；必须直接扩展现有 token 校验分支：

```javascript
if ((to.meta.requiresAuth || to.path === '/login' || to.path === '/register') && token) {
  ...
}
```

也就是说，`/register` 必须和 `/login` 一样先走 `authApi.getMe()` 校验与缓存分支，再做 guest-only 重定向，避免本地残留 token 或过期缓存绕过真实鉴权状态。
3. 如果已登录且资料完整，访问 `/register` 时跳：
   - 管理员 -> `/admin`
   - 非管理员 -> `/`
4. 如果已登录但 `hasRequiredProfileSetup(currentUser)` 为真，访问 `/register` 时仍跳 `/profile?...`

在 `Login.vue` 中新增“注册账号”入口，保持当前登录表单不变。

- [ ] **Step 4: 重跑前端测试与构建**

Run:

```bash
cd frontend-vue
node --test src/services/auth.register.test.js src/views/Register.structure.test.js src/router/register-route.test.js
npm run build
```

Expected:
- 新增注册相关测试通过
- 构建通过

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/services/auth.js \
  frontend-vue/src/views/Login.vue \
  frontend-vue/src/router/index.js \
  frontend-vue/src/views/Register.vue \
  frontend-vue/src/views/Register.structure.test.js \
  frontend-vue/src/services/auth.register.test.js \
  frontend-vue/src/router/register-route.test.js
git commit -m "feat: add canonical self-service registration page"
```

## Task 4: 对齐辅助认证封装并完成总验证

**Files:**
- Modify: `frontend-vue/src/api/auth.js`
- Modify: `frontend-vue/src/features/auth/composables/useAuthSession.js`

- [ ] **Step 1: 先写辅助封装契约失败测试**

如果当前仓库没有合适的现成测试文件，就在 `frontend-vue/src/services/auth.register.test.js` 追加一组 source/runtime 断言，覆盖：

```javascript
test('auxiliary auth helpers no longer expose a username-password-only register signature', async () => { ... })
```

最小断言目标：

1. `registerAuth()` 接收完整 payload
2. `useAuthSession().register()` 把完整 payload 原样传下去

- [ ] **Step 2: 跑测试确认辅助封装仍是旧签名**

Run:

```bash
cd frontend-vue
node --test src/services/auth.register.test.js
```

Expected:
- `frontend-vue/src/api/auth.js` 与 `useAuthSession.js` 的旧签名断言失败

- [ ] **Step 3: 对齐辅助认证封装**

在 `frontend-vue/src/api/auth.js` 中把：

```javascript
registerAuth(username, password)
```

改成：

```javascript
registerAuth(payload)
```

在 `frontend-vue/src/features/auth/composables/useAuthSession.js` 中把：

```javascript
const resp = await registerAuth(username, password)
```

改成：

```javascript
const resp = await registerAuth(payload)
```

不要在这个 task 里去接入 `AuthBar.vue`，因为它当前不在 canonical 路径上；本 task 只消除仓库内仍然导出的旧 register 函数签名。

- [ ] **Step 4: 跑总验证**

Run:

```bash
cd frontend-vue && node --test src/services/auth.register.test.js src/views/Register.structure.test.js src/router/register-route.test.js
cd /home/cqy/worktrees/highThinking && conda run -n agent pytest public-service/backend/tests/test_auth_module.py public-service/backend/tests/test_route_surface.py -v
cd /home/cqy/worktrees/highThinking && conda run -n agent pytest gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py -v
cd /home/cqy/worktrees/highThinking/frontend-vue && npm run build
```

Expected:
- 前端注册相关测试通过
- public-service 注册契约与行为测试通过
- gateway 现有 register 代理相关测试保持通过
- 前端构建通过

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/api/auth.js \
  frontend-vue/src/features/auth/composables/useAuthSession.js \
  frontend-vue/src/services/auth.register.test.js
git commit -m "chore: align auxiliary register helpers with canonical flow"
```
