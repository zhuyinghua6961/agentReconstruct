# Patent Tabular and Hybrid Table FastQA Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `patent` 的 `tabular_qa` 与 `hybrid_qa` 表格分支在规划、summary 统计、上下文渲染、提示词和 table handoff 行为上对齐 `fastQA`，同时严格保证普通问答、KB-only、PDF-only 和不含 `table` 的 hybrid 路径不被改动。

**Architecture:** 保留 `patent` 现有文件问答主结构，不新建新的大模块，也不把 `fastQA` 整套实现硬搬过来。实现分五层推进：先修 `tabular/planner.py` 的误规划，再升级 `tabular/executor.py` 的 summary 统计结构，然后对齐 `tabular/renderer.py`、`tabular_context.py`、`tabular_service.py` 的 context 和 prompt，最后只在 `hybrid_qa` 且 `source_scope` 含 `table` 的分支中复用 richer table context，并用非目标回归测试锁死边界。

**Tech Stack:** Python 3, FastAPI, pytest, patent file-route services, patent tabular planner/executor/context modules

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-15-patent-tabular-hybrid-fastqa-parity-design.md`
- Existing related implementation:
  - `patent/server/patent/tabular/planner.py`
  - `patent/server/patent/tabular/executor.py`
  - `patent/server/patent/tabular/renderer.py`
  - `patent/server/patent/tabular_context.py`
  - `patent/server/patent/tabular_service.py`
  - `patent/server/patent/file_routes.py`
  - `patent/server/patent/executor.py`
  - `patent/server/patent/cache_keys.py`
- Existing regression suites:
  - `patent/tests/test_patent_tabular_planner.py`
  - `patent/tests/test_patent_tabular_executor_renderer.py`
  - `patent/tests/test_patent_tabular_context.py`
  - `patent/tests/test_patent_tabular_service.py`
  - `patent/tests/test_patent_file_routes.py`
  - `patent/tests/test_patent_executor.py`
  - `patent/tests/test_patent_kb_service.py`
  - `patent/tests/test_patent_pdf_contract.py`
  - `patent/tests/fastapi_contract/test_ask_contract.py`

## Hard Rules

1. 只允许在请求已经进入 `tabular_qa`，或已经进入 `hybrid_qa` 且 `source_scope` 含 `table` 的路径上生效本次行为变更。
2. 不允许把本次 planner、summary context、prompt version、cache version 复用到 `patent` 普通问答、KB-only、PDF-only 路径。
3. 不新增新的 `fastQA` 操作族；`按批次统计数量/按材料统计均值` 仍归入 `aggregate + group_by`，不引入新的 operation name。
4. `focus_columns` 不能基于“第一个数值列”兜底；纯概览问题允许空焦点列，保持整表视角。
5. summary 预算策略必须是确定性的，不能随机选择列或类别。
6. 每个 task 都先写红灯测试，再实现最小代码，再跑目标测试，再做 subagent review，再 commit。
7. 若需要跑测试，不要在沙箱环境跑；执行阶段必须申请提权。

## Per-Task Review Gate

每个 task 完成后都必须执行：

1. 写红灯测试
2. 运行目标测试并确认失败
3. 做最小实现
4. 重跑目标测试并确认转绿
5. 发给 reviewer subagent 做 review
6. 根据 review 修正并重跑目标测试
7. reviewer pass 后再 commit

## File Map

### Planner Layer

- Modify: `patent/server/patent/tabular/planner.py`
  Purpose: 把概览类问题默认映射到 `summary`，把 grouped 统计继续保留在 `aggregate + group_by` 里，新增 `focus_columns` 选择规则。
- Modify: `patent/tests/test_patent_tabular_planner.py`
  Purpose: 锁定 summary/aggregate/lookup/compare 边界、grouped aggregate 归属、focus columns 规则。

### Summary Executor Layer

- Modify: `patent/server/patent/tabular/executor.py`
  Purpose: 为 `summary` 构造 richer `summary_stats` 和代表性样例。
- Modify: `patent/tests/test_patent_tabular_executor_renderer.py`
  Purpose: 锁定 `summary_stats` 字段、代表性样例和非 summary 路径不回归。

### Context / Prompt Layer

- Modify: `patent/server/patent/tabular/renderer.py`
  Purpose: 在 summary 模式下优先渲染全表统计、列画像、分布摘要，而不是简单结果样例。
- Modify: `patent/server/patent/tabular_context.py`
  Purpose: 使用 richer `summary_stats` 构建 answer/synthesis context，并遵守预算与焦点列规则。
- Modify: `patent/server/patent/tabular_service.py`
  Purpose: 对齐 summary prompt 约束，让模型先讲全表分布、差异、异常。
- Modify: `patent/tests/test_patent_tabular_context.py`
  Purpose: 锁定 rich context 的结构和预算策略。
- Modify: `patent/tests/test_patent_tabular_service.py`
  Purpose: 锁定 summary prompt 和 answer context 的关键语义。

### Hybrid Table Handoff And Cache Layer

- Modify: `patent/server/patent/file_routes.py`
  Purpose: 只在包含 `table` 的 hybrid 路径上传递 richer table context，并保持非 table hybrid 不变。
- Modify: `patent/server/patent/executor.py`
  Purpose: 确保包含 `table` 的 merge 路径消费 richer table context，不污染普通问答或非 table hybrid。
- Modify: `patent/server/patent/cache_keys.py`
  Purpose: 把 planner/context/prompt version 与 table-scoped context budget 纳入 table 相关缓存键。
- Modify: `patent/tests/test_patent_file_routes.py`
  Purpose: 锁定 table-only gating、hybrid-with-table handoff、cache 隔离。
- Modify: `patent/tests/test_patent_executor.py`
  Purpose: 锁定 executor merge 边界和非目标路径不回归。

### Non-Target Regressions And Contracts

- Modify: `patent/tests/test_patent_kb_service.py`
  Purpose: 锁定 KB-only QA 不读取 table planner/prompt/cache 版本。
- Modify: `patent/tests/test_patent_pdf_contract.py`
  Purpose: 锁定 PDF-only QA 不读取 richer table context。
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
  Purpose: 锁定 tabular/hybrid with table 的对外行为提升，同时 ordinary/pdf/kb/non-table-hybrid 路径不变。

## Lock Decisions

1. 本次不引入新的 `planner` 文件或 shared abstraction，在现有 `patent/server/patent/tabular/planner.py` 内最小增量完成。
2. `focus_columns` 是 `summary` 专用的聚焦字段集合，不复用 `metric_columns` 兜底逻辑。
3. richer `summary_stats` 只在 `summary` 路径出现，`lookup` / `aggregate` / `compare` 仍维持当前精确返回。
4. `summary_stats.columns` 内部保留全量列名；渲染时再依据预算裁剪。
5. table 相关 cache version 只作用于 `tabular_qa` 和 `hybrid_qa` with `table` 的 key family。
6. 若 hybrid 最终综合阶段表格与 PDF/KB 冲突，输出并列冲突描述，不允许把表格侧统计发现吞掉。

## Task Order

1. Planner 对齐
2. Summary executor 对齐
3. Context 和 prompt 对齐
4. Hybrid table handoff 与 cache 对齐
5. 非目标路径回归和 API contract 收口
6. 整体复审与全量验证

---

### Task 1: Planner Parity And Focus Columns

**Files:**
- Modify: `patent/server/patent/tabular/planner.py`
- Modify: `patent/tests/test_patent_tabular_planner.py`

**Testing Requirement:**
- 锁死概览类问题默认走 `summary`
- 锁死 grouped 统计仍走 `aggregate + group_by`
- 锁死 `aggregate(mean)` / `lookup` / `compare` 不被 summary 吞掉
- 锁死 `focus_columns` 不会退化成第一个数值列
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_tabular_planner.py -q`

- [ ] **Step 1: 写红灯测试**

在 `patent/tests/test_patent_tabular_planner.py` 增加至少这些 case：

```python
def test_plan_tabular_query_defaults_analysis_questions_to_summary():
    plan = plan_tabular_query(question="分析这个表格有什么特点", profile=_profile())
    assert plan["operation"] == "summary"


def test_plan_tabular_query_keeps_grouped_count_inside_aggregate():
    plan = plan_tabular_query(question="按批次统计数量", profile=_profile())
    assert plan["operation"] == "aggregate"
    assert plan["aggregate"] == "count"
    assert plan["group_by"] == "批次"


def test_plan_tabular_query_keeps_lookup_when_question_is_filtered_value_lookup():
    plan = plan_tabular_query(question="材料为LFP时容量是多少", profile=_profile())
    assert plan["operation"] == "lookup"


def test_plan_tabular_query_keeps_explicit_mean_aggregate():
    plan = plan_tabular_query(question="平均容量是多少", profile=_profile())
    assert plan["operation"] == "aggregate"
    assert plan["aggregate"] == "mean"


def test_plan_tabular_query_keeps_compare_for_explicit_difference_question():
    plan = plan_tabular_query(question="比较不同材料的容量差异", profile=_profile())
    assert plan["operation"] == "compare"


def test_plan_tabular_query_focus_columns_can_include_non_numeric_columns():
    plan = plan_tabular_query(question="总结不同批次和材料分布", profile=_profile())
    assert "批次" in plan["focus_columns"]
    assert "材料" in plan["focus_columns"]


def test_plan_tabular_query_general_summary_does_not_fallback_to_first_numeric_focus_column():
    plan = plan_tabular_query(question="总结这个表格", profile=_profile())
    assert plan["operation"] == "summary"
    assert plan["focus_columns"] == []
```

- [ ] **Step 2: 运行红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_planner.py -q
```

Expected:
- FAIL
- 失败点集中在 `operation` 仍错误返回 `aggregate`、`focus_columns` 不存在或仍退化到数值列

- [ ] **Step 3: 最小实现 planner 对齐**

在 `patent/server/patent/tabular/planner.py` 中：

1. 提取显式 summary 信号识别
2. 把 grouped 统计继续映射到 `aggregate + group_by`
3. 新增 `_pick_focus_columns()`，扫描所有列名而不是只看数值列
4. 保持 `lookup` / `compare` 现有能力不被打穿

建议实现轮廓：

```python
def _pick_focus_columns(question: str, sheet: dict[str, Any], *, filters: list[dict[str, str]], group_by: str, metric_columns: list[str]) -> list[str]:
    ...


def _is_summary_intent(question: str) -> bool:
    ...


def plan_tabular_query(...):
    ...
    if explicit_compare:
        operation = "compare"
    elif filtered_lookup:
        operation = "lookup"
    elif explicit_grouped_or_aggregate:
        operation = "aggregate"
    else:
        operation = "summary"
```

- [ ] **Step 4: 重跑测试确认转绿**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_planner.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular/planner.py patent/tests/test_patent_tabular_planner.py
git commit -m "fix: align patent tabular planner with summary intent"
```

---

### Task 2: Summary Executor Rich Stats And Representative Rows

**Files:**
- Modify: `patent/server/patent/tabular/executor.py`
- Modify: `patent/tests/test_patent_tabular_executor_renderer.py`

**Testing Requirement:**
- 锁死 `summary` 返回 richer `summary_stats`
- 锁死 `numeric_summaries` 含 `median`
- 锁死 `categorical_summaries` 含 `top_values` 和 `ratio`
- 锁死 `row_count_before` / `row_count_after`、`column_profiles` 关键字段、top-value 稳定排序
- 锁死 representative rows 不是简单前 N 行
- 锁死 `lookup` / `aggregate` / `compare` 不回归
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q`

- [ ] **Step 1: 写红灯测试**

在 `patent/tests/test_patent_tabular_executor_renderer.py` 增加至少这些 case：

```python
def test_execute_tabular_plan_summary_returns_rich_summary_stats():
    result = execute_tabular_plan(workbook=_workbook(), plan={"operation": "summary", "sheet_name": "Sheet1"})
    stats = result["summary_stats"]
    assert stats["row_count"] == 6
    assert stats["column_count"] >= 3
    assert "numeric_summaries" in stats
    assert "categorical_summaries" in stats
    assert result["row_count_before"] == 6
    assert result["row_count_after"] == 6


def test_execute_tabular_plan_summary_column_profiles_expose_kind_missing_ratio_and_unique_count():
    result = execute_tabular_plan(workbook=_workbook(), plan={"operation": "summary", "sheet_name": "Sheet1"})
    profile = next(item for item in result["summary_stats"]["column_profiles"] if item["name"] == "material")
    assert set(profile.keys()) >= {"name", "kind", "missing_ratio", "unique_count"}


def test_execute_tabular_plan_summary_numeric_stats_include_median():
    result = execute_tabular_plan(workbook=_workbook(), plan={"operation": "summary", "sheet_name": "Sheet1"})
    assert result["summary_stats"]["numeric_summaries"]["capacity"]["median"] == 110.0


def test_execute_tabular_plan_summary_categorical_top_values_are_stably_sorted():
    result = execute_tabular_plan(workbook=_workbook(), plan={"operation": "summary", "sheet_name": "Sheet1"})
    top_values = result["summary_stats"]["categorical_summaries"]["material"]["top_values"]
    assert top_values == sorted(top_values, key=lambda item: (-item["count"], str(item["value"])))


def test_execute_tabular_plan_summary_uses_representative_rows_not_head_only():
    result = execute_tabular_plan(workbook=_workbook_with_extremes(), plan={"operation": "summary", "sheet_name": "Sheet1"})
    rows = result["rows"]
    assert any(row["capacity"] == 50 for row in rows)
    assert any(row["capacity"] == 280 for row in rows)
```

- [ ] **Step 2: 运行红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q
```

Expected:
- FAIL
- 失败点集中在 `summary_stats` 过薄、缺少 `median`、代表性样例仍是 `rows[:5]`

- [ ] **Step 3: 最小实现 summary executor**

在 `patent/server/patent/tabular/executor.py` 中新增或扩展：

```python
def _build_column_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ...


def _build_numeric_summaries(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ...


def _build_categorical_summaries(rows: list[dict[str, Any]], *, top_n: int = 5) -> dict[str, dict[str, Any]]:
    ...


def _build_representative_summary_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    ...
```

`summary` 返回结构至少要有：

```python
{
    "sheet_name": ...,
    "operation": "summary",
    "rows": representative_rows,
    "row_count_before": len(source_rows),
    "row_count_after": len(filtered_rows),
    "summary_stats": {
        "row_count": ...,
        "column_count": ...,
        "columns": [...],
        "column_profiles": [...],
        "numeric_summaries": {...},
        "categorical_summaries": {...},
    },
}
```

- [ ] **Step 4: 重跑测试确认转绿**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular/executor.py patent/tests/test_patent_tabular_executor_renderer.py
git commit -m "feat: enrich patent tabular summary stats"
```

---

### Task 3: Context Rendering And Prompt Parity

**Files:**
- Modify: `patent/server/patent/tabular/renderer.py`
- Modify: `patent/server/patent/tabular_context.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/tests/test_patent_tabular_context.py`
- Modify: `patent/tests/test_patent_tabular_service.py`

**Testing Requirement:**
- 锁死 summary context 先渲染统计，再渲染样例
- 锁死预算策略是确定性的
- 锁死列画像/数值列/类别列/样例数上限和稳定顺序
- 锁死 `focus_columns` 为空时保持整表视角
- 锁死 summary prompt 包含“先讲分布/差异/异常”和“样例不能代表整体”
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_tabular_context.py tests/test_patent_tabular_service.py -q`

- [ ] **Step 1: 写红灯测试**

在 `patent/tests/test_patent_tabular_context.py` 增加至少这些 case：

```python
def test_build_tabular_context_bundle_summary_renders_full_table_sections_before_examples():
    bundle = build_tabular_context_bundle(...)
    text = bundle["answer_context"]
    assert "全表统计摘要" in text
    assert "列画像摘要" in text
    assert text.index("全表统计摘要") < text.index("代表性")


def test_build_tabular_context_bundle_respects_focus_columns_and_budget():
    bundle = build_tabular_context_bundle(...)
    text = bundle["answer_context"]
    assert "focus_columns" in text
    assert "capacity" in text


def test_build_tabular_context_bundle_summary_budget_is_deterministic():
    bundle1 = build_tabular_context_bundle(...)
    bundle2 = build_tabular_context_bundle(...)
    assert bundle1["answer_context"] == bundle2["answer_context"]
    assert bundle1["synthesis_context"] == bundle2["synthesis_context"]


def test_build_tabular_context_bundle_summary_caps_profiles_categories_and_examples():
    bundle = build_tabular_context_bundle(...)
    text = bundle["answer_context"]
    assert text.count("kind=") <= 12
    assert text.count("ratio=") <= 6 * 5
    assert text.count("- 样例 ") <= 5


def test_build_tabular_context_bundle_summary_caps_synthesis_context_sections():
    bundle = build_tabular_context_bundle(...)
    text = bundle["synthesis_context"]
    assert text.count("kind=") <= 20
    assert text.count("ratio=") <= 10 * 5
    assert text.count("- 样例 ") <= 5


def test_build_tabular_context_bundle_summary_retains_focus_columns_before_other_columns():
    bundle = build_tabular_context_bundle(...)
    text = bundle["answer_context"]
    assert text.index("focus_columns: 批次, 材料") < text.index("capacity")


def test_build_tabular_context_bundle_summary_keeps_stable_order_for_profiles_and_categories():
    bundle = build_tabular_context_bundle(question="总结材料分布", workbook=_workbook_with_equal_category_counts(), ...)
    text = bundle["answer_context"]
    assert text.index("- 批次: kind=") < text.index("- 材料: kind=")
    assert text.index("LFP(") < text.index("LMFP(")


def test_build_tabular_context_bundle_general_summary_does_not_collapse_to_single_numeric_column():
    bundle = build_tabular_context_bundle(question="总结这个表格", ...)
    assert "类别列分布摘要" in bundle["answer_context"]
```

在 `patent/tests/test_patent_tabular_service.py` 增加至少这些 case：

```python
def test_build_patent_tabular_prompt_summary_requires_distribution_difference_anomaly_first():
    prompt = _build_patent_tabular_prompt(...)
    assert "先总结整体分布、差异、异常" in prompt
    assert "不能把少量样例当成整体结论" in prompt


def test_build_patent_tabular_prompt_hybrid_table_summary_preserves_table_fact_boundary():
    prompt = _build_patent_tabular_prompt(route_hint="hybrid_qa", ...)
    assert "表格结论" in prompt or "只能使用当前表格" in prompt
```

- [ ] **Step 2: 运行红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_context.py tests/test_patent_tabular_service.py -q
```

Expected:
- FAIL
- 失败点集中在 context 仍为旧式“统计摘要 + 代表性行”，prompt 仍未强制整体分布/差异/异常

- [ ] **Step 3: 最小实现 context 和 prompt**

在 `patent/server/patent/tabular/renderer.py` 中：

1. 给 `summary` 单独渲染全表统计摘要、列画像、数值列摘要、类别列摘要
2. 用 `focus_columns` 控制可见列，但当其为空时保留整表视角

在 `patent/server/patent/tabular_context.py` 中：

1. 直接消费 richer `summary_stats`
2. 严格执行预算：
   - answer context: 12 个列画像 / 8 个数值列 / 6 个类别列 / 5 行样例
   - synthesis context: 20 个列画像 / 12 个数值列 / 10 个类别列 / 5 行样例

在 `patent/server/patent/tabular_service.py` 中更新 summary prompt：

```python
intro += " 对于概览类问题，优先根据全表统计摘要作答，先总结整体分布、差异、异常，再引用少量代表性样例举例。不能把少量样例当成整体结论。"
```

- [ ] **Step 4: 重跑测试确认转绿**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_context.py tests/test_patent_tabular_service.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular/renderer.py patent/server/patent/tabular_context.py patent/server/patent/tabular_service.py patent/tests/test_patent_tabular_context.py patent/tests/test_patent_tabular_service.py
git commit -m "fix: align patent tabular summary context with fastqa"
```

---

### Task 4: Hybrid Table Handoff And Table-Scoped Cache Versioning

**Files:**
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_executor.py`

**Testing Requirement:**
- 锁死 richer table context 只在 `hybrid_qa` 且含 `table` 的路径上传递
- 锁死 `pdf+table` 与 `pdf+table+kb` 都复用 richer table context
- 锁死 `pdf+kb` 或其他不含 `table` 的 hybrid 不受影响
- 锁死 table cache version 不会污染普通 QA、KB-only、PDF-only
- 锁死 table cache version 会 bust 掉旧的浅答案缓存
- 锁死对外 payload shape 保持兼容
- 锁死表格与 PDF/KB 冲突时保留并列结构，而不是吞掉表格统计
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_file_routes.py tests/test_patent_executor.py -q`

- [ ] **Step 1: 写红灯测试**

在 `patent/tests/test_patent_file_routes.py` 增加至少这些 case：

```python
def test_hybrid_with_table_uses_richer_table_context_for_synthesis():
    result = dispatch_patent_file_route(...)
    assert "全表统计摘要" in result["metadata"]["table_evidence_context"] or result["metadata"]["table_answer_context_chars"] > len(result["metadata"]["table_evidence_context"])


def test_hybrid_with_table_and_kb_uses_richer_table_context_for_synthesis():
    result = dispatch_patent_file_route(contract=_pdf_table_kb_contract(), ...)
    assert result["source_scope"] == "pdf+table+kb"
    assert result["metadata"]["synthesis_contract"]["source_scope"] == "pdf+table+kb"


def test_hybrid_without_table_does_not_pick_up_table_prompt_or_cache_version():
    result = dispatch_patent_file_route(contract=_pdf_kb_contract(), ...)
    assert result["source_scope"] == "pdf+kb"
    assert "table" not in str(result["metadata"])


def test_table_scoped_cache_version_does_not_change_pdf_only_key_family():
    assert build_file_route_cache_key(route="pdf_qa", ...) == expected_pdf_key


def test_table_scoped_cache_version_busts_old_tabular_cache_payload():
    old_key = build_file_route_cache_key(route="tabular_qa", runtime_signature={"prompt_version": "old"}, ...)
    new_key = build_file_route_cache_key(route="tabular_qa", runtime_signature={"prompt_version": "new"}, ...)
    assert old_key != new_key


def test_hybrid_with_table_keeps_public_payload_shape_compatible():
    result = dispatch_patent_file_route(...)
    assert set(result.keys()) >= {"answer_text", "metadata", "route", "source_scope"}
```

在 `patent/tests/test_patent_executor.py` 增加至少这些 case：

```python
def test_executor_merge_uses_richer_table_context_only_when_source_scope_contains_table():
    result = executor.execute(_hybrid_contract("pdf+table"))
    assert result["source_scope"] == "pdf+table"


def test_executor_merge_uses_richer_table_context_for_pdf_table_kb():
    result = executor.execute(_hybrid_contract("pdf+table+kb"))
    assert result["source_scope"] == "pdf+table+kb"


def test_executor_pdf_only_path_is_unchanged_by_table_parity_versions():
    result = executor.execute(_pdf_only_contract())
    assert result["source_scope"] == "pdf"


def test_executor_ordinary_qa_dispatch_is_unchanged_by_table_parity_versions():
    result = executor.execute(_ordinary_qa_contract())
    assert result["route"] not in {"tabular_qa", "hybrid_qa"} or result.get("source_scope") in {None, "kb", "pdf"}


def test_executor_ordinary_qa_result_does_not_expose_table_parity_metadata():
    result = executor.execute(_ordinary_qa_contract())
    normalized = str(result)
    assert "table_answer_context_chars" not in normalized
    assert "table_evidence_context" not in normalized
    assert "summary_context_version" not in normalized
```

- [ ] **Step 2: 运行红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_file_routes.py tests/test_patent_executor.py -q
```

Expected:
- FAIL
- 失败点集中在 richer table context 尚未只在 table 分支传递、cache key 尚未隔离版本

- [ ] **Step 3: 最小实现 hybrid handoff 和 cache**

在 `patent/server/patent/file_routes.py` 中：

1. 只有 `source_scope` 包含 `table` 时才把 richer table answer/synthesis context 送入 hybrid 后续阶段
2. 对 `pdf+kb`、`pdf`、`kb` 等路径不注入本次版本字段

在 `patent/server/patent/executor.py` 中：

1. 只在 table merge 路径读取 richer table context
2. 若 table 和 PDF/KB 综合阶段结论冲突，保留并列冲突表达

在 `patent/server/patent/cache_keys.py` 中：

1. 将 `planner_version`、`summary_context_version`、`prompt_version`、`table_context_budget` 纳入 table-scoped key family
2. 不改变普通 QA、KB-only、PDF-only 的 key family

- [ ] **Step 4: 重跑测试确认转绿**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_file_routes.py tests/test_patent_executor.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/file_routes.py patent/server/patent/executor.py patent/server/patent/cache_keys.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "fix: scope patent table parity to hybrid table routes only"
```

---

### Task 5: Non-Target Regressions And FastAPI Contract Hardening

**Files:**
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_kb_service.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: production files only if a regression test exposes remaining leakage

**Testing Requirement:**
- 锁死普通问答/shared dispatch 不受影响
- 锁死普通问答、KB-only、PDF-only 不受影响
- 锁死 `tabular_qa` 与 `hybrid_qa` with `table` 的最终 answer 更接近 fastQA 风格
- 锁死对外 API contract 不破坏
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_executor.py tests/test_patent_kb_service.py tests/test_patent_pdf_contract.py tests/fastapi_contract/test_ask_contract.py -q`

- [ ] **Step 1: 写红灯测试**

在 `patent/tests/test_patent_executor.py`、`patent/tests/test_patent_kb_service.py`、`patent/tests/test_patent_pdf_contract.py`、`patent/tests/fastapi_contract/test_ask_contract.py` 增加至少这些 case：

```python
def test_executor_ordinary_qa_path_does_not_pick_up_tabular_summary_versions():
    result = executor.execute(_ordinary_qa_contract())
    assert result["route"] not in {"tabular_qa", "hybrid_qa"} or result.get("source_scope") in {None, "kb", "pdf"}
    assert "table_evidence_context" not in str(result)
    assert "prompt_version" not in str(result) or "table" not in str(result)


def test_kb_only_service_does_not_depend_on_tabular_parity_versions():
    result = service.execute(_kb_only_contract())
    assert result["source_scope"] == "kb"


def test_pdf_only_contract_does_not_emit_table_summary_sections():
    payload = build_pdf_contract(...)
    assert payload.source_scope == "pdf"


def test_http_sync_tabular_summary_route_answers_from_full_table_summary_not_single_mean(monkeypatch, tmp_path):
    response = client.post("/api/ask", json=_tabular_payload(question="分析这个表格有什么特点"))
    body = response.json()
    assert "中位数" in body["final_answer"] or "分布" in body["final_answer"] or "异常" in body["final_answer"]


def test_http_sync_pdf_only_route_remains_unchanged_by_table_parity(monkeypatch, tmp_path):
    response = client.post("/api/ask", json=_pdf_payload())
    body = response.json()
    assert body["route"] == "pdf_qa"


def test_http_sync_hybrid_pdf_kb_route_remains_unchanged_by_table_parity(monkeypatch, tmp_path):
    response = client.post("/api/ask", json=_hybrid_payload("pdf+kb"))
    body = response.json()
    assert body["route"] == "hybrid_qa"
    assert body["source_scope"] == "pdf+kb"


def test_http_sync_hybrid_pdf_table_kb_route_keeps_richer_table_context_and_public_shape(monkeypatch, tmp_path):
    response = client.post("/api/ask", json=_hybrid_payload("pdf+table+kb"))
    body = response.json()
    assert body["route"] == "hybrid_qa"
    assert body["source_scope"] == "pdf+table+kb"
    assert set(body.keys()) >= {"final_answer", "metadata", "route", "source_scope"}


def test_http_sync_ordinary_qa_route_remains_unchanged_by_table_parity(monkeypatch):
    response = client.post("/api/ask", json=_normal_payload())
    body = response.json()
    assert body.get("route") not in {"tabular_qa", "hybrid_qa"} or body.get("source_scope") in {None, "kb", "pdf"}
    assert "table_evidence_context" not in str(body)
    assert "summary_context_version" not in str(body)
```

- [ ] **Step 2: 运行红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_executor.py tests/test_patent_kb_service.py tests/test_patent_pdf_contract.py tests/fastapi_contract/test_ask_contract.py -q
```

Expected:
- FAIL
- 至少有一部分失败在 table summary 最终回答仍然过浅，或 table parity 版本字段泄漏到非目标路径

- [ ] **Step 3: 做最小补丁修剩余泄漏**

只有在红灯测试暴露缺口时，才允许继续修改以下生产文件：

```python
patent/server/patent/tabular_service.py
patent/server/patent/file_routes.py
patent/server/patent/executor.py
patent/server/patent/cache_keys.py
```

修补原则：

1. 优先修 table-only gating
2. 其次修 prompt/context 泄漏
3. 最后才修 contract 边角问题

- [ ] **Step 4: 重跑测试确认转绿**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_executor.py tests/test_patent_kb_service.py tests/test_patent_pdf_contract.py tests/fastapi_contract/test_ask_contract.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/tests/test_patent_executor.py patent/tests/test_patent_kb_service.py patent/tests/test_patent_pdf_contract.py patent/tests/fastapi_contract/test_ask_contract.py patent/server/patent/tabular_service.py patent/server/patent/file_routes.py patent/server/patent/executor.py patent/server/patent/cache_keys.py
git commit -m "test: lock patent non-table regressions for tabular parity"
```

---

### Task 6: Overall Review And Acceptance

**Files:**
- No new planned production files
- Review all touched files from Tasks 1-5

**Testing Requirement:**
- 全量跑通本次相关回归矩阵
- subagent 最终 review pass
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_tabular_planner.py tests/test_patent_tabular_executor_renderer.py tests/test_patent_tabular_context.py tests/test_patent_tabular_service.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/test_patent_kb_service.py tests/test_patent_pdf_contract.py tests/fastapi_contract/test_ask_contract.py -q`

- [ ] **Step 1: 跑整套回归**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_planner.py tests/test_patent_tabular_executor_renderer.py tests/test_patent_tabular_context.py tests/test_patent_tabular_service.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/test_patent_kb_service.py tests/test_patent_pdf_contract.py tests/fastapi_contract/test_ask_contract.py -q
```

Expected:
- PASS

- [ ] **Step 2: 发最终 reviewer subagent**

Reviewer 关注点：

1. summary 规划是否默认正确
2. richer summary 是否只限 table paths
3. non-target regressions 是否充分
4. cache/versioning 是否隔离

- [ ] **Step 3: 根据 review 修正并重跑整套回归**

如果 reviewer 有 finding：

1. 修正最小代码
2. 重跑整套回归
3. 再次发 reviewer，直到 pass

- [ ] **Step 4: Final Commit**

```bash
git status --short
git add <all touched patent files for this plan>
git commit -m "fix: align patent table behavior with fastqa summaries"
```
