# auth 细拆索引

对应代码：
- `backend/app/modules/auth/api.py`
- `backend/app/modules/auth/deps.py`
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/auth/schemas.py`
- `frontend-vue/src/api/auth.js`
- `frontend-vue/src/services/auth.js`
- `frontend-vue/src/features/auth/composables/useAuthSession.js`
- `frontend-vue/src/features/auth/components/AuthBar.vue`
- `frontend-vue/src/views/Login.vue`
- `frontend-vue/src/views/ForgotPassword.vue`
- `frontend-vue/src/views/UserProfile.vue`
- `frontend-vue/src/router/index.js`
- `backend/tests/test_auth.py`

本目录把 `auth` 再拆成 6 个视角：

- `01-api-and-token-model.md`
  说明认证接口、HTTP 状态语义、token 载荷和依赖注入模型。
- `02-password-policy-and-account-state.md`
  说明密码哈希、强度策略、锁定策略、密码过期和登录/改密状态流转。
- `03-repository-schema-compat.md`
  说明 `users` 表、可选字段、密码历史表、安全问题表以及 schema 兼容降级方式。
- `04-first-login-security-questions-and-reset.md`
  说明首次登录强制流程、安全问题维护、忘记密码两阶段重置和实际退化边界。
- `05-frontend-session-and-compat-notes.md`
  说明前端两套认证调用面、本地存储 key 差异、路由守卫和页面级兼容逻辑。
- `06-dependencies-and-integration-points.md`
  说明 auth 在后端里的上下游依赖关系，以及它如何向 quota、documents、conversation、admin_users 提供统一身份基础。

总体判断：
- `auth` 是明确的公共能力，不是业务功能。
- 它真正提供的是统一身份、口令安全、账户状态和强制安全流程。
- 当前最大的复杂度不在接口数量，而在三层兼容同时存在：
  - token 不是 JWT，而是 `itsdangerous` 签名票据
  - 后端 repository 兼容多版数据库 schema
  - 前端同时保留新旧两套认证封装与本地存储 key

当前已确认问题：
- 安全问题设置在缺少 `user_security_questions` 表时可能“返回成功但未实际写入”。
- 前端登录态同时使用 `agentcode.auth.*` 和 `token/user` 两套本地存储 key。
