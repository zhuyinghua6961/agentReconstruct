# Internal Quota Grant 并行预占设计

## 1. 目标

修复当前交互式问答链路里“同一用户已有一个进行中的问答时，新的 fastQA / patentQA / fileQA 请求会被 quota 直接拦住”的问题，同时保持现有 canonical quota 模型、公开 API 路径和 admission 并发模型不分叉。

本次设计要解决的问题：

1. 让同一用户在同一个 canonical quota bucket 下可以同时持有多个进行中的 internal quota grant
2. 保证并行放开后不会因为 race condition 超卖 quota
3. 保持 ask/ask_stream 仍然走 `gateway -> public-service` 的 `precheck -> finalize` 闭环
4. 让 “额度已被进行中的请求预占完” 对外表现为正常 quota 不足，而不是 `GRANT_ALREADY_ACTIVE`

不在本次设计范围内：

1. 改造 `public-service` 里同步 `require_quota()` 依赖的行为
2. 新增 patent / fast / thinking 专属 quota bucket
3. 改动前端 quota 页面展示字段或新增 pending/reserved 展示
4. 改动 gateway admission 的并发调度策略

---

## 2. 当前事实基线

### 2.1 当前拦截点不在 admission，而在 internal quota grant

当前 `gateway` ask/ask_stream 会在转发上游前调用：

1. `POST /internal/quota/grants/precheck`
2. 上游业务执行
3. `POST /internal/quota/grants/{grant_id}/finalize`

`public-service` 当前的 `create_internal_quota_grant()` 会先做一次 `check_quota()`，然后再对 `(user_id, normalized_quota_type)` 获取一把活跃 grant 锁。

这把锁现在是互斥锁，因此：

1. 同一用户只要已经有一个活跃 `ask_query` grant
2. 第二个 `ask_query` precheck 即使理论上还有 quota 余额
3. 也会直接返回 `GRANT_ALREADY_ACTIVE`

所以现在 “patent kb_qa 正在问，再发一个 fastQA” 被拦住，不是因为 `gateway` admission 的 `fast_or_patent` 容量键，而是因为 `public-service` 的 internal quota grant 锁按 `user + canonical quota_type` 全互斥。

### 2.2 当前 canonical quota bucket 仍然只有 4 个

当前系统的 canonical quota type 只有：

1. `ask_query`
2. `file_qa`
3. `file_view`
4. `doc_assist`

本次设计不新增任何新 bucket，也不按 backend 名称拆 bucket。

### 2.3 当前 admission 已经负责总体并发上限

`gateway` admission 当前已有独立并发控制：

1. 全局 `INTERACTIVE_EXECUTION_MAX_CONCURRENT`
2. `fast_or_patent_max_concurrent`
3. `thinking_max_concurrent`

因此 quota 这一层不需要再承担“总体并发数限制”的职责。本轮只处理 quota 次数和活跃预占，不新增第二层并发上限。

---

## 3. 设计决策

### 3.1 internal quota grant 从“互斥锁”改为“并行预占”

本次设计把 `internal quota grant` 的语义从：

- 同一个 `(user_id, quota_type)` 只能同时存在 1 个活跃 grant

改成：

- 同一个 `(user_id, quota_type)` 可以同时存在多个活跃 grant
- 每个活跃 grant 在 precheck 成功时都会预占 1 个该 bucket 的可用额度位
- 后续 `finalize(success=true)` 才把这次预占落成已完成 usage
- `finalize(success=false)` 只释放预占，不记 usage

这意味着 internal grant 不再是“排他执行锁”，而是“成功候选名额保留”。

### 3.2 预占检查范围覆盖 gateway ask/ask_stream 的全部 canonical bucket

本次设计不是只修 `ask_query`，而是统一覆盖 `gateway` 通过 internal grant 调用到的所有 canonical bucket：

1. `ask_query`
2. `file_qa`
3. `file_view`
4. `doc_assist`

但本轮只改变 `internal quota grant` 这条链路的语义，不改变 `require_quota()` 直接使用的同步依赖行为。

### 3.3 预占绑定 precheck 时的 quota window

每个 internal grant 在 precheck 成功时必须锁定当时的：

1. canonical `quota_type`
2. `period`
3. `period_days`
4. `period_key`

后续 finalize 必须沿用这个 grant 自己的 window 信息记账，不能在 finalize 时重新按“当前时刻”计算 period window。

原因：

1. 避免请求跨天、跨周、跨自定义周期时，预占判断和最终记账不在同一个窗口
2. 避免 precheck 时允许、finalize 时却写进另一个新窗口，破坏额度一致性

### 3.4 quota 被活跃预占占满时，按正常 quota 超限返回

当某个用户某个 canonical bucket 的：

- 已完成 usage
- 加上活跃 grant 预占数量

已经达到 limit 时，新 precheck 直接返回现有的 `QUOTA_EXCEEDED` 语义，不再把这种情况当成 `GRANT_ALREADY_ACTIVE`。

本次设计明确规定：

1. `GRANT_ALREADY_ACTIVE` 不再作为正常业务拒绝路径
2. 前端和 gateway 继续按 quota exceeded 处理即可
3. 本轮不额外新增 `cause=reserved_exhausted` 之类的细分字段

### 3.5 quota 不再等待旧 grant 释放

当前互斥模型里，grant 获取会等待一小段时间尝试等旧锁释放。

本次设计改成预占后：

1. precheck 不再为“等待别人 finalize”而阻塞
2. 如果额度已被活跃 grant 预占完，直接失败
3. quota precheck 的目标变成稳定延迟，而不是抢占式等待

### 3.6 pending/reserved 本轮不对前端暴露

本轮修复只保证：

1. 并行请求不会被错误互斥挡住
2. quota 不会被超卖
3. 失败请求不会误扣 usage

但本轮不改：

1. `/api/quota/my`
2. `/api/quota/users/{id}`
3. 前端 quota 归一化和 quota 页面展示

因此 quota 页这轮仍只展示已完成 usage，不显示 pending/reserved。

---

## 4. 行为设计

### 4.1 precheck 的新判定规则

对非 exempt 用户，`create_internal_quota_grant()` 的 precheck 改为以下顺序：

1. 归一化 `quota_type`
2. 读取对应 quota config / override / limit
3. 基于当前时刻计算并锁定该 grant 的 `period_key`
4. 读取该 `user_id + quota_type + period_key` 的已完成 usage
5. 统计同一 `user_id + quota_type + period_key` 下所有活跃 pending grant 数量
6. 计算 `effective_used = completed_usage + active_pending_count`
7. 若 `effective_used >= limit`，返回 `QUOTA_EXCEEDED`
8. 否则创建一个新的 pending grant 并返回 `grant_id`

这里的 active pending grant 统计，不再通过单个互斥锁表达，而是通过 grant 自身的 pending 记录集合表达。

为了避免两个并发 precheck 同时看到旧快照而超卖，本次设计还要求：

1. precheck 的 “读取 usage -> 统计 active pending -> 写入新 pending grant” 必须放在一个短生命周期的 reservation decision critical section 内完成
2. 这把短锁的粒度是 `user_id + canonical quota_type + period_key`
3. 这把锁只用于创建 reservation 时的原子判定，不再覆盖整个请求执行期

也就是说，旧的长期活跃 grant 锁被取消，但必须保留一个仅用于 precheck 原子判断的短锁。

对 exempt/noop grant，本次设计明确要求：

1. exempt 用户仍可拿到 `grant_id`
2. `noop=true` 的 grant 不参与 active pending 数量统计
3. `noop=true` 的 grant 不占用 quota reservation 名额

为了防止实现阶段只改错误码或只改前端提示而不改真实判定链路，本次设计额外要求：

1. precheck 必须真实从 “completed usage + active pending reservations” 计算可用余额
2. 不允许仅把 `GRANT_ALREADY_ACTIVE` 改写成 `QUOTA_EXCEEDED` 而保留原互斥锁模型
3. 不允许只在 gateway 层吞掉 `GRANT_ALREADY_ACTIVE` 并伪装成 quota exceeded，而不改 `public-service` 的 reservation 判定
4. 不允许仅靠 mock 或测试桩伪造并行成功结果，真实 `create_internal_quota_grant()` 路径必须支持同 bucket 多个活跃 grant 并存

### 4.2 finalize(success=true) 的新语义

`finalize_internal_quota_grant(grant_id, success=True)` 必须：

1. 读取该 grant 创建时锁定的 `quota_type` 与 `period_key`
2. 以该 grant 自己的 window 进行最终 usage 增量写入
3. 记账成功后持久化 finalized result
4. 删除 pending grant
5. 停止该 grant 的续租/保活

同一个 `grant_id` 的 finalize 仍保持幂等：

1. 第一次成功 finalize 真实记账
2. 后续重复 finalize 直接返回已持久化 finalized result
3. 不能重复加 usage

为了让“按 precheck window 记账”可实现，本次设计要求实现阶段补齐一种显式写入旧窗口的能力，允许通过 service 内部参数或更底层 repo 调用把 usage 记到指定 `period_key`，而不是只能按 finalize 当下时间隐式计算当前 window。

这里的实现决策在本 spec 中明确锁定为：

1. 保持 repository 合同不变，继续复用现有 `increment_usage(user_id, quota_type, period_key)` 形式
2. 在 `QuotaService.increment_quota()` 增加一个仅供内部 finalize 路径使用的可选 anchored window 参数
3. 该 anchored window 参数至少包含 `period`、`period_days`、`period_key`
4. 正常调用不传该参数时，`increment_quota()` 继续按当前时刻计算 window，保持现有行为
5. internal grant finalize 路径必须传入 grant 创建时锁定的 anchored window

也就是说，本次实现必须把“锚定旧窗口记账”的入口收敛在 `QuotaService.increment_quota()` 内部扩展上，而不是让 finalize 直接绕过 service 去拼 repo 写入逻辑。

### 4.3 finalize(success=false) 的新语义

`finalize_internal_quota_grant(grant_id, success=False)` 必须：

1. 不增加 usage
2. 仍然持久化 finalized result
3. 删除 pending grant
4. 释放该 grant 的活跃预占

因此 ask/ask_stream 只要最终判定失败，就只会释放预占，不会消耗次数。

如果 finalize 前 grant 因续租失败或 TTL 失效已经被清理：

1. 该 grant 不应再继续占用 reservation
2. 后续 finalize 若找不到 pending grant，继续沿用现有 `NOT_FOUND`/已 finalized 幂等语义
3. 不为这类过期 grant 追加补记 usage

这条规则对应的用户可见合同也在本次设计中锁定：

1. 如果上游业务最终失败，而 finalize 因 grant 过期返回 `NOT_FOUND`，gateway 继续按失败路径处理，不补记 usage
2. 如果上游业务最终成功，但 finalize 因 grant 过期返回 `NOT_FOUND`，gateway 仍返回业务成功结果
3. 上述“业务成功但 reservation 已失效”的情况，gateway 必须像其他 finalize 失败一样把它降级为 quota warning，而不是把业务结果改成 5xx
4. 这种情况明确接受“成功回答但未记额”的结果，不做补记、不做回滚、不改成 fail-closed

为了避免空壳收尾，本次设计明确禁止以下替代实现：

1. 不允许 finalize 在 pending grant 缺失时偷偷补造一个 finalized 结果并补记 usage
2. 不允许 gateway 在 finalize `NOT_FOUND` 时重试一次新的 precheck 再补记
3. 不允许把 reservation 过期后的成功请求改成整体失败来回避 quota warning 处理

### 4.4 gateway 行为边界

`gateway` 本轮不需要改动公开 ask 协议，也不新增额外 quota 类型。

`gateway` 继续：

1. 在 ask/ask_stream 前调用 `precheck`
2. 在成功路径调用 `finalize(success=true)`
3. 在失败路径调用 `finalize(success=false)` 或 abort helper

变化只在于：

1. 同 bucket 并行 precheck 现在可能同时成功
2. precheck 失败主路径从 `GRANT_ALREADY_ACTIVE` 变成 `QUOTA_EXCEEDED`

### 4.5 admission 与 quota 的职责边界

本次设计明确保持：

1. admission 负责总体并发与 backend 容量上限
2. quota 负责次数额度与活跃预占

也就是说：

1. quota 不新增用户级活跃 grant 上限
2. quota 不新增 bucket 级并发 ceiling
3. “最多同时 5 个对话” 仍由 gateway admission / 现有并发配置负责

---

## 5. 数据与接口约束

### 5.1 不改公开 API 路径

本次设计不新增也不改动以下公开路径：

1. `gateway` 对外 ask / ask_stream 路径
2. `public-service` 对外 quota 查询路径
3. 现有 documents / conversation 公开路径

### 5.2 internal grant 接口形状尽量保持兼容

本次设计优先保持 internal 接口形状兼容：

1. `/internal/quota/grants/precheck`
2. `/internal/quota/grants/{grant_id}/finalize`

允许内部 pending grant 持久化 payload 新增字段，例如：

1. `period`
2. `period_days`
3. `period_key`
4. `reserved_at`
5. `config_limit`
6. `config_period`
7. `config_period_days`

但对 gateway 的响应结构不要求新增前端必须消费的新字段。

active pending 统计时，只统计满足以下条件的 grant：

1. `noop` 不为 `true`
2. `config_active` 为 `true`
3. grant 仍处于 pending 且未过期
4. `user_id + canonical quota_type + period_key` 与当前 precheck 完全一致

实现必须把这些字段真实持久化到 pending grant payload 中，至少包括：

1. `user_id`
2. `quota_type`
3. `noop`
4. `period`
5. `period_days`
6. `period_key`
7. `config_active`

不允许只在内存局部变量里短暂计算这些值，而不把它们写进 grant 持久化记录；否则 finalize、cleanup 和 TTL 恢复路径都无法验证真实 reservation 语义。

### 5.3 当前 `GRANT_ALREADY_ACTIVE` 只保留给异常态

实现上如果需要保留 `GRANT_ALREADY_ACTIVE` 常量或少量防御代码，只允许用于异常态，例如：

1. 内部状态损坏
2. grant 自身 identity 冲突
3. 非预期持久化竞争

不允许再把“同 bucket 有别的活跃请求”作为 `GRANT_ALREADY_ACTIVE` 主路径。

---

## 6. 验收标准

至少要有以下回归证据：

1. 同一用户已有进行中的 `patent kb_qa` 时，再发一个 `fastQA`，两个 `ask_query` precheck 都能成功
2. 同一用户已有进行中的 `pdf_qa` 时，再发一个同 bucket `file_qa`，只要 limit 允许，也能并行成功
3. 当 limit 为 1 时，第一个 grant 成功后，第二个同 bucket precheck 返回 `QUOTA_EXCEEDED`，不再返回 `GRANT_ALREADY_ACTIVE`
4. 两个并行 grant 都 `finalize(success=true)` 时，usage 总增量正确且不超卖
5. 并行 grant 中一个成功、一个失败时，只增加 1 次 usage
6. grant 跨过周期边界后 finalize，记账仍落在 precheck 锁定的 `period_key`
7. 现有 gateway ask/ask_stream 的 sync/stream quota 注入行为不变
8. 如果业务成功但 grant 因 TTL/续租失效导致 finalize `NOT_FOUND`，gateway 仍返回业务成功，并附带 quota warning
9. 前端 quota 归一化仍然只有 4 个 canonical bucket

除此之外，还必须保留以下“不是空壳”的实现证据：

1. 至少一组 `public-service` service 层测试直接证明两个同 bucket grant 能同时创建成功，前提是 quota limit 足够
2. 至少一组 `public-service` service 层测试直接证明 limit 为 1 时，第二个同 bucket grant 因 reservation 占满而返回 `QUOTA_EXCEEDED`
3. 至少一组 `gateway` 集成测试直接证明 `patent kb_qa` 进行中时，`fastQA` precheck 不再因 active grant 冲突失败
4. 至少一组 `gateway` 集成测试直接证明成功业务响应在 finalize `NOT_FOUND` 时仍然保留业务结果并带 warning

当前依赖互斥语义的旧测试不能简单删除，必须改写为新语义回归：

1. 原先断言 `GRANT_ALREADY_ACTIVE` 的重叠 grant 测试，应改成 reservation 占满 / 限额不足场景
2. 原先断言“等待旧 grant 释放后再成功”的测试，应改成“立即按 quota 余额判断”的新语义
3. 任何被删掉的旧互斥测试，都必须有新测试覆盖对应风险，不允许只通过删除旧断言来让测试变绿

---

## 7. 默认实现约束

本次设计默认采用以下实现约束，后续实现阶段不再重新决策：

1. 只改 `internal quota grant` 链路，不改 `require_quota()` 同步依赖
2. 不新增新的 public API、frontend API 字段或前端展示逻辑
3. 不新增 quota 级别的并发 ceiling
4. quota 余额被活跃 grant 占满时立即失败，不做等待与排队
5. 活跃 grant 的预占统计按 `user_id + canonical quota_type + period_key` 计算

## 8. 空壳实现禁令

以下做法一律视为未实现功能：

1. 只修改错误码映射或文案，把 `GRANT_ALREADY_ACTIVE` 包装成 `QUOTA_EXCEEDED`
2. 只修改 spec、plan、前端提示或日志，不修改 `public-service` 的 internal grant 判定与持久化
3. 只在 gateway 层做重试/吞错，绕过真正的 reservation 语义
4. 只补 mock 测试，不提供 service 层和 gateway 集成层的真实并行回归
5. 让两个请求“看起来都成功”，但 usage、warning 或 pending grant 清理语义不正确
