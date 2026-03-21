# admin_users 模块代码细读

模块路径：
- `backend/app/modules/admin_users/api.py`
- `backend/app/modules/admin_users/service.py`
- `backend/app/modules/admin_users/import_service.py`
- `backend/app/modules/admin_users/schemas.py`
- `backend/tests/test_admin_users.py`
- `frontend-vue/src/services/admin.js`
- `frontend-vue/src/views/AdminDashboard.vue`
- `frontend-vue/src/components/BatchImportDialog.vue`
- `frontend-vue/src/components/ImportResultDialog.vue`

模块定位：
- 明确属于公共能力
- 负责后台平台账号管理、身份切换、管理员重置口令、批量导入工具链
- 底层直接复用 auth 的 `users` 数据域和部分密码规则

已细拆到：

- `admin_users/README.md`
- `admin_users/01-api-guards-and-contracts.md`
- `admin_users/02-user-lifecycle-and-state-transitions.md`
- `admin_users/03-batch-import-and-template-pipeline.md`
- `admin_users/04-dependencies-shared-schema-and-boundaries.md`
- `admin_users/05-frontend-dashboard-and-contract-gaps.md`

本模块的关键结论：

- 所有后台接口都必须通过 `require_admin_context`，所以真正后台权限仍然只认 `role == admin`
- `super` 只是 `user_type = 2`，不是后台管理员角色
- 单用户创建、管理员重置、批量导入三条路径没有完全共享同一套安全规则
- 管理员手工创建用户和批量导入用户，发放的是初始登录口令，不套用用户后续自行改密的强口令规则；管理员重置密码则仍会走 auth 强度校验
- 批量导入绕过 `create_user()` 主流程，未显式补写首次登录、安全问题和密码历史副作用
- 前端导入结果弹窗与后端明细字段存在明显契约偏差

当前已确认问题与迁移修复点：

- `P1` 批量导入逐行直接调用 `AuthRepository.create_user()`，没有复用 `AdminUsersService.create_user()`，因此没有显式补写：
  - `is_first_login=True`
  - `must_set_security_questions=True`
  - `add_password_history()`
  - `trim_password_history()`
- 这里要特别澄清：问题不在于“管理员新建/导入新用户是否需要遵守后续用户改密规则”。按照当前确认过的业务口径，这两条路径发放的是初始登录口令，本来就不需要套用后续用户自行改密规则。真正的问题是这两条路径的状态副作用没有统一。
- `P2` 管理员重置密码时，会更新 hash、密码历史、首次登录标记和安全问题标记，但不会重置登录失败次数与锁定状态；用户可能在管理员重置后仍然受旧锁定窗口影响。
- `P1` 导入结果前后端契约不一致：
  - 后端导入明细写的是 `reason`
  - 前端结果弹窗读取的是 `message`
  - 前端还展示 `user_id`，但成功项并未返回该字段
- 抽成独立公共后端前，这三个问题都应优先修掉，否则后台用户管理会把副作用不一致和现存契约 bug 一并迁出。

建议阅读顺序：

1. 先看 `admin_users/01-api-guards-and-contracts.md`
2. 再看 `admin_users/02-user-lifecycle-and-state-transitions.md`
3. 然后看 `admin_users/03-batch-import-and-template-pipeline.md`
4. 如果要判断它和 auth/quota 的边界，再看 `admin_users/04-dependencies-shared-schema-and-boundaries.md`
5. 如果要梳理管理台真实行为和前后端偏差，再看 `admin_users/05-frontend-dashboard-and-contract-gaps.md`
