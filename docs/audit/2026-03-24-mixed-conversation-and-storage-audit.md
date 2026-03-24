# 2026-03-24 同一对话混合 fastQA / highThinkingQA 与存储链路审阅

## 范围

本次只读审阅覆盖两件事：

1. 同一个 `conversation_id` 里，是否允许同时存在：
   - `fastQA` 链路的普通 QA / 文件 QA / 混合 QA
   - `highThinkingQA` 链路的 thinking QA
2. 当前系统里与文件、会话文档有关的关键链路，是否已经形成“MinIO 优先，本地兜底/缓存”的统一模型。

本次未改任何业务代码。

---

## 结论先行

### 1. 同一个对话里混用 fastQA 和 highThinkingQA：支持

当前架构下，同一 `conversation_id` 可以混合保存和展示两类回合：

- `fastQA` 产生的回合
- `highThinkingQA` 产生的回合

前提是请求经过 `gateway` 主链。

原因不是“两个后端互相兼容对方协议”，而是：

- `gateway` 会先判定本轮应该落到哪个后端
- 最终两条链路都把聊天记录写入同一个 `public-service conversation authority`
- 前端展示层读取的是这份统一会话记录，而不是分别读两个 QA 服务的私有会话

### 2. 但同一个对话里不能指望 highThinkingQA 执行文件 / 混合问答

这点要说清楚。

如果本轮带文件上下文，或者属于 `mixed`：

- `gateway` 会把 `actual_mode` 强制改成 `fast`
- 路由会落到 `pdf_qa` / `tabular_qa` / `hybrid_qa`
- 不会继续留在 `thinking` 后端执行

所以“同一对话里既有 fast 又有 thinking”是支持的；
但“同一对话里 thinking 模式直接处理文件 QA / 混合 QA”当前不支持，设计上就是强制回到 `fastQA`。

### 3. 同一会话的持久化形态：是单一 authority 文档，不是双会话

当前不是“fastQA 一份聊天记录，highThinkingQA 再来一份聊天记录”。

真实形态是：

- `public-service` 持有一份统一 conversation document
- document 中的 message 按时间顺序混排
- 每条消息 metadata 记录自己的：
  - `source_service`
  - `route`
  - `requested_mode`
  - `actual_mode`
  - `trace_id`
  - `used_files`
  - `references`
  - `steps`
  - `timings`
  - `done_seen`

因此，同一个对话里既可以看到 fast 回合，也可以看到 thinking 回合；区别不是靠分表，而是靠 message metadata。

### 4. 当前存储模型不是全系统统一的“MinIO-first”

当前更准确的状态是：

- 上传文件下载：基本是 `MinIO-first`
- `papers`：是“MinIO 远端权威 + 本地执行物化副本”
- `public-service` 会话 JSON：是“本地先读/先写，再镜像到对象存储”
- `fastQA` 上传文件执行：已经补了 `storage_ref -> 本地物化` 能力，但执行态仍然必须拿到本地文件路径

所以现在不能笼统说“所有文件链路都已经完全以 MinIO 为主、本地仅保险”。
更准确的说法是：

- 下载 / 分发层正在偏向 MinIO-first
- 执行层仍然高度依赖本地可读文件
- 会话 JSON 权威层也还不是严格 remote-first

---

## 一、同一对话里混用 fastQA 和 highThinkingQA 的真实行为

## 1. gateway 决定这一轮到底走 fast 还是 thinking

`gateway` 的路由决策里，文件类与混合类请求会被强制改成 `actual_mode="fast"`。

证据：
- [route_decision.py](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py#L13)
- [route_decision.py](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py#L15)
- [route_decision.py](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py#L18)
- [route_decision.py](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py#L49)

关键点：

- `turn_mode in {"file_only", "mixed"}` 时，`actual_mode = "fast"`
- `mixed` 且路由属于 `pdf_qa/tabular_qa/hybrid_qa` 时，会统一归一成 `hybrid_qa`
- `source_scope` 会进一步标识这是 `pdf`、`table`、`pdf+kb`、`table+kb`、`pdf+table+kb`

因此：

- 纯知识库 thinking 问题，可以继续走 `highThinkingQA`
- 文件问答 / 混合问答，不管前端请求的是不是 thinking，最终都走 `fastQA`

## 2. gateway 会把本轮执行决策写进转发 payload

证据：
- [qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L55)
- [qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L61)
- [qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L67)
- [qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L71)
- [qa.py](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L198)

转发给下游后端的 payload 里已经明确带了：

- `requested_mode`
- `actual_mode`
- `route`
- `source_scope`
- `turn_mode`
- `used_files`
- `execution_files`
- `selected_file_ids`
- `file_selection`

这意味着两个 QA 服务看到的不是前端原始请求，而是 gateway 归一化后的执行请求。

## 3. 前端本来就允许一个 conversation 里连续发不同 ask mode

证据：
- [Home.vue](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue#L1047)
- [Home.vue](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue#L1055)
- [Home.vue](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue#L1058)
- [api.js](/home/cqy/worktrees/highThinking/frontend-vue/src/services/api.js#L480)
- [api.js](/home/cqy/worktrees/highThinking/frontend-vue/src/services/api.js#L488)

前端每次发问时：

- 传的是当前聊天的 `conversation_id`
- 同时传本轮选择的 `mode`
- 也会把文件上下文 `pdf_context` 一起带上
- 还会带上 `last_turn_route`

这说明前端层面本来就没有“一个对话只能绑定一种 mode”的限制。

## 4. fastQA 与 highThinkingQA 最终都能落到同一个 authority 会话

### 4.1 fastQA 侧

`fastQA` 的 router 会在 ask 前后调用可插拔 hook：

- 读取 authority 上下文
- 持久化用户消息
- 持久化 assistant 总结

证据：
- [main.py](/home/cqy/worktrees/highThinking/fastQA/app/main.py#L56)
- [main.py](/home/cqy/worktrees/highThinking/fastQA/app/main.py#L58)
- [qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L172)
- [qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L225)
- [qa.py](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L257)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L175)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L199)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L226)

注意点：

- `fastQA` 代码本身支持直接写 authority
- 但当前代码默认 `CHAT_PERSIST_ENABLED` 是 `False`，且 authority target 默认还是 `legacy`
- 所以它是否自己直接持久化，要看运行时环境是否打开

证据：
- [config.py](/home/cqy/worktrees/highThinking/fastQA/app/core/config.py#L83)
- [config.py](/home/cqy/worktrees/highThinking/fastQA/app/core/config.py#L85)
- [config.py](/home/cqy/worktrees/highThinking/fastQA/app/core/config.py#L262)

但这不影响 gateway 主链，因为 gateway 自己会为 `actual_mode != thinking` 的请求做持久化汇总。

### 4.2 highThinkingQA 侧

`highThinkingQA` 已经具备 public-service authority 读写能力，并且默认 target 是 `public_service`。

证据：
- [config.py](/home/cqy/worktrees/highThinking/highThinkingQA/config.py#L84)
- [config.py](/home/cqy/worktrees/highThinking/highThinkingQA/config.py#L86)
- [config.py](/home/cqy/worktrees/highThinking/highThinkingQA/config.py#L87)
- [config.py](/home/cqy/worktrees/highThinking/highThinkingQA/config.py#L427)
- [config.py](/home/cqy/worktrees/highThinking/highThinkingQA/config.py#L429)
- [config.shared.env](/home/cqy/worktrees/highThinking/resource/config/services/highThinkingQA/config.shared.env#L78)
- [config.shared.env](/home/cqy/worktrees/highThinking/resource/config/services/highThinkingQA/config.shared.env#L82)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L353)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L421)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L484)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L299)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L311)

也就是说，thinking 回合的 authority 读写链路现在是成立的。

## 5. public-service 会把两条链路的消息混排到同一个 conversation document

`public-service` 的 authority 内部 API 允许：

- `fastQA` 以 `source_service=fastQA`、`requested_mode/actual_mode=fast`
- `highThinkingQA` 以 `source_service=highThinkingQA`、`requested_mode/actual_mode=thinking`

证据：
- [internal_api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py#L26)
- [internal_api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py#L86)
- [internal_api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py#L93)

这个约束很重要：

- 它允许同一个 conversation 出现来自两个 source service 的消息
- 但禁止服务冒充另一个模式写入
- 所以结构上支持“同会话混合 fast/thinking”，但不支持“highThinkingQA 伪装成 fast 写文件问答”

消息写入时，metadata 会保留来源与执行信息。

证据：
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1317)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1364)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1423)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1532)

用户消息 metadata 里会带：

- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `context_hints`

assistant 消息 metadata 里会带：

- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `used_files`
- `references`
- `steps`
- `timings`
- `done_seen`

## 6. authority snapshot 是 mode-agnostic 的

`public-service` 返回给 QA 服务的上下文快照，并不会按 mode 过滤消息。

证据：
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1118)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1126)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1130)
- [chat_persistence.py](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L226)
- [conversation_context_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation_context_service.py#L109)
- [conversation_context_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation_context_service.py#L131)

这意味着：

- 后续 `highThinkingQA` 会看到前面的 fastQA 回合
- 后续 `fastQA` 也可能看到前面的 highThinkingQA 回合

这是当前的真实行为。

它的优点是：

- 用户体验连续
- 同一会话上下文不断裂

它的风险是：

- 没有 mode 级隔离
- thinking 回合可能读到文件回合留下的自然语言答案
- fast 回合也会读到 thinking 回合留下的长答案

## 7. 前端展示层本身就是 mode-agnostic

前端展示会统一规范 message：

- `queryMode`
- `references`
- `reference_links`
- `pdf_links`
- `doi_locations`
- `steps`
- `metadata`

证据：
- [chatStore.js](/home/cqy/worktrees/highThinking/frontend-vue/src/stores/chatStore.js#L180)
- [chatStore.js](/home/cqy/worktrees/highThinking/frontend-vue/src/stores/chatStore.js#L185)
- [chatStore.js](/home/cqy/worktrees/highThinking/frontend-vue/src/stores/chatStore.js#L197)
- [chatStore.js](/home/cqy/worktrees/highThinking/frontend-vue/src/stores/chatStore.js#L223)

因此 UI 层并不要求“这个会话只能有一种问答模式”。

---

## 二、同一对话里混合 fast / thinking 时，聊天记录到底长什么样

## 1. 不是双轨会话，而是单轨消息流

当前 conversation document 的真实模型是：

- 一个 conversation
- 一组按时间追加的 messages
- 每条 message 的 metadata 标明来源和执行形态

因此一个混合会话的消息序列可能是：

1. user: 纯知识库问题，`requested_mode=thinking`
2. assistant: `source_service=highThinkingQA`，`actual_mode=thinking`
3. user: 选中文件继续问，前端仍然点 thinking
4. assistant: gateway 改写后落到 `fastQA`，`actual_mode=fast`，`route=hybrid_qa`
5. user: 再问一个普通问题
6. assistant: 重新回到 `highThinkingQA`

从 authority 存储角度看，这是完全允许的。

## 2. conversation_state 只看最近一个 assistant 回合

authority 还会从最近 assistant message 的 metadata 推导出：

- `last_turn_route`
- `last_focus_file_ids`
- `last_assistant_trace_id`

证据：
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1089)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1099)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1102)
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1113)

这意味着：

- 如果上一轮是 fast 文件 / 混合 QA，那么 `last_turn_route` 会是 `pdf_qa/tabular_qa/hybrid_qa`
- 如果上一轮是 thinking QA，那么 `last_turn_route` 会回到 thinking 侧的 route

所以“同一对话混合多模式”虽然成立，但 `conversation_state` 永远只描述最近一轮，不会帮你保留多模式并行状态机。

## 3. 当前 authority summary 还是空壳

context snapshot 里的 `summary` 当前仍然是空结构：

- `short_summary = ""`
- `memory_facts = []`
- `open_threads = []`

证据：
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1082)

所以当前跨 mode 连续对话更多依赖：

- `recent_turns`
- `last_turn_route`
- `last_focus_file_ids`

而不是依赖一个成熟的 authority summary。

---

## 三、文件与会话文档链路是否已经 MinIO 优先

## 1. 上传文件：持久化语义接近 MinIO-first，但实现仍是先落本地

`public-service` 上传时的真实流程：

1. 先把上传内容保存到本地 upload 目录
2. 再调用对象存储镜像
3. 如果镜像失败，清理本地文件并直接报错，拒绝这次上传
4. 成功后才把 `local_path + storage_ref` 写入会话文件元数据

证据：
- [api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/uploads/api.py#L152)
- [api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/uploads/api.py#L177)
- [api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/uploads/api.py#L193)
- [api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/uploads/api.py#L275)

这条链路的结论是：

- 实现上不是 remote-first write
- 但业务语义上把 MinIO 成功视为持久化成功的必要条件

## 2. 上传文件下载：是明确的 MinIO-first

下载解析时会优先解释 `storage_ref=minio://...`。

证据：
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L218)
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L231)
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L235)

只有 MinIO 路径不可用时，才会回退到本地路径。

所以“下载 / 查看原文件”这条链路，当前可以认定为 `MinIO-first`。

## 3. papers：MinIO 是远端权威，但执行时仍然要物化到本地

`public-service` 与 `fastQA` 的 `papers` 相关逻辑都不是远端直读，而是：

- 先查 MinIO 是否有对象
- 若本地已存在则直接复用本地副本
- 若本地不存在则从 MinIO 下载到本地
- 后续 PDF 解析、摘要、句子定位都读取本地文件

证据：
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L106)
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L120)
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L158)
- [paper_storage.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/paper_storage.py#L150)
- [paper_storage.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/paper_storage.py#L169)
- [paper_storage.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/paper_storage.py#L193)

因此，`papers` 的准确表述应当是：

- 远端权威在 MinIO
- 执行态依赖本地物化副本

## 4. fastQA 上传文件执行：比以前进了一步，但执行态仍然依赖本地文件

这是本次交叉审阅里最重要的一点之一。

现在 `fastQA` 已经不是“只认 local_path，不认 storage_ref”了。
它新增了 `storage_ref -> local_path` 的物化能力：

- 如果 `local_path` 已经可读，直接用
- 如果 `storage_ref` 是 `local://...`，尝试解析成本地文件
- 如果 `storage_ref` 是 `minio://...`，会下载到 `FASTQA_UPLOAD_CACHE_DIR` 或 `/tmp/fastqa-upload-cache`
- 成功后把生成出的本地缓存路径回填到 `local_path`

证据：
- [upload_materializer.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/upload_materializer.py#L99)
- [upload_materializer.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/upload_materializer.py#L112)
- [upload_materializer.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/upload_materializer.py#L125)
- [upload_materializer.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/upload_materializer.py#L145)
- [upload_materializer.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/upload_materializer.py#L166)

但执行层依然是“必须有一个最终可读的本地文件”。

例如表格问答 loader 最终还是：

- 拿到物化后的 `local_path`
- 本地文件不存在就报错

证据：
- [workbook_loader.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py#L187)
- [workbook_loader.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py#L190)
- [workbook_loader.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py#L193)

所以结论不是“fastQA 现在完全 MinIO-first”。
更准确的说法是：

- `fastQA` 已经补上了跨服务 `storage_ref` 物化兜底
- 但其执行态仍然是本地文件驱动
- 它并不是 remote-streaming execution

## 5. public-service 会话 JSON：不是 MinIO-first，而是 local-first + remote mirror

会话文档 JSON 的读取和写入策略是：

读取：
- 先读本地 JSON
- 本地没有，再尝试从远端下载恢复

写入：
- 先原子写本地 JSON
- 再上传对象存储
- 如果上传失败，会保留本地并标记 `sync_failed`

证据：
- [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L164)
- [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L182)
- [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L196)
- [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L239)
- [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L254)

所以，聊天记录 authority 虽然已经统一到了 `public-service`，但底层 JSON 存储策略并没有变成严格意义上的 MinIO-first。

---

## 四、这两个问题合在一起时，系统的真实边界

## 1. 用户视角

如果用户在同一个聊天里这样操作：

1. 先用 fast 模式问普通知识库问题
2. 再用 thinking 模式问纯知识库问题
3. 再选中 PDF 或表格做文件 / 混合问答
4. 再切回 thinking 问追问

当前系统在主链上是支持的。

表现为：

- 所有消息都进入同一个 conversation
- 前端能一起展示
- 后端会根据当轮上下文切换实际执行服务

## 2. 系统边界

但要明确以下边界：

- thinking 不能直接执行文件 QA / 混合 QA
- 混合文件上下文最终仍由 `fastQA` 执行
- 跨 mode 的多轮上下文是共享的，不是隔离的
- 最近一轮的 `last_turn_route` 会影响后续文件路由提示
- authority summary 目前几乎为空，所以跨 mode 记忆更多依赖原始 recent turns

## 3. 当前最大的架构风险

### 风险 A：上下文混用是支持的，但没有 mode 隔离

这会带来两个后果：

- `fastQA` 可能吃到上一轮 `highThinkingQA` 的长答案作为 chat history
- `highThinkingQA` 也可能吃到上一轮 `fastQA` 的文件回合答案

如果之后发现 prompt 污染、模式切换后答案跑偏，这里会是优先排查点。

### 风险 B：会话权威已经统一，但底层 JSON 存储策略仍未统一成 remote-first

也就是说：

- authority 是统一的
- 但 authority 自己的 JSON 副本仍然 local-first

这不影响“同一对话混用 fast/thinking”成立；
但影响后续如果你要把整个系统完全收敛到“对象存储权威，本地仅缓存”的架构目标。

### 风险 C：文件执行虽然已经可从 `storage_ref` 物化，但执行态仍然要求本地文件可读

这意味着：

- 分布式执行能力比之前好了很多
- 但执行节点依然必须拥有本地缓存目录与下载权限
- 真正的“零本地依赖文件问答”当前仍不存在

---

## 五、最终结论

### 关于“同一个对话里能否同时存在 fastQA 与 highThinkingQA 回合”

答案是：能。

但要补完整一句：

- 能混合存在 `fastQA` 回合和 `highThinkingQA` 回合
- 不能要求 `highThinkingQA` 直接执行文件 / 混合问答；这类请求会被 gateway 转成 `fastQA`

### 关于“这些混合回合的聊天记录是怎么保存的”

答案是：

- 统一保存到 `public-service` 的同一个 conversation authority
- 不是分裂成两份会话
- 区分来源靠 message metadata，而不是靠分库分表

### 关于“系统是否已经统一成 MinIO-first”

答案是：还没有。

当前更准确的状态是：

- 上传下载链路：已经明显向 MinIO-first 靠拢
- `papers`：MinIO 远端权威，本地物化执行
- `fastQA` 上传文件执行：支持 `storage_ref` 物化，但执行态仍依赖本地文件
- `public-service` 会话 JSON：依旧是 local-first + remote mirror

---

## 相关文档

这次结论和下面两份已有文档是互补关系：

- [minio_priority_file_storage_review.md](/home/cqy/worktrees/highThinking/docs/minio_priority_file_storage_review.md)
- [2026-03-22-chat-persistence-and-redis-audit.md](/home/cqy/worktrees/highThinking/docs/audit/2026-03-22-chat-persistence-and-redis-audit.md)

其中：

- 上一份文档更偏“全系统文件/对象存储策略审阅”
- 本文更偏“同一 conversation 混合 fast/thinking 的行为边界 + 当前存储形态交叉结论”
