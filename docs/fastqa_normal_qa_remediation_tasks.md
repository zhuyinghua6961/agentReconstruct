# fastQA 普通问答修复任务清单

本文将 `docs/fastqa_normal_qa_gap_analysis.md` 中确认的缺口拆成可执行任务。范围只覆盖普通问答 `kb_qa`，不包含 PDF 问答、表格问答、混合问答。

## 1. 目标

修复目标不是“能回答”，而是让 `fastQA` 普通问答尽量对齐旧版 `fastapi-version` 的行为，包括：

- 请求入口与默认值
- 五阶段执行语义
- stage2 检索质量护栏
- SSE 契约
- 错误与限流语义
- 聊天记录持久化

## 2. 执行原则

- 先修结果质量，再修链路边界，再修展示与运维细节。
- 普通问答所有修复都要以旧版源码行为为基准，不以当前 `fastQA` 现状为基准。
- 每个任务完成后都要补最少一条自动化测试，避免后续修别的地方时回退。
- stage2 与持久化是两条最高风险链路，优先单独验证。

## 2.1 当前进度（2026-03-20）

已完成：

- `P0-1` stage1 回退语义已恢复，并有对应回归测试。
- `P0-2` stage2 检索护栏主链路已恢复，并有对应回归测试。
- `P0-3` 服务端真实 `active_stream_count` 注入已完成，并有路由层测试。
- `P1-1` `iter_answer_events()` 编排入口已恢复；当前 `new` 模式可用，`legacy/request` 明确返回不支持。

部分完成：

- `P1-2` 已完成服务端唯一 `trace_id`、`chat_history` 最近 10 轮截断、`question` 边界放宽，并补了 `user_id` header/body 透传与冲突校验；空白问题等边界语义仍需和旧版再核对一遍。
- `P1-3` 已完成 `busy -> HTTP 429 JSON`、`adapter error -> HTTP 400 JSON`；`runtime error/not ready` 的最终 transport 语义还需再收敛。
- `P1-4` 已完成 `QA_STAGE4_MIN_CITATIONS=10` 的配置基线、代码默认值对齐，以及 `model_identity_shortcut` 文案对齐；更多 stage4 开关只剩补充核对，不再是默认值缺口。
- `P2-1` 已完成 `kb_qa` 单次流只发一套权威 `metadata` 的修复，并已把 `AskStreamTap` 正式接进 `ask/ask_stream` 路由闭环；后续只剩 summary 持久化接线。

未开始或未闭环：

- `P1-5` 普通问答持久化闭环：已完成 `user_id` 透传、路由层可插拔钩子、`CHAT_PERSIST_ENABLED` 控制下的默认 `conversation_service` 落库接线，以及 `CHAT_PERSIST_ASYNC` + 按 `conversation:{user_id}:{conversation_id}` 串行调度；剩余主要是鉴权上下文强绑定。
- `P2-2` `done.references` richer object 与持久化结构对齐：已完成 `reference_objects` 保留，同时继续对前端返回 `references: list[str]`；剩余仅是是否继续扩展 richer object 字段。
- `P2-3` 步骤文案和阶段提示已对齐旧版，包括图标、`阶段二点五命中`、`阶段三跳过PDF溯源`、`top 8 chunk` 和 stage4 文案。
- `T-2` 配置清单逐项核对。

最近一轮已通过测试：

- `conda run -n agent pytest fastQA/tests/test_qa_generation_orchestrator.py -q`
- `conda run -n agent pytest fastQA/tests/test_chat_persistence.py fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_generation_driven_rag_init.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_stream_contract.py fastQA/tests/test_qa_kb_models.py fastQA/tests/test_qa_route_aliases.py fastQA/tests/test_qa_routes_file_modes.py fastQA/tests/test_request_adapter.py fastQA/tests/test_qa_placeholder.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_qa_kb_service.py fastQA/tests/test_qa_kb_service_runtime.py fastQA/tests/test_generation_stage4_synthesis.py -q`

结果：`86 passed`

## 3. P0 任务

### P0-1 恢复 stage1 旧版回退语义

目标：

- 让 stage1 在没有有效 `retrieval_claims` 时，行为与旧版完全一致。

涉及文件：

- `fastQA/app/modules/generation_pipeline/stage1_planning.py`
- `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- `fastQA/tests/` 下对应 stage1 / orchestrator 测试

子任务：

1. 对照旧版 stage1，收紧 `deep_answer` 和 `retrieval_claims` 解析逻辑。
2. 删除“claim 为空时回退到用户原问题继续检索”的分支。
3. 对齐 orchestrator 中“仅预回答”路径的触发条件和 `query_mode`。
4. 补单测：stage1 返回空 claims 时，必须直接进入 fallback done。

前置依赖：

- 无

风险点：

- 收紧解析后，可能暴露当前 prompt 输出不规范问题。
- 需要区分“JSON 解析失败”与“JSON 成功但 claims 为空”两种回退路径。

验收标准：

- stage1 无 claim 时不进入 stage2。
- SSE 结果直接输出“仅预回答”路径，与旧版一致。
- 不再出现“用户原问题被硬塞回 stage2”行为。

建议测试：

- `test_stage1_empty_claims_falls_back_to_pre_answer`
- `test_stage1_json_parse_failed_falls_back_without_retrieval`

### P0-2 补齐 stage2 检索护栏

目标：

- 让普通问答的检索链从“简单召回”恢复成旧版“受控召回”。

涉及文件：

- `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
- 可能新增：
  - `fastQA/app/modules/generation_pipeline/stage2_query_builder.py`
  - `fastQA/app/modules/generation_pipeline/stage2_guardrails.py`
- `fastQA/tests/` 下对应 stage2 测试

子任务：

1. 引入旧版 query 生成流程，不再直接使用 `claim + keywords` 作为最终检索 query。
2. 补 `normalize_user_question_for_stage2()`，剥离聊天包装语与噪声上下文。
3. 补 `preprocess_retrieval_query()`，对 query 做统一清洗。
4. 补“强制关键词注入”逻辑。
5. 补“元素锁定”逻辑。
6. 补 `QA_STAGE2_QUERY_EXPANSION_ENABLED` 路径。
7. 补 rerank 接入，至少对齐：
   - `QA_RETRIEVAL_RERANK_PROVIDER`
   - `QA_RETRIEVAL_RERANK_CANDIDATES`
   - `QA_RETRIEVAL_RERANK_MODEL`
8. 补检索结果相关性校验，避免明显误召回直接进入 stage3。
9. 对齐 `claim_to_results` 的扩展字段，至少保留：
   - `query`
   - `query_guardrail`
   - `rerank`
   - `relevance_validation`
10. 补单测覆盖各个 feature flag 与 guardrail 分支。

前置依赖：

- `P0-1` 建议先完成，避免 stage1 误触发 stage2 导致调试混乱。

风险点：

- stage2 是最大改动点，容易和当前资源路径、向量库、rerank 配置耦合。
- query expansion 与 rerank 的默认值要按旧版配置，不要引入新默认行为。
- 这里最容易出现“功能看似恢复，但字段结构不一致”的隐性问题。

验收标准：

- stage2 主流程与旧版一致，不再只是 `claim + keywords` 直接检索。
- 配置开关能控制关键词注入、元素锁定、query expansion、rerank。
- 检索日志和 `claim_to_results` 字段可以逐项对照旧版。

建议测试：

- `test_stage2_force_keyword_injection`
- `test_stage2_entity_lock`
- `test_stage2_query_expansion_toggle`
- `test_stage2_rerank_toggle`
- `test_stage2_relevance_validation_filters_irrelevant_hits`

### P0-3 恢复服务端真实 `active_stream_count` 注入

目标：

- 让 stage2 动态 worker 策略基于服务端真实并发，而不是客户端输入。

涉及文件：

- `fastQA/app/routers/qa.py`
- limiter 相关实现
- `fastQA/tests/` 下路由/并发测试

子任务：

1. 在 `router` 层读取 limiter 当前快照。
2. 把真实 `active_stream_count` 注入 `QaKbRequest`。
3. 保留客户端字段时，明确其仅用于调试，不参与核心决策。
4. 补测试，验证不同活跃流数下 stage2 收到的参数不同。

前置依赖：

- 无

风险点：

- 需要避免 on_disconnect / finally release 导致统计抖动。
- 不能因为补这个逻辑影响现有 busy 限流释放时机。

验收标准：

- stage2 动态并发逻辑读取到的是服务端真实活跃流数。
- 并发压测时能观察到 worker 策略随活跃流数变化。

建议测试：

- `test_router_injects_server_active_stream_count`
- `test_stage2_dynamic_worker_policy_uses_server_count`

## 4. P1 任务

### P1-1 恢复 `iter_answer_events()` 编排层

目标：

- 把普通问答重新收敛到服务层编排入口，而不是 `router` 直接进生成链。

涉及文件：

- `fastQA/app/modules/qa_kb/service.py`
- `fastQA/app/modules/qa_kb/models.py`
- `fastQA/app/routers/qa.py`
- 如有必要，新增 ask stream tap / helper

子任务：

1. 恢复 `resolve_pipeline_mode()`。
2. 恢复 `iter_answer_events()`。
3. 先保证 `new` 模式完整可用。
4. 决定 `legacy/request` 是暂时 stub 兼容，还是显式返回“当前阶段不支持”。
5. `router` 改为只构造 request 和透传事件。
6. 补回编排层单测。

前置依赖：

- `P0-1`、`P0-2`、`P0-3`

风险点：

- 这是职责边界调整，容易和当前 `router` 的 metadata/done 补齐逻辑冲突。
- 如果只恢复方法名，不恢复职责，会变成假编排层。

验收标准：

- 普通问答统一从 `iter_answer_events()` 入链。
- `router` 不再承担过多编排职责。

建议测试：

- `test_qakb_service_iter_answer_events_dispatches_new_mode`
- `test_router_calls_iter_answer_events_not_generation_directly`

### P1-2 对齐请求默认值与 trace_id 语义

目标：

- 让请求填充与 trace 行为和旧版一致。

涉及文件：

- `fastQA/app/routers/qa.py`
- `fastQA/app/services/request_adapter.py`
- 配置/工具模块若需要新增 trace helper

子任务：

1. 默认 `trace_id` 改为服务端实时生成唯一值。
2. 对齐 trim / default / coerce 行为。
3. 决定是否服务端截断 `chat_history` 为最近 10 轮。
4. 重新核对 `question` 校验边界。
5. 补单测覆盖 trace_id 生成、空白问题、超长问题、脏 `chat_history`。

前置依赖：

- `P1-1` 建议先完成

风险点：

- 如果前端已依赖当前校验行为，改动后要同步验证。
- `trace_id` 一旦改成随机值，现有日志分析脚本可能需要调整。

验收标准：

- 每次请求都有唯一 `trace_id`。
- 边界输入的返回行为稳定，可复现旧版逻辑。

建议测试：

- `test_trace_id_generated_when_missing`
- `test_request_adapter_normalizes_invalid_chat_history`
- `test_blank_question_behaves_as_expected`

### P1-3 对齐错误与限流语义

目标：

- 让普通问答错误路径对前端和网关来说可预测，且尽量复刻旧版。

涉及文件：

- `fastQA/app/routers/qa.py`
- `app/core/sse.py` 若需要
- API 测试

子任务：

1. 明确 busy 行为是回归 HTTP `429` 还是保留 SSE 错误流。
2. 明确 adapter error 行为是否与 busy 保持同一风格。
3. 统一 runtime error / busy / adapter error 的 payload 结构。
4. 对前端实际消费方式做一次回归验证。

前置依赖：

- `P1-1`

风险点：

- 这里是协议层改动，容易影响前端已有容错逻辑。
- 如果未来要挂 gateway，还要考虑 gateway 对上游 429/SSE error 的处理方式。

验收标准：

- busy、适配失败、runtime 未就绪三类错误路径语义一致。
- 前端和网关能稳定区分 HTTP 错误与正常 SSE 结束。

建议测试：

- `test_stream_busy_returns_expected_transport`
- `test_stream_adapter_error_returns_expected_transport`
- `test_runtime_not_ready_returns_expected_payload`

### P1-4 对齐 stage4 配置基线

目标：

- 让 stage4 默认配置和旧版一致，避免“能回答但引用数量不对”。

涉及文件：

- `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
- `fastQA` 配置文件
- 配置加载测试

子任务：

1. 对齐 `QA_STAGE4_MIN_CITATIONS=10`。
2. 核对 `QA_STAGE4_REFERENCE_TOPK`。
3. 核对 `QA_STAGE4_ELEMENT_GUARD`。
4. 核对 `QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS`。
5. 决定 `model_identity_shortcut` 是回退旧版文案还是保留新文案并单独标注。
6. 补配置回归测试。

前置依赖：

- 无

风险点：

- 如果资源库不完整，引用篇数基线调高后可能暴露更多失败场景。

验收标准：

- 默认配置下，引用篇数与旧版一致。
- stage4 关键开关与旧版配置对齐。

建议测试：

- `test_stage4_default_min_citations_matches_legacy`
- `test_model_identity_shortcut_matches_expected_contract`

### P1-5 补齐普通问答持久化闭环

目标：

- 恢复 user message + assistant summary 的完整入库链路。

涉及文件：

- 普通问答入口层
- 对话/消息存储模块
- 若无对应汇总器，新增 stream tap/helper
- conversation 相关测试

子任务：

1. 请求进入时持久化 user message。
2. 流结束时汇总 assistant content。
3. 汇总并写入：
   - `query_mode`
   - `references`
   - `steps`
   - `route`
   - `used_files`
   - `timings`
   - `trace_id`
   - `file_selection`
   - `done_seen`
4. 校验异常中断、cancel、无 done 时的持久化策略。
5. 补 conversation 读回测试，确保前端历史记录能读到这些 metadata。

前置依赖：

- `P1-1`
- `P2-1` 最好同步设计，但不必等待实现完

风险点：

- 这是最容易引入脏数据和重复写入的地方。
- 若 stream 中途断开，要明确是否写 assistant summary。
- 若 reference 结构后面还会改，持久化层要留可扩展空间。

验收标准：

- 同一 `conversation_id` 下，普通问答能看到 user/assistant 完整消息。
- assistant message metadata 字段与旧版可比对。

建议测试：

- `test_persist_user_message_on_request_entry`
- `test_persist_assistant_summary_on_done`
- `test_do_not_persist_assistant_summary_without_done`

## 5. P2 任务

### P2-1 收敛 SSE 契约

目标：

- 让流式事件来源唯一、含义稳定，避免前端靠顺序猜语义。

涉及文件：

- `fastQA/app/routers/qa.py`
- `fastQA/app/services/stream_contract.py`
- 可能新增统一 stream tap

子任务：

1. 明确 metadata 只由一层产出。
2. 避免 `_iter_qa_frames()` 先补默认 metadata、后面生成链再补真实 metadata。
3. 统一 `query_mode` 来源。
4. 评估是否引入旧版风格 `AskStreamTap`。
5. 明确 `step`、`content`、`done` 的最小字段集。

前置依赖：

- `P1-1`

风险点：

- 这里改动会直接影响前端步骤展示。
- 如果历史记录依赖 `step` 结构，不能只改实时流，不改 summary。

验收标准：

- 每次普通问答只出现一套一致的 `metadata` 语义。
- `done` 事件字段稳定，前端不需要靠顺序猜测真实 `query_mode`。

建议测试：

- `test_stream_emits_single_authoritative_metadata`
- `test_done_event_contains_stable_contract_fields`

### P2-2 对齐 `done.references` 结构

目标：

- 不在链路末端丢失 richer reference object。

涉及文件：

- `fastQA/app/modules/qa_kb/streaming.py`
- `fastQA/app/routers/qa.py`
- 持久化相关代码

子任务：

1. 决定对外返回结构是否继续只暴露 DOI 字符串。
2. 如果前端只需要 DOI，也要把完整 reference object 保留给持久化与内部逻辑。
3. 明确 `normalize_references()` 应该发生在哪一层。
4. 补 done/persistence 两条链路的结构对齐测试。

前置依赖：

- `P1-5`

风险点：

- 最容易出现“前端正常，但持久化丢字段”。

验收标准：

- `done.references` 与持久化 summary 的 reference 结构清晰且稳定。
- 不再在链路末端丢失 reference 对象信息。

建议测试：

- `test_done_references_preserve_internal_reference_objects`
- `test_persistence_keeps_reference_structure`

### P2-3 文案与步骤提示对齐

目标：

- 让步骤展示和旧版尽量一致，减少“功能一样但体验完全不同”的偏差。

涉及文件：

- `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- `fastQA/app/services/stream_contract.py`

子任务：

1. 对齐 stage2.5 / stage3 / stage4 的提示文案。
2. 核查步骤 `title/detail/status` 拆分是否需要兼容旧版前端。
3. 校准 `model_identity_shortcut`。

前置依赖：

- `P2-1`

风险点：

- 文案改动虽小，但很容易破坏现有前端对步骤 key 的依赖。

验收标准：

- 阶段展示与旧版差异只剩可接受的小文案差异。

建议测试：

- `test_step_titles_match_expected_stage_labels`
- `test_model_identity_shortcut_text_contract`

## 6. 横向任务

### T-1 回归测试补齐

目标：

- 给普通问答关键路径建立稳定回归保护。

涉及范围：

- `fastQA/tests/` 下新增或重写以下类型测试：
  - stage1
  - stage2
  - orchestrator
  - router
  - stream contract
  - persistence

子任务：

1. 补 stage1 fallback 测试。
2. 补 stage2 guardrail 测试。
3. 补 `active_stream_count` 注入测试。
4. 补 busy / adapter error / runtime error 测试。
5. 补单次流中只有一套有效 metadata 的测试。
6. 补 done 事件 references/timings/trace_id 测试。
7. 补 user/assistant 消息持久化测试。

验收标准：

- 普通问答关键路径都有自动化测试保护。
- 任一关键修复都能通过一组明确测试验证。

### T-2 配置对齐清单

目标：

- 让当前配置层能表达旧版普通问答所需全部开关。

涉及文件：

- `fastQA` 配置文件
- 配置加载代码
- 配置说明文档

子任务：

1. 逐项核对旧版配置：
   - `QA_QUERY_PIPELINE_MODE`
   - `QA_STAGE2_PARALLEL_WORKERS`
   - `QA_STAGE2_DYNAMIC_WORKERS_*`
   - `QA_STAGE2_QUERY_EXPANSION_ENABLED`
   - `QA_RETRIEVAL_RERANK_*`
   - `QA_STAGE4_MIN_CITATIONS`
   - 其他 stage2/stage4 开关
2. 标注哪些是当前必须启用的，哪些只是兼容保留。
3. 补配置说明，避免后续重复踩坑。

验收标准：

- 当前配置文件能完整表达旧版普通问答所需开关。
- 配置说明文档能直接指导联调与部署。

## 7. 建议执行顺序

建议按下面顺序推进：

1. `P0-1` stage1 回退语义
2. `P0-2` stage2 检索护栏
3. `P0-3` 服务端 `active_stream_count`
4. `P1-4` stage4 配置基线
5. `P1-1` 恢复 `iter_answer_events()` 编排层
6. `P1-2` 请求默认值与 trace_id
7. `P1-3` 错误与限流语义
8. `P2-1` SSE 契约收敛
9. `P1-5` 持久化闭环
10. `P2-2` references 结构
11. `P2-3` 文案对齐
12. `T-1/T-2` 回归测试与配置核对

说明：

- `P2-1` 要放到 `P1-5` 前面，因为持久化 summary 依赖稳定的 SSE 契约。
- `P1-4` 提前做，是因为它成本低但收益直接，能快速减少“引用篇数不对”的明显问题。

## 8. 里程碑定义

### M1：结果质量对齐

完成标准：

- stage1 回退语义恢复
- stage2 检索护栏恢复
- 服务端真实并发上下文恢复
- stage4 引用基线恢复

### M2：链路行为对齐

完成标准：

- `iter_answer_events()` 恢复
- 请求默认值与 trace_id 语义对齐
- 错误/限流语义对齐
- SSE metadata/done 语义稳定

### M3：历史与运维对齐

完成标准：

- 普通问答持久化闭环完成
- references 结构稳定
- 回归测试补齐
- 配置项补齐并固化
