# admin_users 前端管理页与契约偏差

对应代码：
- `frontend-vue/src/views/AdminDashboard.vue`
- `frontend-vue/src/services/admin.js`
- `frontend-vue/src/components/BatchImportDialog.vue`
- `frontend-vue/src/components/ImportResultDialog.vue`
- `backend/app/modules/admin_users/service.py`
- `backend/app/modules/admin_users/import_service.py`

## 1. 前端后台入口主要集中在 `AdminDashboard.vue`

管理页负责：

- 拉当前用户信息
- 拉用户列表
- 创建用户
- 修改用户密码
- 一键重置临时密码
- 切换用户类型
- 启用/停用
- 删除用户
- 打开批量导入弹窗
- 展示导入结果弹窗

这意味着 admin_users 的前端并不是拆分式管理控制台，而是一页集中承载。

## 2. `services/admin.js` 只对 `getUsers()` 做了完整错误处理

`getUsers()` 走：

- `fetchWithErrorHandling()`

会处理：

- `ACCOUNT_DISABLED`
- token 失效

但其他接口例如：

- `createUser`
- `changeUserPassword`
- `changeUserStatus`
- `changeUserType`
- `deleteUser`
- `batchImportUsers`

都只是简单 `fetch + safeJson`，没有统一 auth 错误处理。

所以管理端自己的错误处理也不是统一的。

## 3. 管理页和批量导入弹窗还存在重复 API 实现

`services/admin.js` 已经提供：

- `batchImportUsers()`
- `downloadImportTemplate()`

但 `BatchImportDialog.vue` 仍然直接：

- `fetch('/api/admin/users/batch-import')`
- `fetch('/api/admin/users/import-template?...')`

这说明：

- 前端 admin API 封装没有被完全复用
- 模板下载与导入上传逻辑在页面组件里复制了一份

## 4. 前端密码规则和后端真实规则并不完全对齐

管理页“修改密码”弹窗提交前只校验：

- 新密码长度至少 6

但后端 `reset_password()` 实际会调用 auth 的密码强度规则：

- 普通用户至少 8 位且 4 类字符满足 3 类
- admin 则更严格

所以这里存在明显契约差：

- 前端可能放行
- 后端再拒绝

此外弹窗 placeholder 写的是：

- `12位以上，包含大小写字母、数字、特殊符号`

这更像管理员密码规则，而后台当前重置的大部分目标用户其实是普通用户。

与之相对，管理页“添加用户”弹窗几乎不校验密码强度：

- 只要求用户名和密码非空
- 用户名长度 `3..50`
- 用户名不能以 `admin` 开头

这和后端 `create_user()` 不做强度校验是对齐的。

结合当前业务约束，更准确的表述应当是：

- 后台创建链把初始密码视为临时口令
- 它与 auth 自助注册链、用户后续自行改密链不是同一套安全门槛

## 5. “一键重置密码”并不是后端生成临时密码

`openResetPasswordModal()` 的实际行为是：

1. 前端本地 `generateTemporaryPassword()`
2. 把这个明文密码发给后端 `changeUserPassword`
3. 成功后把同一个明文展示给管理员

因此临时密码的生成源在前端浏览器，不在后端。

这意味着：

- 后端不控制临时密码生成策略
- 管理员页面直接持有明文临时密码

## 6. 批量导入结果弹窗和后端返回字段不一致

`ImportResultDialog.vue` 读取每条明细时使用：

- `item.message`
- `item.user_id`

但后端 `import_service.py` 实际返回的明细字段主要是：

- `row`
- `username`
- `status`
- 失败/跳过时的 `reason`

并没有：

- `message`
- `user_id`

直接后果：

- 结果表的“消息”列大概率为空
- “用户ID”列始终是 `-`
- 下载失败记录时也会把错误信息写成空，因为它同样读取 `record.message`

这是当前最明确的前后端契约偏差之一。

## 7. 批量导入弹窗的说明文案与后端约束不完全一致

弹窗写明：

- 文件必须包含三列 `username/password/user_type`
- 单次最多导入 1000 条
- 密码不少于 6 位

后端实际情况：

- `user_type` 列是可选的，不提供时默认 `common`
- 没有 1000 条上限校验
- 密码确实只检查不少于 6 位

因此前端文案里有一部分比后端更严格，但并未真实落约束。

## 8. 管理页对“超级用户”的展示依赖 `user_type` 而不是 `role`

`getRoleText()` 和 `getRoleClass()` 都优先按：

- `user.user_type`

区分：

- 管理员
- 超级用户
- 普通用户

这和后端 `update_type()` 只改 `user_type` 是一致的。

所以前端实际上也接受了：

- `super` 只是用户类型，不是后台管理员 role

## 9. 列表和操作页使用的是旧 auth 本地存储 key

管理端请求 header 里读的是：

- `localStorage.getItem('token')`

而不是新 composable 体系的：

- `agentcode.auth.token.v1`

所以 admin_users 前端仍然属于旧页面体系，不是新的统一 auth session 体系。

## 10. 对公共能力拆分的意义

如果以后要把后台用户管理单独抽成公共后台服务，前端至少要同时处理三类问题：

- admin API 封装收敛
- 导入结果字段契约统一
- 密码规则与临时密码生成职责重新收口

否则后端就算保持不变，管理页行为也还是会继续分裂。
