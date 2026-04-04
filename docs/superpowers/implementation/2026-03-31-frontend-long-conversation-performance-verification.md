# Frontend Long Conversation Performance Verification

**Date:** 2026-03-31
**Scope:** `frontend-vue` 长会话性能验证
**Related Spec:** `/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-31-frontend-long-conversation-performance-design.md`
**Related Plan:** `/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-31-frontend-long-conversation-performance-plan.md`

---

## Purpose

本文件定义前端长会话性能优化的统一验证基线，避免后续实现阶段依赖主观体感做判断。

所有 P0 / P1 的 stop-go 决策都必须基于本文件中的固定 workload、固定 trace 方式和固定 gate checklist。

---

## Automated Check Log

### 2026-03-31 Automated Verification Snapshot

- 执行人：Codex
- git commit：`working tree dirty / not committed yet`
- 验证范围：P0-P1 当前代码链路的自动化回归；不含浏览器手工 workload profiling

执行命令：

```bash
cd frontend-vue && npm test -- src/stores/chatStore.persistenceTiming.test.js src/stores/streamPersistPolicy.test.js src/stores/chatPersistence.test.js src/utils/messageWindowing.test.js src/views/Home.structure.test.js
cd frontend-vue && npm test
cd frontend-vue && npm run build
```

结果：

- `src/stores/chatStore.persistenceTiming.test.js`：PASS
- `src/stores/streamPersistPolicy.test.js`：PASS
- `src/stores/chatPersistence.test.js`：PASS
- `src/utils/messageWindowing.test.js`：PASS
- `src/views/Home.structure.test.js`：PASS
- 全量前端测试：`20/20` PASS
- `vite build`：PASS

当前已自动化验证的能力：

- reload-cycle persistence boundary
- streaming target fast path
- historical memo recompute reduction
- outline signature rebuild policy
- near-bottom scroll gating
- streaming persist debounce policy（`1200ms`）
- terminal force persist on streaming end transition
- stable absolute message identity in render layer
- hidden-history reveal flow
- outline reveal-first flow
- DOI / steps / highlight 的 stable identity contract

当前尚未完成的验证：

- 固定 `10 / 20 / 30-turn` 浏览器 profiling workload
- `p95 chunk script time`
- `saveChats()` 真实触发次数观测
- 浏览器“页面无响应”/ 输入 / 滚动 / 大纲交互的人工 gate 判定

Task 6 当前状态：

- `Step 1` 已完成
- `Step 2-4` 仍依赖真实浏览器联调、Performance trace 与人工判读，当前不能仅靠终端结果替代

---

## Environment Template

每次记录前必须先填写：

- 日期：
- 执行人：
- git commit：
- 浏览器与版本：
- DevTools 版本记录方式：
- 操作系统：
- 前端启动方式：
- 后端启动方式：
- 是否清空浏览器缓存：
- 是否清空 localStorage：
- 是否使用全新会话开始：

约束：

- 同一轮 before / after 对比必须尽量使用相同浏览器版本和相同环境
- 如果使用不同环境，必须单独标注，不能直接和旧数据横向比较
- 如果使用 Chrome / Edge 内置 DevTools，则 `DevTools 版本记录方式` 填写为：`same as browser version`

---

## Fixed Workload

### Conversation Setup

固定使用一个新建会话，从空白会话开始。

会话内总共执行 `30` 轮问答，过程中不切换会话。

### Message Mix Requirements

这 30 轮必须覆盖以下内容，顺序可以微调，但不能缺项：

1. 至少 `8` 轮普通长文本回答
2. 至少 `6` 轮包含 markdown 列表和二级标题的回答
3. 至少 `4` 轮包含 markdown 表格的回答
4. 至少 `4` 轮包含 DOI 链接的回答
5. 至少 `4` 轮包含处理步骤 `steps` 更新的回答
6. 至少 `2` 轮包含引用位置或参考文献信息展示的回答
7. 至少 `1` 次在流式输出中途主动上滑查看历史，再等待回答继续输出
8. 至少 `1` 次在 assistant 尚未完成时刷新页面，验证 reload-cycle 语义
9. 至少 `1` 次在右侧问题大纲中点击较早问题，验证定位行为

### Fixed Special Turns

以下特殊场景必须绑定到固定轮次，不能随意改动：

- 第 `12` 轮：在 assistant 流式输出中途主动上滑查看历史，再等待回答继续输出
- 第 `18` 轮：在 assistant 尚未完成时刷新页面，验证 reload-cycle
- 第 `20` 轮：在回答完成后点击右侧问题大纲中的较早问题，只验证 P0 下的大纲可用性，不新增问题
- 第 `29` 轮：通过右侧问题大纲点击较早问题，再继续追问

计数规则：

- 第 `18` 轮 mid-stream refresh 被打断的问题仍计入 30 轮 workload
- 刷新后不重发第 `18` 轮问题，直接继续后续轮次
- `10-turn` trace 从第 `10` 轮发问前开始，到第 `10` 轮回答完成后结束
- `20-turn` trace 从第 `18` 轮发问前开始，必须包含第 `18` 轮刷新恢复过程，到第 `20` 轮回答完成后结束
- 第 `20` 轮固定大纲点击只做“点击后 `2s` 内完成定位”的可用性验证，不改变 workload 计数
- `30-turn` trace 从第 `29` 轮点击右侧问题大纲前开始，必须包含第 `29` 轮大纲跳转与继续追问，到第 `30` 轮回答完成后结束
- 第 `20` 轮固定大纲点击在 `20-turn` trace 停止后执行，不要求纳入 trace，只要求记录行为结果

### Suggested Question Script

以下脚本用于尽量稳定地产生长回答，不要求逐字一致，但每次测试应尽量复用同一套问题。

1. 解释磷酸铁锂厚电极在高倍率下的主要限制，并给出分点总结。
2. 用二级标题整理一下厚电极、孔隙率、离子扩散三者关系。
3. 输出一个表格，对比高压实密度和高孔隙率方案的优缺点。
4. 结合参考文献，列出 3 个 DOI 并解释各自结论。
5. 按步骤说明厚电极液相浓差极化的形成过程。
6. 总结一下高面载量电极的工程优化方向，要求带小标题和列表。
7. 输出一个两列表格，左边是问题，右边是解决思路。
8. 解释为什么电子传导和离子传导的最优设计不完全一致。
9. 给一个包含 DOI 的参考阅读列表。
10. 继续展开第 8 个问题，要求更详细，带分点。
11. 再生成一张表，总结高倍率、低温、长循环三种工况下的主要矛盾。
12. 用处理步骤说明如何分析厚电极倍率性能。
13. 总结厚电极和薄电极的本质差异，要求二级标题。
14. 给出 3 篇相关论文 DOI，并分别用一句话说明用途。
15. 解释压实密度升高后为什么润湿会变差。
16. 输出一个 markdown 表格，比较不同粘结剂方案。
17. 按步骤写一个“如何排查倍率差”的 checklist。
18. 解释离子传输瓶颈和电子传输瓶颈的判别方式。
19. 继续第 18 个问题，要求给出引用和参考文献。
20. 总结一下当前会话前面讨论过的关键结论。
21. 生成一个包含 DOI、列表、加粗项的综合回答。
22. 用表格总结本会话讨论过的厚电极问题。
23. 说明如何设计一套实验来验证液相浓差极化。
24. 带步骤地解释如何从 EIS 和倍率数据反推问题来源。
25. 输出一个带 DOI 的延伸阅读建议列表。
26. 总结电极厚度、孔隙率、颗粒尺寸三者的权衡。
27. 用二级标题和列表输出一版更结构化的总结。
28. 再输出一个表格，对比实验诊断方法。
29. 点击右侧较早问题后，要求继续追问一个延伸问题。
30. 在最终回答中要求同时包含表格、列表、DOI 与步骤信息。

---

## Trace Procedure

### Before Recording

1. 打开一个新的浏览器标签页，仅保留本系统前端页面。
2. 打开 DevTools。
3. 进入 `Performance` 面板。
4. 确认浏览器没有其他明显占用 CPU 的后台任务。
5. 如果本轮是基线 before 测试，先清空 localStorage 后刷新页面。

### Performance Recording Steps

对每一个关键阶段分别录制：

1. `10-turn` 录制
2. `20-turn` 录制
3. `30-turn` 录制

每次录制步骤：

1. 按固定起点开始录制 Performance trace：
   - `10-turn`：第 `10` 轮发问前
   - `20-turn`：第 `18` 轮发问前
   - `30-turn`：第 `29` 轮点击右侧问题大纲前
2. 完成该录制窗口内要求的交互。
3. 到固定终点停止录制：
   - `10-turn`：第 `10` 轮回答完成
   - `20-turn`：第 `20` 轮回答完成
   - `30-turn`：第 `30` 轮回答完成
4. 记录本轮 trace 文件或截图引用。

每次录制还必须同步打开 Console，执行以下只读计数语句：

```js
document.querySelectorAll('.messages-area .message[data-message-index]').length
```

该值作为 `实际渲染消息数` 记录来源。

### Required Metrics

每次记录必须填写：

- 当前轮次：`10 / 20 / 30`
- 当前消息总数：
- 实际渲染消息数：
- `p95 chunk script time`：
- 本轮是否出现浏览器“无响应”：`yes / no`
- 本轮输入框是否可交互：`yes / no`
- 本轮滚动是否可交互：`yes / no`
- 本轮右侧大纲点击是否可交互：`yes / no`
- `saveChats()` 触发次数：
- 观察到的主热点：
- 备注：

字段记录来源：

- `当前消息总数`：
  - 优先使用开发环境只读计数日志
  - 若暂时没有计数日志，则填 `not directly observable`，并在备注中说明使用的替代观测方式
- `实际渲染消息数`：
  - 在 Console 执行 `document.querySelectorAll('.messages-area .message[data-message-index]').length`
- `p95 chunk script time`：
  - 按下方 `How To Read p95 chunk script time` 章节统一方法记录
- `本轮右侧大纲点击是否可交互`：
  - `20-turn`：执行第 `20` 轮固定 outline check
  - `30-turn`：执行第 `29` 轮 outline reveal 场景
- `saveChats()` 触发次数：
  - 按下方 `How To Count saveChats() Frequency` 章节统一方法记录
- `configured_window_size`：
  - 从实现中的窗口常量或配置值读取；若无单独配置界面，则直接记录代码常量名与当前值

### How To Read `p95 chunk script time`

本项目统一使用以下单一方法：

1. 在 Performance trace 中只选中“当前轮 assistant 开始输出第一段可见内容”到“本轮回答完成”为止的时间范围。
2. 打开该选区下方的 `Bottom-Up` 视图。
3. 过滤类型为主线程 `Scripting`。
4. 只保留明显属于前端应用渲染的 scripting slices，排除浏览器扩展和无关后台任务。
5. 读取该区间内所有应用侧 scripting slice 的 duration。
6. 如果 DevTools 无法直接显示 p95，则导出 trace JSON，用同一脚本按 duration 计算 p95。

要求：

- 同一轮 before / after 必须使用相同计算方法
- 不允许本轮使用人工目测，下轮改成导出 JSON 计算
- 如果导出 JSON 计算，则 before / after 都必须使用导出 JSON 计算

流式时间窗口边界固定为：

- 起点：assistant 第一段可见正文进入消息区
- 终点：该轮 assistant 完成，或出现明确 `done/final` 结束信号对应的最后一次前端更新

### How To Count `saveChats()` Frequency

开发验证阶段允许临时加入仅开发环境可见的计数日志或断点辅助，但要求：

- 不打印消息正文
- 不改变业务行为
- before / after 统计方法一致

最终记录内容：

- 本轮回答期间 `saveChats()` 总触发次数
- 是否符合当前 debounce 设定

机械判定规则：

- 记 `D = 当前实现的 streaming persistence debounce 毫秒数`
  - 记录为对应常量名与当前值
- 记 `T = 本轮 assistant 流式阶段持续毫秒数`
- 允许的 `saveChats()` 最大次数为 `ceil(T / D) + 1`
- 若实际次数大于该值，则判定为 `no`

### How To Judge Rendered-Message Count

统一使用以下方法：

1. 在目标轮回答完成后，打开 Console。
2. 执行：

```js
document.querySelectorAll('.messages-area .message[data-message-index]').length
```

3. 记录返回值。

说明：

- 在 P0 阶段，该值通常接近完整消息数
- 在 P1 阶段，该值应明显低于完整消息数，并稳定在窗口范围附近

### How To Judge Tab Responsiveness

机械判定规则：

- 若浏览器出现系统级“页面无响应”提示，则记为 `no`
- 若在回答流式期间尝试点击输入框、滚动消息区、点击右侧问题大纲，任一操作在 `2s` 内没有可见响应，则对应项记为 `no`
- 若 `2s` 内完成可见响应，则对应项记为 `yes`

### How To Judge Dominant Hotspot

统一依据同一轮 trace 中 `Bottom-Up` 或导出 JSON 的主线程 `Scripting` duration 排名判断。

如果排名最靠前的应用侧热点仍明显属于以下类别之一，则记为“整会话渲染类热点仍然存在”：

- `buildMessageRenderMemoKey`
- `formatAnswer` / 流式格式化在整段历史消息上反复执行
- `questionOutlineItems` 或等价的整数组遍历
- `getStreamingTargetMessage` 或等价的整数组扫描
- `saveChats` / `persistChatsNow` / `sanitizeChats` / `JSON.stringify(chats)` 等整段持久化

如果排名最靠前的热点已转移到其他局部逻辑，且不再表现为整段会话全量遍历/序列化，则该项可记为 `yes`

### Allowed Debug Observation Methods

允许的辅助观测方式仅限：

- DevTools `Performance`
- DevTools `Console`
- DevTools `Elements`
- 开发环境只读计数日志

限制：

- 不允许为验证临时修改业务行为
- 不允许打印消息正文
- 不允许通过断点暂停后人工改状态
- 能用外部行为判定的，一律优先用外部行为，不记录内部状态名作为结论

---

## Reload-Cycle Verification

在固定 workload 中必须包含一次“流式回答尚未完成时刷新页面”的验证。

### Required Checks

刷新后必须记录：

- 页面是否仍显示“正在生成/可中断”的活跃流式 UI：`yes / no`
- 刷新后输入框是否回到可提问状态：`yes / no`
- 未完成 assistant 内容是否仍可作为静态内容查看：`yes / no`
- 步骤折叠状态是否按预期恢复：`yes / no`
- 引用 / DOI / 已完成步骤信息是否恢复：`yes / no`
- 消息数组顺序和数量是否保持不变：`yes / no`
- runtime-only 状态是否被正确重置：`yes / no`

`runtime-only 状态是否被正确重置` 的统一行为判定：

- 刷新后页面不应继续自动滚动旧回答
- 刷新后不应保留旧的高亮定位效果
- 若没有新回答开始，不应出现“仍在流式中”的假状态

---

## Current Automated Check Snapshot

### Latest Local Verification

- Date: 2026-03-31
- Scope: Task 5 P0 implementation after outline/scroll/persist changes
- Commands:
  - `cd frontend-vue && npm test -- src/stores/streamPersistPolicy.test.js src/stores/chatPersistence.test.js`
  - `cd frontend-vue && npm test`
  - `cd frontend-vue && npm run build`
- Result:
  - store/helper focused tests: pass
  - full frontend tests: `17/17` pass
  - production build: pass

### Current Implementation Values

- streaming persistence debounce constant: `STREAM_PERSIST_DEBOUNCE_MS = 1200`
- near-bottom threshold: `DEFAULT_NEAR_BOTTOM_THRESHOLD_PX = 120`

### What This Snapshot Proves

- P0 code paths compile and pass regression coverage
- reload-cycle persistence matrix still passes after Task 5 changes
- streaming persistence strategy is now explicitly testable and fixed at `1200ms` during streaming
- terminal streaming transition still force-persists on `true -> false`

### What This Snapshot Does Not Prove

- it does not replace browser `Performance` trace measurement
- it does not establish `p95 chunk script time`
- it does not establish 20-turn / 30-turn no-freeze behavior
- it does not decide the P0 stop-go gate

## P0 Stop-Go Gate

只有当以下所有条件都满足时，P0 才允许停止，不进入 P1：

1. `20-turn` workload 下没有出现浏览器“页面无响应”
2. 输入框仍可继续输入
3. 消息区仍可正常滚动
4. 右侧问题大纲仍可点击并响应
5. `saveChats()` 触发频率符合实现中的 debounce 预期
6. 当前主热点已经不再是“整段会话全量渲染 / 全量扫描 / 全量持久化”类问题

如果以上任一条不满足，则结论必须写为：

- `P0 not sufficient`
- `Proceed to P1`

不允许使用“感觉差不多”“体感还可以”这类描述替代 gate。

### P0 Gate Checklist

| Check | Rule | Result |
| --- | --- | --- |
| No browser unresponsive | 无系统级“页面无响应”提示 | yes / no |
| Input usable | 点击输入框后 `2s` 内可见响应 | yes / no |
| Scroll usable | 手动滚动消息区后 `2s` 内可见响应 | yes / no |
| Outline usable | 第 `20` 轮回答完成后点击右侧问题大纲中的较早问题，`2s` 内完成定位 | yes / no |
| Persist frequency OK | `saveChats()` 次数 `<= ceil(T / D) + 1` | yes / no |
| Dominant hotspot no longer whole-conversation churn | 主热点不再属于整段渲染/扫描/持久化类 | yes / no |

机械结论：

- 若以上任一项为 `no`，则 `P0 stop-go result = proceed to P1`
- 仅当以上全部为 `yes`，才允许 `P0 stop-go result = stop at P0`

---

## P1 Verification Focus

如果进入 P1，则必须额外验证：

1. 实际渲染消息数在 `30-turn` 场景下被限制在窗口范围附近
2. 右侧大纲点击较早问题时，会先展开隐藏历史，再定位到正确消息
3. DOI 点击仍能命中正确原始消息
4. steps 展开/收起仍作用于正确原始消息
5. highlight 与滚动定位不再依赖窗口局部 index

### P1 Checklist

| Check | Rule | Result |
| --- | --- | --- |
| Render count bounded | `30-turn` 时 `rendered_count <= configured_window_size + 4` | yes / no |
| Outline reveal works | 点击较早问题时先展开隐藏历史，再定位到正确消息，`2s` 内完成 | yes / no |
| DOI target works | DOI 点击仍命中正确原始消息 | yes / no |
| Steps target works | steps 展开/收起仍命中正确原始消息 | yes / no |
| Highlight uses stable identity | 点击隐藏历史中的目标后，高亮和定位仍落在原始消息，不出现错位 | yes / no |

机械结论：

- 若任一项为 `no`，则 P1 不通过
- 仅当全部为 `yes`，才允许写入“P1 可上线保留”

---

## Recording Template

### Run Meta

`Run Meta` 直接复用上方 `Environment Template` 字段，只允许补充，不允许改名。

- 日期：
- 执行人：
- git commit：
- 浏览器与版本：
- DevTools 版本记录方式：
- 操作系统：
- 前端启动方式：
- 后端启动方式：
- 是否清空浏览器缓存：
- 是否清空 localStorage：
- 是否使用全新会话开始：
- 环境：

### 10-Turn Result

- 消息总数：
- 渲染消息数：
- p95 chunk script time：
- `saveChats()` 次数：
- 输入可用：yes / no
- 滚动可用：yes / no
- 大纲可用：yes / no
- 无响应：yes / no
- 主热点：
- 备注：

### 20-Turn Result

- 消息总数：
- 渲染消息数：
- p95 chunk script time：
- `saveChats()` 次数：
- 输入可用：yes / no
- 滚动可用：yes / no
- 大纲可用：yes / no
- 无响应：yes / no
- 主热点：
- 备注：

### 30-Turn Result

- 消息总数：
- 渲染消息数：
- p95 chunk script time：
- `saveChats()` 次数：
- 输入可用：yes / no
- 滚动可用：yes / no
- 大纲可用：yes / no
- 无响应：yes / no
- 主热点：
- 备注：

### Reload-Cycle Result

- mid-stream refresh 触发轮次：
- reload 后不再显示活跃流式 UI：yes / no
- reload 后输入框可提问：yes / no
- unfinished assistant 静态可见：yes / no
- 步骤折叠状态恢复正确：yes / no
- 引用 / DOI / 已完成步骤信息恢复正确：yes / no
- 消息顺序和数量保持：yes / no
- runtime-only 状态已重置：yes / no
- 备注：

### Gate Decision

- P0 stop-go result：`stop at P0 / proceed to P1`
- P0 checklist：
  - No browser unresponsive：yes / no
  - Input usable：yes / no
  - Scroll usable：yes / no
  - Outline usable：yes / no
  - Persist frequency OK：yes / no
  - Dominant hotspot no longer whole-conversation churn：yes / no
- 当前 dominant hotspot：

### Final Decision After P1

- P1 是否需要保留上线：yes / no
- 是否还需要 P2 虚拟列表预研：yes / no
- configured_window_size：
- P1 checklist：
  - Render count bounded：yes / no
  - Outline reveal works：yes / no
  - DOI target works：yes / no
  - Steps target works：yes / no
  - Highlight uses stable identity：yes / no
- 理由：

---

## Notes

- 本文件是验证基线，不是实现文档。
- 若后续 workload 需要修改，必须先更新本文件，再进行新的 before / after 对比。
- 不允许在不同 workload 下直接比较性能数字。
