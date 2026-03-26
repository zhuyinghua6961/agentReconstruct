# 2026-03-25 Answer Summary Current Contract Audit

## 1. 范围

本审计只回答 `P4-1 / Task 1` 需要的三个问题：
- `fastQA` 当前流式正文和 `done.final_answer` 的真实来源是什么
- `highThinkingQA` 当前 draft / checker / final 的边界是什么
- 前端在哪个时点用 `final_answer` 覆盖流式内容，以及这会带来什么风险

---

## 2. 总结结论

当前两条链路在前端层面都属于同一种收尾契约：
- 后端先发送 `content` 事件，把正文流式渲染出来
- 结束时再发送一个 `done` 事件，其中带完整 `final_answer`
- 前端在 `done` 到达时，会直接用 `data.final_answer` 覆盖当前消息内容

因此：
- 如果“总结块”只出现在流式正文里，但没有进入最终 `done.final_answer`，它会在完成态被覆盖掉
- 如果“总结块”只在 `done` 后补结构化字段，当前前端不会自动把它渲染进正文
- `P4` 的任何实现都必须把“流式中看到的最终正文”和 `done.final_answer` 对齐，否则一定会出现完成态跳变

---

## 3. gateway 边界

### 3.1 gateway 不改写 ask_stream 正文契约

`gateway` 的 `ask_stream` 代理只是把上游 SSE 直接透传给前端，没有在代理层重组 `content` / `done`。

证据：
- `gateway/app/routers/qa.py:176-220`
- 其中 `_proxy_ask_stream()` 直接 `return StreamingResponse(handle.body_iter(), ...)`

含义：
- `P4` 的正文/总结块契约，不需要优先改 gateway 代理层
- 核心边界在 QA 后端和前端 `done` 覆盖点

### 3.2 gateway 持久化侧把 `done.final_answer` 作为最终助手消息

`gateway` 的旁路持久化 tap 会在收到 `done` 后，把 `summary.assistant_content` 改写为 `payload.final_answer`。

证据：
- `gateway/app/services/conversation_persistence.py:266-298`
- `summary.assistant_content = str(payload.get("final_answer") or summary.assistant_content or "")`

含义：
- 即使流式阶段已经累计了正文，最终持久化仍以 `done.final_answer` 为准
- `P4` 如果做总结块实验，也必须保证持久化拿到的是带总结块的最终答案

---

## 4. fastQA 当前契约

### 4.1 普通 `QaKbExecutionResult` 路径

通用流式 helper `iter_result_events()` 的行为是：
- 先发 `metadata`
- 再把 `result.final_answer` 按块拆成多个 `content`
- 最后发 `done.final_answer = result.final_answer`

证据：
- `fastQA/app/modules/qa_kb/streaming.py:45-90`

这条路径下：
- 流式正文和最终答案来自同一个 `result.final_answer`
- 只要总结块被写进 `result.final_answer`，流式与完成态天然一致

### 4.2 generation-driven `kb_qa` 主链路

`generation` 编排器的 stage4 流式路径是：
- `stage4.stream(...)` 一边吐字符串 chunk，一边累积到 `final_chunks`
- 完成后拿 `final_result["final_answer"]` 作为最终答案；若没有，则回退到 `"".join(final_chunks)`
- 最后发送 `done.final_answer = final_answer`

证据：
- `fastQA/app/modules/qa_kb/orchestrators/generation.py:838-894`

关键含义：
- `fastQA` 主链路里，“流式看见的正文”和“完成态最终答案”并不一定天然相同
- 决定完成态的是 `final_result["final_answer"]`
- 如果未来在 stage4 流式末尾直接追加总结块，但没有同步进 `final_result["final_answer"]`，前端完成时会被覆盖回旧文本

### 4.3 `fastQA` 的 P4 风险点

`fastQA` 的风险不是前端，而是后端自身存在“两份最终文本来源”：
- 一份是流式阶段累计的 `final_chunks`
- 一份是 stage4 返回的 `final_result.final_answer`

因此 `P4-1` 若做实验，必须明确：
- 总结块加在真正的 `final_result.final_answer` 生成侧
- 或者在发 `done` 前保证 `final_answer` 与流式累计文本重新对齐

否则会出现：
- 流式中看见总结块
- 完成后又消失

---

## 5. highThinkingQA 当前契约

### 5.1 流式正文的真实来源

`highThinkingQA` 的 `stream_ask_events()` 中：
- `on_content()` 收到 agent 内部流式原文后，先累计到 `streamed_raw_content`
- 然后用 `_adapt_answer_for_frontend()` 处理“安全前缀”
- 再通过 `_emit_adapted_delta()` 按 delta 发 `content`

证据：
- `highThinkingQA/server/services/ask_service.py:639-667`

含义：
- 前端流式中看到的是“适配后的正文增量”，不是原始 draft 文本的简单直通
- 如果正文尾部存在未闭合引用或 markdown 结构，服务端会等到安全前缀再继续吐内容

### 5.2 `highThinkingQA` 的 done.final_answer 来源

`stream_ask_events()` 收尾时：
- 等 worker 完整结束
- 用 `state.final_answer` 再次执行 `_adapt_answer_for_frontend()`
- 以这个结果作为 `done.final_answer`

证据：
- `highThinkingQA/server/services/ask_service.py:812-835`

含义：
- `highThinkingQA` 的完成态以 `state.final_answer` 为准
- 流式阶段即便已经吐出了大量内容，完成时仍会被完整的 `frontend_answer` 覆盖

### 5.3 draft / checker / final 的边界

`graph.run_agent()` 的后半段边界非常清楚：
- Step 4：综合生成草稿答案，产物是 `state.draft_answer`
- Step 5：Checker-Reviser 循环检查并可能修改 `current_answer`
- 循环结束后才执行 `state.final_answer = current_answer`

证据：
- `highThinkingQA/agent_core/graph.py:573-631`
- `highThinkingQA/agent_core/graph.py:633-799`

这意味着：
- 流式阶段开始输出的是 Step 4 的草稿答案
- 但最终 `done.final_answer` 取的是 Step 5 结束后的 `state.final_answer`
- 也就是“前端流式看到的正文”和“完成态最后固化的正文”天然存在可能差异

### 5.4 `highThinkingQA` 的 P4 风险点

如果把总结块放在错误阶段，会出现不同问题：
- 放在 Step 4 草稿流式阶段：可能被 Step 5 修订后的 `final_answer` 覆盖
- 放在 Step 5 之后但不进入 `state.final_answer`：前端不会展示
- 放在 `done` 的结构化 metadata：当前前端不会把它拼进正文

因此对 `highThinkingQA` 来说，最安全的总结块插入边界是：
- 进入最终 `state.final_answer` 的生成/收尾路径
- 并且保证它经过与正文同样的 `_adapt_answer_for_frontend()` 适配

---

## 6. 前端当前契约

### 6.1 流式阶段

前端收到 `content` 时：
- 先把 `data.content` 累加到 `pendingStreamContent`
- 再定时 flush 到当前消息内容

证据：
- `frontend-vue/src/views/Home.vue:1121-1123`

### 6.2 完成阶段

前端收到 `done` 时：
- 先 `flushPendingStreamContent()`
- 然后如果 `data.final_answer` 存在，直接执行 `updates.content = data.final_answer`
- 最后把消息标记为 `isComplete = true`

证据：
- `frontend-vue/src/views/Home.vue:1124-1149`

这说明当前前端的最终正文来源非常明确：
- 完成态不是“保留流式阶段已经显示的内容”
- 而是“用 `done.final_answer` 重置一次消息正文”

### 6.3 前端对 P4 的直接约束

所以 `P4` 任何方案都必须满足以下至少一条：
- 总结块是 `done.final_answer` 的正文组成部分
- 或者前端新增单独的 `summary_block` 渲染路径，并在 `done` 时显式拼接

在当前代码下，不能依赖：
- “流式时已经显示出来了，所以完成后也会保留”

这在当前实现里不成立。

---

## 7. 对 P4-1 的直接启示

### 7.1 两条链路的共同事实

共同点：
- 都是先发流式 `content`
- 最后用 `done.final_answer` 固化
- gateway 持久化也以 `done.final_answer` 为准

所以：
- 第一阶段实验如果只改 prompt，不改 SSE 协议，也是可行的
- 但实验产物必须落进真正的最终答案字段，而不是只出现在中间流式内容里

### 7.2 `fastQA` 与 `highThinkingQA` 的差异

`fastQA`：
- 风险主要在 stage4 流式累计文本 vs `final_result.final_answer` 双来源不一致

`highThinkingQA`：
- 风险主要在 Step 4 draft 流式输出 vs Step 5 checker/reviser 后 `state.final_answer` 不一致

### 7.3 对方案 A 的最低实现要求

如果先做“prompt 约束总结块”实验，最低要求是：
- `fastQA`：总结块必须进入 `final_result.final_answer`
- `highThinkingQA`：总结块必须进入 `state.final_answer`
- 前端暂时不用新增结构化字段，只需继续消费 `done.final_answer`

否则实验结果会失真，因为：
- 用户流式时看到的，不一定是最终完成态保存下来的

---

## 8. 结论

`P4-1` 当前最关键的技术事实不是“能不能让模型写总结”，而是：

> 当前系统的完成态正文、持久化正文、后续再打开会话时看到的正文，全部以 `done.final_answer` 为最终权威。

所以 `P4` 第一阶段应该优先保证：
- 总结块进入最终答案权威字段
- 不在流式与完成态之间制造二次跳变
- 不新增一个前端还不会渲染的影子字段

在这个前提下，方案 A 才有可比较价值。

---

## 9. 第一阶段实验结论（2026-03-26）

本轮已经完成第一阶段最小实现，并验证以下事实：
- `fastQA` 与 `highThinkingQA` 都已接入统一实验开关 `ANSWER_SUMMARY_EXPERIMENT`
- 两条链路都会在最终权威答案边界处理总结块，而不是只改中间流式内容
- 前端不需要新增结构化字段，只要继续消费 `done.final_answer`，总结块即可在 streaming 完成态稳定保留

### 9.1 当前实现形态

当前第一阶段不是新增一轮独立 LLM 总结调用，而是：
- 在答案生成 prompt 中加入“末尾输出 `## 总结`”的约束
- 如果模型没有稳定产出总结块，则在最终答案收尾边界执行本地兜底
- 本地兜底只压缩现有正文，不引入新证据、不新增新引用

这保证了：
- 不会额外增加一次模型往返
- 不会进一步放大 `highThinkingQA` 当前已存在的慢点
- 完成态与持久化仍以同一个最终答案为准

### 9.2 验证结果

后端定向测试：
- `conda run -n agent pytest fastQA/tests/test_qa_placeholder.py highThinkingQA/tests/test_prompt_boundary.py -q`
- 结果：`31 passed`

前端定向测试：
- `cd frontend-vue && node --test src/utils/answerSummary.test.js`
- 结果：通过

前端构建验证：
- `cd frontend-vue && npm run build`
- 结果：通过

### 9.3 当前判断

本轮实验说明：
- 总结块可以在不改变 SSE 协议的前提下稳定进入最终答案
- 当前前端渲染链路不会在完成态把总结块吞掉
- 第一阶段可继续保留“prompt 约束 + 本地兜底”的轻量方案

### 9.4 是否值得进入方案 B

当前判断是：值得继续观察，但还不建议立刻跳到方案 B。

原因：
- 方案 A 已经满足“稳定进入最终答案”和“不增加新模型调用”这两个当前最关键目标
- `highThinkingQA` 还存在明显性能瓶颈，尤其是 `direct_answer` 和 `checker`，现阶段不适合再引入新的流式末尾小阶段
- 只有在第一阶段真实线上效果证明“总结块质量稳定但格式控制仍不够”时，再进入方案 B 会更稳妥
