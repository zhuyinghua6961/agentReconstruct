# Frontend Long Conversation Performance Design Review

**Date:** 2026-03-31
**Target:** `docs/superpowers/specs/2026-03-31-frontend-long-conversation-performance-design.md`

## Findings

### 1. Medium: 渲染控制状态如果直接混入消息体，会污染持久化边界

风险：

- `renderVersion`、`renderFrozen`、`isStreamingTarget` 这类字段如果直接写进 `store.chats` 的消息对象并参与 `saveChats()`，会把本来只属于前端运行时的渲染状态一并持久化。
- 这会扩大 `localStorage` 体积，也会让后续与 `public-service` / 后端消息结构对齐变得混乱。

处理结果：

- 已在 design 文档中补充约束：渲染控制状态优先保存在组件内存态或 `WeakMap`，不进入持久化消息体。

结论：已修正，不阻塞进入实现。

### 2. Medium: 首版窗口化方案需要更明确的启用阈值和回退边界

风险：

- 如果只写“只渲染最近窗口”，实现时很容易出现两个问题：
  - 触发过早，导致短会话也被折叠
  - 触发后无回退开关，一旦影响定位/引用跳转就很难快速止损

处理结果：

- 已在 design 文档中补充：
  - 首版窗口化启用阈值建议 `totalMessages > 30`
  - 首版窗口大小 `N = 24`
  - 增加 `Rollout Guard`，要求 P0 与 P1 分开提交和验证，且窗口化逻辑保留独立开关

结论：已修正，不阻塞进入实现。

## Open Notes

### 1. 无阻塞但仍需实测确认的项

- `near-bottom = 120px`
- 流式持久化 debounce `1200ms`
- 历史窗口阈值 `> 30`
- 最近窗口 `24` 条

这些值现在适合作为首版默认值，但最终仍应以浏览器性能录制结果为准，而不是写死为“理论最优值”。

### 2. P0 应先于 P1 独立验证

这是本 review 最重要的执行建议。

原因：

- 当前瓶颈未必需要直接上历史窗口化
- P0 已经针对最主要的热点路径开刀，可能足以把 10+ 轮卡死推进到 20+ 轮稳定
- 如果 P0 已满足目标，再上 P1 反而会引入不必要的交互复杂度

## Review Verdict

本次 spec **无 blocking findings**。

这份设计已经满足以下要求：

1. 根因分析基于真实代码热点，而不是拍脑袋调参数。
2. 方案按收益与风险拆成了 `P0 -> P1 -> P2`，顺序合理。
3. 对消息持久化、引用定位、右侧大纲、滚动行为这些高风险回归点都做了明确约束。
4. 已经补齐实现阈值、回退开关和运行时状态边界，不再是空泛建议。

结论：可以进入 implementation plan 阶段。
