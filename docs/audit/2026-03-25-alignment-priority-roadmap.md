# 2026-03-25 系统剩余未对齐项优先级路线图

## 范围

本文只盘当前 `highThinking` 仓库里，和“旧版对齐 / 系统边界收口 / 用户可感知行为”直接相关的剩余未完成项。

覆盖服务：
- `gateway`
- `public-service`
- `fastQA`
- `highThinkingQA`
- `frontend-vue`

不再重复列入已基本收口的问题：
- `fastQA` 的 `DOI -> pdf_url/reference_links/pdf_links` 边界统一
- `fastQA` 常见 DOI 链接输出契约
- `fastQA` sync ask / stream done 的引用链接一致性

---

## 结论先行

当前剩余未对齐项，按优先级建议分成 4 档：

### P0：必须先做
1. 表格 `summary` 上下文增强
2. `gateway + public-service` 公共协议兼容层收口

### P1：高优先级
3. `fastQA` 普通 `kb_qa` 多轮上下文真正接入主链
4. `highThinkingQA` authority / persistence 主链最终收口

### P2：中优先级
5. storage legacy helper / shim / 调用点继续收口
6. 同会话 mixed fastQA / highThinkingQA 的上下文分层固化

### P3：后续优化
7. `public-service` summary/memory 从空壳进化为真正可用的长期摘要
8. 网关兼容字段、旧路由、历史兼容层的进一步清理

如果只看“最直接影响用户感知”的顺序：
1. 表格 summary
2. 公共协议兼容层
3. fastQA 普通多轮上下文
4. highThinkingQA authority 收口

如果只看“最直接影响系统边界正确性”的顺序：
1. 公共协议兼容层
2. highThinkingQA authority 收口
3. storage legacy 收口
4. 上下文分层固化

---

## 一、优先级判定标准

本次排序按 4 个维度综合判断：

1. 用户感知影响
- 会不会直接造成回答不对、前端行为异常、接口打不开、体验明显退化

2. 架构边界风险
- 会不会导致职责混乱、重复写入、路径漂移、后续迁移越来越难

3. 依赖关系
- 是否是后续多项工作的前置条件

4. 旧版对齐度缺口
- 是否属于当前与旧版或目标架构相比的核心差口，而不是边缘差异

---

## 二、P0：必须先做

## P0-1 表格 `summary` 上下文增强

### 当前状态
表格 QA 的执行层已经是全表执行，但传给 LLM 的上下文仍然偏瘦，核心还是：
- 行数
- 列数
- 列名
- 前 5 条样例

这会导致模型虽然不是“只算了 5 条”，但很容易表现得像“只根据 5 条在总结”。

### 为什么是 P0
这是当前最直接的用户可见质量问题之一。

它的特点是：
- 用户一眼能看出来不对劲
- 会直接影响对系统能力的信任
- 不需要先完成更大的架构重构才能改

### 风险
- summary 看起来不基于整表
- 容易出现“样例即总体”的误导
- 会继续让用户怀疑表格链路是空壳或半成品

### 建议动作
第一阶段先增强 `summary` 上下文，不要一上来就做“大一统表格 agent”重构：
- 增加列级画像
- 增加数值列统计摘要
- 增加类别列 top-k 分布
- 把“前 5 条样例”改成“代表性样例”
- prompt 中明确区分“全表统计”与“样例”

### 相关文档
- [当前状态](/home/cqy/worktrees/highThinking/docs/audit/2026-03-25-tabular-summary-current-state.md)
- [改造建议](/home/cqy/worktrees/highThinking/docs/audit/2026-03-25-tabular-summary-context-improvement-spec.md)

---

## P0-2 `gateway + public-service` 公共协议兼容层收口

### 当前状态
公共能力的协议边界虽然基本明确了，但代码层还有几类明确缺口：
- 一部分 canonical `/api/...` path 与当前后端真实 path 不兼容
- `/api/v1/...` 兼容层未完全补齐
- 部分 gateway public proxy 覆盖不全
- trace header / query token 兼容并未完全落地

这不是“未来优化”，而是已经明确存在的协议缺口。

### 为什么是 P0
这是所有前后端联调和服务拆分的基础层。

如果这里不收口：
- 你后面继续做功能迁移，会不断踩兼容坑
- 有些公共接口在 gateway 看似存在，实际透传上游会 404 或 shape 漂移
- 服务边界会长期处于“文档一套、代码一套”状态

### 风险
- 上传、下载、健康检查、文档预览等公共能力出现假对齐
- gateway proxy 与当前后端路径不一致
- 后续 public-service 彻底接管时，风险集中爆发

### 建议动作
优先做协议收口，不要先做大规模功能重写：
- 先把已确认不兼容的 path 对齐
- 补齐 gateway public proxy 缺失路由
- 把 `/api/v1/...` 兼容层补完整
- trace header 兼容按文档落地

### 相关文档
- [公共协议对齐说明](/home/cqy/worktrees/highThinking/public-service/gateway-public-backend-protocol-alignment.md)

---

## 三、P1：高优先级

## P1-1 `fastQA` 普通 `kb_qa` 多轮上下文真正接入主链

### 当前状态
`fastQA` 已经能从 authority 读取历史与 summary，但普通 `kb_qa` 主执行链并没有真正把这份历史用进 rewrite / answer 主链。

现在的状态更像：
- 上下文已经读到了
- 但普通 fast QA 还没有真正变成成熟的多轮 QA

### 为什么是 P1
这不是接口 bug，而是效果层核心差口。

它会直接导致：
- `fastQA` 普通问答多轮能力弱
- authority 已经统一，但 fast 链路没有吃到真正收益
- 与 `highThinkingQA` 相比，上下文利用明显不对齐

### 风险
- 同一会话里 fast 问答上下文延续性差
- 用户会感觉普通 QA 不“记得前文”
- 后续做上下文缓存、summary 策略时没有稳定主链承接点

### 建议动作
优先做“最小闭环接入”：
- question rewrite 明确消费 recent turns
- answer synthesis 明确消费压缩后的历史上下文
- 区分“给 LLM 的 history”和“给 retriever 的 route/source_scope”

### 相关文档
- [上下文架构对比](/home/cqy/worktrees/highThinking/docs/audit/2026-03-24-context-architecture-comparison.md)

---

## P1-2 `highThinkingQA` authority / persistence 主链最终收口

### 当前状态
`highThinkingQA` 这条线不能再简单说“已经完全迁完”。

虽然部分能力已经往 `public-service` 靠拢，但审计和当前工作树都说明：
- 它历史上长期依赖旧 `conversation_service`
- authority / persistence 迁移是一段多阶段过程
- 旧路由、旧 storage、兼容态代码仍然很多

### 为什么是 P1
这是 `highThinkingQA` 是否真正从旧单体剥离的关键。

如果这块不收口：
- 你会一直处在“好像已经迁了，但又不是完全迁完”的状态
- 问答主链、上下文读取、聊天持久化、旧兼容路由之间很难彻底厘清

### 风险
- conversation authority 职责冲突
- 兼容路径误用
- 误判“已经 public-service 化”

### 建议动作
建议按主链优先，不要先做大面积删除：
1. 先确认 ask 主链 authority/persistence 的唯一活路径
2. 再确认旧 conversation/upload/documents 路由是否仍在承载真实业务
3. 最后再做退役/兼容标记或删除

### 相关文档
- [highThinking parity 审计](/home/cqy/worktrees/highThinking/docs/audit/2026-03-22-highthinking-parity-audit.md)
- [highThinkingQA 持久化迁移 spec](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-23-highthinkingqa-persistence-migration-spec.md)

---

## 四、P2：中优先级

## P2-1 storage legacy helper / shim / 调用点继续收口

### 当前状态
storage 这组已经开始收口，但还没达到“全系统单一权威出口”。

当前仍存在：
- legacy helper
- 兼容 shim
- 新旧调用点并存
- 部分 generation / documents / papers 路径仍带旧直连方式

### 为什么是 P2
这块很重要，但不如前面几项直接影响用户当前体验。

它更偏：
- 迁移正确性
- 架构可维护性
- 后续服务独立部署的可靠性

### 风险
- 新旧路径继续并存
- 物化/镜像/本地 fallback 语义长期漂移
- 未来排障困难

### 建议动作
- 先画清“唯一推荐出口”
- 再逐步把活链路迁过去
- 最后再处理 shim 和 legacy helper

### 相关文档
- [storage legacy 迁移未收口点](/home/cqy/worktrees/highThinking/public-service/public-modules/storage/04-legacy-paper-helper-and-call-site-migration.md)
- [总任务清单相关段落](/home/cqy/worktrees/highThinking/public-service/public-backend-extraction-task-list.md)

---

## P2-2 同会话 mixed fastQA / highThinkingQA 的上下文分层固化

### 当前状态
系统现在支持同一个 `conversation_id` 里混合 `fastQA` 和 `highThinkingQA` 回合。

这本身没问题。

真正的问题是：
- message authority 是统一的
- 但什么该进 LLM history，什么只该保留为 route/file/runtime state，还没有完全制度化
- 当前 summary 也还不够强，导致跨模式上下文策略还不够稳

### 为什么是 P2
这块是“系统正确性 + 长期稳定性”问题，不是眼前最炸的用户问题。

### 风险
- 模式间上下文污染
- 中间步骤/路由元信息误入主对话上下文
- 混合会话越做越复杂，越难维护

### 建议动作
- 固化 history / summary / runtime state / retrieval context 的边界
- 明确哪些字段只用于路由与日志，不进入 LLM prompt
- 让 fastQA / highThinkingQA 对 authority snapshot 的消费规则可预测

### 相关文档
- [混合会话与存储审计](/home/cqy/worktrees/highThinking/docs/audit/2026-03-24-mixed-conversation-and-storage-audit.md)
- [上下文架构对比](/home/cqy/worktrees/highThinking/docs/audit/2026-03-24-context-architecture-comparison.md)

---

## 五、P3：后续优化

## P3-1 `public-service` summary/memory 从空壳进化为真正可用的长期摘要

### 当前状态
`public-service` 已经有 summary 结构，但当前更多是骨架：
- `short_summary`
- `memory_facts`
- `open_threads`

这还不足以支撑成熟的长期会话压缩。

### 为什么是 P3
它很重要，但不是现在阻塞主链正确性的第一优先级。

当前先把：
- 普通多轮上下文主链接通
- authority 边界收口
- 公共协议对齐
做好，收益更直接。

### 风险
- 长对话质量上限受限
- history budget 压力大
- 后续 fast/highThinking 对 summary 的依赖仍偏弱

### 建议动作
在前面主链稳定后，再做专门的 summary/memory 设计与落地，不要和当前兼容修复混做。

### 相关文档
- [上下文架构对比](/home/cqy/worktrees/highThinking/docs/audit/2026-03-24-context-architecture-comparison.md)

---

## P3-2 gateway 兼容字段、旧路由、历史兼容层进一步清理

### 当前状态
现在很多地方为了迁移平滑，还保留了：
- legacy mode 字段兼容
- 历史 API alias
- compatibility-only payload 形态

### 为什么是 P3
这些兼容层短期内未必是 bug，本质上更偏“技术债清理”。

### 风险
- 代码噪音越来越多
- 新人难以判断活链路
- 长期维护成本上升

### 建议动作
等主链完全稳定后，再统一做兼容层清理，不要在当前迁移中与活功能修复交叉进行。

---

## 六、推荐执行顺序

### 推荐顺序 A：用户体验优先
1. 表格 `summary` 上下文增强
2. `gateway + public-service` 公共协议兼容层收口
3. `fastQA` 普通 `kb_qa` 多轮上下文接入主链
4. `highThinkingQA` authority / persistence 最终收口
5. storage legacy 收口
6. mixed 会话上下文分层固化
7. `public-service` summary/memory 增强
8. 兼容层清理

### 推荐顺序 B：架构边界优先
1. `gateway + public-service` 公共协议兼容层收口
2. `highThinkingQA` authority / persistence 最终收口
3. storage legacy 收口
4. `fastQA` 普通 `kb_qa` 多轮上下文接入主链
5. mixed 会话上下文分层固化
6. 表格 `summary` 上下文增强
7. `public-service` summary/memory 增强
8. 兼容层清理

### 当前建议
结合你现在的诉求，更推荐 **顺序 A**。

原因：
- 你当前更关注“系统能不能真实好用”
- 表格 summary 和公共协议兼容层，都是最容易被直接感知的问题
- 在这两个点收口后，再做上下文和 authority 深层收口，反馈会更稳定

---

## 七、当前建议的下一步

如果按这份优先级继续推进，建议下一步直接进入：

1. `P0-1` 表格 `summary` 上下文增强
2. 完成后立刻做定向 review 和联调
3. 然后进入 `P0-2` 公共协议兼容层收口

这两个做完以后，再回到：
- `fastQA` 普通多轮上下文
- `highThinkingQA` authority 收口

这样推进，收益最大，也最不容易出现“架构做了很多、用户还是觉得不好用”的情况。
