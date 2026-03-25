# 表格 Summary 模式当前实现分析

日期：2026-03-25
范围：`fastQA/app/modules/qa_tabular/*`
目的：说明当前表格问答中，`summary` 场景到底是“全表执行”还是“只基于 5 条样例回答”，并明确真正传给 LLM 的上下文内容

---

## 1. 结论摘要

当前表格问答的 `summary` 模式不是“只执行前 5 条数据”。

真实行为是：

1. 后端先对整张表完成读取和执行
2. 然后把压缩后的执行结果传给 LLM 生成自然语言答案
3. 这个压缩后的上下文里包含：
- 全表行数
- 全表列数
- 全部列名
- 前 5 条样例记录
4. 因此：
- 计算层是“全表执行”
- 但 LLM 看到的具体数据样本只有前 5 条

这也是用户会感觉“模型像只根据 5 条数据在分析”的根本原因。

---

## 2. 执行链路

### 2.1 表格文件是按“全表加载”处理的

在 `fastQA/app/modules/qa_tabular/service.py` 中，表格问答流程是：

1. 加载工作簿
2. 做 schema/profile 分析
3. planner 识别用户问题对应的操作类型
4. executor 执行
5. renderer 构造 LLM prompt
6. LLM 输出自然语言答案

关键点在于：
- `load_workbook_cached()` 会把整个 CSV/Excel 工作表读成 dataframe
- planner 和 executor 操作的输入都是这个完整 dataframe

因此，底层不是“只取 5 条记录做计算”。

### 2.2 服务层日志明确记录的是全表执行结果

在 `fastQA/app/modules/qa_tabular/service.py` 中，执行完成后会产生步骤日志：

- `🧮 已完成全表执行，得到 {row_count_after} 条结果记录`

这里使用的是：
- `execution_result['row_count_after']`

这说明服务层默认认为执行结果是针对全表计算后的结果，而不是 5 条样例。

---

## 3. `summary` 模式到底返回了什么

### 3.1 executor 返回的数据结构

在 `fastQA/app/modules/qa_tabular/executor.py` 中：

当 `operation == "summary"` 时，返回结构大致包含：

1. `row_count_before`
2. `row_count_after`
3. `summary_stats`
- `row_count`
- `column_count`
- `columns`
4. `result_rows`
- 只保留前 5 条

也就是说，`summary` 模式并没有把完整表内容装进 `result_rows`。

### 3.2 关键限制点

当前代码中：

- `result["result_rows"] = _to_records(filtered_frame, limit=5)`

这意味着：
- 即使表格里有 100 行、1000 行、50000 行
- 传给渲染层的明细样例也只会有前 5 条

这不是 planner 决定的，而是 executor 在 `summary` 场景里固定裁剪的。

---

## 4. LLM prompt 当前实际拿到了什么

### 4.1 renderer 会把执行结果转成文本上下文

在 `fastQA/app/modules/qa_tabular/renderer.py` 中，`build_tabular_result_context()` 会把执行结果渲染成 prompt 上下文。

当前上下文包含：

1. 文件名
2. 工作表名
3. 操作类型
4. 过滤前行数
5. 过滤后行数
6. `summary_stats`
7. `结果样例`
8. 最多 5 条 warning

其中 `结果样例` 使用：
- `_render_rows(result_rows, limit=5)`

也就是说，即使 executor 里未来放了更多 `result_rows`，当前 renderer 也仍然只展示前 5 条。

### 4.2 `summary_stats` 里有什么

当前 `summary` 模式的 `summary_stats` 只有：

1. `row_count`
2. `column_count`
3. `columns`

它没有包含：

1. 数值列的均值/最小值/最大值
2. 类别列的分布
3. 缺失值比例
4. 异常值概览
5. 关键字段 top-k 分布
6. 时间列范围
7. 多列之间的显著关系

因此，对 LLM 来说：
- 它知道表有多大
- 它知道有哪些字段
- 它只看到前 5 行具体数据

这在“总结整个表格”时信息是不够的。

---

## 5. 为什么模型会说出“只根据 5 条分析”的感觉

虽然代码没有明确输出“我只看了 5 条”，但模型会出现这种表达偏差，原因通常有三类：

### 5.1 样例被误当成总体

因为 prompt 里唯一的具体数据明细就是前 5 行，所以模型很容易把：
- “结果样例”
误解为：
- “主要分析依据”

### 5.2 缺少全表统计特征

如果没有分布、聚合、异常、类别统计，模型就缺少真正的“全表概览锚点”，只能围绕样例展开描述。

### 5.3 自然语言总结倾向会放大样例痕迹

LLM 在做 summary 时，如果输入里统计结构很少、样例文本很强，就会偏向：
- 用样例描述整体
- 把样例观察写成总体判断

这会让用户感知成“只看了 5 条数据”。

---

## 6. 当前实现的优点

当前实现不是完全错误，它有几个合理点：

1. 真正的计算是在全表完成的
2. 对大表不直接把整表原始内容塞进 prompt，避免 prompt 过长
3. 对 `count_rows` / `filter_rows` / `groupby` / `topk` 等结构化操作，已经有一定程度的结果压缩
4. 日志和步骤展示里能看到“全表执行”而不是“样例执行”

因此问题不在于“执行层没有全表算”，而在于“传给 LLM 的 summary 上下文过瘦”。

---

## 7. 当前实现的主要不足

对于“总结这个表格”这类问题，当前 `summary` 模式的上下文主要不足是：

1. 只有前 5 行样例，没有更多结构化代表性信息
2. 没有按列类型生成摘要
3. 没有识别重点指标列
4. 没有给出类别/状态/异常分布
5. 没有给出数值列的统计范围
6. 没有给出高频值/Top-K 值
7. 没有区分“样例”与“总体统计”的权重

结果是：
- prompt 形式上写着“全表执行结果”
- 但语义上更像“字段概览 + 5 条样例”

---

## 8. 最终判断

当前系统在 `summary` 模式下的真实状态应准确表述为：

- 不是“只处理了 5 条数据”
- 而是“全表执行后，只给 LLM 看了有限的结构化摘要和前 5 条样例”

所以如果用户觉得：
- “这不像是基于整个表格在总结”

这种感觉是合理的，因为 LLM 端获得的上下文确实还不够体现“全表”。

