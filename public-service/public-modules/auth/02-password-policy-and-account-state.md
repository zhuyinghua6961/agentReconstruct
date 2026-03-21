# auth 密码策略与账户状态机

对应代码：
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/repository.py`
- `backend/tests/test_auth.py`

## 1. 口令哈希不是框架默认实现

当前密码哈希完全在 `service.py` 内实现：

- `hashlib.pbkdf2_hmac("sha256", ...)`
- salt 由 `secrets.token_hex(16)` 生成
- 默认迭代次数 `120000`

持久化格式：

- `pbkdf2_sha256$iterations$salt$digest`

这套格式同时用于：

- 登录密码
- 历史密码记录
- 安全问题答案哈希

也就是说，auth 模块内部复用了同一套 PBKDF2 方案，而不是为安全问题单独设计一套简化存储。

## 2. 普通用户和管理员的密码规则不同

`_validate_password_strength()` 把策略分成两类：

普通用户：

- 长度至少 8
- 小写/大写/数字/符号 4 类里至少满足 3 类

管理员：

- 长度至少 12
- 4 类必须全部具备

这里是按 `role` 判定，不是按 `user_type`。

所以真正影响密码强度的是：

- `role == admin`

而不是：

- `user_type == 1`

## 3. 密码历史限制也是角色分层

历史条数：

- 管理员最近 5 次
- 其他用户最近 3 次

校验分两层：

1. 先检查新密码是否与当前密码一致
2. 再检查 `password_history` 里的最近 N 次 hash

因此“不能复用最近密码”不只是查历史表，也包括当前口令本身。

## 4. 注册时就写入密码历史

`register()` 在创建用户后立即：

1. `add_password_history()`
2. `trim_password_history()`

这意味着密码历史并不是从第一次改密开始，而是从注册时就开始积累。

后果是：

- 刚注册后第一次改密，也不能改回初始密码

这与很多系统“历史只记录修改后的密码”不同。

## 5. 账户状态不是只有 active/disabled

从登录行为看，auth 的账户状态至少由三层组成：

1. `status`
2. `failed_login_attempts`
3. `locked_until`

其中：

- `status != active` 直接视为禁用
- `locked_until > now` 视为临时锁定
- `failed_login_attempts` 是锁定前的累计计数

所以“账户不可登录”可能有两种完全不同来源：

- 管理停用
- 失败次数触发临时锁

## 6. 登录失败锁定策略

环境变量：

- `LOGIN_FAILURE_LOCK_THRESHOLD` 默认 `5`
- `LOGIN_FAILURE_LOCK_MINUTES` 默认 `5`

登录流程里：

1. 先查当前用户
2. 如果 `locked_until` 还没过，直接返回锁定信息
3. 如果锁定时间已过，先 reset attempts
4. 密码错误时调用 `increment_login_attempts()`

返回语义有两种：

未到阈值：

- `INVALID_CREDENTIALS`
- 附带 `failed_attempts`
- 附带 `remaining_attempts`

到达阈值：

- `ACCOUNT_LOCKED_DUE_TO_FAILURES`
- 不再只是“密码错”

而如果用户在真正登录前就已处于锁定时间窗内，则返回：

- `ACCOUNT_LOCKED`
- 带 `locked_until`
- 带 `remaining_seconds`

## 7. 登录成功不会立刻刷新用户状态对象

`login()` 登录成功后：

1. `reset_login_attempts()`
2. 生成 token
3. 用登录前读出的 `user` 构造 payload

这有一个微妙点：

- 返回给前端的 `user_payload` 不是 reset 之后重新回库拿的

但因为返回体里不暴露 `failed_login_attempts` 和 `locked_until`，所以对当前前端没有直接影响。

## 8. 首次登录和密码过期不是一回事

`login()` 成功后会叠加两类安全信号：

首次登录：

- 如果 `is_first_login == true`
- 返回顶层 `require_password_change = true`
- 并把 `message` 改成“首次登录，请立即修改密码”

密码过期：

- 如果 `password_updated_at` 距离当前达到 `PASSWORD_EXPIRE_DAYS`
- 返回 `warning.code = PASSWORD_EXPIRED`

关键区别：

- 首次登录是强制流，前端会跳转到 `/profile`
- 密码过期只是 warning，不会阻断登录

## 9. 改密成功会顺便清安全状态

`change_password()` 成功后会：

- 更新 `password_hash`
- 写历史
- trim 历史
- `mark_first_login_completed()`
- `reset_login_attempts()`

因此改密除了改口令，还承担两个副作用：

- 首次登录流程完成
- 清空失败登录计数

也就是说，“密码修改”在这里不是纯 profile 操作，而是账户状态机的一个关键跃迁节点。

## 10. 忘记密码重置也会完成首次登录

`verify_and_reset_password()` 成功后同样会：

- 更新密码
- 写历史
- trim 历史
- `mark_first_login_completed()`
- `reset_login_attempts()`

这意味着只要能通过安全问题重置密码，系统就认为：

- 用户已经完成首次口令安全动作

这是当前代码的明确语义，不区分“主动改密”还是“找回后重置”。

## 11. 密码过期依赖字段存在

`_password_expired()` 只看：

- `password_updated_at`

而 repository 又把这个字段当成可选列。

所以如果数据库没有 `password_updated_at`：

- 密码过期检查整体失效
- 但登录、改密、注册本身仍可继续工作

这说明密码过期提醒是可降级能力，不是 auth 基础能力中的强依赖。

## 12. 测试固定的状态机事实

测试至少固定了两件核心事实：

- 密码复用历史必须被拒绝
- 连续失败到阈值时必须进入 `ACCOUNT_LOCKED_DUE_TO_FAILURES`

测试没有完整覆盖管理员密码规则和密码过期 warning，但 service 代码已经明确写死了这些分支。
