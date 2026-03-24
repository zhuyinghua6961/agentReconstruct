# 2026-03-22 highThinking 旧版 vs highThinkingQA 迁移对照审查

## 审查范围
- 旧版 highThinking baseline：仓库根目录 monolith
- 新版迁移服务：`highThinkingQA/`
- 关注：问答入口、SSE 流、metadata、聊天持久化、上下文读取、缓存层、功能缺口

## 基线说明
这里的“旧版 highThinking”不是一个单独的 `highThinking/` 目录。

旧版 source of truth 实际是仓库根目录的单体结构：
- `server/`
- `server_fastapi/`
- `retriever/`
- `ingest/`
- `papers/`
- `vectordb/`
- `prompts/`
- `uploads/`

新版迁移服务则是：
- `highThinkingQA/`

---

## 一、整体判断

### 1. 问答核心链并没有被“重写成另一套”，而是高度平移
- `highThinkingQA/server/services/ask_service.py` 与旧版 `server/services/ask_service.py` 主体结构高度一致。
- `highThinkingQA/server/services/conversation_context_service.py` 与旧版同名文件逻辑一致。
- `highThinkingQA/retriever/`、`highThinkingQA/ingest/`、`highThinkingQA/agent_core/` 与旧版对应模块基本保持同构。

### 2. 新版最明显的变化不在 agent 核心，而在 FastAPI 包装层与输出 envelope
新版增强集中在：
- 更丰富的 `done` 事件 metadata
- 更完整的 SSE summary 汇总
- assistant message 持久化时写入更多 metadata 字段
- 兼容 gateway/前端路由语义的包装字段

### 3. 但新版 highThinkingQA 还没有从旧版 conversation authority 里解耦
- 持久化仍写旧版 `server.services.conversation.conversation_service`
- 多轮上下文读取仍读旧版 snapshot
- 没有切到 `public-service`

---

## 二、旧版 highThinking 的核心能力

### A. 问答执行入口
核心证据：
- `server_fastapi/routers/ask.py`
- `server/services/ask_service.py`

旧版能力：
- `/ask` / `/ask_stream`
- auth context 绑定
- ask slot concurrency guard
- SSE 输出
- 用户/assistant 消息持久化
- 多轮上下文构建
- rewrite
- 调 `agent_core.graph.run_agent(...)`

### B. 多轮上下文
核心证据：`server/services/conversation_context_service.py`

能力：
- 读取 conversation snapshot
- 合并 request.chat_history 与 server 侧消息
- 限制 recent turns / char budget
- 读取 summary

### C. agent pipeline
核心证据：`server/services/ask_service.py` + `agent_core/graph.py`

旧版主链：
- metadata 预热
- context_ready / rewrite_ready
- 进入 `run_agent`
- `run_agent` 内部分步：
  - 直接回答 / 查询分解
  - 子问题预回答
  - 文献检索
  - 综合草稿流式生成
  - checker / revise 循环
  - 最终答案

### D. 持久化
核心证据：
- `server_fastapi/routers/ask.py`
- `server/services/conversation/conversation_service.py`

旧版行为：
- user message: `conversation_service.add_message(...)`
- assistant message: `conversation_service.add_message(...)`
- 完成后 `refresh_conversation_summary(...)`

### E. 缓存
旧版没有 Redis 业务缓存层。
有的只是：
- 进程内/本地文件级缓存
- translation cache
- parsed markdown cache
- lru cache

---

## 三、新版 highThinkingQA 的能力

### A. 核心 ask service 仍然保留旧版主链
核心证据：`highThinkingQA/server/services/ask_service.py`

保留能力：
- `execute_ask(...)`
- `stream_ask_events(...)`
- `resolve_profile(...)`
- `ConversationContext + rewrite`
- `run_agent(...)`
- step/progress/content 事件
- `done` 事件输出 references / links / timings

### B. SSE envelope 更丰富
相比旧版，新版 `stream_ask_events(...)` / `done` 事件额外包含：
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `used_files`
- `doi_locations`
- `file_selection`
- `metadata = _build_done_metadata(...)`

### C. `_build_done_metadata(...)` 是新版新增的显式封装
核心证据：`highThinkingQA/server/services/ask_service.py`

新增字段：
- `mode`
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `query_mode`
- `conversation_id`
- `raw_question`
- `effective_question`
- `rewrite_applied`
- `rewrite_reason`
- `context_turns`
- `summary_available`
- `summary_updated_at`

旧版 execute/done 虽然也返回其中很多内容，但没有这个统一 helper，也没有把 envelope 做得这么完整。

### D. router 层 assistant summary 汇总更丰富
核心证据：`highThinkingQA/server_fastapi/routers/ask.py`

新版在 SSE 汇总 summary 时，除旧版已有的：
- `assistant_content`
- `query_mode`
- `references`
- `steps`
- `done_seen`

还增加了：
- `reference_links`
- `pdf_links`
- `doi_locations`
- `route`
- `used_files`
- `timings`
- `trace_id`
- `file_selection`

并在 assistant message persistence metadata 中一并写入。

### E. conversation / upload 路由仍绑旧版 service
核心证据：
- `highThinkingQA/server_fastapi/routers/conversation.py`
- `highThinkingQA/server_fastapi/routers/upload.py`

说明：
- 新版 highThinkingQA 虽然是独立目录，但 conversation / upload / ask 仍大量 import 旧版 `server.services...`
- 这意味着它更像“迁移中的独立服务包装”，还不是彻底自治的一套后端

---

## 四、旧版与新版的主要差异

## 差异 1：问答结果 envelope 更丰富
旧版：
- `done` 事件以 `references` / `pdf_links` / `reference_links` / `trace_id` 为主

新版：
- 在此基础上增加 `metadata`、`used_files`、`doi_locations`、`file_selection`、更多 mode/route 字段

影响：
- 更适合 gateway / 前端多模式路由场景
- 更利于后续持久化与审计

## 差异 2：assistant 持久化 metadata 更丰富
旧版 assistant persistence metadata 主要有：
- `source`
- `query_mode`
- `references`
- `steps`
- `done_seen`

新版 assistant persistence metadata 额外有：
- `reference_links`
- `pdf_links`
- `doi_locations`
- `route`
- `used_files`
- `timings`
- `trace_id`
- `file_selection`

影响：
- 新版更适合后续前端还原完整会话视图

## 差异 3：stream progress 进入前端的方式更偏“规范化”
新版在 `on_progress(...)` 中主要通过 `_progress_to_step_event(...)` 发规范 step 事件；旧版是 `event_queue.put(normalized)` + `event_queue.put(_progress_to_step_event(normalized))` 的双通道。

这意味着：
- 新版更强调前端可消费的 step contract
- 但也可能和旧版调试/原始 progress 可见性不同

## 差异 4：新版显式返回 `used_files`
- 旧版 thinking QA 主要还是纯知识库/高思考链
- 新版 envelope 已经为 gateway 文件上下文兼容预留了 `used_files` 等字段

但注意：
- 这不代表 highThinkingQA 已经完整拥有和 fastQA 一样的文件/混合问答闭环
- 更准确地说，它在接口层已经开始向 gateway 统一协议对齐

---

## 五、聊天记录持久化在 highThinkingQA 到底是谁做

直接结论：
- **不是 public-service。**
- **是旧版 `server.services.conversation.conversation_service`。**

证据：
- `highThinkingQA/server_fastapi/routers/ask.py` 直接 import `conversation_service`
- user / assistant 持久化都直接调用它
- `highThinkingQA/server/services/conversation_context_service.py` 读取上下文也直接读它

因此：
- `highThinkingQA` 当前不是“public-service authority + thinking executor”的结构
- 而是“thinking executor + 旧 monolith conversation service”的结构

---

## 六、缓存层差异

### 旧版
- 无 Redis 业务缓存
- 仅有本地/进程内轻缓存

### 新版 highThinkingQA
- 依然无 Redis 业务缓存
- 仍只有：
  - `lru_cache`
  - parsed markdown 文件缓存
  - translation dict cache

结论：
- **新版 highThinkingQA 在缓存能力上并没有比旧版前进到一个新架构层级。**

---

## 七、从“功能缺口”角度看，新版还差什么

### 1. 与 public-service 统一 authority 还没完成
差距：
- ask persistence 仍旧写旧版 conversation service
- context snapshot 仍从旧版读取

### 2. Redis 级缓存层没有接入
差距：
- 多 worker / 多实例无法共享 rewrite / retrieval / decomposition 等中间结果

### 3. 服务独立性还不彻底
差距：
- 目录虽然独立，但大量核心依赖仍从仓库根旧模块 import

### 4. “迁移完成”更多体现在接口包装，而不是底层能力完全抽离
换句话说：
- **highThinkingQA 当前更像“包装后的独立运行单元”，还不是彻底独立的自治服务。**

---

## 八、对用户问题的直接回答

### 问题：看看现在新版旧版还差什么功能？
- **问答核心能力本身差异不大，核心 agent pipeline 仍然基本平移。**
- 真正的差别主要在：
  - 新版 envelope 更丰富
  - 新版持久化 metadata 更丰富
  - 新版更偏向 gateway/front-end 协议对齐
- 但在架构完成度上，新版还差：
  - conversation authority 抽离到 `public-service`
  - Redis 中间缓存层
  - 对旧版 `server/...` 的彻底解耦

### 问题：highThinkingQA 的聊天记录持久化是 public-service 在做还是哪里在做？
- **不是 public-service。**
- **当前仍是旧版 `server.services.conversation.conversation_service` 在做。**

---

## 九、最终判断
- 如果按“问答能力是否可跑”看，`highThinkingQA` 已经具备完整主链能力。
- 如果按“是否真正完成服务迁移”看，**还没有完全完成**。
- 当前最大的未完成点不是 agent 推理本身，而是：
  - conversation authority 没统一
  - Redis 缓存没接入
  - 仍依赖旧 monolith service
