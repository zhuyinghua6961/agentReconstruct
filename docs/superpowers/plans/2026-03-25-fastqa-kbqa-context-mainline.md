# fastQA kb_qa Context Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `fastQA` 普通 `kb_qa` 的 recent turns/summary 不只进入 Stage1 检索规划，还进入 Stage4 最终答案生成主链，形成真正的多轮普通问答闭环。

**Architecture:** 保持当前 `gateway -> fastQA -> generation-driven orchestrator` 结构不变，只扩展 `conversation_context` 在 generation pipeline 中的下游传递范围。第一阶段只做最小闭环：把规范化后的 `recent_turns_for_llm` 与 `summary_for_llm` 从 router/service/orchestrator 继续传到 Stage4 synthesis prompt，并补回归测试验证 Stage1 + Stage4 都能看到上下文，同时保证 trace/steps/timings 等运行态字段不泄漏进 LLM 提示词。

**Tech Stack:** FastAPI, Python, generation-driven RAG pipeline, pytest

---

### Task 1: 盘清当前断点并锁定测试目标

**Files:**
- Modify: `docs/superpowers/plans/2026-03-25-fastqa-kbqa-context-mainline.md`
- Read: `fastQA/app/routers/qa.py`
- Read: `fastQA/app/modules/qa_kb/service.py`
- Read: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Read: `fastQA/app/modules/generation_pipeline/stage1_planning.py`
- Read: `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
- Test: `fastQA/tests/test_qa_kb_context_usage.py`

- [ ] **Step 1: 确认当前真实状态**

结论必须写清：
- Stage1 已消费 `conversation_context`
- Stage4 当前未消费 `conversation_context`
- `P1-1` 的第一刀目标是 “Stage4 接入上下文”，不是重复改 Stage1

- [ ] **Step 2: 记录最小行为目标**

目标行为：
- `QaKbRequest.recent_turns_for_llm` / `summary_for_llm` 经 `normalize_conversation_context()` 后，既传给 Stage1，也传给 Stage4
- Stage4 prompt 能看到 recent turns/summary 的精简文本
- `trace_id` / `steps` / `timings` 等运行态字段不进入 Stage4 prompt

### Task 2: 先写失败测试锁定 Stage4 缺口

**Files:**
- Modify: `fastQA/tests/test_qa_kb_context_usage.py`
- Test: `fastQA/tests/test_qa_kb_context_usage.py`

- [ ] **Step 1: 写失败测试**

新增一个最小测试，验证：
- 运行 `qa_kb_service.iter_answer_events()` 时
- Stage4 收到的 prompt/上下文里应包含：
  - 最近一轮用户问题
  - 最近一轮助手回答
  - `short_summary`
- 但不包含：
  - `trace_id`
  - `steps`
  - `timings`

- [ ] **Step 2: 运行单测确认失败**

Run: `conda run -n agent pytest fastQA/tests/test_qa_kb_context_usage.py -q`
Expected: 新增 Stage4 上下文测试失败，失败原因是当前 Stage4 未接到 `conversation_context`

### Task 3: 做最小实现，把上下文送进 Stage4 synthesis

**Files:**
- Modify: `fastQA/app/modules/qa_kb/models.py`
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
- Possibly Modify: `fastQA/app/modules/qa_kb/service.py`

- [ ] **Step 1: 扩 Stage4 相关函数签名**

把 `conversation_context` 从 orchestrator/facade 继续传到 Stage4 synthesis 函数。

- [ ] **Step 2: 实现 Stage4 prompt 上下文格式化**

在 `synthesis_streaming.py` 增加专用格式化函数：
- 只提取 `summary_for_llm.short_summary/open_threads/memory_facts`
- 只提取 `recent_turns_for_llm` 的 `role/content`
- 不引入 `conversation_state`、`source_selection`、`trace_id`、`steps`、`timings`

- [ ] **Step 3: 把格式化后的上下文插入 Stage4 prompt**

要求：
- 不破坏当前 evidence / deep_answer / references 主结构
- 只作为“会话连续性补充信息”注入
- 文案明确：用于承接上下文，不可覆盖证据事实

### Task 4: 验证并做回归

**Files:**
- Test: `fastQA/tests/test_qa_kb_context_usage.py`
- Test: `fastQA/tests/test_generation_stage1_planning.py`
- Test: `fastQA/tests/test_qa_generation_orchestrator.py`
- Test: `fastQA/tests/test_qa_kb_service_runtime.py`

- [ ] **Step 1: 跑 Stage4 新增测试**

Run: `conda run -n agent pytest fastQA/tests/test_qa_kb_context_usage.py -q`
Expected: PASS

- [ ] **Step 2: 跑既有上下文相关回归**

Run: `conda run -n agent pytest fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_qa_kb_service_runtime.py -q`
Expected: PASS

- [ ] **Step 3: 跑 router/service 相关回归**

Run: `conda run -n agent pytest fastQA/tests/test_qa_placeholder.py -q`
Expected: PASS

### Task 5: 提交第一批 P1-1 改动

**Files:**
- Modify: `fastQA/tests/test_qa_kb_context_usage.py`
- Modify: `fastQA/app/modules/qa_kb/models.py`
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`

- [ ] **Step 1: 检查 git diff 仅包含 P1-1 第一刀**

Run: `git diff -- fastQA/tests/test_qa_kb_context_usage.py fastQA/app/modules/qa_kb/models.py fastQA/app/modules/qa_kb/orchestrators/generation.py fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
Expected: only P1-1 context-mainline files changed

- [ ] **Step 2: 提交**

```bash
git add fastQA/tests/test_qa_kb_context_usage.py fastQA/app/modules/qa_kb/models.py fastQA/app/modules/qa_kb/orchestrators/generation.py fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py fastQA/app/modules/generation_pipeline/synthesis_streaming.py docs/superpowers/plans/2026-03-25-fastqa-kbqa-context-mainline.md
git commit -m "feat: thread kb qa context into stage4 synthesis"
```
