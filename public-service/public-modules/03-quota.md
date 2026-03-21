# quota 模块代码细读

模块路径：
- `backend/app/modules/quota/api.py`
- `backend/app/modules/quota/service.py`
- `backend/app/modules/quota/repository.py`
- `backend/app/modules/quota/deps.py`
- `backend/app/modules/quota/cache.py`
- `backend/app/modules/quota/schemas.py`

关联代码：
- `frontend-vue/src/api/quota.js`
- `frontend-vue/src/services/quota.js`
- `frontend-vue/src/views/QuotaManagement.vue`
- `frontend-vue/src/features/controls/composables/useQuotaAdmin.js`
- `frontend-vue/src/features/controls/components/ControlsPanel.vue`
- `backend/tests/test_quota.py`

模块定位：
- 明确属于公共能力
- 是横切配额规则中心
- 负责配置、窗口计算、路由依赖、后置记账和管理接口

## 1. 结论先说

`quota` 不是简单“每日次数限制”，而是一套通用配额引擎，当前至少包含：
- quota config 管理
- 多周期窗口计算
- 用户级限额覆盖
- 依赖注入式 precheck/finalize
- Redis 元数据缓存

而且它已经被多个公共模块共同使用：
- ask_gateway
- conversation 文件下载
- documents 查看/总结/翻译
- uploads 特例路径
- admin_users 批量导入

## 2. 深拆文档索引

本次已把 `quota` 再细分为子文档，放在：
- `/home/cqy/worktrees/public-service/public-modules/quota/README.md`
- `/home/cqy/worktrees/public-service/public-modules/quota/01-api-and-admin-surface.md`
- `/home/cqy/worktrees/public-service/public-modules/quota/02-config-model-and-window-calculation.md`
- `/home/cqy/worktrees/public-service/public-modules/quota/03-deps-precheck-and-finalize.md`
- `/home/cqy/worktrees/public-service/public-modules/quota/04-repository-and-cache.md`
- `/home/cqy/worktrees/public-service/public-modules/quota/05-frontend-and-management-ui.md`

这份 `03-quota.md` 保留为总览。

## 3. 当前最重要的代码事实

### 3.1 配置缺失默认允许，不默认失败

`check_quota()` 如果找不到 config，不会直接报错，而是返回：
- `success=true`
- `allowed=true`
- `config_missing=true`
- `config_active=false`

只有当调用方使用：
- `require_quota(..., strict_config=True)`

时，这种“缺配置”才会被升级成 `503 / QUOTA_CONFIG_MISSING`。

### 3.2 inactive config 也不是失败，而是“成功但跳过”

如果 config 存在但 `is_active != 1`：
- `check_quota()` 仍返回 success/allowed
- `increment_quota()` 返回 success + `skipped=true`

因此：
- inactive 不是错误态
- 更像关闭配额规则

### 3.3 多窗口模式是真正并行生效，不是择一生效

如果配置了：
- `daily_limit`
- `weekly_limit`
- `monthly_limit`

那么：
- `check_quota()` 会生成多个 windows
- `allowed` 需要所有窗口都允许
- `increment_quota()` 会同时给每个窗口各加 1

这意味着：
- 一次操作可能同时消耗日、周、月三条 usage 记录

### 3.4 管理 UI 目前并没有完整暴露多窗口编辑面

后端 `create/update_config()` 完整支持：
- `daily_limit`
- `weekly_limit`
- `monthly_limit`
- `period`
- `period_days`

但前端有一部分管理面只编辑：
- `default_limit`
- `is_active`

所以：
- 后端能力比部分前端控制面更丰富
- 当前管理 UI 对多窗口支持并不完全统一

## 4. 为什么它属于公共能力

- 它不承载具体业务，只承载平台规则
- 它已经跨模块服务多个入口
- `quota_type` 已经成为跨模块契约字符串

因此它明显属于平台级公共能力。

## 5. 模块内部可分成哪几层

### 5.1 API 层

`api.py` 负责：
- 路由
- 管理权限
- HTTP 状态码映射

### 5.2 Service 层

`service.py` 负责：
- 配额窗口计算
- override 合并
- config 校验
- config CRUD
- reset 逻辑

### 5.3 Deps 层

`deps.py` 负责：
- precheck
- strict_config 语义
- quota 豁免
- finalize 计数

### 5.4 Repository / Cache 层

`repository.py` 管：
- `quota_configs`
- `user_quota_overrides`
- `user_quota_usage`

`cache.py` 管：
- config 缓存
- list 缓存
- override 缓存

## 6. 当前最需要注意的边界

- `require_quota/finalize_quota` 是标准接入方式
- `uploads` 的即时扣减是特例，不代表 quota 标准语义
- `quota_type` 命名已经是跨模块硬契约
- 多窗口逻辑足够强，但也增加了配置和前端展示复杂度

所以 quota 的下一步重点不是继续加功能，而是统一各模块接入方式和管理面表达。

## 7. 当前已确认问题与迁移修复点

- `P2` `uploads` 没有走标准的 `require_quota() + finalize_quota()` 语义，而是在控制器里直接 `check_quota() + increment_quota()`；这会导致上传链与 documents/conversation/download 的计数时机不一致。
- `P2` quota 标准豁免逻辑是 `user_type in {1, 2}`，但 `uploads` 的 `_optional_quota_response()` 只跳过 `user_type == 2`；管理员和超级用户在不同公共模块里的配额待遇并不完全一致。
- `P3` 后端 quota 能力已经支持多窗口并行限制，但部分前端管理面仍更接近单 `default_limit` 视图；如果后续把 quota 抽成公共后端而前端不一起收口，管理端仍会只覆盖部分真实能力。
- 因此 quota 模块本体并不是“逻辑错误最多”的部分，但它是当前多个模块接入语义不一致的中心点。后续迁移时应把“接入收口”作为重点任务，而不是只迁表和接口。
