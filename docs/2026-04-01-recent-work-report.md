# 最近工作汇报整理（2026-03-22 至 2026-04-01）

## 1. 汇报目的

这份文档用于对 `highThinking` 仓库最近一轮工作做统一汇报，重点区分三类状态：

1. 已经完成并落到代码、测试、联调链路里的工作
2. 已完成设计与实施计划、但尚未进入实现的工作
3. 并行推进中的支线工作与后续待收口问题

本文只基于当前仓库中的 `docs/` 文档、最近提交记录和已形成的设计/验证文档整理，不额外扩写未落地的结论。

---

## 2. 时间范围与总体结论

### 2.1 时间范围

- 起始：2026-03-22
- 截止：2026-04-01

### 2.2 总体结论

最近这轮工作的主线，可以概括为 5 个方向：

1. **会话 authority 迁移继续推进**
   - 目标是把 `public-service` 收口为统一的会话权威存储
   - `fastQA` 已率先完成较多 authority 化改造
   - `highThinkingQA` 也已进入同一迁移方向

2. **QA 主链路逐步收口**
   - `gateway` 成为更清晰的统一路由中心
   - `fastQA` 承接文件 QA / 混合 QA
   - `highThinkingQA` 收敛到 thinking 普通 QA

3. **用户可感知问题被持续修复**
   - DOI 渲染与引用链路问题
   - 流式输出与 markdown 保真问题
   - 聊天记录持久化/刷新恢复问题
   - 长会话前端卡顿问题

4. **配额体系完成统一建模并已落地主干**
   - `ask_query / file_qa / file_view / doc_assist`
   - `public-service` 作为统一 quota authority
   - `gateway` 负责 QA 主链 quota 编排
   - 前端开始接入统一配额受限反馈

5. **专利 `patentQA` 支线已形成较完整交付设计**
   - 已完成 Phase 1 骨架与多份交付/原文查看 spec
   - 已有部分代码提交进入主分支工作树
   - 但整体仍属于“支线持续推进中”，不能和主 QA 主线混为“全部完成”

---

## 3. 已落地的主线成果

以下内容已经不仅是文档，而是已在提交记录中体现为代码改动。

### 3.1 会话 authority 与聊天持久化主线

相关设计与计划：

- [conversation authority migration design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-22-conversation-authority-migration-design.md)
- [conversation authority migration plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-22-conversation-authority-migration.md)
- [highThinkingQA persistence migration spec](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-23-highthinkingqa-persistence-migration-spec.md)
- [highThinkingQA authority migration plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-23-highthinkingqa-authority-migration.md)

已落地结果：

1. `public-service` 已被明确为 conversation authority 的目标中心。
2. `gateway` 的目标职责被持续收敛为“接入 + 路由 + 转发”，而不是长期承担会话写入。
3. `fastQA` 和 `highThinkingQA` 都在向“执行服务直连 `public-service` authority”这一方向迁移。
4. 失败问答 assistant turn 的持久化问题已经被正式建模并完成第一阶段闭环。

对应最近提交：

- `e19a2a5 feat: migrate thinking persistence to public-service`
- `d00e7aa feat: persist failed qa terminal turns`

### 3.2 QA 失败/取消问答持久化闭环

相关文档：

- [failed-turn persistence spec](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-30-qa-failed-turn-persistence-design.md)
- [failed-turn persistence rollout plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-31-qa-failed-turn-persistence-rollout.md)
- [failed-turn persistence verification](/home/cqy/worktrees/highThinking/docs/superpowers/implementation/2026-03-31-qa-failed-turn-persistence-verification.md)

已落地结果：

1. `public-service` 新增 terminal assistant contract / materialization / read model 能力。
2. `fastQA` 与 `highThinkingQA` 失败或取消时的 assistant terminal turn 可经 authority 落库。
3. 前端刷新后能够恢复显示 failed/canceled assistant 历史消息，而不是直接消失。
4. `gateway` 保持 SSE `type="error"` + cancel code 语义，没有引入新的 transport 破坏。

自动验证结论：

- `public-service`: `85 passed`
- `fastQA`: `52/60 passed`（不同阶段文档记录略有更新，最新提交前验证为 60）
- `highThinkingQA`: `23 passed`
- `gateway`: `55 passed`
- `frontend-vue`: `17 passed`
- `vite build`: passed

这条线现在已经属于“代码已落地 + 自动验证已完成”的状态。

### 3.3 Gateway QA 路由体系重构

相关文档：

- [gateway qa routing design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-30-gateway-qa-routing-design.md)
- [gateway qa routing implementation plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-30-gateway-qa-routing-implementation.md)

已落地结果：

1. `gateway` 的 QA 路由被重新制度化为“规则优先，歧义时轻量分类器兜底”。
2. 明确了：
   - `kb_qa`
   - `pdf_qa`
   - `tabular_qa`
   - `hybrid_qa`
3. 明确了 `mode` 与 `route` 的职责分离：
   - `mode` 决定执行风格
   - `route` 决定问答数据源与链路
4. 明确了文件问答和混合问答统一走 `fastQA`，`highThinkingQA` 不承担文件链路。
5. 对“因为会话中有文件就误路由到文件 QA”的问题做了系统收口。

对应最近提交：

- `a37c61c feat: implement unified quota management`
- `8850082 feat: refine qa routing and streaming fixes`
- `cf7f005 feat: improve citation display and ask payload forwarding`

### 3.4 QA 多轮上下文与缓存主线

相关文档：

- [qa context architecture design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-24-qa-context-architecture-design.md)
- [qa context implementation plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-24-qa-context-architecture-implementation.md)
- [qa stage cache design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-24-qa-stage-cache-design.md)
- [qa stage cache implementation](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-24-qa-stage-cache-implementation.md)
- [context architecture comparison audit](/home/cqy/worktrees/highThinking/docs/audit/2026-03-24-context-architecture-comparison.md)

已落地结果：

1. QA 主链上下文不再只停留在“读取历史”，而是开始真正进入主执行链。
2. `fastQA` 各阶段缓存已经引入，并统一了较长 TTL 方向。
3. `gateway / public-service / fastQA / highThinkingQA` 的上下文职责边界被持续理顺。
4. 同会话内跨模式问答、文件/普通问答上下文分层问题已经有明确设计基线。

相关提交：

- `7d5d683 feat: add QA stage caches across services`
- `31b9a6d feat: align qa conversation context across services`
- `0c83435 feat: thread kb qa context into stage4 synthesis`
- `1b35cc9 chore: extend qa cache ttl to 12h`

### 3.5 配额管理统一模型已落地主干

相关文档：

- [quota management design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-28-quota-management-design.md)
- [quota management rollout plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-28-quota-management-rollout.md)

已落地结果：

1. 配额体系统一为 4 个用户可理解桶：
   - `ask_query`
   - `file_qa`
   - `file_view`
   - `doc_assist`
2. `public-service` 成为唯一 quota authority。
3. `gateway` 成为 QA 主链 quota 编排层。
4. 成功结果后才扣费的策略被制度化。
5. 超级用户/管理员免限，普通用户受限的模型被固定在 `user_type` 上，而不是 `role`。

对应提交：

- `a37c61c feat: implement unified quota management`
- `e10022d fix: harden quota grant lifecycle`

### 3.6 配额受限前端反馈已做统一化

相关文档：

- [quota limit feedback design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-31-quota-limit-feedback-design.md)
- [quota limit feedback rollout plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-31-quota-limit-feedback-rollout.md)

已落地结果：

1. 聊天页问答和文献辅助链路开始使用统一的 quota 失败表达。
2. `PdfReader` 中查看原文、总结、翻译等能力的 quota 提示被纳入同一产品语言。
3. `gateway` 对 quota 失败的透传字段更结构化。

对应提交：

- `d3b18ab feat: improve quota limit feedback`

### 3.7 前端长会话性能治理已落地第一阶段

相关文档：

- [frontend long conversation performance design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-31-frontend-long-conversation-performance-design.md)
- [frontend long conversation performance plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-31-frontend-long-conversation-performance-plan.md)
- [frontend long conversation performance verification](/home/cqy/worktrees/highThinking/docs/superpowers/implementation/2026-03-31-frontend-long-conversation-performance-verification.md)

已落地结果：

1. 识别出长会话卡顿不是后端瓶颈，而是前端整段会话渲染、持久化、滚动、大纲计算等叠加成本。
2. 第一阶段已收口：
   - 历史消息冻结/窗口化方向
   - 流式消息目标定位优化
   - 自动滚动 gating
   - 本地持久化节流
   - 稳定 message identity
3. 自动化前端验证已通过，人工浏览器 profiling 仍需持续补完。

对应提交：

- `7478bf4 fix(frontend): stabilize streaming render and chat persistence`

### 3.8 DOI、引用与 markdown 渲染修复

相关设计/审计散落在：

- [fastqa doi normalization boundary design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-25-fastqa-doi-normalization-boundary-design.md)
- [highthinking doi normalization boundary design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-25-highthinking-doi-normalization-boundary-design.md)
- [reference object contract audit](/home/cqy/worktrees/highThinking/docs/audit/2026-03-26-reference-object-contract-audit.md)

已落地结果：

1. DOI 解析、规范化、引用链接边界持续收口。
2. markdown 流式结束后退化的问题做了修复。
3. 表格内 DOI 渲染等用户可见问题已处理。

对应提交：

- `b8bfb26 fix: harden doi parsing across qa pipelines`
- `b0b33d6 fix: render doi links inside answer tables`
- `9bcdb94 fix: preserve markdown rendering after streaming`
- `ed584da fix: preserve markdown during doi postprocess`

---

## 4. 已完成设计与计划，但未进入实现或未完全收口的工作

这一部分在汇报时必须明确说成“已完成设计/规划”，不能说成“功能已经全部完成”。

### 4.1 PDF Reader 一键“粘贴并翻译”

相关文档：

- [clipboard translate design](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-04-01-pdf-reader-clipboard-translate-design.md)
- [clipboard translate plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-04-01-pdf-reader-clipboard-translate-plan.md)

当前状态：

- spec 和 implementation plan 已写完
- 目标是保留手动粘贴翻译，同时新增“粘贴并翻译”
- 当前仍未进入代码实现

### 4.2 上传文件选择行为优化

相关文档：

- [upload selection behavior plan](/home/cqy/worktrees/highThinking/docs/superpowers/plans/2026-03-31-upload-selection-behavior.md)

当前状态：

- 设计为“新上传文件成功后自动成为唯一选中文件”
- 仍属于前端局部交互优化项
- 汇报时应归为体验增强待收口，不应归为已交付主成果

### 4.3 若干 audit / roadmap / todo 文档

例如：

- [alignment priority roadmap](/home/cqy/worktrees/highThinking/docs/audit/2026-03-25-alignment-priority-roadmap.md)
- [retrieval and quota todo](/home/cqy/worktrees/highThinking/docs/audit/2026-03-28-retrieval-and-quota-todo.md)

这些文档的价值主要在于：

1. 帮助安排后续优先级
2. 固定已知问题
3. 明确边界与剩余工作

不应在汇报中表述为“功能已经实现”。

---

## 5. PatentQA 并行支线进展

这部分和主 QA 主线并行推进，建议在汇报里单列，不要混进主线“已交付”结果里。

### 5.1 PatentQA Phase 1 交付设计已经比较完整

相关文档：

- [patentqa delivery spec](/home/cqy/worktrees/highThinking/docs/2026-03-30-patentqa-delivery-spec.md)
- [patent original view minio/public-service spec](/home/cqy/worktrees/highThinking/docs/2026-03-31-patent-original-view-minio-public-service-spec.md)
- [patent original view implementation plan](/home/cqy/worktrees/highThinking/docs/2026-03-31-patent-original-view-implementation-plan.md)
- [patentqa implementation task breakdown](/home/cqy/worktrees/highThinking/docs/2026-03-31-patentqa-implementation-task-breakdown.md)
- [patentqa vector retrieval task breakdown](/home/cqy/worktrees/highThinking/docs/2026-03-31-patentqa-vector-retrieval-task-breakdown.md)

文档层已经明确：

1. `patentQA` 首期只承接 `mode=patent` 下的普通问答
2. 文件 QA、混合 QA 不归 `patentQA`
3. 专利原文查看统一向 `MinIO + public-service + gateway` 架构收口
4. `patentQA` 负责领域定位与 viewer link 生成，不负责直接向前端回传原文流

### 5.2 Patent 支线已有部分代码落地

从最近提交看，以下功能已经开始落地：

- `f585cd4 feat: add patent phase1 service scaffold`
- `35abd0c feat: add patent original store core`
- `b71d965 feat: add patent original view routes`
- `bac3631 feat: proxy patent original routes through public backend`

这说明专利支线已经不是纯文档，而是进入“边实现边扩协议”的阶段。

### 5.3 汇报时的正确表述

建议表述为：

- `PatentQA` 支线已形成较完整交付规格，并已有原文查看链路和服务骨架开始落地
- 但整体仍是并行推进中的子项目，不应对外表述为“专利系统已整体交付完成”

---

## 6. 当前还存在的主要风险与未收口项

### 6.1 DNS / 外部模型依赖稳定性

近期运行日志已经出现：

- `Temporary failure in name resolution`

这说明外部模型调用的网络层稳定性仍然可能影响问答主链，尤其是 `fastQA` 阶段一对外部 LLM 的依赖。

### 6.2 前端长会话性能还需要真实浏览器 profiling

虽然自动测试和 build 已通过，但以下仍需要人工验证：

1. 20+ 轮长会话下的真实交互响应
2. 浏览器 `Performance` 面板中 chunk 脚本耗时
3. 刷新恢复和右侧大纲定位体验

### 6.3 authority 迁移尚未百分之百“全服务完全收口”

当前方向已经明确，但仍不能简单表述为：

- “所有会话能力已经完全统一”

更准确的说法是：

- 主链已经明确朝 `public-service` authority 收口
- `fastQA` 和 `highThinkingQA` 的关键问答路径持续接入
- 兼容路径、旧路由、遗留调用点仍在逐步清理中

### 6.4 部分新需求仍在 spec/plan 阶段

例如：

- `PdfReader` 一键粘贴并翻译
- 上传后文件自动选择行为

这些需求已经有设计，不代表已经实现。

---

## 7. 建议汇报口径

如果要做口头或书面汇报，建议按下面这套口径来讲。

### 7.1 一句话版本

最近这轮工作的核心，是把多服务 QA 系统的路由、持久化、配额、前端稳定性和失败场景闭环逐步做实，同时把 `patentQA` 支线从纯规划推进到开始落地。

### 7.2 三段式版本

第一段：主线收口

- `gateway / public-service / fastQA / highThinkingQA` 的职责边界比之前清晰很多
- 配额、持久化、路由和上下文这几条主线都已经从“局部 patch”进入制度化阶段

第二段：用户可感知改善

- 失败问答刷新不再直接消失
- 配额失败提示更可理解
- DOI / markdown / 流式渲染问题持续修复
- 长会话性能开始系统治理

第三段：并行支线

- `patentQA` 已形成交付级规格，原文查看链和公共服务对接开始落地
- 但仍属于并行推进中，不应和主线“已交付功能”混为一谈

### 7.3 风险口径

汇报时建议主动说明：

1. authority 迁移方向已经清晰，但仍处于逐步收口过程
2. 前端长会话性能仍需更多浏览器侧实测
3. 外部模型网络稳定性仍可能影响主链
4. 一些新体验需求已完成 spec/plan，但还未开始实现

---

## 8. 可直接引用的近期提交

如果汇报需要带最近几个代表性提交，可以优先引用：

- `d00e7aa feat: persist failed qa terminal turns`
- `d3b18ab feat: improve quota limit feedback`
- `bac3631 feat: proxy patent original routes through public backend`
- `b71d965 feat: add patent original view routes`
- `7478bf4 fix(frontend): stabilize streaming render and chat persistence`
- `a37c61c feat: implement unified quota management`
- `8850082 feat: refine qa routing and streaming fixes`
- `1b35cc9 chore: extend qa cache ttl to 12h`
- `7d5d683 feat: add QA stage caches across services`
- `e19a2a5 feat: migrate thinking persistence to public-service`

---

## 9. 当前最适合继续推进的后续项

从当前文档与提交状态看，后续最适合继续推进的工作是：

1. 完成 `PdfReader` 一键“粘贴并翻译”实现
2. 继续补完前端长会话真实性能验证
3. 继续收口 authority 兼容路径与遗留调用点
4. 继续推进 `patentQA` 原文查看链路与 MinIO/public-service/gateway 联调

