# QA Context Architecture Design

**Date:** 2026-03-24

## Scope

本设计只覆盖“问答上下文架构”，不改动以下大方向：

- 文件 QA / 混合 QA 继续由 `fastQA` 执行
- `highThinkingQA` 继续只负责自己的 thinking 普通 QA
- `gateway` 继续负责前端统一入口和后端分发
- `public-service` 继续是 conversation authority

本设计要解决的是：

1. 问答上下文应该如何分层
2. 各层由哪个服务负责
3. `fastQA / highThinkingQA` 如何对齐到一致原则
4. 文件会话、混合会话如何把文件状态转成标准检索上下文

---

## Goal

建立一套统一的 QA 上下文架构，使系统中的多轮问答、文件问答、混合问答都遵循同一组原则：

- `最终会话历史` 只服务于多轮理解
- `文件/路由状态` 只服务于 source selection 与检索范围控制
- `检索证据` 只服务本轮回答生成
- `中间执行轨迹` 只服务日志、UI 和调试

最终目标不是让所有服务做同样的事，而是让每个服务的边界清晰、输入输出可预测。

---

## Non-Goals

本轮设计不直接包含：

1. 改写现有文件 QA / 混合 QA 的业务策略
2. 引入新的 document-agent 框架
3. 重写 `gateway` 路由系统
4. 把所有会话能力迁出 `public-service`
5. 把所有中间步骤从持久化里删除
6. 直接实现长期记忆系统或复杂 memory block 系统

---

## Design Summary

目标架构将上下文拆成四层：

1. `Conversation History Layer`
- 只保存最终 `user/assistant` 对话
- 由 `public-service authority` 提供
- 供 `fastQA / highThinkingQA` 读取

2. `Conversation Routing State Layer`
- 保存最近一轮 route、文件焦点、选择集合等状态
- 由 `public-service authority` 提供
- 供 `gateway`、`fastQA`、`highThinkingQA` 用于路由和文件恢复

3. `Retrieval Context Layer`
- 由各 QA 服务在本轮内部生成
- 包含检索 query、metadata filters、retrieved chunks、表格执行结果等
- 不直接持久化为下一轮的 chat history

4. `Execution Trace Layer`
- 保存步骤、timings、trace、引用校验等过程信息
- 主要用于日志、前端步骤展示、debug
- 不进入下一轮 LLM prompt 主体

这四层中：

- `public-service` 负责 1 和 2 的权威数据
- `gateway` 负责 2 的消费与 route 决策
- `fastQA / highThinkingQA` 负责 3 的生成与消费
- 各服务共同产出 4，但 4 只作辅助信息，不作为会话语义历史

---

## Core Principles

## 1. `history != state != retrieval_context != trace`

这条是整个设计的根。

### `history`
- 只包含最终 user/assistant 消息
- 是用户可感知的对话历史

### `state`
- 只包含 routing / file focus / source selection 所需状态
- 例如：`last_turn_route`、`last_focus_file_ids`

### `retrieval_context`
- 是本轮的证据上下文
- 包括本轮文件范围、本轮 KB 范围、本轮 chunks、本轮表格执行结果

### `trace`
- 是本轮执行过程
- 例如：steps、timings、rerank、citation-check、debug events

禁止把四层混成一个“上下文大包”。

## 2. `history` 进入 LLM，`state` 进入编排器，`trace` 进入 UI/日志

### 给 LLM 的
- `recent_turns_for_llm`
- 可选 `summary_for_llm`
- 本轮 `retrieval_context`

### 给路由层 / 编排器的
- `conversation_state`
- 文件选择状态
- 当前 `source_scope`

### 给前端 / 诊断的
- `steps`
- `timings`
- `trace_id`
- 其它执行轨迹

## 3. 文件选择应转成标准检索范围，而不是自然语言描述

选中的文件不应该被“翻译成一大段背景说明”再丢给 prompt。
更合理的做法是：

- 文件选择状态 -> 标准 source contract
- source contract -> retriever filters / file scope
- retriever filters -> 本轮证据

这样才能避免 prompt 污染和路由歧义。

---

## Target Architecture

## 1. Frontend Responsibilities

前端只负责发送三类输入：

1. `question`
2. `chat_history`
- 当前页面上的最终 user/assistant 可见消息
- 只作为客户端冗余上下文和未完全同步期间的补充

3. `pdf_context`
- `selected_ids`
- `newly_uploaded_ids`
- `all_available_ids`
- `last_focus_ids`
- `last_turn_route`

前端不负责：

- 拼接 prompt
- 推断 source scope 语义细节
- 决定最终检索范围

## 2. Gateway Responsibilities

`gateway` 继续做统一入口，但上下文职责要严格限定为：

### 保留职责
1. 会话文件解析
2. file intent 解析
3. route 决策
4. source scope 决策
5. execution file 归一化
6. 代理请求到后端

### 新增/强化职责
1. 将文件状态归一成标准上下文输入契约
2. 不直接构造 QA prompt
3. 不对 `chat_history` 做业务语义加工
4. 将 `selected_file_ids / source_scope / file_selection` 明确作为“状态输入”传下游

### 输出契约
`gateway -> QA backend` 应统一包含：

- `conversation_id`
- `chat_history`
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `source_scope`
- `selected_file_ids`
- `used_files`
- `execution_files`
- `file_selection`
- `trace_id`

这部分当前已经大致存在，本设计要求的是“制度化边界”，不是推倒重来。

## 3. Public-Service Responsibilities

`public-service` 是 authority，不是问答执行器。

### 负责
1. conversation message authority
2. recent turns snapshot
3. conversation routing state
4. assistant summary metadata 持久化
5. 后续可演进的 compact summary / long-term summary

### 不负责
1. retrieval context 生成
2. prompt 组装
3. 文件问答检索策略
4. 问答阶段缓存

### Authority 输出应拆分为两类

#### A. `recent_turns_for_llm`
- 只返回最终 user/assistant 消息
- 不包含 steps / tool logs / trace message

#### B. `conversation_state`
- `last_turn_route`
- `last_focus_file_ids`
- `last_assistant_trace_id`
- 后续可增加：`last_source_scope`、`last_selected_file_ids`

### `summary` 的目标

当前 `summary` 几乎为空。
目标应分两阶段演进：

#### Phase 1
- 保持兼容结构
- 补齐简单会话摘要
  - `short_summary`
  - `open_threads`
  - `memory_facts`

#### Phase 2
- 在 authority 层引入真正可消费的 compact summary
- 供 `fastQA / highThinkingQA` 在长会话里替代远古 turns

## 4. fastQA Responsibilities

`fastQA` 继续负责：

- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

但上下文架构需要按下面方式调整。

### 4.1 普通 `kb_qa`

#### 当前问题
- 会读取 authority
- 但主执行链几乎没真正用 `chat_history`

#### 目标
让 `kb_qa` 与 `highThinkingQA` 在“上下文层次”上对齐：

1. 读取 authority `recent_turns + summary + conversation_state`
2. 与 request `chat_history` 合并去重
3. 做 budget 裁剪
4. 先做 optional rewrite / condense
5. 再跑 retrieval / synthesis

### 4.2 文件/混合 QA

对于 `pdf_qa / tabular_qa / hybrid_qa`：

#### 会话历史的作用
- 主要用于理解当前追问、省略指代、上一轮比较对象
- 不负责直接指定文件范围

#### 文件范围的作用
- 通过 `selected_file_ids + source_scope + used_files` 决定 retriever / loader 范围
- 不依赖历史自然语言反推文件范围

#### retrieval context
- 由文件问答执行器本轮内部生成
- 例如：
  - PDF chunks
  - table execution results
  - KB chunks

#### execution trace
- steps / timings / rerank / doi insertion / citation verification
- 保留给 UI / persistence metadata
- 不回灌成下一轮 `recent_turns`

### 4.3 fastQA 的目标输入结构

为 `fastQA` 统一引入内部上下文对象，例如：

```python
FastQaConversationContext = {
  "recent_turns": [...],
  "summary": {...},
  "conversation_state": {...},
  "source_scope": "pdf+kb",
  "selected_file_ids": [...],
  "used_files": [...],
  "execution_files": [...],
}
```

再由不同 route 消费不同字段：

- `kb_qa`：主要消费 `recent_turns/summary`
- `pdf_qa`：主要消费 `recent_turns + execution_files`
- `hybrid_qa`：同时消费 `recent_turns + source_scope + execution_files`

## 5. highThinkingQA Responsibilities

`highThinkingQA` 当前方向基本正确，但需要明确边界。

### 保持的设计
1. authority snapshot 读取
2. request history 与 server history 合并
3. history budget 裁剪
4. rewrite 使用 context
5. agent 执行使用 `conversation_context`

### 需要强化的点
1. 更明确区分：
   - `conversation_context` 给 agent
   - `steps/timings` 不属于对话上下文
2. 在 authority summary 可用后，优先消费 authority summary，而不是长期依赖原始历史
3. 对 `file/hybrid` 相关 metadata，只保留必要信息做理解，不把它当成本链路文件执行输入

也就是说：

- `highThinkingQA` 可以看见 fast 回合的最终消息
- 但不应尝试消费 fast 的执行轨迹作为推理上下文

---

## Context Contracts

## 1. Authority Snapshot Contract

建议在逻辑上明确拆成：

```json
{
  "recent_turns": [
    {"role": "user", "content": "...", "created_at": "...", "trace_id": "..."},
    {"role": "assistant", "content": "...", "created_at": "...", "trace_id": "..."}
  ],
  "summary": {
    "short_summary": "...",
    "memory_facts": [...],
    "open_threads": [...]
  },
  "conversation_state": {
    "last_turn_route": "hybrid_qa",
    "last_focus_file_ids": [12, 13],
    "last_assistant_trace_id": "..."
  }
}
```

约束：

- `recent_turns` 只允许最终对话消息
- `summary` 只允许压缩后的语义摘要
- `conversation_state` 只允许路由/文件状态

## 2. QA Execution Context Contract

建议 QA 服务内部都对齐到同一种逻辑结构：

```json
{
  "recent_turns_for_llm": [...],
  "summary_for_llm": {...},
  "conversation_state": {...},
  "source_selection": {
    "source_scope": "pdf+kb",
    "selected_file_ids": [12],
    "used_files": [...],
    "execution_files": [...]
  }
}
```

其中：

- `recent_turns_for_llm` 和 `summary_for_llm` 可给 rewrite / answer model
- `conversation_state` 和 `source_selection` 主要给编排器 / retriever

## 3. Retrieval Context Contract

建议所有 QA route 在进入回答模型前都统一形成：

```json
{
  "rewrite": {...},
  "retrieval_plan": {...},
  "retrieval_scope": {...},
  "retrieved_context": [...],
  "supporting_artifacts": {...}
}
```

示例：

- `kb_qa`：`retrieved_context = KB chunks`
- `pdf_qa`：`retrieved_context = PDF chunks`
- `tabular_qa`：`supporting_artifacts = table execution result`
- `hybrid_qa`：`retrieved_context = PDF chunks + KB chunks`, `supporting_artifacts = table result if any`

---

## Mixed Conversation Semantics

## 1. 同一 conversation 中允许 fast + thinking 混排

保持当前语义：

- 同一对话里允许同时存在：
  - `fastQA` 回合
  - `highThinkingQA` 回合

## 2. thinking 不执行文件问答

保持当前语义：

- 文件/混合问答统一落 `fastQA`
- `highThinkingQA` 只消费“最终消息层”的上下文，不承担文件执行

## 3. 跨 mode 上下文共享的边界

推荐共享：

- 最终 user/assistant 消息
- authority summary

推荐不共享：

- 具体执行步骤
- rerank / check 过程
- trace 调试信息
- 文件执行内部临时结果

---

## Error Handling and Degradation

## 1. Authority 读取失败

### fastQA / highThinkingQA
- 允许退回 request `chat_history`
- 但要明确日志：authority read failed / fallback to request history

## 2. Summary 不可用

- 可继续仅使用 recent turns
- 不阻断主问答

## 3. 文件状态不完整

- 由 `gateway` 尽量做 clarification / selection fallback
- 不要把“文件范围不确定”转成模糊自然语言上下文

## 4. Retrieval context 构造失败

- 明确返回是 `history-only answer` 还是 `partial evidence answer`
- 不要伪装成完整证据答案

---

## Testing Strategy

## 1. Contract Tests

需要覆盖：

1. authority snapshot 只返回最终消息
2. `conversation_state` 与 `recent_turns` 解耦
3. `summary` 缺失时 QA 服务的降级路径
4. `fastQA` / `highThinkingQA` 对同一 snapshot 的消费方式符合预期

## 2. Context Merge Tests

需要覆盖：

1. request history 与 authority history overlap merge
2. history budget 裁剪
3. mixed conversation 下 fast/thinking 交替回合读取一致性

## 3. Retrieval Scope Tests

需要覆盖：

1. `selected_file_ids -> source filters`
2. `last_focus_file_ids -> fallback source selection`
3. `source_scope` 对 route 的约束
4. 文件+KB 混合时的 source scope 传递

## 4. Prompt Boundary Tests

需要覆盖：

1. `steps/timings/trace` 不进入最终 LLM history payload
2. `recent_turns_for_llm` 只包含最终 user/assistant
3. retrieval context 单独注入，不混回 authority history

---

## Rollout Strategy

## Phase 1: 文档与契约固化

1. 写清 authority snapshot contract
2. 写清 QA execution context contract
3. 写清 retrieval context contract
4. 写清 mixed conversation semantics

## Phase 2: fastQA 普通问答上下文补齐

1. 给 `fastQA kb_qa` 增加标准 context builder
2. 让 `kb_qa` 真正消费 recent turns / summary
3. 增加 rewrite / condense 能力或等价结构

## Phase 3: authority summary 实装

1. `public-service` 实装简单 summary
2. QA 服务开始优先消费 summary
3. 减少对长 recent turns 的依赖

## Phase 4: 文件状态 -> 标准 retrieval filter contract

1. 在 `gateway/fastQA` 间统一 source selection contract
2. 文件 route 内统一转换成 retriever scope
3. 为多文件 / 混合问答后续演进做准备

## Phase 5: 扩展多文件/复杂 mixed QA

1. 评估是否需要 per-document retriever / tool
2. 评估是否需要 document-agent 风格的调度层
3. 在不破坏现有 `gateway -> fastQA` 责任边界前提下逐步演进

---

## Final Recommendation

推荐按以下优先级推进：

1. 先把 `fastQA kb_qa` 的上下文链补齐
2. 再补 `public-service summary`
3. 再把文件状态沉淀成统一 retrieval contract
4. 最后再考虑复杂多文档 agent 化

原因是：

- 现在最直接的不对齐，是 `fastQA` 普通问答没有真正吃到会话上下文
- 第二个明显短板，是 authority summary 为空
- 文件/混合问答的 route 架构本身已经基本合理，不需要优先推翻

这条路径风险最低，也最符合当前系统的存量架构。

## Rollout Notes

1. `gateway` 对外继续兼容旧字段 `last_focus_ids`，同时内部接受 authority 主命名 `last_focus_file_ids`，避免 mixed conversation 在新旧状态源之间断裂。
2. `chat_history` 仍可作为前端冗余输入下传，但 `pdf_context` 只能在 gateway 内部转成 route/source_scope/file_selection，不能下沉成 prompt 文本。
3. `highThinkingQA` 必须在两个边界做清洗：
- 构建 `ConversationContext` 时清洗 `recent_turns/summary`
- 进入 rewrite 和 agent 前再次清洗，防止调用方注入执行态字段
4. 当前 rollout 已验证的最小 mixed conversation 为：`thinking -> hybrid(pdf+kb) -> file follow-up`。更完整的 cross-service e2e 仍需在后续任务中继续扩大覆盖。
