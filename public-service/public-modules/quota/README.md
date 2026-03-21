# quota 细拆索引

对应代码：
- `backend/app/modules/quota/api.py`
- `backend/app/modules/quota/service.py`
- `backend/app/modules/quota/repository.py`
- `backend/app/modules/quota/deps.py`
- `backend/app/modules/quota/cache.py`
- `backend/app/modules/quota/schemas.py`
- `frontend-vue/src/api/quota.js`
- `frontend-vue/src/services/quota.js`
- `frontend-vue/src/views/QuotaManagement.vue`
- `frontend-vue/src/features/controls/composables/useQuotaAdmin.js`
- `frontend-vue/src/features/controls/components/ControlsPanel.vue`
- `backend/tests/test_quota.py`

本目录把 `quota` 再拆成 5 个视角：

- `01-api-and-admin-surface.md`
  说明对外接口、权限边界、状态码语义和管理接口 surface。
- `02-config-model-and-window-calculation.md`
  说明 period 模型、多窗口规则、override 合并和 reset 键生成逻辑。
- `03-deps-precheck-and-finalize.md`
  说明 `require_quota`、`strict_config`、豁免逻辑和 finalize 计数语义。
- `04-repository-and-cache.md`
  说明 MySQL 表职责、usage 写入方式、Redis 缓存键和失效机制。
- `05-frontend-and-management-ui.md`
  说明前端 quota 展示、管理页和控制面里的管理入口。

总体判断：
- quota 已经是平台横切规则中心。
- 它最大的价值不在 CRUD，而在“统一接入的 precheck/finalize 语义”。
- 当前真正需要收敛的是各业务模块的接入一致性和前端管理面对多窗口配置的表达能力。

当前已确认问题：
- `uploads` 没有走 quota 标准 finalize 语义，而是自己直接预扣与计数。
- quota 标准豁免规则与 uploads 的豁免规则不一致。
- 后端多窗口能力强于部分前端管理面的表达能力。
