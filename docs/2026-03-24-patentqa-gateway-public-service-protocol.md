# PatentQA Gateway / Public-Service Protocol

## 文档状态

- 最后更新：2026-03-26
- 依据：`gateway`、`public-service`、`fastQA`、`highThinkingQA` 现有代码，以及已经落地的 `patent/` Phase 1 基础设施
- 作用：定义 `gateway -> patentQA -> public-service` 的跨服务协议、服务边界、运行时约束、落库责任和后续 rollout 要求

这份文档有三个明确目标：

1. 说明当前仓库里已经确认的事实
2. 说明 `patent/` 目录里已经实现到什么程度
3. 说明后续要把专利系统真正接通时，哪些外部改动必须补上

为了避免把“目标态”误写成“当前事实”，本文显式区分三种状态：

- 当前事实：已经存在于仓库代码中的行为
- patent 已实现能力：已经在 `patent/` 中实现，但外部系统尚未放行
- rollout 后目标态：外部依赖补齐后才能成立的端到端协议

## 适用读者

- `gateway` 路由与代理链路开发者
- `patentQA` 实现者
- `public-service` 会话权威层开发者
- 做联调、验收、上线 gating 的开发者或 reviewer

## 非目标

本文不定义以下内容：

- 专利检索、召回、排序、重排、引用抽取的内部算法
- 专利专用 citation schema
- 专利文件问答的最终协议
- 专利索引、切片、入库流水线

## 1. 当前架构结论

### 1.1 服务拓扑

当前仓库中的角色分工已经比较明确：

- `gateway`：统一入口、鉴权透传、路由决策、代理转发
- `public-service`：会话真相源、文件元数据权威、聊天持久化与异步物化
- `fastQA`：当前文件 QA / 混合 QA 的执行 owner
- `highThinkingQA`：thinking 模式执行 owner，已经更接近 authority-first 持久化模式
- `patent`：新建的独立 FastAPI 服务，当前已完成 Phase 1 基础设施骨架

### 1.2 patent 模式在 gateway 中已经被预留

`gateway` 代码已经预留了 `patent` backend role 和对应的 mode 名称，说明系统结构上已经允许：

- `POST /api/patent/ask`
- `POST /api/patent/ask_stream`
- 以及 `/api/{mode}/ask` / `/api/{mode}/ask_stream` 下的 `mode=patent`

但“结构可路由”不等于“生产可落地”。要让真实 durable 专利会话生效，还需要补齐本文后面列出的 rollout gate。

### 1.3 当前文件 QA 与混合 QA 的专利链路仍未打通

这里要区分“gateway 的路由意图”和“实际可执行链路”。

当前确认的事实是：

- `turn_mode=kb_only`：才有可能由 `patentQA` 执行
- `turn_mode=file_only`：gateway 会把 `actual_mode` 判成 `fast`
- `turn_mode=mixed`：gateway 会把 `actual_mode` 判成 `fast`

但当前还不能把这句话写成“文件和混合专利 turn 已经可用地走 fastQA”，因为：

- gateway 现在仍会把 `requested_mode=patent` 原样转发
- `fastQA` ingress 当前要求：
  - `requested_mode=fast`
  - `actual_mode=fast`
- 所以专利文件 / 混合 turn 的兼容链路还缺少 gateway rewrite，当前并未真正打通

因此现阶段正确表述是：

- `patentQA` 只实现 `kb_only` 专利问答
- 文件问题和混合问题的目标 owner 仍是 `fastQA`
- 但要让这条兼容链路真正可用，gateway 还必须补 rewrite

### 1.4 public-service 仍然是聊天持久化 owner

`patentQA` 当前设计和实现都遵守同一个原则：

- 聊天真相源不在 `patentQA`
- 聊天落库、会话上下文快照、assistant 最终物化仍由 `public-service` 负责
- `patentQA` 只负责：
  - 在 durable ask 上调用 authority API
  - 从 authority 读取 context snapshot
  - 在执行完成后提交 assistant final event
  - 用 Redis 提供跨实例协调、去重、overlay、缓存

这也是后续继续扩展专利系统时必须保留的边界。

## 2. 当前已实现状态

### 2.1 `patent/` Phase 1 已经落地的内容

`patent/` 目录下已经实现了可运行的 FastAPI 骨架，包含：

- ask / ask_stream HTTP 接口
- health 接口和 durable readiness probe 语义
- forwarded auth 校验与 `user_id` 推导
- 严格的 patent Phase 1 请求协议校验
- durable / ephemeral 双路径分流
- authority client
- Redis key factory、锁、inflight、pending-turn、overlay、execution cache、retrieval cache 预留
- Gunicorn 包装配置
- 多实例一致性所需的 conversation lock + turn dedupe + runtime renewal 机制
- Phase 1 stub executor / pipeline

### 2.2 当前还没有落地的内容

当前没有实现的能力也必须明确写出来，避免后续开发者误判：

- 真正的专利检索流水线
- 专利专有引用对象
- 文件 / 混合专利问答
- `gateway` 的专利生产流量切换
- `public-service` 对 `patentQA/patent` authority caller 的正式放行

### 2.3 当前 durable 模式默认仍应视为关闭

虽然 `patent` 内部已经有 durable 代码路径，但它仍然是 feature-gated 的：

- `PATENT_DURABLE_MODE_ENABLED=false` 时，durable ask 会直接被拒绝
- `PATENT_DURABLE_AUTHORITY_ENABLED=false` 时，不会初始化 authority client
- `PATENT_REDIS_ENABLED=false` 时，durable 依赖也不 ready

也就是说，当前 `patent/` 实现的是“durable 基础设施已经具备”，不是“仓库外部已经允许 durable patent 生产可用”。

## 3. 服务边界与责任分工

### 3.1 Gateway 的责任

`gateway` 在专利模式下仍然是以下事实边界的 owner：

- 前端 ask 输入接入
- `requested_mode -> actual_mode` 决策
- `turn_mode` 判定
- 文件上下文解析
- 是否走 `kb_only / file_only / mixed`
- trace header 透传
- auth header 透传
- 将规范化后的 ask payload 转发给目标 QA backend

`patentQA` 不应该在收到请求后重新做 mode 路由决策，只应该校验协议是否符合 Phase 1 约束。

### 3.2 PatentQA 的责任

`patentQA` 当前负责：

- 校验 gateway 是否按约定转发了 `patent/kb_only` 请求
- 在 durable ask 上从 forwarded bearer token 推导 `user_id`
- 通过 authority API 完成：
  - user turn write
  - context snapshot read
  - assistant async accept
- 使用 Redis 进行多实例并发协调与短期状态管理
- 输出统一的 sync response / SSE event
- 在 authority accept 成功前，不宣布这次 durable turn 成功

### 3.3 Public-Service 的责任

`public-service` 在目标态中继续承担：

- 会话元数据真相源
- conversation file metadata 真相源
- canonical transcript 持久化
- context snapshot 输出
- assistant async 接受与最终物化
- JSON / object storage / cache 刷新与重试

结论很明确：

- `patentQA` 不持久化 canonical transcript
- `patentQA` 的 Redis 也不是 durable transcript store
- durable transcript owner 必须保持为 `public-service`

## 4. Gateway -> PatentQA 协议

### 4.1 Phase 1 only 接入条件

当前任何真正送到 `patentQA` 的 ask，都必须满足：

- `requested_mode = patent`
- `actual_mode = patent`
- `route = kb_qa`
- `turn_mode = kb_only`
- `used_files = []`
- `execution_files = []`
- `selected_file_ids = []`
- `primary_file_id = null`
- `allow_kb_verification = false`

其中任一条件不满足，`patentQA` 都会把它判定为协议错误，而不是兜底执行。

### 4.2 请求字段归一化规则

当前 `patent` 服务里对请求做了以下规范化：

- `question`
  - 必须是非空字符串
- `trace_id`
  - 必须是非空字符串
- `conversation_id`
  - 允许 `int`
  - 允许正整数 numeric string
  - `null`、空串、非数字字符串、非正数都会被归一化为 `None`
- `chat_history`
  - 缺省时归一化为 `[]`
- `file_selection`
  - 缺省时归一化为 `{}`
- `options`
  - 缺省时归一化为 `{}`
- `source_scope`
  - 允许 `string | null`
  - 空字符串会归一化为 `None`

一个重要行为是：

- `conversation_id` 非法时不会报错
- 而是把本次 ask 自动降为 ephemeral

### 4.3 Durable 与 Ephemeral 判定

`patent` 里的持久化模式判定非常简单：

- `conversation_id` 归一化后为正整数：`durable`
- 否则：`ephemeral`

这意味着 durable ask 的前置条件不是“前端说自己想持久化”，而是“请求里确实有合法 conversation_id，并且 durable feature gate 已打开”。

### 4.4 Patent 服务暴露的 ask 路径

当前 `patent` 服务已经实现以下等价入口：

- `POST /api/ask`
- `POST /api/v1/ask`
- `POST /api/patent/ask`
- `POST /api/v1/patent/ask`
- `POST /api/ask_stream`
- `POST /api/v1/ask_stream`
- `POST /api/patent/ask_stream`
- `POST /api/v1/patent/ask_stream`

这让它既能直接本地测试，也能兼容 gateway 代理路径。

## 5. PatentQA -> Public-Service Authority 协议

### 5.1 这部分要区分“patent 已实现 outbound contract”和“public-service 当前是否接受”

当前 `patent` 代码已经实现了完整的 authority client 和 outbound payload 模型，但这不代表端到端现在已经可用。

当前真实状态是：

- `patent` 已经会按 `source_service=patentQA`、`requested_mode=patent`、`actual_mode=patent` 组装 authority 请求
- 但 `public-service` 当前 schema 和 allowlist 仍只接受：
  - `source_service in {fastQA, highThinkingQA}`
  - `requested_mode/actual_mode in {fast, thinking}`

因此：

- 下文描述的是 `patent` 侧已经实现好的 outbound contract
- 它属于“patent 已实现能力 / rollout 后目标态协议”
- 不是“当前 public-service 已经接受的现状”

### 5.2 目标调用顺序

rollout 完成后的 durable patent ask 标准顺序是：

1. `write user turn`
2. `read context snapshot`
3. 执行专利问答
4. `accept assistant async`
5. 由 `public-service` worker 异步物化 assistant turn

这个顺序已经在 `patent` 代码里落地，但当前仍受 `public-service` 放行条件阻塞。

### 5.3 Authority headers

`patentQA` 调用 `public-service` internal API 时使用：

- `X-Internal-Service-Name: patentQA`
- `X-Internal-Service-Token: <PATENT_AUTHORITY_INTERNAL_TOKEN>`
- `X-Trace-Id: <trace_id>`

这里还有一个 rollout 关键约束：

- `PATENT_AUTHORITY_INTERNAL_TOKEN` 必须与 `public-service` 侧的 `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` 对齐
- 如果两边 token 不一致，所有 authority 调用都会直接 `401`

### 5.4 User turn write

接口：

- `POST /internal/conversations/{conversation_id}/messages/user`

当前 `patent` 侧已经固定以下 outbound 语义：

- `source_service = patentQA`
- `route = kb_qa`
- `requested_mode = patent`
- `actual_mode = patent`
- `idempotency_key = {conversation_id}:{trace_id}:user`
- `message.role = user`
- `message.content = 原始 question`

### 5.5 Context snapshot read

接口：

- `GET /internal/conversations/{conversation_id}/context-snapshot`

当前 `patent` 侧固定 query 参数：

- `user_id`
- `trace_id`
- `source_service=patentQA`
- `route=kb_qa`
- `requested_mode=patent`
- `actual_mode=patent`

`patent` 当前依赖以下返回字段：

- `conversation_id`
- `user_id`
- `snapshot_version`
- `updated_at`
- `summary`
- `recent_turns`
- `conversation_state`

### 5.6 Assistant async accept

接口：

- `POST /internal/conversations/{conversation_id}/messages/assistant-async`

当前 `patent` 侧固定以下 outbound 语义：

- `source_service = patentQA`
- `route = kb_qa`
- `requested_mode = patent`
- `actual_mode = patent`
- `idempotency_key = {conversation_id}:{trace_id}:assistant`
- `final_event.done_seen = true`
- `final_event.answer_text = 最终答案`
- `final_event.steps = execution_result.steps`
- `final_event.references = execution_result.references`
- `final_event.used_files = execution_result.used_files`
- `final_event.timings = execution_result.timings`

### 5.7 Authority 调用的失败策略

这是后续开发者必须遵守的硬边界：

- user write 失败：本次 durable ask 直接失败
- context snapshot 失败：本次 durable ask 直接失败
- assistant async accept 失败：本次 durable ask 直接失败，不能再返回成功

在当前实现里：

- sync ask：不会返回成功响应
- stream ask：不会发送 `done`，而是发送 terminal `error`

这意味着 `patentQA` 当前采用的是严格的 authority-first durable 语义。

## 6. 聊天持久化与上下文恢复模型

### 6.1 durable transcript owner

rollout 完成后，专利聊天的 durable transcript 只应由 `public-service` 负责：

- user message：authority write
- context snapshot：authority read
- assistant durable message：authority async accept + worker materialize

### 6.2 Patent 侧为什么还需要 Redis overlay

虽然 durable transcript 在 `public-service`，`patent` 仍然需要 Redis overlay，原因是：

- assistant async accept 成功后，assistant turn 不是立刻就出现在权威 transcript 里
- 下一轮问题如果非常快到达，`context-snapshot` 可能暂时还看不到刚刚的 assistant turn
- 为了保证短时间连续对话的“读己之写”，`patent` 会把 assistant 文本写入 Redis overlay

当前 overlay 行为是：

- key：`{prefix}:{env}:overlay:assistant:{user_id}:{conversation_id}`
- 内容至少包含：
  - `trace_id`
  - `route`
  - `assistant_content`
- TTL 默认 `300s`

### 6.3 Overlay 合并规则

当前 `patent` 的上下文读取顺序是：

1. 从 `public-service` 拿 `context-snapshot`
2. 读取 Redis overlay
3. 如果 snapshot 已经包含该 assistant `trace_id`，清掉 overlay
4. 如果 snapshot 还没收敛，且 `chat_history` 里也没有同 trace assistant，则把 overlay assistant 临时拼回上下文

这意味着：

- Redis overlay 是 authority snapshot 的补偿层
- 不是单独的长期会话存储
- authority 收敛后，overlay 必须退出

### 6.4 Chat history 的来源优先级

当前 durable ask 的上下文来源优先级是：

1. authority `recent_turns`
2. Redis overlay assistant 补偿

而不是直接信任 gateway 传来的 `chat_history`。  
`chat_history` 在 durable 模式下更像是兼容输入，不是权威来源。

## 7. Redis、多实例一致性与性能基础设施

### 7.1 Redis 在 patent 中的角色

当前 Redis 在 `patent` 里承担的是“协调层”，不是“真相源”：

- conversation lock
- inflight marker
- pending turn marker
- turn result cache
- assistant overlay
- retrieval cache 预留

### 7.2 Key model

当前 key factory 已经固定出以下 key 族：

- conversation lock
  - `{prefix}:{env}:exec:conversation-lock:{conversation_id}`
- turn identity
  - `{prefix}:{env}:exec:turn:{conversation_id}:{trace_id}`
- inflight
  - `{prefix}:{env}:coord:inflight:{conversation_id}:{trace_id}`
- pending turn
  - `{prefix}:{env}:coord:pending-turn:{conversation_id}`
- execution cache
  - `{prefix}:{env}:exec:cache:{normalized_request_key}`
- retrieval cache
  - `{prefix}:{env}:retrieval:cache:{normalized_query_key}`
- overlay assistant
  - `{prefix}:{env}:overlay:assistant:{user_id}:{conversation_id}`

默认前缀来自：

- `PATENT_REDIS_KEY_PREFIX`
- 默认值 `patent`

### 7.3 单会话单活动执行

多实例部署时，`patent` 当前通过 Redis conversation lock 保证：

- 同一个 `conversation_id`
- 同一时刻只允许一个 durable turn 真正进入执行区

如果锁已被持有：

- 会返回 `PATENT_BUSY`
- 当前 HTTP 状态是 `409`

### 7.4 同 trace 幂等与结果复用

对于同一个 `conversation_id + trace_id`，当前实现还做了两层处理：

- `turn identity`：防止重复执行
- `turn result cache`：如果之前同 trace 已经执行完成，可直接复用执行结果

这保证了：

- 重试请求不会轻易造成重复 authority side effect
- 多实例下的重复转发也有较强幂等保护

### 7.5 Pending turn marker

`pending-turn` key 用来表达：

- 某个 conversation 当前已有一个 trace 正在处理
- 并记录 user write 是否已经完成

它解决的是“新 trace 抢入同一 conversation”以及“中途失败时该如何清理”的问题。

### 7.6 Runtime guard renewal

durable turn 执行中，`patent` 会启动后台 guard 线程定期续租：

- conversation lock TTL
- inflight TTL

如果续租失败：

- 当前请求会被视为运行时不健康
- 返回 `SERVICE_NOT_READY`
- 不继续假装本次 durable turn 成功

这对多实例很关键，因为它避免锁过期后出现双活执行。

### 7.7 当前 TTL 策略

当前 `ChatPersistenceService` 的默认 TTL 是：

- conversation lock：`120s`
- inflight：`120s`
- turn state / turn result：`1800s`
- overlay：`300s`

这些值目前是 Phase 1 默认值，后续如果检索链路变长，需要一起复核。

## 8. Patent 对外返回协议

### 8.1 Sync response

当前 `patent` sync 成功响应已经固定为 wrapped shape：

```json
{
  "success": true,
  "data": {
    "final_answer": "string",
    "timings": {},
    "metadata": {
      "requested_mode": "patent",
      "actual_mode": "patent",
      "route": "kb_qa",
      "mode": "patent",
      "query_mode": "patent",
      "conversation_id": 123
    },
    "references": [],
    "pdf_links": [],
    "reference_links": [],
    "trace_id": "req_xxx"
  },
  "trace_id": "req_xxx"
}
```

### 8.2 SSE event set

当前 stream ask 已实现并真实发出的事件类型是：

- `metadata`
- `step`
- `content`
- `done`
- `error`

注意：

- `heartbeat` schema 已预留
- 但当前实现还没有主动发送 heartbeat event

### 8.3 Done 事件成功条件

这是当前协议里最关键的成功语义之一：

- durable ask 只有在 `assistant-async` 已被 authority 接受后，才允许发送 `done`
- 如果 accept 失败，stream 只能以 `error` 结束

### 8.4 Error envelope

当前 HTTP error 统一形状为：

```json
{
  "success": false,
  "code": "STRING_CODE",
  "message": "human readable message",
  "error": "machine_readable_slug",
  "retriable": false
}
```

SSE terminal error 形状为：

```json
{
  "type": "error",
  "code": "STRING_CODE",
  "error": "machine_readable_slug",
  "message": "human readable message",
  "trace_id": "req_xxx",
  "seq": 3,
  "ts": "2026-03-26T00:00:00Z"
}
```

### 8.5 当前已实现的 error code

`patent` 当前已声明并测试覆盖的错误码包括：

- `TOKEN_MISSING`
- `TOKEN_INVALID`
- `INVALID_REQUEST`
- `PROTOCOL_MISMATCH`
- `AUTHORITY_UNAVAILABLE`
- `PATENT_BUSY`
- `DURABLE_MODE_DISABLED`
- `SERVICE_NOT_READY`
- `INTERNAL_ERROR`

其中 `PATENT_BUSY` 目前有两种实际触发场景：

- durable conversation 已有 in-flight turn：`409`
- stream 并发槽位已满：`429`

## 9. Auth 与身份边界

### 9.1 Gateway 必须透传 Authorization

`patent` 当前不会从 gateway body 中拿 `user_id`。durable ask 下它依赖：

- `Authorization: Bearer <token>`

然后本地解码 token，推导 `user_id`。

### 9.2 Patent 当前的 user_id 推导规则

当前 token payload 中会按以下顺序找用户身份字段：

- `user_id`
- `uid`
- `sub`

最终都必须能转成正整数。

### 9.3 Durable ask 的 auth 前置条件

durable ask 下，如果出现以下任一情况，请求都会失败：

- Authorization 缺失
- bearer header 不合法
- token 无法解码
- token 中拿不到正整数 `user_id`
- `JWT_SECRET` 未配置

但 ephemeral ask 不依赖这些条件。

## 10. Health / Durable Probe 协议

### 10.1 当前接口现状

当前 `patent` 只实现了 health 接口，没有单独命名的 readiness endpoint：

- `GET /api/health`
- `GET /api/v1/health`

但它通过 `?durable=true` 提供了更严格的 durable readiness probe 语义。

### 10.2 默认 health 语义

默认 health 返回：

- `success`
- `service`
- `status`
- `durable_mode_enabled`
- `durable_requested`
- `components`

组件层面至少包含：

- `runtime`
- `redis`
- `authority`

### 10.3 `?durable=true` 的特殊语义

如果调用：

- `GET /api/health?durable=true`

那么当前 `patent` 会做更严格检查：

- 必须带合法 Authorization
- `PATENT_DURABLE_MODE_ENABLED` 必须开启
- `runtime`、`redis`、`authority` 三个组件都必须 ready

如果鉴权失败返回 `401`；如果 durable 未开启或依赖未 ready，则返回 `503`。

这意味着后续接入环境探针时，可以区分：

- 进程活着
- durable 专利链路真的 ready

## 11. 运行配置与部署约束

### 11.1 关键配置项

后续专利系统开发和部署时，至少要理解这些配置：

- `PATENT_DURABLE_MODE_ENABLED`
- `PATENT_DURABLE_AUTHORITY_ENABLED`
- `PATENT_AUTHORITY_BASE_URL`
- `PATENT_AUTHORITY_INTERNAL_TOKEN`
- `PATENT_REDIS_ENABLED`
- `PATENT_REDIS_URL`
- `PATENT_REDIS_KEY_PREFIX`
- `PATENT_ASK_STREAM_MAX_CONCURRENT`
- `PATENT_ASK_EXECUTOR_MAX_WORKERS`
- `PATENT_GUNICORN_WORKERS`
- `PATENT_GUNICORN_THREADS`
- `PATENT_GUNICORN_TIMEOUT`
- `PATENT_GUNICORN_KEEPALIVE`
- `PATENT_GUNICORN_MAX_REQUESTS`
- `PATENT_GUNICORN_MAX_REQUESTS_JITTER`

### 11.2 Gunicorn 包装

`patent` 当前已经有单独的 `gunicorn.conf.py`，从配置读取：

- bind host / port
- worker class
- workers
- threads
- timeout
- keepalive
- `max_requests`
- `max_requests_jitter`

这意味着：

- `patent` 已经按照独立服务部署形态在准备
- 不是只面向单进程开发态

## 12. 外部 rollout gate

这是最重要的联调清单。只要其中任何一项没完成，都不能说“patent durable 链路已经正式上线”。

### 12.1 Gateway 侧必须补的改动

- 真正把 `kb_only + requested_mode=patent` 路由到 `patent` backend
- 对 `file_only / mixed + requested_mode=patent` 保持当前 `fastQA` ownership 意图
- 在兼容转发到 `fastQA` 时，按 `fastQA` 现有 ingress 限制重写：
  - `requested_mode=fast`
  - `actual_mode=fast`
- 在 rewrite 完成前，不要把专利文件 / 混合 turn 视为可用兼容链路
- 对 `actual_mode=patent` 禁止继续走 gateway 旧的 direct persistence
- 持续透传 Authorization 和 trace headers

### 12.2 Public-Service 侧必须补的改动

- authority allowlist 接受 `source_service=patentQA`
- authority schema literals 接受：
  - `requested_mode=patent`
  - `actual_mode=patent`
- `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` 与 `PATENT_AUTHORITY_INTERNAL_TOKEN` 对齐
- conversation authority 内部 API 与 worker 路径对 `patent` source/mode 保持一致处理

### 12.3 联调必须验证的行为

- durable patent ask 能完成 user write
- 随后能读到 authority context snapshot
- assistant async accept 成功
- worker 最终只物化一条 assistant turn
- 下一轮 ask 在 authority 未收敛前能通过 overlay 读到上一轮 assistant
- authority 收敛后 overlay 会退出
- 多实例下不会出现同 conversation 双活执行

## 13. 后续专利系统开发建议

### 13.1 应该继续在 `patent/server/patent/` 内扩展的内容

后续真正做专利系统时，优先往这些文件和目录演进：

- `patent/server/patent/pipeline.py`
- `patent/server/patent/executor.py`
- `patent/server/services/mode_profiles.py`
- 未来新增的 retrieval / rerank / grounding 相关模块

### 13.2 不应该破坏的稳定边界

后续扩展检索能力时，不要破坏这些已经确定的稳定边界：

- `public-service` 继续做 durable transcript owner
- `gateway` 继续做 route decision owner
- `patent` Redis 继续只做协调层，不做真相源
- `assistant-async accepted before done` 继续作为 durable 成功条件
- `file_only / mixed` 在正式接管前继续归 `fastQA`，但在 rewrite 完成前不能宣称链路可用

### 13.3 专利检索 Redis 的推荐位置

后续专利检索引入 Redis 时，应该优先复用当前已经预留的：

- `retrieval cache`
- `execution cache`

而不是新建另一套与 ask runtime 脱节的 key 模型。

## 14. 当前明确开放的问题

这些问题在当前仓库中仍然没有定论，后续做专利系统时要单独立项，不要在实现里默认拍脑袋决定：

- 专利文件问答什么时候由 `patentQA` 接管
- 专利 citation object 长什么样
- patent retrieval 的缓存粒度是 query 级、候选集级还是 rerank 级
- overlay 是否只存 assistant 文本，还是未来要带引用对象摘要
- compatibility phase 中，经过 `fastQA` 的 patent file turn 是否要额外保留原始 `requested_mode=patent`

## 15. 实现参考

主要实现参考文件：

- `gateway/app/routers/qa.py`
- `gateway/app/services/route_decision.py`
- `gateway/app/services/conversation_persistence.py`
- `fastQA/app/services/request_adapter.py`
- `public-service/backend/app/modules/conversation/internal_api.py`
- `public-service/backend/app/modules/conversation/authority_schemas.py`
- `public-service/backend/app/modules/conversation/service.py`
- `patent/server_fastapi/routers/ask.py`
- `patent/server_fastapi/routers/health.py`
- `patent/server/services/chat_persistence.py`
- `patent/server/services/conversation_authority_client.py`
- `patent/server/services/execution_cache.py`
- `patent/server/services/execution_lock.py`
- `patent/server/patent/cache_keys.py`
- `patent/server/patent/pipeline.py`
