# auth 模块代码细读

模块路径：
- `backend/app/modules/auth/api.py`
- `backend/app/modules/auth/deps.py`
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/auth/schemas.py`
- `frontend-vue/src/api/auth.js`
- `frontend-vue/src/services/auth.js`
- `frontend-vue/src/features/auth/composables/useAuthSession.js`
- `frontend-vue/src/views/Login.vue`
- `frontend-vue/src/views/ForgotPassword.vue`
- `frontend-vue/src/views/UserProfile.vue`
- `frontend-vue/src/router/index.js`
- `backend/tests/test_auth.py`

模块定位：
- 明确属于公共能力
- 负责整站统一认证、口令安全、账户状态、首次安全初始化
- 通过 dependency 把统一身份上下文输送给 `quota`、`documents`、`conversation`、`uploads`、`admin_users`

已细拆到：

- `auth/README.md`
- `auth/01-api-and-token-model.md`
- `auth/02-password-policy-and-account-state.md`
- `auth/03-repository-schema-compat.md`
- `auth/04-first-login-security-questions-and-reset.md`
- `auth/05-frontend-session-and-compat-notes.md`
- `auth/06-dependencies-and-integration-points.md`

本模块的关键结论：

- token 不是 JWT，而是 `itsdangerous.URLSafeTimedSerializer` 签名票据
- `require_auth_context()` 解 token 后一定回库校验用户存在且 `status == active`
- 注册成功并不等于普通登录完成，而是自动进入“首次改密 + 设置安全问题”的安全初始化流程
- 密码历史、失败锁定、密码过期、安全问题都依赖 `users` 的可选列与附属表存在，repository 做了明显的多版本 schema 兼容
- 前端当前存在两套 auth 接入面和两套本地存储 key，兼容层复杂度高于后端接口本身

当前已确认问题与迁移修复点：

- `P1` `AuthService.set_security_questions()` 调用 `replace_security_questions()` 后直接返回成功，但 repository 在 `user_security_questions` 表不存在时会静默返回；这会造成“前端显示设置成功，但后续读取仍无安全问题”的假成功状态。
- `P2` 前端登录态存在两套本地存储键：
  - 新 composable 使用 `agentcode.auth.token.v1 / agentcode.auth.user.v1`
  - 旧页面、旧服务和路由守卫仍大量读取 `token / user`
- 上面这个问题不是文档层面的兼容提醒，而是已经体现在源码中的真实状态分裂风险；后续抽成独立公共后端时，应同时收口前端 session key，否则会把认证兼容债务一起带走。
- 这两个问题分别对应：
  - `auth/service.py` 与 `auth/repository.py` 的安全问题落库链
  - `frontend-vue/src/features/auth/composables/useAuthSession.js`、`frontend-vue/src/router/index.js`、`frontend-vue/src/services/auth.js` 等前端调用面

建议阅读顺序：

1. 先看 `auth/01-api-and-token-model.md`
2. 再看 `auth/02-password-policy-and-account-state.md`
3. 然后看 `auth/03-repository-schema-compat.md`
4. 如果要理解前端为什么会强制跳 `/profile`，继续看 `auth/04-first-login-security-questions-and-reset.md` 和 `auth/05-frontend-session-and-compat-notes.md`
