# fastQA 普通问答对照缺口文档

本文只对照普通问答，即用户提交问题后，系统走 `kb_qa` 路由直到输出最终答案的全过程。不包含 PDF 问答、表格问答、混合问答，也不讨论 `gateway` 额外逻辑。

对照基线：

- 旧版：`/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/` 与 `.../qa_kb/`
- 当前：`/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py` 与 `.../modules/qa_kb/`
- 旧版源码链路说明：`docs/legacy_fastapi_normal_qa_pipeline.md`

## 1. 结论

当前 `fastQA` 的普通问答已经具备旧版五阶段主骨架：

1. stage1 预回答与检索规划
2. stage2 DOI 检索
3. stage2.5 MD 扩展
4. stage3 PDF chunk 加载
5. stage4 基于证据流式合成答案

但它还**没有与旧版完全对齐**。最关键的缺口有六类：

1. **入口编排层缺失**：当前没有旧版 `ask_gateway -> qa_kb_service.iter_answer_events()` 这一层统一分发、兼容和持久化闭环。
2. **stage1 / stage2 语义漂移**：尤其 stage2，当前实现比旧版明显简化，检索精度护栏少了一整层。
3. **真实并发上下文没有服务端注入**：旧版会把实际 `active_stream_count` 注入 stage2，当前默认只吃请求里带入的值。
4. **SSE / done 契约漂移**：当前 `done.references`、元数据补齐方式、步骤归一化位置与旧版不同，且可能出现双 `metadata`。
5. **错误与限流语义漂移**：旧版 `ask_stream` 拿不到并发槽位时直接返回 HTTP `429`，当前流式接口会返回一段 SSE 错误流。
6. **对话持久化缺失**：当前普通问答链路里没有发现与旧版等价的 user / assistant 消息落库闭环。

如果目标是“能回答问题”，当前骨架已经可用；如果目标是“普通问答行为与旧版完全等价”，当前仍有明显缺口，尤其集中在 **入口层、stage2 检索层、并发上下文层、流式契约层、历史持久化层**。

## 2. 请求入口与调度层

### 2.1 旧版实现

旧版普通问答入口不是直接进入生成链，而是：

1. `ask_gateway/api.py` 统一接收 `/api/v1/ask_stream`
2. `ask_gateway/service.py` 做请求增强、并发槽位控制、用户消息持久化
3. `_dispatch_kb()` 构造 `QaKbRequest`
4. `qa_kb_service.iter_answer_events()` 根据 `QA_QUERY_PIPELINE_MODE` 决定走 `new / legacy / request`
5. `AskStreamTap` 汇总 `content / steps / references / timings`
6. `conversation_service.persist_assistant_summary()` 持久化 assistant summary

### 2.2 当前 fastQA

当前普通问答入口在 `fastQA/app/routers/qa.py`：

- 路由直接接 `/api/ask_stream`、`/api/v1/ask_stream`、`/api/fast/ask_stream`
- 经过 `adapt_gateway_ask_payload()` 适配后，`kb_qa` 直接调用 `qa_kb_service.iter_generation_answer_events()`
- 当前 `fastQA/app/modules/qa_kb/service.py` 只保留了 `iter_generation_answer_events()`，**没有**旧版的：
  - `resolve_pipeline_mode()`
  - `iter_answer_events()`
  - `iter_legacy_answer_events()`
- 当前适配层还额外施加了 `fast-only` 约束：`requested_mode` 或 `actual_mode` 只要不是 `fast` 就直接报错

### 2.3 缺口结论

这意味着当前 `fastQA` 普通问答缺了旧版入口层的四类能力：

1. **模式分发缺失**
   当前只能走生成链，不能像旧版那样由 `QA_QUERY_PIPELINE_MODE` 控制 `new / legacy / request`。
2. **旧版兼容层缺失**
   旧版是 `ask_gateway` 统一补齐请求、流事件、持久化；当前这些逻辑散落在 `router` 本身。
3. **编排闭环缺失**
   旧版普通问答从“入流”到“summary 持久化”是一条完整链；当前只是“请求适配 + 直接执行”。
4. **服务边界变窄**
   当前入口天然假设自己只服务 `fast` 模式，这和旧版统一 ask 编排层的中性边界不同。

这个缺口是结构性的。后面如果只补 stage2，而不补入口编排层，行为仍然不会完全等价。

## 3. 请求体契约、默认值与错误语义

### 3.1 已对齐部分

当前 `AskRequest` 仍保留了旧版普通问答必需字段：

- `question`
- `chat_history`
- `conversation_id`
- `pdf_context`
- `use_generation_driven`
- `n_results_per_claim`

`request_adapter.py` 也保留了 `n_results_per_claim` 默认值为 `10`。

### 3.2 偏差与缺口

1. **字段来源已变化**
   当前入口优先适配 `gateway` 风格字段，如 `requested_mode / actual_mode / route / turn_mode / options / used_files`；旧版普通问答入口没有这些字段依赖。
2. **旧版默认增强逻辑未完整迁移**
   旧版默认增强发生在 `ask_gateway/service.py`，不是 `router`。当前 `router` 自行补 `metadata/done`，职责边界不同。
3. **`chat_history.slice(-10)` 的前端约定没有在 fastQA 服务端明确兜底**
   旧版前端固定只传最近 10 轮；当前服务端接受任意长度 `chat_history`，未看到等价保护。
4. **`trace_id` 生成策略不一致**
   旧版默认会在服务端生成唯一 `uuid`；当前 `_trace_id()` 在请求头和 payload 都没有时，会固定落到 `fastqa-pending`。
   这会直接影响日志追踪与问题排查。
5. **请求校验语义更严格**
   旧版 `question` schema 默认是空字符串，后续由编排层再统一 trim/处理；当前 `AskRequest` 直接要求 `min_length=1`，并限制 `max_length=4000`。
   这会导致边界输入的返回路径不同。

### 3.3 错误与限流语义差异

1. **旧版 busy 行为**
   `ask_gateway/api.py` 在拿不到并发槽位时，直接返回 HTTP `429` + JSON。
2. **当前 fastQA busy 行为**
   对流式接口，当前会返回一段 SSE：`metadata -> error -> done`，而不是 HTTP `429`。
3. **适配失败行为也不同**
   当前 `/api/v1/ask_stream` 这类流式接口在请求适配失败时，同样返回 SSE 错误流；旧版 ask 入口的失败主要体现为正常 HTTP 错误响应。

这部分会直接影响前端、网关、监控和压测脚本对异常路径的判断。

## 4. Stage1 对照

### 4.1 已对齐部分

当前 `fastQA/app/modules/generation_pipeline/stage1_planning.py` 与旧版都保留了：

- LLM 生成 `deep_answer`
- 结构化解析 `retrieval_claims`
- JSON 解析失败时降级为“仅预回答”

### 4.2 明确漂移

当前 stage1 比旧版更“宽松”，这会改变后续检索语义：

1. **`deep_answer` 回退键更多**
   当前接受 `deep_answer / pre_answer / answer / draft_answer / final_answer / response / content / analysis`；
   旧版只按约定结构读取，失败时直接降级。
2. **`retrieval_claims` 回退键更多**
   当前接受 `retrieval_claims / claims / queries / dict.items`
3. **最关键差异：当前会在 claim 为空时强制回退到“用户原问题作为检索主张”**
   旧版不会这样做。旧版如果没有 claim，会直接进入“仅预回答”路径。

### 4.3 影响

这不是小差异。它会直接改变后续 stage2 是否触发，以及触发后检索的查询内容。

- 旧版：claim 为空 -> 不检索 -> 直接输出预回答
- 当前：claim 为空 -> 用原问题硬凑 claim -> 继续进入检索链

这会造成普通问答结果与旧版不一致，尤其是 stage1 输出质量不稳定时。

## 5. Stage2 对照

### 5.1 已对齐部分

当前 `fastQA` 仍保留了 stage2 的几个基础特征：

- 基于 `retrieval_claims` 逐条并行检索
- 返回 `claim_to_results / documents / metadatas / distances`
- 有 stage2 cache + singleflight 骨架

### 5.2 当前缺失能力

相较旧版 `fastapi-version/backend/app/modules/generation_pipeline/stage2_retrieval.py`，当前 `fastQA/app/modules/generation_pipeline/stage2_retrieval.py` 缺了整层“检索护栏”：

1. **缺少 LLM 检索查询生成**
   旧版不是直接用 `claim + keywords` 查，而是会生成更适合检索的 query。
2. **缺少 `normalize_user_question_for_stage2()` / `preprocess_retrieval_query()` 预处理**
   旧版会先剥离对话包装语、噪声上下文，再清洗查询。
3. **缺少强制关键词注入**
   旧版会把问题中的关键术语强制注入查询，避免 query 漂移。
4. **缺少元素锁定**
   旧版会对材料体系中的关键元素做锁定，例如 Ti、Mg、Mn 等，防止召回错体系文献。
5. **缺少 rerank 能力接入**
   旧版可按配置开启 `use_rerank / rerank_candidates`。
6. **缺少检索结果相关性校验**
   旧版并不是拿到向量召回就直接用，还会做额外校验。
7. **缺少查询扩展路径**
   旧版代码保留了 `QA_STAGE2_QUERY_EXPANSION_ENABLED` 相关路径；当前实现没有这层能力。
8. **缺少更完整的 runtime toggles**
   当前只有简单 `QA_STAGE2_PARALLEL_WORKERS`；旧版还有动态 worker 策略与多组 feature flag。

### 5.3 并发上下文缺口

这里有一个之前容易漏掉、但实际上很关键的差异：

- 旧版 `_dispatch_kb()` 会把 `active_stream_count=self.active_stream_count(runtime)` 注入 `QaKbRequest`
- 当前 `fastQA` 只是把请求体里的 `active_stream_count` 继续往下传
- 如果客户端不传，当前值通常就是 `None`

这意味着即使当前 stage2 代码里保留了“根据活跃流数调整并发”的参数入口，它拿到的也不是服务端真实并发，而是一个可为空的客户端输入。

### 5.4 影响

这是当前普通问答最大的准确性缺口。

当前 stage2 的核心逻辑基本是：

- 从 claim 拿 `claim_text + keywords`
- 拼接 query
- 直接调用 `literature_expert.search()`

而旧版 stage2 是“查询构造 + 护栏注入 + rerank + 并发策略 + 结果校验”的组合链。两者都叫 stage2，但不是一个复杂度级别。

如果目标是“普通问答结果质量对齐旧版”，stage2 需要优先补齐。并且补时不能只补检索逻辑，还要补 `active_stream_count` 的服务端注入。

## 6. Stage2.5 与 Stage3 对照

### 6.1 基本情况

这两段目前是对齐度最高的部分。

当前 orchestrator 与旧版都保留了：

- stage2.5 MD 原文扩展
- 命中阈值后跳过 stage3 PDF
- 否则按 DOI 加载 PDF chunk
- MD 命中后与 PDF chunk 合并

### 6.2 仍存在的轻微漂移

1. **阶段提示文案不同**
   例如当前 stage3 提示更短，旧版会显示更详细的“加载多少篇文献、提取 top chunk”等信息。
2. **部分命中提示未完全对齐**
   旧版对 stage2.5 命中统计、stage3 skip 原因的流式提示更完整。

这些属于体验层差异，不是普通问答准确性的第一优先级问题。

## 7. Stage4 对照

### 7.1 已对齐部分

当前 stage4 仍保留旧版关键能力：

- 基于 PDF chunk 流式生成最终答案
- `QA_STAGE4_REFERENCE_TOPK`
- `QA_STAGE4_MIN_CITATIONS`
- `QA_STAGE4_ELEMENT_GUARD`
- `QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS`
- two-stage synthesis / structure-only synthesis
- 最终按引用 DOI 生成 reference 信息

### 7.2 偏差

1. **默认最少引用数存在漂移风险**
   当前 `QA_STAGE4_MIN_CITATIONS` 默认读取为 `3`；旧版配置基线是 `10`。如果配置没补齐，最终引用篇数会直接偏少。
2. **`model_identity_shortcut` 文案不一致**
   旧版固定返回 “运行在 claude-4.5-sonnet-thinking 模型上的 AI 助手”；当前返回的是 “fastQA 已切到 OpenAI-compatible 协议传输层”。
   这属于明确行为漂移。
3. **最终 `references` 在路由层被压平成 DOI 字符串**
   stage4 内部仍会构造 reference 对象，但 `qa.py -> _done_event()` / `normalize_references()` 会把它收敛成 `list[str]`。

### 7.3 影响

对最终答案正文质量而言，stage4 主能力大体还在；但对“引用数量是否足够”“引用结构是否保真”“前端历史记录能否复用旧版 metadata”而言，当前仍不完全等价。

## 8. SSE 契约与前端消费对照

### 8.1 当前已做的事

当前 `fastQA` 用两层方式补流事件：

1. orchestrator 发 `thinking / metadata / content / done`
2. `stream_contract.py` 把 `thinking` 归一化成 `step`
3. `router/qa.py` 在 `_iter_qa_frames()` 里补默认 `metadata`，并在末尾兜底 `done`

### 8.2 与旧版的差异

1. **旧版是在 `ask_gateway` 用 `AskStreamTap` 统一收敛；当前是在 `router` 里临时补齐**
2. **当前 `done.references` 被标准化为 DOI 字符串列表**
   旧版生成链输出的 `references` 可以保留更丰富结构，后续持久化也直接带入 summary。
3. **当前 metadata / done 的补齐时机不同**
   旧版是编排层包装；当前是 `fastQA` 自己的 route 层包装。
4. **步骤标题和消息拆分规则是新实现**
   当前 `normalize_stream_event()` 会按正则把 “阶段X：说明” 拆成 `title / detail / status`，这不是旧版同一实现。
5. **当前可能出现两次 metadata**
   因为 `_iter_qa_frames()` 只要先收到的不是 `metadata`，就会先补一个默认 `metadata(query_mode=route)`；
   而生成链在 stage4 之前还会再发一个真实 `metadata(query_mode=生成驱动检索（PDF溯源）...)`。
   这和旧版的统一包装方式不一样，前端若按“只认首个 metadata”处理，就可能读到错误的 `query_mode`。

### 8.3 结论

当前前端能收到“可消费的步骤流”，但 SSE 契约实现方式与旧版不同。若后续要做完全兼容，应该回到统一编排层，而不是继续在 `router` 堆补丁。

## 9. 对话历史与持久化对照

这部分当前是**明确缺失**。

旧版普通问答完整闭环是：

1. `persist_user_request()` 先写入 user 消息
2. 流式完成后 `AskStreamTap` 汇总 `assistant_content / steps / references / timings / route / file_selection`
3. `persist_assistant_summary()` 写入 assistant 消息

当前 `fastQA` 普通问答代码里没有看到等价闭环：

- 没有普通问答入口前的 user message 落库
- 没有普通问答流完成后的 assistant summary 落库
- 没有旧版那种把 `steps / references / timings / trace_id / file_selection` 一并写入消息 metadata 的逻辑

因此当前 `fastQA` 普通问答即使能流式回答，也还不能说与旧版“聊天记录层”完全对齐。

## 10. 优先级排序

按“对普通问答结果一致性影响”排序，建议修复顺序如下：

1. **P0：补齐 stage2 检索护栏**
   这是准确率影响最大的缺口。
2. **P0：把 stage1 回退策略改回旧版语义**
   claim 为空时不能强制继续检索。
3. **P0：补回服务端真实 `active_stream_count` 注入**
   不然 stage2 的动态并发策略就没有真实依据。
4. **P1：补回入口编排层**
   至少要恢复 `iter_answer_events()` 这一层，而不是 `router` 直接进生成链。
5. **P1：补齐普通问答持久化闭环**
   包括 user / assistant 消息与 `steps / references / timings`。
6. **P1：校准错误/限流语义与 trace_id 生成策略**
   尤其流式接口的 `429` 行为与默认 `trace_id`。
7. **P1：校准 stage4 配置默认值**
   尤其 `QA_STAGE4_MIN_CITATIONS`。
8. **P2：收敛 SSE 契约**
   让 `metadata/done/step` 的输出方式与旧版保持同一边界。
9. **P2：修正文案级漂移**
   如 `model_identity_shortcut`、阶段提示文本。

## 11. 最终判断

如果只看“是否具备普通问答五阶段骨架”，当前 `fastQA` 已经具备。

如果看“是否与旧版普通问答从请求进入到答案输出全过程完全对齐”，当前还不成立，主要差在：

- 入口编排层少了一层
- stage2 检索精度护栏少了一层
- 服务端并发上下文注入少了一层
- SSE 契约实现边界变了
- 错误与限流语义变了
- 对话历史持久化闭环缺失

其中最不能回避的是 **stage2、`active_stream_count`、持久化**。这三块不补，普通问答只能算“形似”，还不能算“行为等价”。
