# System/Auth/Quota 迁移核对清单

目的：
- 这份清单只服务于 `public-service/backend` 当前正在迁移的三个模块：
  - `system`
  - `auth`
  - `quota`
- 每个模块先从 `/home/cqy/worktrees/fastapi-version/backend/app/modules/...` 和已有拆分文档读出功能列表。
- 后续迁移只能按清单逐项核对，不能靠印象裁剪。

来源：
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/system/*`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/auth/*`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/quota/*`
- `/home/cqy/worktrees/public-service/public-modules/08-system.md`
- `/home/cqy/worktrees/public-service/public-modules/01-auth.md`
- `/home/cqy/worktrees/public-service/public-modules/03-quota.md`

---

## 1. system 功能清单

API 面：
- `GET /health`
- `GET /api/v1/health`
- `GET /api/background_status`
- `GET /api/v1/background_status`
- `GET /api/cache_debug/conversation`
- `GET /api/v1/cache_debug/conversation`
- `GET /api/kb_info`
- `GET /api/v1/kb_info`
- `POST /api/refresh_kb`
- `POST /api/v1/refresh_kb`
- `POST /api/clear_cache`
- `POST /api/v1/clear_cache`

能力项：
- health 返回组件状态、agent/runtime/vector 初始化状态、storage backend、qa cache metrics/config、timestamp
- background_status 返回：
  - current answer preview
  - conversation outbox 状态
  - upload processing 状态
  - 最新 background 文件
  - qa cache metrics/config
- kb_info 返回：
  - neo4j 节点数
  - chromadb 向量数
  - source_stats
- refresh_kb 能通过 runtime 的 `init_agent` 触发刷新
- clear_cache 能清理 `answer_cache`
- cache_debug/conversation 能读取 conversation cache 细节

依赖项：
- runtime
- qa cache metrics
- retrieval/vector client
- redis conversation cache

当前 public-service 状态：
- 已迁：
  - health
  - background_status
  - kb_info
  - refresh_kb
  - clear_cache
- 已补齐：
  - conversation cache debug
  - health 对 pending/skeleton 的非健康判定
  - runtime 真实依赖探测

验证点：
- `tests/test_health.py`
- `tests/test_system_module.py`

---

## 2. auth 功能清单

API 面：
- `POST /api/auth/login`
- `POST /api/v1/auth/login`
- `POST /api/auth/register`
- `POST /api/v1/auth/register`
- `GET /api/auth/me`
- `GET /api/v1/auth/me`
- `PUT|POST /api/auth/password`
- `PUT|POST /api/v1/auth/password`
- `POST /api/auth/forgot-password/initiate`
- `POST /api/v1/auth/forgot-password/initiate`
- `POST /api/auth/forgot-password/verify`
- `POST /api/v1/auth/forgot-password/verify`
- `GET /api/auth/security-questions`
- `GET /api/v1/auth/security-questions`
- `PUT|POST /api/auth/security-questions`
- `PUT|POST /api/v1/auth/security-questions`

能力项：
- token 签发与解码
- Bearer token 与 `?token=` 兼容读取
- 注册
- 登录失败计数与锁定
- 首登改密标记
- 密码强度校验
- 密码历史复用校验
- 忘记密码初始化
- 安全问题校验后重置密码
- 获取安全问题
- 设置安全问题
- `/me` 返回用户信息与首登/安全问题状态

Repository 依赖：
- `users`
- `password_history`
- `user_security_questions`
- `users.failed_login_attempts`
- `users.locked_until`
- `users.is_first_login`
- `users.must_set_security_questions`
- `users.password_updated_at`

当前 public-service 状态：
- 已迁：
  - schemas
  - API route surface
  - token service
  - password policy
  - login/register/change_password/reset/security_questions service logic
- 已补齐：
  - 真实 MySQL repository
  - app 级 service wiring
  - protected route 在 DB 不可用时的结构化错误语义
  - API / deps 读取当前 live auth service，而不是导入期固定实例

验证点：
- `tests/test_auth_module.py`

---

## 3. quota 功能清单

API 面：
- `GET /api/quota/my`
- `GET /api/v1/quota/my`
- `GET /api/quota/configs`
- `GET /api/v1/quota/configs`
- `POST /api/quota/configs`
- `POST /api/v1/quota/configs`
- `PUT /api/quota/configs/{quota_type}`
- `PUT /api/v1/quota/configs/{quota_type}`
- `POST /api/quota/reset/{user_id}/{quota_type}`
- `POST /api/v1/quota/reset/{user_id}/{quota_type}`
- `GET /api/quota/users/{user_id}`
- `GET /api/v1/quota/users/{user_id}`

能力项：
- quota config 读取
- active/inactive config 语义
- config missing 语义
- 多窗口 quota：
  - daily
  - weekly
  - monthly
  - custom_days
  - none
- user override limit
- usage 查询
- usage increment
- reset user usage
- get_my_quotas / get_user_quotas
- config create/update
- precheck quota
- strict config missing
- finalize quota
- admin/super 配额豁免
- Redis config/override/list cache

Repository / cache 依赖：
- `quota_configs`
- `user_quota_usage`
- `user_quota_overrides`
- Redis quota config cache

当前 public-service 状态：
- 已迁：
  - route surface
  - request schemas
  - quota service 核心算法
  - quota deps
- 已补齐：
  - 真实 MySQL repository
  - Redis cache 接入
  - app 级 auth/quota service wiring
  - auth 查询异常时的豁免语义修正
  - API / deps 读取当前 live quota service，而不是导入期固定实例

验证点：
- `tests/test_quota_module.py`

---

## 4. 当前迁移准则

这三块后续补齐时必须满足：
- 不丢现有 `/api` 与 `/api/v1` 双入口
- 不丢 token query 兼容
- 不丢 quota 多窗口语义
- 不丢 auth 密码历史/首登/安全问题状态机
- 不丢 system health/background/kb/cache 的返回字段
- 不把“默认未接线”状态伪装成健康可用

完成标准：
- 模块可连接真实底层依赖工作
- 模块在底层依赖不可用时返回结构化错误，而不是内部异常
- 剩余外部阻塞只应是：
  - 还没挂到 `gateway`
  - 还没迁移其他模块对它的调用

当前结论：
- `system`、`auth`、`quota` 这三个模块在 `public-service/backend` 内已经补到“独立后端可运行、只差 gateway 接入和其他未迁模块调用”的阶段。
- 本轮新增回归测试后，当前测试状态为 `44 passed`。
