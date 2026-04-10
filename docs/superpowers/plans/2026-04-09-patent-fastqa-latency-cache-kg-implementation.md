# Patent/FastQA 延迟、缓存、渲染缺陷与知识图谱接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 2026-04-09 这轮迭代里，真实修复 patent 普通 QA 首 chunk 慢、前端重复渲染与 Raw Markdown 泄露、patent 文件/混合回答过短问题，并把 patentQA 与 fastQA 一并接入可降级的知识图谱问答能力。

**Architecture:** 方案分三条主线推进。第一条是“时延与缓存主线”，先把 gateway -> patent 的首包链路埋点锁死，再拆除首包前的同步阻塞，并补齐 patent 各阶段 Redis 缓存与运行时证据。第二条是“流契约与渲染正确性主线”，先定义 gateway/前端共享的流事件终态契约，再分别加固 relay 去重与前端终态渲染收敛。第三条是“答案质量与 KG 主线”，先把 patent 文件/混合回答质量对齐 fastQA，再建立统一 KG 请求契约、配置契约与失败降级路径，最后通过联调矩阵收口。

**Tech Stack:** Vue 3 + Pinia + Vite, FastAPI, Python, pytest, Node test runner, Redis, gateway task/event pipeline, patent runtime, fastQA request adapter and file-route pipeline

---

## Source Documents

- Streaming / task recovery design:
  - `docs/superpowers/specs/2026-04-07-streaming-latency-remediation-design.md`
  - `docs/superpowers/plans/2026-04-07-streaming-latency-remediation-implementation.md`
- Patent file / answer quality context:
  - `docs/superpowers/specs/2026-04-04-patent-file-qa-remediation-design.md`
  - `docs/superpowers/specs/2026-04-02-patent-file-routing-design.md`
- Cache / routing context:
  - `docs/superpowers/specs/2026-03-24-qa-stage-cache-design.md`
  - `docs/superpowers/specs/2026-03-30-gateway-qa-routing-design.md`
- Refresh-survivable task context:
  - `docs/superpowers/specs/2026-04-06-refresh-survivable-qa-tasks-design.md`

## Workspace Conventions

### Repository Root

所有命令默认以仓库根目录为起点；不要把 `/home/cqy/worktrees/highThinking` 之类的绝对路径写死到执行命令里。

推荐先拿根目录：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
```

然后使用 repo-relative 命令，例如：

```bash
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_chat_persistence.py -q
cd "$REPO_ROOT/gateway" && pytest tests/test_task_api.py -q
cd "$REPO_ROOT/frontend-vue" && npm test -- src/views/Home.structure.test.js
```

### Verification Tiers

1. **Tier A: Repo-local verification**
   - 适用于绝大多数单元测试、契约测试、Node 测试、frontend build。
   - 不默认要求提权；只要求依赖已安装且工作树可写。
2. **Tier B: Service-backed verification**
   - 适用于 Redis 命中验证、gateway/patent/fastQA 联调、首包时延观测、真实 KG backend 探活。
   - 只有这些步骤才需要额外环境前提，例如运行中的 Redis、本地服务进程、可访问的 KG 服务。
3. **如果当前环境策略要求通过特定方式运行命令**
   - 在不改变命令语义的前提下外包一层即可，例如 `conda run -n agent bash -lc 'cd ... && pytest ...'`。
   - 计划里的“验收是否成立”取决于命令本身和期望输出，不取决于外层包装方式。

## Fixed Fixtures And Acceptance Targets

### A. 首包时延验收基线

Task 1/2/11 必须围绕同一组固定夹具锁定，不允许临时换 prompt 规避预算。

**Latency Fixture A: patent durable kb_qa hot path**
- route: `kb_qa`
- requested_mode / actual_mode: `patent`
- source_scope: `kb`
- question: `请概括该专利方案的核心创新点、替代风险和适用边界。`
- chat history: 空数组
- 额外要求：authority/context、planner、retrieval 均使用测试替身或 warmed runtime，避免把外部模型不确定性混入“是否存在同步阻塞”判断。

**Latency telemetry fields that must exist after Task 1**
- `accepted_at_ms`
- `dispatch_started_at_ms`
- `backend_stream_opened_at_ms`
- `first_step_at_ms`
- `first_content_at_ms`
- `accepted_to_first_step_ms`
- `dispatch_to_first_step_ms`
- `accepted_to_first_content_ms`

**Automated budget for Task 2**
- 在 Latency Fixture A + 可控时钟/测试替身下：
  - `accepted_to_first_step_ms <= 300`
  - `dispatch_to_first_step_ms <= 150`
- 该预算的目标是证明“metadata/step 不再被 authority/context 等同步阻塞吞掉”，不是衡量真实模型生成速度。

**Service-backed budget for Task 11 release gate**
- 在 warmed service 进程 + 健康 Redis + 本地 stack 正常时：
  - `accepted_to_first_step_ms <= 500`
  - `accepted_to_first_content_ms <= 1500`
- 如果真实模型波动导致 `first_content` 超出预算，但 `first_step` 预算满足，必须单独记录为模型或下游依赖风险，不能回填成“首包阻塞已修复”。

### B. 文件 / 混合回答深度验收基线

Task 6/11 必须围绕固定夹具与固定 rubric，不能只看字数。

**Depth Fixture P1: patent file QA**
- route: `pdf_qa`
- question: `请总结这篇文献/专利的核心结论、关键证据、与现有方案的差异，以及仍不确定的边界。`
- execution_files: 单个 PDF
- 断言目标：证据充足时，回答不是“单段结论 + 两句泛化描述”。

**Depth Fixture P2: patent hybrid QA**
- route: `hybrid_qa`
- question: `请结合文件内容和知识库，比较该方案与现有技术的优势、局限、替代风险，并给出建议。`
- execution_files: 至少 1 个 PDF 或表格文件
- kb_enabled: `true`

**Answer-depth rubric**
- 必须同时满足：
  1. 输出包含四类信息块，允许标题不同，但语义必须完整：`结论` / `证据` / `对比` / `限制或边界`
  2. 至少 3 条可归因的证据点
  3. mixed 场景下至少 1 条文件证据 + 1 条 KB 证据
  4. 至少 1 条明确的不确定性、限制条件或适用边界
  5. 失败样例要在测试里锁住：
     - 只有一段笼统总结
     - 没有证据点列表
     - 没有对比项
     - 没有局限/边界

### C. KG Service Contract

Task 7/8/9/10/11 必须遵守统一 KG 契约；计划阶段先把契约写死，执行时不得各自发明字段。

**Gateway request contract additions**
- `kg_enabled: bool`
- `kg_mode: "disabled" | "entity" | "path" | "hybrid"`

**fastQA runtime config contract**
- `QA_KG_ENABLED`
- `QA_KG_BASE_URL`
- `QA_KG_API_KEY`
- `QA_KG_TIMEOUT_SECONDS`
- `QA_KG_TOPK`
- `QA_KG_DEFAULT_MODE`

**patent runtime config contract**
- `PATENT_KG_ENABLED`
- `PATENT_KG_BASE_URL`
- `PATENT_KG_API_KEY`
- `PATENT_KG_TIMEOUT_SECONDS`
- `PATENT_KG_TOPK`
- `PATENT_KG_DEFAULT_MODE`

**KG HTTP contract**
- Request:

```json
{
  "question": "...",
  "trace_id": "...",
  "mode": "entity|path|hybrid",
  "top_k": 5,
  "requested_mode": "fast|patent",
  "route": "kb_qa|hybrid_qa",
  "source_scope": "kb|pdf+kb|table+kb|pdf+table+kb"
}
```

- Response:

```json
{
  "items": [
    {
      "entity_id": "kg-node-1",
      "entity_name": "...",
      "relation": "...",
      "evidence_text": "...",
      "source": "kg",
      "source_id": "...",
      "score": 0.91
    }
  ],
  "trace_id": "..."
}
```

**KG failure / test-double contract**
- 单元测试和契约测试一律使用 in-process fake client、stub service 或 `httpx.MockTransport`；不依赖真实网络。
- 真正访问外部 KG 服务只允许出现在 Task 11 release gate。
- 当 KG backend timeout / 5xx / contract invalid 时，必须：
  - 记录 `kg_used=false`
  - 保留原有 non-KG 主链路答案
  - 不让 ask 请求整体失败

### D. Patent Cache Evidence Contract

Task 3/11 不能只靠 pytest 证明“缓存接上了”；必须留下真实运行时证据。

**A task is not complete unless the second identical request proves a real hit via at least one of:**
- response metadata 中出现 `cache_hit=true` 或 stage-specific hit field
- 日志中出现 stage-specific cache hit 记录
- Redis 中能观察到对应 namespace 的 key

**Expected Redis namespaces**
- `patent:<env>:qa-core:cache:stage1:*`
- `patent:<env>:qa-core:cache:stage2:*`
- `patent:<env>:qa-core:cache:stage25:*`
- `patent:<env>:qa-core:cache:stage3:*`
- `patent:<env>:qa-core:cache:stage4:*`
- `patent:<env>:qa-core:cache:file-route:*` 或等价 file-route namespace

### E. Stream Contract For Task 4 And Task 5

Task 4 与 Task 5 必须围绕同一条 canonical stream contract；否则一个 task 的测试会被另一个 task 推翻。

**Canonical contract**
1. frontend 只信 gateway relay 分配的递增 `seq`；不再以 downstream 原始 `payload.seq` 作为本地消息去重依据。
2. 对同一 request，如果 upstream 重复发送同一个 `payload.seq`，gateway relay 只能保留第一次；后续重复帧不得制造新的用户可见 content。
3. 一旦出现 terminal event（`done` / `failed` / `canceled` / `expired`），后续 content 不得再出现在 replay 或 live stream 中。
4. frontend 即使遇到“历史脏 replay 窗口”或“漏网重复 terminal/content”，也必须保持只渲染一份最终答案。
5. Task 4 的测试必须同时覆盖：
   - 当前重复 replay 行为
   - Task 5 落地后的去重行为
   这样无论先落哪个 task，测试都不失效。

## Hard Rules

1. 不能做空壳子。新增字段、缓存、开关、helper、日志、注释而未接入真实主链路，都不算完成。
2. Task 1-10 必须遵守：红灯测试 -> 最小实现 -> 目标测试转绿 -> review -> commit。
3. Task 11 是 release gate，不要求“红灯测试 -> 实现 -> commit”，但要求完整验证、review、以及残余风险记录。
4. Repo-local 测试和 build 默认直接跑；只有真实需要外部依赖或服务启动的步骤才进入 Tier B。
5. frontend 相关任务不能只跑 `npm test`；至少还要跑一次 `cd frontend-vue && npm run build`。
6. 任何 task 如果声明会改动某个已有测试覆盖面的文件，就必须把对应专测加入 required command，除非在 task 里明确写明为什么该专测不受影响。
7. 所有路径、命令、环境变量都必须可被零上下文工程师直接理解；禁止把当前工作树绝对路径写进执行命令。

## Per-Task Review Gate

### Task 1-10 必须统一执行的收口流程

1. 写红灯测试
2. 跑红灯测试并确认失败点对准本 task
3. 做最小实现
4. 跑本 task 的 required command，确认转绿
5. 发起 review
6. 如果 review 有问题，修复后重跑本 task required command，再复审，直到 pass
7. Commit

### Task 11 例外规则

Task 11 是验证与发布前验收，不做功能实现。它的完成标准是：
- 验证矩阵跑完
- 关键手工场景过一遍
- reviewer pass
- 残余风险被明确记录

## File Map

### Latency & Admission Chain

- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/execution_event_relay.py`
- Modify if timestamps need surfacing: `gateway/app/routers/tasks.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Modify: `patent/server/patent/orchestrators/generation.py`

### Patent Cache Surface

- Modify: `patent/server/services/execution_cache.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/config.shared.env.example`
- Modify: `resource/config/services/patent/config.shared.env`

### Frontend Rendering / Recovery

- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `frontend-vue/src/utils/streamingRender.js`
- Modify: `frontend-vue/src/utils/messageRenderMemo.js`
- Modify if state wiring is required: `frontend-vue/src/stores/chatStore.js`
- Modify if panel-level guards are required: `frontend-vue/src/features/chat/components/ChatPanel.vue`

### Patent File / Hybrid Answer Quality

- Modify: `patent/server/patent/pdf_contract.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server/patent/file_routes.py`

### KG Contract & Runtime Integration

- Modify: `gateway/app/models/ask.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/services/request_adapter.py`
- Modify: `fastQA/app/services/file_routes.py`
- Create: `fastQA/app/modules/kg_qa/client.py`
- Create: `fastQA/app/modules/kg_qa/service.py`
- Modify: `fastQA/app/modules/qa_kb/service.py`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify if example config is kept in sync: `resource/config/services/fastQA/config.env.example`
- Modify: `patent/server/schemas/request_models.py`
- Modify: `patent/server/patent/kb_service.py`
- Create: `patent/server/patent/kg_client.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/stages/synthesis.py`
- Modify: `frontend-vue/src/utils/chatRequestContext.js`
- Modify: `frontend-vue/src/features/chat/composables/useAskPreferences.js`
- Modify: `frontend-vue/src/features/controls/components/ControlsPanel.vue`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `patent/config.shared.env.example`
- Modify: `resource/config/services/patent/config.shared.env`

### Tests

- Modify: `gateway/tests/test_task_api.py`
- Modify: `gateway/tests/test_execution_event_relay.py`
- Modify: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Modify: `gateway/tests/test_ask_models.py`
- Modify: `gateway/tests/test_route_decision.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/test_chat_persistence.py`
- Modify: `patent/tests/test_conversation_authority_client.py`
- Modify: `patent/tests/test_execution_cache.py`
- Modify: `patent/tests/test_patent_generation_orchestrator.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/test_patent_retrieval_service.py`
- Modify: `patent/tests/test_patent_kb_service.py`
- Modify: `patent/tests/test_patent_stage4_synthesis.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `fastQA/tests/test_request_adapter.py`
- Modify: `fastQA/tests/test_qa_kb_service.py`
- Modify: `fastQA/tests/test_file_routes_tabular_kb.py`
- Modify: `fastQA/tests/test_qa_routes_file_modes.py`
- Modify if file-route materialization is affected: `fastQA/tests/test_file_routes_materialization.py`
- Create: `fastQA/tests/test_kg_qa_service.py`
- Modify: `frontend-vue/src/utils/recoverableTaskController.test.js`
- Modify: `frontend-vue/src/utils/streamingRender.test.js`
- Modify: `frontend-vue/src/utils/messageRenderMemo.test.js`
- Modify: `frontend-vue/src/utils/chatRequestContext.test.js`
- Modify: `frontend-vue/src/views/Home.structure.test.js`
- Create: `frontend-vue/src/features/chat/composables/useAskPreferences.test.js`

## Current State Notes The Implementer Must Honor

### Patent Stage Cache Inventory Before Task 3

当前仓库里已经能看到：
- `stage1` fingerprint builder: `build_stage1_cache_fingerprint()`
- `stage2` fingerprint builder: `build_stage2_cache_fingerprint()`
- `stage25` fingerprint builder: `build_stage25_cache_fingerprint()`
- `stage3` fingerprint builder: `build_stage3_cache_fingerprint()`
- stage-specific tests already存在于：
  - `patent/tests/test_execution_cache.py`
  - `patent/tests/test_patent_generation_orchestrator.py`
  - `patent/tests/test_patent_retrieval_service.py`

当前没有在这份计划里被明确锁死的，是：
- stage4 cache fingerprint / key / invalidation contract
- file-route cache namespace 与 singleflight contract
- second identical request 的真实 Redis hit 证据

Task 3 必须基于这个现状写，不要把“审计已有 stage1/2/25/3”与“新增 stage4/file-route”混成一句话带过。

---

### Task 1: 建立 patent 首 chunk 时延可观测基线

**Files:**
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Test: `gateway/tests/test_task_api.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`

**Testing Requirement:**
- 先把时延字段缺失锁成红灯，再补齐完整时间戳链路。
- 必须锁住从 `task accepted` 到 `first step` / `first content` 的字段命名与传播位置，不允许只打日志。
- Required commands:
  - `cd gateway && pytest tests/test_task_api.py -q`
  - `cd patent && PYTHONPATH=. pytest tests/fastapi_contract/test_ask_contract.py tests/test_patent_generation_orchestrator.py -q`

- [x] **Step 1: 写红灯测试，锁死 telemetry 字段契约**

覆盖：
- task detail 或 events metadata 内存在 `accepted_at_ms` / `dispatch_started_at_ms` / `backend_stream_opened_at_ms`
- 一旦出现 `step` 或 `done`，就能看到 `first_step_at_ms` / `first_content_at_ms`
- duration 字段是数值型且非负

- [x] **Step 2: 跑红灯测试并确认失败点集中在字段未落主链路**

Run:
```bash
cd gateway && pytest tests/test_task_api.py -q
cd patent && PYTHONPATH=. pytest tests/fastapi_contract/test_ask_contract.py tests/test_patent_generation_orchestrator.py -q
```

Expected:
- FAIL
- 失败点是字段不存在、字段名不稳定或 timestamp 没被透传到 gateway-visible payload

- [x] **Step 3: 最小实现统一 telemetry 采集点**

实现要求：
- `qa_tasks.py` 负责记录 `accepted_at_ms` 与 `dispatch_started_at_ms`
- patent ask/generation 主链路负责记录 `backend_stream_opened_at_ms` / `first_step_at_ms` / `first_content_at_ms`
- 不在多个层级重复计算同一 delta；统一由最接近事件源的位置落时间戳，再在对外 payload 中补导出 delta

- [x] **Step 4: 重跑时延 telemetry 相关测试**

Expected:
- PASS
- telemetry 字段可被稳定读取

- [ ] **Step 5: 发起 review，修到 pass**

- [ ] **Step 6: Commit**

```bash
git add gateway/app/services/qa_tasks.py patent/server/services/ask_service.py patent/server/patent/orchestrators/generation.py gateway/tests/test_task_api.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_generation_orchestrator.py
git commit -m "feat(patent): add first-chunk latency telemetry chain"
```

### Task 2: 修复 patent 普通 QA 首 chunk 前阻塞卡点

**Files:**
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/test_conversation_authority_client.py`
- Test: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Test if telemetry assertions live here: `gateway/tests/test_task_api.py`

**Testing Requirement:**
- 必须覆盖“authority/context 慢响应时，metadata/step 仍先出来”的 hot-path 行为。
- Required commands:
  - `cd patent && PYTHONPATH=. pytest tests/test_chat_persistence.py tests/test_conversation_authority_client.py -q`
  - `cd gateway && pytest tests/test_refresh_survivable_task_e2e.py tests/test_task_api.py -q`
- Release-gate metrics 见 Task 11；Task 2 自身先用 controlled fixture 锁住 `accepted_to_first_step_ms <= 300`。

- [x] **Step 1: 写红灯测试，复现 authority/context 同步阻塞吞掉首 step**

覆盖：
- authority snapshot 慢时，stream 仍先发 metadata 或 `step(stage1)`
- 失败路径下不会整段卡在 context fetch 之后才第一次出流
- `accepted_to_first_step_ms` 超预算时红灯

- [x] **Step 2: 跑红灯测试并确认失败**

Expected:
- FAIL
- 失败点是 metadata/step 迟迟不出现，或预算断言超时

- [x] **Step 3: 最小实现拆分 pre-stream 阻塞路径**

实现要求：
- 把“必须先完成的同步写/读”与“可在流启动后继续的工作”拆开
- `metadata/step` 必须先于慢 authority/context 路径暴露
- 保留现有 durable write 语义，不通过跳过 authority 写入来伪造快首包

- [x] **Step 4: 对慢调用补超时与降级边界**

实现要求：
- authority timeout / contract error 不能无限卡住首 step
- 降级必须有明确 metadata / failure 语义
- 不得吞异常后静默给出空答案

- [x] **Step 5: 重跑目标测试并记录预算已回到阈值内**

Expected:
- PASS
- controlled fixture 下 `accepted_to_first_step_ms <= 300`

- [ ] **Step 6: 发起 review，修到 pass**

- [ ] **Step 7: Commit**

```bash
git add patent/server/services/ask_service.py patent/server/services/chat_persistence.py patent/server/services/conversation_authority_client.py gateway/app/services/qa_tasks.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py gateway/tests/test_refresh_survivable_task_e2e.py gateway/tests/test_task_api.py
git commit -m "fix(patent): unblock first-step emission before slow authority work"
```

### Task 3: patentQA 各阶段 Redis 缓存全覆盖并留下真实命中证据

**Files:**
- Modify: `patent/server/services/execution_cache.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/config.shared.env.example`
- Modify: `resource/config/services/patent/config.shared.env`
- Test: `patent/tests/test_execution_cache.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`
- Test: `patent/tests/test_patent_file_routes.py`
- Test: `patent/tests/test_patent_retrieval_service.py`

**Testing Requirement:**
- 这不是“看看有没有 cache key”任务，而是“stage1/2/25/3 审计 + stage4/file-route 补齐 + Redis 实命中证明”。
- Required commands:
  - `cd patent && PYTHONPATH=. pytest tests/test_execution_cache.py tests/test_patent_generation_orchestrator.py tests/test_patent_file_routes.py tests/test_patent_retrieval_service.py -q`
- Required service-backed evidence in this task:
  - `redis-cli --scan --pattern 'patent:*:qa-core:cache:*'`
  - 第二次相同请求出现 stage-specific `cache_hit` 证据
  - 修改 runtime signature 或 file selection 后出现 miss

- [x] **Step 1: 先写红灯测试，按 stage inventory 把现状与缺口锁开**

覆盖：
- stage1/2/25/3 已有 fingerprint 不可回退
- stage4 当前没有稳定 fingerprint / key / invalidation 时必须红灯
- file-route 当前如果没有 namespace / singleflight / metadata contract，也必须红灯

- [x] **Step 2: 跑红灯测试并确认失败点只落在真实缓存缺口**

Expected:
- FAIL
- 失败点集中在 stage4 / file-route / runtime evidence，而不是已有 stage1/2/25/3 无谓回归

- [x] **Step 3: 补齐 stage4 缓存 fingerprint、key、TTL 与 invalidation contract**

实现要求：
- fingerprint 必须受 question、retrieval_results、evidence bundle、runtime signature 影响
- cache 结果不得吞掉 `metadata.references` / `original_links` 等最终答案相关字段
- runtime signature 变化必须 miss

- [x] **Step 4: 补齐 file-route cache namespace、singleflight 与 metadata**

实现要求：
- pdf_qa / tabular_qa / hybrid_qa 的 file-route 命中都要带 metadata
- singleflight 只保护同 fingerprint 并发，不扩大为全局串行
- 不能用配置项占位代替真实 route-level cache

- [x] **Step 5: 重跑 pytest，并完成 Redis-backed 二次请求命中验证**

Service-backed procedure:
1. 打开 `PATENT_REDIS_ENABLED=true`
2. 对同一 fixture 请求执行两次 patent ask
3. 记录第二次响应中的 `cache_hit`
4. `redis-cli --scan --pattern 'patent:*:qa-core:cache:*'` 截图或记录 key
5. 修改 runtime signature / source_scope / selected_file_ids 后再次请求，确认 miss

- [x] **Step 6: 发起 review，修到 pass**

- [ ] **Step 7: Commit**

```bash
git add patent/server/services/execution_cache.py patent/server/patent/cache_keys.py patent/server/patent/orchestrators/generation.py patent/server/patent/file_routes.py patent/config.shared.env.example resource/config/services/patent/config.shared.env patent/tests/test_execution_cache.py patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_retrieval_service.py
git commit -m "feat(patent): complete stage and file-route redis cache coverage"
```

### Task 4: 修复前端“重复内容 + Raw Markdown 泄露 + 截断”缺陷

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `frontend-vue/src/utils/streamingRender.js`
- Modify: `frontend-vue/src/utils/messageRenderMemo.js`
- Modify if state invalidation is required: `frontend-vue/src/stores/chatStore.js`
- Test: `frontend-vue/src/utils/recoverableTaskController.test.js`
- Test: `frontend-vue/src/utils/streamingRender.test.js`
- Test: `frontend-vue/src/utils/messageRenderMemo.test.js`
- Test: `frontend-vue/src/views/Home.structure.test.js`

**Dependency Note:**
- Task 4 不能假设 relay 一定已经完成去重。
- 它自己的测试必须能在“重复 content/terminal 仍可能出现”的输入下保持最终 UI 只渲染一份答案；Task 5 只是减少这种脏输入进入前端的概率。

**Testing Requirement:**
- Required commands:
  - `cd frontend-vue && npm test -- src/utils/recoverableTaskController.test.js src/utils/streamingRender.test.js src/utils/messageRenderMemo.test.js src/views/Home.structure.test.js`
  - `cd frontend-vue && npm run build`
- 必须新增行为测试锁住：
  - done 后不重复 append 原始文本
  - `##` / `###` 不会以 raw text 再次渲染
  - 主区域只保留一份最终答案
  - 即使收到重复 terminal/content，也不重复显示最终结果

- [ ] **Step 1: 写红灯测试，复现“上半渲染 + 下半 raw 文本重复”**

- [ ] **Step 2: 跑红灯测试并确认失败**

Expected:
- FAIL
- 失败点集中在终态 flush、memo key、render cache 或 message source 双写

- [ ] **Step 3: 最小实现单一消息写入源与终态 rerender 收敛**

实现要求：
- 流式渲染与终态渲染共享统一消息源
- terminal 状态切换即使内容字符串不变，也必须触发 final formatter rerender
- 不允许在 terminal 时再 append 一次 raw markdown

- [ ] **Step 4: 修正 truncation / flush 边界**

实现要求：
- buffer flush 发生在 terminal settle 之前
- render memo 必须把 terminal flags 纳入 key
- Home 级缓存切换到 terminal markdown 时必须失效

- [ ] **Step 5: 重跑前端测试 + build**

Expected:
- targeted tests PASS
- `npm run build` PASS

- [ ] **Step 6: 发起 review，修到 pass**

- [ ] **Step 7: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/utils/recoverableTaskController.js frontend-vue/src/utils/streamingRender.js frontend-vue/src/utils/messageRenderMemo.js frontend-vue/src/stores/chatStore.js frontend-vue/src/utils/recoverableTaskController.test.js frontend-vue/src/utils/streamingRender.test.js frontend-vue/src/utils/messageRenderMemo.test.js frontend-vue/src/views/Home.structure.test.js
git commit -m "fix(frontend): collapse duplicate terminal rendering and raw markdown leaks"
```

### Task 5: gateway 事件重放与终态收敛去重加固（配合 Task 4）

**Files:**
- Modify: `gateway/app/services/execution_event_relay.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Modify if event contract needs surfacing: `gateway/app/routers/tasks.py`
- Test: `gateway/tests/test_execution_event_relay.py`
- Test: `gateway/tests/test_task_api.py`

**Testing Requirement:**
- Required commands:
  - `cd gateway && pytest tests/test_execution_event_relay.py tests/test_task_api.py -q`
- 必须覆盖：
  - duplicate upstream seq
  - seq-less frame interleave
  - terminal 后重复 content
  - polluted replay window
  - worker 侧不 double-count duplicate frame

- [ ] **Step 1: 写红灯测试，锁死 canonical stream contract**

覆盖：
- relay sequence 是 frontend 唯一可信 seq
- duplicate upstream `payload.seq` 不会制造第二份用户可见 content
- terminal 后的新 frame 不再进入 replay 或 live tail

- [ ] **Step 2: 跑红灯测试并确认失败**

- [ ] **Step 3: 最小实现 relay 去重与 replay-window 防污染**

实现要求：
- append 时拦截 duplicate upstream seq
- replay 时也能隐藏历史污染帧
- 如果 relay 已判定 frame ignored，worker 不再本地重复计数或重复 flush

- [ ] **Step 4: 重跑 gateway 目标测试**

Expected:
- PASS
- duplicate / post-terminal frame 不再对外可见

- [ ] **Step 5: 发起 review，修到 pass**

- [ ] **Step 6: Commit**

```bash
git add gateway/app/services/execution_event_relay.py gateway/app/services/qa_tasks.py gateway/app/routers/tasks.py gateway/tests/test_execution_event_relay.py gateway/tests/test_task_api.py
git commit -m "fix(gateway): dedupe replay frames and block post-terminal content"
```

### Task 6: patent 文件 QA / 混合 QA 输出深度对齐 fastQA

**Files:**
- Modify: `patent/server/patent/pdf_contract.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Test: `patent/tests/test_patent_pdf_contract.py`
- Test: `patent/tests/test_patent_file_routes.py`

**Testing Requirement:**
- 不能只新增长度断言，必须把前面的 depth rubric 写进测试。
- Required commands:
  - `cd patent && PYTHONPATH=. pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -q`

- [x] **Step 1: 写红灯测试，锁死 Depth Fixture P1 / P2 的结构化输出要求**

覆盖：
- `结论/证据/对比/限制` 四类信息完整
- 至少 3 条证据点
- mixed 场景下至少一条文件证据和一条 KB 证据
- 单段泛化答案必须失败

- [x] **Step 2: 跑红灯测试并确认失败**

- [x] **Step 3: 最小实现 file / hybrid 路由的结构化 answer policy**

实现要求：
- 对齐 fastQA 的结构化回答骨架
- 在证据充足时优先补足 evidence / comparison / limitation，而不是堆字数
- route metadata 要明确区分 file-only 与 mixed 来源

- [x] **Step 4: 调整 prompt / fallback / merge 策略**

实现要求：
- file-only 不能伪造 KB 证据
- mixed 不允许只吃其中一边证据就给出“完整结论”
- fallback 答案也必须保留 limitation 提醒

- [x] **Step 5: 重跑目标测试，确认 rubric 过线**

- [x] **Step 6: 发起 review，修到 pass**

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/pdf_contract.py patent/server/patent/file_routes.py patent/server/patent/pdf_service.py patent/server/patent/tabular_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py
git commit -m "fix(patent): deepen file and hybrid answer structure"
```

### Task 6.1: 修复 compare 模式尾部模板泄露与重复总结拼接

**Why this is split out:**
- 这是 Task 6 范围内新发现的 compare 子缺陷，但它不是单纯“深度不够”，而是 compare answer 的结构识别与 merge/rewrite 逻辑把已经成型的 Markdown 对比答案再次重包，直接制造了用户可见的尾部脏文本。
- 现象是：主答案已经出现完整“总结”后，末尾又追加 `总结：` 与 `两篇文献对比分析 ## 1.` 之类未正确结构化的残余文本，破坏排版与专业度。

**Root-cause hypothesis already confirmed in code review:**
- `patent/server/patent/pdf_contract.py` 的 compare prompt 允许/鼓励模型输出 `1..5` 或 `## 1..## 5` 这种标准 Markdown 编号结构。
- 但 `patent/server/patent/pdf_service.py::_has_ordered_compare_sections()` 只识别 `各自概要 / 相同点 / 差异点 / 总结` 这组中文段落标题，不识别 `1.` / `## 1.` / `5. 总结` 等标准编号标题。
- 当模型已经返回完整 compare answer 时，`_ensure_compare_answer_structure()` 误判结构不合格，再次把整段答案塞进 `差异点`，并用 `_first_sentence()` 从原答案头部抽一句作为新的 `总结`，于是把 `两篇文献对比分析 ## 1.` 这种模板残片直接暴露给前端。

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify if prompt contract needs narrowing: `patent/server/patent/pdf_contract.py`
- Test: `patent/tests/test_patent_file_routes.py`
- Test if prompt contract changes: `patent/tests/test_patent_pdf_contract.py`

**Testing Requirement:**
- Required commands:
  - `cd patent && PYTHONPATH=. pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -q`
- 必须新增行为测试锁住：
  - compare 模式下，模型返回标准 Markdown 编号结构（如 `## 1. 文献概要 ... ## 5. 总结`）时，不得再次重包
  - 已有完整 compare 结构时，最终 `answer_text` 末尾不得再出现第二个 `总结：`
  - 最终 `answer_text` 中不得出现由后处理重新拼出的 `两篇文献对比分析 ## 1.` 之类跨行模板残片
  - 仍要保留对真正缺结构 / 缺证据答案的重整能力，不能把已有防线一起拆掉

- [x] **Step 1: 写红灯测试，复现“标准 compare Markdown 被误判后重包”**

覆盖：
- 模型返回 `## 1. ... ## 5. 总结` 的完整 compare 结构
- 后端当前实现错误地产生第二个 `总结：`
- 末尾包含 `两篇文献对比分析 ## 1.` 或同类跨行残片

- [x] **Step 2: 跑红灯测试并确认失败**

Expected:
- FAIL
- 失败点集中在 `_has_ordered_compare_sections()` / `_ensure_compare_answer_structure()` 的结构识别与 summary line 提取

- [x] **Step 3: 最小实现 compare answer 结构识别对齐 prompt contract**

实现要求：
- 识别标准 Markdown 编号 compare 结构：`1.` / `## 1.` / `5. 总结` 等
- 对已经完整且覆盖文献事实的 compare answer 直接保留原文，不再重包
- 不要靠简单截断去“掩盖”尾部脏文本，必须修正误判源头

- [x] **Step 4: 收紧 compare rewrite / summary 抽取逻辑**

实现要求：
- 只有在 compare answer 真正缺结构或缺文献覆盖时才触发 rewrite
- `_first_sentence()` 或等价逻辑不能再把跨行 Markdown 标题压成 `两篇文献对比分析 ## 1.` 这种残片
- 保持现有“缺证据时自动补结构”测试继续成立

- [x] **Step 5: 重跑 patent 目标测试**

Expected:
- compare 旧回归 PASS
- 新增 bug 回归 PASS
- Task 6 的 file / hybrid 深度对齐测试也不被回归破坏

- [x] **Step 6: 发起 review，修到 pass**

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/pdf_contract.py patent/server/patent/pdf_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py
git commit -m "fix(patent): preserve valid compare markdown structure"
```

### Task 7: 统一 KG QA 请求契约与 gateway 透传

**Files:**
- Modify: `gateway/app/models/ask.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `fastQA/app/services/request_adapter.py`
- Modify: `patent/server/schemas/request_models.py`
- Test: `gateway/tests/test_ask_models.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `gateway/tests/test_task_api.py`
- Test: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Test: `fastQA/tests/test_request_adapter.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

**Testing Requirement:**
- 先定义字段契约，再实现透传；不要先改 runtime 再回填 gateway model。
- Required commands:
  - `cd gateway && pytest tests/test_ask_models.py tests/test_route_decision.py tests/test_task_api.py tests/test_refresh_survivable_task_e2e.py -q`
  - `cd fastQA && pytest tests/test_request_adapter.py -q`
  - `cd patent && PYTHONPATH=. pytest tests/fastapi_contract/test_ask_contract.py -q`

- [ ] **Step 1: 写红灯测试，定义 `kg_enabled` / `kg_mode` 契约**

覆盖：
- gateway model 接受并校验字段
- route decision 不篡改用户显式 `kg_mode`
- fast/patent adapter 都能收到字段

- [ ] **Step 2: 跑红灯测试并确认失败**

- [ ] **Step 3: 最小实现 gateway -> fast/patent 字段透传**

实现要求：
- `kg_enabled=false` 时，`kg_mode` 归一到 `disabled`
- mixed / file-only 路由不因为 KG 字段而破坏现有 `source_scope`
- 不允许在 gateway 偷偷为 KG 打开默认路由改写

- [ ] **Step 4: 重跑目标测试确认协议稳定**

- [ ] **Step 5: 发起 review，修到 pass**

- [ ] **Step 6: Commit**

```bash
git add gateway/app/models/ask.py gateway/app/services/route_decision.py gateway/app/services/qa_tasks.py fastQA/app/services/request_adapter.py patent/server/schemas/request_models.py gateway/tests/test_ask_models.py gateway/tests/test_route_decision.py gateway/tests/test_task_api.py gateway/tests/test_refresh_survivable_task_e2e.py fastQA/tests/test_request_adapter.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat(gateway): add canonical kg request contract"
```

### Task 8: fastQA 接入知识图谱问答

**Files:**
- Create: `fastQA/app/modules/kg_qa/client.py`
- Create: `fastQA/app/modules/kg_qa/service.py`
- Modify: `fastQA/app/modules/qa_kb/service.py`
- Modify: `fastQA/app/services/file_routes.py`
- Modify: `fastQA/app/core/config.py`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify if example contract is maintained: `resource/config/services/fastQA/config.env.example`
- Test: `fastQA/tests/test_env_loader.py`
- Test: `fastQA/tests/test_qa_kb_service.py`
- Test: `fastQA/tests/test_file_routes_tabular_kb.py`
- Test: `fastQA/tests/test_qa_routes_file_modes.py`
- Test: `fastQA/tests/test_file_routes_materialization.py`
- Create: `fastQA/tests/test_kg_qa_service.py`

**Testing Requirement:**
- 不能依赖真实网络；单元测试必须 stub KG client。
- Required commands:
  - `cd fastQA && pytest tests/test_env_loader.py tests/test_qa_kb_service.py tests/test_file_routes_tabular_kb.py tests/test_qa_routes_file_modes.py tests/test_file_routes_materialization.py tests/test_kg_qa_service.py -q`

- [ ] **Step 1: 写红灯测试，锁死 fastQA KG 融合与降级语义**

覆盖：
- KG 检索成功时 evidence 并入结果
- KG timeout / 5xx / contract invalid 时不让 ask 整体失败
- mixed/file route 下 KG 只在允许的 source_scope 中补充，不篡改文件语义

- [ ] **Step 2: 跑红灯测试并确认失败**

- [ ] **Step 3: 最小实现 KG client / service / config 读取**

实现要求：
- config keys 使用前文约定的 `QA_KG_*`
- client 层负责 HTTP contract 与 timeout
- service 层负责 normalize / topk / fallback / metadata `kg_used`

- [ ] **Step 4: 把 KG 融合接入 qa_kb 与 file routes**

实现要求：
- `kb_qa` 与允许 KG 的 file/mixed route 都能使用 KG 证据
- route metadata 保留 evidence 来源
- 失败时只降级，不 silent success

- [ ] **Step 5: 重跑目标测试验证成功与失败路径**

- [ ] **Step 6: 发起 review，修到 pass**

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/modules/kg_qa/client.py fastQA/app/modules/kg_qa/service.py fastQA/app/modules/qa_kb/service.py fastQA/app/services/file_routes.py fastQA/app/core/config.py resource/config/services/fastQA/config.shared.env resource/config/services/fastQA/config.env.example fastQA/tests/test_env_loader.py fastQA/tests/test_qa_kb_service.py fastQA/tests/test_file_routes_tabular_kb.py fastQA/tests/test_qa_routes_file_modes.py fastQA/tests/test_file_routes_materialization.py fastQA/tests/test_kg_qa_service.py
git commit -m "feat(fastqa): integrate kg evidence with safe fallback"
```

### Task 9: patentQA 接入知识图谱问答

**Files:**
- Create: `patent/server/patent/kg_client.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/stages/synthesis.py`
- Modify: `patent/config.shared.env.example`
- Modify: `resource/config/services/patent/config.shared.env`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/test_patent_retrieval_service.py`
- Test: `patent/tests/test_patent_stage4_synthesis.py`
- Test: `patent/tests/test_patent_executor.py`

**Testing Requirement:**
- Required commands:
  - `cd patent && PYTHONPATH=. pytest tests/test_patent_kb_service.py tests/test_patent_retrieval_service.py tests/test_patent_stage4_synthesis.py tests/test_patent_executor.py -q`
- 必须覆盖：
  - KG 命中时 evidence 融入 retrieval / synthesis
  - KG backend 不可用时安全降级
  - 白名单引用 / original links / KG evidence 不冲突

- [ ] **Step 1: 写红灯测试，锁死 patent KG 融合契约**

- [ ] **Step 2: 跑红灯测试并确认失败**

- [ ] **Step 3: 最小实现 patent KG client 与 retrieval 层融合**

实现要求：
- config keys 使用 `PATENT_KG_*`
- retrieval metadata 明确区分 patent retrieval evidence 与 KG evidence
- 不能让 KG 结果绕过专利引用白名单约束

- [ ] **Step 4: 在 synthesis 层合入 KG 证据并保持引用安全**

实现要求：
- KG 证据可以补充 comparison / limitation
- 但不能伪装成 patent original link
- final answer metadata 要标识 `kg_used`

- [ ] **Step 5: 重跑目标测试验证成功与降级路径**

- [ ] **Step 6: 发起 review，修到 pass**

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/kg_client.py patent/server/patent/kb_service.py patent/server/patent/stages/retrieval.py patent/server/patent/stages/synthesis.py patent/config.shared.env.example resource/config/services/patent/config.shared.env patent/tests/test_patent_kb_service.py patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_stage4_synthesis.py patent/tests/test_patent_executor.py
git commit -m "feat(patent): integrate kg evidence into retrieval and synthesis"
```

### Task 10: 前端接入 KG 问答开关与展示

**Files:**
- Modify: `frontend-vue/src/utils/chatRequestContext.js`
- Modify: `frontend-vue/src/features/chat/composables/useAskPreferences.js`
- Modify: `frontend-vue/src/features/controls/components/ControlsPanel.vue`
- Modify: `frontend-vue/src/views/Home.vue`
- Test: `frontend-vue/src/utils/chatRequestContext.test.js`
- Test: `frontend-vue/src/views/Home.structure.test.js`
- Create: `frontend-vue/src/features/chat/composables/useAskPreferences.test.js`

**Testing Requirement:**
- Required commands:
  - `cd frontend-vue && npm test -- src/utils/chatRequestContext.test.js src/views/Home.structure.test.js src/features/chat/composables/useAskPreferences.test.js`
  - `cd frontend-vue && npm run build`
- 必须锁住：
  - payload 含 `kg_enabled` / `kg_mode`
  - UI 开关回显
  - 刷新后状态持久化
  - fast / patent 两模式都能工作

- [ ] **Step 1: 写红灯测试，锁死 KG 开关与请求同步**

- [ ] **Step 2: 跑红灯测试并确认失败**

- [ ] **Step 3: 最小实现前端开关、请求透传、状态持久化**

实现要求：
- `kg_enabled=false` 时 UI 不发送脏 `kg_mode`
- 模式切换不破坏现有 requested_mode / route 流程
- Home 与 ControlsPanel 对同一 preference source of truth 读写一致

- [ ] **Step 4: 重跑测试 + build**

- [ ] **Step 5: 发起 review，修到 pass**

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/utils/chatRequestContext.js frontend-vue/src/features/chat/composables/useAskPreferences.js frontend-vue/src/features/controls/components/ControlsPanel.vue frontend-vue/src/views/Home.vue frontend-vue/src/utils/chatRequestContext.test.js frontend-vue/src/views/Home.structure.test.js frontend-vue/src/features/chat/composables/useAskPreferences.test.js
git commit -m "feat(frontend): add kg ask preference controls"
```

### Task 11: 联调、回归与发布前验收（Release Gate）

**Task Type:** 验证-only；不走红灯测试 -> 实现 -> commit 流程。

**Files:**
- Modify only if real integration gaps force it: `scripts/start_all.sh`, `scripts/status_all.sh`, `scripts/stop_all.sh`
- Verification evidence only for all other services

**Prerequisites:**
- Redis 可用
- gateway / patent / fastQA 可启动
- 如果需要真实 KG 验证，KG backend 可访问；若不可访问，只能把真实 KG 互通标为阻塞，不能把 Task 8/9 假装验过

**Required verification matrix:**
- `bash scripts/stop_all.sh && bash scripts/start_all.sh && bash scripts/status_all.sh`
- `cd gateway && pytest tests/test_task_api.py tests/test_execution_event_relay.py tests/test_refresh_survivable_task_e2e.py tests/test_ask_models.py tests/test_route_decision.py -q`
- `cd patent && PYTHONPATH=. pytest tests/test_chat_persistence.py tests/test_conversation_authority_client.py tests/test_execution_cache.py tests/test_patent_generation_orchestrator.py tests/test_patent_file_routes.py tests/test_patent_pdf_contract.py tests/test_patent_retrieval_service.py tests/test_patent_kb_service.py tests/test_patent_stage4_synthesis.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -q`
- `cd fastQA && pytest tests/test_env_loader.py tests/test_request_adapter.py tests/test_qa_kb_service.py tests/test_file_routes_tabular_kb.py tests/test_qa_routes_file_modes.py tests/test_file_routes_materialization.py tests/test_kg_qa_service.py -q`
- `cd frontend-vue && npm test -- src/utils/recoverableTaskController.test.js src/utils/streamingRender.test.js src/utils/messageRenderMemo.test.js src/utils/chatRequestContext.test.js src/views/Home.structure.test.js src/features/chat/composables/useAskPreferences.test.js`
- `cd frontend-vue && npm run build`

**Required manual / service-backed scenarios:**
1. Latency Fixture A：记录 `accepted_to_first_step_ms` 与 `accepted_to_first_content_ms`
2. Depth Fixture P1：确认输出满足四类信息块 + 3 条证据点
3. Depth Fixture P2：确认 mixed 输出同时引用文件与 KB 证据
4. Duplicate replay scenario：确认最终 UI 只有一份答案
5. Patent cache hit scenario：第二次相同请求出现 `cache_hit=true`，并在 Redis 中看到对应 key
6. fastQA KG scenario：真实 KG 可用时，metadata 标识 `kg_used=true`
7. patent KG scenario：真实 KG 可用时，metadata 标识 `kg_used=true`
8. KG failure scenario：断开或 mock 失败后 ask 仍成功，只是 `kg_used=false`

- [ ] **Step 1: 跑完整自动化测试矩阵并记录结果**

- [ ] **Step 2: 跑 frontend build 并记录结果**

- [ ] **Step 3: 执行 8 条 manual / service-backed 场景并记录证据**

记录至少包括：
- latency telemetry 数值
- cache hit/miss 证据
- KG used / fallback 证据
- duplicate answer 已消失的页面或日志证据

- [ ] **Step 4: 汇总 release blocking issues 与 remaining risks**

至少区分：
- must-fix before release
- acceptable with note
- blocked by external dependency

- [ ] **Step 5: 发起最终 review，修到 pass**

- [ ] **Step 6: 只有在脚本或文档因此 task 被真实修改时才单独 commit**

---

## Acceptance Checklist (必须全部满足)

- [x] Task 1 telemetry 字段稳定存在，`accepted_at_ms -> first_step/content` 链路可读。
- [ ] Task 2 在 controlled fixture 下满足：`accepted_to_first_step_ms <= 300`，且 Task 11 service-backed 场景满足：`accepted_to_first_step_ms <= 500`、`accepted_to_first_content_ms <= 1500`。
- [x] patent stage1/2/25/3/4 与 file-route 的 Redis 缓存都有真实命中证据；第二次相同请求能看到 `cache_hit` 或等价证据，runtime signature 或 file selection 变化会 miss。
- [ ] frontend 不再出现重复答案块、Raw Markdown 泄露、terminal 后重复 append 或 build 失败。
- [x] patent 文件 / 混合 QA 在固定夹具下满足四类信息块 + 3 条证据点 + 限制/边界说明。
- [ ] gateway / frontend 对流契约理解一致：duplicate upstream seq 不再形成第二份用户可见答案，terminal 后 replay/live 都不再追加 content。
- [ ] `kg_enabled` / `kg_mode` 契约在 gateway、fastQA、patent、frontend 四侧一致。
- [ ] fastQA 与 patentQA 都完成 KG QA 接入，并具备 timeout / 5xx / contract-invalid 的失败降级路径。
- [ ] frontend 的 KG 设置可透传、回显、刷新后保持，且 `npm run build` 通过。
- [ ] Task 11 reviewer pass，且 release blocking issues 已清零或明确记录为外部阻塞。
