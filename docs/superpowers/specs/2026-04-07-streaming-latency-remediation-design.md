# Streaming Latency Remediation Design

**Date:** 2026-04-07

## Summary

本设计处理当前 QA 任务链路中的真实流式卡顿问题，覆盖：

- 发送问题后前端短暂卡住
- 流式输出期间前端持续卡顿
- `task-recovery` attach 判定链路高频重复触发
- 前端本地持久化在流式期间放大主线程压力
- gateway 在流式期间对 `public-service` 的 progress 写入过于频繁
- 首 token 前的任务创建 / 对话真相同步 / 上下文快照读取导致的额外延迟
- patent 模式答案把内部引用协议 `(patent_id=...)` 直接暴露给用户
- patent 模式在 `done` 后可能出现双份相同答案

本设计要求真实降低端到端时延和主线程压力，不接受“只改日志”“只调小 flush 频率”“只加占位节流参数但实际未接入”的空壳式修复。
如果定位过程中还需要更细粒度证据，可以直接加前后端详细诊断日志，并通过联调验证后再决定是否保留、降级或加开关。

---

## Scope

本设计覆盖以下真实链路：

1. `frontend-vue` 的 recoverable task 发送、attach、events 消费、流式渲染、本地持久化
2. `gateway` 的 task executor 对下游 SSE 的消费和 progress/terminal 持久化
3. `public-service` 的 authority task progress 写入热点
4. `fastQA` / `highThinkingQA` / `patent` 在首阶段读取 authority context snapshot 的共同延迟来源
5. `patent` 模式的引用输出协议、前端专利引用展示协议，以及 gateway task 模式下的 assistant 双重持久化风险

本设计不覆盖：

- 专利文件 QA / 混合 QA 答案过短问题
- DOI 内容正确性本身
- 引用展示样式重构
- admission 配额策略本身
- 非 QA 类长任务，如翻译、原文查看、文献辅助

---

## Problem Statement

当前系统已经支持 recoverable task，但流式性能存在明显退化，表现为：

1. 用户点击发送后，前端会卡一下，首个 token 到达前等待偏长。
2. 流式开始后，控制台持续出现 `task-recovery:attach:start`，说明 attach 判定链路被重复触发。
3. 流式期间前端会持续掉帧，严重时页面接近失去响应。
4. 后端流式期间存在大量 progress 写入，导致 gateway 和 public-service 都承受不必要的高频写压力。
5. 问题不是单纯后端模型慢，而是“前端重复工作 + 后端重复持久化 + 首阶段同步开销”叠加造成的系统性卡顿。

同时，2026-04-07 的新增 bug 报告又暴露出 patent 任务链路里的两个真实问题：

6. patent 引用协议把 `(patent_id=公开号)` 作为用户可见文本直接输出，导致内部字段名泄露、列表文本混乱、引用可读性差。
7. patent 在 gateway task 模式下缺少 gateway-owned persistence 旁路，`done` 后可能把同一条 assistant 答案写两次，最终在主内容区出现双份相同答案。

---

## Evidence

以下证据已经从现有代码确认，不是猜测。

### 1. 前端流式期间会重复进入 attach 判定链

文件：`frontend-vue/src/views/Home.vue`

`watch(() => ({ chatId, taskId, status }))` 依赖 `store.currentChat`：

- `store.currentChat` 是 `computed(() => chats.find(...))`
- 流式期间 `messages` 不断变化，会持续触发 `currentChat` 相关依赖失效
- watch getter 每次返回一个新对象，即使 `taskId/status` 不变，也会重复触发回调

结果：

- 流式期间会不断重新进入 `attachRecoverableTask()` 判定链
- 即使最终被 runtime / lock 挡住，也会持续产生额外判断、日志、状态读取和局部抖动

### 2. 发送后立即做 replace-sync，会额外拉长首 token 前延迟

文件：`frontend-vue/src/utils/recoverableTaskController.js`

`sendTaskMessage()` 在 `createTask()` 成功后立即：

- `finishChatBusyRuntime()`
- `attachRecoverableTask(... replaceMessagesFromServer: true)`

而 attach 链路在 `replaceMessagesFromServer=true` 时会先执行 `refreshConversationTruth()`：

- 拉取整段 conversation detail
- 重写本地消息数组
- 持久化本地状态

这条链路发生在真正进入稳定流式消费之前，直接增加发送后的等待时间。

### 3. 前端每个 task event 都会强制写 localStorage

文件：`frontend-vue/src/utils/recoverableTaskController.js`

在 `api.streamTaskEvents(... onEvent)` 内：

- 每收到一个 event，都执行 `store.updateChatTaskReplayCursor(...)`
- 然后立刻执行 `store.persistLocalState()`

文件：`frontend-vue/src/stores/chatStore.js`

`persistLocalState()` 会走 `saveChats({ force: true })`，最终同步写 `localStorage`。

结果：

- token 越碎，强制持久化越频繁
- 主线程频繁做 `sanitizeChats + JSON.stringify + localStorage.setItem`
- 这是前端持续卡顿的核心热点之一

### 4. 前端 `refreshConversationTruth()` 也会立即持久化

文件：`frontend-vue/src/views/Home.vue`

`refreshConversationTruth()` 在更新：

- `messages`
- `pdf_list`
- `excel_list`
- `activeTask`

之后，直接执行 `store.persistLocalState()`。

如果 attach/recovery 路径被频繁走到，就会把这部分开销放大。

### 5. gateway 在每个流式事件上都执行 progress 持久化

文件：`gateway/app/services/qa_tasks.py`

在 task executor 中：

- `thinking` 事件调用 `_sync_progress_best_effort()`
- `step` 事件调用 `_sync_progress_best_effort()`
- `content` 事件调用 `_sync_progress_best_effort()`

这意味着当前实现接近“每个 token 一次 progress 持久化”。

### 6. progress 持久化不是轻操作，而是 authority document 写路径

文件：`gateway/app/services/conversation_persistence.py`

`progress_task_assistant()` 会调用内部 authority API：

- `/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-progress`

文件：`public-service/backend/app/modules/conversation/service.py`

`progress_authority_task_assistant()` 会：

- 获取 conversation lock
- 加载 runtime document
- 查找 placeholder assistant message
- 拼接 `content_delta`
- 写回 metadata / steps / last_seq
- 刷新文档和缓存

这不是轻量计数器更新，而是一条真实文档持久化链路。把它放在每个 token 上，本身就是高成本设计。

### 7. 首 token 前上下文快照读取在三种 QA 模式里都位于主链路

文件：

- `fastQA/app/routers/qa.py`
- `highThinkingQA/server/services/ask_service.py`
- `patent/server/services/chat_persistence.py`

现状：

- `fastQA` 在执行前会 `_load_conversation_context_if_needed()`
- `highThinkingQA` 在 `_prepare_execution()` 中构建 conversation context
- `patent` durable ask 也会读取 authority snapshot

说明：

- 上下文快照读取会影响首 token 延迟
- 但它更像“发送后首段等待”问题，不足以解释整个流式阶段的持续卡顿
- 持续卡顿的更大来源仍是前端重复 attach / 前端强制持久化 / gateway 高频 progress 写

### 8. patent 当前是故意输出 `(patent_id=...)`，不是偶发脏数据

文件：`patent/server/patent/answering.py`

现状：

- `sanitize_patent_id_citations()` 会把合法引用保留为 `(patent_id=CN...)`
- prompt 明确要求“引用必须使用 `(patent_id=公开号)`”
- 最终答案约束也明确要求“每个关键结论后补 `(patent_id=公开号)`”

这说明：

- bug 报告中的 `patent_id=` 泄露不是模型偶发跑偏
- 是当前 patent 输出协议本身就把内部字段名暴露给了前端

### 9. 前端当前也会把 `patent_id=` 原样展示给用户

文件：`frontend-vue/src/utils/index.js`

现状：

- `linkifyPatentTextSegment()` 把 `(patent_id=CN...)` 渲染成可点击链接
- 但链接文本仍然是 `patent_id=CN...`

这意味着：

- 即使前端已经把它变成链接，用户看到的仍是内部字段名
- 这是展示协议设计错误，不是样式问题

### 10. 前端 Markdown 规范化对“段内列表标记”处理不够

文件：`frontend-vue/src/utils/index.js`

现状：

- `normalizeMarkdownForRender()` 会修复行首列表
- 但它不会把“中文冒号后直接跟 `- 条目`”这种段内列表强制拆成新行列表

这意味着：

- 如果 patent/QA 输出为 `如下：- 充电上限... - 放电下限...`
- 前端会把它保留成段落中的纯文本，而不是渲染成真正列表

### 11. gateway task 模式下，patent 缺少 gateway-owned persistence 旁路

对照文件：

- `gateway/app/services/qa_tasks.py`
- `fastQA/app/routers/qa.py`
- `patent/server/services/chat_persistence.py`
- `patent/server/services/ask_service.py`

现状：

1. gateway task executor 会给下游请求附带：
   - `X-Gateway-Task-Execution: 1`
   - `X-Gateway-Owned-Persistence: 1`
2. `fastQA` 明确识别该标记，并跳过自有 user/assistant persistence。
3. `patent` 没有对应的 gateway-owned persistence 检测。
4. `PatentAskRequest.persistence_mode` 只由 `conversation_id` 决定，只要有 conversation_id 就进入 durable 模式。
5. durable 模式下，`ChatPersistenceService.prepare_turn()` 会写 user turn，`finalize_turn()` 会再 accept assistant turn。

结果：

- gateway task 路径已经创建 placeholder assistant 并负责 terminal 持久化
- patent durable 路径又会再写一轮 authority assistant
- `done` 后一旦 conversation truth 被刷新，就可能在主内容区看到第二条相同答案

这类问题在外观上表现为“双份答案”，但根因是 patent 路径的双重持久化，而不是简单的 DOM 重绘。

---

## Goals

### 产品目标

1. 发送问题后，前端不再出现明显卡住再开始流式的体验。
2. 流式输出期间页面保持可交互，不出现持续性卡顿或频繁 attach 抖动。
3. recoverable task、刷新恢复、事件回放、自动结束等功能继续有效。
4. 修复必须是真实行为改进，不接受表面降噪但主链路仍高频重活的做法。
5. patent 模式输出对用户必须使用可读引用展示，不能再暴露 `patent_id=` 这类内部字段名。
6. patent gateway task 模式在 `done` 后必须只保留一条最终 assistant 答案。

### 性能目标

1. attach 主链路在同一 chat / task 的稳定流式期间不得被重复触发。
2. 前端本地状态持久化从“每 event 强制写”降低为“批量或节流写”。
3. gateway progress 同步从“每 token 写一次”降低为“受控批量刷新”。
4. terminal `done/error/canceled` 后仍然保证最终状态立即落稳，不因节流丢失终态。
5. 首 token 前同步链路在不破坏一致性的前提下减少不必要全量 detail 刷新。
6. patent 引用显示协议从“内部标记直出”收敛为“内部协议可保留、用户展示脱敏”。
7. patent task 路径不得再发生 gateway 与 patent backend 的 assistant 双写。

### 验收目标

1. 连续流式期间，前端控制台不再持续出现高频 `attach:start`。
2. 长答案流式阶段，页面主观卡顿明显下降。
3. 单任务输出过程中，gateway 到 public-service 的 progress 写次数显著下降。
4. 刷新恢复、切换会话、终态收敛、重复事件去重仍正确。
5. patent 答案正文中不再出现 `patent_id=` 这样的内部协议字段名。
6. patent 普通问答在完成瞬间不会多出第二条相同答案。

### 量化验收预算

以下预算用于避免“感觉变快了但主链路仍未收敛”的伪验收。

1. 同一 `(chatId, taskId)` 的稳定流式生命周期内：
   - 正常路径 `attach:start` 最多 `1` 次
   - 若发生一次明确 fallback/recovery，最多额外 `1` 次
   - 总 attach 次数上限为 `2`
2. 健康后端热路径下，前端 `createTask` 成功到开始稳定消费 task events 的时间目标为 `<= 300ms`。
3. 单任务连续 `100` 个 `content` 事件窗口内：
   - 前端 `persistLocalState()` 实际调用次数必须 `<= 20`
   - gateway progress flush 次数必须 `<= 20`
4. `progress flush / content event` 比值目标不高于 `0.2`；终态强制 flush 可额外增加 `1` 次，不计入普通窗口预算。
5. terminal 到前端退出“生成中”状态的收敛时间目标为 `<= 500ms`，且不得再继续消费重复文本增量。

---

## Non-Goals

1. 不改为 WebSocket。
2. 不移除 recoverable task 架构。
3. 不取消 authority context snapshot。
4. 不把 progress 完全改成“只在 done 时写一次”。
5. 不在本阶段做完整虚拟列表重构。

---

## Constraints

1. 必须保留刷新后可恢复的 task 语义。
2. 必须保留 `last_seq` 回放和重复事件去重。
3. 必须保留终态消息与 authority conversation truth 的一致性。
4. 必须兼容 `fast` / `thinking` / `patent` 三个模式。
5. 必须避免“节流后 done 没落盘”或“刷新后丢失最新内容”的回归。
6. 必须是真正修复根因，不能靠 UI 占位、日志掩盖、延后刷新、降低采样来伪装问题消失。
7. 如果现有证据不足以判定具体故障点，允许直接加详细日志，并把日志设计纳入实现与测试要求。

---

## Approaches

### 方案 A：只降低前端 flush 频率

做法：

- 增大 `requestAnimationFrame` 之外的文本刷新间隔
- 减少 debug 日志

优点：

- 改动最小

缺点：

- 没有解决重复 attach
- 没有解决每 event 强制持久化
- 没有解决 gateway 高频 progress 写
- 只是把症状延后

结论：拒绝。

### 方案 B：只修前端

做法：

- 修正 watch 依赖
- 本地持久化节流
- 优化 replace-sync

优点：

- 风险较低

缺点：

- 后端每 token progress 写仍会持续施压
- 首 token 前等待和流式期后端写放大仍存在

结论：不足以完整解决问题。

### 方案 C：前后端同时收缩重复工作

做法：

- 前端修正 attach 触发条件
- 前端把 replay cursor / 本地状态持久化改成批量节流
- 前端把发送后 replace-sync 改为最小必要同步
- gateway 引入 progress 聚合器，按时间窗/字节窗/事件窗刷写 authority
- patent 输出协议与前端显示协议解耦
- patent gateway task 路径增加 gateway-owned persistence 旁路
- terminal 事件强制 flush，保证最终一致性

优点：

- 可以同时降低“发送后卡一下”和“流式期间持续卡”
- 可以同时修掉 patent 的引用脏展示和双份答案
- 修复真实主链路热点
- 不改变现有任务模型

缺点：

- 改动跨 frontend / gateway / public-service 交界
- 需要更严谨的回归测试

结论：采用本方案。

---

## Recommended Design

采用“前端去重 + 前端持久化降频 + gateway progress 聚合 + 首阶段同步瘦身”的组合修复。
同时纳入 patent 引用显示协议收口和 patent gateway task 双写修复，避免把这两个 bug 留在同一条任务链路之外。

### Design Principle 1: attach 只能由真实任务身份变化触发

前端必须把“是否需要 attach”收敛到真实 task identity 变化，而不是依赖整个 `currentChat` 的响应式波动。

要求：

1. attach watch 只订阅稳定标量：
   - `currentChatId`
   - `activeTask.task_id`
   - `activeTask.status`
   - 必要时 `activeTask.replay_available`
2. watch 不能再因为 `messages` 数组变化而重复触发。
3. 同一 `(chatId, taskId)` 在 runtime 活跃期间，只允许：
   - 初次 attach
   - 明确 replace-sync attach
   - 明确 fallback/recovery attach
4. 普通流式内容增长不得触发新的 attach 尝试。

### Design Principle 2: 前端本地持久化只能批量执行

前端当前最大的结构性问题之一，是 task event 每来一次就 `persistLocalState()`。

要求：

1. 将 replay cursor 更新与本地持久化解耦：
   - cursor 可以高频更新内存态
   - 持久化必须经过统一的 task-recovery persist scheduler
2. scheduler 至少满足：
   - 时间窗节流
   - 终态强制 flush
   - 页面卸载前强制 flush
3. `refreshConversationTruth()` 不得在每次调用后无条件立即强制持久化；应改为：
   - 标记 dirty
   - 交给统一 scheduler
   - 终态/切换关键点再强制 flush

### Design Principle 3: 发送后只做最小必要同步

`createTask()` 后立即 `replaceMessagesFromServer` 是首 token 前卡顿来源之一。

要求：

1. 新创建任务后，优先使用 `taskSummary + 本地 placeholder` 启动 attach。
2. 只有在以下情形才需要额外 detail sync：
   - 本地无 assistant placeholder
   - 需要补齐 authoritative `active_task`
   - 发生 fallback/recovery
3. 普通“发送成功后立即开始流式”的主路径，不应先全量刷新 conversation detail 再开始消费 events。

### Design Principle 4: gateway progress 必须聚合刷写

gateway 当前对 `thinking/step/content` 的 progress 同步过于频繁，需要收缩为聚合模型。

要求：

1. 为每个 active task 引入 progress accumulator：
   - 累积 `content_delta`
   - 追踪最新 `steps`
   - 记录最新 `last_seq`
   - 记录最后一次写入时间
2. flush 触发条件至少包含：
   - 到达时间窗
   - 到达最小字节阈值
   - step/thinking 状态变化需要尽快落盘
   - `done/error/canceled` 前强制 flush
3. terminal 事件时必须按顺序执行：
   - 先 flush 剩余 progress
   - 再写 terminal
   - 再 finalize quota

### Design Principle 5: public-service 不承担 token 级写频率

本设计不要求 public-service 改协议，但要求 gateway 不再把 token 级事件直接映射为 authority progress 写。

public-service 仍保留：

- progress API
- terminal API

但它接收到的 progress 调用频率应显著下降，回到“阶段性快照同步”而不是“token 传输通道”。

### Design Principle 6: 内部引用协议不能直接暴露给终端用户

patent 模式可以在内部继续使用结构化专利引用标记，但用户展示层不能直接看到 `patent_id=...`。

要求：

1. backend 内部引用协议与前端显示协议分离。
2. 用户最终看到的专利引用至少应显示为：
   - `CN118645714A`
   - 或 `专利号 CN118645714A`
3. 前端点击行为保持不变，仍可打开专利原文。
4. 不能因为脱敏显示而丢失专利点击能力。
5. `patent_id=` 只允许存在于内部 prompt / 中间解析 / 内部日志，不允许出现在任何用户可见载荷中，包括：
   - 流式 SSE `content`
   - authority progress 内容
   - authority terminal 最终内容
   - 刷新恢复后的 replay/overlay 内容
   - 前端最终渲染文本

### Design Principle 7: gateway-owned task 只能有一个 authority assistant writer

只要请求已经进入 gateway-owned task 路径，下游 backend 就不能再独立向 authority 写最终 assistant turn。

要求：

1. `fastQA`/`highThinkingQA`/`patent` 在 gateway-owned task 路径下语义对齐。
2. patent 必须增加 gateway-owned persistence 检测与旁路。
3. 在 gateway-owned 模式下：
   - patent 不再自写 user/assistant authority turn
   - gateway 继续作为唯一 placeholder/progress/terminal authority writer
4. cached replay / overlay 能力不能因为旁路而退化为假恢复。
5. 同一 `task_id/trace_id` 在 authority 中最终必须满足：
   - 恰好 `1` 条 user turn
   - 恰好 `1` 条 assistant turn
   - 不存在额外 pending-turn residue
   - 不存在 refresh 后再次冒出的 overlay duplicate

---

## Detailed Design

### Frontend

#### F1. 收窄 attach watch 依赖

修改目标：

- `frontend-vue/src/views/Home.vue`

设计：

1. 将当前对象式 watch 改为稳定 tuple / 标量 watch。
2. 不再通过 `store.currentChat` 间接订阅整个会话对象。
3. attach 判定前增加本地“上次已附着 task identity”比较，避免相同身份重复进入 attach。

#### F2. 引入 task recovery persist scheduler

修改目标：

- `frontend-vue/src/utils/recoverableTaskController.js`
- `frontend-vue/src/views/Home.vue`
- `frontend-vue/src/stores/chatStore.js`

设计：

1. 新增面向 task recovery 的轻量持久化调度器。
2. `onEvent` 内只更新内存态，不再每 event 立即 `persistLocalState()`。
3. `state` 事件、普通 `content` 事件、普通 replay cursor 前进均走节流持久化。
4. `done/error/canceled`、显式 detach、页面卸载等时机强制 flush。

#### F3. 缩小发送后同步范围

修改目标：

- `frontend-vue/src/utils/recoverableTaskController.js`
- `frontend-vue/src/views/Home.vue`

设计：

1. `sendTaskMessage()` 创建任务后，默认先用 `taskSummary` 和本地 runtime 建立流式消费。
2. 只有在缺少 authoritative assistant message / active_task 绑定的情况下才触发 replace-sync。
3. recovery/fallback 流程仍保留从 conversation truth 校准的能力。

#### F4. 保留现有 requestAnimationFrame flush，不做伪优化

当前文本 flush 已经是 `requestAnimationFrame` 驱动，本阶段不把它当主修复点。

只要求：

1. 保持现有 RAF 文本拼接逻辑。
2. 先解决 attach 重复和持久化写放大。
3. 如果后续仍有瓶颈，再进入二期渲染层优化。

#### F5. 专利引用展示脱敏

修改目标：

- `frontend-vue/src/utils/index.js`
- 相关 streaming / render 测试

设计：

1. 新链路下，frontend 不应再依赖用户可见文本中出现 `(patent_id=CN...)` 才能完成引用渲染。
2. 用户可见内容进入前端时，应已经是可读引用文本；frontend 负责：
   - linkify 可读专利号
   - 绑定 `data-patent-id`
   - 保持 reader 打开逻辑不变
3. 若历史消息或旧数据中仍残留 `(patent_id=CN...)`，可保留兼容性识别作为迁移兜底，但它不能再是新链路的主契约。

#### F6. 修复段内列表的 Markdown 归一化

修改目标：

- `frontend-vue/src/utils/index.js`

设计：

1. 对 `：- 条目`、`:- item`、`；- 条目` 等模式做轻量拆行归一化。
2. 只在明确的段内列表起始模式上生效，避免误伤普通连字符文本。
3. 保证 patent/QA 结果里常见的“结论如下：- A - B”能转成真正列表。

### Gateway

#### G1. 为 active task 增加 progress accumulator

修改目标：

- `gateway/app/services/qa_tasks.py`

设计：

1. executor 在读取下游 SSE 时，不再每个事件直接 `_sync_progress_best_effort()`。
2. 维护每 task 的聚合状态：
   - pending content delta
   - latest steps snapshot
   - latest seq
   - latest status
   - last flushed at
3. 在 flush 条件达成时统一调用 progress sync。

#### G2. 设计 flush policy

flush policy 需要同时兼顾体验与一致性。

推荐策略：

1. `content`：
   - 按时间窗 flush
   - 或达到字节阈值 flush
2. `thinking/step`：
   - 更新 accumulator
   - 若阶段发生变化，允许更快 flush
3. `done/error/canceled`：
   - 先强制 flush progress
   - 再 terminalize

#### G2.1 明确 `persisted_last_seq` 与 replay 语义

聚合写入不能破坏 recoverable replay，因此必须定义清楚 authority 侧“已经持久化到哪里”。

要求：

1. gateway accumulator 维护两个序号：
   - `observed_last_seq`：下游 SSE 已收到的最大 `seq`
   - `persisted_last_seq`：已成功写入 authority progress 的最大 `seq`
2. 每次 progress flush 成功后，authority 中的 assistant progress 内容必须完整覆盖所有 `seq <= persisted_last_seq` 的用户可见文本与步骤状态。
3. flush 失败时，不得错误推进 `persisted_last_seq`；只能保留在上一次成功值。
4. 刷新/重连时：
   - 前端先以 authority truth 恢复到 `persisted_last_seq`
   - 再从事件流继续消费 `seq > persisted_last_seq` 的后续内容
5. 若刷新发生在聚合器仍有未 flush 文本期间，允许前端临时看不到那一小段未持久化内容；但恢复后不得重复拼接已持久化区间。
6. terminal 前强制 flush 成功后，必须满足：
   - `persisted_last_seq == terminal_last_seq`
   - authority terminal 内容等于最终完整 assistant 答案

#### G3. 保证 terminal 顺序正确

必须保证：

1. 所有尚未落盘的 `content_delta` 在 terminal 前被吸收。
2. terminal 写入后的 authority message 为最终版本。
3. queue terminal reconcile 逻辑继续兼容聚合模式。

#### G4. 明确 patent gateway-owned persistence 契约

修改目标：

- `gateway/app/services/qa_tasks.py`
- 与 patent downstream contract 对应的文档/测试

设计：

1. gateway 继续在 task executor 请求头中显式传递 gateway-owned persistence 标记。
2. 将 patent 纳入与 fast/highThinking 一致的“下游不得自持久化”契约。
3. 验证 gateway 仍是唯一 authority assistant writer。

### Public-Service

#### P1. 协议保持兼容

本阶段不新增 authority progress API。

要求：

1. 继续兼容现有 `assistant-progress` 请求结构。
2. 通过 gateway 降频，而不是把复杂度下推到 public-service。

#### P2. 验证锁竞争和文档刷新压力下降

虽然不改 public-service 协议，但需要验证：

1. `conversation_lock` 占用频率下降。
2. detail cache 刷新频率下降。
3. 同一 task 的 progress 写次数明显减少。

### QA Backends

#### B1. 不改上下文快照协议

本阶段不取消：

- `fastQA` authority context read
- `highThinkingQA` authority snapshot build
- `patent` durable context snapshot

但需要在 spec 中明确：

1. 首 token 延迟优化只做“避免发送后额外 replace-sync 全量 detail”。
2. context snapshot 主链路优化属于下一层专项，可单独出 spec。

#### B2. patent 增加 gateway-owned persistence 旁路

修改目标：

- `patent/server/services/ask_service.py`
- `patent/server/services/chat_persistence.py`
- 如有需要，`patent/server/schemas/request_models.py` 或 request context 辅助层

设计：

1. patent 识别 gateway 内部请求头：
   - `X-Gateway-Task-Execution`
   - `X-Gateway-Owned-Persistence`
   - 内部 service token
2. gateway-owned 模式下，patent durable 路径不再执行 authority user/assistant accept。
3. patent 仍可保留：
   - 本地执行缓存
   - cached replay
   - context snapshot 读取
   但 authority assistant 终态写入由 gateway 独占。
4. 必须验证不会破坏：
   - 非 gateway 直连 patent durable ask
   - patent cached replay
   - patent overlay / pending turn 状态清理

#### B3. patent 引用协议与显示协议分层

修改目标：

- `patent/server/patent/answering.py`
- frontend render 层

设计：

1. backend 内部可以继续保留 `(patent_id=...)` 作为规范化中间协议。
2. 对用户可见答案，需要有清晰层次：
   - 内部可解析
   - 对外可读
3. 本次必须建立端到端用户可见契约：
   - 若 backend 输出进入 gateway/public-service/frontend 任一用户可见面，必须先转成可读引用
   - 任何用户可见持久化内容都不得依赖“仅前端渲染时再遮掉前缀”才能正确
4. 推荐做法：
   - backend 仍可维护可解析标记
   - 但进入用户可见流之前，需要统一转成 `CN...` 或 `专利号 CN...`
   - frontend 只负责 linkify，不承担唯一的数据清洗职责
5. 如果后续希望彻底替换协议，应另出 contract spec；本次先修所有用户可见面的问题。

---

## Failure Handling

1. 如果前端节流持久化过程中页面异常关闭，允许丢失少量“最新 replay cursor 持久化”，但不能丢失 authority 已写入的终态消息。
2. 如果 gateway accumulator flush 失败：
   - 记录 warning
   - 标记 progress sync pending
   - 继续沿用已有 reconcile 机制补偿
3. 如果 terminal 前强制 flush 失败：
   - terminal 仍必须继续写入
   - 待补偿内容只能通过 terminal 最终答案兜底，不能阻塞终态收敛

---

## Implementation Integrity

本次修复必须是实修，不接受以下伪修复：

1. 只关掉 debug 日志，但 attach 仍高频触发。
2. 只把前端 flush 文本改慢，但每 event `persistLocalState()` 仍存在。
3. 只在 gateway 增加“节流配置项”，但 executor 仍每 token 调 progress sync。
4. 只在 public-service 忽略重复 `last_seq`，但 gateway 仍继续高频发请求。
5. 只优化单一模式，其他模式仍走高频写路径。
6. 只把 patent 的 `patent_id=` 文本替换掉，但 authority 双写 assistant 的根因还在。
7. 只通过延迟、隐藏或局部去重让第二条答案“看起来不见了”，但 authority 中仍真实写入两条 assistant。
8. 只在前端渲染层硬编码忽略一条消息，而不修复 patent gateway-owned persistence 缺失。

真实达标标准：

1. 同一 task 流式期间不再重复 attach。
2. 前端本地持久化不再按 event 级别强制同步写入。
3. gateway progress 持久化降为聚合刷写。
4. terminal 一致性、刷新恢复、回放去重全部继续成立。
5. patent 引用对用户可读，但内部点击和原文定位能力不丢。
6. patent 在 gateway task 模式下 authority 中只保留一条对应 assistant 终态消息。

### Diagnostic Logging Policy

本次修复允许为了判定根因和验证修复而增加详细日志。

要求：

1. 日志必须服务于真实故障定位，不能为了“看起来有动作”而滥加。
2. 日志优先加在组件边界和状态边界：
   - 前端 attach 判定、task event 消费、done 收敛、conversation truth refresh
   - gateway progress flush、terminal flush、quota finalize、task replay/relay
   - patent authority accept / terminal accept / gateway-owned persistence 旁路判定
3. 日志应尽量结构化，至少包含：
   - `chatId` / `conversation_id`
   - `task_id` / `trace_id`
   - `seq` / `last_seq`
   - 当前状态和关键分支原因
4. 调试日志应优先使用现有 debug flag 或新增显式开关，避免默认长期噪声。
5. 如果某一批日志是为联调临时加的，impl 文档中必须标注：
   - 保留为长期诊断日志
   - 或在验证后降级/删除

### Correlation ID Contract

前后端联调已经证明，仅靠局部日志无法稳定追踪重复 attach、重复回放、双重持久化和 terminal 收敛问题，因此本次必须统一关联标识。

要求：

1. `task_id` 作为本次链路的规范关联 ID。
2. `createTask()` 返回前的前端预提交阶段，允许先使用 `client_request_id` 记录日志；一旦拿到 `task_id`，必须立即补齐 `client_request_id -> task_id` 映射，并在后续日志中统一输出 `task_id`。
3. 如果某层历史上使用 `trace_id` 命名，则其值必须与 `task_id` 相同，或能通过明确一跳映射还原到同一任务。
4. pre-task 日志若仅有 `client_request_id`，也必须能通过该映射与同一任务的 `task_id` 日志拼接成完整链路。
5. 以下日志都必须输出同一个关联 ID：
   - frontend `sendTaskMessage` / `attachRecoverableTask` / event consume / done settle
   - gateway admission / executor / progress flush / terminal flush / quota finalize
   - public-service authority progress / terminal write
   - patent ask / persistence bypass / finalize path
6. reviewer 和联调时，必须能用单个 `task_id` 串起一条完整链路；若需要追到 pre-task 阶段，也必须能先从 `task_id` 反查到对应 `client_request_id`，而不是依赖时间猜测。

---

## Verification Plan

### Frontend verification

1. 发送单个 fast task，确认控制台不再高频出现 `attach:start`。
2. 流式长答案过程中观察：
   - 输入框
   - 会话切换
   - 页面滚动
   是否仍可流畅响应。
3. 记录单 task 生命周期内 `persistLocalState` 的实际调用次数，应显著低于 event 数量。

### Gateway verification

1. 为单任务长答案记录：
   - 下游 `content` event 数量
   - progress sync 次数
   - terminal sync 次数
2. progress sync 次数必须远小于 content event 数量。
3. 如果需要通过详细日志验证 flush 行为，日志中必须能明确看到：
   - 收到的原始 content event 数量
   - 实际 progress flush 次数
   - terminal 前的最后一次强制 flush
4. 必须增加一条边界验证：
   - 在 gateway accumulator 持有未 flush `content_delta` 时主动刷新/重连
   - 验证恢复后 authority truth 只覆盖到 `persisted_last_seq`
   - 随后继续消费 `seq > persisted_last_seq` 时，不得重复已持久化内容，也不得丢失未持久化后的后续内容

### Public-service verification

1. 观察 `assistant-progress` 请求次数明显下降。
2. 确认 terminal 后 conversation detail 中 assistant 内容完整。
3. 对 patent gateway task 模式，确认 authority 中：
   - 不存在重复 assistant terminal 消息
   - 不存在重复 user turn
   - 不存在 lingering pending turn / overlay residue

### End-to-end verification

1. fast 模式长答案
2. highThinking 模式长答案
3. patent 普通问答长答案
4. 刷新恢复中的 running task
5. done 后前端自动停止生成状态
6. patent 普通问答完成后 conversation detail 中只存在一条对应 assistant 终态消息
7. patent 答案中的引用点击仍能打开对应原文
8. patent / fast / thinking 输出中的 `如下：- 条目` 能被正确渲染为列表
9. 如联调期间启用详细日志，需基于日志验证：
   - attach 不再高频重复触发
   - patent gateway-owned persistence 旁路确实生效
   - terminal 收敛只发生一次
10. patent 普通问答在以下四个观察面都不得出现 `patent_id=`：
   - 流式输出中
   - 完成后的最终消息中
   - 刷新恢复后的消息中
   - 再次切换会话后的消息中
11. 对同一 patent task，在刷新前、刷新后、切换会话后都只保留同一条 user + assistant 对，不得出现第二份完整答案块。

### Test Execution Requirement

1. 本 spec 的实现验收必须包含自动化测试，不允许只靠人工观察或控制台截图判定通过。
2. 至少必须补齐并运行以下定向测试：
   - frontend：同一 `(chatId, taskId)` attach 次数预算、event 风暴下 `persistLocalState()` 节流预算、done 自动收敛
   - gateway：progress accumulator flush 预算、`persisted_last_seq` 边界恢复、terminal 前强制 flush
   - patent/public-service 链路：gateway-owned persistence 旁路、单 user turn + 单 assistant turn、无 pending/overlay residue、`patent_id=` 不出现在用户可见载荷
3. 只要验证步骤需要真实启动多服务、访问本地端口、跑跨服务集成测试或写入沙箱外运行目录，就必须提权执行。
4. 如果当前环境不能提权，就必须暂停在对应 task，并明确说明哪个验证无法继续，不能假装已验证通过。

---

## Risks

1. 前端持久化降频后，如果 flush 时机设计不好，刷新瞬间可能丢失一小段本地 replay cursor。
2. gateway progress 聚合后，如果 terminal 前 flush 顺序错误，可能出现 authority 中间态不完整。
3. attach 去重如果条件写错，可能导致真实 recovery attach 被误抑制。
4. patent gateway-owned persistence 旁路如果处理不完整，可能破坏非 gateway 直连专利问答。
5. 引用展示脱敏如果只改文案不改解析，可能导致专利原文点击失效。

对应策略：

1. 终态强制 flush
2. fallback 继续以 conversation truth 为最终校准
3. 为 attach 去重、progress aggregation、terminal flush 增加专项测试
4. 为 patent gateway-owned / non-gateway 两条路径分别补测试
5. 为专利引用渲染和点击补前端测试

---

## Follow-Up

本设计完成后，如果仍存在可感知首 token 延迟，下一份专项 spec 再处理：

1. authority context snapshot 读取成本
2. conversation detail truth 刷新瘦身
3. 前端 markdown / citation 渲染成本
4. 长会话下的进一步窗口化或虚拟化
