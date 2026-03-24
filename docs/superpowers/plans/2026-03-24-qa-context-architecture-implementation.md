# QA Context Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `fastQA`、`highThinkingQA`、`gateway`、`public-service` 在问答上下文架构上对齐到统一原则：最终会话历史、路由状态、检索上下文、执行轨迹四层分离。

**Architecture:** 继续保持 `gateway -> fastQA/highThinkingQA -> public-service authority` 的总体结构，不推翻当前文件/混合 QA 分工。改造重点是：补齐 `fastQA kb_qa` 的上下文消费链、补强 `public-service summary`、把文件状态沉淀成统一 retrieval contract，并通过测试保证 steps/trace 不进入 LLM 历史上下文。

**Tech Stack:** FastAPI, Python, Redis, pytest, conversation authority, SSE, conda `agent` env

---

## Current Status Summary

- 已有：
  - `gateway` route/source_scope/file_selection 决策链
  - `public-service` conversation authority / recent_turns / conversation_state
  - `highThinkingQA` 的 context merge + history budget + rewrite + agent context 传递
- 主要差口：
  - `fastQA kb_qa` 读取了 authority，但普通问答主链没有真正消费多轮历史
  - `public-service summary` 仍接近空壳
  - 文件状态尚未收敛为统一 retrieval contract
  - “steps/timings 不进入 prompt”还缺系统级测试和契约

## File Structure Lock-In

### Authority / Shared Contract
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/modules/conversation/authority_schemas.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`
- Test: `public-service/backend/tests/test_conversation_module.py` 或对应 conversation service tests

### Gateway
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/services/file_context_resolver.py`
- Test: `gateway/tests/test_qa_routes.py`
- Test: `gateway/tests/test_route_decision.py`

### fastQA
- Modify: `fastQA/app/routers/qa.py`
- Create or Modify: `fastQA/app/services/conversation_context_builder.py`
- Modify: `fastQA/app/services/request_adapter.py`
- Modify: `fastQA/app/modules/qa_kb/models.py`
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Modify: `fastQA/app/modules/qa_kb/service.py`
- Add tests under: `fastQA/tests/`

### highThinkingQA
- Modify: `highThinkingQA/server/services/conversation_context_service.py`
- Modify: `highThinkingQA/server/services/ask_service.py`
- Test: `highThinkingQA/tests/test_ask_service_executor.py`
- Test: `highThinkingQA/tests/test_conversation_context_service.py` if absent create it

### Docs
- Update: `docs/audit/2026-03-24-context-architecture-comparison.md`
- Update: `docs/superpowers/specs/2026-03-24-qa-context-architecture-design.md`
- Update: `docs/superpowers/plans/2026-03-24-qa-context-architecture-implementation.md`

---

### Task 1: Freeze Authority Context Contract

**Files:**
- Modify: `public-service/backend/app/modules/conversation/authority_schemas.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`

- [x] Step 1: 写 authority context contract 的测试用例
  重点断言：
  - `recent_turns` 只包含最终消息
  - `conversation_state` 只包含 route/file focus 状态
  - `summary` 结构稳定但可为空

- [x] Step 2: 运行定向测试，确认当前失败点

- [x] Step 3: 在 authority schema 中明确字段语义注释/验证
  - `recent_turns`
  - `summary`
  - `conversation_state`

- [x] Step 4: 在 conversation service 中确保 snapshot builder 不把 `steps/timings/trace/debug` 混入 `recent_turns`

- [x] Step 5: 运行定向测试，确认通过

- [ ] Step 6: Commit
  `git commit -m "refactor: freeze authority context contract"`

### Task 2: Add Minimal Authority Summary

**Files:**
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`
- Test: `public-service/backend/tests/test_conversation_module.py`

- [x] Step 1: 写 summary 行为测试
  覆盖：
  - 有 recent turns 时生成非空 `short_summary` 或等价最小摘要
  - 无 recent turns 时保持空结构

- [x] Step 2: 运行测试确认失败

- [x] Step 3: 实现最小 summary 逻辑
  第一版只做轻量摘要，不做复杂记忆抽取

- [x] Step 4: 确保 summary 不包含执行轨迹字段

- [x] Step 5: 运行测试确认通过

- [ ] Step 6: Commit
  `git commit -m "feat: add minimal authority conversation summary"`

### Task 3: Build Shared fastQA Conversation Context Builder

**Files:**
- Create: `fastQA/app/services/conversation_context_builder.py`
- Modify: `fastQA/app/routers/qa.py`
- Modify: `fastQA/app/services/request_adapter.py`
- Test: `fastQA/tests/test_conversation_context_builder.py`

- [x] Step 1: 写 fastQA context builder failing tests
  覆盖：
  - authority history + request history overlap merge
  - history budget 裁剪
  - summary / conversation_state 透传
  - `selected_file_ids / execution_files / source_scope` 进入标准结构

- [x] Step 2: 运行测试确认失败

- [x] Step 3: 实现 `conversation_context_builder.py`
  输出统一结构：
  - `recent_turns_for_llm`
  - `summary_for_llm`
  - `conversation_state`
  - `source_selection`

- [x] Step 4: 在 `fastQA/app/routers/qa.py` 中接入 builder，而不是零散把 snapshot 塞进 `options`

- [x] Step 5: 运行测试确认通过

- [ ] Step 6: Commit
  `git commit -m "refactor: add fastqa conversation context builder"`

### Task 4: Thread Conversation Context into fastQA kb_qa

**Files:**
- Modify: `fastQA/app/modules/qa_kb/models.py`
- Modify: `fastQA/app/modules/qa_kb/service.py`
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Test: `fastQA/tests/test_qa_generation_orchestrator.py`
- Test: `fastQA/tests/test_qa_kb_context_usage.py`

- [x] Step 1: 写 `fastQA kb_qa` 真正消费会话上下文的 failing tests
  覆盖：
  - recent turns 能进入普通 QA 主链
  - summary 可选进入 rewrite/plan 阶段
  - 纯 steps/timings 不会进入 LLM payload

- [x] Step 2: 运行测试确认失败

- [x] Step 3: 扩展 `QaKbRequest` 增加 conversation context 字段

- [x] Step 4: 在 orchestrator / stage1 输入处接入 recent turns / summary
  如果采用 rewrite/condense，则在 stage1 前插入轻量 rewrite；如果暂不引入 rewrite，则至少要把 `recent_turns_for_llm` 进入 pre-answer/planning prompt

- [x] Step 5: 跑定向测试，确认多轮上下文真正生效

- [ ] Step 6: Commit
  `git commit -m "feat: use conversation context in fastqa kb qa"`

### Task 5: Introduce Retrieval Scope Contract for fastQA File Routes

**Files:**
- Modify: `fastQA/app/routers/qa.py`
- Modify: `fastQA/app/services/file_routes.py`
- Modify: `fastQA/app/services/request_adapter.py`
- Test: `fastQA/tests/test_request_adapter.py`
- Test: `fastQA/tests/test_file_routes_materialization.py`
- Test: `fastQA/tests/test_file_context_service.py`

- [ ] Step 1: 写 retrieval scope contract 测试
  覆盖：
  - `selected_file_ids -> source_selection`
  - `source_scope` 合法性
  - `last_focus_file_ids` fallback
  - `execution_files` 与 route 的一致性

- [x] Step 2: 运行测试确认失败

- [ ] Step 3: 引入统一的 `source_selection` / `retrieval_scope` 结构
  让文件 route 都消费同一种 contract，而不是各处散落读字段

- [ ] Step 4: 在 file route 中统一把文件状态转换为 loader/retriever scope

- [x] Step 5: 运行测试确认通过

- [ ] Step 6: Commit
  `git commit -m "refactor: standardize fastqa retrieval scope contract"`

### Task 6: Tighten highThinkingQA Context Boundary

**Files:**
- Modify: `highThinkingQA/server/services/conversation_context_service.py`
- Modify: `highThinkingQA/server/services/ask_service.py`
- Test: `highThinkingQA/tests/test_ask_service_executor.py`
- Create or Modify: `highThinkingQA/tests/test_conversation_context_service.py`

- [x] Step 1: 写 highThinking context boundary tests
  覆盖：
  - `recent_turns` 仅包含最终 user/assistant
  - summary 可进入 rewrite
  - `steps/timings/file_selection` 不进入 `conversation_context` 主体

- [x] Step 2: 运行测试确认失败

- [x] Step 3: 调整 context builder / ask service，使 `conversation_context` 字段职责更清晰

- [x] Step 4: 运行测试确认通过

- [ ] Step 5: Commit
  `git commit -m "refactor: tighten highthinking context boundary"`

### Task 7: Clarify Gateway Input/Output Context Contract

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/app/services/file_context_resolver.py`
- Modify: `gateway/app/services/route_decision.py`
- Test: `gateway/tests/test_qa_routes.py`
- Test: `gateway/tests/test_route_decision.py`

- [x] Step 1: 写 gateway contract tests
  覆盖：
  - `chat_history` 原样作为会话冗余输入下传
  - `pdf_context` 只转成 route/source_scope/file selection，不生成 prompt 文本
  - 文件/混合请求仍强制落 `fast`

- [x] Step 2: 运行测试确认失败

- [x] Step 3: 收敛 gateway 输出字段注释与结构，减少语义歧义

- [x] Step 4: 运行测试确认通过

- [ ] Step 5: Commit
  `git commit -m "refactor: clarify gateway context contract"`

### Task 8: Add Prompt-Boundary Regression Tests

**Files:**
- Create: `fastQA/tests/test_prompt_boundary.py`
- Create: `highThinkingQA/tests/test_prompt_boundary.py`
- Possibly Modify: shared test helpers under both services

- [ ] Step 1: 写跨服务 prompt-boundary tests
  核心断言：
  - `steps`
  - `timings`
  - `trace_id`
  - `file_selection`
  - `source_usage`
  不会被当作 `recent_turns_for_llm` 直接喂给模型

- [ ] Step 2: 运行失败测试

- [ ] Step 3: 补足实现缺口

- [x] Step 4: 运行测试确认通过

- [ ] Step 5: Commit
  `git commit -m "test: guard prompt context boundaries"`

### Task 9: End-to-End Mixed Conversation Coverage

**Files:**
- Create or Modify: `gateway/tests/test_mixed_conversation_context.py`
- Create or Modify: integration tests under `public-service/backend/tests/` or top-level integration suite

- [x] Step 1: 写混合会话 e2e 测试场景
  场景至少包括：
  - same conversation: `thinking -> fast kb -> hybrid -> thinking`
  - authority snapshot 对两条链路均可读
  - `last_turn_route` / `last_focus_file_ids` 行为符合预期

- [x] Step 2: 运行测试确认失败

- [x] Step 3: 修正 contract / integration 细节

- [x] Step 4: 运行测试确认通过

- [ ] Step 5: Commit
  `git commit -m "test: cover mixed conversation context flow"`

### Task 10: Documentation and Rollout Notes

**Files:**
- Modify: `docs/audit/2026-03-24-context-architecture-comparison.md`
- Modify: `docs/superpowers/specs/2026-03-24-qa-context-architecture-design.md`
- Modify: `docs/superpowers/plans/2026-03-24-qa-context-architecture-implementation.md`
- Optionally Update: service README / runbook docs if contract changes are externally visible

- [x] Step 1: 更新文档中的已完成状态
- [x] Step 2: 补充 rollout 注意事项
  - mixed conversation compatibility
  - authority summary fallback
  - file scope compatibility
- [x] Step 3: 写验证记录
- [ ] Step 4: Commit
  `git commit -m "docs: update qa context rollout notes"`

---

## Suggested Execution Order

1. Task 1: Freeze Authority Context Contract
2. Task 2: Add Minimal Authority Summary
3. Task 3: Build Shared fastQA Conversation Context Builder
4. Task 4: Thread Conversation Context into fastQA kb_qa
5. Task 5: Introduce Retrieval Scope Contract for fastQA File Routes
6. Task 6: Tighten highThinkingQA Context Boundary
7. Task 7: Clarify Gateway Input/Output Context Contract
8. Task 8: Add Prompt-Boundary Regression Tests
9. Task 9: End-to-End Mixed Conversation Coverage
10. Task 10: Documentation and Rollout Notes

## Verification Commands

### Public-Service
- `conda run --no-capture-output -n agent pytest public-service/backend/tests/test_conversation_authority_api.py -q`

### Gateway
- `conda run --no-capture-output -n agent pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_mixed_conversation_context.py -q`

### fastQA
- `conda run --no-capture-output -n agent pytest fastQA/tests/test_conversation_context_builder.py fastQA/tests/test_qa_kb_context_usage.py fastQA/tests/test_request_adapter.py fastQA/tests/test_file_routes_materialization.py -q`

### highThinkingQA
- `conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_conversation_context_service.py highThinkingQA/tests/test_prompt_boundary.py highThinkingQA/tests/test_ask_service_executor.py -q`

### Cross-Service
- `conda run --no-capture-output -n agent pytest gateway/tests/test_mixed_conversation_context.py -q`

## 2026-03-24 Implementation Update

- `gateway` 已补齐 `last_focus_file_ids` -> `last_focus_ids` alias，并用测试锁定：`chat_history` 原样下传、`pdf_context` 不下传上游、clarification 在 gateway 直接短路返回 SSE/JSON。
- `gateway/tests/test_mixed_conversation_context.py` 已覆盖最小 mixed conversation 场景：`thinking -> mixed(pdf+kb) -> file follow-up`，验证 `last_focus_file_ids` 行为。
- `highThinkingQA` 已在 `conversation_context_service` 与 `ask_service` 执行边界统一清洗 `recent_turns/summary`，确保 `steps/timings/file_selection/source_usage/trace_id` 不进入 rewrite 或 agent prompt context。
- 最新验证记录：
  - `conda run --no-capture-output -n agent pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_mixed_conversation_context.py -q` -> `48 passed`
  - `conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_conversation_context_service.py highThinkingQA/tests/test_prompt_boundary.py highThinkingQA/tests/test_ask_service_executor.py -q` -> `39 passed`

## Risk Notes

### Risk 1: fastQA 引入多轮上下文后 prompt 行为变化明显
- 对策：先把 conversation context 接入为最小增量，优先只影响 rewrite / planning 层

### Risk 2: authority summary 一旦生成不稳定，可能误导两条 QA 链路
- 对策：summary 采用渐进 rollout；初期允许服务侧忽略 summary

### Risk 3: 文件状态 contract 改动影响现有文件/混合问答
- 对策：保持外部字段兼容，新增统一内部结构而不是立刻删老字段

### Risk 4: mixed conversation 跨 mode 历史共享导致 prompt 污染
- 对策：通过 prompt-boundary tests 保证只共享最终消息，不共享执行轨迹

## Definition of Done

达到以下条件才算完成：

1. `fastQA kb_qa` 真正消费 authority recent turns / summary
2. `highThinkingQA` context contract 与设计文档一致
3. `public-service` 输出清晰的 `recent_turns / summary / conversation_state`
4. 文件状态有统一 retrieval contract
5. `steps/timings/trace` 不会进入 LLM 历史上下文
6. mixed conversation e2e 测试通过
7. 文档与测试全部更新完毕
