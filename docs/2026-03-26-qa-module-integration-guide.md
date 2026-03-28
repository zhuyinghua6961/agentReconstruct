# QA 模块接入基座指南

## 文档状态

- 最后更新：2026-03-26
- 目标：定义后续任何新 QA 服务如何接入现有基座
- 适用范围：后续专利 QA，以及未来其他独立 QA 服务
- 平台目标：后续新 QA 的 durable 聊天统一归 `public-service`，不再为新接入链路使用 `gateway` 旧的 direct persistence

这份文档不是某个具体 QA 的内部设计文档，而是“接入现有平台”的统一规范。  
后续有人实现新的 QA 服务时，应先看这份文档，再做该 QA 自己的检索、Prompt、排序、引用等业务设计。

为了避免把“平台目标”误写成“当前现状”，本文明确区分三种状态：

- 当前代码现状：仓库今天已经如此实现
- 新接入强约束：后续新 QA 必须遵守的接入标准
- rollout gate：在某个具体新 QA 真正上线 durable 之前，平台其他服务还必须补齐的改动

## 1. 基座总原则

### 1.1 基座三段式拓扑

新 QA 服务接入当前系统时，必须遵守这个总拓扑：

```text
Frontend
  -> gateway
    -> QA service
      -> public-service
```

三者分工必须稳定：

- `gateway`
  - 统一对前端暴露 ask 接口
  - 做 mode 路由、文件上下文判定、代理转发
  - 透传 `Authorization` 和 trace headers
- `QA service`
  - 执行该模式自己的问答逻辑
  - 负责协议校验、上下文装配、运行时控制、SSE 输出
  - durable 模式下调用 `public-service` authority API
- `public-service`
  - 持有聊天真相源
  - 输出 context snapshot
  - 接受 assistant async final event
  - 最终落库物化 transcript

### 1.2 持久化必须 authority-first

对后续任何新 QA，只要涉及 durable 聊天，都必须采用：

- `QA -> public-service /internal/.../messages/user`
- `QA -> public-service /internal/.../context-snapshot`
- `QA -> public-service /internal/.../messages/assistant-async`

不允许再采用以下方式作为新链路默认方案：

- `gateway` 直接替 QA 往 `public-service` append user/assistant message
- QA 自己维护 canonical transcript
- QA 自己把 durable transcript 放在本地 JSON、Redis、磁盘里作为真相源

这里要强调一件事：

- 这是新接入标准，不是说当前仓库里所有历史路径都已经完成了清理
- `gateway` 旧的 direct persistence 代码仍然存在于仓库中，但后续新 QA 不得再接这条旧路径

### 1.3 Redis 是协调层，不是真相源

新 QA 可以用 Redis，但只能承担这些职责：

- 并发锁
- inflight 标记
- 幂等去重
- overlay
- execution cache
- retrieval cache

不能把 Redis 当成 durable transcript owner。

## 2. 当前代码现状与平台目标的差异

## 2.1 当前 `public-service` authority 还不是“对任意新 QA 自动开放”

这是目前最容易被误解的地方。

平台目标是：

- 新 QA durable persistence 统一走 `public-service` authority

但当前代码现状是：

- `public-service` authority schema 仍只接受已有的特定 `source_service` 和 mode 值
- internal allowlist 也只放行已有 QA
- service 层对 `source_service` / mode 也有现状校验

所以结论必须写清楚：

- authority-first 是后续新 QA 的强约束
- 但某个具体新 QA 要真的跑通 durable，必须先扩 `public-service`
- 没完成 allowlist/schema/service 放行之前，durable 只是目标态，不是当前可用现状

## 2.2 替换已有 mode，不等于自动接管该 mode 的所有 turn

如果后续是“替换已有 mode 的实现”，要先确认当前 `gateway` 对这个 mode 的实际 ownership。

例如当前 `patent` 场景：

- `kb_only` patent turn 才有机会发往 `patent`
- `file_only` / `mixed` patent turn 目前在 route decision 上仍会被改成 `actual_mode=fast`

所以：

- 替换 `PATENT_BACKEND_BASE_URL` 只会影响当前真正落到 `patent` backend 的那部分流量
- 不会自动接管文件 / 混合 turn
- 如果想接管更多 turn，必须连同 `gateway` 的 route ownership 一起改

## 3. 新 QA 服务必须具备的能力

一个可接入的 QA 模块，最少要具备以下能力：

### 3.1 HTTP 路由面

最少应提供：

- sync ask
- stream ask
- health

如果该服务要挂在当前 `gateway` 后面，那么以下路径是硬要求：

- `POST /api/{mode}/ask`
- `POST /api/v1/{mode}/ask`
- `POST /api/{mode}/ask_stream`
- `POST /api/v1/{mode}/ask_stream`

原因很简单：

- `gateway` 当前固定调用上游 `/api/{actual_mode}/ask`
- `gateway` 当前固定调用上游 `/api/{actual_mode}/ask_stream`

以下路径属于推荐兼容别名，不是挂在 gateway 后面的必需项：

- `POST /api/ask`
- `POST /api/v1/ask`
- `POST /api/ask_stream`
- `POST /api/v1/ask_stream`

health 方面至少应提供：

- `GET /api/health`
- `GET /api/v1/health`

其中 `{mode}` 应与 gateway 中实际注册的 mode 名称一致。

### 3.2 两种持久化模式

每个新 QA 都应该天然支持：

- `durable`
  - 有合法 `conversation_id`
  - 可做 user write / snapshot read / assistant accept
- `ephemeral`
  - 无合法 `conversation_id`
  - 不做 authority side effect

推荐判定方式：

- `conversation_id` 能归一化成正整数：durable
- 否则：ephemeral

### 3.3 标准错误面

新 QA 应提供：

- HTTP JSON 错误 envelope
- SSE terminal error envelope
- 可区分的错误码
- retriable / non-retriable 语义

至少要能区分：

- 鉴权失败
- 请求格式错误
- 协议不匹配
- authority 不可用
- runtime 不 ready
- 并发冲突 / 忙碌
- 内部错误

### 3.4 多实例运行能力

新 QA 如果要进入生产，必须支持多实例部署下的一致性要求：

- 同一 conversation 不双活执行
- 同一 trace 幂等
- assistant async accept 前不宣称 durable success
- authority snapshot 未收敛时，后续 ask 仍可读到上一轮 assistant

## 4. Gateway 接入流程

## 4.1 先判断你是在“新增 mode”还是“替换已有 mode”

这是第一步，否则很容易改错范围。

### 情况 A：替换已有 mode 的实现

例如：

- 后续专利 QA 仍然使用 `mode=patent`
- 只是把当前 `patent` stub 替换成真正的专利系统

这种情况下：

- `gateway` 里的 mode 名称不用新增
- 主要是替换对应 backend URL、保持契约一致、补 rollout gate
- 但仍要核对当前这个 mode 在 `gateway` 下到底拥有哪些 turn
- 如果文件 / 混合 turn 当前仍被改路由到其他 backend，单纯替换 base URL 不会接管它们

### 情况 B：新增全新 mode

例如新增一个以前不存在的 `mode=newqa`

这种情况下必须同时修改 `gateway` 的硬编码注册层。

## 4.2 新增 mode 时必须改的 gateway 文件

当前 `gateway` 的 mode/role 是硬编码的，不是动态发现的。  
新增 mode 时，至少要看这些文件：

- `gateway/app/core/config.py`
- `gateway/app/services/backend_registry.py`
- `gateway/app/services/route_table.py`
- `gateway/app/models/ask.py`
- `gateway/app/models/routing.py`
- `gateway/app/routers/qa.py`
- `gateway/app/services/route_decision.py`

### 4.2.1 配置入口

在 `gateway/app/core/config.py` 中：

- 为新 QA 增加 backend base URL 配置
- 约定新的环境变量，比如：
  - `NEWQA_BACKEND_BASE_URL`

### 4.2.2 Backend registry

在 `gateway/app/services/backend_registry.py` 中：

- 扩展 `BackendRole`
- 注册新 backend target
- 更新 `get_mode_backend()` 的允许集合

### 4.2.3 路由表

在 `gateway/app/services/route_table.py` 中：

- 把新 mode 纳入 `_mode_paths("ask")`
- 把新 mode 纳入 `_mode_paths("ask_stream")`

### 4.2.4 请求模型

在 `gateway/app/models/ask.py` 中：

- 扩展 `requested_mode` literal

### 4.2.5 显式路由入口

在 `gateway/app/routers/qa.py` 中：

- 增加显式的 `/api/{mode}/ask` handler
- 增加显式的 `/api/{mode}/ask_stream` handler

### 4.2.6 路由决策

在 `gateway/app/services/route_decision.py` 中：

- 明确这个 mode 的 `requested_mode -> actual_mode` 策略
- 明确 `kb_only / file_only / mixed` 时该由谁执行
- 明确 route、source_scope、file_selection 等生成规则

## 4.3 新 QA 不应在服务内重新做路由决策

`gateway` 是 mode 和 turn 路由的 owner。  
新 QA 服务收到请求后应该：

- 校验 payload 是否符合自己支持的 contract
- 不要重新解释 `requested_mode`
- 不要自己再做 `kb_only / file_only / mixed` 路由切换
- 不要自行改写 `actual_mode`

如果 payload 不符合约定，直接返回协议错误。

## 5. Gateway -> QA 请求契约

## 5.1 当前 gateway 规范化 ask payload 的稳定字段

当前 `gateway/app/routers/qa.py` 转发给上游 QA 的 payload 至少包含：

- `question`
- `conversation_id`
- `chat_history`
- `requested_mode`
- `actual_mode`
- `route`
- `source_scope`
- `turn_mode`
- `kb_enabled`
- `allow_kb_verification`
- `used_files`
- `execution_files`
- `selected_file_ids`
- `primary_file_id`
- `file_selection`
- `trace_id`
- `options`

后续新 QA 应当围绕这个规范化 payload 建立自己的 ingress contract，而不是重新发明另一套完全不兼容字段集。

## 5.2 新 QA 需要明确定义自己支持哪一类 turn

每个新 QA 都必须明确回答下面这些问题：

- 只支持 `kb_only`，还是也支持 `file_only` / `mixed`
- 支持哪些 `route`
- 是否允许 `used_files` 非空
- 是否允许 `execution_files` 非空
- 是否允许 `selected_file_ids` 非空
- 是否允许 `allow_kb_verification=true`

这些约束必须在 QA 服务入口做显式校验。

## 5.3 推荐做法

新 QA 在 Phase 1 接入时，推荐先只支持一个最窄 contract：

- 只支持 `kb_only`
- 只支持单一 `route`
- 不支持文件 payload
- 不支持额外 verification 变种

先把 authority-first durable 流程、SSE、健康检查、多实例一致性打稳，再扩充能力。

## 6. QA -> Frontend 响应契约

## 6.1 Sync 响应

建议所有新 QA 统一使用 wrapped sync shape：

```json
{
  "success": true,
  "data": {
    "final_answer": "...",
    "timings": {},
    "metadata": {
      "requested_mode": "...",
      "actual_mode": "...",
      "route": "...",
      "mode": "...",
      "query_mode": "...",
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

这样做的目的：

- 不为每个 QA 再造一套不同 sync shape
- 让 gateway 和前端更容易统一消费
- 为后续 summary、引用、文件链接等兼容字段预留稳定位置

## 6.2 SSE 事件集

推荐所有新 QA 至少支持以下 SSE event：

- `metadata`
- `step`
- `content`
- `done`
- `error`

如果需要可以再加：

- `heartbeat`

但不建议先加太多个性化事件类型，否则下游消费会碎片化。

## 6.3 SSE 通用要求

每个 event 都应尽量带：

- `type`
- `trace_id`
- `seq`
- `ts`

其中：

- `seq` 用于保证顺序可见
- `ts` 用于排障与时序核对
- `trace_id` 用于跨 `gateway / QA / public-service` 联查

## 6.4 durable 成功语义

对于 durable ask，必须满足以下规则：

- sync：assistant async accept 成功后，才允许返回成功
- stream：assistant async accept 成功后，才允许发 `done`

如果 assistant accept 失败：

- sync 直接失败
- stream 发 terminal `error`

不能把“模型已经回答出来了”当成 durable 成功。

## 7. 聊天持久化接入流程

## 7.1 统一 authority 时序

新 QA 服务的 durable ask 必须采用这个时序：

```text
1. 校验请求
2. 校验 / 推导 user_id
3. user write
4. context snapshot read
5. 执行 QA
6. assistant async accept
7. 返回 sync success 或 SSE done
```

不能把 assistant 持久化放成“异步后台可选补偿”。

## 7.2 User write

接口：

- `POST /internal/conversations/{conversation_id}/messages/user`

作用：

- 把当前 user turn 写入 authority transcript
- 让后续 snapshot 可以基于这个 user turn 组织上下文

## 7.3 Context snapshot read

接口：

- `GET /internal/conversations/{conversation_id}/context-snapshot`

作用：

- 读取 authority 视角下的近期聊天
- 读取 summary
- 读取 conversation_state
- 为本轮 QA 组装上下文

## 7.4 Assistant async accept

接口：

- `POST /internal/conversations/{conversation_id}/messages/assistant-async`

作用：

- 把最终 assistant turn 提交给 `public-service`
- 由其 inbox/worker 进行最终物化
- 保持 QA 服务无状态，不自己持有 durable transcript

## 7.5 为什么不允许新接入继续使用 gateway 旧持久化路径

因为这会引入长期问题：

- 持久化 owner 错位
- QA 运行结果与持久化契约脱节
- 后续 authority snapshot 难以和执行链路严格对齐
- 不同 QA 的持久化策略继续分叉

所以从新 QA 开始，必须统一收敛到 `public-service` authority。  
再次强调：

- 这里说的是新接入规范
- 不是说仓库中 legacy gateway persistence 代码已经物理删除

## 8. Public-Service 接入要求

## 8.1 允许列表必须显式扩展

当前 `public-service` internal authority API 并不是自动接受任意新 QA 的。  
接一个新 QA 时，必须至少扩以下内容：

- authority schema 中的 `source_service`
- authority schema 中的 `requested_mode`
- authority schema 中的 `actual_mode`
- internal API policy allowlist
- service 层对新 source/mode 的校验逻辑

这里要注意当前代码里这套校验通常不只存在一层。实际 rollout 时，至少要同步检查：

- internal API 层的 allowlist / 校验
- `ConversationService` 层的 source/mode 校验

否则很容易出现 schema 放开了、入口放开了，但 service 层仍然拒绝的半开状态。

## 8.2 internal token 必须对齐

新 QA 的 internal token 配置必须与 `public-service` 对齐。

举例：

- QA 侧：`<QA>_AUTHORITY_INTERNAL_TOKEN`
- public-service 侧：`PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`

如果两边值不一致：

- 所有 authority 调用都会 `401`

## 8.3 service name / mode naming 是硬兼容要求

一旦决定某个 QA 的：

- `source_service`
- `requested_mode`
- `actual_mode`

这些值就不是“推荐命名”，而是 authority 边界上的硬兼容要求：

- `X-Internal-Service-Name` 必须等于 `source_service`
- `source_service` 必须被 `public-service` allowlist 接受
- `requested_mode` / `actual_mode` 必须落在 `public-service` 当前允许集合内
- 三者在 QA 与 `public-service` 之间必须完全一致

## 8.4 idempotency key 是硬要求

authority API 当前要求 idempotency key 必须精确匹配：

- user write：`{conversation_id}:{trace_id}:user`
- assistant accept：`{conversation_id}:{trace_id}:assistant`

这不是推荐格式，而是当前 authority API 的硬校验要求。

## 9. Redis 与多实例一致性基线

## 9.1 至少要有 conversation 级单活锁

新 QA 进入多实例部署前，至少要保证：

- 同一个 `conversation_id`
- 同一时间只有一个 durable turn 真正执行

否则很容易出现：

- user/assistant 顺序错乱
- 双写 authority
- overlay 乱序
- 缓存污染

## 9.2 至少要有 trace 级幂等

推荐 key 维度：

- `conversation_id + trace_id`

最少要能做到：

- 同 trace 重试不会重复执行副作用
- 同 trace 重放时可以复用已完成结果，或者至少安全拒绝

## 9.3 Overlay 是强烈推荐项

如果 durable transcript 由 `public-service` 最终物化，那么新 QA 最好实现 assistant overlay，用来覆盖这段窗口：

- assistant async accept 已成功
- 但 authority snapshot 还没收敛

否则多轮对话中，用户很快连续追问时，会出现“上一轮 assistant 明明成功了，下一轮上下文却读不到”的体验问题。

## 9.4 retrieval cache 要和 ask runtime key 模型统一

后续新 QA 引入 Redis 检索缓存时，建议直接纳入同一套 key factory 中管理：

- execution cache
- retrieval cache
- overlay
- inflight / lock

不要把检索缓存独立搞成另一套不可观测的 Redis namespace。

## 10. Health / Readiness 规范

## 10.1 必须提供 health

每个新 QA 至少提供：

- `GET /api/health`
- `GET /api/v1/health`

## 10.2 不要把普通 health 简化理解成“纯 liveness”

当前平台更实用的做法是：

- 普通 `/api/health` 也可以返回 readiness / degraded 语义
- 如果关键依赖未 ready，可以直接返回 `503`
- 在类似当前 `patent` 的实现中，即使没有 `?durable=true`，普通 `/api/health` 也可能因为 durable 关键依赖未 ready 而返回 `503`

也就是说：

- health 不一定只是“进程活着”
- 完全可以包含对 runtime / Redis / authority 的就绪判断

## 10.3 建议额外提供 durable probe

推荐做法是支持：

- `GET /api/health?durable=true`

用于检查 durable 链路是否真的 ready。

对这个 probe，至少要检查：

- auth 依赖是否可用
- runtime 是否 ready
- Redis 是否 ready
- authority client 是否 ready

如果采用类似当前 `patent` 的实现风格，还要明确：

- `?durable=true` 可能要求 browser auth
- 因此鉴权失败可能返回 `401`
- durable 依赖未 ready 时返回 `503`

### 10.4 health 返回中建议暴露的 components

建议至少包含：

- `runtime`
- `redis`
- `authority`

这样上线时可以快速判断是：

- 服务没起来
- Redis 不通
- authority token/base_url 没配好
- 并发 runtime 没初始化好

## 11. 配置规范

后续新 QA 至少应有以下几类配置：

- HTTP / Gunicorn
- runtime 并发
- Redis
- authority
- auth

建议命名约定统一采用服务前缀，比如：

- `NEWQA_HOST`
- `NEWQA_PORT`
- `NEWQA_GUNICORN_WORKERS`
- `NEWQA_REDIS_ENABLED`
- `NEWQA_REDIS_URL`
- `NEWQA_AUTHORITY_BASE_URL`
- `NEWQA_AUTHORITY_INTERNAL_TOKEN`
- `NEWQA_DURABLE_MODE_ENABLED`

如果该 QA 采用类似 `patent` 的 rollout 控制方式，建议把这两个 gate 分开：

- 用户侧 durable mode gate
  - 例如：`NEWQA_DURABLE_MODE_ENABLED`
- authority client / durable authority gate
  - 例如：`NEWQA_DURABLE_AUTHORITY_ENABLED`

原因是：

- 允许 durable 请求进入
- 和 authority client 是否真的初始化并 ready

是两个不同层级的开关，混成一个 flag 容易出错。

这样做的好处：

- 部署时一眼能知道配置归属
- 多 QA 并存时不会互相污染
- 运维可以按服务维度管理配置

## 12. 接入步骤清单

下面是一份推荐顺序，后续接新 QA 基本按这个流程做。

### 步骤 1：先确定 mode 和 ownership

必须先定清楚：

- 它是新 mode，还是替换现有 mode
- 它支持哪些 turn_mode
- 文件 / 混合 turn 是否仍归其他 QA
- 它的 `source_service` 和 mode canonical name 是什么

### 步骤 2：搭好 QA 服务自己的最小路由面

先做到：

- ask
- ask_stream
- health
- auth
- 错误面

### 步骤 3：在 gateway 注册 backend 和路由

如果是新增 mode，就按第 4 节把 gateway 硬编码入口扩全。

### 步骤 4：扩 `public-service` authority 接受面

把新 QA 对应的：

- `source_service`
- `requested_mode`
- `actual_mode`

先在 `public-service` 里放行，否则 durable 不可能真正打通。

### 步骤 5：接入 `public-service` authority

把 durable ask 路径完整接通：

- user write
- snapshot read
- assistant async accept

### 步骤 6：补 Redis runtime

至少补：

- conversation lock
- inflight marker
- trace 幂等
- overlay

### 步骤 7：做 contract tests

至少要覆盖：

- sync ask contract
- stream ask contract
- health contract
- authority client contract
- durable failure semantics
- multi-instance / lock / overlay 语义

### 步骤 8：再开 rollout gate

在 `gateway` 和 `public-service` 都补齐之前，不要把 durable 流量真正切过去。

## 13. 联调与验收清单

一个新 QA 要正式接入，至少应验证：

- `gateway` 能正确路由到该 QA
- request contract 与 QA ingress 一致
- auth 与 trace header 正确透传
- durable ask 能完成 user write
- durable ask 能读到 authority snapshot
- assistant async accept 成功
- stream 只有在 accept 成功后才发 `done`
- authority worker 最终只物化一条 assistant turn
- 下一轮 ask 在 snapshot 未收敛前能读到 overlay
- snapshot 收敛后 overlay 能退出
- 多实例下不会同 conversation 双活
- health 和 durable probe 都能准确反映状态

## 14. 禁止事项

后续任何新 QA 接入时，默认禁止以下做法：

- 让 `gateway` 继续作为新 QA durable transcript 的长期持久化 owner
- 在 QA 服务内维护 canonical transcript 真相源
- 把 Redis 当成 durable transcript store
- 不做 trace 级幂等就直接上线多实例
- assistant accept 失败后仍返回 success / done
- 在 QA 服务里偷偷重写 gateway 的 mode/route 决策
- 没有 health / durable probe 就直接接入基座

## 15. 专利 QA 的预留扩展位

后续专利 QA 接入时，可以直接复用这份指南的骨架，再单独补这几类专利专有设计：

- 专利检索与召回流程
- 专利 citation object
- 专利文件问答 ownership
- 专利检索缓存粒度与 TTL
- 专利领域特有 summary / conversation_state 扩展

换句话说：

- 这份文档解决“怎么接入基座”
- 专利设计文档解决“专利问答本身怎么做”

## 16. 当前代码参考

接入时建议优先阅读这些文件：

- `gateway/app/core/config.py`
- `gateway/app/services/backend_registry.py`
- `gateway/app/services/route_table.py`
- `gateway/app/models/ask.py`
- `gateway/app/routers/qa.py`
- `gateway/app/services/route_decision.py`
- `gateway/app/services/conversation_persistence.py`
- `public-service/backend/app/modules/conversation/internal_api.py`
- `public-service/backend/app/modules/conversation/authority_schemas.py`
- `public-service/backend/app/modules/conversation/service.py`
- `fastQA/app/services/conversation_authority_client.py`
- `highThinkingQA/server/services/conversation_authority_client.py`
- `patent/server/services/conversation_authority_client.py`
- `patent/server/services/chat_persistence.py`
- `patent/server/services/execution_cache.py`
- `patent/server/services/execution_lock.py`
- `patent/server_fastapi/routers/ask.py`
- `patent/server_fastapi/routers/health.py`

