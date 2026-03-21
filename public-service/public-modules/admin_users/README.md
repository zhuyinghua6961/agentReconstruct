# admin_users 细拆索引

对应代码：
- `backend/app/modules/admin_users/api.py`
- `backend/app/modules/admin_users/service.py`
- `backend/app/modules/admin_users/import_service.py`
- `backend/app/modules/admin_users/schemas.py`
- `backend/tests/test_admin_users.py`
- `frontend-vue/src/services/admin.js`
- `frontend-vue/src/views/AdminDashboard.vue`
- `frontend-vue/src/components/BatchImportDialog.vue`
- `frontend-vue/src/components/ImportResultDialog.vue`

本目录把 `admin_users` 再拆成 5 个视角：

- `01-api-guards-and-contracts.md`
  说明管理接口面、管理员权限边界、schema 宽松性、multipart 文件提取方式和状态码语义。
- `02-user-lifecycle-and-state-transitions.md`
  说明单用户创建、密码重置、状态切换、身份切换、删除的真实业务语义和状态副作用。
- `03-batch-import-and-template-pipeline.md`
  说明 CSV/XLSX 模板、导入解析链、quota 预检查/计数、逐行校验和导入结果结构。
- `04-dependencies-shared-schema-and-boundaries.md`
  说明它对 `auth`/`quota` 的复用、`role` 与 `user_type` 的分层、共享 `users` 表后的边界问题。
- `05-frontend-dashboard-and-contract-gaps.md`
  说明后台管理页、批量导入弹窗和结果弹窗的前端接入方式，以及前后端契约偏差。

总体判断：
- `admin_users` 是平台级后台用户管理能力，不属于问答业务。
- 它最大的复杂度不在 CRUD，而在三条路径并行存在：
  - 单用户创建链
  - 管理员重置/状态修改链
  - 批量导入链
- 这三条路径没有完全共享同一套安全规则，因此文档里必须分开看。

当前已确认问题：
- 批量导入绕过单用户创建主流程，首次登录、安全问题、密码历史副作用未统一。
- 管理员重置密码不会清理失败次数和锁定状态。
- 导入结果弹窗读取 `message/user_id`，但后端明细实际提供的是 `reason` 且成功项无 `user_id`。
