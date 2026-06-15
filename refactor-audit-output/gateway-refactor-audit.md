# gateway 重构审计文档

> 状态：已完成第一轮只读审计。本文档只记录基于代码阅读得到的证据，审计产物位于独立目录，不修改业务代码。

## 1. 审计范围

- 已阅读目录：`gateway/app/`、`gateway/tests/`、`gateway/docs/` 中与路由、任务、admission、public proxy、SSE、quota、conversation persistence 相关文件。
- 已阅读关键文件：`app/main.py`、`app/routers/qa.py`、`app/routers/tasks.py`、`app/routers/public_proxy.py`、`app/routers/admission.py`、`app/routers/health.py`、`app/services/file_context_resolver.py`、`app/services/route_decision.py`、`app/services/qa_tasks.py`、`app/services/execution_admission.py`、`app/services/execution_queue_status.py`、`app/services/execution_slot_leases.py`、`app/services/execution_event_relay.py`、`app/services/conversation_persistence.py`、`app/services/conversation_files.py`、`app/services/quota_proxy.py`、`app/services/sse_frames.py`、`app/core/config.py`、`app/core/auth.py`。
- 未覆盖或需要本地进一步验证的范围：未运行测试；`conversation_persistence.extract_stream()` 的 live caller 需要进一步调用图确认；admission worker 在真实 Redis 多进程环境下的行为需要集成验证。

## 2. 当前 live path

### 2.1 服务入口

- app factory / main entry：`gateway/app/main.py:create_app()`，同步构造 `FastAPI`，没有 `lifespan=`、`startup`、`shutdown` hook。
- router 注册位置：`gateway/app/main.py:91-95` 注册 `health_router`、`admission_router`、`tasks_router`、`public_proxy_router`、`qa_router`。
- runtime 状态：入口直接在 `app.state` 挂载 Redis runtime、queue/relay/slot lease、distributed lock、backend registry、file context resolver、route decision service、proxy、active task registry、auth、quota、conversation persistence。

关键证据：

```python
app.state.execution_queue_status_store = ExecutionQueueStatusStore(redis_service=redis_runtime.service)
app.state.execution_event_relay_store = ExecutionEventRelayStore(redis_service=redis_runtime.service)
app.state.execution_slot_lease_store = ExecutionSlotLeaseStore(redis_service=redis_runtime.service)
app.state.distributed_lock_manager = DistributedLockManager(redis_service=redis_runtime.service)
app.state.backend_registry = BackendRegistry(settings)
app.state.conversation_file_service = ConversationFileService(
    provider=build_conversation_file_provider(settings),
)
app.state.file_context_resolver = FileContextResolver(...)
app.state.route_decision_service = RouteDecisionService()
app.state.proxy_service = ProxyService(settings)
app.state.active_task_streams = {}
app.state.gateway_auth_service = GatewayAuthService(settings)
app.state.quota_proxy_service = QuotaProxyService(settings)
app.state.conversation_persistence_service = ConversationPersistenceService(settings)
```

### 2.2 对外接口路径

| 接口路径 | 方法 | 所在文件 | 当前职责 | 是否 active |
|---|---|---|---|---|
| `/healthz` | GET | `gateway/app/routers/health.py` | 后端健康、component/admission/Redis 状态 | active live path |
| `/api/admission/status` | GET | `gateway/app/routers/admission.py` | admission 控制面状态 | active live path/control API |
| `/api/admission/requests/{request_id}` | GET | `gateway/app/routers/admission.py` | 查看 admission request/result/relay | active live path/control API |
| `/api/admission/requests/{request_id}/cancel` | POST | `gateway/app/routers/admission.py` | 控制面取消 request | active live path/control API |
| `/api/admission/requests/{request_id}/frames` | GET | `gateway/app/routers/admission.py` | relay frames 查询 | active live path/control API |
| `/api/fast/ask` | POST | `gateway/app/routers/qa.py` | fast 同步 QA proxy | active live path |
| `/api/v1/fast/ask` | POST | `gateway/app/routers/qa.py` | fast 同步 QA proxy v1 alias | active live path/compat |
| `/api/thinking/ask` | POST | `gateway/app/routers/qa.py` | thinking 同步 QA proxy | active live path |
| `/api/v1/thinking/ask` | POST | `gateway/app/routers/qa.py` | thinking 同步 QA proxy v1 alias | active live path/compat |
| `/api/patent/ask` | POST | `gateway/app/routers/qa.py` | patent 同步 QA proxy | active live path |
| `/api/v1/patent/ask` | POST | `gateway/app/routers/qa.py` | patent 同步 QA proxy v1 alias | active live path/compat |
| `/api/fast/ask_stream` | POST | `gateway/app/routers/qa.py` | fast SSE QA proxy | active live path |
| `/api/v1/fast/ask_stream` | POST | `gateway/app/routers/qa.py` | fast SSE QA proxy v1 alias | active live path/compat |
| `/api/thinking/ask_stream` | POST | `gateway/app/routers/qa.py` | thinking SSE QA proxy | active live path |
| `/api/v1/thinking/ask_stream` | POST | `gateway/app/routers/qa.py` | thinking SSE QA proxy v1 alias | active live path/compat |
| `/api/patent/ask_stream` | POST | `gateway/app/routers/qa.py` | patent SSE QA proxy | active live path |
| `/api/v1/patent/ask_stream` | POST | `gateway/app/routers/qa.py` | patent SSE QA proxy v1 alias | active live path/compat |
| `/api/v1/tasks` | POST | `gateway/app/routers/tasks.py` | refresh-survivable QA task 创建 | active but feature-flag gated |
| `/api/v1/tasks/{task_id}` | GET | `gateway/app/routers/tasks.py` | task summary 查询 | active live path |
| `/api/v1/tasks/{task_id}/events` | GET | `gateway/app/routers/tasks.py` | task events JSON 或 SSE replay | active live path |
| `/api/v1/tasks/{task_id}/cancel` | POST | `gateway/app/routers/tasks.py` | task cancel | active live path |
| `/api/conversations`, `/api/v1/conversations` | mixed | `gateway/app/routers/public_proxy.py` | public-service conversation proxy，并注入 active_task | active live path |
| `/api/conversations/{conversation_id}/files/{file_id}/download` | GET | `gateway/app/routers/public_proxy.py` | public-service 文件下载 streaming proxy | active live path |
| `/api/upload_pdf`, `/api/upload_excel` | POST | `gateway/app/routers/public_proxy.py` | public-service upload streaming proxy | active live path |
| `/api/auth/*`, `/api/v1/auth/*` | mixed | `gateway/app/routers/public_proxy.py` | auth proxy | active live path |
| `/api/quota/*`, `/api/v1/quota/*` | mixed | `gateway/app/routers/public_proxy.py` | quota proxy | active live path |
| `/api/admin/*` | mixed | `gateway/app/routers/public_proxy.py` | admin/personnel/departments proxy | active live path |

接口注册证据：

```python
@router.post("/api/fast/ask")
@router.post("/api/v1/fast/ask")
async def ask_fast(payload: AskRequest, request: Request):
    return await _proxy_ask(request, payload, "fast")

@router.post("/api/fast/ask_stream")
@router.post("/api/v1/fast/ask_stream")
async def ask_stream_fast(payload: AskRequest, request: Request):
    return await _proxy_ask_stream(request, payload, "fast")
```

```python
@router.post("/api/v1/tasks")
async def create_task(...):
    if not bool(getattr(request.app.state.settings, "refresh_survivable_qa_tasks_enabled", False)):
        raise HTTPException(status_code=404, detail="task_api_disabled")
    service = QATaskService(request)
    return await service.create_task(payload, auth_context=auth_context)
```

### 2.3 核心调用链

```text
frontend-vue
  -> gateway /api/{mode}/ask_stream
  -> qa.py resolve file context
  -> route_decision.py builds requested/actual route contract
  -> quota_proxy.py precheck grant from public-service
  -> proxy.py opens backend /api/{actual_mode}/ask_stream
  -> qa.py parses SSE frames and injects quota result into done event
  -> public-service internal conversation APIs for persistence where applicable

frontend-vue
  -> gateway /api/v1/tasks
  -> QATaskService create_task
  -> execution_admission policy + queue/relay/slot lease
  -> GatewayTaskExecutor streams backend ask_stream
  -> relay store persists frames for refresh replay
  -> conversation_persistence terminal/progress sync
```

## 3. 发现的重构点

### R-001：gateway 入口 `app.state` 过重

- 严重程度：P1
- 类型：生命周期混乱 / 依赖注入缺失 / 巨型 composition root
- 代码位置：
  - `gateway/app/main.py`
  - `create_app()`
- 接口路径：
  - 全部 gateway routes
- 关键代码片段：

```python
app.state.settings = settings
app.state.redis_runtime = redis_runtime
app.state.execution_queue_status_store = ExecutionQueueStatusStore(redis_service=redis_runtime.service)
app.state.execution_event_relay_store = ExecutionEventRelayStore(redis_service=redis_runtime.service)
app.state.execution_slot_lease_store = ExecutionSlotLeaseStore(redis_service=redis_runtime.service)
app.state.distributed_lock_manager = DistributedLockManager(redis_service=redis_runtime.service)
app.state.backend_registry = BackendRegistry(settings)
app.state.file_context_resolver = FileContextResolver(...)
app.state.route_decision_service = RouteDecisionService()
app.state.proxy_service = ProxyService(settings)
app.state.quota_proxy_service = QuotaProxyService(settings)
app.state.conversation_persistence_service = ConversationPersistenceService(settings)
```

- 当前问题：`app.state` 是事实 service locator。router 和 service 直接取命名字段，导致生命周期、测试替换、依赖边界都分散；当前也没有统一 shutdown/close registry。
- 建议重构方式：引入 `GatewayContainer` 或 `GatewayRuntime` dataclass，`create_app()` 只创建 container 并挂 `app.state.container`。过渡期保留原字段作为兼容桥。
- 是否可抽共享包：部分可抽。`runtime.ResourceRegistry`、`component_status`、public-service client contract 可进入共享包。
- 建议目标模块：`gateway/app/runtime/container.py`、`gateway/app/runtime/dependencies.py`、`packages/agent_common/runtime/resource_registry.py`。
- 设计模式建议：Composition Root、Dependency Injection、Ports/Adapters。
- 影响范围：全部 routers、gateway tests、task runner。
- 风险：中高。一次性替换会破坏测试 monkeypatch 和运行期状态。
- 测试计划：`gateway/tests/test_health.py`、`test_qa_proxy.py`、`test_task_api.py`、`test_admission_api.py`、`test_public_proxy.py`。
- 是否可立即删除：否。
- 删除或迁移前置条件：先定义 container 接口，并保持 `app.state.*` 兼容至少一个版本。

### R-002：`qa.py` 混合 HTTP 路由、路由决策、quota、SSE 透传与事件改写

- 严重程度：P1
- 类型：巨型模块 / 边界越界 / streaming 风险
- 代码位置：
  - `gateway/app/routers/qa.py`
  - `_proxy_ask()`
  - `_proxy_ask_stream()`
  - `_stream_with_quota()`
- 接口路径：
  - `/api/{mode}/ask`
  - `/api/v1/{mode}/ask`
  - `/api/{mode}/ask_stream`
  - `/api/v1/{mode}/ask_stream`
- 关键代码片段：

```python
route_decision, file_context = await _resolve(request, payload, mode)
if route_decision.needs_clarification:
    return _clarification_json(trace_id=trace_id, route_decision=route_decision)
if route_decision.status_code:
    return _file_status_json(trace_id=trace_id, route_decision=route_decision)
quota_proxy: QuotaProxyService = request.app.state.quota_proxy_service
precheck = await quota_proxy.precheck(
    request=request,
    user_id=user_id,
    quota_type=quota_type,
    strict_config=False,
)
```

```python
for frame in frame_buffer.feed(chunk):
    payload, prefix_lines = parse_sse_json_frame(frame)
    payload_type = str(payload.get("type") or "").lower()
    if payload_type == "done":
        done_payload = payload
        done_prefix_lines = prefix_lines
        continue
finalize_result = await asyncio.shield(quota_proxy.finalize(...))
done_payload["quota"] = _quota_payload_from_finalize(...)
yield _encode_sse_payload(done_payload, prefix_lines=done_prefix_lines)
```

- 当前问题：浏览器-facing streaming path 上解析 upstream SSE、暂存 `done`、finalize quota、重写 `done`，这些关键行为与 FastAPI route 绑定在一起，重构或 bugfix 时容易引入 first-byte 延迟、done 丢失或 quota 双计费。
- 建议重构方式：拆 `AskRouteOrchestrator`、`QuotaGrantLifecycle`、`GatewaySSEQuotaFinalizer`、`GatewaySSEErrorFactory`。router 只保留参数绑定和返回 response。
- 是否可抽共享包：`sse_frames.py` 可优先抽到 `packages/agent_common/sse/`；quota grant contract 可抽到 `packages/agent_common/contracts/quota.py`。
- 建议目标模块：`gateway/app/services/qa_orchestrator.py`、`gateway/app/services/quota_grants.py`、`gateway/app/services/sse_gateway_adapter.py`。
- 设计模式建议：Facade、Pipeline、Decorator。
- 影响范围：所有 ask/ask_stream、quota、stream error、clarification/file status 响应。
- 风险：高。SSE 不能变成整流式缓冲，`done` frame 中 quota 注入必须保持。
- 测试计划：扩展 `gateway/tests/test_qa_proxy.py`，增加 first chunk 不等待 `done` 的 regression；覆盖 quota precheck fail、upstream non-SSE error、provider error、clarification、file not ready、patent file route disabled。
- 是否可立即删除：否。
- 删除或迁移前置条件：先冻结 SSE golden contract。

### R-003：`file_context_resolver.py` 是规则引擎型巨型模块

- 严重程度：P1
- 类型：巨型模块 / 复杂策略对象
- 代码位置：
  - `gateway/app/services/file_context_resolver.py`
  - `FileContextResolver.resolve()`
  - `_selection_status()`
- 接口路径：
  - `/api/{mode}/ask`
  - `/api/{mode}/ask_stream`
  - `/api/v1/tasks`
- 关键代码片段：

```python
explicit_refs = self._extract_explicit_refs(text)
has_ordinal_reference = self._has_ordinal_reference(text)
ordinal_selection = self._extract_ordinal_selection(...)
deictic_count_selection = self._extract_deictic_count_selection(...)
literature_identifier = self._has_literature_identifier(text)
strong_file_intent = bool(explicit_refs or has_ordinal_reference or ...)
file_intent = strong_file_intent or table_focus or file_name_focus or selected_scope_action
```

```python
if not row.is_ready:
    return {"code": "FILE_NOT_READY", ...}
if self._strict_minio_only() and not row.has_minio_storage_ref:
    return {"code": status_code, ...}

def _strict_minio_only(self) -> bool:
    return _env_bool("QA_ORIGINAL_MINIO_ONLY", True)
```

- 当前问题：1146 行单类同时做文件提及检测、序号/指代解析、最近上传、DOI guard、generic knowledge guard、表格/文件名 focus、readiness/storage policy、clarification payload、classifier fallback。
- 建议重构方式：保持 `FileContextResolver.resolve()` public API，内部按策略拆分：
  - `FileMentionDetector`
  - `OrdinalReferenceResolver`
  - `RecentUploadResolver`
  - `DOIIntentGuard`
  - `GenericKnowledgeGuard`
  - `FileReadinessPolicy`
  - `ClarificationBuilder`
- 是否可抽共享包：`FileReadinessPolicy` 和 normalized file metadata contract 可抽；中文启发式意图规则建议先留 gateway。
- 建议目标模块：`gateway/app/services/file_context/{resolver.py,detectors.py,ordinal.py,readiness.py,clarification.py}`。
- 设计模式建议：Chain of Responsibility、Policy Objects。
- 影响范围：QA routing、task create、file route behavior。
- 风险：高。中文/英文启发式和文件选择优先级容易回归。
- 测试计划：`gateway/tests/test_file_context_resolver.py`、`test_route_decision.py`、`test_qa_proxy.py` 全量；新增 fixture 固化 DOI、序号、最近上传、MinIO-only、clarification 分支。
- 是否可立即删除：否。
- 删除或迁移前置条件：先把现有 reason/strategy/status_code 输出做 snapshot。

### R-004：`qa_tasks.py` 同时是 task API、状态机、worker、SSE parser、persistence coordinator

- 严重程度：P0
- 类型：巨型模块 / 手写基础设施 / 持久化一致性风险
- 代码位置：
  - `gateway/app/services/qa_tasks.py`
  - `QATaskService`
  - `GatewayTaskExecutor`
- 接口路径：
  - `/api/v1/tasks`
  - `/api/v1/tasks/{task_id}`
  - `/api/v1/tasks/{task_id}/events`
  - `/api/v1/tasks/{task_id}/cancel`
  - public conversation read enrichment
- 关键代码片段：

```python
task_id = f"task_{uuid4().hex}"
precheck = await quota_proxy.precheck(...)
record = {"request_id": task_id, "status": "provisioning", ...}
stored = self.queue_store.put_request(record, ...)
created_turn = await persistence_service.create_task_turn(...)
updated_record["status"] = "queued"
self._append_state_frame(task_id, status="queued")
```

```python
dispatcher = ExecutionAdmissionDispatcher(...)
admitted_seq = self._append_state_if_needed(request_id, status="admitted")
await self._sync_progress_best_effort(...)
running = dispatcher.transition_to_running(...)
path = f"/api/{str(request.get('actual_mode') or '').strip()}/ask_stream"
```

```python
for frame in frame_buffer.feed(chunk):
    payload, _prefix_lines = parse_sse_json_frame(frame)
    event_type = str(payload.get("type") or "").strip().lower()
    appended = self.relay_store.append_frame(...)
    if event_type == "content":
        content_parts.append(delta)
    if event_type == "done":
        await self.conversation_persistence_service.terminal_task_assistant(...)
        quota_result = await self._finalize_quota(...)
```

- 当前问题：2462 行文件内含 task API facade、queue store 使用、状态机、admission dispatch、worker executor、SSE parser、progress accumulator、conversation persistence retry、quota finalize、cancel live stream registry。任何拆分失误都可能造成 task 卡死、重复计费、刷新后不可恢复。
- 建议重构方式：
  - `TaskRepository` 包装 `ExecutionQueueStatusStore`、relay、lease 访问。
  - `TaskStateMachine` 管 provisioning/queued/admitted/running/completed/failed/canceled/expired。
  - `TaskRunner` 承接 `GatewayTaskExecutor._execute_async()`。
  - `TaskEventPublisher` 包装 relay frame append/public event encoding。
  - `TaskPersistenceCoordinator` 管 progress/terminal sync pending 与 public-service calls。
  - `TaskCancellationService` 管 cancel flag、stream abort、lease release。
- 是否可抽共享包：task contract/event shape/SSE parser/progress accumulator 可抽；backend route execution 留 gateway。
- 建议目标模块：`gateway/app/tasks/{service.py,repository.py,state_machine.py,runner.py,events.py,persistence.py,cancellation.py}`。
- 设计模式建议：State Machine、Saga、Unit of Work、Hexagonal Architecture。
- 影响范围：task API、admission worker、conversation active_task、quota。
- 风险：P0。涉及持久化一致性、quota 幂等、取消、刷新恢复。
- 测试计划：`gateway/tests/test_task_api.py`、`test_refresh_survivable_task_e2e.py`、`test_execution_admission.py`、`test_execution_event_relay.py`、`test_execution_queue_status.py`、`test_execution_slot_leases.py`。
- 是否可立即删除：否。
- 删除或迁移前置条件：先保持 `QATaskService` façade，逐步抽纯函数和小类。

### R-005：admission/slot/queue 混合业务策略和底层队列机制

- 严重程度：P1
- 类型：手写基础设施 / 可替换机制边界不清
- 代码位置：
  - `gateway/app/services/execution_admission.py`
  - `gateway/app/services/execution_queue_status.py`
  - `gateway/app/services/execution_slot_leases.py`
- 接口路径：
  - `/api/v1/tasks`
  - `/api/admission/*`
  - `/healthz`
- 关键代码片段：

```python
if same_conversation_live:
    return TaskCreateAdmissionDecision(False, 409, "task_conversation_active")
if live_for_user >= per_user_limit:
    return TaskCreateAdmissionDecision(False, 429, "task_user_active_limit")
if queued_count >= queue_max_size:
    return TaskCreateAdmissionDecision(False, 503, "task_queue_full")
```

```python
active_total = int(slot_metrics.get("active_leases") or 0)
if active_total >= int(self.settings.admission.max_concurrent):
    return False
minimum_slots = max(0, int(self.settings.admission.thinking_min_slots))
```

- 当前问题：业务策略和 Redis/index/lease 机制耦合。必须保留的策略包括同会话互斥、用户并发限制、全局队列容量、fast/patent/thinking 并发上限、thinking 防饿死、状态转移、取消语义、TTL/过期转 terminal sync。可替换的底层机制包括 Redis queue indexes、lease store、dirty rebuild、polling worker、result store、memory fallback。
- 建议重构方式：先定义 `TaskQueueBackend`、`LeaseBackend`、`AdmissionPolicy`、`CapacityPolicy`、`EventRelay` 接口；再评估 arq/dramatiq/celery/rq 替换 queue store/worker claim/lease/retry/result backend。
- 是否可抽共享包：可抽端口和 contract，不建议直接共享 gateway 业务实现。
- 建议目标模块：`gateway/app/tasks/admission_policy.py`、`gateway/app/tasks/backends/redis_queue.py`、`gateway/app/tasks/backends/redis_leases.py`。
- 设计模式建议：Strategy、Ports/Adapters。
- 影响范围：task API、worker、health/admission status。
- 风险：高。成熟队列库默认语义可能改变 cancel latency、event replay retention、lease ownership、quota finalize 幂等。
- 测试计划：现有 execution tests 加 fake backend contract tests；新增 queue replacement compatibility test。
- 是否可立即删除：否。
- 删除或迁移前置条件：先明确 task state contract 和 lease ownership contract。

### R-006：`conversation_persistence.py` 混合 public-service client 与 SSE summary parser

- 严重程度：P2
- 类型：边界越界 / 共享能力候选
- 代码位置：
  - `gateway/app/services/conversation_persistence.py`
  - `StreamSummary`
  - `terminal_task_assistant()`
  - `extract_stream()`
- 接口路径：
  - `/api/{mode}/ask_stream`
  - `/api/v1/tasks/{task_id}/events`
  - public-service internal conversation task APIs
- 关键代码片段：

```python
@dataclass
class StreamSummary:
    assistant_content: str = ""
    query_mode: str = ""
    references: list[Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "source": "gateway_ask_stream",
            "query_mode": self.query_mode,
            "references": list(self.references or []),
        }
```

```python
return await self._post_internal_json(
    path=f"/internal/conversations/{cid}/tasks/{task_id}/assistant-terminal",
    payload=payload,
)
```

```python
async def extract_stream(...):
    frame_buffer = SSEFrameBuffer()
    async for chunk in body_iter:
        for frame in frame_buffer.feed(chunk):
            self._apply_sse_frame(...)
        yield chunk
```

- 当前问题：一个 service 同时是 public-service internal API client、legacy ask_stream summary aggregator、SSE parser consumer。
- 建议重构方式：拆 `ConversationPersistenceClient` 和 `AskStreamSummaryExtractor`；task persistence 和 old ask_stream persistence 分不同 façade。
- 是否可抽共享包：public-service internal conversation task contract、SSE summary parser 可抽；token/env adapter 留 gateway。
- 建议目标模块：`gateway/app/services/conversation_persistence_client.py`、`gateway/app/services/stream_summary.py`、`packages/agent_common/contracts/conversation_tasks.py`。
- 设计模式建议：Adapter、Parser。
- 影响范围：stream persistence、task progress/terminal sync。
- 风险：中。旧 ask_stream summary 可能仍被测试或未显式 caller 依赖。
- 测试计划：`gateway/tests/test_qa_proxy.py` stream summary assertions、`gateway/tests/test_task_api.py` persistence calls；补调用图测试。
- 是否可立即删除：否。
- 删除或迁移前置条件：确认 `persist_user_message/persist_assistant_summary/extract_stream` 当前调用方。

### R-007：public-service proxy 中混入 gateway task active_task 注入

- 严重程度：P1
- 类型：边界越界 / BFF composition 风险
- 代码位置：
  - `gateway/app/routers/public_proxy.py`
  - `_live_task_summary_by_conversation()`
  - `_maybe_enrich_conversation_reads()`
- 接口路径：
  - `/api/conversations`
  - `/api/v1/conversations`
  - `/api/conversations/{conversation_id}`
  - `/api/v1/conversations/{conversation_id}`
- 关键代码片段：

```python
def _live_task_summary_by_conversation(request: Request) -> dict[int, dict]:
    queue_store = request.app.state.execution_queue_status_store
    task_service = QATaskService(request)
    for record in queue_store.list_requests():
        ...
        summaries[conversation_id] = task_service.build_task_summary(request_id)
```

- 当前问题：auth/quota/conversation persistence 基本由 public-service authority 提供，但 gateway 对 public conversation read response 注入 `active_task`，使 public API 响应形态由 gateway runtime 再加工，边界不纯。
- 建议重构方式：短期保留 gateway BFF composition；中期显式化为 gateway-owned `TaskConversationEnricher`；长期让前端读 `/api/v1/tasks` 或 public-service 引用正式 active_task contract。
- 是否可抽共享包：active task summary schema 可抽到 contract。
- 建议目标模块：`gateway/app/services/conversation_task_enricher.py`、`packages/agent_common/contracts/task_summary.py`。
- 设计模式建议：Backend-for-Frontend Composition、Anti-corruption Layer。
- 影响范围：前端 conversation list/detail active_task 展示和 refresh recovery。
- 风险：中。移除 enrichment 会破坏 UI 恢复。
- 测试计划：`gateway/tests/test_public_proxy.py`、`test_refresh_survivable_task_e2e.py` active_task assertions。
- 是否可立即删除：否。
- 删除或迁移前置条件：前端改读 task endpoint 或 public-service 实现正式 active_task。

### R-008：compat route 面大量活跃，不能简单下线

- 严重程度：P2
- 类型：遗留代码 / 路由契约债务
- 代码位置：
  - `gateway/app/routers/public_proxy.py`
  - `_paths()`
  - `_ROUTE_SPECS`
- 接口路径：
  - `/api/v1/*`
  - `/api/admin/departments/secondary/{secondary_id}/legacy-users`
- 关键代码片段：

```python
def _paths(path: str, *, include_v1: bool = True) -> tuple[str, ...]:
    paths = [path]
    if include_v1:
        paths.append(path.replace("/api/", "/api/v1/", 1))
    return tuple(paths)
```

```python
(_paths("/api/admin/departments/secondary/{secondary_id}/legacy-users", include_v1=False), ("GET",)),
```

- 当前问题：`/api/v1/*` alias 和 `legacy-users` 都是 active live path，不能按名字直接删除。route specs 目前没有 canonical/compat/deprecated 标记。
- 建议重构方式：把 public proxy route specs 声明化，给 route family 标注 `canonical`、`compatibility`、`deprecated but still referenced`。
- 是否可抽共享包：route contract 表可生成文档或共享 schema。
- 建议目标模块：`gateway/app/services/public_route_table.py` 或扩展现有 `route_table.py`。
- 设计模式建议：Declarative Route Registry。
- 影响范围：public proxy、frontend paths、tests route table。
- 风险：中低。
- 测试计划：`gateway/tests/test_public_proxy.py`、`gateway/tests/test_route_table.py`。
- 是否可立即删除：否。
- 删除或迁移前置条件：frontend 和 public-service 确认不再使用。

## 4. 可抽共享能力清单

| 能力 | 当前重复位置 | 建议共享模块 | 迁移优先级 |
| -- | ------ | ------ | ----- |
| SSE frame parser/buffer | `gateway/app/services/sse_frames.py`、`qa.py`、`qa_tasks.py`、`conversation_persistence.py` | `packages/agent_common/sse/frames.py` | P1 |
| Gateway ask/task event contract | `qa.py`、`qa_tasks.py`、backend routers | `packages/agent_common/contracts/stream_event.py`、`task_event.py` | P1 |
| Quota grant client contract | `quota_proxy.py`、`qa.py`、`qa_tasks.py` | `packages/agent_common/contracts/quota.py`、`clients/quota.py` | P1 |
| Public-service auth client | `gateway/app/core/auth.py` | `packages/agent_common/clients/auth.py` | P2 |
| Conversation task persistence client | `conversation_persistence.py` | `packages/agent_common/clients/conversations.py` | P1 |
| File metadata/readiness contract | `conversation_files.py`、`conversation_file_normalizer.py`、`file_context_resolver.py` | `packages/agent_common/files/metadata.py` | P2 |
| Task queue ports | `execution_queue_status.py`、`execution_slot_leases.py` | `packages/agent_common/runtime/task_queue_ports.py` | P2 |
| Runtime component status | `main.py`、`health.py`、backend services | `packages/agent_common/runtime/component_status.py` | P2 |

## 5. 可清理遗留代码清单

| 代码位置 | 当前状态 | 是否注册 | 是否被引用 | 建议处理 |
| ---- | ---- | ---- | ----- | ---- |
| `gateway/app/routers/qa.py` `/api/v1/{mode}/ask*` | active live path / compatibility | 是 | 是，前端和 tests 仍可能使用 | 先标记 compatibility，不能删除 |
| `gateway/app/routers/tasks.py` `/api/v1/tasks*` | active live path，create feature-flag gated | 是 | 是 | 保留；补充 flag 关闭时查询/取消是否应 gate 的决策 |
| `gateway/app/routers/public_proxy.py` `/api/v1/*` | active live path / compatibility | 是 | 是 | 迁到显式 route registry |
| `gateway/app/routers/public_proxy.py` `legacy-users` | deprecated but still referenced / active route | 是 | 需要进一步验证具体前端使用 | 标记 deprecated，迁移前查调用 |
| `gateway/app/services/execution_admission.py` 顶部 infra-only 注释 | deprecated but still referenced / stale comment | 不适用 | 模块 live 使用 | 后续文档更新，不是删除代码 |
| `gateway/app/services/file_context_resolver.py` `QA_ORIGINAL_MINIO_ONLY` guard | active live path compatibility/env guard | 不适用 | 是 | 保留，抽成 `FileReadinessPolicy` |
| `gateway/app/services/conversation_persistence.py` old stream summary APIs | unknown，需要进一步验证 | 不适用 | 需要调用图确认 | 若无 live caller，标注 deprecated and unregistered |
| `gateway/app/services/route_classifier.py` `NoopRouteClassifier` | scaffold / placeholder but active default | 不适用 | 是 | 保留为默认策略，后续替换真实 classifier |

## 6. 接口与契约风险

- gateway -> backend contract：`requested_mode`、`actual_mode`、`route`、`source_scope`、`turn_mode`、`execution_files`、`selected_file_ids`、`primary_file_id`、`file_selection` 由 gateway 组装，多个 backend 重复校验，适合抽共享 contract。
- frontend -> gateway contract：`/api/v1/tasks*`、`/api/v1/{mode}/ask_stream`、public proxy `/api/v1/*` 都是 active compatibility routes，不能直接下线。
- backend -> public-service contract：quota precheck/finalize、conversation task create/progress/terminal/rollback 是内部关键契约，需要 contract tests。
- internal token/auth headers：admission control token、public-service internal token、user auth 由不同服务读取，需统一 client。
- SSE event schema：gateway 会解析并改写 `done` event，必须冻结 schema。
- task event schema：relay event、public task summary、active_task enrichment 共用 shape，但目前散落在 `qa_tasks.py` 和 `public_proxy.py`。

## 7. 测试计划

- 单元测试：`test_file_context_resolver.py`、`test_route_decision.py`、`test_execution_admission.py`、`test_execution_queue_status.py`、`test_execution_slot_leases.py`。
- contract test：新增 gateway normalized ask contract、task summary contract、quota grant contract。
- stream/SSE test：`test_qa_proxy.py` 覆盖 first chunk、done event quota 注入、error SSE、upstream non-SSE。
- integration smoke test：`test_refresh_survivable_task_e2e.py` 和 public proxy active_task enrichment。
- backward compatibility test：`/api/v1/{mode}/ask*`、`/api/v1/tasks*`、`/api/v1/conversations*`。
- failure/cancel/retry test：task cancel、lease release、quota abort/finalize fail、terminal sync pending recovery。
- persistence test：conversation task create/progress/terminal/rollback。
- quota/auth test：quota precheck/finalize、auth context resolution、admission control token。
- file route test：file not ready、MinIO-only、patent file route disabled、clarification。

## 8. 建议重构顺序

1. P0：为 `qa_tasks.py` 的状态和事件输出补 contract snapshot，不先改实现。
2. P1：抽 `sse_frames.py` 和 SSE golden tests，降低 streaming 重构风险。
3. P1：把 `qa.py` 中 quota grant lifecycle 和 SSE done 注入抽成独立 service，router 行为保持不变。
4. P1：把 `file_context_resolver.py` 内部拆策略对象，保持 public API 不变。
5. P1：为 task queue/admission 定义端口接口，把业务策略和 Redis 机制分离。
6. P2：引入 `GatewayContainer`，先作为兼容 wrapper，不大规模改 router。
7. P2：把 public proxy route table 声明化并标注 canonical/compat/deprecated。

## 9. 需要进一步确认的问题

1. `conversation_persistence.extract_stream/persist_user_message/persist_assistant_summary` 是否仍有 live caller。
2. `/api/v1/tasks/{task_id}`、`events`、`cancel` 在 create feature flag 关闭时仍注册且不检查 flag，是有意兼容还是遗漏。
3. `execution_admission.py` 顶部 “infra-only” 注释与当前 live 使用不一致，应确认是否更新文档。
4. public proxy active_task enrichment 是长期 contract 还是过渡 BFF 行为。
5. 若替换为 arq/dramatiq/celery/rq，需要先明确 replay retention、cancel latency、quota finalize 幂等、terminal sync repair、lease ownership。

## 第二轮深度补充

> 执行身份：Agent 1 gateway 深度只读重构审计。  
> 执行约束：只读采集代码证据；未运行测试；未修改 gateway 源码、配置、测试、脚本、README、依赖文件。  
> 必跑命令已执行：`find gateway -type f`、`find gateway -type f \( -name "*.py" -o -name "*.js" -o -name "*.vue" \) -print0 | xargs -0 wc -l | sort -nr | head -50`、以及用户指定的 6 组 `rg` 检索。  
> 文件清点结论：`find gateway -type f` 显示 gateway 下含源码、测试、docs、scripts、`.pytest_cache`、`__pycache__`；行为证据仅采用源码/测试/脚本/README，不把 pycache 当行为来源。  
> 规模热点：`wc -l` 前 10 为 `tests/test_task_api.py` 4719、`tests/test_qa_proxy.py` 3579、`app/services/qa_tasks.py` 2462、`tests/test_execution_admission.py` 1183、`tests/test_public_proxy.py` 1173、`app/services/file_context_resolver.py` 1146、`app/services/execution_admission.py` 1027、`tests/test_file_context_resolver.py` 906、`app/routers/qa.py` 867、`tests/test_refresh_survivable_task_e2e.py` 790。

### A. 第一轮结论复核

| 第一轮结论 | 第二轮复核 | 代码证据 |
|---|---|---|
| R-001 `app.state` 过重 | 确认。`create_app()` 一次性挂载配置、Redis runtime、queue/relay/lease、lock、registry、file service/resolver、route decision、proxy、task runtime、auth、quota、conversation persistence、component status。没有 lifespan/shutdown。 | `gateway/app/main.py:39-96` |
| R-002 `qa.py` 混合 route/quota/SSE | 确认。同步问答和流式问答均在 router 内做 resolve、quota precheck/finalize、proxy、错误整形；流式路径解析 SSE，暂存 done 后注入 quota。 | `gateway/app/routers/qa.py:226-353`, `621-831` |
| R-003 `file_context_resolver.py` 是规则引擎巨型模块 | 确认。规则顺序横跨 explicit ref、ordinal、deictic count、selected scope、classifier、generic guard、plural/latest/table/singular/metadata focus、readiness/storage。 | `gateway/app/services/file_context_resolver.py:228-516`, `824-872` |
| R-004 `qa_tasks.py` 过大且承担状态机/worker/SSE/persistence | 确认。创建任务、恢复 provisioning、事件流、取消、immediate dispatch、worker executor、progress flush、terminal sync、quota finalize 均在同文件。 | `gateway/app/services/qa_tasks.py:252-681`, `1400-2462` |
| R-005 admission/slot/queue 混合策略和存储机制 | 确认。admission policy、capacity、claim、requeue、complete 与 Redis/memory store 行为耦合；store 内部还有 dirty-index repair。 | `gateway/app/services/execution_admission.py:67-113`, `160-641`; `execution_queue_status.py:15-608`; `execution_slot_leases.py:24-394` |
| R-006 conversation persistence client 与 SSE summary parser 混合 | 确认，但 live caller 要分层看：task path active 调用 internal create/progress/terminal/rollback；老 `persist_user_message/persist_assistant_summary/extract_stream` 在 gateway 源码内未见直接调用，测试仍覆盖 summary parser。 | `gateway/app/services/conversation_persistence.py:119-336`, `338-424`; `gateway/tests/test_qa_proxy.py` 中 `test_gateway_stream_summary_keeps_reference_objects_from_done_event` |
| R-007 public proxy 混入 active_task enrichment | 确认。conversation list/detail 读 response 后扫描 gateway queue 并注入 `active_task`。 | `gateway/app/routers/public_proxy.py:49-69`, `113-195` |
| R-008 compat route 面活跃，不能按名字删除 | 部分修正。`/api/v1/{mode}/ask*` active；`/api/v1/*` public proxy active；`legacy-users` active 且 frontend 调用。README 提到 `/api/ask`、`/api/ask_stream` 兼容 alias，但 gateway tests 明确要求 `/api/ask`、`/api/v1/ask`、`/api/v1/ask_stream` removed。 | `gateway/app/routers/qa.py:834-867`; `gateway/app/routers/public_proxy.py:222-317`; `gateway/tests/test_qa_proxy.py:2409`, `3041`, `3059`; `frontend-vue/src/services/admin.js:593` |

### B. `app.state` 全量挂载表

| 对象 | 类别 | 初始化位置 | 关闭责任 | 是否建议进 ServiceContainer/ResourceRegistry/lazy init |
|---|---|---|---|---|
| `settings` | 配置 | `main.py:40`, `53` | 无需关闭 | 进 `GatewayContainer.settings` |
| `redis_runtime` | 外部资源/runtime | `main.py:51`, `54` | 当前无 close；Redis client 没有 lifespan close | 进 `ResourceRegistry`，补 close/probe |
| `execution_queue_status_store` | runtime 状态/外部资源适配 | `main.py:55` | 无显式关闭；Redis/memory fallback | 进 `TaskRuntime.queue_store` |
| `execution_event_relay_store` | runtime 状态/外部资源适配 | `main.py:56` | 无显式关闭 | 进 `TaskRuntime.relay_store` |
| `execution_slot_lease_store` | runtime 状态/外部资源适配 | `main.py:57` | 无显式关闭；lease TTL 自清理 | 进 `TaskRuntime.slot_lease_store` |
| `distributed_lock_manager` | 外部资源/并发控制 | `main.py:58` | 无显式关闭 | 进 `TaskRuntime.lock_manager`，lazy 可行 |
| `backend_registry` | 配置/业务服务 | `main.py:59` | 无需关闭 | 进 `GatewayContainer.backends` |
| `conversation_file_service` | 业务服务/外部 public-service client facade | `main.py:60-62` | provider 若有 httpx transport 不持久，无 close | 进 `GatewayContainer.files` |
| `file_context_resolver` | 业务服务/规则引擎 | `main.py:63-70` | 无需关闭 | 进 `GatewayContainer.routing`；classifier provider 可 lazy |
| `route_decision_service` | 业务服务 | `main.py:71` | 无需关闭 | 进 `GatewayContainer.routing` |
| `proxy_service` | 外部资源 client factory | `main.py:72` | 每次构建 httpx client，无持久 close | 进 `GatewayContainer.proxy` |
| `active_task_streams` | runtime 状态 | `main.py:73` | 当前 cancel/unregister best effort，无 shutdown drain | 进 `TaskRuntime.live_stream_registry`；需 shutdown drain |
| `active_task_streams_lock` | runtime 状态 | `main.py:74` | 无需关闭 | 与 live registry 封装 |
| `gateway_auth_service` | 业务服务/外部 public-service auth client | `main.py:75` | 无持久 close | 进 `GatewayContainer.auth` |
| `quota_proxy_service` | 业务服务/外部 public-service internal client | `main.py:76` | 无持久 close | 进 `GatewayContainer.quota` |
| `conversation_persistence_service` | 业务服务/外部 public-service internal client | `main.py:77` | 无持久 close | 进 `GatewayContainer.conversations` |
| `component_status` | runtime 状态快照 | `main.py:78-89` | 静态快照；health 再动态覆盖 | 进 `ComponentStatusRegistry`，避免启动快照和 live 指标混杂 |

关键片段（`gateway/app/main.py:51-95`，<=40 行）：

```python
redis_runtime = bootstrap_redis_runtime(settings.redis)
app.state.settings = settings
app.state.redis_runtime = redis_runtime
app.state.execution_queue_status_store = ExecutionQueueStatusStore(redis_service=redis_runtime.service)
app.state.execution_event_relay_store = ExecutionEventRelayStore(redis_service=redis_runtime.service)
app.state.execution_slot_lease_store = ExecutionSlotLeaseStore(redis_service=redis_runtime.service)
app.state.distributed_lock_manager = DistributedLockManager(redis_service=redis_runtime.service)
app.state.backend_registry = BackendRegistry(settings)
app.state.conversation_file_service = ConversationFileService(
    provider=build_conversation_file_provider(settings),
)
app.state.file_context_resolver = FileContextResolver(...)
app.state.route_decision_service = RouteDecisionService()
app.state.proxy_service = ProxyService(settings)
app.state.active_task_streams = {}
app.state.active_task_streams_lock = threading.RLock()
app.state.gateway_auth_service = GatewayAuthService(settings)
app.state.quota_proxy_service = QuotaProxyService(settings)
app.state.conversation_persistence_service = ConversationPersistenceService(settings)
app.include_router(health_router)
app.include_router(admission_router)
app.include_router(tasks_router)
app.include_router(public_proxy_router)
app.include_router(qa_router)
```

### C. Router 完整表

| 路径 | 方法 | 文件 | 入参模型 | 调用 service | 下游 backend | 持久化/鉴权/quota/SSE | 测试覆盖 |
|---|---|---|---|---|---|---|---|
| `/healthz` | GET | `gateway/app/routers/health.py:15-51` | 无 | `ProxyService.probe_health`, `build_admission_status`, store `.describe()` | public/fast/thinking/patent health | 无鉴权；不持久化；无 quota；非 SSE | `test_health.py` |
| `/api/admission/status` | GET | `gateway/app/routers/admission.py:72-91` | query/header token | `build_admission_status`, queue/relay/lease describe | 无直接下游 | prod 需 `x-admission-control-token` 或 dev/test 放行；非 SSE | `test_admission_api.py`, `test_execution_admission.py` |
| `/api/admission/requests/{request_id}` | GET | `admission.py:94-112` | path `request_id` | `ExecutionQueueStatusStore`, `ExecutionEventRelayStore` | 无 | admission control auth；读 queue/result/relay | `test_admission_api.py` |
| `/api/admission/requests/{request_id}/cancel` | POST | `admission.py:115-134` | path `request_id` | `ExecutionQueueStatusStore.cancel_request` | 无 | admission control auth；仅 queued 可取消 | `test_admission_api.py` |
| `/api/admission/requests/{request_id}/frames` | GET | `admission.py:137-153` | path + `after_sequence` | `ExecutionEventRelayStore.get_frames` | 无 | admission control auth；JSON replay | `test_admission_api.py` |
| `/api/v1/tasks` | POST | `gateway/app/routers/tasks.py:15-24` | `AskRequest`; `AuthContext` dependency | `QATaskService.create_task` | public auth/quota/conversation; QA backend later by worker | create 被 `refresh_survivable_qa_tasks_enabled` gate；需要 user auth；quota precheck；conversation task turn persisted；非 SSE response | `test_task_api.py`, `test_refresh_survivable_task_e2e.py` |
| `/api/v1/tasks/{task_id}` | GET | `tasks.py:27-35` | path `task_id`; auth | `QATaskService.reconcile_pending_terminal_tasks`, `get_task` | public-service terminal/progress/quota repair if pending | create flag 关闭时仍可读；auth owner check；非 SSE | `test_task_api.py` |
| `/api/v1/tasks/{task_id}/events` | GET | `tasks.py:38-50` | path + `after_seq`; auth | `QATaskService.stream_task_events` or `get_task_events` | may immediate dispatch to QA backend; public-service progress sync | auth owner check；`Accept: text/event-stream` 时 SSE；否则 JSON replay | `test_task_api.py` |
| `/api/v1/tasks/{task_id}/cancel` | POST | `tasks.py:53-60` | path; auth | `QATaskService.cancel_task` | public-service terminal/quota; active stream abort | auth owner check；cancel live/queued；quota finalize false；非 SSE response | `test_task_api.py` |
| `/api/fast/ask`, `/api/v1/fast/ask` | POST | `gateway/app/routers/qa.py:834-837` | `AskRequest` | `_proxy_ask` -> resolver/decision/quota/proxy | fast unless file/mixed can route actual `fast`; public quota | no auth dependency in gateway router; quota when valid user_id; JSON | `test_qa_proxy.py` |
| `/api/thinking/ask`, `/api/v1/thinking/ask` | POST | `qa.py:840-843` | `AskRequest` | `_proxy_ask` | thinking for kb_only; fast for file/mixed | quota; no gateway persistence in direct path | `test_qa_proxy.py`, `test_route_decision.py` |
| `/api/patent/ask`, `/api/v1/patent/ask` | POST | `qa.py:846-849` | `AskRequest` | `_proxy_ask` | patent; file routes gated by `patent_file_routes_enabled` | quota; JSON; `X-Gateway-Backend` on gate errors | `test_qa_proxy.py` |
| `/api/fast/ask_stream`, `/api/v1/fast/ask_stream` | POST | `qa.py:852-855` | `AskRequest` | `_proxy_ask_stream`, `_stream_with_quota` | fast | quota precheck/finalize; SSE passthrough with done rewrite; no direct persistence | `test_qa_proxy.py` |
| `/api/thinking/ask_stream`, `/api/v1/thinking/ask_stream` | POST | `qa.py:858-861` | `AskRequest` | `_proxy_ask_stream` | thinking for kb_only; fast for file/mixed | SSE; quota; provider/status/clarification synthetic SSE | `test_qa_proxy.py` |
| `/api/patent/ask_stream`, `/api/v1/patent/ask_stream` | POST | `qa.py:864-867` | `AskRequest` | `_proxy_ask_stream` | patent | SSE; quota; patent file route gate; preserves patent capability header in task path, direct path forwards request headers via proxy | `test_qa_proxy.py`, `test_task_api.py` |
| `/api/auth/login`, `/api/v1/auth/login` | POST | `public_proxy.py:222-317` | passthrough body | `ProxyService.forward` | public | public auth authority; no gateway quota; non-SSE | `test_public_proxy.py`, `test_route_table.py` |
| `/api/auth/register`, `/api/v1/auth/register` | POST | same | passthrough | `ProxyService.forward` | public | same | `test_route_table.py` |
| `/api/auth/me`, `/api/v1/auth/me` | GET | same | passthrough | `ProxyService.forward` | public | forwards auth headers | `test_public_proxy.py` |
| `/api/auth/departments/tree`, `/api/v1/auth/departments/tree` | GET | same | passthrough | `ProxyService.forward` | public | auth proxy | `test_route_table.py` |
| `/api/auth/department`, `/api/v1/auth/department` | PUT | same | passthrough | `ProxyService.forward` | public | auth proxy | `test_route_table.py` |
| `/api/auth/username`, `/api/v1/auth/username` | PUT | same | passthrough | `ProxyService.forward` | public | auth proxy | `test_route_table.py` |
| `/api/auth/personnel-binding`, `/api/v1/auth/personnel-binding` | PUT | same | passthrough | `ProxyService.forward` | public | auth proxy | `test_route_table.py` |
| `/api/auth/password`, `/api/v1/auth/password` | POST, PUT | same | passthrough | `ProxyService.forward` | public | auth proxy | `test_route_table.py` |
| `/api/auth/forgot-password/initiate`, `/api/v1/auth/forgot-password/initiate` | POST | same | passthrough | `ProxyService.forward` | public | public auth | `test_route_table.py` |
| `/api/auth/forgot-password/verify`, `/api/v1/auth/forgot-password/verify` | POST | same | passthrough | `ProxyService.forward` | public | public auth | `test_route_table.py` |
| `/api/auth/security-questions`, `/api/v1/auth/security-questions` | GET, POST, PUT | same | passthrough | `ProxyService.forward` | public | public auth | `test_route_table.py` |
| `/api/conversations`, `/api/v1/conversations` | GET, POST | `public_proxy.py:234` | passthrough | `ProxyService.forward`, `_maybe_enrich_conversation_reads` | public | GET list injects `active_task`; POST passthrough; auth forwarded | `test_public_proxy.py` |
| `/api/conversations/{conversation_id}`, `/api/v1/conversations/{conversation_id}` | GET, DELETE | `public_proxy.py:235` | path passthrough | `QATaskService.reconcile_pending_terminal_tasks` before GET; proxy; enrichment | public | GET detail active_task enrichment; auth forwarded | `test_public_proxy.py`, `test_refresh_survivable_task_e2e.py` |
| `/api/conversations/{conversation_id}/title`, `/api/v1/.../title` | PUT | `public_proxy.py:236` | passthrough | proxy | public | auth forwarded | `test_route_table.py` |
| `/api/conversations/{conversation_id}/messages`, `/api/v1/.../messages` | POST | `public_proxy.py:237` | passthrough | proxy | public | conversation persistence authority | `test_public_proxy.py` |
| `/api/conversations/{conversation_id}/files`, `/api/v1/.../files` | GET | `public_proxy.py:238` | passthrough | proxy | public | file metadata authority | `test_provider_factory.py`, `test_qa_proxy.py` for provider |
| `/api/conversations/{conversation_id}/files/{file_id}` and `/download`, plus v1 aliases | GET/DELETE; download GET | `public_proxy.py:239-240` | path passthrough | proxy; download uses streaming path | public | auth/query token forwarded; download SSE no, raw streaming | `test_public_proxy.py` |
| `/api/upload_pdf`, `/api/v1/upload_pdf` | POST | `public_proxy.py:241`; streaming list `28-34` | multipart passthrough | `ProxyService.open_request_stream`; public path rewrite to `/upload_pdf` | public | upload streaming; auth forwarded | `test_public_proxy.py` |
| `/api/upload_excel`, `/api/v1/upload_excel` | POST | `public_proxy.py:242`; streaming list | multipart passthrough | streaming proxy; rewrite to `/upload_excel` | public | upload streaming | `test_public_proxy.py` |
| `/api/clear_pdf`, `/api/v1/clear_pdf` | POST | `public_proxy.py:243`; rewrite in `proxy.py:42-47` | passthrough | proxy | public path `/clear_pdf` | no gateway persistence | `test_public_proxy.py` |
| `/api/translate`, `/api/v1/translate` | POST | `public_proxy.py:244` | passthrough | proxy | public | no gateway SSE | `test_route_table.py` |
| `/api/translate_document`, `/api/v1/translate_document` | POST | `public_proxy.py:245`; streaming list | passthrough | streaming proxy | public | raw streaming | `test_public_proxy.py` |
| `/api/kb_info`, `/api/v1/kb_info`; `/api/refresh_kb`, `/api/v1/refresh_kb`; `/api/clear_cache`, `/api/v1/clear_cache`; `/api/background_status`, `/api/v1/background_status`; `/api/health`, `/api/v1/health` | GET/POST as registered | `public_proxy.py:246-250` | passthrough | proxy; `/api/health` rewrite to `/health` | public | no gateway persistence/quota | `test_public_proxy.py`, `test_route_table.py` |
| `/api/literature_content`, `/api/v1/literature_content` | GET | `public_proxy.py:251` | passthrough | proxy | public | auth/query forwarded | `test_public_proxy.py` |
| `/api/reference_preview`, `/api/v1/reference_preview` | POST | `public_proxy.py:252` | passthrough | proxy | public | auth forwarded | `test_public_proxy.py` |
| `/api/patent/original/{canonical_patent_id}`, `/api/v1/...` | GET, HEAD | `public_proxy.py:253`, streaming decision `40-45` | path/query passthrough | proxy or streaming proxy for fulltext | public | `section` controls streaming; auth forwarded | `test_public_proxy.py` |
| `/api/summarize_pdf/{doi:path}`, `/api/v1/...` | POST | `public_proxy.py:254` | path/body passthrough | proxy | public | DOI passthrough | `test_route_table.py` |
| `/api/extract_pdf_text/{doi:path}`, `/api/v1/...` | GET | `public_proxy.py:255` | path passthrough | proxy | public | DOI passthrough | `test_route_table.py` |
| `/api/check_pdf/{doi:path}`, `/api/v1/...` | GET | `public_proxy.py:256` | path passthrough | proxy | public | DOI passthrough | `test_route_table.py` |
| `/api/view_pdf/{doi:path}`, `/api/v1/...` | GET, HEAD | `public_proxy.py:257`; streaming list | path/query passthrough | streaming proxy | public | query token compatibility preserved | `test_public_proxy.py` |
| `/api/quota/my`, `/api/v1/quota/my` | GET | `public_proxy.py:258` | passthrough | proxy | public | user quota authority | `test_public_proxy.py` |
| `/api/quota/configs`, `/api/v1/quota/configs` | GET, POST | `public_proxy.py:259` | passthrough | proxy | public | quota admin | `test_public_proxy.py` |
| `/api/quota/configs/{quota_type:path}`, `/api/v1/...` | PUT | `public_proxy.py:260` | passthrough | proxy | public | quota admin | `test_route_table.py` |
| `/api/quota/users/{user_id}`, `/api/v1/...` | GET | `public_proxy.py:261` | passthrough | proxy | public | quota admin | `test_route_table.py` |
| `/api/quota/reset/{user_id}/{quota_type:path}`, `/api/v1/...` | POST | `public_proxy.py:262` | passthrough | proxy | public | quota admin | `test_route_table.py` |
| `/api/admin/model-status`, `/api/admin/model-status/test` | GET, POST | `public_proxy.py:263-264` | passthrough | proxy | public | admin; no v1 alias | `test_route_table.py` |
| `/api/admin/users...` all registered user routes | mixed | `public_proxy.py:265-275` | passthrough | proxy | public | admin; no v1 alias | `test_route_table.py`, frontend `admin.js` |
| `/api/admin/personnel...` all registered personnel routes | mixed | `public_proxy.py:276-286` | passthrough | proxy | public | admin; no v1 alias | `test_route_table.py`, frontend `admin.js` |
| `/api/admin/departments...` all registered department routes including `legacy-users` | mixed | `public_proxy.py:287-307` | passthrough | proxy | public | admin; no v1 alias; `legacy-users` active | `test_route_table.py`, `test_public_proxy.py`; frontend `admin.js:593` |

### D. QA live path 细化

#### D.1 Direct ask_stream path

```text
frontend-vue/src/services/api.js:626-630
  -> POST /api/{mode}/ask_stream or /api/v1/{mode}/ask_stream
  -> gateway/app/routers/qa.py:852-867 route function
  -> _proxy_ask_stream(): qa.py:723-831
  -> _resolve(): qa.py:41-54
  -> ConversationFileService.list_files(): conversation_files.py:31-37
  -> FileContextResolver.resolve(): file_context_resolver.py:228-516
  -> RouteDecisionService.decide(): route_decision.py:12-65
  -> QuotaProxyService.precheck(): quota_proxy.py:34-50, /internal/quota/grants/precheck
  -> ProxyService.open_json_stream(): proxy.py:208-238
  -> backend /api/{actual_mode}/ask_stream
  -> _stream_with_quota(): qa.py:226-353 parses SSE, withholds done, finalizes quota, injects quota into done
  -> QuotaProxyService.finalize(): quota_proxy.py:52-63, /internal/quota/grants/{grant_id}/finalize
```

重要修正：direct `qa.py` path 当前不调用 `ConversationPersistenceService`。`test_mode_ask_stream_does_not_persist_gateway_messages`、`test_mode_ask_stream_thinking_skips_public_message_persistence` 等测试名显示直接流式路径刻意跳过 gateway message persistence；conversation persistence 在 task path 才是 live authority 写入。

#### D.2 Refresh-survivable task QA path

```text
frontend-vue task rollout surface
  -> POST /api/v1/tasks
  -> tasks.py:create_task()
  -> QATaskService.create_task(): quota precheck + provisional queue record + public-service create-turn
  -> /api/v1/tasks/{task_id}/events with Accept: text/event-stream
  -> QATaskService.stream_task_events()
  -> _try_fast_dispatch_streaming_task()
  -> ExecutionAdmissionDispatcher.claim_specific_request_if_eligible()
  -> ExecutionAdmissionWorker.run_claimed_request()
  -> GatewayTaskExecutor.execute()
  -> backend /api/{actual_mode}/ask_stream
  -> SSE parse into ExecutionEventRelayStore
  -> progress_task_assistant / terminal_task_assistant to public-service
  -> quota finalize
  -> conversation list/detail reads enriched by public_proxy active_task
```

关键证据：

```python
created_turn = await persistence_service.create_task_turn(...)
updated_record["status"] = "queued"
self._append_state_frame(task_id, status="queued")
```

`gateway/app/services/qa_tasks.py:397-440`

```python
path = f"/api/{str(request.get('actual_mode') or '').strip()}/ask_stream"
handle = await self._await_with_cancel(
    self.proxy_service.open_json_stream(...),
    cancel_event=cancel_event,
    request_id=request_id,
    label="open",
)
```

`gateway/app/services/qa_tasks.py:1736-1777`

### E. task/admission 深挖

| 子系统 | 状态/规则 | Redis 行为 | memory fallback | cancel/retry/recover | 证据 |
|---|---|---|---|---|---|
| create admission | 同会话 live task 返回 409；用户 active 达上限返回 429；queued 达队列上限返回 503 | `queue_status_store.list_requests()` 全量扫描 live records | 同逻辑扫描 memory dict | create 失败会 rollback public-service turn、delete queue record、quota finalize false | `execution_admission.py:67-113`; `qa_tasks.py:291-523`, `1203-1228` |
| queue/status store | request/result TTL；queued 过期 terminalize 为 `expired` 且 `terminal_sync_pending=True` | request/result keys + index sets/zsets；dirty flag + rebuild | `_memory_requests`, `_memory_results`, indexes sets | `cancel_request` 只 queued；`cancel_active_request` 可取消 provisioning/queued/admitted/running | `execution_queue_status.py:15-608` |
| event relay | gateway sequence 单调；upstream `seq` 去重；terminal 后阻止追加 | frames list + cursor/sequence/upstream_sequence/frame_count/index/dirty repair | `_memory_frames`, latest sequence/upstream sequence | replay after terminal 返回空；污染窗口过滤 post-terminal frames | `execution_event_relay.py:15-523` |
| slot leases | request_id 独占 lease；capacity key 计数；owner 校验 renew/release | lease key + active/capacity/acquired/expiry indexes + dirty repair | `_memory_leases`, capacity sets | complete/requeue/cancel 释放 lease；缺失 lease 在 record owner 匹配时可容忍 | `execution_slot_leases.py:24-394`; `execution_admission.py:359-499` |
| dispatcher | priority: fast/patent before thinking；starvation threshold；thinking_min_slots reserve | 通过 store describe 和 list_requests | 同上 | `requeue_request`, `transition_to_running`, `complete_request` | `execution_admission.py:160-641` |
| worker | poll claim -> executor -> complete/requeue/failed；executor exception requeue | 需要 shared Redis，worker start probe fail returns 3 | worker 不允许 memory-only；web path 可 fallback memory | `_coalesce_terminal_race` 合并 cancel/terminal race | `execution_admission.py:643-914`, `917-1023` |
| event stream API | JSON replay or SSE polling live tail；缺 terminal frame 时补 state terminal | Relay store | Relay store | client disconnect break；immediate dispatch thread may start head queued task | `qa_tasks.py:541-622`, `1141-1180` |
| live cancel | active stream registry + cancel event + handle abort + progress flush | queue/lease/relay still authoritative | same | cancel terminal sync + quota finalize false；失败标 `terminal_sync_pending` | `qa_tasks.py:624-681`, `1043-1112`, `2417-2431` |
| recovery | provisioning read repair creates public-service turn and moves queued | queue store + distributed lock if Redis | same lock may degrade if Redis unavailable | pending progress/terminal reconcile on task/conversation reads | `qa_tasks.py:692-823`, `779-823`, `873-950` |

状态机实测代码顺序：

```text
provisioning
  -> queued
  -> admitted
  -> running
  -> completed | failed | cancelled/canceled | expired
```

状态命名风险：内部多处使用 `cancelled`，public 输出 normalize 为 `canceled`。`normalize_public_task_status()` 在 `execution_admission.py:55-57` 做映射；`ExecutionQueueStatusStore` terminal 集合和 relay terminal 集合同时接受 canceled/cancelled。

### F. `file_context_resolver` 规则顺序拆解

| 顺序 | 规则 | 输入字段 | 输出/策略 | 证据 |
|---|---|---|---|---|
| 0 | 构造 active file universe | `available_files`, `pdf_context.selected_ids/newly_uploaded_ids/all_available_ids/last_focus_ids/last_turn_route` | `file_map`, `known_selected_ids`, `newly_uploaded_ids`, `reference_ids`, `candidate_ids` | `file_context_resolver.py:235-255` |
| 1 | 空问题 | `question` 空 | `kb_only`, 保留 selected ids | `256-257`, `518-525` |
| 2 | explicit file mention | `#n` | `explicit_ref` -> `_resolved_file_turn`; out-of-range clarify | `260`, `309-324`, `1015-1043` |
| 3 | ordinal reference | 第 N/前 N/后 N/倒数第 N | `ordinal_ref`; unresolved clarify | `261-263`, `326-340`, `1045-1068` |
| 4 | deictic count | “这 N 篇/个” 且 N 等于 candidates 数 | `deictic_count_scope` | `263`, `342-349`, `1070-1081` |
| 5 | selected files action | selected ids + action/ref pattern | `selected_scope`; stale selected -> clarify | `272-284`, `351-365`, `975-987` |
| 6 | DOI guard | DOI pattern without strong file/table/name focus | `kb_only` | `285-296`, `1121-1122` |
| 7 | classifier ambiguity fallback | classifier enabled + known selected ids + no deterministic file intent | `classifier_resolved` or no-op | `367-377`, `758-803`; default classifier `NoopRouteClassifier` at `route_classifier.py:37-39` |
| 8 | generic knowledge guard | generic file words but no strong intent/table/name | `kb_only` | `379-380` |
| 9 | plural scope | “这些/所有/all files/papers” | `plural_scope` over candidate ids | `382-389` |
| 10 | recent upload | “最新上传/刚上传/latest uploaded” | last newly uploaded id or clarify | `391-406` |
| 11 | table singular | selected table -> last focus table -> single table candidate -> multi clarify -> no table clarify | `408-460` |
| 12 | singular file | selected single -> last focus when last route file -> single candidate -> multi clarify -> none clarify | `462-500` |
| 13 | table/name metadata focus | table operation/column/file name token | `metadata_focus_scope` | `502-514`, `942-973` |
| 14 | default | none matched | `kb_only` | `516` |
| 15 | processing/failed/ready/storage gate | `_file_turn` and `_resolved_file_turn` call `_selection_status()` | `FILE_NOT_FOUND`, `FILE_PROCESSING_FAILED`, `FILE_NOT_READY`, `FILE_STORAGE_REF_MISSING`, `FILE_STORAGE_REF_NOT_MINIO`; status turns have `execution_files=[]` | `564-628`, `630-668`, `824-872` |
| 16 | pdf/table/hybrid route | selected family and table focus | `pdf_qa`, `tabular_qa`, `hybrid_qa`; mixed later normalized by `RouteDecisionService` | `917-940`; `route_decision.py:67-114` |
| 17 | clarification payload | candidate rows summarize display/file status | `clarify_candidates` | `545-562`, `724-747` |

Storage/ready key behavior:

```python
if self._is_file_failed(row):
    return {"code": "FILE_PROCESSING_FAILED", ...}
if not row.is_ready:
    return {"code": "FILE_NOT_READY", ...}
if self._strict_minio_only() and not row.has_minio_storage_ref:
    storage_reason = "storage_ref_missing" if not str(row.storage_ref or "").strip() else "storage_ref_not_minio"
    status_code = "FILE_STORAGE_REF_MISSING" if storage_reason == "storage_ref_missing" else "FILE_STORAGE_REF_NOT_MINIO"
```

`gateway/app/services/file_context_resolver.py:835-868`

### G. legacy/deprecated/scaffold 引用验证

| 标记/对象 | router 注册 | import | script | test | frontend/gateway 调用 | 结论 |
|---|---|---|---|---|---|---|
| `/api/v1/{mode}/ask*` compat | 是，`qa.py:834-867` | n/a | README/脚本无特殊 | `test_qa_proxy.py` 多处覆盖 | frontend `api.js:626-630` 用 `/api/{mode}/ask_stream`，`V1` 实际为 `/api` | active compatibility |
| `/api/ask`, `/api/ask_stream`, `/api/v1/ask`, `/api/v1/ask_stream` | 未在 `qa.py` 注册 | n/a | README 仍声称 `/api/ask` 和 `/api/ask_stream` remain compatibility aliases | tests `test_v1_ask_stream_alias_is_removed`, `test_v1_ask_alias_is_removed`, `test_ask_alias_is_removed` | frontend fallback 仍存在：`api.js:626-628`, `src/api/chat.js:78-80`, README fallback | gateway removed；frontend/README 残留需清理或确认 |
| `legacy-users` | 是，`public_proxy.py:297` | n/a | 无 | `test_route_table.py:31`, `test_public_proxy.py:676-677` | frontend `frontend-vue/src/services/admin.js:593` | active；不能删除 |
| `execution_admission.py` “infra-only scaffolding” 注释 | admission router active；tasks immediate dispatch active | `main.py:23`, `qa_tasks.py:23-29`, `admission.py:15` | `run_admission_worker_foreground.sh`, `start_admission_worker.sh` | `test_execution_admission.py`, `test_task_api.py`, script tests | task live path uses dispatcher/worker | 注释过期；模块 active |
| `NoopRouteClassifier` scaffold | resolver initialized with Noop in `main.py:63-70` | `main.py`, `file_context_resolver.py` | 无 | `test_route_classifier.py`, `test_file_context_resolver.py` | active default; no non-noop factory | scaffold active default |
| `conversation_persistence.extract_stream/persist_user_message/persist_assistant_summary` | 未注册 route | service mounted in `main.py:77` | 无 | summary parser test exists | gateway source未见 direct caller；task methods active | old ask_stream persistence APIs unknown/live caller not confirmed |
| `REDIS_ENABLED` env | n/a | config tests mention | scripts load infra env | tests assert mandatory Redis/admission | `GatewaySettings.from_env()` hard-codes `redis_enabled=True` | env flag legacy/ignored by design per tests |
| `.pytest_cache`, `__pycache__` | n/a | n/a | n/a | n/a | generated files present in `find` | generated runtime artifacts, not behavior source |

### H. 测试覆盖补充和缺口

| 区域 | 已覆盖 | 缺口 |
|---|---|---|
| config/env | `test_config.py`, `test_config_env_loader.py` 覆盖 backend endpoint warning、strict、patent flag、mandatory Redis/admission、runtime role、env loader | README 与代码兼容 alias 不一致缺文档约束测试；Redis hard-on 的运维说明缺 contract |
| route table/public proxy | `test_public_proxy.py`, `test_route_table.py` 覆盖 route 注册、stream upload/download/view_pdf、legacy rewrite/internal quota endpoint blocking、active_task enrichment | public proxy 表很大，缺自动生成 route inventory/compat 标记校验 |
| QA direct proxy | `test_qa_proxy.py` 覆盖 quota precheck/finalize/abort、SSE passthrough、upstream error、file context short-circuit、alias removal、provider failure、patent gating | 缺“首 chunk 不等待 quota finalize”的明确 latency regression；缺 direct path conversation-persistence negative contract 的集中 snapshot |
| file resolver/decision | `test_file_context_resolver.py`, `test_route_decision.py`, `test_mixed_conversation_context.py` 覆盖 DOI、ordinal、latest、selected、last focus、table/name focus、MinIO-only、classifier threshold | 规则多但没有单一 ordered-rule snapshot；重构时容易改变优先级 |
| tasks/admission | `test_task_api.py`, `test_refresh_survivable_task_e2e.py`, `test_execution_admission.py`, `test_execution_queue_status.py`, `test_execution_event_relay.py`, `test_execution_slot_leases.py` 覆盖 create/cancel/recover/replay/progress/terminal/quota/lease/dirty index | 缺真实 Redis 多进程集成；worker + web 并发取消/complete race 需要集成层 |
| auth/quota/public clients | `test_task_api.py`, `test_qa_proxy.py`, `test_provider_factory.py` 覆盖 auth me、internal quota、conversation file provider | 缺 public-service internal contract schema tests，当前依赖 mock path/assert |
| scripts | `test_admission_worker_scripts.py` 覆盖 worker env and startup stability | gunicorn scripts 未见同等深度测试；stop scripts 包含 kill/fuser，未运行验证 |

### I. 新增重构点

以下 `R-009` 至 `R-022` 均为第二轮深度补充，所属服务均为 `gateway`。每个条目的 `当前状态` 以对应条目中的接口路径和调用链为准：QA/task/public-proxy/admission 路径为 `active live path`，README/compat 差异为 `deprecated but referenced` 或 `unknown，需要进一步验证`。本节所有条目均按第二轮模板补充代码位置、行号范围、接口路径、当前调用链、关键代码片段、目标结构、迁移步骤、兼容/回滚、测试计划、风险和阻塞项。

### R-009：README/前端 fallback 与 gateway alias removal 不一致

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：deprecated but referenced / contract drift

- 严重程度：P1
- 类型：契约漂移 / compat 文档债务
- 代码位置和行号范围：
  - `gateway/README.md:55-60`
  - `gateway/app/routers/qa.py:834-867`
  - `gateway/tests/test_qa_proxy.py:2409`, `3041`, `3059`
  - `frontend-vue/src/services/api.js:626-630`
- 接口路径：`/api/ask`、`/api/ask_stream`、`/api/v1/ask`、`/api/v1/ask_stream`、`/api/{mode}/ask*`、`/api/v1/{mode}/ask*`
- 当前调用链：
  - frontend fallback may build `/api/ask_stream` when normalized mode is not fast/thinking/patent。
  - gateway only registers `/api/{mode}/ask*` and `/api/v1/{mode}/ask*`。
  - tests assert old no-mode aliases are removed。
- <=40 行关键片段：

```python
@router.post("/api/fast/ask_stream")
@router.post("/api/v1/fast/ask_stream")
async def ask_stream_fast(payload: AskRequest, request: Request):
    return await _proxy_ask_stream(request, payload, "fast")
```

```js
const askPath = ['fast', 'thinking', 'patent'].includes(normalizedMode)
  ? `${V1}/${normalizedMode}/ask_stream`
  : `${V1}/ask_stream`;
```

- 目标结构：建立 `GatewayRouteContract` 或生成式 route inventory，明确 `canonical`、`compat-active`、`removed` 三类；README/frontend fallback/tests 共用一份契约。
- 迁移步骤：
  1. 从 FastAPI route table 生成当前 registered paths。
  2. 给 no-mode ask aliases 标记 `removed`，给 mode/v1 aliases 标记 `compat-active`。
  3. 更新 frontend fallback 决策为 compile-time impossible 或显示错误。
  4. 更新 README 的 Current Behavior。
- 兼容/回滚：若必须恢复 `/api/ask_stream`，只能通过显式 route 加回并补 tests；默认不恢复。
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：route contract parser。
  - contract：registered route inventory snapshot。
  - router：继续保留 removed alias 404 tests。
  - stream：fallback mode 不应发起无 mode stream。
  - integration：frontend ask path structure test 更新。
  - regression：README example 与 route inventory diff fail。
- 风险：中高。前端某些异常 mode 值可能仍落 fallback，线上会 404。
- 阻塞项：需确认 frontend `normalizedMode` 所有来源是否保证三值枚举。

### R-010：`GatewaySettings.from_env()` 忽略 `REDIS_ENABLED`/admission disabled 语义但 scripts/tests 仍出现 disabled 概念

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P1
- 类型：配置契约歧义 / 运维风险
- 代码位置和行号范围：
  - `gateway/app/core/config.py:121-123`, `144-160`
  - `gateway/app/integrations/redis/service.py:402-410`
  - `gateway/tests/test_config.py` 中 `test_gateway_settings_keep_mandatory_redis_and_admission_enabled`
  - `gateway/tests/test_execution_admission.py` 中 `test_run_admission_worker_ignores_disabled_env_and_requires_shared_redis`
- 接口路径：`/healthz`、`/api/v1/tasks*`、`/api/admission/*`
- 当前调用链：
  - env loader 可加载 shared infra env。
  - `GatewaySettings.from_env()` 直接 `redis_enabled=True`、`admission_enabled=True`、`dispatcher_enabled=True`。
  - Redis bootstrap 支持 disabled status，但 gateway settings 不会进入 disabled 分支。
- <=40 行关键片段：

```python
redis_enabled = True
gateway_runtime_role = str(os.getenv("GATEWAY_RUNTIME_ROLE", "web") or "web").strip().lower() or "web"
admission_enabled = True
...
redis=RedisSettings(
    enabled=redis_enabled,
    url=str(os.getenv("REDIS_URL", "") or "").strip(),
...
admission=AdmissionSettings(
    enabled=admission_enabled,
    runtime_role=gateway_runtime_role,
    dispatcher_enabled=True,
```

- 目标结构：将“mandatory shared Redis/admission”显式命名为 policy，例如 `GatewayRuntimePolicy(shared_state_required=True)`；不要保留看似可关闭但实际忽略的 env contract。
- 迁移步骤：
  1. 文档和 config tests 改名为 mandatory policy。
  2. `RedisSettings.enabled` 若保留，仅用于底层 library；gateway settings 明确不读 `REDIS_ENABLED`。
  3. `/healthz` 输出增加 `config_policy`，解释 disabled env ignored。
  4. admission worker startup log 同步。
- 兼容/回滚：保留 `RedisSettings.enabled` 底层能力；不在 gateway web/worker 暴露关闭行为。
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：`from_env()` 对 `REDIS_ENABLED=0` 仍 mandatory。
  - contract：health `shared_state_required=true`。
  - router：admission status degraded when Redis probe false。
  - stream：task event stream在 Redis unavailable 情况的 memory fallback 仅 web-local。
  - integration：worker refuses Redis unavailable。
  - regression：env loader 不误导 `REDIS_ENABLED`。
- 风险：中。运维误以为可关闭 Redis，但 worker 需要 shared Redis。
- 阻塞项：需产品/运维确认是否接受 mandatory Redis 策略。

### R-011：`component_status` 是启动快照，health 又动态覆盖，状态来源混杂

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P2
- 类型：可观测性/生命周期边界
- 代码位置和行号范围：
  - `gateway/app/main.py:78-89`
  - `gateway/app/routers/health.py:24-50`
- 接口路径：`/healthz`
- 当前调用链：
  - create_app 时写 `app.state.component_status`。
  - `/healthz` 复制启动快照后动态覆盖 redis/admission/queue/relay/slot。
  - `conversation_file_provider` 另以顶层字段输出。
- <=40 行关键片段：

```python
components = dict(getattr(request.app.state, "component_status", {}))
redis_status = dict(request.app.state.redis_runtime.status.to_dict())
redis_status["live_available"] = bool(request.app.state.redis_runtime.service.probe())
components["redis"] = redis_status
components["admission"] = build_admission_status(...)
components["queue_status_store"] = queue_store.describe()
components["event_relay_store"] = relay_store.describe()
components["slot_lease_store"] = slot_lease_store.describe()
```

- 目标结构：`ComponentStatusRegistry` 统一注册 provider 函数，health 只调用 registry；启动快照另命名 `boot_status`。
- 迁移步骤：
  1. 提取 `build_health_payload(container)`。
  2. 将 static boot warnings 与 live metrics 分区。
  3. 给每个 component 定义 `probe()`/`describe()`。
- 兼容/回滚：保留 `/healthz` 字段名；新增字段只扩展。
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：registry provider 输出。
  - contract：health payload snapshot。
  - router：`/healthz` still 200 with upstream failures。
  - stream：无。
  - integration：Redis unavailable health degraded。
  - regression：existing test_health assertions unchanged。
- 风险：低中。字段移动会影响监控。
- 阻塞项：需确认监控使用哪些 JSON path。

### R-012：`public_proxy.py` route table 动态注册但缺 canonical/compat/internal 分类

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P1
- 类型：路由契约债务 / 安全边界风险
- 代码位置和行号范围：
  - `gateway/app/routers/public_proxy.py:210-317`
  - `gateway/app/services/proxy.py:42-47`, `325-329`
- 接口路径：全部 public proxy routes，尤其 `/api/quota/*` 与 public upload/view/download streaming routes
- 当前调用链：
  - `_ROUTE_SPECS` 动态 add route。
  - `_proxy_public()` 忽略 path params，只转发 request。
  - `ProxyService` 仅少数 public path rewrite。
- <=40 行关键片段：

```python
_ROUTE_SPECS = (
    (_paths("/api/auth/login"), ("POST",)),
    ...
    (_paths("/api/quota/reset/{user_id}/{quota_type:path}"), ("POST",)),
    (_paths("/api/admin/model-status", include_v1=False), ("GET",)),
    ...
)

for paths, methods in _ROUTE_SPECS:
    for path in paths:
        router.add_api_route(path, _proxy_public, methods=list(methods), name=_route_name(path, methods))
```

- 目标结构：声明式 `PublicRouteSpec(path, methods, aliases, backend_path, auth_policy, streaming_policy, category, lifecycle)`。
- 迁移步骤：
  1. 将 tuple route specs 转 dataclass。
  2. 显式标注 internal blocked routes，替代靠 absence。
  3. 生成 docs/test snapshot。
  4. 把 `_is_streaming_route` 与 `_PUBLIC_PATH_REWRITES` 合并入 spec。
- 兼容/回滚：保持 `router.add_api_route` 输出路径和 route names；spec 可回退为旧 tuple 生成。
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：spec expansion includes v1 aliases where expected。
  - contract：route table snapshot。
  - router：internal quota grant endpoints not exposed。
  - stream：upload/download/view_pdf/patent original fulltext streaming。
  - integration：frontend admin/auth/conversation paths。
  - regression：legacy-users remains registered until frontend removed。
- 风险：中。动态注册容易漏 alias 或误开放 internal endpoint。
- 阻塞项：需 public-service route authority list。

### R-013：`public_proxy` 在 conversation read path 扫描全量 queue 注入 `active_task`

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P1
- 类型：性能/边界越界
- 代码位置和行号范围：
  - `gateway/app/routers/public_proxy.py:49-69`, `113-195`
  - `gateway/app/services/qa_tasks.py:683-690`, `1295-1327`
- 接口路径：`GET /api/conversations`, `GET /api/v1/conversations`, `GET /api/conversations/{conversation_id}`, `GET /api/v1/conversations/{conversation_id}`
- 当前调用链：
  - proxy public conversation response。
  - decode JSON。
  - scan `queue_store.list_requests()`。
  - instantiate `QATaskService(request)` and build summaries。
  - inject `active_task` into public-service payload。
- <=40 行关键片段：

```python
def _live_task_summary_by_conversation(request: Request) -> dict[int, dict]:
    queue_store = request.app.state.execution_queue_status_store
    task_service = QATaskService(request)
    chosen: dict[int, dict] = {}
    for record in queue_store.list_requests():
        ...
        if str(record.get("status") or "").strip().lower() not in _LIVE_PUBLIC_TASK_STATUSES:
            continue
        ...
        summaries[conversation_id] = task_service.build_task_summary(request_id)
    return summaries
```

- 目标结构：`ConversationActiveTaskEnricher` + indexed lookup by conversation_id，或前端独立拉 `/api/v1/tasks?conversation_id=...`。
- 迁移步骤：
  1. 给 queue store 增加 conversation live index port。
  2. 抽 enrichment service 并注入 public proxy。
  3. 明确 active_task schema contract。
  4. 评估迁往 public-service authority 或 frontend composition。
- 兼容/回滚：保留 response `data.active_task` 和 list item `active_task`；新 index 可 fallback list scan。
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：enricher picks newest live task。
  - contract：conversation list/detail active_task payload。
  - router：public proxy still enriches only GET list/detail。
  - stream：无。
  - integration：refresh-survivable e2e。
  - regression：large queue list scan benchmark or metric。
- 风险：中高。队列大时 conversation list hot path 读放大；边界不清导致 public payload 非 authority 原样。
- 阻塞项：需确认 active_task 长期归属 gateway 还是 public-service。

### R-014：`QATaskService` 和 `GatewayTaskExecutor` 重复 progress accumulator 与 state/event helper

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P1
- 类型：重复状态逻辑 / 一致性风险
- 代码位置和行号范围：
  - `gateway/app/services/qa_tasks.py:962-1012`
  - `gateway/app/services/qa_tasks.py:1415-1498`
  - `gateway/app/services/qa_tasks.py:1276-1293`, `2381-2395`
- 接口路径：`/api/v1/tasks*`
- 当前调用链：
  - service cancel/read/reconcile 和 executor runtime 分别实现 progress accumulator/state frame helper。
  - executor 多了 inflight buffering，service 版无 inflight。
- <=40 行关键片段：

```python
def _new_progress_accumulator(self, *, persisted_last_seq: int = 0) -> dict[str, Any]:
    return {
        "status": "running",
        "pending_content_delta": "",
        "pending_content_events": 0,
        "observed_last_seq": max(0, int(persisted_last_seq)),
        "persisted_last_seq": max(0, int(persisted_last_seq)),
        "latest_steps": [],
        "dirty": False,
        "last_flush_monotonic": time.monotonic(),
    }
```

- 目标结构：`TaskProgressAccumulator` value object + `TaskEventPublisher` service。
- 迁移步骤：
  1. 抽纯类，保持 dict serialization adapter。
  2. 用 shared accumulator 替换 service/executor 两份。
  3. 抽 state frame append/idempotency。
  4. 增加 golden tests 覆盖 overlapping flush。
- 兼容/回滚：保留 public event payload 和 queue fields；内部类可 behind adapter。
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：accumulator flush/inflight failure。
  - contract：progress_sync_payload shape。
  - router：task events replay unchanged。
  - stream：content batching, idle flush, cancel flush。
  - integration：refresh-survivable e2e。
  - regression：existing overlapping flush tests。
- 风险：高。重复逻辑正覆盖复杂 cancel/progress race，抽取易破坏。
- 阻塞项：先冻结 progress accumulator behavioral snapshot。

### R-015：task create 的 public-service side effects、queue record、quota grant 缺显式 Saga/Unit-of-Work

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P0
- 类型：一致性/补偿路径风险
- 代码位置和行号范围：
  - `gateway/app/services/qa_tasks.py:291-523`
  - `gateway/app/services/qa_tasks.py:1182-1228`
  - `gateway/app/services/conversation_persistence.py:161-270`
- 接口路径：`POST /api/v1/tasks`
- 当前调用链：
  - quota precheck。
  - provisional queue `provisioning`。
  - public-service create task turn。
  - queue update `queued`。
  - failures rollback turn/delete queue/finalize quota false。
- <=40 行关键片段：

```python
precheck = await quota_proxy.precheck(...)
quota_grant_id = str(grant_data.get("grant_id") or "").strip()
record = {"request_id": task_id, "status": "provisioning", ...}
stored = self.queue_store.put_request(record, ttl_seconds=...)
created_turn = await persistence_service.create_task_turn(...)
updated_record = dict(record)
updated_record["status"] = "queued"
updated_record["cancel_allowed"] = True
stored = self.queue_store.put_request(updated_record, ttl_seconds=...)
```

- 目标结构：`TaskCreationSaga` with steps `reserve_quota -> write_provisional -> create_turn -> publish_queued -> commit`, compensation table.
- 迁移步骤：
  1. 抽每个 side effect 为 idempotent command。
  2. Saga state 写入 queue record。
  3. 补 `provisioning` recovery 只读取 saga state。
  4. 将 rollback failures 转 terminal repair queue。
- 兼容/回滚：保留 `provisioning` record 和 existing recovery method；新 saga 可 shadow log。
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：每个 step/compensation。
  - contract：public-service create-turn/rollback payload。
  - router：create returns same summary。
  - stream：n/a。
  - integration：queue write fail/public-service fail/quota fail。
  - regression：`test_create_task_*rollback*`, `provisioning_recover`。
- 风险：P0。错误会造成 orphan user/assistant message、quota grant 泄漏或 task 卡 provisioning。
- 阻塞项：public-service internal idempotency contract 需固定。

### R-016：admission worker 在 `SimpleNamespace` runtime app 中重建依赖，绕过 web composition root

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P1
- 类型：composition root 分叉 / 行为漂移
- 代码位置和行号范围：
  - `gateway/app/services/execution_admission.py:917-1023`
  - `gateway/app/main.py:39-96`
- 接口路径：admission worker executes `/api/{actual_mode}/ask_stream`; externally affects `/api/v1/tasks*`
- 当前调用链：
  - script starts `python -m app.services.execution_admission`。
  - `run_admission_worker()` builds `SimpleNamespace(state=...)` with settings, stores, registry, proxy, persistence, quota。
  - does not initialize conversation_file_service/file_context_resolver/active_task_streams/auth/component_status because worker execution does not need them after queue record exists。
- <=40 行关键片段：

```python
runtime_app = SimpleNamespace(
    state=SimpleNamespace(
        settings=settings,
        execution_queue_status_store=ExecutionQueueStatusStore(redis_service=redis_runtime.service),
        execution_event_relay_store=None,
        execution_slot_lease_store=ExecutionSlotLeaseStore(redis_service=redis_runtime.service),
        backend_registry=BackendRegistry(settings),
        proxy_service=ProxyService(settings),
        conversation_persistence_service=ConversationPersistenceService(settings),
        quota_proxy_service=QuotaProxyService(settings),
    )
)
runtime_app.state.execution_event_relay_store = ExecutionEventRelayStore(redis_service=redis_runtime.service)
effective_executor = GatewayTaskExecutor(runtime_app).execute
```

- 目标结构：shared `build_gateway_runtime(settings, redis_runtime, role)` returns typed container for web/worker with role-specific services.
- 迁移步骤：
  1. Introduce container dataclass without changing app.state aliases.
  2. Use same builder in `main.create_app()` and `run_admission_worker()`.
  3. Explicitly mark worker-required subset.
  4. Add parity tests between web and worker service wiring.
- 兼容/回滚：keep `SimpleNamespace` fallback during transition; worker executor constructor accepts protocol.
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：container builds web/worker subsets。
  - contract：worker has all `GatewayTaskExecutor` required attrs。
  - router：no change。
  - stream：worker stream execution still works。
  - integration：script worker uses same endpoints/timeouts。
  - regression：admission worker script tests。
- 风险：中高。worker drift can silently miss future dependency changes.
- 阻塞项：need typed service protocol for `GatewayTaskExecutor`.

### R-017：direct `qa.py` 和 task executor 各自实现 SSE parse/error/quota/finalize 分支

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P1
- 类型：streaming 行为分叉 / quota 幂等风险
- 代码位置和行号范围：
  - `gateway/app/routers/qa.py:226-353`, `723-831`
  - `gateway/app/services/qa_tasks.py:1829-2118`, `2296-2304`
  - `gateway/app/services/sse_frames.py:1-47`
- 接口路径：`/api/{mode}/ask_stream`, `/api/v1/{mode}/ask_stream`, `/api/v1/tasks/{task_id}/events`
- 当前调用链：
  - direct path parses SSE only to withhold done and inject quota。
  - task executor parses SSE to relay every event, accumulate content/steps, progress sync, terminal sync, quota finalize。
- <=40 行关键片段：

```python
for frame in frame_buffer.feed(chunk):
    payload, prefix_lines = parse_sse_json_frame(frame)
    if isinstance(payload, dict):
        payload_type = str(payload.get("type") or "").strip().lower()
        if payload_type == "done":
            done_payload = payload
            done_prefix_lines = prefix_lines
            continue
    outbound_frames.append(frame)
...
finalize_result = await asyncio.shield(quota_proxy.finalize(...))
done_payload["quota"] = _quota_payload_from_finalize(...)
```

- 目标结构：`GatewaySSEConsumer` with hooks: `on_metadata`, `on_step`, `on_content`, `on_done`, `on_error`, `on_stream_error`, plus `QuotaGrantLifecycle`.
- 迁移步骤：
  1. Extract parse loop without behavior change.
  2. Implement direct stream adapter.
  3. Implement task relay/persistence adapter.
  4. Add golden event fixtures for both adapters.
- 兼容/回滚：old loops can remain behind feature flag; output byte format compared in tests.
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：SSE parser edge frames/chunk boundaries。
  - contract：done quota payload schema。
  - router：direct stream routes。
  - stream：first-byte, done missing, upstream error event, timeout, client close。
  - integration：task worker event relay。
  - regression：quota count once and abort on failures。
- 风险：高。SSE first-token behavior and quota count semantics are sensitive.
- 阻塞项：define exact byte-level SSE compatibility needs.

### R-018：route classifier 配置存在，但 provider factory 只有 noop，classifier rollout 边界不完整

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：scaffold / unknown，需要进一步验证

- 严重程度：P2
- 类型：scaffold active default / feature flag debt
- 代码位置和行号范围：
  - `gateway/app/core/config.py:86-92`, `171-176`
  - `gateway/app/main.py:63-70`
  - `gateway/app/services/route_classifier.py:7-39`
  - `gateway/app/services/file_context_resolver.py:758-803`
- 接口路径：all QA routes and task create route resolution
- 当前调用链：
  - config reads `GATEWAY_ROUTE_CLASSIFIER_ENABLED` and provider string。
  - main ignores provider value and injects `NoopRouteClassifier()`。
  - resolver only calls classifier for ambiguity after rule layer。
- <=40 行关键片段：

```python
app.state.file_context_resolver = FileContextResolver(
    route_classifier=NoopRouteClassifier(),
    classifier_enabled=settings.route_classifier.enabled,
    classifier_policy=ClassifierThresholdPolicy(
        high_confidence=settings.route_classifier.high_confidence_threshold,
        medium_confidence=settings.route_classifier.medium_confidence_threshold,
    ),
)
```

- 目标结构：`build_route_classifier(settings)` provider factory; unknown provider should fail fast or degrade explicitly with health warning.
- 迁移步骤：
  1. Add factory returning noop only for provider `noop`。
  2. If enabled + noop, expose health warning `route_classifier_noop_enabled`。
  3. Add provider interface contract tests。
  4. Only then implement real classifier adapter。
- 兼容/回滚：default remains noop disabled；enabled noop remains allowed in dev/test if documented.
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：threshold policy and factory。
  - contract：classifier decision shape。
  - router：deterministic rules bypass classifier。
  - stream：same route decisions in ask_stream。
  - integration：health warning。
  - regression：existing resolver classifier tests。
- 风险：中。Operators may enable classifier believing it is active, but noop returns None.
- 阻塞项：real classifier provider not present in gateway.

### R-019：`ConversationPersistenceService` 同时承载 task internal client、legacy message writer、SSE summary parser

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path / deprecated but referenced

- 严重程度：P2
- 类型：边界混合 / 可抽共享 client
- 代码位置和行号范围：
  - `gateway/app/services/conversation_persistence.py:75-111`, `119-336`, `338-424`, `532-616`
  - `gateway/app/services/qa_tasks.py:397-409`, `650-660`, `2020-2031`
- 接口路径：`/api/v1/tasks*`; old direct ask_stream persistence caller unknown
- 当前调用链：
  - task create/progress/terminal/rollback call public-service `/internal/conversations/...` endpoints。
  - legacy `persist_user_message/persist_assistant_summary` posts public `/api/v1/conversations/{id}/messages`。
  - `extract_stream` parses SSE summaries but direct `qa.py` does not call it。
- <=40 行关键片段：

```python
async def terminal_task_assistant(...):
    payload = {
        "conversation_id": cid,
        "user_id": uid,
        "task_id": str(task_id or "").strip(),
        "terminal_status": str(terminal_status or "failed").strip() or "failed",
        "last_seq": max(0, int(last_seq)),
        "answer_text": str(answer_text or ""),
        "steps": list(steps or []),
        "failure": dict(failure or {}),
        "timings": dict(timings or {}),
    }
    return await self._post_internal_json(
        path=f"/internal/conversations/{cid}/tasks/{str(task_id or '').strip()}/assistant-terminal",
        payload=payload,
    )
```

- 目标结构：`ConversationTaskClient`, `ConversationMessageClient`, `AskStreamSummaryExtractor` 三分。
- 迁移步骤：
  1. Move internal task endpoints to typed client。
  2. Keep public message writer separate and mark legacy caller status。
  3. Extract stream summary parser to pure module。
  4. Search/remove old APIs only after caller map proves unused。
- 兼容/回滚：retain `ConversationPersistenceService` facade delegating to new clients.
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：summary extractor and payload builders。
  - contract：internal conversation task endpoints schema。
  - router：task create/cancel/get recovery。
  - stream：summary parser golden frames。
  - integration：public-service internal mock。
  - regression：pending terminal/progress reconcile。
- 风险：中。拆错会影响 task persistence or old ask_stream tests.
- 阻塞项：old direct persistence caller仍需全仓确认；本轮 gateway 内未确认 live caller。

### R-020：`GatewayTaskExecutor._build_internal_request()` 手工构造 FastAPI Request，耦合 headers/auth/internal token

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P1
- 类型：测试替身泄漏到生产路径 / auth header contract 风险
- 代码位置和行号范围：
  - `gateway/app/services/qa_tasks.py:2120-2162`
  - `gateway/app/services/proxy.py:284-294`
  - `gateway/app/services/quota_proxy.py:109-126`
- 接口路径：worker execution to `/api/{actual_mode}/ask_stream`; indirectly `/api/v1/tasks/{task_id}/events`
- 当前调用链：
  - worker has no incoming browser Request。
  - executor creates `Request(scope, receive=...)` with internal/gateway-owned headers and optional saved downstream Authorization。
  - ProxyService and QuotaProxyService read headers/trace from this Request。
- <=40 行关键片段：

```python
headers = [
    (b"accept", b"text/event-stream"),
    (b"content-type", b"application/json"),
    (b"x-gateway-task-execution", b"1"),
    (b"x-gateway-owned-persistence", b"1"),
    (b"x-internal-service-name", b"gateway"),
    (b"x-internal-service-token", str(internal_token or "").encode("utf-8")),
]
authorization = str(downstream_authorization or "").strip()
if authorization:
    headers.append((b"authorization", authorization.encode("utf-8")))
scope = {"type": "http", "method": "POST", "path": f"/api/{...}/ask_stream", "state": {"trace_id": str(trace_id or "").strip()}, "app": self.app}
return Request(scope, receive=_receive)
```

- 目标结构：`GatewayExecutionContext` value object passed to proxy/quota/conversation clients instead of synthetic Request.
- 迁移步骤：
  1. Define `RequestContext(trace_id, headers, app)` protocol。
  2. Update ProxyService/QuotaProxyService/ConversationPersistenceService to accept context protocol or Request。
  3. Replace synthetic Request in executor。
  4. Keep adapter for FastAPI Request at router boundary。
- 兼容/回滚：services support both FastAPI Request and context during migration.
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：context header building。
  - contract：Authorization/internal headers forwarded exactly。
  - router：direct request still works。
  - stream：worker stream opens with patent capability header。
  - integration：admission worker saved authorization path。
  - regression：`test_admission_worker_executes_thinking_task_stream_with_saved_authorization_header`, patent capability tests。
- 风险：中高。Header loss can break auth, persistence authority, or backend gateway-owned persistence behavior.
- 阻塞项：clients currently accept FastAPI `Request`; needs protocol refactor.

### R-021：Redis stores hand-roll dirty index repair in three modules without shared index-maintenance abstraction

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P1
- 类型：基础设施重复 / consistency risk
- 代码位置和行号范围：
  - `gateway/app/services/execution_queue_status.py:292-347`, `568-608`
  - `gateway/app/services/execution_event_relay.py:100-170`, `486-523`
  - `gateway/app/services/execution_slot_leases.py:107-160`, `359-394`
- 接口路径：`/api/v1/tasks*`, `/api/admission/*`, `/healthz`
- 当前调用链：
  - put/append/acquire mark dirty。
  - index writes attempt consistency check。
  - describe path may rebuild indexes synchronously。
- <=40 行关键片段：

```python
def _redis_dirty(self) -> bool:
    if not self.redis_service.available:
        return False
    return self.redis_service.get_int(self.dirty_flag_key(), default=0) > self.redis_service.get_int(
        self.clean_version_key(),
        default=0,
    )

def describe(self) -> dict[str, Any]:
    if self.redis_service.available:
        if self._redis_dirty():
            self._rebuild_redis_indexes()
```

- 目标结构：shared `RedisIndexedStoreMaintenance` helper with dirty version, rebuild lock, scan budget, metrics.
- 迁移步骤：
  1. Extract dirty version helper。
  2. Add rebuild lock to avoid concurrent health/request rebuild storms。
  3. Add max scan budget or background repair option。
  4. Keep store-specific rebuild callbacks。
- 兼容/回滚：helper delegates to existing rebuild methods; disable via feature flag if needed.
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：dirty version does not clear newer mutation。
  - contract：describe output unchanged。
  - router：health does not block excessively。
  - stream：event relay replay after dirty repair。
  - integration：Redis partial write repair。
  - regression：existing dirty-index tests in queue/relay/lease。
- 风险：高. Repair on hot health/read paths can cause latency spikes; shared helper bug affects all task state.
- 阻塞项：need performance budget and Redis key cardinality expectations.

### R-022：task API create flag 只 gate POST，read/events/cancel 保持可用但契约散落在 tests

- 来源：第二轮深度补充
- 所属服务：gateway
- 当前状态：active live path

- 严重程度：P2
- 类型：rollout policy 隐式化
- 代码位置和行号范围：
  - `gateway/app/routers/tasks.py:15-60`
  - `gateway/tests/test_task_api.py:382`, `487`
  - `gateway/app/core/config.py:177-178`
- 接口路径：`/api/v1/tasks`, `/api/v1/tasks/{task_id}`, `/api/v1/tasks/{task_id}/events`, `/api/v1/tasks/{task_id}/cancel`
- 当前调用链：
  - POST create checks `refresh_survivable_qa_tasks_enabled`。
  - GET/events/cancel do not check flag and operate on existing records。
  - tests explicitly expect read/cancel remain available when create flag disabled。
- <=40 行关键片段：

```python
@router.post("/api/v1/tasks")
async def create_task(...):
    if not bool(getattr(request.app.state.settings, "refresh_survivable_qa_tasks_enabled", False)):
        raise HTTPException(status_code=404, detail="task_api_disabled")
    service = QATaskService(request)
    return await service.create_task(payload, auth_context=auth_context)

@router.get("/api/v1/tasks/{task_id}")
async def get_task(...):
    service = QATaskService(request)
    await service.reconcile_pending_terminal_tasks(task_ids={task_id})
    return await service.get_task(task_id, auth_context=auth_context)
```

- 目标结构：`TaskApiRolloutPolicy` that declares per-operation policy: create gated, read/replay/cancel recovery open.
- 迁移步骤：
  1. Extract policy from router。
  2. Add `/healthz` rollout status。
  3. Document read/cancel open-for-recovery semantics。
  4. Add route tests using policy names。
- 兼容/回滚：behavior remains same; policy centralizes future flip.
- unit/contract/router/stream/integration/regression 测试计划：
  - unit：policy matrix。
  - contract：404 create disabled detail unchanged。
  - router：read/events/cancel still available。
  - stream：events stream available for existing task。
  - integration：refresh recovery after disabling create。
  - regression：existing `test_existing_task_reads_and_cancel_remain_available_when_create_flag_is_disabled`。
- 风险：中。Without explicit policy, future refactor may accidentally gate recovery endpoints and strand existing tasks.
- 阻塞项：confirm rollout intent with product/ops.

### J. 第二轮结论

1. gateway 已经不是“薄代理”：它是 QA route authority、file context rule engine、quota grant coordinator、task/admission runtime、conversation active_task BFF、public-service compatibility boundary。
2. 最大重构风险集中在 task path：`qa_tasks.py` + admission/queue/relay/lease + public-service persistence + quota finalize 形成跨进程状态机，不能用普通队列替换而不保留 replay/cancel/recover/terminal sync 语义。
3. direct ask_stream 与 refresh-survivable task stream 是两条不同 live path：前者不持久化 conversation message，后者由 gateway-owned persistence 写 public-service internal task endpoints。
4. legacy/compat 不能按名称判断：`legacy-users` 是 active；`/api/v1/{mode}/ask*` active；无 mode ask aliases 已移除但 README/frontend fallback 残留。
5. 下一轮若进入实施，建议先做只读生成式 route/state/SSE contract snapshots，再抽 container/Saga/SSE consumer，避免先动行为。

## 第三轮证据闭环补充

本轮仅做只读复核并追加审计结论，未改动 `gateway/` 下源码、配置、测试、脚本、README 或依赖文件。闭环基线为第二轮 `R-016`、`R-017`、`R-019`、`R-021` 及文末列出的未确认项；本轮重点补齐 live caller、task/live 边界、admission worker、Redis fallback、alias、active_task enrichment、quota finalize 与测试护栏。

### 1. 第二轮未确认项复核

#### gateway 未确认项闭环表

| 验证项 | 第二轮条目 | 状态 | 证据位置 | 结论 |
| --- | --- | --- | --- | --- |
| V-301 | R-019 | closed | `gateway/app/services/conversation_persistence.py:393`、`gateway/app/routers/qa.py:723`、`gateway/tests/test_qa_proxy.py:3541` | `extract_stream()` 当前没有 gateway runtime live caller；仅服务定义和测试直接调用。 |
| V-302 | R-002/R-004/R-017/R-019 | closed | `gateway/app/routers/qa.py:226`、`gateway/app/routers/qa.py:723`、`gateway/app/services/qa_tasks.py:261`、`gateway/app/services/qa_tasks.py:1702` | 普通 `/ask_stream` 只做 route/quota/proxy；task create/worker 才持久化 conversation turn/progress/terminal。 |
| V-303 | R-016/R-022 | closed | `gateway/app/services/execution_admission.py:67`、`:917`、`:955`、`gateway/app/routers/qa.py:723` | admission worker 接管 task/admission infra，不接管 direct live ask/ask_stream。 |
| V-304 | R-004/R-014/R-015/R-020 | partially closed | `gateway/app/services/qa_tasks.py:252`、`:1400`、`wc -l gateway/app/services/qa_tasks.py` | `qa_tasks.py` 可按 facade/state-machine/repository/runner/persistence/cancellation 拆，但需先补 contract/snapshot。 |
| V-305 | R-005/R-021 | partially closed | `gateway/app/services/execution_admission.py:67`、`:178`、`:643`；`execution_queue_status.py`、`execution_event_relay.py`、`execution_slot_leases.py` | 已区分业务策略与底层机制；机制抽象可做，策略不可被通用队列吞掉。 |
| V-306 | R-010/R-021 | partially closed | `gateway/app/core/config.py:121`、`:123`、`gateway/app/services/execution_admission.py:932`、`gateway/tests/test_health.py:53` | web runtime 可 memory fallback，worker Redis 失败关闭；生产 fallback 策略仍需显式文档/健康门禁。 |
| V-307 | R-003 | still open | `gateway/app/services/file_context_resolver.py:228`、`:749`、`gateway/tests/test_file_context_resolver.py` | 有场景测试，但未发现 ordered rules snapshot/golden；拆 rule 前必须补。 |
| V-308 | R-008/R-009/R-012 | partially closed | `gateway/app/routers/qa.py:854`、`:860`、`:866`；`gateway/app/routers/public_proxy.py:210`；`gateway/app/services/route_table.py:6` | `/api` 与 `/api/v1` 可收敛为 registry 生成，但当前测试明确锁定双路径兼容。 |
| V-309 | R-007/R-013 | partially closed | `gateway/app/routers/public_proxy.py:49`、`:113`、`:156`、`gateway/tests/test_public_proxy.py:83` | active_task 注入是 gateway BFF 行为，但会改变 public-service conversation read 响应，需 contract 化。 |
| V-310 | R-002/R-017 | partially closed | `gateway/app/routers/qa.py:226`、`:322`；`gateway/tests/test_qa_proxy.py:1560`、`:1653`、`gateway/tests/test_task_api.py:2284` | done 丢失/metadata/close/cancel 有护栏；direct first-byte latency 未见显式延迟断言。 |

### V-301

- 来源：第三轮只读复核，命令 `rg "extract_stream|ConversationPersistenceService|append.*assistant|assistant-terminal|assistant-progress" gateway/app gateway/tests`
- 所属服务：gateway
- 对应第二轮条目：R-019
- closed|partially closed|still open：closed
- 代码位置：`gateway/app/services/conversation_persistence.py:393` 定义 `ConversationPersistenceService.extract_stream()`；`gateway/tests/test_qa_proxy.py:3541` 直接单测；`gateway/app/main.py` 和 `gateway/app/services/execution_admission.py` 只构造 service。
- 接口路径：无直接 runtime 路径；历史意图疑似 `/api/{mode}/ask_stream`。
- 只读验证命令：`rg "extract_stream|ConversationPersistenceService|append.*assistant|assistant-terminal|assistant-progress" gateway/app gateway/tests`
- 证据摘要：`extract_stream()` 只在 service 内定义并在测试中 `async for chunk in service.extract_stream(...)`；direct stream `_proxy_ask_stream()` 调用 `_stream_with_quota()`，没有调用 conversation persistence；task path 调用的是 `create_task_turn()`、`progress_task_assistant()`、`terminal_task_assistant()`。
- 最终判定：live caller 未闭环问题已闭环；该方法当前属于 legacy/test-covered parser helper，不是 live path 依赖。
- 是否可进入重构：可进入，但先标记 deprecation 或迁入 SSE consumer 时保留单测。
- 阻塞项：无 runtime blocker。
- 必须先补测试：若删除或迁移 `extract_stream()`，先迁移 `test_gateway_stream_summary_keeps_reference_objects_from_done_event`。
- 建议实施任务：TASK-302。
- 回滚方式：保留 `ConversationPersistenceService.extract_stream()` 原签名并让新 SSE helper 代理回旧实现。

### V-302

- 来源：第三轮只读复核，命令 `sed -n '226,355p' gateway/app/routers/qa.py`、`sed -n '723,831p' gateway/app/routers/qa.py`、`sed -n '261,440p' gateway/app/services/qa_tasks.py`、`sed -n '1702,1915p' gateway/app/services/qa_tasks.py`
- 所属服务：gateway
- 对应第二轮条目：R-002/R-004/R-017/R-019
- closed|partially closed|still open：closed
- 代码位置：direct stream `gateway/app/routers/qa.py:723` `_proxy_ask_stream()`；quota wrapper `gateway/app/routers/qa.py:226` `_stream_with_quota()`；task create `gateway/app/services/qa_tasks.py:261`；task runner `gateway/app/services/qa_tasks.py:1702` `_execute_async()`。
- 接口路径：`POST /api/{mode}/ask_stream`、`POST /api/v1/{mode}/ask_stream`、`POST /api/v1/tasks`、`GET /api/v1/tasks/{task_id}/events`
- 只读验证命令：`rg "QATaskService|GatewayTaskExecutor|execution_admission|slot_lease|event_relay" gateway/app gateway/tests`
- 证据摘要：direct stream 只做 `_resolve()`、quota `precheck()`、`open_json_stream()` 和 `_stream_with_quota()`；未调用 `conversation_persistence_service`。task create 在 quota precheck 后写 `provisioning` queue record，再调用 `create_task_turn()`，随后更新为 `queued` 并 append queued event。task runner 使用 internal request 打开 `/api/{actual_mode}/ask_stream`，解析 SSE，写 relay，并通过 progress/terminal internal endpoints 同步 public-service。
- 最终判定：普通 ask/ask_stream 与 task 持久化边界已确认；不能把两者视为同一 live path。
- 是否可进入重构：可进入，但 task refactor 必须保持 create side effects 顺序。
- 阻塞项：无证据 blocker。
- 必须先补测试：task create side-effect rollback、direct stream no-persistence negative guard。
- 建议实施任务：TASK-301、TASK-302。
- 回滚方式：保留 `QATaskService` public API 和 router 不变，内部新组件可一键回接旧方法。

### V-303

- 来源：第三轮只读复核，命令 `rg "QATaskService|GatewayTaskExecutor|execution_admission|slot_lease|event_relay" gateway/app gateway/tests`
- 所属服务：gateway
- 对应第二轮条目：R-016/R-022
- closed|partially closed|still open：closed
- 代码位置：admission policy `gateway/app/services/execution_admission.py:67`；worker bootstrap `gateway/app/services/execution_admission.py:917`；runtime app `SimpleNamespace` `gateway/app/services/execution_admission.py:958`；direct live `_proxy_ask_stream` `gateway/app/routers/qa.py:723`。
- 接口路径：task/admission: `/api/v1/tasks`、`/api/v1/tasks/{task_id}/events`、admission worker CLI/runtime；direct live: `/api/{mode}/ask_stream`。
- 只读验证命令：`rg -n "def evaluate_task_create_admission|def run_admission_worker|SimpleNamespace|GatewayTaskExecutor" gateway/app/services/execution_admission.py`
- 证据摘要：worker 只构造 `GatewayTaskExecutor(runtime_app).execute` 并消费 queue/lease；`qa.py` 的 direct stream 没有 admission dispatcher/worker 引用。admission create policy 覆盖 same-conversation active、per-user active、queue size 等 task admission 约束。
- 最终判定：admission worker 未接管 live ask；它是 task/admission infra worker。
- 是否可进入重构：可进入，但先抽 runtime/container，避免 web app 与 worker app 依赖漂移。
- 阻塞项：worker `SimpleNamespace` composition root 仍是重构风险。
- 必须先补测试：web runtime 与 worker runtime dependency parity test。
- 建议实施任务：TASK-303。
- 回滚方式：保留 `run_admission_worker()` 当前构造路径，新增 container 只作为可替换工厂。

### V-304

- 来源：第三轮只读复核，命令 `find gateway -type f \( -name "*.py" -o -name "*.js" -o -name "*.vue" \) -print0 | xargs -0 wc -l | sort -nr | head -50`
- 所属服务：gateway
- 对应第二轮条目：R-004/R-014/R-015/R-020
- closed|partially closed|still open：partially closed
- 代码位置：`gateway/app/services/qa_tasks.py:252` `QATaskService`；`gateway/app/services/qa_tasks.py:1400` `GatewayTaskExecutor`；文件总长 2462 行。
- 接口路径：`POST /api/v1/tasks`、`GET /api/v1/tasks/{task_id}`、`GET /api/v1/tasks/{task_id}/events`、`POST /api/v1/tasks/{task_id}/cancel`
- 只读验证命令：`rg -n "class QATaskService|class GatewayTaskExecutor|SSEFrameBuffer|assistant-progress|assistant-terminal" gateway/app/services/qa_tasks.py`
- 证据摘要：同一文件同时承担 API facade、route resolve、queue record repository、状态转换、event relay、worker runner、progress batching、conversation persistence、quota finalize、cancel live handle 等职责。
- 最终判定：可拆，但不是无行为风险的文件移动；第一批应保留 façade，先抽 repository/state-machine/runner/persistence/cancellation 小组件。
- 是否可进入重构：可进入第一批“内部组件抽取”，不可先改接口行为。
- 阻塞项：缺少状态机 transition golden 和 event replay snapshot。
- 必须先补测试：task state transition matrix、event replay ordering、cancel/finalize compensation。
- 建议实施任务：TASK-301。
- 回滚方式：新组件由 `QATaskService`/`GatewayTaskExecutor` 调用，保留旧类入口和旧测试导入路径。

### V-305

- 来源：第三轮只读复核，命令 `rg -n "def evaluate_task_create_admission|def capacity_key_for_record|def queue_priority_for_record|class ExecutionAdmissionWorker" gateway/app/services/execution_admission.py`
- 所属服务：gateway
- 对应第二轮条目：R-005/R-021
- closed|partially closed|still open：partially closed
- 代码位置：业务策略 `gateway/app/services/execution_admission.py:67`、`:178`、`:185`；worker mechanism `gateway/app/services/execution_admission.py:643`；mechanism stores `gateway/app/services/execution_queue_status.py`、`execution_event_relay.py`、`execution_slot_leases.py`。
- 接口路径：`POST /api/v1/tasks` admission check、worker queue loop、`GET /api/v1/tasks/{task_id}/events`
- 只读验证命令：`rg "QATaskService|GatewayTaskExecutor|execution_admission|slot_lease|event_relay" gateway/app gateway/tests`
- 证据摘要：业务策略包括同 conversation active 冲突、per-user active cap、queue full、capacity key、priority、thinking/fast/patent 分流。底层机制包括 Redis/memory queue index、lease acquire/renew/release、event frame append/replay/dedupe、dirty index repair。
- 最终判定：策略与机制已能分层；抽公共 Redis indexed-store/lease/relay helper 可行，但不得把 capacity/priority/terminal mapping 移入通用机制。
- 是否可进入重构：可进入机制抽取，不建议第一步替换 worker policy。
- 阻塞项：dirty repair CAS/TTL 语义复杂，缺共享抽象前的 parity checklist。
- 必须先补测试：跨 queue/relay/lease 的 shared index repair behavior tests。
- 建议实施任务：TASK-306。
- 回滚方式：每个 store 保留原 describe/get/list 行为，新 helper 后挂 feature flag 或模块级 adapter。

### V-306

- 来源：第三轮只读复核，命令 `rg -n "redis_enabled|admission_enabled|REDIS_ENABLED|GATEWAY_ADMISSION_ENABLED|memory_fallback|run_admission_worker|probe" gateway/app gateway/tests/test_config.py gateway/tests/test_execution_admission.py gateway/tests/test_health.py`
- 所属服务：gateway
- 对应第二轮条目：R-010/R-021
- closed|partially closed|still open：partially closed
- 代码位置：mandatory settings `gateway/app/core/config.py:121`、`:123`；store describe fallback `gateway/app/services/execution_queue_status.py:599`、`execution_event_relay.py:499`、`execution_slot_leases.py:389`；worker fail-closed `gateway/app/services/execution_admission.py:932`。
- 接口路径：`GET /healthz`/health router components、admission worker runtime、task APIs。
- 只读验证命令：同来源命令。
- 证据摘要：`GatewaySettings.from_env()` 强制 Redis/admission enabled；web stores 在 Redis unavailable 时显示 `memory_fallback`；worker `run_admission_worker()` 要求 `redis_runtime.service.probe()` 成功，否则返回 3。测试锁定 disabled env 被忽略与 worker fail-closed。
- 最终判定：测试/生产边界部分闭环；当前代码允许 web fallback 以便 degraded health/test，但 worker 生产共享队列必须 Redis live。缺少“生产 web 是否允许 fallback 继续接 task create”的明确门禁。
- 是否可进入重构：可进入 container/health 抽取；不应先改变 fallback 行为。
- 阻塞项：生产策略未显式：Redis down 时 task create 是否应 fail closed。
- 必须先补测试：Redis unavailable 下 task create/read/cancel 的期望矩阵。
- 建议实施任务：TASK-303、TASK-306。
- 回滚方式：保持现有 `RedisRuntime.service.available/probe` 语义，新增 policy 只报告不拦截，待测试齐备再启用拦截。

### V-307

- 来源：第三轮只读复核，命令 `rg -n "snapshot|golden|ordered rules|ordered_candidate|strategy=\"" gateway/app/services/file_context_resolver.py gateway/tests/test_file_context_resolver.py`
- 所属服务：gateway
- 对应第二轮条目：R-003
- closed|partially closed|still open：still open
- 代码位置：ordered rule chain `gateway/app/services/file_context_resolver.py:228`；candidate ordering `gateway/app/services/file_context_resolver.py:749`；selection status `gateway/app/services/file_context_resolver.py:824`；测试 `gateway/tests/test_file_context_resolver.py`。
- 接口路径：indirect via `POST /api/{mode}/ask`、`POST /api/{mode}/ask_stream`、task create。
- 只读验证命令：同来源命令。
- 证据摘要：resolver 中存在 explicit_ref、ordinal_ref、deictic_count_scope、selected_scope、plural_scope、latest_new_upload、table_focus、metadata_focus_scope 等有序策略；测试文件覆盖大量场景，但未发现 snapshot/golden 关键字或完整 ordered-rule matrix。
- 最终判定：ordered rules 快照未闭环；拆 resolver 前必须补 golden matrix。
- 是否可进入重构：不可先拆规则；可先新增测试快照。
- 阻塞项：无快照时重排 `if` 顺序会改变路由/文件选择且难以发现。
- 必须先补测试：多文件/selected/latest/table/DOI/ordinal/no-file-intent 组合 snapshot。
- 建议实施任务：TASK-305。
- 回滚方式：任何 resolver 拆分都保持旧 `resolve()` 单入口；失败时回滚到 monolith。

### V-308

- 来源：第三轮只读复核，命令 `rg -n "@router\\.post\\(\"/api/\\{mode\\}/ask_stream|@router\\.post\\(\"/api/v1/\\{mode\\}/ask_stream|_paths\\(\"/api|_mode_paths|QA_ROUTE_PATTERNS" gateway/app/routers/qa.py gateway/app/routers/public_proxy.py gateway/app/services/route_table.py gateway/tests/test_route_table.py gateway/tests/test_public_proxy.py`
- 所属服务：gateway
- 对应第二轮条目：R-008/R-009/R-012
- closed|partially closed|still open：partially closed
- 代码位置：QA aliases `gateway/app/routers/qa.py:854`、`:860`、`:866`；public aliases `gateway/app/routers/public_proxy.py:210`、`:222`；route table aliases `gateway/app/services/route_table.py:6`、`:13`。
- 接口路径：`/api/{mode}/ask*`、`/api/v1/{mode}/ask*`、public `/api/*` and `/api/v1/*`
- 只读验证命令：同来源命令。
- 证据摘要：QA router 使用多个 decorator 注册 `/api` 与 `/api/v1`；public proxy `_paths()` 生成 v1 alias；route table 单独维护 `_paths()`/`_mode_paths()`。测试 `test_route_table_patterns_are_registered` 和 public proxy parity tests 锁定双路径。
- 最终判定：可收敛为单 route registry 生成 router/table/doc，但不能直接删除 `/api/v1` alias。
- 是否可进入重构：可进入 registry 抽取，不可先删兼容路径。
- 阻塞项：缺少 canonical-vs-compat API 决策表。
- 必须先补测试：route registry snapshot、public/QA route disjoint parity、legacy v1 redirect/proxy contract。
- 建议实施任务：TASK-304。
- 回滚方式：保留 `_paths()` 兼容输出；新 registry 生成结果与旧 tuple diff 不一致时回滚。

### V-309

- 来源：第三轮只读复核，命令 `rg "active_task|public_proxy|conversation" gateway/app/routers gateway/tests`
- 所属服务：gateway
- 对应第二轮条目：R-007/R-013
- closed|partially closed|still open：partially closed
- 代码位置：proxy entry `gateway/app/routers/public_proxy.py:49`；live summary scan `gateway/app/routers/public_proxy.py:113`；response mutation `gateway/app/routers/public_proxy.py:156`；tests `gateway/tests/test_public_proxy.py:83`、`:149`、`:302`、`:378`。
- 接口路径：`GET /api/conversations`、`GET /api/v1/conversations`、`GET /api/conversations/{conversation_id}`、`GET /api/v1/conversations/{conversation_id}`
- 只读验证命令：`rg "active_task|public_proxy|conversation" gateway/app/routers gateway/tests`
- 证据摘要：`_proxy_to_public()` 先转发 public-service response，再 `_maybe_enrich_conversation_reads()` 将 gateway live task summary 注入 `data.conversations[*].active_task` 或 `data.active_task`；测试同时覆盖 list/detail、无 live task、任务过期降级。
- 最终判定：这是 gateway BFF enrichment，不是 public-service authority 原始响应；当前行为被测试锁定但 authority 边界需文档化。
- 是否可进入重构：可进入 contract 文档/adapter 抽取，不应移除字段。
- 阻塞项：未明确 active_task 字段归属、版本化和下游依赖。
- 必须先补测试：public-service raw response 与 gateway enriched response contract 对照；active_task schema snapshot。
- 建议实施任务：TASK-304。
- 回滚方式：保留 enrichment adapter，必要时仅在 gateway response 层开关，不改 public-service。

### V-310

- 来源：第三轮只读复核，命令 `rg "quota|finalize|precheck|done_payload|SSEFrameBuffer" gateway/app gateway/tests`
- 所属服务：gateway
- 对应第二轮条目：R-002/R-017
- closed|partially closed|still open：partially closed
- 代码位置：direct quota stream `gateway/app/routers/qa.py:226`；done withheld/finalize `gateway/app/routers/qa.py:322`；task runner SSE parser `gateway/app/services/qa_tasks.py:1829`；tests `gateway/tests/test_qa_proxy.py:1560`、`:1653`、`:1757`、`:1793`、`:1830`、`gateway/tests/test_task_api.py:2284`、`:2372`。
- 接口路径：`POST /api/{mode}/ask_stream`、`POST /api/v1/{mode}/ask_stream`、`GET /api/v1/tasks/{task_id}/events`
- 只读验证命令：同来源命令。
- 证据摘要：direct `_stream_with_quota()` yields non-done outbound frames immediately, withholds `done` until `quota_proxy.finalize()` completes, then appends `quota` to done. Tests cover missing done abort, metadata preservation, client close before/after done, cancel error envelope, and task latency telemetry. No explicit direct-path test introduces slow finalize and asserts first content frame is not delayed.
- 最终判定：done 丢失和 final done mutation 护栏部分闭环；first-byte latency 护栏仍需补 direct stream focused test。
- 是否可进入重构：可进入 shared SSE consumer only after补 direct first-byte/finalize-delay tests。
- 阻塞项：direct path 与 task path duplicated SSE parsing; latency-sensitive refactor风险高。
- 必须先补测试：slow finalize 不阻塞 first content、finalize failure 仍发 done+warning、upstream done 后 client close 的计数语义。
- 建议实施任务：TASK-302。
- 回滚方式：新 SSE consumer behind old `_stream_with_quota()` wrapper；失败即切回旧 wrapper。

### 2. dead-code / legacy 引用闭环

1. `ConversationPersistenceService.extract_stream()`：本轮 `rg` 证明无 runtime caller，只有 `gateway/tests/test_qa_proxy.py:3541` 的 summary parser 单测直接调用。判定为 legacy/helper，不是 live ask 持久化路径。处理建议：迁移到 shared SSE summary helper 或保留兼容 shim；不得在未迁移测试前删除。
2. `/api/v1` aliases：不是 dead code。`gateway/app/routers/qa.py`、`gateway/app/routers/public_proxy.py`、`gateway/app/services/route_table.py` 均显式生成或注册 v1 alias；测试锁定 parity。
3. `legacy-users` 类 public/admin 路由：本轮没有重新展开 admin 业务语义，但 route table/public proxy 仍注册 active paths；不得按名字清理。
4. `memory_fallback`：不是 test-only dead code。health tests 使用它，但 runtime store `describe()` 也会在 Redis 不可用时上报该 mode；worker 侧另行 fail-closed。

### 3. live path 调用链闭环

#### direct ask_stream live path

`POST /api/{mode}/ask_stream` 和 `POST /api/v1/{mode}/ask_stream`：

1. `gateway/app/routers/qa.py:723` `_proxy_ask_stream()` 进入。
2. `_resolve()` 完成 route/file context；clarification/status/file-disabled 直接返回 SSE。
3. quota `precheck()` 成功后调用 `proxy_service.open_json_stream()` 转发到 `/api/{actual_mode}/ask_stream`。
4. 返回 `StreamingResponse(_stream_with_quota(...))`。
5. `gateway/app/routers/qa.py:226` `_stream_with_quota()` 用 `SSEFrameBuffer` 解析 frame，非 done 立即 yield，done 被暂存，quota finalize 后注入 `quota` 再发 done。
6. 未发现 `conversation_persistence_service`、admission dispatcher、queue/lease/relay 参与该 path。

#### task/admission live path

`POST /api/v1/tasks` + `GET /api/v1/tasks/{task_id}/events`：

1. `gateway/app/services/qa_tasks.py:261` `QATaskService.create_task()` 绑定 auth user、route resolve、admission check、backend ready、quota precheck。
2. 先写 queue `provisioning` record，再调用 `conversation_persistence_service.create_task_turn()` 写 public-service authority turn。
3. 成功后更新 queue record 为 `queued`，append queued event，可 immediate dispatch 或等待 admission worker。
4. `gateway/app/services/execution_admission.py:917` worker 构造 runtime app 与 `GatewayTaskExecutor(runtime_app).execute`。
5. `gateway/app/services/qa_tasks.py:1702` `GatewayTaskExecutor._execute_async()` transition admitted/running，打开 backend `/api/{actual_mode}/ask_stream`。
6. runner 解析 upstream SSE，写 `execution_event_relay_store`，累积 progress，通过 `assistant-progress`/`assistant-terminal` internal endpoints 同步 public-service，并 finalize quota。

结论：direct stream 与 task stream 共享“上游 SSE”形态，但 persistence、replay、cancel、quota terminalization 责任边界不同。后续可抽共享 SSE parser/quota lifecycle，但不能合并业务状态机。

### 4. 测试护栏闭环

#### 测试护栏清单

| 护栏 | 当前测试 | 覆盖状态 | 重构前要求 |
| --- | --- | --- | --- |
| direct stream done/quota finalize | `gateway/tests/test_qa_proxy.py:1560`、`:1653`、`:1757`、`:1793`、`:1830` | partially covered | 补 slow finalize 不阻塞首个非 done frame。 |
| task live latency telemetry | `gateway/tests/test_task_api.py:2284`、`:2372` | covered for task summary | 与 SSE consumer 抽取一起保留。 |
| active_task enrichment | `gateway/tests/test_public_proxy.py:83`、`:149`、`:302`、`:378` | covered | 补 schema snapshot/authority boundary assertion。 |
| `/api` 与 `/api/v1` parity | `gateway/tests/test_route_table.py:5`、`:47`、`:56`；`gateway/tests/test_public_proxy.py:495`、`:505` | covered for selected surfaces | 补 generated route registry snapshot。 |
| Redis mandatory/fallback | `gateway/tests/test_config.py:106`、`gateway/tests/test_execution_admission.py:72`、`:135`、`gateway/tests/test_health.py:53` | partially covered | 补 Redis down 下 task create/read/cancel policy matrix。 |
| file context ordered rules | `gateway/tests/test_file_context_resolver.py` scenario tests | insufficient | 必须补 ordered rules golden/snapshot。 |
| queue/relay/lease dirty repair | `gateway/tests/test_execution_event_relay.py`、`test_execution_queue_status.py`、`test_execution_slot_leases.py` | covered per module | 抽 shared helper 前补 cross-store shared behavior tests。 |

本轮未运行 `pytest --collect-only gateway/tests`：即使 collect-only 也可能更新 `.pytest_cache`，用户允许担心写缓存时不运行；且硬约束要求不运行会写文件的命令。只读证据来自 `rg/find/wc/sed/cat`。

### 5. 可实施重构任务拆分

#### 可实施任务卡

### TASK-301

- 来源：V-302/V-304
- 所属服务：gateway
- 优先级：P0
- 目标：将 `qa_tasks.py` 拆成保留 `QATaskService`/`GatewayTaskExecutor` 外观的 task facade、repository、state machine、runner、persistence coordinator、cancellation/live-handle 组件。
- 非目标：不改变 `/api/v1/tasks*` 响应 schema、状态名、event replay、quota 计数、public-service internal endpoint。
- 涉及代码：`gateway/app/services/qa_tasks.py`，后续可新增 gateway service module；测试在 `gateway/tests/test_task_api.py`、`gateway/tests/test_refresh_survivable_task_e2e.py`。
- 接口：`POST /api/v1/tasks`、`GET /api/v1/tasks/{task_id}`、`GET /api/v1/tasks/{task_id}/events`、`POST /api/v1/tasks/{task_id}/cancel`
- 当前行为：单文件 2462 行同时处理 create side effects、queue 状态、relay、worker runner、progress/terminal persistence、quota finalize、cancel。
- 目标行为：外部行为不变；内部职责分层，状态转换集中可测，persistence/quota/cancel 补偿路径显式。
- 迁移步骤：先提取只读 repository adapter；再提取 state transition helper；再提取 persistence coordinator；最后让 runner 依赖接口而不是直接散落 app.state。
- 兼容策略：保留旧类名、方法签名和 router import；新组件只从旧类内部调用。
- 回滚策略：删除新组件调用并恢复旧类内联方法；router 无需改动。
- 必须新增或固定的测试：state transition matrix、create rollback side effects、cancel running/queued、terminal sync retry、event replay ordering、quota finalize success/failure。
- 验收标准：现有 task/e2e 测试通过；新增 matrix 覆盖 provisioning->queued->admitted->running->terminal/canceled/failed；public-service internal call 顺序不变。
- 风险：高，涉及跨 Redis/public-service/quota/backend stream 状态一致性。
- 是否建议第一批实施：是，但只做内部拆分和测试加固。

### TASK-302

- 来源：V-301/V-302/V-310
- 所属服务：gateway
- 优先级：P1
- 目标：抽共享 SSE frame consumer 与 quota grant lifecycle，供 direct `_stream_with_quota()` 和 task runner 使用，消除重复 parse/done/error/finalize 分支。
- 非目标：不改变上游 SSE protocol，不改变 direct path 无 conversation persistence 的边界，不改变 task relay/persistence 语义。
- 涉及代码：`gateway/app/routers/qa.py`、`gateway/app/services/qa_tasks.py`、`gateway/app/services/conversation_persistence.py`、`gateway/app/services/sse_frames.py`。
- 接口：`POST /api/{mode}/ask_stream`、`POST /api/v1/{mode}/ask_stream`、backend `/api/{actual_mode}/ask_stream`、task event replay。
- 当前行为：direct wrapper 暂存 done 并 finalize quota；task runner 自行 parse frame、append relay、progress flush、terminalize。
- 目标行为：共享 frame parser 只输出 typed events；direct/task 各自保留 policy callback。
- 迁移步骤：补 direct first-byte/finalize-delay 测试；提取 parser 不改逻辑；让 `extract_stream()` 复用 parser；再接 direct；最后评估 task runner 接入。
- 兼容策略：保留 `_stream_with_quota()` wrapper；`ConversationPersistenceService.extract_stream()` 保留原签名。
- 回滚策略：新 parser behind wrapper，发现 latency/done regression 即切回旧实现。
- 必须新增或固定的测试：slow quota finalize 不阻塞 first content；done metadata/prefix/id 保留；error event 后 quota abort；client close before/after done；task duplicate upstream seq 与 terminal frame 去重。
- 验收标准：direct stream 首个非 done frame 在 finalize 前发出；done 总能在 finalize 后保留 prefix 并注入 quota；task replay 顺序不变。
- 风险：高，流式首字节和 done 丢失是用户可见问题。
- 是否建议第一批实施：是，但必须以测试先行。

### TASK-303

- 来源：V-303/V-306
- 所属服务：gateway
- 优先级：P1
- 目标：抽 `GatewayRuntime`/composition container，统一 web app 与 admission worker 的 settings、backend registry、proxy、quota、conversation persistence、Redis stores 构造。
- 非目标：不改变 worker CLI、Redis mandatory setting、health response 字段。
- 涉及代码：`gateway/app/main.py`、`gateway/app/services/execution_admission.py`、`gateway/app/core/config.py`、Redis integration/services。
- 接口：web startup、admission worker startup、`GET /healthz`、admission status endpoints。
- 当前行为：web `main.py` 构造 app.state；worker 在 `run_admission_worker()` 里用 `SimpleNamespace` 重建部分依赖。
- 目标行为：web/worker 共用 runtime factory，worker 仍可 fail-closed when Redis probe false。
- 迁移步骤：定义 factory 只搬运构造逻辑；web 接入；worker 接入；补 parity describe/probe tests；最后清理重复 imports。
- 兼容策略：app.state 属性名保持不变。
- 回滚策略：worker 可退回 `SimpleNamespace` 构造；web app startup 保留旧分支。
- 必须新增或固定的测试：web/worker state attribute parity；Redis unavailable worker return 3；web health memory_fallback 上报不变。
- 验收标准：`test_config.py`、`test_execution_admission.py`、`test_health.py` 既有断言不变；worker runtime 依赖不再分叉。
- 风险：中，高风险点在启动路径和测试 monkeypatch。
- 是否建议第一批实施：是。

### TASK-304

- 来源：V-308/V-309
- 所属服务：gateway
- 优先级：P2
- 目标：建立 route registry 与 public conversation enrichment contract，收敛 `/api`/`/api/v1` route table/public proxy/QA router 的重复定义，并明确 `active_task` 是 gateway-enriched 字段。
- 非目标：不删除 `/api/v1`，不改变 public-service raw response，不改变 active_task schema。
- 涉及代码：`gateway/app/routers/qa.py`、`gateway/app/routers/public_proxy.py`、`gateway/app/services/route_table.py`、`gateway/tests/test_public_proxy.py`、`gateway/tests/test_route_table.py`。
- 接口：所有 public proxy `/api*`、QA `/api/{mode}/ask*`、conversation list/detail。
- 当前行为：多处 `_paths()`/decorator 手写 alias；public proxy response 注入 `active_task`。
- 目标行为：单 registry 生成 router specs 与 route table；active_task enrichment 独立 adapter 并有 schema snapshot。
- 迁移步骤：先生成只读 registry snapshot；route_table 改为读 registry；public proxy specs 改为读 registry；最后 QA decorators 如可行再收敛。
- 兼容策略：registry 默认仍输出现有 `/api` 与 `/api/v1` path。
- 回滚策略：保留旧 tuple/spec 常量备份分支；snapshot diff 失败则回退。
- 必须新增或固定的测试：route registry golden；public/QA route disjoint；conversation enriched/raw schema；v1 parity。
- 验收标准：现有 route table/public proxy tests 不变；新增 snapshot 与当前注册 routes 完全一致。
- 风险：中，主要风险是遗漏路径或方法。
- 是否建议第一批实施：否，建议第二批。

### TASK-305

- 来源：V-307
- 所属服务：gateway
- 优先级：P1
- 目标：为 `file_context_resolver.py` 建 ordered rules golden/snapshot matrix，冻结拆分前行为。
- 非目标：不改 resolver 行为，不调参 classifier，不改变 route decision。
- 涉及代码：`gateway/app/services/file_context_resolver.py`、`gateway/tests/test_file_context_resolver.py`。
- 接口：indirect via QA ask/ask_stream/task create。
- 当前行为：单个 `resolve()` 内多层 ordered `if` 决策，已有场景测试但无规则顺序快照。
- 目标行为：每个输入场景输出 strategy、resolved_ids、candidate_ids、selection_status、clarification reason 的 stable snapshot。
- 迁移步骤：整理 fixture；生成 matrix；用显式 expected dict 而非外部新文件；覆盖 DOI/explicit/ordinal/selected/latest/table/metadata/no-intent。
- 兼容策略：仅新增测试，不改生产代码。
- 回滚策略：删除新增测试即可，不影响 runtime。
- 必须新增或固定的测试：ordered rules golden matrix。
- 验收标准：未来任何 resolver 拆分必须通过 matrix；策略名和 candidate ordering 改动可见。
- 风险：低，但能显著降低后续高风险拆分。
- 是否建议第一批实施：是，作为 resolver 重构前置。

### TASK-306

- 来源：V-305/V-306
- 所属服务：gateway
- 优先级：P1
- 目标：抽 Redis indexed-store maintenance helper，并明确生产 Redis fallback policy。
- 非目标：不改变 queue priority/capacity/admission business policy，不替换 Redis key schema。
- 涉及代码：`gateway/app/services/execution_queue_status.py`、`gateway/app/services/execution_event_relay.py`、`gateway/app/services/execution_slot_leases.py`、`gateway/app/integrations/redis/service.py`、`gateway/app/routers/health.py`。
- 接口：task queue internals、event replay、slot leases、health/admission status。
- 当前行为：三个 store 分别实现 dirty index repair、TTL、describe fallback；web 可 memory fallback，worker Redis probe fail-closed。
- 目标行为：共享底层机制；业务 store 只声明 key/index/record semantics；production fallback policy 可观测且可测试。
- 迁移步骤：先提 shared helper with no behavior change；逐个 store 接入；补 cross-store dirty repair tests；再加 policy report。
- 兼容策略：key prefix、describe payload 字段、memory fallback mode 不变。
- 回滚策略：逐 store 回退旧实现。
- 必须新增或固定的测试：partial index write failure、dirty version newer mutation、TTL expire、Redis probe fail-closed、web memory fallback health。
- 验收标准：execution queue/event relay/slot lease test suites 保持通过；health/admission describe 与现有字段兼容。
- 风险：中到高，错误会影响 replay/cancel/worker leasing。
- 是否建议第一批实施：是，但限机制抽取，不改 admission policy。

### 6. 不可立即处理项与阻塞原因

1. 不可立即删除 `extract_stream()`：虽然无 runtime caller，但测试覆盖了 reference object summary 行为；应在 TASK-302 中迁移测试后再删或保留 shim。
2. 不可立即拆 `file_context_resolver.resolve()`：ordered rules 没有 golden/snapshot；任何重排都可能改变 file route/source_scope。
3. 不可立即统一 `/api` 与 `/api/v1` 为单一路径：当前 router/table/tests 明确支持双路径；需先 route registry snapshot 和兼容策略。
4. 不可立即把 `active_task` 下沉到 public-service：目前证据显示它来自 gateway queue live state；除非 public-service 接管 task authority，否则下沉会混淆 authority。
5. 不可立即让 admission worker 接管 direct ask_stream：当前 direct path 没有 queue/lease/replay/cancel 语义；接管会改变 first-byte、quota finalize 和 persistence 边界。
6. 不可立即改变 Redis fallback：web degraded fallback 与 worker fail-closed 都被测试锁定；生产 fail-open/fail-closed 需先形成 policy matrix。

### 7. 最终进入重构前检查清单

- [ ] 已补 direct `_stream_with_quota()` slow-finalize first-byte latency 测试。
- [ ] 已补 direct stream done metadata/prefix/finalize failure regression 测试。
- [ ] 已补 task state transition matrix，覆盖 provisioning/queued/admitted/running/completed/failed/canceled/expired。
- [ ] 已补 task create side-effect rollback 测试，覆盖 queue record、conversation turn、quota grant 任一步失败。
- [ ] 已补 `file_context_resolver` ordered rules golden matrix。
- [ ] 已生成 route registry snapshot，确认 `/api` 与 `/api/v1` 当前 path/method parity。
- [ ] 已补 `active_task` enriched response schema snapshot，并标注 gateway-owned field。
- [ ] 已补 web/worker runtime dependency parity 测试。
- [ ] 已补 Redis unavailable 下 task create/read/events/cancel 策略矩阵。
- [ ] 已为 queue/event relay/slot lease dirty index repair 抽象建立 shared behavior tests。
- [ ] 第一批只做 TASK-301/TASK-302/TASK-303/TASK-305/TASK-306 的“测试加固 + 内部抽取”，不改变外部 API。
- [ ] 第二批再做 TASK-304 alias/contract 收敛。
