# Patent Process-Local Upstream Pool Design

**Date:** 2026-04-14

## Summary

本设计定义 `patent` 服务内“进程内共享上游连接池”的第一阶段方案，目标是降低当前 `pdf_qa`、普通 QA stage1/stage4 对同一 LLM 上游重复打冷连接所带来的首字延迟。

本方案只解决“同一 worker 进程内、同一上游的连接复用”问题，不解决跨 worker 共享，不改变模型、prompt、业务路由，也不处理单篇 PDF summary 现有的本地缓冲逻辑。

shared pool 的资源归属以 `app` 级 bootstrap 为主，`patent_runtime` 只是消费者之一。这样可以在 `patent_runtime` 不可用时，继续保留纯文件路由的 degraded-mode 可用性。

---

## Scope

本设计覆盖：

1. `patent/server/patent/runtime.py` 中的 `PatentPlanningClient`
2. `patent/server/patent/answering.py` 中的 `PatentAnswerBuilder`
3. `patent/server/patent/pdf_service.py` 中的 `PatentPdfAnswerClient`
4. `patent/server_fastapi/app.py` 中的依赖注入与生命周期收口
5. `patent` 进程内面向 OpenAI-compatible LLM 上游的 `httpx.Client` 共享与关闭语义

本设计不覆盖：

1. `PatentEmbeddingClient`
2. `conversation_authority_client`
3. 跨 worker / 跨进程连接池共享
4. `pdf_qa` summary 路径的本地缓冲和补发问题
5. prompt、答案格式、SSE 协议和前端渲染逻辑

---

## Problem Statement

当前 `patent` 内部已经存在多个长期存活对象，但它们各自持有独立的 `httpx.Client`：

1. `PatentPlanningClient` 负责普通 QA stage1 / stage2 的上游调用
2. `PatentAnswerBuilder` 负责普通 QA stage4 的上游调用
3. `PatentPdfAnswerClient` 负责 `pdf_qa` 的上游调用

这些对象虽然都具有“进程级长期存活”的条件，但连接池仍然是割裂的，导致冷启动维度接近：

`worker × client类型`

而不是更理想的：

`worker × upstream`

已经确认的实测现象：

1. 新连接打同一上游时，首字可能在约 `60s` 后才出现
2. 同一个 `httpx.Client` 立即复用热连接时，首字可降到约 `0.3s`
3. `keepalive_expiry=5s` 时，空闲约 `6s` 后热连接收益基本丢失
4. 把实验脚本里的 `keepalive_expiry` 拉长到 `120s` 后，同一 client 在 `6s` 空闲后仍可保持亚秒级首字

因此，当前的核心问题不是“模型天然就慢”，而是：

1. 同一进程内对同一上游缺少共享连接池
2. 即使单个 client 自己能复用，client 之间也彼此隔离
3. 默认 `5s` keepalive 太短，热连接命中率很低

需要明确区分：

1. 单篇 PDF summary 的“本地缓冲到上游结束后才发”是独立问题
2. 60s 级首字慢与上游连接复用策略强相关

本设计只处理第 2 类问题。

---

## Goals

1. 把 `patent` 进程内针对同一 LLM 上游的冷启动维度从 `worker × client类型` 收敛到更接近 `worker × upstream`
2. 保持现有业务分层，不把 `pdf_qa` 和普通 QA 改造成一条新链路
3. 保持纯文件路由在 `patent_runtime` bootstrap 失败时仍可运行
4. 保持调用方对 `api_key`、`base_url`、`model`、`timeout` 的控制能力
5. 保持测试可注入性，不破坏现有 `MockTransport` 风格测试
6. 明确资源归属，避免共享 client 被子组件重复关闭

---

## Non-Goals

1. 不承诺“所有请求只有第一个慢”
2. 不承诺跨 worker 复用热连接
3. 不引入 sidecar、代理层或新的网络中间件
4. 不一次性统一 `patent` 内所有 HTTP 客户端
5. 不在第一阶段改动 `http2`、代理、重试、熔断等策略

---

## Options Considered

### Option A: 只调现有各 client 的 keepalive / expiry 参数

优点：

1. 改动最小
2. 风险低
3. 能提升“同 worker、同 client、短间隔复访”的命中率

缺点：

1. `PatentPlanningClient`、`PatentAnswerBuilder`、`PatentPdfAnswerClient` 之间仍然互不复用
2. `pdf_qa -> 普通 QA`、普通 QA 不同 stage 之间仍然容易重新打冷连接
3. 本质上只是“提高命中率”，不是结构性消除割裂

结论：

可以作为兜底参数优化，但不应作为主方案。

### Option B: 在每个 worker 进程内共享一个面向 LLM 上游的 `httpx.Client`

优点：

1. 结构简单，改动边界清晰
2. 可以让 `pdf_qa`、普通 QA stage1/stage4 在同一进程内共享热连接
3. 资源关闭点清楚，适合挂到现有 app bootstrap 生命周期
4. 不需要跨进程机制

缺点：

1. 仍然无法跨 worker 复用
2. 需要调整若干构造函数和关闭语义
3. 需要处理测试场景下的 transport / mock 注入

结论：

这是第一阶段推荐方案。

### Option C: 做跨 worker 共享池或额外上游代理

优点：

1. 理论上能进一步减少“不同 worker 命中不到热连接”的问题

缺点：

1. 已经是架构级改动
2. 运维、故障域、容量规划都会变化
3. 当前证据不足以支持先上这一级复杂度

结论：

不作为本阶段目标。

---

## Recommended Design

### 1. 引入进程内共享的 LLM Upstream Client Provider

新增一个 `patent` 内部模块，例如：

`patent/server/patent/upstream_http.py`

职责只做一件事：

1. 创建并持有一个进程内共享的 `httpx.Client`
2. 用统一 `Limits` 配置 keepalive / connection 上限
3. 在进程关闭时统一 `close()`

第一阶段不做 keyed pool，不按 model、route 或 client 类型拆多份 client。
理由是当前 3 条 LLM 链路都已经在请求层传入完整 URL 和请求头，完全可以共享同一个 `httpx.Client`，由 HTTP 连接池按 origin 自动分流。

这能以最小复杂度把连接复用粒度压到“同一进程、同一上游域名”。

### 2. 共享 client 只负责连接池，请求参数仍然留在各业务 wrapper

以下信息仍然由现有 wrapper 自己保留和控制：

1. `api_key`
2. `base_url`
3. `model`
4. `top_p`
5. `max_tokens`
6. 请求级超时

共享的只是底层 `httpx.Client`，不是把三类业务 wrapper 合并成一个大对象。

这样做的好处是：

1. 不改变业务分工
2. 连接复用和业务语义解耦
3. 后续如果 stage1 / stage4 / pdf 使用不同上游地址，仍可由同一 `httpx.Client` 管理多 origin 连接

### 3. 调用级 timeout 改为 request-level，不再绑定在 client 实例上

当前多个 wrapper 把 timeout 写在 `httpx.Client(...)` 上。

共享 client 后，timeout 应改为：

1. shared client 使用统一基础配置，不固化某个业务 timeout
2. 每次 `post(...)` / `stream(...)` 调用显式传入 `timeout=...`

这样 `PatentPlanningClient`、`PatentAnswerBuilder`、`PatentPdfAnswerClient` 仍可保留各自不同的超时设定，同时共享同一底层连接池。

### 4. wrapper 构造函数改为支持注入 shared client

以下对象应支持显式传入 `http_client`：

1. `PatentPlanningClient`
2. `PatentAnswerBuilder`
3. `PatentPdfAnswerClient`

关闭语义需要同步调整：

1. 如果 wrapper 自己创建 client，则 `close()` 负责关闭
2. 如果 wrapper 使用外部注入的 shared client，则 `close()` 必须是 no-op，或只关闭自己私有资源

核心约束是：

共享 client 的所有权只能有一个地方持有，不能让子组件各自 `close()`。

### 5. shared client 生命周期挂到 app 级 bootstrap，而不是 `patent_runtime`

当前 `patent_runtime` 不是一个无条件存在的资源。`build_default_patent_runtime()` 在 archive bootstrap 不可用时可以返回 `None`，而现有执行器又允许纯文件路由在这种情况下继续运行。

因此第一阶段推荐：

1. 在 `app.py` 的 service bootstrap 中先创建 shared upstream client provider
2. 将 provider 挂到 `app.state`
3. 如果 `patent_runtime` 成功创建，则把 shared client 注入 `PatentPlanningClient` 和 `PatentAnswerBuilder`
4. 无论 `patent_runtime` 是否可用，都可以基于同一个 provider 构造 `PatentPdfService`
5. 由 `app.py` 的生命周期统一关闭 provider 和 app-owned `pdf_service`

这样可以同时满足两件事：

1. 普通 QA 链路能消费同一条 shared pool
2. `pdf_qa` 不会被错误地绑到 `patent_runtime` 可用性上

同时不需要新增全局模块级单例，也不需要隐藏式懒加载。

### 6. `pdf_qa` 通过显式注入接入 shared client

`PatentPdfService` 当前不是 `PatentRuntime` 内部资源，而是由 `PatentExecutor` 持有；同时纯文件路由在 `runtime=None` 时仍然允许执行。

第一阶段推荐做显式注入，而不是在 `PatentPdfService` 内部偷偷读取全局状态：

1. app bootstrap 先创建 shared upstream client provider
2. 基于 provider 持有的 shared client 创建 `PatentPdfAnswerClient`
3. 用该 client 构造 `PatentPdfService`
4. 再把 `pdf_service` 显式传给 `PatentExecutor`

如果 shared pool 开关关闭，或者 provider 初始化失败，则仍然允许回退到私有 client 的 `PatentPdfService`，但该实例必须成为 app-owned 资源并在 shutdown 时显式关闭。

这样依赖关系是可见的，也更容易测试，同时不破坏当前 degraded-mode 文件路由。

### 7. 保留 transport 注入测试通道

`PatentAnswerBuilder` 现有测试已经依赖 `httpx.MockTransport`。

因此设计要求：

1. 测试传入 `transport` 时，仍允许创建私有 `httpx.Client`
2. shared client 方案不能吞掉测试注入能力
3. 如果 `transport` 与 `http_client` 同时出现，应优先走显式测试注入，并拒绝混用或在构造时明确报错

这能避免把测试体系一起打碎。

---

## Configuration

第一阶段建议新增面向 shared upstream client 的配置，但保持默认值保守：

1. `PATENT_LLM_HTTP_SHARED_POOL_ENABLED`
   - 初始默认：`false`
   - 作用：灰度阶段允许按环境逐步开启，必要时快速退回各自私有 client
2. `PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS`
   - 默认：`120`
   - 理由：先解决默认 `5s` 几乎总是冷掉的问题
3. `PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS`
   - 默认：沿用 `httpx` 默认值 `20`
4. `PATENT_LLM_HTTP_MAX_CONNECTIONS`
   - 默认：沿用 `httpx` 默认值 `100`

本阶段不建议同时引入太多连接池调优参数。当前最关键的收益来自：

1. 共用同一个 client
2. 把 `keepalive_expiry` 从 `5s` 拉长到可用水平

对于“系统需要扛住 20 并发”这个目标，连接池参数仍需和以下配置一起看：

1. worker 数
2. `PATENT_ASK_STREAM_MAX_CONCURRENT`
3. `PATENT_ASK_EXECUTOR_MAX_WORKERS`

共享池能改善冷连接命中率，但不替代整体并发容量规划。

---

## File-Level Design

### New File

`patent/server/patent/upstream_http.py`

建议职责：

1. 解析 shared pool 配置
2. 创建 `httpx.Limits`
3. 创建并持有共享 `httpx.Client`
4. 提供 `close()`
5. 输出一次性 bootstrap 日志

### Modified Files

`patent/server/patent/runtime.py`

1. `build_default_patent_runtime()` 支持接收外部注入的 shared client 或 provider
2. 构造 `PatentPlanningClient` 和 `PatentAnswerBuilder` 时传入 shared client
3. 继续只负责 runtime 自身资源，不再假设自己拥有 shared pool 的唯一生命周期

`patent/server/patent/answering.py`

1. `PatentAnswerBuilder` 支持注入 `http_client`
2. 请求调用改为传 request-level `timeout`
3. 调整 `close()` 语义，避免误关 shared client

`patent/server/patent/pdf_service.py`

1. `PatentPdfAnswerClient` 支持注入 `http_client`
2. 请求调用改为传 request-level `timeout`
3. `PatentPdfService` 支持接收已构造好的 answer client

`patent/server_fastapi/app.py`

1. 在 service bootstrap 中创建并挂载 app-owned shared upstream client provider
2. 基于该 provider 构造 app-owned `PatentPdfService`
3. 将 `pdf_service` 显式传给 `PatentExecutor`
4. 如果 `patent_runtime` 成功创建，则把 shared client 继续注入 runtime consumers
5. 在 shutdown / bootstrap fail cleanup 中显式关闭 `patent_pdf_service` 与 shared provider

### Existing Hook Reused As-Is

`patent/server/patent/executor.py`

1. 现有 `pdf_service` 构造注入点已经存在
2. 本阶段只复用这条注入路径，不要求额外修改执行器接口

---

## Observability

本次改造应增加但不过度增加日志，重点是验证“是否真的共享到了同一个 client”。

建议日志点：

1. shared pool bootstrap
   - 是否启用 shared pool
   - keepalive / connection limits
2. `PatentPlanningClient` / `PatentAnswerBuilder` / `PatentPdfAnswerClient` 初始化
   - 是否使用 shared client
   - `id(http_client)` 或等价标识
3. 关键请求开始时
   - 当前 wrapper 名称
   - 当前 `base_url`
   - 当前 `timeout`
   - shared client 标识

目标不是长期保留高噪音日志，而是在上线前后能明确回答：

1. 三条链路是不是用了同一个进程内 client
2. 哪些请求仍然落在冷连接路径

---

## Risks

### Risk 1: shared client 被错误关闭

如果 wrapper 仍按旧语义在自己的 `close()` 中关闭 client，可能导致其他链路在运行中突然失去连接池。

缓解：

1. 明确 client ownership
2. 对注入 shared client 的 wrapper 使用 no-op close
3. 用测试锁住关闭语义

### Risk 2: 过长 keepalive 带来陈旧连接

更长 keepalive 会增加复用命中率，但也可能遇到上游回收闲置连接后的半失效 socket。

缓解：

1. 先把 expiry 拉到 `120s`，不要一步极端放大
2. 保持错误恢复为 `httpx` 正常重连，不额外叠加复杂重试逻辑

### Risk 3: 把“连接池优化”误当成“所有慢请求都解决”

共享池只改善 cold connection 问题，不会解决：

1. summary 本地缓冲
2. 上游模型真实推理耗时
3. 不同 worker 命不中同一热连接

缓解：

上线说明和验收口径里必须把这些边界写清楚。

### Risk 4: 回归当前纯文件路由的 degraded-mode 可用性

如果 shared pool 错误地以 `runtime` 为唯一宿主，会把 `pdf_qa` 隐式绑定到 `patent_runtime` 是否可用，和当前行为不兼容。

缓解：

1. shared pool 归属放在 app bootstrap，而不是 runtime 内部
2. 验收中明确要求 `runtime=None` 时纯文件路由仍可运行
3. 保留 `PatentExecutor` 现有的文件路由 fallback 语义

---

## Acceptance Criteria

1. `PatentPlanningClient`、`PatentAnswerBuilder`、`PatentPdfAnswerClient` 在同一进程内可指向同一个 shared `httpx.Client`
2. shared pool 打开时，子 wrapper 的 `close()` 不会关闭共享 client
3. app-owned `PatentPdfService` 与 shared provider 的 shutdown ownership 明确，bootstrap fail 和正常 shutdown 都不会遗留未关闭资源
4. `patent_runtime` 不可用时，纯文件路由仍可执行，不回归当前 degraded-mode 行为
5. 自动化测试至少覆盖：
   - shared client 注入
   - timeout 仍按调用方生效
   - close ownership 正确
   - transport 测试通道不受破坏
   - app bootstrap / shutdown 对 app-owned `pdf_service` 的资源关闭语义
6. 跨 `pdf_qa` / 普通 QA 的热连接复用通过开发或预发环境的日志与集成验证确认，不把它错误表述成当前单元测试已可直接证明的事项
7. 本次改造不改变现有答案内容、SSE 事件协议和 summary 缓冲行为

---

## Rollout Strategy

第一阶段建议灰度方式：

1. 先引入 shared pool 开关，且初始默认关闭
2. 在开发/预发环境用日志确认三类 wrapper 已共享同一 client
3. 用真实链路验证：
   - `pdf_qa -> pdf_qa`
   - `pdf_qa -> 普通 QA`
   - 普通 QA 连续两次
   - `patent_runtime` 不可用时，纯文件路由仍然可用
4. 观察是否仍有异常首字慢，并区分是：
   - 连接未命中
   - summary 本地缓冲
   - 上游真实生成慢

如果灰度稳定，再把 shared pool 调整为默认开启。

---

## Out of Scope Follow-Ups

如果本设计落地后仍需进一步压缩首字延迟，后续可独立评估：

1. 单篇 PDF summary 取消或缩短 `aligned_summary_mode` 的本地缓冲
2. `patent` 内更多 HTTP client 的统一资源管理
3. 更细粒度的 keyed pool
4. 跨 worker 共享连接的 sidecar / proxy 方案

这些都不属于本 spec 的第一阶段交付范围。
