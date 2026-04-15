# Patent Tabular and Hybrid Table FastQA Parity Design

**Date:** 2026-04-15

## Summary

本设计定义 `patent` 后端中 `tabular_qa` 与 `hybrid_qa` 的表格分支行为对齐方案。

目标不是泛化地把 `patent` 文件问答“变长”，也不是改造 `patent` 普通问答主链路，而是把 `patent` 内部和表格有关的规划、执行、上下文渲染、提示词行为，整体对齐到 `fastQA` 当前已经验证过的表格 QA 路径。

用户给出的典型现象是：

1. `patent` 单表分析会退化成“某个数值列均值 + 少量样例行”。
2. `fastQA` 对同一张表能先给出全表分布、均值/中位数差异、批次离散度、异常样例，再引用少量样例佐证。

这不是单一 prompt 质量问题，而是 `planner -> executor -> context -> prompt -> hybrid table handoff` 整条链路都没有对齐 `fastQA`。

本方案选择：

1. 保持改动范围限定在 `patent` 内。
2. 只覆盖 `tabular_qa` 以及 `hybrid_qa` 中包含 `table` 的路径。
3. `fastQA` 只作为参考实现，不改 `fastQA` 本身。
4. 不触碰 `patent` 普通问答、知识库 QA 主链路、PDF-only QA 主链路。

如果与已有文档 [2026-04-15-patent-tabular-hybrid-answer-depth-design.md](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-04-15-patent-tabular-hybrid-answer-depth-design.md) 有冲突，本设计在“表格行为对齐”范围内优先。

## Scope

本设计覆盖：

1. `patent/server/patent/tabular/planner.py`
2. `patent/server/patent/tabular/executor.py`
3. `patent/server/patent/tabular_context.py`
4. `patent/server/patent/tabular/renderer.py`
5. `patent/server/patent/tabular_service.py`
6. `patent/server/patent/file_routes.py`
7. `patent/server/patent/executor.py`
8. `patent` 侧与以上行为相关的单测和 FastAPI contract test

本设计不覆盖：

1. `patent` 普通问答主链路
2. `patent` 纯知识库 QA
3. `patent` PDF-only QA 的总结逻辑
4. `fastQA` 代码本身
5. 前端 UI 或 API 结构大改
6. 新的表格查询语言、跨 sheet join、图表理解

## Hard Boundaries

以下边界是强约束，后续实现不得放宽：

1. 只有在请求已经被路由为 `tabular_qa` 时，或已经被路由为 `hybrid_qa` 且 `source_scope` 明确包含 `table` 时，本设计中的行为变更才允许生效。
2. `patent` 普通问答、知识库 QA、PDF-only QA 不允许复用本次 planner、summary context、prompt 或 cache 版本逻辑。
3. `file_routes.py` 与 `executor.py` 中如果需要改动，只能落在现有 table branch、tabular handler、hybrid table handoff 的局部路径中，不能改 shared dispatch 判定规则。
4. cache/versioning 变更只允许作用于表格文件路由相关 fingerprint/runtime signature，不能扩大为所有 `patent` QA 的全局缓存失效。
5. 若 `hybrid_qa` 不含 `table`，其输入、行为、输出必须与改动前保持一致。

## Problem Statement

### 1. `patent` 概览类问题经常被规划成 `aggregate`

当前 `patent` planner 的关键逻辑是：

1. 只要 `_pick_metric_columns()` 找到数值列，就优先走 `aggregate`
2. `_pick_metric_columns()` 在没有命中明确列名时，会默认回退到第一个数值列

这意味着“分析这个表格”“总结这个表格”“这个表格有什么特点”这类概览类问题，即使本意是全表总结，也会被错误规划成“对第一个数值列求均值”。

而 `fastQA` 的逻辑相反：只有命中明确聚合/过滤/对比/趋势信号时才走定向操作，其他情况默认 `summary`。

### 2. `patent` 的 `summary` 执行结果缺少全表摘要能力

当前 `patent` 的 `summary` 执行只返回：

1. 前 5 行
2. `row_count`
3. 一个很薄的 `summary_stats`

缺少：

1. `row_count_before` / `row_count_after`
2. `column_count`
3. `columns`
4. `column_profiles`
5. `numeric_summaries`
6. `categorical_summaries`
7. 面向 summary 的代表性样例选择

因此即使进入 `summary`，LLM 看到的也不是“全表概览”，而只是“前几行 + 极少数统计”。

### 3. `patent` 的上下文渲染是样例优先，不是统计优先

当前 `tabular_context` 与 `tabular/renderer` 的特点是：

1. 统计摘要只渲染 `count/min/max/mean`
2. 代表性行基本是前 N 行
3. 没有中位数、分布、类别占比、缺失率
4. 没有极值样例、稀有类别样例、均匀采样样例

所以 `patent` 的表格证据上下文天然更像“查数结果说明”，而不是“整表分析摘要”。

### 4. `patent` prompt 没有对齐 `fastQA` 的 summary 约束

`fastQA` 的表格 summary prompt 有三个关键约束：

1. 优先根据全表统计摘要作答
2. 先总结整体分布、差异、异常
3. 不能把少量样例当成整体结论

`patent` 当前 prompt 没有把这三点作为硬约束，因此模型更容易围绕少量字段和样例写出偏浅的结论。

### 5. `hybrid_qa` 的表格分支没有复用一套强 summary 语义

即使单表行为修好了，如果 `hybrid_qa` 里表格子上下文仍然保留旧式弱摘要：

1. 最终 hybrid answer 仍会丢失表格分布和异常信息
2. 用户会看到“单表能讲明白，混合问答又变浅”的分裂体验

因此本设计要求 `hybrid_qa` 中只要包含 `table`，就必须复用同一套 richer table summary/context 行为。

## Goals

1. `patent` 的概览/分析类表格问题默认走 `summary`，行为与 `fastQA` 一致。
2. `patent` 的 `summary` 执行结果具备完整的全表统计语义，而不是只返回前几行。
3. `patent` 的表格上下文优先呈现全表分布、差异、异常，再展示样例。
4. `patent` 的表格 prompt 明确要求模型先做全表总结，再用样例佐证。
5. `hybrid_qa` 中的表格分支复用同一套 richer summary/context 语义。
6. 非 summary 的 lookup/aggregate/compare 行为继续保留其精确性。
7. 对外 API contract 尽量保持兼容，不做无关字段改名。

## Non-Goals

1. 不把 `patent` 普通问答改成 `fastQA`。
2. 不把 `patent` 所有文件问答都改成统一大模型合成工程。
3. 不在本次设计中重写 KB 检索或 PDF 主链路。
4. 不要求 `patent` 完整复刻 `fastQA` 的全部操作集，例如 trend/topk/compound。
5. 不为大表引入全文塞给模型的粗暴方案。

## FastQA Reference Behaviors

本设计只采纳 `fastQA` 中与当前问题直接相关的行为：

1. 默认把非显式定向问题识别为 `summary`
2. `summary` 输出完整 `summary_stats`
3. `summary_stats` 至少包含：
   - `row_count`
   - `column_count`
   - `columns`
   - `column_profiles`
   - `numeric_summaries`
   - `categorical_summaries`
4. 代表性样例不是前几行，而是：
   - 数值列极值样例
   - 类别列稀有值/常见值样例
   - 均匀采样样例
5. prompt 明确要求先讲整体分布、差异、异常
6. summary 上下文把“统计摘要”和“代表性样例”区分开，不让样例冒充整体

不强制照搬的部分：

1. `fastQA` 的全部操作名和内部字段命名
2. `trend` / `topk` / `compound` 等当前 `patent` 并不需要的操作族
3. 与 `fastQA` 普通问答或其他模块耦合的实现细节

## Options Considered

### Option A: 只改 prompt

优点：

1. 改动最小
2. 不会动 planner/executor

缺点：

1. 错误的 `aggregate` 规划仍然存在
2. LLM 仍拿不到中位数、类别分布、缺失率等关键统计
3. 混合问答里的表格上下文仍然偏弱

结论：

不足以解决根因。

### Option B: 只改 planner，让概览问题进入 `summary`

优点：

1. 能消除最明显的“均值误规划”
2. 风险低于全链路改造

缺点：

1. `summary` 执行结果仍然很薄
2. context/prompt 仍然不强调全表分布分析
3. hybrid table branch 仍会沿用弱上下文

结论：

能缓解，但不能达到“行为对齐 fastQA”。

### Option C: 按四层对齐 `fastQA`

四层指：

1. planner
2. summary executor
3. context/renderer/prompt
4. hybrid table handoff

优点：

1. 直接对齐用户在意的实际行为
2. 不需要碰普通问答主链路
3. 单表与混合问答表格分支能保持一致

缺点：

1. 改动点比单纯修 prompt 更大
2. 需要补充更多测试和缓存隔离

结论：

推荐采用 Option C。

## Recommended Design

### 1. Planner 对齐：概览类问题默认走 `summary`

`patent/server/patent/tabular/planner.py` 调整原则：

1. 不再以“是否存在任意数值列”作为 `aggregate` 触发条件
2. 只有命中显式聚合意图时才走 `aggregate`
3. 只有命中显式查值意图且伴随过滤条件时才走 `lookup`
4. 只有命中显式对比意图且存在可用比较轴时才走 `compare`
5. 其他情况默认走 `summary`

需要新增或补齐的 summary 信号包括：

1. `分析`
2. `总结`
3. `概述`
4. `概括`
5. `特点`
6. `规律`
7. `分布`
8. `异常`
9. `整体`
10. `总体`

实现要求：

1. `_pick_metric_columns()` 仍可保留，用于 summary 的 focus columns 或非 summary 操作
2. 但 `_pick_metric_columns()` 不能再单独决定 `operation`
3. `plan` 中可以新增 `focus_columns`，供后续 summary 渲染聚焦
4. 若当前问题只是在问“这张表讲了什么/有什么特点”，没有显式聚合词，则必须返回 `summary`
5. 本设计不新增 `fastQA` 风格的新操作族；“按批次统计数量/按材料统计均值”这类 grouped 统计，继续归入现有 `aggregate`，仅通过 `group_by + aggregate=count|mean|sum` 表达，不引入新的 operation name
6. `compare` 仍保留给“比较/对比/差异/区别”这类显式比较问题，不承担 grouped aggregate 的职责

### 1.1 Focus Columns 规则

`focus_columns` 是 summary 路径的重点字段集合，不等同于 `metric_columns`。

生成规则必须单独定义，不能复用“第一个数值列兜底”的逻辑。

优先级如下：

1. 问题中明确点名的任意列名
2. 过滤条件涉及的列
3. `group_by` 列
4. 显式提到的 `metric_columns`
5. 若以上都为空，则不强行生成 `focus_columns`

额外约束：

1. `focus_columns` 可以同时包含数值列和非数值列
2. 不允许因为没有匹配到列名，就回退到“第一个数值列”
3. 对纯概览问题，如果没有明显焦点列，允许 `focus_columns=[]`，让 summary 保持整表视角

### 2. Executor 对齐：为 `summary` 构造全表统计

`patent/server/patent/tabular/executor.py` 的 `summary` 路径需要升级为真正的 summary executor。

`summary` 结果至少应补齐：

1. `row_count_before`
2. `row_count_after`
3. `summary_stats.row_count`
4. `summary_stats.column_count`
5. `summary_stats.columns`
6. `summary_stats.column_profiles`
7. `summary_stats.numeric_summaries`
8. `summary_stats.categorical_summaries`
9. `result_rows` 或等价字段中的代表性样例

其中：

1. `column_profiles` 至少包含 `name`、`kind`、`missing_ratio`、`unique_count`
2. `numeric_summaries` 至少包含 `min`、`max`、`mean`、`median`
3. `categorical_summaries` 至少包含 top values、count、ratio

代表性样例选择策略对齐 `fastQA`，顺序为：

1. 优先加入前两列数值列的极小值/极大值样例
2. 再加入类别列中的稀有值与高频值样例
3. 最后用均匀采样补足样例数

限制：

1. 这套 richer summary 只用于 `summary`
2. 现有 `lookup` / `aggregate` / `compare` 的精确返回行为不被 summary 逻辑污染

### 2.1 Summary Budget And Truncation Policy

为了避免宽表或高基数类别把上下文撑爆，本设计把 summary 的渲染预算写死为确定性策略：

1. `summary_stats.columns` 保留全量列名，按原表列顺序存储在内部结果中
2. `column_profiles` 内部结果保留全量，渲染到 answer/synthesis context 时按以下顺序裁剪：
   - `focus_columns`
   - 其余列按原表列顺序
   - answer context 最多渲染 12 列画像
   - synthesis context 最多渲染 20 列画像
3. `numeric_summaries` 内部结果保留所有满足数值判定的列，渲染时：
   - answer context 最多渲染 8 列
   - synthesis context 最多渲染 12 列
4. `categorical_summaries` 内部结果保留所有非数值列，但每列只保留 top 5 values
5. `categorical_summaries` 的 top values 顺序固定为：
   - `count` 降序
   - 若 `count` 相同，则按值的稳定字符串顺序
6. answer context 最多渲染 6 个类别列摘要
7. synthesis context 最多渲染 10 个类别列摘要
8. representative rows 最多保留 5 行
9. 若超出预算，必须优先保留 `focus_columns` 相关统计，其次保留原表靠前列；不能随机丢字段

### 3. Renderer 和 Context 对齐：统计优先，样例次之

`patent/server/patent/tabular/renderer.py` 和 `patent/server/patent/tabular_context.py` 需要改成 summary-aware。

对于 `summary`，上下文顺序应为：

1. 文件与工作表概览
2. 全表统计摘要
3. 列画像摘要
4. 数值列摘要
5. 类别列分布摘要
6. 代表性样例

对于非 `summary`，继续保留“执行结果优先”的结构。

必须修复的问题：

1. 不能再把前 N 行当作代表性样例
2. 不能只从 `result_rows` 反推统计摘要
3. 不能只渲染 `count/min/max/mean`

推荐做法：

1. 直接渲染 executor 提供的 `summary_stats`
2. 在 summary 模式下明确标注“下方仅展示少量代表性样例，不代表全部数据”
3. 支持 `focus_columns`，当用户只问部分字段时，summary 输出只围绕重点列收敛
4. 若 `focus_columns` 为空，则按整表视角渲染，不允许自动退化成单个数值列摘要

### 4. Prompt 对齐：强制先讲全表分布、差异、异常

`patent/server/patent/tabular_service.py` 中的表格 prompt 需要分为两类：

1. `summary`
2. 非 `summary`

`summary` prompt 需要新增硬约束：

1. 优先根据全表统计摘要作答
2. 先总结整体分布、差异、异常
3. 代表性样例只能作为举例，不能替代整体结论
4. 若问题只涉及部分字段，只围绕重点列回答
5. 若统计摘要不能支持某个判断，明确写证据不足

`hybrid_qa` 中的表格子 prompt 也要复用同一原则，只是仍保留文件边界约束：

1. 表格结论只能写成表格事实
2. PDF/KB 只能用于后续综合阶段，不能覆盖当前表格结论

### 5. Hybrid Table Handoff 对齐：混合问答复用同一套 richer table context

`hybrid_qa` 不需要在本次设计里全面重写。

但只要 `source_scope` 包含 `table`，就必须保证：

1. 表格分支产出的 `table_answer_context` 与 `table_synthesis_context` 使用 richer summary/context 版本
2. hybrid 最终合成阶段看到的表格证据，不再是旧式“少量样例 + 薄统计”
3. `pdf+table` 与 `pdf+table+kb` 至少在表格侧拥有一致的统计摘要质量

边界：

1. 若 `hybrid_qa` 不含 `table`，本设计不改变其行为
2. 若当前最终 synthesis 仍保留规则 fallback，该 fallback 也应优先从 richer table context 取材
3. `file_routes.py` 和 `executor.py` 中与 hybrid 相关的改动，只允许发生在“已确认包含表格输入”的分支；禁止改动不含 `table` 的 shared merge 行为

### 5.1 Hybrid Conflict Rule

本设计不重写 `patent` 全局证据优先级，但必须明确表格侧冲突处理规则。

规则如下：

1. 表格子阶段回答只能陈述表格事实，不能用 PDF/KB 覆盖表格结论
2. hybrid 最终综合阶段，如果表格与 PDF/KB 结论一致，可以综合表述
3. hybrid 最终综合阶段，如果表格与 PDF/KB 结论不一致，必须显式报告冲突，而不是合并成单一结论
4. 报告冲突时，至少要保留“表格显示 … / PDF 或 KB 显示 … / 需要进一步核验”这种并列结构
5. 本设计不允许因为 hybrid 综合而弱化或删除表格侧已经明确成立的统计发现

### 6. Cache And Versioning

由于表格 planning、summary_stats、prompt、context shape 都会发生变化，需要更新与表格文件路由相关的 cache fingerprint/runtime signature。

至少应纳入：

1. planner version
2. summary context version
3. prompt version
4. 与 table summary 相关的 context budget
5. 限定在 table file-route / hybrid-with-table 相关 key family，不扩散到普通 QA 缓存

目标是避免新代码命中旧缓存，继续返回旧式浅答案。

## Data Contract Changes

优先采用“内部增强、外部兼容”的方式。

建议：

1. 保留现有 `answer_text`、`metadata.table_evidence_context`、`steps` 等对外字段
2. 新增 richer `summary_stats` 内部字段，供表格 answer 和 hybrid synthesis 使用
3. 若需要新增 `focus_columns`、`row_count_before`、`row_count_after` 等字段，优先作为内部结果结构扩展，不强行暴露到最终 API 顶层

## Testing Strategy

### 1. Planner Tests

新增或补充以下断言：

1. `分析这个表格` -> `summary`
2. `总结这个表格` -> `summary`
3. `这个表格有什么特点` -> `summary`
4. `平均容量是多少` -> `aggregate(mean)`
5. `按批次统计数量` -> 定向统计操作
6. `材料为 LFP 时容量是多少` -> `lookup`
7. `按批次统计数量` -> `aggregate(count)` 且带 `group_by=批次`

### 2. Summary Executor Tests

锁定：

1. `summary_stats` 含 `column_profiles`
2. `numeric_summaries` 含 `median`
3. `categorical_summaries` 含 `ratio`
4. 代表性样例不是单纯前 5 行

### 3. Context And Prompt Tests

锁定：

1. summary context 会渲染“全表统计摘要”
2. summary context 会渲染“列画像摘要”
3. summary prompt 包含“先总结整体分布、差异、异常”
4. summary prompt 包含“不能把少量样例当成整体结论”

### 4. Hybrid Regression Tests

锁定：

1. `pdf+table` 路径会把 richer table context 传给后续合成
2. `pdf+table+kb` 路径会把 richer table context 传给后续合成
3. 不含 `table` 的 hybrid 路径行为不变

### 5. Non-Target Regression Tests

必须新增显式“未受影响”回归测试，避免 scope 只停留在文档描述层。

锁定：

1. `patent` 普通问答主链路不会加载本次 table planner/prompt/context 版本
2. `patent` KB-only QA 不会读取或写入本次 table-scoped cache key 版本
3. `patent` PDF-only QA 不会使用本次 table summary prompt 或 richer table context
4. `hybrid_qa` 中 `pdf+kb` 这类不含 `table` 的路径，其 prompt、metadata、cache fingerprint 行为保持不变
5. `file_routes.py` 与 `executor.py` 的共享入口改动，不会改变 ordinary QA、KB-only、PDF-only 的 dispatch 结果
6. table 相关 cache 版本升级后，只影响 `tabular_qa` 和 `hybrid_qa` with `table` 的 key family，不影响其他 QA 路径的缓存命中行为

### 6. Contract Tests

锁定：

1. 对外 API 仍返回兼容结构
2. 表格 summary 问答的最终答案不再只剩单列均值风格
3. 缓存变更后不会重放旧版浅答案

## Risks

1. `patent` 如果直接照搬 `fastQA` 的全部字段命名，容易扩大改动面
2. richer summary context 会增加 prompt token 消耗
3. 旧测试可能默认依赖“前几行样例”行为，需要系统性更新
4. hybrid 如果仍有独立规则裁剪点，可能吞掉 richer table context 的收益

## Risk Mitigations

1. 只对齐行为，不强制逐字逐字段照搬 `fastQA`
2. 先限制在 `summary` 路径增强，避免污染 lookup/aggregate
3. 给 planner/context/prompt 单独打版本，保证缓存隔离
4. 把 hybrid 的表格输入先对齐，再看是否还需要进一步调整最终 synthesis

## Implementation Order

1. planner 对齐
2. summary executor 对齐
3. renderer/context 对齐
4. tabular prompt 对齐
5. hybrid table handoff 对齐
6. cache/versioning 与回归测试收口

## Acceptance Criteria

满足以下条件才算完成：

1. `patent` 对“分析/总结/特点/规律”类表格问题默认走 `summary`
2. `patent` summary answer 能稳定先描述全表分布、差异、异常，再举例
3. `patent` 不再把“第一个数值列的均值”当成整表分析主结论
4. `hybrid_qa` 里只要包含 `table`，表格侧也能保留同样的 summary 语义
5. `patent` 普通问答主链路没有被改动

## Out Of Scope Follow-Up

如果后续还要继续靠近 `fastQA`，可以另开后续设计，不放进本 spec：

1. 扩充 `trend` / `topk` / `compound` 操作
2. 把更多 `fastQA` 的列聚焦逻辑迁移到 `patent`
3. 进一步统一 hybrid 最终 synthesis 的跨来源写作风格
