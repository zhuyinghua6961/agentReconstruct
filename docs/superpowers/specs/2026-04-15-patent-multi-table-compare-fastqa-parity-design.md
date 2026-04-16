# Patent Multi-Table Compare FastQA Parity Design

**Date:** 2026-04-15

## Summary

本设计定义 `patent` 后端中“多表对比”能力对齐 `fastQA` 的方案。

问题不是网关误路由，也不是文件未处理完成。真实现状是：

1. `patent` 已经把双表问题路由进了 `tabular_qa`
2. 两个表格文件已经 `ready`
3. 但 `patent` 当前仍按“单表 compare”去规划和执行
4. 一旦问题没有显式 `group_by`，执行层就返回 `group_by_missing`
5. 上层又把这个执行失败误报成“未拿到可读的表格原始内容”

`fastQA` 之所以能回答同一类问题，不是因为 prompt 更强，而是它有一条完整的多表 compare 链路：

1. planner 能识别 `compare_tables`
2. planner 能跨多个 workbook 对齐 sheet / metric / group / filter
3. executor 能对多个 workbook 产出统一 compare result
4. service 能把该结果直接交给 LLM 生成最终答案

本设计的目标是把这条能力链路在 `patent` 内部补齐，但严格限制在 `patent` 的表格文件问答范围内，不改 `fastQA`，不改普通问答，不改 gateway 路由决策。

## Scope

本设计覆盖：

1. `patent/server/patent/tabular/planner.py`
2. `patent/server/patent/tabular/executor.py`
3. `patent/server/patent/tabular/renderer.py`
4. `patent/server/patent/tabular_context.py`
5. `patent/server/patent/tabular_service.py`
6. `patent/server/patent/tabular/workbook_loader.py`
7. `patent/server/patent/tabular/schema_profiler.py`
8. `patent/server/patent/file_routes.py`
9. `patent/server/patent/cache_keys.py`
10. 与以上行为相关的 `patent` 单测与 FastAPI contract test

本设计不覆盖：

1. `patent` 普通问答主链路
2. `patent` KB-only QA
3. `patent` PDF-only QA
4. `gateway` 路由逻辑
5. `fastQA` 代码本身
6. 前端展示逻辑
7. 新增图表理解、跨 sheet join、SQL 式复杂查询语言

## Hard Boundaries

以下边界是强约束：

1. 不允许从 `patent` 运行时直接 import `fastQA` 模块。
2. 不允许为了双表对比去改 `patent` 普通问答、KB-only、PDF-only 的行为。
3. 只有当请求已经进入 `tabular_qa`，且选择了至少 2 个 `table` 文件，同时问题命中 compare intent 时，本次多表 compare 新能力才允许生效。
4. 非 compare 的多表问题不借本次需求顺手改语义；避免把已有多文件表格问答整体重写成另一套模式。
5. 单表问题继续沿用现有 `summary / lookup / aggregate / compare` 语义；本次新增的是多表 `compare_tables`，不是重命名所有 compare。
6. “文件不可读”兜底文案只允许用于真实读文件失败或无可用表格内容，不能再用于 planner / executor 逻辑失败。
7. 缓存指纹变更必须限制在 table-scoped file route 上，不能扩大到普通 QA 或 PDF-only 路径。

## Current State

### 已复现的真实失败链路

失败请求来自 `conversation=399`、`trace=task_7f6c362ac8b648968a7d01a6cf094647`，问题为“对比一下这两个表格”。

已确认：

1. 请求走到了 `patent` 的 `tabular_qa`
2. 选中的文件是 `file_id=[234, 233]`
3. 两个文件都处于 `processing_stage=ready`
4. 本地文件路径存在且可加载
5. 返回的 42 字符答案就是“当前未拿到可读的表格原始内容...”

根因不是文件处理失败，而是 `patent` 当前在 `_load_table_context_bundle()` 中逐文件独立执行：

1. 对每个文件单独 `profile_workbook()`
2. 对每个文件单独 `plan_tabular_query()`
3. 对每个文件单独 `execute_tabular_plan()`
4. 只要某个结果 `has_usable_tabular_result() == False` 就直接跳过

对于“对比一下这两个表格”：

1. planner 把两个文件都规划成 `operation=compare`
2. 但 plan 中没有 `group_by`
3. executor 对 `compare` 强制要求 `group_by`
4. 所以两个文件都得到 `empty_reason=group_by_missing`
5. 最终 `answer_context` 为空，落到“文件不可读”兜底

### FastQA 当前的多表 compare 行为

`fastQA` 对同类问题会走一条完全不同的链路：

1. planner 识别 `compare_tables`
2. 当 `len(profile_list) > 1` 时进行跨 profile 的 sheet 对齐
3. 如果问题带 `按/各/每个` 等 hint，则进一步解析 grouped compare
4. 如果 aggregate 不是 `count`，再解析 metric columns
5. 在需要时跨多个 profile 解析 metric / group / filter 映射
6. service 在 `operation == compare_tables` 时调用 `execute_compare_plan(workbooks=[...], plan=plan)`
7. executor 返回单一的多表 compare result，而不是逐文件孤立 result

因此 `fastQA` 在没有 `group_by` 的泛化双表问题上，仍可以走“按文件 compare count”这条可用路径，而不是失败。

## Current Differences

### 1. Planner 输入模型不同

`fastQA` planner：

1. 接收 `profile`、`profiles`、`workbook_count`
2. 可以在多 workbook 条件下切换到 `compare_tables`

`patent` planner：

1. 只接收单个 `profile`
2. 没有多 workbook 视角
3. 没有 `compare_tables` operation

### 2. Compare intent 语义不同

`fastQA`：

1. compare 关键词默认落到 `compare_tables`
2. 默认 aggregate 为 `count`
3. 只有显式均值/求和/最大/最小时才切到对应 aggregate
4. 无 `group_column` 也可以做 ungrouped compare

`patent`：

1. compare 关键词只会落到单表 `compare`
2. `compare` 被实现成“单表分组对比”
3. 没有 `group_by` 就必失败

### 3. Sheet / 列 / 过滤条件跨文件对齐能力缺失

`fastQA` planner 有：

1. `_match_sheet_across_profiles`
2. `_resolve_column_across_profiles`
3. `_resolve_columns_across_profiles`
4. `_resolve_filters_across_profiles`

`patent` planner 没有对应能力，因此：

1. 无法在多文件下识别共同 sheet
2. 无法识别不同文件中“同义列”对应关系
3. 无法在 compare 时对每个文件绑定自己的 metric/group/filter 列

### 4. Service 编排方式不同

`fastQA` service：

1. 先整体加载多个表
2. 统一 planning
3. 若是 `compare_tables`，统一执行一次 compare

`patent` service：

1. 在 `_load_table_context_bundle()` 里逐文件独立循环
2. planner / executor 都按单文件执行
3. 没有“多表 compare 统一执行”这一层

### 5. Workbook / profile 缺少跨文件 compare 所需标识

`fastQA` workbook / profile 包含 `file_id`、`file_name`、`signature`

`patent` 当前：

1. `load_workbook_cached()` 返回的数据没有 `file_id`
2. `profile_workbook()` 输出也没有 `file_id`

这使得 planner / executor 难以像 `fastQA` 一样构造 `sheet_map`、`metric_column_map`、`group_column_map`、`filter_map`。

### 6. 错误语义与日志语义错误

当前 `patent`：

1. `group_by_missing` 被吞掉
2. 执行失败和文件不可读被混成同一条用户文案
3. `_load_table_context_bundle()` 对异常和 unusable result 几乎无 warning 日志

这会让线上排查误入“文件处理”方向。

## Goals

1. `patent` 在多表 compare 问题上具备 `fastQA` 同等级的 planner -> executor -> context -> answer 基础能力。
2. 对“对比一下这两个表格”这类无显式 `group_by` 的问题，`patent` 必须可用，不再因为 `group_by_missing` 退化到“文件不可读”。
3. `patent` 支持 grouped compare 与 ungrouped compare 两种多表 compare 形态。
4. 当问题显式要求均值/求和/最大/最小时，`patent` 支持多表 metric compare。
5. planner / executor 失败时返回明确的澄清或能力边界说明，而不是误报文件不可读。
6. 缓存、日志、metadata、contract test 一并更新，确保多表 compare 的行为可追踪、可回归。

## Non-Goals

1. 不把 `patent` 单表 planner 整体改造成 `fastQA` 全量操作集。
2. 不在本次需求里引入 `trend`、`topk`、`compound` 等 `fastQA` 其他操作族。
3. 不重写 `patent` workbook loader 为 pandas 版。
4. 不修改 gateway，把请求转发给 `fastQA`。
5. 不追求逐字逐句复刻 `fastQA` 当前答案文案。

## Options Considered

### Option A: 只改 prompt，让 LLM 比较两个表格文本

优点：

1. 改动最小
2. 不用改 planner / executor

缺点：

1. 双表 compare 的结构化执行能力仍然缺失
2. 遇到列名、sheet、过滤条件映射时无法稳定工作
3. 无法解决当前 `group_by_missing -> 文件不可读` 的根因

结论：

不可接受。

### Option B: 在现有逐文件循环上做“先各自 summary，再让 LLM 自己对比”

优点：

1. service 改动相对少
2. 对简单泛化对比可能能出答案

缺点：

1. 不是 `fastQA` 的 compare_tables 语义
2. 无法做 grouped compare
3. 无法做跨文件 metric / filter / column mapping
4. 容易把两份表的独立 summary 当作 compare 结果，稳定性差

结论：

只能缓解，不能对齐功能。

### Option C: 在 patent 内新增 compare_tables 专用路径，对齐 fastQA 的 compare_tables 结构

优点：

1. 根因对齐
2. planner / executor / service / context 职责清晰
3. 多表 compare 可测试、可缓存、可追踪
4. 对单表和普通问答影响最小

缺点：

1. 改动点多于 prompt 级 patch
2. 需要补更多测试与缓存版本升级

结论：

推荐方案。

## Recommended Design

### 1. 引入 compare_tables 专用 operation，但只在多表 compare 意图下启用

设计规则：

1. 当 `selected_execution_files` 中 `table` 文件数 >= 2，且问题命中 compare 关键词时，planner 进入 `compare_tables`
2. 单表请求继续保持当前 `compare`
3. 多表非 compare 请求保持现有语义，不借此需求统一改造成 “file_ambiguous”

这样可以把本次变更严格限制在用户真实报障的双表 compare 路径。

### 2. 让 patent planner 具备 fastQA 风格的多 profile compare 能力

`patent/server/patent/tabular/planner.py` 需要扩展为：

1. 支持 `profile`、`profiles`、`workbook_count`
2. 支持 `_detect_operation()`，在 compare 关键词下返回 `compare_tables`
3. 支持 `_match_sheet_across_profiles()`
4. 支持 `_resolve_column_hint()` 与跨 profile 列映射
5. 支持 `_resolve_filters_across_profiles()`

但不是全盘照搬 `fastQA` planner。限制为：

1. 只为 `compare_tables` 引入跨 profile 能力
2. 单表 summary / lookup / aggregate / compare 尽量保持现状
3. 不额外引入 `trend` / `topk` / `compound`

### 3. 让 patent executor 具备多 workbook compare 执行能力

在 `patent/server/patent/tabular/executor.py` 中新增 `execute_compare_plan(workbooks, plan)`，语义对齐 `fastQA`：

1. ungrouped compare
   - 每个文件输出 1 行
   - 默认 `aggregate=count`
   - 结果行包含 `file_name`、`sheet_name`、`matched_count`、`value`

2. grouped compare
   - 当存在 `group_column_map` 时按 group 聚合
   - 每个 group 输出 1 行
   - 列值按文件名展开

3. metric compare
   - 当 aggregate 为 `mean/sum/max/min` 时，对每个文件各自 metric column 聚合

4. summary_stats
   - `aggregate`
   - `metric_column`
   - `metric_columns`
   - `group_column`
   - `grouped_compare`
   - `table_count`
   - `returned_count`
   - `truncated_count`

为避免打穿 `patent` 现有 consumer，本设计明确要求：

1. `patent` compare 结果继续使用现有字段名 `rows`
2. 不把 `patent` 渲染链路整体切到 `fastQA` 的 `result_rows`
3. 若内部实现为了复用逻辑临时生成 `result_rows`，必须在 executor 边界归一化回 `rows`

单表 `execute_tabular_plan()` 保持存在，不和多表 compare 混成一个大分支。

### 4. 将多表 compare 从“逐文件循环”提升为“统一规划、统一执行、统一渲染”

这是本设计的核心重构点。

当前 `_load_table_context_bundle()` 的逐文件循环必须拆分为两条路径：

1. 单文件或非多表 compare：
   - 保持现有逐文件构建 context 的模式

2. 多表 compare：
   - 先统一加载所有 selected table workbooks
   - 为每个文件构造 request-scoped compare descriptor，例如 `{file_id, file_name, workbook, profile}`
   - 统一构建 `profiles`
   - 统一 `plan_tabular_query(...)`
   - 若 `operation == compare_tables`，调用 `execute_compare_plan(workbooks=[...], plan=plan)`
   - 基于统一 compare result 构建单个 compare context bundle

这里的 compare descriptor 是 request-scoped 包装层，不允许把 `file_id` / `file_name` 直接写回 `_WORKBOOK_CACHE` 返回的共享 workbook dict；否则会污染后续 cache hit 的文件标识。

这一步如果不做，planner / executor 即使增强了，也会被 service 再次拆回逐文件孤立执行，问题仍然存在。

### 5. 明确 compare_tables 的 context 渲染与答案生成语义

`patent/server/patent/tabular/renderer.py` 与 `patent/server/patent/tabular_context.py` 需要补 compare_tables 语义：

1. context 里明确写出：
   - 对比的文件数
   - 工作表
   - compare 类型（count / mean / sum / max / min）
   - 是否 grouped compare
   - 关键 compare rows

2. compare_tables 的样例不再按“代表性样例行”解释，而是按“对比结果行”解释

3. prompt 不需要强行改成 `fastQA` 的同一句话，但必须保证：
   - LLM 看到的是统一 compare result
   - 不会再因为 answer_context 为空掉到 unreadable fallback
   - 问题是 compare 时，回答优先描述文件间差异，不把对比结果说成单文件总结

这里需要一个明确的上下文契约：

1. 单文件路径继续复用现有 `build_tabular_context_bundle(question, workbook, plan, result, ...)`
2. 多表 compare 新增专用 builder，例如 `build_compare_tabular_context_bundle(compare_tables, plan, result, ...)`
3. 不把现有 `build_tabular_context_bundle()` 的单 workbook 签名直接扩成“既吃单 workbook 又吃多个 workbook”的混合接口

原因是当前 `tabular_context.py` 明确围绕单个 `workbook` 与 `result["rows"]` 工作，直接扩签名会让单表和多表逻辑纠缠。

### 6. 改正错误语义与日志

`patent/server/patent/tabular_service.py` 需要区分三种失败：

1. 真实文件不可读
2. planner 需要澄清
3. compare 执行失败或无可用对比结果

目标行为：

1. 不再把 2 和 3 包装成“未拿到可读的表格原始内容”
2. 对 planner clarify 直接返回澄清消息
3. 对 compare execution unusable 返回明确的 compare 能力边界说明
4. 日志中记录：
   - compare_tables plan
   - compare_tables empty_reason
   - skipped file / skipped result 原因

为实现这点，`_load_table_context_bundle()` 不能继续只返回三段文本。需要升级成结构化返回契约，至少包含：

1. `status`
   - `ok`
   - `unreadable`
   - `clarification`
   - `execution_unavailable`
2. `compact_evidence_context`
3. `answer_context`
4. `synthesis_context`
5. `user_message`
6. `answer_mode`
7. `_skip_file_route_cache`
8. `log_fields`

`execute()` 必须按 `status` 分支，而不是继续仅用 `bool(answer_context)` 判断所有结果。

### 7. 在 patent 内补齐 file_id / file_name 级 compare 元信息

为了让 planner / executor 像 `fastQA` 一样构造 map，需要保证 compare 路径中能稳定拿到：

1. `file_id`
2. `file_name`
3. `sheet_name`

推荐做法：

1. 在 `tabular_service` 中维护 request-scoped compare descriptor，不直接改 cached workbook
2. compare descriptor 中显式携带 `file_id`、`file_name`、`workbook`、`profile`
3. `profile_workbook()` 输出可补 `file_id` / `file_name`，但输入对象若来自 cache，必须基于拷贝或包装层构建，不能原地改共享对象

不要求改造成 pandas workbook，只要求 compare 路径上的标识完整。

### 8. 缓存和 runtime signature 必须随 compare_tables 能力升级而升级

当前 file-route cache 指纹已经有：

1. `tabular_prompt_version`
2. `table_parity_signature.planner_version`
3. `summary_context_version`

本设计要求：

1. bump compare 相关版本号
2. 让 compare_tables 新语义必然失效旧缓存
3. 确保多表 compare 的 unavailable 旧缓存不会继续复用

若 compare 执行中出现异常或 context 构建失败，仍应保持 `_skip_file_route_cache=True`。

## File-Level Design

### `patent/server/patent/tabular/planner.py`

职责：

1. 为多表 compare 增加 compare_tables detection
2. 增加跨 profile sheet / column / filter 对齐
3. 保持单表行为最小扰动

新增或改造重点：

1. `_detect_operation()`
2. `_match_sheet_across_profiles()`
3. `_resolve_column_hint()`
4. `_resolve_column_across_profiles()`
5. `_resolve_columns_across_profiles()`
6. `_resolve_filters_across_profiles()`
7. `plan_tabular_query(..., profiles=None, workbook_count=1)`

### `patent/server/patent/tabular/executor.py`

职责：

1. 保留单表 `execute_tabular_plan()`
2. 新增多表 `execute_compare_plan()`

新增重点：

1. `_finalize_rows()`
2. 多文件循环执行
3. grouped compare 与 ungrouped compare
4. compare_tables summary_stats
5. 对外结果字段继续归一到 `rows`

### `patent/server/patent/tabular/renderer.py`

职责：

1. compare_tables 结果可用性判定
2. compare_tables 结果上下文渲染

重点：

1. `has_usable_tabular_result()` 兼容 compare_tables
2. compare_tables 渲染输出不再沿用 summary 的“代表性样例”话术

### `patent/server/patent/tabular_context.py`

职责：

1. compare_tables 的 compact / answer / synthesis context
2. 区分 summary 和 compare_tables 的上下文构造语义

重点：

1. 保留现有单 workbook builder
2. 新增 compare_tables 专用 builder，而不是让单 workbook builder 同时承担多 workbook compare

### `patent/server/patent/tabular_service.py`

职责：

1. 统一加载多个表
2. compare_tables 统一 planning/execution/context 生成
3. 修正错误语义与日志
4. answer_mode / metadata 升级

重点：

1. 不能再逐文件独立 compare
2. compare clarify / unusable 不能误报 unreadable
3. `_load_table_context_bundle()` 返回结构化状态
4. 记录 compare_tables 相关步骤和日志

### `patent/server/patent/file_routes.py`

职责：

1. runtime signature 版本升级
2. table-scoped cache 指纹正确失效
3. 允许 table compare 的 metadata 继续参与 hybrid handoff，但不主动扩 scope

### `patent/server/patent/cache_keys.py`

职责：

1. 保证 table-scoped compare 升级后的 cache miss
2. 保持非 table route fingerprint 稳定

## Error Semantics

用户可见语义需要改成以下规则：

1. 文件真不可读：
   - 继续返回“当前未拿到可读的表格原始内容...”

2. 多表 compare 需要澄清：
   - 直接返回 planner clarification，例如：
   - “多表对比时未能唯一定位工作表，请指定 sheet 名”
   - “文件 X 缺少与 Y 对应的数值列，无法执行多表对比”

3. compare 执行结果为空但文件可读：
   - 返回 compare failure / unsupported 文案
   - 不能再说文件不可读

## Test Strategy

至少补以下测试：

### Planner

1. 多表 compare 问题命中 `compare_tables`
2. 泛化 compare 默认 aggregate=`count`
3. 带“平均/均值”问题命中 metric compare
4. 带“按/各/每个”问题命中 grouped compare
5. ambiguous sheet / missing column 返回 clarify
6. 单表 compare 行为不回归

### Executor

1. ungrouped compare_tables 返回每文件 1 行
2. grouped compare_tables 返回按 group 聚合的 compare rows
3. metric compare 能输出按文件聚合值
4. truncate / warnings / summary_stats 稳定
5. 单表 `compare` 行为不回归
6. compare_tables 结果对外字段仍为 `rows`

### Renderer / Context

1. compare_tables result 会被 `has_usable_tabular_result()` 视为可用
2. compare_tables 能生成非空 `answer_context`
3. compare_tables 能生成非空 `synthesis_context`
4. 单表 builder 与 compare_tables builder 边界清晰，不共享错误输入契约

### Service

1. 多表 compare 统一 planning + execution，而不是逐文件 compare
2. `group_by_missing` 不再落到 unreadable fallback
3. planner clarify 透传给用户
4. compare execution unusable 返回 compare-specific 文案
5. compare_tables metadata / answer_mode / chars 正确
6. true unreadable / clarification / execution_unavailable 三类结果可区分
7. compare descriptor 不会污染 workbook cache 命中对象

### Cache / File Routes / Contract

1. table-scoped runtime signature 包含新版本号
2. 旧 compare cache 不会命中新语义
3. 非 table route 不暴露 table parity metadata
4. `/api/ask` 与 `/api/ask_stream` 的 tabular compare contract 正常
5. hybrid with table 非 compare 行为不回归
6. cache hit 不共享被 compare 路径污染过的 workbook 文件标识

## Risks

### 风险 1：误伤现有单表 compare

缓解：

1. compare_tables 只在多表 compare intent 生效
2. 单表 compare 测试全部保留

### 风险 2：多表非 compare 语义意外改变

缓解：

1. 本次不统一改多表非 compare 的 planner 语义
2. compare intent gating 明确写死

### 风险 3：列映射过于激进导致 compare 误配

缓解：

1. 优先显式 hint
2. 无法稳定映射时走 clarify，不猜

### 风险 4：compare 结果虽然可执行，但回答仍然过于含糊

缓解：

1. 先保证结构化 compare result 可用
2. 再通过 compare-specific context/prompt 让 LLM围绕差异作答

## Rollout Notes

实现完成后应使用真实请求回归至少两类场景：

1. “对比一下这两个表格”
2. “按某列对比这两个表格的平均值/数量”

验收标准：

1. `patent` 不再返回“未拿到可读的表格原始内容”
2. 日志中能看到 compare_tables plan / execution
3. answer_context 非空
4. 对单表与普通 QA 无明显回归
