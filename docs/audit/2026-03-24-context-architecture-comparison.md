# 2026-03-24 当前系统上下文架构 vs 主流 LlamaIndex 模式对照

## 范围

本文聚焦四个服务在“问答上下文”上的职责与边界：

- `frontend-vue`
- `gateway`
- `public-service`
- `fastQA`
- `highThinkingQA`

对照对象是以 `LlamaIndex` 为代表的主流多轮 RAG / 文件会话做法。

本文不讨论模型效果优劣，只讨论：

- 上下文从哪里来
- 怎么裁剪
- 怎么传递
- 什么应该给 LLM
- 什么应该只给编排器 / 路由 / 检索层

---

## 结论先行

### 1. 当前系统已经具备“统一会话 authority + 文件路由状态”基础

这部分其实方向是对的：

- 前端始终围绕一个 `conversation_id`
- `gateway` 负责根据文件状态和意图决定 route / source_scope
- `public-service` 统一保存 conversation authority
- `fastQA / highThinkingQA` 都可以从 authority 拉会话历史

这说明“统一会话、分后端执行”的骨架已经成立。

### 2. 当前最大的不对齐，不是文件路由，而是上下文分层不够彻底

与主流模式对比，目前差口主要在三点：

1. `fastQA` 普通 `kb_qa` 虽然会读取 authority 上下文，但主执行链几乎没有真正消费聊天历史
2. `public-service authority summary` 目前基本是空壳，无法承担“长对话压缩摘要”职责
3. 当前系统已经保存了很多 `route/source_scope/steps/used_files` 之类的 metadata，但还没有明确把它们分成：
   - 给 LLM 的
   - 给 retriever 的
   - 给路由层的
   - 给日志/UI 的

### 3. 与 LlamaIndex 主流方案相比，当前系统更像“有 authority 的多后端问答”，还不是“明确分层的会话-检索-状态架构”

主流模式一般明确分成：

- `chat history`
- `rewrite / condense`
- `retrieval filters / source scope`
- `retrieved context`
- `runtime state`

当前系统已经具备这些元素，但很多元素还没有完全落在清晰的层里。

---

## 一、当前系统的真实数据流

## 1. 前端：携带当前聊天历史 + 文件状态

前端每次发问时会带：

- `question`
- `conversation_id`
- `chat_history`
- `pdf_context`
  - `newly_uploaded_ids`
  - `all_available_ids`
  - `selected_ids`
  - `last_focus_ids`
  - `last_turn_route`
- 当前选择的 ask mode

证据：
- [Home.vue](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue#L1047)
- [Home.vue](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue#L1058)
- [api.js](/home/cqy/worktrees/highThinking/frontend-vue/src/services/api.js#L480)

这意味着前端已经天然区分了两类信息：

- 会话消息历史 `chat_history`
- 文件/路由状态 `pdf_context`

这个分法本身是合理的。

## 2. gateway：把文件状态转成 route/source_scope，而不是自己做 QA 上下文构造

`gateway` 的核心职责是：

- 结合当前问题与会话文件列表做文件意图解析
- 产出：
  - `route`
  - `turn_mode`
  - `source_scope`
  - `selected_file_ids`
  - `file_selection`
  - `used_files / execution_files`
- 如果是文件/混合问题，把 `actual_mode` 强制改成 `fast`

证据：
- [route_decision.py](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py#L13)
- [route_decision.py](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py#L15)
- [route_decision.py](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py#L49)
- [qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L55)

这和主流模式是相符的：

- `gateway` 更像 route/state resolver
- 不直接决定最终 prompt 长什么样

## 3. public-service：统一 authority，但 summary 还很弱

`public-service` 当前会把所有消息存成一份统一 conversation document：

- `recent_turns`
- `conversation_state`
  - `last_turn_route`
  - `last_focus_file_ids`
  - `last_assistant_trace_id`
- `summary`

证据：
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1082)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1089)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1118)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1223)

但问题在于：

- `recent_turns` 已经可用
- `conversation_state` 已经可用
- `summary` 目前只有最小结构：`short_summary / memory_facts / open_threads`

也就是：

- 短期历史有了
- 路由状态有了
- 长期压缩记忆已经有最小骨架，但还没有进化成更强的语义摘要

## 4. highThinkingQA：已经接近主流模式

`highThinkingQA` 当前上下文链路相对成熟：

1. 读 authority 快照
2. 取出 `chat_history + summary`
3. 和请求体里的 `chat_history` 做 overlap merge
4. 对历史做 budget 控制
5. 形成 `ConversationContext`
6. 用这个 context 做：
   - `rewrite_question`
   - `run_agent(conversation_context=...)`

证据：
- [chat_persistence.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L338)
- [conversation_context_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation_context_service.py#L109)
- [conversation_context_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation_context_service.py#L131)
- [ask_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/ask_service.py#L489)
- [ask_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/ask_service.py#L533)
- [ask_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/ask_service.py#L676)

`highThinkingQA` 当前实际上已经具备：

- 会话历史读取
- 历史去重合并
- 历史预算裁剪
- rewrite 使用历史
- 主 agent 使用历史

这和主流 conversational RAG / agent 模式是比较接近的。

## 5. fastQA：有 authority 读取，但普通 kb_qa 主链上下文利用不足

`fastQA` 当前也会：

1. 读取 authority 快照
2. 把 `chat_history` 替换成 authority 返回的最近历史
3. 把 `snapshot/summary/conversation_state` 塞到 `options`

证据：
- [qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L198)
- [qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L205)
- [qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L210)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L226)

但普通 `kb_qa` 最终执行请求 `QaKbRequest` 并没有携带 `chat_history`：

证据：
- [models.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/models.py#L58)

也就是说：

- fastQA 有“读上下文”能力
- 但普通 `kb_qa` generation-driven 主链并没有真正消费这份多轮历史

这是目前和主流模式最明显的不对齐之一。

---

## 二、当前系统 vs 主流 LlamaIndex 模式对照表

| 维度 | 当前系统 | LlamaIndex 主流模式 | 结论 |
| --- | --- | --- | --- |
| 会话 authority | `public-service` 统一保存 conversation document | 通常有统一 memory/chat store | 基本对齐 |
| 最近会话历史 | `recent_turns` 已有，`fast/highThinking` 都可读取 | `chat_history` / memory blocks | 基本对齐 |
| 长期摘要 | `summary` 当前接近空壳 | 常见做法是 memory summary / blocks / flush | 未对齐 |
| 路由状态 | `last_turn_route`、`last_focus_file_ids` 已有 | 主流也会保留运行状态 / tool state | 基本对齐 |
| 文件状态输入 | 前端 `pdf_context` + gateway resolver | 主流常用 metadata/doc selection/tool routing | 基本对齐 |
| source scope | `gateway` 明确产出 `pdf/table/pdf+kb/...` | 主流常体现为 metadata filters / retriever selection | 思路对齐，表达层不同 |
| 问题改写 | `highThinkingQA` 有 rewrite；`fastQA kb_qa` 目前没有标准多轮 rewrite | 主流 conversational RAG 常先 condense question | 部分对齐 |
| 历史裁剪 | `highThinkingQA` 有预算裁剪；`fastQA` 仅请求截断 | 主流通常有 token budget/trim/summary | 部分对齐 |
| 检索范围控制 | `gateway + source_scope + selected_file_ids` | metadata filters / per-doc agent | 方向对齐 |
| 检索证据与聊天历史分层 | 概念上已有，但系统边界未彻底固化 | 主流明确分层 | 部分未对齐 |
| 中间步骤是否进入上下文 | 持久化里保存了 `steps/timings/...`，但消费边界未完全制度化 | 主流通常不把 scratchpad 直接当 chat history | 需要明确边界 |
| `fastQA` 多轮普通问答上下文利用 | 读取了 authority，但主链未真正用上 | 主流会真正把 history 用于 rewrite/answer | 未对齐 |
| `highThinkingQA` 多轮上下文利用 | 已实际用于 rewrite + agent | 主流常见做法 | 对齐度较高 |
| 多文件会话 | 当前偏 gateway route + selected files | 主流少量文件用 filters，多文件复杂任务常用 doc agents | 部分对齐 |
| 文件+KB 混合 | 已有 `source_scope` 和 hybrid route | 主流也常按 source selection 分检索再 synthesis | 基本对齐 |

---

## 三、逐项差口说明

## 1. `public-service` 还不是成熟的 context memory service

当前它更像：

- authority message store
- route/file focus state store

还不是：

- 会话摘要服务
- 长期记忆服务
- 历史压缩服务

这会导致：

- `highThinkingQA` 和未来 `fastQA` 都无法依赖一个高质量 summary
- 只能靠 recent turns 做短期上下文

## 2. `fastQA` 的普通问答上下文链是“读到了，但没真正进入主推理”

这会导致两个后果：

1. `fastQA kb_qa` 多轮能力弱
2. authority 虽然统一了，但 fast 链路没有充分受益

对比主流模式，这是一个明确差口。

## 3. 文件状态目前更多停留在 route 层，还没完全下沉为 retriever 过滤策略规范

当前系统里，文件/混合问答已经有：

- `selected_file_ids`
- `used_files`
- `source_scope`
- `last_focus_file_ids`

但这些信息更多体现为：

- gateway 路由决策
- file route payload
- downstream 执行参数

还没有被抽象成统一的“context filter contract”。

而在主流模式里，这类信息更常被明确定义为：

- metadata filters
- document scope
- per-doc tool selection

## 4. 中间步骤的持久化价值已经有了，但“不给 prompt”这条制度还没被系统化写清楚

当前保存 `steps / timings / references / used_files / trace_id` 是有价值的：

- UI 能显示步骤
- 调试定位更方便
- authority 能知道上一轮发生了什么

但如果后续要统一上下文架构，必须明确：

- 这些内容不是 `recent_turns_for_llm`
- 这些内容主要属于 `execution_trace`

否则以后任何一条链路都可能误把它们混进 prompt。

---

## 四、主流模式对“带文件会话”的启发

## 1. 单文件问答

主流做法：

- 会话历史只保留最终 user/assistant
- 当前文件选择状态不直接自然语言描述给模型
- 先把文件选择转换成检索过滤条件
- 本轮只在当前文件中检索
- 检索结果作为本轮 context 送给 LLM

### 当前系统对应状态

- `selected_file_ids` 已有
- `source_scope` 已有
- `gateway` 已能判定 file route

差口不在“有没有文件状态”，而在“是否形成统一的 retriever filter contract”。

## 2. 多文件比较

主流做法：

- 少量文件：metadata filter + 合并 synthesis
- 多文件复杂问题：每个文档独立 agent / tool，再由上层 agent 组合

### 当前系统对应状态

- 已能支持多文件选择和 `hybrid_qa`
- 但暂时更偏单后端内的汇总执行
- 还没有系统化的“per-document retriever / tool”抽象

## 3. 文件 + KB 混合

主流做法：

- 明确 source selection
- 文件源和 KB 源分开检索
- 最后在 synthesis 层融合

### 当前系统对应状态

- `source_scope` 已经显式存在
- `gateway` 已经承担 source selection 入口职责
- 这部分总体方向是对的

所以当前改造优先级不在“是否保留 fastQA 执行文件 QA”，而在于：

- 把文件状态下沉成更标准的检索 contract
- 把会话历史、检索证据、运行状态彻底分层

---

## 五、总体判断

## 已对齐的部分

1. 统一 `conversation_id`
2. `gateway` 负责 route / source_scope / file selection
3. `public-service` 作为 authority
4. `highThinkingQA` 已经具备较标准的多轮上下文构造流程
5. 文件/混合问答已经是“状态驱动路由”，不是前端硬编码分支

## 未对齐的核心部分

1. `fastQA kb_qa` 多轮上下文尚未真正进入主执行链
2. authority summary 仍然很弱
3. 文件状态尚未沉淀成统一的 retriever filter contract
4. execution trace 和 prompt context 的边界尚未被系统级制度化

## 最关键的结论

当前系统已经具备“统一 authority + route/source_scope + 多后端执行”的基础能力。

真正需要补的，不是把系统推翻重写，而是把以下三条系统化：

1. `history` 只管最终对话
2. `state` 只管路由与文件焦点
3. `retrieval context` 只服务本轮答案生成

这三条一旦固化，当前架构是可以继续往主流模式演进的。

## 2026-03-24 已完成收口

### Gateway

- 已兼容 `last_focus_file_ids`，与 `public-service conversation_state` 的主命名对齐。
- 已通过测试锁定：`chat_history` 原样下传；`pdf_context` 只在 gateway 内部消费，不向 QA backend 透传。
- 已通过测试锁定：文件选择歧义在 gateway 直接短路，不进入上游 QA backend。
- 已新增最小 mixed conversation 回归：`thinking -> hybrid(pdf+kb) -> follow-up file turn`。

### highThinkingQA

- `ConversationContext` 现在在构建期与执行期双重清洗，`recent_turns` 只保留 `role/content`。
- `summary` 只保留 prompt-facing 语义字段，`steps/timings/file_selection/source_usage/trace_id` 不再进入 rewrite 或 agent 输入。
- 这意味着当前 `highThinkingQA` 已满足“history/state/trace 分层”的最小边界要求。

### 验证

- `conda run --no-capture-output -n agent pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_mixed_conversation_context.py -q` -> `48 passed`
- `conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_conversation_context_service.py highThinkingQA/tests/test_prompt_boundary.py highThinkingQA/tests/test_ask_service_executor.py -q` -> `39 passed`
