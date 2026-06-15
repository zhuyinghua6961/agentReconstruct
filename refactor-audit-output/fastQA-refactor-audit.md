# fastQA 重构审计文档

> 状态：已完成第一轮只读审计。本文档只记录基于代码阅读得到的证据，审计产物位于独立目录，不修改业务代码。

## 1. 审计范围

- 已阅读目录：`fastQA/app/`、`fastQA/tests/`、`fastQA/docs/`、`fastQA/pyproject.toml`。
- 已阅读关键文件：`app/main.py`、`app/routers/qa.py`、`app/routers/health.py`、`app/modules/documents/api.py`、`app/core/config.py`、`app/core/env_loader.py`、`app/core/runtime.py`、`app/integrations/llm/openai_compat.py`、`app/integrations/llm/shared_http_pool.py`、`app/modules/qa_kb/`、`app/modules/qa_pdf/`、`app/modules/qa_tabular/`、`app/modules/generation_pipeline/`、`app/modules/graph_kb/`、`app/modules/storage/`、`app/services/request_adapter.py`、`app/services/file_route_service.py`、`app/services/conversation_authority_client.py`、`app/services/conversation_context_builder.py`、`app/services/stream_contract.py`。
- 未覆盖或需要本地进一步验证的范围：未运行测试；`services/file_routes.py` 与 `services/file_route_service.py` 两套 file-route 入口的 live 占比需进一步调用图确认。

## 2. 当前 live path

### 2.1 服务入口

- app factory / main entry：`fastQA/app/main.py:create_app()`。
- router 注册位置：`fastQA/app/main.py` 注册 `health_router`、`documents_router`、`qa_router`。
- lifespan/startup/shutdown：`_lifespan()` 在 shutdown 关闭 `shared_llm_adapter`、graph KB、generation runtime、Redis。
- runtime 状态：`app.state` 挂载 settings、logger、ask_limiter、component_status、Redis、generation runtime、Neo4j graph client、shared LLM pool、stage2 hot pools、PDF web bindings、conversation persistence hooks。

关键证据：

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        yield
    finally:
        close = getattr(getattr(app.state, "shared_llm_adapter", None), "close", None)
        if callable(close):
            close()
        close_graph_kb(app.state)
        close_generation_runtime(app.state)
        close_redis(app.state)
```

```python
app.state.ask_limiter = AskConcurrencyLimiter(max_concurrent=settings.ask_stream_max_concurrent)
app.state.redis_service = None
app.state.generation_runtime = None
app.state.neo4j_client = None
app.state.shared_llm_http_pool = None
app.state.stage2_chat_hot_pool = None
app.state.stage2_rerank_hot_pool = None
app.state.pdf_web_bindings = None
app.include_router(health_router)
app.include_router(documents_router)
app.include_router(qa_router)
```

### 2.2 对外接口路径

| 接口路径 | 方法 | 所在文件 | 当前职责 | 是否 active |
|---|---|---|---|---|
| `/api/fast/ask` | POST | `fastQA/app/routers/qa.py` | fast 同步 JSON 聚合 | active live path |
| `/api/v1/fast/ask` | POST | `fastQA/app/routers/qa.py` | fast ask v1 alias，但绑定 `ask_stream()` | active live path，契约风险 |
| `/api/fast/ask_stream` | POST | `fastQA/app/routers/qa.py` | fast SSE | active live path |
| `/api/v1/fast/ask_stream` | POST | `fastQA/app/routers/qa.py` | fast SSE v1 alias | active live path |
| `/api/ask` | POST | `fastQA/app/routers/qa.py` | legacy sync alias | active live path/compat |
| `/api/ask_stream` | POST | `fastQA/app/routers/qa.py` | legacy SSE alias | active live path/compat |
| `/api/v1/ask` | POST | `fastQA/app/routers/qa.py` | v1 alias，但绑定 `ask_stream()` | active live path/compat，契约风险 |
| `/api/v1/ask_stream` | POST | `fastQA/app/routers/qa.py` | v1 SSE alias | active live path/compat |
| `/api/{mode}/ask` | POST | `fastQA/app/routers/qa.py` | mode alias dispatch | active live path |
| `/api/v1/{mode}/ask` | POST | `fastQA/app/routers/qa.py` | mode v1 alias dispatch | active live path |
| `/api/{mode}/ask_stream` | POST | `fastQA/app/routers/qa.py` | mode SSE alias dispatch | active live path |
| `/api/v1/{mode}/ask_stream` | POST | `fastQA/app/routers/qa.py` | mode SSE alias dispatch | active live path |
| `/healthz`, `/api/health` | GET | `fastQA/app/routers/health.py` | runtime readiness/components | active live path |
| documents/PDF related paths | mixed | `fastQA/app/modules/documents/api.py` | PDF view/check/extract/reference preview | active live path |

接口注册证据：

```python
@router.post("/api/ask")
@router.post("/api/fast/ask")
def ask(...):
    ...

@router.post("/api/ask_stream")
@router.post("/api/v1/ask")
@router.post("/api/v1/ask_stream")
@router.post("/api/fast/ask_stream")
@router.post("/api/v1/fast/ask")
@router.post("/api/v1/fast/ask_stream")
def ask_stream(...):
    ...
```

### 2.3 核心调用链

```text
gateway -> fastQA /api/fast/ask_stream
  -> request_adapter adapts normalized gateway contract
  -> qa.py dispatches route: kb_qa/pdf_qa/tabular_qa/hybrid_qa
  -> optional graph_kb direct/RAG fallback
  -> generation_runtime / PDF / table / storage modules
  -> stream event mapper and authority persistence hooks
  -> gateway receives SSE and may persist/relay
```

## 3. 发现的重构点

### R-001：`app/routers/qa.py` 是巨型调度器

- 严重程度：P1
- 类型：巨型模块 / router god-object / contract+runner 混杂
- 代码位置：
  - `fastQA/app/routers/qa.py`
  - `_iter_events()`
  - `ask()`
  - `ask_stream()`
- 接口路径：
  - `/api/fast/ask`
  - `/api/v1/fast/ask`
  - `/api/fast/ask_stream`
  - `/api/v1/fast/ask_stream`
  - `/api/ask`
  - `/api/ask_stream`
- 关键代码片段：

```python
if route == "kb_qa":
    conversation_context = build_conversation_context(...)
    routing_result = route_graph_kb_v2(...)
    if routing_result.mode == "direct_answer":
        yield from _iter_graph_kb_events(...)
    for event in qa_kb_service.iter_answer_events(...):
        yield _merge_graph_v2_event(event, graph_v2_metadata)
if route == "pdf_qa":
    yield from iter_pdf_route_events(...)
if route == "tabular_qa":
    yield from iter_tabular_route_events(...)
if route == "hybrid_qa":
    ...
```

- 当前问题：约 1614 行，同文件处理 HTTP aliases、gateway adapter error、authority persistence、Graph fallback、KB runner、PDF/table/hybrid dispatch、SSE enrichment、sync aggregation、pool timeout、reference links。fastQA 已从“fast QA”膨胀为 KB/PDF/table/hybrid/Graph/cache/documents/retrieval/storage 平台。
- 建议重构方式：拆 `routers/qa.py`、`services/qa_dispatcher.py`、`runners/kb_runner.py`、`runners/pdf_runner.py`、`runners/tabular_runner.py`、`runners/hybrid_runner.py`、`runners/graph_kb_runner.py`、`services/stream_event_mapper.py`、`services/error_event_builder.py`。
- 是否可抽共享包：contract、stream、authority hooks 可抽。
- 建议目标模块：`fastQA/app/services/qa_dispatcher.py`、`fastQA/app/runners/*`、`packages/agent_common/contracts/gateway_ask_request.py`。
- 设计模式建议：Ports & Adapters、Strategy dispatcher。
- 影响范围：所有 fast ask routes。
- 风险：高。alias 行为、SSE first-byte、terminal persistence、Graph fallback 都不能变。
- 测试计划：保留并扩展 `fastQA/tests/test_qa_route_aliases.py`、`test_qa_placeholder.py`、`test_qa_pool_timeout_contract.py`、`test_fastqa_kb_graph_integration.py`。
- 是否可立即删除：否。
- 删除或迁移前置条件：先冻结路径契约表和 stream event golden tests。

### R-002：`request_adapter.py` 是有价值的 gateway contract 防御层，但应共享

- 严重程度：P1
- 类型：gateway contract 重复 / 共享能力候选
- 代码位置：
  - `fastQA/app/services/request_adapter.py`
  - `adapt_gateway_request()`
- 接口路径：
  - 所有 ask/ask_stream
- 关键代码片段：

```python
if not explicit_route and (has_file_execution_signal or has_file_scope_signal):
    raise RequestAdapterError(code="route_required", ...)

if route in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
    if not raw_source_scope:
        _require_contract_field(route=route, field="source_scope")
    if not raw_turn_mode:
        _require_contract_field(route=route, field="turn_mode")
```

- 当前问题：该模块已经承担 normalized gateway ask contract 防御，但只存在 fastQA 内部。thinking/patent 也有类似字段校验，容易产生差异。
- 建议重构方式：抽 `GatewayAskRequest`、`Route`、`SourceScope`、`TurnMode`、`ExecutionFile`、`RequestAdapterError` 到共享包，fastQA 保留 thin adapter 处理 fast-specific mode。
- 是否可抽共享包：是。
- 建议目标模块：`packages/agent_common/contracts/gateway_ask_request.py`、`qa_route.py`、`source_scope.py`、`execution_file.py`。
- 设计模式建议：Adapter、Anti-corruption Layer。
- 影响范围：gateway -> fastQA contract，后续 thinking/patent contract。
- 风险：中。fastQA 要求 `actual_mode == "fast"`，patent 要求 `actual_mode == "patent"`，共享层需支持 service-specific policy。
- 测试计划：迁移 `test_request_adapter.py` 为共享 contract tests，并在 fastQA 保留 service-specific tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：主 gateway contract 版本化。

### R-003：`file_route_service.py` 保留 legacy `MaterialScienceAgent` compatibility shim

- 严重程度：P1
- 类型：遗留代码 / compatibility shim still live
- 代码位置：
  - `fastQA/app/services/file_route_service.py`
  - `_build_pdf_agent()`
  - `smart_query()`
  - `query_pdf_directly()`
- 接口路径：
  - `pdf_qa`
  - `hybrid_qa`
- 关键代码片段：

```python
def _build_pdf_agent(self, *, app_state: Any):
    class _PdfAgent:
        # Compatibility shim for legacy MaterialScienceAgent entrypoints kept during V2 rollout.
        llm = service._resolve_llm(app_state=app_state)

        def smart_query(...):
            ...
        def query_pdf_directly(...):
            ...
```

- 当前问题：这是 `deprecated but still referenced`，不是死代码。`services/file_routes.py` 调 `_build_pdf_agent()`，`qa_pdf/streaming.py` 调 `smart_query()`，`qa_pdf/service.py` 调 `query_pdf_directly()`。
- 建议重构方式：定义正式 `PdfKbVerificationPort` 与 `PdfDirectQueryPort`，替换动态 `_PdfAgent` 类；保留旧入口适配一版。
- 是否可抽共享包：接口可抽，业务实现不建议抽。
- 建议目标模块：`fastQA/app/modules/qa_pdf/ports.py`、`fastQA/app/services/pdf_query_adapter.py`。
- 设计模式建议：Port/Adapter、Compatibility Adapter。
- 影响范围：PDF QA、hybrid PDF evidence。
- 风险：中高。PDF direct query 和 KB verification 行为可能被测试固定。
- 测试计划：`fastQA/tests/test_file_routes_materialization.py`、`test_qa_placeholder.py` PDF 分支、`qa_pdf` tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：PDF service 不再要求 legacy agent shape。

### R-004：`FASTQA_NOT_READY` placeholder 仍被测试固定

- 严重程度：P2
- 类型：scaffold / placeholder
- 代码位置：
  - `fastQA/app/modules/qa_kb/service.py`
  - `iter_phase1_placeholder_events()`
- 接口路径：
  - KB ask fallback
- 关键代码片段：

```python
yield {
    "type": "error",
    "code": "FASTQA_NOT_READY",
    "error": "fastQA 暂未接入真实执行闭包",
    "message": "fastQA execution closure has not been extracted yet",
}
```

- 当前问题：service 层保留 Phase 1 placeholder，同时 router 在 runtime not ready 时也发 `FASTQA_NOT_READY`。这会让“未接入执行闭包”和“runtime degraded”混在同一错误码下。
- 建议重构方式：统一为 `RuntimeReadinessError` 和 `NotReadyEventFactory`，把历史 placeholder 文案标记为 compatibility。
- 是否可抽共享包：错误 event shape 和 error code registry 可抽。
- 建议目标模块：`fastQA/app/services/error_event_builder.py`、`packages/agent_common/contracts/error_event.py`。
- 设计模式建议：Factory。
- 影响范围：runtime not ready、tests、frontend error message。
- 风险：中。`fastQA/tests/test_qa_kb_service.py` 明确断言该 code。
- 测试计划：更新前先加兼容测试，确保 code 不变、detail 更清晰。
- 是否可立即删除：否。
- 删除或迁移前置条件：frontend/gateway 不再依赖旧 message。

### R-005：generation runtime 重复解析 LLM、embedding、vector DB 配置

- 严重程度：P2
- 类型：重复配置
- 代码位置：
  - `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
  - `fastQA/app/core/config.py`
  - `fastQA/app/core/runtime.py`
- 接口路径：
  - health/readiness
  - all QA execution routes
- 关键代码片段：

```python
app.state.generation_runtime = None
app.state.generation_runtime_ready = False
app.state.neo4j_client = None
app.state.graph_kb_ready = False
app.state.shared_llm_http_pool = None
app.state.shared_llm_adapter = None
app.state.stage2_chat_hot_pool = None
app.state.stage2_rerank_hot_pool = None
```

- 当前问题：LLM、embedding、vector DB、shared pool、stage2 hot pool env 解析分散；`file_route_service.py` 又解析 PDF QA 相关限制。与 patent/highThinkingQA 重复概率高。
- 建议重构方式：抽 `ModelEndpointSettings`、`EmbeddingSettings`、`VectorStoreSettings`、`RuntimeSettings`；统一优先级和 secret handling。
- 是否可抽共享包：是。
- 建议目标模块：`packages/agent_common/config/{model_endpoint.py,embedding.py,neo4j.py,storage.py,http_pool.py}`。
- 设计模式建议：Configuration Object。
- 影响范围：startup、health、LLM/embedding/rerank 调用。
- 风险：中。配置优先级和 env alias 兼容容易出错。
- 测试计划：`fastQA/tests/test_generation_runtime_bootstrap.py`、`test_redis_runtime.py`、`test_stage2_hot_connection_runtime.py`、`test_env_loader.py`。
- 是否可立即删除：否。
- 删除或迁移前置条件：配置兼容矩阵和 env alias tests。

### R-006：`qa_tabular/service.py` 职责过大

- 严重程度：P2
- 类型：巨型模块 / 边界混合
- 代码位置：
  - `fastQA/app/modules/qa_tabular/service.py`
  - `iter_answer_events()`
- 接口路径：
  - `tabular_qa`
  - `hybrid_qa` with table scope
- 关键代码片段：

```python
def iter_answer_events(...):
    ...
    # file readiness / strict storage / workbook load
    # tabular planner / executor
    # optional PDF/KB hybrid evidence
    # LLM synthesis
    # stream cleanup and references/done event
```

- 当前问题：同一 service 处理文件状态判断、MinIO-only 策略、表格加载、tabular planner、tabular executor、PDF hybrid evidence、KB evidence、LLM synthesis、streaming clean、references/done event。
- 建议重构方式：拆 `file_readiness.py`、`workbook_loader_adapter.py`、`tabular_runner.py`、`hybrid_evidence.py`、`tabular_stream.py`。
- 是否可抽共享包：文件 readiness 和 uploaded-file contract 可抽。
- 建议目标模块：`fastQA/app/modules/qa_tabular/{runner.py,evidence.py,streaming.py}`。
- 设计模式建议：Pipeline、Strategy。
- 影响范围：table QA、hybrid QA、file readiness。
- 风险：中高。表格和 hybrid evidence 易有边界 case。
- 测试计划：`fastQA/tests/test_file_routes_materialization.py`、tabular service/planner/executor tests、hybrid route tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：先固化 tabular event sequence。

### R-007：`openai_compat.py` 是可抽共享的 LLM transport god-object

- 严重程度：P1
- 类型：共享 LLM / 巨型模块
- 代码位置：
  - `fastQA/app/integrations/llm/openai_compat.py`
  - `fastQA/app/integrations/llm/shared_http_pool.py`
- 接口路径：
  - all generation paths
- 关键代码片段：

```python
class OpenAICompatibleChatClient:
    ...
class AsyncOpenAICompatibleChatClient:
    ...
# auth header normalization, httpx client ownership, timeout,
# stream parser, thinking controls, auth logging, model call logging
```

- 当前问题：约 1162 行，混合 auth/header、httpx pool ownership、timeout、SSE parser、thinking controls、upstream auth logging、model call logging、SDK-compatible facade、retry/timeout。highThinkingQA 和 patent 也有类似实现。
- 建议重构方式：拆 `auth.py`、`transport.py`、`stream_parser.py`、`retry_policy.py`、`model_call_logger.py`、`chat_adapter.py`。
- 是否可抽共享包：是，优先级高。
- 建议目标模块：`packages/agent_common/llm/{auth.py,openai_compatible.py,stream_parser.py,http_pool.py,retry_policy.py,model_call_logger.py,upstream_auth_logger.py}`。
- 设计模式建议：Strategy for auth mode、Adapter for OpenAI-compatible/DashScope/vLLM、Decorator/Middleware for logging/retry/timeout.
- 影响范围：KB generation、PDF/table/hybrid synthesis、stage2 hot pools。
- 风险：高。stream parser 和 timeout 行为必须保持。
- 测试计划：LLM compat tests、stage2 hot pool tests、pool timeout contract tests、stream parser tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：跨服务对齐 API surface。

### R-008：`pyproject.toml` 存在重复依赖

- 严重程度：P3
- 类型：依赖文件清理
- 代码位置：
  - `fastQA/pyproject.toml`
- 接口路径：
  - 不直接涉及运行时接口
- 关键代码片段：

```toml
"openai>=1.40,<2.0",
"openpyxl>=3.1,<4.0",
"pandas>=2.0,<3.0",
"pymupdf>=1.24,<2.0",
...
"openai>=1.0,<2.0",
"pandas>=2.0,<3.0",
"openpyxl>=3.1,<4.0",
"PyMuPDF>=1.24,<2.0",
```

- 当前问题：重复依赖和大小写差异会增加安装解析噪声。
- 建议重构方式：去重并统一 casing/range，保留更严格或项目实际验证过的范围。
- 是否可抽共享包：否。
- 建议目标模块：不适用。
- 设计模式建议：不适用。
- 影响范围：dependency install/build。
- 风险：低。
- 测试计划：包安装/`pip check`、相关 PDF/table tests。
- 是否可立即删除：是，作为独立 chore。
- 删除或迁移前置条件：确认 lock/CI 环境不依赖重复项顺序。

### R-009：`app.state` runtime registry 过载

- 严重程度：P2
- 类型：生命周期混乱
- 代码位置：
  - `fastQA/app/main.py`
  - `fastQA/app/core/runtime.py`
- 接口路径：
  - all routes
- 关键代码片段：

```python
app.state.settings = settings
app.state.ask_limiter = AskConcurrencyLimiter(...)
app.state.component_status = {}
app.state.redis_bindings = None
app.state.redis_client = None
app.state.generation_runtime = None
app.state.neo4j_client = None
app.state.shared_llm_http_pool = None
app.state.shared_llm_adapter = None
app.state.stage2_chat_hot_pool = None
app.state.stage2_rerank_hot_pool = None
```

- 当前问题：runtime 对象分散挂载，health/router/tests 直接访问，后续替换 bootstrap/lifecycle 时 blast radius 大。
- 建议重构方式：封装 `FastQARuntimeContext`，只把 context 放入 `app.state.container`，保留兼容 bridge。
- 是否可抽共享包：ResourceRegistry、ComponentStatusRegistry 可抽。
- 建议目标模块：`fastQA/app/runtime/container.py`、`packages/agent_common/runtime/`.
- 设计模式建议：ServiceContainer、ResourceRegistry。
- 影响范围：startup/shutdown、health、QA dispatcher。
- 风险：中。
- 测试计划：`test_health.py`、`test_generation_runtime_bootstrap.py`、`test_redis_runtime.py`、pool/hot lane tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：先做只读 container façade。

## 4. 可抽共享能力清单

| 能力 | 当前重复位置 | 建议共享模块 | 迁移优先级 |
| -- | ------ | ------ | ----- |
| Gateway ask normalized contract | `fastQA/app/services/request_adapter.py` | `packages/agent_common/contracts/gateway_ask_request.py` | P1 |
| route/source_scope/turn_mode/execution_files | `request_adapter.py`、backend request models | `packages/agent_common/contracts/{qa_route.py,source_scope.py,turn_mode.py,execution_file.py}` | P1 |
| SSE event normalize/tap/terminal summary | `stream_contract.py`、`routers/qa.py` | `packages/agent_common/sse/` | P1 |
| Conversation authority client | `conversation_authority_client.py` | `packages/agent_common/clients/conversation_authority.py` | P1 |
| Conversation context normalization | `conversation_context_builder.py` | `packages/agent_common/context/conversation.py` | P2 |
| OpenAI-compatible transport | `integrations/llm/openai_compat.py`、`shared_http_pool.py` | `packages/agent_common/llm/` | P1 |
| Storage/upload materialization contract | `modules/storage/`、`file_route_service.py` | `packages/agent_common/storage/` | P2 |
| Runtime health component status | `core/runtime.py`、`routers/health.py` | `packages/agent_common/runtime/component_status.py` | P2 |

## 5. 可清理遗留代码清单

| 代码位置 | 当前状态 | 是否注册 | 是否被引用 | 建议处理 |
| ---- | ---- | ---- | ----- | ---- |
| `fastQA/app/routers/qa.py` `/api/ask`, `/api/ask_stream`, `/api/v1/ask` aliases | active live path / compatibility | 是 | 是 | 标记 compatibility，不能直接删 |
| `fastQA/app/routers/qa.py` `/api/v1/fast/ask` 绑定 `ask_stream()` | active live path / unknown contract risk | 是 | 需要前端/gateway 确认 | 先加 contract test 明确是否预期 |
| `fastQA/app/services/file_route_service.py:_build_pdf_agent` | deprecated but still referenced | 不适用 | 是 | 迁移到正式 PDF ports 后删除 |
| `fastQA/app/services/file_route_service.py:smart_query` | deprecated but still referenced | 不适用 | 是 | 先替换 `qa_pdf/streaming.py` 调用 |
| `fastQA/app/services/file_route_service.py:query_pdf_directly` | deprecated but still referenced | 不适用 | 是 | 先替换 `qa_pdf/service.py` 调用 |
| `fastQA/app/modules/qa_kb/service.py:iter_phase1_placeholder_events` | scaffold / placeholder | 不适用 | 是，tests 固定 | 统一 NotReadyEvent 后再删 |
| `fastQA/app/modules/retrieval/.gitkeep` | scaffold / placeholder | 不适用 | 否 | 可保留目录占位或删除空目录 |
| `fastQA/docs/legacy_*` | archive / historical baseline | 不适用 | 文档引用未知 | 不进入主线，另做文档清理 |
| `fastQA/pyproject.toml` duplicate deps | deprecated and unregistered cleanup | 不适用 | build 读取 | 可独立 chore 去重 |

## 6. 接口与契约风险

- gateway -> backend contract：fastQA 已严格校验 `requested_mode`、`actual_mode`、`route`、`source_scope`、`turn_mode`、`execution_files`。共享前需支持 service-specific mode。
- frontend -> gateway contract：`/api/v1/fast/ask` 在 fastQA 直接绑定 SSE，有命名/行为风险。若 gateway 永远调用 `/api/fast/ask` 则风险较低，但 direct frontend route 需确认。
- backend -> public-service contract：conversation authority client 内部 hardcode public-service internal conversation APIs，适合共享 SDK。
- internal token/auth headers：LLM/upstream auth 和 public-service internal auth 都分散。
- SSE event schema：router 生成 metadata/content/done/error 事件，并做 sync aggregation，需要 golden tests。
- task event schema：fastQA 本身不拥有 gateway task，但 stream event 会被 gateway relay。

## 7. 测试计划

- 单元测试：request adapter、file route service、tabular runner、openai compat parser、runtime config。
- contract test：gateway ask contract、route/source_scope/turn_mode/execution_files。
- stream/SSE test：ask_stream metadata/content/done/error、first-byte、pool timeout SSE。
- integration smoke test：gateway -> fastQA ask_stream，KB/PDF/table/hybrid routes 各一条。
- backward compatibility test：`/api/ask`、`/api/ask_stream`、`/api/v1/ask`、`/api/v1/fast/ask`。
- failure/cancel/retry test：runtime not ready、pool timeout、upstream error、stream abort。
- persistence test：conversation authority hooks user/assistant summary/terminal。
- quota/auth test：fastQA 主要接受 gateway normalized request；auth/quota 在 gateway/public-service 侧验证。
- file route test：PDF/table execution_files required、MinIO/local availability、hybrid scope。

## 8. 建议重构顺序

1. P1：先冻结 fastQA route alias contract，特别是 `/api/v1/fast/ask` 的 SSE 行为。
2. P1：抽 `request_adapter.py` 到共享 contract，fastQA 保留 service-specific policy。
3. P1：拆 `openai_compat.py` 的 auth/stream parser/http pool/logging，先不改变 public API。
4. P1：拆 `routers/qa.py` 为 dispatcher 和 runner，router 保持现有 decorator。
5. P2：替换 `MaterialScienceAgent` shim 为 PDF ports。
6. P2：拆 tabular service 的 file readiness/evidence/runner/stream。
7. P2：引入 `FastQARuntimeContext` 和 ResourceRegistry。
8. P3：清理 `pyproject.toml` 重复依赖。

## 9. 需要进一步确认的问题

1. `POST /api/v1/fast/ask` 是否有意返回 SSE；如果 gateway/前端认为它是同步 JSON，就是接口契约风险。
2. `FASTQA_ALLOW_PLACEHOLDER_FALLBACK` 当前是否仍有生产意义。
3. `file_route_service.FileRouteService.iter_events()` 与 `services/file_routes.py` 是否都是 live path。
4. `conversation_authority_client.py` 是否应归 public-service SDK。
5. 抽 OpenAI transport 前需确认 highThinkingQA/patent 的兼容差异。

## 第二轮深度补充

> 状态：第二轮只读审计补充。已按要求执行 8 组扫描命令，并继续阅读 `fastQA/app/main.py`、`app/core/`、`app/routers/`、`app/services/`、`app/integrations/`、`app/modules/`、`tests/`、`pyproject.toml`、`README.md`。本轮只追加本文档，未运行测试，未修改业务代码。

### 10. 指定扫描命令结果摘要

已执行命令：

```text
find fastQA -type f
find fastQA -type f \( -name "*.py" -o -name "*.js" -o -name "*.vue" \) -print0 | xargs -0 wc -l | sort -nr | head -50
rg "APIRouter|@router|app.include_router|path:|fetch|axios|EventSource" fastQA
rg "deprecated|legacy|fallback|scaffold|placeholder|NOT_READY|not ready|shim|compat|TODO|FIXME|shadow|archive|obsolete|retired|rollout" fastQA
rg "app\.state|request\.app\.state" fastQA
rg "LLM_|EMBEDDING_|RERANK|REDIS|MINIO|NEO4J|VECTOR_DB|AUTH|TOKEN|RESOURCE_ROOT|RUNTIME_ROOT|STATE_ROOT" fastQA
rg "OpenAI|openai|embedding|rerank|auth_headers|httpx|stream|SSE|api_key|Bearer" fastQA
rg "requested_mode|actual_mode|source_scope|turn_mode|execution_files|selected_file_ids|primary_file_id|gateway-owned|X-Gateway" fastQA
```

关键结果：

- `find fastQA -type f` 显示 fastQA 不只是 `app/`、`tests/`、`pyproject.toml`、`README.md`，还包含 `docs/`、`scripts/`、`.runtime/`、`.pytest_cache/`、大量 `__pycache__` 和 `logs/alignment_audit_*.jsonl`。本轮结论只基于代码、测试和配置，不把 README 当作事实源。
- 代码行数 top 结果显示当前热点：`generation_pipeline/stage2_retrieval.py` 1778 行、`qa_kb/orchestrators/generation.py` 1673 行、`routers/qa.py` 1614 行、`integrations/llm/openai_compat.py` 1162 行、`core/runtime.py` 863 行、`qa_tabular/planner.py` 966 行。
- router 扫描确认 `main.py` 注册 `health_router`、`documents_router`、`qa_router`；`qa.py` 注册 12 个 ask/stream alias；`documents/api.py` 注册 view/check/extract/reference preview。
- legacy/fallback 扫描确认 `MaterialScienceAgent` compatibility shim、`FASTQA_NOT_READY`、Graph legacy template、PDF sidecar compatible、storage legacy local fallback、conversation authority legacy target、README 过期状态说明均存在。
- `app.state` 扫描确认运行时对象直接挂载并被 router/health/tests 访问：settings、ask_limiter、component_status、redis、generation_runtime、neo4j_client、shared_llm_http_pool、shared_llm_adapter、stage2 hot pools、pdf_web_bindings、persistence hooks。
- env 扫描确认 fastQA 自持 LLM/embedding/rerank/Redis/MinIO/Neo4j/vector DB/auth/token/resource/runtime/state 配置，不是单纯 gateway worker。
- LLM 扫描确认 OpenAI-compatible transport、stream parser、httpx pool、auth headers、thinking controls、embedding/rerank 客户端都在 fastQA 内。
- gateway contract 扫描确认 `requested_mode`、`actual_mode`、`source_scope`、`turn_mode`、`execution_files`、`selected_file_ids`、`primary_file_id`、`X-Gateway-*` 都进入 live code 和 tests。

### 11. 第一轮结论复核

- R-001 复核：成立且严重程度应保持 P1。`fastQA/app/routers/qa.py:33-59` 定义 ask 请求模型，`149-420` 做 auth/authority/persistence hook，`691-905` 做 route dispatch，`1085-1322` 做 SSE event mapper/error mapper，`1386-1449` 做 sync 聚合，`1452-1611` 做 HTTP alias 注册。
- R-002 复核：成立。`request_adapter.py:29-60` 的 `GatewayAskRequest` 已经是共享 contract 雏形；`247-413` 同时承担字段归一化、service-specific mode policy、file route contract 防御。
- R-003 复核：成立且 live。`file_routes.py:216-225` 在 KB verification 场景调用 `file_route_service._build_pdf_agent()`；`qa_pdf/streaming.py:46-66` 调 `agent.smart_query()`；`qa_pdf/service.py:283-324` 调 `agent.query_pdf_directly()`。
- R-004 复核：成立但 README 不能作为状态依据。`qa_kb/service.py:24-55` 的 phase1 placeholder 仍存在；`qa.py:806-818` runtime not ready 也发 `FASTQA_NOT_READY`。代码里同时存在真实 generation path 和 placeholder path。
- R-005/R-007/R-009 复核：成立。`config.py:147-249`、`core/runtime.py:471-751`、`openai_compat.py:162-498`、`shared_http_pool.py:80-258` 分散承担配置、生命周期、HTTP pool、transport 观测。
- R-006 复核：成立且范围比第一轮更大。`file_routes.py:377-530` 已先做 materialize/KB evidence，再进入 `qa_tabular/service.py:331-652`；service 内仍重复 readiness、PDF hybrid evidence、KB evidence、LLM synthesis、stream done。
- R-008 复核：成立。`pyproject.toml` 同时声明 `openai>=1.40,<2.0` 和 `openai>=1.0,<2.0`、`pymupdf` 和 `PyMuPDF`、重复 `pandas/openpyxl/httpx`。
- 第一轮需修正：`README.md` 仍称“real kb_qa execution closure has not been extracted yet”和“39 passed”，但代码证据显示 `GenerationDrivenRAG`、Graph V2、PDF/table/hybrid、qa_cache 和大量 tests 已存在。因此 README 是滞后文档，不应用于判断当前 live path。

### 12. fastQA 是否膨胀成执行平台

结论：是。fastQA 已从“fast-mode ask contract + kb_qa worker”膨胀成一个包含 gateway contract、conversation authority hooks、Redis cache、MinIO/local storage、documents API、PDF QA、tabular QA、hybrid QA、Graph KB、generation RAG、LLM transport、embedding/rerank/runtime health 的执行平台。

| 模块 | active | 残留/遗留 | 归属判断 | 可共享 | 迁移建议 | 测试证据 |
|---|---|---|---|---|---|---|
| `qa_kb/` | active：`qa.py:823-844` 调 `qa_kb_service.iter_answer_events()` | `iter_phase1_placeholder_events()`、`QA_QUERY_PIPELINE_MODE` legacy aliases | fastQA 业务执行 | stream/result models 可共享 | 保留 runner，拆 orchestrator/cache 边界 | `test_qa_kb_service.py`、`test_qa_generation_orchestrator.py` |
| `qa_pdf/` | active：`file_routes.py:244-375`、`qa_pdf/service.py:326-383` | `legacy_answer_from_pdf`、sidecar compatible、`query_pdf_directly` | fastQA 文件 QA，sidecar 可独立 | PDF stream event/ports 可共享 | 引入正式 `PdfQaRunner`/`PdfKbVerificationPort` | `test_qa_pdf_service.py`、`test_file_routes_materialization.py` |
| `qa_tabular/` | active：`file_routes.py:377-530`、`qa_tabular/service.py:331-652` | legacy extracted module docstring | fastQA 文件 QA，部分文件 readiness 应归公共 | uploaded-file readiness 可共享 | 拆 readiness/loader/planner/executor/evidence/stream | `test_qa_tabular_service.py`、`test_qa_tabular_summary_context.py` |
| `graph_kb/` | active：`qa.py:730-785` V2、`786-804` classic fallback | legacy route/template/fallback fields | fastQA domain graph runner，Neo4j bootstrap 可共享 | Neo4j client/metadata partially | 保留 V2，隔离 classic compat | `test_graph_kb_*`、`test_fastqa_kb_graph_integration.py` |
| `generation_pipeline/` | active：runtime bootstrap、`qa_kb/orchestrators/generation.py` | fallback paths、legacy prompt aliases | fastQA core RAG | LLM config/cache key helpers partially | 拆 stage runners 与 shared transport/config | `test_generation_stage*.py`、`test_generation_runtime_bootstrap.py` |
| `retrieval/` | inactive placeholder：仅 `.gitkeep` | scaffold | 不应作为模块保留判断 | 否 | 删除或补文档说明 | 无直接测试 |
| `storage/` | active：documents/PDF links/upload materialize/table loader | legacy local fallback filename/metrics | public-service/storage 更合适 | 是 | 抽 object reader/upload materializer/paper locator | `test_documents_storage.py`、`test_upload_materializer.py` |
| `documents/` | active API：`main.py:84` 注册，`documents/api.py:45-128` | 与 fastQA planned role 冲突 | 应归 public-service 或 shared document service | 是 | gateway/public-service 接管，fastQA 保留 compat proxy | `test_documents*.py` |
| `file_context/` | active tests，`file_routes.py:228-241` fallback resolver | fallback mode | gateway/public-service 更合适 | 是 | fastQA 只消费 normalized `execution_files` | `test_file_context_service.py` |
| `qa_cache/` | active：orchestrator imports stage caches；RedisService | stage25/stage3 naming复杂 | fastQA RAG cache，Redis primitives 可共享 | 部分 | 统一 cache key registry/TTL config | `test_qa_cache*.py` |
| `microscopic_runtime/` | active via `microscopic_expert.py` semantic search | local/remote embedding aliases | fastQA retrieval runtime，embedding client 可共享 | 是 | 抽 embedding client/config，保留 domain expert | `test_embedding_client.py`、`test_microscopic_expert.py` |

### 13. `app/routers/qa.py` 逐段拆解

| 范围 | 当前职责 | 代码证据 | 目标拆分文件 |
|---|---|---|---|
| `33-59` | HTTP request model，包含 gateway 字段和 legacy PDF 字段 | `AskRequest` 定义 `requested_mode/actual_mode/route/source_scope/execution_files/pdf_path` | `app/contracts/ask_request.py` + shared `GatewayAskRequest` |
| `62-83` | request adapter error payload + trace id | `_adapter_error_payload()`、`_trace_id()` | `services/error_event_builder.py`、`services/trace.py` |
| `149-225` | user id header/body 校验、gateway-owned persistence 判断 | `X-User-ID`、`X-Gateway-Task-Execution`、`X-Gateway-Owned-Persistence`、internal token | shared `gateway_auth.py`、`persistence_policy.py` |
| `228-420` | user message persistence、authority context read、assistant terminal persistence | hook names hardcoded in router | `services/conversation_hooks.py` |
| `424-595` | metadata/done/error/upstream timeout event builder | `_metadata_event()`、`_done_event()`、`_runtime_error_event()` | `services/stream_event_mapper.py`、shared SSE contract |
| `602-688` | quota/concurrency busy payload、request adaptation、file context prebuild | `ask_limiter`、`adapt_gateway_ask_payload()`、`_upstream_file_context()` | `services/qa_request_preparer.py` |
| `691-845` | KB route：conversation context、Graph V2 direct/RAG、Graph classic fallback、generation fallback、`FASTQA_NOT_READY` | calls `route_graph_kb_v2()`、`try_graph_kb_answer()`、`qa_kb_service.iter_answer_events()` | `runners/kb_runner.py`、`runners/graph_runner.py` |
| `846-895` | PDF/table/hybrid dispatch | `iter_pdf_route_events()`、`iter_tabular_route_events()`；hybrid pdf-only special case | `dispatchers/file_route_dispatcher.py` |
| `915-1083` | Graph step/metadata/direct-answer event mapping | `_graph_retrieval_step_event()`、`_iter_graph_kb_events()` | `modules/graph_kb/streaming.py` |
| `1085-1322` | stream mapper：metadata synthesis、done normalization、reference link materialization、error event、pool timeout | `storage_service.build_pdf_links()`、`normalize_reference_objects()` | `services/ask_stream_mapper.py` |
| `1342-1383` | stream tap + persistence hook | `AskStreamTap.wrap()` then terminal hook | `services/stream_persistence.py` |
| `1386-1449` | sync JSON aggregation over stream events | `_collect_sync_result()` | `services/sync_ask_aggregator.py` |
| `1452-1611` | HTTP routes and aliases | `/api/ask`、`/api/fast/ask`、`/api/v1/ask`、`/api/v1/fast/ask` 等 | `routers/qa.py` should become thin |

逐项要求覆盖：

- HTTP route：`qa.py:1452-1611`。
- request parsing：`AskRequest` 在 `33-59`，adapter 在 `618-644`。
- gateway contract normalize：`request_adapter.py:247-413`。
- KB：`qa.py:709-845`。
- PDF：`qa.py:846-855` -> `file_routes.py:244-375`。
- table：`qa.py:856-866` -> `file_routes.py:377-530`。
- hybrid：`qa.py:868-895`，pdf-only 进 PDF runner，其余进 tabular runner。
- graph fallback：V2 `730-785`；classic `786-804`。
- generation fallback：Graph skip/error 后 `806-844`。
- `FASTQA_NOT_READY`：`qa.py:806-818`，另有 `qa_kb/service.py:24-55` placeholder。
- stream mapper/error event：`qa.py:1085-1322`。
- persistence hook：`qa.py:228-420`、`1342-1383`。
- quota：`ask_limiter` in `qa.py:602-615`、`1459-1462`、`1528-1534`。
- auth：user id header/body `149-163`，gateway-owned internal token `214-225`。
- file materialization：not in router directly；actual in `file_routes.py:255-263` and `387-395`。

### 14. `request_adapter.py` 深挖

`GatewayAskRequest` 字段范围：`request_adapter.py:29-60`，字段包括 question、conversation_id、user_id、request/authority chat history、requested_mode、actual_mode、route、route_was_explicit、source_scope、kb_enabled、turn_mode、allow_kb_verification、trace_id、generation flags、active_stream_count、used_files、execution_files、selected_file_ids、primary_file_id、file_selection、pdf_context、pdf_path、current_pdf_path、use_pdf。

关键校验：

- mode policy：`247-260` 允许 requested_mode 为 fast/thinking/patent，但 `actual_mode` 必须为 fast。
- route：`272-279` 仅允许 `kb_qa/pdf_qa/tabular_qa/hybrid_qa`。
- source_scope normalize：`183-197` 只允许 `pdf/table/kb`，按固定顺序输出。
- route/source_scope matrix：`226-244` 限定 PDF/table/hybrid scope。
- route required：`306-318` 禁止有 file 执行信号但 route 未显式声明。
- file routes 必填：`319-329` 要求 `source_scope` 和 `turn_mode`，且 turn_mode 只能 `file_only/mixed`。
- execution_files：`343-354` 对 PDF/table scope 要求对应文件存在。
- selected/primary：`281-299` 要求 primary 属于 selected 且属于 execution_files。

共享接口建议：

- shared contract：`GatewayAskRequest`、`ExecutionFilePayload`、`Route`、`SourceScope`、`TurnMode`、`RequestAdapterError`。
- service-specific policy：`AllowedActualModePolicy("fast")`，供 patent/highThinkingQA 使用 `patent`/`thinking`。
- shared validation：route/source_scope/turn_mode/execution_files/selected_file_ids/primary_file_id。
- fastQA private：`use_generation_driven`、`n_results_per_claim` 默认、PDF legacy `pdf_context/current_pdf_path` 兼容。

### 15. `file_route_service.py` 深挖

live path：

```text
qa.py hybrid/pdf route
  -> services/file_routes.py:244 or 377
  -> _pdf_agent_for_request(file_routes.py:216-225) when KB verification needed
  -> file_route_service._build_pdf_agent(file_route_service.py:163-213)
  -> qa_pdf/streaming.py smart_query OR qa_pdf/service.py query_pdf_directly
```

关键事实：

- `_build_pdf_agent()` 在 `file_route_service.py:163-213` 动态构造 `_PdfAgent`，注释明确是 legacy `MaterialScienceAgent` entrypoints compatibility shim。
- `smart_query()` 在 `171-187` 调 `qa_kb_service.run_generation_pipeline()`，依赖 `app_state.generation_runtime` 和 Redis。
- `query_pdf_directly()` 在 `189-211` 用 DOI 找本地 PDF，再 `_answer_from_pdf()`。
- `file_routes.py:216-225` 只有 `allow_kb_verification` 为真才返回 shim，否则只返回 `SimpleNamespace(llm=...)`。
- `qa_pdf/streaming.py:46-66` 如果 agent 有 `smart_query` 就做 KB verification；否则无 KB verification。
- `qa_pdf/service.py:283-324` DOI direct query 要求 agent 有 `query_pdf_directly`。
- 测试覆盖：`test_file_route_service.py`、`test_file_routes_materialization.py`、`test_qa_pdf_service.py`、`test_qa_placeholder.py`、`test_qa_pool_timeout_contract.py`。

替换正式 runner 的前置条件：

1. 定义 `PdfKbVerificationPort.smart_query(question, use_dual_retrieval)` 的稳定返回 schema。
2. 定义 `PdfDirectQueryPort.query_pdf_directly(question, doi)` 的错误/成功 schema。
3. `qa_pdf/streaming.py` 和 `qa_pdf/service.py` 不再通过 duck-typed `MaterialScienceAgent` shape 调用。
4. 保留一版 compat adapter，并用 feature flag 或 constructor injection 回滚。
5. 用 stream contract tests 固化 PDF direct、single PDF、multi PDF、hybrid PDF+KB 的 event sequence。

### 16. `qa_tabular` 深挖

当前链路：

```text
qa.py route=tabular_qa/hybrid_qa
  -> file_routes.iter_tabular_route_events()
  -> materialize_uploaded_files()
  -> optional KB stage1/stage2 retrieval
  -> qa_tabular_service.iter_answer_events()
  -> readiness -> workbook loading/cache -> planner -> executor
  -> optional PDF hybrid evidence -> optional KB evidence
  -> LLM synthesis stream -> references/done
```

分项证据：

- readiness：`qa_tabular/service.py:33-90` 判断 failed/ready/source 可用，`351-389` 对 table/pdf candidates 阻断或 warning。
- MinIO-only：`qa_tabular/service.py:53-59`、`file_routes.py:53-57`、`file_routes.py:412-438`。
- workbook loading：`qa_tabular/workbook_loader.py:215-261`，支持 MinIO materialize/temp/local file；`65-111` signature；`22-54` TTL cache。
- planner：`qa_tabular/planner.py` 966 行，`460-516` detect operation，`_match_sheet/_match_columns` 等大量规则。
- executor：`qa_tabular/executor.py:252-505` 单表执行；`508-657` 多表 compare。
- evidence retrieval：PDF hybrid evidence 在 `qa_tabular/service.py:238-285` 和 `520-545`；KB hybrid evidence 在 `file_routes.py:450-505` 后传入 `qa_tabular/service.py:548-561`。
- LLM synthesis：`qa_tabular/service.py:562-607` 调 `iter_synthesize_answer()` 并做增量清理。
- stream output：metadata `393`，thinking/step 多处，content `588-595`，done `645-652`。
- references/done event：`609-618` 合并 PDF DOI 和 KB references，`645-652` 输出 done。

目标拆分结构：

```text
app/modules/qa_tabular/
  readiness.py
  workbook_repository.py
  planner.py
  executor.py
  hybrid_evidence.py
  kb_evidence.py
  synthesis.py
  stream_events.py
  runner.py
```

测试计划：

- unit：readiness matrix、MinIO-only、workbook signature/cache、planner operation、executor operation。
- contract：`source_scope` table/table+kb/pdf+table/pdf+table+kb 输入输出。
- router：`test_qa_routes_file_modes.py`、`test_file_routes_tabular_kb.py`。
- stream：metadata -> step -> content -> done 顺序，LLM failure fallback。
- integration：materialized MinIO table + PDF hybrid + KB evidence。
- regression：多 sheet 澄清、多表 compare、summary representative rows、missing local_path strict rejection。

### 17. LLM/integrations 深挖

- auth headers：`thinking.py:74-92` 支持 bearer、authorization、x-api-key、none；`openai_compat.py:250-254` 使用默认 `LLM_AUTH_MODE`；rerank warmup 用 `core/runtime.py:414`。
- HTTP pool：`shared_http_pool.py:80-150` env config；`153-258` FastQASharedUpstreamHttpPool；`core/runtime.py:267-274` bootstrap；`openai_compat.py:194-212` 私有/注入 client ownership。
- stream parser：`openai_compat.py:478-498` 解析 `data:` SSE JSON，忽略 malformed、`[DONE]`，error frame raise。
- retry/timeout：没有通用 retry policy；有 per-request timeout build `224-248`、PoolTimeout 特判 `389-443`、stream read timeout `256-259`。
- thinking controls：`thinking.py:95-148` 只在 `stage4_final_answer` 且 env enable 时启用，启用后扩大 max_tokens 并移除 sampling。
- model logging：`openai_compat.py:316-388` start/success/failed 日志硬编码 `service=fastQA`；`core/runtime.py:423-468` rerank warmup 也硬编码。
- embedding/rerank config：`microscopic_runtime/embedding_client.py` 使用 `EMBEDDING_*`；`generation_pipeline/rerank_service.py` 使用 `RERANK_*`；`core/runtime.py:619-624` 兼容 `QA_RETRIEVAL_RERANK_*`。
- 与 highThinkingQA/patent 重复风险：OpenAI-compatible transport、auth headers、pool config、thinking controls、upstream auth logging、embedding/rerank client 都是跨服务基础设施，应参数化 service_name 后抽共享。

### 18. router/API 完整表

| 路径 | 方法 | 文件 | 入参模型 | service/runner | 外部依赖 | 持久化/鉴权/quota/SSE | 测试覆盖 |
|---|---|---|---|---|---|---|---|
| `/api/ask` | POST | `routers/qa.py:1452-1454` | `AskRequest` | `_iter_route_frames` -> route runner | LLM/Redis/Neo4j/MinIO by route | local persistence hooks；user header check；ask_limiter；JSON aggregate | `test_qa_placeholder.py`、`test_qa_route_aliases.py` |
| `/api/fast/ask` | POST | `routers/qa.py:1452-1454` | `AskRequest` | same | same | same | `test_qa_route_aliases.py` |
| `/api/ask_stream` | POST | `routers/qa.py:1518-1524` | `AskRequest` | `_iter_qa_frames` | same | persistence hooks；gateway-owned skip；ask_limiter；SSE | `test_qa_placeholder.py`、`test_qa_pool_timeout_contract.py` |
| `/api/v1/ask` | POST | `routers/qa.py:1518-1524` | `AskRequest` | `ask_stream()` | same | SSE despite ask name | `test_qa_routes_file_modes.py` legacy v1 |
| `/api/v1/ask_stream` | POST | `routers/qa.py:1518-1524` | `AskRequest` | `ask_stream()` | same | SSE | route alias tests |
| `/api/fast/ask_stream` | POST | `routers/qa.py:1518-1524` | `AskRequest` | `ask_stream()` | same | SSE | placeholder/pool tests |
| `/api/v1/fast/ask` | POST | `routers/qa.py:1518-1524` | `AskRequest` | `ask_stream()` | same | SSE despite ask name | `test_qa_route_aliases.py` |
| `/api/v1/fast/ask_stream` | POST | `routers/qa.py:1518-1524` | `AskRequest` | `ask_stream()` | same | SSE | route alias tests |
| `/api/{mode}/ask` | POST | `routers/qa.py:1598-1602` | `AskRequest` | `ask()` | same | rejects mode != fast | route alias tests |
| `/api/v1/{mode}/ask` | POST | `routers/qa.py:1605-1611` | `AskRequest` | `ask_stream()` | same | rejects mode != fast；SSE | route alias tests |
| `/api/{mode}/ask_stream` | POST | `routers/qa.py:1605-1611` | `AskRequest` | `ask_stream()` | same | rejects mode != fast；SSE | route alias tests |
| `/api/v1/{mode}/ask_stream` | POST | `routers/qa.py:1605-1611` | `AskRequest` | `ask_stream()` | same | rejects mode != fast；SSE | route alias tests |
| `/healthz` | GET | `routers/health.py` | none | health builder | app.state runtime | no quota；status exposure | `test_health.py` |
| `/api/health` | GET | `routers/health.py` | none | health builder | Redis/generation/graph/pools | no quota | `test_health.py` |
| `/api/v1/view_pdf/{doi:path}` | GET/HEAD | `documents/api.py:45-66` | path doi | `documents_service.view_pdf_path` | storage/papers_dir | no auth/quota in fastQA | `test_documents*.py` |
| `/api/view_pdf/{doi:path}` | GET/HEAD | `documents/api.py:45-66` | path doi | same | same | same | `test_documents_view_pdf.py` |
| `/api/v1/check_pdf/{doi:path}` | GET | `documents/api.py:69-77` | path doi | `documents_service.check_pdf` | storage | same | `test_documents.py` |
| `/api/check_pdf/{doi:path}` | GET | `documents/api.py:69-77` | path doi | same | storage | same | `test_documents_router.py` |
| `/api/v1/extract_pdf_text/{doi:path}` | GET | `documents/api.py:80-88` | path doi | `documents_service.extract_pdf_text` | PyMuPDF/storage | same | documents tests |
| `/api/extract_pdf_text/{doi:path}` | GET | `documents/api.py:80-88` | path doi | same | PyMuPDF/storage | same | documents tests |
| `/api/v1/reference_preview` | GET/POST | `documents/api.py:91-128` | query or `ReferencePreviewRequest` | `documents_service.reference_preview` | graph/chroma/storage | no auth/quota | `test_documents_reference_preview.py` |
| `/api/reference_preview` | GET/POST | `documents/api.py:91-128` | query or model | same | same | same | documents tests |

### 19. legacy/deprecated/scaffold 引用验证

- README scaffold stale：`README.md` 仍称当前是 phase-1 skeleton、ask 返回 `FASTQA_NOT_READY`，但代码已有 active Graph/generation/PDF/table/hybrid。结论：README 是迁移残留。
- `FASTQA_NOT_READY`：`qa.py:811` runtime not ready；`qa_kb/service.py:41` phase1 placeholder。结论：同码不同语义。
- MaterialScienceAgent shim：`file_route_service.py:167-169` 注释明确；`file_routes.py:222-223` live 调用。
- PDF legacy：`qa_pdf/service.py:13` imports `legacy_answer_from_pdf`；`qa_pdf/__init__.py` docstring says extracted from legacy backend。
- Graph legacy：`graph_kb/client.py:108` legacy template plan；`planner_v2.py`、`executor_v2.py`、`service.py` 大量 legacy route/template metadata；tests 固定 legacy parity。
- storage legacy local fallback：`storage/service.py`、`paper_storage.py`、`upload_materializer.py` 有 legacy filename/local fallback metrics。
- conversation legacy：`core/config.py:33` authority targets include `legacy`，defaults at `111-112`。
- scaffold：`app/modules/retrieval/.gitkeep` 无代码；README planned role 与现状不一致。

### 20. 第二轮新增重构点

以下 `R-010` 至 `R-020` 均为第二轮深度补充，所属服务均为 `fastQA`。每个条目的 `当前状态` 以对应接口路径和调用链为准：QA/router/generation/tabular/LLM 路径为 `active live path`；`MaterialScienceAgent` 与 PDF legacy shim 为 `deprecated but referenced`；`FASTQA_NOT_READY` 为 `placeholder but tested`；README/依赖漂移为 `archive/doc drift`。本节所有条目均按第二轮模板补充代码位置、行号范围、接口路径、当前调用链、关键代码片段、目标结构、迁移步骤、兼容/回滚、测试计划、风险和阻塞项。

### R-010：拆 `qa.py` 的 authority/persistence hook

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path

- 代码位置、行号范围：`fastQA/app/routers/qa.py:149-420`。
- 接口路径：所有 ask/ask_stream。
- 当前调用链：HTTP route -> `_adapt_request()` -> `_persist_user_message_if_needed()` -> `_load_conversation_context_if_needed()` -> stream tap -> `_persist_assistant_terminal_if_needed()`。
- <=40 行关键片段：

```python
def _gateway_owned_persistence(request: Request) -> bool:
    expected_token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()
    ...
    return (
        _header_truthy(_request_header(request, "X-Gateway-Task-Execution"))
        and _header_truthy(_request_header(request, "X-Gateway-Owned-Persistence"))
        and internal_service_name == "gateway"
        and internal_service_token == expected_token
    )

def _persist_user_message_if_needed(...):
    if _gateway_owned_persistence(request):
        return
    ...
    result = _call_hook(... hook_name="persist_user_message_hook", ...)
```

- 目标结构：`app/services/conversation_execution_hooks.py`、`app/services/gateway_persistence_policy.py`、shared `agent_common/conversation/authority_hooks.py`。
- 迁移步骤：先复制逻辑到服务类；router 只注入 `request.app.state`；保持 hook kwargs；添加 adapter tests；最后删除 router 内函数。
- 兼容/回滚：保留原函数名作为薄 wrapper 一版；feature flag 切回 router-local。
- unit 测试计划：gateway-owned truth table、strict user write/context read、terminal failure payload。
- contract 测试计划：public-service authority hook 入参 schema。
- router 测试计划：现有 persistence tests 不改断言。
- stream 测试计划：done/error/cancel terminal status。
- integration 测试计划：gateway-owned stream 不本地持久化。
- regression 测试计划：`test_qa_placeholder.py` persistence sections。
- 风险：高，可能重复写对话或漏写 terminal。
- 阻塞项：需确认 public-service authority SDK 最终 schema。

### R-011：把 `qa.py` 的 stream event mapper 抽成稳定 SSE contract

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path

- 代码位置、行号范围：`fastQA/app/routers/qa.py:424-595`、`1085-1322`、`1342-1449`。
- 接口路径：所有 ask_stream 和 sync ask 聚合。
- 当前调用链：runner event -> `_iter_qa_frames()` enrich/normalize -> `AskStreamTap` -> `sse_response()`；sync ask 把同一 event list 聚合成 JSON。
- <=40 行关键片段：

```python
if event_type == "done":
    normalized_reference_objects = normalize_reference_objects(...)
    normalized_references = normalize_references(normalized_reference_objects)
    links = storage_service.build_pdf_links(normalized_references)
    done_event["references"] = normalized_references
    done_event["reference_links"] = links
    done_event["pdf_links"] = links
    done_event["doi_locations"] = build_doi_locations(normalized_reference_objects)
    done_event["metadata"] = {
        **dict(done_event.get("metadata") or {}),
        "requested_mode": requested_mode,
        "actual_mode": actual_mode,
        "route": route,
    }
```

- 目标结构：`app/services/ask_stream_mapper.py`、`app/services/sync_ask_aggregator.py`、shared `agent_common/sse/events.py`。
- 迁移步骤：提取 pure functions；保留 `storage_service` 注入；先覆盖 golden event tests；router 调 mapper。
- 兼容/回滚：保留 `_iter_qa_frames = mapper.iter_frames` wrapper；JSON/SSE shape 不变。
- unit 测试计划：metadata synthesis、done reference links、error early return、synthetic done。
- contract 测试计划：metadata/content/done/error schema。
- router 测试计划：aliases response type/status。
- stream 测试计划：PoolTimeout first-byte 前后两种路径。
- integration 测试计划：KB/PDF/table/hybrid 各一路 event sequence。
- regression 测试计划：`test_stream_contract.py`、`test_reference_link_boundary.py`。
- 风险：高，SSE first-byte/terminal persistence 很容易退化。
- 阻塞项：需要冻结 gateway replay 期望的 event schema。

### R-012：把 KB runner 和 Graph runner 从 router 中分离

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path

- 代码位置、行号范围：`fastQA/app/routers/qa.py:691-845`。
- 接口路径：`route=kb_qa` 的所有 ask/stream。
- 当前调用链：router -> conversation context -> Graph V2 direct/RAG -> Graph classic fallback -> generation runtime -> `qa_kb_service.iter_answer_events()`。
- <=40 行关键片段：

```python
if graph_enabled and graph_v2_enabled:
    yield _graph_retrieval_step_event(status="processing")
    routing_result = route_graph_kb_v2(...)
    if routing_result.mode == "direct_answer":
        yield from _iter_graph_kb_events(...)
        return
    if routing_result.mode == "graph_for_rag":
        graph_rag_payload = routing_result.rag_payload
...
runtime = getattr(request.app.state, "generation_runtime", None) if generation_runtime_is_ready(request.app.state) else None
if runtime is None:
    yield _runtime_error_event(code="FASTQA_NOT_READY", ...)
    return
```

- 目标结构：`app/runners/kb_runner.py`、`app/runners/graph_kb_runner.py`、`app/modules/graph_kb/streaming.py`。
- 迁移步骤：先提取 `GraphKbRunner.run()`；再提取 `KbQaRunner.iter_events()`；router 只 dispatch。
- 兼容/回滚：runner 调用保留相同 generator event；graph flags 不变。
- unit 测试计划：graph direct、graph_for_rag injected/disabled、skip_graph、runtime not ready。
- contract 测试计划：Graph metadata keys and legacy aliases。
- router 测试计划：KB ask/stream aliases。
- stream 测试计划：graph processing/success/error step 顺序。
- integration 测试计划：`test_fastqa_kb_graph_integration.py`。
- regression 测试计划：Graph seeded DOI fallback through generation orchestrator。
- 风险：高，Graph direct short-circuit 与 generation fallback 互斥关系复杂。
- 阻塞项：Graph classic fallback retirement gates 未完全清除。

### R-013：统一 file-route materialization/readiness contract

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path

- 代码位置、行号范围：`fastQA/app/services/file_routes.py:244-375`、`377-530`；`qa_tabular/service.py:33-90`、`351-389`。
- 接口路径：`pdf_qa`、`tabular_qa`、`hybrid_qa`。
- 当前调用链：router -> `iter_pdf_route_events()`/`iter_tabular_route_events()` -> `materialize_uploaded_files()` -> module service 再次 readiness。
- <=40 行关键片段：

```python
execution_files = materialize_uploaded_files(...)
table_files = [...]
readable_table_files = [item for item in table_files if str(item.get("local_path") or "").strip()]
if table_files and len(readable_table_files) != len(table_files) and _strict_upload_minio_only():
    yield {"type": "error", "error": "execution_file_unavailable", ...}
    return
```

- 目标结构：`app/services/file_execution_materializer.py`、shared `agent_common/files/execution_file.py`。
- 迁移步骤：定义 `MaterializedExecutionFiles`；PDF/table runners 接收已分组对象；删除重复 readiness。
- 兼容/回滚：保留 old functions 代理新 materializer；错误文案/code 不变。
- unit 测试计划：MinIO-only、local path strict block、partial materialization、missing storage_ref。
- contract 测试计划：execution_files schema and error schema。
- router 测试计划：file route invalid requests。
- stream 测试计划：soft error before content。
- integration 测试计划：MinIO-backed PDF/table。
- regression 测试计划：`test_file_routes_materialization.py`。
- 风险：中高，严格模式下 local fallback 行为容易改变。
- 阻塞项：uploads/storage authority 最终归 public-service 后需重定边界。

### R-014：以正式 PDF ports 替代 `MaterialScienceAgent` shim

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：deprecated but referenced

- 代码位置、行号范围：`fastQA/app/services/file_route_service.py:163-213`、`qa_pdf/streaming.py:46-66`、`qa_pdf/service.py:283-324`。
- 接口路径：`pdf_qa`、`hybrid_qa` pdf-only、DOI direct PDF。
- 当前调用链：file routes -> `_pdf_agent_for_request()` -> `_build_pdf_agent()` -> `smart_query()`/`query_pdf_directly()` duck typing。
- <=40 行关键片段：

```python
class _PdfAgent:
    # Compatibility shim for legacy MaterialScienceAgent entrypoints kept during V2 rollout.
    llm = service._resolve_llm(app_state=app_state)

    def smart_query(self, question: str, use_dual_retrieval: bool = False) -> dict[str, Any]:
        if not generation_runtime_is_ready(app_state):
            return {"success": False, "error": "generation_runtime_unavailable"}
        result = qa_kb_service.run_generation_pipeline(...)

    def query_pdf_directly(self, user_question: str, doi: str) -> dict[str, Any]:
        pdf_path = find_pdf_path(...)
```

- 目标结构：`app/modules/qa_pdf/ports.py`、`app/modules/qa_pdf/kb_verification.py`、`app/modules/qa_pdf/direct_query.py`。
- 迁移步骤：定义 protocols；实现 fastQA adapters；`qa_pdf` 改为 protocol 方法；保留 shim adapter。
- 兼容/回滚：旧 `_build_pdf_agent()` 返回 protocol adapter，供旧调用继续工作。
- unit 测试计划：KB unavailable、KB success、DOI PDF missing、PDF answer success。
- contract 测试计划：port return schema。
- router 测试计划：PDF file_only/mixed。
- stream 测试计划：KB verification step/content/done。
- integration 测试计划：PDF+KB hybrid。
- regression 测试计划：`test_file_route_service.py`、`test_qa_pdf_service.py`。
- 风险：中高，PDF direct query 是兼容入口。
- 阻塞项：是否保留 sidecar compatible 路径需产品决策。

### R-015：拆 `qa_tabular/service.py` 为 runner pipeline

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path

- 代码位置、行号范围：`fastQA/app/modules/qa_tabular/service.py:331-652`。
- 接口路径：`tabular_qa`、`hybrid_qa` table scope。
- 当前调用链：file route -> `qa_tabular_service.iter_answer_events()` -> readiness -> load workbook -> plan -> execute -> evidence -> synthesize -> done。
- <=40 行关键片段：

```python
table_files = [item for item in table_candidates if _is_table_file_usable(item)]
...
workbook = self.load_workbook(item)
profile = profile_workbook(workbook)
...
plan = self.plan(question=question, profile=primary_table["profile"], profiles=[...])
...
execution_result = self.execute(workbook=primary_table["workbook"], plan=plan)
...
for piece in self.iter_synthesize_answer(...):
    for event in incremental_clean_events_for_piece(...):
        yield _emit(event, sse_event)
```

- 目标结构：`readiness.py`、`workbook_repository.py`、`runner.py`、`hybrid_evidence.py`、`synthesis.py`、`stream_events.py`。
- 迁移步骤：先提取 pure helpers；保持 `QaTabularService.iter_answer_events()` facade；逐步注入 dependencies。
- 兼容/回滚：facade 不变；文件级 feature flag 允许旧 service。
- unit 测试计划：每个 pipeline stage。
- contract 测试计划：done references/source_scope。
- router 测试计划：table/hybrid dispatch。
- stream 测试计划：metadata/thinking/step/content/done。
- integration 测试计划：table+kb、pdf+table、pdf+table+kb。
- regression 测试计划：`test_qa_tabular_service.py`、`test_file_routes_tabular_kb.py`。
- 风险：中，planner/executor 与 stream 交织较深。
- 阻塞项：需要 golden event sequence。

### R-016：抽共享 OpenAI-compatible transport

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path

- 代码位置、行号范围：`fastQA/app/integrations/llm/openai_compat.py:1-1162`、`thinking.py:74-148`、`shared_http_pool.py:80-258`。
- 接口路径：所有 KB/PDF/table generation 和 rerank/intent/model调用。
- 当前调用链：runtime/file_route/qa_pdf factory -> `build_chat_adapter()` or `build_chat_completions_client()` -> httpx post/stream -> parser/logging。
- <=40 行关键片段：

```python
def _headers(self) -> dict[str, str]:
    return auth_headers(self._cfg.api_key, accept="application/json, text/event-stream")

def _iter_sse_json(self, response: Any) -> Iterator[dict[str, Any]]:
    for raw_line in response.iter_lines():
        line = str(raw_line or "").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        payload = json.loads(data)
        if isinstance(payload, Mapping) and isinstance(payload.get("error"), Mapping):
            raise RuntimeError(message)
        yield dict(payload)
```

- 目标结构：`packages/agent_common/llm/{auth.py,http_pool.py,openai_compatible.py,stream_parser.py,thinking.py,model_call_logger.py}`。
- 迁移步骤：先复制 tests 到 shared；参数化 `service_name`；fastQA imports shared via adapter; remove local duplicate later。
- 兼容/回滚：fastQA local wrapper 保留旧 class/function names。
- unit 测试计划：auth mode、endpoint normalize、stream parser malformed/error frames、thinking controls。
- contract 测试计划：OpenAI-compatible request/response shape。
- router 测试计划：PoolTimeout maps 503/error event。
- stream 测试计划：first chunk logging and stream read timeout。
- integration 测试计划：shared http pool + generation runtime。
- regression 测试计划：`test_llm_openai_compat.py`、`test_llm_shared_http_pool.py`。
- 风险：高，跨服务共用 transport 改错会扩大 blast radius。
- 阻塞项：需读取 highThinkingQA/patent transport 差异。

### R-017：把 documents/storage 从 fastQA 执行面迁出

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path but boundary-overlapping

- 代码位置、行号范围：`fastQA/app/modules/documents/api.py:45-128`、`storage/service.py`、`storage/paper_storage.py`、`storage/upload_materializer.py`。
- 接口路径：view/check/extract/reference_preview；QA done reference links。
- 当前调用链：`main.py:84` 注册 documents router；QA done event 调 `storage_service.build_pdf_links()`；documents service 调 storage local/MinIO。
- <=40 行关键片段：

```python
@router.get("/api/v1/view_pdf/{doi:path}")
@router.head("/api/v1/view_pdf/{doi:path}")
@router.get("/api/view_pdf/{doi:path}")
@router.head("/api/view_pdf/{doi:path}")
def view_pdf(doi: str, request: Request):
    payload, status_code, pdf_path = documents_service.view_pdf_path(...)
    if pdf_path is None:
        return _json(payload, status_code)
    return FileResponse(path=str(pdf_path), media_type="application/pdf", headers=headers)
```

- 目标结构：public-service `documents` API；fastQA `DocumentLinkClient` only。
- 迁移步骤：public-service parity inventory；gateway route documents to public-service；fastQA returns links via shared builder; keep proxy aliases temporarily。
- 兼容/回滚：documents router remains mounted under feature flag until gateway switch verified。
- unit 测试计划：DOI normalize/link building/reference preview item。
- contract 测试计划：documents API status codes and payloads。
- router 测试计划：FastQA proxy routes during transition。
- stream 测试计划：done reference_links unchanged。
- integration 测试计划：gateway/public-service document view。
- regression 测试计划：`test_documents*.py`、`test_reference_link_boundary.py`。
- 风险：中，front-end may still call fastQA document aliases directly。
- 阻塞项：gateway routing and public-service ownership decision。

### R-018：统一 runtime container，降低 `app.state` 过载

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path

- 代码位置、行号范围：`fastQA/app/main.py:48-75`、`core/runtime.py:126-863`、`routers/health.py`。
- 接口路径：all routes and health。
- 当前调用链：create_app 直接在 `app.state` 设置每个资源；runtime bootstrap/close 修改同一 state；router/health/tests 直接读属性。
- <=40 行关键片段：

```python
app.state.redis_service = None
app.state.generation_runtime = None
app.state.generation_runtime_ready = False
app.state.neo4j_client = None
app.state.graph_kb_ready = False
app.state.shared_llm_http_pool = None
app.state.shared_llm_adapter = None
app.state.stage2_chat_hot_pool = None
app.state.stage2_rerank_hot_pool = None
app.state.pdf_web_bindings = None
bootstrap_redis(app.state)
bootstrap_generation_runtime(app.state)
bootstrap_graph_kb(app.state)
```

- 目标结构：`app/runtime/container.py` with `FastQARuntimeContext`，`app.state.runtime_context` plus compatibility properties。
- 迁移步骤：定义 context dataclass；bootstrap 接收 context；health/router 逐步改用 accessor；保留 app.state bridge。
- 兼容/回滚：bridge writes both context and old attributes。
- unit 测试计划：container init/close idempotency。
- contract 测试计划：health payload unchanged。
- router 测试计划：route uses runtime accessor。
- stream 测试计划：ask_limiter and runtime readiness。
- integration 测试计划：create_app lifecycle。
- regression 测试计划：`test_health.py`、`test_generation_runtime_bootstrap.py`、`test_redis_runtime.py`。
- 风险：中，tests monkeypatch `app.state` heavily。
- 阻塞项：需分阶段适配 monkeypatch-heavy tests。

### R-019：更新 README/依赖清单以反映真实代码状态

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：archive/doc drift

- 代码位置、行号范围：`fastQA/README.md:1-61`、`fastQA/pyproject.toml:1-25`。
- 接口路径：不直接影响 runtime；影响开发/部署理解。
- 当前调用链：README 描述 phase-1 skeleton；pyproject 供 packaging/install 使用。
- <=40 行关键片段：

```text
This service is not functionally complete yet.
...
The real `kb_qa` execution closure has not been extracted yet.
...
ask routes return explicit `FASTQA_NOT_READY` placeholders until real `kb_qa` extraction lands
```

- 目标结构：README 状态按 live path 重写；`pyproject.toml` 去重依赖并统一 casing。
- 迁移步骤：先生成 status inventory；更新 README；单独 chore 去重 pyproject。
- 兼容/回滚：文档/依赖变更独立提交；依赖范围保守取交集或更严格上界。
- unit 测试计划：无直接 unit；依赖导入 smoke。
- contract 测试计划：无。
- router 测试计划：无。
- stream 测试计划：无。
- integration 测试计划：install/build smoke。
- regression 测试计划：PDF/table/LLM imports。
- 风险：低到中，依赖去重可能影响 resolver 选择。
- 阻塞项：需要确认当前部署 lockfile/镜像构建策略。

### R-020：把 generation orchestrator 的 stage/cache/fallback 拆离

- 来源：第二轮深度补充
- 所属服务：fastQA
- 当前状态：active live path

- 代码位置、行号范围：`fastQA/app/modules/qa_kb/orchestrators/generation.py:268-1640`。
- 接口路径：`kb_qa` generation-driven path；PDF KB verification via `smart_query()`。
- 当前调用链：`qa_kb_service.iter_answer_events()` -> `GenerationPipelineOrchestrator.stream()` -> stage1/stage2/stage25/stage3/stage35/stage4/cache/singleflight/fallback。
- <=40 行关键片段：

```python
from app.modules.qa_cache.stage1_cache import ...
from app.modules.qa_cache.stage2_cache import ...
from app.modules.qa_cache.stage25_cache import ...
from app.modules.qa_cache.stage3_cache import ...
...
class GenerationPipelineOrchestrator:
    def stream(...):
        ...
        stage1_result = ...
        stage2_result = ...
        evidence_rerank_result = ...
        stage4_output = self.stage4.stream(...)
```

- 目标结构：`app/modules/qa_kb/pipeline/{runner.py,cache_policy.py,fallbacks.py,streaming.py}`。
- 迁移步骤：提取 cache policy facade；提取 fallback result builder；提取 stream stage event emitter；保持 orchestrator facade。
- 兼容/回滚：`GenerationPipelineOrchestrator` public methods 不变。
- unit 测试计划：cache hit/miss、fallback result、source DOI gate、graph seeded DOI fallback。
- contract 测试计划：`QaKbExecutionResult`/metadata unchanged。
- router 测试计划：KB route unaffected。
- stream 测试计划：stage event sequence and cancellation.
- integration 测试计划：generation runtime with Redis.
- regression 测试计划：`test_qa_generation_orchestrator.py`、`test_generation_stage*.py`、`test_qa_cache*.py`。
- 风险：高，cache/fallback/stage events 是主业务路径。
- 阻塞项：需要先冻结 stage event golden tests。

### 21. 未能确认项

- 未确认 highThinkingQA/patent 当前 LLM transport 和 request adapter 的具体差异；本轮仅基于 fastQA 判断“可共享接口形态”。
- 未确认 gateway 是否仍直接暴露 `/api/v1/fast/ask` 给前端；代码事实是它绑定 `ask_stream()`。
- 未运行 pytest，原因是用户要求只读且不要跑可能写入仓库的命令；本轮只分析已有 tests 文件。
- 未确认 `file_route_service.FileRouteService.iter_events()` 是否仍被生产调用；代码扫描显示 router live path 使用 `services/file_routes.py`，但 tests 仍覆盖 `file_route_service.py`。

## 第三轮证据闭环补充

> 状态：第三轮只读证据闭环。仅追加本文档；未修改 `fastQA/` 下源码、配置、测试、脚本、README 或依赖文件；未创建新目录/新文档。第三轮围绕第二轮未闭环项补充调用链证据，重点判断“live path / shim / legacy / 可实施任务”的边界。

已执行只读命令：

```text
sed -n '1,260p' refactor-audit-output/fastQA-refactor-audit.md
sed -n '260,760p' refactor-audit-output/fastQA-refactor-audit.md
tail -80 refactor-audit-output/fastQA-refactor-audit.md
wc -l refactor-audit-output/fastQA-refactor-audit.md
find fastQA -type f
find fastQA -type f \( -name "*.py" -o -name "*.js" -o -name "*.vue" \) -print0 | xargs -0 wc -l | sort -nr | head -50
rg "file_routes|file_route_service|_build_pdf_agent|MaterialScienceAgent|smart_query|query_pdf_directly" fastQA/app fastQA/tests
rg "FASTQA_NOT_READY|NotReady|not_ready|placeholder" fastQA/app fastQA/tests
rg "legacy_answer_from_pdf|legacy|parity|template" fastQA/app/modules fastQA/tests
rg "OpenAICompat|auth_headers|SharedHttpPool|thinking|stream_parser|rerank" fastQA/app fastQA/tests
rg "execution_files|selected_file_ids|primary_file_id|source_scope|turn_mode" fastQA/app fastQA/tests
rg "file_route_service\.iter_events|FileRouteService|iter_events\(" fastQA/app fastQA/tests
rg "iter_phase1_placeholder_events\(|allow_placeholder_fallback|FASTQA_ALLOW_PLACEHOLDER_FALLBACK" fastQA/app fastQA/tests
rg "iter_phase1_placeholder_events\(" fastQA/app fastQA/tests gateway public-service highThinkingQA
rg "placeholder_fallback_enabled|FASTQA_NOT_READY|fastQA generation runtime is not ready|execution closure has not been extracted" gateway public-service frontend-vue fastQA/tests
rg "persist_user_message_hook|persist_assistant_terminal_hook|conversation_authority|X-Gateway-Owned-Persistence|PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN|selected_file_ids" gateway public-service fastQA/app fastQA/tests
rg "resolve_generation_runtime_inputs|LLM_BASE_URL|LLM_AUTH_MODE|SharedHttpPoolConfig|RERANK_|EMBEDDING_|QA_RETRIEVAL_RERANK" fastQA/app fastQA/tests/test_generation_runtime_bootstrap.py fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_env_loader.py
rg "openai|PyMuPDF|pymupdf|pandas|openpyxl|httpx" fastQA/pyproject.toml fastQA/tests fastQA/app
nl -ba fastQA/app/services/file_routes.py | sed -n '1,120p'
nl -ba fastQA/app/services/file_routes.py | sed -n '200,530p'
nl -ba fastQA/app/services/file_route_service.py | sed -n '1,330p'
nl -ba fastQA/app/routers/qa.py | sed -n '1,120p'
nl -ba fastQA/app/routers/qa.py | sed -n '140,430p'
nl -ba fastQA/app/routers/qa.py | sed -n '690,905p'
nl -ba fastQA/app/routers/qa.py | sed -n '1450,1614p'
nl -ba fastQA/app/modules/qa_pdf/service.py | sed -n '1,120p'
nl -ba fastQA/app/modules/qa_pdf/service.py | sed -n '260,390p'
nl -ba fastQA/app/modules/qa_pdf/streaming.py | sed -n '1,120p'
nl -ba fastQA/app/modules/qa_kb/service.py | sed -n '1,240p'
nl -ba fastQA/app/modules/graph_kb/client.py | sed -n '1,190p'
nl -ba fastQA/app/modules/graph_kb/planner_v2.py | sed -n '1,190p'
nl -ba fastQA/app/modules/graph_kb/executor_v2.py | sed -n '100,155p'
nl -ba fastQA/app/modules/graph_kb/service.py | sed -n '400,590p'
nl -ba fastQA/app/modules/generation_pipeline/runtime_bootstrap.py | sed -n '1,220p'
nl -ba fastQA/app/core/runtime.py | sed -n '260,470p'
nl -ba fastQA/app/core/runtime.py | sed -n '540,760p'
nl -ba fastQA/app/core/config.py | sed -n '260,420p'
nl -ba fastQA/app/integrations/llm/openai_compat.py | sed -n '1,180p'
nl -ba fastQA/app/integrations/llm/openai_compat.py | sed -n '180,330p'
nl -ba fastQA/app/integrations/llm/openai_compat.py | sed -n '330,520p'
nl -ba fastQA/app/integrations/llm/openai_compat.py | sed -n '520,760p'
nl -ba fastQA/app/integrations/llm/thinking.py | sed -n '1,170p'
nl -ba fastQA/app/modules/qa_tabular/service.py | sed -n '1,110p'
nl -ba fastQA/app/modules/qa_tabular/service.py | sed -n '330,665p'
nl -ba fastQA/app/modules/file_context/service.py | sed -n '450,520p'
nl -ba fastQA/app/modules/qa_pdf/llm_factory.py | sed -n '1,100p'
nl -ba fastQA/pyproject.toml | sed -n '1,80p'
nl -ba fastQA/tests/test_qa_kb_service.py | sed -n '1,80p'
nl -ba fastQA/tests/test_qa_placeholder.py | sed -n '45,90p'
nl -ba fastQA/tests/test_qa_placeholder.py | sed -n '330,430p'
nl -ba fastQA/tests/test_file_routes_materialization.py | sed -n '330,610p'
nl -ba fastQA/tests/test_graph_kb_planner_v2.py | sed -n '1,110p'
nl -ba fastQA/tests/test_graph_kb_query_templates.py | sed -n '1,220p'
nl -ba fastQA/tests/test_generation_runtime_bootstrap.py | sed -n '1,420p'
nl -ba fastQA/tests/test_llm_openai_compat.py | sed -n '1,140p'
nl -ba fastQA/tests/test_llm_shared_http_pool.py | sed -n '140,190p'
nl -ba fastQA/tests/test_env_loader.py | sed -n '360,450p'
nl -ba fastQA/tests/test_qa_tabular_service.py | sed -n '1,180p'
nl -ba fastQA/tests/test_qa_routes_file_modes.py | sed -n '250,350p'
git status --short -- refactor-audit-output/fastQA-refactor-audit.md fastQA
```

未运行测试：未执行 `pytest --collect-only fastQA/tests`。原因是本轮硬约束为只读排查，pytest collect 可能刷新 `.pytest_cache` 或导入时触发本地缓存/运行时副作用；因此只读取既有测试文件作为护栏证据。

### 1. 第二轮未确认项复核

### V-301：两套 file route 入口 live 占比

- 验证问题：`services/file_routes.py` 与 `services/file_route_service.py` 是否都是生产 live runner。
- 证据命令：`rg "file_routes|file_route_service|_build_pdf_agent|MaterialScienceAgent|smart_query|query_pdf_directly" fastQA/app fastQA/tests`；`rg "file_route_service\.iter_events|FileRouteService|iter_events\(" fastQA/app fastQA/tests`。
- 代码证据：`fastQA/app/routers/qa.py:24` 只导入 `iter_pdf_route_events`、`iter_tabular_route_events`；`qa.py:848-854`、`858-865`、`877-894` 只调 `services/file_routes.py` 的两个函数。`fastQA/app/services/file_route_service.py:230-303` 定义 `FileRouteService.iter_events()`，但 `rg` 仅命中定义，没有生产调用。
- 结论：HTTP live file route runner 是 `services/file_routes.py`；`FileRouteService.iter_events()` 当前不是 router live path，但 `file_route_service.py` 仍通过 `_build_pdf_agent()` 被 live path 间接使用。
- 状态：闭环，不能整体删除 `file_route_service.py`；可先将 `iter_events()` 标注为待迁移/待删 legacy runner，并保留 `_build_pdf_agent()` 兼容 shim。

fastQA live file route 闭环表：

| 入口/对象 | live 占比结论 | 调用证据 | 被测护栏 | 重构动作 |
|---|---|---|---|---|
| `services/file_routes.py:iter_pdf_route_events()` | 生产 live | `qa.py:846-855` 调用；`qa.py:868-883` 在 `hybrid_qa` pdf-only 分支调用 | `test_file_routes_materialization.py`、`test_qa_routes_file_modes.py`、`test_qa_pool_timeout_contract.py` | 保留入口，提取 `PdfRouteRunner` facade |
| `services/file_routes.py:iter_tabular_route_events()` | 生产 live | `qa.py:856-865` 调用；`qa.py:886-894` 在 table/hybrid 分支调用 | `test_file_routes_materialization.py`、`test_file_routes_tabular_kb.py`、`test_qa_routes_file_modes.py` | 保留入口，提取 `TabularRouteRunner` facade |
| `services/file_route_service.py:FileRouteService.iter_events()` | 非 router live，旧 runner | `rg` 仅命中定义 `file_route_service.py:230`，无 router/app 调用 | 无直接命中；`test_file_route_service.py` 主要测 LLM 解析/shared pool fallback | 可在 runner 拆分后删除或改成 compat wrapper |
| `file_route_service._build_pdf_agent()` | live shim | `file_routes.py:220-223` 在 `allow_kb_verification=True` 时调用 | PDF/file-route tests 间接覆盖；`test_qa_pdf_service.py` 固化 PDF service event | 先替换为正式 PDF ports，不可先删 |
| `resolve_app_owned_llm()` | live shared helper | `file_routes.py:15` 导入，`get_aux_llm()` 使用；`file_route_service.py:50-92` 实现 | `test_file_route_service.py` | 可迁入共享 LLM runtime helper |

### V-302：`_build_pdf_agent` / `MaterialScienceAgent` / `smart_query` / `query_pdf_directly` live path

- 验证问题：这些 legacy/shim 名称是否仍在 live path。
- 证据命令：同 V-301；另读 `file_route_service.py`、`qa_pdf/streaming.py`、`qa_pdf/service.py`。
- 代码证据：`file_route_service.py:163-213` 动态构造 `_PdfAgent`，注释为 legacy `MaterialScienceAgent` entrypoints compatibility shim；`smart_query()` 在 `171-187` 调 `qa_kb_service.run_generation_pipeline()`；`query_pdf_directly()` 在 `189-211` 通过 DOI 找 PDF 并调用 `_answer_from_pdf()`。`file_routes.py:216-225` 在允许 KB verification 时返回该 agent；`qa_pdf/streaming.py:46-76` 仅当 agent 有 `smart_query` 时执行 KB verification；`qa_pdf/service.py:283-324` DOI 直查要求 agent 有 `query_pdf_directly`。
- 结论：四者仍在 live path，但只作为 duck-typed shim。删除前必须先建立正式 `PdfKbVerificationPort` 和 `PdfDirectQueryPort`。
- 状态：闭环，判定为 `deprecated but referenced`。

### V-303：`FASTQA_NOT_READY` 两种语义

- 验证问题：`FASTQA_NOT_READY` 是否可统一。
- 证据命令：`rg "FASTQA_NOT_READY|NotReady|not_ready|placeholder" fastQA/app fastQA/tests`；`rg "iter_phase1_placeholder_events\(" fastQA/app fastQA/tests gateway public-service highThinkingQA`。
- 代码证据：`qa.py:806-818` 在 `generation_runtime_is_ready()` 为 false 时发 `FASTQA_NOT_READY`，语义是 runtime degraded/not ready；`qa_kb/service.py:24-55` 的 `iter_phase1_placeholder_events()` 发同 code，文案是“execution closure has not been extracted yet”。`rg` 显示 `iter_phase1_placeholder_events()` 只有 `tests/test_qa_kb_service.py:13-23` 直接调用，未见生产调用。
- 测试证据：`test_qa_placeholder.py:55-83` 断言 runtime not ready path；`test_qa_kb_service.py:13-23` 断言 phase1 placeholder event contract。
- 结论：可以统一到 `NotReadyEventFactory`/`RuntimeReadinessError`，但短期需保留 `FASTQA_NOT_READY` code 以兼容测试和前端/gateway错误识别。应新增 `detail.reason` 或 `not_ready_kind` 区分 `runtime_unavailable` 与 `legacy_placeholder`。
- 状态：闭环，可作为中低风险任务，但不是“直接改 code”的无测试清理。

### V-304：`qa.py` authority/persistence hook 归属

- 验证问题：authority/persistence hook 属于 fastQA 业务还是 gateway/public-service 共享能力。
- 证据命令：`rg "persist_user_message_hook|persist_assistant_terminal_hook|conversation_authority|X-Gateway-Owned-Persistence|PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN|selected_file_ids" gateway public-service fastQA/app fastQA/tests`。
- 代码证据：`qa.py:214-225` 用 `X-Gateway-Task-Execution`、`X-Gateway-Owned-Persistence` 和 `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` 跳过 fastQA 本地 persistence；`qa.py:228-316` 调 `persist_user_message_hook`/`load_conversation_context_hook`；`qa.py:366-421` 调 terminal hook 并回退 legacy summary hook。`conversation_authority_client.py:10-16` 默认指向 public-service internal base url/token，`135-197` 使用 `/internal/conversations/...` canonical schema。
- 跨服务证据：gateway `qa_tasks.py`、`conversation_persistence.py` 同样处理 selected_file_ids 和 internal token；public-service 有 `conversation/authority_schemas.py`、`conversation/internal_api.py` 和 authority API tests。
- 结论：fastQA 拥有的是 hook 接入适配层和 stream terminal summary 采集；authority schema、internal token、conversation persistence 本体归 gateway/public-service/shared SDK。拆 `qa.py` 时应迁出为 shared conversation authority client/persistence policy，fastQA runner 只消费 adapter。
- 状态：闭环，不能在 fastQA 内继续扩展为业务逻辑。

### V-305：Graph legacy parity tests 是否阻止删除旧模板

- 验证问题：Graph legacy template 是否仍参与 live V2，tests 是否固定 parity。
- 证据命令：`rg "legacy_answer_from_pdf|legacy|parity|template" fastQA/app/modules fastQA/tests`；读取 graph client/planner/executor/service/tests。
- 代码证据：`graph_kb/client.py:56-109` 保留 `plan_graph_kb_query()` 和 `build_legacy_template_query_plan()`；`planner_v2.py:62-82` 用 legacy plan 生成 intent/slots，`145-164` 写入 `legacy_template_id` 和 `legacy_template_plan`，`152-153` 将策略置为 `template`；`executor_v2.py:112-145` 对 `strategy=="template"` 直接执行旧 `GraphKbQueryPlan`；`graph_kb/service.py:421-448` 记录 legacy metadata，`548-573` direct answer 仍输出 legacy template id/fallback 标记。
- 测试证据：`test_graph_kb_planner_v2.py:8-20` 明确断言 old supported queries preserve legacy template；`test_graph_kb_query_templates.py:42-52` 固化 raw material count legacy path；`test_graph_kb_service.py`、`test_fastqa_kb_graph_integration.py` 大量断言 `legacy_route_family`、`legacy_template_fallback_used`、`graph_strategy=template`。
- 结论：Graph legacy parity tests 会阻止直接删除旧模板。正确路径是先把 legacy template 包装为 classic adapter，并用 V2 path parity 替代旧断言。
- 状态：闭环，旧模板删除 blocked。

### V-306：`qa_pdf` legacy `legacy_answer_from_pdf` 是否可替换

- 验证问题：`legacy_answer_from_pdf` 是否是死代码，能否直接替换。
- 证据命令：`rg "legacy_answer_from_pdf|legacy|parity|template" fastQA/app/modules fastQA/tests`；读 `qa_pdf/service.py`。
- 代码证据：`qa_pdf/service.py:13` 从 `engine.answer_from_pdf` 导入为 `legacy_answer_from_pdf`；`PdfQaService.answer_from_pdf()` 在 `63-64` 直接返回该实现；single/multi PDF streaming 最终在 `iter_route_answer_events()` 后进入 `iter_dispatched_uploaded_pdf_answer_events()`，其参数 `answer_from_pdf_fn` 在 `file_routes.py:353` 由 PDF web bindings 注入。
- 测试证据：`test_qa_pdf_service.py:32-62` 固化 single PDF streaming event sequence；`65-134` 固化 first-token timeout 默认行为；`test_file_routes_materialization.py` 固化 materialization fail-fast。
- 结论：`legacy_answer_from_pdf` 是当前 PDF engine facade，不是 dead code。可替换，但必须以 `PdfAnswerEngine` port 包装后逐步替换，保持 `answer_from_pdf_fn` 注入点和 streaming contract。
- 状态：闭环，不可直接删。

### V-307：runtime bootstrap 与 OpenAI-compatible client 配置重复

- 验证问题：配置重复是否可抽共享。
- 证据命令：`rg "resolve_generation_runtime_inputs|LLM_BASE_URL|LLM_AUTH_MODE|SharedHttpPoolConfig|RERANK_|EMBEDDING_|QA_RETRIEVAL_RERANK" ...`。
- 代码证据：`runtime_bootstrap.py:58-112` 解析 LLM/embedding/vector DB；`runtime_bootstrap.py:115-146` 读取 `SharedHttpPoolConfig` 创建 OpenAI-compatible client；`core/runtime.py:267-274` bootstrap shared LLM pool，`552-688` 再创建 chat/rerank hot pools；`core/config.py:306` 也读取 `SharedHttpPoolConfig` 并写入 settings；`file_route_service.py:60-89` 与 `qa_pdf/llm_factory.py:60-97` 再解析 LLM/shared pool 参数。
- 测试证据：`test_generation_runtime_bootstrap.py:15-105` 固化 env 优先级和 retired aliases；`259-295` 固化 unified LLM timeout namespace 优先级；`test_llm_shared_http_pool.py:150-172` 固化 invalid env fallback；`test_env_loader.py:362-447` 固化 shared model/embedding/rerank namespace。
- 结论：可抽 `ModelEndpointSettings`、`SharedHttpTransportSettings`、`EmbeddingRuntimeSettings`、`RerankEndpointSettings`，但必须先保留 env 优先级兼容矩阵。
- 状态：闭环，可进入第一批中风险基础设施任务。

### V-308：`openai_compat.py` 拆 transport/auth/parser/logging/thinking controls

- 验证问题：`openai_compat.py` 是否可拆。
- 证据命令：`rg "OpenAICompat|auth_headers|SharedHttpPool|thinking|stream_parser|rerank" fastQA/app fastQA/tests`；读取 `openai_compat.py` 与 `thinking.py`。
- 代码证据：`openai_compat.py:61-95` message/endpoint normalization；`162-223` client ownership/transport construction；`224-259` timeout/header/auth；`291-388` auth/model call logging；`389-443` pool timeout handling；`445-497` payload/SSE parser；`542-760` adapter invoke/stream 包含 thinking controls。`thinking.py:64-92` 提供 auth mode/header；`95-148` 提供 thinking controls。
- 测试证据：`test_llm_openai_compat.py:77-115` 覆盖 normalize/invoke/stream；`118-140` 覆盖 auth success once；后续测试覆盖 bad JSON/error frame、timeout override、injected client ownership。
- 结论：可拆，但必须保留 public API：`OpenAICompatChatAdapter`、`OpenAICompatClient`、`build_chat_adapter()`、`build_chat_completions_client()`、`extract_openai_compatible_text()`、`normalize_openai_compatible_endpoint()`。
- 状态：闭环，可作为 P1 runner 前置基础设施任务。

### V-309：`qa_tabular/service.py` file readiness 是否应抽共享 file contract

- 验证问题：file readiness 是否重复，是否能抽共享 contract。
- 证据命令：`rg "execution_files|selected_file_ids|primary_file_id|source_scope|turn_mode" fastQA/app fastQA/tests`；读取 `file_routes.py`、`qa_tabular/service.py`、`file_context/service.py`。
- 代码证据：`file_routes.py:255-263` 和 `387-395` 先 materialize execution_files；`277-315`、`412-438` 对 PDF/table local_path 与 strict MinIO 做 fail-fast。`qa_tabular/service.py:33-90` 又定义 parse/index/processing readiness、source availability、preview fallback；`351-389` 对 table/pdf candidates 再判断；`file_context/service.py:491-512` 产生 route_hint/turn_mode/execution_files/selected_file_ids。
- 测试证据：`test_file_routes_materialization.py:379-454` 固化 materialized table file；`457-524` 固化 partial table materialization fail-fast；`527-601` 固化 strict MinIO 下 PDF preview 不可用。`test_qa_tabular_service.py:31-86` 固化 pending file 有源可继续、无源软错误；`130-173` 固化非 strict 下 PDF preview 可用。
- 结论：应抽共享 file contract，但 contract 必须包含 `source availability`、`materialization_error`、`strict_minio_only`、`preview_allowed` 四类语义，不能只抽 `parse_status == ready`。
- 状态：闭环，可进入 file contract 任务。

### V-310：`pyproject.toml` 重复依赖是否可作为第一批低风险任务

- 验证问题：重复依赖是否独立、低风险。
- 证据命令：`rg "openai|PyMuPDF|pymupdf|pandas|openpyxl|httpx" fastQA/pyproject.toml fastQA/tests fastQA/app`；读取 `pyproject.toml`。
- 代码证据：`fastQA/pyproject.toml:7-24` 同时包含 `openai>=1.40,<2.0` 与 `openai>=1.0,<2.0`、`pymupdf>=1.24,<2.0` 与 `PyMuPDF>=1.24,<2.0`、重复 `pandas/openpyxl`；`dev` extras 重复声明 `httpx>=0.27,<1.0`。
- 影响证据：PDF/table/LLM 使用这些依赖，但 import 路径不依赖重复顺序；风险主要来自 resolver/lockfile，而非运行时代码。
- 结论：可作为第一批低风险 chore，但本轮硬约束禁止修改 `fastQA/pyproject.toml`。后续建议保留更严格 `openai>=1.40,<2.0`，统一 `PyMuPDF` casing，去重 `pandas/openpyxl`，`httpx` dev 重复可接受或移除 dev extras 中重复项。
- 状态：闭环，可实施但需单独提交并跑 install/import smoke。

### 2. dead-code / legacy 引用闭环

| 对象 | 第三轮状态 | 证据 | 处理结论 |
|---|---|---|---|
| `FileRouteService.iter_events()` | legacy runner / 非 router live | `rg` 只命中 `file_route_service.py:230` 定义；router 只调 `file_routes.py` | 可在新 runner facade 建立后删除或改 compat wrapper |
| `_build_pdf_agent()` | live compatibility shim | `file_routes.py:220-223` live 调用；`qa_pdf/streaming.py:54-66` 需要 `smart_query` | 不可删，先建正式 ports |
| `smart_query()` | live through PDF KB verification | `file_route_service.py:171-187`；`qa_pdf/streaming.py:112-119` 调 `_run_kb_verification()` | 替换为 `PdfKbVerificationPort` |
| `query_pdf_directly()` | live through DOI direct query | `file_route_service.py:189-211`；`qa_pdf/service.py:295-324` | 替换为 `PdfDirectQueryPort` |
| `legacy_answer_from_pdf` | active PDF engine facade | `qa_pdf/service.py:13`、`63-64` | 包装后替换，不直接删 |
| Graph legacy template | active V2 dependency | `planner_v2.py:145-164`、`executor_v2.py:112-145` | blocked，先隔离 classic adapter |
| `iter_phase1_placeholder_events()` | non-production placeholder, test-fixed | `rg` 仅 tests 直接调用；`qa_kb/service.py:24-55` | 可统一 NotReady factory，保留 compat test |

### 3. live path 调用链闭环

KB live path：

```text
qa.py ask/ask_stream
  -> _adapt_request()
  -> _persist_user_message_if_needed()
  -> _load_conversation_context_if_needed()
  -> _iter_route_events(route="kb_qa")
  -> route_graph_kb_v2()
  -> direct graph answer OR graph_for_rag evidence
  -> generation_runtime_is_ready()
  -> qa_kb_service.iter_answer_events()
  -> GenerationPipelineOrchestrator.stream()
  -> AskStreamTap terminal summary
  -> _persist_assistant_terminal_if_needed()
```

PDF live path：

```text
qa.py route="pdf_qa" or hybrid pdf-only
  -> services/file_routes.iter_pdf_route_events()
  -> materialize_uploaded_files()
  -> load_pdf_content_for_streaming()
  -> pdf_qa_service.iter_route_answer_events()
  -> _pdf_agent_for_request()
     -> _build_pdf_agent() only when allow_kb_verification=True
  -> qa_pdf.streaming._run_kb_verification(agent.smart_query) OR qa_pdf.service.iter_doi_direct_query_events(agent.query_pdf_directly)
```

Tabular/hybrid live path：

```text
qa.py route="tabular_qa" or hybrid table/pdf+table
  -> services/file_routes.iter_tabular_route_events()
  -> materialize_uploaded_files()
  -> optional KB stage1/stage2 retrieval
  -> qa_tabular_service.iter_answer_events()
  -> readiness/source checks
  -> load_workbook/profile/plan/execute
  -> optional PDF hybrid evidence
  -> optional KB evidence
  -> LLM synthesis stream
  -> done references
```

### 4. 测试护栏闭环

测试护栏清单：

| 护栏组 | 现有测试 | 保护行为 | 第三轮结论 |
|---|---|---|---|
| route alias / dispatch | `test_qa_route_aliases.py`、`test_qa_routes_file_modes.py` | `/api/ask` vs SSE aliases、mode alias、hybrid dispatch matrix | runner 拆分时必须保留 |
| runtime not ready | `test_qa_placeholder.py:55-83` | `FASTQA_NOT_READY` runtime 语义 | NotReady 统一时先加 detail，不改 code |
| phase1 placeholder | `test_qa_kb_service.py:13-23` | `iter_phase1_placeholder_events()` event sequence | 可改为 compat factory 测试 |
| file materialization | `test_file_routes_materialization.py` | PDF/table strict MinIO、partial materialization fail-fast | file contract 抽取必须保留 |
| tabular readiness | `test_qa_tabular_service.py` | pending source available/soft error、preview compatibility | readiness 抽共享时必须建 matrix |
| PDF engine/stream | `test_qa_pdf_service.py` | single/multi PDF event sequence、first-token timeout | PDF port 替换必须保留 |
| Graph legacy parity | `test_graph_kb_planner_v2.py`、`test_graph_kb_query_templates.py`、`test_graph_kb_service.py`、`test_fastqa_kb_graph_integration.py` | legacy template strategy、metadata、direct/RAG fallback | 阻止直接删旧模板 |
| LLM transport | `test_llm_openai_compat.py`、`test_llm_shared_http_pool.py`、`test_generation_runtime_bootstrap.py` | auth/logging/SSE parser/timeout/injected client/shared pool | openai_compat 拆分前必须先跑 |
| authority hooks | `test_qa_placeholder.py`、`test_chat_persistence.py`、`test_conversation_authority_client.py`、`test_generation_runtime_bootstrap.py` | hook 调用、gateway-owned skip、public-service schema | authority/persistence 抽共享时必须保留 |
| env/config namespace | `test_env_loader.py`、`test_generation_runtime_bootstrap.py` | unified LLM/embedding/rerank namespace，retired aliases | runtime config 抽取前置 |

建议重构前最小 collect/test 套件，等允许写缓存时执行：

```text
pytest --collect-only fastQA/tests
pytest fastQA/tests/test_qa_routes_file_modes.py fastQA/tests/test_file_routes_materialization.py fastQA/tests/test_qa_pdf_service.py fastQA/tests/test_qa_tabular_service.py
pytest fastQA/tests/test_qa_placeholder.py fastQA/tests/test_qa_kb_service.py
pytest fastQA/tests/test_graph_kb_planner_v2.py fastQA/tests/test_graph_kb_query_templates.py fastQA/tests/test_fastqa_kb_graph_integration.py
pytest fastQA/tests/test_llm_openai_compat.py fastQA/tests/test_llm_shared_http_pool.py fastQA/tests/test_generation_runtime_bootstrap.py
pytest fastQA/tests/test_chat_persistence.py fastQA/tests/test_conversation_authority_client.py
```

### 5. 可实施重构任务拆分

### TASK-301：Runner 拆分任务卡：拆 `qa.py` dispatcher/runner，保持 HTTP alias 不变

- 目标：把 `qa.py` 中 KB/PDF/tabular/hybrid runner 与 stream mapper/persistence hook 解耦，router 只保留 request/response shell 和 decorators。
- 范围：新增 `qa_dispatcher`、`kb_runner`、`pdf_runner`、`tabular_runner`、`hybrid_runner`、`stream_event_mapper`、`stream_persistence`；不改 HTTP path。
- 证据基础：`qa.py:691-905` 是 route dispatch；`1452-1611` 是 aliases；`1085-1322` 是 stream mapper；`366-421` 是 terminal persistence。
- 前置条件：冻结 route alias 表、SSE golden event、gateway-owned persistence skip 行为。
- 实施步骤：先提取纯函数/小 facade，保留 `_iter_route_frames = _iter_qa_frames` 兼容；每步迁移一个 route runner；最后移动 stream mapper。
- 兼容/回滚：router decorators 和 `AskRequest` 不动；runner facade 返回同样 generator event。
- 验证：`test_qa_route_aliases.py`、`test_qa_routes_file_modes.py`、`test_qa_placeholder.py`、`test_qa_pool_timeout_contract.py`、authority hook tests。
- 风险：高。SSE first event、pool timeout、terminal hook、hybrid dispatch 都在同文件耦合。

### TASK-302：PDF shim ports：替换 `MaterialScienceAgent` duck typing

- 目标：用正式 `PdfKbVerificationPort` 和 `PdfDirectQueryPort` 替换 `_build_pdf_agent()` 动态 `_PdfAgent`。
- 范围：`file_route_service.py:163-213`、`file_routes.py:216-225`、`qa_pdf/streaming.py:46-76`、`qa_pdf/service.py:283-324`。
- 证据基础：`smart_query` 与 `query_pdf_directly` 均在 live path；`FileRouteService.iter_events()` 非 router live。
- 前置条件：定义成功/失败返回 schema；为 DOI direct、single PDF、PDF+KB verification 建 contract tests。
- 实施步骤：新增 port/protocol；写 adapter 包旧 `_PdfAgent` 行为；`qa_pdf` 调 port 方法；`file_routes.py` 注入 port；删除或降级旧 duck type。
- 兼容/回滚：保留一版 `LegacyMaterialScienceAgentAdapter`，feature flag 或 constructor injection 回滚。
- 验证：`test_file_routes_materialization.py`、`test_qa_pdf_service.py`、`test_file_route_service.py`、`test_qa_pool_timeout_contract.py`。
- 风险：中高。PDF direct query 和 KB verification 是用户可见行为。

### TASK-303：NotReady event factory：统一 `FASTQA_NOT_READY` 语义但保留 code

- 目标：统一 runtime unavailable 与 phase1 placeholder 的事件创建，消除同码不同文案不可追踪问题。
- 范围：`qa.py:806-818`、`qa_kb/service.py:24-55`、`health.py:107-109`。
- 证据基础：生产 runtime not ready path 与测试-only phase1 placeholder 同用 `FASTQA_NOT_READY`。
- 前置条件：新增 `not_ready_kind`/`detail.reason` 测试；确认 frontend/gateway 只依赖 `code` 而非全文 message。
- 实施步骤：新增 `NotReadyEventFactory`；router 使用 `runtime_unavailable`；placeholder 使用 `legacy_placeholder`；保持 `code=FASTQA_NOT_READY`。
- 兼容/回滚：旧 message 可保留一版；health `runtime_mode=placeholder` 不变。
- 验证：`test_qa_placeholder.py`、`test_qa_kb_service.py`、`test_health.py`。
- 风险：中。错误文案可能被前端或人工流程依赖。

### TASK-304：Graph classic adapter：隔离旧模板，暂不删除

- 目标：把 `build_legacy_template_query_plan()` 和 `execute_graph_kb_plan()` 包装为 `GraphClassicTemplateAdapter`，让 V2 planner/executor 对 legacy 依赖可见且可逐步替代。
- 范围：`graph_kb/client.py`、`planner_v2.py`、`executor_v2.py`、`service.py`、direct renderer metadata。
- 证据基础：V2 planner/executor 仍直接使用 legacy template；tests 固定 parity。
- 前置条件：保留 `legacy_template_id`、`legacy_route_family`、`graph_strategy=template` metadata golden tests。
- 实施步骤：抽 adapter；V2 通过 adapter 查询 classic template；新增 parity tests；逐个 intent 建 V2 native replacement。
- 兼容/回滚：旧 `client.py` API 暂保留导出。
- 验证：`test_graph_kb_planner_v2.py`、`test_graph_kb_query_templates.py`、`test_graph_kb_service.py`、`test_fastqa_kb_graph_integration.py`。
- 风险：高。Graph direct answer 与 RAG fallback 都依赖 metadata。

### TASK-305：共享 file readiness/materialization contract

- 目标：把 `execution_files` materialization、source availability、strict MinIO、preview policy 抽成共享 file contract。
- 范围：`services/file_routes.py`、`qa_tabular/service.py`、`file_context/service.py`、`request_adapter.py` 的 file 字段。
- 证据基础：file readiness 在 `file_routes.py` 和 `qa_tabular/service.py` 双重实现，但语义不完全相同。
- 前置条件：建立 readiness matrix：PDF/table、ready/pending/failed、local_path/storage_ref/storage_error、strict/non-strict、preview allowed。
- 实施步骤：新增 `MaterializedExecutionFiles`、`FileAvailability`；先在 `file_routes.py` 使用；再迁入 `qa_tabular/service.py`；最后收敛 error event。
- 兼容/回滚：保留现有 error message 和 `execution_file_unavailable` code。
- 验证：`test_file_routes_materialization.py`、`test_qa_tabular_service.py`、`test_request_adapter.py`、`test_qa_routes_file_modes.py`。
- 风险：中高。文件未就绪处理是用户上传链路核心体验。

### TASK-306：OpenAI-compatible transport 分层

- 目标：拆 `openai_compat.py` 为 auth、transport、SSE parser、model logging、thinking controls、public facade。
- 范围：`openai_compat.py:1-1162`、`thinking.py`、`upstream_auth_logging.py`、`shared_http_pool.py`。
- 证据基础：单文件混合 endpoint normalization、client ownership、timeouts、headers、logging、pool timeout、parser、thinking controls。
- 前置条件：保留 `OpenAICompatChatAdapter/OpenAICompatClient` public API；冻结 SSE parser bad-json/error-frame 行为。
- 实施步骤：先移动纯函数 parser/auth 到新模块；再移动 logging decorator；最后保留 facade imports。
- 兼容/回滚：`app.integrations.llm.__init__` export 不变；类名不变。
- 验证：`test_llm_openai_compat.py`、`test_llm_thinking.py`、`test_llm_shared_http_pool.py`、`test_generation_runtime_bootstrap.py`、pool timeout tests。
- 风险：高。streaming/timeout 轻微变化会影响全链路。

### TASK-307：依赖去重低风险 chore

- 目标：去重 `fastQA/pyproject.toml` 依赖，降低 resolver 噪声。
- 范围：仅 `fastQA/pyproject.toml`，独立提交。
- 证据基础：`openai`、`pandas`、`openpyxl` 重复；`pymupdf/PyMuPDF` casing 重复；dev `httpx` 与 project 依赖重复。
- 前置条件：确认 CI/镜像没有依赖重复顺序；确认 lockfile 策略。
- 实施步骤：保留 `openai>=1.40,<2.0`；统一 `PyMuPDF>=1.24,<2.0`；去重 `pandas/openpyxl`；评估 dev `httpx` 是否保留。
- 兼容/回滚：单独 commit，失败可直接 revert。
- 验证：`pip install -e fastQA` 或镜像 install smoke、`python -c` import smoke、PDF/table/LLM tests。
- 风险：低。本轮未实施，因硬约束禁止修改 fastQA 依赖文件。

### 6. 不可立即处理项与阻塞原因

- Graph legacy template 删除：blocked。V2 planner/executor live 使用旧模板，且 parity tests 明确断言旧策略和 metadata。
- `_build_pdf_agent()` 删除：blocked。PDF KB verification 与 DOI direct query 仍依赖 `smart_query`/`query_pdf_directly`。
- `legacy_answer_from_pdf` 直接替换：blocked。当前是 PDF engine facade，tests 固化 streaming/timeout。
- `FASTQA_NOT_READY` 改码：blocked。runtime not ready 和 placeholder tests 都断言该 code；只能先加 detail 区分语义。
- authority/persistence 从 fastQA 完全移除：blocked。router 当前负责 hook 调用和 gateway-owned skip；只能先抽 shared adapter/policy。
- `qa_tabular/service.py` readiness 直接删减：blocked。file_routes 与 tabular service 的 readiness 语义不同，且 strict/non-strict preview tests 固定差异。
- `pytest --collect-only fastQA/tests`：本轮未运行。阻塞原因是只读硬约束和 pytest cache 风险。
- `pyproject.toml` 去重：本轮未实施。阻塞原因是用户明确禁止修改 fastQA 依赖文件。

### 7. 最终进入重构前检查清单

- [ ] 已运行 `pytest --collect-only fastQA/tests`，并确认没有收集错误。
- [ ] 已冻结 HTTP alias 表，特别是 `/api/v1/fast/ask` 绑定 SSE 的现状。
- [ ] 已冻结 KB/PDF/tabular/hybrid 四条 live path 的 metadata/content/done/error event 序列。
- [ ] 已为 `FASTQA_NOT_READY` 建立 `runtime_unavailable` 与 `legacy_placeholder` 两种 detail 兼容测试。
- [ ] 已确认 gateway/public-service 对 authority hook、internal token、gateway-owned persistence 的契约。
- [ ] 已建立 file readiness matrix，覆盖 strict/non-strict、PDF/table、local_path/storage_ref/storage_error、preview。
- [ ] 已保留 Graph legacy parity tests，不在 native V2 parity 完成前删除旧模板。
- [ ] 已保留 `OpenAICompatChatAdapter/OpenAICompatClient` public API 和 SSE parser 行为。
- [ ] 已将 pyproject 去重作为独立低风险 chore，不混入 runner/transport 重构。
- [ ] 已确认任何实施 PR 都只触及一个任务卡范围，并在 PR 描述列出 touched services、commands run、API/routing contract changes。
