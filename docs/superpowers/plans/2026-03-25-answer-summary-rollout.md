# Answer Summary Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `fastQA` 与 `highThinkingQA` 建立可灰度、可验证、不中断流式体验的答案总结块能力，并先完成方案 A 实验所需的协议、埋点与最小实现闭环。

**Architecture:** 第一阶段不直接改前端结构化协议，先在后端各自主链中引入受开关控制的“总结块生成”能力，并统一日志、实验开关和回归测试。`fastQA` 先在现有 generation pipeline / done 收尾边界内接入，`highThinkingQA` 在 draft/final 收尾链路内接入，但都避免改变现有主正文的首 token 时序。

**Tech Stack:** FastAPI, Python, Vue 3, SSE streaming, pytest

---

## Current Status

- [x] `Task 1` 当前输出契约审计已完成
- [x] 当前协议已收敛到“总结块属于最终 markdown 正文，并进入 `done.final_answer`”
- [x] 失败测试、实验开关与最小实现已接入

参考文档：
- [当前输出契约审计](/home/cqy/worktrees/highThinking/docs/audit/2026-03-25-answer-summary-current-contract.md)
- [P4-1 最新 spec](/home/cqy/worktrees/highThinking/docs/audit/2026-03-25-p4-answer-summary-spec.md)

---
### Task 1: 盘清两条链路当前答案输出契约

**Files:**
- Read: `fastQA/app/routers/qa.py`
- Read: `fastQA/app/modules/qa_kb/streaming.py`
- Read: `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
- Read: `highThinkingQA/server/routes/ask.py`
- Read: `highThinkingQA/server/services/answer_stream_service.py`
- Read: `frontend-vue/src/views/Home.vue`
- Create: `docs/audit/2026-03-25-answer-summary-current-contract.md`

- [x] **Step 1: 记录 `fastQA` 当前流式阶段与 done 字段**

输出文档必须写清：
- 正文 token 从哪里开始流
- `done.final_answer` 在什么时点覆盖/固化前端展示
- 哪些字段是前端最终 markdown 渲染的真实来源

- [x] **Step 2: 记录 `highThinkingQA` 当前草稿/校验/最终答案边界**

输出文档必须写清：
- draft 与 final 的时序关系
- 引用检查是否会影响最终答案固化
- 哪个阶段最适合插入总结块

- [x] **Step 3: 记录前端最终消息渲染切换点**

输出文档必须写清：
- 流式中 markdown 如何渲染
- 完成后是否会重新以 `final_answer` 覆盖
- 这一步是否会再次触发格式退化风险

### Task 2: 先写失败测试，锁定“总结块实验开关 + 输出契约”

**Files:**
- Modify: `fastQA/tests/test_qa_placeholder.py`
- Modify: `highThinkingQA/tests/test_prompt_boundary.py`
- Create: `frontend-vue/src/utils/answerSummary.test.js`

- [x] **Step 1: 为 `fastQA` 写失败测试**

新增测试验证：
- 当 `ANSWER_SUMMARY_EXPERIMENT=1` 时，最终答案包含稳定总结标题
- 当开关关闭时，不强制要求该块出现
- 总结块位于正文末尾而不是开头

- [x] **Step 2: 为 `highThinkingQA` 写失败测试**

新增测试验证：
- 总结块只在 final answer 语义完成后出现
- 不应破坏现有引用检查阶段的 done 行为

- [x] **Step 3: 为前端写失败测试**

新增测试验证：
- 流式过程中追加的总结块仍按 markdown 正常渲染
- 消息完成后不会因 `final_answer` 覆盖导致总结块格式退化

### Task 3: 为实验能力加统一配置与埋点

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `highThinkingQA/server/config.py`
- Modify: `fastQA/app/modules/qa_kb/streaming.py`
- Modify: `highThinkingQA/server/services/answer_stream_service.py`
- Possibly Modify: `resource/config/services/fastQA/*.env*`
- Possibly Modify: `resource/config/services/highThinkingQA/*.env*`

- [x] **Step 1: 增加统一实验开关**

要求：
- `fastQA` 与 `highThinkingQA` 使用同名或明确对齐命名的环境变量
- 默认关闭
- 日志能明确打印当前请求是否命中实验开关

- [x] **Step 2: 增加总结块埋点**

日志至少包含：
- `summary_enabled`
- `summary_generated`
- `summary_length`
- `summary_has_citation`
- 生成耗时

- [x] **Step 3: 明确失败降级**

如果总结块生成失败：
- 主体答案不能失败
- 只记录日志
- SSE 完成语义保持不变

### Task 4: 先做方案 A 最小实现

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
- Modify: `fastQA/app/modules/qa_kb/streaming.py`
- Modify: `highThinkingQA/server/services/answer_generation_service.py`
- Modify: `highThinkingQA/server/services/answer_stream_service.py`
- Possibly Modify: `highThinkingQA/server/prompts/*.py`

- [x] **Step 1: `fastQA` 引入末尾总结 prompt 约束**

要求：
- 只在实验开关打开时生效
- 不改变现有引用生成主链
- 总结块标题固定

- [x] **Step 2: `highThinkingQA` 引入末尾总结 prompt 约束**

要求：
- 放在 final answer 生成侧
- 不干扰 draft/checker 的主逻辑
- 若 checker 仍会重写最终文本，要验证总结块不会被吃掉

- [x] **Step 3: 限制总结块长度**

要求：
- 2 到 4 句或等价上限
- 明确禁止再展开成二次长答案

### Task 5: 跑定向验证并记录实验结论

**Files:**
- Test: `fastQA/tests/test_qa_placeholder.py`
- Test: `highThinkingQA/tests/test_prompt_boundary.py`
- Test: `frontend-vue/src/utils/answerSummary.test.js`
- Modify: `docs/audit/2026-03-25-answer-summary-current-contract.md`

- [x] **Step 1: 跑后端定向测试**

Run: `conda run -n agent pytest fastQA/tests/test_qa_placeholder.py highThinkingQA/tests/test_prompt_boundary.py -q`
Expected: PASS

- [x] **Step 2: 跑前端定向测试或最小构建验证**

Run: `cd frontend-vue && npm run build`
Expected: exit 0

- [x] **Step 3: 记录实验结论**

结论必须回答：
- 总结块是否稳定出现
- 是否破坏流式体验
- 是否破坏 markdown / citation 渲染
- 是否值得进入方案 B

### Task 6: 提交总结块实验第一阶段

**Files:**
- Modify: `docs/audit/2026-03-25-answer-summary-current-contract.md`
- Modify: `fastQA/...`
- Modify: `highThinkingQA/...`
- Modify: `frontend-vue/...`
- Modify: `docs/superpowers/plans/2026-03-25-answer-summary-rollout.md`

- [ ] **Step 1: 检查 diff 只包含总结块实验相关文件**

Run: `git diff -- docs/audit/2026-03-25-answer-summary-current-contract.md fastQA highThinkingQA frontend-vue docs/superpowers/plans/2026-03-25-answer-summary-rollout.md`
Expected: only answer-summary rollout files changed

- [ ] **Step 2: 提交**

```bash
git add docs/audit/2026-03-25-answer-summary-current-contract.md fastQA highThinkingQA frontend-vue docs/superpowers/plans/2026-03-25-answer-summary-rollout.md
git commit -m "feat: add answer summary experiment hooks"
```
