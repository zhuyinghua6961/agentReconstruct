# quota 配置模型与窗口计算

对应代码：
- `backend/app/modules/quota/service.py`
- `backend/tests/test_quota.py`

## 1. 支持的 period 并不只有 daily

系统允许的 period：
- `daily`
- `weekly`
- `monthly`
- `custom_days`
- `none`

其中：
- `none` 表示无限制窗口
- `custom_days` 表示固定天数滑窗分段

## 2. 两种配置模式

### 2.1 单窗口模式

主要由：
- `period`
- `default_limit`

驱动。

典型例子：
- daily 10 次
- weekly 50 次
- custom_days 7 天 100 次

### 2.2 多窗口模式

由这些字段直接驱动：
- `daily_limit`
- `weekly_limit`
- `monthly_limit`

只要路由层传入 `multi_limits_provided=True`，service 就按多窗口模式处理。

## 3. 多窗口模式不是“择一”，而是“同时生效”

`check_quota()` 会为每个存在 limit 的窗口构造：
- `period`
- `period_key`
- `current`
- `limit`
- `remaining`
- `allowed`
- `reset_hint`

最终：
- `allowed = 所有窗口都 allowed`

所以：
- 日超了，即使周和月还有余额，也不允许
- 周超了，即使今日还有余额，也不允许

## 4. primary window 的选择规则

返回顶层：
- `current`
- `limit`
- `remaining`
- `period`

这些不是所有窗口的汇总，而是 `primary window` 的值。

选择顺序：
- 先 daily
- 再 weekly
- 再 monthly
- 否则取 windows[0]

这意味着前端如果只看顶层字段，会优先看到日额度。

## 5. `custom_days` 的 period key 生成方式

`custom_period_window()` 以：
- `1970-01-01`

作为 anchor，把当前日期映射到固定长度窗口。

period key 形如：
- `2026-03-12:7d`

reset hint 形如：
- `next_custom_window_start:2026-03-19`

这不是 rolling window，而是固定锚点分桶窗口。

## 6. `none` 的语义

如果 period 是 `none`：
- `period_key = unlimited`
- `reset_hint = never`

不过从当前实现看：
- `none` 更常用于 check 层展示无限制
- 真正 increment 的常见业务一般还是 daily/weekly/monthly/custom_days

## 7. override limit 如何参与计算

`_repo_get_user_override_limit()` 只能返回一个 `custom_limit`。

service 使用规则：

### 7.1 多窗口模式

如果已有 multi limits：
- override 会把所有“已存在的窗口 limit”统一替换成同一个 override 值

例如原来：
- daily 10
- weekly 50
- monthly 100

override=7 后会变成：
- daily 7
- weekly 7
- monthly 7

### 7.2 单窗口模式

直接覆盖单窗口 `default_limit`

这意味着 override 不是“按窗口分别覆盖”，而是一个统一上限。

## 8. `config_missing` 与 `config_active` 的真实意义

### 8.1 配置缺失

返回：
- `config_missing=true`
- `config_active=false`
- `allowed=true`

### 8.2 配置存在但 inactive

返回：
- `config_missing=false`
- `config_active=false`
- `allowed=true`

两者都允许通过，但语义不同：
- 一个是没配
- 一个是显式停用

## 9. create_config 的验证逻辑

关键验证包括：

- `default_limit >= 0`
- `quota_type` 必须匹配正则
- `quota_name` 长度 <= 128
- `period` 必须是允许值
- 如果是 active 且多窗口模式，至少一个窗口 limit 不能为 null

还有一个细节：
- 单窗口模式下，service 会自动把 `default_limit` 映射到对应 period 的 daily/weekly/monthly_limit 上

因此存到 DB 里时：
- 即使你只配了 single period，相关窗口字段也可能被补出一个值

## 10. update_config 的一个重要限制

如果前端没有走多窗口模式，只传：
- `default_limit`
- `period`

那 service 会：
- 清掉 daily/weekly/monthly 的其他值
- 只保留当前 period 对应的一个窗口值

所以前端更新方式会直接影响配置模型。

这也是为什么“简化管理 UI”其实有潜在破坏多窗口配置的风险。

## 11. reset_user_quota 怎么决定要清哪些 key

逻辑：

1. 先根据 config 决定当前有哪些窗口
2. 多窗口模式就清：
   - daily key
   - weekly key
   - monthly key
3. 否则按当前 period 算一个 key

这里 reset 不是清空所有历史 usage，而是清空“当前生效窗口对应的 period_key”。

## 12. 这套窗口模型的本质

quota 的配置模型本质是：

- 单窗口 period-limit 模式
- 多窗口并行约束模式
- 单个 override 统一覆盖模式

它足够灵活，但也带来一个要求：
- 所有接入方和管理端都必须真正理解“windows”而不是只理解顶层 `current/limit/remaining`。
