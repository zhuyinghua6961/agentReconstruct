# auth 接口面与 token 模型

对应代码：
- `backend/app/modules/auth/api.py`
- `backend/app/modules/auth/deps.py`
- `backend/app/modules/auth/schemas.py`
- `backend/app/modules/auth/service.py`
- `backend/tests/test_auth.py`

## 1. 对外接口面

`api.py` 暴露了两套路由前缀：

- `/api/v1/auth/...`
- `/api/auth/...`

覆盖接口：

- `POST /auth/login`
- `POST /auth/register`
- `GET /auth/me`
- `PUT|POST /auth/password`
- `POST /auth/forgot-password/initiate`
- `POST /auth/forgot-password/verify`
- `GET /auth/security-questions`
- `PUT|POST /auth/security-questions`

这说明 `auth` 自身就承担了接口兼容层，而不是只服务单一版本客户端。

## 2. schema 层非常薄，核心校验都在 service

`schemas.py` 只定义了：

- `LoginRequest`
- `RegisterRequest`
- `ChangePasswordRequest`
- `ForgotPasswordInitiateRequest`
- `ForgotPasswordVerifyRequest`
- `SecurityQuestionItem`
- `SetSecurityQuestionsRequest`

这些 schema 的字段基本都给了空字符串或空数组默认值。

实际含义是：
- FastAPI/Pydantic 只负责把 body 结构接进来
- 用户名长度、密码强度、答案数量、问题数量这些规则，绝大多数都延后到 `AuthService` 判定

因此，auth 的真实契约中心不在 schema，而在 service 返回的 payload 和 `status_code_for()`。

## 3. 状态码由 service 统一映射

`api.py` 的 `_respond()` 不直接判断错误类型，而是调用：

- `auth_service.status_code_for(result, ok_status=...)`

映射结果比较明确：

- `VALIDATION_ERROR` -> `400`
- 密码强度/密码复用/答案错误/无安全问题 -> `400`
- `INVALID_CREDENTIALS`、`TOKEN_MISSING`、`TOKEN_INVALID` -> `401`
- `USER_NOT_FOUND` -> `404`
- `ACCOUNT_DISABLED` -> `403`
- `ACCOUNT_LOCKED`、`ACCOUNT_LOCKED_DUE_TO_FAILURES` -> `423`
- `USERNAME_EXISTS` -> `409`
- `DB_UNAVAILABLE` -> `503`
- 其他未识别错误 -> `500`

这里有两个边界值得记：

- 登录失败并不只有 `401`，被锁定时会落到 `423`
- 安全问题答案错误不是 `401`，而是业务校验错误 `400`

## 4. token 不是 JWT，而是签名票据

`TokenService` 使用：

- `itsdangerous.URLSafeTimedSerializer`

而不是 JWT 库。

配置项：

- `JWT_SECRET`
- `JWT_EXPIRE_SECONDS`，默认 `86400`

固定 salt：

- `agentcode.auth.access`

token payload 只有：

- `user_id`
- `role`
- `iat`

这意味着：

- token 不包含复杂 claim
- 没有标准 JWT header/payload 结构
- 过期检查完全依赖 `URLSafeTimedSerializer.loads(..., max_age=...)`

因此如果后续要拆独立认证服务，不能把当前 token 误当成 JWT 去对接。

## 5. token 读取方式支持 header 和 query

`get_bearer_token()` 同时支持：

- `Authorization: Bearer <token>`
- query 参数 `?token=<token>`

这不是多余兼容，而是整站里真实被使用的能力。

其中 query token 的价值主要体现在：

- 浏览器直接访问受保护资源 URL
- `view_pdf` 这类不方便携带自定义 header 的场景

所以 auth 模块本身虽然是“登录能力”，但它的 token 提取方式已经在服务静态资源访问场景。

## 6. require_auth_context 的实际语义

`require_auth_context()` 不是“token 解开就放行”，它还会继续：

1. 解 token
2. 读取 `user_id`
3. 回库查 `users`
4. 检查用户是否存在
5. 检查 `status == active`

失败时抛：

- `TOKEN_MISSING`
- `TOKEN_INVALID`
- `USER_NOT_FOUND`
- `ACCOUNT_DISABLED`

这说明权限判断最终以数据库当前状态为准，而不是只信任 token 里的历史信息。

## 7. optional auth 和 admin auth 的边界

`get_optional_auth_context()`：

- 没 token 返回 `None`
- token 无效返回 `None`
- 用户不存在或非 active 也返回 `None`

这适合“匿名可访问，但登录用户可增强”的路由。

`require_admin_context()`：

- 依赖 `require_auth_context()`
- 只接受 `context.role == admin`

注意这里没有把 `super` 视为管理员路由角色。

因此：
- `user_type == 2`
- 或 `role == super`

并不会天然拿到管理员接口权限。

## 8. `/me` 是全站会话真相源

`GET /auth/me` 最终调用：

- `auth_service.get_user_info(user_id)`

它会返回经过 `_build_user_payload()` 归一后的用户信息，包括：

- `id`
- `username`
- `role`
- `user_type`
- `status`
- `is_first_login`
- `has_security_questions`
- `require_security_questions_setup`
- `created_at`

这组字段不是普通“个人资料”，而是前端路由守卫和强制安全流程的控制信号。

也就是说：
- `/login` 返回 token 和初始标记
- `/me` 返回当前数据库状态下的最终控制位

## 9. 当前没有真正的 logout 接口

后端 `auth/api.py` 没有 `/logout`。

前端 `services/auth.js` 的 `logout()` 只是本地成功：

- 返回 `{ success: true }`
- 真正动作是页面侧清 `localStorage`

因此当前登录态模型本质上是：

- 有状态用户数据存在 MySQL
- 无状态 access token 保存在客户端
- 服务端没有 token 黑名单，也没有会话注销表

## 10. 测试覆盖的契约重点

`backend/tests/test_auth.py` 主要固定了这些事实：

- `/api/v1/auth/*` 和 `/api/auth/*` 两套路由都必须注册
- `/me`、`/password`、`/security-questions` 都必须挂 `require_auth_context`
- `register()` 返回体必须带首次登录和安全问题强制标记
- `set_security_questions()` 的请求体会被转成 `list[dict]` 传给 service
- `require_auth_context(None)` 必须报 `TOKEN_MISSING`

测试并没有覆盖完整登录成功/失败全路径，但它已经把最关键的公共契约钉住了：

- 双前缀兼容
- 认证依赖强制性
- 首次安全流程标记
