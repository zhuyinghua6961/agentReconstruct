# auth 依赖关系与系统集成点

对应代码：
- `backend/app/modules/auth/deps.py`
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/admin_users/service.py`
- `backend/app/modules/quota/deps.py`
- `backend/app/modules/documents/api.py`
- `backend/app/modules/conversation/api.py`
- `backend/app/modules/uploads/api.py`

## 1. auth 是其他公共能力的入口依赖

从后端结构看，auth 的核心输出不是“用户信息接口”，而是：

- `AuthContext`
- `require_auth_context()`
- `get_optional_auth_context()`
- `require_admin_context()`

其他公共能力通过这些依赖完成：

- 登录鉴权
- 管理员鉴权
- 当前用户身份绑定

因此 auth 是其他公共能力的前置层。

## 2. auth 自己的下游依赖比较少，但位置很底

auth 依赖：

- `app.core.db.Database`
- `app.core.config.get_settings`
- `app.core.errors.AppError`
- `app.core.errors.PermissionDeniedError`
- `app.core.deps.AuthContext`

它没有依赖：

- conversation
- uploads
- documents
- quota

这正是它能被定义为公共基础能力的关键原因之一。

## 3. quota 通过 auth 拿到 user_id

`quota/deps.py` 的标准接法是：

- `require_quota(...)`

而这条 dependency 内部又依赖：

- `require_auth_context`

所以 quota 并不自己解析 token，而是复用 auth 的认证结果。

这意味着：

- quota 的预检查身份前提完全建立在 auth 上
- auth 一旦变更 token 机制，quota 接入层也会受影响

## 4. documents、conversation 等模块都把 auth 当成统一登录面

典型模式是：

- 路由直接挂 `Depends(require_auth_context)`
- 或挂 `Depends(require_quota(...))`，间接依赖 auth
- 个别匿名可访问路由挂 `get_optional_auth_context()`

所以这些模块都没有重复实现自己的 token 解析。

这说明当前后端至少在“身份入口”这件事上已经相对统一。

## 5. uploads 对 auth 的依赖是可选身份 + 条件绑定

uploads 的一个特殊点是：

- 它既支持匿名上传落本地
- 又支持带登录态时绑定 conversation / quota / user

因此上传模块会使用 optional auth 语义，而不是纯强制 auth。

从公共能力角度看，这说明 auth 不只是“阻断未登录访问”，也被用于：

- 让匿名和登录用户共用一条上传能力链

## 6. admin_users 与 auth 共用同一个 users 数据域

`AuthRepository` 本身就暴露了：

- `count_users`
- `list_users`
- `update_status`
- `update_user_type`
- `delete_user`

而 `admin_users` 会继续围绕这些用户主数据做管理逻辑。

这说明当前系统里的用户域并没有严格拆成：

- 身份认证仓储
- 用户管理仓储

而是共享在 auth 模块附近。

## 7. auth 对 conversation 的价值不只是“知道是谁”

conversation、documents、uploads 等模块都要把用户身份用于：

- 数据权限
- 会话归属
- 配额扣减
- 下载/访问限制

所以 auth 产出的 `user_id / role / username` 是整个公共能力链条的基础上下文，而不是仅用于显示昵称。

## 8. 当前依赖关系可以抽成一张简图

后端基础方向大致是：

- `core.config/core.db/core.errors/core.deps`
  -> `auth`
  -> `quota`
  -> `documents/conversation/uploads/admin_users/...`

其中：

- `auth` 直接贴近数据库和错误模型
- `quota` 在 auth 之上复用 `user_id`
- 业务型公共模块再在 quota 和 auth 之上叠加自己的流程

## 9. auth 本身没有跨模块反向写入

从当前代码看，auth 不会主动调用其他模块服务：

- 登录不会调 quota
- 改密不会调 conversation
- 设置安全问题不会调 documents

这意味着它的副作用主要局限在：

- `users`
- `password_history`
- `user_security_questions`

这是很好的基础服务特征。

## 10. 但它的接口返回已经被前端多个模块当控制信号使用

虽然 auth 后端很底层，但这些返回字段已经被页面逻辑深度依赖：

- `role`
- `user_type`
- `is_first_login`
- `has_security_questions`
- `require_security_questions_setup`
- `warning`

所以如果未来要重构 auth，真正需要保持兼容的不只是 token 和 `/me`，还包括这些“流程控制位”。

## 11. 公共能力视角下的结论

auth 的模块边界可以概括成：

- 它不拥有业务数据
- 它拥有平台级身份和账户安全规则
- 它通过 dependency 注入把统一身份上下文输送给其他公共能力模块

因此它是“所有公共能力的公共前提”，不是其中之一的附属能力。
