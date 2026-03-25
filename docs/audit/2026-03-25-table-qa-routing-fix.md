# 表格 QA 路由收缩修复说明

## 背景

用户在同一会话中先上传多篇 PDF 进行问答，随后再上传一个表格并发起“请总结这个表格/分析这个表格”的问题。

现象是系统没有把本轮执行范围收缩到表格，而是把旧的 PDF 选中状态一起带入，最终被 gateway 判成 `hybrid_qa`，导致表格总结时混入 PDF 证据。

## 根因

问题不在 `fastQA` 执行层，而在 gateway 的文件上下文解析阶段。

当前 `gateway/app/services/file_context_resolver.py` 存在两条不同路径：

1. PDF 单文件指代
- 通过 `singular_ref` 识别“这篇文献/这篇论文/这个文件/这份文件”
- 命中后会按 `selected_single -> last_focus -> latest_new_upload -> single_candidate -> clarify` 的优先级收缩到单文件

2. 表格问题
- 通过 `table_focus` 识别“表格/excel/csv/工作表”或列名/统计类关键词
- 但此前没有“这个表格/这张表/该表格”这种单表格指代路径
- 一旦命中 `table_focus`，resolver 会直接把整组 `candidate_ids` 作为执行集
- 如果这组候选里同时存在 PDF 和表格，就会路由到 `hybrid_qa`

因此，用户说“请总结这个表格”时，并没有像“请总结这篇文献”那样先收缩到单目标表格，而是错误地带上了旧 PDF。

## 参考的正确模式

这次修复参考的是现有 `pdf_qa` 的文件选择行为，而不是 `qa_pdf` 的答案生成逻辑。

可复用的正确模式是：

1. 对强单目标指代，先缩窄执行集
2. 如果当前上下文已经能唯一解析目标，就直接选中唯一目标
3. 如果不能唯一解析，再依次尝试 `last_focus`、`newly_uploaded` 等上下文
4. 仍不能唯一解析时，返回澄清，而不是盲目多文件执行

## 本次修改

修改文件：
- `gateway/app/services/file_context_resolver.py`
- `gateway/tests/test_route_decision.py`

### 1. 新增单表格指代识别

新增了 `_SINGULAR_TABLE_REFS`，覆盖以下语义：
- 这个表格
- 这张表
- 这份表格
- 该表格
- 该表
- 这个 excel / csv / 工作表
- this table / this sheet / this excel / this csv

### 2. 新增单表格收缩分支

在普通 `singular_ref` 之前新增 `table_singular_ref` 分支。

命中后，不再直接拿整组 `candidate_ids` 执行，而是先把候选集过滤为“表格子集”，再按以下优先级处理：

1. `selected_single`
- 如果当前选中集合里过滤后只剩 1 个表格，直接使用该表格

2. `last_focus`
- 如果上轮焦点文件过滤后只有 1 个表格，且上轮属于文件问答路由，则复用该表格

3. `latest_new_upload`
- 如果本轮上传集合里存在表格，则优先最新上传表格

4. `single_candidate`
- 如果整体候选表格仅剩 1 个，则自动使用该表格

5. `clarify`
- 如果仍然存在多个候选表格，则返回澄清，不自动扩成混合问答

### 3. 混合问答的影响

修复后：
- “请总结这个表格” 在存在旧 PDF 选中残留时，会优先路由到 `tabular_qa`
- “请结合知识库分析这个表格” 会保持 `table+kb`，而不是被旧 PDF 放大成 `pdf+table+kb`

也就是说，混合问答仍然保留，但只在问题语义明确要求混合时才升级。

## 新增测试

在 `gateway/tests/test_route_decision.py` 中新增了两个回归用例：

1. `test_table_singular_reference_ignores_stale_selected_pdfs`
- 验证“请总结这个表格”在 `selected_ids=[pdf,pdf,table]` 时应收缩到单表格
- 期望：`decision.route == tabular_qa`
- 期望：`selected_file_ids == [table]`
- 期望：`source_scope == table`

2. `test_table_singular_reference_with_kb_stays_table_kb_not_pdf_table_kb`
- 验证“请结合知识库分析这个表格”在旧 PDF 残留时也应只保留表格
- 期望：文件选择阶段仍是 `tabular_qa`
- 经 `RouteDecisionService` 归一化后进入 `hybrid_qa`
- 但 `source_scope` 必须是 `table+kb`，不能是 `pdf+table+kb`

## 验证结果

已通过：
- `pytest gateway/tests/test_route_decision.py -q`
- 结果：`26 passed`

未完成的补充验证：
- `pytest gateway/tests/test_qa_proxy.py gateway/tests/test_mixed_conversation_context.py -q`
- 当前默认 Python 环境缺少 `fastapi` 依赖，测试收集失败，需要切换到项目可用的 conda 环境继续跑

## 结论

本次修复是一个 gateway 层的路由收缩修复，不改 fastQA 执行逻辑。

修复后的系统行为与 `pdf_qa` 的单目标文件解析思路对齐：
- 单目标问题优先收缩执行集
- 旧选中状态只能作为候选上下文，不能机械决定最终执行集
- 多文件混合必须由问题语义明确触发，而不是被历史残留选中状态放大
