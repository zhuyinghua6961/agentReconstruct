# admin_users 单用户管理链与状态变化

对应代码：
- `backend/app/modules/admin_users/service.py`
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/auth/service.py`
- `backend/tests/test_admin_users.py`

## 1. 这个模块管理的是 `users` 主表，不是独立后台表

`AdminUsersService` 内部直接持有：

- `AuthRepository()`

所以这里所有用户管理动作都直接落在 auth 共用的：

- `users`
- `password_history`

这意味着 admin_users 不是独立用户域，而是 auth 用户域的后台控制面。

## 2. 列表能力只是 `users` 表的管理视图

`list_users()`：

1. 规范化 `page`
2. 把越界 `page_size` 回退到 `10`
3. 读总数
4. 读分页 rows
5. 返回：
  - `id`
  - `username`
  - `role`
  - `user_type`
  - `status`
  - `created_at`

这里的 `user_type` 会优先取数据库字段；没有时再按 role 推断。

所以管理列表展示的是“兼容后的用户身份视图”，不是原始表值直出。

## 3. 单用户创建链有强制首登安全流程，但不套用用户后续改密规则

`create_user()` 的规则：

- 用户名和密码不能为空
- 用户名长度 `3..50`
- `user_type` 只能是 `common/super`
- 用户名不能以 `admin` 开头
- 用户名不能重复

然后会：

- `role = user`
- `user_type = 2/3`
- `is_first_login = True`
- `must_set_security_questions = True`
- 写入密码历史
- trim 密码历史

但这里有一个很重要的代码事实：

- 它没有调用 `auth_service.validate_password_strength()`

也就是说：

- 管理员手工创建用户时，初始密码不受 auth 自助注册/用户后续改密规则约束

结合当前产品约束，更准确的理解应当是：

- 这里发放的是管理员设置的初始登录口令
- 后续用户登录后再进入“首次改密 + 安全问题设置”的安全流程

所以它和普通用户注册链明显不同，但这不是文档里需要被误写成缺陷。

## 4. “超级用户”不是管理员角色

创建用户时即使传：

- `user_type = super`

最终仍然写：

- `role = user`
- `user_type = 2`

因此：

- 这个“超级用户”是类型位，不是后台管理员 role
- 它不会通过 `require_admin_context`

但它可能在 quota 等模块里被当作豁免用户。

## 5. 管理员重置密码复用了 auth 的强度规则

`reset_password()` 流程：

1. 新密码不能为空
2. 用户必须存在
3. 不能重置自己的密码
4. 调 `auth_service.validate_password_strength(...)`
5. 重新哈希密码
6. 更新密码
7. 写历史
8. trim 历史
9. `mark_first_login_required(True)`
10. `set_security_setup_required(True)`

所以管理员重置密码的真实语义不是“代改一次密码”，而是：

- 给目标用户下发一个新口令
- 强制他下次登录时重新完成首登安全动作

## 6. 管理员重置不会清登录失败计数或锁定状态

`reset_password()` 没有调用：

- `reset_login_attempts()`

因此如果用户此前：

- `failed_login_attempts` 很高
- 或仍处于 `locked_until` 时间窗内

管理员重置密码后，这些状态可能仍然保留。

这意味着：

- 重置密码并不等于解锁账号

这是一个很容易被误解的状态机细节。

## 7. 获取密码提示接口本质上是占位接口

`get_password_hint()` 的实现不是返回提示，而是固定返回：

- `username`
- `password = 当前系统采用哈希存储，无法查看明文密码`

它的作用更像：

- 明确告诉管理端“这里不支持看明文密码”

而不是提供真正的密码找回线索。

## 8. 状态切换只支持 active/disabled

`update_status()` 限制：

- status 只能是 `active` 或 `disabled`
- 用户必须存在
- 不能停用自己
- 不能停用管理员账号

这里没有：

- soft lock
- archive
- freeze

所以用户后台状态模型非常简单，真正的临时锁定仍然属于 auth 登录失败链。

## 9. 身份切换只改 `user_type`，不改 `role`

`update_type()` 的前提：

- 数据库必须有 `user_type` 列，否则 `NOT_SUPPORTED`

它只允许切：

- `2` / `3`
- `super` / `common`

并且：

- 不能修改管理员身份
- 如果无变化直接返回 success

最关键的是：

- 它只调用 `update_user_type()`
- 不改 `role`

所以“设为超级用户”并不是授权后台管理，只是把用户放到另一类普通账户。

## 10. 删除是物理删除，不做级联补偿

`delete_user()` 规则：

- 用户必须存在
- 不能删自己
- 不能删管理员

然后直接：

- `AuthRepository.delete_user(user_id=...)`

代码里没有看到：

- 会话回收
- 配额清理
- 关联业务数据迁移
- conversation/documents 的归档处理

因此这里是很直接的账号物理删除。

## 11. 三条单用户链的安全语义并不一致

普通注册链：

- 有密码强度校验
- 自动登录
- 首登安全初始化

管理员创建链：

- 初始密码不套用用户后续改密规则
- 有首登安全初始化
- 无自动登录

管理员重置链：

- 有密码强度校验
- 会再次强制首登安全初始化
- 不会清账户锁定状态

这说明 admin_users 不是简单复用 auth，而是在 auth 之上叠了新的后台控制语义。
