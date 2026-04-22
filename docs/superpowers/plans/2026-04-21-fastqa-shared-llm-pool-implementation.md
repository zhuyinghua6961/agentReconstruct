# FastQA Shared LLM Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `fastQA` 落地 app-owned、per-worker 的共享 LLM 上游连接池，把 `kb_qa`、`QueryExpander`、`file_routes`、`file_route_service` 统一到同一个底层 `httpx.Client` 连接池，并把默认池容量调大到适合流式输出的水平。

**Architecture:** FastAPI app bootstrap 成为共享 upstream HTTP pool 的唯一 owner，每个 Gunicorn worker 进程持有一套共享 `httpx.Client`。`OpenAICompatClient` 与 `OpenAICompatChatAdapter` 改为支持注入外部 `httpx.Client` 且不关闭共享资源；`GenerationDrivenRAG`、`QueryExpander`、文件路由和兼容路径都改为消费这套 app-owned pool。共享粒度锁定为“每个 worker 针对当前服务解析出的单套上游配置一套 client”，不是“按 provider 名字建多个池”。跨 worker 的会话粘性不在本计划内解决，那是 gateway 的后续单独计划。

**Tech Stack:** Python 3, FastAPI, Gunicorn/UvicornWorker, httpx, pytest, OpenAI-compatible upstream APIs

---

## Scope

本计划只覆盖 `fastQA` 服务内的 **进程内共享池** 与 **池容量治理**，不包含：

1. Gateway 层按 `conversation_id` 的会话粘性路由
2. 跨 worker 共享同一个内存连接池
3. 跨服务共享上游连接池

当前多 worker 前提下，正确目标不是“整个服务只有一个池”，而是：

1. 先把 **单 worker 内的多套 client / 多个小池** 收敛成一套共享池
2. 再在后续独立计划里通过 gateway 粘性路由提高“同一会话命中同一 worker”的概率

## Source Documents

- Current architecture notes:
  - `fastQA/docs/01-system-overview.md`
  - `fastQA/docs/03-rag-planner-retriever-llm.md`
- Current LLM transport/runtime code:
  - `fastQA/app/integrations/llm/openai_compat.py`
  - `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
  - `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
  - `fastQA/app/modules/generation_pipeline/query_expander.py`
  - `fastQA/app/services/file_routes.py`
  - `fastQA/app/services/file_route_service.py`
  - `fastQA/app/main.py`
  - `fastQA/app/routers/health.py`
- Existing tests to extend:
  - `fastQA/tests/test_llm_openai_compat.py`
  - `fastQA/tests/test_llm_shared_http_pool.py`
  - `fastQA/tests/test_generation_runtime_bootstrap.py`
  - `fastQA/tests/test_generation_driven_rag_init.py`
  - `fastQA/tests/test_redis_runtime.py`
  - `fastQA/tests/test_qa_routes_file_modes.py`
  - `fastQA/tests/test_health.py`

## Hard Rules

1. 共享池的 owner 只能是 FastAPI app state；`GenerationDrivenRAG`、`QueryExpander`、文件路由 wrapper 都只能消费它，不能拥有它。
2. “共享池”指的是 **共享同一个底层 `httpx.Client`**；高层 wrapper 可以有多个，但都必须挂在同一个 `httpx.Client` 上。
3. 任何消费外部注入 `httpx.Client` 的 wrapper，在 `close()` 时都不能误关共享资源。
4. request-level timeout 语义必须保留；不能因为共享池而把所有调用硬绑到同一个全局 timeout。
5. 本计划默认池参数必须显式调大，不能继续沿用今天的 `max_connections=50 / max_keepalive_connections=20 / keepalive_expiry=5s` 组合。
6. 锁定初始默认值时必须考虑流式输出；Stage4 SSE 会长时间占用连接，池太小会导致后续 Stage1/Stage2/file 路径等待甚至耗尽。
7. 不能把 `keepalive_expiry` 直接设成 `1h`；先落到 `60-120s` 范围，减少 stale socket 风险。
8. 如果共享池开关关闭，或共享池 bootstrap 失败，系统必须退回私有 client 路径，不能直接让 `kb_qa` 或文件路由不可用。
9. 本计划不声称解决跨 worker 复用；文档、日志、注释都必须明确这是 **per-worker** 共享池。
10. 每个 task 都先写红灯测试，再做最小实现，再跑目标测试，再 commit。

## Lifecycle / Fork Safety

1. 共享池绝不能在模块 import 时创建；`shared_http_pool.py` 只能定义类型与 builder，不能在模块级持有 `httpx.Client` 单例。
2. 当前 `fastQA/scripts/start_gunicorn.sh` 没有启用 `--preload`，实现默认依赖“worker 进程内 import + `create_app()` bootstrap”来创建共享池。
3. 计划内实现必须保证：共享池只在 worker 进程内的 app bootstrap / lifespan startup / 首次 post-fork lazy bootstrap 其中之一创建，并挂到 `app.state`。
4. 如果未来引入 Gunicorn `preload_app`，共享池创建必须自动退化成 post-fork lazy init；不允许父进程预先创建后被 worker 继承。
5. 启动日志必须打印至少这些字段：`pid`、`pool_owner=app`、`shared_client_id`、`client_owner=shared|private`、`bootstrap_source=startup|lazy`。
6. shutdown 时必须只关闭一次共享池；实现和测试都要覆盖“worker 关闭 / app reload / 显式 `close_generation_runtime()`”三类路径，不允许遗留 socket。

## Timeout Semantics

共享池只负责 **transport / connection limits / keepalive**，不能把所有请求强制绑定到同一个不可覆盖的 timeout 组合。timeout 语义锁定如下：

| Timeout 维度 | 默认来源 | 是否允许 request 级覆盖 | 规则 |
| --- | --- | --- | --- |
| `connect` | 共享 client 默认值 | 允许 | 默认由 env 配置，必要时可按请求覆盖，但不为共享池单独放大 |
| `write` | 共享 client 默认值 | 允许 | 保持 bounded，避免上传/写入卡死 |
| `read` | 共享 client 默认值 | 必须允许 | 普通 completion 可沿用默认值；Stage4 / PDF / Tabular 上游流式请求必须允许更高或单独的 streaming-safe read timeout |
| `pool` | 共享 client 默认值 | 允许，但默认 bounded | `pool_timeout` 只表示等待连接的时间，不能与 `connect/read` 混淆；超时后要明确进入可观测失败路径 |

推荐 env 映射锁定如下：

| Timeout 维度 | 首选 env | 兼容回退 env | 默认值 | 作用域 |
| --- | --- | --- | --- | --- |
| `connect_timeout_seconds` | `FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS` | `OPENAI_CONNECT_TIMEOUT_SECONDS` / `DASHSCOPE_CONNECT_TIMEOUT_SECONDS` / `LLM_CONNECT_TIMEOUT_SECONDS` | `15s` | shared client 默认值 |
| `read_timeout_seconds` | `FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS` | `OPENAI_READ_TIMEOUT_SECONDS` / `DASHSCOPE_READ_TIMEOUT_SECONDS` / `LLM_READ_TIMEOUT_SECONDS` | `180s` | 非 streaming 请求默认值 |
| `stream_read_timeout_seconds` | `FASTQA_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS` | 无；首次实现显式新增 | `600s` | streaming 请求 request-level override |
| `write_timeout_seconds` | `FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS` | `OPENAI_WRITE_TIMEOUT_SECONDS` / `DASHSCOPE_WRITE_TIMEOUT_SECONDS` / `LLM_WRITE_TIMEOUT_SECONDS` | `180s` | shared client 默认值 |
| `pool_timeout_seconds` | `FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS` | `OPENAI_POOL_TIMEOUT_SECONDS` / `DASHSCOPE_POOL_TIMEOUT_SECONDS` / `LLM_POOL_TIMEOUT_SECONDS` | `30s` | shared client 默认值，可按请求覆盖 |

env 解析顺序也要锁死：

1. 每个 timeout 字段都遵循 **first-defined-wins**：先看 `FASTQA_*`，再看 `OPENAI_*`，再看 `DASHSCOPE_*`，最后看 `LLM_*`
2. 空字符串按“未设置”处理
3. 非法值回退到该字段默认值，并打 warning log

实现要求补充：

1. `OpenAICompatClient` / `OpenAICompatChatAdapter` 除了支持 injected `http_client`，还必须支持 request-level timeout override，至少能区分普通 completion 与 streaming completion 的 `read` / `pool` 语义。
2. streaming 路径不能因为共享池而退回到过小的统一 `read_timeout_seconds`；否则长流会被误杀。
3. `pool_timeout_seconds` 保持 bounded，用于暴露竞争，不作为“吞掉拥塞”的手段。
4. 告警与日志必须能区分：`PoolTimeout`、connect timeout、read timeout。
5. 如果命中 `PoolTimeout`，请求必须走显式失败路径并打出结构化日志；不允许无限重试，也不允许把调用方静默挂到比 `pool_timeout_seconds` 更久。
6. 对外契约锁定为：
   - **非流式 HTTP 路由**：返回 `503`，错误码 `UPSTREAM_POOL_TIMEOUT`，message 使用稳定文本 `upstream_pool_timeout`，默认不自动重试
   - **流式 SSE 路由，若首字节前失败**：直接返回 `503` JSON，同样使用 `UPSTREAM_POOL_TIMEOUT`
   - **流式 SSE 路由，若流已建立后再失败**：发一个终止型 error event，`code=UPSTREAM_POOL_TIMEOUT`、`retriable=true`，随后结束流

非流式 `503` JSON body 锁定为：

```json
{
  "success": false,
  "code": "UPSTREAM_POOL_TIMEOUT",
  "error": "upstream_pool_timeout",
  "message": "upstream_pool_timeout",
  "retriable": true,
  "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "trace_id": "..."
}
```

流式 SSE error frame 锁定为：

```text
data: {"type":"error","code":"UPSTREAM_POOL_TIMEOUT","error":"upstream_pool_timeout","message":"upstream_pool_timeout","retriable":true,"route":"kb_qa","trace_id":"..."}
```

补充规则：

1. 第一版不使用自定义 `event:` 名称，沿用现有 `data:` JSON frame 约定
2. `route` 与 `trace_id` 必须透传，便于日志与前端关联
3. 默认不自动重试；是否重试由上游调用方自行决定

## Shared Client Identity

本计划把“共享 client 身份”简化为：

1. **每个 worker 只维护一套 app-owned shared client**，对应 `fastQA` 当前解析出的单套上游 LLM 配置。
2. `model` 不参与 shared client 身份判定；不同 model 仍可复用同一个底层 `httpx.Client`。
3. 如果未来真的需要同一 worker 内多套 endpoint / proxy / TLS 配置并存，必须另起计划；本计划不做“按 provider 名字缓存多个池”。

## Capacity Model

当前 `fastQA` 共享配置：

- `ASK_STREAM_MAX_CONCURRENT=20`
- `FASTQA_GUNICORN_WORKERS=4`
- `QA_STAGE2_PARALLEL_WORKERS=5`

这意味着单个 worker 的上游连接需求不能按“20 个并发问答 = 20 条上游连接”这么简单估算。至少要预算：

1. 长连接占用：
   - Stage4 流式合成
   - PDF 文件流式回答
   - 表格/混合文件流式回答
2. 短请求突发：
   - Stage1 planning
   - Stage2 AI query generation
   - QueryExpander
   - 文件路由里的辅助 LLM 调用

### Per-Worker Budgeting Model

用来解释 `160` 的不是“拍脑袋更大”，而是以下保守预算：

1. **长占用基线**
   - 每条活跃 `kb_qa` / `pdf_qa` / `tabular_qa` / `hybrid_qa` 流，稳态至少会占 1 条上游 streaming 连接。
   - 在极端情况下，单 worker 如果承担了全部 `ASK_STREAM_MAX_CONCURRENT=20` 的活跃流，长占用基线就是 20。
2. **新请求进入时的短突发**
   - 单条 `kb_qa` 请求在进入流式合成前，至少会打 1 次 Stage1 completion。
   - Stage2 还可能并行打最多 `QA_STAGE2_PARALLEL_WORKERS=5` 条短 completion，并伴随 QueryExpander 调用。
   - 文件路由也会出现辅助 LLM 调用，但通常是短请求。
3. **保守容量目标**
   - 把 `20` 条长连接当作不可压缩基线。
   - 再给新进入请求的 Stage1/Stage2/QueryExpander/file-aux 预留一段不会立即互相阻塞的短连接缓冲。
   - 最后再留重试/瞬时毛刺 headroom，而不是把池开到刚刚够用。

因此，`160` 的含义是：在流式连接长期占住池子的情况下，仍然给短 completion 留出足够冗余，避免 Stage1/Stage2 直接因连接竞争排队到尾部。它是 **per-worker overprovisioned ceiling**，不是预期长期占满值。

### Locked Initial Defaults

第一版共享池默认值锁成：

- `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED=0`（代码支持共享池，但首个上线窗口默认关闭，先走 canary）
- `FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS=90`
- `FASTQA_LLM_HTTP_MAX_CONNECTIONS=160`
- `FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS=64`
- `FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS=30`

### Why These Numbers

1. `160` 是 **per-worker** 的上限，不是整个服务总上限。按 4 worker 计算，服务总上限约为 640 条上游连接预算。
2. 当前 `ASK_STREAM_MAX_CONCURRENT=20`，只算 Stage4/文件流输出，长占用连接就可能到 20。
3. Stage1/Stage2/QueryExpander 需要额外短连接突发；把 `max_connections` 提到 `160` 才有足够缓冲，不会因为少量长流直接把短请求全部堵死。
4. `64` 个 keep-alive 连接足够留住热连接，但又不会像 `1h` 那样把大量空闲 socket 长时间留在池里。
5. `90s` 基本覆盖“同一对话里 10-60 秒后追问”的常见场景，比 `5s` 明显更实用。
6. `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED` 与容量参数分开治理：容量默认值先锁住，是否真正启用通过 rollout 控制。

### Tuning Rule

如果后续 `ASK_STREAM_MAX_CONCURRENT` 提升，不要继续写死固定数值；优先采用：

```text
max_connections = clamp(160, 8 * ask_stream_max_concurrent, 256)
max_keepalive_connections = clamp(64, 3 * ask_stream_max_concurrent, 96)
keepalive_expiry_seconds = 90
pool_timeout_seconds = 30
```

其中 `clamp(min, value, max)` 表示上下限夹紧。`1h keepalive` 不在默认支持范围内。

### Acceptance Criteria And Saturation Signals

共享池不是“只要不报错就算成功”。至少要满足：

1. 在 `ASK_STREAM_MAX_CONCURRENT=20`、`QA_STAGE2_PARALLEL_WORKERS=5` 的现有配置下，canary 流量中不出现持续性的 `PoolTimeout`。
2. 同一 worker 内，多轮请求命中同一个 `shared_client_id`，但不同 worker 的 `pid` / `shared_client_id` 可以不同。
3. 在有长流的同时，新请求的 Stage1/Stage2 不应出现明显的长时间排队尾延迟；canary 期间 `pool_wait_ms` 的 p95 目标应低于 250ms，如持续高于 1000ms 视为饱和告警。
4. 共享池关闭后，回退路径应恢复为稳定的 app-owned private clients，不出现每请求新建 client。

必须新增的可观测信号：

1. `pool_timeout_count`
2. `pool_wait_ms` 或等价的连接等待时延
3. `pid`
4. `shared_client_id`
5. `client_owner=shared|private`
6. `bootstrap_source=startup|lazy`

`pool_wait_ms` 的测量方法也要锁定：

1. 对普通 completion，从进入 shared-client `request/create` 调用开始计时，到收到 response headers 或抛出 `PoolTimeout` 为止。
2. 对 streaming completion，从进入 shared-client `stream/create` 调用开始计时，到拿到 upstream headers / stream connected 或抛出 `PoolTimeout` 为止，不包含后续 token 迭代耗时。
3. 这个指标在第一版里表示“连接获取阶段总等待时间近似值”，不是 httpcore 内部纯队列时间；成功和 timeout 两条路径都必须打日志。

## File Map

### Shared Pool Primitive And Transport Wiring

- Create: `fastQA/app/integrations/llm/shared_http_pool.py`
- Modify: `fastQA/app/integrations/llm/openai_compat.py`
- Modify: `fastQA/app/integrations/llm/__init__.py`
- Test: `fastQA/tests/test_llm_shared_http_pool.py`
- Test: `fastQA/tests/test_llm_openai_compat.py`

### Runtime And Query Expansion Wiring

- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/generation_pipeline/query_expander.py`
- Modify: `fastQA/app/core/runtime.py`
- Test: `fastQA/tests/test_generation_runtime_bootstrap.py`
- Test: `fastQA/tests/test_generation_driven_rag_init.py`
- Test: `fastQA/tests/test_redis_runtime.py`
- Test: `fastQA/tests/test_generation_stage1_planning.py`
- Test: `fastQA/tests/test_generation_stage4_synthesis.py`

### File Route And Compatibility Path Wiring

- Modify: `fastQA/app/services/file_routes.py`
- Modify: `fastQA/app/services/file_route_service.py`
- Modify: `fastQA/app/main.py`
- Test: `fastQA/tests/test_qa_routes_file_modes.py`
- Test: `fastQA/tests/test_qa_pdf_service.py`
- Create: `fastQA/tests/test_file_route_service.py`

### Config, Health, And Observability

- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/routers/health.py`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Test: `fastQA/tests/test_health.py`
- Test: `fastQA/tests/test_generation_runtime_bootstrap.py`
- Test: `fastQA/tests/test_llm_shared_http_pool.py`
- Test: `fastQA/tests/test_redis_runtime.py`
- Create: `fastQA/tests/test_qa_pool_timeout_contract.py`

## Lock Decisions

1. 共享池对象叫做 `shared_llm_http_pool` 或等价命名，挂在 `app.state`，并暴露底层 `httpx.Client`。
2. `app.state.shared_llm_adapter` 继续保留，但变成 **基于共享池构建的 chat adapter**，不是独立私有 client owner。
3. `GenerationDrivenRAG.client` 和 `QueryExpander._client` 可以继续是不同 wrapper 类型，但必须指向同一个底层 `httpx.Client`。
4. `file_routes.py` 的 `aux_llm` 不能再独立起私有池；最多只能作为兼容别名指向 app-owned shared adapter。
5. `file_route_service.py` 当前私有 `_llm` 缓存是需要清理的重复 owner；计划中要把它改成消费 `app.state` 的共享 adapter / pool。
6. `shared_llm_adapter_ready` 不够表达共享池健康度；新增单独的 `shared_llm_pool` component status。
7. 本计划完成后，`fastQA` 仍然是“每 worker 一套共享池”；如果用户后续要求“同一会话尽量复用同一池”，必须另起 gateway 粘性路由计划。

## Ownership Matrix

| 场景 | 底层 client 归属 | 高层 wrapper 归属 | 关闭时机 | 预期日志 |
| --- | --- | --- | --- | --- |
| 共享池启用且 bootstrap 成功 | `app.state.shared_llm_http_pool` | `GenerationDrivenRAG` / `QueryExpander` / `shared_llm_adapter` / `aux_llm` 兼容别名都只是 consumer | app shutdown / runtime close 统一关闭一次 | `client_owner=shared` |
| 共享池关闭 | app-owned private runtime/client | 各模块消费 app state 上缓存的 private runtime / adapter，不允许每请求自建 | app shutdown / runtime close | `client_owner=private status=skipped` |
| 共享池启用但 bootstrap 失败 | app-owned private fallback runtime/client | consumer 改接 private fallback；component status 记 `degraded` | app shutdown / runtime close | `client_owner=private status=degraded` |

补充规则：

1. 无论 shared 还是 private fallback，**都不允许 request-scope 新建 `httpx.Client`**。
2. 对 injected `http_client` 的 wrapper `close()` 不能关闭底层共享资源。
3. private fallback 的 owner 必须明确，并且要有测试覆盖它会在 shutdown 时被关闭。

### Task 1: 新增 app-owned shared upstream pool，并让 OpenAI-compatible wrappers 支持 injected client

**Files:**
- Create: `fastQA/app/integrations/llm/shared_http_pool.py`
- Modify: `fastQA/app/integrations/llm/openai_compat.py`
- Modify: `fastQA/app/integrations/llm/__init__.py`
- Test: `fastQA/tests/test_llm_shared_http_pool.py`
- Test: `fastQA/tests/test_llm_openai_compat.py`

**Testing Requirement:**
- 锁死上游 env 解析、shared client 复用、limits 配置、timeout override 语义，以及 wrapper 在 injected-client 路径下的 ownership 语义。
- 必跑命令：

```bash
pytest fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_llm_openai_compat.py -q
```

- [ ] **Step 1: 写红灯测试**

覆盖这些断言：

```python
def test_shared_pool_from_env_reuses_one_httpx_client_per_worker_runtime_config():
    ...

def test_shared_pool_reads_keepalive_and_capacity_from_env():
    ...

def test_openai_compat_client_supports_request_level_timeout_override():
    ...

def test_openai_compat_stream_supports_streaming_safe_read_timeout_override():
    ...

def test_openai_compat_client_does_not_close_injected_http_client():
    ...

def test_openai_compat_chat_adapter_does_not_close_injected_http_client():
    ...
```

- [ ] **Step 2: 跑红灯测试**

Run:

```bash
pytest fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_llm_openai_compat.py -q
```

Expected:
- FAIL
- 失败点集中在缺少 shared pool builder、wrapper 还不支持 `http_client=` 注入/timeout override、以及 ownership 语义未锁住

- [ ] **Step 3: 最小实现 shared pool primitive 与 injected-client support**

实现要求：

```python
class FastQASharedUpstreamHttpPool:
    @classmethod
    def from_env(cls) -> "FastQASharedUpstreamHttpPool":
        ...

    def client(self) -> httpx.Client | None:
        ...

    def close(self) -> None:
        ...
```

并让 `build_chat_adapter(...)` / `build_chat_completions_client(...)` 支持：

```python
build_chat_adapter(..., http_client=shared_http_client, keepalive_expiry_seconds=None, max_connections=None, max_keepalive_connections=None)
build_chat_completions_client(..., http_client=shared_http_client, keepalive_expiry_seconds=None, max_connections=None, max_keepalive_connections=None)
```

并补充这些硬要求：

1. `shared_http_pool.py` 不能持有模块级 client 单例。
2. wrapper 必须支持 request-level timeout override，至少能区分普通 completion 与 streaming completion 的 timeout。
3. injected-client 路径下，transport owner 始终是 app state，不是 wrapper。

- [ ] **Step 4: 重跑 Task 1 目标测试**

Run:

```bash
pytest fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_llm_openai_compat.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add fastQA/app/integrations/llm/shared_http_pool.py fastQA/app/integrations/llm/openai_compat.py fastQA/app/integrations/llm/__init__.py fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_llm_openai_compat.py
git commit -m "feat(fastqa): add app-owned shared llm upstream pool primitive"
```

### Task 2: 让 generation runtime 与 QueryExpander 共用同一个底层 httpx.Client

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/generation_pipeline/query_expander.py`
- Modify: `fastQA/app/core/runtime.py`
- Test: `fastQA/tests/test_generation_runtime_bootstrap.py`
- Test: `fastQA/tests/test_generation_driven_rag_init.py`
- Test: `fastQA/tests/test_redis_runtime.py`
- Create: `fastQA/tests/test_generation_runtime_shared_pool.py`

**Testing Requirement:**
- 锁死 `GenerationDrivenRAG.client` 与 `QueryExpander` 底层共享同一个 `httpx.Client`，而不是各起一套池。
- 锁死 runtime shutdown 会关闭共享池一次，并且不会在 import / 模块级提前创建 client。
- 必跑命令：

```bash
pytest fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_generation_driven_rag_init.py fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_redis_runtime.py -q
```

- [ ] **Step 1: 写红灯测试**

覆盖这些断言：

```python
def test_bootstrap_generation_runtime_builds_shared_pool_once_per_app_state():
    ...

def test_generation_driven_rag_and_query_expander_share_same_underlying_http_client():
    ...

def test_generation_runtime_degrades_to_private_path_when_shared_pool_bootstrap_fails():
    ...

def test_close_generation_runtime_closes_shared_pool_once():
    ...
```

- [ ] **Step 2: 跑红灯测试**

Run:

```bash
pytest fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_generation_driven_rag_init.py fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_redis_runtime.py -q
```

Expected:
- FAIL
- 失败点集中在 runtime 还没有 app-owned shared pool、`QueryExpander` 仍自己起 private client

- [ ] **Step 3: 最小实现 runtime wiring**

实现要求：
- `app.state` 新增共享池对象与状态字段
- `bootstrap_generation_runtime(...)` 优先消费 app-owned shared pool
- `GenerationDrivenRAG(...)` 构造函数支持接收 injected `http_client` 或 injected provider
- `QueryExpander(...)` 支持接收 injected `http_client`
- `GenerationDrivenRAG._get_query_expander()` 必须把共享 `http_client` 传进去
- 共享池 bootstrap 必须发生在 worker 内的 app bootstrap / startup 路径，不能依赖模块级 import side effect
- `close_generation_runtime(...)` / app shutdown 必须能安全关闭 shared pool，且只关闭一次

- [ ] **Step 4: 重跑 Task 2 目标测试**

Run:

```bash
pytest fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_generation_driven_rag_init.py fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_redis_runtime.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add fastQA/app/modules/generation_pipeline/runtime_bootstrap.py fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py fastQA/app/modules/generation_pipeline/query_expander.py fastQA/app/core/runtime.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_generation_driven_rag_init.py fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_redis_runtime.py
git commit -m "feat(fastqa): wire generation runtime and query expander to shared llm pool"
```

### Task 3: 收敛文件路由与兼容路径，消除 `aux_llm` / `file_route_service._llm` 的重复 owner

**Files:**
- Modify: `fastQA/app/services/file_routes.py`
- Modify: `fastQA/app/services/file_route_service.py`
- Modify: `fastQA/app/main.py`
- Test: `fastQA/tests/test_qa_routes_file_modes.py`
- Test: `fastQA/tests/test_qa_pdf_service.py`
- Create: `fastQA/tests/test_file_route_service.py`

**Testing Requirement:**
- 锁死 `file_routes.py` 与 `file_route_service.py` 都消费 app-owned shared adapter / shared pool，不再各自建私有池。
- 必跑命令：

```bash
pytest fastQA/tests/test_qa_routes_file_modes.py fastQA/tests/test_qa_pdf_service.py fastQA/tests/test_file_route_service.py -q
```

- [ ] **Step 1: 写红灯测试**

覆盖这些断言：

```python
def test_get_aux_llm_reuses_app_owned_shared_adapter_when_available():
    ...

def test_file_route_service_uses_app_owned_shared_adapter_instead_of_private_cache():
    ...

def test_file_routes_fallback_path_still_works_when_shared_pool_disabled():
    ...
```

- [ ] **Step 2: 跑红灯测试**

Run:

```bash
pytest fastQA/tests/test_qa_routes_file_modes.py fastQA/tests/test_qa_pdf_service.py fastQA/tests/test_file_route_service.py -q
```

Expected:
- FAIL
- 失败点集中在 `get_aux_llm()` 仍会创建 `aux_llm` 私有 owner，`file_route_service.py` 仍缓存自己的 `_llm`

- [ ] **Step 3: 最小实现 file-route convergence**

实现要求：
- `app.state.shared_llm_adapter` 成为文件路由优先使用的 adapter
- `aux_llm` 最多保留为兼容引用，不再拥有独立私有池
- `file_route_service.py` 去掉模块级私有 `_llm` owner，改成从 `app_state` 解析共享 adapter / shared pool
- generation runtime 不可用时，文件路由仍可直接走 app-owned shared adapter，不允许退化成每次新建私有池

- [ ] **Step 4: 重跑 Task 3 目标测试**

Run:

```bash
pytest fastQA/tests/test_qa_routes_file_modes.py fastQA/tests/test_qa_pdf_service.py fastQA/tests/test_file_route_service.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add fastQA/app/services/file_routes.py fastQA/app/services/file_route_service.py fastQA/app/main.py fastQA/tests/test_qa_routes_file_modes.py fastQA/tests/test_qa_pdf_service.py fastQA/tests/test_file_route_service.py
git commit -m "refactor(fastqa): converge file routes on shared llm pool"
```

### Task 4: 加入共享池配置、健康状态、容量日志，并锁死默认参数

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/routers/health.py`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Test: `fastQA/tests/test_health.py`
- Test: `fastQA/tests/test_generation_runtime_bootstrap.py`
- Test: `fastQA/tests/test_llm_shared_http_pool.py`
- Test: `fastQA/tests/test_redis_runtime.py`
- Create: `fastQA/tests/test_qa_pool_timeout_contract.py`

**Testing Requirement:**
- 锁死共享池默认参数、健康暴露与降级状态。
- 锁死 `ok|degraded|skipped` 语义，以及 `ready` 与 `status` 的一致性。
- 必跑命令：

```bash
pytest fastQA/tests/test_health.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_redis_runtime.py fastQA/tests/test_qa_pool_timeout_contract.py -q
```

- [ ] **Step 1: 写红灯测试**

覆盖这些断言：

```python
def test_healthz_exposes_shared_llm_pool_component():
    ...

def test_shared_llm_pool_default_capacity_matches_streaming_budget():
    ...

def test_runtime_bootstrap_reports_shared_pool_degraded_when_provider_init_fails():
    ...

def test_healthz_marks_shared_llm_pool_skipped_when_disabled():
    ...

def test_healthz_ready_is_true_only_when_shared_pool_status_is_ok():
    ...

def test_kb_stream_route_surfaces_upstream_pool_timeout_with_stable_error_contract():
    ...

def test_sync_kb_route_surfaces_upstream_pool_timeout_as_http_503():
    ...
```

触发策略也要锁定：

1. 代表性路由选 `kb_qa`，因为它同时覆盖 fastQA 主路径和 SSE 合成路径
2. 不使用真实并发和睡眠来“碰运气”制造耗尽
3. 测试里通过 monkeypatch / fake shared client，让底层 `chat.completions.create(...)` 或 `stream(...)` **同步抛出 `httpx.PoolTimeout`**
4. 断言只检查稳定契约：HTTP status、JSON/SSE payload、`code`、`message`、`retriable`、`trace_id`、`route`

- [ ] **Step 2: 跑红灯测试**

Run:

```bash
pytest fastQA/tests/test_health.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_redis_runtime.py fastQA/tests/test_qa_pool_timeout_contract.py -q
```

Expected:
- FAIL

- [ ] **Step 3: 最小实现 config + health + observability**

新增并锁定这些 env：

```dotenv
FASTQA_LLM_HTTP_SHARED_POOL_ENABLED=0
FASTQA_LLM_HTTP_CONNECT_TIMEOUT_SECONDS=15
FASTQA_LLM_HTTP_READ_TIMEOUT_SECONDS=180
FASTQA_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS=600
FASTQA_LLM_HTTP_WRITE_TIMEOUT_SECONDS=180
FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS=90
FASTQA_LLM_HTTP_MAX_CONNECTIONS=160
FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS=64
FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS=30
```

并在 health/component status 中新增一个 **`ok` 示例**：

```json
{
  "shared_llm_pool": {
    "status": "ok",
    "ready": true,
    "max_connections": 160,
    "max_keepalive_connections": 64,
    "keepalive_expiry_seconds": 90
  }
}
```

语义锁定：

1. `ok`: shared pool enabled 且 bootstrap 成功，`ready=true`
2. `skipped`: shared pool 被显式关闭，服务走 app-owned private path，`ready=false`
3. `degraded`: shared pool enabled 但 bootstrap 失败，服务退回 app-owned private fallback，`ready=false`
4. `shared_llm_pool` 的 `degraded` 不应单独把 `/api/health` 打成 503；只要 generation runtime 的 fallback 仍健康，readiness 仍按 generation runtime 判定

日志至少包含：
- `pool_owner=app`
- `client_owner=shared|private`
- `shared_client_id`
- `pid`
- `bootstrap_source=startup|lazy`
- `pool_wait_ms`
- `pool_timeout_count`
- `max_connections`
- `max_keepalive_connections`
- `keepalive_expiry_seconds`

- [ ] **Step 4: 重跑 Task 4 目标测试**

Run:

```bash
pytest fastQA/tests/test_health.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_redis_runtime.py fastQA/tests/test_qa_pool_timeout_contract.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add fastQA/app/core/config.py fastQA/app/routers/health.py resource/config/services/fastQA/config.shared.env fastQA/tests/test_health.py fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_redis_runtime.py fastQA/tests/test_qa_pool_timeout_contract.py
git commit -m "feat(fastqa): add shared llm pool config and health status"
```

## Operational Rollout / Rollback

共享池不做“一上线就全量默认开启”。上线策略锁定为：

1. **代码先合并，配置默认关闭**
   - `config.shared.env` 首次合入保持 `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED=0`
   - 同时把容量参数、日志、health、fallback 一起上线
2. **按实例 / Pod canary**
   - 只在一小部分实例 / Pod 上把 `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED=1`
   - 如果需要“单 worker canary”，要通过单独的 canary 部署把 `FASTQA_GUNICORN_WORKERS=1` 跑起来，而不是假设同一 Gunicorn 实例内部可以对单个 worker 单独开关
   - 观察 `shared_llm_pool.status=ok`
   - 日志确认同一 `pid` 内 `shared_client_id` 稳定，且没有 `PoolTimeout`
3. **扩大流量**
   - canary 稳定后再扩大到更多 worker / 实例
4. **全量切换**
   - 只有在 health / 日志 / tail latency 都稳定后，才把共享池从 rollout 配置层面切到默认开启

回滚步骤锁定为：

1. 把 `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED` 改回 `0`
2. 重启 `fastQA` worker
3. 验证 `shared_llm_pool.status=skipped`
4. 验证 `generation_runtime` 仍为 `ok`
5. 验证日志中出现 `client_owner=private`，且 file routes / `kb_qa` 仍可回答

如果启用共享池后出现这些信号，直接回滚：

1. 持续性 `PoolTimeout`
2. worker 重启后连接未释放
3. `shared_llm_pool.status=degraded` 持续存在
4. 长流被异常 read timeout 中断

## Manual Verification Checklist

实现完成后，除了单测，还要做一次真实流量下的人工验证：

1. 启动 `fastQA`，确认 health 暴露了 `shared_llm_pool`
2. 启动日志确认打印了 `pid`、`shared_client_id`、`client_owner`、`bootstrap_source`
3. 连续发起多轮 `kb_qa` 请求，检查同一 worker 内的 `shared_client_id` 保持不变
4. 打开 `pdf_qa` / `tabular_qa` / `hybrid_qa`，确认它们不再创建额外的 private client
5. 在有流式输出的情况下观察是否出现 `PoolTimeout` / `pool_wait_ms` 异常增长 / 长流 read timeout
6. 做一次 worker 重启或完整服务 restart，确认旧 worker 退出后没有 lingering shared client / socket
7. 如果 `PoolTimeout` 仍出现，再按下列顺序调参：
   - 先提高 `FASTQA_LLM_HTTP_MAX_CONNECTIONS`
   - 再提高 `FASTQA_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS`
   - 最后才考虑增加 `FASTQA_LLM_HTTP_POOL_TIMEOUT_SECONDS`
8. 不要先把 `keepalive_expiry` 提到 `1h`

## Follow-Up Plan (Separate)

本计划完成后，下一份独立计划再做：

1. gateway 按 `conversation_id` 的会话粘性
2. admission / relay 维度的 worker 命中率观测
3. 是否需要 sidecar / egress proxy 级别的跨 worker 共享连接管理

在此之前，不要误把“per-worker 共享池”宣传成“整个服务的会话级共享池”。
