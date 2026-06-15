# highThinkingQA 重构审计文档

> 状态：已完成第一轮只读审计。本文档只记录基于代码阅读得到的证据，审计产物位于独立目录，不修改业务代码。

## 1. 审计范围

- 已阅读目录：`highThinkingQA/server_fastapi/`、`highThinkingQA/server/`、`highThinkingQA/agent_core/`、`highThinkingQA/tests/`。
- 已阅读关键文件：`server_fastapi/app.py`、`server_fastapi/routers/__init__.py`、`ask.py`、`health.py`、未注册 routers、`server/services/ask_service.py`、`chat_persistence.py`、`conversation_context_service.py`、`agent_core/openai_compat.py`、`llm_client.py`、`upstream_auth_logging.py`、`config.py`、`env_loader.py`。
- 未覆盖或需要本地进一步验证的范围：未运行测试；retired router 对应 service/schema/storage 是否仍被脚本或测试直接 import 需要进一步引用清单。

## 2. 当前 live path

### 2.1 服务入口

- app factory / main entry：`highThinkingQA/server_fastapi/app.py:create_app()`。
- router 注册位置：`server_fastapi/routers/__init__.py:register_routers()` 只注册 `health_router` 和 `ask_router`。
- lifespan/startup/shutdown：没有 FastAPI lifespan；`create_app()` 内配置 logging、`app.state.config`、Redis state、ask semaphore、CORS、trace middleware。

关键证据：

```python
from server_fastapi.routers.ask import router as ask_router
from server_fastapi.routers.health import router as health_router

def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(ask_router)
```

```python
app.state.config = {
    "ASK_STREAM_MAX_CONCURRENT": settings.ask_stream_max_concurrent,
    "ASK_TIMEOUT_SECONDS": settings.ask_timeout_seconds,
    "SSE_HEARTBEAT_SECONDS": settings.sse_heartbeat_seconds,
    "chat_persistence_enabled": settings.chat_persist_enabled,
}
app.state.component_status = {}
app.state.redis_service = None
app.state.ask_slots = threading.BoundedSemaphore(...)
```

### 2.2 对外接口路径

| 接口路径 | 方法 | 所在文件 | 当前职责 | 是否 active |
|---|---|---|---|---|
| `/api/v1/health` | GET | `server_fastapi/routers/health.py` | health | active live path |
| `/api/health` | GET | `server_fastapi/routers/health.py` | health legacy alias | active live path |
| `/health` | GET | 无 | 未注册 | deprecated and unregistered / absent |
| `/api/v1/ask` | POST | `server_fastapi/routers/ask.py` | sync ask | active live path |
| `/api/ask` | POST | `server_fastapi/routers/ask.py` | sync ask alias | active live path |
| `/api/v1/{mode}/ask` | POST | `server_fastapi/routers/ask.py` | mode sync ask | active live path |
| `/api/{mode}/ask` | POST | `server_fastapi/routers/ask.py` | mode sync ask | active live path |
| `/api/v1/thinking/ask` | POST | `server_fastapi/routers/ask.py` | thinking sync ask via `{mode}` | active live path |
| `/api/thinking/ask` | POST | `server_fastapi/routers/ask.py` | thinking sync ask via `{mode}` | active live path |
| `/api/v1/ask_stream` | POST | `server_fastapi/routers/ask.py` | SSE ask | active live path |
| `/api/ask_stream` | POST | `server_fastapi/routers/ask.py` | SSE ask alias | active live path |
| `/api/v1/{mode}/ask_stream` | POST | `server_fastapi/routers/ask.py` | mode SSE ask | active live path |
| `/api/{mode}/ask_stream` | POST | `server_fastapi/routers/ask.py` | mode SSE ask | active live path |
| `/api/v1/thinking/ask_stream` | POST | `server_fastapi/routers/ask.py` | thinking SSE ask via `{mode}` | active live path |
| `/api/thinking/ask_stream` | POST | `server_fastapi/routers/ask.py` | thinking SSE ask via `{mode}` | active live path |
| `/api/v1/conversations*`, `/api/conversations*` | mixed | `server_fastapi/routers/conversation.py` | retired conversation API | deprecated and unregistered |
| `/api/v1/upload_pdf`, `/api/upload_pdf`, `/upload_pdf` | POST | `server_fastapi/routers/upload.py` | retired upload | deprecated and unregistered |
| `/api/admin/users*` | mixed | `server_fastapi/routers/admin.py` | retired admin | deprecated and unregistered |
| `/api/v1/auth/*`, `/api/auth/*` | mixed | `server_fastapi/routers/auth.py` | retired auth | deprecated and unregistered |
| `/api/v1/view_pdf/{doi:path}` and document paths | mixed | `server_fastapi/routers/documents.py` | retired documents | deprecated and unregistered |
| `/api/v1/ingest*`, `/api/ingest*` | mixed | `server_fastapi/routers/ingest.py` | retired ingest | deprecated and unregistered |
| `/api/v1/quota*`, `/api/quota*` | mixed | `server_fastapi/routers/quota.py` | retired quota | deprecated and unregistered |
| `/api/v1/kb_info`, `/api/kb_info` | GET | `server_fastapi/routers/system.py` | retired system | deprecated and unregistered |

接口注册证据：

```python
@router.post("/api/v1/ask")
@router.post("/api/ask")
...
@router.post("/api/v1/{mode}/ask")
@router.post("/api/{mode}/ask")
...
@router.post("/api/v1/ask_stream")
@router.post("/api/ask_stream")
...
@router.post("/api/v1/{mode}/ask_stream")
@router.post("/api/{mode}/ask_stream")
```

### 2.3 核心调用链

```text
gateway -> highThinkingQA /api/thinking/ask_stream
  -> ask.py auth/header/persistence policy/slot/SSE wrapper
  -> ask_service.stream_ask_events()
  -> _prepare_execution() context + rewrite
  -> _run_agent_for_profile() / agent_core.graph
  -> event mapper formats Chinese stage messages + references
  -> ask.py encodes SSE and optionally service-owned persistence
```

## 3. 发现的重构点

### R-001：Router surface 已收缩，但遗留 router 仍在代码闭包内

- 严重程度：P1
- 类型：遗留代码 / boundary cleanup
- 代码位置：
  - `highThinkingQA/server_fastapi/routers/__init__.py`
  - `conversation.py`
  - `upload.py`
  - `admin.py`
  - `auth.py`
  - `documents.py`
  - `ingest.py`
  - `quota.py`
  - `system.py`
- 接口路径：
  - live：ask/health
  - unregistered：conversation/upload/admin/auth/documents/ingest/quota/system 全部路径
- 关键代码片段：

```python
def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(ask_router)
```

- 当前问题：HTTP surface 已最小化，但代码库保留大量 retired HTTP closure，增加误注册、测试噪音和维护成本。
- 建议重构方式：将未注册 router 和仅服务这些 router 的 schema/service 标记下线清单，分阶段移除或归档。
- 是否可抽共享包：否。
- 建议目标模块：public-service/gateway 拥有公共 API；highThinkingQA 只保留 thinking execution。
- 设计模式建议：Strangler fig cleanup、explicit boundary。
- 影响范围：FastAPI route surface、旧迁移测试、admin/auth/document/upload/quota/ingest/system 服务。
- 风险：中。仍有测试或脚本直接 import 旧模块时会失败。
- 测试计划：保留 `tests/fastapi_migration/test_fastapi_route_surface_minimal.py`，新增旧路径 404 contract。
- 是否可立即删除：不建议一次性删除全部。
- 删除或迁移前置条件：gateway/public-service 已覆盖对应 API，旧迁移 contract 不再直接 import retired routers。

### R-002：`ask.py` 混合 HTTP、SSE、persistence、gateway 协议、线程与断连

- 严重程度：P1
- 类型：巨型 router / responsibility overload
- 代码位置：
  - `highThinkingQA/server_fastapi/routers/ask.py`
  - `_gateway_owned_persistence()`
  - `_start_sync_stream_producer()`
  - `_build_stream_response()`
- 接口路径：
  - `/api/v1/ask`
  - `/api/ask`
  - `/api/v1/thinking/ask`
  - `/api/thinking/ask`
  - `/api/v1/ask_stream`
  - `/api/v1/thinking/ask_stream`
- 关键代码片段：

```python
def _gateway_owned_persistence(request: Request) -> bool:
    expected_token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()
    return (
        _header_truthy(_request_header(request, "X-Gateway-Task-Execution"))
        and _header_truthy(_request_header(request, "X-Gateway-Owned-Persistence"))
        and internal_service_name == "gateway"
        and internal_service_token == expected_token
    )
```

```python
def _start_sync_stream_producer(...):
    thread = threading.Thread(target=_run, daemon=True, name="thinkingqa-stream-producer")
    thread.start()
    return thread
```

- 当前问题：router 不只是 HTTP adapter，还承担执行并发控制、persistence 策略、SSE framing、summary accumulation、disconnect cancellation、thread bridge。
- 建议重构方式：拆 `ask_routes.py`、`ask_sse_adapter.py`、`ask_persistence_policy.py`、`ask_cancellation.py`。
- 是否可抽共享包：SSE framing/error envelope/cancellation adapter 可抽到 shared backend utilities。
- 建议目标模块：`server_fastapi/ask_routes.py`、`server/services/ask_stream_adapter.py`、`server/services/ask_persistence_policy.py`。
- 设计模式建议：Hexagonal adapter、thin controller。
- 影响范围：ask/ask_stream live path、summary persistence tests。
- 风险：高。SSE 首包、断连、terminal persistence 时序容易回归。
- 测试计划：normal done、error、timeout、disconnect、gateway-owned skip persistence、public headers without internal auth still persist。
- 是否可立即删除：否。
- 删除或迁移前置条件：冻结 SSE event contract 和 persistence contract。

### R-003：gateway-owned persistence 与 service-owned persistence 双模式并存

- 严重程度：P1
- 类型：persistence ownership split / 边界不纯
- 代码位置：
  - `highThinkingQA/server_fastapi/routers/ask.py`
  - `server/services/chat_persistence.py`
  - `server/services/conversation_authority_client.py`
  - `highThinkingQA/README.md`
- 接口路径：
  - ask/ask_stream live path
- 关键代码片段：

```python
target = str(getattr(config, "CONVERSATION_EXECUTION_CONTEXT_READ_TARGET", "legacy") or "legacy").strip().lower()
if target == "legacy":
    return _load_legacy_context(...)
if target == "shadow_public_service":
    local_result = _load_legacy_context(...)
    _get_authority_client().read_context_snapshot(...)
    return local_result
snapshot = _get_authority_client().read_context_snapshot(...)
```

```markdown
- thinking-mode QA execution only
- independent backend process
- no long-term ownership of public auth/conversation/upload/document truth data
```

- 当前问题：thinking 后端既能跳过 persistence 让 gateway owning，又能自己写 public-service/legacy persistence；与 README 的“只做 thinking execution”目标不一致。
- 建议重构方式：目标收缩为纯 thinking QA 执行后端，只读取 execution context、执行 agent、返回 stream/terminal event；conversation truth 由 gateway/public-service 管理。
- 是否可抽共享包：public-service internal client contract 可抽 shared internal client。
- 建议目标模块：public-service owning persistence；highThinkingQA 仅保留 `conversation_context_client.py`。
- 设计模式建议：Single Writer、Authority Service。
- 影响范围：conversation summary、multi-turn context、assistant terminal event。
- 风险：高。下线 legacy/shadow 前若 gateway 未 100% owning persistence，会导致消息丢失。
- 测试计划：gateway-owned 不写本地；public-service authority read/write；断连 terminal event。
- 是否可立即删除：否。
- 删除或迁移前置条件：gateway 100% owning persistence，public-service internal APIs 稳定，移除 legacy/shadow rollout 需求。

### R-004：`ask_service.py` 混合执行准备、agent 调用、引用构造、前端阶段文案

- 严重程度：P1
- 类型：巨型 service / presentation leakage
- 代码位置：
  - `highThinkingQA/server/services/ask_service.py`
  - `_format_frontend_step_message()`
  - `_build_reference_links()`
  - `_prepare_execution()`
  - `_run_agent_for_profile()`
  - `stream_ask_events()`
- 接口路径：
  - ask/ask_stream live path
- 关键代码片段：

```python
def _format_frontend_step_message(stage: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_message = str(payload.get("message") or stage or "处理中").strip() or "处理中"

    if stage == "step1":
        if "等待直接回答" in raw_message:
            return "阶段1：子问题处理完成，等待直接回答收尾", data
        return "阶段1：开始执行直接回答与查询分解", data
```

- 当前问题：execution service 同时处理 UI 文案、引用 URL、agent executor、question rewrite、SSE event 生成。
- 建议重构方式：拆 execution orchestrator、reference mapper、event mapper、frontend adapter。
- 是否可抽共享包：reference DOI normalization/link mapping、event schema mapper 可抽。
- 建议目标模块：`server/services/thinking_execution.py`、`reference_mapper.py`、`event_mapper.py`。
- 设计模式建议：Application Service + Mapper。
- 影响范围：final answer、done event、metadata、step event、reference payload。
- 风险：高。前端可能依赖中文 `message` 或 `/api/v1/view_pdf/{doi}`。
- 测试计划：snapshot SSE events、reference payload contract、non-stream/stream done parity。
- 是否可立即删除：否。
- 删除或迁移前置条件：明确 event schema 中哪些字段由后端生成，哪些由前端 mapper 生成。

### R-005：后端包含前端 UI 中文阶段文案

- 严重程度：P2
- 类型：presentation leakage
- 代码位置：
  - `highThinkingQA/server/services/ask_service.py`
  - `_format_frontend_step_message()`
- 接口路径：
  - `/api/v1/ask_stream`
  - `/api/v1/thinking/ask_stream`
- 关键代码片段：

```python
return "阶段2：子问题预回答全部完成，开始等待检索结果", data
return f"阶段3：文献检索：已完成 {completed_batches_int}/{total_batches_int} 批", data
return "阶段4：综合草稿开始流式输出", data
return "阶段5A：开始引用检查", data
```

- 当前问题：后端绑定中文 UI 呈现，阻碍国际化/前端样式变更，也让 execution service 依赖展示语义。
- 建议重构方式：后端只发稳定 `stage/status/data`；前端或 gateway event mapper 负责中文文案。
- 是否可抽共享包：可抽 event code registry；UI 文案不宜放后端 execution。
- 建议目标模块：`frontend-vue` event mapper 或 gateway event mapper。
- 设计模式建议：Event Code + Presentation Mapper。
- 影响范围：SSE step message、前端进度条。
- 风险：中。前端当前可能直接展示 `message`。
- 测试计划：前端 mapper snapshot；后端只断言 event code/data。
- 是否可立即删除：否。
- 删除或迁移前置条件：前端已支持基于 `step/status/data` 渲染文案。

### R-006：`openai_compat.py` 重复实现 OpenAI-compatible client，`max_retries` 未生效

- 严重程度：P2
- 类型：共享 LLM / misleading parameter
- 代码位置：
  - `highThinkingQA/agent_core/openai_compat.py`
  - `highThinkingQA/agent_core/llm_client.py`
- 接口路径：
  - 内部 LLM/embedding upstream
- 关键代码片段：

```python
class OpenAICompatibleChatClient:
    def __init__(..., max_retries: int | None = None) -> None:
        del max_retries
        self.endpoint = normalize_openai_compatible_endpoint(base_url)
        self._client = http_client or httpx.Client(timeout=float(timeout_seconds), http2=False)
```

- 当前问题：调用层传 `max_retries`，底层直接删除；行为与参数名不一致。该 client 还自建 sync/async chat、stream、embedding、SSE parser、logging。
- 建议重构方式：接入统一 OpenAI-compatible adapter，或实现真实 retry policy；删除无效参数或显式文档说明。
- 是否可抽共享包：是。
- 建议目标模块：`packages/agent_common/llm/openai_compatible.py`、`retry_policy.py`。
- 设计模式建议：Adapter + Retry Policy。
- 影响范围：LLM 调用、embedding 调用、auth headers、tests。
- 风险：中。改 retry 会改变 upstream 压力和失败语义。
- 测试计划：timeout/retry/status code/stream parser/auth mode 单测。
- 是否可立即删除：否。
- 删除或迁移前置条件：选定统一 model client，确认 qwen/dashscope 兼容参数。

### R-007：`config.py` 存在 typed settings 与全局常量双轨配置

- 严重程度：P2
- 类型：重复配置
- 代码位置：
  - `highThinkingQA/config.py`
- 接口路径：
  - 全服务
- 关键代码片段：

```python
SETTINGS = get_runtime_settings()
HTTP_SETTINGS = get_http_service_settings()
...
LLM_BASE_URL = SETTINGS.llm_base_url
LLM_MODEL = SETTINGS.llm_model
...
ASK_STREAM_MAX_CONCURRENT = HTTP_SETTINGS.ask_stream_max_concurrent
CONVERSATION_EXECUTION_AUTHORITY_TARGET = CONVERSATION_ROLLOUT_SETTINGS.execution_authority_target
```

- 当前问题：新代码可能用 `SETTINGS`，旧代码用全局常量；测试 monkeypatch 全局常量会绕过 settings 来源。
- 建议重构方式：逐步统一为 typed settings object；全局常量作为 deprecated compatibility facade。
- 是否可抽共享包：是，env/settings loader 可抽。
- 建议目标模块：`packages/agent_common/config/` + highThinkingQA-specific settings。
- 设计模式建议：Typed Configuration、Compatibility Facade。
- 影响范围：agent_core、ingest、retriever、server_fastapi。
- 风险：中。直接删除全局常量会破坏大量 imports。
- 测试计划：config defaults、env override、service root resolution、conversation rollout 单测。
- 是否可立即删除：否。
- 删除或迁移前置条件：完成调用点迁移，保留兼容 facade。

### R-008：`env_loader.py` 是 service-local 复制型基础能力

- 严重程度：P2
- 类型：共享配置 / bootstrap duplication
- 代码位置：
  - `highThinkingQA/env_loader.py`
- 接口路径：
  - 服务启动/config load
- 关键代码片段：

```python
SERVICE_CODE = "HIGHTHINKINGQA"
...
def resolve_resource_root(...) -> Path: ...
def resolve_service_root(...) -> Path: ...
def load_workspace_env(...) -> None: ...
```

- 当前问题：env/root resolution 是 monorepo 通用能力，却内嵌 service code；fastQA/patent/public-service 有类似配置加载需求。
- 建议重构方式：抽参数化 shared env loader：`load_service_env(service_name, service_code)`。
- 是否可抽共享包：是。
- 建议目标模块：`packages/agent_common/config/env_loader.py`、`service_roots.py`。
- 设计模式建议：Parameterized Service Bootstrap。
- 影响范围：`config.py`、package init、worker scripts。
- 风险：中。env 文件优先级变化会影响部署。
- 测试计划：复用 `tests/test_env_loader.py` 作为 shared package contract。
- 是否可立即删除：否。
- 删除或迁移前置条件：其他服务 env loader 对齐后再替换。

## 4. 可抽共享能力清单

| 能力 | 当前重复位置 | 建议共享模块 | 迁移优先级 |
| -- | ------ | ------ | ----- |
| OpenAI-compatible chat/stream/embedding client | `agent_core/openai_compat.py`、fastQA/patent 同类实现 | `packages/agent_common/llm/openai_compatible.py` | P1 |
| upstream auth logging | `agent_core/upstream_auth_logging.py` | `packages/agent_common/llm/upstream_auth_logger.py` | P2 |
| env/resource root loader | `env_loader.py` | `packages/agent_common/config/env_loader.py` | P2 |
| typed settings + legacy facade | `config.py` | `packages/agent_common/config/` | P2 |
| conversation authority internal client | `server/services/conversation_authority_client.py` | `packages/agent_common/clients/conversation_authority.py` | P1 |
| SSE framing/error envelope | `server_fastapi/routers/ask.py` | `packages/agent_common/sse/` | P1 |
| DOI/reference mapping | `server/services/ask_service.py` | `packages/agent_common/files/references.py` | P2 |
| stage event schema mapper | `server/services/ask_service.py` | `packages/agent_common/contracts/stream_event.py` + frontend mapper | P2 |

## 5. 可清理遗留代码清单

| 代码位置 | 当前状态 | 是否注册 | 是否被引用 | 建议处理 |
| ---- | ---- | ---- | ----- | ---- |
| `server_fastapi/routers/ask.py` | active live path | 是 | 是 | 保留 |
| `server_fastapi/routers/health.py` | active live path | 是 | 是 | 保留 |
| `/health` | deprecated and unregistered / absent | 否 | unknown | 若 gateway 需要，增加 proxy alias；否则文档明确不存在 |
| `server_fastapi/routers/conversation.py` | deprecated and unregistered | 否 | tests/旧模块可能 import | 分阶段归档 |
| `server_fastapi/routers/upload.py` | deprecated and unregistered | 否 | tests/旧模块可能 import | 分阶段归档 |
| `server_fastapi/routers/admin.py` | deprecated and unregistered | 否 | tests/旧模块可能 import | 分阶段归档 |
| `server_fastapi/routers/auth.py` | deprecated and unregistered | 否 | tests/旧模块可能 import | 分阶段归档 |
| `server_fastapi/routers/documents.py` | deprecated and unregistered | 否 | tests/旧模块可能 import | 分阶段归档 |
| `server_fastapi/routers/ingest.py` | deprecated and unregistered | 否 | tests/旧模块可能 import | 分阶段归档 |
| `server_fastapi/routers/quota.py` | deprecated and unregistered | 否 | tests/旧模块可能 import | 分阶段归档 |
| `server_fastapi/routers/system.py` | deprecated and unregistered | 否 | tests/旧模块可能 import | 分阶段归档 |
| `server/services/chat_persistence.py` legacy paths | deprecated but still referenced | 不适用 | 是，配置可选 | 移除前确认 rollout target |
| `archive/root-highthinking-legacy-2026-03-23/` | archive / historical baseline | 否 | 文档引用 | 不进入重构主线 |

## 6. 接口与契约风险

- gateway -> backend contract：thinking 后端接受 `{mode}` paths，但需确认 gateway 只走 `/api/thinking/*` 或 `/api/v1/thinking/*`。
- frontend -> gateway contract：未注册 `/health`，若前端/ops 期望 `/health` 会 404。
- backend -> public-service contract：conversation authority read/write 仍在 highThinkingQA，目标应是只读 execution context + stream result。
- internal token/auth headers：gateway-owned persistence 依赖 `X-Gateway-*` 和 internal token，需共享 header contract。
- SSE event schema：后端直接发中文 `message`，前端可能展示；迁移需保留 event code/data。
- task event schema：gateway task-owned persistence 与 service-owned persistence 双模式会影响 terminal event 落点。

## 7. 测试计划

- 单元测试：`ask_service` event mapper、reference mapper、config/env loader、OpenAI compat retry/timeout。
- contract test：active route surface、removed routes 404、gateway-owned persistence headers。
- stream/SSE test：first event、done/error、disconnect、timeout、summary accumulation。
- integration smoke test：gateway -> highThinkingQA ask_stream。
- backward compatibility test：`/api/ask`、`/api/v1/ask`、`/api/thinking/ask_stream`。
- failure/cancel/retry test：slot exhaustion、client disconnect、thread producer cancel、LLM failure。
- persistence test：gateway-owned skip service persistence、public-service authority target、legacy/shadow modes。
- quota/auth test：auth context headers where applicable；quota should stay gateway/public-service owned。
- file route test：不适用当前 thinking live path；retired upload/doc routes 只做 404。

## 8. 建议重构顺序

1. P1：先冻结 active route surface 和 removed route 404。
2. P1：拆 `ask.py` 的 SSE/persistence/cancellation adapter，router 保持 decorator。
3. P1：收敛 persistence ownership，默认 gateway/public-service owning，legacy/shadow 标为迁移期。
4. P1：拆 `ask_service.py` execution/reference/event mapper。
5. P2：把中文阶段文案迁到 frontend/gateway mapper。
6. P2：抽 OpenAI-compatible client/retry/auth logging。
7. P2：统一 `config.py` typed settings，保留常量 facade。
8. P3：删除或归档 unregistered retired routers 和其专属服务。

## 9. 需要进一步确认的问题

1. gateway 当前是否 100% 使用 `/api/v1/thinking/ask(_stream)` 或 `/api/thinking/ask(_stream)`，而不是 `/api/v1/ask(_stream)`。
2. `/health` 是否由 gateway 另行映射；当前 highThinkingQA FastAPI 未注册。
3. 前端是否直接展示 SSE `message` 中文文案。
4. public-service 是否已完全拥有 auth/conversation/upload/document/quota/system API 和数据真相。
5. `legacy` / `shadow_public_service` rollout 是否仍在任何部署环境启用。
6. `max_retries` 应实现 retry 还是删除参数。
7. retired router 对应 service/schema/storage 是否仍被测试或脚本直接 import。

## 10. 第二轮深度补充

> 状态：第二轮只读审计补充。已执行用户指定的 8 组只读命令，并继续用 `nl -ba ... | sed -n` 与 `rg -n` 做行号级验证。未运行测试、构建、服务启动或任何会写入仓库的命令。以下结论只基于代码、测试与脚本证据，不基于 README 单独推断。

### 10.1 必跑命令结果摘要

已执行命令：

```text
find highThinkingQA -type f
find highThinkingQA -type f \( -name "*.py" -o -name "*.js" -o -name "*.vue" \) -print0 | xargs -0 wc -l | sort -nr | head -50
rg "APIRouter|@router|app.include_router|path:|fetch|axios|EventSource" highThinkingQA
rg "deprecated|legacy|fallback|scaffold|placeholder|NOT_READY|not ready|shim|compat|TODO|FIXME|shadow|archive|obsolete|retired|rollout" highThinkingQA
rg "app\.state|request\.app\.state" highThinkingQA
rg "LLM_|EMBEDDING_|RERANK|REDIS|MINIO|NEO4J|VECTOR_DB|AUTH|TOKEN|RESOURCE_ROOT|RUNTIME_ROOT|STATE_ROOT" highThinkingQA
rg "OpenAI|openai|embedding|rerank|auth_headers|httpx|stream|SSE|api_key|Bearer" highThinkingQA
rg "requested_mode|actual_mode|source_scope|turn_mode|execution_files|selected_file_ids|primary_file_id|gateway-owned|X-Gateway" highThinkingQA
```

关键结果：

- `find highThinkingQA -type f` 显示被审计目录包含 `server_fastapi/`、`server/`、`agent_core/`、`ingest/`、`retriever/`、`prompts/`、`tests/`、`config.py`、`env_loader.py`、`README.md`，另有 `.pytest_cache/`、`__pycache__/`、`.runtime/`、`resource/runtime/.../logs`、`data/conversations/7/225.json`、`vectordb/chroma.sqlite3` 等运行产物。
- `wc -l` 前 10：`agent_core/graph.py` 1299、`tests/test_ask_service_executor.py` 1257、`server/services/conversation/conversation_service.py` 1247、`server/services/ask_service.py` 1081、`tests/test_ask_router_summary_persistence.py` 811、`server/services/chat_persistence.py` 786、`server_fastapi/routers/ask.py` 745、`agent_core/openai_compat.py` 690、`tests/test_run_agent_overlap.py` 596、`tests/test_conversation_mysql_alignment.py` 596。
- router 搜索显示 `server_fastapi/routers/__init__.py:10-11` 只 include `health_router` 和 `ask_router`；未注册 routers 仍有 decorators。
- deprecated/legacy 搜索显示未注册 routers 文件头部均有 Deprecated 注释；`chat_persistence.py` 仍有 legacy/shadow/public_service 三种 authority target；`openai_compat.py` 属于 compat client；`config.py` 有 rollout 与 legacy path resolution。
- `app.state` 搜索显示 `app.py:41-63` 负责 config/component_status/redis/ask_slots；`ask.py:53-60,162-167,412,424,534-535` 直接读 app state；`upload.py` 虽未注册但仍读 `request.app.state.config`。

### 10.2 第一轮结论复核

第一轮结论整体成立，但第二轮将几个“待进一步验证”项改为明确判断：

- Router surface 复核：成立。`server_fastapi/routers/__init__.py:5-11` 只导入并注册 ask/health；`server_fastapi/app.py:88-89` 调用 `register_exception_handlers(app)` 与 `register_routers(app)`；`app.py:91-103` 根路径只公布 health/ask/ask_stream。
- 未注册 router 引用复核：`rg` 未发现 `server_fastapi.routers.conversation|upload|admin|auth|documents|ingest|quota|system` 被 `register_routers`、脚本或服务直接 import；迁移测试显式断言这些路径 404，例如 `tests/fastapi_migration/test_fastapi_route_surface_minimal.py:16-35`。
- 文档/PDF 路由复核：`documents.py` 未注册，但 `/api/v1/view_pdf/{doi}` 仍是 ask/reference 输出契约字符串，见 `ask_service.py:355-365` 与测试 `test_fastapi_ask_contract.py:61-63,114-116`、`test_ask_service_executor.py:412-417`。
- `ask.py` 职责过宽复核：成立且更严重。它不仅做 HTTP/SSE，还做 gateway-owned trust、service-owned persistence、终态失败落库、thread producer、disconnect cancel 与 error mapping。
- `ask_service.py` 职责过宽复核：成立。它同时处理 mode profile、conversation context、question rewrite、runtime resource snapshot、agent executor、frontend 中文 step 文案、DOI adapter、reference objects、done event、sync/stream 双路径。
- `openai_compat.py` retry 问题复核：明确成立。`OpenAICompatibleChatClient.__init__`、`AsyncOpenAICompatibleChatClient.__init__`、`OpenAICompatibleEmbeddingClient.__init__` 分别在 `openai_compat.py:361-364,493-496,625-628` 直接 `del max_retries`。
- `config.py` 双轨复核：成立。`config.py:170-245` 定义 typed dataclasses，`config.py:390-467` 再导出全局常量 facade。
- `env_loader.py` service-local 复制能力复核：成立。`env_loader.py:14-18` 固定 `SERVICE_CODE/SERVICE_NAME/WORKSPACE_DIR/DEFAULT_ENV_FILENAMES`，`env_loader.py:70-91` 内嵌 CONFIG/STATE/RUNTIME/ASSET root 解析。

### 10.3 Router 注册与废弃验证

注册证据：

```python
# highThinkingQA/server_fastapi/routers/__init__.py:5-11
from server_fastapi.routers.ask import router as ask_router
from server_fastapi.routers.health import router as health_router

def register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(ask_router)
```

```python
# highThinkingQA/server_fastapi/app.py:88-103
register_exception_handlers(app)
register_routers(app)

@app.get("/")
async def _index():
    return JSONResponse(content={"endpoints": [
        "/api/v1/health",
        "/api/v1/ask",
        "/api/v1/ask_stream",
    ]})
```

状态表：

| router | 文件 | 当前判断 | 注册证据 | import/test/script 引用验证 |
|---|---|---|---|---|
| health | `server_fastapi/routers/health.py` | active | `__init__.py:6,10` | `test_fastapi_route_surface_minimal.py:10` 断言存在 |
| ask | `server_fastapi/routers/ask.py` | active | `__init__.py:5,11` | `test_fastapi_ask_contract.py` 与 `test_ask_router_summary_persistence.py` 覆盖 |
| conversation | `server_fastapi/routers/conversation.py` | deprecated unregistered | 未被 include；文件头 `conversation.py:1-4` Deprecated | `rg` 未发现 import；`test_fastapi_route_surface_minimal.py:18,31` 断言 404 |
| upload | `server_fastapi/routers/upload.py` | deprecated unregistered | 未被 include；文件头 `upload.py:1-4` Deprecated | `rg` 未发现 import；`test_fastapi_route_surface_minimal.py:16-17,30` 断言 404 |
| admin | `server_fastapi/routers/admin.py` | deprecated unregistered | 未被 include；文件头 `admin.py:1-4` Deprecated | `rg` 未发现 import；`test_fastapi_admin_contract.py:10-12` 断言 404 |
| auth | `server_fastapi/routers/auth.py` | deprecated unregistered | 未被 include；文件头 `auth.py:1-4` Deprecated | `rg` 未发现 import；`test_fastapi_auth_contract.py:10-11` 断言 404 |
| documents | `server_fastapi/routers/documents.py` | deprecated unregistered, but referenced as link contract | 未被 include；文件头 `documents.py:1-4` Deprecated | router 未 import；`ask_service.py:364` 与多处测试仍生成 `/api/v1/view_pdf/...` |
| ingest | `server_fastapi/routers/ingest.py` | deprecated unregistered | 未被 include；文件头 `ingest.py:1-4` Deprecated | `rg` 未发现 import；`test_fastapi_low_risk_routes.py:25` 断言 404 |
| quota | `server_fastapi/routers/quota.py` | deprecated unregistered | 未被 include；文件头 `quota.py:1-4` Deprecated | `rg` 未发现 import；`test_fastapi_quota_contract.py:10-12` 断言 404 |
| system | `server_fastapi/routers/system.py` | deprecated unregistered | 未被 include；文件头 `system.py:1-4` Deprecated | `rg` 未发现 import；`test_fastapi_documents_contract.py:10` 断言 `/api/v1/kb_info` 404 |

结论：未注册 routers 是“代码仍在、HTTP surface 已退役”。删除前置条件不是“没有注册”，而是确认 public-service/gateway 已拥有对应契约，并迁移或替换仍由 ask 输出的 PDF link contract。

### 10.4 ask.py 深挖

| 维度 | 代码证据 | 判断 | 目标归属 |
|---|---|---|---|
| HTTP path | `ask.py:626-745` 注册 `/api/v1/ask`、`/api/ask`、`/api/v1/{mode}/ask`、`/api/{mode}/ask`、`/api/v1/ask_stream`、`/api/ask_stream`、`/api/v1/{mode}/ask_stream`、`/api/{mode}/ask_stream` | active | router 保留薄入口 |
| ask slot | `ask.py:53-72` 非阻塞 acquire/release；`app.py:59-61` 初始化 `BoundedSemaphore` | active capacity control | `StreamController` 或 shared concurrency guard |
| SSE response | `ask.py:75-79,433-623` 编码 `data: JSON\n\n`，加 seq/ts/header | active stream transport | `StreamController/SSEEncoder` |
| sync response | `ask.py:626-721` 调 `execute_ask` 后返回 `{"success": true, "data": ...}` | active | router + `ThinkingAskRunner` |
| gateway headers | `ask.py:184-195` 校验 `X-Gateway-Task-Execution`、`X-Gateway-Owned-Persistence`、`X-Internal-Service-Name=gateway`、`X-Internal-Service-Token` | active trust decision | gateway/public-service contract + `PersistenceAdapter` |
| gateway-owned persistence | `ask.py:215-217,237-239,330-331` gateway-owned 时跳过本地 user/assistant/terminal persistence | active | gateway owns persistence；后端只执行 |
| service-owned persistence | `ask.py:224-234,252-261,344-359` 调 `chat_persistence` | migration mode | `PersistenceAdapter` until removed |
| user message persistence | `ask.py:215-234,632-633,683-684,730,744` sync/stream 执行前写 user turn | active if not gateway-owned | public-service authority |
| assistant summary persistence | `ask.py:237-261,655-667,706-718` sync done 与 stream done 后写 assistant summary | active if not gateway-owned | public-service authority |
| stream summary | `ask.py:441-496,562-584` 收集 content/metadata/step/done | active | `StreamSummaryCollector` |
| disconnect/cancel | `ask.py:390-429,543-556,600-613` request disconnect 设置 cancel_event，join producer，release slot | active | `StreamController` |
| thread producer | `ask.py:364-387,531-547` 同步 generator 通过 daemon thread 桥接 async StreamingResponse | active | `SyncToAsyncStreamBridge` |
| error mapping | `ask.py:82-129,284-319,591-599,640-654,691-705` service errors 映射 HTTP/SSE/terminal failure | active | `ErrorMapper` |

拆分边界：

- 进 `StreamController`：slot、SSE encode、sync generator bridge、heartbeat passthrough、disconnect monitor、cancel_event lifecycle、producer join。
- 进 `PersistenceAdapter`：gateway-owned predicate、user turn write、assistant done/failed/canceled terminal write、summary collector 到 persistence payload 的转换。
- 进 gateway/public-service：`X-Gateway-*` header contract、terminal persistence authority、conversation context snapshot authority、PDF/document link resolution authority。
- 保留在 router：decorators、auth dependency、request parse、thin call into controller。

### 10.5 ask_service 深挖

核心链路：

```text
ask.py -> execute_ask()/stream_ask_events()
  -> resolve_profile(mode)
  -> build/sanitize conversation context
  -> rewrite_question()
  -> runtime_resource_snapshot logging
  -> _run_agent_for_profile() -> agent_core.graph.run_agent()
  -> frontend step/content/done/error event mapping
  -> reference DOI/link/object/location extraction
```

代码证据：

- mode profile：`mode_profiles.py:18-43` 定义 fast/thinking/patent，`patent` 为 `implemented=False`；`ask_service.py:568-575` 将未实现映射为 `ModeNotImplementedError`。
- conversation context：`ask_service.py:597-612` 调 `build_conversation_context` 和 `sanitize_conversation_context`；`conversation_context_service.py:149-168` 从 `chat_persistence.load_conversation_context` 读取 server snapshot。
- question rewrite：`query_rewrite_service.py:133-172` 基于模糊指代与 anchor 生成 `effective_question`；`ask_service.py:599-611` 失败时 fallback 到 raw question。
- runtime snapshot：`ask_service.py:148-179` 日志读取 `PAPERS_DIR/CHROMA_PERSIST_DIR/CHROMA_COLLECTION_NAME` 并尝试 Chroma count。
- run_agent：`ask_service.py:578-594` 将 profile 参数传给 `agent_core.graph.run_agent`。
- frontend step event：`ask_service.py:193-348` 将内部 progress 映射为中文阶段文案。
- reference extraction/DOI links：`ask_service.py:351-365` 从答案提取 DOI 并生成 `/api/v1/view_pdf/{doi}`。
- reference_objects/doi_locations：`ask_service.py:387-481` 从 retrieved chunks 构造 evidence/title/page/section 等结构。
- done event：`ask_service.py:710-742` 输出 mode/requested/actual/route/turn_mode/final_answer/timings/references/link objects。
- sync vs stream：`execute_ask` 在 `ask_service.py:615-707` 同步等待 future；`stream_ask_events` 在 `ask_service.py:745-1081` 生产 metadata/preflight/content/heartbeat/error/done。

目标拆分：

- `ThinkingAskRunner`：`resolve_profile`、`_prepare_execution`、executor、timeout/cancel、`run_agent`。
- `ReferenceBuilder`：`_extract_references`、`_build_reference_links`、`_build_reference_objects`、`_build_doi_locations`、DOI normalize。
- `EventMapper`：metadata/done/error/content event schema、sync payload schema。
- `FrontendTextAdapter`：中文阶段文案和 DOI display adapter；长期建议把 display 文案移出 backend，backend 只发 stable code/data。

### 10.6 agent_core LLM 深挖

| 能力 | 代码证据 | 判断 |
|---|---|---|
| endpoint normalize | `openai_compat.py:17-40` chat/embedding base_url 归一到 `/v1/chat/completions` 与 `/v1/embeddings` | 可抽 shared |
| auth headers | `openai_compat.py:378-382,510-514,642-646` 调 `auth_headers/resolve_auth_mode` | 可抽 shared |
| API key validation | LLM chat client 不强制 key；`ingest/embedder.py:52-67` embedding 在 auth_mode != none 时强制 `HIGHTHINKINGQA_EMBEDDING_API_KEY` | 语义不一致，需明确 |
| max_retries 生效性 | `openai_compat.py:361-364,493-496,625-628` 直接 `del max_retries` | 未生效，确定问题 |
| sync chat | `openai_compat.py:384-429` `httpx.Client.post` 解析 message response | 可抽 shared |
| sync stream parser | `openai_compat.py:431-481` `client.stream` + `_iter_sse_payloads` | 可抽 shared |
| async chat/stream | `openai_compat.py:516-613` `httpx.AsyncClient` 实现 | 可抽 shared |
| embedding client | `openai_compat.py:616-690` sync embeddings | 可抽 shared，但 retry 外置在 embedder |
| upstream auth logging | `llm_client.py:132-154,210-232` 与 `upstream_auth_logging.py:57-117` 双层记录 auth success/failure | 可抽 shared |
| 与 fastQA/patent 重复 | 第一轮已列共享候选；第二轮未跨目录重读 fastQA/patent 源码，不能断言行级重复，只能依据同类能力命名与公共迁移目标判定“应对齐验证” | 未完全确认 |

### 10.7 config/env 深挖

- settings 与全局常量双轨：`RuntimeSettings/HttpServiceSettings/ConversationRolloutSettings/GunicornSettings` 在 `config.py:170-259`；实例化在 `config.py:390-393`；常量 facade 在 `config.py:395-467`。
- legacy env：`env_loader.py:27-32` 包含 `config.env`、`config.shared.env`、`config.secret.env`、`.env`；`config.env.example:1` 标注 Deprecated compatibility template；测试 `test_env_loader.py:124-175` 固化 legacy/shared/service layer 顺序。
- service/resource root：`env_loader.py:59-91` 解析 `RESOURCE_ROOT` 与 CONFIG/STATE/RUNTIME/ASSET；`config.py:27-31` 暴露 service root；scripts 通过 `HIGHTHINKINGQA_SERVICE_STATE_ROOT/RUNTIME_ROOT/ASSET_ROOT` 启动。
- LLM/embedding/retrieval/http 共享配置：`config.py:261-359` 将 LLM、embedding、VLM、chunk、Chroma、retrieval、cache、HTTP、CORS、persistence worker 混在一个 runtime/http settings 文件中。
- legacy alias 收敛：`test_config_runtime_defaults.py:94-172` 明确忽略 `OPENAI_*`、`DASHSCOPE_*`、共享 `EMBEDDING_*` 等旧 alias；`HIGHTHINKINGQA_EMBEDDING_*` 优先测试在 `test_config_runtime_defaults.py:46-91`。

### 10.8 Router/API 完整表

| 路径 | 方法 | 文件 | 入参模型/解析 | service | 外部依赖 | 持久化/鉴权/quota/SSE | 测试覆盖 |
|---|---|---|---|---|---|---|---|
| `/api/v1/health`, `/api/health` | GET | `routers/health.py` | Request | Redis component status | Redis state optional | no auth/no quota/no SSE | route surface tests |
| `/api/v1/ask`, `/api/ask` | POST | `routers/ask.py:626-670` | `parse_ask_request` + `AuthContext` | `execute_ask` | LLM/embedding/Chroma/Redis/public-service context | auth, ask slot, optional persistence, no SSE | `test_fastapi_ask_contract.py:40-88` |
| `/api/v1/{mode}/ask`, `/api/{mode}/ask` | POST | `routers/ask.py:673-721` | forced mode + body mismatch validation | `execute_ask` | 同上 | auth, ask slot, optional persistence | `test_fastapi_ask_contract.py:145-155` |
| `/api/v1/ask_stream`, `/api/ask_stream` | POST | `routers/ask.py:724-731` | `parse_ask_request` + `AuthContext` | `stream_ask_events` | LLM/embedding/Chroma/Redis/public-service context | auth, ask slot, SSE, optional persistence, cancel | `test_fastapi_ask_contract.py:90-143` |
| `/api/v1/{mode}/ask_stream`, `/api/{mode}/ask_stream` | POST | `routers/ask.py:734-745` | forced mode + body mismatch validation | `stream_ask_events` | 同上 | auth, ask slot, SSE, optional persistence, cancel | mode/error contract tests |
| `/api/v1/conversations*`, `/api/conversations*` | mixed | `routers/conversation.py:48-209` | body/query/path + `AuthContext` | `conversation_service` | DB/JSON/object storage via services | auth, persistence, file delivery | retired 404 tests |
| `/api/v1/upload_pdf`, `/api/upload_pdf`, `/upload_pdf` | POST | `routers/upload.py:161-207` | multipart file/form + `AuthContext` | `conversation_service`, `upload_service` | filesystem/MinIO | auth, upload metadata persistence | retired 404 tests |
| `/api/v1/upload_excel`, `/api/upload_excel`, `/upload_excel` | POST | `routers/upload.py:210-263` | multipart file/form + `AuthContext` | `conversation_service`, `upload_service` | filesystem/MinIO | auth, upload metadata persistence | route surface excludes upload_excel |
| `/api/admin/users*` | mixed | `routers/admin.py:35-149` | Pydantic admin schemas | admin user services | DB, xlsx import | admin auth | retired 404 tests |
| `/api/v1/auth/*`, `/api/auth/*` | mixed | `routers/auth.py:34-109` | Pydantic auth schemas | `auth_service` | DB/JWT/password hashing | auth for me/password/security | retired 404 tests |
| `/api/v1/view_pdf/{doi:path}`, `/api/view_pdf/{doi:path}` | GET/HEAD | `routers/documents.py:21-39` | doi path + optional auth | `documents_service` | paper storage/MinIO/filesystem | optional auth, file response | retired 404 tests; link contract tests still expect string |
| `/api/v1/translate`, `/api/translate` | POST | `routers/documents.py:42-51` | JSON texts + auth | `documents_service` | LLM | auth, no quota here | retired 404 tests |
| `/api/v1/summarize_pdf/{doi:path}`, `/api/summarize_pdf/{doi:path}` | POST | `routers/documents.py:54-58` | doi path + auth | `documents_service` | LLM/PDF text | auth | retired 404 tests |
| `/api/v1/extract_pdf_text/{doi:path}`, `/api/extract_pdf_text/{doi:path}` | GET | `routers/documents.py:61-65` | doi path | `documents_service` | PDF extractor/storage | no auth in retired router | no active route |
| `/api/v1/check_pdf/{doi:path}`, `/api/check_pdf/{doi:path}` | GET | `routers/documents.py:68-72` | doi path | `documents_service` | storage | no auth in retired router | no active route |
| `/api/v1/ingest`, `/api/ingest` | POST | `routers/ingest.py:36-42` | body dict | `ingest_service` | ingest pipeline/Chroma/embedding | no auth in retired router | retired 404 tests |
| `/api/v1/ingest/{job_id}`, `/api/ingest/{job_id}` | GET | `routers/ingest.py:45-51` | path job_id | `ingest_service` | job state | no auth in retired router | no active route |
| `/api/v1/quota*`, `/api/quota*` | mixed | `routers/quota.py:39-117` | quota schemas/path | `quota_service` | DB | auth/admin auth/quota management | retired 404 tests |
| `/api/v1/kb_info`, `/api/kb_info` | GET | `routers/system.py:21-25` | none | `system_service` | system config | no auth | retired 404 tests |
| `/api/v1/refresh_kb`, `/api/refresh_kb` | POST | `routers/system.py:28-32` | none | `system_service` | unsupported stub | no auth | no active route |
| `/api/v1/clear_cache`, `/api/clear_cache` | POST | `routers/system.py:35-39` | none | `system_service` | unsupported stub | no auth | no active route |

### 10.9 Legacy/deprecated/scaffold 引用验证

- Deprecated routers：conversation/upload/admin/auth/documents/ingest/quota/system 文件头均明确标注不再注册，且 `rg` 未发现它们被 router registration、scripts 或服务 import。
- Deprecated services/schemas：`server/services/admin_users_service.py`、`admin_users_import_service.py`、`quota_service.py`、`documents_service.py`、`ingest_service.py`、`system_service.py`、`server_fastapi/admin_schemas.py`、`quota_schemas.py`、`auth/schemas.py` 文件头均标注 retired HTTP surface 相关，但仍被对应未注册 router import。
- Legacy persistence：`chat_persistence.py:265-371` 明确 legacy local read/write；`chat_persistence.py:548-574,616-656,718-786` 仍根据 config target 选择 legacy/public_service/shadow。
- Shadow rollout：`chat_persistence.py:552-565,631-645,748-772` 有 shadow public-service 读/写路径；`config.py:33,104-126,363-372,454-458` 有 rollout target 和 overlay 开关。
- Placeholder/scaffold：`system_service.py` 返回 `refresh_kb_not_supported`、`clear_cache_not_supported`，但 router 未注册。未发现 `NOT_READY` 命中。
- Runtime产物：`find` 显示 `.pytest_cache/`、`__pycache__/`、`.runtime/`、`resource/runtime/.../logs`、`vectordb/chroma.sqlite3`、`data/conversations/...` 位于 `highThinkingQA/` 下；本次只审计，不清理。

### 10.10 新增重构点

以下 `R-009` 至 `R-020` 均为第二轮深度补充，所属服务均为 `highThinkingQA`。每个条目的 `当前状态` 以对应接口路径和调用链为准：ask/ask_stream/health 路径为 `active live path`；conversation/upload/admin/auth/documents/ingest/quota/system routers 为 `deprecated unregistered`；service-owned persistence 为 `deprecated but referenced`；config/env/LLM 为 `active live path`。本节所有条目均按第二轮模板补充代码位置、行号范围、接口路径、当前调用链、关键代码片段、目标结构、迁移步骤、兼容/回滚、测试计划、风险和阻塞项。

### R-009：将 `ask.py` 的 SSE/线程桥接抽为 StreamController

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P1
- 类型：streaming boundary / cancellation
- 代码位置、行号范围：`highThinkingQA/server_fastapi/routers/ask.py:364-623`
- 接口路径：`POST /api/v1/ask_stream`、`POST /api/ask_stream`、`POST /api/v1/{mode}/ask_stream`、`POST /api/{mode}/ask_stream`
- 当前调用链：router -> `_build_stream_response()` -> `stream_ask_events()` -> `_start_sync_stream_producer()` -> async generator -> `StreamingResponse`
- <=40 行关键片段：

```python
def _start_sync_stream_producer(...):
    def _run() -> None:
        try:
            for item in iterator:
                if stop_event.is_set():
                    break
                _publish(_SyncStreamItem(kind=_SYNC_STREAM_EVENT, payload=item))
        except Exception as exc:
            _publish(_SyncStreamItem(kind=_SYNC_STREAM_ERROR, payload=exc))
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
            _publish(_SyncStreamItem(kind=_SYNC_STREAM_DONE))
    thread = threading.Thread(target=_run, daemon=True, name="thinkingqa-stream-producer")
    thread.start()
    return thread
```

- 目标结构：`server_fastapi/streaming/controller.py` 提供 `StreamController.build_response(iterator_factory, request, slot, trace_id, callbacks)`；`ask.py` 只传 request/model。
- 迁移步骤：先复制现有行为到 controller；加 router contract tests；再把 `_build_stream_response` 内部逻辑迁出；最后保留薄 wrapper 兼容。
- 兼容/回滚：保留 `_build_stream_response` 调 controller；异常时可回滚 wrapper 到旧实现。
- 测试计划：unit 覆盖 producer close/error；contract 覆盖 SSE headers/seq/ts；router 覆盖四个 stream path；stream 覆盖 heartbeat/disconnect/cancel；integration 覆盖 gateway-owned task；regression 覆盖 `test_ask_router_summary_persistence.py:757-811`。
- 风险：高。first-token、cancel、slot release 任一回归都会影响前端长连接。
- 阻塞项：需要确认 gateway 对断连后的终态持久化期望。

### R-010：将 gateway-owned/service-owned persistence 从 router 抽为 PersistenceAdapter

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path / deprecated but referenced

- 严重程度：P1
- 类型：persistence ownership / gateway contract
- 代码位置、行号范围：`highThinkingQA/server_fastapi/routers/ask.py:184-261,322-361`
- 接口路径：所有 ask/ask_stream path
- 当前调用链：router -> `_gateway_owned_persistence()` -> `_persist_user_message_if_needed()` / `_persist_assistant_*_if_needed()` -> `chat_persistence`
- <=40 行关键片段：

```python
def _gateway_owned_persistence(request: Request) -> bool:
    expected_token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()
    if not expected_token:
        return False
    internal_service_name = str(_request_header(request, "X-Internal-Service-Name") or "").strip().lower()
    internal_service_token = str(_request_header(request, "X-Internal-Service-Token") or "").strip()
    return (
        _header_truthy(_request_header(request, "X-Gateway-Task-Execution"))
        and _header_truthy(_request_header(request, "X-Gateway-Owned-Persistence"))
        and internal_service_name == "gateway"
        and internal_service_token == expected_token
    )
```

- 目标结构：`server/services/persistence_adapter.py` 或 shared `agent_common/persistence/gateway_task.py`；封装 `is_gateway_owned`、`persist_user`、`persist_done`、`persist_terminal`。
- 迁移步骤：先引入 adapter 并保持函数名代理；迁移 sync path；迁移 stream summary/terminal path；删除 router 内 persistence helpers。
- 兼容/回滚：adapter 读同样 headers/env；保留旧 helpers 一版作为 fallback。
- 测试计划：unit 覆盖 header truth table；contract 覆盖 internal token 缺失时仍 service-owned；router 覆盖 sync/stream skip；stream 覆盖 error/cancel terminal；integration 覆盖 public-service authority；regression 覆盖 `test_stream_skips_local_persistence_for_gateway_owned_task`。
- 风险：高。误判 gateway-owned 会导致重复写或漏写会话。
- 阻塞项：需要 gateway/public-service 确认 `X-Gateway-*` 头长期契约。

### R-011：统一 sync/stream assistant terminal semantics

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P1
- 类型：terminal state consistency
- 代码位置、行号范围：`highThinkingQA/server_fastapi/routers/ask.py:237-361,498-523,582-609,640-718`
- 接口路径：所有 ask/ask_stream path
- 当前调用链：sync 异常直接 `_persist_assistant_terminal_if_needed`；stream error frame 时 `_persist_terminal_once`；done 时 `_persist_summary_once`
- <=40 行关键片段：

```python
def _persist_terminal_once(*, terminal_status: str, error_payload: dict | None = None) -> None:
    nonlocal assistant_persisted
    if assistant_persisted:
        return
    _persist_assistant_terminal_if_needed(
        request=request,
        ask_request=ask_request,
        summary=dict(summary),
        terminal_status=terminal_status,
        error_payload=error_payload,
    )
    assistant_persisted = True
```

- 目标结构：`TerminalEventBuilder` 产出统一 `{status, answer, summary, failure}`；sync 与 stream 都调用同一 adapter。
- 迁移步骤：抽 `_failure_from_error_payload` 与 `_summary_payload`；为 sync success/error 和 stream done/error/cancel 构建同一 terminal event；替换两处路径。
- 兼容/回滚：保持 `persist_assistant_summary()` 调用入口，同时新增 terminal adapter；若 public-service 不接受失败终态可降级旧 summary-only。
- 测试计划：unit 覆盖 done/failed/canceled payload；contract 覆盖 public-service terminal fields；router 覆盖 sync upstream error；stream 覆盖 timeout/cancel/no done；integration 覆盖 pending overlay；regression 覆盖 summary persistence tests。
- 风险：中高。assistant_persisted 互斥逻辑改动可能丢失 completion callback 结果。
- 阻塞项：public-service terminal API 对 failed/canceled 的最终字段要求。

### R-012：拆分 `ask_service.py` 为 ThinkingAskRunner 与 EventMapper

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P1
- 类型：service decomposition
- 代码位置、行号范围：`highThinkingQA/server/services/ask_service.py:568-707,745-1081`
- 接口路径：ask/ask_stream 业务执行层
- 当前调用链：`execute_ask` 和 `stream_ask_events` 都做 profile/context/rewrite/executor/reference/event。
- <=40 行关键片段：

```python
def execute_ask(...):
    profile = resolve_profile(request.mode)
    context, rewrite = _prepare_execution(request)
    future = _get_agent_executor().submit(
        _run_agent_for_profile,
        rewrite.effective_question,
        profile,
        raw_question=context.raw_question,
        conversation_context={...},
        cancel_event=active_cancel_event,
        trace_id=trace_id,
    )
```

- 目标结构：`ThinkingAskRunner.prepare()`、`ThinkingAskRunner.run_sync()`、`ThinkingAskRunner.run_stream()`；`EventMapper.to_metadata/to_content/to_done/to_error`。
- 迁移步骤：先提取 immutable execution context；迁移 sync；迁移 stream worker；把 event dict 构造改走 mapper。
- 兼容/回滚：保留 `execute_ask/stream_ask_events` 公共函数不改签名，内部代理到新类。
- 测试计划：unit 覆盖 runner prepare/run；contract 覆盖 metadata/done schema；router 覆盖响应不变；stream 覆盖 content before done；integration 覆盖 run_agent mock；regression 覆盖 `test_ask_service_executor.py` 全部。
- 风险：高。stream generator 顺序是前端契约。
- 阻塞项：是否允许引入新模块文件需要独立实现阶段确认；本审计不改代码。

### R-013：将中文阶段文案迁出 backend 或隔离为 FrontendTextAdapter

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P2
- 类型：frontend/backend boundary
- 代码位置、行号范围：`highThinkingQA/server/services/ask_service.py:193-348,535-565`
- 接口路径：ask_stream step events
- 当前调用链：agent progress -> `_progress_to_step_event()` -> `_format_frontend_step_message()` -> SSE step message
- <=40 行关键片段：

```python
if stage == "step2":
    if "全部完成" in raw_message:
        ...
        return "阶段2：子问题预回答全部完成，开始等待检索结果", data
    ...
    return f"阶段2：子问题预回答：已完成 {completed_int}/{total_int}", data
```

- 目标结构：backend 发 stable `stage/status/data/message_code`；gateway/frontend 负责中文 display；过渡期 `FrontendTextAdapter` 保持现有 `message`。
- 迁移步骤：先在 step event 增加 `message_code`/`default_message`；前端读 code；再把中文适配移到 frontend/gateway；最后 backend message 简化。
- 兼容/回滚：保留现有 `message` 字段至少一版。
- 测试计划：unit 覆盖 code 映射；contract 覆盖旧 message 仍存在；router 不变；stream 覆盖 step order；integration 覆盖前端展示；regression 覆盖 `test_progress_is_mapped_to_frontend_compatible_step`。
- 风险：中。前端可能直接展示中文 `message`。
- 阻塞项：需要确认 frontend/gateway 当前消费字段。

### R-014：将 DOI/reference 构造抽为 ReferenceBuilder，并迁移 `/api/v1/view_pdf` link authority

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path / deprecated unregistered dependency

- 严重程度：P1
- 类型：document contract / public-service boundary
- 代码位置、行号范围：`highThinkingQA/server/services/ask_service.py:351-481,684-707,710-742`
- 接口路径：ask/ask_stream done payload；retired documents path `/api/v1/view_pdf/{doi:path}`
- 当前调用链：answer text -> DOI extraction -> `/api/v1/view_pdf/{doi}` -> reference_objects/doi_locations -> done/sync payload
- <=40 行关键片段：

```python
def _build_reference_links(references: list[str]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in references:
        for doi in extract_dois(raw):
            key = doi.lower()
            if key in seen:
                continue
            seen.add(key)
            links.append({"doi": doi, "pdf_url": f"/api/v1/view_pdf/{doi}"})
    return links
```

- 目标结构：`ReferenceBuilder` 接收 link resolver；默认 resolver 指向 gateway/public-service document route，而非 retired local documents router。
- 迁移步骤：抽 builder；新增配置或 gateway header 指定 public file route base；更新 tests；保留 `/api/v1/view_pdf` 兼容一版。
- 兼容/回滚：旧 `pdf_url` 保留；新增 `document_url` 或 `file_route` 字段供前端迁移。
- 测试计划：unit 覆盖 DOI normalize/dedupe/location；contract 覆盖 link route；router 覆盖 retired documents 仍 404；stream 覆盖 done links；integration 覆盖 gateway document proxy；regression 覆盖 `test_build_reference_links_uses_normalized_doi`。
- 风险：高。当前 documents router 已 404，但输出仍指向该路径，可能依赖 gateway 重写。
- 阻塞项：需要确认 public-service/gateway 的正式 PDF route。

### R-015：为 OpenAI-compatible client 实现或删除 `max_retries`

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P1
- 类型：LLM reliability contract
- 代码位置、行号范围：`highThinkingQA/agent_core/openai_compat.py:352-690`，`highThinkingQA/agent_core/llm_client.py:25-42`，`highThinkingQA/ingest/embedder.py:52-67`
- 接口路径：所有 ask/ingest/retrieval LLM/embedding 上游调用
- 当前调用链：`get_llm_client(max_retries)` -> `OpenAICompatibleChatClient(..., max_retries=max_retries)` -> `del max_retries`
- <=40 行关键片段：

```python
class OpenAICompatibleChatClient:
    def __init__(..., max_retries: int | None = None) -> None:
        del max_retries
        self.endpoint = normalize_openai_compatible_endpoint(base_url)
        self.api_key = str(api_key or "")
        self.auth_mode = auth_mode
        self._client = http_client or httpx.Client(timeout=float(timeout_seconds), http2=False)
```

- 目标结构：shared `OpenAICompatibleClient` 明确定义 retry policy；chat/stream/embedding 分别说明是否重试。
- 迁移步骤：决定 retry 策略；若实现则加 backoff/status allowlist；若不实现则删除参数和注释；同步 tests。
- 兼容/回滚：保留参数但记录 warning 一版，或实现 no-op policy class。
- 测试计划：unit 覆盖 max_retries=0/2；contract 覆盖 timeout/status retry；router 间接覆盖 upstream timeout；stream 覆盖 stream 不重放；integration 覆盖 fake 503 后成功；regression 覆盖 `test_llm_client.py` endpoint/auth/stream tests。
- 风险：中高。对 stream retry 不当会重复 token。
- 阻塞项：需要产品确认 LLM/embedding retry 策略是否在 client 还是 caller。

### R-016：收敛 ConversationAuthorityClient 的 mode 字段固定 thinking 行为

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P2
- 类型：mode contract consistency
- 代码位置、行号范围：`highThinkingQA/server/services/conversation_authority_client.py:134-198,200-303`
- 接口路径：internal public-service `/internal/conversations/...`
- 当前调用链：`chat_persistence` 传 requested/actual -> `ConversationAuthorityClient` 删除参数 -> payload 固定 thinking
- <=40 行关键片段：

```python
def write_user_turn(..., requested_mode: str, actual_mode: str, ...):
    del requested_mode, actual_mode
    payload = {
        "source_service": _SERVICE_NAME,
        "route": str(route),
        "requested_mode": _THINKING_MODE,
        "actual_mode": _THINKING_MODE,
        ...
    }
```

- 目标结构：明确 highThinkingQA internal authority 始终写 thinking，或透传 gateway requested/actual；不要同时在签名上显示可传、payload 又覆盖。
- 迁移步骤：审计 public-service internal contract；若固定 thinking，则删除无效参数并更新调用方；若透传，则使用参数并补 tests。
- 兼容/回滚：新增字段 `source_mode="thinking"`，保留 requested/actual 透传。
- 测试计划：unit 覆盖 payload mode；contract 覆盖 public-service schema；router 覆盖 `requested_mode=fast actual_mode=thinking`；stream 覆盖 metadata一致；integration 覆盖 authority client；regression 覆盖 `test_conversation_authority_client.py`。
- 风险：中。mode 字段用于多服务任务追踪时会出现归因错误。
- 阻塞项：public-service 对 requested/actual 的语义定义。

### R-017：将 conversation context/rewrite 从 ask_service 抽为 ConversationExecutionContext

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P2
- 类型：multi-turn context boundary
- 代码位置、行号范围：`highThinkingQA/server/services/conversation_context_service.py:149-190`，`query_rewrite_service.py:133-172`，`ask_service.py:597-612`
- 接口路径：ask/ask_stream
- 当前调用链：`_prepare_execution()` -> `build_conversation_context()` -> `chat_persistence.load_conversation_context()` -> `rewrite_question()`
- <=40 行关键片段：

```python
def _prepare_execution(request: AskRequest) -> tuple[ConversationContext, QuestionRewriteResult]:
    context = sanitize_conversation_context(build_conversation_context(request=request))
    try:
        rewrite = rewrite_question(
            raw_question=context.raw_question,
            recent_turns=context.recent_turns,
            summary=context.summary,
        )
    except Exception:
        rewrite = QuestionRewriteResult(..., rewrite_reason="rewrite_failed")
    return context, rewrite
```

- 目标结构：`ConversationExecutionContext` 产出 raw/effective question、recent_turns、summary、rewrite metadata、authority snapshot version。
- 迁移步骤：抽 context object；让 runner 只消费 context；把 rewrite failure 作为 metadata；统一 sync/stream metadata。
- 兼容/回滚：旧 metadata 字段 `raw_question/effective_question/rewrite_*` 保持。
- 测试计划：unit 覆盖 overlap/history budget/rewrite fallback；contract 覆盖 metadata；router 不变；stream 覆盖 metadata首帧；integration 覆盖 public-service snapshot；regression 覆盖 `test_conversation_context_service.py`。
- 风险：中。上下文预算变化会影响模型回答。
- 阻塞项：需要确认多轮上下文最大字符与 summary freshness 是否服务统一。

### R-018：拆分 config typed settings 与 legacy constant facade

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P2
- 类型：configuration architecture
- 代码位置、行号范围：`highThinkingQA/config.py:170-467`，`highThinkingQA/env_loader.py:14-187`
- 接口路径：全服务启动与运行配置
- 当前调用链：import config -> `load_workspace_env()` -> dataclass settings -> module globals -> service modules read globals
- <=40 行关键片段：

```python
SETTINGS = get_runtime_settings()
HTTP_SETTINGS = get_http_service_settings()
CONVERSATION_ROLLOUT_SETTINGS = get_conversation_rollout_settings()

LLM_BASE_URL = SETTINGS.llm_base_url
LLM_MODEL = SETTINGS.llm_model
LLM_API_KEY = SETTINGS.llm_api_key
...
ASK_STREAM_MAX_CONCURRENT = HTTP_SETTINGS.ask_stream_max_concurrent
```

- 目标结构：`settings.py` 只暴露 typed settings；`legacy_config.py` 或 `config.py` 保留只读 facade；shared env loader 参数化 service code/name。
- 迁移步骤：抽 shared env loader；让 `config.py` 导入 typed settings；逐步将模块从 globals 改为 settings 对象；最后收缩 facade。
- 兼容/回滚：`config.py` 常量保留到所有服务迁移完成。
- 测试计划：unit 覆盖 env priority；contract 覆盖 legacy constants；router 覆盖 app.state config；stream 覆盖 timeout/heartbeat；integration 覆盖 scripts env roots；regression 覆盖 `test_env_loader.py` 与 `test_config_runtime_defaults.py`。
- 风险：中。env 优先级或默认 root 改动会影响部署。
- 阻塞项：fastQA/patent/public-service 的 env loader 对齐计划。

### R-019：清理 retired router/service/schema/storage 闭包前先冻结 404 与 link contract

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：deprecated unregistered / deprecated but referenced

- 严重程度：P2
- 类型：legacy cleanup
- 代码位置、行号范围：`server_fastapi/routers/conversation.py:1-209`、`upload.py:1-263`、`admin.py:1-149`、`auth.py:1-109`、`documents.py:1-72`、`ingest.py:1-51`、`quota.py:1-117`、`system.py:1-39`
- 接口路径：conversation/upload/admin/auth/documents/ingest/quota/system 全部 retired path
- 当前调用链：未注册 router -> 不进入 HTTP；但文件仍 import retired services/schemas/storage
- <=40 行关键片段：

```python
# highThinkingQA/server_fastapi/routers/upload.py:1-4
"""FastAPI upload routes for PDF and Excel files."""

# Deprecated: this router is no longer registered in the current architecture.
# Upload HTTP APIs are owned by public-service behind gateway public proxy.
```

- 目标结构：保留最小 404 contract tests；删除或迁移 retired HTTP modules；保留仍被 active path 需要的 storage/client 能力。
- 迁移步骤：先列每个 retired router 下游 service；确认 public-service parity；迁移 `/api/v1/view_pdf` link resolver；删除 router 文件和专属 schema/service；保留 conversation persistence 依赖。
- 兼容/回滚：删除前打 tag/保留迁移文档；如需临时恢复只重新注册 public-service proxy，不恢复本地实现。
- 测试计划：unit 覆盖保留服务；contract 覆盖 retired path 404；router 覆盖 active path 不变；stream 覆盖 reference link；integration 覆盖 gateway proxy；regression 覆盖 fastapi_migration retired route tests。
- 风险：中。某些工具或手工运维可能仍直接调用 retired endpoints。
- 阻塞项：确认 public-service 已拥有 auth/admin/quota/doc/upload/system 全部契约。

### R-020：将 runtime resource snapshot 从请求热路径移到诊断/health

- 来源：第二轮深度补充
- 所属服务：highThinkingQA
- 当前状态：active live path

- 严重程度：P3
- 类型：performance / observability
- 代码位置、行号范围：`highThinkingQA/server/services/ask_service.py:148-179,633-635,764-766`
- 接口路径：ask/ask_stream
- 当前调用链：每次 ask/stream 开始 -> `_log_runtime_resource_snapshot()` -> `get_or_create_collection()` 与 `get_collection_count()`
- <=40 行关键片段：

```python
try:
    from ingest.vector_store import get_collection_count, get_or_create_collection
    collection = get_or_create_collection()
    collection_count = int(get_collection_count(collection))
except Exception as exc:
    collection_error = f"{type(exc).__name__}: {exc}"
logger.info("[trace_id=%s] runtime_resource_snapshot ... collection_count=%s ...")
```

- 目标结构：启动/health/diagnostics 缓存 resource snapshot；ask path 只记录 snapshot id 或 cached status。
- 迁移步骤：抽 `RuntimeResourceProbe`；加 TTL cache；health 暴露 component status；ask 读取 cached probe。
- 兼容/回滚：保留 env 开关 `ASK_LOG_RUNTIME_SNAPSHOT_INLINE` 一版。
- 测试计划：unit 覆盖 probe TTL/error；contract 覆盖 health component status；router 覆盖 ask 不触发 probe mock；stream 覆盖 first event latency；integration 覆盖 Chroma unavailable；regression 覆盖 runtime snapshot logging tests（需新增）。
- 风险：低中。诊断日志减少可能影响排障。
- 阻塞项：需要确认 ops 是否依赖每请求 snapshot 日志。

### 10.11 未能确认项

- 未跨目录行号级审计 fastQA/patent 的 OpenAI-compatible client，所以“重复实现”在本轮只作为共享能力候选，不作为已证明的逐行重复结论。
- 未确认 gateway 当前是否重写 `/api/v1/view_pdf/{doi}` 到 public-service；代码只证明 highThinkingQA 本地 documents router 已退役但 ask payload 仍生成该路径。
- 未确认生产环境是否仍启用 `legacy` 或 `shadow_public_service` rollout；代码证明配置仍支持。
- 未运行测试；所有测试覆盖描述来自测试文件阅读。

## 第三轮证据闭环补充

> 状态：第三轮只读证据闭环。未修改 `highThinkingQA/` 下源码、配置、测试、脚本、README、依赖文件；本轮仅追加本文档。未创建新目录或新文档，未提交 commit，未运行会写文件的构建、格式化、服务启动命令。

### 1. 第二轮未确认项复核

### V-301

- 验证目标：retired routers 是否仍被注册。
- 证据：`highThinkingQA/server_fastapi/routers/__init__.py:5-11` 只导入并 `include_router(health_router)`、`include_router(ask_router)`；`rg -n "include_router|conversation_router|upload_router|admin_router|auth_router|documents_router|ingest_router|quota_router|system_router" highThinkingQA` 仅命中这两行 include。
- 结论：conversation/upload/admin/auth/documents/ingest/quota/system routers 均未注册到当前 FastAPI app。
- 影响：HTTP surface 已闭合到 ask/health；删除前仍需处理下游 service/schema/storage 与测试引用。
- 后续：以 retired closure 判定表作为删除顺序门禁。

### V-302

- 验证目标：retired routers 对应 service/schema/storage 是否仍被脚本或测试 import。
- 证据：`rg` 未发现 `server_fastapi.routers.conversation|upload|admin|auth|documents|ingest|quota|system` 的直接 import；但测试仍 import 下游能力：`tests/test_documents_service.py:7-8` import `documents_service` 与 `paper_storage`，`tests/test_llm_client.py:15` import `DocumentsService`，`tests/fastapi_migration/test_file_delivery_baseline.py:3` import `resolve_uploaded_file_delivery`，`tests/fastapi_migration/test_fastapi_auth_token_compat.py:5` import `TokenService`，`tests/test_conversation_mysql_alignment.py:5` import `ConversationService`。脚本侧只发现 `server/tools/run_chat_json_outbox_worker.py:17` import conversation outbox worker，未发现 retired router import。
- 结论：router 文件本身没有脚本/测试直接依赖；documents/file-delivery/auth token/conversation persistence 相关下游模块仍有测试依赖，不能按“router 未注册”一刀删除。
- 影响：cleanup 要先拆 HTTP-retired 专属闭包，再保留或迁移 active persistence/storage/token 边界。
- 后续：TASK-301 先做下游能力分层清单。

### V-303

- 验证目标：gateway 当前是否把 `/api/v1/view_pdf/{doi}` 转到 public-service。
- 证据：gateway route table 包含 `_paths("/api/view_pdf/{doi:path}")`，会生成 `/api/view_pdf/{doi:path}` 与 `/api/v1/view_pdf/{doi:path}`，见 `gateway/app/services/route_table.py:50-55`；public proxy route spec 覆盖 GET/HEAD，见 `gateway/app/routers/public_proxy.py:252-257`；`_proxy_to_public()` 使用 `registry.get_public()`，见 `gateway/app/routers/public_proxy.py:49-69`；测试 `gateway/tests/test_public_proxy.py:886-907` 断言 `/api/v1/view_pdf/10.1000/test?token=token-1` 被转发并保留 query token。
- 结论：gateway 当前有代码级和测试级证据将 `/api/v1/view_pdf/{doi}` 作为 public proxy 路径转到 public backend。
- 影响：`highThinkingQA` 输出 `/api/v1/view_pdf/{doi}` 仍可通过 gateway 工作，但这是 gateway/public-service contract，不应再指向本地 retired documents router。
- 后续：TASK-303 将 ask reference link resolver 显式绑定 gateway/public-service route contract。

### V-304

- 验证目标：ask payload 是否仍生成 retired documents 路径，是否需要改 contract。
- 证据：`highThinkingQA/server/services/ask_service.py:355-365` 在 `_build_reference_links()` 中直接生成 `{"pdf_url": f"/api/v1/view_pdf/{doi}"}`；sync payload 在 `ask_service.py:696-704` 输出 `pdf_links/reference_links/doi_locations`；stream done 在 `ask_service.py:724-737` 输出同样字段。自身 documents router 对 `/api/v1/view_pdf/{doi}` 未注册且 route surface 测试断言 404，见 `tests/fastapi_migration/test_fastapi_route_surface_minimal.py:16-23,27-35`。
- 结论：ask payload 仍生成高思考本地已退役的 document route 字符串；当前依赖 gateway public proxy 兜底。需要把 contract 从“本服务 documents route”改为“gateway/public-service document route”。
- 影响：如果绕过 gateway 直接调 highThinkingQA，PDF link 会指向 404；若未来 gateway 改 path，后端硬编码会漂移。
- 后续：TASK-303。

### V-305

- 验证目标：生产环境是否仍可能启用 legacy 或 shadow_public_service rollout。
- 证据：`config.py:33` 允许 `legacy/public_service/shadow_public_service`；`config.py:104-126` 默认 execution 与 assistant target 为 `public_service`，但仍读取 env overrides；当 user/context split 不一致且 `APP_ENV` 是生产环境时会抛错，见 `config.py:110-113`，但没有禁止 execution target 本身设置为 `legacy` 或 `shadow_public_service`；`chat_persistence.py:548-565,616-656,718-786` 仍实现 legacy 与 shadow 读写路径；测试 `tests/test_chat_persistence.py:232-327` 覆盖 shadow 行为。
- 结论：默认值已是 public_service，但生产环境仍可通过 env 选择 `legacy` 或 `shadow_public_service`，除 split-target guard 外没有全量禁用。
- 影响：不能立即删除 legacy/shadow persistence；需先明确部署策略或加生产禁用 guard。
- 后续：TASK-302。

### V-306

- 验证目标：gateway-owned persistence 与 service-owned persistence 的切换条件。
- 证据：highThinkingQA 只有在同时满足 `X-Gateway-Task-Execution`、`X-Gateway-Owned-Persistence`、`X-Internal-Service-Name=gateway`、`X-Internal-Service-Token == PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` 时跳过本地 persistence，见 `server_fastapi/routers/ask.py:184-195,215-239,322-331`。gateway task worker 会注入这些头，见 `gateway/app/services/qa_tasks.py:2128-2136`，测试断言见 `gateway/tests/test_task_api.py:3893-3896`。gateway 普通 mode ask/ask_stream 直通使用 `proxy_service.forward_json/open_json_stream`，见 `gateway/app/routers/qa.py:684-690,786-793`，`ProxyService._prepare_upstream_headers()` 仅透传过滤后的原请求头与 trace，见 `gateway/app/services/proxy.py:284-294`，未注入 gateway-owned headers。gateway 普通 thinking 测试还断言不写 public message persistence，见 `gateway/tests/test_qa_proxy.py:2761-2791,2793-2834`。
- 结论：当前只有 gateway task execution path 是 gateway-owned persistence；普通 gateway mode proxy path 仍依赖 highThinkingQA service-owned persistence。
- 影响：不能把 highThinkingQA service-owned persistence 直接删掉，除非普通 mode proxy 也改为 gateway-owned 或统一走 task execution。
- 后续：persistence ownership 决策表与 TASK-302。

### V-307

- 验证目标：`ask_service.py` 中 UI 文案是否可迁到 frontend event mapper。
- 证据：backend 在 `_format_frontend_step_message()` 写死中文阶段文案，见 `ask_service.py:193-348`；测试显式断言这些文案，见 `tests/test_ask_service_executor.py:636-686`。frontend 已有 message parser 和 step payload builder：`frontend-vue/src/views/Home.vue:794-810` 从 `data.message/data.content/step` 构造 title/detail/status，`Home.vue:1070-1078` 对 `type === "step"` upsert step；但没有看到基于 `message_code` 的 mapper。
- 结论：frontend 已能承接“解析和展示 step message”，但还没有稳定 event code mapper；迁移可行，前提是先新增后端稳定 code/data 并保留旧 `message` 兼容。
- 影响：不能立即删除 backend 中文 message；应先做双写 contract。
- 后续：TASK-304。

### V-308

- 验证目标：runtime resource snapshot 是否确实在请求热路径。
- 证据：`_log_runtime_resource_snapshot()` 在 `ask_service.py:148-179` 中同步检查 `PAPERS_DIR/CHROMA_PERSIST_DIR` 并调用 `get_or_create_collection()` 与 `get_collection_count()`；sync ask 在 `ask_service.py:633-635` 调用；stream ask 在第二轮已定位 `ask_service.py:764-766` 调用。
- 结论：runtime resource snapshot 确实在 ask 与 ask_stream 请求热路径中执行。
- 影响：Chroma 初始化/count 可能放大首包延迟或请求抖动，适合移到 health/diagnostics 或带 TTL 的 probe。
- 后续：TASK-305。

### V-309

- 验证目标：OpenAI-compatible client 与 fastQA/patent 的重复是否要做逐行矩阵。
- 证据：highThinkingQA 的 OpenAI-compatible client 同时实现 endpoint normalize、auth headers、sync chat、sync stream、async chat/stream、embedding，见 `agent_core/openai_compat.py:352-690`；fastQA/patent 搜索显示也有 LLM auth/stream/upstream pool/rerank endpoint 能力，例如 `fastQA/app/integrations/llm/thinking.py` 被多处引用、`fastQA/app/core/runtime.py` 有 shared upstream pool、`patent/server/patent/upstream_http.py` 有 shared upstream http provider。但本轮只做 `rg` 定位，未逐文件读取 fastQA/patent 等价实现。
- 结论：可以确认存在跨服务同类能力，但不能声称逐行重复。进入共享抽取前必须做逐行矩阵。
- 影响：不要直接把 highThinkingQA client 抽成共享基线；先比较 fastQA/patent 对 timeout、pool、stream、auth、retry 的差异。
- 后续：TASK-306。

### V-310

- 验证目标：`max_retries` 参数是否实际未生效。
- 证据：`get_llm_client(max_retries)` 与 `get_async_llm_client(max_retries)` 透传参数，见 `agent_core/llm_client.py:25-42`；`OpenAICompatibleChatClient.__init__`、`AsyncOpenAICompatibleChatClient.__init__`、`OpenAICompatibleEmbeddingClient.__init__` 分别 `del max_retries`，见 `agent_core/openai_compat.py:352-364,484-496,616-628`；测试 `tests/test_llm_client.py:440-447` 只断言接受参数和 endpoint，不断言 retry 行为。
- 结论：`max_retries` 是 no-op，实际未生效。
- 影响：调用方以为能禁用或启用 retry，但底层没有行为差异；重构前要决定实现 retry 还是删除参数。
- 后续：TASK-306。

### 2. dead-code / legacy 引用闭环

#### retired closure 判定表

| retired router | 最终状态 | 注册证据 | import 证据 | test 证据 | script 证据 | service/schema/storage 处理判断 |
|---|---|---|---|---|---|---|
| `conversation.py` | deprecated unregistered；HTTP 可删候选，下游 conversation persistence 不可直接删 | `routers/__init__.py:5-11` 只注册 ask/health | `rg server_fastapi.routers.*` 未发现直接 import；文件自身 `conversation.py:17-18` 依赖 `conversation_service/file_delivery_service` | `test_fastapi_route_surface_minimal.py:18,31` 与 `test_fastapi_conversation_upload_contract.py:10-12` 断言 404；`test_conversation_mysql_alignment.py:5` 仍测 `ConversationService` | 未发现 retired router import；`server/tools/run_chat_json_outbox_worker.py:17` 仍用 conversation outbox worker | router 可归档；`conversation_service/chat_json/outbox` 仍服务 legacy persistence 与测试，需先经 TASK-302 拆分 |
| `upload.py` | deprecated unregistered；HTTP 可删候选，下游 upload/file storage 需核验 | 未注册 | 未发现直接 import；文件自身 `upload.py:20-21` 依赖 `conversation_service/upload_service` | `test_fastapi_route_surface_minimal.py:16-17,30` 与 `test_fastapi_conversation_upload_contract.py:13-14` 断言 404 | 未发现 | router 可归档；`upload_service` 只在 router 与 `server/storage/__init__.py` 暴露，删除前确认 public-service 上传完全接管 |
| `admin.py` | deprecated unregistered；HTTP 与 admin schema/service 可删候选 | 未注册 | 未发现直接 import；文件自身 `admin.py:13-23` 依赖 `admin_users_*` 与 `admin_schemas` | `test_fastapi_admin_contract.py:7-12` 断言 404 | 未发现 | `admin_users_service/import_service/admin_schemas` 目前只被 retired admin 闭包牵引，迁移到 public-service 后可删除 |
| `auth.py` | deprecated unregistered；HTTP router 可删，auth token deps 不能一刀删 | 未注册 | 未发现直接 import；文件自身 `auth.py:13-22` 依赖 `auth_service/auth.schemas` | `test_fastapi_auth_contract.py:7-11` 断言 404；`test_fastapi_auth_token_compat.py:5` 仍测 `TokenService` | 未发现 | `auth.py/auth.schemas` 可归档；`auth_service.TokenService` 与 `auth/deps.py:10-59` 仍用于 ask auth dependency，需要保留或替换 |
| `documents.py` | deprecated unregistered；HTTP 已退役，但 link/storage contract 仍活跃 | 未注册 | 未发现 router 直接 import；文件自身 `documents.py:14-16` 依赖 `documents_service/auth deps` | `test_fastapi_documents_contract.py:10-13`、`test_fastapi_low_risk_routes.py:25-26` 断言 404；`test_documents_service.py:7-130` 与 `test_llm_client.py:486-500` 仍测 documents service；ask tests 仍期望 `/api/v1/view_pdf` | 未发现 | router 可归档；`documents_service/paper_storage/object_reader` 仍有测试和 DOI/PDF link contract，需先 TASK-303 迁移 |
| `ingest.py` | deprecated unregistered；HTTP 与 ingest service 可删候选 | 未注册 | 未发现直接 import；文件自身 `ingest.py:15` 依赖 `ingest_service` | `test_fastapi_route_surface_minimal.py:23,35` 与 `test_fastapi_low_risk_routes.py:25` 断言 404 | 未发现 | `ingest_service` 仅见 retired router 使用，若 ingest CLI/ops 不再需要可删除 |
| `quota.py` | deprecated unregistered；HTTP 与 quota schema/service 可删候选 | 未注册 | 未发现直接 import；文件自身 `quota.py:13-15` 依赖 `quota_service/quota_schemas` | `test_fastapi_route_surface_minimal.py:20,33` 与 `test_fastapi_quota_contract.py:7-12` 断言 404 | 未发现 | quota 已 gateway/public-service owner；本地 `quota_service/quota_schemas` 可在 public parity 确认后删除 |
| `system.py` | deprecated unregistered；HTTP 与 system service 可删候选 | 未注册 | 未发现直接 import；文件自身 `system.py:12` 依赖 `system_service` | `test_fastapi_documents_contract.py:10` 断言 `/api/v1/kb_info` 404 | 未发现 | `system_service` 只被 retired router 使用，health/diagnostics 新设计落地后可删除或替换 |

结论：第三轮将“retired router”分为两类。第一类是只被 retired HTTP 闭包牵引的 admin/ingest/quota/system/upload 部分，可在 public-service parity 与 404 guard 稳定后删除。第二类是 documents/auth/conversation 相关下游能力仍被 active auth、persistence、PDF link contract 或测试使用，必须先拆出 active 子能力。

### 3. live path 调用链闭环

#### live path 调用链

```text
gateway direct mode path
  /api/thinking/ask(_stream)
  -> gateway/app/routers/qa.py:_proxy_ask/_proxy_ask_stream
  -> ProxyService.forward_json/open_json_stream
  -> highThinkingQA /api/thinking/ask(_stream)
  -> ask.py service-owned persistence helpers unless gateway-owned headers are present
  -> ask_service.execute_ask/stream_ask_events
  -> conversation context/read target
  -> runtime resource snapshot
  -> agent_core.graph.run_agent
  -> reference/pdf link builder and event mapper

gateway task execution path
  gateway/app/services/qa_tasks.py:_build_internal_request
  -> inject X-Gateway-Task-Execution + X-Gateway-Owned-Persistence + internal token
  -> highThinkingQA ask_stream
  -> ask.py skips local user/assistant persistence
  -> gateway task service persists terminal outcome to public-service
```

#### persistence ownership 决策表

| 请求来源/条件 | gateway header 条件 | highThinkingQA 行为 | gateway 行为证据 | 当前 owner 判定 | 重构前置条件 |
|---|---|---|---|---|---|
| gateway普通 `/api/thinking/ask` | 默认不注入 `X-Gateway-Owned-Persistence` | `_gateway_owned_persistence()` false，`_persist_user_message_if_needed()` 与 assistant persistence 继续执行 | `qa.py:684-690` 直通 `forward_json`；`proxy.py:284-294` 只补 trace；`test_qa_proxy.py:2761-2791` 断言 gateway 不写 public messages | service-owned persistence | 不能删除本地 persistence，除非 gateway 普通 path 改为 owning |
| gateway普通 `/api/thinking/ask_stream` | 默认不注入 gateway-owned headers | stream 前写 user，done/error/cancel 时写 assistant terminal | `qa.py:786-793` 直通 `open_json_stream`；`test_qa_proxy.py:2793-2834` 断言 gateway 不写 public messages | service-owned persistence | 同上 |
| gateway task worker ask_stream | 注入 `X-Gateway-Task-Execution=1`、`X-Gateway-Owned-Persistence=1`、internal service name/token | `_gateway_owned_persistence()` true，本地 user/assistant/terminal persistence 全部 skip | `qa_tasks.py:2128-2136` 注入；`test_task_api.py:3893-3901` 断言头与 public terminal call | gateway-owned persistence | 该 path 可作为目标架构样板 |
| 直接调用 highThinkingQA 或 token 缺失 | headers 不全或 `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` 空 | `_gateway_owned_persistence()` false | `ask.py:184-195` 要求 token 与 gateway 头同时满足 | service-owned persistence | 需要决定直连是否受支持；否则用 gateway 强制入口 |
| `CONVERSATION_*_TARGET=public_service` | 与 gateway-owned 无关 | `chat_persistence.py` 走 authority client read/write | `config.py:104-126,363-372,454-458` 默认 public_service | service 写 public-service | 可保留为过渡 |
| `CONVERSATION_*_TARGET=legacy/shadow_public_service` | 与 gateway-owned 无关 | 走 legacy 或 legacy+shadow authority | `chat_persistence.py:548-565,616-656,718-786` | migration/compat owner | 生产禁用或显式决策后才能删 |

#### 其他 live path 闭环

- PDF/document link：`ask_service.py:355-365` 生成 `/api/v1/view_pdf/{doi}`；gateway public proxy 能处理该路径；frontend 文档打开走 `/api/view_pdf` API helper，见 `frontend-vue/src/api/literature.js:66-108`，也能消费后端 `pdf_links`，见 `frontend-vue/src/services/api.js:270-315`。
- UI 文案：backend 生成中文 `message`；frontend 通过 `splitStepMessage/buildStepPayload` 解析展示，见 `frontend-vue/src/views/Home.vue:794-810,1070-1078`。迁移目标应新增 stable code，不应直接删 message。
- runtime snapshot：ask/stream 每请求调用 Chroma probe，已在 V-308 闭环。
- OpenAI client：`max_retries` no-op，且跨服务共享前需要 fastQA/patent 矩阵。

### 4. 测试护栏闭环

#### 测试护栏清单

| 护栏编号 | 覆盖目标 | 现有证据 | 状态 | 缺口 |
|---|---|---|---|---|
| GUARD-301 | active route surface 只保留 health/ask/ask_stream | `test_fastapi_route_surface_minimal.py:6-23` | 已有 | 删除 retired router 前必须保留 |
| GUARD-302 | retired routes 返回 404 | `test_fastapi_route_surface_minimal.py:27-35`、admin/auth/documents/quota/low-risk/conversation-upload contract tests | 已有 | 应增加 `/api/v1/upload_excel`、system refresh/clear cache 全量断言 |
| GUARD-303 | gateway `/api/v1/view_pdf` public proxy | `gateway/tests/test_public_proxy.py:886-907` | 已有 | highThinkingQA reference link contract 需新增“link authority = gateway/public-service”断言 |
| GUARD-304 | gateway-owned persistence skip | `highThinkingQA/tests/test_ask_router_summary_persistence.py:533-811`、`gateway/tests/test_task_api.py:3893-3901` | 已有 | 普通 gateway direct path 与 task path ownership 差异需单独文档化测试 |
| GUARD-305 | service-owned public_service/legacy/shadow rollout | `tests/test_chat_persistence.py:68-491`、`tests/test_env_loader.py:376-425` | 已有 | 生产禁用 legacy/shadow guard 缺测试 |
| GUARD-306 | frontend step message 兼容 | `tests/test_ask_service_executor.py:522-686` | 已有 | 新增 `message_code` 后需要 backend/frontend 双写 snapshot |
| GUARD-307 | runtime resource snapshot 非热路径 | 无专门测试 | 缺失 | TASK-305 需加 mock 确认 ask 不触发 Chroma count |
| GUARD-308 | OpenAI max_retries 行为 | `tests/test_llm_client.py:440-447` 只测接受参数 | 不足 | 需补 `max_retries=0/2` 行为测试或删除参数测试 |
| GUARD-309 | documents service/storage active 子能力 | `tests/test_documents_service.py:65-130` | 已有 | 若迁到 public-service，需转为 shared/gateway contract |

未运行 `pytest --collect-only highThinkingQA/tests`。原因：用户允许在“担心写缓存则不要运行并记录原因”的条件下跳过；当前 `find` 已显示 `highThinkingQA/.pytest_cache/` 与大量 `__pycache__/` 存在，pytest collection 可能更新缓存或触发导入副作用。本轮严格只读，故仅阅读测试文件和使用 `rg/nl/sed/find/wc` 取证。

### 5. 可实施重构任务拆分

### TASK-301

- 任务目标：按 retired closure 判定表拆分可删除 HTTP 闭包与需保留 active 子能力。
- 范围：`server_fastapi/routers/{conversation,upload,admin,auth,documents,ingest,quota,system}.py`，对应 service/schema/storage，fastapi_migration 404 tests。
- 前置条件：确认 public-service 已覆盖 auth/admin/quota/upload/document/system HTTP contract；确认直连 highThinkingQA 不再对外暴露 retired endpoints。
- 实施步骤：先保留 GUARD-301/GUARD-302；删除或归档只被 retired router 牵引的 admin/ingest/quota/system/upload HTTP 模块；对 conversation/auth/documents 只删除 router 层，保留 active persistence/token/storage 子能力；更新 import 清单。
- 验证：运行 highThinkingQA route surface/404 tests、auth token compat、documents service、conversation persistence tests。
- 风险/回滚：误删 active auth/persistence/storage 会破坏 ask；回滚方式是恢复对应模块并重新注册测试依赖。

### TASK-302

- 任务目标：收敛 persistence ownership，明确普通 gateway direct path 与 task path 的最终 owner。
- 范围：`gateway/app/routers/qa.py`、`gateway/app/services/qa_tasks.py`、`highThinkingQA/server_fastapi/routers/ask.py`、`highThinkingQA/server/services/chat_persistence.py`、conversation authority client。
- 前置条件：产品/部署确认是否所有 thinking 执行都走 gateway task-owned；若保留 direct mode path，则决定 gateway 是否要在 direct path 写 public-service messages。
- 实施步骤：冻结当前决策表；为 direct path 增加 owner contract test；若目标是 gateway-owned，则让 direct path 注入同等 internal headers 并在 gateway 侧持久化 user/assistant terminal；若目标保留 service-owned，则只抽 `PersistenceAdapter`，不删 chat_persistence。
- 验证：gateway task API tests、gateway qa proxy tests、highThinkingQA summary persistence tests、chat_persistence public/legacy/shadow tests。
- 风险/回滚：owner 双写导致重复消息，owner 漏写导致会话丢失；回滚为关闭 direct gateway-owned headers 并恢复 service-owned。

### TASK-303

- 任务目标：将 ask reference PDF link contract 从本地 retired documents router 改为 gateway/public-service authority。
- 范围：`highThinkingQA/server/services/ask_service.py` reference builder，gateway public proxy docs/tests，frontend reference/pdf open helpers。
- 前置条件：确认标准路径是 `/api/view_pdf/{doi}` 还是 `/api/v1/view_pdf/{doi}`，以及 token/query auth 规则。
- 实施步骤：抽 `ReferenceLinkResolver`；默认产出 gateway/public-service route；保留旧 `pdf_url` 字段并可新增 `document_url/link_source`；更新 highThinkingQA ask contract tests 与 gateway proxy contract。
- 验证：highThinkingQA ask sync/stream contract、gateway public proxy view_pdf tests、frontend literature/api tests。
- 风险/回滚：前端无法打开 PDF；回滚为保留 `/api/v1/view_pdf` 兼容输出。

### TASK-304

- 任务目标：把 `ask_service.py` 中文阶段文案迁移为 stable event code + frontend mapper。
- 范围：`ask_service.py:_format_frontend_step_message/_progress_to_step_event`，`frontend-vue/src/views/Home.vue` step mapper，相关 tests。
- 前置条件：定义 `stage/message_code/status/data` schema，前端能基于 code 渲染中文文案。
- 实施步骤：后端先双写 `message_code/default_message/message`；前端优先读 code，fallback 旧 message；迁移测试后再瘦身 backend 中文文案。
- 验证：`test_ask_service_executor.py` step tests、frontend mapper tests、stream snapshot。
- 风险/回滚：进度面板文案缺失或标题拆分错误；回滚为继续使用旧 `message`。

### TASK-305

- 任务目标：将 runtime resource snapshot 从 ask 热路径移到 health/diagnostics 或 TTL probe。
- 范围：`ask_service.py:148-179,633-635,764-766`，health/component status。
- 前置条件：确认 ops 是否依赖每请求 snapshot 日志。
- 实施步骤：抽 `RuntimeResourceProbe`；启动或 health 刷新 probe；ask 只记录 cached status；增加开关保留 inline probe 一版。
- 验证：新增 ask 不触发 Chroma count 的单测；health probe 单测；stream first event latency 回归。
- 风险/回滚：排障日志减少；回滚为打开 inline probe 开关。

### TASK-306

- 任务目标：建立 OpenAI-compatible client 跨服务矩阵并处理 `max_retries` no-op。
- 范围：`highThinkingQA/agent_core/openai_compat.py`、`llm_client.py`、fastQA LLM integration、patent upstream HTTP/provider。
- 前置条件：逐行读取 fastQA/patent 等价 client，列出 chat/stream/embedding/auth/timeout/pool/retry/observability 差异。
- 实施步骤：先产出矩阵；决定 retry policy 所在层；实现 retry 或删除参数；stream retry 必须默认禁用或明确不可重放。
- 验证：max_retries 行为测试、timeout/status retry tests、stream 不重放 tests、embedding retry tests。
- 风险/回滚：不当 retry 会重复 token或增加上游压力；回滚为 no-op 参数加 deprecation warning。

### 6. 不可立即处理项与阻塞原因

- 不能立即删除 `chat_persistence.py` legacy/shadow 路径：普通 gateway direct path 当前仍是 service-owned persistence，且 config 仍允许 legacy/shadow。
- 不能立即删除 `documents_service/paper_storage/object_reader`：documents router 未注册，但 tests 与 PDF link/storage contract 仍使用这些能力。
- 不能立即把中文 `message` 从后端删掉：frontend 当前主要从 `data.message` 构造 step title/detail，后端测试也固定文案。
- 不能立即抽 OpenAI-compatible shared client：还缺 fastQA/patent 逐行矩阵，不能把 highThinkingQA 行为当唯一基线。
- 不能立即移除 `/api/v1/view_pdf` 输出：gateway 已支持该路径，frontend/测试仍消费；应先定义标准 route 与兼容期。
- 不能立即运行 pytest collect：本轮只读硬约束下，pytest 可能更新 `.pytest_cache` 或导入生成缓存。

### 7. 最终进入重构前检查清单

- [ ] GUARD-301/GUARD-302 作为必跑，确认 active route surface 与 retired 404 未回归。
- [ ] 完成 TASK-301 的下游能力清单，明确每个 service/schema/storage 是 active、retired-only 还是 migration-only。
- [ ] 完成 TASK-302 的 persistence owner 决策，普通 direct path 与 task path 不能继续语义不明。
- [ ] 明确 `/api/view_pdf` 与 `/api/v1/view_pdf` 的 gateway/public-service canonical contract，并更新 TASK-303。
- [ ] 为 UI step event 增加 stable code/data 双写方案，保留旧 `message` 一版。
- [ ] 为 runtime resource snapshot 增加非热路径 probe 测试。
- [ ] 产出 fastQA/patent/highThinkingQA OpenAI-compatible client 逐行矩阵，决定 `max_retries` 实现或删除。
- [ ] 确认生产部署禁止或允许 `legacy/shadow_public_service`，并补对应 config guard 测试。
- [ ] 重构执行前先跑只读可接受的测试收集或在可写测试阶段跑完整 highThinkingQA/gateway/frontend 护栏。
