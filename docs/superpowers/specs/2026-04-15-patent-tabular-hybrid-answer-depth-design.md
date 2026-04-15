# Patent Tabular and Hybrid QA Answer Depth Design

**Date:** 2026-04-15

## Summary

本设计定义 `patent` 后端中 `tabular_qa` 与 `hybrid_qa` 答案过短问题的修复方案。

当前 `tabular_qa` 在生产路径中默认没有可用的 LLM answer client，容易退化为最多 4 条表格行摘要的 fallback。当前 `hybrid_qa` 最终答案主要由规则函数从 PDF、表格、KB 的短 evidence context 中抽点合成，普通问答通常只保留少量 lead 和证据行，summary 问答也会经过多层候选条数和字符裁剪。

本方案采用两层修复：

1. `tabular_qa` 接入真实 OpenAI-compatible 表格回答 client，并扩大表格证据上下文。
2. `hybrid_qa` 新增统一 synthesis client，优先用 LLM 基于完整 PDF、表格、KB 证据生成最终答案，现有规则合成只保留为降级路径。

---

## Scope

本设计覆盖：

1. `patent/server/patent/tabular_service.py` 中表格 QA 的回答 client、fallback 标记、表格上下文构建和 summary 后处理。
2. `patent/server/patent/file_routes.py` 中 `hybrid_qa` 的 synthesis contract、文件侧 `pdf+table` 合成和规则 fallback。
3. `patent/server/patent/executor.py` 中带 KB hybrid 路径的最终合成。
4. `patent/server_fastapi/app.py` 中 `PatentTabularService` 与 hybrid synthesis 相关 client 的依赖注入。
5. `patent/server/patent/pdf_service.py` 和 `patent/server/patent/tabular_service.py` 暴露给 hybrid synthesis 的更完整上下文。
6. 文件路由缓存 fingerprint 中与新 answer client / synthesis client / prompt version 相关的缓存隔离。
7. 自动化测试和 FastAPI contract test。

本设计不覆盖：

1. 前端 UI、按钮、文件选择交互改版。
2. `gateway` 路由策略改造。
3. `patent` 普通知识库 QA 的 stage1 到 stage4 主链路重构。
4. PDF QA 的完整重构；本方案只要求 PDF 分支向 hybrid 暴露更适合 synthesis 的上下文。
5. 跨 worker 共享连接池；本方案只复用现有进程内 shared HTTP client 能力。

---

## Problem Statement

### 1. `tabular_qa` 生产路径默认退化为 fallback

`PatentExecutor` 默认构造 `PatentTabularService()`。`server_fastapi/app.py` 当前只显式注入 `PatentPdfService`，没有给 `PatentTabularService` 注入 answer client。

`PatentTabularService._build_answer()` 只有在 `answer_question_fn` 存在时才调用外部回答函数。生产默认没有该函数时，会走 `_table_fallback_answer()`。

这个 fallback 的行为是：

1. 从表格上下文中挑最多 4 条候选行。
2. 每条候选行截断到 220 字。
3. 再由结构化后处理包装成文献总结或四段式问答。

结果是用户看到的表格 QA 天然偏短，不是模型质量波动。

### 2. `tabular_qa` 默认执行上下文不是全表语义

当前默认表格链路会：

1. 加载 workbook。
2. 用简单 planner 选择 sheet、operation、metric columns、filters。
3. 用 executor 生成结构化结果。
4. 用 renderer 输出最多 5 条样例。

这套执行链路适合快速查数、聚合和代表性展示，但不适合直接作为唯一 LLM 上下文来回答“总结这个表格”“分析这批数据有什么规律”这类需要更充分覆盖的问题。

### 3. `hybrid_qa` 最终答案由规则抽点主导

`hybrid_qa` 当前会先跑 PDF 分支和表格分支，再把 `pdf_answer`、`tabular_answer`、`pdf_evidence_context`、`table_evidence_context` 放入 synthesis contract。

最终 `synthesize_patent_hybrid_answer()` 是确定性规则函数：

1. 普通问答输出固定 `结论 / 证据 / 对比 / 限制`。
2. `file_only_hybrid` 普通问答通常只保留一个表格 evidence 和一个 PDF evidence。
3. 多处使用 `_clip_lead_text()`，默认 120 字，部分候选 220 字。
4. summary 路径也会把候选数限制在 2、3、8、12 等固定上限。

这导致 hybrid final answer 不会自然保留子答案中的完整细节。

### 4. Hybrid synthesis 使用的 evidence context 本身被截短

PDF metadata 中的 `pdf_evidence_context` 只保留 `prepared_for_generation` 或 `pdf_text` 的前 1200 字。

表格 metadata 中的 `table_evidence_context` 也只保留 `execution_context` 的前 1200 字。

这些字段适合作为 UI/metadata 的轻量预览，不适合作为最终合成答案的唯一证据来源。

### 5. 缓存可能固化旧短答案

文件路由有 `file-route` cache fingerprint。修复后如果 prompt、answer client、synthesis client 或 context shape 发生变化，需要避免命中旧版本短答案缓存。

---

## Goals

1. `tabular_qa` 在生产配置存在时走真实 LLM 表格回答路径，不再默认退回 4 条 fallback。
2. `tabular_qa` 对 summary 类问题提供足够表格证据，包括字段、统计、代表性行和可解释边界。
3. `tabular_qa` 对普通定向问题保留结构化执行的精确性，同时补充足够上下文让答案不是只剩少量样例。
4. `hybrid_qa` 最终答案优先由统一 LLM synthesis 生成，覆盖 PDF、表格、KB 的可用证据。
5. `hybrid_qa` 普通问答不再默认只保留每类一条 evidence。
6. `hybrid_qa` summary 问答在证据充分时能输出多条方法、结果、结论要点。
7. 文件证据优先规则不变：KB 只能补充验证，不能覆盖 PDF 或表格直接证据。
8. 流式输出、metadata、steps、cache 行为保持兼容。
9. 无模型配置、模型失败、文件不可读时仍有可解释降级结果。

---

## Non-Goals

1. 不引入复杂表格查询语言或 SQL 引擎。
2. 不一次性解决所有表格推理能力问题，例如跨 sheet join、公式计算、图表解析。
3. 不让 hybrid synthesis 绕过文件证据边界去编造结论。
4. 不把 KB 内容写成 PDF 或表格事实。
5. 不删除现有规则 synthesis；它仍作为 fallback 和测试稳定路径。
6. 不改变 SSE 协议和前端渲染 contract。

---

## Options Considered

### Option A: 只放宽现有规则抽点和 fallback 限制

优点：

1. 改动最小。
2. 不需要新增上游 client。
3. 测试修改范围较小。

缺点：

1. `tabular_qa` 仍然没有真实 LLM 回答路径。
2. `hybrid_qa` 仍然是规则拼装，不是真正跨来源合成。
3. 放宽条数只能缓解，不会解决语义整合不足。

结论：

不作为主方案，只作为规则 fallback 的补充优化。

### Option B: 只修 `tabular_qa`，让 hybrid 继续复用子答案

优点：

1. 能解决表格单独问答短的问题。
2. 对 hybrid 影响间接，风险较低。

缺点：

1. Hybrid final answer 仍被 synthesis contract 的 1200 字 context 和规则抽点压缩。
2. 子答案变长不等于最终答案会保留这些细节。
3. `pdf+table+kb` 仍无法稳定生成统一高质量答案。

结论：

不足以解决用户看到的混合 QA 最终答案过短问题。

### Option C: Tabular 接入 LLM，Hybrid 接入统一 LLM synthesis

优点：

1. 同时解决表格分支和混合最终合成两个根因。
2. 保留现有结构化执行能力，避免纯 prompt 读全表导致查数不稳。
3. 能把规则 synthesis 降为兜底，不影响无模型环境和现有降级测试。
4. 与已存在的 `PatentPdfAnswerClient` 和 shared upstream pool 方向一致。

缺点：

1. 需要新增 client、prompt、缓存版本和更多测试。
2. 需要谨慎控制 metadata 和 stream 兼容。
3. 上游 token 消耗会上升，需要有上下文预算策略。

结论：

推荐采用 Option C。

---

## Recommended Design

### 1. 新增 `PatentTabularAnswerClient`

在 `patent/server/patent/tabular_service.py` 中新增或拆出一个 OpenAI-compatible 表格回答 client，行为对齐 `PatentPdfAnswerClient`。

职责：

1. 从环境变量读取配置。
2. 支持注入 shared `httpx.Client`。
3. 构建 chat completions 请求。
4. 支持 stream 和非 stream，第一阶段可以先实现非 stream，再保持 `PatentTabularService` 对外仍按现有 `emit_text_chunks` 输出。
5. 记录 `model`、`max_tokens`、`top_p` 等参数用于日志和 cache fingerprint。

环境变量建议：

1. 优先使用 `PATENT_OPENAI_API_KEY`、`PATENT_OPENAI_BASE_URL`、`PATENT_OPENAI_MODEL`。
2. 复用 `PATENT_OPENAI_USE_SHARED_ENV` 逻辑。
3. 新增可选 `PATENT_TABULAR_MAX_TOKENS`，默认不低于 2500。
4. 新增可选 `PATENT_TABULAR_TOP_P`，默认沿用 `PATENT_OPENAI_TOP_P` 或 0.95。

构造语义：

1. `PatentTabularService(answer_question_fn=...)` 仍然优先使用测试注入函数。
2. `PatentTabularService(answer_client=...)` 使用真实 client。
3. 如果二者都没有，才走 fallback。

### 2. App bootstrap 注入表格回答能力

`server_fastapi/app.py` 中在构建 `PatentExecutor` 时同时注入：

1. `PatentPdfService(answer_client=pdf_answer_client)`
2. `PatentTabularService(answer_client=tabular_answer_client)`
3. 后续 hybrid synthesis client

如果 shared HTTP client 可用，tabular answer client 使用同一个 shared client。

如果 tabular client bootstrap 失败，不应导致整个 app 不可用。应降级为当前 fallback 行为，并在 component status 或日志中记录 degraded 状态。

### 3. 明确表格 answer mode

`PatentTabularService.execute()` 的 metadata 中应区分：

1. `table_execution_llm`
2. `table_execution_fallback`
3. `table_execution_unavailable`

当前统一写 `table_execution_summary` 不利于定位生产是否真的调用了模型。

兼容策略：

1. 可以保留 `answer_mode="table_execution_summary"` 作为旧字段语义。
2. 新增 `answer_backend` 或 `generation_backend`，例如 `llm`、`fallback`、`unavailable`。
3. 或直接升级 `answer_mode`，同时更新 tests 和 contract。

推荐：

新增 `answer_backend`，减少对现有 contract 的破坏。后续大版本再调整 `answer_mode`。

### 4. 构建更完整的表格上下文

当前 `execution_context` 只适合短问答。新增一个面向 LLM 的 `table_answer_context` 或 `table_synthesis_context`。

上下文应包含：

1. 文件名、sheet 名、sheet 数量。
2. 每个参与 sheet 的列名和行数。
3. planner/executor 的结构化结果。
4. 代表性行。
5. 与问题相关的 top rows。
6. 数值列的基本统计摘要，例如 count、min、max、mean。
7. 对 summary 问题，允许覆盖更多代表性行。
8. 对 lookup/aggregate/compare 问题，优先保留命中行和聚合结果。

预算建议：

1. `table_evidence_context` 继续保留 1200 字左右，用于 metadata/UI。
2. `table_answer_context` 用于 tabular LLM，默认 12000 字，可由 `PATENT_TABULAR_MAX_CONTEXT_CHARS` 配置。
3. `table_synthesis_context` 用于 hybrid synthesis，默认 6000 到 12000 字，可由 `PATENT_HYBRID_TABLE_CONTEXT_CHARS` 配置。

实现边界：

1. 不要求把整张大表完整塞给模型。
2. 不做跨 sheet join。
3. 不做复杂统计库依赖；基础统计可直接基于已加载 rows 计算。

### 5. 表格 prompt 调整

`_build_patent_tabular_prompt()` 保留现有结构，但需要补充：

1. 不允许只复述 1 到 2 条样例。
2. 如果上下文包含多个字段和多条统计结果，需要尽量覆盖关键字段、数值范围、代表性差异。
3. summary 问题按文献总结结构输出，有证据的章节尽量 3 到 5 条。
4. 普通问题按 `结论 / 证据 / 对比 / 限制` 输出，证据应覆盖关键字段、统计、样例行。
5. 明确表格无法支持背景或方法时，应说明边界，但不能因此忽略主要发现和结果。

### 6. 新增 `PatentHybridSynthesisClient`

新增 hybrid synthesis client，优先作为 `hybrid_qa` 的最终答案生成器。

推荐位置：

1. 可放在 `patent/server/patent/file_routes.py` 周边，但该文件已经较大。
2. 推荐新增 `patent/server/patent/hybrid_synthesis.py`，避免继续扩大 `file_routes.py`。

职责：

1. 接收 normalized synthesis contract。
2. 构建 summary 或普通问答 prompt。
3. 调用 OpenAI-compatible chat completions。
4. 返回最终答案文本。
5. 失败时抛出可捕获异常，由调用方回退到 `synthesize_patent_hybrid_answer()`。

构造语义：

1. 支持 `from_env(http_client=shared_http_client)`。
2. 支持注入 mock client / answer function 以便测试。
3. 暴露 runtime signature，用于 cache fingerprint。

### 7. Ownership / Wiring

`PatentHybridSynthesisClient` 的持有和传递路径必须在设计层先固定，避免 implementation 只覆盖 file-only hybrid 或只覆盖 KB merge。

推荐归属：

1. `server_fastapi/app.py` 负责 bootstrap `PatentHybridSynthesisClient`。
2. `PatentExecutor.__init__()` 新增可选参数 `hybrid_synthesis_service` 或 `hybrid_synthesis_client`，并持有为实例字段。
3. `PatentExecutor._execute_file_route()` 在调用 `dispatch_patent_file_route()` 时把该对象显式下传。
4. `dispatch_patent_file_route()` 新增 `hybrid_synthesis_service` 参数，并继续下传给 `_build_hybrid_result()`。
5. `PatentExecutor._merge_file_and_kb_results()` 也显式接收同一个 `hybrid_synthesis_service`，保证 `pdf+kb`、`table+kb`、`pdf+table+kb` 与 file-only hybrid 走同一套最终 synthesis 逻辑。

签名变化要求：

1. `dispatch_patent_file_route(..., hybrid_synthesis_service: Any | None = None, ...)`
2. `_build_hybrid_result(..., hybrid_synthesis_service: Any | None = None, ...)`
3. `PatentExecutor._merge_file_and_kb_results(..., hybrid_synthesis_service: Any | None = None, ...)`

这样可以保证：

1. file-only hybrid 的最终答案由 file route 内部完成 synthesis。
2. 带 KB hybrid 的最终答案由 executor merge 阶段使用同一个 synthesis service 完成。
3. app bootstrap 只有一个 owner，shared HTTP client 生命周期也只有一个 owner。

### 8. 扩展 hybrid synthesis contract

`build_patent_hybrid_synthesis_contract()` 需要新增字段：

1. `pdf_synthesis_context`
2. `table_synthesis_context`
3. `kb_synthesis_context`
4. `synthesis_prompt_version`
5. `available_sources`
6. `source_answer_modes`

保留现有字段：

1. `pdf_answer`
2. `tabular_answer`
3. `kb_answer`
4. `pdf_evidence_context`
5. `table_execution_context`
6. `kb_evidence_context`
7. `kb_reference_instruction`
8. `file_precedence`

字段用途区分：

1. `*_evidence_context` 是 compact metadata/UI 预览。
2. `*_synthesis_context` 是最终合成用的更完整证据。
3. `*_answer` 是子分支自然语言答案。

### 9. Internal vs External Payload Boundary

完整 `*_synthesis_context` 不能直接暴露到对外 API `metadata` 中，也不应无边界地塞进现有 `metadata.synthesis_contract`。

边界规则：

1. 对外 API `metadata` 只保留 compact 字段，例如：
   1. `pdf_evidence_context`
   2. `table_evidence_context`
   3. `kb_evidence_context`
   4. `hybrid_synthesis_backend`
   5. `hybrid_synthesis_prompt_version`
   6. `*_context_chars`
2. `metadata.synthesis_contract` 如继续保留，只允许放 public/compact 字段，不放完整 `pdf_synthesis_context`、`table_synthesis_context`、`kb_synthesis_context`。
3. 完整 synthesis 上下文只允许存在于内部载体 `_hybrid_internal_state` 或等价内部对象中。
4. `_hybrid_internal_state` 允许被 file-route cache 使用，但它是服务内部字段，不属于 API contract。
5. 任何对外返回给前端或 FastAPI contract test 的 payload，都必须在最终返回前去掉 `_hybrid_internal_state`。

内部传递要求：

1. file-only hybrid 不需要把完整 synthesis context 存进返回 payload；`_build_hybrid_result()` 在本次请求内直接调用 synthesis service 即可。
2. 带 KB hybrid 需要跨 file route 与 executor merge 传递完整上下文，因此允许 file route 返回内部字段 `_hybrid_internal_state`，由 executor merge 消费。
3. executor merge 完成后，最终返回结果必须移除 `_hybrid_internal_state`。
4. file-route cache fingerprint 仍需要纳入 synthesis prompt version 和上下文预算；但 compact metadata contract 不应因此膨胀。

### 10. Hybrid 普通问答 prompt

普通问答最终 prompt 应输出：

1. `## 结论`
2. `## 证据`
3. `## 对比`
4. `## 限制`

要求：

1. 先直接回答用户问题。
2. `证据` 至少按来源覆盖可用 PDF、表格、KB。
3. 如果 PDF 和表格都有证据，不要只保留其中一个来源。
4. 如果 KB 参与，只能作为补充验证或背景，不得覆盖文件证据。
5. 如果来源冲突，明确冲突点，并以文件证据优先。
6. 不输出 raw execution markers，例如 `匹配工作表:`、`执行操作:`、`source_scope=`。
7. 不输出 `真实 PDF 总结：`、`真实表格总结：` 这类测试/中间标签。

### 11. Hybrid summary prompt

Summary 问题最终 prompt 应输出：

1. `## 研究目的和背景`
2. `## 研究方法/实验设计`
3. `## 主要发现和结果`
4. `## 结论和意义`
5. `## 局限性`
6. `注*：所有总结内容均严格基于文件原文中明确提到的信息，未添加任何通用知识或推测内容。`

要求：

1. 有证据的章节尽量 3 到 5 条。
2. 表格证据优先进入 `主要发现和结果`，除非表格本身包含方法字段。
3. PDF 证据优先补充背景、方法、结果、结论。
4. KB 只作为交叉验证或补充背景，不替代文件结论。
5. 缺少证据的章节明确说明证据不足。
6. 不把表格 schema 行当作研究发现。

### 12. 保留并强化规则 fallback

现有 `synthesize_patent_hybrid_answer()` 保留为 fallback。

需要调整：

1. 普通问答 fallback 可以保留多条 PDF evidence、多条 table evidence，而不是每类只取一条。
2. 增大 summary 候选条数，避免过早丢失细节。
3. 对最终答案正文减少 120 字 lead 裁剪，裁剪只用于极长单行或日志预览。
4. 保持“不输出 raw table structure”的过滤。
5. 保持 shell placeholder 过滤。

Fallback 仍应满足：

1. 无证据时返回明确不可用说明。
2. 文件与 KB 冲突时能给出冲突说明。
3. 不使用通用知识补写事实。

### 13. Cache fingerprint 更新

文件路由 cache fingerprint 需要包含：

1. `PatentTabularAnswerClient` runtime signature。
2. `PatentHybridSynthesisClient` runtime signature。
3. tabular prompt version。
4. hybrid synthesis prompt version。
5. context budget 关键配置。

避免以下情况：

1. 新代码仍命中旧短答案缓存。
2. prompt 调整后返回旧版本格式。
3. fallback 与 LLM 路径共用同一个 fingerprint。

### 14. Streaming 行为

第一阶段不要求 tabular 和 hybrid 直接把上游流式 token 逐字转发。

兼容目标：

1. `tabular_qa` 可以先 buffer LLM 完整结果，再用 `emit_text_chunks()` 输出。
2. `hybrid_qa` 文件-only 路径仍保留 PDF/table preview，再发送 final hybrid answer。
3. `pdf+kb`、`table+kb`、`pdf+table+kb` 仍由 executor merge 后发送 final hybrid answer。
4. structured stream event 的 `content_source` 和 `content_role` 不变。

后续可单独优化真实 token streaming。

---

## Data Flow

### `tabular_qa`

目标流程：

1. `PatentExecutor.execute_with_progress()`
2. `dispatch_patent_file_route()`
3. `PatentTabularService.execute()`
4. 加载 workbook
5. 构建 `execution_context`
6. 构建 `table_answer_context`
7. 调用 `PatentTabularAnswerClient`
8. 执行 summary 或 four-block 结构修复
9. emit final chunks
10. 返回 answer、metadata、steps、used files

降级流程：

1. 没有 answer client 或 client 失败
2. 调用 `_table_fallback_answer()`
3. 结构化包装
4. metadata 标记 `answer_backend=fallback`

### `hybrid_qa` without KB

目标流程：

1. `dispatch_patent_file_route()`
2. `_build_hybrid_result()`
3. PDF branch 生成 `pdf_answer` 和 `pdf_synthesis_context`
4. Table branch 生成 `tabular_answer` 和 `table_synthesis_context`
5. `PatentHybridSynthesisClient` 生成 final answer
6. client 失败时回退到 `synthesize_patent_hybrid_answer()`
7. emit final hybrid answer

### `hybrid_qa` with KB

目标流程：

1. 文件分支先生成 file result、public metadata 和内部 `_hybrid_internal_state`。
2. executor 调用 KB service。
3. `_merge_file_and_kb_results()` 从 `_hybrid_internal_state` 读取 `pdf_synthesis_context` 与 `table_synthesis_context`，并加入 KB answer 和 KB synthesis context。
4. `PatentHybridSynthesisClient` 生成最终 answer。
5. client 失败时回退到规则 synthesis。
6. executor 在最终返回前移除 `_hybrid_internal_state`。
7. emit final hybrid answer。

---

## Compatibility

### API response compatibility

保持以下字段：

1. `answer_text`
2. `route`
3. `query_mode`
4. `source_scope`
5. `steps`
6. `metadata`
7. `timings`
8. `used_files`
9. `selected_file_ids`
10. `file_selection`
11. `kb_enabled`

新增 metadata 字段：

1. `answer_backend`
2. `table_answer_context_chars`
3. `table_synthesis_context_chars`
4. `hybrid_synthesis_backend`
5. `hybrid_synthesis_prompt_version`
6. `hybrid_synthesis_context_chars`

保留 compact context 字段：

1. `pdf_evidence_context`
2. `table_evidence_context`
3. `kb_evidence_context`

### Internal-only payload compatibility

新增内部字段：

1. `_hybrid_internal_state`

约束：

1. 它不是对外 API contract 的一部分。
2. 它可以存在于 server 内部 file-route payload 和 file-route cache 中。
3. 它不能出现在最终 FastAPI 响应中。
4. tests 需要覆盖“executor merge 后 internal state 被剥离”。

### Test injection compatibility

保留：

1. `PatentTabularService(answer_question_fn=...)`
2. `PatentPdfService(answer_question_fn=...)`
3. 可以注入 fake hybrid synthesis client。

测试中使用 `answer_question_fn` 时，不要求真实上游配置。

### Degraded mode compatibility

无上游配置时：

1. `tabular_qa` 仍返回 fallback。
2. `hybrid_qa` 仍返回规则 synthesis。
3. metadata 必须显示 fallback/backend 状态，便于排查。

---

## Testing Strategy

### Unit tests

新增或更新：

1. `test_patent_file_routes.py`
2. `test_patent_executor.py`
3. `test_patent_tabular_executor_renderer.py`
4. `fastapi_contract/test_ask_contract.py`
5. 可新增 `test_patent_hybrid_synthesis.py`

### Required test cases

`tabular_qa`：

1. 有 `answer_client` 时调用 LLM 路径，不走 fallback。
2. 无 `answer_client` 时走 fallback，并标记 `answer_backend=fallback`。
3. answer client 报错时走 fallback，并保留可解释 metadata。
4. summary 表格问题输出文献总结结构。
5. 普通表格问题输出四段结构。
6. 表格上下文包含 planner/executor 结果、统计摘要、代表性行。
7. `table_evidence_context` 仍保持 compact。
8. `table_synthesis_context` 或等价字段比 compact context 更完整。

`hybrid_qa`：

1. `pdf+table` 有 synthesis client 时调用 LLM synthesis。
2. synthesis client 失败时回退规则 synthesis。
3. 最终答案包含多条 PDF 和表格证据，而不是只保留一个 lead。
4. summary hybrid 输出五段文献总结结构。
5. `pdf+table+kb` 中 KB 作为补充出现，但不覆盖文件结论。
6. 原始 markers 不进入最终答案，例如 `匹配工作表:`、`执行操作:`、`source_scope=`。
7. shell placeholder 不进入最终答案。
8. 无 usable evidence 时仍返回不可用说明并标记 hybrid step error。
9. file-only hybrid 与 KB merge hybrid 都使用同一个 injected synthesis service。
10. 最终 FastAPI 返回结果不包含 `_hybrid_internal_state`。

Cache：

1. prompt version 变化会改变 file-route cache fingerprint。
2. tabular backend 从 fallback 变为 llm 时 fingerprint 不相同。
3. hybrid synthesis backend 从 rules 变为 llm 时 fingerprint 不相同。

Streaming：

1. `tabular_qa` streamed chunks 拼接后等于 `answer_text`。
2. `pdf+table` structured streaming 仍先有 PDF/table preview，再有 hybrid final。
3. 带 KB hybrid final 只在 merge 后输出。

FastAPI contract：

1. `tabular_qa` 返回 final answer、metadata、steps contract 不破坏。
2. `hybrid_qa` 返回 final answer、metadata、references contract 不破坏。
3. degraded mode 下仍返回 200 和明确 fallback/unavailable answer。

---

## Rollout Plan

### Phase 1: Tabular LLM path

1. 新增 `PatentTabularAnswerClient`。
2. `PatentTabularService` 支持 `answer_client`。
3. app bootstrap 注入 tabular client。
4. metadata 标记 `answer_backend`。
5. 补 tabular tests。

### Phase 2: Tabular context expansion

1. 新增表格上下文 builder。
2. 添加统计摘要和更多代表性行。
3. 区分 compact evidence context 和 synthesis/answer context。
4. 补 context budget tests。

### Phase 3: Hybrid synthesis client

1. 新增 `PatentHybridSynthesisClient`。
2. 扩展 synthesis contract。
3. 文件-only hybrid 使用 LLM synthesis。
4. 带 KB executor merge 使用 LLM synthesis。
5. 保留规则 fallback。

### Phase 4: Cache, streaming, contract hardening

1. 更新 cache runtime signature。
2. 更新 file-route cache tests。
3. 更新 structured streaming tests。
4. 更新 FastAPI contract tests。
5. 回归现有 PDF/table/hybrid 文件路由测试。

---

## Acceptance Criteria

整体完成条件：

1. `tabular_qa` 在上游模型配置可用时不再默认走 4 条 fallback。
2. `tabular_qa` 的 answer metadata 能明确区分 llm 与 fallback。
3. `tabular_qa` summary 问题在表格证据充分时输出多条结构化要点。
4. `hybrid_qa` 在上游模型配置可用时使用 LLM synthesis 生成最终答案。
5. `hybrid_qa` final answer 能覆盖 PDF、表格、KB 的可用证据，不再只取每类一条短 lead。
6. `hybrid_qa` 无上游或上游失败时仍能使用规则 fallback 返回可解释结果。
7. 所有新增 tests 通过。
8. 现有文件路由、executor、FastAPI contract tests 不出现行为回归。

---

## Open Questions

1. `PatentTabularAnswerClient` 是否应与 `PatentPdfAnswerClient` 抽一个共享 base class，还是先保持独立实现以降低改动面？
2. Hybrid synthesis client 是否放在新文件 `hybrid_synthesis.py`，还是先留在 `file_routes.py` 降低导入变更？
3. `table_answer_context` 与 `table_synthesis_context` 是否使用同一个 builder，只用不同 budget 参数？
4. 是否需要把 prompt version 明确暴露到 config，还是代码常量即可？
5. 上游模型不可用时，是否需要在 health/status 中显示 tabular/hybrid generation degraded？

推荐默认答案：

1. 第一阶段不抽 base class，避免过度重构。
2. 新增 `hybrid_synthesis.py`，避免继续扩大 `file_routes.py`。
3. 使用同一个 context builder，不同 budget 参数。
4. prompt version 用代码常量。
5. app bootstrap 日志先记录 degraded，health/status 可作为后续增强。
