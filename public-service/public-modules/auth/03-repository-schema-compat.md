# auth 仓储层与 schema 兼容

对应代码：
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/auth/service.py`

## 1. auth repository 明显在兼容多版数据库

`AuthRepository` 初始化后不会预先声明固定 schema，而是运行时探测：

- `SHOW COLUMNS FROM users`
- `SHOW TABLES`

并把结果缓存在：

- `_columns_cache`
- `_tables_cache`

这意味着它不是“严格 ORM 模型”，而是“按现网字段存在情况拼 SQL”的兼容仓储。

## 2. `users` 是主表，但字段不是固定集合

基础读取字段：

- `id`
- `username`
- `password_hash`（按场景可选）
- `role`
- `status`
- `created_at`
- `updated_at`

按存在性追加的字段：

- `user_type`
- `is_first_login`
- `must_set_security_questions`
- `password_updated_at`
- `failed_login_attempts`
- `locked_until`

这种设计的直接结果是：

- 同一份 auth 代码能在不同演进阶段的库结构上运行
- 但许多高级安全能力会自动退化

## 3. create_user() 会按列存在情况决定能力是否生效

创建用户时基础写入：

- `username`
- `password_hash`
- `role`
- `status=active`

附加写入要依赖列存在：

- `user_type`
- `is_first_login`
- `must_set_security_questions`
- `password_updated_at`

因此如果旧库没有这些列，会出现这些退化：

- 注册用户不能落 `user_type`
- 首次登录强制改密标记无法存储
- 强制设置安全问题标记无法存储
- 密码过期时间起点无法存储

但是接口仍然能正常返回成功。

## 4. password history 是可选表，不存在时直接降级

相关表：

- `password_history`

相关方法：

- `list_recent_password_hashes()`
- `add_password_history()`
- `trim_password_history()`

如果表不存在：

- 查询历史返回空数组
- 添加历史直接返回 `0`
- trim 直接返回 `0`

这意味着：

- 当前密码重复仍会被拦住
- 但“最近 N 次历史密码不可复用”会失效

所以密码历史是增强安全能力，不是 auth 运行的硬前提。

## 5. 安全问题也是可选表

相关表：

- `user_security_questions`

相关方法：

- `list_security_questions()`
- `has_security_questions()`
- `replace_security_questions()`

如果表不存在：

- 查询问题返回空列表
- `has_security_questions()` 恒为 `False`
- `replace_security_questions()` 直接返回，不报错

这会导致两个重要后果：

1. 忘记密码重置功能整体不可用
2. `set_security_questions()` 在 service 层可能仍返回“设置成功”，但实际没有持久化任何问题

第二点尤其值得记，因为这是典型的“兼容式静默降级”。

## 6. 登录失败计数能力也依赖列存在

计数相关列：

- `failed_login_attempts`
- `locked_until`

如果 `failed_login_attempts` 不存在：

- `reset_login_attempts()` 返回 `0`
- 登录失败不会真正累计

如果 `locked_until` 不存在：

- 仍可更新失败次数
- 但不会写临时锁定时间

所以“失败次数可见”和“账户可自动锁定”是两层不同能力。

## 7. repository 里混着 auth 之外的用户管理能力

`AuthRepository` 除了认证本身，还提供：

- `count_users()`
- `list_users()`
- `update_status()`
- `update_user_type()`
- `delete_user()`

这些方法会被 `admin_users` 复用。

这说明当前后端里：

- auth repository 不是纯认证仓储
- 它同时承担了 `users` 主表的部分基础管理职责

因此后续拆服务时，`users` 主表边界要特别小心，不然容易把 admin 用户管理和 auth 身份能力一起耦死。

## 8. SQL 风格偏直接，事务粒度靠连接上下文

repository 没有 ORM，也没有显式事务编排器。

模式是：

- `with self._db.connection() as conn`
- `with conn.cursor() as cursor`
- 执行单条 SQL

像 `replace_security_questions()` 这种多步写入：

1. delete 旧问题
2. insert 新问题若干条

代码层没有显式事务声明。

是否整体事务提交，取决于 `Database.connection()` 的实现。

这对文档非常重要，因为不能仅凭 repository 代码就武断认为它具备“原子替换”保证。

## 9. 查询兼容优先于约束表达

`get_by_id()` 和 `get_by_username()` 最终都走 `_select_user_fields()` 动态拼字段。

这类写法优先解决的是：

- 不同库结构都能查出来

而不是：

- 用固定 schema 严格约束返回对象

所以 service 层里对 `dict.get(...)` 的大量使用，不是随意写法，而是与 repository 的兼容目标一致。

## 10. 这个模块的真正数据库依赖图

auth service 对库的依赖可以拆成三层：

第一层，必需：

- `users`

第二层，增强安全：

- `password_history`

第三层，找回与首次安全流程：

- `user_security_questions`

而列级依赖则决定增强能力是否真正生效：

- `is_first_login`
- `must_set_security_questions`
- `password_updated_at`
- `failed_login_attempts`
- `locked_until`

因此 auth 在数据库侧不是单一“开或关”，而是明显的渐进式能力模型。
