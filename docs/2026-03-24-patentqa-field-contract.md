# PatentQA Field-Level Contract

## 文档状态

- 最后更新：2026-03-26
- 这是 `docs/2026-03-24-patentqa-gateway-public-service-protocol.md` 的字段级 companion 文档
- 目标：把当前 `patent/` 已实现的请求模型、响应模型、错误模型、authority 模型、health 协议写成可直接落地执行的契约

为了避免把本地已实现 outbound model 和跨服务已可执行链路混为一谈，本文同样区分：

- 当前事实
- `patent/` 本地已实现 contract
- rollout 后才能成立的外部 contract

## 0. 术语

- `requested_mode`：调用方请求的模式
- `actual_mode`：gateway 最终决定的执行模式
- `route`：gateway 选出的执行 route
- `turn_mode`：gateway 选出的本轮类型，当前主要是 `kb_only / file_only / mixed`
- `durable`：有合法 `conversation_id`，且走 authority + Redis 协调链路
- `ephemeral`：没有合法 `conversation_id`，不做 authority side effect

## 1. Frontend -> Gateway 里会影响 patent 路由的输入

| 字段 | 类型 | 是否必须 | 当前含义 |
| --- | --- | --- | --- |
| `question` | `string` | 是 | 用户问题文本 |
| `conversation_id` | `int|string|null` | 否 | 决定是否能进入 durable |
| `chat_history` | `array<object>` | 否 | 兼容输入 |
| `requested_mode` | `fast|thinking|patent` | 否 | 若为 `patent`，gateway 才会考虑专利路径 |
| `mode` | `fast|thinking|patent|null` | 否 | 兼容字段 |
| `pdf_context.*` | `object` | 否 | 会影响 gateway 是否把本轮判成文件 / 混合 |
| `options` | `object` | 否 | passthrough 扩展位 |

### 当前 Phase 1 路由结论

| 条件 | 当前事实 |
| --- | --- |
| `requested_mode=patent` 且 `turn_mode=kb_only` | 可按协议发往 `patentQA` |
| `requested_mode=patent` 且 `turn_mode=file_only` | gateway 路由意图会指向 `fast`，但当前兼容 rewrite 未完成，链路未真正打通 |
| `requested_mode=patent` 且 `turn_mode=mixed` | gateway 路由意图会指向 `fast`，但当前兼容 rewrite 未完成，链路未真正打通 |

## 2. Gateway -> PatentQA 请求契约

### 2.1 当前实际支持的 ask endpoint

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/ask` | sync ask 别名 |
| `POST` | `/api/v1/ask` | sync ask 别名 |
| `POST` | `/api/patent/ask` | sync ask 主观语义更明确 |
| `POST` | `/api/v1/patent/ask` | sync ask 主观语义更明确 |
| `POST` | `/api/ask_stream` | stream ask 别名 |
| `POST` | `/api/v1/ask_stream` | stream ask 别名 |
| `POST` | `/api/patent/ask_stream` | stream ask 主观语义更明确 |
| `POST` | `/api/v1/patent/ask_stream` | stream ask 主观语义更明确 |

### 2.2 Phase 1 协议硬约束

对于任何真正发送到 `patentQA` 的请求，当前代码要求：

| 字段 | 必须值 / 约束 | 不满足时 |
| --- | --- | --- |
| `requested_mode` | `patent` | `PROTOCOL_MISMATCH` |
| `actual_mode` | `patent` | `PROTOCOL_MISMATCH` |
| `route` | `kb_qa` | `PROTOCOL_MISMATCH` |
| `turn_mode` | `kb_only` | `PROTOCOL_MISMATCH` |
| `allow_kb_verification` | `false` | `PROTOCOL_MISMATCH` |
| `used_files` | `[]` | `PROTOCOL_MISMATCH` |
| `execution_files` | `[]` | `PROTOCOL_MISMATCH` |
| `selected_file_ids` | `[]` | `PROTOCOL_MISMATCH` |
| `primary_file_id` | `null` | `PROTOCOL_MISMATCH` |

### 2.3 请求字段表

| 字段 | 类型 | 是否必须 | 当前实现行为 |
| --- | --- | --- | --- |
| `question` | `string` | 是 | 去首尾空白后必须非空 |
| `conversation_id` | `int|string|null` | 否 | 正整数或正整数 numeric string 才保留，否则归一化为 `null` |
| `chat_history` | `array<object>` | 否 | 缺省归一化为 `[]`，元素必须是 object |
| `requested_mode` | `string` | 是 | 必须为 `patent` |
| `actual_mode` | `string` | 是 | 必须为 `patent` |
| `route` | `string` | 是 | 必须为 `kb_qa` |
| `source_scope` | `string|null` | 否 | 空字符串会归一化为 `null` |
| `turn_mode` | `string` | 是 | 必须为 `kb_only` |
| `kb_enabled` | `bool` | 是 | 当前只校验类型，不在 stub pipeline 中消费 |
| `allow_kb_verification` | `bool` | 是 | 当前必须为 `false` |
| `used_files` | `array<object>` | 是 | 当前必须为空 |
| `execution_files` | `array<object>` | 是 | 当前必须为空 |
| `selected_file_ids` | `array<int>` | 是 | 当前必须为空 |
| `primary_file_id` | `int|null` | 否 | 当前必须为 `null` |
| `file_selection` | `object` | 否 | 缺省归一化为 `{}`，可透传结构化元数据 |
| `trace_id` | `string` | 是 | 去首尾空白后必须非空 |
| `options` | `object` | 否 | 缺省归一化为 `{}` |

### 2.4 Durable / Ephemeral 判定表

| `conversation_id` 输入 | 归一化结果 | 持久化模式 |
| --- | --- | --- |
| `123` | `123` | `durable` |
| `"123"` | `123` | `durable` |
| `0` | `null` | `ephemeral` |
| `""` | `null` | `ephemeral` |
| `"abc"` | `null` | `ephemeral` |
| `null` | `null` | `ephemeral` |

### 2.5 Canonical Phase 1 请求样例

```json
{
  "question": "请总结这个专利主题",
  "conversation_id": 123,
  "chat_history": [],
  "requested_mode": "patent",
  "actual_mode": "patent",
  "route": "kb_qa",
  "source_scope": null,
  "turn_mode": "kb_only",
  "kb_enabled": false,
  "allow_kb_verification": false,
  "used_files": [],
  "execution_files": [],
  "selected_file_ids": [],
  "primary_file_id": null,
  "file_selection": {},
  "trace_id": "req_abc123",
  "options": {}
}
```

## 3. Authorization 与用户身份契约

### 3.1 什么时候必须带 Authorization

| 场景 | 是否必须带 `Authorization` |
| --- | --- |
| ephemeral ask | 否 |
| durable ask | 是 |
| `GET /api/health?durable=true` | 是 |
| 普通 `GET /api/health` | 否 |

### 3.2 Header 形状

必须符合：

```http
Authorization: Bearer <token>
```

否则返回：

- `401`
- `TOKEN_INVALID`

### 3.3 Token 解码与 user_id 提取

当前 `patent` 会用本地配置的：

- `JWT_SECRET`
- `JWT_EXPIRE_SECONDS`
- `JWT_COMPATIBLE_ACCESS_SALTS`

尝试解码 token。  
兼容 salt 集合里固定包含：

- `highthinking.auth.access`

解码成功后，按以下顺序取 user id：

1. `user_id`
2. `uid`
3. `sub`

都必须可转为正整数。

## 4. PatentQA -> Public-Service Authority 模型

### 4.1 本节描述的是 `patent` 已实现的 outbound model，不代表 `public-service` 当前已放行

当前 `patent` 本地已实现以下 authority request model：

- `source_service = patentQA`
- `requested_mode = patent`
- `actual_mode = patent`

但当前 `public-service` 还没有放行这组值，所以这部分属于：

- `patent/` 本地已实现 contract
- rollout 后才能真正生效的外部 contract

### 4.2 Authority headers

| Header | 值 |
| --- | --- |
| `X-Internal-Service-Name` | `patentQA` |
| `X-Internal-Service-Token` | `PATENT_AUTHORITY_INTERNAL_TOKEN` |
| `X-Trace-Id` | 当前请求 `trace_id` |

关键 rollout 约束：

- `PATENT_AUTHORITY_INTERNAL_TOKEN` 必须与 `public-service` 侧 `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` 一致
- 否则 authority 调用会直接 `401`

### 4.3 `AuthorityUserWriteRequest`

接口：

- `POST /internal/conversations/{conversation_id}/messages/user`

字段：

| 字段 | 类型 | 是否必须 | 当前固定值 / 规则 |
| --- | --- | --- | --- |
| `conversation_id` | `int` | 是 | `> 0` |
| `user_id` | `int` | 是 | `> 0` |
| `trace_id` | `string` | 是 | 与 ask trace 一致 |
| `source_service` | `"patentQA"` | 是 | 固定 |
| `route` | `"kb_qa"` | 是 | 固定 |
| `requested_mode` | `"patent"` | 是 | 固定 |
| `actual_mode` | `"patent"` | 是 | 固定 |
| `idempotency_key` | `string` | 是 | `{conversation_id}:{trace_id}:user` |
| `message.role` | `"user"` | 是 | 固定 |
| `message.content` | `string` | 是 | 原始 question |
| `context_hints.selected_file_ids` | `array<int>` | 否 | 当前通常为 `[]` |
| `context_hints.last_turn_route_hint` | `string|null` | 否 | 当前为 `kb_qa` |

样例：

```json
{
  "conversation_id": 123,
  "user_id": 456,
  "trace_id": "req_abc123",
  "source_service": "patentQA",
  "route": "kb_qa",
  "requested_mode": "patent",
  "actual_mode": "patent",
  "idempotency_key": "123:req_abc123:user",
  "message": {
    "role": "user",
    "content": "请总结这个专利主题"
  },
  "context_hints": {
    "selected_file_ids": [],
    "last_turn_route_hint": "kb_qa"
  }
}
```

### 4.4 `AuthorityContextSnapshotQuery`

接口：

- `GET /internal/conversations/{conversation_id}/context-snapshot`

query 字段：

| 字段 | 类型 | 是否必须 | 当前固定值 / 规则 |
| --- | --- | --- | --- |
| `user_id` | `int` | 是 | `> 0` |
| `trace_id` | `string` | 是 | 与 ask trace 一致 |
| `source_service` | `"patentQA"` | 是 | 固定 |
| `route` | `"kb_qa"` | 是 | 固定 |
| `requested_mode` | `"patent"` | 是 | 固定 |
| `actual_mode` | `"patent"` | 是 | 固定 |

### 4.5 `AuthorityContextSnapshotResponse`

`patent` 当前依赖的响应字段：

| 字段 | 类型 | 是否必须 | 当前用途 |
| --- | --- | --- | --- |
| `conversation_id` | `int` | 是 | 一致性检查 |
| `user_id` | `int` | 是 | 一致性检查 |
| `snapshot_version` | `int` | 是 | 诊断 |
| `updated_at` | `string` | 是 | 诊断 |
| `summary` | `object` | 是 | 上下文拼装 |
| `recent_turns` | `array<object>` | 是 | 上下文拼装 |
| `conversation_state` | `object` | 是 | overlay 收敛判断 |

### 4.6 `AuthorityAssistantAsyncRequest`

接口：

- `POST /internal/conversations/{conversation_id}/messages/assistant-async`

字段：

| 字段 | 类型 | 是否必须 | 当前固定值 / 规则 |
| --- | --- | --- | --- |
| `conversation_id` | `int` | 是 | `> 0` |
| `user_id` | `int` | 是 | `> 0` |
| `trace_id` | `string` | 是 | 与 ask trace 一致 |
| `source_service` | `"patentQA"` | 是 | 固定 |
| `route` | `"kb_qa"` | 是 | 固定 |
| `requested_mode` | `"patent"` | 是 | 固定 |
| `actual_mode` | `"patent"` | 是 | 固定 |
| `idempotency_key` | `string` | 是 | `{conversation_id}:{trace_id}:assistant` |
| `final_event.done_seen` | `true` | 是 | 固定 |
| `final_event.answer_text` | `string` | 是 | 最终答案 |
| `final_event.steps` | `array<object>` | 否 | 来自执行结果 |
| `final_event.references` | `array<object>` | 否 | 来自执行结果 |
| `final_event.used_files` | `array<object>` | 否 | 当前通常为 `[]` |
| `final_event.timings` | `object` | 否 | 来自执行结果 |

样例：

```json
{
  "conversation_id": 123,
  "user_id": 456,
  "trace_id": "req_abc123",
  "source_service": "patentQA",
  "route": "kb_qa",
  "requested_mode": "patent",
  "actual_mode": "patent",
  "idempotency_key": "123:req_abc123:assistant",
  "final_event": {
    "done_seen": true,
    "answer_text": "Patent Phase 1 stub answer: 请总结这个专利主题",
    "steps": [
      {
        "step": "patent_stub",
        "title": "Patent Stub",
        "message": "Patent Phase 1 stub execution completed.",
        "status": "success"
      }
    ],
    "references": [],
    "used_files": [],
    "timings": {
      "stub_total_ms": 1
    }
  }
}
```

## 5. Patent 当前上下文与持久化运行时模型

### 5.1 `prepare_turn()` 输出语义

当前 `ChatPersistenceService.prepare_turn()` 会返回一个内部 prepared turn，至少可能携带：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `trace_id` | `string` | 本轮 trace id |
| `context` | `object` | 执行上下文 |
| `assistant_accept` | `object|null` | authority accept 返回值 |
| `assistant_accept_required` | `bool` | 是否必须在 `done` 前完成 accept |
| `assistant_accept_skipped` | `bool` | 是否命中已缓存 turn 结果 |
| `_state` | `object` | durable 运行时协调状态 |

### 5.2 `context` 当前字段

当前上下文对象至少包含：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `persistence_mode` | `durable|ephemeral` | 当前模式 |
| `conversation_id` | `int|null` | 会话 id |
| `trace_id` | `string` | 本轮 trace |
| `chat_history` | `array<object>` | 归一化后的上下文历史 |
| `summary` | `object` | authority summary |
| `conversation_state` | `object` | authority state |
| `snapshot` | `object|null` | authority snapshot 原始体 |
| `pending_overlay` | `object|null` | 命中的 assistant overlay |

## 6. Sync response 契约

### 6.1 成功响应 envelope

| 字段 | 类型 | 是否必须 |
| --- | --- | --- |
| `success` | `true` | 是 |
| `data` | `object` | 是 |
| `trace_id` | `string` | 是 |

### 6.2 `data` 字段

| 字段 | 类型 | 是否必须 | 当前实现说明 |
| --- | --- | --- | --- |
| `final_answer` | `string` | 是 | 当前 stub answer |
| `timings` | `object` | 是 | 当前至少含 `stub_total_ms` |
| `metadata` | `object` | 是 | 模式与 route 元数据 |
| `references` | `array<object>` | 是 | 当前为空 |
| `pdf_links` | `array<string>` | 是 | 当前为空 |
| `reference_links` | `array<string>` | 是 | 当前为空 |
| `trace_id` | `string` | 是 | 与顶层重复 |

### 6.3 `metadata` 字段

| 字段 | 类型 | 是否必须 | 当前值 |
| --- | --- | --- | --- |
| `requested_mode` | `"patent"` | 是 | 固定 |
| `actual_mode` | `"patent"` | 是 | 固定 |
| `route` | `"kb_qa"` | 是 | 固定 |
| `mode` | `"patent"` | 是 | 固定 |
| `query_mode` | `"patent"` | 是 | 固定 |
| `conversation_id` | `int|null` | 是 | durable 时为正整数，ephemeral 时显式为 `null` |

### 6.4 成功响应样例

```json
{
  "success": true,
  "data": {
    "final_answer": "Patent Phase 1 stub answer: 请总结这个专利主题",
    "timings": {
      "stub_total_ms": 1
    },
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
    "trace_id": "req_abc123"
  },
  "trace_id": "req_abc123"
}
```

## 7. SSE 契约

### 7.1 通用规则

- 每帧都是标准 SSE：

```text
data: {json}

```

- 每个事件都带：
  - `seq`
  - `ts`

### 7.2 `metadata` event

| 字段 | 类型 | 是否必须 | 当前值 |
| --- | --- | --- | --- |
| `type` | `"metadata"` | 是 | 固定 |
| `requested_mode` | `"patent"` | 是 | 固定 |
| `actual_mode` | `"patent"` | 是 | 固定 |
| `route` | `"kb_qa"` | 是 | 固定 |
| `query_mode` | `"patent"` | 是 | 固定 |
| `trace_id` | `string` | 是 | 当前请求 trace |
| `seq` | `int` | 是 | 从 0 开始 |
| `ts` | `string` | 是 | UTC ISO8601 |

### 7.3 `step` event

注意当前 stream 中的 `step` event 只暴露：

| 字段 | 类型 | 是否必须 |
| --- | --- | --- |
| `type` | `"step"` | 是 |
| `title` | `string|null` | 否 |
| `message` | `string|null` | 否 |
| `seq` | `int` | 是 |
| `ts` | `string` | 是 |

执行结果里虽然可能有 `step` / `status` 字段，但当前不会透传到 `step event`。

### 7.4 `content` event

| 字段 | 类型 | 是否必须 |
| --- | --- | --- |
| `type` | `"content"` | 是 |
| `content` | `string` | 是 |
| `seq` | `int` | 是 |
| `ts` | `string` | 是 |

### 7.5 `done` event

| 字段 | 类型 | 是否必须 | 当前实现说明 |
| --- | --- | --- | --- |
| `type` | `"done"` | 是 | 固定 |
| `final_answer` | `string` | 是 | 最终答案 |
| `timings` | `object` | 是 | 执行 timings |
| `references` | `array<object>` | 是 | 当前通常为空 |
| `trace_id` | `string` | 是 | 当前请求 trace |
| `used_files` | `array<object>` | 是 | 当前通常为空 |
| `reference_links` | `array<string>` | 是 | 当前通常为空 |
| `pdf_links` | `array<string>` | 是 | 当前通常为空 |
| `file_selection` | `object` | 是 | 当前会回填 request `file_selection` |
| `seq` | `int` | 是 | 单调递增 |
| `ts` | `string` | 是 | UTC ISO8601 |

### 7.6 `error` event

| 字段 | 类型 | 是否必须 |
| --- | --- | --- |
| `type` | `"error"` | 是 |
| `code` | `string` | 是 |
| `error` | `string` | 是 |
| `message` | `string` | 是 |
| `trace_id` | `string` | 是 |
| `seq` | `int` | 是 |
| `ts` | `string` | 是 |

### 7.7 当前未启用的 `heartbeat`

虽然 schema 中已经声明了：

- `type=heartbeat`

但当前 ask streaming 实现还没有主动发送 heartbeat。  
后续如果要加，需要单独更新本契约。

## 8. Health / Durable Probe 契约

### 8.1 Endpoint

当前只实现：

| 方法 | 路径 |
| --- | --- |
| `GET` | `/api/health` |
| `GET` | `/api/v1/health` |

### 8.2 成功响应字段

| 字段 | 类型 | 是否必须 | 说明 |
| --- | --- | --- | --- |
| `success` | `true` | 是 | 固定 |
| `service` | `string` | 是 | 当前为 `patent` |
| `status` | `ok|degraded` | 是 | 组件状态汇总 |
| `durable_mode_enabled` | `bool` | 是 | 来自配置 |
| `durable_requested` | `bool` | 是 | 查询参数结果 |
| `components` | `object` | 是 | 至少含 `runtime`/`redis`/`authority` |

### 8.3 `components.runtime`

当前 runtime payload 只明确包含：

- `ready`
- `stream_slots_capacity`
- `stream_slots_available`
- `ask_executor_max_workers`

### 8.4 `components.redis`

至少包含：

- `ready`
- `enabled`
- `detail`
- `error`
- `url`
- `key_prefix`

其中 `url` 会做密码打码。

### 8.5 `components.authority`

至少包含：

- `ready`
- `enabled`
- `base_url`
- `token_configured`

### 8.6 `?durable=true` 失败响应

如果 durable 模式未启用或依赖未 ready，会返回 `503` 错误 envelope，并在 `extra` 中附带：

- `status=degraded`
- `components`
- `durable_requested=true`

## 9. Redis key 与协调契约

### 9.1 Key 命名规则

所有 key 都以以下结构拼接：

```text
{PATENT_REDIS_KEY_PREFIX}:{PATENT_ENV}:...
```

默认前缀：

- `patent`

默认环境：

- `dev`

### 9.2 当前 key 一览

| 逻辑用途 | key 形状 |
| --- | --- |
| conversation lock | `{prefix}:{env}:exec:conversation-lock:{conversation_id}` |
| turn identity | `{prefix}:{env}:exec:turn:{conversation_id}:{trace_id}` |
| execution cache | `{prefix}:{env}:exec:cache:{normalized_request_key}` |
| retrieval cache | `{prefix}:{env}:retrieval:cache:{normalized_query_key}` |
| inflight | `{prefix}:{env}:coord:inflight:{conversation_id}:{trace_id}` |
| pending turn | `{prefix}:{env}:coord:pending-turn:{conversation_id}` |
| assistant overlay | `{prefix}:{env}:overlay:assistant:{user_id}:{conversation_id}` |

### 9.3 当前协调语义

| 机制 | 作用 |
| --- | --- |
| conversation lock | 同一会话单活执行 |
| inflight marker | 标记该 trace 正在执行 |
| pending turn | 标记该 conversation 当前已有待完成 trace |
| turn result cache | 同 trace 重试复用 |
| assistant overlay | authority 未收敛前的读己之写补偿 |

## 10. 错误码与状态码矩阵

### 10.1 HTTP error

| code | 常见状态码 | 当前触发场景 |
| --- | --- | --- |
| `TOKEN_MISSING` | `401` | durable ask / durable health 缺少 Authorization |
| `TOKEN_INVALID` | `401` | bearer header/token/user_id 不合法 |
| `INVALID_REQUEST` | `400` | JSON 非法、字段类型错误、必填字段为空 |
| `PROTOCOL_MISMATCH` | `400` | 不是 patent Phase 1 合法请求 |
| `AUTHORITY_UNAVAILABLE` | `503` | authority 写 / 读 / accept 失败，或 public-service 尚未放行 patent contract |
| `PATENT_BUSY` | `409` / `429` | 会话已在执行中，或 stream 并发槽位打满 |
| `DURABLE_MODE_DISABLED` | `503` | durable feature gate 未开 |
| `SERVICE_NOT_READY` | `503` | Redis / authority / runtime 依赖不 ready，或续租失败 |
| `INTERNAL_ERROR` | `500` | 未捕获内部错误 |

### 10.2 SSE terminal error

stream ask 出错时，最终会产出一个：

- `type=error`

其 `code/error/message` 与 HTTP APIError 对齐。

## 11. 运行配置契约

### 11.1 HTTP / Gunicorn

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `PATENT_HOST` | `0.0.0.0` | bind host |
| `PATENT_PORT` | `8787` | bind port |
| `PATENT_GUNICORN_WORKERS` | `1` | worker 数 |
| `PATENT_GUNICORN_THREADS` | `8` | thread 数 |
| `PATENT_GUNICORN_TIMEOUT` | `120` | 请求超时 |
| `PATENT_GUNICORN_KEEPALIVE` | `15` | keepalive |
| `PATENT_GUNICORN_MAX_REQUESTS` | `1000` | worker 重启阈值 |
| `PATENT_GUNICORN_MAX_REQUESTS_JITTER` | `100` | worker 重启抖动 |
| `PATENT_GUNICORN_WORKER_CLASS` | `uvicorn.workers.UvicornWorker` | worker class |

### 11.2 Runtime

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `PATENT_ASK_STREAM_MAX_CONCURRENT` | `8` | stream ask 并发槽位 |
| `PATENT_ASK_EXECUTOR_MAX_WORKERS` | `4` | 执行器 worker 上限 |

### 11.3 Redis

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `PATENT_REDIS_ENABLED` | `false` | 是否启用 Redis |
| `PATENT_REDIS_URL` | `redis://localhost:6379/0` | Redis 地址 |
| `PATENT_REDIS_KEY_PREFIX` | `patent` | key 前缀 |
| `PATENT_REDIS_SOCKET_CONNECT_TIMEOUT_SEC` | `1.5` | 连接超时 |
| `PATENT_REDIS_SOCKET_TIMEOUT_SEC` | `1.5` | IO 超时 |

### 11.4 Authority

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `PATENT_DURABLE_MODE_ENABLED` | `false` | 是否允许 durable ask |
| `PATENT_AUTHORITY_BASE_URL` | `http://public-service` | authority base url |
| `PATENT_AUTHORITY_TIMEOUT_SECONDS` | `10.0` | authority 超时 |
| `PATENT_AUTHORITY_INTERNAL_TOKEN` | `""` | internal token，必须与 `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` 对齐 |
| `PATENT_DURABLE_AUTHORITY_ENABLED` | `false` | 是否初始化 authority durable client |

## 12. 兼容转发到 fastQA 的专利文件 turn 约束

因为当前 `fastQA` ingress 只接受：

- `requested_mode=fast`
- `actual_mode=fast`

所以当 gateway 想把专利文件 / 混合问题兼容转给 `fastQA` 时，仍需要重写为：

| 原始判定 | 转发到 `fastQA` 的兼容 payload |
| --- | --- |
| `requested_mode=patent`, `turn_mode=file_only` | `requested_mode=fast`, `actual_mode=fast` |
| `requested_mode=patent`, `turn_mode=mixed` | `requested_mode=fast`, `actual_mode=fast` |

在这个 rewrite 真正落地前，不能把专利文件 / 混合 turn 说成“当前已经可用地走 fastQA”。

## 13. 后续扩展时必须保留的稳定契约

后续开发者继续做专利系统时，这些契约默认不能改：

- `public-service` 继续做 durable transcript owner
- `patent` durable 成功以 authority accept 成功为准
- `conversation_id` 非法时按 ephemeral 处理，而不是强行 durable
- Redis 继续做协调层，不做 canonical transcript store
- 文件 / 混合专利 turn 在正式接管前仍归 `fastQA`，但 rewrite 落地前不能宣称链路已可用
