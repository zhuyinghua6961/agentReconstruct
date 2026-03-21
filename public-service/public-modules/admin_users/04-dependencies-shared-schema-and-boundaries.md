# admin_users 依赖关系、共享数据域与边界

对应代码：
- `backend/app/modules/admin_users/service.py`
- `backend/app/modules/admin_users/import_service.py`
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/deps.py`
- `backend/app/modules/quota/deps.py`
- `backend/app/modules/quota/service.py`

## 1. admin_users 不是独立账户系统，而是 auth 的后台控制面

最直接的事实是：

- `AdminUsersService` 直接 new `AuthRepository`
- 批量导入也通过 `admin_users_service.users` 落 auth 的 repository

所以这里并不存在独立的：

- `AdminUsersRepository`
- `admin_users` 表

管理能力和认证能力共用同一份用户主数据。

## 2. auth 提供了三层复用

第一层，数据域复用：

- `users`
- `password_history`

第二层，密码策略复用：

- `auth_service.validate_password_strength()`

第三层，权限入口复用：

- `require_admin_context`

但这个复用并不完全一致。

## 3. 它只复用了一部分 auth 规则

单用户创建：

- 不复用密码强度校验

管理员重置：

- 复用密码强度校验

批量导入：

- 既不复用强度校验
- 也不复用单用户创建链的首登安全副作用

所以 admin_users 不是“auth 能力的简单 UI”，而是只挑部分 auth 能力复用。

## 4. `role` 和 `user_type` 在这里被刻意分开使用

后台访问控制看：

- `role`

普通/超级用户区分看：

- `user_type`

因此会出现一种非常重要的系统状态：

- `role = user`
- `user_type = 2`

这类用户：

- 不是后台管理员
- 但可能在 quota 等横切模块中被视为豁免或高级用户

这说明当前平台权限不是单轴模型，而是至少双轴：

- 路由级管理权限轴：`role`
- 平台待遇/等级轴：`user_type`

## 5. quota 只接在批量导入链，不接在单用户管理链

当前看到的 quota 接入只有：

- `excel_upload`

也就是：

- 下载模板不扣 quota
- 单用户创建不扣 quota
- 改密码/删用户/改状态不扣 quota
- 只有批量导入走 quota

因此 admin_users 里的 quota 不是“后台用户管理额度”，而是单独针对 Excel/CSV 导入动作的资源控制。

## 6. quota 豁免逻辑和 admin guard 并不完全同一层

admin guard：

- 只看 `role == admin`

导入 quota 豁免：

- 看 `user_type in {1, 2}`

这两层判断不一致，造成的结果是：

- 在“谁能访问后台”这件事上，`super` 不是管理员
- 在“谁免导入 quota”这件事上，`super` 被当成高级豁免用户

这种分层是代码里的真实事实。

## 7. admin_users 的数据库副作用边界很窄

它主要写：

- `users`
- `password_history`

没有看到它主动维护：

- user_security_questions 内容
- 配额配置表
- 会话表
- conversation/documents 关联表

即使管理员重置密码，也只是把：

- `must_set_security_questions = True`

重新立起来，而不直接写安全问题内容。

## 8. 删除用户的后果会外溢到其他公共模块

虽然 `delete_user()` 只是一条 `DELETE FROM users`，但这个用户 ID 又被：

- auth 登录态
- quota 使用记录
- conversation 所属关系
- documents 访问行为

等模块复用。

当前 admin_users 自己并不处理这些外溢影响。

所以从公共能力边界看，它是：

- 用户主数据控制面

而不是：

- 全平台用户下线编排器

## 9. import_service 与 service 的分层也值得单独记

`service.py` 负责：

- 单用户后台管理语义

`import_service.py` 负责：

- 文件模板
- CSV/XLSX 解析
- 逐行导入
- 导入 quota

二者之间没有严格共享同一条“创建用户”应用服务。

这会带来：

- 规则分叉
- 副作用分叉
- 返回结构分叉

## 10. 作为公共能力的真实定位

admin_users 适合被定义为：

- 平台账户后台治理能力

而不是：

- auth 的附属页面
- 或单纯用户 CRUD

它之所以单独成立，是因为它处理了后台用户生命周期和导入工具链；但它的底层数据和规则又与 auth 紧耦合，这就是它最需要被文档化的边界。
