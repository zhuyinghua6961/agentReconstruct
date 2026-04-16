# Patent Multi-Table Compare FastQA Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `patent` 的双表对比问题具备 `fastQA` 同等级的多表 compare 规划、执行、上下文和回答能力，消除当前 `group_by_missing -> 文件不可读` 的错误退化，同时严格保证普通问答、KB-only、PDF-only 和单表路径不被意外改动。

**Architecture:** 保留 `patent` 现有单表 `summary / lookup / aggregate / compare` 主体，新增只在“多表 + compare intent”下启用的 `compare_tables` 支路。实现分七个 task 推进：先补 planner 的多 profile compare 规划，再补 executor 的多 workbook compare 结果，然后新增 compare 专用 context builder，接着在 `PatentTabularService` 中引入 request-scoped compare descriptor 与结构化状态返回，最后补 cache/file-route/version 和 contract/regression 收口，并做整体复审。

**Tech Stack:** Python 3, FastAPI, pytest, patent file-route stack, patent tabular planner/executor/context modules

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-15-patent-multi-table-compare-fastqa-parity-design.md`
- FastQA reference:
  - `fastQA/app/modules/qa_tabular/planner.py`
  - `fastQA/app/modules/qa_tabular/executor.py`
  - `fastQA/app/modules/qa_tabular/service.py`
- Patent target modules:
  - `patent/server/patent/tabular/planner.py`
  - `patent/server/patent/tabular/executor.py`
  - `patent/server/patent/tabular/renderer.py`
  - `patent/server/patent/tabular_context.py`
  - `patent/server/patent/tabular_service.py`
  - `patent/server/patent/file_routes.py`
  - `patent/server/patent/cache_keys.py`
- Existing test suites:
  - `patent/tests/test_patent_tabular_planner.py`
  - `patent/tests/test_patent_tabular_executor_renderer.py`
  - `patent/tests/test_patent_tabular_context.py`
  - `patent/tests/test_patent_tabular_service.py`
  - `patent/tests/test_patent_file_routes.py`
  - `patent/tests/test_patent_executor.py`
  - `patent/tests/fastapi_contract/test_ask_contract.py`

## Hard Rules

1. 只允许在 `tabular_qa` 且已选中至少 2 个表格文件、同时问题命中 compare intent 的条件下启用 `compare_tables`。
2. 不允许 import `fastQA` 运行时代码；只允许参考其实现形状。
3. 单表 `summary / lookup / aggregate / compare` 必须保持可回归，不借本次需求整体重写。
4. 非 compare 的多表问题不顺手改造成统一 `file_ambiguous` 语义。
5. compare 路径中禁止修改 `_WORKBOOK_CACHE` 返回的共享 workbook 对象；只能使用 request-scoped compare descriptor 或拷贝。
6. patent compare 结果对外字段必须继续使用 `rows`，不能把 patent consumer 全量迁移为 `result_rows`。
7. `build_tabular_context_bundle()` 保持单 workbook 语义；多表 compare 使用新的专用 context builder。
8. `_load_table_context_bundle()` 必须升级为结构化状态返回，`execute()` 不得再只靠 `bool(answer_context)` 判定所有错误路径。
9. 所有测试命令都不要在沙箱环境跑；执行阶段必须申请提权。
10. 每个 task 都采用红灯测试 -> 最小实现 -> 目标测试转绿 -> commit 的顺序。

## File Map

### Planner Layer

- Modify: `patent/server/patent/tabular/planner.py`
  Purpose: 为多表 compare 新增 `compare_tables` 规划、跨 profile sheet/column/filter 对齐，但不打穿单表语义。
- Modify: `patent/tests/test_patent_tabular_planner.py`
  Purpose: 锁定 compare_tables planner 进入条件、grouped/ungrouped compare、clarify 和单表回归。

### Executor Layer

- Modify: `patent/server/patent/tabular/executor.py`
  Purpose: 新增多 workbook `execute_compare_plan()`，对外结果字段保持 `rows`。
- Modify: `patent/tests/test_patent_tabular_executor_renderer.py`
  Purpose: 锁定 compare_tables rows contract、summary_stats、warnings 和单表 compare 回归。

### Compare Context Layer

- Modify: `patent/server/patent/tabular/renderer.py`
  Purpose: compare_tables 结果可用性判定与 compare 结果上下文渲染。
- Modify: `patent/server/patent/tabular_context.py`
  Purpose: 新增 compare_tables 专用 context builder，保持现有单 workbook builder 不变。
- Modify: `patent/tests/test_patent_tabular_context.py`
  Purpose: 锁定 compare_tables 的 compact/answer/synthesis context 非空且语义正确。

### Service Layer

- Modify: `patent/server/patent/tabular_service.py`
  Purpose: 引入 request-scoped compare descriptor，统一多表 compare 规划/执行/上下文构建，并把 `_load_table_context_bundle()` 升级为结构化状态返回。
- Modify: `patent/tests/test_patent_tabular_service.py`
  Purpose: 锁定 true unreadable / clarification / execution_unavailable 三类状态，以及 compare descriptor 不污染 cache 对象。

### Cache / File Route Layer

- Modify: `patent/server/patent/file_routes.py`
  Purpose: bump table parity version，保证 compare_tables 新语义参与 file-route runtime signature。
- Modify: `patent/server/patent/cache_keys.py`
  Purpose: 保证 table-scoped cache 指纹随 compare_tables 语义变化而失效。
- Modify: `patent/tests/test_patent_file_routes.py`
  Purpose: 锁定 runtime signature/version/cache 边界与非 table route 隔离。
- Modify: `patent/tests/test_patent_executor.py`
  Purpose: 锁定 compare 相关 metadata 与 hybrid table handoff 不回归。

### API Contract Layer

- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
  Purpose: 锁定 `/api/ask` 与 `/api/ask_stream` 的多表 compare contract，不再返回“文件不可读”误导文案。

## Task Order

1. Planner compare_tables 能力
2. Executor 多 workbook compare
3. Compare context / renderer
4. Tabular service orchestration 与结构化状态
5. Cache/file-route/runtime signature 收口
6. FastAPI contract 与回归测试
7. 整体复审与全量验证

---

### Task 1: Add Compare-Tables Planner Path

**Files:**
- Modify: `patent/server/patent/tabular/planner.py`
- Modify: `patent/tests/test_patent_tabular_planner.py`

**Testing Requirement:**
- 锁定多表 compare 命中 `compare_tables`
- 锁定默认 aggregate=`count`
- 锁定显式均值/求和/最大/最小时进入 metric compare
- 锁定 grouped compare 与 clarify 行为
- 锁定 filtered compare 能生成 `filter_map`
- 锁定单表 compare 回归
- Run with escalation: `bash patent/scripts/test.sh tests/test_patent_tabular_planner.py -q`

- [ ] **Step 1: 写红灯 planner 测试**

在 `patent/tests/test_patent_tabular_planner.py` 增加至少这些 case：

```python
def test_plan_tabular_query_uses_compare_tables_for_multi_table_compare_intent():
    plan = plan_tabular_query(
        question="对比一下这两个表格",
        profile=_profile_a(),
        profiles=[_profile_a_with_file(101), _profile_b_with_file(102)],
        workbook_count=2,
    )
    assert plan["operation"] == "compare_tables"
    assert plan["aggregate"] == "count"


def test_plan_tabular_query_supports_multi_table_grouped_compare():
    plan = plan_tabular_query(
        question="按批次对比这两个表格的平均容量",
        profile=_profile_a(),
        profiles=[_profile_a_with_file(101), _profile_b_with_file(102)],
        workbook_count=2,
    )
    assert plan["operation"] == "compare_tables"
    assert plan["group_column_map"]
    assert plan["metric_column_map"]


def test_plan_tabular_query_returns_clarification_when_compare_sheet_is_ambiguous():
    plan = plan_tabular_query(
        question="对比这两个表格",
        profile=_multi_sheet_profile_a(),
        profiles=[_multi_sheet_profile_a(), _multi_sheet_profile_b()],
        workbook_count=2,
    )
    assert plan["needs_clarification"] is True
    assert plan["clarification_reason"] == "sheet_compare_ambiguous"


def test_plan_tabular_query_builds_filter_map_for_multi_table_compare():
    plan = plan_tabular_query(
        question="对比温度=25时这两个表格的平均容量",
        profile=_profile_a(),
        profiles=[_profile_a_with_file(101), _profile_b_with_file(102)],
        workbook_count=2,
    )
    assert plan["operation"] == "compare_tables"
    assert plan["filter_map"]
```

- [ ] **Step 2: 跑红灯**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_planner.py -q
```

Expected:
- FAIL
- 失败点集中在 `compare_tables` 不存在、multi-profile 参数不支持、clarify 原因缺失

- [ ] **Step 3: 最小实现 compare_tables planner**

在 `patent/server/patent/tabular/planner.py` 中：

1. 增加 `profiles` 与 `workbook_count` 入参
2. 新增 compare-only 的 `_detect_operation()`
3. 加入 `_match_sheet_across_profiles()`
4. 加入 `_resolve_column_hint()`、`_resolve_column_across_profiles()`、`_resolve_columns_across_profiles()`
5. 加入 `_resolve_filters_across_profiles()`
6. 只在多表 + compare intent 下返回 `compare_tables`
7. 对外保持单表路径不回归

建议骨架：

```python
def plan_tabular_query(*, question: str, profile=None, profiles=None, workbook_count: int = 1) -> dict[str, Any]:
    if workbook_count >= 2 and _is_multi_table_compare_intent(question):
        return _plan_multi_table_compare(...)
    return _plan_single_table_query(...)
```

- [ ] **Step 4: 重跑 planner 测试**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_planner.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular/planner.py patent/tests/test_patent_tabular_planner.py
git commit -m "feat: add patent multi-table compare planner"
```

---

### Task 2: Implement Multi-Workbook Compare Executor

**Files:**
- Modify: `patent/server/patent/tabular/executor.py`
- Modify: `patent/tests/test_patent_tabular_executor_renderer.py`

**Testing Requirement:**
- 锁定 ungrouped compare 每文件 1 行
- 锁定 grouped compare 按 group 展开
- 锁定 metric compare 的 `value` / 文件列输出
- 锁定对外仍使用 `rows`
- Run with escalation: `bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q`

- [ ] **Step 1: 写红灯 executor 测试**

增加至少这些 case：

```python
def test_execute_compare_plan_returns_rows_for_each_file_on_count_compare():
    result = execute_compare_plan(workbooks=[_wb_a(), _wb_b()], plan=_count_compare_plan())
    assert result["operation"] == "compare_tables"
    assert len(result["rows"]) == 2
    assert {row["file_name"] for row in result["rows"]} == {"a.csv", "b.csv"}


def test_execute_compare_plan_supports_grouped_compare():
    result = execute_compare_plan(workbooks=[_wb_a(), _wb_b()], plan=_grouped_compare_plan())
    assert result["summary_stats"]["grouped_compare"] == 1
    assert result["rows"]


def test_execute_compare_plan_keeps_patent_rows_contract():
    result = execute_compare_plan(workbooks=[_wb_a(), _wb_b()], plan=_count_compare_plan())
    assert "rows" in result
    assert "result_rows" not in result
```

- [ ] **Step 2: 跑红灯**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q
```

Expected:
- FAIL
- 失败点集中在 `execute_compare_plan` 缺失、rows contract 不满足

- [ ] **Step 3: 实现 compare executor**

在 `patent/server/patent/tabular/executor.py` 中：

1. 新增 `_finalize_rows()`
2. 新增 `execute_compare_plan(workbooks, plan)`
3. 支持：
   - `aggregate=count`
   - `aggregate in {mean, sum, max, min}`
   - grouped compare / ungrouped compare
4. 对外统一返回：

```python
{
    "operation": "compare_tables",
    "rows": [...],
    "row_count_before": 0,
    "row_count_after": len(rows),
    "summary_stats": {...},
    "warnings": [...],
}
```

- [ ] **Step 4: 重跑 executor 测试**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular/executor.py patent/tests/test_patent_tabular_executor_renderer.py
git commit -m "feat: add patent multi-table compare executor"
```

---

### Task 3: Add Dedicated Compare Context Builder

**Files:**
- Modify: `patent/server/patent/tabular/renderer.py`
- Modify: `patent/server/patent/tabular_context.py`
- Modify: `patent/tests/test_patent_tabular_context.py`
- Modify: `patent/tests/test_patent_tabular_executor_renderer.py`

**Testing Requirement:**
- 锁定 compare_tables 可被判定为 usable
- 锁定 compare_tables answer/synthesis context 非空
- 锁定单 workbook builder 签名和 summary context 不回归
- Run with escalation:
  - `bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q`
  - `bash patent/scripts/test.sh tests/test_patent_tabular_context.py -q`

- [ ] **Step 1: 写红灯 renderer/context 测试**

在 `patent/tests/test_patent_tabular_context.py` 增加至少这些 case：

```python
def test_build_compare_tabular_context_bundle_returns_non_empty_contexts():
    bundle = build_compare_tabular_context_bundle(
        question="对比一下这两个表格",
        compare_tables=_compare_tables(),
        plan=_count_compare_plan(),
        result=_count_compare_result(),
        compact_limit=1200,
        answer_limit=6000,
        synthesis_limit=8000,
    )
    assert bundle["answer_context"]
    assert bundle["synthesis_context"]


def test_compare_tables_result_is_usable():
    assert has_usable_tabular_result(_count_compare_result()) is True
```

- [ ] **Step 2: 跑红灯**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q
bash patent/scripts/test.sh tests/test_patent_tabular_context.py -q
```

Expected:
- FAIL
- compare_tables 渲染与 context builder 缺失

- [ ] **Step 3: 实现 compare 专用 builder**

在 `patent/server/patent/tabular_context.py` 中：

1. 保留 `build_tabular_context_bundle(...)` 单 workbook 语义
2. 新增 `build_compare_tabular_context_bundle(...)`
3. compare context 明确渲染：
   - 文件数
   - 工作表
   - compare 类型
   - grouped / ungrouped
   - 对比结果样例

在 `patent/server/patent/tabular/renderer.py` 中：

1. 扩展 `has_usable_tabular_result()`
2. 为 compare_tables 提供专用结果渲染

- [ ] **Step 4: 重跑 renderer/context 测试**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q
bash patent/scripts/test.sh tests/test_patent_tabular_context.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular/renderer.py patent/server/patent/tabular_context.py patent/tests/test_patent_tabular_context.py patent/tests/test_patent_tabular_executor_renderer.py
git commit -m "feat: add patent multi-table compare context"
```

---

### Task 4: Rework PatentTabularService For Structured Compare Flow

**Files:**
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/tests/test_patent_tabular_service.py`

**Testing Requirement:**
- 锁定 compare descriptor 不污染 cache workbook
- 锁定 `_load_table_context_bundle()` 结构化状态返回
- 锁定 true unreadable / clarification / execution_unavailable 三类结果
- 锁定 compare_tables answer path 不再误报 unreadable
- Run with escalation: `bash patent/scripts/test.sh tests/test_patent_tabular_service.py -q`

- [ ] **Step 1: 写红灯 service 测试**

增加至少这些 case：

```python
def test_tabular_service_multi_table_compare_uses_request_scoped_descriptors_without_mutating_cached_workbooks(...):
    ...


def test_tabular_service_returns_clarification_status_for_compare_plan_ambiguity(...):
    result = service.execute(...)
    assert result["metadata"]["answer_mode"] == "table_execution_clarification"
    assert "请指定" in result["answer_text"]


def test_tabular_service_returns_compare_unavailable_not_unreadable_when_execution_is_logical_failure(...):
    result = service.execute(...)
    assert result["metadata"]["answer_mode"] == "table_execution_compare_unavailable"
    assert "表格原始内容" not in result["answer_text"]
```

- [ ] **Step 2: 跑红灯**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py -q
```

Expected:
- FAIL
- 失败点集中在结构化状态缺失、仍只靠 `answer_context` 分支

- [ ] **Step 3: 实现 compare descriptor 与结构化状态**

在 `patent/server/patent/tabular_service.py` 中：

1. 新增 compare descriptor loader，例如：

```python
{
    "file_id": item.file_id,
    "file_name": item.file_name,
    "workbook": workbook,
    "profile": profile,
}
```

2. `_load_table_context_bundle()` 返回：

```python
{
    "status": "ok|unreadable|clarification|execution_unavailable",
    "compact_evidence_context": "...",
    "answer_context": "...",
    "synthesis_context": "...",
    "user_message": "...",
    "answer_mode": "...",
    "_skip_file_route_cache": bool(...),
    "log_fields": {...},
}
```

3. `execute()` 按 `status` 分支，而不是继续只看 `bool(answer_context)`
4. compare_tables 路径统一 planning / execution / context 构建
5. compare-specific prompt 只作用在 compare_tables 路径
6. 在调用 `plan_tabular_query()` 前，基于 compare descriptor 生成带 `file_id` / `file_name` 的 copied profiles；不要依赖当前 `schema_profiler.py` 直接产出这些字段

- [ ] **Step 4: 重跑 service 测试**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular_service.py patent/tests/test_patent_tabular_service.py
git commit -m "fix: orchestrate patent multi-table compare service flow"
```

---

### Task 5: Update Cache Fingerprints And File-Route Runtime Signatures

**Files:**
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_executor.py`

**Testing Requirement:**
- 锁定 table parity version bump
- 锁定 compare upgrade 导致 table-scoped cache miss
- 锁定非 table route 不暴露 compare parity metadata
- Run with escalation:
  - `bash patent/scripts/test.sh tests/test_patent_file_routes.py::test_file_route_runtime_signature_exposes_new_compare_tables_versions_for_table_scopes -q`
  - `bash patent/scripts/test.sh tests/test_patent_file_routes.py::test_file_route_cache_fingerprint_changes_when_compare_tables_runtime_signature_changes -q`
  - `bash patent/scripts/test.sh tests/test_patent_executor.py::test_executor_tabular_multi_table_compare_preserves_table_metadata_contract -q`

- [ ] **Step 1: 写红灯 cache/file-route 测试**

新增或扩展这些方向：

```python
def test_file_route_runtime_signature_exposes_new_compare_tables_versions_for_table_scopes():
    ...


def test_file_route_cache_fingerprint_changes_when_compare_tables_runtime_signature_changes():
    ...


def test_non_table_routes_do_not_expose_compare_tables_parity_metadata():
    ...


def test_executor_tabular_multi_table_compare_preserves_table_metadata_contract():
    ...
    assert result["metadata"]["answer_mode"] != "table_execution_unavailable"
    assert "table_evidence_context" in result["metadata"]
```

- [ ] **Step 2: 跑红灯**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_file_routes.py::test_file_route_runtime_signature_exposes_new_compare_tables_versions_for_table_scopes -q
bash patent/scripts/test.sh tests/test_patent_file_routes.py::test_file_route_cache_fingerprint_changes_when_compare_tables_runtime_signature_changes -q
bash patent/scripts/test.sh tests/test_patent_executor.py::test_executor_tabular_multi_table_compare_preserves_table_metadata_contract -q
```

Expected:
- FAIL
- 旧 version/runtime signature 未覆盖 compare_tables 语义

- [ ] **Step 3: 实现 cache/version 收口**

在 `patent/server/patent/file_routes.py` 中：

1. bump `_PATENT_TABLE_PLANNER_VERSION`
2. 如 compare context builder 或 status contract 有独立版本，也写入 `table_parity_signature`

在 `patent/server/patent/cache_keys.py` 中：

1. 确保 table-scoped runtime signature 变化参与 fingerprint
2. 保持非 table route 指纹稳定

必要时在 `patent/server/patent/executor.py` 中补 metadata 透传，但不要改普通 QA 结构。

- [ ] **Step 4: 重跑 cache/file-route 测试**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_file_routes.py::test_file_route_runtime_signature_exposes_new_compare_tables_versions_for_table_scopes -q
bash patent/scripts/test.sh tests/test_patent_file_routes.py::test_file_route_cache_fingerprint_changes_when_compare_tables_runtime_signature_changes -q
bash patent/scripts/test.sh tests/test_patent_executor.py::test_executor_tabular_multi_table_compare_preserves_table_metadata_contract -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/file_routes.py patent/server/patent/cache_keys.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "fix: version patent table compare cache boundaries"
```

---

### Task 6: Add FastAPI Contract Coverage For Real Multi-Table Compare

**Files:**
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`

**Testing Requirement:**
- 锁定 `/api/ask` 下多表 compare 不再返回 unreadable fallback
- 锁定 `/api/ask_stream` 下 compare steps / done contract 正常
- 锁定现有单表和 hybrid route 关键 contract 不回归
- Run with escalation:
  - `bash patent/scripts/test.sh tests/fastapi_contract/test_ask_contract.py::test_http_sync_tabular_multi_table_compare_uses_real_compare_flow -q`
  - `bash patent/scripts/test.sh tests/fastapi_contract/test_ask_contract.py::test_http_stream_tabular_multi_table_compare_emits_compare_steps_before_done -q`

- [ ] **Step 1: 写红灯 contract 测试**

增加至少这些 case：

```python
def test_http_sync_tabular_multi_table_compare_uses_real_compare_flow(monkeypatch, tmp_path):
    ...
    assert body["route"] == "tabular_qa"
    assert body["metadata"]["answer_mode"] != "table_execution_unavailable"
    assert "无法生成基于表格的回答" not in body["final_answer"]


def test_http_stream_tabular_multi_table_compare_emits_compare_steps_before_done(monkeypatch, tmp_path):
    ...
    assert any(event.get("step") == "tabular_load" for event in step_events)
    assert events[-1]["type"] == "done"
```

- [ ] **Step 2: 跑红灯**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/fastapi_contract/test_ask_contract.py::test_http_sync_tabular_multi_table_compare_uses_real_compare_flow -q
bash patent/scripts/test.sh tests/fastapi_contract/test_ask_contract.py::test_http_stream_tabular_multi_table_compare_emits_compare_steps_before_done -q
```

Expected:
- FAIL
- 现状会返回 unreadable fallback 或缺 compare-specific 行为

- [ ] **Step 3: 完成 contract 级收口**

如果前几个 task 已完成，这一步应主要是：

1. 补测试 fixture
2. 调整 metadata/assertions 到 compare_tables 新语义
3. 确认单表 / hybrid 旧 contract 仍通过

- [ ] **Step 4: 重跑 contract 测试**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/fastapi_contract/test_ask_contract.py::test_http_sync_tabular_multi_table_compare_uses_real_compare_flow -q
bash patent/scripts/test.sh tests/fastapi_contract/test_ask_contract.py::test_http_stream_tabular_multi_table_compare_emits_compare_steps_before_done -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "test: cover patent multi-table compare contract"
```

---

### Task 7: Overall Review And Full Validation

**Files:**
- No new product files by default
- Update tests or small fixes only if validation or review发现真实问题

**Testing Requirement:**
- 所有 compare 相关目标测试通过
- review pass
- 不在沙箱里跑测试

- [ ] **Step 1: 跑 compare 相关目标测试矩阵**

Run with escalation:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_planner.py -q
bash patent/scripts/test.sh tests/test_patent_tabular_executor_renderer.py -q
bash patent/scripts/test.sh tests/test_patent_tabular_context.py -q
bash patent/scripts/test.sh tests/test_patent_tabular_service.py -q
bash patent/scripts/test.sh tests/test_patent_file_routes.py -q
bash patent/scripts/test.sh tests/test_patent_executor.py -q
bash patent/scripts/test.sh tests/fastapi_contract/test_ask_contract.py -q
```

Expected:
- PASS

- [ ] **Step 2: 复用 reviewer subagent 做整体复审**

Review context:
- Spec: `docs/superpowers/specs/2026-04-15-patent-multi-table-compare-fastqa-parity-design.md`
- Plan: `docs/superpowers/plans/2026-04-15-patent-multi-table-compare-fastqa-parity-implementation.md`
- Final code diff after Tasks 1-6

要求 reviewer：

1. 先报 findings
2. 若无 material issues，明确 PASS
3. 如建议跑额外测试，必须要求提权，不得在沙箱跑

- [ ] **Step 3: 若 review 有问题，做最小修正并回归受影响测试**

Run with escalation:

```bash
bash patent/scripts/test.sh <affected-test-files> -q
```

- [ ] **Step 4: 整理最终提交**

建议提交方式：

```bash
git log --oneline --decorate -n 10
```

确保提交是按 task 切分的、消息清晰、没有把普通 QA / KB / PDF-only 无关改动混进去。

- [ ] **Step 5: Final Commit（如果仍有未提交收尾修正）**

```bash
git add <remaining-files>
git commit -m "fix: align patent multi-table compare with fastqa"
```
