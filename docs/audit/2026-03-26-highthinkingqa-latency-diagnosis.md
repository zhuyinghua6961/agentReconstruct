# 2026-03-26 highThinkingQA 慢点诊断与优化优先级

## 1. 范围

本轮只做只读诊断，不改代码。

目标：
- 找出 `highThinkingQA` 当前真正的慢点
- 判断慢点是在 `decompose`、`direct_answer`、检索流水线，还是 `checker`
- 给出后续优化的优先级，而不是继续凭感觉调整

诊断输入：
- [ask_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/ask_service.py)
- [graph.py](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/graph.py)
- [direct_answerer.py](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/direct_answerer.py)
- [decomposer.py](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/decomposer.py)
- [checker.py](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/checker.py)
- [highThinkingQA-app.log](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log)
- [gateway-access.log](/home/cqy/worktrees/highThinking/resource/logs/dev/gateway/gateway-access.log)

---

## 2. 结论摘要

当前 `highThinkingQA` 的主慢点不是检索，而是：
- `direct_answer`
- `decompose`
- `checker` 的“假超时”问题

准确说：
- 对用户可见的关键路径，`direct_answer` 是第一大慢点
- 对系统资源消耗和尾延迟风险，`checker` 是第一大隐患

---

## 3. 分阶段耗时结论

### 3.1 `decompose` 慢，但不是最长板

在样本中：
- 一次约 `84.352s`
- 一次约 `111.498s`

证据：
- [highThinkingQA-app.log#L19](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L19)
- [highThinkingQA-app.log#L140](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L140)

结论：
- 这个阶段只是生成子问题 JSON，却耗时 80 到 110 秒，明显偏重
- 但它仍不是整个链路的最长板

### 3.2 `direct_answer` 是关键路径主瓶颈

在样本中：
- 一次约 `169.362s`
- 一次约 `235.066s`

证据：
- [highThinkingQA-app.log#L85](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L85)
- [highThinkingQA-app.log#L213](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L213)
- [graph.py#L529](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/graph.py#L529)

结论：
- Step 1 总耗时取 `max(direct_answer, decompose)`
- 所以最终 Step 1 的用户体感主要被 `direct_answer` 决定
- 即使检索流水线已经推进完，流程仍然会卡在等待 `direct_answer` 收尾

### 3.3 `step2/step3` 不是当前主问题

在样本中：
- 一组约 `31.6s / 78.8s`
- 一组约 `37.5s / 87.3s`

证据：
- [highThinkingQA-app.log#L89](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L89)
- [highThinkingQA-app.log#L92](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L92)
- [highThinkingQA-app.log#L217](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L217)
- [highThinkingQA-app.log#L220](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L220)

结论：
- 检索流水线不算快，但不是当前最大问题
- 现阶段优先优化检索，不会显著改善 thinking 模式整体体感

### 3.4 `checker` 对用户可见固定卡 60 秒，但后台继续跑几分钟

在样本中：
- 前台超时在 60 秒左右结束
- 但后台真实 LLM 调用一次跑到约 `352.295s`
- 另一次跑到约 `323.393s`

证据：
- [highThinkingQA-app.log#L111](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L111)
- [highThinkingQA-app.log#L114](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L114)
- [highThinkingQA-app.log#L240](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L240)
- [highThinkingQA-app.log#L251](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L251)

结论：
- 现在的 60 秒只是“对外返回超时”，不是“真正取消底层任务”
- 这会造成后台线程、LLM 配额和系统吞吐继续被拖住

---

## 4. 根因分析

### 4.1 高置信度：`direct_answer` 本身就太重

证据：
- [direct_answerer.py#L32](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/direct_answerer.py#L32)
- [graph.py#L423](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/graph.py#L423)
- [graph.py#L510](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/graph.py#L510)

判断：
- 它是单独的 LLM 调用
- token 上限高
- 还允许 `thinking`
- 并且结果必须完成后，主链才能正式进入综合收尾

所以它不是“可被其他阶段隐藏掉的后台工作”，而是实打实的关键路径。

### 4.2 高置信度：`direct_answer` 的大头耗在首个正文 token 之前

证据：
- [highThinkingQA-app.log#L49](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L49)
- [highThinkingQA-app.log#L174](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L174)

判断：
- 第一个正文 chunk 要到 `113s` 和 `174s` 才出现
- 说明不是前端渲染慢，也不是后端 chunk flush 慢
- 主要时间耗在模型端推理、排队或首包生成前阶段

### 4.3 高置信度：`checker` 超时策略失效在“取消语义”层面

证据：
- [graph.py#L53](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/graph.py#L53)
- [graph.py#L671](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/graph.py#L671)

判断：
- 当前只是 `future.result(timeout=60)` 包装超时
- 超时后调用 `future.cancel()` 和 `shutdown(wait=False)`
- 这不能中断一个已经在跑的阻塞 LLM 请求
- 所以前台返回了，后台继续跑

### 4.4 中高置信度：`decompose` 对任务规模来说过重

证据：
- [decomposer.py#L35](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/decomposer.py#L35)
- [highThinkingQA-app.log#L19](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L19)
- [highThinkingQA-app.log#L140](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L140)

判断：
- 这一步只是产出有限数量的子问题
- 80 到 110 秒的耗时明显不合理
- 本质还是模型选择或 thinking 配置过重

### 4.5 中等置信度：`checker` 输入缩减后仍然偏大

证据：
- [checker.py#L215](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/checker.py#L215)
- [checker.py#L405](/home/cqy/worktrees/highThinking/highThinkingQA/agent_core/checker.py#L405)
- [highThinkingQA-app.log#L110](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L110)
- [highThinkingQA-app.log#L238](/home/cqy/worktrees/highThinking/resource/logs/dev/highThinkingQA/highThinkingQA-app.log#L238)

判断：
- 即使已经裁剪到每 slice 最多 `8` chunks、最多 `6000 chars`
- 实际总输入仍达到 `18064` 到 `26996 chars`
- 对审计型模型依然很重

---

## 5. 对用户体验的直接影响

### 5.1 为什么会感觉“系统卡住”

因为当前链路是：
- 草稿答案先出来
- 但最终 `done` 还要等 Step 5 结束或超时
- 如果 checker 超时不干净，前台结束与后台资源释放还会脱节

所以用户看到的是：
- 已经像快结束了
- 但长时间不完成

### 5.2 为什么不能只盯 `checker`

因为即使完全跳过 `checker`：
- `direct_answer` 仍然有 169 到 235 秒级耗时
- `decompose` 也仍然有 84 到 111 秒级耗时

所以 `checker` 是大问题，但不是唯一问题。

---

## 6. 优化优先级

### P0：先压 `direct_answer`

建议方向：
- 换更快的 `DIRECT_ANSWER_MODEL`
- 关闭 `DIRECT_ANSWER_ENABLE_THINKING`
- 评估是否可以把它从“强依赖完整收尾”降成“可降级参考输入”

原因：
- 它是当前关键路径最大耗时
- 不动它，thinking 模式整体响应不会明显变快

### P0：修 `checker` 的假超时

建议方向：
- 要么短期先弱化或关闭 checker
- 要么改成真正可中断的请求模型
- 至少要防止超时后后台还继续跑数分钟

原因：
- 这是最严重的吞吐与资源风险
- 会把后续请求一起拖慢

### P1：压 `decompose`

建议方向：
- 换更快的 `DECOMPOSE_MODEL`
- 关闭 `DECOMPOSE_ENABLE_THINKING`
- 必要时减少子问题数量

原因：
- 这一步本身不复杂，不值得吃 80 到 110 秒

### P1：间接压 `checker` 负载

建议方向：
- 降低 `NUM_SUB_QUESTIONS`
- 降低 `RETRIEVAL_TOP_K`
- 减少最终草稿中的引用密度和证据面

原因：
- 这会直接降低 checker slice 数和输入体积

### P2：运营层限流 thinking 模式

建议方向：
- 在修掉假超时前，限制 thinking 并发
- 或减少简单问题进入 `highThinkingQA`

原因：
- 当前系统在后台悬挂请求存在时，对并发很脆弱

### P3：补 gateway 侧时长观测

建议方向：
- access log 增加 `trace_id`
- access log 增加 request duration

原因：
- 这不是当前主慢点
- 但后续如果要进一步分层定位，网关层可观测性还是要补

---

## 7. 建议的执行顺序

建议顺序：
1. 先优化 `direct_answer`
2. 再修 `checker` 的真取消/假超时问题
3. 然后压 `decompose`
4. 最后再做检索和网关侧可观测性增强

---

## 8. 结论

当前 `highThinkingQA` 的性能问题不能再笼统归因成“checker 太慢”。

更准确的结论是：
- 对用户等待时间，`direct_answer` 是第一瓶颈
- 对系统资源和尾延迟，`checker` 的假超时是第一风险
- `decompose` 也明显偏慢，但优先级低于前两者

后续如果要动优化，优先级不能再反过来。
