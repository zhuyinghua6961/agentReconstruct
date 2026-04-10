# Patent Stage2/3 Parallelization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 `patent` 普通问答阶段边界、缓存语义、引用协议和最终结果顺序的前提下，实现 stage2 claim 级并行与 stage3 patent_id 级并行，并锁死共享状态、顺序稳定和部分失败语义。

**Architecture:** stage2 先串行冻结每个 claim 的 query 列表，再把 claim-local retrieval 放进受限线程池，主线程按 claim index 和 query index 归并，并保留现有“两级 `_dedupe_matches_by_prefix(...)`”语义。stage3 先按 `patent_id` 预切 `retrieval_rows_by_patent_id` 与 reference 索引，再并行装配单 patent bundle，最终只返回成功 bundle 的 `source_ids/evidences` 子序列，保持严格 1:1 对齐。

**Tech Stack:** Python 3, dataclasses, `ThreadPoolExecutor`, patent runtime/service modules, pytest, `conda run -n agent`

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-10-patent-stage23-parallelization-design.md`
- Reference implementation pattern: `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`

## Hard Rules

1. 不改 `stage4`，不改 `stage25`，不并行 `retrieval_plan` 的 `localization_queries` 路径。
2. `PatentQaGenerationOrchestrator` 本期继续传 `should_cancel=None`；实现时只能做 future-proof 内部透传，不能把端到端取消伪装成“已修复”。
3. stage2 的 query 生成必须保持串行预处理，不允许把共享 `planning_client` 直接并行化。
4. stage2 并行必须保留当前“两级去重”边界：`query` 级 `_dedupe_matches_by_prefix(...)` + 全局 `_dedupe_matches_by_prefix(...)`。
5. stage3 发生部分失败时，最终 `source_ids` 只保留成功 bundle 的 patent_id，并且始终满足 `evidences[i].canonical_patent_id == source_ids[i]`。
6. 不能假设 `PatentRetrievalService`、向量 runtime、archive loader 或规划 client 天然线程安全；任何并发写共享状态都必须显式收口。
7. 不允许把并行 worker 数写入 stage2/stage3 cache fingerprint 或 runtime signature。
8. 每个 task 必须先写红灯测试，再做最小实现，再重跑目标测试。
9. 执行任何命令或测试时，必须使用提权，并且测试命令必须用 `conda run -n agent`；如果无法提权或环境不可用，停止并报告，不得假装验证通过。
10. `PatentRuntime` 新增字段必须提供安全默认值，不能破坏现有直接构造点。
11. 本期只锁 `stage3` payload 的 `source_ids/evidences` 对齐 contract，不改 `PatentQaExecutionResult.metadata.source_ids` 顶层语义。
12. `_evidence_counts()` 的日志计数缺陷不在本期范围内，除非实现 stage3 并行时自然顺手修正；不能把它扩成额外 task。

## Per-Task Review Gate

对下面每一个 task，都必须执行同一条收口流程：

1. 写红灯测试。
2. 提权运行目标测试，确认失败。
3. 做最小实现。
4. 提权重跑目标测试，确认转绿。
5. 发起 review，只允许 reviewer 审，不允许 reviewer 改文件。
6. 根据 review 结论修正并重跑本 task 目标测试，直到 reviewer pass。
7. 然后才能 commit，并进入下一个 task。

## File Map

### Runtime / Stage Entrypoints

- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/stages/evidence_loading.py`

### Retrieval Core

- Modify: `patent/server/patent/retrieval_service.py`

### Tests

- Modify: `patent/tests/test_patent_retrieval_service.py`
- Modify: `patent/tests/test_patent_stage3_evidence_loading.py`
- Modify: `patent/tests/test_patent_generation_orchestrator.py`
- Modify: `patent/tests/test_execution_cache.py`

## Lock Decisions For Implementation

1. 新增 runtime 配置字段并固定 env 名称：
   - `PATENT_STAGE2_PARALLEL_WORKERS`
   - `PATENT_STAGE3_PARALLEL_WORKERS`
2. `PatentRuntime` 持有：
   - `stage2_parallel_workers: int`
   - `stage3_parallel_workers: int`
3. `should_cancel` 不新增顶层 payload 字段；如果内部 helper 需要显式取消标记，只能复用现有 `metadata` 容器，例如 `metadata["cancelled"] = True`。
4. stage2 的并行单元是“已冻结 query 列表的单个 claim”，不是“共享 client 的 query 生成任务”。
5. stage2 中所有共享可变状态写入口必须收敛到可同步的 helper：
   - `_ensure_catalog_record(...)`
   - `_disable_vector_search(...)`
6. stage3 必须新增 `retrieval_rows_by_patent_id` 预切分，不允许 worker 继续直接扫描完整 `documents/metadatas/distances`。
7. stage2/stage3 的 cache fingerprint 语义不变；worker 数只能影响执行策略，不能影响 cache key。
8. `PatentRuntime` 新字段必须声明默认值，保证现有 `PatentRuntime(...)` 测试构造点无需同步修改也能继续工作。
9. cache invariant 不只测 fingerprint builder；还必须锁 `PatentQaGenerationOrchestrator` 组装 `runtime_retrieval_signature` 时不混入 worker 数。

### Task 1: 锁定 Runtime Knobs、Cache 边界与 Stage2 串行 Query Freeze

**Files:**
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/tests/test_patent_retrieval_service.py`
- Modify: `patent/tests/test_patent_generation_orchestrator.py`
- Modify: `patent/tests/test_execution_cache.py`

**Testing Requirement:**
- 先锁死三件事：
  - runtime 会读取 `PATENT_STAGE2_PARALLEL_WORKERS` / `PATENT_STAGE3_PARALLEL_WORKERS`
  - worker 数不会进入 stage2/stage3 cache fingerprint
  - stage2 query 生成发生在任何并行 dispatch 之前，且按 claim 原始顺序执行
- 必跑命令：
  - `cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_patent_generation_orchestrator.py tests/test_execution_cache.py -q`
- 执行时必须提权。

- [ ] **Step 1: 写 runtime/config/query-freeze 红灯测试**

覆盖以下新增测试：

```python
def test_build_default_patent_runtime_reads_parallel_worker_envs(monkeypatch):
    ...
    assert runtime.stage2_parallel_workers == 4
    assert runtime.stage3_parallel_workers == 3

def test_patent_runtime_direct_construction_keeps_safe_parallel_worker_defaults():
    runtime = PatentRuntime(retrieval_service=_service(...), resources=[])
    assert runtime.stage2_parallel_workers >= 1
    assert runtime.stage3_parallel_workers >= 1

def test_stage2_query_generation_is_frozen_serially_before_parallel_dispatch():
    ...
    assert generated_claims == ["claim-a", "claim-b", "claim-c"]
    assert dispatch_started_after_query_freeze is True

def test_stage2_and_stage3_fingerprints_do_not_change_with_parallel_worker_counts():
    assert stage2_fp_workers_1 == stage2_fp_workers_8
    assert stage3_fp_workers_1 == stage3_fp_workers_8

def test_orchestrator_runtime_retrieval_signature_excludes_parallel_worker_counts():
    ...
    assert "stage2_parallel_workers" not in captured_stage2_runtime_signature
    assert "stage3_parallel_workers" not in captured_stage3_runtime_signature

def test_orchestrator_continues_passing_none_for_should_cancel():
    ...
    assert captured_stage2_should_cancel is None
    assert captured_stage3_should_cancel is None
```

- [ ] **Step 2: 提权运行红灯测试**

Run:
```bash
cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_patent_generation_orchestrator.py tests/test_execution_cache.py -q
```

Expected:
- FAIL
- 失败点集中在 runtime 尚无并行 worker 配置、stage2 尚未区分 query freeze 与 worker dispatch、缓存边界尚未被测试锁死。

- [ ] **Step 3: 做最小实现**

实现要求：
- `PatentRuntime` dataclass 新增 `stage2_parallel_workers` / `stage3_parallel_workers`
- 两个字段都提供安全默认值，兼容现有直接构造点
- `build_default_patent_runtime()` 读取上述 env，并对非法值回退到安全默认值
- `run_stage2_targeted_retrieval(...)` 增加“先串行生成 claim_queries，再进入 retrieval_service”所需的入参或辅助结构
- 不修改 orchestrator 的 `should_cancel=None` 现状
- 不把 worker 数并入 orchestrator `runtime_retrieval_signature` / cache fingerprint

- [ ] **Step 4: 提权重跑 Task 1 测试**

Run:
```bash
cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_patent_generation_orchestrator.py tests/test_execution_cache.py -q
```

Expected:
- PASS
- runtime knobs、query freeze 边界、cache invariant、orchestrator `None` 现状都被锁住。

- [ ] **Step 5: 发起 review，修到 pass**

要求：
- 只把 Task 1 的真实改动和测试结果发给 reviewer
- reviewer 如要求补测或收紧边界，必须先修正并重跑本 task 目标测试
- reviewer pass 前不能进入 Task 2

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/runtime.py patent/server/patent/stages/retrieval.py patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_generation_orchestrator.py patent/tests/test_execution_cache.py
git commit -m "feat(patent): add parallel runtime knobs and stage2 query freeze boundary"
```

### Task 2: 实现 Stage2 确定性 Claim Worker Pool 与共享状态收口

**Files:**
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/tests/test_patent_retrieval_service.py`

**Testing Requirement:**
- 必须锁死：
  - 串行与并行路径产出的 `documents/metadatas/source_ids/retrieval_plan_queries` 一致
  - query 级去重 + 全局去重语义保持不变
  - `_vector_runtime_enabled=False` 的 stage-wide degrade 语义保持不变
  - `parallel_workers=1` 回退串行
  - 显式提供 `should_cancel` 时，future wait 循环会停止收集剩余 claim jobs
- 必跑命令：
  - `cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py -q`
- 执行时必须提权。

- [ ] **Step 1: 写 stage2 红灯测试**

覆盖以下新增测试：

```python
def test_targeted_retrieval_parallel_matches_serial_output_and_order():
    assert serial_payload["documents"] == parallel_payload["documents"]
    assert serial_payload["metadatas"] == parallel_payload["metadatas"]
    assert serial_payload["metadata"]["retrieval_plan_queries"] == parallel_payload["metadata"]["retrieval_plan_queries"]

def test_targeted_retrieval_parallel_preserves_query_level_then_global_dedupe():
    assert payload["documents"][0] == "重复证据段。后缀B"
    assert "重复证据段。后缀A" not in payload["documents"]

def test_targeted_retrieval_parallel_worker_one_falls_back_to_serial():
    ...

def test_targeted_retrieval_parallel_keeps_vector_degrade_semantics():
    assert service._vector_runtime_enabled is False
    assert payload["references"] == ["CN123456789A"]

def test_targeted_retrieval_parallel_honors_explicit_should_cancel():
    assert payload["documents"] == []
    assert payload["metadata"]["cancelled"] is True
```

- [ ] **Step 2: 提权运行 stage2 红灯测试**

Run:
```bash
cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py -q
```

Expected:
- FAIL
- 失败点集中在 `_targeted_retrieve_from_claims()` 仍是纯串行、共享状态未收口、取消路径未接通。

- [ ] **Step 3: 做最小实现**

实现要求：
- `PatentRuntime.stage2_targeted_retrieval()` 把 `should_cancel`、`active_stream_count`、`stage2_parallel_workers` 传到底层
- 在 `retrieval_service.py` 中增加 stage2 claim worker helper，输入是“已冻结 query 列表”
- worker 返回：
  - `index`
  - `claim_key`
  - `generated_queries`
  - `candidate_patent_ids`
  - `per_query_matches`
  - `ok/error`
- 主线程按 `claim index -> query index` 归并，并保留两级 `_dedupe_matches_by_prefix(...)`
- 对共享写入口加窄锁，至少覆盖 `_ensure_catalog_record(...)` 与 `_disable_vector_search(...)`
- 在 `parallel_workers <= 1` 时保持原串行路径
- 在显式提供 `should_cancel` 时使用 `wait(..., timeout=0.2)` 轮询停止收集
- `run_stage2_targeted_retrieval(...)` 把 `should_cancel` 和 `stage2_parallel_workers` 继续传到底层

- [ ] **Step 4: 提权重跑 stage2 测试**

Run:
```bash
cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py -q
```

Expected:
- PASS
- stage2 并行输出与串行一致，degrade 语义与取消边界都被测试锁住。

- [ ] **Step 5: 发起 review，修到 pass**

要求：
- 只提交 Task 2 的 retrieval core 改动和测试结果
- reviewer 如果质疑锁粒度、去重边界或取消结果表达，必须先修并重跑 `tests/test_patent_retrieval_service.py`

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/runtime.py patent/server/patent/retrieval_service.py patent/server/patent/stages/retrieval.py patent/tests/test_patent_retrieval_service.py
git commit -m "feat(patent): parallelize stage2 claim retrieval deterministically"
```

### Task 3: 实现 Stage3 预切分输入、Patent Bundle Worker Pool 与 1:1 对齐 Contract

**Files:**
- Modify: `patent/server/patent/stages/evidence_loading.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/tests/test_patent_stage3_evidence_loading.py`
- Modify: `patent/tests/test_patent_generation_orchestrator.py`

**Testing Requirement:**
- 必须锁死：
  - `retrieval_rows_by_patent_id` 预切分存在且 worker 不再扫描完整 payload
  - `source_ids/evidences` 在部分失败场景下仍严格 1:1 对齐
  - `force_pdf=True` 多 patent 并行不串包
  - `parallel_workers=1` 回退串行
  - 显式提供 `should_cancel` 时尽快停止收集剩余 patent jobs
- 必跑命令：
  - `cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage3_evidence_loading.py tests/test_patent_generation_orchestrator.py -q`
- 执行时必须提权。

- [ ] **Step 1: 写 stage3 红灯测试**

覆盖以下新增测试：

```python
def test_stage3_parallel_matches_serial_success_bundle_order():
    assert serial_bundle["source_ids"] == parallel_bundle["source_ids"]
    assert [item["canonical_patent_id"] for item in parallel_bundle["evidences"]] == parallel_bundle["source_ids"]

def test_stage3_parallel_drops_failed_patents_from_source_ids_and_keeps_alignment():
    assert bundle["source_ids"] == ["CN115132975B"]
    assert [item["canonical_patent_id"] for item in bundle["evidences"]] == ["CN115132975B"]

def test_stage3_parallel_force_pdf_keeps_pdf_chunks_on_the_right_patent():
    ...

def test_stage3_parallel_honors_explicit_should_cancel():
    assert bundle["metadata"]["cancelled"] is True

def test_runtime_stage3_passes_parallel_workers_and_should_cancel():
    ...
```

并额外明确：

- `test_patent_generation_orchestrator.py` 只锁 stage 顺序、`should_cancel=None` 现状、以及 stage3 payload 被下游消费的契约
- 不在本 task 中新增“顶层 `PatentQaExecutionResult.metadata.source_ids` 改为成功子序列”的断言

- [ ] **Step 2: 提权运行 stage3 红灯测试**

Run:
```bash
cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage3_evidence_loading.py tests/test_patent_generation_orchestrator.py -q
```

Expected:
- FAIL
- 失败点集中在 `run_stage3_load_patent_evidence()` 仍顺序扫描整份 payload、`runtime.stage3_load_patent_evidence()` 仍丢弃 `should_cancel`。

- [ ] **Step 3: 做最小实现**

实现要求：
- 在 `evidence_loading.py` 中新增预切分 helper，把 `documents/metadatas/distances` 先按 `patent_id` 切成稳定顺序行集
- 把 `reference_object/reference_link/original_links` 也先做 `patent_id` 索引
- 新增单 patent worker helper，只消费该 patent 的切片数据和只读 loader
- 主线程按输入 `source_ids` index 聚合，只保留成功 bundle 的 patent_id 子序列
- `PatentRuntime.stage3_load_patent_evidence()` 透传 `should_cancel` 与 `stage3_parallel_workers`
- 在显式提供 `should_cancel` 时，stage3 wait 循环提前收口；如果需要显式标记，仅放进 `metadata["cancelled"]`

- [ ] **Step 4: 提权重跑 stage3 测试**

Run:
```bash
cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_stage3_evidence_loading.py tests/test_patent_generation_orchestrator.py -q
```

Expected:
- PASS
- stage3 预切分、并行 worker、部分失败子序列 contract、runtime 接线全部成立。

- [ ] **Step 5: 发起 review，修到 pass**

要求：
- 只提交 Task 3 的 stage3 改动和测试结果
- reviewer 若指出对齐 contract、pdf 隔离或取消语义问题，必须先修并重跑本 task 目标测试

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/stages/evidence_loading.py patent/server/patent/runtime.py patent/tests/test_patent_stage3_evidence_loading.py patent/tests/test_patent_generation_orchestrator.py
git commit -m "feat(patent): parallelize stage3 evidence bundle assembly"
```

### Final Verification Batch

**After all tasks are green and each task review has passed, run this exact final batch before claiming completion:**

```bash
cd patent && PYTHONPATH=. conda run -n agent pytest tests/test_patent_retrieval_service.py tests/test_patent_stage3_evidence_loading.py tests/test_patent_generation_orchestrator.py tests/test_execution_cache.py -q
```

Expected:
- PASS
- stage2/stage3 并行化、cache invariant、orchestrator invariants 一次性同时成立

**If this batch fails, do not claim completion. Fix the failing scope, rerun the relevant task review gate, then rerun the final batch.**
