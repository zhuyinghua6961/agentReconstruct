# Refresh-Survivable QA Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 QA 问答升级成 gateway 托管的 admission-aware 后台任务：任务能在刷新后存活，能在全局 LLM 并发上限下排队/恢复/取消，且 conversation 里落下的 user turn、assistant placeholder、terminal 状态都是真实持久化结果，而不是 UI 空壳。

**Architecture:** `gateway` 继续作为统一入口，但不再只是页面内流式转发器，而是把问答请求建模成真实 `task_id`，复用现有 admission / queue / relay / slot lease 基础设施承载 `queued -> admitted -> running -> terminal` 生命周期。`public-service` 负责 conversation truth，`fastQA` / `highThinkingQA` 负责真实生成并在 task 路径下关闭自身 persistence；全局 LLM admission 只限制真实执行入口，不替换已 admitted 任务的 live streaming transport。

**Tech Stack:** FastAPI, Python, Redis-backed gateway admission/queue/relay stores, public-service conversation persistence, Vue 3 + Pinia frontend, pytest, node test / Vite frontend tests

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-06-refresh-survivable-qa-tasks-design.md`
- Related prior design:
  - `docs/2026-03-25-redis-mq-architecture-spec.md`
  - `docs/superpowers/specs/2026-03-27-interactive-admission-kickoff-decisions.md`
  - `docs/superpowers/specs/2026-04-04-multi-chat-background-streaming-design.md`

## Hard Rules

1. 不能做空壳子。任何“只有 UI queued/generating 状态、没有真实 admission/task 生命周期”的方案都不算完成。
2. 每个 task 必须先写红灯测试，再做最小实现，再跑目标测试。
3. 每个 task 完成后必须做 code review，再根据结论修到通过。
4. 所有需要的测试都必须真实运行。若命令需要提权，必须提权运行；若当前环境不能提权或无法完成验证，必须停下来说明阻塞，不能继续声称 task 完成。
5. 旧 `ask` / `ask_stream` 在本期只保兼容，不要求具备 admission-aware refresh survival；新能力只对新 task API 路径成立。
6. `patent` 在本仓库里是外部依赖门槛，不允许为了“看起来三模式齐了”而在本地写假实现。
7. Queue-full / create-failed 场景不能留下半成品 conversation state。
8. 已 admitted/running 的 live streaming transport 不能被“队列化 token 输送”替换。

---

## File Map

### Gateway Admission-Aware Task Runtime

- Create: `gateway/app/routers/tasks.py`
- Create: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/app/core/config.py`
- Modify: `gateway/app/routers/admission.py`
- Modify: `gateway/app/services/execution_admission.py`
- Modify: `gateway/app/services/execution_queue_status.py`
- Modify: `gateway/app/services/execution_event_relay.py`
- Modify: `gateway/app/services/execution_slot_leases.py`
- Modify only if task creation shares logic with legacy ask normalization: `gateway/app/routers/qa.py`

### Gateway Conversation Enrichment

- Modify: `gateway/app/routers/public_proxy.py`

### Public-Service Conversation Task Persistence

- Create: `public-service/backend/app/modules/conversation/task_schemas.py`
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/modules/conversation/repository.py`
- Modify only if route surface wiring needs it: `public-service/backend/app/modules/conversation/api.py`

### QA Backend Execution Contract

- Modify: `fastQA/app/routers/qa.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Modify: `highThinkingQA/server/services/ask_service.py`

### Frontend Task UX

- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Create: `frontend-vue/src/utils/taskReplayCursor.js`
- Create: `frontend-vue/src/utils/taskReplayCursor.test.js`
- Create or extend: `frontend-vue/src/stores/chatStore.task-recovery.test.js`

### Tests

- Create: `gateway/tests/test_task_api.py`
- Modify: `gateway/tests/test_admission_api.py`
- Modify: `gateway/tests/test_execution_admission.py`
- Modify: `gateway/tests/test_execution_event_relay.py`
- Modify: `gateway/tests/test_execution_queue_status.py`
- Modify: `gateway/tests/test_qa_proxy.py`
- Modify: `gateway/tests/test_public_proxy.py`
- Modify: `gateway/tests/test_config.py`
- Create: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Modify: `public-service/backend/tests/test_conversation_authority_api.py`
- Modify: `public-service/backend/tests/test_conversation_module.py`
- Create: `public-service/backend/tests/test_conversation_task_runtime.py`
- Modify: `fastQA/tests/test_qa_route_aliases.py`
- Modify: `fastQA/tests/test_qa_placeholder.py`
- Modify: `highThinkingQA/tests/test_ask_router_summary_persistence.py`
- Modify: `highThinkingQA/tests/test_ask_service_executor.py`
- Modify: `frontend-vue/src/services/api.structure.test.js`

---

## Lock Decisions For Implementation

1. `task_id` 与 gateway admission `request_id` 是同一标识，不做双轨 ID。
2. `POST /api/v1/tasks` 创建 admission-aware 真实任务；`GET /api/v1/tasks/{task_id}/events` 负责 queued/running 状态事件回放与 live 续流。
3. 新 task 路径的 per-user active-task cap、same-conversation guard、global admission ceiling 只在新 task API 上强制。
4. `assistant_message_id` 一律使用 conversation detail 里的稳定字符串 `message_id`，不暴露内部数值 row id。
5. conversation list/detail 的 `active_task` enrichment 必须覆盖 `/api/...` 与 `/api/v1/...` 两套读取别名。
6. `fastQA` / `highThinkingQA` 在 gateway task 执行头 `X-Gateway-Owned-Persistence: 1` 下必须跳过原有 persistence hook。
7. queue-full / overloaded 不创建 task，也不持久化 user turn 或 assistant placeholder。
8. quota 生命周期绑定 task create / terminal，不绑定 queued recovery、reattach 或多页订阅。
9. backend-specific admission ceilings 继续保留为一等配置，与 global ceiling 同时生效。
10. scheduler/dispatcher 继续由 dedicated admission-worker role 承担，web gateway 进程保持 producer-only。
11. patent 相关代码只允许接线到现有 gateway backend registry 和外部 contract gate，不允许本地伪造 backend。

---

### Task 1: 扩展 Gateway Task API 为 Admission-Aware 状态模型

**Files:**
- Create: `gateway/app/routers/tasks.py`
- Create: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/app/services/execution_admission.py`
- Modify: `gateway/app/services/execution_queue_status.py`
- Modify: `gateway/app/services/execution_event_relay.py`
- Test: `gateway/tests/test_task_api.py`
- Test: `gateway/tests/test_admission_api.py`
- Test: `gateway/tests/test_execution_admission.py`

**Testing Requirement:**
- 先写 gateway 侧红灯测试，锁死 `task_id == request_id`、用户态状态集为 `queued/admitted/running/completed/failed/canceled/expired`、以及用户态 task API 的基本 contract。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py -p no:cacheprovider`
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_admission_api.py gateway/tests/test_execution_admission.py -p no:cacheprovider`
- 若这些命令因权限、Redis 访问、环境隔离等原因无法在当前环境跑通，必须先提权；若提不了权或仍被环境阻塞，停止并明确报告阻塞点。

- [ ] **Step 1: 写 admission-aware task API 红灯测试**

新增或改写测试，至少覆盖：
- `POST /api/v1/tasks` 返回 `task_id`
- `GET /api/v1/tasks/{task_id}` 返回规范化 task summary
- `GET /api/v1/tasks/{task_id}/events?after_seq=N` 走 relay + seq contract
- 用户态 status 从 raw admission 状态规范化为 `queued/admitted/running/completed/failed/canceled/expired`
- raw `cancelled` 在用户态规范化为 `canceled`

- [ ] **Step 2: 跑 task API 红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py -p no:cacheprovider
```

Expected:
- FAIL
- 失败点集中在缺少 `/api/v1/tasks` 路由、状态正规化、queued/admitted 可见性、事件回放入口

- [ ] **Step 3: 最小实现 `qa_tasks.py` 与 `tasks.py`**

实现要求：
- 在 `qa_tasks.py` 中封装 task create/detail/events/cancel 的用户态 contract
- 明确把 admission `request` record 归一为用户态 task summary
- 不复制第二套状态存储
- `main.py` 注册新 router

- [ ] **Step 4: 扩展 admission/queue/relay store 的 task 视角 helper**

实现要求：
- 增加从 admission store 读取用户态 task summary 的 helper
- 增加 `after_seq` 过滤和 state-event replay 所需的最小接口
- 保持 `/api/admission/*` 作为 raw control-plane，不把用户态逻辑直接塞回 operator 路由响应里

- [ ] **Step 5: 重跑 gateway task 相关测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_admission_api.py gateway/tests/test_execution_admission.py -p no:cacheprovider
```

Expected:
- PASS
- task 路由、状态映射、事件入口基础 contract 稳定

- [ ] **Step 6: Commit**

```bash
git add gateway/app/routers/tasks.py gateway/app/services/qa_tasks.py gateway/app/main.py gateway/app/services/execution_admission.py gateway/app/services/execution_queue_status.py gateway/app/services/execution_event_relay.py gateway/tests/test_task_api.py gateway/tests/test_admission_api.py gateway/tests/test_execution_admission.py
git commit -m "feat: add admission-aware qa task api contract"
```

### Task 2: 在 Public-Service 落真实的 Queued/Running Placeholder 生命周期

**Files:**
- Create: `public-service/backend/app/modules/conversation/task_schemas.py`
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/modules/conversation/repository.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Test: `public-service/backend/tests/test_conversation_task_runtime.py`

**Testing Requirement:**
- 必须先把 `(conversation_id, task_id)` 下的 `assistant-start / assistant-progress / assistant-terminal` 三段内部写协议锁成红灯测试，并显式覆盖 queued 创建即落库、expired 终态收口。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider`
- 如果需要提权才能访问测试依赖或运行环境，就提权跑；如果无法提权或当前环境无法满足测试依赖，停止并报告。

- [ ] **Step 1: 写 public-service task runtime 红灯测试**

新增测试至少覆盖：
- `assistant-start` 在 task create 成功时创建真实 placeholder 并绑定 `active_task_id`
- queued task 也有 placeholder message
- 重复 `assistant-start` 幂等返回同一 placeholder
- `assistant-progress` 只更新绑定 placeholder，不新增消息
- `assistant-terminal` 清掉 `active_task_id` 并写入 `completed/failed/canceled/expired`
- `assistant_message_id` 暴露为稳定字符串 `message_id`
- 会话里最终只有一条 user turn 和一条对应 assistant placeholder/terminal message

- [ ] **Step 2: 跑 public-service 红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider
```

Expected:
- FAIL
- 当前缺少 task-keyed internal APIs、缺少 queued placeholder contract、缺少 expired terminal sync

- [ ] **Step 3: 新增 task schemas 与 internal API 路由**

实现要求：
- 定义 `assistant-start / progress / terminal` 的 request/response schema
- 在 `internal_api.py` 加新端点
- 保持现有 authority async 路径不受破坏

- [ ] **Step 4: 在 service/repository 中实现真实占位消息生命周期**

实现要求：
- `assistant-start` 创建真实 placeholder message，生成稳定 `message_id`
- queued/admitted/running 状态统一写到同一 placeholder
- `assistant-progress` 做 append/update，不插入重复 assistant turn
- `assistant-terminal` 封口并清理 `active_task_id`
- detail/list/cache 真实刷新

- [ ] **Step 5: 重跑 public-service task runtime 测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider
```

Expected:
- PASS
- queued/running/terminal placeholder 三段式写契约稳定

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/conversation/task_schemas.py public-service/backend/app/modules/conversation/internal_api.py public-service/backend/app/modules/conversation/service.py public-service/backend/app/modules/conversation/repository.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_task_runtime.py
git commit -m "feat: add queued task conversation persistence"
```

### Task 3: 让 FastQA / HighThinkingQA 在 Task 路径下关闭自身持久化

**Files:**
- Modify: `fastQA/app/routers/qa.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Test: `fastQA/tests/test_qa_placeholder.py`
- Test: `fastQA/tests/test_qa_route_aliases.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`

**Testing Requirement:**
- 必须先用红灯测试证明：带 `X-Gateway-Owned-Persistence: 1` 时，QA 服务仍正常产出流事件，但不再执行 user/assistant persistence hook。
- `highThinkingQA` 的 disconnect-driven cancel parity 不放在这里混做，单独由 Task 4 负责。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q fastQA/tests/test_qa_placeholder.py fastQA/tests/test_qa_route_aliases.py -p no:cacheprovider`
  - `conda run --no-capture-output -n agent pytest -q highThinkingQA/tests/test_ask_router_summary_persistence.py -p no:cacheprovider`
- 如果环境要求提权才能跑这些测试，必须提权；提不了就停下说明。

- [ ] **Step 1: 为两个 QA 服务的新 header contract 写红灯测试**

至少覆盖：
- `X-Gateway-Task-Execution: 1`
- `X-Gateway-Owned-Persistence: 1`
- ask/ask_stream 仍产出正常事件
- `_persist_*` hook 不被触发

- [ ] **Step 2: 跑红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q fastQA/tests/test_qa_placeholder.py fastQA/tests/test_qa_route_aliases.py highThinkingQA/tests/test_ask_router_summary_persistence.py -p no:cacheprovider
```

Expected:
- FAIL
- 失败点只在 persistence suppression contract 缺失

- [ ] **Step 3: 在两个 QA 服务里实现 header-based persistence bypass**

实现要求：
- 只在 gateway task 执行头存在时跳过持久化
- 不破坏旧 ask/ask_stream 路径
- 不改变事件内容与终态 contract

- [ ] **Step 4: 重跑 QA backend 定向测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q fastQA/tests/test_qa_placeholder.py fastQA/tests/test_qa_route_aliases.py highThinkingQA/tests/test_ask_router_summary_persistence.py -p no:cacheprovider
```

Expected:
- PASS
- persistence suppression 只对 task 路径生效

- [ ] **Step 5: Commit**

```bash
git add fastQA/app/routers/qa.py highThinkingQA/server_fastapi/routers/ask.py fastQA/tests/test_qa_placeholder.py fastQA/tests/test_qa_route_aliases.py highThinkingQA/tests/test_ask_router_summary_persistence.py
git commit -m "feat: add gateway-owned persistence mode for qa backends"
```

### Task 4: 补齐 HighThinkingQA 的断连取消与真实取消语义

**Files:**
- Modify: `highThinkingQA/server/services/ask_service.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Test: `highThinkingQA/tests/test_ask_service_executor.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`

**Testing Requirement:**
- 必须先把 `highThinkingQA` 的 disconnect-driven cancel parity 锁成红灯测试，证明 gateway worker 断连或显式 cancel 后，下游真实工作会停止，而不是 gateway 自己先标 canceled、thinking 端继续跑完。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_ask_router_summary_persistence.py -p no:cacheprovider`
- 如果测试依赖、端口或运行环境受限，必须先提权；若当前环境无法完成验证，停止并明确报告。

- [ ] **Step 1: 写 `highThinkingQA` cancel parity 红灯测试**

至少覆盖：
- disconnect 或 cancel signal 触发 `ask_service` 内部 `cancel_event`
- canceled 执行不再继续产出成功终态
- router 层在 gateway task 头下断连时，把取消语义传递到 executor

- [ ] **Step 2: 跑红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_ask_router_summary_persistence.py -p no:cacheprovider
```

Expected:
- FAIL
- 失败点集中在 disconnect/cancel 没有真正中止 thinking 执行或仍可能落成功终态

- [ ] **Step 3: 在 `ask_service.py` 实现 cancel parity**

实现要求：
- 让 `highThinkingQA` 对 gateway-owned upstream 断连与显式 cancel 使用和 `fastQA` 等价的停止语义
- 已经收到取消后，禁止再落成功 done terminal
- 不破坏旧非 task 路径的正常成功执行

- [ ] **Step 4: 在 router 层补齐 cancel 传递**

实现要求：
- 把 request disconnect / gateway cancel 传递到 `ask_service`
- 与 Task 3 的 persistence suppression 共存，不相互覆盖

- [ ] **Step 5: 重跑 `highThinkingQA` cancel 定向测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_ask_router_summary_persistence.py -p no:cacheprovider
```

Expected:
- PASS
- gateway 断连/取消能真实停掉下游 thinking 工作

- [ ] **Step 6: Commit**

```bash
git add highThinkingQA/server/services/ask_service.py highThinkingQA/server_fastapi/routers/ask.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_ask_router_summary_persistence.py
git commit -m "feat: align thinking ask cancellation with gateway tasks"
```

### Task 5: 打通 Admission-Aware Task 创建语义与失败补偿

**Files:**
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/execution_admission.py`
- Modify: `gateway/app/routers/tasks.py`
- Modify only if shared helpers are needed: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_task_api.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `public-service/backend/tests/test_conversation_task_runtime.py`

**Testing Requirement:**
- 必须先锁红灯测试，覆盖 create path 的真实语义：same-conversation guard、用户 active-task cap、queue-full 不落会话、create-failed 回滚 conversation side effects。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider`
- 若测试或依赖需要提权，必须提权跑；提不了权就停下汇报阻塞。

- [ ] **Step 1: 写 create-path 红灯测试**

至少覆盖：
- `POST /api/v1/tasks` 成功时只持久化一条 user turn、一条 assistant placeholder，并绑定 `active_task_id`
- 同一 `conversation_id` 已有 queued/admitted/running task 时，第二次 create 被拒绝
- 同一用户达到 configured active-task cap 时，下一次 create 被 busy/cap 语义拒绝
- queue_full / overloaded 不创建 task，也不持久化 user turn 或 assistant placeholder
- create 半途失败会整体回滚，不留下脏 `active_task_id`

- [ ] **Step 2: 跑红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider
```

Expected:
- FAIL
- 失败点集中在 create path side effects、失败补偿、guard 语义缺失

- [ ] **Step 3: 实现 create path guard、持久化与补偿**

实现要求：
- create 前先检查 same-conversation active-task guard
- create 前执行 per-user active-task cap 检查
- create 成功后只写一条 user turn、只创建一条 assistant placeholder，并把 `active_task_id` 绑定到 conversation
- queue_full / overloaded 不留下会话痕迹
- create 失败或半途异常时回滚 conversation side effects

- [ ] **Step 4: 重跑 create-path 定向测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider
```

Expected:
- PASS
- create 语义和失败补偿稳定

- [ ] **Step 5: Commit**

```bash
git add gateway/app/services/qa_tasks.py gateway/app/services/execution_admission.py gateway/app/routers/tasks.py gateway/app/routers/qa.py gateway/tests/test_task_api.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_task_runtime.py
git commit -m "feat: add admission-aware task creation semantics"
```

### Task 6: 打通全局 LLM Admission、队列、Tier Ceiling 与 Worker 执行

**Files:**
- Modify: `gateway/app/core/config.py`
- Modify: `gateway/app/services/execution_admission.py`
- Modify: `gateway/app/services/execution_queue_status.py`
- Modify: `gateway/app/services/execution_slot_leases.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Test: `gateway/tests/test_config.py`
- Test: `gateway/tests/test_execution_admission.py`
- Test: `gateway/tests/test_execution_queue_status.py`
- Test: `gateway/tests/test_task_api.py`

**Testing Requirement:**
- 必须先锁红灯测试，覆盖 global ceiling、tier ceiling、thinking 保底、queue ttl、queue max size、queued 不占 LLM slot、admitted/running 才占 slot。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_task_api.py -p no:cacheprovider`
- 若测试或依赖需要提权，必须提权跑；提不了权就停下汇报阻塞。

- [ ] **Step 1: 写 admission/capacity 红灯测试**

至少覆盖：
- `INTERACTIVE_EXECUTION_MAX_CONCURRENT`
- `INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT`
- `INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT`
- `INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE`
- `INTERACTIVE_EXECUTION_THINKING_MIN_SLOTS`
- `INTERACTIVE_QUEUE_MAX_SIZE`
- `INTERACTIVE_QUEUED_TTL_SECONDS`
- per-user active-task cap 是配置项，不是写死常量 `5`
- queued 任务不占 LLM slot
- admitted/running 才占 slot
- high tier 优先但 thinking 不饿死
- web gateway role 不 claim/dispatch queued work
- admission-worker role 才 claim/dispatch queued work

- [ ] **Step 2: 跑红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_task_api.py -p no:cacheprovider
```

Expected:
- FAIL
- 失败点集中在新 admission config surface、runtime-role 边界、queue max size、thinking min slot、状态占槽规则缺失

- [ ] **Step 3: 实现 admission config 与 scheduler rule**

实现要求：
- 保留 dedicated admission-worker role，不让 web 进程变成 scheduler source of truth
- global ceiling 与 tier ceiling 同时生效
- per-user active-task cap 从 `INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE` 读取，不写死在业务代码里
- thinking min slot 只做保底，不替代 low-tier ceiling
- queue max size、queue ttl 生效

- [ ] **Step 4: 实现 queued/admitted 调度与 slot 生命周期**

实现要求：
- queue wait 不占 slot
- admitted/running 才占 slot
- worker/lease 失效能回收 slot
- patent readiness fail-fast 不占 queue/backlog

- [ ] **Step 5: 重跑 admission/capacity 定向测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_task_api.py -p no:cacheprovider
```

Expected:
- PASS
- admission/capacity/queue lifecycle 稳定

- [ ] **Step 6: Commit**

```bash
git add gateway/app/core/config.py gateway/app/services/execution_admission.py gateway/app/services/execution_queue_status.py gateway/app/services/execution_slot_leases.py gateway/app/services/qa_tasks.py gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_task_api.py
git commit -m "feat: add global llm admission for qa tasks"
```

### Task 7: 打通 Worker 执行、State Events、回放、取消与 Quota 生命周期

**Files:**
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/execution_event_relay.py`
- Modify: `gateway/app/services/execution_admission.py`
- Modify: `gateway/app/routers/tasks.py`
- Test: `gateway/tests/test_task_api.py`
- Test: `gateway/tests/test_execution_event_relay.py`
- Test: `gateway/tests/test_qa_proxy.py`

**Testing Requirement:**
- 必须先锁红灯测试，覆盖 queued/admitted state events、同一 seq 空间、`after_seq` 补帧、running cancel、quota precheck/finalize/abort 绑定 task，而不是订阅。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_execution_event_relay.py gateway/tests/test_qa_proxy.py -p no:cacheprovider`
- 若测试或依赖需要提权，必须提权跑；提不了权就停下汇报阻塞。

- [ ] **Step 1: 写 worker/replay/cancel/quota 红灯测试**

至少覆盖：
- create task 后先发 queued/admitted 状态事件
- queued/admitted/running/content 共用一条单调递增 `seq`
- `after_seq` 只补缺失帧
- queued task `cancel` 会移出队列并落真实终态 `canceled`
- running task `cancel` 触发真实终态 `canceled`
- terminal `expired` / `failed` / `canceled` abort grant
- terminal `completed` finalize grant
- reattach 不二次占 quota

- [ ] **Step 2: 跑红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_execution_event_relay.py gateway/tests/test_qa_proxy.py -p no:cacheprovider
```

Expected:
- FAIL
- 失败点集中在 state-event relay、running cancel、quota terminal 绑定、事件续接

- [ ] **Step 3: 实现 queued/admitted/running state-event relay**

实现要求：
- queued/admitted/running 都能写入 relay
- 状态事件与内容事件共用一个 `seq` 空间
- state-event 不伪造 content frame

- [ ] **Step 4: 实现 worker claim/run/finalize 主链路**

实现要求：
- claim admitted task 后打开 downstream stream
- 每个事件写 relay、更新 summary、推动 public-service progress
- done/error/cancel/expired 走统一 terminal 流程

- [ ] **Step 5: 实现 queued/running cancel 与 quota terminal 处理**

实现要求：
- queued task cancel 会移出队列、写 terminal `canceled`、清掉 `active_task_id`
- task cancel 设置 cancel intent，并中止 worker-owned upstream stream
- success finalize grant
- failed/canceled/expired abort grant
- 不能把 reattach 当成新 quota 事件

- [ ] **Step 6: 重跑 worker/replay/quota 定向测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_execution_event_relay.py gateway/tests/test_qa_proxy.py -p no:cacheprovider
```

Expected:
- PASS
- state-event、真实后台任务、回放、取消、quota 生命周期全部跑通

- [ ] **Step 7: Commit**

```bash
git add gateway/app/services/qa_tasks.py gateway/app/services/execution_event_relay.py gateway/app/services/execution_admission.py gateway/app/routers/tasks.py gateway/tests/test_task_api.py gateway/tests/test_execution_event_relay.py gateway/tests/test_qa_proxy.py
git commit -m "feat: run qa asks as queued recoverable tasks"
```

### Task 8: 在 Gateway Conversation 读取路径上补 `active_task` enrichment

**Files:**
- Modify: `gateway/app/routers/public_proxy.py`
- Test: `gateway/tests/test_public_proxy.py`
- Test: `gateway/tests/test_task_api.py`

**Testing Requirement:**
- 必须用红灯测试锁住 `/api/conversations` 与 `/api/v1/conversations` 两套 alias 都能拿到 queued/admitted/running `active_task`。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_public_proxy.py gateway/tests/test_task_api.py -p no:cacheprovider`
- 如命令运行需要提权，必须提权；提不了权则停止说明。

- [ ] **Step 1: 写 conversation enrichment 红灯测试**

至少覆盖：
- list/detail 两个接口
- `/api/...` 与 `/api/v1/...` 两套 alias
- 绑定 queued/admitted/running task 存在时有 `active_task`
- task 缺失/过期/超出保留时 `active_task = null`

- [ ] **Step 2: 跑红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_public_proxy.py gateway/tests/test_task_api.py -p no:cacheprovider
```

Expected:
- FAIL
- 当前 pass-through proxy 不会 enrichment

- [ ] **Step 3: 实现 gateway list/detail enrichment cutover**

实现要求：
- 仅拦截 conversation list/detail GET
- 其他 conversation 路由暂时保持 pass-through
- enrichment 来源是 public-service conversation truth + gateway task summary join

- [ ] **Step 4: 重跑 conversation enrichment 定向测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_public_proxy.py gateway/tests/test_task_api.py -p no:cacheprovider
```

Expected:
- PASS
- shipped frontend 当前使用的非 `v1` 读取路径也能看到 queued/admitted/running `active_task`

- [ ] **Step 5: Commit**

```bash
git add gateway/app/routers/public_proxy.py gateway/tests/test_public_proxy.py gateway/tests/test_task_api.py
git commit -m "feat: enrich conversation reads with queued task state"
```

### Task 9: 补齐 rollout flag 接线，保证可灰度切换

**Files:**
- Modify: `gateway/app/core/config.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/app/routers/tasks.py`
- Modify: `gateway/tests/test_config.py`
- Modify: `gateway/tests/test_task_api.py`
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/services/api.structure.test.js`
- Modify: `frontend-vue/src/stores/chatStore.js`

**Testing Requirement:**
- 必须先把 rollout flag 行为锁成红灯测试，明确 backend 与 frontend 都支持灰度：flag 关闭时保留 legacy ask 路径，flag 开启时切到 admission-aware task API。
- 推荐固定 env 名称：
  - `GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED`
  - `VITE_REFRESH_SURVIVABLE_QA_TASKS_ENABLED`
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_config.py gateway/tests/test_task_api.py -p no:cacheprovider`
  - `cd frontend-vue && npm run test -- src/services/api.structure.test.js`
- 若命令或依赖需要提权，必须提权；提不了权则停止并报告。

- [ ] **Step 1: 写 rollout flag 红灯测试**

至少覆盖：
- backend flag 默认关闭
- flag 打开时 admission-aware task path 可用
- flag 关闭时前端默认路径不走新 task API
- 回滚后已存在 task 仍可读取/完成，但新建关闭

- [ ] **Step 2: 跑 rollout flag 红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_config.py gateway/tests/test_task_api.py -p no:cacheprovider
cd frontend-vue && npm run test -- src/services/api.structure.test.js
```

Expected:
- FAIL
- 当前缺少 admission-aware tasks flag 的 backend/frontend 对齐接线

- [ ] **Step 3: 实现 gateway / frontend rollout flag**

实现要求：
- `GatewaySettings` 暴露 task-path 开关
- gateway 只在 flag 打开时正式暴露 admission-aware task path
- frontend 在 flag 关闭时不切换 shipped send path
- flag 打开后，Task 10 的真实前端恢复逻辑才能成为默认路径

- [ ] **Step 4: 重跑 rollout flag 定向测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_config.py gateway/tests/test_task_api.py -p no:cacheprovider
cd frontend-vue && npm run test -- src/services/api.structure.test.js
```

Expected:
- PASS
- rollout 开关在 backend/frontend 两端对齐

- [ ] **Step 5: Commit**

```bash
git add gateway/app/core/config.py gateway/app/main.py gateway/app/routers/tasks.py gateway/tests/test_config.py gateway/tests/test_task_api.py frontend-vue/src/services/api.js frontend-vue/src/services/api.structure.test.js frontend-vue/src/stores/chatStore.js
git commit -m "feat: gate admission-aware qa tasks behind rollout flags"
```

### Task 10: 前端改成真实 Task 创建、恢复与停止

**Files:**
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Create: `frontend-vue/src/utils/taskReplayCursor.js`
- Create: `frontend-vue/src/utils/taskReplayCursor.test.js`
- Create or extend: `frontend-vue/src/stores/chatStore.task-recovery.test.js`

**Testing Requirement:**
- 必须先用前端测试锁住：新发送路径走 task API、queued 显式展示、刷新后从 `active_task + last_seq` 自动恢复 queued/admitted/running、停止是真实 cancel，不是只清本地状态。
- 必跑命令：
  - `cd frontend-vue && npm run test -- src/utils/taskReplayCursor.test.js src/stores/chatStore.task-recovery.test.js`
  - `cd frontend-vue && npm run build`
- 如前端测试、构建或浏览器相关依赖需要提权，必须提权；若提不了或无法跑，停止并报告。

- [ ] **Step 1: 写 task replay cursor 与 chat recovery 红灯测试**

至少覆盖：
- 保存/读取 `last_seq`
- 打开会话时依据 `active_task` 自动发起 recover
- queued/admitted/running 都能自动恢复
- `after_seq` 不重复渲染已消费事件
- replay 不可用时回落 conversation truth

- [ ] **Step 2: 写 Home task send/stop/queue-state 红灯测试**

至少覆盖：
- 发送不再直接调用旧 `ask_stream`
- queued 状态有明确 UI 文案/状态
- admitted 状态有明确 UI 文案/状态
- 停止调用 task cancel API
- 当前与非当前会话的 recoverable 状态渲染

- [ ] **Step 3: 跑前端红灯测试**

Run:
```bash
cd frontend-vue && npm run test -- src/utils/taskReplayCursor.test.js src/stores/chatStore.task-recovery.test.js
```

Expected:
- FAIL
- 当前前端仍是页面级 `ask_stream` fetch + local runtime

- [ ] **Step 4: 实现 frontend task API / recovery cursor / Home 交互**

实现要求：
- `api.js` 增加 task create/detail/events/cancel
- `chatStore` 维护 `active_task` 与 `last_seq`
- `Home.vue` 发送走 create task，恢复走 events attach，停止走 cancel
- queued 时显示明确排队态，而不是伪装成 running
- 保留 legacy path behind flag，不直接删除旧逻辑

- [ ] **Step 5: 跑前端定向测试与构建**

Run:
```bash
cd frontend-vue && npm run test -- src/utils/taskReplayCursor.test.js src/stores/chatStore.task-recovery.test.js
cd frontend-vue && npm run build
```

Expected:
- PASS
- build 成功

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/services/api.js frontend-vue/src/stores/chatStore.js frontend-vue/src/views/Home.vue frontend-vue/src/utils/taskReplayCursor.js frontend-vue/src/utils/taskReplayCursor.test.js frontend-vue/src/stores/chatStore.task-recovery.test.js
git commit -m "feat: recover queued qa tasks after refresh"
```

### Task 11: 做显式 E2E 恢复/续流/取消验证，证明不是空壳

**Files:**
- Create: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Verify: `gateway/tests/test_task_api.py`
- Verify: `gateway/tests/test_public_proxy.py`
- Verify: `gateway/tests/test_qa_proxy.py`
- Verify: `public-service/backend/tests/test_conversation_task_runtime.py`
- Verify: `frontend-vue`

**Testing Requirement:**
- 这是 reviewer 明确要求的跨系统 E2E task，必须包含完整路径：gateway create -> queue/admit -> 首次订阅 -> 刷新后 `after_seq` 续流 -> cancel -> conversation truth 校验。
- E2E 测试不能把 replay / cancel / conversation truth 断言退化成纯 unit mock；至少要在 gateway 测试层把真实 task create、事件续流和 conversation detail 校验串起来。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_task_api.py gateway/tests/test_public_proxy.py gateway/tests/test_qa_proxy.py -p no:cacheprovider`
  - `conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider`
  - `cd frontend-vue && npm run build`
- 若任何命令因提权、环境能力或依赖服务不可达无法完成，必须先提权；若仍无法完成，停止并逐条报告。

- [ ] **Step 1: 写显式 E2E 红灯测试**

至少覆盖：
- 通过 gateway `POST /api/v1/tasks` 创建任务
- 如果容量已满，先进入 queued，再被 admitted
- 首次订阅消费一部分事件并记录 `last_seq`
- 模拟刷新后以 `after_seq=last_seq` 续流，只拿到缺失帧
- 续流后的 stop 走真实 cancel API
- 最终通过 gateway conversation detail 断言：只有一条 user turn、同一个 assistant placeholder/terminal message、`active_task` 已清空

- [ ] **Step 2: 跑 E2E 红灯测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_task_api.py gateway/tests/test_public_proxy.py gateway/tests/test_qa_proxy.py -p no:cacheprovider
```

Expected:
- FAIL
- 若前面 task 仍有遗漏，这里必须显式暴露，而不是跳过

- [ ] **Step 3: 补齐 E2E 暴露出的残余缺口**

实现要求：
- 只能修复真实缺口，不能把 E2E 测试降级成假断言
- 若失败来自 create side effects、queueing、conversation enrichment、cancel 或 replay，回到对应模块补最小实现

- [ ] **Step 4: 重跑 E2E 与回归**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_task_api.py gateway/tests/test_public_proxy.py gateway/tests/test_qa_proxy.py -p no:cacheprovider
conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider
cd frontend-vue && npm run build
```

Expected:
- PASS
- queueing、刷新恢复、续流、取消、conversation truth 五个维度同时成立

- [ ] **Step 5: Commit**

```bash
git add gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_task_api.py gateway/tests/test_public_proxy.py gateway/tests/test_qa_proxy.py public-service/backend/tests/test_conversation_task_runtime.py frontend-vue
git commit -m "test: verify queued task recovery end to end"
```

### Task 12: 做真实跨子系统回归，验证不是空壳

**Files:**
- Verify: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Verify: `gateway/tests/test_task_api.py`
- Verify: `gateway/tests/test_public_proxy.py`
- Verify: `gateway/tests/test_qa_proxy.py`
- Verify: `gateway/tests/test_execution_admission.py`
- Verify: `gateway/tests/test_execution_queue_status.py`
- Verify: `public-service/backend/tests/test_conversation_authority_api.py`
- Verify: `public-service/backend/tests/test_conversation_module.py`
- Verify: `public-service/backend/tests/test_conversation_task_runtime.py`
- Verify: `fastQA/tests/test_qa_placeholder.py`
- Verify: `fastQA/tests/test_qa_route_aliases.py`
- Verify: `highThinkingQA/tests/test_ask_router_summary_persistence.py`
- Verify: `highThinkingQA/tests/test_ask_service_executor.py`
- Verify: `frontend-vue`

**Testing Requirement:**
- 这是整项功能的真实性验证 task，不能跳过。
- 必跑命令：
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_refresh_survivable_task_e2e.py -p no:cacheprovider`
  - `conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_public_proxy.py gateway/tests/test_qa_proxy.py gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py -p no:cacheprovider`
  - `conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider`
  - `conda run --no-capture-output -n agent pytest -q fastQA/tests/test_qa_placeholder.py fastQA/tests/test_qa_route_aliases.py -p no:cacheprovider`
  - `conda run --no-capture-output -n agent pytest -q highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_ask_service_executor.py -p no:cacheprovider`
  - `cd frontend-vue && npm run build`
- 若任何命令因为提权、环境能力、依赖服务不可达而跑不起来，必须先提权；若仍无法完成验证，停止并逐条报告。

- [ ] **Step 1: 跑 E2E 定向回归**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_refresh_survivable_task_e2e.py -p no:cacheprovider
```

Expected:
- PASS

- [ ] **Step 2: 跑 gateway 回归**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_task_api.py gateway/tests/test_public_proxy.py gateway/tests/test_qa_proxy.py gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py -p no:cacheprovider
```

Expected:
- PASS

- [ ] **Step 3: 跑 public-service 回归**

Run:
```bash
conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_task_runtime.py -p no:cacheprovider
```

Expected:
- PASS

- [ ] **Step 4: 跑 QA backend 回归**

Run:
```bash
conda run --no-capture-output -n agent pytest -q fastQA/tests/test_qa_placeholder.py fastQA/tests/test_qa_route_aliases.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_ask_service_executor.py -p no:cacheprovider
```

Expected:
- PASS

- [ ] **Step 5: 跑前端构建验证**

Run:
```bash
cd frontend-vue && npm run build
```

Expected:
- PASS

- [ ] **Step 6: 提交整体验证后的收口 commit**

```bash
git add gateway public-service fastQA highThinkingQA frontend-vue
git commit -m "feat: support queued refresh-survivable qa task recovery"
```

### Task 13: 外部 Patent Backend Gate 与发布前检查

**Files:**
- Verify only: external patent backend behind `PATENT_BACKEND_BASE_URL`
- Verify only: runtime config / feature flag wiring
- Optional docs note if needed: `docs/superpowers/specs/2026-04-06-refresh-survivable-qa-tasks-design.md`

**Testing Requirement:**
- 这个 task 不是本仓库本地实现，而是 rollout gate。
- 若当前环境接不上真实 patent backend，就不能声称 patent 模式已经随此功能完成，只能记录为 rollout gate 未打开。
- 需要真实验证时，若命令/环境访问需要提权，必须提权；若无法提权或无法访问外部 backend，停止并说明 patent gate 未满足。

- [ ] **Step 1: 验证 patent backend contract 是否存在**

检查项：
- gateway worker 能否以 task 模式连上 patent backend
- patent backend 是否支持 disconnect-driven cancel
- patent backend 是否在 `X-Gateway-Owned-Persistence: 1` 下跳过自身 persistence

- [ ] **Step 2: 若 gate 未满足，明确记录为未开启**

要求：
- 不改成假实现
- 不在本地 mock 一个 patent backend 冒充完成
- 只把 `patent` 标记为 rollout gate pending

- [ ] **Step 3: 若 gate 满足，再做单独发布验证**

建议命令由部署环境决定，必须基于真实 backend，不在本仓库假造。

---

## Review Loop

Plan written. Next required workflow:

1. 发一个 reviewer 审这份 impl 文档
2. 按 reviewer 结论修改
3. 复用同一个 reviewer 复审直到 PASS

## Execution Note

实现时严格按 task 顺序推进，不要跳 task。

- Task 1-8 先把后端 admission-aware 真实能力打通
- Task 9 再完成 rollout flag
- Task 10 再切前端
- Task 11-12 做显式 E2E 与整体验证
- Task 13 只作为 patent rollout gate，不是本地伪实现入口
