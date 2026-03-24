# highThinkingQA 聊天持久化迁移 Spec（草稿）

## 1. 目标与边界

- 目标：将 highThinkingQA 普通 QA 的聊天记录持久化能力迁移到 public-service，保持用户体验优先，答案流式输出不被持久化阻塞。
- 明确边界：
  - fastQA 负责文件 QA、混合 QA，以及 fast 模式知识库问答。
  - highThinkingQA 只负责自己的普通 QA / thinking 模式知识库问答。
  - gateway 只负责接收前端请求、判定路由、转发给对应后端，不承担会话持久化真写入。
  - public-service 负责会话权威存储、上下文快照、异步 assistant turn 物化、最终一致性保障。

## 2. 当前分析方法

- source of truth（旧版）: `/home/cqy/worktrees/fastapi-version/backend`
- 当前实现：`gateway/`、`public-service/`、`highThinkingQA/`
- 方法：边读边记，先确认入口、再下沉到 service / repository / runtime / worker / contract。

## 3. 已确认事实

### 3.1 系统职责切分（先记结论，后续继续补代码锚点）

- gateway 是前端唯一入口，负责根据请求模式、文件上下文、选择文件等信息决定发往 fastQA 还是 highThinkingQA。
- public-service 已经接管 fastQA 的 authority 持久化能力，这部分可作为 highThinkingQA 迁移基线。
- highThinkingQA 这次只看普通 QA，不纳入文件 QA / 混合 QA。

## 4. 需要补充的审阅块

- gateway: 普通 QA / 文件 QA / 混合 QA / thinking 模式分发规则
- highThinkingQA 当前 ask / ask_stream 入口、流式输出、当前持久化位置
- 旧版 highThinking 同位置实现
- public-service authority contract 如何复用于 highThinkingQA
- 迁移方案与阶段拆分


## 5. 当前代码已确认的关键事实（持续补充）

### 5.1 gateway 当前不仅分发，还在做会话持久化代理层写入

代码锚点：
- `gateway/app/routers/qa.py`
- `gateway/app/services/route_decision.py`

已确认行为：
- `gateway/app/routers/qa.py` 的 `_proxy_ask()` 和 `_proxy_ask_stream()` 在完成 `_resolve()` 后，会先调用 `request.app.state.conversation_persistence_service.persist_user_message(...)`。
- 同一路由在同步 ask 成功返回后，会从后端返回体提取 `final_answer/references/steps/used_files/...`，再次调用 `persist_assistant_summary(...)`。
- 流式 ask 中，gateway 会先打开上游 SSE，再通过 `conversation_persistence_service.extract_stream(...)` 边转发边抽取 summary，最终在流结束后调用 `persist_assistant_summary(...)`。
- 这说明当前 gateway 仍承担“前端入口层持久化代理”职责，而不是纯转发层。

与目标边界的关系：
- 这和目标边界不完全一致。目标边界应是：gateway 只负责分发，不成为持久化写入主责任方。
- 对 highThinkingQA 的下一步迁移，需要明确是否复用 gateway 的现有 persistence proxy，还是让 highThinkingQA 直接对 public-service authority API 负责写入。目前从职责纯度上，后者更干净。

### 5.2 gateway 的分流规则已经把“文件/混合 -> fastQA”写死

代码锚点：
- `gateway/app/services/route_decision.py`

已确认行为：
- `RouteDecisionService.decide()` 先取 `requested_mode`，默认 `actual_mode = requested_mode`。
- 只要 `file_context.turn_mode in {"file_only", "mixed"}`，就会强制 `actual_mode = "fast"`。
- `_normalized_route()` 会把 mixed 且命中 `pdf_qa/tabular_qa/hybrid_qa` 的请求统一标准化为 `hybrid_qa`。
- `_source_scope()` 负责把文件家族进一步映射为 `pdf` / `table` / `pdf+kb` / `table+kb` / `pdf+table+kb`。

结论：
- 这和用户要求一致：文件 QA、混合 QA 都应该由 fastQA 处理。
- 因此 highThinkingQA 的迁移范围应明确限定为“thinking 模式普通 QA / knowledge-only QA”，不要把文件链路混进来。

### 5.3 当前 highThinkingQA 仍然直接依赖本地 conversation_service 做上下文读取

代码锚点：
- `highThinkingQA/server/services/conversation_context_service.py`

已确认行为：
- `build_conversation_context()` 会调用 `_load_server_context_snapshot()`。
- `_load_server_context_snapshot()` 内部直接 `from server.services.conversation.conversation_service import conversation_service`。
- 随后调用 `conversation_service.get_conversation_context_snapshot(user_id, conversation_id)` 读取服务内本地会话快照。
- 之后会把服务器快照与请求体 `chat_history` 进行 overlap 合并，再裁剪为 `recent_turns` 和 `summary`。

影响：
- 这说明 highThinkingQA 当前“读上下文”仍是本地 authority，本地 conversation 子系统仍是事实数据源。
- 若迁移到 public-service，则这里将成为第一批必须抽离的调用点之一。

### 5.4 当前 highThinkingQA 仍然直接写本地 conversation_service.add_message

代码锚点：
- `highThinkingQA/server_fastapi/routers/ask.py`

已确认行为：
- `_persist_user_message_if_needed()` 在持久化开启时，会把 user turn 提交到 `conversation_service.add_message(...)`。
- `_persist_assistant_message_if_needed()` 在 `done_seen=true` 且 `assistant_content` 非空时，也会直接调用 `conversation_service.add_message(...)` 写 assistant turn。
- assistant 写入后还会调用 `conversation_service.refresh_conversation_summary(...)` 本地刷新摘要。
- 若 `CHAT_PERSIST_ASYNC=true`，则只是把这些本地写操作扔给 `ordered_task_dispatcher`，并不是写到 public-service。

结论：
- highThinkingQA 目前还没有完成“public-service authority 化”。
- 它和之前迁移前的 fastQA 一样，仍处在“执行服务自己读写会话”的阶段。

### 5.5 当前 highThinkingQA 普通 QA 执行链与持久化是并列关系，不是 authority 驱动

代码锚点：
- `highThinkingQA/server/services/ask_service.py`
- `highThinkingQA/server_fastapi/routers/ask.py`

已确认行为：
- `stream_ask_events()` 负责：读取上下文、改写问题、提交 agent 执行、把回调转为 `metadata/step/content/done/error` 事件。
- `done` 事件里会带 `final_answer`、`references`、`pdf_links`、`reference_links`、`metadata` 等。
- 路由层 `_build_stream_response()` 在消费这些事件时累计 `summary`，最后再调用 `_persist_assistant_message_if_needed()`。
- 即：持久化发生在路由层收尾阶段，执行链本身并不知道 authority/public-service。

这对迁移的意义：
- 迁移可以沿用 fastQA 已经采用的思路：
  - 路由前置 user write
  - 执行前 authority context snapshot read
  - 流结束后 assistant final event 异步 accept
- 但 highThinkingQA 还没有对应的 authority client / pending overlay / contract 层，需要单独补齐。


### 5.6 旧版 integrated backend 的持久化基线：ask_gateway 直接绑定 conversation_service

代码锚点：
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/api.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/service.py`

已确认行为：
- 旧版 `ask_gateway/api.py` 在接到请求后，会执行三步：
  1. `ask_gateway_service.enrich_request(...)`
  2. `ask_gateway_service.persist_user_request(...)`
  3. `ask_gateway_service.stream_events(...)`
- `ask_gateway/service.py` 在模块底部显式 `register_defaults(...)`：
  - `persist_user_message=conversation_service.persist_user_request`
  - `persist_assistant_summary=conversation_service.persist_assistant_summary`
- 旧版 `stream_events()` 收尾时，如果 `done_seen=true`，就调用 `persist_assistant_summary(...)`。
- 旧版 `conversation/service.py` 的 `persist_user_request()` / `persist_assistant_summary()` 只是本地 `add_message(...)` 的薄封装。

结论：
- 旧版 thinking 普通 QA 的会话持久化，本质就是“ask gateway -> 本地 conversation_service”。
- 当前 highThinkingQA 的路由层本地持久化，本质上仍是这一旧模式的延续，只是从 integrated backend 拆成了独立服务。

### 5.7 fastQA 已完成的 authority 化模式，是 highThinkingQA 迁移的最近参考实现

代码锚点：
- `fastQA/app/services/conversation_authority_client.py`
- `fastQA/app/services/chat_persistence.py`
- `public-service/backend/app/modules/conversation/internal_api.py`

已确认行为：
- `ConversationAuthorityClient` 已封装 3 个关键 authority 调用：
  - `write_user_turn(...)`
  - `read_context_snapshot(...)`
  - `accept_assistant_turn_async(...)`
- `public-service/internal_api.py` 已提供与之匹配的 internal routes：
  - `POST /internal/conversations/{conversation_id}/messages/user`
  - `GET /internal/conversations/{conversation_id}/context-snapshot`
  - `POST /internal/conversations/{conversation_id}/messages/assistant-async`
- `fastQA/chat_persistence.py` 已在 adapter 层实现：
  - 执行前 authority snapshot 读取
  - user turn authority write
  - assistant final event authority async accept
  - pending overlay 的 Redis 兜底，用来在 assistant 事件异步物化前改善多实例/前后端时序体验

对 highThinkingQA 的意义：
- 不需要重新发明一套协议；highThinkingQA 应优先复用 fastQA 现成的 authority contract 模式。
- 真正要做的是把 highThinkingQA 当前本地 `conversation_service` 读写路径替换为等价的 authority adapter。

## 6. 当前阶段的中间结论

### 6.1 迁移目标不是把 highThinkingQA 的整个 conversation 子系统搬去 public-service，而是先切换 ask 主链路的 authority 读写

理由：
- 用户当前目标聚焦在“highThinkingQA 普通 QA 的聊天记录持久化”。
- 文件上传、文件列表、文件下载、混合 QA 等能力已经明确不属于 highThinkingQA 的长期职责边界。
- 因此第一阶段更合理的做法是只迁移：
  - ask 前置 user write
  - ask 前置 context snapshot read
  - ask 收尾 assistant final accept
- highThinkingQA 本地 conversation CRUD/上传路由后续可逐步收缩，未必要一次性重构完。

### 6.2 gateway 的最终职责建议仍应收敛为“分发 + 透传”，而不是 authority 写入枢纽

当前事实：
- gateway 现在确实在做 persistence proxy。

但从职责边界看：
- gateway 负责路由分发；
- 执行服务（fastQA/highThinkingQA）最清楚本次实际 route、final summary、steps、引用、流结束状态；
- public-service 负责权威落盘。

因此更稳定的边界是：
- gateway 不做真实持久化；
- gateway 只把标准化 payload 转发给目标执行服务；
- highThinkingQA 与 fastQA 各自通过 authority client 调 public-service。

这点与用户此前的架构判断一致。


### 5.8 highThinkingQA 当前仍挂着完整 conversation/upload 路由面，说明它还没完成“只保留普通 QA”的边界收缩

代码锚点：
- `highThinkingQA/server_fastapi/app.py`
- `highThinkingQA/server_fastapi/routers/__init__.py`
- `highThinkingQA/server_fastapi/routers/conversation.py`
- `highThinkingQA/server_fastapi/routers/upload.py`

已确认行为：
- `register_routers()` 仍然注册了 `ask_router`、`conversation_router`、`upload_router`、`documents_router` 等整套路由。
- 这意味着 highThinkingQA 目前不仅处理普通 QA，也仍暴露本地会话 CRUD、上传、文件相关接口。

对迁移边界的影响：
- 用户已经明确：文件 QA / 混合 QA 最终都归 fastQA；highThinkingQA 只做自己的普通 QA。
- 因此 highThinkingQA 后续除了迁移 ask 主链路的 authority 读写外，还需要评估：
  - 是否保留本地 conversation/upload 路由仅作兼容
  - 还是逐步下线并完全由 gateway + public-service 承接
- 但这是“边界收缩”议题，优先级低于本次 ask 持久化迁移本身。

### 5.9 highThinkingQA 当前配置已经预留 authority rollout 字段，但实现层尚未真正接入

代码锚点：
- `highThinkingQA/config.py`

已确认行为：
- 配置层已有：
  - `CONVERSATION_EXECUTION_AUTHORITY_TARGET`
  - `CONVERSATION_ASSISTANT_WRITE_TARGET`
  - `CONVERSATION_USER_WRITE_TARGET`
  - `CONVERSATION_CONTEXT_READ_TARGET`
  - `CONVERSATION_OVERLAY_ENABLED`
- `_resolve_conversation_rollout()` 也已经能解析 `legacy/public_service/shadow_public_service`。

但当前实现事实：
- ask 路由与 context loader 仍直接调用本地 `conversation_service`，并未看到对应 authority client 落地。

结论：
- highThinkingQA 当前属于“配置先行，执行未切”的状态。
- 这对迁移是好事，因为 rollout 配置模型已经有了，但实现仍需补齐 adapter/client/hook。


### 5.10 gateway 当前的 persistence proxy 不是 authority contract，而是走 public-service 浏览器侧消息接口

代码锚点：
- `gateway/app/services/conversation_persistence.py`

已确认行为：
- `persist_user_message()` / `persist_assistant_summary()` 最终都会走 `_add_message()`。
- `_add_message()` 调用的是：
  - `POST {public-service}/api/v1/conversations/{conversation_id}/messages`
- 它透传的是前端 `Authorization`，而不是 `X-Internal-Service-Name / Token` 这种 internal authority 头。
- 它写入的是标准浏览器消息接口，而不是 authority 专用的：
  - `/internal/.../messages/user`
  - `/internal/.../messages/assistant-async`
  - `/internal/.../context-snapshot`

结论：
- gateway 当前这条 persistence proxy 更像“代前端补写会话消息”，而不是“执行服务 authority 集成”。
- 如果 highThinkingQA 也继续依赖 gateway 代写，会导致职责继续耦合在 gateway 上。
- 从最终边界看，高优先级方案仍应是：highThinkingQA 直接复用 fastQA 那套 authority client -> public-service internal API。


## 7. 建议迁移方案（针对 highThinkingQA 普通 QA）

### 7.1 目标链路

目标链路应统一为：

1. 前端 -> gateway
2. gateway 根据 mode/file_context 做路由判定
3. 若是 thinking 普通 QA，则转发到 highThinkingQA
4. highThinkingQA 在执行前：
   - 调 public-service authority `write_user_turn`
   - 调 public-service authority `read_context_snapshot`
5. highThinkingQA 使用 authority snapshot + 请求体 chat_history 构造执行上下文
6. highThinkingQA 流式产出答案给 gateway/前端
7. 流结束后，highThinkingQA 以 final event 调 public-service authority `accept_assistant_turn_async`
8. public-service 异步 inbox worker 物化 assistant turn，并更新 summary / conversation_state

### 7.2 明确职责边界

- gateway:
  - 接收前端请求
  - 解析认证、trace_id
  - 判定 actual_mode / route / source_scope
  - 转发到 highThinkingQA 或 fastQA
  - 不作为会话写入主责任方

- highThinkingQA:
  - 仅负责 thinking 普通 QA 的执行
  - 负责 authority user write / context read / assistant async accept
  - 不负责文件 QA / 混合 QA

- fastQA:
  - 继续负责文件 QA / 混合 QA / fast 模式普通 QA
  - 其 authority 集成模式可作为 highThinkingQA 模板

- public-service:
  - 继续作为权威会话存储
  - 负责 internal authority contract、幂等、最终一致性、assistant inbox 物化

### 7.3 为什么不建议把持久化先转回 gateway

虽然 gateway 当前已经能通过浏览器会话接口写消息，但不建议把 highThinkingQA 的持久化迁移目标定义成“由 gateway 继续代写”，原因是：

- gateway 不掌握最完整的执行内部状态，特别是 highThinkingQA 最终 `done` 的真实字段、内部 route、steps、rewrite/context 元信息；
- gateway 目前使用的是 browser-facing conversation API，不是 authority contract；
- gateway 这样会逐渐变成“分发 + 会话写入编排器”，职责过重；
- fastQA 已有更好的 authority 模式，不需要再走回头路。

结论：
- highThinkingQA 应直接对接 public-service internal authority API，而不是让 gateway 继续持久化代理化。


### 7.4 `public_service` 模式下的失败策略

为了同时兼顾多实例一致性与用户体验，失败策略明确如下：

- `write_user_turn(...)`：`fail-closed`
  - 原因：如果用户问题没有进入权威会话，就继续执行会制造顺序错乱与上下文漂移。
  - 行为：直接返回错误，不启动 ask 执行。

- `read_context_snapshot(...)`：`fail-closed`
  - 原因：thinking 普通 QA 是多轮上下文敏感链路，缺失 authority snapshot 会让回答在错误上下文上继续生成。
  - 行为：直接返回错误，不启动 ask 执行。

- `accept_assistant_turn_async(...)`：`fail-open`，但必须伴随补救机制
  - 原因：此时答案通常已经生成甚至已流给前端，强行回滚会显著伤害用户体验。
  - 行为：
    - 前端答案继续正常结束
    - 记录错误日志/指标
    - 保留 pending overlay
    - 触发本地重试任务（至少进 ordered dispatcher；若后续需要更强保证，再补 durable retry）

这套策略意味着：
- ask 启动前的 authority 依赖必须严格；
- ask 收尾后的 authority 依赖允许最终一致，但不能无补救地静默丢失。

## 8. highThinkingQA 迁移时需要补齐的组件

### 8.1 authority client

建议直接仿照 fastQA 新增（名称可调整）：
- `highThinkingQA/server/services/conversation_authority_client.py`

能力至少包括：
- `write_user_turn(...)`
- `read_context_snapshot(...)`
- `accept_assistant_turn_async(...)`

要求：
- internal auth header 与 fastQA 一致
- source_service 固定为 `highThinkingQA`
- 幂等键规则与 fastQA/public-service 一致：`{conversation_id}:{trace_id}:{operation}`
- requested_mode / actual_mode 必须都使用 `thinking`

### 8.2 authority-aware chat persistence adapter

建议仿照 fastQA 新增 adapter 层，而不是把 authority 调用散在 ask router：
- `highThinkingQA/server/services/chat_persistence.py`

建议提供：
- `load_conversation_context(...)`
- `persist_user_message(...)`
- `persist_assistant_summary(...)`

好处：
- 可以把 rollout 开关、sync/async、overlay、异常降级都集中到一层
- ask router / ask service 只依赖抽象接口，不关心 authority 细节

### 8.3 authority snapshot -> 当前上下文模型的映射层

当前 `conversation_context_service.py` 期望拿到：
- `recent_turns`
- `summary`
- `conversation_id`
- `user_id`

迁移后应改为：
- 先从 authority snapshot 读取 `recent_turns + summary + conversation_state`
- 再与请求体 `chat_history` 做 overlap 合并
- 保持当前 `_merge_turns()` 和 `_apply_history_budget()` 的行为不变

迁移原则：
- 优先替换数据源，不先改上下文裁剪逻辑
- 这样回归面最小

### 8.4 assistant pending overlay（建议复用 fastQA 模式）

是否需要 overlay：
- 建议需要

原因：
- 用户已经明确“用户体验优先，答案输出必须平滑”
- assistant turn 走 async accept 后，到 public-service 真正物化之间会存在短暂空窗
- 如果下一问马上到达，只读 authority snapshot 可能还看不到刚刚结束的 assistant 回复

建议方案：
- 复用 fastQA 的 `pending_overlay` 思路
- 在 highThinkingQA `persist_assistant_summary()` 收尾时：
  - 先把 minimal assistant overlay 写到 Redis
  - 再异步调用 authority `accept_assistant_turn_async`
- `load_conversation_context()` 读 snapshot 后：
  - 尝试 merge pending overlay
  - 当 authority snapshot 已追上时清除 overlay

### 8.5 rollout 开关

highThinkingQA 配置层已经有 rollout 相关字段，因此建议按阶段启用：

- Phase 0: `legacy`
  - 继续本地 conversation_service 读写
- Phase 1: `shadow_public_service`
  - 主读写仍用 legacy
  - 并行向 public-service 影子写入/对比
- Phase 2: `public_service`
  - ask 主链路切到 authority read/write
  - 本地 conversation_service 不再参与 ask 持久化

注意：
- 即便进入 `public_service`，也不代表 highThinkingQA 的本地 conversation/upload 路由立刻删除
- 先切 ask 主链路，再收边界更稳妥

## 9. 迁移实施顺序建议

### Phase A: 影子实验态，只允许 `shadow_public_service` 对比，不允许单独上线半切主链

目标：
- 在不改变线上 authority 的前提下，验证 public-service authority 读写是否与现有路径一致。

动作：
- 保持 `legacy` 为主读写路径
- `shadow_public_service` 只做并行 shadow write / snapshot compare / 指标记录
- 不允许出现“user/context 已切 authority，但 assistant 仍落本地”这种半切生产态

约束：
- 该阶段只能作为实验/压测/对照阶段存在，不能作为正式上线终态
- 一旦进入正式 `public_service` 模式，必须同时切换 user write、context read、assistant async accept、overlay

### Phase B: 正式切 `public_service`，一次性完成 ask 主链闭环

目标：
- 完成 ask 主链路 authority 化闭环

动作：
- ask 路由前置 user write 改成 authority user write
- context loader 改成 authority snapshot read
- ask 路由收尾改成 `accept_assistant_turn_async`
- 增加 pending overlay Redis 兜底
- 去掉本地 `conversation_service.add_message` / `refresh_conversation_summary` 对 ask 主链路的直接依赖

收益：
- 完整实现“执行服务只负责生成，public-service 负责权威持久化”

### Phase C: 收缩 highThinkingQA 本地 conversation 能力

目标：
- 让 highThinkingQA 的边界与用户定义一致：只做普通 QA

动作：
- 评估 conversation/upload/documents 路由是否还需要保留兼容
- 若前端与 gateway 已全部切到 public-service，可逐步下线本地 conversation/upload surface

说明：
- 这是边界治理阶段，不是 ask 持久化迁移的阻塞项

## 10. 风险与检查清单

### 高风险

- [open] highThinkingQA 当前上下文读取仍走本地 `conversation_service.get_conversation_context_snapshot`
  - 风险：切换 authority 后如果上下文 merge 行为变了，多轮普通 QA 可能退化

- [open] highThinkingQA 当前 assistant 持久化仍直接本地 `add_message + refresh_conversation_summary`
  - 风险：若直接硬切 async accept，没有 overlay 会出现“上一轮回答刚结束，下一轮看不到”的时序空窗

- [open] gateway 当前仍会对 ask 做 browser API 持久化代理
  - 风险：若 highThinkingQA 也开始 authority 写入，而 gateway 未关停对应代理，会产生重复写入/职责冲突

### 中风险

- [open] highThinkingQA 当前仍挂着 conversation/upload/documents 路由
  - 风险：即使 ask 主链路迁完，服务边界仍不清晰，后续容易再引入旁路写入

- [open] highThinkingQA rollout 配置虽已存在，但代码层尚无 authority adapter
  - 风险：配置与执行脱节，容易误判“已经支持 public_service”

### 低风险

- [open] 请求 schema 仍允许 `fast/thinking/patent`
  - 风险：只要 gateway 路由正确，这不是主问题；但后续可以考虑收紧 highThinkingQA 自己的 mode 保护

## 11. 下一步待补的审阅内容

- 子 agent 的 gateway 详细判定文档结果
- 子 agent 的 public-service authority/runtime/inbox 详细结果
- 子 agent 的 highThinkingQA 新旧实现逐文件对照结果
- 基于以上结果，再把本 spec 扩展成“实施任务清单 + 回归检查矩阵”


### 5.11 public-service 的 authority 能力已经明确支持 `highThinkingQA`

代码锚点：
- `public-service/backend/app/modules/conversation/internal_api.py`
- `public-service/backend/app/modules/conversation/service.py`
- `public-service/backend/app/modules/conversation/assistant_inbox.py`
- `public-service/backend/app/core/runtime.py`

已确认行为：
- `internal_api.py` 的 `_ALLOWED_SOURCE_SERVICE_MODES` 已显式声明：
  - `fastQA -> {fast}`
  - `highThinkingQA -> {thinking}`
- 这意味着 public-service authority contract 在策略层已经预留了 highThinkingQA 的合法调用身份。

具体能力：
- `add_authority_user_message(...)`
  - 校验 `source_service in {fastQA, highThinkingQA}`
  - 基于 `conversation_lock` 落盘
  - 按 `idempotency_key` 对 user turn 去重
  - 更新 JSON 文档、message_count、cache
- `accept_authority_assistant_async(...)`
  - 先校验幂等和 source_service
  - 不同步写 assistant message，而是 enqueue 到 authority assistant inbox
- `AuthorityAssistantInboxWorker.run_once()`
  - claim pending tasks
  - 调 `materialize_authority_assistant_task(...)`
  - 成功后 mark done，失败则 retry/fail
- runtime 已在 `public-service/backend/app/core/runtime.py` 启动并监控 authority assistant inbox worker

结论：
- 从 public-service 视角看，highThinkingQA 迁移所需的服务端承接能力已经存在。
- 当前缺口主要不在 public-service，而在 highThinkingQA 客户端/adaptor 侧尚未接入。

### 5.12 public-service 的 assistant 持久化是“accept -> queue -> materialize”，不是同步完成

这点对 highThinkingQA 很关键：
- `accept_authority_assistant_async()` 返回 accepted，不代表 assistant turn 已立即出现在权威快照里。
- 真正写入发生在 `materialize_authority_assistant_task()` 被 inbox worker 消费时。

迁移含义：
- 如果 highThinkingQA 不做 overlay，下一轮问题可能读到落后的 snapshot。
- 因此 overlay 不是锦上添花，而是保持体验连续性的关键配套。


### 5.13 子文档补充：gateway 分流边界已经与目标职责基本一致

子文档：
- `docs/superpowers/specs/2026-03-23-gateway-routing-review.md`

补充结论：
- 当前 gateway 已经把“文件/混合 -> fastQA、普通 kb_only + thinking -> highThinkingQA”这条边界写进分流规则。
- 因此 highThinkingQA 持久化迁移不需要再讨论文件 QA / 混合 QA 的归属，范围可以明确锁定为 thinking 普通 QA。
- 子文档同时指出一个重要风险：`conversation file provider` 故障会影响带 `conversation_id` 的请求，包括普通 QA；这意味着 gateway 在进入普通 QA 分支前仍依赖文件上下文解析链路。
- 子文档还指出 legacy alias 兼容削弱的风险：若旧客户端仍发送旧字段，可能把本该进入 fastQA 的文件请求误判成普通 QA。


### 5.14 highThinkingQA 当前 ask / ask_stream 的持久化挂点已经很集中，适合替换为 authority adapter

代码锚点：
- `highThinkingQA/server_fastapi/routers/ask.py`

已确认行为：
- 同步 ask：
  - `ask_v1()` / `ask_v1_mode()` 在真正执行前调用 `_persist_user_message_if_needed()`
  - 执行成功后立即调用 `_persist_assistant_message_if_needed()`
- 流式 ask：
  - `ask_stream_v1()` / `ask_stream_v1_mode()` 在开始流式前调用 `_persist_user_message_if_needed()`
  - `_build_stream_response()` 在 finally 中调用 `_persist_assistant_message_if_needed()`
- 也就是说，高ThinkingQA 的持久化挂点已经集中在两个 helper：
  - `_persist_user_message_if_needed()`
  - `_persist_assistant_message_if_needed()`

这对迁移的意义：
- 这是非常好的切口。
- 第一阶段甚至不需要改动 `execute_ask()` / `stream_ask_events()` 主执行逻辑，只需替换这两个 helper 和 `conversation_context_service` 的数据源，就能完成 ask 主链路 authority 化。


### 5.15 子文档补充：public-service authority 的真相层是 JSON chat document，不是旧消息表

子文档：
- `docs/superpowers/specs/2026-03-23-public-service-authority-review.md`

补充结论：
- `public-service` 现阶段的 authority 真相层已经是 JSON chat document；`conversations` 表主要承担索引、版本锚点和定位作用。
- `conversation_files` 继续保留文件元数据；`conversation_json_outbox` 用于 JSON 镜像失败后的补偿。
- 这意味着 highThinkingQA 一旦切到 authority，不应再假设自己面对的是传统消息表接口，而应把 `context snapshot + async accept` 当作正式契约。
- 子文档还指出：assistant inbox 如果失败后落入 `failed`，当前没有自动 retry/requeue；这是 public-service 一侧真实存在的 operability 风险。
- 另一个关键点是：`snapshot.summary` 目前仍然偏空壳，不能简单等价成 highThinkingQA 现有本地 summary 语义。

迁移影响：
- highThinkingQA 迁移时，最近多轮上下文可以直接切 authority snapshot；
- 但若当前 highThinking 普通 QA 对会话摘要质量有依赖，就要单独评估 summary 字段的补齐/兼容策略。


### 5.16 子文档补充：highThinkingQA 当前存在 authority 分裂，不应再补镜像桥，而应直接切 authority

子文档：
- `docs/superpowers/specs/2026-03-23-highthinkingqa-persistence-review.md`

补充结论：
- 当前新版规范链路下，前端主会话 authority 已经在 `gateway + public-service`，但 highThinkingQA 仍保留并调用一整套本地 conversation 实现。
- 代码里没有看到 `public-service -> highThinkingQA` 的 conversation create/sync 桥接，因此 highThinkingQA 本地 conversation 大概率不是规范主链。
- 这意味着正确方向不是“把 public-service 会话再镜像一份到 highThinkingQA 本地”，而是直接把 highThinkingQA ask 主链切到 authority read/write。
- 子文档还确认了一个重要事实：highThinkingQA 本地 conversation 实现比旧版更重，已经包含 MySQL message row + JSON + outbox 的组合；如果继续维持这套本地 authority，只会让系统更分裂。

迁移上的直接决策：
- 放弃“跨服务镜像本地 conversation”方案；
- 采用“highThinkingQA 直接复用 fastQA authority 模式”方案。


## 12. 实施计划文档

基于本 spec 已产出可执行计划：
- `docs/superpowers/plans/2026-03-23-highthinkingqa-authority-migration.md`

该计划当前覆盖：
- highThinkingQA authority client
- chat persistence adapter + overlay
- context snapshot 切换
- ask router persistence hook 切换
- rollout wiring
- gateway thinking 路径去代理持久化
- cross-service regression coverage
- 最终联调与验收清单

