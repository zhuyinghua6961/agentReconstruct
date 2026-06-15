# patent 重构审计文档

> 状态：已完成第一轮只读审计。本文档只记录基于代码阅读得到的证据，审计产物位于独立目录，不修改业务代码。

## 1. 审计范围

- 已阅读目录：`patent/server_fastapi/`、`patent/server/`、`patent/tests/`、`patent/docs/`。
- 已阅读关键文件：`server_fastapi/app.py`、`routers/ask.py`、`routers/health.py`、`routers/original.py`、`server/schemas/request_models.py`、`server/services/chat_persistence.py`、`server/patent/file_routes.py`、`executor.py`、`kb_service.py`、`pdf_service.py`、`tabular/`、`hybrid_synthesis.py`、`planning_hot_pool.py`、`rerank_service.py`、`retrieval_service.py`、`runtime.py`、`upstream_http.py`、`upstream_transport.py`、`config.py`、`pyproject.toml`、`README.md`。
- 未覆盖或需要本地进一步验证的范围：未运行测试；gateway 当前是否仍调用兼容 `/api/ask` 路径需要跨服务调用日志或 gateway registry 确认。

## 2. 当前 live path

### 2.1 服务入口

- app factory / main entry：`patent/server_fastapi/app.py:create_app()`。
- router 注册位置：`server_fastapi/routers/register_routers()`，包括 ask、health、original 等 router。
- lifespan/startup/shutdown：`_lifespan` 和 `_bootstrap_app_state` 负责 startup bootstrap，shutdown 逐项 close app.state resource。
- README 漂移：`patent/README.md` 仍称 “Phase 1 scaffold”，但代码已包含完整 FastAPI、durable persistence、PDF/table/hybrid、Graph KB、LLM pool、planning hot pool、rerank、runtime dispatcher 和大量 tests。

关键证据：

```text
patent/README.md:
Phase 1 currently bootstraps the standalone `patent` FastAPI service scaffold under `patent/` only.
```

```text
核心模块规模：
patent/server_fastapi/app.py              765 lines
patent/server_fastapi/routers/ask.py      391 lines
patent/server/patent/file_routes.py       1711 lines
patent/server/patent/runtime.py           1077 lines
patent/server/patent/retrieval_service.py 2922 lines
```

### 2.2 对外接口路径

| 接口路径 | 方法 | 所在文件 | 当前职责 | 是否 active |
|---|---|---|---|---|
| `/api/patent/ask` | POST | `server_fastapi/routers/ask.py` | patent sync ask | active live path |
| `/api/v1/patent/ask` | POST | `server_fastapi/routers/ask.py` | patent sync ask v1 | active live path |
| `/api/patent/ask_stream` | POST | `server_fastapi/routers/ask.py` | patent SSE ask | active live path |
| `/api/v1/patent/ask_stream` | POST | `server_fastapi/routers/ask.py` | patent SSE ask v1 | active live path |
| `/api/ask`, `/api/v1/ask` | POST | `server_fastapi/routers/ask.py` | non-patent-prefix compatibility ask | deprecated but still referenced / registered |
| `/api/ask_stream`, `/api/v1/ask_stream` | POST | `server_fastapi/routers/ask.py` | non-patent-prefix compatibility stream | deprecated but still referenced / registered |
| `/api/health`, `/api/v1/health` | GET | `server_fastapi/routers/health.py` | dynamic readiness and durable/file gate | active live path |
| `/api/patent/original/{canonical_patent_id}` | GET/HEAD | `server_fastapi/routers/original.py` | local original compatibility route returns 503 | deprecated but still referenced / registered |
| `/api/v1/patent/original/{canonical_patent_id}` | GET/HEAD | `server_fastapi/routers/original.py` | local original compatibility route returns 503 | deprecated but still referenced / registered |
| file route `pdf_qa` | contract route | `server/patent/file_routes.py` | PDF patent file QA | active live path via ask contract |
| file route `tabular_qa` | contract route | `server/patent/file_routes.py` | tabular patent file QA | active live path via ask contract |
| file route `hybrid_qa` | contract route | `server/patent/file_routes.py` | hybrid PDF/table/KB patent QA | active live path via ask contract |

接口注册证据：

```python
@router.post("/api/ask")
@router.post("/api/v1/ask")
@router.post("/api/patent/ask")
@router.post("/api/v1/patent/ask")
async def patent_ask(...):
    ...

@router.post("/api/ask_stream")
@router.post("/api/v1/ask_stream")
@router.post("/api/patent/ask_stream")
@router.post("/api/v1/patent/ask_stream")
async def patent_ask_stream(...):
    ...
```

### 2.3 核心调用链

```text
gateway -> patent /api/patent/ask_stream
  -> ask router parses headers + patent request contract
  -> durable/file/readiness gates
  -> AskService
  -> PatentExecutor
  -> route:
     kb_qa -> patent runtime/retrieval/planning/synthesis
     pdf_qa/table/hybrid -> file_routes planner/cache/branch runners/hybrid synthesis
  -> stream events / terminal persistence
```

## 3. 发现的重构点

### R-001：FastAPI app 是巨型 bootstrap

- 严重程度：P1
- 类型：生命周期混乱 / app.state 过载 / bootstrap god-object
- 代码位置：
  - `patent/server_fastapi/app.py`
  - `create_app()`
  - `_bootstrap_service_state()`
  - `_lifespan()`
- 接口路径：
  - 全部 `/api/patent/*`
  - `/api/v1/patent/*`
  - health/original
- 关键代码片段：

```python
def _bootstrap_service_state(app: FastAPI) -> None:
    settings = app.state.settings
    key_factory = app.state.redis_key_factory
    redis_client = getattr(getattr(app.state, "redis_bindings", None), "client", None)
    execution_lock_manager = ExecutionLockManager(redis_client, key_factory=key_factory)
    execution_cache = ExecutionCache(redis_client, key_factory)
    chat_persistence_service = ChatPersistenceService(...)
    patent_shared_upstream_provider = None
    patent_pdf_service = None
    patent_tabular_service = None
    patent_hybrid_synthesis_client = None
    patent_runtime = None
    patent_graph_kb_client = None
```

- 当前问题：`app.py` 约 765 行，负责配置读取、component status、Redis/authority、LLM pool、planning hot pool、upstream gate、PDF/Tabular/Hybrid、runtime、Graph KB、AskService、OriginalService、关闭链。`app.state` 挂载 `authority_client`、`execution_cache`、`execution_lock_manager`、`chat_persistence_service`、`shared_llm_pool`、`planning_hot_pool`、`planning_upstream_gate`、`patent_pdf_service`、`patent_tabular_service`、`patent_hybrid_synthesis_client`、`patent_runtime`、`patent_graph_kb_client`、`ask_service`、`original_service` 等。
- 建议重构方式：引入 `PatentBootstrapper` 构建容器，`PatentServiceContainer` 持有服务实例，`ComponentStatusRegistry` 统一 status。
- 是否可抽共享包：bootstrap/lifecycle/component status 可共享；patent business wiring 保留本地。
- 建议目标模块：`patent/server/patent/bootstrap.py`、`patent/server/patent/container.py`、`packages/agent_common/runtime/lifecycle.py`。
- 设计模式建议：Composition Root、Service Container、Registry。
- 影响范围：启动、health readiness、tests fixture、router state access。
- 风险：中高。app.state 兼容层不完整会破坏 router/test。
- 测试计划：`patent/tests/fastapi_contract/test_ask_contract.py`、`test_health_contract.py`、app factory lifecycle tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：先列 app.state consumer 清单，容器落地后保留兼容属性。

### R-002：手写资源关闭链应改为 LifecycleManager/ResourceRegistry

- 严重程度：P1
- 类型：生命周期混乱 / resource cleanup
- 代码位置：
  - `patent/server_fastapi/app.py`
  - `_close_state_resource()`
  - `_bootstrap_app_state()`
  - `_lifespan()`
- 接口路径：
  - 启动失败、shutdown 影响全部接口
- 关键代码片段：

```python
def _close_state_resource(container: object, attr_name: str) -> None:
    resource = getattr(container, attr_name, None)
    if resource is None:
        return
    try:
        setattr(container, attr_name, None)
    except Exception:
        pass
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return
```

```python
finally:
    _close_state_resource(app.state, "authority_client")
    _close_state_resource(app.state, "shared_llm_pool")
    _close_state_resource(app.state, "planning_hot_pool")
    _close_state_resource(app.state, "planning_upstream_gate")
    _close_state_resource(app.state, "patent_pdf_service")
    _close_state_resource(app.state, "patent_tabular_service")
    _close_state_resource(app.state, "patent_hybrid_synthesis_client")
    _close_state_resource(app.state, "patent_runtime")
```

- 当前问题：异常路径和 lifespan finally 逐个 close，吞异常，无关闭顺序声明，无 close 结果记录。`server/patent/resource_registry.py` 现有 registry 只是路径发现，不是资源生命周期 registry。
- 建议重构方式：新增 `LifecycleManager` 注册 closeable，按 LIFO 关闭，记录错误到 status/log。
- 是否可抽共享包：是，gateway/public/QA 都有 closeable resources。
- 建议目标模块：`patent/server/patent/lifecycle.py`、`packages/agent_common/runtime/resource_registry.py`。
- 设计模式建议：Resource Registry、LIFO Lifecycle Manager。
- 影响范围：startup/shutdown、Gunicorn worker restart、tests teardown。
- 风险：中。关闭顺序改变可能影响 shared HTTP client 和下游 answer clients。
- 测试计划：模拟 bootstrap 中途失败；TestClient lifespan exit 后资源 closed；关闭异常被记录但不阻断。
- 是否可立即删除：否。
- 删除或迁移前置条件：为每个资源声明 owner、close method、依赖顺序。

### R-003：gateway-facing patent contract 应抽共享包

- 严重程度：P1
- 类型：gateway contract / protocol drift
- 代码位置：
  - `patent/server/schemas/request_models.py`
  - `parse_patent_request()`
- 接口路径：
  - `/api/patent/ask`
  - `/api/v1/patent/ask`
  - `/api/patent/ask_stream`
  - `/api/v1/patent/ask_stream`
- 关键代码片段：

```python
requested_mode = _require_exact_string(payload, "requested_mode", "patent", protocol=True)
actual_mode = _require_exact_string(payload, "actual_mode", "patent", protocol=True)
route = _require_protocol_literal(payload, "route", set(_ROUTE_TO_SOURCE_SCOPES))
turn_mode = _require_protocol_literal(payload, "turn_mode", {"kb_only", "file_only", "mixed"})
kb_enabled = _require_bool(payload, "kb_enabled")
allow_kb_verification = _require_bool(payload, "allow_kb_verification")
execution_files = _require_list_of_dicts(payload, "execution_files")
selected_file_ids = _require_int_list(payload, "selected_file_ids")
```

```python
allowed_source_scopes = _ROUTE_TO_SOURCE_SCOPES[route]
if source_scope not in allowed_source_scopes:
    raise ProtocolMismatchRequestError(...)
if any(file_id not in execution_file_ids for file_id in selected_file_ids):
    raise ProtocolMismatchRequestError("selected_file_ids must exist in execution_files")
if selected_families != expected_families:
    raise ProtocolMismatchRequestError("selected_file_ids must match source_scope exactly")
```

- 当前问题：校验保护得很严格，但 gateway/public-service 若另写 route/source_scope/file_selection 规则会漂移。
- 建议重构方式：抽 `PatentAskContract`、`PatentRouteName`、`PatentSourceScope`、file family inference、protocol mismatch error schema 到共享 contract 包。
- 是否可抽共享包：是，高优先级。
- 建议目标模块：`packages/agent_common/contracts/patent_ask.py`。
- 设计模式建议：Shared Contract、Parser/Validator。
- 影响范围：gateway payload builder、patent ask、contract tests、错误码。
- 风险：中高。错误文案/status code 改变会破坏 contract tests。
- 测试计划：把 `patent/tests/fastapi_contract/test_ask_contract.py` 中协议错误用例迁移为共享 contract tests；gateway/patent 双边复用。
- 是否可立即删除：否。
- 删除或迁移前置条件：确认 gateway patent payload builder 字段全集。

### R-004：ask router 混合 durable mode、file route gate、dependency readiness、stream slot、gateway headers

- 严重程度：P1
- 类型：router orchestration / policy leakage
- 代码位置：
  - `patent/server_fastapi/routers/ask.py`
  - `_parse_patent_request_or_raise()`
  - `patent_ask()`
  - `patent_ask_stream()`
- 接口路径：
  - `/api/patent/ask`
  - `/api/v1/patent/ask`
  - `/api/patent/ask_stream`
  - `/api/v1/patent/ask_stream`
- 关键代码片段：

```python
payload = await _read_json_payload(request)
gateway_task_execution = _is_truthy_header(request.headers.get("x-gateway-task-execution"))
gateway_owned_persistence = _is_truthy_header(request.headers.get("x-gateway-owned-persistence"))
patent_stream_capability = request.headers.get(PATENT_STREAM_CAPABILITY_HEADER)
payload["options"] = inject_stream_capability_option(...)
return parse_patent_request(payload)
```

```python
ask_request = await _parse_patent_request_or_raise(request)
_ensure_patent_file_routes_enabled(request=request, ask_request=ask_request)
_ensure_durable_mode_enabled(request=request, ask_request=ask_request)
user_id = _resolve_user_id(ask_request=ask_request, authorization=authorization)
_ensure_durable_dependencies_ready(request=request, ask_request=ask_request)
payload = await _run_in_ask_executor(request, _get_ask_service(request).sync_ask, ask_request, user_id=user_id)
```

- 当前问题：router 同时处理 gateway persistence header 注入、durable mode gate、file route gate、dependency readiness、auth user resolve、stream slot、thread executor、SSE error event。
- 建议重构方式：抽 `PatentRequestAdapter`、`PatentReadinessGate`、`PatentStreamController`。
- 是否可抽共享包：stream slot/SSE error 和 readiness gate 可部分共享；patent route policy 留本地。
- 建议目标模块：`patent/server_fastapi/adapters/patent_request.py`、`gates.py`、`streaming.py`。
- 设计模式建议：Adapter、Policy/Gate、Controller。
- 影响范围：ask/ask_stream 行为、错误码、headers/options contract。
- 风险：中高。header 注入顺序改变会影响 gateway-owned persistence 和 stream capability。
- 测试计划：headers `x-gateway-task-execution`、`x-gateway-owned-persistence`、stream capability、429 slot、durable disabled、file route disabled。
- 是否可立即删除：否。
- 删除或迁移前置条件：先冻结 ask router contract tests。

### R-005：`file_routes.py` 已是独立 file-QA 子系统

- 严重程度：P1
- 类型：巨型模块 / domain orchestration
- 代码位置：
  - `patent/server/patent/file_routes.py`
  - `plan_patent_file_route()`
  - `dispatch_patent_file_route()`
  - `PatentExecutor._execute_file_route()`
- 接口路径：
  - ask contract `route=pdf_qa`
  - `route=tabular_qa`
  - `route=hybrid_qa`
- 关键代码片段：

```python
def plan_patent_file_route(contract: PatentFileContract) -> PatentFileRoutePlan:
    if contract.route == "pdf_qa":
        return PatentFileRoutePlan(... handler="pdf", file_families=("pdf",), include_kb=False)
    if contract.route == "tabular_qa":
        return PatentFileRoutePlan(... handler="tabular", file_families=("table",), include_kb=False)
    handler, file_families, include_kb = _HYBRID_SCOPE_TO_PLAN[contract.source_scope]
    return PatentFileRoutePlan(...)
```

```python
contract = build_patent_file_contract(
    question=request.question,
    route=request.route,
    source_scope=request.source_scope,
    selected_file_ids=request.selected_file_ids,
    primary_file_id=request.primary_file_id,
    execution_files=request.execution_files,
    file_selection=request.file_selection,
    kb_enabled=request.kb_enabled,
    allow_kb_verification=request.allow_kb_verification,
)
```

- 当前问题：`file_routes.py` 约 1711 行，含 plan、cache/singleflight、PDF branch、tabular branch、hybrid synthesis、stream preview/final、fallback rules。不是 helper，而是 file-QA orchestrator。
- 建议重构方式：拆 `PatentFileRoutePlanner`、`FileRouteCacheCoordinator`、`PdfBranchRunner`、`TabularBranchRunner`、`HybridFileSynthesizer`。
- 是否可抽共享包：file route cache/singleflight、structured stream emitter 可共享；patent synthesis prompt/规则留本地。
- 建议目标模块：`patent/server/patent/file_qna/`。
- 设计模式建议：Strategy、Orchestrator、Cache Coordinator。
- 影响范围：PDF/table/hybrid 文件问答、streaming preview/final、cache replay。
- 风险：中高。stream event 顺序和 cache replay 语义容易回归。
- 测试计划：`patent/tests/test_patent_file_routes.py`、fastapi file ask/stream contract tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：先提取 planner/cache coordinator，保持 public function 兼容。

### R-006：LLM/upstream/planning/rerank 与 fastQA/highThinkingQA 重复

- 严重程度：P2
- 类型：共享 LLM / upstream infrastructure duplication
- 代码位置：
  - `patent/server/patent/upstream_http.py`
  - `upstream_transport.py`
  - `planning_hot_pool.py`
  - `rerank_service.py`
  - `pdf_service.py`
  - `hybrid_synthesis.py`
- 接口路径：
  - all LLM-backed patent ask/file/kb paths
- 关键代码片段：

```python
class PatentSharedUpstreamHttpProvider:
    if self.enabled:
        timeout = httpx.Timeout(...)
        self._client = httpx.Client(
            timeout=timeout,
            limits=httpx.Limits(
                max_keepalive_connections=self.max_keepalive_connections,
                max_connections=self.max_connections,
                keepalive_expiry=self.keepalive_expiry_seconds,
            ),
        )
```

```python
def build_patent_stage2_rerank_fn(...):
    base_url = _first_env("RERANK_BASE_URL", "PATENT_STAGE2_RERANK_BASE_URL")
    model = _first_env("RERANK_MODEL", "PATENT_STAGE2_RERANK_MODEL")
    ...
    return _rerank_fn
```

- 当前问题：patent 自有 httpx pool/transport metrics、rerank、planning hot lane；fastQA/highThinkingQA 也有 OpenAI-compatible client、rerank/hot pool/upstream gate。
- 建议重构方式：抽共享 OpenAI-compatible client、upstream pool/gate、model call logging、rerank adapter、thinking controls；patent 保留 prompt 和业务 stage。
- 是否可抽共享包：是。
- 建议目标模块：`packages/agent_common/llm/`、`packages/agent_common/rerank/`、`packages/agent_common/runtime/upstream_gate.py`。
- 设计模式建议：Adapter、Provider、Policy。
- 影响范围：LLM 调用、连接池、日志、超时、rerank fallback。
- 风险：中高。各服务日志字段/env 前缀不同。
- 测试计划：三服务 openai-compatible client tests、pool timeout tests、rerank fallback tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：定义服务名/env 前缀/日志字段兼容策略。

## 4. 可抽共享能力清单

| 能力 | 当前重复位置 | 建议共享模块 | 迁移优先级 |
| -- | ------ | ------ | ----- |
| Gateway patent ask contract | `server/schemas/request_models.py`、gateway payload builder | `packages/agent_common/contracts/patent_ask.py` | P1 |
| Resource lifecycle close | `server_fastapi/app.py:_close_state_resource`、其他服务 app.state close | `packages/agent_common/runtime/lifecycle.py` | P1 |
| Component status/readiness | `app.py`、`routers/health.py` | `packages/agent_common/runtime/component_status.py` | P2 |
| OpenAI-compatible HTTP client | patent upstream/pdf/hybrid、fastQA/highThinkingQA | `packages/agent_common/llm/openai_compatible.py` | P1 |
| Upstream pool/gate metrics | `upstream_transport.py`、`planning_hot_pool.py`、fastQA pools | `packages/agent_common/llm/upstream_pool.py` | P2 |
| Rerank adapter | `rerank_service.py`、fastQA rerank | `packages/agent_common/llm/rerank_client.py` | P2 |
| File route cache/singleflight | `file_routes.py` | `packages/agent_common/runtime/singleflight.py` | P3 |
| Structured stream events | `stream_events.py`、ask routers | `packages/agent_common/contracts/stream_event.py` | P1 |

## 5. 可清理遗留代码清单

| 代码位置 | 当前状态 | 是否注册 | 是否被引用 | 建议处理 |
| ---- | ---- | ---- | ----- | ---- |
| `/api/patent/ask`, `/api/v1/patent/ask` | active live path | 是 | 是 | 保留 |
| `/api/patent/ask_stream`, `/api/v1/patent/ask_stream` | active live path | 是 | 是 | 保留 |
| `/api/ask`, `/api/v1/ask` | deprecated but still referenced | 是 | gateway 可能仍可调用 | 标记兼容入口，gateway cutover 后下线 |
| `/api/ask_stream`, `/api/v1/ask_stream` | deprecated but still referenced | 是 | gateway 可能仍可调用 | 标记兼容入口 |
| original local routes | deprecated but still referenced | 是 | gateway/public route 需确认 | 保留 503 兼容错误或取消注册 |
| `server/patent/resource_registry.py` | scaffold / placeholder | 不适用 | 路径发现引用未知 | 改名或另建 lifecycle registry |
| `patent/README.md` scaffold 描述 | scaffold / doc drift | 不适用 | 文档 | 更新为真实服务说明 |
| `patent/docs/2026-*` | archive / historical baseline | 不适用 | 文档 | 保留历史基线，不进入主线 |

## 6. 接口与契约风险

- gateway -> backend contract：patent 严格要求 `requested_mode=patent`、`actual_mode=patent`、`route/source_scope/turn_mode/file_selection` 匹配，需共享 validator。
- frontend -> gateway contract：frontend 不应直连 patent；兼容 `/api/ask` 是否仍需保留由 gateway cutover 决定。
- backend -> public-service contract：chat persistence/authority client 与 gateway-owned persistence 需统一 owner。
- internal token/auth headers：gateway task headers、stream capability headers、durable mode auth 要抽 contract。
- SSE event schema：file route preview/final/cache replay 顺序需冻结。
- task event schema：gateway task execution headers影响 patent persistence path。

## 7. 测试计划

- 单元测试：request contract parser、file route planner/cache、lifecycle manager、rerank/upstream clients。
- contract test：`/api/patent/ask*`、兼容 `/api/ask*`、protocol mismatch errors。
- stream/SSE test：ask_stream content/done/error、file preview/final/cache replay。
- integration smoke test：gateway -> patent ask_stream kb/pdf/table/hybrid。
- backward compatibility test：non-patent prefix aliases、original local route 503。
- failure/cancel/retry test：durable disabled、file route disabled、dependency degraded、stream slot exhaustion。
- persistence test：gateway-owned persistence headers、ChatPersistenceService terminal behavior。
- quota/auth test：patent uses gateway/public-service ownership; verify auth user resolve and internal headers.
- file route test：pdf/table/hybrid source_scope, selected_file_ids, primary_file_id, execution_files family matching。

## 8. 建议重构顺序

1. P1：更新 README/inventory，标明 patent 已非 scaffold。
2. P1：冻结 patent ask contract tests，并抽共享 contract。
3. P1：引入 `PatentServiceContainer` 和兼容 app.state bridge。
4. P1：引入 `LifecycleManager`，先替换 close 链但保持资源顺序。
5. P1：拆 ask router 的 request adapter/readiness gate/stream controller。
6. P1：拆 file_routes planner/cache/branch runners。
7. P2：抽共享 LLM/upstream/rerank。
8. P2：评估兼容 `/api/ask*` 和 local original route 下线。

## 9. 需要进一步确认的问题

1. gateway 当前是否仍调用 `/api/ask`，还是已经切到 `/api/patent/ask`。
2. public-service/gateway 是否已有 patent contract 副本，需要 diff 规则。
3. original route 是否计划永久由 gateway/public-service 承载。
4. `PatentResourceRegistry` 是否只作为路径 registry；若是，应另起 `LifecycleManager`。
5. LLM shared package 抽取前需确认 fastQA/highThinkingQA/patent 的 env 前缀、auth mode、thinking 参数、日志字段。

## 第二轮深度补充

> 审计方式：本轮只读执行了用户指定的 8 条命令，并继续用 `nl -ba` / `sed` / `rg` / `wc` 读取代码证据；未运行测试、未启动服务、未修改源码。

### A. 强制命令结果摘要

已执行命令：

```text
find patent -type f
find patent -type f \( -name "*.py" -o -name "*.js" -o -name "*.vue" \) -print0 | xargs -0 wc -l | sort -nr | head -50
rg "APIRouter|@router|app.include_router|path:|fetch|axios|EventSource" patent
rg "deprecated|legacy|fallback|scaffold|placeholder|NOT_READY|not ready|shim|compat|TODO|FIXME|shadow|archive|obsolete|retired|rollout" patent
rg "app\.state|request\.app\.state" patent
rg "LLM_|EMBEDDING_|RERANK|REDIS|MINIO|NEO4J|VECTOR_DB|AUTH|TOKEN|RESOURCE_ROOT|RUNTIME_ROOT|STATE_ROOT" patent
rg "OpenAI|openai|embedding|rerank|auth_headers|httpx|stream|SSE|api_key|Bearer" patent
rg "requested_mode|actual_mode|source_scope|turn_mode|execution_files|selected_file_ids|primary_file_id|gateway-owned|X-Gateway" patent
```

关键结果：

- `find patent -type f` 显示 `patent/` 不只是源码，也包含 `.tmp/`、`.pytest_cache/`、`__pycache__/`、历史 `docs/` 和运行产物；审计结论只引用源码、配置、README 和 tests 证据。
- 代码行数 top 50 中最大模块包括 `patent/server/patent/retrieval_service.py` 2922 行、`pdf_service.py` 2777 行、`file_routes.py` 1711 行、`tabular_service.py` 1596 行、`runtime.py` 1077 行，说明 patent 已是完整业务服务而非 scaffold。
- HTTP router 仅发现 `server_fastapi/routers/ask.py`、`health.py`、`original.py`，注册点在 `server_fastapi/routers/__init__.py`。
- `app.state` 主要由 `server_fastapi/app.py` 写入，由 ask/health/original routers 和 tests 读取；这是目前隐式 Service Locator。
- LLM/embedding/rerank/Redis/MinIO/Neo4j/auth 配置跨 `config.py`、runtime、pdf/tabular/hybrid、rerank、object reader、scripts 和 tests 分散存在。

### B. 第一轮结论复核

第一轮 R-001..R-006 结论经第二轮代码复核仍成立，但需要补充三点：

- R-001/R-002 的 `app.py` bootstrap 问题比第一轮更明确：`_bootstrap_service_state()` 行 309-658 同时创建 infra、LLM pool、planning hot pool、file QA、runtime、Graph KB、AskService 和 OriginalService；`_lifespan()` 行 700-721 手写关闭链。
- R-003/R-004 的 contract/router 问题需要纳入 gateway 和 fastQA 对照：gateway 在 `gateway/app/services/route_decision.py` 行 12-72 自行决策 `actual_mode/source_scope`；fastQA 在 `fastQA/app/services/request_adapter.py` 行 247-413 自行适配并校验相似字段。patent 的 `parse_patent_request()` 行 185-317 更严格，三方规则存在漂移风险。
- R-005/R-006 需要拆分优先级：`file_routes.py` 是 file-QA orchestrator；`pdf_service.py`、`tabular_service.py`、`hybrid_synthesis.py` 同时包含业务提示词、OpenAI-compatible client、输出修复和流式回放，不应一次性抽共享包，需先切业务层/基础层边界。

### C. Bootstrap 深挖

`server_fastapi/app.py` 当前是组合根，但没有显式容器。`create_app()` 行 725-765 先创建 dispatcher 和默认 component status，再立即 `_bootstrap_app_state(app)`；`_bootstrap_service_state()` 行 309-658 把所有资源挂到 `app.state`。

| 资源 | 创建位置 | 职责 | 主要依赖者 | 是否需关闭 | 当前关闭逻辑 | 审计结论 |
|---|---:|---|---|---|---|---|
| `runtime_dispatcher` / dispatcher | `app.py:731-738` | ask 线程 limiter、stream 并发 slot、runtime_state | ask router、health router、tests | 未见 close | 无 close | 应纳入容器但不必 close；状态快照应走 registry |
| Redis client/bindings | `app.py:678-681` 调 `bootstrap_redis_state` | cache、lock、overlay、file route cache | ExecutionCache、ExecutionLockManager、tests | 是 | `app.py:694-696`、`718-720` close `redis_bindings.client` | 资源 owner 隐式在 app.state |
| `authority_client` | `app.py:269-283` | public-service authority writes/snapshots | ChatPersistenceService | 是 | `app.py:684`、`708` | 创建条件由 config/token/base_url 决定 |
| `execution_lock_manager` | `app.py:313` | durable conversation lock | ChatPersistenceService | 否 | 未 close | 纯 wrapper，可容器托管 |
| `execution_cache` | `app.py:314` | turn state、overlay、file-route cache/singleflight | ChatPersistenceService、runtime、file_routes、OriginalService | 否 | 未 close | redis-backed wrapper，不该单独 close |
| `chat_persistence_service` | `app.py:315-320` | prepare/accept/finalize/abort durable turn | AskService | 否 | 未 close | 与 gateway-owned persistence header 强耦合 |
| `shared_llm_pool` / `patent_shared_upstream_provider` | `app.py:341-379`、`646-650` | app 级 httpx shared client | PDF/Tabular/Hybrid/Runtime planning | 是 | `app.py:363-368`、`639-644`、`685/709/691/715` | 当前有重复 close alias 风险：`shared_llm_pool` 与 `patent_shared_upstream_provider` 同一对象 |
| `planning_hot_pool` | `app.py:379-427` | planning lane pool、hot client proxy | PatentRuntime stage1/stage2 | 是 | `app.py:413-418`、`633-638`、`686/710` | 应显式声明 lane/client ownership |
| `planning_upstream_gate` | `app.py:428-469` | planning 并发 gate | PatentRuntime stage1/stage2 | 可能 | `app.py:687/711`，异常分支未 close | 当前创建失败时只置 None |
| `patent_pdf_service` | `app.py:470-475` | PDF file QA | PatentExecutor/file_routes | 是 | `app.py:615-620`、`688/712` | answer client may own private httpx |
| `patent_tabular_service` | `app.py:477-502` | table file QA | PatentExecutor/file_routes | 是 | `app.py:609-614`、`689/713` | fallback answer client degraded status 在 component_status |
| `patent_hybrid_synthesis_client` | `app.py:503-526` | file/KB unified LLM synthesis | PatentExecutor/file_routes | 是 | `app.py:603-608`、`690/714` | client 与 fallback_rules 混在 orchestrator |
| `patent_runtime` | `app.py:527-540` | staged KB runtime | PatentKbService via PatentExecutor | 是 | `app.py:621-626`、`692/716` | `PatentRuntime.close()` 行 947-954 关闭内部 resources |
| `patent_graph_kb_client` | `app.py:541-579` | Neo4j graph KB | PatentKbService | 是 | `app.py:627-632`、`693/717` | degraded Graph KB 不影响普通 health |
| `ask_service` | `app.py:580-598` | sync/stream ask orchestration | ask router | 否 | 未 close | 构造时注入 PatentExecutor + ChatPersistenceService |
| `original_service` | `app.py:599-601` | local original view compatibility | original router | 否 | 未 close | router 当前硬禁用本地 route |

ResourceRegistry/ServiceContainer/lazy init/testability 结论：

- `server/patent/resource_registry.py` 行 7-33 的 `PatentResourceRegistry` 只是文件路径发现和 archive/vector 可用性判断，不是生命周期 registry。
- 当前没有 `ServiceContainer`；tests 直接读写 `app.state.ask_service`、`app.state.component_status`、`app.state.patent_runtime`，导致重构必须保留 app.state bridge。
- runtime 是 eager bootstrap：`build_default_patent_runtime()` 行 957-1077 发现 archive、创建 AnswerBuilder、PlanningClient、Chroma/embedding、MinIO loader、RetrievalService；archive 不存在时返回 None。可考虑 lazy init 但必须保留 file-only routes 在 runtime 缺失时仍可用的行为，已有 contract tests 覆盖。
- testability 当前靠 monkeypatch 类构造和 app.state 替换，缺容器后门；建议用 `PatentServiceContainer` + typed provider，app.state 只保留兼容属性。

### D. Ask Router 深挖

| 项 | 代码位置 | 证据与行为 |
|---|---:|---|
| sync ask paths | `ask.py:358-375` | `/api/ask`、`/api/v1/ask`、`/api/patent/ask`、`/api/v1/patent/ask` 都进 `patent_ask()` |
| stream ask paths | `ask.py:378-391` | `/api/ask_stream`、`/api/v1/ask_stream`、`/api/patent/ask_stream`、`/api/v1/patent/ask_stream` 都进 `patent_ask_stream()` |
| gateway persistence headers | `ask.py:99-136` | 只信 header `x-gateway-task-execution` / `x-gateway-owned-persistence`，会先从 body options 移除再注入 |
| stream capability | `ask.py:103-134` | `x-patent-stream-capability` 注入 options，仅按 route 生效 |
| durable mode gate | `ask.py:183-195` | durable request 且 `settings.durable_mode_enabled` false 返回 `DURABLE_MODE_DISABLED` |
| file route gate | `ask.py:198-210` | `pdf_qa/tabular_qa/hybrid_qa` 且 `PATENT_FILE_ROUTES_ENABLED` false 返回 `PATENT_FILE_ROUTE_DISABLED` |
| dependency readiness | `ask.py:213-230` | durable 需 redis/authority；KB 路径还需 runtime |
| auth user resolve | `ask.py:241-244` | 仅 durable 调 `require_auth_context()` |
| stream slot | `ask.py:248-261` | `runtime_dispatcher.try_acquire_stream_slot()` 失败映射 429 `PATENT_BUSY` |
| thread offload | `ask.py:264-271` | sync/stream next 都走 anyio thread limiter |
| SSE response | `ask.py:294-348` | `StreamingResponse(text/event-stream)`，错误被包装为 `{type:error}` frame |
| error mapping | `ask.py:42-65` | APIError 原样保留，非 APIError 映射 INTERNAL_ERROR |

调用链：

```text
POST /api/patent/ask_stream
  -> _parse_patent_request_or_raise()
  -> parse_patent_request()
  -> _ensure_patent_file_routes_enabled()
  -> _ensure_durable_mode_enabled()
  -> _resolve_user_id()
  -> _ensure_durable_dependencies_ready()
  -> _build_streaming_response()
  -> AskService.stream_ask()
  -> PatentExecutor.execute_with_progress()
```

### E. Request Contract 深挖

`server/schemas/request_models.py` 是 patent gateway-facing contract：

- 模型字段：`PatentAskRequest` 行 26-45 包含 `question/conversation_id/chat_history/requested_mode/actual_mode/route/source_scope/turn_mode/kb_enabled/allow_kb_verification/used_files/execution_files/selected_file_ids/primary_file_id/file_selection/trace_id/options`。
- mode 严格校验：行 203-204 要求 `requested_mode == actual_mode == "patent"`，否则 `ProtocolMismatchRequestError`。
- route/source_scope：行 15-20 定义 `kb_qa/pdf_qa/tabular_qa/hybrid_qa` 到 `kb/pdf/table/pdf+kb/table+kb/pdf+table/pdf+table+kb` 的映射；行 247-253 校验 turn_mode 和 source_scope。
- KB-only 约束：行 255-267 要求 `used_files/execution_files/selected_file_ids/file_selection` 为空，`primary_file_id` null，`allow_kb_verification` false。
- file route 约束：行 269-288 要求 execution/selected 非空，selected id 存在于 execution_files，primary 属于 selected，selected family 精确等于 source_scope 的 pdf/table token。
- KB 混合约束：行 289-297 要求 `kb_enabled` 和 `allow_kb_verification` 与 `source_scope` 是否含 kb 一致。
- patent-specific 存储约束不在这一层，而在 `server/patent/file_contract.py` 行 101-163：file_type 限定、`PATENT_ORIGINAL_MINIO_ONLY` 默认 true、`storage_ref` 必须是 `minio://`、excel/table 后缀限定。

共享 vs patent 专属：

- 可共享：route/source_scope/turn_mode/file-family/kb flags 的基础 contract；`requested_mode/actual_mode` 的 ModeName；Gateway/patent/fastQA 的 file selection 字段。
- patent 专属：`actual_mode=patent` 强约束、`PATENT_ORIGINAL_MINIO_ONLY`、MinIO original object policy、PDF/table/hybrid 合成规则。
- 与 fastQA 对照：`fastQA/app/services/request_adapter.py` 行 247-413 允许 `requested_mode` 为 fast/thinking/patent，但强制 `actual_mode == "fast"`；它会推断/规范 source_scope，而 patent 行 203-253 不做宽松推断。这不是继承关系，而是两个平行 contract。
- 与 gateway 对照：`gateway/app/services/route_decision.py` 行 67-72 规定 file/mixed 时 patent 请求保持 actual_mode patent，其他模式进 fast；gateway 是 payload 源头，patent 是最终强校验者。

### F. Patent Execution 深挖

| 文件 | 代码证据 | 专属业务 | 重复基础能力 | 测试覆盖信号 |
|---|---:|---|---|---|
| `executor.py` | 105-147 构造 KB/PDF/Table/Hybrid；196-373 file+KB merge | Patent source precedence、Graph KB、hybrid merge | `_call_with_supported_kwargs`、stream emitter glue | `test_patent_executor.py`、contract stream tests |
| `file_routes.py` | 193-405 cache/singleflight；408-598 dispatch；601-819 hybrid branches；1171-1530 synthesis | PDF/table/KB file precedence、回答结构、冲突说明 | cache/singleflight、runtime signature、structured stream replay | `test_patent_file_routes.py` 5960 行 |
| `kb_service.py` | 57-160 run；162-241 graph preflight；387-404 runtime detection | Patent staged runtime、Graph KB direct/RAG | runtime routing fallback pattern | `test_patent_kb_service.py`、Graph KB tests |
| `pdf_service.py` | 852-900 answer client；722-841 summary normalization | PDF compare/summary repair、literature sections | OpenAI-compatible client、model logging、httpx transport | extensive compare/summary tests |
| `tabular_service.py` | 27-32 planner/executor/loader imports；135-195 fastQA markdown structure | Table planner/executor and evidence rendering | OpenAI-compatible client, fallback output | `test_patent_tabular_service.py`、tabular executor tests |
| `hybrid_synthesis.py` | 110-158 synthesis contract；161-240 prompt | file-over-kb synthesis contract | OpenAI-compatible client/logging | `test_patent_hybrid_synthesis.py`、file route hybrid tests |
| `planning_hot_pool.py` | 40-105 config；174-240 lane bootstrap | Patent planning lane warm pool | generic hot lane pool/gate mechanics | `test_patent_planning_hot_pool.py` |
| `rerank_service.py` | 76-220 rerank request/fallback；223-249 builder | Patent stage2 env fallback | generic OpenAI-compatible rerank client | `test_patent_rerank_service.py` |
| `retrieval_service.py` | 1-260 diagnostics, scoring helpers; total 2922 lines | patent id extraction, catalog/evidence | vector/lexical retrieval framework, diagnostics toggles | `test_patent_retrieval_service.py` 3153 lines |
| `runtime.py` | 957-1077 default runtime bootstrap | patent archive/vector/original loader | staged runtime composition, embedding client, LLM planning client | `test_runtime_controls.py` |
| `upstream_http.py` | 85-182 shared httpx provider | none except name/env | shared http pool with metrics | `test_patent_upstream_transport.py` |
| `upstream_transport.py` | 16-43 timeout; 46-73 describe; 76-109 metrics | none except `_patent_shared_pool` attr | transport helper | `test_patent_upstream_transport.py` |

Agent-common candidates:

- `OpenAICompatibleClient` + `auth_headers` + model-call logging + thinking controls.
- `HttpClientPoolProvider` + `TransportMetrics`.
- `RerankClient`.
- `SingleFlightCacheCoordinator`.
- `StructuredContentStreamRouter`.
- `GatewayAskContract` with service-specific mode policy hooks.

Giant orchestrator risks:

- `file_routes.py` combines planner, cache, stream replay, branch execution, synthesis, validation and answer repair.
- `executor.py` now owns both route dispatch and hybrid file+KB merge.
- `pdf_service.py` and `tabular_service.py` combine file loading, prompt construction, LLM client, answer normalization and fallback.

### G. Router/API 完整表

| 路径 | 方法 | 文件 | 入参模型/解析 | service | 外部依赖 | 持久化/鉴权/quota/SSE | 测试覆盖 |
|---|---|---|---|---|---|---|---|
| `/api/ask` | POST | `routers/ask.py:358-375` | `parse_patent_request()` | `AskService.sync_ask` | optional Redis/authority/runtime/LLM | durable 才鉴权；兼容 alias；无 SSE | `test_patent_route_aliases_all_dispatch_to_patent_ask` |
| `/api/v1/ask` | POST | `routers/ask.py:358-375` | 同上 | 同上 | 同上 | 同上 | 同上 |
| `/api/patent/ask` | POST | `routers/ask.py:358-375` | 同上 | 同上 | 同上 | durable gate/file gate/readiness | sync contract tests |
| `/api/v1/patent/ask` | POST | `routers/ask.py:358-375` | 同上 | 同上 | 同上 | 同上 | sync contract tests |
| `/api/ask_stream` | POST | `routers/ask.py:378-391` | `parse_patent_request()` | `AskService.stream_ask` | optional Redis/authority/runtime/LLM | stream slot 429；SSE；兼容 alias | stream contract tests |
| `/api/v1/ask_stream` | POST | `routers/ask.py:378-391` | 同上 | 同上 | 同上 | 同上 | stream alias tests |
| `/api/patent/ask_stream` | POST | `routers/ask.py:378-391` | 同上 | 同上 | 同上 | gateway headers/options；SSE | file/kb/hybrid stream tests |
| `/api/v1/patent/ask_stream` | POST | `routers/ask.py:378-391` | 同上 | 同上 | 同上 | 同上 | stream contract tests |
| `/api/health` | GET | `routers/health.py:105-190` | query `durable/route/source_scope` | app.state component status | runtime/redis/authority/pools/graph status | durable probe 需 auth；无 quota/SSE | `test_health_contract.py` |
| `/api/v1/health` | GET | `routers/health.py:105-190` | 同上 | 同上 | 同上 | 同上 | `test_health_contract.py` |
| `/api/patent/original/{canonical_patent_id}` | GET/HEAD | `routers/original.py:129-147` | `parse_original_request()` | `OriginalViewService` but route disabled first | execution cache/object view if enabled | 无鉴权；当前固定 503 | `test_original_contract.py` |
| `/api/v1/patent/original/{canonical_patent_id}` | GET/HEAD | `routers/original.py:129-147` | 同上 | 同上 | 同上 | 当前固定 503 | `test_original_contract.py` |

### H. Legacy / Deprecated / Scaffold 引用验证

- README 明确漂移：`patent/README.md` 行 3-10 仍称 Phase 1 scaffold/minimal app factory，但实际 app factory 行 309-658 构建完整服务栈。
- pyproject 仍有 scaffold 描述：`patent/pyproject.toml` grep 命中 `"Phase 1 scaffold for the patent FastAPI service"`。
- original route 是兼容壳：`routers/original.py` 行 29-36 固定抛出 “use the gateway/public original route”，但行 129-147 仍注册 GET/HEAD 路径。
- fallback 是真实运行策略，不全是遗留：`app.py` 行 358-375 shared pool 失败降级 private clients；行 487-497 tabular answer client 失败降级；`rerank_service.py` 行 19-36/92-113/145-166 将 rerank 不可用映射 fallback 结果。
- legacy 保留行为有测试：`pdf_service.py` 行 722-841 有 legacy literature summary normalization；`test_patent_file_routes.py` 有 `legacy`、`placeholder`、`fallback` 大量用例，不能简单删除。

### I. 新增重构点

以下 `R-007` 至 `R-020` 均为第二轮深度补充，所属服务均为 `patent`。每个条目的 `当前状态` 以对应接口路径和调用链为准：ask/ask_stream/file route/runtime/LLM/retrieval/rerank 路径为 `active live path`；README/pyproject scaffold 描述为 `archive/doc drift`；original route 为 `deprecated but still registered`。本节所有条目均按第二轮模板补充代码位置、行号范围、接口路径、当前调用链、关键代码片段、目标结构、迁移步骤、兼容/回滚、测试计划、风险和阻塞项。

### R-007：引入 `PatentServiceContainer`，把 app.state 降为兼容桥

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P1
- 类型：composition root / testability / app.state sprawl
- 代码位置：`patent/server_fastapi/app.py:309-658`、`725-765`
- 接口路径：全部 `/api/patent/*`、`/api/v1/patent/*`、health、original
- 当前调用链：`create_app()` -> `_bootstrap_app_state()` -> `_bootstrap_service_state()` -> `app.state.ask_service` -> router
- 关键片段：

```python
execution_lock_manager = ExecutionLockManager(redis_client, key_factory=key_factory)
execution_cache = ExecutionCache(redis_client, key_factory)
chat_persistence_service = ChatPersistenceService(...)
...
ask_service = AskService(
    patent_executor=PatentExecutor(...),
    persistence_service=chat_persistence_service,
)
original_service = OriginalViewService(execution_cache=execution_cache)
app.state.execution_lock_manager = execution_lock_manager
app.state.execution_cache = execution_cache
app.state.ask_service = ask_service
```

- 目标结构：`PatentServiceContainer(settings, registry, resources, services, component_status)`；`app.state.container = container`，同时保留 legacy aliases。
- 迁移步骤：先创建只读 container dataclass；bootstrap 返回 container；app.state alias 从 container 同步；router 优先读 container、fallback app.state；tests 分批改 fixture。
- 兼容/回滚：保留全部现有 `app.state.*` 名称；失败时切回 `_bootstrap_service_state()` 直接写 app.state。
- 测试计划：unit container construction；contract create_app app.state aliases；router sync/stream；health status；integration TestClient lifespan；regression 对现有 app.state monkeypatch。
- 风险：tests 大量直接替换 `app.state.ask_service`，需要兼容桥。
- 阻塞项：需要列出所有 app.state consumer，第二轮 rg 已定位主要在 app/ask/health/original/tests。

### R-008：把关闭链替换为 LIFO `LifecycleManager`

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P1
- 类型：resource lifecycle / duplicate close alias
- 代码位置：`patent/server_fastapi/app.py:602-645`、`661-721`
- 接口路径：startup/shutdown 影响全部接口
- 当前调用链：bootstrap 创建资源 -> 异常分支逐个 close -> lifespan finally 逐个 close
- 关键片段：

```python
def _close_state_resource(container: object, attr_name: str) -> None:
    resource = getattr(container, attr_name, None)
    if resource is None:
        return
    setattr(container, attr_name, None)
    close = getattr(resource, "close", None)
    if callable(close):
        close()
...
_close_state_resource(app.state, "shared_llm_pool")
_close_state_resource(app.state, "patent_shared_upstream_provider")
```

- 目标结构：`LifecycleManager.register(name, resource, close=True, aliases=...)`，LIFO close，id 去重，记录 close errors 到 component_status/log。
- 迁移步骤：先包装现有 close 顺序；为 shared provider alias 去重；为 redis nested client 注册 owner；最后删除手写 duplicated close。
- 兼容/回滚：保留 `_close_state_resource()` 作为 fallback helper 一版。
- 测试计划：unit LIFO/order/idempotent；contract bootstrap failure cleanup；router unaffected；stream shutdown closes active resources；integration TestClient restart；regression `test_create_app_closes_*`。
- 风险：关闭顺序改变可能影响 shared httpx client 和 service-owned clients。
- 阻塞项：需确认每个 closeable ownership，尤其 shared provider 与 injected clients。

### R-009：抽 `PatentRequestAdapter`，隔离 header/options 注入与 contract parse

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P1
- 类型：router policy leakage / gateway contract
- 代码位置：`patent/server_fastapi/routers/ask.py:77-153`
- 接口路径：`/api/patent/ask`、`/api/patent/ask_stream` 及 aliases
- 当前调用链：router -> `_read_json_payload()` -> header 注入 -> `parse_patent_request()`
- 关键片段：

```python
gateway_task_execution = _is_truthy_header(request.headers.get("x-gateway-task-execution"))
gateway_owned_persistence = _is_truthy_header(request.headers.get("x-gateway-owned-persistence"))
normalized_options.pop("gateway_task_execution", None)
normalized_options.pop("gateway_owned_persistence", None)
if gateway_task_execution:
    normalized_options["gateway_task_execution"] = True
payload["options"] = inject_stream_capability_option(...)
return parse_patent_request(payload)
```

- 目标结构：`PatentRequestAdapter.from_fastapi_request(request).parse()`，输出 `(PatentAskRequest, RequestIngressMetadata)`。
- 迁移步骤：复制现逻辑到 adapter；router 调 adapter；将 header trust 规则加 unit tests；再移除 router helper。
- 兼容/回滚：body options 中 gateway-owned 字段继续被忽略，只有 header 可注入。
- 测试计划：unit header truthy/body stripping/stream capability；contract protocol mismatch；router sync/stream; stream gateway-owned; integration gateway task stream; regression existing header tests。
- 风险：headers 大小写由 Starlette 已规范，但字段命名需保留。
- 阻塞项：gateway 是否永远使用 `X-Gateway-*` 头需确认。

### R-010：抽 `PatentReadinessGate`，统一 ask 与 health 的 readiness 规则

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P1
- 类型：duplicated readiness policy
- 代码位置：`routers/ask.py:169-238`、`routers/health.py:17-119`
- 接口路径：ask/ask_stream/health
- 当前调用链：ask router 自己 copy components；health router 自己 copy components
- 关键片段：

```python
required_components = ["redis", "authority"]
if _request_requires_runtime(ask_request):
    required_components.append("runtime")
ready = all(bool(dict(components.get(name) or {}).get("ready", False)) for name in required_components)
...
if _route_requires_runtime(route=route, source_scope=source_scope):
    durable_required_components.append("runtime")
```

- 目标结构：`PatentReadinessGate.components_snapshot()`、`required_for(request_or_probe)`、`assert_ready()`
- 迁移步骤：先将 `_copy_components` 合并；把 route/source_scope runtime 规则作为纯函数；ask/health 共用；health 保持 response shape。
- 兼容/回滚：错误码、extra.components、503/200 判断保持一致。
- 测试计划：unit kb/file-only/hybrid runtime requirements；contract health durable probes；router ask durable not ready；stream not ready error frame；integration degraded graph unaffected；regression health tests。
- 风险：health 当前 plain request 在 runtime degraded 时 503，durable file-only probe 可跳过 runtime；需精确保留。
- 阻塞项：是否将 graph_kb 纳入 durable required 组件需要产品确认，目前不是 required。

### R-011：把 patent ask contract 与 file contract 合并为共享基础 + patent policy

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P1
- 类型：contract drift / duplicate validation
- 代码位置：`server/schemas/request_models.py:185-317`、`server/patent/file_contract.py:21-98`
- 接口路径：全部 ask/ask_stream file routes
- 当前调用链：router parse `PatentAskRequest` -> executor `_execute_file_route()` -> `build_patent_file_contract()`
- 关键片段：

```python
if selected_families != expected_families:
    raise ProtocolMismatchRequestError("selected_file_ids must match source_scope exactly")
...
if families != expected_families:
    raise ValueError("selected files must match source_scope exactly")
if bool(kb_enabled) != includes_kb:
    raise ValueError("kb_enabled must match source_scope")
```

- 目标结构：`agent_common.contracts.file_route` 提供 route/scope/family/kb 基础校验；`PatentFilePolicy` 加 `actual_mode=patent` 和 MinIO-only 规则。
- 迁移步骤：提取纯函数和 fixtures；patent/fastQA/gateway 先只引用常量；再切校验；最后去重错误映射。
- 兼容/回滚：patent 对外错误仍由 router 映射 `PROTOCOL_MISMATCH` / `INVALID_REQUEST`。
- 测试计划：unit shared matrix；contract patent parser; fastQA adapter; gateway route_decision; router bad payload; integration gateway->patent; regression selected ids/families。
- 风险：fastQA 当前宽松推断与 patent 严格校验不同，不能直接统一行为。
- 阻塞项：共享包位置和三服务依赖路径需确定。

### R-012：拆 `file_routes.py` 为 planner/cache/branch/synthesis 四层

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P1
- 类型：giant orchestrator / cache-stream coupling
- 代码位置：`server/patent/file_routes.py:193-405`、`408-598`、`601-819`、`1171-1530`
- 接口路径：`route=pdf_qa/tabular_qa/hybrid_qa`
- 当前调用链：`PatentExecutor._execute_file_route()` -> `dispatch_patent_file_route()` -> cache/singleflight -> pdf/table/hybrid services
- 关键片段：

```python
plan = plan_patent_file_route(contract)
cache_fingerprint = build_file_route_cache_fingerprint(...)
if plan.handler == "pdf":
    result = _run_cached_file_route(... service.execute ...)
if plan.handler == "tabular":
    result = _run_cached_file_route(... service.execute ...)
result = _run_cached_file_route(... compute=lambda: _build_hybrid_result(...))
```

- 目标结构：`file_qna/planner.py`、`cache.py`、`branches.py`、`hybrid_rules.py`、`orchestrator.py`。
- 迁移步骤：先 move pure planner and cache with public function compatibility; then branch runner classes; keep `dispatch_patent_file_route()` facade; finally split synthesis rules.
- 兼容/回滚：保持 `plan_patent_file_route` / `dispatch_patent_file_route` import path。
- 测试计划：unit planner/cache/singleflight; contract file ask/stream; router file gate; stream preview/final/cache replay; integration pdf/table/hybrid; regression all `test_patent_file_routes.py`。
- 风险：SSE event ordering and cache replay are fragile.
- 阻塞项：需要冻结 structured stream event contract。

### R-013：拆 `PatentExecutor` 的 file+KB merge 为 `HybridAnswerCoordinator`

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P2
- 类型：orchestrator responsibility overlap
- 代码位置：`server/patent/executor.py:196-373`、`375-545`
- 接口路径：`hybrid_qa` with `source_scope` containing `kb`
- 当前调用链：executor -> file route -> kb service -> `_merge_file_and_kb_results()`
- 关键片段：

```python
file_result = dispatch_patent_file_route(...)
if not contract.includes_kb:
    return file_result
kb_result = self._kb_service.run(...)
merged = self._merge_file_and_kb_results(
    file_result=file_result,
    kb_result=kb_result,
    source_scope=request.source_scope,
)
```

- 目标结构：`HybridAnswerCoordinator.merge(file_result, kb_result, contract, synthesis_service)`；executor 只负责 route dispatch。
- 迁移步骤：提取 `_merge_file_and_kb_results` 和 helper；注入 coordinator；保持 metadata shape；再移除 executor static helpers。
- 兼容/回滚：保留 static method 调 coordinator 一版。
- 测试计划：unit merge metadata/steps/references; contract sync/stream hybrid; router SSE final; stream preview-before-final; integration file+KB; regression conflict and fallback tests。
- 风险：metadata 合并顺序和 `synthesis_contract` public subset 可能被前端或 persistence 依赖。
- 阻塞项：需确认 metadata contract 是否已有外部消费者。

### R-014：抽 OpenAI-compatible client 基础层，保留 patent prompt/service

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P1
- 类型：LLM infrastructure duplication
- 代码位置：`pdf_service.py:852-900`、`tabular_service.py:1-45`、`hybrid_synthesis.py:243-260`、`runtime.py:159-260`
- 接口路径：所有 LLM-backed ask/file/kb paths
- 当前调用链：bootstrap shared http client -> service-specific AnswerClient -> `httpx.Client.post`
- 关键片段：

```python
self._owns_http_client = http_client is None
self._client = http_client or httpx.Client(timeout=self._timeout_seconds)
...
headers = auth_headers(self._api_key)
request_timeout = build_patent_request_timeout(...)
response = self._client.post(..., headers=headers, json=request_payload, timeout=request_timeout)
```

- 目标结构：`agent_common.llm.OpenAICompatibleChatClient` with service label, timeout provider, auth mode, model_call logging hooks。
- 迁移步骤：先 wrap current clients behind common transport; keep from_env names; migrate pdf/tabular/hybrid/planning one by one; compare logs.
- 兼容/回滚：service classes remain public; common client hidden behind composition.
- 测试计划：unit auth modes/timeouts/log payload; contract PDF/table/hybrid fallback; router stream; integration shared pool; regression model_call logging and thinking controls。
- 风险：env names and logging fields differ across services; shared abstraction can accidentally erase patent-specific telemetry.
- 阻塞项：fastQA/highThinkingQA client inventory required before shared package.

### R-015：把 planning hot pool/upstream gate 泛化为 shared hot lane runtime

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P2
- 类型：runtime infra duplication / pool ownership
- 代码位置：`planning_hot_pool.py:40-105`、`174-240`、`runtime.py:801-879`、`app.py:379-469`
- 接口路径：KB routes stage1/stage2
- 当前调用链：app bootstrap hot pool/gate -> PatentRuntime stage1/stage2 wraps planning client
- 关键片段：

```python
if self.planning_hot_pool is not None:
    proxy_client = getattr(self.planning_hot_pool, "proxy_client", None)
    if callable(proxy_client):
        planning_client = proxy_client(fallback_client=self.planning_client)
if self.planning_upstream_gate is not None:
    gate_proxy_client = getattr(self.planning_upstream_gate, "proxy_client", None)
```

- 目标结构：`agent_common.llm.HotLanePool` + `UpstreamGate` with lane builder callbacks and service labels。
- 迁移步骤：extract config/status snapshot first; preserve PatentPlanningHotPool facade; then migrate runtime proxy usage.
- 兼容/回滚：`PatentPlanningHotPool.from_settings/from_env` remains facade.
- 测试计划：unit pool lane fail/close/snapshot; contract health hot pool; stream stage latency; integration startup/shutdown; regression `test_patent_planning_hot_pool.py`。
- 风险：planning first-token latency and pool timeout metrics are performance-sensitive.
- 阻塞项：need compare with fastQA/highThinkingQA pool behavior.

### R-016：统一 rerank adapter，替换 requests-only patent rerank

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P2
- 类型：rerank infra duplication / inconsistent HTTP stack
- 代码位置：`server/patent/rerank_service.py:76-249`、`runtime.py:1071`
- 接口路径：KB retrieval stage2
- 当前调用链：`build_default_patent_runtime()` -> `build_patent_stage2_rerank_fn()` -> `run_stage2_targeted_retrieval(... rerank_fn=...)`
- 关键片段：

```python
endpoint = _normalize_rerank_endpoint(base_url)
headers = auth_headers(api_key, auth_mode=auth_mode) if api_key else {"Content-Type": "application/json"}
response = req.post(endpoint, headers=headers, json=payload, timeout=float(timeout_seconds))
...
return _fallback_result(... reason="request_failed", provider=RERANK_PROVIDER_NAME)
```

- 目标结构：`agent_common.rerank.OpenAICompatibleRerankClient` supporting httpx/requests injection, normalized fallback result, service telemetry.
- 迁移步骤：define shared result schema; port endpoint normalization; inject client into runtime; keep builder env precedence.
- 兼容/回滚：`build_patent_stage2_rerank_fn()` remains and returns callable.
- 测试计划：unit endpoint/auth/fallback; contract stage2 metadata; router KB answer; integration rerank disabled/enabled; regression `test_patent_rerank_service.py` and stage2 controls.
- 风险：fallback scores preserve current ordering; changing scores may change retrieval answers.
- 阻塞项：shared rerank schema compatibility with fastQA.

### R-017：拆 `PatentRuntime` bootstrap 中的 path registry、object loader、vector bootstrap

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P2
- 类型：runtime bootstrap complexity / path registry misuse
- 代码位置：`resource_registry.py:7-33`、`runtime.py:957-1077`
- 接口路径：KB routes and hybrid routes containing KB
- 当前调用链：app bootstrap -> `build_default_patent_runtime()` -> `PatentResourceRegistry.discover()` -> archive/vector/minio/retrieval
- 关键片段：

```python
registry = PatentResourceRegistry.discover()
if not registry.archive_available():
    return None
archive_loader = PatentArchiveLoader(registry.archive_root)
...
if strict_original_minio_only:
    original_minio_loader = PatentOriginalMinioLoader(reader=ObjectReader(), ...)
retrieval_service = PatentRetrievalService(...)
```

- 目标结构：`PatentRuntimeFactory` with `PathRegistry`, `VectorBootstrapper`, `OriginalLoaderFactory`, `RetrievalServiceFactory`。
- 迁移步骤：extract pure path discovery; extract vector bootstrap with close-on-fail; extract original loader policy; keep `build_default_patent_runtime()` facade.
- 兼容/回滚：return `None` when archive unavailable remains.
- 测试计划：unit registry path cases; contract runtime missing file-only still works; router KB runtime not ready; integration resource root; regression `test_runtime_controls.py`。
- 风险：RESOURCE_ROOT/resource layout behavior can regress.
- 阻塞项：current `PatentResourceRegistry.discover()` hardcodes repo root and `resource/patentQA`; shared config strategy needed.

### R-018：把 structured stream event schema 抽为共享 contract

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：active live path

- 严重程度：P1
- 类型：SSE protocol / frontend contract
- 代码位置：`ask.py:294-348`、`executor.py:215-256`、`file_routes.py:174-190`、`stream_events.py`
- 接口路径：`/api/patent/ask_stream`、aliases
- 当前调用链：router StreamingResponse -> AskService stream events -> executor/file_routes content emitters
- 关键片段：

```python
yield _to_sse_line({**dict(payload), "trace_id": trace_id_local})
...
final_stream_emitter = structured_router.final_emitter(
    content_source=final_content_source_for_route(request.route),
)
...
emit_snapshot = getattr(content_callback, "emit_snapshot", None)
```

- 目标结构：`agent_common.contracts.stream_event` for metadata/content/done/error and structured preview/final phases.
- 迁移步骤：freeze schema from tests; create dataclasses/validators; router uses serializer; file_routes uses emitters from shared package.
- 兼容/回滚：legacy untyped content remains when stream capability header absent.
- 测试计划：unit event validators; contract stream schema; router SSE error; stream cache replay; integration gateway progress parser; regression all structured stream tests.
- 风险：frontend/gateway may rely on frame order and optional fields.
- 阻塞项：need gateway stream parser inventory.

### R-019：收敛 original route：明确下线或迁到 gateway/public-service contract

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：deprecated but still registered

- 严重程度：P2
- 类型：legacy compatibility / dead route
- 代码位置：`routers/original.py:29-36`、`98-147`
- 接口路径：`/api/patent/original/{canonical_patent_id}`、`/api/v1/patent/original/{canonical_patent_id}`
- 当前调用链：router -> `_ensure_original_route_compatibility_enabled()` -> fixed 503 before service
- 关键片段：

```python
def _ensure_original_route_compatibility_enabled(request: Request) -> None:
    raise APIError(
        code=codes.SERVICE_NOT_READY,
        message="patent local original route is disabled; use the gateway/public original route",
        status_code=503,
    )
```

- 目标结构：either remove router registration after gateway cutover, or implement explicit compatibility feature flag with documented 503.
- 迁移步骤：confirm gateway/public route ownership; add deprecation metric; gate registration by settings; later remove aliases.
- 兼容/回滚：keep fixed 503 route while external callers exist.
- 测试计划：unit parse_original_request; contract disabled route 503; router GET/HEAD; integration gateway original route; regression existing original tests.
- 风险：unknown clients may still call local route.
- 阻塞项：gateway/public original route ownership not confirmed in this audit.

### R-020：更新 README/pyproject scaffold drift after refactor baseline is accepted

- 来源：第二轮深度补充
- 所属服务：patent
- 当前状态：archive/doc drift

- 严重程度：P3
- 类型：documentation drift / operational risk
- 代码位置：`patent/README.md:1-16`、`patent/pyproject.toml` grep scaffold hit
- 接口路径：N/A
- 当前调用链：developer/operator reads README -> underestimates service state
- 关键片段：

```text
Phase 1 currently bootstraps the standalone `patent` FastAPI service scaffold under `patent/` only.
Current scaffold includes:
- a minimal `server_fastapi.app:create_app` factory
```

- 目标结构：README describes active FastAPI service, routers, config gates, runtime dependencies, and test commands.
- 迁移步骤：after code refactor plan accepted, update README and package description; mark historical docs as archive.
- 兼容/回滚：docs-only; no runtime impact.
- 测试计划：docs lint if available; contract none; router none; stream none; integration none; regression ensure no config examples removed.
- 风险：low, but current doc drift can mislead rollout decisions.
- 阻塞项：本任务最高约束只允许修改审计文档，不能改 README/pyproject。

### J. 未能确认项

- 未运行测试，不能确认当前全量 test suite 是否通过。
- 未启动 gateway/public-service/patent，不能确认生产运行中 gateway 实际使用 `/api/ask_stream` 还是 `/api/patent/ask_stream`。
- 未读取完整 gateway backend registry 配置，不能确认 `/api/{actual_mode}/ask_stream` 对 patent 是否被重写为 `/api/patent/ask_stream`。
- 未确认外部客户端是否仍调用 original local route；代码显示该 route 注册但固定 503。
- 未确认 fastQA/highThinkingQA LLM/rerank/upstream 实现全量差异；这里只读对照了 fastQA request adapter 和 gateway route decision。

## 第三轮证据闭环补充

> Agent 5 只读审计补充。仅修改本文档；未修改 `patent/` 源码、配置、测试、脚本、README 或依赖文件；未创建新目录/新文档；未运行会写文件的测试命令。

### 1. 第二轮未确认项复核

#### patent path cutover 判定表

| 问题 | 第三轮判定 | 证据 | 处理建议 |
|---|---|---|---|
| gateway 当前是否仍调用 `/api/ask` | 源码 live path 已不调用；gateway app 未注册未带 mode 的 `/api/ask*` | `rg '"/api/ask"...' gateway/app` 无命中；`gateway/app/routers/qa.py:834-867` 只注册 `/api/{fast,thinking,patent}/ask*`；`gateway/app/routers/qa.py:684`、`:786` 拼 `f"/api/{route_decision.actual_mode}/ask*"` | 可把 patent 的 `/api/ask*` 标为 backward-compat alias，不再视为 gateway live dependency |
| gateway patent sync upstream path | 已切到 `/api/patent/ask` | `gateway/app/services/route_decision.py:67-72` 规定 patent file/mixed 保持 `actual_mode=patent`；`gateway/app/routers/qa.py:684-689` 使用 `actual_mode` 拼 path 并发给对应 backend；`gateway/tests/test_qa_proxy.py:2146-2158` 断言 patent URL 以 `/api/patent/ask` 结尾 | 保留 gateway tests 作为 cutover 护栏 |
| gateway patent stream upstream path | 已切到 `/api/patent/ask_stream`，含 task runner | `gateway/app/routers/qa.py:786-793`；`gateway/app/services/qa_tasks.py:1736-1773`；`gateway/app/services/qa_tasks.py:2148-2149` 均拼 `/api/{actual_mode}/ask_stream`；patent actual_mode 来自 route_decision | task runner 与直接 proxy 均已 cutover，后续删除旧 alias 前仍需检查外部非 gateway 客户端 |
| patent 本地 alias | 仍注册 `/api/ask`、`/api/v1/ask`、`/api/ask_stream`、`/api/v1/ask_stream` | `patent/server_fastapi/routers/ask.py:358-381`；`patent/tests/fastapi_contract/test_ask_contract.py:2607-2608` 明确把 alias 和 `/api/patent/*` 一起覆盖 | 暂不删除；作为 TASK-004 的受控下线项 |
| frontend 用户入口 | README 仍提到 `/api/v1/{mode}/ask_stream` fallback alias；src 本轮未找到 `/api/ask*` 活跃调用 | `frontend-vue/README.md:11`；`rg '/api/(v1/)?(fast|thinking|patent)/ask|/api/v1/ask|/api/ask_stream|/api/ask' frontend-vue/src` 无源码命中 | README fallback 需和 gateway 路由现实对齐，归入低风险 docs 修正 |

#### 第二轮未确认项状态

| 编号 | 未确认项 | 状态 | 证据闭环 |
|---|---|---|---|
| U-01 | gateway 当前是否仍调用 `/api/ask` | closed | gateway direct proxy 与 task runner 均拼 `/api/{actual_mode}/ask*`，patent actual_mode 时落 `/api/patent/ask*`；gateway app 无 `/api/ask*` 注册 |
| U-02 | public-service/gateway 是否已有 patent contract 副本 | partially closed | gateway 有通用 `AskRequest` 和 `RouteDecision` 字段集；public-service 有 authority mode/source_service contract；未发现 `PatentAskRequest`/`parse_patent_request` 副本 |
| U-03 | original route 是否永久由 gateway/public-service 承载 | closed for current code, product permanence open | gateway public proxy 注册 `/api/patent/original/{canonical_patent_id}` 并转 public；public-service documents api 实现；patent local route 固定 503 文案指向 gateway/public |
| U-04 | `PatentResourceRegistry` 是否只是路径 registry | closed | `patent/server/patent/resource_registry.py:7-33` 只有 repo/resource path discovery 与 availability 判断，无 register/close/owner 语义 |
| U-05 | app.state consumers 完整清单 | closed for code path | 见本节 app.state consumer 清单；生产 consumers 主要是 app bootstrap、ask/health/original routers，tests 大量 monkeypatch |
| U-06 | retrieval/pdf/file/tabular 重构优先级 | closed | 行数与测试覆盖显示 file_routes/cache/stream 是最高风险拆分点；retrieval/pdf/tabular 需分层渐进 |
| U-07 | LLM/upstream/planning/rerank 共享边界 | closed | fastQA 已有 `app.integrations.llm`；patent 本地重复 auth/timeout/logging/rerank；highThinkingQA 主要保留 thinking-mode 兼容对照 |
| U-08 | `request_models.py` 字段分层 | closed | 见 contract 分层表；gateway 通用路由字段与 patent 严格验收字段分离，file storage policy 在 `file_contract.py` |
| U-09 | README/pyproject scaffold 描述 | closed | `patent/README.md:3-9` 和 `patent/pyproject.toml:4` 仍称 scaffold/minimal；应作为低风险 docs 任务 |
| U-10 | file route planner/cache/branch runner tests 是否足够 | closed with caveat | `test_patent_file_routes.py` 5960 行，覆盖 planning/cache/singleflight/preview-final；拆分可开始，但必须先保留 facade/import path |

#### app.state consumer 清单

| app.state 属性 | 写入/owner | 生产消费者 | 测试消费者 | 重构含义 |
|---|---|---|---|---|
| `settings` | `patent/server_fastapi/app.py:736-737` | ask gates `ask.py:186,201`；health `health.py:115`；app bootstrap `app.py:270,310,541` | health/ask fixtures 间接依赖 | container 首批字段，保留 alias |
| `service_name` | `app.py:736` | health response `health.py:176` | `test_health_contract.py` | 保留稳定 health contract |
| `component_status` | `app.py:739-753` 和 bootstrap 多处更新 | ask readiness `ask.py:169-180,213-230`；health `_copy_components()` `health.py:17-75` | health/ask tests 直接 mutate | 抽 `PatentReadinessGate` 前必须兼容 dict mutation |
| `runtime_dispatcher` | `app.py:738` | ask runtime snapshot/stream slot/thread limiter `ask.py:172,249,265`；health runtime snapshot `health.py:20-27` | runtime_controls、ask/health tests | container 中可作为 runtime control，不是 closeable |
| `ask_service` | `app.py:657` | ask router `_get_ask_service()` `ask.py:156-166` | 多数 ask contract tests 直接替换 | app.state bridge 必须保留到 tests 改完 |
| `original_service` | `app.py:658` | original router `_get_original_service()` `original.py:16-26`，但当前 fixed 503 先触发 | original tests | 若下线 local original route，可同步移除 consumer |
| `authority_client` | `app.py:275` / `646-648` | ChatPersistenceService owner 注入，shutdown close `app.py:684,708` | health lifecycle tests | LifecycleManager 需声明 owner/close |
| `redis_bindings` / `redis_key_factory` | `bootstrap_redis_state(app.state)`；`app.py:311,680` | execution cache/lock/persistence bootstrap；shutdown nested close `app.py:694-720` | health lifecycle tests | nested resource owner 需纳入 lifecycle |
| `execution_cache` | `app.py:647` | injected into runtime/file routes/original service | ask/runtime tests 直接断言 | container 字段；非 closeable wrapper |
| `execution_lock_manager` | `app.py:646` | ChatPersistenceService | 未见直接生产读取 | container 字段 |
| `chat_persistence_service` | `app.py:648` | AskService | 未见直接 router 读取 | container 字段 |
| `shared_llm_pool` / `patent_shared_upstream_provider` | `app.py:649-650` | health dynamic snapshot `health.py:28-45`；shutdown close | ask/health tests 断言同一对象 | 生命周期去重是 TASK-002 必做点 |
| `planning_hot_pool` | `app.py:651` | health `health.py:46-63`；shutdown close | health tests | 可后续抽 shared hot lane facade |
| `planning_upstream_gate` | `app.py:469,756` | health `health.py:64-74`；shutdown close | health tests | closeable/metrics 需确认 |
| `patent_pdf_service` | `app.py:652` | PatentExecutor 内部，app.state 仅 lifecycle/test bridge | ask/health tests | 拆 file routes 前保留 alias |
| `patent_tabular_service` | `app.py:653` | PatentExecutor 内部，app.state 仅 lifecycle/test bridge | ask/health tests | 同上 |
| `patent_hybrid_synthesis_client` | `app.py:654` | PatentExecutor 内部，app.state 仅 lifecycle/test bridge | health lifecycle tests | 同上 |
| `patent_runtime` | `app.py:655` | PatentExecutor/KB service 内部，app.state 仅 lifecycle/test bridge | ask/runtime tests | runtime factory 可先 facade |
| `patent_graph_kb_client` | `app.py:656` | injected into KB service；health status from component_status | health tests | graph readiness 不应自动纳入 durable required |
| `_rebootstrap_on_startup` | `app.py:704-721,758` | lifespan rebootstrap guard | tests via lifespan behavior | container 引入后仍保留 internal flag |
| `original_route_compatibility_enabled` | `app.py:757` | tests set，但 router 当前不读取 | original tests | dead flag；应和 original route 下线一起处理 |

#### contract 分层表

| 字段/能力 | gateway 通用 contract | patent 专属 contract | public-service/gateway 副本状态 | 证据 |
|---|---|---|---|---|
| `question/conversation_id/user_id/chat_history/options` | gateway `AskRequest` 入口字段 | patent parser 再验 `question/trace_id/conversation_id/options` | gateway 有 Pydantic model；public-service authority 使用 conversation/user/trace | `gateway/app/models/ask.py:26-34`；`patent/server/schemas/request_models.py:185-237` |
| `requested_mode/actual_mode` | `ModeName = fast/thinking/patent`，gateway route_decision 决定 actual | `requested_mode == actual_mode == patent` 严格协议 | public-service authority 有 source_service policy，允许 patentQA 只写 patent/patent | `gateway/app/models/routing.py:8,37-40`；`patent/server/schemas/request_models.py:203-204`；`public-service/.../internal_api.py:36-48` |
| `route/turn_mode/source_scope` | gateway `RouteDecision` 生成并转发 | patent parser 校验 route/scope/turn_mode 矩阵 | 未发现 public-service ask/file-route 副本；gateway 有同名类型与决策逻辑 | `gateway/app/models/routing.py:9-11`；`gateway/app/services/route_decision.py:67-114`；`patent/server/schemas/request_models.py:247-253` |
| `kb_enabled/allow_kb_verification` | gateway 从 source_scope 推导 | patent 严格要求与 `source_scope` 是否含 kb 一致 | public-service authority 不校验 file-route kb 语义 | `gateway/app/routers/qa.py:57-82`；`patent/server/schemas/request_models.py:289-297` |
| `used_files/execution_files/selected_file_ids/primary_file_id/file_selection` | gateway file_context 与 RouteDecision 产物 | patent parser 校验 ids/family/scope；file_contract 再校验 storage/table payload | public-service authority 仅持久化 selected hints/final_event used_files | `gateway/app/models/routing.py:30-57`；`patent/server/schemas/request_models.py:255-288`；`public-service/.../authority_schemas.py:24-32,75-86` |
| storage policy | gateway 只转发 execution file payload | patent `file_contract.py` 要求 MinIO storage_ref、table 后缀 | public-service original store 使用 `QA_ORIGINAL_MINIO_ONLY` 管原文对象，不是 ask file contract | `patent/server/patent/file_contract.py:101-163`；`public-service/.../patent_original_store.py:81-180` |
| original view | gateway public proxy contract | patent 只生成 viewer_uri/local route fixed 503 | public-service documents module 是当前实现 owner | `gateway/app/routers/public_proxy.py:40-53,253`；`public-service/.../documents/api.py:136-160`；`patent/server_fastapi/routers/original.py:29-36` |

### V-001 验证项：gateway patent path cutover
- 状态：通过代码证据闭环。
- 范围：gateway direct sync/stream proxy 与 async task runner。
- 命令/证据：`rg "/api/ask|/api/patent/ask|BackendRegistry|patent" gateway patent/tests`；`gateway/app/routers/qa.py:684-689,786-793,846-867`；`gateway/app/services/qa_tasks.py:1736-1773,2148-2149`；`gateway/app/services/backend_registry.py:19-32`。
- 结论：gateway live path 对 patent 已是 `/api/patent/ask` 和 `/api/patent/ask_stream`；未带 mode 的 `/api/ask*` 不是 gateway app 注册入口。
- 剩余风险：patent 自身兼容 alias 仍可能被外部客户端直连。
- 下一步：TASK-004 先加访问观测/废弃说明，再考虑移除 alias。

### V-002 验证项：public-service/gateway patent contract 副本 diff
- 状态：部分闭环。
- 范围：gateway request/routing model、public-service authority/original、patent ask/file contract。
- 命令/证据：`rg "PatentAskRequest|requested_mode|actual_mode|source_scope|turn_mode|execution_files|selected_file_ids|primary_file_id" patent/server patent/tests gateway fastQA public-service`；`rg "PatentAskRequest|ProtocolMismatchRequestError|parse_patent_request..." gateway public-service patent/server`。
- 结论：gateway 有通用 route/file-selection contract 副本；public-service 有 authority mode/source_service contract 与 original view contract；未发现 public-service/gateway 复制 `PatentAskRequest` 或 `parse_patent_request` 严格校验器。
- 剩余风险：gateway `RouteDecisionService` 与 patent parser 的 route/source_scope/family 规则仍可能漂移。
- 下一步：TASK-001 抽共享常量/矩阵/validator tests，不直接搬 patent 全量 parser。

### V-003 验证项：original route owner
- 状态：当前代码闭环，长期产品决策未闭环。
- 范围：gateway public proxy、public-service documents api、patent local original route、frontend viewer_uri。
- 命令/证据：`rg "original|OriginalViewService|use the gateway/public original route" patent gateway public-service frontend-vue`；`gateway/app/routers/public_proxy.py:40-53,253`；`public-service/backend/app/modules/documents/api.py:136-160`；`public-service/backend/app/modules/documents/service.py:398-520`；`patent/server_fastapi/routers/original.py:29-36,129-147`。
- 结论：当前 live owner 是 gateway -> public-service；patent local route 仍注册但固定返回 503 并提示使用 gateway/public。
- 剩余风险：无法仅凭代码证明“永久”承载策略；只能证明当前实现与测试 owner。
- 下一步：TASK-004 在重构前明确 local original route 保留 503、加 deprecation metric，或在发布计划中移除注册。

### V-004 验证项：PatentResourceRegistry 语义
- 状态：闭环。
- 范围：`PatentResourceRegistry` 与 app lifecycle close chain。
- 命令/证据：`patent/server/patent/resource_registry.py:7-33`；`patent/server_fastapi/app.py:680-721`。
- 结论：`PatentResourceRegistry` 只是路径 registry，不是 lifecycle/resource owner registry；需要另起 `LifecycleManager` 或 `PatentLifecycleManager`，不能复用该类名语义。
- 剩余风险：新 lifecycle 抽象若改关闭顺序会影响 shared pool 与 service-owned clients。
- 下一步：TASK-002 先 id 去重包装现有 close 顺序。

### V-005 验证项：app.state consumers
- 状态：闭环。
- 范围：`patent/server_fastapi`、`patent/server`、`patent/tests`。
- 命令/证据：`rg -o "(request\\.app\\.state|app\\.state)\\...." patent/server_fastapi patent/server patent/tests | sort -u`；`rg "getattr\\(request\\.app\\.state|..." patent/server_fastapi/routers patent/server_fastapi/app.py patent/server`。
- 结论：生产 consumer 集中在 app bootstrap、ask/health/original routers；tests 直接 mutate `ask_service/component_status/runtime_dispatcher/execution_cache/*service` 等，因此 container 化必须提供 app.state bridge。
- 剩余风险：tests monkeypatch 内部 executor services，不能一次性隐藏所有 app.state alias。
- 下一步：TASK-002/TASK-003 将 `app.state.container` 作为新增入口，保留旧 alias。

### V-006 验证项：file route tests 是否足以开始拆分
- 状态：通过，带条件。
- 范围：planner/cache/singleflight/branch runners/structured stream。
- 命令/证据：`wc -l` 显示 `test_patent_file_routes.py` 5960 行、`fastapi_contract/test_ask_contract.py` 5196 行；`rg "plan_patent_file_route|cache_fingerprint|singleflight|structured_stream_router" patent/tests/test_patent_file_routes.py` 命中 planning/cache/singleflight/preview-final 用例。
- 结论：足以先拆 planner/cache/branch runner，但必须保持 `plan_patent_file_route()`、`dispatch_patent_file_route()` facade 和 stream event 顺序。
- 剩余风险：测试覆盖多但耦合当前 import path 与 metadata shape。
- 下一步：TASK-005 先 move pure code + facade，不改行为。

### V-007 验证项：LLM/upstream/rerank 共享边界
- 状态：闭环。
- 范围：patent、fastQA、highThinkingQA。
- 命令/证据：`rg "OpenAI|upstream|planning_hot_pool|rerank|auth|timeout|stream" patent/server patent/tests fastQA/app highThinkingQA`；`fastQA/app/integrations/llm/openai_compat.py:85-180`；`fastQA/app/modules/generation_pipeline/rerank_service.py:72-226`；`patent/server/patent/rerank_service.py:76-249`；`patent/server/patent/pdf_service.py`、`hybrid_synthesis.py`、`runtime.py` 均本地调用 `auth_headers/model_call_logging/httpx`。
- 结论：共享边界应抽 auth_headers、OpenAI-compatible endpoint/timeout/httpx pool、model_call logging、rerank adapter、hot lane/gate；patent prompt、stage metadata、file synthesis、retrieval scoring留本地。
- 剩余风险：fastQA 日志字段服务名、patent env fallback、highThinkingQA thinking flags 不一致。
- 下一步：TASK-006 先做 inventory/test matrix，再抽 facade。

### V-008 验证项：scaffold 文档漂移
- 状态：闭环。
- 范围：README/pyproject。
- 命令/证据：`rg "Phase 1 scaffold|minimal|description =|scaffold" patent/README.md patent/pyproject.toml`；`patent/README.md:3-9`；`patent/pyproject.toml:4`。
- 结论：README/pyproject scaffold 描述已明显漂移，适合作为低风险文档修正任务，但本轮按约束不改。
- 剩余风险：误导开发者低估服务复杂度。
- 下一步：TASK-007 文档修正独立提交。

### 2. dead-code / legacy 引用闭环

| 项 | 当前状态 | 证据 | 第三轮判定 |
|---|---|---|---|
| patent `/api/ask*` aliases | legacy/compat，仍 active registered | `patent/server_fastapi/routers/ask.py:358-381`；`patent/tests/fastapi_contract/test_ask_contract.py:2607-2608` | 不是 gateway live path；删除需先确认外部直连 |
| gateway `/api/ask*` aliases | gateway app 源码未注册 | `rg '"/api/ask"...' gateway/app` 无命中；`gateway/tests/test_qa_proxy.py:2409` 有 removed alias 测试名 | 可视为 gateway 已 cutover |
| patent local original route | deprecated but registered fixed 503 | `original.py:29-36,129-147`；`test_original_contract.py:129,147,221` 检查文案 | 保留作为兼容错误，或 TASK-004 下线 |
| `original_route_compatibility_enabled` | dead/test-only flag | app 写 `False`；original router 不读取；tests 设置 | 可纳入 original cleanup |
| README/pyproject scaffold | doc drift | README/pyproject rg 命中 | 低风险 docs task |
| fallback/legacy normalization | live behavior，不是 dead code | file/pdf/rerank tests 大量 fallback/legacy 用例；`test_patent_file_routes.py` 覆盖 repair/fallback/cache not seed | 禁止泛删；只在拆分时迁移测试 |

### 3. live path 调用链闭环

```text
frontend/gateway mode route
  -> gateway/app/routers/qa.py:/api/patent/ask or /api/patent/ask_stream
  -> RouteDecisionService decides requested_mode=patent, actual_mode=patent
  -> ProxyService forwards to patent backend path /api/patent/ask*
  -> patent/server_fastapi/routers/ask.py parses headers/options and parse_patent_request()
  -> file gate + durable gate + readiness gate + auth resolve
  -> AskService.sync_ask / stream_ask
  -> PatentExecutor.execute / execute_with_progress
  -> kb_qa: PatentKbService + PatentRuntime + PatentRetrievalService + stage planning/retrieval/synthesis
  -> pdf_qa/tabular_qa/hybrid_qa: build_patent_file_contract() + dispatch_patent_file_route()
  -> optional file+KB merge + structured stream events + persistence/authority terminal write
```

Task runner stream path is also live:

```text
QATaskService worker
  -> target = backend_registry.get(actual_mode)
  -> path = /api/{actual_mode}/ask_stream
  -> internal request headers x-gateway-task-execution + x-gateway-owned-persistence
  -> patent ask router injects options from trusted headers
```

关键代码：

- gateway direct sync：`gateway/app/routers/qa.py:621-690`
- gateway direct stream：`gateway/app/routers/qa.py:747-793`
- gateway task stream：`gateway/app/services/qa_tasks.py:1736-1773`、`:2128-2155`
- patent ask router：`patent/server_fastapi/routers/ask.py:99-153`、`:358-391`
- patent contract：`patent/server/schemas/request_models.py:185-317`
- file contract：`patent/server/patent/file_contract.py:21-98`
- file route facade：`patent/server/patent/file_routes.py:408-450`

### 4. 测试护栏闭环

| 护栏 | 覆盖文件/证据 | 已覆盖能力 | 拆分前要求 |
|---|---|---|---|
| gateway patent path/quota | `gateway/tests/test_qa_proxy.py` 多处断言 `/api/patent/ask*`，如 `:2146-2158`、`:2535-2569` | path cutover、quota precheck/finalize、file route gate、stream capability header | 抽 contract 后必须继续跑 gateway proxy tests |
| patent ask contract | `patent/tests/fastapi_contract/test_ask_contract.py` 5196 行 | alias、protocol mismatch、sync/stream、durable/gateway-owned persistence、file routes、structured content | 改 parser/router 前冻结错误码和 SSE frame |
| patent health/lifecycle | `patent/tests/fastapi_contract/test_health_contract.py` 1563 行 | component_status、shared pool、planning gate、bootstrap failure close、lifespan close | TASK-002 必须先新增 close-order/idempotency tests |
| original route | `patent/tests/fastapi_contract/test_original_contract.py`、`public-service/backend/tests/test_patent_original_view_module.py`、`gateway/tests/test_public_proxy.py` | patent fixed 503、public-service original rendering/streaming、gateway proxy streaming | TASK-004 需同时改三侧 tests 或保留兼容 |
| file route planner/cache/stream | `patent/tests/test_patent_file_routes.py` 5960 行；`test_patent_stream_events.py` | planner matrix、handler dispatch、PDF/table/hybrid branches、cache fingerprint、singleflight、preview-before-final | TASK-005 可开始，但保持 facade/import path |
| retrieval | `patent/tests/test_patent_retrieval_service.py` 3153 行 | retrieval identity/catalog/vector/archive/cache/routing | TASK-008 拆前需先分出纯 helper tests |
| pdf/tabular services | `test_patent_file_routes.py` + `test_patent_pdf_contract.py` + `test_patent_tabular_service.py` | PDF compare/summary repair、table rendering/answer client、MinIO file payload | TASK-008 不应和 file route facade 同批 |
| LLM/rerank/upstream | `test_patent_rerank_service.py`、`test_patent_upstream_transport.py`、`test_patent_planning_hot_pool.py`、fastQA `test_rerank_service.py` | auth mode、endpoint normalization、fallback scores、pool/gate metrics | TASK-006 先共享 test matrix，再抽实现 |

本轮未运行 `pytest --collect-only patent/tests`。原因：仓库已有 `.pytest_cache/` 和 `patent/.tmp/pytest-*` 历史产物，pytest collect-only 仍可能刷新 cache 或创建临时/bytecode 文件；用户明确要求只读且“如担心写缓存则不要运行并记录原因”。

### 5. 可实施重构任务拆分

### TASK-001：抽 gateway/patent route-source contract matrix
- 类型：contract/shared validator。
- 优先级：P1。
- 目标文件：新增共享 contract 包或先在 gateway/patent 双侧引用同一 matrix；涉及 `gateway/app/models/routing.py`、`gateway/app/services/route_decision.py`、`patent/server/schemas/request_models.py`。
- 前置验证：V-001、V-002。
- 实施范围：只抽 route/source_scope/turn_mode/file-family/kb flag 纯常量和 matrix tests；保留 patent `actual_mode=patent` 严格策略在本地。
- 非目标：不迁移 MinIO storage policy，不改变错误码文案。
- 验收：gateway route_decision tests、patent parser protocol mismatch tests、public-service authority tests 全部通过。
- 风险/回滚：共享包导入路径影响三服务；可先复制测试 matrix，再切实现。

### TASK-002：引入 LifecycleManager 并包装现有 close 链
- 类型：lifecycle/resource owner。
- 优先级：P1。
- 目标文件：`patent/server_fastapi/app.py` 周边或新 lifecycle 模块；后续可抽 agent_common。
- 前置验证：V-004、V-005。
- 实施范围：注册 closeable、按现有顺序或声明顺序关闭、id 去重 `shared_llm_pool`/`patent_shared_upstream_provider`、记录 close error。
- 非目标：不改变 bootstrap 资源创建顺序，不移除 app.state alias。
- 验收：bootstrap failure cleanup、lifespan shutdown、duplicate close idempotent、redis nested close tests。
- 风险/回滚：关闭顺序变化导致 client ownership 回归；保留 `_close_state_resource()` fallback 一版。

### TASK-003：建立 PatentServiceContainer + app.state bridge
- 类型：composition root/testability。
- 优先级：P1。
- 目标文件：`patent/server_fastapi/app.py`、新 container/bootstrap 模块。
- 前置验证：V-005。
- 实施范围：bootstrap 返回 typed container，`app.state.container = container`，同步现有 aliases；routers 可先继续读 alias。
- 非目标：不拆 file_routes/pdf/retrieval，不改 public API。
- 验收：现有 ask/health/runtime_controls tests 不改或少量 fixture 通过；新增 container alias test。
- 风险/回滚：tests 直接 mutate `app.state.ask_service` 和 executor internals；bridge 必须双向兼容至少一轮。

### TASK-004：收敛 legacy path/original route
- 类型：compat/deprecation。
- 优先级：P2。
- 目标文件：`patent/server_fastapi/routers/ask.py`、`routers/original.py`、gateway public proxy docs/tests。
- 前置验证：V-001、V-003、V-008。
- 实施范围：明确 `/api/ask*` 和 local original route 的 deprecation policy；可先加 metrics/logging/docs，再下线。
- 非目标：不删除 public-service original implementation。
- 验收：gateway path tests 保持 `/api/patent/ask*`；patent alias tests 根据策略调整；public original route tests 保持。
- 风险/回滚：外部直连 patent alias/local original 的客户端不可见；需发布窗口或访问日志。

### TASK-005：拆 file_routes planner/cache/branch runner，保留 facade
- 类型：domain orchestrator split。
- 优先级：P1。
- 目标文件：`patent/server/patent/file_routes.py` -> `file_qna/planner.py`、`cache.py`、`branches.py`、`orchestrator.py`。
- 前置验证：V-006。
- 实施范围：先 move `PatentFileRoutePlan`、`plan_patent_file_route()`、cache fingerprint/singleflight coordinator；`file_routes.py` re-export facade。
- 非目标：不改 PDF/tabular service internals，不改 stream schema。
- 验收：`test_patent_file_routes.py`、`fastapi_contract/test_ask_contract.py` file-route subset、`test_patent_stream_events.py`。
- 风险/回滚：cache replay metadata 和 preview/final order fragile；任何 import path 变化都需 facade。

### TASK-006：LLM/upstream/rerank shared boundary inventory and facade
- 类型：shared infra extraction。
- 优先级：P2。
- 目标文件：patent `upstream_http.py`、`upstream_transport.py`、`planning_hot_pool.py`、`upstream_gate.py`、`rerank_service.py`、`pdf_service.py`、`hybrid_synthesis.py`、`runtime.py`；fastQA `app/integrations/llm/*`。
- 前置验证：V-007。
- 实施范围：先列 env/auth/logging/timeout matrix；抽 endpoint/auth/timeout/model_call logging/rerank fallback facade；patent 保留 prompt/stage/service labels。
- 非目标：不一次性替换 highThinkingQA agent runtime。
- 验收：patent rerank/upstream/planning hot pool tests + fastQA rerank/openai_compat tests。
- 风险/回滚：服务名日志字段、env fallback、thinking controls 差异；先 facade 后替换。

### TASK-007：修正 patent README/pyproject scaffold drift
- 类型：docs-only。
- 优先级：P3。
- 目标文件：`patent/README.md`、`patent/pyproject.toml`。
- 前置验证：V-008。
- 实施范围：描述真实 FastAPI service、routes、runtime dependencies、test commands；更新 package description。
- 非目标：不改代码/依赖。
- 验收：文档审查；无需 runtime tests。
- 风险/回滚：低；但本轮禁止修改 patent 文档，需另开任务。

### TASK-008：retrieval/pdf/tabular service 分层拆分
- 类型：large module split。
- 优先级：P2/P3。
- 目标文件：`retrieval_service.py`、`pdf_service.py`、`tabular_service.py`。
- 前置验证：V-006、V-007。
- 实施范围：retrieval 先拆 scoring/diagnostics/catalog helpers；pdf 先拆 answer client/normalization/compare repair；tabular 先拆 prompt/render/answer client。
- 非目标：不和 TASK-005 同批，不改 gateway contract。
- 验收：`test_patent_retrieval_service.py`、`test_patent_pdf_contract.py`、`test_patent_tabular_service.py`、file route integration subset。
- 风险/回滚：模块巨大且业务修复规则多；每次只移动纯函数或 facade-backed class。

### 6. 不可立即处理项与阻塞原因

| 项 | 阻塞原因 | 当前安全处理 |
|---|---|---|
| 删除 patent `/api/ask*` alias | 无生产访问日志/外部客户端清单；patent tests 仍覆盖 alias | 标记 deprecated compat；先观测再删 |
| 证明 original route “永久”由 public-service 承载 | 代码只能证明当前 owner，不能证明产品长期决策 | 当前按 gateway/public owner 重构；local patent route 保留 503 |
| 立即替换 `PatentResourceRegistry` | 它不是 lifecycle registry；直接改名会混淆 path discovery 与 close ownership | 新增 LifecycleManager，保留 PathRegistry |
| 一次性抽全服务 LLM common | fastQA/patent/highThinkingQA env、日志、thinking 控制不一致 | 先 facade + matrix tests |
| 运行 pytest collect-only | 可能写 `.pytest_cache`/bytecode/临时文件，违反只读风险边界 | 本轮不运行，记录原因 |
| 修改 README/pyproject | 用户约束只允许修改审计文档 | 作为 TASK-007 |

### 7. 最终进入重构前检查清单

- [ ] 以 V-001 为准确认 gateway direct 与 task runner 均继续走 `/api/patent/ask*`。
- [ ] 以 V-002 为准先共享 route/source_scope/turn_mode/file-family matrix，不把 patent storage policy 错抽到 gateway/public-service。
- [ ] 以 V-003 为准确认 original route 策略：public-service owner、patent local fixed 503 或下线计划。
- [ ] 以 V-004 为准新建 LifecycleManager，禁止复用 `PatentResourceRegistry` 表示 closeable resources。
- [ ] 以 V-005 为准落地 app.state bridge；改 routers 前保留 tests 可 monkeypatch 的 alias。
- [ ] 以 V-006 为准先拆 `file_routes.py` facade，锁定 stream preview/final/cache replay 行为。
- [ ] 以 V-007 为准先做 LLM/upstream/rerank env/auth/log matrix，再抽共享实现。
- [ ] 以 V-008 为准把 README/pyproject 作为低风险 docs-only 后续任务。
- [ ] 每个 TASK 必须先跑对应最小测试集，再扩大到 gateway/public-service/patent contract tests。
- [ ] 删除 legacy route 或改变 contract 前，必须先更新 gateway/frontend/public-service 侧引用与测试。
