# Patent Stage2/3 Parallelization Design

**Date:** 2026-04-10

## Summary

本设计针对 `patent` 普通问答链路中最明确的两段串行瓶颈做并行化：

1. `stage2` 按 `retrieval_claim` 串行生成 query、跑摘要向量召回、跑 chunk 向量召回，再聚合结果。
2. `stage3` 按 `patent_id` 串行装配 catalog / table / pdf / retrieval matched evidence，再拼成 `PatentEvidenceBundle`。

设计目标不是改变检索策略，也不是重写整条 pipeline，而是在不改变外部接口、缓存键语义、引用协议、阶段边界和最终输出顺序的前提下，把 stage 内部可独立执行的工作改成受控并行。

本设计明确不覆盖：

- `stage1` prompt 或 pre-answer 行为调整
- `stage4` 综合写作并行化
- `stage25` 重构
- `retrieval_plan` 路径下多 query 并行
- prompt 对齐、引用格式、前端渲染问题

---

## Scope

### In Scope

1. `patent/server/patent/retrieval_service.py` 中 claim 级 targeted retrieval 并行化。
2. `patent/server/patent/stages/retrieval.py` / `patent/server/patent/runtime.py` 暴露并传递 stage2 并行配置与取消钩子。
3. `patent/server/patent/stages/evidence_loading.py` 中 `patent_id` 级证据装配并行化。
4. `patent/server/patent/runtime.py` 暴露并传递 stage3 并行配置与取消钩子。
5. 结果稳定排序、受控失败语义、日志与测试覆盖。

### Out Of Scope

1. `patent/server/patent/orchestrators/generation.py` 阶段间串并行重排。
2. `stage4` 的 LLM 调用方式、流式写作方式、prompt 内容。
3. `stage25` 的 no-op 设计。
4. `retrieval_plan` 的 `localization_queries` 并行 fan-out。
5. `PatentQaGenerationOrchestrator` 端到端取消能力接线。
6. 任何 API contract 变更、前端协议变更、缓存 key schema 变更。

---

## Problem Statement

当前 `patent` 普通问答虽然整体阶段顺序合理，但 `stage2` 和 `stage3` 内部仍保留明显的“可独立工作却串行执行”的实现，直接拉长总耗时。

### Stage2 现状

`PatentRetrievalService._targeted_retrieve_from_claims()` 以 claim 为外层顺序循环：

1. 为单个 claim 调 query 生成。
2. 顺序执行摘要向量检索。
3. 基于摘要命中的 `candidate_patent_ids` 再顺序执行 chunk 检索。
4. 把该 claim 的结果 append 到总列表。
5. 再处理下一个 claim。

这意味着当 `stage1` 产出多个 `retrieval_claims` 时，总耗时接近各 claim 耗时之和，而不是接近最慢 claim 耗时。

### Stage3 现状

`run_stage3_load_patent_evidence()` 以 `source_ids` 为顺序循环：

1. 加载 catalog。
2. 从 retrieval payload 里扫描 `reference_object` / `reference_link` / `original_links`。
3. 构造 retrieval matched evidence。
4. 加载 tables。
5. 如果开启 `force_pdf`，再加载 pdf document 并抽取文本。
6. 组装 `PatentEvidenceBundle`。

每个 `patent_id` 之间不存在共享中间态，也没有必须顺序依赖，但当前全部串行。

### 为什么不直接改阶段顺序

`patent` orchestrator 仍是标准的 `stage1 -> stage2 -> stage25 -> stage3 -> stage4`。这层顺序是合理的，因为：

1. `stage3` 依赖 `stage2` 的 `source_ids` 与 retrieval payload。
2. `stage4` 依赖 `stage3` 的证据 bundle。
3. 当前问题主要是阶段内部的 claim/patent 粒度串行，而不是阶段编排错误。

因此本期只做 stage 内并行，不改阶段间依赖。

---

## Evidence

以下结论都已从现有代码确认。

### 1. Stage2 claim 路径当前完全串行

文件：`patent/server/patent/retrieval_service.py`

- `_targeted_retrieve_from_claims()` 在单线程 `for claim in retrieval_claims` 中处理全部 claim。
- 每个 claim 内部依次执行：
  - `_generate_claim_queries(...)`
  - `_run_abstract_vector_search(...)`
  - `_run_chunk_vector_search(...)`
- 所有 claim 结果最后统一 `all_matches.extend(...)`。

这说明 `retrieval_claims` 数量一旦增加，stage2 时延会线性放大。

### 2. Stage2 plan 路径也串行，但本期不做

同文件 `_targeted_retrieve_from_plan()` 中，`localization_queries` 走的是顺序列表推导：

- 每个 query 依次 `_vector_matches(...)`
- 再做 `_merge_targeted_matches(...)`

这条路径也能并行，但它与本期要做的 claim 路径是两种不同入口。为了控制改动面，本期先不碰。

### 3. Stage3 patent bundle 装配当前完全串行

文件：`patent/server/patent/stages/evidence_loading.py`

- `run_stage3_load_patent_evidence()` 对 `normalized_source_ids` 做顺序 `for patent_id in normalized_source_ids`
- 每个 patent 独立完成：
  - catalog/table/pdf 加载
  - retrieval matched evidence 组装
  - `PatentEvidenceBundle` 构造

各 patent 间无共享状态写入，因此天然适合 worker pool。

### 4. fastQA 已有可借鉴的 claim 级并行模式

文件：`fastQA/app/modules/generation_pipeline/stage2_retrieval.py`

`fastQA` 已使用 `ThreadPoolExecutor` 处理 claim jobs，并包含：

1. 受限 worker 数。
2. `wait(..., timeout=0.2)` 的轮询取消检查。
3. future 异常隔离。
4. 聚合前按原始 claim index 排序，保证输出顺序稳定。

`patent` 本期应尽量复用同一思想，而不是重新发明并发控制方式。

### 5. patent runtime 已预留取消参数，但还没有真正传进去

文件：`patent/server/patent/runtime.py`

- `stage2_targeted_retrieval(... should_cancel=None, active_stream_count=None)`
- `stage3_load_patent_evidence(... should_cancel=None)`

但当前实现里：

- stage2 没把 `should_cancel` 和 `active_stream_count` 传给下层。
- stage3 直接 `del should_cancel`。

这意味着并行化如果不一起补上取消语义，就会把“仍可取消”的接口变成空壳。

---

## Goals

### Product Goals

1. 在多 claim 的 `patent` 普通问答里，`stage2` 总耗时显著下降。
2. 在多 `source_ids` 的证据装配场景里，`stage3` 总耗时显著下降。
3. 最终答案内容、引用来源、专利顺序、缓存命中语义保持稳定，不引入“更快但结果漂移”的退化。

### Engineering Goals

1. 仅对 stage 内部做并行，不破坏 orchestrator 的阶段语义。
2. 输出顺序必须可预测、可测试。
3. 任一 job-local 失败不能无条件拖垮整个 stage；但现有 stage-wide degrade 语义也不能被并行化破坏。
4. 并行开关与 worker 数必须受控，避免无上限线程扩张。
5. 测试必须锁死串/并行一致性、共享状态边界、顺序稳定性。

---

## Non-Goals

1. 不承诺把 `patent` 变成完全异步 pipeline。
2. 不引入 `asyncio` 改造。
3. 不改 `PatentQaGenerationOrchestrator` 的阶段串行关系。
4. 不把 `stage4` 拆成多 worker 并行综合。
5. 不改变 `stage2` / `stage3` cache fingerprint 字段。

---

## Constraints

1. `PatentQaGenerationOrchestrator` 继续按阶段顺序执行，`stage3` 不能早于 `stage2` 完成。
2. `source_ids` 顺序仍由 `stage2` 抽取得出，`stage3` 返回的 `evidences` 必须与该顺序对齐。
3. stage2 聚合后的 `metadata["retrieval_plan_queries"]` 仍需是稳定、有意义的顺序，不能变成 nondeterministic。
4. stage2 / stage3 对外 payload shape 不变。
5. `PatentRuntime` 仍是入口适配层，真正的并行实现应落在 stage / service 模块，而不是 orchestrator。
6. 当前 orchestrator 在真实链路中仍向 stage2 / stage3 传 `should_cancel=None`；因此本期不承诺用户可见的端到端取消行为改善。
7. `PatentRetrievalService` 当前包含共享可变状态，不能把它当成天然线程安全对象处理。

---

## Design Overview

### 总体策略

1. `stage2` 以 claim 为 job 单位，用线程池并行执行 claim-local retrieval。
2. `stage3` 以 patent_id 为 job 单位，用线程池并行执行单专利证据 bundle 装配。
3. 两个阶段都采用“并行执行 + 主线程按原始 index 归并”的模式，确保结果顺序稳定。
4. `should_cancel` 只作为内部 future-proof 接口保留和透传；当调用方显式提供该回调时，并行循环应尊重它，但本期不把真实 pipeline 取消作为验收项。
5. worker 内部异常不应让单个 future 直接打断整个 stage；但已有的 stage-wide degrade 语义必须保留。

### 为什么使用 `ThreadPoolExecutor`

1. 现有实现主要是 I/O 密集型：
   - 向量检索
   - 档案加载
   - table/pdf 文件读取
2. `fastQA` 已经采用线程池，同仓迁移成本最低。
3. 不需要改变现有函数签名为 async，也不需要重写上层 orchestrator。

---

## Stage2 Detailed Design

### Current Boundary

入口链路：

1. `PatentQaGenerationOrchestrator.execute()` 调 `runtime.stage2_targeted_retrieval(...)`
2. `PatentRuntime.stage2_targeted_retrieval()` 调 `run_stage2_targeted_retrieval(...)`
3. `run_stage2_targeted_retrieval()` 调 `retrieval_service.targeted_retrieve(...)`
4. `PatentRetrievalService.targeted_retrieve()` 在 claim 路径进入 `_targeted_retrieve_from_claims(...)`

并行化主改动应落在 `_targeted_retrieve_from_claims(...)`。

### Job Unit

stage2 不直接把“query 生成 + 向量检索 + 结果归并”整个 claim 流水线一次性并行出去，而是拆成两段：

1. 串行预处理阶段：按 claim 原始顺序生成 `claim_queries`，冻结每个 claim 的 query 列表。
2. 并行执行阶段：每个 claim worker 只消费已冻结的 query 列表，执行向量检索、hit 转换和 claim 级结果归并。

这样做的原因是当前 `query_client/planning_client` 没有被证明可以安全地被多线程共享调用，本期不在 spec 中假设它天然线程安全。

一个 `retrieval_claim` 在“并行执行阶段”对应一个 stage2 job。每个 job 负责：

1. 消费该 claim 已冻结的 queries。
2. 逐 query 做 abstract 搜索。
3. 收集该 claim 的 candidate patent ids。
4. 逐 query 做 chunk 搜索。
5. 保留“先 query 内去重、再全局去重”所需的中间结果。
6. 返回 claim 级 metadata：
   - `index`
   - `claim_key`
   - `generated_queries`
   - `candidate_patent_ids`
   - `per_query_matches`
   - `ok/cancelled/error`

### Worker Pool

新增 `stage2_parallel_workers` 配置，原则：

1. 默认值有限，例如 `min(4, len(retrieval_claims))` 对应的固定上限逻辑。
2. `<=1` 时自动退回串行路径，便于测试和问题回退。
3. 不根据机器核数无限扩张，因为 stage2 主要受外部检索/存储能力限制。

### Aggregation

主线程聚合时必须：

1. 按 claim 原始 index 排序。
2. 先按 claim 顺序合并 `generated_queries`，并做去重保序。
3. 先按 claim 顺序合并 `candidate_patent_ids`，并做去重保序。
4. 再按 claim 顺序、claim 内 query 顺序，拼接每个 query 已做过一次 `_dedupe_matches_by_prefix(...)` 的结果。
5. 最后仅在全局层面再执行一次当前同款 `_dedupe_matches_by_prefix(...)`。

这样可以保证：

1. 同一输入在串行/并行路径下得到同序输出。
2. `stage2` cache fingerprint 不受影响，因为 fingerprint 仍来自上游输入，而不是内部执行顺序。
3. 下游 `source_ids` 和 `stage3` 输入保持稳定。
4. 并行实现不会把当前“query 级去重 + 全局去重”的两级边界偷偷简化成“claim 级一次性去重”。

### Cancellation

stage2 本期只做“内部接口不再丢弃 `should_cancel`”的 future-proof 接线，不做真实 orchestrator 取消能力改造：

1. `PatentQaGenerationOrchestrator` 仍传 `should_cancel=None`，本期不改 orchestrator 行为。
2. `PatentRuntime.stage2_targeted_retrieval()` 不再吞掉可选 `should_cancel`。
3. `run_stage2_targeted_retrieval()` 再把 `should_cancel` 传到 `retrieval_service.targeted_retrieve(...)`。
4. `_targeted_retrieve_from_claims()` 在调用方显式提供 `should_cancel` 时，参考 `fastQA`：
   - 提交 future 后循环 `wait(... timeout=0.2 ...)`
   - 每轮检查 `should_cancel()`
   - 取消剩余 future，并返回显式 cancelled payload

本期不要求把 claim 内部的单个 query 搜索打断到更细粒度，只要求在可选回调存在时：

1. 不再继续收集未完成 future。
2. 尽快退出 stage2。

### Error Isolation

当前 `PatentRetrievalService` 不是“无共享状态”的纯函数服务。与 stage2 claim 并行直接相关的共享可变状态至少包括：

1. `_catalog_by_id`
2. `_catalog_identifier_index`
3. `_vector_runtime_enabled`

因此 stage2 设计不能宣称“所有 worker 失败都只影响自己”。必须明确保留两类语义：

1. claim-local 失败：
   - 某个 claim 的 query 生成、hit 解析、结果装配失败，只影响该 claim job
   - 记录 warning
   - 其他 claim 继续
2. stage-wide degrade：
   - 任一 worker 遇到向量检索异常，仍沿用当前 `_disable_vector_search(...)` 语义
   - 这会影响同一 stage 后续 worker 是否继续使用 vector path
   - 并行化不能把这个既有降级行为改成悄悄吞掉

实现上，本期必须把共享状态写入口收窄到可同步的边界，至少覆盖：

1. `_ensure_catalog_record(...)` 中的懒加载写入
2. `_disable_vector_search(...)` 中的全局降级开关

单个 claim worker 出错时：

1. 记录 warning 日志，带上 claim index / claim 摘要。
2. 该 claim 输出视为 `ok=False`。
3. 其他 claim 正常继续，除非命中 stage-wide degrade 语义。

只有在以下情况才让整个 stage2 进入明显失败/退化：

1. 调用方显式提供 `should_cancel` 且返回真。
2. 全部 claim 都失败且无任何 merged matches。
3. 现有 fallback 路径被触发且仍无结果。
4. 向量后端故障触发既有 stage-wide degrade。

### Logging

新增或增强日志应覆盖：

1. stage2 并行启动时的 `claim_count`、`parallel_workers`。
2. claim worker 完成/失败/是否命中 vector degrade。
3. 聚合后的 `candidate_patent_ids` 数量、`retrieval_plan_queries` 数量、最终 references 数量。

日志目标是定位慢 claim，不是输出海量逐 query debug 噪声。

---

## Stage3 Detailed Design

### Current Boundary

入口链路：

1. `PatentQaGenerationOrchestrator.execute()` 调 `runtime.stage3_load_patent_evidence(...)`
2. `PatentRuntime.stage3_load_patent_evidence()` 调 `run_stage3_load_patent_evidence(...)`
3. `run_stage3_load_patent_evidence()` 顺序装配每个 `patent_id` 的 evidence bundle

并行化主改动应落在 `run_stage3_load_patent_evidence(...)`。

### Job Unit

一个 `patent_id` 就是一个 stage3 job。每个 job 独立完成：

1. 读取 catalog record。
2. 消费主线程按 `patent_id` 切好的 retrieval rows 与 reference 索引。
3. 构建 retrieval matched evidence 或 legacy matched evidence。
4. 加载 tables。
5. 按需加载 pdf 文本。
6. 组装 `PatentEvidenceBundle`。
7. 返回：
   - `index`
   - `patent_id`
   - `evidence_bundle`
   - `ok/cancelled/error`

### Worker Pool

新增 `stage3_parallel_workers` 配置，原则：

1. 默认值受限，例如最多 `4`。
2. `<=1` 时回退串行路径。
3. `force_pdf=True` 时仍允许并行，但上限要保守，避免同时抽多个 pdf 放大 I/O。

### Precomputed Shared Inputs

为了避免每个 worker 重复扫描整份 `retrieval_results`，主线程应先构造只读索引：

1. `retrieval_rows_by_patent_id`
   - 值是保持 stage2 原始顺序的 `(document, metadata, distance)` 切片列表
2. `reference_object_by_patent_id`
3. `reference_link_by_patent_id`
4. `original_links_by_patent_id`

这些索引在 worker 启动前一次性构建，然后按 `patent_id` 分发给 worker 读取。

这样能避免“虽然并行了，但每个 worker 都 O(N) 扫一遍大列表”的伪优化。

### Aggregation

主线程聚合时：

1. 按原始 `source_ids` index 排序。
2. 只收集 `ok=True` 的 bundle。
3. `PatentStage3EvidenceResult.source_ids` 只保留成功产出 bundle 的 patent_id，并保持原顺序。
4. `evidences` 的顺序与最终 `source_ids` 严格 1:1 对齐。

如果某个 patent worker 失败：

1. 记录日志。
2. 默认不让整个 stage3 立刻崩溃。
3. 失败 patent 不进入最终 `source_ids/evidences`。
4. 但最终如果 `evidences` 为空，则 stage3 仍按现有失败语义让上层处理。

### Cancellation

stage3 与 stage2 一样，本期只做“内部接口不再丢弃 `should_cancel`”的 future-proof 接线：

1. `PatentRuntime.stage3_load_patent_evidence()` 不再 `del should_cancel`。
2. `run_stage3_load_patent_evidence()` 接收 `should_cancel`。
3. 当调用方显式提供 `should_cancel` 时，并行路径轮询 future 完成时检查取消。
4. 取消后停止继续收集剩余 patent jobs。

stage3 的取消粒度也是“patent bundle 粒度退出”，不强求中断正在跑的 pdf 抽取函数。

### Logging

新增或增强日志应覆盖：

1. stage3 启动时的 `source_id_count`、`parallel_workers`、`force_pdf`。
2. 单 patent bundle 的完成/失败/是否加载 pdf。
3. stage3 聚合完成后的 bundle 数量。

---

## Configuration Design

建议在 `PatentRuntime` 层增加两个可配置字段：

1. `stage2_parallel_workers`
2. `stage3_parallel_workers`

来源可以与现有 runtime env pattern 保持一致，例如通过环境变量读取，但本设计只锁边界，不锁死变量名。

配置要求：

1. 默认开启有限并行，而不是默认关闭。
2. 显式设为 `1` 时必须强制串行，便于回归对比与故障回退。
3. 读取到非法值时回退到安全默认值，而不是抛启动异常。
4. 如果后续验证发现共享 vector runtime 在并发下不稳定，允许把默认值快速回退到 `1`，不改变 payload 语义。

---

## Ordering And Determinism

这是本设计最关键的正确性要求。

### Stage2

并行后必须保证以下顺序稳定：

1. `retrieval_plan_queries` 按原始 claim 顺序保序去重。
2. `candidate_patent_ids` 按 claim 首次出现顺序保序去重。
3. 每个 claim 内部仍保持“query 原始顺序 -> query 内 `_dedupe_matches_by_prefix(...)` -> 全局 `_dedupe_matches_by_prefix(...)`”的两级去重语义。
4. 合并后的 `references` / `reference_objects` / `metadatas` / `documents` 不因 future 返回先后而漂移。

### Stage3

并行后必须保证以下顺序稳定：

1. `source_ids` 与“成功产出 bundle 的 patent_id 原始顺序子序列”一致。
2. `evidences[i].canonical_patent_id == source_ids[i]`。
3. 同一 patent 内部 `matched_evidence` 的构造规则不变。

如果做不到这些，并行化就不算可接受。

---

## Cache Compatibility

本期不改以下 fingerprint 语义：

1. `build_stage2_cache_fingerprint(...)`
2. `build_stage3_cache_fingerprint(...)`

原因：

1. 并行化是执行策略优化，不是输入语义变化。
2. 如果因为内部 worker 数变化导致 fingerprint 变化，会破坏缓存稳定性。

允许变化的只有时序和日志，不允许变化的是同输入下的 payload 语义。

---

## Test Strategy

本设计要求至少补齐以下自动化验证。

### Stage2 Tests

目标文件优先：

- `patent/tests/test_patent_retrieval_service.py`

至少覆盖：

1. 多 claim 场景下，并行路径与串行路径产出的：
   - `source_ids`
   - `metadata["retrieval_plan_queries"]`
   - `references/reference_objects`
   顺序一致。
2. query 顺序与两级 `_dedupe_matches_by_prefix(...)` 语义在并行路径下保持不变。
3. 单个 claim-local worker 抛异常时，其他 claim 结果仍能被保留。
4. 调用方显式提供 `should_cancel` 时，stage2 返回 cancelled 结果，不继续等待全部 future。
5. `parallel_workers=1` 时走串行回退路径。
6. 向量检索异常触发的 `_vector_runtime_enabled=False` 仍保持 stage-wide degrade 语义。

### Stage3 Tests

目标文件优先：

- `patent/tests/test_patent_stage3_evidence_loading.py`

至少覆盖：

1. 多 `source_ids` 场景下，并行路径与串行路径产出的成功 bundle 顺序一致。
2. `source_ids/evidences` 在任何部分失败场景下仍保持严格 1:1 对齐。
3. `force_pdf=True` 时，多 patent 并行仍只产生对应 patent 的 pdf 证据，不串包。
4. 单个 patent worker 抛异常时，其他 patent bundle 仍可返回。
5. 调用方显式提供 `should_cancel` 时，stage3 尽快终止收集。
6. 主线程预切分 `retrieval_rows_by_patent_id` 后，worker 不再重复扫描整份 `documents/metadatas/distances`。

### Runtime Wiring Tests

目标文件优先：

- `patent/tests/test_patent_retrieval_service.py`
- `patent/tests/test_patent_generation_orchestrator.py`

至少覆盖：

1. runtime 把 `should_cancel` / 并行配置传给 stage2 / stage3。
2. orchestrator 不需要改阶段顺序，也不会因为并行化破坏已有 stage2/stage3/stage4 调用约定。
3. 当前 orchestrator 继续传 `None` 时，行为与今天一致；本期不对端到端取消做成功宣称。

---

## Rollout And Safety

### Rollout Strategy

1. 先补红灯测试锁定顺序、共享状态边界、失败语义。
2. 再接入 stage2 并行。
3. 再接入 stage3 并行。
4. 最后跑 targeted pytest 和必要的集成回归。

### Safety Guardrails

1. worker pool 必须有上限。
2. 并行路径必须保留串行回退分支。
3. 不允许在未受控的情况下让 worker 并发写 `PatentRetrievalService` 的共享可变状态。
4. 不允许 future 完成先后决定最终输出顺序。

---

## Risks

### 1. 顺序漂移

如果直接按 future 完成顺序聚合，`retrieval_plan_queries`、`candidate_patent_ids`、`evidences` 的顺序都会漂移，进而影响 stage4 写作稳定性。

应对：

1. job 返回原始 index。
2. 主线程统一排序后再聚合。

### 2. 假并行

如果 stage3 每个 worker 仍反复扫描整份 `retrieval_results`，CPU 与对象复制开销会上升，收益被抵消。

应对：

1. 主线程预构建 `retrieval_rows_by_patent_id`。
2. 同时预构建 patent_id -> reference 索引。
3. worker 只消费对应切片。

### 3. 取消语义继续空转

如果把“内部透传 `should_cancel`”错误表述成“本期真实取消已修复”，会造成验收与真实行为不一致。

应对：

1. runtime -> stage module -> service 全链路保留可选 `should_cancel`。
2. future wait 轮询里在回调存在时持续检查取消。
3. 明确标注 orchestrator 仍传 `None`，不把端到端取消纳入本期完成标准。

### 4. 外部依赖并发压力

摘要/chunk 检索、pdf 抽取、table 加载都可能在高并发下放大 I/O 压力。

应对：

1. worker 数保守上限。
2. 默认值小于等于 4。
3. 支持快速回退到 `1`。

---

## Acceptance Criteria

本设计完成后，以下条件必须同时成立：

1. `patent` 普通问答的 stage2 在多 claim 场景下使用 claim 级并行。
2. `patent` 普通问答的 stage3 在多 `source_ids` 场景下使用 patent 级并行。
3. stage2/stage3 的 payload shape 与 cache fingerprint 语义不变。
4. 串行与并行路径在同输入下输出顺序一致，并保留 stage2 当前的两级去重语义。
5. stage3 在部分失败场景下仍保持 `source_ids/evidences` 严格 1:1 对齐。
6. job-local 失败不会让整个 stage 无条件崩溃，但现有 stage-wide degrade 语义被保留。
7. 当调用方显式提供 `should_cancel` 时，stage2/stage3 内部并行循环会尊重它；当前 orchestrator 继续传 `None` 的事实在文档和测试中被明确锁住。
8. 自动化测试覆盖顺序稳定、共享状态边界、失败语义、串行回退。

---

## Implementation Boundary

后续 implementation plan 必须严格基于本设计，按以下边界拆任务：

1. 先做 stage2 claim 并行与取消接线。
2. 再做 stage3 patent bundle 并行与取消接线。
3. 每一步都必须先补测试，再做最小实现。
4. 不允许把“顺便并行 stage4”或“顺便改 prompt”夹带进本期。
