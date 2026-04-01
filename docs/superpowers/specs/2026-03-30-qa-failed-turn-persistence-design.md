# QA Failed Turn Persistence Design

> Scope: `fastQA` + `highThinkingQA` + `public-service` + `frontend-vue`
> Status: draft
> Date: 2026-03-30
> Updated: 2026-03-31

## 1. Background

当前系统已经把会话持久化 authority 收敛到 `public-service`，但 QA 失败场景仍然没有被建模成正式的 assistant turn。

这会导致两个用户可见问题：

- 用户提问通常已经落库，但失败 assistant 结果没有落库
- 前端流式过程中看见了失败文本，刷新后却消失

本 spec 解决的不是“多打一条日志”或者“临时把错误文本塞进前端缓存”，而是把 **failed/canceled assistant turn** 变成正式的会话模型，并让它通过 `public-service` 持久化、读取、刷新恢复、前端展示这整条链路闭环。

## 2. Confirmed Current-State Findings

以下结论来自当前代码实现，而不是推测。

### 2.1 User Turn Already Persists Early

- `fastQA` 在执行前写 user message
- `highThinkingQA` 在执行前写 user message
- `public-service` 已经是 authority store

结论：

- “失败问答刷新后消失”不是因为整次问答完全没落库
- 现在丢的是 **assistant failure turn**

### 2.2 Assistant Persistence Is Still Success-Gated

#### `fastQA`

`fastQA` assistant 持久化入口要求：

- `conversation_id` 有效
- `summary.done_seen == true`
- `assistant_content` 非空

也就是说，标准路径只接受“已完成 assistant summary”。

#### `highThinkingQA`

`highThinkingQA` assistant 持久化同样要求：

- `summary.done_seen == true`
- `assistant_content` 非空

流式异常时只发 `error` 事件，不会自动补一条 failed assistant 持久化。

### 2.3 `public-service` Authority Assistant Protocol Is Completion-Only

当前 authority schema 的 assistant final event 强制要求：

- `done_seen is True`
- `answer_text` 非空

这意味着当前内部 authority API 语义上是：

- “接收一个已完成 assistant turn”
- 不是“接收一个终态 assistant turn”

因此，光改 `fastQA/highThinkingQA` 还不够；如果 `public-service` 协议不改，失败问答仍然没有正式入口可写。

### 2.4 `public-service` Already Has an Assistant Async Inbox State Machine

`public-service` 当前已经具备：

- 在 `conversation_messages` 中插入 assistant 占位记录
- `assistant_async_state = pending / processing / failed / dead / done`
- `last_error` / `failed_at` / `dead_at` / `attempt_count`

但这套状态机当前服务的是：

- “已经完成的 answer 异步物化”

不是：

- “失败问答也作为正式 assistant turn 写入会话历史”

换句话说：

- 底层仓储已经有部分失败态基础设施
- 但 authority contract、service materialization、对外 detail 输出，还没有把失败 assistant turn 做成正式产品能力

### 2.5 `public-service` Detail Output Still Flattens Messages Toward `done`

当前 `public-service` 在多个路径里对外准备消息时：

- 默认把 message `status` 输出成 `done`
- 只透出 `done_seen` / `references` / `steps` 等成功路径常用字段
- 没有把 `terminal_status=failed/canceled` 建模为稳定的 detail payload

所以即使底层行里存在 inbox 的 `assistant_async_state=failed`：

- 会话 detail 也不会以“失败 assistant turn”的形式稳定返回给前端

### 2.6 `fastQA` Has a Risky Special Case

`fastQA` 在某些流式异常路径里会：

1. 先发 `error`
2. 再补一个 synthetic `done`

这会带来语义歧义：

- 如果前面已经有 partial content，系统可能把“失败但已有部分输出”的执行结果当成成功终态收口

这部分必须在改造里一起收敛，不然失败持久化语义会和真实执行结果不一致。

## 3. Problem Statement

当前系统缺少一个正式能力：

- 对于已经进入 QA 后端执行生命周期的请求，无论最终成功、失败还是取消，都必须能产出 **唯一一个 assistant terminal turn**。

现在的模型是：

- user turn 是 first-class
- assistant success turn 是 first-class
- assistant failed/canceled turn 不是 first-class

目标是把后两类也拉平为正式消息模型。

## 4. Goals

### 4.1 Product Goals

- 失败问答刷新后不消失
- 用户能在同一会话里看到完整的失败结果闭环
- 如果失败前已经产出部分答案，保留 partial answer
- 如果失败前尚未产出任何内容，也要保留结构化失败记录

### 4.2 Engineering Goals

- 会话 authority 仍然放在 `public-service`
- `gateway` 不接管问答持久化
- `fastQA` 和 `highThinkingQA` 仍然是执行 owner
- success / failed / canceled 三类终态都使用统一的 assistant terminal 抽象
- 每个 execution trace 最多只收敛成一个 terminal assistant turn

### 4.3 Non-Goals

- 不做 token 级持久化
- 不把 gateway precheck 拒绝全部落成对话消息
- 不做历史数据回填
- 不在 phase 1 重做整套前端消息 UI

## 5. Design Principles

1. 以 terminal turn 为持久化单位，而不是以 token stream 为单位。
2. once accepted, must close: 只要请求已经进入 QA backend 执行生命周期，就必须产生唯一 assistant 终态。
3. authority stays in `public-service`。
4. success / failed / canceled 必须是同层级概念。
5. 协议要允许“空内容失败”与“partial content 失败”同时存在。
6. 失败 turn 的读取模型必须和成功 turn 走同一条 detail/read API，而不是额外旁路查询。

## 6. Approaches Considered

### Option A: Extend Existing `assistant-async` Endpoint In-Place

做法：

- 直接放宽当前 `/internal/conversations/{id}/messages/assistant-async`
- 让 `final_event` 支持 `terminal_status=done|failed|canceled`
- 去掉 `done_seen must be true` 的硬约束

优点：

- 改动 surface 最小
- 客户端只改 payload，不需要新 endpoint

缺点：

- 现有命名和 schema 强烈暗示“completed final event”
- 风险是成功/失败语义混在同一个历史接口里，兼容判断更脆弱
- reviewer 和后续维护者更难区分新旧 contract

### Option B: Add a New `assistant-terminal-async` Internal Endpoint

做法：

- 保留现有 success-only endpoint 兼容老链路
- 新增 `/internal/conversations/{id}/messages/assistant-terminal-async`
- 用新的 `terminal_event` / `terminal_status` schema 表达 `done|failed|canceled`

优点：

- 语义清楚
- 可以阶段化迁移
- 更容易在 review/test 中明确 success-only vs terminal contract
- 避免把现有 success contract 改成“半兼容半重写”

缺点：

- 多一个内部 endpoint
- `fastQA/highThinkingQA` 客户端都要切

### Option C: Let `gateway` Persist Failure Messages

做法：

- `gateway` 收到 backend error / stream error 后直接写会话

优点：

- implementation 直觉上简单

缺点：

- 破坏现有 owner 边界
- gateway 会开始承担 execution terminal semantics
- 同一 assistant turn 可能被 gateway 和 backend 双写
- 与当前迁移方向冲突

## 7. Recommendation

推荐 **Option B**。

原因：

- 当前 `public-service` 的 assistant authority protocol 已经被代码和 schema绑定成“completed final event”语义
- 要把失败 turn 做成一等公民，最稳的是新增一个明确的 terminal contract，而不是把已有 success-only contract 做成语义漂移
- 这样还能保留向后兼容：旧链路继续工作，新链路逐步切换

## 8. Target Model

## 8.1 Terminal Status Enum

assistant terminal turn 支持以下状态：

- `done`
- `failed`
- `canceled`

语义：

- `done`: 正常成功结束
- `failed`: 后端执行失败或终态异常
- `canceled`: phase 1 仅表示明确的 stop/cancel 语义

完整定义上，未来也可以扩展到：

- 连接断开后显式取消
- 后端判定取消

但这些不属于 phase 1 的实现承诺

## 8.2 Assistant Terminal Message Shape

对外 detail 返回的 assistant message 应统一为：

```json
{
  "message_id": "m_000123",
  "role": "assistant",
  "content": "partial answer or final answer",
  "created_at": "2026-03-31T12:00:00+08:00",
  "status": "failed",
  "query_mode": "知识库问答",
  "steps": [],
  "references": [],
  "reference_objects": [],
  "reference_links": [],
  "pdf_links": [],
  "doi_locations": {},
  "done_seen": false,
  "metadata": {
    "trace_id": "trace-abc",
    "source_service": "fastQA",
    "route": "kb_qa",
    "requested_mode": "fast",
    "actual_mode": "fast",
    "terminal_status": "failed",
    "done_seen": false,
    "failure_stage": "llm_stream",
    "failure_code": "UPSTREAM_TIMEOUT",
    "failure_message": "model stream timed out",
    "retriable": true,
    "partial_content_chars": 821,
    "used_files": [],
    "timings": {},
    "steps": []
  }
}
```

## 8.3 Content Rules

### `done`

- `content` 通常非空
- `done_seen = true`

### `failed`

- 如果已有 partial answer，保留 partial content
- 如果还没开始输出，允许 `content = ""`
- `done_seen = false`
- 必须有 failure metadata
- 最低必须有：
  - `failure_message`
  - `retriable`
  - `failure_stage`，缺失时归一到 `unknown`

### `canceled`

- `content` 可为空或 partial
- `done_seen = false`
- 必须至少有 cancel message
- `retriable = false`

## 9. Failure Boundary Policy

### 9.1 Not Persisted as Conversation Turns

以下场景 phase 1 不落失败 assistant turn：

- 请求 payload 非法
- mode/path 不支持
- auth 失败
- gateway quota precheck reject
- gateway file provider reject
- 任何还没进入 QA backend execution owner 的失败

理由：

- 这些都还没进入 backend execution lifecycle
- 不应让 gateway 成为 assistant terminal turn owner

### 9.2 Must Persist as Failed/Canceled Assistant Turns

以下场景必须落失败 assistant turn：

- backend 已经完成 user write 后的 pre-execution fail
- authority context load fail
- runtime not ready
- retrieval/rerank/route execution fail
- LLM request timeout / stream interruption
- citation validation fail
- synthesis/postprocess fail
- explicit cancel / stop

判定原则：

- 只要 backend 已经接管本次执行，并且会话里的 user turn 已经写入 authority，就必须收口出唯一 assistant terminal turn

## 10. Public-Service Contract Design

## 10.1 New Internal Endpoint

新增：

- `POST /internal/conversations/{conversation_id}/messages/assistant-terminal-async`

用途：

- authority 接受来自 `fastQA` / `highThinkingQA` 的 assistant terminal event

## 10.2 Request Contract

```json
{
  "conversation_id": 456,
  "user_id": 123,
  "trace_id": "trace-abc",
  "source_service": "fastQA",
  "route": "hybrid_qa",
  "requested_mode": "thinking",
  "actual_mode": "fast",
  "idempotency_key": "456:trace-abc:assistant",
  "terminal_event": {
    "terminal_status": "failed",
    "done_seen": false,
    "answer_text": "partial answer if any",
    "failure": {
      "stage": "llm_stream",
      "code": "UPSTREAM_TIMEOUT",
      "message": "model stream timed out",
      "retriable": true
    },
    "steps": [],
    "timings": {},
    "used_files": [],
    "references": [],
    "reference_objects": [],
    "reference_links": [],
    "pdf_links": [],
    "doi_locations": {}
  }
}
```

## 10.3 Validation Rules

- `terminal_status` 必须是 `done|failed|canceled`
- `done`:
  - `done_seen = true`
  - `answer_text` 必须非空
- `failed|canceled`:
  - `done_seen = false`
  - `answer_text` 可空
  - `failed` 必须带 `failure` 对象
  - `canceled` 建议带 `failure` 对象；如果缺失，materialization 层必须补出最小 cancel message
- `failure` 最低字段约束：
  - `failed`: `message`、`retriable` 必填；`stage` 缺失时归一到 `unknown`
  - `canceled`: `message` 必填；`retriable=false`
- `idempotency_key` 继续冻结为当前 authority canonical format：
  - `{conversation_id}:{trace_id}:assistant`
- 新 terminal endpoint 与旧 success-only endpoint 必须共用同一 canonical key，禁止引入第二种 assistant key 格式

## 10.4 Compatibility

- 现有 `/messages/assistant-async` 保留
- 它继续承接旧 success-only 路径
- 新实现全部切到 `/messages/assistant-terminal-async`
- 旧 endpoint 后续可以内部委托到 terminal path 的 `done` 分支
- 旧 success-only endpoint 的兼容严格度不变：
  - `done_seen=true`
  - `answer_text` 非空
  - 输入 contract 不因 terminal path 引入而放宽

## 11. Public-Service Storage and Materialization

## 11.1 Keep Reusing `conversation_messages`

不新建 failed-turn 专用表。

继续复用：

- `conversation_messages`
- `metadata_json`
- 当前 assistant async inbox state machinery

理由：

- 现有仓储已经有 async assistant task 的 pending/failed/dead 状态基础
- 不需要再造第二套 outbox/inbox 体系

## 11.2 Inbox Placeholder Semantics

当前占位 assistant row 可以继续保留，但其 metadata 语义要升级：

- `authority_assistant_async = true`
- `assistant_async_state = pending|processing|done|failed|dead`
- `terminal_status = done|failed|canceled`
- `failure_*` 字段仅在失败/取消时存在

要区分两层状态：

1. **inbox processing state**
   - pending / processing / failed / dead / done
2. **assistant terminal business state**
   - done / failed / canceled

前者描述“物化任务跑没跑完”，后者描述“这次问答本身的终态是什么”。

## 11.3 Materialized Conversation Message Rules

真正出现在 conversation detail 的 assistant message 必须带：

- `status = terminal_status`
- `metadata.terminal_status = same value`
- `done_seen` 反映真实执行终态，而不是被统一强制成 true

也就是说：

- 对 `done` 消息，仍然是 `status=done`
- 对失败消息，不允许再在 detail path 中被扁平化成 `done`

## 11.4 Read Surface Rules

`public-service` 必须明确以下 read surface 的行为，避免“数据库里有 failed turn，但不同读接口各说各话”。

### A. Conversation Detail

- 返回完整 terminal assistant message
- 必须保留：
  - `status`
  - `terminal_status`
  - `done_seen`
  - `failure_*`
  - `steps/timings/references`
- 这是前端刷新恢复失败问答的 canonical read surface

### B. Cached Detail / JSON Mirror

- 必须与 conversation detail 保持相同的 terminal status 语义
- 不允许在缓存写回时把 `failed/canceled` 重新压平成 `done`

### C. Context Snapshot `recent_turns`

- 必须包含 failed/canceled assistant turn，保持真实时序
- 最低字段：
  - `message_id`
  - `role`
  - `content`
  - `created_at`
  - `trace_id`
  - `status`
  - `terminal_status`

### D. Context-to-LLM Projection

- `recent_turns` 是 authority truth
- 但构造实际 `chat_history` 输入给 LLM 时：
  - `status=done` 的 assistant 正常参与上下文
  - `status=failed/canceled` 的 assistant 默认不进入 LLM 对话上下文
  - 它们只对 routing/debug/history 可见

### E. Conversation List Preview / Summary

- list preview 可以展示失败 turn 的最后消息文本
- 若 `content` 为空，则使用最小预览文案：
  - `处理失败`
  - `已取消`
- preview 不展开完整 failure metadata，但必须保持终态类型正确

## 11.5 Message Count Policy

失败 assistant turn 是正式会话消息。

因此：

- 应计入 `message_count`
- 应更新 conversation `updated_at`
- 应参与 detail/list cache 刷新

## 12. QA Backend Responsibilities

## 12.1 fastQA

必须改成：

- success -> emit `done`
- failure -> emit terminal failed event for persistence
- cancel -> emit terminal canceled event for persistence

同时修正现有语义歧义：

- 不再通过 synthetic `done` 把 runtime exception 伪装成 success terminal
- stream 给前端的 `done` / `error` 和 authority persistence 的 terminal event 必须语义一致

## 12.1.1 Failure-Side Durability Ordering

这是 phase 1 的硬要求，不是 best-effort。

对于 backend-executed failed/canceled turn：

1. backend 必须先向 `public-service` 发送 terminal event
2. 只有在收到 authority `accepted/deduped` 结果后，才能向前端发 terminal `error` / `canceled` 结果
3. 不允许继续沿用“先把 error 给前端，再异步尝试失败持久化”的模式

原因：

- 如果还是 `error-first`，刷新竞争窗口仍然存在
- 用户仍可能在 terminal failure frame 到达后、authority write 完成前刷新页面

### Stream Path Rule

- stream 失败/取消时：
  - 先做 authority terminal accept
  - accept 成功后再发最终错误/取消 frame
- phase 1 的流式 transport 约束：
  - `failed` 继续走现有 `type=\"error\"` envelope
  - `canceled` 在 phase 1 也继续走现有 `type=\"error\"` envelope
  - 通过稳定的 cancel code 区分，例如 `error=\"cancelled\"` / `code=\"ASK_CANCELLED\"`
  - phase 1 不新增独立 SSE `type=\"canceled\"` 事件
- 这样可以避免同时改 gateway、QA backend、前端三处 SSE 终态枚举

### Sync Path Rule

- sync 失败时：
  - 先做 authority terminal accept
  - accept 成功后再返回最终错误 JSON

### Accept Failure Rule

- 如果 authority terminal accept 自身失败：
  - 后端仍返回原始执行错误给前端
  - 但必须额外记录 `terminal_persistence_unconfirmed`
  - phase 1 不承诺这类请求刷新后一定可见
- 这属于“持久化链路自身失败”，不属于正常失败问答闭环
- 监控和日志必须单独区分这类错误

## 12.2 highThinkingQA

必须改成：

- 在 stream/sync 两条 ask 路径里统一收集 terminal summary
- 异常或取消时也显式调用 failed/canceled assistant persistence
- 允许 empty content failure turn

## 12.3 Failure Metadata Taxonomy

推荐统一字段：

- `failure_stage`
- `failure_code`
- `failure_message`
- `retriable`
- `partial_content_chars`

推荐 stage enum：

- `authority_user_write`
- `authority_context_read`
- `runtime_prepare`
- `route_resolution`
- `retrieval`
- `rerank`
- `pdf_loading`
- `tabular_execution`
- `llm_request`
- `llm_stream`
- `citation_validation`
- `synthesis`
- `postprocess`
- `cancelled`
- `unknown`

phase 1 不要求所有老错误都完美归类，但新 terminal contract 必须为这些字段留位。

## 13. Frontend Requirements

## 13.1 Minimum Requirement for Phase 1

前端不需要先做复杂新 UI，最低要求是：

- 刷新后能看到 failed/canceled assistant turn
- 如果 `content` 非空，继续正常 markdown 渲染
- 如果 `content` 为空，展示一个最小失败壳：
  - 状态
  - failure_message
  - retriable

## 13.2 Message Model Compatibility

`frontend-vue` 当前已经能吃：

- `content`
- `metadata`
- `steps`
- `query_mode`

因此前端不是主阻塞点；只要后端 detail payload 正式返回失败消息，前端改造主要是：

- 识别 `status=failed/canceled`
- 在内容为空时给出 fallback shell
- 避免把失败 assistant turn误当“加载中”

## 14. Idempotency and Convergence Rules

### 14.1 Identity

assistant terminal idempotency key 继续按 trace 级唯一：

- `{conversation_id}:{trace_id}:assistant`

### 14.2 Convergence

同一个 key 最终只能收敛成一个 terminal business state。

推荐优先级：

- `done` > `failed` > `canceled`

规则：

1. 已有 `done`，忽略后续 `failed/canceled`
2. 已有 `failed`，重复 `failed` 幂等
3. 已有 `canceled`，后续 `failed` 可覆盖
4. 已有 `failed/canceled`，后续 `done` 可升级为 `done`

### 14.3 Upgrade Semantics

如果发生 `failed -> done` 或 `canceled -> done` 升级：

- 以 `done` 作为最终对外可见 terminal status
- `content/references/reference_links/pdf_links/doi_locations/steps/timings` 以 `done` 事件为准整体覆盖
- 失败态 metadata 不再作为当前 terminal truth 对外返回
- 但可以在内部日志中保留 upgrade 轨迹，便于排障

前端可见行为：

- 刷新后只看到最终 `done` 结果
- 不同时显示一条旧失败消息和一条新成功消息

这样可以应对：

- duplicate callbacks
- retry race
- slow terminal reconciliation

## 15. Rollout Phases

### Phase 1

- `public-service` 新增 assistant terminal internal contract
- `public-service` detail/read path 支持返回 `status=failed/canceled`
- `fastQA` 接入 terminal persistence
- `highThinkingQA` 接入 terminal persistence
- `frontend-vue` 最小化显示 failed/canceled assistant turn
- `canceled` 在 phase 1 只覆盖明确 stop/cancel 语义，不强行覆盖所有 disconnect 场景

### Phase 2

- 评估是否把 gateway precheck reject 也纳入 conversation history
- 仅在产品明确需要“全链路失败日志”时再做

## 16. Testing Matrix

### 16.1 public-service

- terminal `done` accepted and materialized
- terminal `failed` accepted with empty content
- terminal `failed` accepted with partial content
- terminal `canceled` accepted
- duplicate terminal event idempotent
- `failed -> done` convergence works
- detail payload returns `status=failed/canceled`
- legacy success-only endpoint still works

### 16.2 fastQA

- sync success persists `done`
- stream success persists `done`
- failure before first chunk persists `failed` with empty content
- failure after partial content persists `failed` with partial content
- cancel persists `canceled`
- runtime exception no longer materializes as fake `done`

### 16.3 highThinkingQA

- sync success persists `done`
- stream success persists `done`
- stream exception persists `failed`
- stop/cancel persists `canceled`
- refresh can reload failed assistant turn from authority history

### 16.4 frontend-vue

- failed assistant turn survives refresh
- empty-content failure still renders a visible error shell
- partial-content failure renders content + failure badge/message
- legacy successful history still renders unchanged

## 17. Risks

### Risk 1: Contract Split Complexity

新增 endpoint 会增加一点 surface area。

可接受，因为语义清晰性比少一个 endpoint 更重要。

### Risk 2: Old Success Path and New Terminal Path Diverge

需要明确：

- old success-only path 是 compat path
- new development 一律走 terminal path

### Risk 3: fastQA Synthetic Done Legacy Behavior

如果不一起修：

- 会出现“失败 turn 被误记成 done”的脏数据

这是本次改造的 P0 风险，不能留到后面。

## 18. Final Recommendation

实施时按以下顺序推进：

1. 先改 `public-service` contract + service/read model
2. 再改 `fastQA`
3. 再改 `highThinkingQA`
4. 最后补前端失败态显示与回归测试

原因：

- authority contract 是根
- 不先把 authority 的失败 terminal model 建好，QA backend 只能继续把失败语义塞进不合适的 success-only contract 里
