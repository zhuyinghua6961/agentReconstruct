# quota 前端消费与管理 UI

对应代码：
- `frontend-vue/src/api/quota.js`
- `frontend-vue/src/services/quota.js`
- `frontend-vue/src/views/QuotaManagement.vue`
- `frontend-vue/src/features/controls/composables/useQuotaAdmin.js`
- `frontend-vue/src/features/controls/components/ControlsPanel.vue`

## 1. 前端至少有两类 quota 消费面

### 1.1 普通用户配额展示

来自：
- `services/quota.js`
- `ControlsPanel.vue`

### 1.2 管理员 quota 管理

来自：
- 独立页 `QuotaManagement.vue`
- 控制面 `useQuotaAdmin + ControlsPanel`

这两套管理面并不等价。

## 2. API 封装层比较薄

`src/api/quota.js` 只是简单包一层：
- get my quotas
- get configs
- create config
- update config
- get user quotas
- reset user quota

没有额外的前端业务逻辑。

## 3. `services/quota.js` 会把我的 quota 结果重塑

后端 `get_user_quotas()` 返回：
- `data.quotas` 数组

但 `quotaApi.getMyQuotas()` 会把它转成：
- 以 `quota_type` 为 key 的对象映射

并且会把每一项 windows 继续标准化成：
- `period`
- `period_days`
- `current`
- `limit`
- `remaining`
- `reset_time`
- `allowed`

这意味着：
- 前端普通用户消费面已经显式理解了 windows 概念

## 4. reset_hint 到中文展示的映射

前端把：
- `next_day_start`
- `next_week_start`
- `next_month_start`
- `next_custom_window_start:...`
- `never`

映射成更人类可读的：
- `今日24:00`
- `下周开始`
- `下月1号00:00`
- `YYYY-MM-DD 00:00`
- `无限制`

所以前端展示已经在消费 quota 的 period/reset 语义。

## 5. 独立管理页支持多窗口创建，但保存逻辑仍有简化

`QuotaManagement.vue` 的 create 页面允许输入：
- `daily_limit`
- `weekly_limit`
- `monthly_limit`
- `quota_type`
- `quota_name`
- `is_active`

并且会：
- 自动把第一个非空窗口值当 `default_limit`

这说明独立页比控制面更接近后端真实模型。

但要注意：
- 这个页面当前没有显式编辑 `period` / `period_days`
- 创建时默认按窗口值主导

## 6. 控制面里的 admin quota UI 更简化

`useQuotaAdmin.js + ControlsPanel.vue` 只允许编辑：
- `default_limit`
- `is_active`

不会编辑：
- `daily_limit`
- `weekly_limit`
- `monthly_limit`
- `period`
- `period_days`

所以：
- 控制面适合轻量维护
- 不适合完整管理多窗口 quota

## 7. 一个关键风险：简化更新可能改变后端模型

因为后端 `update_config()` 的行为受传参模式影响。

控制面只传：
- `default_limit`
- `is_active`

这可能让原本多窗口配置被按单窗口逻辑重写。

也就是说：
- 轻量管理 UI 未必只是“少显示字段”
- 它可能真的会改变 quota 配置结构

这是当前前后端协作里最值得警惕的点之一。

## 8. quota type 预置列表已经形成产品层契约

`QuotaManagement.vue` 里预置了：
- `ask_query`
- `file_upload`
- `file_view`
- `pdf_summary`
- `text_translate`

这说明这些 quota_type 已经不只是后端字符串，而是产品层已知类型。

同时也意味着：
- 新增 quota_type 如果不更新前端预置列表，管理体验会不完整

## 9. 普通用户侧当前怎么展示 quota

`ControlsPanel.vue` 目前展示的仍是简化版：
- `quota_name`
- `current/limit`

没有把 windows 全量展开。

所以：
- 后端支持多窗口
- service 也做了 windows 标准化
- 但普通控制面 UI 仍主要展示 primary window

这会让一些多窗口限制在 UI 上不够直观。

## 10. 当前前端层面的关键结论

- quota API surface 比前端某些管理面更强
- 普通用户消费面已经理解 windows，但展示仍然简化
- admin 控制面过于轻量，可能误伤多窗口配置
- 独立管理页比控制面更接近后端真实能力

如果后面继续整理公共能力，这块最需要统一的是：
- 哪个前端入口才是 quota 的“权威管理面”
- 多窗口配置是否允许被简化 UI 修改
