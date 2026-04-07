# Streaming Latency Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 真实修复 QA 流式卡顿、重复 attach、过度 progress 持久化、patent `patent_id=` 外泄、以及 patent gateway-owned 双写导致的重复答案问题，并保证刷新恢复、回放去重、终态收敛都继续成立。

**Architecture:** 前端把 recoverable task runtime 从“消息变化驱动的重复 attach + 每 event 强制本地持久化”收敛为“task identity 驱动 attach + 节流持久化调度器 + 最小必要 truth sync”。gateway 引入每 task 的 progress accumulator，把 token 级 authority 写入降为批量 flush，并用 `persisted_last_seq` 明确恢复边界。patent backend 在 gateway-owned 路径下关闭 authority 双写，并在进入任何用户可见载荷前完成专利引用可读化，frontend 只负责链接化和兼容旧数据。

**Tech Stack:** Vue 3, Pinia, Vite, Node test runner, FastAPI, pytest, gateway/public-service internal authority APIs, patent backend ask pipeline

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-07-streaming-latency-remediation-design.md`
- Related prior plans:
  - `docs/superpowers/plans/2026-04-06-refresh-survivable-qa-tasks-implementation.md`
  - `docs/superpowers/plans/2026-04-04-multi-chat-background-streaming-implementation.md`

## Hard Rules

1. 不能做空壳子。任何“仅减少日志”“仅调大节流值”“仅在前端遮住重复文本”“仅让 UI 看起来停止了”的方案都不算完成。
2. 每个 task 都必须先写或扩展红灯测试，再做最小实现，再跑目标测试。
3. 每个 task 完成后必须发起 code review，并根据结论修到 pass 后才能进入下一个 task。
4. 只要测试、联调、服务启动、跨服务集成验证需要脱离沙箱，就必须提权执行；如果当前无法提权，就必须停下并说明阻塞点，不能假装验证通过。
5. 所有修复都必须接入真实 runtime 主链路，不允许新增未被调用的 helper 或仅测试路径使用的分支。
6. `persisted_last_seq`、attach 次数预算、`persistLocalState()` 节流预算、以及 patent 单 user turn + 单 assistant turn，都是必须被自动化测试锁住的验收项。

## Per-Task Review Gate

对下面每一个 task，都必须执行同一条收口流程，不能跳过：

1. 红灯测试
2. 最小实现
3. 目标测试转绿
4. 发起 code review
5. 按 review 结论修正并重跑该 task 的目标测试，直到 review pass
6. 然后才能 commit，并进入下一个 task

## File Map

### Frontend Runtime And Rendering

- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/utils/index.js`
- Modify: `frontend-vue/src/utils/taskRecoveryDebug.js`
- Modify if API helper shape changes are needed: `frontend-vue/src/services/api.js`

### Frontend Tests

- Modify: `frontend-vue/src/utils/recoverableTaskController.test.js`
- Modify: `frontend-vue/src/stores/chatStore.task-recovery.test.js`
- Modify: `frontend-vue/src/stores/chatStore.persistenceTiming.test.js`
- Modify: `frontend-vue/src/stores/chatPersistence.test.js`
- Modify: `frontend-vue/src/utils/taskRecoveryRuntime.test.js`
- Modify: `frontend-vue/src/utils/streamingLifecycle.test.js`
- Modify: `frontend-vue/src/views/Home.structure.test.js`
- Create or extend: `frontend-vue/src/utils/patentCitationRender.test.js`

### Gateway Runtime

- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/conversation_persistence.py`
- Modify if task router contract needs surfacing: `gateway/app/routers/tasks.py`

### Gateway Tests

- Modify: `gateway/tests/test_task_api.py`
- Modify: `gateway/tests/test_execution_event_relay.py`
- Modify: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Modify: `gateway/tests/test_qa_proxy.py`
- Modify if needed for internal persistence contract: `gateway/tests/test_public_proxy.py`

### Public-Service Tests And Contract Locking

- Modify: `public-service/backend/tests/test_conversation_task_runtime.py`
- Modify: `public-service/backend/tests/test_conversation_authority_api.py`
- Modify if needed for persisted message invariants: `public-service/backend/tests/test_conversation_authority_integration.py`

### Patent Backend

- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/patent/answering.py`
- Modify if request contract needs an explicit flag field: `patent/server/schemas/request_models.py`

### Patent Tests

- Modify: `patent/tests/test_chat_persistence.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify if citation formatting is best covered here: `patent/tests/test_patent_generation_orchestrator.py`

## Lock Decisions For Implementation

1. `task_id` 是跨 frontend/gateway/public-service/patent 的规范关联 ID；`createTask()` 返回前，frontend 允许短暂使用 `client_request_id`，但必须建立 `client_request_id -> task_id` 映射。
2. 新链路下任何用户可见载荷都不允许出现 `patent_id=`；frontend 最多只保留旧历史数据的兼容识别，不再把它作为新链路契约。
3. gateway progress flush 成功后，authority truth 必须完整覆盖 `seq <= persisted_last_seq` 的内容；恢复时只能从 `persisted_last_seq + 1` 继续。
4. patent 在 gateway-owned 路径下不得再写 authority user/assistant turn；gateway 是唯一 authority writer。
5. `done/error/canceled` 都必须先 flush 剩余 progress，再写 terminal，再结束前端生成态。
6. 本期不改成 WebSocket，不改 admission 架构，不取消 authority context snapshot。

### Task 1: 锁死前端 attach 身份边界，停止流式期间重复 attach

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `frontend-vue/src/utils/taskRecoveryDebug.js`
- Test: `frontend-vue/src/utils/recoverableTaskController.test.js`
- Test: `frontend-vue/src/utils/taskRecoveryRuntime.test.js`
- Test: `frontend-vue/src/views/Home.structure.test.js`

**Testing Requirement:**
- 先写红灯测试，锁死同一 `(chatId, taskId)` 在稳定流式期间不会因为 `messages` 变化反复进入 attach。
- 必跑命令：
  - `cd frontend-vue && npm test -- src/utils/recoverableTaskController.test.js src/utils/taskRecoveryRuntime.test.js src/views/Home.structure.test.js`
- 如果 Node 测试命令或依赖访问受环境限制，必须提权后再跑；如果仍跑不了，停止并报告。

- [ ] **Step 1: 写 attach 触发预算红灯测试**

覆盖：
- 同一 active task 在内容流式增长时最多 attach 一次
- fallback/recovery 最多额外 attach 一次
- `task-recovery:attach:start` 不会在普通 content event 上不断刷新

- [ ] **Step 2: 跑前端 attach 红灯测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/recoverableTaskController.test.js src/utils/taskRecoveryRuntime.test.js src/views/Home.structure.test.js
```

Expected:
- FAIL
- 失败点集中在 `Home.vue` watch 依赖过宽、attach runtime 未按 task identity 去重

- [ ] **Step 3: 最小实现 task identity 驱动 attach**

实现要求：
- `Home.vue` 只 watch 稳定标量，不再通过整个 `currentChat` 间接订阅 `messages`
- `recoverableTaskController` 增加同一 `(chatId, taskId)` attach guard
- debug 日志带上 `chatId/taskId/status/reason`
- 新 guard 必须仍允许显式 fallback/recovery attach

- [ ] **Step 4: 重跑 attach 相关测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/recoverableTaskController.test.js src/utils/taskRecoveryRuntime.test.js src/views/Home.structure.test.js
```

Expected:
- PASS
- attach 预算被测试锁住

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 1 的真实改动和测试结果发给 reviewer
- reviewer 如要求补测、改实现或修边界，必须先完成并重跑本 task 目标测试
- 只有 reviewer pass 后才能进入 commit

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/utils/recoverableTaskController.js frontend-vue/src/utils/taskRecoveryDebug.js frontend-vue/src/utils/recoverableTaskController.test.js frontend-vue/src/utils/taskRecoveryRuntime.test.js frontend-vue/src/views/Home.structure.test.js
git commit -m "fix(frontend): stop repeated task recovery attach during streaming"
```

### Task 2: 用真实 scheduler 替换每 event 强制本地持久化

**Files:**
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/stores/chatStore.persistenceTiming.test.js`
- Modify: `frontend-vue/src/stores/chatStore.task-recovery.test.js`
- Modify: `frontend-vue/src/stores/chatPersistence.test.js`

**Testing Requirement:**
- 必须锁死 `100` 个 content event 窗口内 `persistLocalState()` 调用次数不超过 spec 预算，并覆盖 done/unload 的强制 flush。
- 必跑命令：
  - `cd frontend-vue && npm test -- src/stores/chatStore.persistenceTiming.test.js src/stores/chatStore.task-recovery.test.js src/stores/chatPersistence.test.js src/utils/recoverableTaskController.test.js`
- 若运行环境受限，先提权再跑。

- [ ] **Step 1: 写持久化节流预算红灯测试**

覆盖：
- 普通 content event 只更新内存 cursor，不立即强制 `persistLocalState()`
- `done/error/canceled` 会触发强制 flush
- 页面卸载或显式 detach 会强制 flush
- `refreshConversationTruth()` 改成标记 dirty 而不是立刻同步写

- [ ] **Step 2: 跑持久化红灯测试**

Run:
```bash
cd frontend-vue && npm test -- src/stores/chatStore.persistenceTiming.test.js src/stores/chatStore.task-recovery.test.js src/stores/chatPersistence.test.js src/utils/recoverableTaskController.test.js
```

Expected:
- FAIL
- 当前实现仍在 `onEvent` 和 truth refresh 后频繁强制持久化

- [ ] **Step 3: 实现 task recovery persist scheduler**

实现要求：
- replay cursor 与 localStorage 持久化解耦
- 引入统一 dirty/scheduled/forced flush 机制
- 保证终态、detach、页面卸载前 flush
- 预算命中时只减少持久化频率，不改变消息内容或 seq 去重逻辑

- [ ] **Step 4: 重跑持久化相关测试**

Run:
```bash
cd frontend-vue && npm test -- src/stores/chatStore.persistenceTiming.test.js src/stores/chatStore.task-recovery.test.js src/stores/chatPersistence.test.js src/utils/recoverableTaskController.test.js
```

Expected:
- PASS
- `persistLocalState()` 预算被锁定，终态 flush 仍成立

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 2 的持久化调度器实现、预算测试结果、终态 flush 证据发给 reviewer
- 根据 review 结论修正后，重跑本 task 目标测试直到 pass

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/utils/recoverableTaskController.js frontend-vue/src/stores/chatStore.js frontend-vue/src/stores/chatStore.persistenceTiming.test.js frontend-vue/src/stores/chatStore.task-recovery.test.js frontend-vue/src/stores/chatPersistence.test.js frontend-vue/src/utils/recoverableTaskController.test.js
git commit -m "fix(frontend): batch task recovery persistence writes"
```

### Task 3: 缩小 send 后 truth sync 范围并锁死 done 自动收敛

**Files:**
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify if API helper shape changes are needed: `frontend-vue/src/services/api.js`
- Test: `frontend-vue/src/utils/streamingLifecycle.test.js`
- Test: `frontend-vue/src/utils/recoverableTaskController.test.js`

**Testing Requirement:**
- 必须覆盖 createTask 成功后优先直接 attach 流，而不是先做全量 detail replace；并锁死 terminal 后前端自动退出“生成中”状态。
- 必须增加可重复的预算检测，证明健康热路径下 `createTask` 成功到开始稳定消费 task events 的时间满足 spec 目标 `<= 300ms`。若直接真实计时会引入脆弱性，应使用可控时钟、埋点断言或测试替身锁死预算。
- 必跑命令：
  - `cd frontend-vue && npm test -- src/utils/streamingLifecycle.test.js src/utils/recoverableTaskController.test.js`
- 如需真实联调发送链路，必须提权启动相关服务再测。

- [ ] **Step 1: 写 send-path 与 terminal 收敛红灯测试**

覆盖：
- `sendTaskMessage()` 成功后默认不先做 `replaceMessagesFromServer=true`
- 缺 placeholder / fallback/recovery 时才允许 detail sync
- 收到 terminal 事件后停止继续消费重复文本，并退出 busy/generating 状态
- `createTask success -> attach/consume start` 满足 `<= 300ms` 的预算断言

- [ ] **Step 2: 跑 send-path 红灯测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/streamingLifecycle.test.js src/utils/recoverableTaskController.test.js
```

Expected:
- FAIL
- 当前实现仍在 send 后走 replace-sync 主路径，terminal 收敛也可能依赖后续 truth refresh

- [ ] **Step 3: 实现最小必要 sync 与终态收口**

实现要求：
- 新任务默认用 `taskSummary + local placeholder` 建立流式消费
- detail sync 仅在缺关键 authority truth 时触发
- terminal 到来后立即停止前端流式 runtime，并禁止后续重复增量继续拼接

- [ ] **Step 4: 重跑 send-path 相关测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/streamingLifecycle.test.js src/utils/recoverableTaskController.test.js
```

Expected:
- PASS
- 首段等待链路缩短，done 自动收敛被测试锁住

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 3 的 send-path 改动、done 自动收敛测试结果发给 reviewer
- reviewer 若指出仍有 replace-sync 主路径或 terminal 收敛漏洞，必须修完并重测

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/utils/recoverableTaskController.js frontend-vue/src/views/Home.vue frontend-vue/src/services/api.js frontend-vue/src/utils/streamingLifecycle.test.js frontend-vue/src/utils/recoverableTaskController.test.js
git commit -m "fix(frontend): minimize task truth sync before streaming"
```

### Task 4: 修复 patent 用户可见引用与段内列表渲染契约

**Files:**
- Modify: `frontend-vue/src/utils/index.js`
- Modify or Create: `frontend-vue/src/utils/patentCitationRender.test.js`
- Modify: `frontend-vue/src/utils/streamingLifecycle.test.js`
- Modify if old-history compatibility tests belong here: `frontend-vue/src/utils/streamingDoiRender.test.js`

**Testing Requirement:**
- 必须锁死新链路用户可见文本里不再出现 `patent_id=`，同时旧历史数据仍可兼容渲染。
- 必跑命令：
  - `cd frontend-vue && npm test -- src/utils/patentCitationRender.test.js src/utils/streamingLifecycle.test.js`

- [ ] **Step 1: 写引用显示与段内列表红灯测试**

覆盖：
- 新链路专利引用显示为 `CN...` 或 `专利号 CN...`
- 点击链接仍能带出正确 patent id
- 历史残留 `(patent_id=CN...)` 仍能兼容打开专利原文
- `如下：- A - B` 被归一化成真实列表

- [ ] **Step 2: 跑渲染红灯测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/patentCitationRender.test.js src/utils/streamingLifecycle.test.js
```

Expected:
- FAIL
- 当前渲染仍直出 `patent_id=`，段内列表也未被正确拆行

- [ ] **Step 3: 实现专利引用 linkify 与列表归一化**

实现要求：
- 新链路不再依赖 raw `(patent_id=...)` 用户文本
- frontend 以用户可读文本做 linkify，并保留旧历史数据兼容分支
- 段内列表只对明确模式做拆行，避免误伤普通连字符

- [ ] **Step 4: 重跑渲染相关测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/patentCitationRender.test.js src/utils/streamingLifecycle.test.js
```

Expected:
- PASS
- 用户可见文本契约稳定，旧数据兼容不回退

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 4 的用户可见引用契约、旧数据兼容、列表归一化改动发给 reviewer
- reviewer 如指出仍有 `patent_id=` 泄露或误伤普通文本，必须先修再 commit

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/utils/index.js frontend-vue/src/utils/patentCitationRender.test.js frontend-vue/src/utils/streamingLifecycle.test.js frontend-vue/src/utils/streamingDoiRender.test.js
git commit -m "fix(frontend): render readable patent citations"
```

### Task 5: 在 gateway 引入 progress accumulator，并锁死 `persisted_last_seq` 恢复边界

**Files:**
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/conversation_persistence.py`
- Test: `gateway/tests/test_execution_event_relay.py`
- Test: `gateway/tests/test_task_api.py`
- Test: `gateway/tests/test_refresh_survivable_task_e2e.py`

**Testing Requirement:**
- 必须用自动化测试锁死 progress flush 预算、terminal 前强制 flush、以及“刷新发生在未 flush delta 期间”时的 `persisted_last_seq` 边界。
- 必须显式断言 `100` 个 content event 窗口内 progress flush 次数 `<= 20`，且 `flush/content <= 0.2`，不能只验证“少于 100 次”。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_execution_event_relay.py gateway/tests/test_task_api.py gateway/tests/test_refresh_survivable_task_e2e.py -p no:cacheprovider`
- 若需要 Redis/多服务依赖或沙箱外运行目录，必须提权。

- [ ] **Step 1: 写 gateway accumulator 红灯测试**

覆盖：
- `100` 个 content event 触发的 progress flush 次数 `<= 20`
- `flush/content <= 0.2`
- flush 成功后才推进 `persisted_last_seq`
- terminal 前必定做最后一次 progress flush
- 刷新/重连落在未 flush delta 期间时，恢复后不重复、不丢失

- [ ] **Step 2: 跑 gateway 红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_execution_event_relay.py gateway/tests/test_task_api.py gateway/tests/test_refresh_survivable_task_e2e.py -p no:cacheprovider
```

Expected:
- FAIL
- 当前实现仍按 event 直接 progress sync，也缺少明确的 `persisted_last_seq` 边界行为

- [ ] **Step 3: 实现 gateway progress accumulator**

实现要求：
- 每 task 聚合 `pending content delta / latest steps / observed_last_seq / persisted_last_seq`
- 受时间窗、字节阈值、阶段变化触发 flush
- terminal 前强制 flush，并在成功后推进终态
- flush 失败时不得错误推进 `persisted_last_seq`

- [ ] **Step 4: 重跑 gateway 累加器测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_execution_event_relay.py gateway/tests/test_task_api.py gateway/tests/test_refresh_survivable_task_e2e.py -p no:cacheprovider
```

Expected:
- PASS
- flush 预算和 replay 边界均被锁定

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 5 的 accumulator、`persisted_last_seq` 语义、预算测试结果发给 reviewer
- reviewer 如指出 flush 上限、terminal 顺序或 replay 边界未锁死，必须先修正并重测

- [ ] **Step 6: Commit**

```bash
git add gateway/app/services/qa_tasks.py gateway/app/services/conversation_persistence.py gateway/tests/test_execution_event_relay.py gateway/tests/test_task_api.py gateway/tests/test_refresh_survivable_task_e2e.py
git commit -m "fix(gateway): batch progress sync for recoverable tasks"
```

### Task 6: 建立跨服务 correlation ID 与调试日志契约

**Files:**
- Modify: `frontend-vue/src/utils/taskRecoveryDebug.js`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
- Test: `frontend-vue/src/utils/taskRecoveryDebug.test.js`
- Test: `gateway/tests/test_task_api.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`

**Testing Requirement:**
- 必须锁死 `client_request_id -> task_id` 映射以及日志字段输出，确保 review 和联调能单 ID 串起完整链路。
- 必跑命令：
  - `cd frontend-vue && npm test -- src/utils/taskRecoveryDebug.test.js`
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_authority_api.py -p no:cacheprovider`
- 若需要跨服务集成验证日志输出，必须提权。

- [ ] **Step 1: 写 correlation ID 红灯测试**

覆盖：
- `createTask()` 返回前使用 `client_request_id`
- 返回后建立映射并统一输出 `task_id`
- patent/gateway 至少一个 contract 测试锁死 `task_id/trace_id` 一致
- public-service authority progress/terminal 边界日志也输出同一 `task_id`

- [ ] **Step 2: 跑 correlation 红灯测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/taskRecoveryDebug.test.js
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_authority_api.py -p no:cacheprovider
```

Expected:
- FAIL
- 当前日志链路无法稳定以单 ID 串联

- [ ] **Step 3: 实现统一关联 ID 输出**

实现要求：
- frontend pre-task 用 `client_request_id`，拿到 `task_id` 后建立映射
- gateway/public-service/patent 统一输出同一 `task_id`
- 若代码中历史字段仍叫 `trace_id`，其值必须等于 `task_id` 或可直接映射
- public-service 的 authority progress/terminal 路径必须成为强制接入点，而不是可选日志增强

- [ ] **Step 4: 重跑 correlation 相关测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/taskRecoveryDebug.test.js
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_authority_api.py -p no:cacheprovider
```

Expected:
- PASS
- 关联 ID 契约被锁住

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 6 的 correlation ID 实现和日志/测试证据发给 reviewer
- reviewer 若指出 public-service、patent 或 frontend 仍无法串成单链路，必须先补齐

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/utils/taskRecoveryDebug.js frontend-vue/src/utils/recoverableTaskController.js frontend-vue/src/utils/taskRecoveryDebug.test.js gateway/app/services/qa_tasks.py gateway/tests/test_task_api.py patent/server/services/ask_service.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/app/modules/conversation/internal_api.py public-service/backend/tests/test_conversation_authority_api.py
git commit -m "chore: standardize task correlation logging"
```

### Task 7: 让 patent 在 gateway-owned 路径下停止 authority 双写

**Files:**
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify if request shape needs an explicit field: `patent/server/schemas/request_models.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `public-service/backend/tests/test_conversation_task_runtime.py`

**Testing Requirement:**
- 必须锁死同一 patent gateway-owned task 最终只保留一条 user turn 和一条 assistant turn，且没有 pending/overlay residue。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q patent/tests/test_chat_persistence.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider`
- 如果测试依赖多服务或本地端口，必须提权。

- [ ] **Step 1: 写 patent gateway-owned persistence 红灯测试**

覆盖：
- 识别 `X-Gateway-Task-Execution: 1` 与 `X-Gateway-Owned-Persistence: 1`
- gateway-owned 路径下 patent 不再写 authority user/assistant
- 非 gateway 直连 durable ask 不受影响
- authority 最终只有一条 user 和一条 assistant，且无 pending/overlay residue

- [ ] **Step 2: 跑 patent persistence 红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q patent/tests/test_chat_persistence.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider
```

Expected:
- FAIL
- 当前 patent 路径仍会 durable 写 authority turn

- [ ] **Step 3: 实现 gateway-owned persistence bypass**

实现要求：
- patent 在 gateway-owned 模式下跳过 authority `prepare_turn/finalize_turn`
- 保留本地执行缓存、cached replay、context snapshot 能力
- 不破坏非 gateway 直连 patent ask

- [ ] **Step 4: 重跑 patent persistence 相关测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q patent/tests/test_chat_persistence.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider
```

Expected:
- PASS
- patent gateway-owned 双写被真实移除

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 7 的 gateway-owned bypass 改动和单 user/单 assistant 证据发给 reviewer
- reviewer 如指出仍有 authority 双写或 residue，必须先修掉并重跑测试

- [ ] **Step 6: Commit**

```bash
git add patent/server/services/ask_service.py patent/server/services/chat_persistence.py patent/server/schemas/request_models.py patent/tests/test_chat_persistence.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_task_runtime.py
git commit -m "fix(patent): bypass authority persistence for gateway-owned tasks"
```

### Task 8: 在 patent 输出链路中完成用户可见引用可读化

**Files:**
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_generation_orchestrator.py`
- Modify if transport contract is asserted there: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `public-service/backend/tests/test_conversation_authority_api.py`
- Modify if authority truth is asserted there: `public-service/backend/tests/test_conversation_authority_integration.py`

**Testing Requirement:**
- 必须锁死 `patent_id=` 不会出现在 streaming content、final content、replay content 这些用户可见载荷里。
- 必须额外锁死 authority progress 和 authority terminal 中的用户可见内容同样不含 `patent_id=`，不能只测 patent 输出端和 UI 端。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q patent/tests/test_patent_executor.py patent/tests/test_patent_generation_orchestrator.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py -p no:cacheprovider`
- 若需要真实集成验证 SSE 载荷，必须提权。

- [ ] **Step 1: 写专利可见引用契约红灯测试**

覆盖：
- patent 生成链路内部可保留结构化专利引用
- 但任何对外 content/final payload 都必须是 `CN...` 或 `专利号 CN...`
- streaming 与 terminal 载荷都不能含 `patent_id=`
- authority progress/terminal 写入后的 conversation truth 也不能含 `patent_id=`

- [ ] **Step 2: 跑专利引用红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q patent/tests/test_patent_executor.py patent/tests/test_patent_generation_orchestrator.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py -p no:cacheprovider
```

Expected:
- FAIL
- 当前 answer contract 仍把 `(patent_id=...)` 视为输出规范

- [ ] **Step 3: 实现对外引用可读化**

实现要求：
- 内部解析与对外展示分层
- 在进入任何用户可见 payload 前完成可读化
- 不破坏已有专利 id 提取和前端点击能力

- [ ] **Step 4: 重跑专利引用相关测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q patent/tests/test_patent_executor.py patent/tests/test_patent_generation_orchestrator.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py -p no:cacheprovider
```

Expected:
- PASS
- `patent_id=` 只留在内部协议，不再进入用户可见面

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 8 的对外引用可读化实现与 payload 测试结果发给 reviewer
- reviewer 如指出任一用户可见载荷仍含 `patent_id=`，必须先补齐

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/answering.py patent/tests/test_patent_executor.py patent/tests/test_patent_generation_orchestrator.py patent/tests/fastapi_contract/test_ask_contract.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py
git commit -m "fix(patent): emit readable citations in user-visible answers"
```

### Task 9: 跑跨服务回归，锁死最终体验与持久化一致性

**Files:**
- Modify if e2e coverage belongs here: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Modify: `gateway/tests/test_qa_proxy.py`
- Modify: `public-service/backend/tests/test_conversation_authority_integration.py`
- Modify: `frontend-vue/src/utils/streamingLifecycle.test.js`

**Testing Requirement:**
- 必须真实覆盖 fast、highThinking、patent 三模式下的流式恢复、done 自动停、无重复输出、无重复消息、预算符合 spec。
- 必跑命令：
  - `cd frontend-vue && npm test -- src/utils/streamingLifecycle.test.js`
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_authority_integration.py -p no:cacheprovider`
- 需要多服务启动、端口访问、跨服务链路时，必须提权执行；不能提权就停下说明。

- [ ] **Step 1: 写最终跨服务回归红灯测试**

覆盖：
- fast/highThinking/patent 的 done 后前端自动结束生成态
- 刷新恢复 running task 时不会重复回放已持久化区间
- patent 完成后 conversation detail 中只保留一条 user + assistant
- 预算项可通过测试替身或日志计数断言

- [ ] **Step 2: 跑最终回归红灯测试**

Run:
```bash
cd frontend-vue && npm test -- src/utils/streamingLifecycle.test.js
conda run --no-capture-output -n agent pytest -q gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_authority_integration.py -p no:cacheprovider
```

Expected:
- FAIL
- 尚未完全满足跨服务一致性与终态收敛约束

- [ ] **Step 3: 补齐剩余 runtime 接缝并校准测试**

实现要求：
- 只补真实缺口，不允许在 e2e 层做假分支
- 确保日志、恢复、progress、terminal、frontend stop 状态全链路一致

- [ ] **Step 4: 重跑最终跨服务回归**

Run:
```bash
cd frontend-vue && npm test -- src/utils/streamingLifecycle.test.js
conda run --no-capture-output -n agent pytest -q gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_authority_integration.py -p no:cacheprovider
```

Expected:
- PASS
- 三模式关键体验与持久化一致性全部达标

- [ ] **Step 5: 发起 code review，修到 pass**

要求：
- 把 Task 9 的跨服务回归结果和预算达标证据发给 reviewer
- reviewer 若指出仍有重复输出、重复消息、done 不收敛或预算未达标，必须先修再 commit

- [ ] **Step 6: Commit**

```bash
git add gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_authority_integration.py frontend-vue/src/utils/streamingLifecycle.test.js
git commit -m "test: lock end-to-end streaming recovery invariants"
```
