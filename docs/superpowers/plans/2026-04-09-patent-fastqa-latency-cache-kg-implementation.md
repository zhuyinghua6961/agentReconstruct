# Patent/FastQA 延迟、缓存、渲染缺陷与知识图谱接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 2026-04-09 这轮迭代里，真实修复 patent 普通 QA 首 chunk 慢、前端重复渲染/Markdown 泄露、patent 文件/混合回答过短问题，并把 patentQA 与 fastQA 一并接入知识图谱问答能力。

**Architecture:** 方案分三条主线并行推进。第一条是“时延与缓存主线”，从 gateway 到 patent 端建立可观测延迟链路并补齐各阶段 Redis 缓存。第二条是“渲染正确性与答案质量主线”，收敛前端流式渲染单一数据源，避免重复 append/raw markdown 泄露，同时把 patent 文件与混合回答策略对齐 fastQA。第三条是“知识图谱主线”，先统一 gateway/请求契约，再分别在 fastQA 与 patentQA 接入 KG 检索与证据合成。

**Tech Stack:** Vue 3 + Pinia + Vite, FastAPI, Python, Redis, pytest, Node test runner, gateway task/event pipeline

---

## Scope Inputs (2026-04-09)

1. 排查并修复 patent 普通 `kb_qa` 从收到问题到首 chunk 过慢问题。
2. patentQA 各阶段补齐 Redis 缓存能力（不是空壳，需真实命中与失效）。
3. 修复 bug 报告中的前端缺陷：
   - 回答末尾重复内容
   - 原始 Markdown 符号（`##`/`###`）泄露
   - 样式异常与内容截断
4. patent 入口的文件 QA/混合 QA 输出内容太少，需要和 fastQA 同级别输出深度。
5. fastQA 与 patentQA 都接入知识图谱问答（KG QA）。

## Hard Constraints

1. 不能做空壳子。每个需求必须在真实主链路生效，不允许仅增加未调用 helper。
2. 每个 task 必须先补红灯测试，再做最小实现，再转绿。
3. 每个 task 完成后必须发起 code review，按结论修到 pass 后再进入下一个 task。
4. 需要跑测试、启动服务、联调、压测时一律提权执行；不能在沙箱里假验证。
5. 修复 bug #3 时必须确保“仅渲染解析后最终内容”，不能再出现 raw markdown 追加。

## File Map

### Latency & Admission Chain

- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/execution_event_relay.py`
- Modify: `gateway/app/routers/tasks.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Modify: `patent/server/patent/orchestrators/generation.py`

### Patent Stage Cache

- Modify: `patent/server/services/execution_cache.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/config.shared.env.example`
- Modify: `resource/config/services/patent/config.shared.env`

### Frontend Rendering Defect (Bug #3)

- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `frontend-vue/src/utils/streamingRender.js`
- Modify: `frontend-vue/src/utils/messageRenderMemo.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify if needed: `frontend-vue/src/features/chat/components/ChatPanel.vue`

### Patent File/Hybrid Answer Depth Alignment

- Modify: `patent/server/patent/pdf_contract.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server/patent/file_routes.py`

### KG QA Contract & Integration

- Modify: `gateway/app/models/ask.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `fastQA/app/services/request_adapter.py`
- Modify: `patent/server/schemas/request_models.py`
- Modify: `patent/server/patent/kb_service.py`
- Create: `fastQA/app/modules/kg_qa/client.py`
- Create: `fastQA/app/modules/kg_qa/service.py`
- Modify: `fastQA/app/modules/qa_kb/service.py`
- Modify: `fastQA/app/services/file_routes.py`
- Create: `patent/server/patent/kg_client.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/stages/synthesis.py`
- Modify: `frontend-vue/src/utils/chatRequestContext.js`
- Modify: `frontend-vue/src/features/chat/composables/useAskPreferences.js`
- Modify: `frontend-vue/src/views/Home.vue`

### Tests

- Modify: `gateway/tests/test_task_api.py`
- Modify: `gateway/tests/test_execution_event_relay.py`
- Modify: `gateway/tests/test_refresh_survivable_task_e2e.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/test_chat_persistence.py`
- Modify: `patent/tests/test_execution_cache.py`
- Modify: `patent/tests/test_patent_generation_orchestrator.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `fastQA/tests/test_request_adapter.py`
- Modify: `fastQA/tests/test_qa_kb_service.py`
- Modify: `fastQA/tests/test_file_routes_tabular_kb.py`
- Create: `fastQA/tests/test_kg_qa_service.py`
- Modify: `frontend-vue/src/utils/recoverableTaskController.test.js`
- Modify: `frontend-vue/src/utils/streamingRender.test.js`
- Modify: `frontend-vue/src/utils/messageRenderMemo.test.js`
- Modify: `frontend-vue/src/views/Home.structure.test.js`

## Task Execution Rules

1. 每个 task 都执行：红灯测试 -> 最小实现 -> 目标测试转绿 -> code review -> 修复到 pass -> commit。
2. 每个 task 的测试命令都需要提权执行；若提权不可用，立即停止并报告阻塞点。
3. 若发现跨 task 依赖冲突，先完成依赖 task 并更新本计划。

---

### Task 1: 建立 patent 首 chunk 时延可观测基线

**Files:**
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Test: `gateway/tests/test_task_api.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

**Testing Requirement:**
- 必须新增自动化断言，验证可输出完整时间戳链路：`task accepted -> dispatch start -> patent stream metadata -> first content event`。
- 必跑命令（提权）：
  - `cd gateway && pytest tests/test_task_api.py -q`
  - `cd patent && env PYTHONPATH=/home/cqy/worktrees/highThinking/patent pytest tests/fastapi_contract/test_ask_contract.py -q`

- [ ] **Step 1: 写红灯测试，锁住首 chunk 时延字段缺失**
- [ ] **Step 2: 跑红灯测试并确认失败原因是时延字段未落链路**
- [ ] **Step 3: 在 gateway/patent 主链路注入统一时延埋点与 trace 字段**
- [ ] **Step 4: 重跑目标测试，确认字段齐全且格式稳定**
- [ ] **Step 5: 发起 code review 并按结论修复到 pass**
- [ ] **Step 6: Commit**

### Task 2: 修复 patent 普通 QA 首 chunk 前阻塞卡点

**Files:**
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `gateway/tests/test_refresh_survivable_task_e2e.py`

**Testing Requirement:**
- 必须覆盖“慢上下文/慢 authority 场景下仍能快速看到 metadata/step，不被同步阻塞吞掉首包”。
- 必跑命令（提权）：
  - `cd patent && env PYTHONPATH=/home/cqy/worktrees/highThinking/patent pytest tests/test_chat_persistence.py -q`
  - `cd gateway && pytest tests/test_refresh_survivable_task_e2e.py -q`

- [ ] **Step 1: 写红灯测试，模拟 authority 慢响应导致首包延迟**
- [ ] **Step 2: 跑红灯测试并确认失败**
- [ ] **Step 3: 拆分同步阻塞路径，优先发 metadata/step，再进入重计算阶段**
- [ ] **Step 4: 对慢调用补超时、降级、重试边界，避免卡死**
- [ ] **Step 5: 重跑目标测试并记录延迟改善**
- [ ] **Step 6: 发起 review，修到 pass 后 commit**

### Task 3: patentQA 各阶段 Redis 缓存全覆盖

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

**Testing Requirement:**
- 要求真实命中缓存并返回 `cache_hit` 相关 metadata，不允许只加配置项。
- 必跑命令（提权）：
  - `cd patent && env PYTHONPATH=/home/cqy/worktrees/highThinking/patent pytest tests/test_execution_cache.py tests/test_patent_generation_orchestrator.py tests/test_patent_file_routes.py -q`

- [ ] **Step 1: 写红灯测试，锁住 stage1/2/25/3/4 与文件路由缓存命中契约**
- [ ] **Step 2: 跑红灯测试并确认缓存链路缺口**
- [ ] **Step 3: 补齐 stage4 与 file-route 缓存键、TTL、singleflight**
- [ ] **Step 4: 将 cache hit/miss 注入 metadata 与日志**
- [ ] **Step 5: 重跑目标测试，验证命中、失效、并发一致性**
- [ ] **Step 6: 发起 review，修到 pass 后 commit**

### Task 4: 修复前端“重复内容 + Raw Markdown 泄露 + 截断”缺陷

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/utils/recoverableTaskController.js`
- Modify: `frontend-vue/src/utils/streamingRender.js`
- Modify: `frontend-vue/src/utils/messageRenderMemo.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Test: `frontend-vue/src/utils/recoverableTaskController.test.js`
- Test: `frontend-vue/src/utils/streamingRender.test.js`
- Test: `frontend-vue/src/utils/messageRenderMemo.test.js`
- Test: `frontend-vue/src/views/Home.structure.test.js`

**Testing Requirement:**
- 必须新增行为测试锁住：
  - done 后不重复 append 原始文本
  - `##/###` 不会以 raw text 再次渲染
  - 主区域只保留一份最终答案
- 必跑命令（提权）：
  - `cd frontend-vue && npm test -- src/utils/recoverableTaskController.test.js src/utils/streamingRender.test.js src/utils/messageRenderMemo.test.js src/views/Home.structure.test.js`

- [ ] **Step 1: 写红灯测试，复现“上半渲染+下半raw文本重复”**
- [ ] **Step 2: 跑红灯测试并确认失败**
- [ ] **Step 3: 收敛到单一消息写入源，禁止终态二次 append raw markdown**
- [ ] **Step 4: 修正终态 flush 与 truncation 边界，避免截断残留**
- [ ] **Step 5: 重跑目标测试并做本地交互回归**
- [ ] **Step 6: 发起 review，修到 pass 后 commit**

### Task 5: gateway 事件重放与终态收敛去重加固（配合 Task 4）

**Files:**
- Modify: `gateway/app/services/execution_event_relay.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/routers/tasks.py`
- Test: `gateway/tests/test_execution_event_relay.py`
- Test: `gateway/tests/test_task_api.py`

**Testing Requirement:**
- 必须覆盖重复 seq、终态后重复 content、回放窗口边界。
- 必跑命令（提权）：
  - `cd gateway && pytest tests/test_execution_event_relay.py tests/test_task_api.py -q`

- [ ] **Step 1: 写红灯测试，锁住 terminal 后重复回放问题**
- [ ] **Step 2: 跑红灯测试并确认失败**
- [ ] **Step 3: 在 relay 层增加 seq/terminal 去重与窗口保护**
- [ ] **Step 4: 重跑目标测试验证不再重复输出**
- [ ] **Step 5: 发起 review，修到 pass**
- [ ] **Step 6: Commit**

### Task 6: patent 文件 QA / 混合 QA 输出深度对齐 fastQA（基线定义）

**Files:**
- Modify: `patent/server/patent/pdf_contract.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Test: `patent/tests/test_patent_pdf_contract.py`
- Test: `patent/tests/test_patent_file_routes.py`

**Testing Requirement:**
- 要求新增可重复断言：当证据充足时，输出必须满足最小结构深度（不是只看长度数字）。
- 必跑命令（提权）：
  - `cd patent && env PYTHONPATH=/home/cqy/worktrees/highThinking/patent pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -q`

- [ ] **Step 1: 写红灯测试，锁住“证据充足却输出过短”场景**
- [ ] **Step 2: 跑红灯测试并确认失败**
- [ ] **Step 3: 对齐 fastQA 的结构化输出策略（结论/证据/对比/限制）**
- [ ] **Step 4: 优化 file/hybrid 合成 prompt 与降级策略**
- [ ] **Step 5: 重跑目标测试并确认输出深度改善**
- [ ] **Step 6: 发起 review，修到 pass 后 commit**

### Task 7: 统一 KG QA 请求契约与 gateway 透传

**Files:**
- Modify: `gateway/app/models/ask.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `fastQA/app/services/request_adapter.py`
- Modify: `patent/server/schemas/request_models.py`
- Test: `gateway/tests/test_ask_models.py`
- Test: `fastQA/tests/test_request_adapter.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

**Testing Requirement:**
- 锁住 `kg_enabled/kg_mode` 协议字段，确保 fast/patent 都能稳定接收。
- 必跑命令（提权）：
  - `cd gateway && pytest tests/test_ask_models.py -q`
  - `cd fastQA && pytest tests/test_request_adapter.py -q`
  - `cd patent && env PYTHONPATH=/home/cqy/worktrees/highThinking/patent pytest tests/fastapi_contract/test_ask_contract.py -q`

- [ ] **Step 1: 写红灯测试，定义 KG 协议字段契约**
- [ ] **Step 2: 跑红灯测试并确认失败**
- [ ] **Step 3: 实现 gateway -> fast/patent 的 KG 字段透传**
- [ ] **Step 4: 重跑目标测试并确认契约稳定**
- [ ] **Step 5: 发起 review，修到 pass**
- [ ] **Step 6: Commit**

### Task 8: fastQA 接入知识图谱问答

**Files:**
- Create: `fastQA/app/modules/kg_qa/client.py`
- Create: `fastQA/app/modules/kg_qa/service.py`
- Modify: `fastQA/app/modules/qa_kb/service.py`
- Modify: `fastQA/app/services/file_routes.py`
- Modify: `fastQA/app/core/config.py`
- Test: `fastQA/tests/test_qa_kb_service.py`
- Test: `fastQA/tests/test_file_routes_tabular_kb.py`
- Test: `fastQA/tests/test_kg_qa_service.py`

**Testing Requirement:**
- 必须覆盖：KG 检索成功、KG 检索失败降级、与向量证据合并、引用输出稳定。
- 必跑命令（提权）：
  - `cd fastQA && pytest tests/test_qa_kb_service.py tests/test_file_routes_tabular_kb.py tests/test_kg_qa_service.py -q`

- [ ] **Step 1: 写红灯测试，锁住 fastQA KG 证据并入行为**
- [ ] **Step 2: 跑红灯测试并确认失败**
- [ ] **Step 3: 实现 KG client/service 与 qa_kb 主链路融合**
- [ ] **Step 4: 在 file/hybrid 模式里接入 KG 补充证据**
- [ ] **Step 5: 重跑目标测试并验证降级路径**
- [ ] **Step 6: 发起 review，修到 pass 后 commit**

### Task 9: patentQA 接入知识图谱问答

**Files:**
- Create: `patent/server/patent/kg_client.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/stages/synthesis.py`
- Modify: `patent/config.shared.env.example`
- Modify: `resource/config/services/patent/config.shared.env`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/test_patent_stage4_synthesis.py`
- Test: `patent/tests/test_patent_executor.py`

**Testing Requirement:**
- 必须覆盖：KG 命中合成、KG 不可用降级、专利引用白名单与 KG 证据共存不冲突。
- 必跑命令（提权）：
  - `cd patent && env PYTHONPATH=/home/cqy/worktrees/highThinking/patent pytest tests/test_patent_kb_service.py tests/test_patent_stage4_synthesis.py tests/test_patent_executor.py -q`

- [ ] **Step 1: 写红灯测试，定义 patent KG 证据融合契约**
- [ ] **Step 2: 跑红灯测试并确认失败**
- [ ] **Step 3: 实现 patent KG client 与检索阶段融合**
- [ ] **Step 4: 在 stage4 合成中引入 KG 证据段并保持引用安全**
- [ ] **Step 5: 重跑目标测试并验证降级路径**
- [ ] **Step 6: 发起 review，修到 pass 后 commit**

### Task 10: 前端接入 KG 问答开关与展示

**Files:**
- Modify: `frontend-vue/src/utils/chatRequestContext.js`
- Modify: `frontend-vue/src/features/chat/composables/useAskPreferences.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/features/controls/components/ControlsPanel.vue`
- Test: `frontend-vue/src/utils/chatRequestContext.test.js`
- Test: `frontend-vue/src/views/Home.structure.test.js`

**Testing Requirement:**
- 覆盖 fast/patent 请求 payload 含 KG 字段、UI 开关回显、刷新后状态保持。
- 必跑命令（提权）：
  - `cd frontend-vue && npm test -- src/utils/chatRequestContext.test.js src/views/Home.structure.test.js`

- [ ] **Step 1: 写红灯测试，锁住 KG 请求字段与 UI 状态同步**
- [ ] **Step 2: 跑红灯测试并确认失败**
- [ ] **Step 3: 实现前端 KG 开关、请求透传、状态持久化**
- [ ] **Step 4: 重跑目标测试验证 fast/patent 两模式兼容**
- [ ] **Step 5: 发起 review，修到 pass**
- [ ] **Step 6: Commit**

### Task 11: 联调、回归与发布前验收

**Files:**
- Modify as needed: `scripts/start_all.sh`, `scripts/status_all.sh`, `scripts/stop_all.sh`（仅在联调发现真实缺口时）
- Test sweep only

**Testing Requirement:**
- 必须做提权联调，至少覆盖：
  - patent 普通 QA 首 chunk 延迟
  - bug #3 页面重复渲染与 raw markdown 泄露
  - patent 文件/混合输出深度
  - fast/patent KG 问答
- 必跑命令（提权）：
  - `bash scripts/stop_all.sh && bash scripts/start_all.sh && bash scripts/status_all.sh`
  - `cd gateway && pytest tests/test_task_api.py tests/test_execution_event_relay.py tests/test_refresh_survivable_task_e2e.py -q`
  - `cd patent && env PYTHONPATH=/home/cqy/worktrees/highThinking/patent pytest tests/test_patent_kb_service.py tests/test_patent_file_routes.py tests/test_patent_stage4_synthesis.py tests/fastapi_contract/test_ask_contract.py -q`
  - `cd fastQA && pytest tests/test_qa_kb_service.py tests/test_file_routes_tabular_kb.py tests/test_request_adapter.py -q`
  - `cd frontend-vue && npm test -- src/utils/recoverableTaskController.test.js src/utils/streamingRender.test.js src/utils/messageRenderMemo.test.js src/utils/chatRequestContext.test.js src/views/Home.structure.test.js`

- [ ] **Step 1: 跑完整后端测试矩阵并记录结果**
- [ ] **Step 2: 跑前端关键行为测试矩阵并记录结果**
- [ ] **Step 3: 进行端到端人工回归（4条核心场景）**
- [ ] **Step 4: 汇总风险与剩余已知问题**
- [ ] **Step 5: 发起最终 code review 并修到 pass**
- [ ] **Step 6: 合并前冻结发布说明**

---

## Acceptance Checklist (必须全部满足)

- [ ] patent 普通 QA 首 chunk 延迟有可观测链路且达到目标预算。
- [ ] patentQA 各阶段 Redis 缓存真实命中，不是配置空转。
- [ ] 前端不再出现重复答案块与 raw markdown 泄露。
- [ ] patent 文件/混合 QA 输出深度与 fastQA 对齐。
- [ ] fastQA 与 patentQA 均完成 KG QA 接入，并具备失败降级能力。

