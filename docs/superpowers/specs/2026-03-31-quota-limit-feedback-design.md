# 配额受限提示统一设计 Spec

**Date:** 2026-03-31

## Scope

本设计只处理“配额相关失败”在前端的展示与中间层透传，不修改配额扣减规则本身。

本设计覆盖：

1. 聊天页问答链路的配额受限提示
2. 文献辅助链路的配额受限提示
3. `PdfReader` 内的查看原文 / 全文总结 / 翻译
4. `gateway` 对 quota 失败明细的结构化透传
5. `frontend-vue` 统一的 quota 错误格式化与卡片渲染

本设计不覆盖：

1. `public-service` 的 quota 计算逻辑改造
2. 配额类型新增或重命名
3. 用户中心 quota 页面改版
4. 非 quota 类业务错误的统一卡片化
5. `patent` 路由的特殊提示策略
6. `PdfReader` 里非 quota 的 PDF 加载失败样式重构
7. 当前未挂载到活跃 UI 的引用辅助面板改造

---

## Problem Statement

当前系统已经有可用的 quota 后端能力，但前端展示仍然是碎片化、弱结构化状态。

用户体验上的主要问题有三类：

1. 聊天页问答在命中 quota 限制时，只能看到通用“处理失败”，看不出是哪个能力耗尽、属于哪个额度池、何时恢复。
2. 文献辅助里的查看原文、翻译、总结等能力，虽然部分地方会提示配额不足，但仍然是拼字符串式文案，样式、信息密度、动作引导都不一致。
3. `QUOTA_EXCEEDED` 与 “系统未就绪 / 配额服务异常” 没被明确区分，用户会把配置或系统问题误认为自己的额度已用完。

结果是：

1. 用户不知道该去哪看剩余额度
2. 用户不知道当前失败是“额度耗尽”还是“系统问题”
3. 聊天页和文献辅助的提示风格不一致，产品感知割裂

---

## Current State And Evidence

以下是已确认的代码事实，不是推测。

### 1. 聊天页问答错误提示入口过于通用

文件：`frontend-vue/src/utils/routingStatus.js`

当前 `buildRoutingErrorMarkdown()` 只对以下错误码做定制化文案：

1. `FILE_SELECTION_CLARIFICATION_REQUIRED`
2. `FILE_NOT_READY`
3. `FILE_PROCESSING_FAILED`
4. `FILE_NOT_FOUND`

除此之外全部退回通用：

- `处理失败`
- `<message>`

这意味着 `QUOTA_EXCEEDED`、`QUOTA_CONFIG_MISSING`、`QUOTA_INTERNAL_UNAVAILABLE` 当前都没有专门前端表达。

### 2. gateway 的流式 quota 失败透传字段太少

文件：`gateway/app/routers/qa.py`

当前 `_quota_precheck_error_stream()` 只透出：

1. `code`
2. `error`
3. `message`
4. `trace_id`

而 `public-service` 在 quota 失败时实际上已经提供了更完整的 quota 失败明细，但没有被 `gateway` 继续透给前端。

### 3. public-service 已有完整 quota 明细

文件：`public-service/backend/app/modules/quota/service.py`

`check_quota()` 已能返回：

1. `quota_type`
2. `quota_name`
3. `current`
4. `limit`
5. `remaining`
6. `period`
7. `period_days`
8. `reset_hint`
9. `windows`
10. `config_missing`
11. `config_active`
12. `multi_period_enabled`

这说明数据不缺，缺的是透传与前端消费。

### 4. 前端已经有 quota 数据规范化能力

文件：

1. `frontend-vue/src/services/quota-normalization.js`
2. `frontend-vue/src/views/UserProfile.vue`

现有工具已经能做：

1. 配额类型归一化
2. 中文 quota 名称映射
3. `reset_hint` 格式化
4. `windows` 规范化

因此本次不应该重新发明一套 quota 数据解释逻辑，而应复用已有能力。

### 5. 文献辅助当前已有弱提示但未统一

文件：`frontend-vue/src/components/PdfReader.vue`

`buildQuotaErrorMessage(error, featureName)` 目前只会返回简单字符串，比如：

- `全文总结配额不足，请在个人中心查看剩余额度`

这说明文献辅助并非完全没有处理 quota，但仍停留在“字符串错误消息”阶段，没有统一卡片、没有 quota 明细、没有系统未就绪分支。

### 6. 存在一组引用辅助代码路径，但当前未挂载到活跃 UI

文件：

1. `frontend-vue/src/features/references/composables/useReferenceInspector.js`
2. `public-service/backend/app/modules/documents/api.py`

当前这组代码会直接调用：

1. `literature_content`
2. `reference_preview`

它们在后端同样属于 `doc_assist` 计额能力，但当前仓库里看不到真实挂载入口。这说明它们更像预留代码路径，而不是当前用户可见界面。

因此本轮 rollout 不把这组未挂载 UI 纳入实现范围，避免把 dead code 改成“看起来完成但用户不可见”的状态。

### 7. 查看原文链路的 quota 实际发生在 `view_pdf`，不是 `check_pdf`

文件：

1. `frontend-vue/src/components/PdfReader.vue`
2. `frontend-vue/src/api/literature.js`
3. `public-service/backend/app/modules/documents/api.py`

当前前端打开原文时，先调用 `check_pdf` 判断是否存在，再把 `view_pdf` URL 直接塞给 `iframe`。

但真正带 `file_view` 配额检查的是：

1. `HEAD /api/view_pdf/{doi}`
2. `GET /api/view_pdf/{doi}`

`check_pdf` 本身不带 quota。

这意味着如果继续沿用“`check_pdf` 成功 -> 直接挂 iframe”的流，前端拿不到结构化 quota 失败信息，也无法展示统一 quota 卡片。

---

## Design Goals

### 产品目标

1. 用户一眼看懂是哪个能力受限
2. 用户一眼看懂是哪个 quota 池被消耗
3. 用户一眼看懂何时恢复，或当前是系统问题
4. 提供统一且明确的下一步动作：去个人中心查看配额

### 工程目标

1. 不复制 quota 解释逻辑
2. 不要求 `public-service` 为这次 UI 改造改协议
3. 由 `gateway` 完成最小必要透传
4. 由 `frontend-vue` 完成统一格式化和展示

### 体验目标

1. 聊天页和文献辅助使用一致的视觉语言
2. `QUOTA_EXCEEDED` 与系统未就绪问题强区分
3. 支持未来把更多 quota 入口接入同一组件

---

## Non-Goals

本次明确不做：

1. 不新增 quota 类型
2. 不改 quota 扣减时机
3. 不改用户中心 quota 页接口
4. 不统一所有业务错误卡片
5. 不把所有失败都包装成 quota 卡片

---

## Approaches

### 方案 A：仅补聊天页文案分支

做法：

1. 在 `routingStatus.js` 里追加 `QUOTA_EXCEEDED` 文案
2. 文献辅助仍保留各自字符串错误

优点：

1. 改动最小
2. 风险低

缺点：

1. 聊天页和文献辅助继续割裂
2. 仍无法展示 `current / limit / reset_hint / windows`
3. 无法区分额度耗尽和系统未就绪

结论：不推荐。

### 方案 B：统一结构化 quota 卡片

做法：

1. `gateway` 透传 quota 明细
2. `frontend-vue` 新增 quota error formatter
3. `frontend-vue` 新增统一 `QuotaLimitCard` 组件
4. 聊天页和文献辅助都消费同一数据结构

优点：

1. 一次收口两条链路
2. 能复用用户中心 quota 规范化逻辑
3. 扩展性最好

缺点：

1. 改动面比方案 A 大
2. 需要梳理聊天页 markdown 错误块和组件化展示的接缝

结论：推荐方案。

### 方案 C：完全由后端直接返回最终展示文案

做法：

1. `gateway` 或 `public-service` 直接拼中文提示文案
2. 前端只原样展示

优点：

1. 前端逻辑少

缺点：

1. 展示层和协议层耦合过深
2. 文案、样式、层级难迭代
3. 用户中心已有 quota 规范化工具无法复用

结论：不推荐。

---

## Recommended Design

采用方案 B：`gateway` 透传最小但完整的 quota 结构化负载，`frontend-vue` 用统一 formatter + 统一卡片展示聊天页和文献辅助的 quota 失败。

### 1. 统一区分两类状态

前端只关心两类 quota 提示变体：

1. `quota_exceeded`
2. `system_unavailable`

映射规则：

1. `QUOTA_EXCEEDED` -> `quota_exceeded`
2. `QUOTA_CONFIG_MISSING` -> `system_unavailable`
3. `QUOTA_INTERNAL_UNAVAILABLE` -> `system_unavailable`

后续如出现新的 quota 系统类错误，只要语义仍是“当前无法确认或执行 quota”，也归到 `system_unavailable`。

当前已知需要明确纳入 `system_unavailable` 的错误码包括：

1. `QUOTA_CONFIG_MISSING`
2. `QUOTA_INTERNAL_UNAVAILABLE`
3. `QUOTA_INTERNAL_INVALID_RESPONSE`
4. `DB_UNAVAILABLE`
5. `QUOTA_LOCK_TIMEOUT`
6. `QUOTA_LOCK_UNAVAILABLE`
7. `QUOTA_CHECK_ERROR`
8. `QUOTA_GRANT_ERROR`

### 2. 统一能力标题而不是只显示 quota type

展示标题按“当前失败的具体能力”来写，不直接只显示 quota type。

例如：

1. 聊天普通问答：`普通问答次数已用完`
2. 聊天文件问答：`文件问答次数已用完`
3. 查看原文：`查看原文次数已用完`
4. 全文总结：`全文总结次数已用完`
5. 翻译：`翻译次数已用完`

而明细里再写：

- `当前消耗配额：文档辅助`
- `当前消耗配额：文件问答`

这样兼顾“用户看到的是当前能力”与“系统真实扣的是哪个额度池”。

### 3. 展示形式统一为卡片式错误块

不继续使用纯字符串拼接。

统一卡片至少包含：

1. 图标区
2. 标题
3. 副文案
4. 使用量摘要
5. 恢复提示
6. 可选的多周期明细
7. 跳转动作

聊天页里，卡片应以内嵌消息块形式展示；文献辅助里，卡片应以内嵌面板块形式展示。

### 4. 系统未就绪和额度耗尽分开

`system_unavailable` 卡片不显示“已用 / 剩余”这类可能误导用户的数字，而是强调：

1. 当前配额服务未就绪
2. 系统暂时无法确认当前额度状态

同时仍保留：

- `去个人中心查看配额`

这是为了避免用户把系统问题误判为自己额度不够。

### 5. 查看原文必须改成“单次 `GET view_pdf` 加载”

查看原文这条链路不能继续拆成“`check_pdf` 探测 + `iframe` 再次请求”。

推荐策略：

1. `PdfReader` 直接发起一次带认证的 `GET /api/view_pdf/{doi}`
2. 如果响应为 `application/pdf`，前端把响应体转成 `Blob URL`，再赋给 `iframe src`
3. 如果响应为 JSON 错误，则直接解析并渲染 quota 卡片或普通错误态
4. 成功路径不再额外再发第二次 `iframe` 网络请求，避免重复计额与体验裂缝

这样才能让“查看原文”这条 `file_view` 链路和其他 quota 提示真正统一，并且前端能稳定拿到可消费的 quota 失败负载。

---

## Card Data Model

前端内部统一卡片模型如下：

```ts
type QuotaLimitCardModel = {
  variant: 'quota_exceeded' | 'system_unavailable'
  featureTitle: string
  headline: string
  description: string
  quotaType: string
  quotaName: string
  usageSummary: string
  resetText: string
  windows: Array<{
    period: string
    current: number
    limit: number
    remaining: number
    resetTime: string
  }>
  action: {
    label: '去个人中心查看配额'
    to: '/profile'
  }
}
```

约束：

1. `system_unavailable` 时，`usageSummary` 和 `windows` 可以为空
2. `quotaType` / `quotaName` 主要用于副文案和调试，不直接裸露给用户做主标题
3. `featureTitle` 由调用方提供，不能只从 `quota_type` 反推
4. 当后端没有返回 quota 明细时，允许模型退化成“仅标题 + 描述 + 动作”

---

## Copy Rules

### 1. `quota_exceeded`

标题规则：

- `<能力名>次数已用完`

副文案规则：

- `当前消耗配额：<配额池名>`

数值规则：

- `已用 X / Y，剩余 Z`

恢复规则：

- `<resetText> 恢复`

示例：

- `全文总结次数已用完`
- `当前消耗配额：文档辅助`
- `已用 20 / 20，剩余 0`
- `今日24:00 恢复`

### 2. `system_unavailable`

标题规则：

- `<能力名>暂不可用`

副文案规则：

- 优先 `当前配额服务未就绪`
- 次选 `系统暂时无法确认当前额度状态`

显示规则：

1. 不显示剩余额度
2. 不显示 `已用 / 上限`
3. 可显示 trace 信息，但不作为主视觉内容

示例：

- `查看原文暂不可用`
- `当前配额服务未就绪`

---

## Responsibility Split

### gateway

职责：

1. 维持现有 precheck 行为
2. 在 quota 失败的流式错误帧中透传与 sync body 同语义的 quota 明细
3. 同步与流式 quota 失败字段语义保持一致
4. 保持已有 `code / error / message / trace_id` 字段不变

不负责：

1. 拼最终中文展示文案
2. 决定展示标题使用“全文总结”还是“文档辅助”
3. 做样式层处理

### frontend-vue

职责：

1. 统一解析 quota 错误
2. 复用 `quota-normalization.js`
3. 生成 `QuotaLimitCardModel`
4. 提供统一组件渲染
5. 在聊天页和文献辅助接入

不负责：

1. 改 quota 扣减逻辑
2. 改 quota 配置协议

### public-service

职责：

1. 继续作为 quota 权威数据源
2. 继续返回 quota 失败明细
3. 对 `view_pdf` 保持当前 `HEAD/GET` 都受 `file_view` 配额保护的行为

本次不需要改协议。

---

## Frontend Integration Points

### 1. 聊天页问答

接入位置：

1. `frontend-vue/src/utils/routingStatus.js`
2. `frontend-vue/src/views/Home.vue`

设计要求：

1. 仍兼容现有 markdown 错误消息路径
2. 当识别到 quota 结构化错误时，优先渲染卡片而不是普通 markdown
3. 普通文件状态错误继续走现有错误文案逻辑

### 2. 文献辅助

接入位置：

1. `frontend-vue/src/components/PdfReader.vue`

覆盖能力：

1. 查看原文
2. 全文总结
3. 翻译

设计要求：

1. 标题写具体能力名
2. 明细里写归属 quota 池
3. 系统未就绪场景用独立状态卡片
4. 查看原文必须改成单次 `GET view_pdf` 加载，不再走“存在性探测 + `iframe` 再请求”的双请求链路

---

## Error Mapping Rules

### 聊天页

聊天页收到 quota 失败时，formatter 需要结合：

1. `route`
2. `actual_mode`
3. `source_scope`
4. quota 返回的 `quota_type`

把能力名稳定映射成：

1. `普通问答`
2. `文件问答`

### 文献辅助

文献辅助不依赖路由，而由调用点显式传能力名：

1. 查看原文
2. 全文总结
3. 翻译

这样避免 formatter 猜测上下文。

### 传输层字段约束

为了避免前端继续靠猜测兼容 quota 失败，各传输方式至少要满足以下约束：

1. sync JSON 失败：
   - 顶层保留 `code / error / message / trace_id`
   - quota 明细统一放在顶层 `data`
2. stream SSE 失败：
   - error frame 保留 `code / error / message / trace_id`
   - quota 明细统一放在同一 error frame 的 `data`
3. 前端 formatter 只把 `data` 视为 quota 明细主来源
4. 若不存在 `data`，则降级为 `system_unavailable` 或普通错误展示

---

## Acceptance Criteria

### 功能验收

1. 聊天页命中 `QUOTA_EXCEEDED` 时，不再只显示“处理失败”，而是显示结构化 quota 卡片。
2. 聊天页命中 `QUOTA_CONFIG_MISSING`、`QUOTA_INTERNAL_UNAVAILABLE`、`QUOTA_INTERNAL_INVALID_RESPONSE`、`DB_UNAVAILABLE`、`QUOTA_LOCK_TIMEOUT`、`QUOTA_LOCK_UNAVAILABLE`、`QUOTA_CHECK_ERROR`、`QUOTA_GRANT_ERROR` 时，显示系统状态卡片，而不是“次数已用完”。
3. `PdfReader` 的查看原文、全文总结、翻译三条链路命中 quota 时，均显示统一风格卡片。
4. 卡片中可见具体能力名、quota 池名、恢复时间。
5. 同一类 quota 失败在同步与流式场景下都能被前端识别为同一套卡片模型。
6. 查看原文改为单次 `GET view_pdf` 加载后，quota 不足时不再出现“存在性探测通过但打开原文失败”的体验裂缝。

### 工程验收

1. `gateway` 仅做透传，不新增中文展示拼接逻辑。
2. `frontend-vue` 复用现有 quota normalization 能力，不复制 reset/window 解析。
3. 聊天页与文献辅助复用同一 formatter 和同一视觉组件。
4. stream error frame 与 sync JSON body 的 quota 明细字段位置一致，前端无需多套猜测逻辑。

### 回归验收

1. 非 quota 类错误展示不受影响。
2. 现有用户中心 quota 展示不受影响。
3. 未携带 quota 明细的旧错误响应仍能按降级路径显示。

---

## Risks And Mitigations

### 风险 1：聊天页当前是 markdown 错误块，和组件化卡片接缝不清晰

缓解：

1. 不强行把所有错误都组件化
2. 只在识别到 quota 结构化错误时切换到卡片
3. 其他错误继续走原有 markdown 流程

### 风险 2：quota 失败协议在同步和流式场景字段不完全一致

缓解：

1. 不再把 `detail`、`data.checked`、顶层 `quota` 作为长期协议目标
2. 本轮直接把 quota 明细主来源统一到顶层 `data`
3. gateway 流式预检错误帧按同一字段位置透传

### 风险 3：文献辅助当前多个入口错误承载方式不同

缓解：

1. 先统一 quota 错误卡片
2. 不在本轮顺手重构所有辅助面板的普通错误样式

---

## Recommended Rollout

建议按以下顺序落地：

1. `gateway` 透传 quota 失败明细
2. `frontend-vue` 新增 quota error formatter
3. `frontend-vue` 新增统一 `QuotaLimitCard` 组件
4. 聊天页接入
5. `PdfReader` 改成单次 `GET view_pdf` 加载并在成功时转成 `Blob URL`
6. `PdfReader` 接入统一 quota 卡片
7. 同步/流式 quota 失败协议对齐验证
8. 构建与手动回归验证

---

## Decision

本次采用：

1. 分层提示
2. 卡片式错误块
3. 动作统一为“去个人中心查看配额”
4. 文献辅助标题写具体能力名，明细里写 quota 池
5. 系统未就绪走单独状态卡片

这是当前改动面、可维护性、用户可理解性三者之间最稳妥的方案。
