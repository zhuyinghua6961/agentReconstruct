# 2026-03-24 问答上下文通用原则与 LlamaIndex 文件会话模式调研

## 范围

本文档回答两件事：

1. 当前系统里，问答上下文应该遵循什么通用原则。
2. 以 `LlamaIndex` 为代表的主流框架，在“带文件的多轮会话”里通常如何整理上下文、检索上下文和运行时状态。

本文是规划/调研文档，不改业务代码。

---

## 结论先行

### 1. 通用原则

建议把问答相关上下文明确拆成三层：

1. `会话历史（for LLM）`
- 只保留最终的 `user / assistant` 消息
- 属于“用户真的说过什么、系统最终回答了什么”
- 这是多轮问答真正应该喂给模型的历史

2. `检索上下文（for retrieval + answer synthesis）`
- 本轮被选中的文档/文件范围
- 本轮检索回来的 chunks / nodes / 表格片段 / 原文片段
- 这是“本轮证据”，不应直接回写成长期 chat history

3. `运行时状态（for orchestration only）`
- `last_turn_route`
- `last_focus_file_ids`
- `selected_file_ids`
- `used_files`
- `requested_mode / actual_mode`
- 中间 step、timings、rerank、引用校验过程、trace

这层主要服务于路由、文件恢复、调试和前端展示，不应该原样拼进下一轮 prompt。

### 2. 对 LLM 的直接输入原则

推荐边界：

- 传给 LLM 的：最终会话历史 + 本轮检索证据 + 必要摘要
- 不传给 LLM 的：中间处理过程、调试日志、rerank 细节、引用检查细节、内部 workflow 状态

### 3. LlamaIndex 的主流实现与这个原则一致

从官方文档看，LlamaIndex 典型实现也是分层的：

- `chat_history / memory`：管理对话历史
- `retriever / query_engine`：管理本轮文档检索
- `context prompt`：把本轮检索结果放进系统提示词
- `metadata / filters / doc agents`：负责文件/文档范围控制
- `memory blocks / workflow context`：处理长对话和运行时状态

它不是“把所有中间过程都塞进 chat history”，而是：

- 会话历史用于多轮理解
- 历史先改写问题，再去检索
- 文件范围靠 metadata filter 或 per-document tool / agent 控制

### 4. 对“带文件会话”的主流做法

以 LlamaIndex 为例，带文件会话通常有三种主路子：

1. `单文档/已选中文档`
- 当前文件先变成索引或 retriever
- 多轮会话时，用 `chat_history` 辅助改写当前问题
- 最后只在当前文件范围内检索

2. `多文件比较/多文件问答`
- 每个文件一个 document agent / tool
- 顶层 agent 先选文件，再调文件级 agent
- 不直接把所有文件全文和整段历史一起塞给一个 prompt

3. `文件 + 通用知识库混合问答`
- 先从会话和文件选择状态里确定本轮 source scope
- 再决定检索哪些源：文件索引、知识库索引，或两者都检索
- 最终在 synthesis 阶段合并证据

这和我们目前 `gateway -> route/source_scope -> fastQA` 的方向是兼容的。

---

## 一、通用上下文原则

## 1. 什么才算“会话历史”

对问答系统来说，真正应该进入多轮上下文的，是用户视角可见的最终对话：

- 用户问题
- 系统最终回答

如果系统中间做了这些动作：

- query rewrite
- retrieval planning
- 多路检索
- rerank
- citation alignment
- answer draft
- 引用校验
- trace / timings / debug

这些都不属于“会话历史”，而属于运行时中间态。

### 推荐定义

#### `recent_turns_for_llm`
- 结构：`[{role, content}]`
- 来源：已持久化的最终 user/assistant 消息
- 用途：多轮理解、问题改写、少量连续对话能力

#### `retrieval_context_for_turn`
- 结构：本轮检索证据
- 来源：向量检索、表格执行结果、PDF 片段、引用对齐结果
- 用途：仅服务本轮答案生成

#### `conversation_state_for_routing`
- 结构：本轮/最近一轮路由状态
- 例如：`last_turn_route`、`last_focus_file_ids`、`selected_file_ids`
- 用途：帮助下一轮路由，而不是帮助模型继续“聊天”

#### `execution_trace_not_for_prompt`
- 结构：中间步骤与诊断信息
- 用途：日志、前端步骤展示、debug、审计
- 原则：不直接拼进主 prompt

## 2. 为什么不能把中间处理过程直接混进 chat history

### 原因 A：污染下一轮语义

中间过程常常包含：

- 草稿答案
- 未验证引用
- 暂时性判断
- 检索噪声
- 工具原始输出

这些如果被当成“历史对话”喂回去，模型会把它们当事实继续推理。

### 原因 B：把系统实现细节暴露给模型

比如：

- rerank 过程
- citation check 失败原因
- timeout / retry
- debug step

这类内容本来只属于运行时，不属于用户意图。

### 原因 C：会显著增加 token 成本与不稳定性

如果把完整步骤流都喂回去：

- token 很快膨胀
- prompt 污染更严重
- fast / thinking / file / hybrid 多模式互相影响会更明显

## 3. 推荐的统一规则

### 给 LLM 的
- 最近若干轮 `user / assistant` 最终消息
- 必要时一份老历史摘要
- 本轮检索证据

### 给 retriever 的
- 当前问题
- 或基于 chat history 改写后的 standalone question

### 给路由层的
- 最近一轮 route
- 文件焦点信息
- 已选中文件
- source scope

### 给日志 / UI / 审计的
- steps
- timings
- trace id
- rerank / citation-check 内部细节

---

## 二、LlamaIndex 的典型上下文实现

## 1. ContextChatEngine：当前消息检索，本轮上下文进系统提示词

LlamaIndex 官方对 `ContextChatEngine` 的描述是：

- 使用 retriever 检索上下文
- 把上下文放进 system prompt
- 再由 LLM 生成回答

来源：
- `ContextChatEngine` 官方 API 说明
  - `https://developers.llamaindex.ai/python/framework-api-reference/chat_engines/context/`

关键点：

- 会话历史和检索上下文不是一回事
- 检索出的 context 是本轮 prompt 的一部分
- 它不是直接写回 chat history

这对应的理念就是：

- `chat history` 负责“你刚才在说什么”
- `retrieved context` 负责“本轮有哪些证据”

## 2. CondenseQuestionChatEngine：先用历史改写问题，再检索

LlamaIndex 官方对 `CondenseQuestionChatEngine` 的描述是：

- 先根据 conversation context 和 latest user message 生成 standalone question
- 再用这个 standalone question 去 query engine 检索并回答

来源：
- `CondenseQuestionChatEngine` 官方 API 说明
  - `https://developers.llamaindex.ai/python/framework-api-reference/chat_engines/condense_question/`

关键点：

- 历史不是直接拿去做最终回答的全部输入
- 先服务于 query rewrite / question condensation
- 检索环节用的是改写后的问题

这正是现在主流 conversational RAG 的典型范式。

## 3. CondensePlusContext：历史改写 + 检索上下文 两步都做

LlamaIndex 官方的 `CondensePlusContextChatEngine` 进一步把两步合在一起：

1. 先把“会话历史 + 最新问题”压缩成 standalone question
2. 用 retriever 为 standalone question 构造 context
3. 把 context 和对话消息一起送给 LLM

来源：
- `CondensePlusContextChatEngine` 官方 API 说明
  - `https://developers.llamaindex.ai/python/framework-api-reference/chat_engines/context/`

这实际上就是当前最主流的多轮 RAG 模式之一。

### 对我们的启发

如果后面要统一 `fastQA / highThinkingQA` 的多轮上下文，最稳的结构就是：

1. 会话历史只保留最终 user/assistant 回合
2. 先做 rewrite / condense
3. 再检索
4. 再把本轮检索结果做 synthesis

而不是把“历史 + 中间 step + 本轮证据”一锅炖进 prompt。

---

## 三、LlamaIndex 的 memory 模型说明了什么

## 1. 默认短期记忆就是“能放进 token 限制内的最近消息”

LlamaIndex 官方 memory 文档说明：

- 默认 short-term memory 存储最近能塞进 token limit 的消息
- 当 chat history 超过阈值时，最老消息会被 flush 掉
- flush 后可以送到 long-term memory blocks 处理

来源：
- LlamaIndex Memory Guide
  - `https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/`

关键参数：

- `token_limit`
- `chat_history_token_ratio`
- `token_flush_size`

### 对我们的启发

这说明主流框架默认并不是“无上限带全历史”。
而是：

- 最近消息有限保留
- 超出的消息做 flush / summary / long-term memory

## 2. 长期记忆不是“完整原始步骤堆积”

LlamaIndex 的 long-term memory 通过 memory blocks 表达，例如：

- fact extraction
- vector memory
- static memory

这说明主流思路是：

- 老历史不一定原封不动带回去
- 更常见的是抽事实、做摘要、做向量化记忆

### 推论

这进一步支持“中间过程不应作为原始聊天历史沉淀到 prompt 主体”。
应该沉淀的是：

- 最近对话
- 结构化摘要
- 抽取后的长期记忆

而不是整段运行时轨迹。

这是基于 LlamaIndex memory 设计的合理推论。

---

## 四、LlamaIndex 如何处理文件/文档范围

## 1. 文件范围首先靠 Document / Node metadata 管理

LlamaIndex 官方文档说明：

- `Document` 可以带 `metadata`
- 这些 metadata 会传播到 source nodes
- 默认 metadata 会参与 embedding 和 LLM 调用

来源：
- Defining and Customizing Documents
  - `https://developers.llamaindex.ai/python/framework/module_guides/loading/documents_and_nodes/usage_documents/`

这说明在 LlamaIndex 里，“文件上下文”不是只靠路径传递的。
更标准的做法是：

- 每个文件/文档携带稳定 metadata
- chunk/node 继承这些 metadata
- 检索和回答阶段都能利用这些 metadata

典型 metadata：

- `file_id`
- `file_name`
- `doc_id`
- `source_type`
- `section`
- `page`
- `title`
- `category`

## 2. 文件选择通常不是靠会话历史硬编码，而是靠 metadata filter

LlamaIndex 官方 vector store API 有 `MetadataFilters`。
这类过滤器就是给“只在某些文档范围内检索”用的。

来源：
- Vector Store API / `MetadataFilters`
  - `https://developers.llamaindex.ai/python/framework-api-reference/storage/vector_store/`

这意味着，如果用户在当前会话里选中了某个文件或某组文件，主流实现通常会：

- 先从会话状态拿到已选文件 ID / doc_id
- 再把这些 ID 转成 retriever 的 metadata filters
- 最终只在被选中文档范围内检索

### 对我们的启发

我们系统里的：

- `selected_file_ids`
- `last_focus_file_ids`
- `used_files`
- `source_scope`

最合理的落点不是直接拼入聊天 prompt，
而是先转成“检索范围约束”。

也就是：

- `会话状态 -> 检索过滤条件`
- 不是 `会话状态 -> 大段自然语言 prompt`

## 3. 对多文件问答，LlamaIndex 更倾向“每个文件一个工具/agent”

LlamaIndex 官方 Multi-Document Agents 示例的核心架构是：

- 为每个 document 建一个 document agent
- 顶层 agent 在这些 document agents 之间做工具检索和组合回答

来源：
- Multi-Document Agents (V1)
  - `https://developers.llamaindex.ai/python/examples/agent/multi_document_agents-v1/`

它明确覆盖的问题类型包括：

- 针对某一个文档 QA
- 比较不同文档
- 对某一个文档总结
- 比较多个文档的总结

### 对我们的启发

对于“多 PDF 比较”“多个文件联合问答”，主流做法不是：

- 把所有文件全文和整段历史一起塞给一个 prompt

而更接近：

- 每个文件有独立检索能力
- 顶层路由/agent 决定调用哪些文件
- 最后做 cross-doc synthesis

这和我们后续如果要强化 `pdf+pdf`、`pdf+table`、`pdf+table+kb` 的设计是同方向的。

---

## 五、带文件会话的上下文整理，按主流方案怎么做

下面给一个推荐模型。

## 1. 单文件会话

### 用户场景
- “这篇文献里厚电极为什么会出现液相浓差极化？”
- “这份表格里 2024 年哪个月波动最大？”

### 推荐上下文结构

#### 会话历史
- 最近若干轮 `user/assistant`
- 只包含最终消息

#### 文件状态
- 当前选中的 `file_id/doc_id`
- 当前 source scope = `pdf` 或 `table`

#### 检索执行
- 把 `file_id/doc_id` 转成 metadata filter
- 仅在该文件范围内检索 chunk / sheet / rows

#### 回答阶段
- 用“最终会话历史 + 本轮检索结果”生成答案

### 不推荐
- 把之前的检索日志和步骤消息继续喂进 prompt
- 把整个文件原文直接混入 chat history

## 2. 多文件比较会话

### 用户场景
- “对比这三篇文献的倍率性能退化机理”
- “比较这两个表格的成本结构变化”

### 推荐上下文结构

#### 会话历史
- 仍然只保留最终 user/assistant 回合

#### 文件状态
- 当前激活文件集合，例如 `[doc_a, doc_b, doc_c]`

#### 检索策略
- 方案 A：单 retriever + metadata filter 限定文件集合
- 方案 B：每个文件一个 retriever / tool，再由上层 agent 组合

#### synthesis
- 最后统一做 comparison synthesis
- 输出时区分每个文件的证据与结论

### 主流偏好

文件数少时：
- 单 retriever + metadata filter 就够

文件数变多、任务变复杂时：
- 更偏 document agent / tool routing

## 3. 文件 + 知识库混合会话

### 用户场景
- “结合这篇论文和知识库，解释为什么高倍率下厚电极更容易极化”
- “结合这张表和知识库，总结 2024 年异常波动的原因”

### 推荐上下文结构

#### 会话历史
- 最近 user/assistant 最终消息

#### 检索范围
- 文件范围：来自 selected files / file focus state
- KB 范围：来自知识库索引

#### 问题改写
- 可先基于历史做 standalone question

#### 检索
- 文件索引检索
- KB 索引检索
- 或者上层先判断 source scope 再组合

#### synthesis
- 文件证据与 KB 证据分开组织
- 最终答案再融合

### 主流关键点

混合问答的关键不是“多带一点聊天历史”，而是：

- source scope 清楚
- retriever 范围清楚
- synthesis 阶段清楚

---

## 六、对当前系统的直接建议

## 1. 会话历史保持克制

建议 `public-service recent_turns` 继续只代表最终 user/assistant 回合。
不要把中间步骤也持久化成“下一轮默认会吃进去的聊天历史”。

## 2. 文件状态走 routing state，不走自然语言 chat history

建议把：

- `selected_file_ids`
- `last_focus_file_ids`
- `last_turn_route`
- `source_scope`

主要用于：

- gateway 路由
- retriever filter
- 文件上下文恢复

而不是拼成一大段自然语言背景再喂给 LLM。

## 3. fastQA / highThinkingQA 最终都应该对齐到同一个原则

即：

- `history` 只放最终对话
- `context` 只放本轮证据
- `state` 只给编排器
- `trace` 只给日志与前端步骤展示

## 4. 多文件能力可以逐步向“document tool / agent”演进

对于：

- 单文件问答
- 少量文件混合

当前 `route + source_scope + selected_files` 的模式已经够用。

对于：

- 多文档比较
- 多文档总结
- 文件 + 文件 + KB 混合深推理

更接近 LlamaIndex 主流方案的方向是：

- 文件级 retriever / tool
- 顶层 planning / routing
- 最终统一 synthesis

---

## 七、最终结论

### 1. 通用上下文原则

最稳的原则就是：

- 最终会话历史进入 LLM
- 本轮检索证据进入本轮 prompt
- 路由状态只给编排层
- 中间步骤不进 chat history

### 2. LlamaIndex 的主流设计与这个方向一致

它没有把“聊天历史、检索结果、运行时状态”混成一个概念，而是明确分层。

### 3. 带文件会话的关键不在于多带更多历史，而在于三件事

1. 当前问题先不要跑偏，必要时做 rewrite / condense
2. 当前文件范围要清楚，最好转成 metadata filters / doc selection
3. 本轮检索证据与长期会话历史要分开

---

## 参考资料

以下为本次调研使用的官方资料：

1. LlamaIndex `ContextChatEngine`
- https://developers.llamaindex.ai/python/framework-api-reference/chat_engines/context/

2. LlamaIndex `CondenseQuestionChatEngine`
- https://developers.llamaindex.ai/python/framework-api-reference/chat_engines/condense_question/

3. LlamaIndex Memory Guide
- https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/

4. LlamaIndex Documents / Metadata Guide
- https://developers.llamaindex.ai/python/framework/module_guides/loading/documents_and_nodes/usage_documents/

5. LlamaIndex Vector Store Metadata Filters
- https://developers.llamaindex.ai/python/framework-api-reference/storage/vector_store/

6. LlamaIndex Multi-Document Agents (V1)
- https://developers.llamaindex.ai/python/examples/agent/multi_document_agents-v1/

---

## 补充说明

文中关于“不要把中间处理过程直接作为 chat history 喂回 prompt”的部分，属于：

- 基于当前主流框架设计方式的工程归纳
- 结合 LlamaIndex 的 memory / retriever / context 分层模型做出的实现建议

也就是说，这是有官方设计依据的工程推论，不是逐字原文规定。
