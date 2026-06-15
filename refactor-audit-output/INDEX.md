# 重构审计总览

> 状态：已完成第一轮只读审计。证据来自 6 份服务审计文档与只读代码检索；未修改业务代码、配置、脚本、测试、README 或依赖文件。

## 1. 当前系统调用链

```text
frontend-vue
  -> gateway
     -> public-service
        - auth / quota / conversation / message / uploaded file metadata / documents / storage / system
     -> fastQA
        - fast KB / PDF / table / hybrid / graph execution
     -> highThinkingQA
        - thinking QA execution
     -> patent
        - patent KB / PDF / table / hybrid execution
```

主要 live paths：

- 前端默认 QA stream：`/api/{mode}/ask_stream`，其中 `mode in fast/thinking/patent`；`/api/v1/{mode}/ask_stream` 前端未见 live 调用。
- gateway QA proxy：`/api/{mode}/ask`、`/api/v1/{mode}/ask`、`/api/{mode}/ask_stream`、`/api/v1/{mode}/ask_stream`。
- refresh-survivable task：`/api/v1/tasks`、`/api/v1/tasks/{task_id}`、`/api/v1/tasks/{task_id}/events`、`/api/v1/tasks/{task_id}/cancel`。
- public-service authority：`/api/v1/conversations*`、`/internal/conversations/*`、`/internal/quota/grants/precheck`、`/internal/quota/grants/{grant_id}/finalize`。
- backend execution：fastQA `/api/fast/ask_stream`，highThinkingQA `/api/thinking/ask_stream`，patent `/api/patent/ask_stream` and v1 aliases.

## 2. 五个后端的职责边界

- gateway：统一入口、mode 路由、文件上下文解析、quota proxy、SSE proxy/finalize、refresh-survivable task、task replay、public-service proxy、active_task enrichment。
- public-service：应是公共权威服务，负责 auth、quota、conversation、message、uploaded file metadata、storage authority、system status；当前还混入 retrieval/vector/Neo4j/PDF processing/model ops。
- fastQA：fast KB/PDF/table/hybrid/Graph 执行后端；当前已膨胀为 execution + retrieval + storage + LLM runtime 平台。
- highThinkingQA：thinking QA 执行后端；当前 HTTP surface 已收缩到 ask/health，但保留大量 unregistered retired routers 和双 persistence 模式。
- patent：patent QA 执行后端；当前远超 README scaffold，包含 runtime bootstrap、durable persistence、file routes、Graph KB、LLM pool、planning hot pool、rerank。

## 3. 当前边界混乱点

| 边界 | 证据 | 风险 |
|---|---|---|
| gateway vs public-service | gateway public proxy 会向 conversation read response 注入 `active_task` | public authority 响应被 gateway task runtime 改写 |
| public-service vs QA execution | public-service startup 初始化 Chroma/VectorDbClient/Neo4j，documents 做 PDF extraction/reference preview | 公共服务可用性受 QA/retrieval 依赖影响 |
| highThinkingQA persistence ownership | gateway-owned persistence 与 service-owned legacy/public/shadow 模式并存 | 单写者不清，可能重复/漏写消息 |
| backend contract 重复 | fastQA `request_adapter.py`、patent `request_models.py`、gateway payload builder 各自维护 route/source_scope/file rules | protocol drift |
| LLM/upstream 重复 | fastQA、highThinkingQA、patent 都有 OpenAI-compatible/http pool/rerank/auth logging | 行为和观测字段不一致 |
| frontend API paths | frontend 默认 `/api/{mode}/ask_stream`，gateway/backend 同时维护 `/api/v1/...` | canonical path 不清 |

## 4. 可复用公共能力候选

推荐共享包结构：

```text
packages/agent_common/
  config/
    env_loader.py
    service_roots.py
    model_endpoint.py
    embedding.py
    rerank.py
    redis.py
    neo4j.py
    storage.py
    http_pool.py
    auth.py
  contracts/
    qa_route.py
    source_scope.py
    turn_mode.py
    execution_file.py
    gateway_ask_request.py
    patent_ask.py
    gateway_task_headers.py
    stream_event.py
    error_event.py
    quota.py
    conversation_authority.py
  llm/
    auth.py
    openai_compatible.py
    embedding_client.py
    rerank_client.py
    stream_parser.py
    http_pool.py
    retry_policy.py
    model_call_logger.py
    upstream_auth_logger.py
  runtime/
    service_container.py
    resource_registry.py
    component_status.py
    lifecycle.py
    task_queue_ports.py
  sse/
    frames.py
    encoder.py
  files/
    identifiers.py
    metadata.py
    readiness.py
  storage/
    storage_ref.py
    object_names.py
  retrieval/
    contracts.py
```

优先抽取顺序：

1. `contracts/gateway_ask_request.py` + route/source_scope/turn_mode/execution_file。
2. `sse/frames.py` + `contracts/stream_event.py`。
3. `contracts/quota.py` and conversation authority DTO/client.
4. `llm/openai_compatible.py` + auth/http_pool/stream_parser/model_call_logger.
5. `runtime/resource_registry.py` + component_status.

## 5. 可清理下线代码候选

| 候选 | 状态 | 处理建议 |
|---|---|---|
| highThinkingQA `conversation/upload/admin/auth/documents/ingest/quota/system` routers | deprecated and unregistered | 引用扫描后分批归档；保留 removed-route 404 tests |
| fastQA `_build_pdf_agent` / `smart_query` / `query_pdf_directly` | deprecated but still referenced | 先引入 PDF ports，再删除 shim |
| fastQA `FASTQA_NOT_READY` placeholder | scaffold / placeholder but tested | 统一 NotReadyEvent 后再删除旧文案 |
| patent non-prefix `/api/ask*` | deprecated but still registered | gateway cutover 确认后下线 |
| patent local original route 503 | deprecated but still registered | 确认 gateway/public original owner 后取消注册或保留兼容错误 |
| public-service retrieval runtime | active live path but boundary-overlapping | 迁出到 QA/retrieval-service，不能直接删 |
| public-service legacy conversation fallback | deprecated but still referenced | 历史数据迁移完成后删除 |
| frontend `src/api/*` + `features/chat/controls` | scaffold/deprecated unknown | 确认是否产品入口；若否归档，若是接入 canonical API clients |
| README/docs scaffold claims | archive/doc drift | 更新 live inventory，避免误导重构 |

## 6. 高风险巨型模块清单

| 文件 | 行数约 | 风险 |
|---|---:|---|
| `patent/server/patent/retrieval_service.py` | 2922 | patent retrieval 领域过大，后续单独审计 |
| `gateway/app/services/qa_tasks.py` | 2462 | task API/state/worker/SSE/persistence/quota 纠缠，P0 |
| `frontend-vue/src/stores/chatStore.js` | 1965 | durable state 和 runtime state 混杂 |
| `patent/server/patent/file_routes.py` | 1711 | file-QA orchestrator，应服务化 |
| `fastQA/app/routers/qa.py` | 1614 | router god-object |
| `public-service/backend/app/modules/conversation/service.py` | 3566 | conversation authority、files、task echo、patent renderer 混杂 |
| `fastQA/app/integrations/llm/openai_compat.py` | 1162 | LLM transport god-object |
| `gateway/app/services/file_context_resolver.py` | 1146 | file intent/readiness/clarification 规则引擎 |
| `highThinkingQA/server/services/ask_service.py` | 1081 | execution/event/UI text/reference 混合 |
| `patent/server_fastapi/app.py` | 765 | bootstrap/resource lifecycle 过重 |

## 7. 重构优先级

P0:

- gateway `qa_tasks.py`：先加 contract/golden tests，再拆 state machine/repository/runner/persistence/cancellation。
- public-service retrieval runtime 越界：增加 public-only 禁用模式和启动 contract，计划迁出。

P1:

- 抽 gateway/backend ask contract 与 SSE contract。
- 拆 gateway `qa.py` 的 quota/SSE adapter。
- 拆 fastQA `routers/qa.py` dispatcher/runners。
- 收敛 highThinkingQA persistence ownership。
- patent contract/container/lifecycle。
- frontend API client route contract + `services/api.js` 拆分 façade。

P2:

- 抽共享 LLM/upstream/rerank。
- 拆 storage/conversation/system QA ops。
- highThinkingQA config/env loader。
- frontend chatStore 内部拆分和 Home selectors。

P3:

- fastQA dependency 去重。
- 文档漂移修正。
- unregistered skeleton cleanup。

## 8. 推荐共享包结构

先做“contract-only + adapter façade”而不是大爆炸迁移：

1. `packages/agent_common/contracts` 只放 dataclass/Pydantic schema、literal enums、validator、error event schema。
2. 每个服务保留本地 adapter，先调用共享 validator，再保留 service-specific policy。
3. `packages/agent_common/llm` 先抽 stream parser/auth/header/model-call logging，再迁移完整 client。
4. `packages/agent_common/runtime` 先抽 ResourceRegistry/ComponentStatusRegistry，不直接替换所有 app.state。
5. 前端用 `src/services/api/routes.js` 固定路径，不必等待后端共享包。

## 9. 测试策略

- 单元测试：先覆盖纯 contract/normalizer/policy，尤其 source_scope/file_selection、SSE frame、storage_ref。
- contract test：gateway -> backend ask request、quota grant、conversation authority、task summary。
- stream/SSE test：first-byte、content/error/done、quota done 注入、task replay after_seq、disconnect。
- integration smoke test：frontend -> gateway -> each backend `/ask_stream`，public-service auth/conversation/upload。
- backward compatibility test：`/api` 与 `/api/v1` aliases、legacy `/api/ask*`、removed highThinkingQA routes 404。
- failure/cancel/retry test：task cancel/lease release/quota finalize fail、LLM timeout、runtime degraded。
- persistence test：public-service conversation JSON/DB/cache/storage consistency，gateway task terminal/progress sync。
- quota/auth test：internal tokens、bearer token invalidation、grant idempotency。
- file route test：PDF/table/hybrid execution_files, selected_file_ids, primary_file_id, MinIO readiness。
- frontend E2E：多 chat 同时生成、刷新 replay、取消、网络中断恢复、upload+ask。

## 10. 不建议立即修改的高风险区域

- 不要直接替换 gateway 手写 queue 为 arq/dramatiq/celery/rq。先分离业务策略和后端接口，否则会丢 replay/cancel/quota/persistence 语义。
- 不要一次性删除 highThinkingQA retired routers 的底层 service。先确认测试/脚本 imports，并保留 route surface 404。
- 不要直接迁出 public-service documents/retrieval live APIs。先确认前端/运维调用，提供兼容 proxy。
- 不要直接统一 LLM client。三服务的 env 前缀、日志字段、timeout、thinking controls、stream parser 差异需要矩阵测试。
- 不要直接拆 frontend `chatStore` public API。先用 façade 保持调用不变，内部逐步搬迁。
- 不要直接改 `/api` vs `/api/v1` canonical。先以 route builder 和 contract tests 固化，随后切前端。

## 11. 分文档索引

- [gateway-refactor-audit.md](./gateway-refactor-audit.md)
- [public-service-refactor-audit.md](./public-service-refactor-audit.md)
- [fastQA-refactor-audit.md](./fastQA-refactor-audit.md)
- [highThinkingQA-refactor-audit.md](./highThinkingQA-refactor-audit.md)
- [patent-refactor-audit.md](./patent-refactor-audit.md)
- [frontend-vue-refactor-audit.md](./frontend-vue-refactor-audit.md)

## 第二轮深度补充

> 状态：第二轮为只读深度复核。本节只补充既有第一轮 `INDEX.md`，不新建第二轮目录；细粒度 router/service/module 证据继续沉淀到 6 份服务文档的同名“第二轮深度补充”章节。

### 1. 本轮审计覆盖率

| 服务 | 目录覆盖 | router 覆盖 | service 覆盖 | tests 覆盖 | config 覆盖 | 风险 |
| -- | ---- | --------- | ---------- | -------- | --------- | -- |
| gateway | `gateway/app/main.py`、`app/core/`、`app/routers/`、`app/services/`、`scripts/`、`README.md`、`pyproject.toml` | `app.include_router()` 注册 `health/admission/tasks/public_proxy/qa`；QA、task、public proxy、health/admission 均需逐表追踪 | `qa.py`、`qa_tasks.py`、`file_context_resolver.py`、`route_decision.py`、`quota_proxy.py`、`conversation_persistence.py`、admission/slot/event relay | `gateway/tests/test_qa_proxy.py`、`test_task_api.py`、`test_refresh_survivable_task_e2e.py`、`test_execution_*`、`test_public_proxy.py` | `app/core/config.py`、Redis/runtime/admission flags、backend endpoints | P0：task/SSE/quota/persistence 一体化，替换队列前必须锁 contract |
| public-service | `backend/app/main.py`、`core/`、`modules/`、`public-modules/`、`scripts/`、`README.md`、`backend-dependency-map.md` | auth/quota/conversation/documents/storage/system/admin-users 等 router；internal conversation/quota 路径需单独分级 | conversation、quota、documents、storage、retrieval、auth、system、admin_users、outbox/workers | `backend/tests/test_route_surface.py`、conversation/quota/documents/storage/live integration tests | Settings、DB/Redis/MinIO/Neo4j/LLM/embedding/retrieval env | P0：公共权威服务混入 retrieval/vector/PDF/model ops，需 feature inventory 后迁出 |
| fastQA | `app/main.py`、`core/`、`routers/`、`services/`、`integrations/`、`modules/`、`tests/`、`README.md`、`pyproject.toml` | `qa.py`、documents/health 等；`/api/v1/fast/ask` 绑定 stream 行为需复核 | qa_kb、qa_pdf、qa_tabular、graph_kb、generation_pipeline、retrieval、storage、qa_cache、file_route_service、request_adapter、LLM integrations | 100+ tests，覆盖 graph、generation、qa route aliases、tabular、LLM、request adapter、stream contract | `core/config.py`、`env_loader.py`、LLM/embedding/rerank/vector/Redis/storage env | P1/P2：已从 fast QA 膨胀为多执行平台，contract 与 LLM 抽共享前不能拆 router |
| highThinkingQA | `server_fastapi/`、`server/`、`agent_core/`、`ingest/`、`retriever/`、`prompts/`、`tests/`、`config.py`、`env_loader.py` | 当前注册 surface 需以 `server_fastapi/app.py` 与 routers `__init__` 为准；未注册 routers 需引用验证 | ask router/service、chat persistence、conversation context、LLM client、upstream auth logging、retriever/ingest | migration/route surface/env/ask/persistence/LLM tests | settings 与全局常量双轨、legacy env loader | P1：router surface 已收缩但服务内仍有 legacy closure 与双 persistence |
| patent | `server_fastapi/`、`server/`、`server/patent/`、`server/services/`、`server/schemas/`、`tests/`、`config.py`、`README.md` | `/api/ask*`、`/api/v1/ask*`、`/api/patent/ask*`、health/original/file route 相关路径 | bootstrap、ask router、request_models、executor、file_routes、kb/pdf/tabular/hybrid/retrieval/rerank/planning/upstream/runtime | `tests/fastapi_contract/test_ask_contract.py` 等 contract/route/runtime tests | `config.py` runtime/model/storage/graph/Redis/env | P0/P1：README scaffold 与代码 reality 不一致；bootstrap/resource lifecycle 过重 |
| frontend-vue | `src/services/`、`src/stores/`、`src/components/`、`src/views/`、`src/router/`、`src/utils/`、tests、`package.json`、`vite.config.js` | API 调用点在 `src/services/api.js`、`src/api/*`、auth/admin/departments services；route view 依赖 store | chatStore、chatPersistence、streamPersistPolicy、recoverableTaskController、routingStatus/queryMode、Home.vue | `src/stores/*.test.js`、`src/services/api.structure.test.js`、`src/utils/recoverableTaskController.test.js`、Home structure tests | Vite proxy/package deps/env examples | P1：前端 task 路径构造与 stream 路径 canonical 需 contract test 先锁 |

### 2. 当前系统真实调用链复核

证据快照：

```text
frontend-vue/src/services/api.js:607-630
  askStream() builds POST ${API_BASE}${V1}/${mode}/ask_stream

frontend-vue/src/services/api.js:683-723
  createTask()/streamTaskEvents() call ${API_BASE}${V1}/v1/tasks...
  注意：这里按代码片段显示为 V1 + "/v1/tasks"，需要由 frontend agent 复核是否 API_BASE/V1 组合后为期望路径。

gateway/app/main.py:55-95
  app.state mounts execution stores, backend_registry, file_context_resolver,
  route_decision_service, proxy_service, quota_proxy_service,
  conversation_persistence_service, then includes health/admission/tasks/public_proxy/qa routers.

gateway/app/routers/qa.py:621-867
  /api/{mode}/ask and /api/{mode}/ask_stream resolve route/file context,
  precheck quota, proxy to /api/{actual_mode}/ask(_stream), then finalize quota.

public-service/backend/app/modules/conversation/api.py:73-225
  public conversation CRUD and file list/detail/download/delete are served
  at both /api and /api/v1 paths.

public-service/backend/app/modules/quota/api.py:131-147
  internal quota precheck/finalize endpoints are not public API paths.
```

#### 普通 KB QA

```text
frontend-vue askStream()
  -> gateway POST /api/v1/{mode}/ask_stream or /api/{mode}/ask_stream
  -> gateway _resolve()
  -> FileContextResolver decides no file route or KB route
  -> RouteDecisionService normalizes requested_mode/actual_mode/route/source_scope
  -> QuotaProxyService POST public-service /internal/quota/grants/precheck
  -> ProxyService POST backend /api/{actual_mode}/ask_stream
  -> fastQA/highThinkingQA/patent runner
  -> model / retrieval / graph / vector as service-specific runtime
  -> backend SSE content/error/done
  -> gateway _stream_with_quota parses/relays frames and finalizes quota on done
  -> ConversationPersistenceService writes user/assistant/task turn to public-service internal conversation endpoints when enabled
  -> frontend chatStore consumes stream events and persists local projection
```

目标调用链：

```text
frontend route builder
  -> gateway thin QA controller
  -> GatewayAskContract validator
  -> FileContextPolicy chain
  -> Admission/QuotaPolicy
  -> BackendAskClient
  -> StreamRelay + PersistenceAdapter
  -> public-service authority
```

#### 文件 QA

```text
frontend selected/uploaded file metadata
  -> gateway file_context_resolver resolves explicit mention / selected files / recent upload / focus / readiness
  -> normalized execution_files + selected_file_ids + primary_file_id
  -> backend file route:
     fastQA file_route_service or qa_pdf/qa_tabular
     patent file_contract -> executor -> file_routes/pdf_service/tabular_service/hybrid_synthesis
  -> MinIO/local storage resolver and file readiness checks
  -> stream references/done with used_files/file_selection
  -> public-service conversation/file authority persists metadata and messages
```

目标调用链：

```text
FileMentionDetector
  -> FileSelectionResolver
  -> FileReadinessPolicy
  -> ExecutionFileContract
  -> backend-specific FileQARunner
```

#### 表格 QA

```text
frontend pdf_context/source_scope=table
  -> gateway route_decision route=tabular_qa/table_qa
  -> fastQA qa_tabular service or patent tabular service
  -> file materialization / MinIO-only policy
  -> workbook loader
  -> planner
  -> executor
  -> evidence/context builder
  -> LLM synthesis
  -> stream references/done
```

目标调用链：

```text
TabularFileReadiness
  -> WorkbookRunner
  -> TabularPlannerService
  -> TabularExecutorService
  -> HybridEvidenceRetriever
  -> TabularAnswerSynthesizer
  -> TabularEventStreamer
```

#### hybrid QA

```text
frontend source_scope=pdf+table / pdf+kb / table+kb / pdf+table+kb
  -> gateway file_context_resolver + route_decision
  -> backend hybrid runner
  -> pdf branch + table branch + optional KB branch
  -> hybrid synthesis client or fallback rules
  -> merged stream events and file_selection metadata
```

目标调用链：

```text
HybridRoutePolicy
  -> BranchPlanner
  -> EvidenceFanout
  -> HybridSynthesisPort
  -> StreamMergeAdapter
```

#### thinking QA

```text
frontend askStream(mode=thinking)
  -> gateway /api/v1/thinking/ask_stream
  -> highThinkingQA /api/thinking/ask_stream or /api/v1/thinking/ask_stream
  -> ask router handles slot/SSE/gateway-owned persistence headers/thread producer
  -> ask_service builds conversation context, rewrites question, runs agent, builds references and frontend step events
  -> agent_core LLM client
  -> gateway stream relay/persistence or service-owned persistence depending headers/config
```

目标调用链：

```text
ThinkingAskController
  -> StreamController
  -> ThinkingAskRunner
  -> ReferenceBuilder
  -> EventMapper
  -> PersistenceAdapter
```

#### patent QA

```text
frontend askStream(mode=patent, options capability headers)
  -> gateway /api/v1/patent/ask_stream
  -> patent /api/patent/ask_stream or aliases
  -> request_models validates requested_mode/actual_mode/route/source_scope/files
  -> ask router checks durable/file-route/dependency/stream slot/gateway headers
  -> PatentAskService / executor
  -> KB/PDF/table/hybrid/graph/rerank/planning/upstream components
  -> stream events with source_scope/file_selection/references
```

目标调用链：

```text
PatentAskController
  -> PatentGatewayAskContract extends GatewayAskContract
  -> PatentExecutionRouter
  -> PatentDomainRunners
  -> Shared LLM/Rerank/Runtime adapters
```

#### task/recoverable QA

```text
frontend recoverableTaskController
  -> api.createTask()
  -> gateway POST /api/v1/tasks
  -> QATaskService creates task, quota precheck, route/file decision, public task turn
  -> admission queue / queue status / event relay / slot lease / worker claim
  -> backend /api/{actual_mode}/ask_stream with X-Gateway-Task-Execution and persistence headers
  -> gateway records public events and task summary
  -> frontend GET /api/v1/tasks/{task_id}/events?after_seq=N for replay
  -> cancel POST /api/v1/tasks/{task_id}/cancel releases side effects and finalizes quota
```

目标调用链：

```text
TaskController
  -> TaskStateMachine
  -> AdmissionPolicy
  -> QueueBackendPort
  -> TaskRunner
  -> EventRelay
  -> PersistenceCoordinator
```

#### upload/download/file metadata

```text
frontend upload/file APIs
  -> gateway public_proxy routes
  -> public-service conversation/api.py file list/detail/download/delete
  -> conversation service/repository/json_store
  -> storage service resolves MinIO/local storage_ref and temporary proxy files
  -> quota file_view/file_upload dependencies where applicable
```

目标调用链：

```text
frontend uploadApi/fileApi
  -> gateway public BFF pass-through
  -> public-service FileMetadataAuthority
  -> StorageResolver
  -> QuotaAdapter
```

### 3. active / legacy / scaffold / archive 判定

| 代码位置 | 判定 | 证据 | 是否可删 | 前置条件 |
| ---- | -- | -- | ---- | ---- |
| `gateway/app/routers/qa.py` `/api` 与 `/api/v1` QA aliases | active live path | router decorators at `qa.py:834-867`; frontend currently builds v1 stream path in `api.js:626-630` | 否 | 先统一 route builder 与 gateway/frontend contract tests |
| `gateway/app/routers/tasks.py` `/api/v1/tasks*` | active live path behind rollout flag | router registered in `main.py:91-95`; tests in `test_refresh_survivable_task_e2e.py` | 否 | task state machine/event replay/quota tests 固化后才能拆内部 |
| `public-service/backend/app/modules/quota/api.py` internal quota grants | active internal authority | `/internal/quota/grants/precheck` and finalize in `quota/api.py:131-147`; gateway calls via `quota_proxy.py` | 否 | 保持 internal-only route surface test |
| `public-service/backend/app/modules/retrieval/` | active but boundary-overlapping | first-round evidence and docs/tests import retrieval bindings; documents tests use retrieval models | 不能直接删 | 建 public-only mode、迁出执行检索、保留 status/readiness contract |
| `public-service/backend/app/modules/documents/` PDF/model ops | active but boundary-overlapping | documents tests cover OpenAI/PDF/reference preview | 不能直接删 | 先做 feature inventory 和 compatibility proxy |
| `fastQA/app/services/file_route_service.py` MaterialScienceAgent shim | deprecated but referenced | first-round rg found `_build_pdf_agent`/`MaterialScienceAgent`/`smart_query` live path | 否 | 正式 PDF runner parity tests 完成 |
| `fastQA/app/modules/qa_kb/service.py` `FASTQA_NOT_READY` | placeholder but tested | first-round evidence and `fastQA/tests/test_qa_placeholder.py` | 否 | 统一 NotReadyEvent contract，更新 tests |
| `highThinkingQA/server_fastapi/routers/conversation.py` 等 retired routers | deprecated/unregistered 或 unknown by file | first-round registered router check显示 only ask/health；第二轮需逐文件 import/test/script 验证 | 需要进一步验证 | old router 404 contract + import 引用清零 |
| `patent/server_fastapi/routers/ask.py` `/api/ask*` aliases | deprecated but registered | router decorators include non-mode aliases in first-round evidence | 暂否 | gateway/frontend 确认只用 `/api/patent/*` 与 `/api/v1/patent/*` |
| `frontend-vue/src/api/*` alongside `src/services/api.js` | scaffold/deprecated unknown | rg shows `src/api/chat.js` duplicates ask stream route builder | 需要进一步验证 | import graph 确认无 product path 或迁入 canonical clients |
| `frontend-vue/dist/` and `.runtime/` | generated/runtime ignored | repo guidelines mark generated runtime ignored | 不作为重构对象 | 只清理 git ignore 内未跟踪产物，不进入业务重构 |

### 4. 共享包设计补充

建议结构保持第一轮方向，但第二轮补充迁移顺序、抽象接口和回滚：

```text
packages/agent_common/
  config/
  contracts/
  llm/
  runtime/
  sse/
  files/
  storage/
  retrieval/
  observability/
```

| 模块 | 要抽什么 | 来自哪些服务 | 先迁哪个服务 | 如何测试 | 如何回滚 |
| -- | ---- | ---- | ---- | ---- | ---- |
| `config/` | env loader、service roots、LLM/embedding/rerank/Redis/MinIO/Neo4j/http timeout/internal token settings | gateway `core/config.py`；fastQA `core/config.py/env_loader.py`；highThinkingQA `config.py/env_loader.py`；patent `config.py`；public-service settings | highThinkingQA env_loader 和 fastQA generation runtime 的只读 adapter 先行 | env override/default precedence tests；legacy alias tests | 保留本地 config façade，env 仍由本地读取，失败时切回 local loader |
| `contracts/` | requested_mode/actual_mode/route/source_scope/turn_mode/execution_files/task headers/quota grant/conversation authority schemas | gateway normalized payload；fastQA `request_adapter.py`；patent `request_models.py`/`file_contract.py`；public-service `conversation/authority_schemas.py`；frontend route metadata | 先抽 fastQA + patent 的 shared validator，gateway 只调用不改 payload shape | contract tests 覆盖 accepted/rejected payload；status/error message golden | 本地 adapter 捕获 shared validation error 并映射旧错误 code |
| `llm/` | OpenAI-compatible client、auth strategy、HTTP pool、stream parser、retry/timeout、model call logging、upstream auth logger、embedding/rerank clients | fastQA `integrations/llm/openai_compat.py`；highThinkingQA `agent_core/openai_compat.py`/`llm_client.py`；patent upstream/rerank/planning clients | 先抽 stream parser/auth headers/model-call logger，不先替换完整 client | fake httpx transport tests；stream chunk parser tests；retry/timeout matrix | 每服务保留 local client，shared client behind env flag |
| `runtime/` | ServiceContainer、ResourceRegistry、ComponentStatusRegistry、LifecycleManager、task queue ports | gateway app.state/execution stores；patent bootstrap; fastQA app.state; highThinkingQA slot/lifespan | patent ResourceRegistry 先行，因为 bootstrap 最集中 | lifespan failure/partial close tests；component status snapshot tests | app.state compatibility shim 保留一个 release |
| `sse/` | SSE frame encoder/parser、error/done event schema、stream relay helpers | gateway `sse_frames.py`/qa_tasks frame parser；fastQA stream contract；highThinkingQA ask SSE; patent stream events；frontend parser | gateway + frontend parser contract 先行 | first-byte、malformed frame、done/error golden tests | 旧 parser 保留，双跑比较事件 |
| `files/` | file mention metadata DTO、file readiness policy、execution file normalizer、DOI/patent id normalizer | gateway file_context_resolver；fastQA file_route/materializer；patent file_contract；public-service storage/conversation files | 先抽 pure DTO/normalizer，不迁自然语言启发式 | DOI/patent/object-name golden tests；file readiness matrix | 共享 normalizer 只作校验，不改变原始 payload |
| `storage/` | storage_ref parser、object name policy、MinIO/local resolver interface | public-service storage service；fastQA storage service；patent file/runtime storage usage | public-service storage_ref parser 先行 | golden storage_ref/object_name compatibility tests | 保留旧 object naming；shared parser only read/validate |
| `retrieval/` | retrieval status/readiness DTO、query result DTO、health contract，不抽业务检索策略 | public-service retrieval; fastQA retrieval/generation/graph; patent retrieval/graph | public-service 先只保留 status/readiness contract，执行检索迁出另议 | public-only startup test；retrieval disabled/degraded health tests | public-service router 兼容转发到旧 service |
| `observability/` | trace id、request id、auth key fingerprint、model call metrics、component status fields | gateway logging；fastQA model call logger；patent upstream auth logging；highThinkingQA auth logging | 先统一字段名，不改调用逻辑 | snapshot log metadata tests | 本地 logger wrapper 映射到旧字段 |

### 5. 巨型模块拆分计划补充

| 文件 | 当前职责 | 拆分目标 | 优先级 | 测试保护 |
| -- | ---- | ---- | --- | ---- |
| `frontend-vue/src/stores/chatStore.js` | conversation/messages/uploads/selected files/streaming/busy runtime/task recovery/localStorage/KB/session/mode | `conversationStore`、`messageStore`、`streamStore`、`uploadStore`、`taskStore`、`kbStore`、`runtimeStore`；保留 `useChatStore` façade | P3，先补 tests | `src/stores/*.test.js`、`recoverableTaskController.test.js`、Home structure/E2E |
| `gateway/app/services/qa_tasks.py` | task API service、state machine、worker、SSE parser、quota、persistence、cancel/recover | `TaskRepository`、`TaskStateMachine`、`TaskRunner`、`TaskEventPublisher`、`TaskPersistenceCoordinator`、`TaskCancellationService`、`QueueBackendPort` | P0 先加 tests，P3 拆 | `test_task_api.py`、`test_refresh_survivable_task_e2e.py`、`test_execution_*` |
| `gateway/app/services/file_context_resolver.py` | mention detection、ordinal/recent/focus/DOI/generic guard/readiness/clarification | `FileMentionDetector`、`OrdinalReferenceResolver`、`RecentUploadResolver`、`DOIIntentGuard`、`GenericKnowledgeGuard`、`FileReadinessPolicy`、`ClarificationBuilder` | P2/P3 | `test_file_context_resolver.py`、route_decision/qa_proxy fixtures |
| `gateway/app/services/execution_admission.py` | user/session/global/backend capacity、fairness、queue state, admission outcomes | `AdmissionPolicy`、`CapacityPolicy`、`FairnessPolicy`、`AdmissionDecision`、`QueueBackendAdapter` | P1/P2 | execution admission/queue/slot lease/event relay tests |
| `fastQA/app/routers/qa.py` | HTTP routes、contract normalize、KB/PDF/table/hybrid/graph dispatch、stream/error/persistence hooks | `routers/qa.py` thin, `services/qa_dispatcher.py`, `runners/kb_runner.py`, `pdf_runner.py`, `tabular_runner.py`, `hybrid_runner.py`, `graph_kb_runner.py`, `stream_event_mapper.py`, `error_event_builder.py` | P3 | `test_qa_route_aliases.py`、`test_request_adapter.py`、stream contract/placeholder/file mode tests |
| `fastQA/app/integrations/llm/openai_compat.py` | auth header、HTTP pool、stream parser、logging、thinking controls、retry/timeout | `agent_common.llm.auth`、`http_pool`、`stream_parser`、`retry_policy`、`model_call_logger`、service wrapper | P2 | `test_llm_openai_compat.py`、shared pool/hot lane/thinking tests |
| `fastQA/app/modules/qa_tabular/service.py` | file readiness、MinIO policy、workbook loading、planner/executor/evidence/synthesis/stream | `file_readiness.py`、`workbook_runner.py`、`tabular_planner_service.py`、`tabular_executor_service.py`、`hybrid_evidence_retriever.py`、`tabular_answer_synthesizer.py`、`tabular_event_streamer.py` | P3 | tabular service/planner/executor, workbook storage ref, file routes tests |
| `patent/server_fastapi/app.py` | app factory、bootstrap all resources、health status、manual close | `PatentBootstrapper`、`PatentServiceContainer`、`ResourceRegistry`、`ComponentStatusRegistry`、`LifecycleManager` | P1/P2 | fastapi contract health/ask tests; lifespan failure close tests |
| `highThinkingQA/server_fastapi/routers/ask.py` | HTTP route、slot、SSE/sync response、headers、persistence、thread/disconnect/error | `AskController`、`StreamController`、`PersistenceAdapter`、`GatewayHeaderAdapter`、`ErrorMapper` | P2/P3 | route surface, stream done/error/disconnect, persistence ownership tests |
| `highThinkingQA/server/services/ask_service.py` | context/rewrite/runtime/agent call/reference/DOI/frontend text/done | `ThinkingAskRunner`、`ReferenceBuilder`、`EventMapper`、`FrontendTextAdapter`、`DoneEventBuilder` | P3 | snapshot SSE/reference contract; sync/stream parity |
| `public-service/backend/app/modules/conversation/service.py` | CRUD/message/file metadata/download/delete/task echo/patent renderer/json/db/cache/storage | `ConversationService`、`MessageService`、`UploadedFileAuthority`、`ConversationJsonCoordinator`、`TaskEchoProjector`、`PatentCitationPresenter` | P1/P2 | conversation authority API, file compensation, assistant inbox/outbox, JSON/DB/cache consistency |

### 6. 接口契约统一计划补充

| 契约 | 当前分散位置 | 统一目标 | 兼容策略 | 回滚策略 |
| -- | ---- | ---- | ---- | ---- |
| frontend -> gateway ask stream | `frontend-vue/src/services/api.js`、`src/api/chat.js`、gateway QA routes | `qaStreamRoute(mode)` and `GatewayAskRequest` payload builder | 先保留 `/api` 与 `/api/v1` aliases；新增 route builder tests | 前端 env flag 切回旧 path builder |
| frontend -> gateway task events | `api.createTask/getTask/streamTaskEvents`、`recoverableTaskController`、gateway tasks router | `TaskApiClient` + `TaskEvent` schema | 保持 response shape；修正路径前加 structure test | 保留 legacy createTask wrapper |
| gateway -> backend ask | gateway `_normalized_payload`、fastQA request_adapter、patent request_models、highThinking request_models | `GatewayAskRequest` + service-specific policy extension | shared validator 不直接改错误文案；本地 adapter 映射旧 code | 关 shared validation flag，回 local adapter |
| backend -> public-service conversation authority | gateway `conversation_persistence.py`、public-service `conversation/internal_api.py`、backend service-owned persistence | `ConversationAuthorityClient` + task turn protocol | 单写者策略：gateway-owned 优先，service-owned shadow 只读/降级 | 保留 service-owned persistence until gateway coverage proven |
| gateway task events | gateway `qa_tasks.py` public event builder、frontend recovery parser、public-service task payload | `TaskSummary` + `TaskEvent` contract | after_seq/replay/cancel fields golden tests | 保留 old event mapper behind adapter |
| SSE stream events | gateway stream relay、backend event mappers、frontend parser | `StreamEvent` union: `content/error/done/status/references/route/file_selection` | 后端继续发 `message`，新增 stable `code/data` 字段 | 前端 fallback 展示旧 `message` |
| file execution contract | gateway file_context_resolver、fastQA request_adapter/file_route_service、patent file_contract、public-service file metadata | `ExecutionFile` + `FileSelection` + `SourceScope` | 先只校验 selected ids and readiness，不改 NLP heuristics | shared contract failure falls back to old resolver |
| quota grant contract | gateway quota_proxy、public-service quota/api/service、conversation/deps local quota | `QuotaPrecheckRequest`、`QuotaFinalizeRequest`、`QuotaGrantResult` | internal endpoints remain internal-only; idempotent finalize tests | local quota deps stay for non-gateway public routes |
| auth/internal token headers | gateway auth/quota/proxy、public-service internal APIs、backend gateway headers | `InternalAuthHeaders` + `GatewayTaskHeaders` | accept old and canonical case-insensitive headers | service-specific header parser remains |

### 7. 测试策略补充

| 层级 | 必补测试 | 覆盖重构阶段 |
| ---- | ---- | ---- |
| unit tests | env loader precedence、source_scope validator、execution_file normalizer、storage_ref/object_name parser、SSE frame parser、quota finalize idempotency | P0/P1 |
| contract tests | frontend route builder vs gateway route table；gateway -> fast/thinking/patent payload；public-service internal conversation/quota schemas | P0/P1 |
| router tests | `/api` and `/api/v1` aliases；removed/deprecated routers 404；public proxy parity；patent ask aliases | P0/P1 |
| SSE stream tests | first-byte, content/error/done, malformed frame, quota done injection, backend non-SSE error, stream disconnect | P1/P2 |
| task recovery tests | create -> admitted/running/completed; after_seq replay; cancel; expired/reconcile; quota finalize failure; public detail active_task enrichment | P0/P2 |
| file route tests | explicit file mention, ordinal/recent upload, selected_file_ids, primary_file_id, MinIO-only, processing/failed/storage_ref missing, PDF/table/hybrid branches | P1/P3 |
| quota/auth tests | internal token required, public endpoints do not expose internal quota, precheck/grant/finalize idempotent, local quota deps for public download/upload | P0/P1 |
| persistence tests | gateway-owned vs service-owned single writer, user message start, assistant progress/terminal, rollback-create, JSON/DB/cache/storage consistency | P0/P2 |
| integration smoke tests | frontend -> gateway -> each backend ask_stream; upload -> file metadata -> file QA; public-only startup without retrieval side effects | P1/P2 |
| backward compatibility tests | legacy `/api/ask*`, `/api/{mode}/ask*`, frontend old API façade, FASTQA_NOT_READY code, MaterialScienceAgent shim behavior | P0/P3 |

### 8. 重构路线图修正

P0：只做低风险整理与保护网。

- 标记 active/legacy/archive：对 highThinkingQA retired routers、fastQA shim/placeholder、frontend duplicate API façade、patent deprecated aliases 做 import/test/script/frontend/gateway 引用表。
- 清理重复依赖仅限确认无行为影响项；`pyproject/package.json` 改动前先补 install/route tests。
- 建立 `agent_common` 空包骨架的计划可以进入后续实现，但本轮不创建。
- 补 contract tests：gateway route table、frontend route builder、quota internal-only、task event replay、backend ask payload。

P1：抽共享配置和 contract。

- `config/env_loader/service_roots/model settings/redis/storage/neo4j` 先以 façade 方式导入，不直接删本地 config。
- `contracts/GatewayAskRequest/ExecutionFile/StreamEvent/QuotaGrant/ConversationAuthority` 先由 fastQA + patent 使用，gateway 保持 payload 兼容。
- public-service retrieval/documents 先增加 public-only mode 和 health/status contract，暂不迁执行逻辑。

P2：抽共享 LLM/runtime。

- 先抽 OpenAI-compatible stream parser、auth strategy、HTTP pool、model call logger，再抽完整 client。
- ResourceRegistry/ServiceContainer 先落 patent/gateway bootstrap，保留 app.state compatibility shim。
- ComponentStatusRegistry 统一 health/status 字段。

P3：拆巨型业务模块。

- gateway `qa_tasks.py`、`file_context_resolver.py`、fastQA `qa.py`/`qa_tabular`、frontend `chatStore.js`、patent bootstrap/file routes、highThinking ask router/service 分阶段拆。
- 每次拆分保持 public façade/API 不变，先迁内部调用，最后再整理 imports。

### 9. 不建议立即做的事情

- 不建议一开始替换 gateway task queue。业务策略包括同会话互斥、用户并发限制、全局容量、backend capacity、thinking 防饿死、task recovery event relay；这些要先抽成 policy，再替换 queue/lease/claim/retry backend。
- 不建议直接删除 fastQA `FASTQA_NOT_READY` placeholder 或 MaterialScienceAgent compatibility shim；二者有测试或 live path 依赖，先引入正式 runner 和兼容 error contract。
- 不建议直接删除 highThinkingQA 未注册 router 文件；先完成 import/test/script 引用验证，并补 removed-route 404 contract。
- 不建议同时修改 frontend、gateway、backend 的 ask contract；先用 shared validator 双跑，保留旧 payload shape。
- 不建议先改业务算法再抽共享基础设施；LLM/retrieval/graph/tabular 输出行为很容易被前端和引用格式依赖。
- 不建议把 public-service retrieval/documents 越界逻辑一次迁走；先做 feature inventory、public-only startup、compatibility proxy。
- 不建议直接统一 LLM client 的 retry/timeout；三服务 upstream 压力、thinking controls、日志字段和 stream parser 语义不同。
- 不建议把 README/scaffold 说法作为事实；必须以 router 注册、import、tests、frontend/gateway 调用为准。

## 第三轮证据闭环总览

### 1. 未确认项关闭情况

| 来源文档 | 未确认项 | 本轮结论 | 证据 | 是否关闭 |
| ---- | ---- | ---- | -- | ---- |
| gateway | public proxy 是否承接 `view_pdf`/literature/reference/model-status 等 public-service 路径 | gateway 当前确实把这些 public path 纳入 public proxy/route table，不能作为 dead code 删除；路径收敛只能作为兼容迁移任务 | `gateway/app/routers/public_proxy.py:241-264`、`gateway/app/services/route_table.py:39-63` | closed |
| gateway | `/api` 与 `/api/v1` alias 是否能立即收敛 | 不能立即收敛；frontend 和 public proxy 均存在 `/api` 使用，public-service tests 也固定双路径 | `frontend-vue/src/api/chat.js:77-80`、`gateway/app/routers/public_proxy.py:241-257`、`public-service/backend/tests/test_route_surface.py` | closed |
| gateway | admission worker 是否可直接替换为成熟队列 | 仍不可直接替换；tests 大量覆盖 dispatcher/claim/requeue/lease/starvation 等策略，第一批只能提取 policy contract 与 state-machine tests | `gateway/tests/test_execution_admission.py` 覆盖 thinking starvation、capacity、claim、requeue、worker failure | partially closed |
| public-service | `/api/v1/literature_content`、`/api/v1/reference_preview`、`/api/admin/model-status` 是否仍 live | live/compat：public-service router 和 tests 明确覆盖，gateway route table 也代理；迁出前必须提供 compatibility proxy | `public-service/backend/app/modules/documents/service.py:841-961`、`public-service/backend/app/modules/system/api.py:38-44`、`gateway/app/routers/public_proxy.py:251-264` | closed |
| public-service | documents/retrieval 是否可直接移出 public-service | 不可直接移出；documents API 仍依赖 runtime/agent metadata，tests 覆盖 reference/literature/quota 行为，需先做 feature inventory 和 parity proxy | `public-service/backend/tests/test_documents_module.py`、`public-service/backend/app/modules/documents/service.py:841-961` | closed |
| public-service | upload-processing worker 是否可迁出 | partially closed：upload/file metadata 是 public authority，但 processing/index 状态有 tests 固定；需要先抽 queue/metadata contract | `public-service/backend/tests/test_uploads_module.py` 覆盖 upload_pdf/upload_excel/clear_pdf/status | partially closed |
| fastQA | `file_routes.py` 与 `file_route_service.py` 双入口 | `file_routes.py` 是 live runner 入口，但仍调用 `file_route_service._build_pdf_agent()` 提供 PDF KB verification 兼容；不能删 shim | `fastQA/app/services/file_routes.py:216-224` | closed |
| fastQA | `FASTQA_NOT_READY` 是否可删 | 不能直接删；同码承载 placeholder 与 runtime degraded 两种语义，需先统一 NotReadyEvent contract 并保留兼容码 | `fastQA/app/modules/qa_kb/service.py:41`、`fastQA/app/routers/qa.py` 第二轮证据 | closed |
| fastQA | Graph legacy parity 是否阻塞删除 | partially closed：tests 命名和 graph_kb parity 覆盖仍存在，删除需先标记 classic parity gate | `fastQA/tests/test_fastqa_kb_graph_integration.py`、`fastQA/tests/test_graph_kb_*` | partially closed |
| highThinkingQA | retired routers 是否注册 | closed：注册入口只 include health/ask，documents/upload/system 等 retired routers 未注册；已有 fastapi migration tests 断言 404 | `highThinkingQA/server_fastapi/routers/__init__.py:5-11`、`highThinkingQA/tests/fastapi_migration/test_fastapi_route_surface_minimal.py` | closed |
| highThinkingQA | ask payload 是否仍生成 retired `view_pdf` path | closed：本地 documents router 未注册，但 `ask_service` 仍生成 `/api/v1/view_pdf/{doi}`，该 URL 现在由 gateway/public-service 承接 | `highThinkingQA/server/services/ask_service.py:355-365`、`gateway/app/routers/public_proxy.py:257` | closed |
| highThinkingQA | `max_retries` 是否未生效 | closed：OpenAI-compatible client 构造函数接收 `max_retries` 后 `del max_retries`，调用端 tests 只能证明参数传入 wrapper，不能证明 client 生效 | `highThinkingQA/agent_core/openai_compat.py:361-364`、`highThinkingQA/tests/test_llm_client.py` | closed |
| patent | gateway 是否仍可能走非 prefix patent ask | partially closed：gateway route decision 已输出 patent mode，patent 服务仍注册 non-prefix alias；需服务文档补 gateway backend path cutover 表 | `patent/server_fastapi/routers/ask.py` 第二轮证据、`gateway/app/routers/qa.py` route payload | partially closed |
| patent | original route 是否可下线 | 不能直接下线；gateway/public proxy 已有 `/api/patent/original/{canonical_patent_id}`，patent local original 503 route 是兼容错误面 | `gateway/app/routers/public_proxy.py:253`、`patent/server_fastapi/routers/original.py` 第二轮证据 | closed |
| patent | ResourceRegistry 是否是 lifecycle registry | closed：第二轮证据显示 `PatentResourceRegistry` 是路径/资源发现，不是 closeable registry；第一批可新增 LifecycleManager 但保留 app.state alias | `patent/server/patent/resource_registry.py` 第二轮证据 | closed |
| frontend-vue | `src/api/*` 与 `src/services/api.js` 是否双门面 | closed：`src/api/chat.js`、`src/api/literature.js` 与 canonical `src/services/api.js` 并存，tests/components 仍引用，需 facade 兼容层 | `frontend-vue/src/api/chat.js:77-80`、`frontend-vue/src/api/literature.js`、`frontend-vue/src/services/api.js` | closed |
| frontend-vue | `/api` vs `/api/v1` canonical path | closed：前端 `src/api/chat.js` 使用 `/api/{mode}/ask_stream`，services API 使用 `V1='/api'` 命名，不能直接切 `/api/v1` | `frontend-vue/src/api/chat.js:67-80`、`frontend-vue/src/services/api.js` | closed |
| frontend-vue | backend 中文阶段文案是否被依赖 | partially closed：前端有 stage timing/message fallback tests，后端 thinking service 仍发中文阶段文案；迁移需 event code + message dual field | `frontend-vue/src/utils/stageTimings.test.js`、`highThinkingQA/server/services/ask_service.py` 第二轮证据 | partially closed |

### 2. 可立即进入重构的任务

| 任务 | 服务 | 为什么可做 | 先补/固定测试 | 回滚方式 |
| ---- | ---- | ---- | ---- | ---- |
| 建立 `packages/agent_common` MVP 空包骨架，仅放 DTO/utility，不替换 live path | cross-service | 不改变运行行为，只为后续 contract tests 提供目标命名空间 | package import smoke；不接入 live 服务 | 删除包骨架与 import smoke |
| 抽/复制 `SSEFrameBuffer` 与 stream event schema tests，先作为 contract fixture | gateway/fastQA/frontend | 当前 gateway、fastQA、frontend 均有 SSE/event schema 依赖，抽测试 fixture 风险低 | gateway SSE done/quota 注入；fastQA stream_contract；frontend SSE parser | 恢复各服务本地 fixture |
| 为 highThinkingQA retired routes 增补 404/route-surface contract tests | highThinkingQA | 注册入口已收缩到 ask/health，测试只固定现状，不删代码 | `/api/v1/upload_pdf`、`/api/v1/view_pdf/{doi}`、admin/auth/quota/system 404 | 回滚新增 tests |
| public-service documents/retrieval feature inventory | public-service | 文档/测试整理，不迁代码；满足 public-service-migration-review 的迁出前置 | reference_preview/literature_content/model-status route contract tests | 回滚 inventory 文档段 |
| frontend API path builder tests | frontend-vue | 不拆 store，只冻结 `/api`/`/api/v1` 兼容行为 | ask path builder、task flag、literature path、upload path tests | 回滚 tests |
| patent app.state consumer inventory + LifecycleManager design tests | patent | 先加 inventory/contract tests，保留 app.state aliases，不动 bootstrap 行为 | app.state alias smoke、LIFO close unit test | 删除新 manager 并保留旧 aliases |

### 3. 必须先补测试的任务

| 任务 | 阻塞测试 | 原因 |
| ---- | ---- | ---- |
| gateway task queue/lease 替换评估 | task create/replay/cancel/terminal persistence；admission worker 与 ordinary ask_stream 隔离；Redis unavailable fallback | 当前 tests 固定很多策略，但缺普通 ask 与 task 边界的端到端护栏 |
| gateway quota finalize 注入 done event 拆分 | first chunk latency；done schema；finalize fail compensation；quota precheck/finalize contract | 拆 SSE 与 persistence 可能造成首包延迟或 done 丢失 |
| fastQA PDF legacy shim 删除 | MaterialScienceAgent shim compatibility；PDF/hybrid execution_files；legacy `smart_query` parity | `file_routes.py` 仍调用 `_build_pdf_agent()`，删除前必须有正式 PDF runner parity |
| fastQA `FASTQA_NOT_READY` 统一 | placeholder path 与 runtime-degraded path 双 golden events | 同码不同语义，直接改会破坏 frontend/gateway 兼容 |
| public-service documents/retrieval 迁出 | literature/reference/model-status/upload-processing compatibility proxy tests | 这些 API live 且 gateway/front/tests 使用，迁出必须零行为丢失 |
| frontend `chatStore.js` facade 拆分 | public API snapshot；multi-chat streaming；task recovery；localStorage durable projection | store 是组件事实入口，不能先拆再补测试 |
| highThinkingQA service-owned persistence 删除 | gateway-owned headers；invalid internal auth；shadow_public_service；disconnect/timeout terminal | 生产配置仍可能走 legacy/shadow，需固定切换条件 |
| patent non-prefix `/api/ask*` 下线 | gateway cutover contract；deprecated alias tests；external client inventory | 服务仍注册 alias，无法证明外部无调用 |

### 4. 不允许立即处理的任务

| 任务 | 不允许原因 | 解除条件 |
| ---- | ---- | ---- |
| 直接替换 gateway 手写 queue/lease/worker | 业务策略与底层机制交织，且 tests 覆盖 starvation/capacity/requeue/claim | 先抽 AdmissionPolicy/CapacityPolicy/TaskStateMachine contract，跑全量 queue contract tests |
| 直接删除 fastQA MaterialScienceAgent shim | `file_routes.py` live path 仍通过 `_build_pdf_agent()` 获取 `smart_query()` | 正式 PDF runner 支持 KB verification，legacy compatibility tests 通过 |
| 直接删除 fastQA `FASTQA_NOT_READY` | placeholder 与 runtime-not-ready 语义未拆 | 新 NotReadyEventFactory 与 frontend/gateway 兼容测试完成 |
| 直接迁出 public-service retrieval/documents live APIs | gateway proxy、frontend、public-service tests 均引用 | compatibility proxy + feature inventory + public-only degraded health tests 完成 |
| 直接统一三服务 LLM client | highThinkingQA `max_retries` 未生效、fastQA pool/thinking controls、patent planning/rerank 差异大 | 先做逐字段 adapter matrix 和 timeout/auth/stream parser contract |
| 直接切换 `/api` 到 `/api/v1` | 前端、gateway public proxy、public-service tests 都仍使用 `/api` alias | 双路径 telemetry/usage 确认后，先 deprecation header，再 cutover |
| 直接拆 frontend `chatStore` public API | 组件/composable/tests 直接依赖大量 state/action | public API snapshot 与 facade 兼容层就绪 |
| 直接删除 highThinkingQA retired services | router 未注册已闭环，但 service/schema/storage 可能仍被 tests/scripts/import 引用 | 每个 retired file 的 import/test/script 表清零或迁移到 public-service/patent/frontend |

### 5. dead-code 删除候选最终判定

| 代码位置 | 最终判定 | 删除安全性 | 前置条件 |
| ---- | ---- | ---- | ---- |
| `highThinkingQA/server_fastapi/routers/conversation.py`、`upload.py`、`admin.py`、`auth.py`、`documents.py`、`ingest.py`、`quota.py`、`system.py` | deprecated unregistered | 暂不直接删；可第一批补 removed-route tests 与 import inventory | import/test/script 引用验证闭环，public-service 已承接契约 |
| `fastQA/app/services/file_route_service.py` 中 `_build_pdf_agent`/`smart_query`/`query_pdf_directly` | deprecated but referenced | 不可删 | 正式 PDF runner parity + `file_routes.py` 不再 import |
| `fastQA/app/modules/qa_kb/service.py` placeholder | placeholder but tested | 不可删 | NotReadyEvent contract 统一 |
| `patent/server_fastapi/routers/ask.py` non-prefix `/api/ask*` | deprecated but registered | 不可删 | gateway/frontend/external cutover 到 `/api/patent/*` 和 `/api/v1/patent/*` |
| `patent/server_fastapi/routers/original.py` local 503 route | deprecated but registered | 暂不删 | public-service/gateway original route ownership 固定 |
| `public-service/backend/app/modules/retrieval/` | active but boundary-overlapping | 不可删，只能迁出或 proxy | public-service 禁用 model deps 仍 health 可用的 tests |
| `public-service/backend/app/modules/documents/reference_preview.py` 和 literature content | active but boundary-overlapping | 不可删 | document-processing/retrieval compatibility proxy |
| `frontend-vue/src/api/*` 与 `features/chat/controls` | scaffold/deprecated/partially live | 暂不删 | import graph 与产品入口确认；保留 facade |
| README/pyproject scaffold drift | archive/doc drift | 可作为低风险文档修正任务，但本审计不改源码/README | 开发阶段另起 docs chore |

### 6. 共享包首批迁移边界

第三轮把 `agent_common` MVP 明确压缩为 contract/utility，不直接迁 LLM、retrieval、storage、queue。

```text
packages/agent_common/
  contracts/
    qa_route.py
    source_scope.py
    turn_mode.py
    execution_file.py
    stream_event.py
  sse/
    frames.py
  runtime/
    resource_registry.py
  config/
    env_utils.py
```

| MVP 模块 | 首批只抽什么 | 来自哪些服务 | 先迁哪个服务 | 测试与回滚 |
| ---- | ---- | ---- | ---- | ---- |
| `contracts/qa_route.py` | `requested_mode`/`actual_mode`/`route` literal 与 alias normalize，不改变 live payload | gateway、fastQA、patent | 先以 tests/fixtures 引入 gateway/fastQA，不替换 runtime | contract tests 失败即回滚 import，保留本地常量 |
| `contracts/source_scope.py` | `kb/pdf/table/pdf+kb/pdf+table+kb` normalize 与 token parser | gateway、fastQA、patent | fastQA request_adapter tests 先接 | 保留本地 fallback parser |
| `contracts/turn_mode.py` | `kb_only/file_only/mixed` literal 与 route-source validation helper | gateway、fastQA、patent | patent request_models 先以 shadow validation 对比 | mismatch 只记录不阻断，回滚 helper |
| `contracts/execution_file.py` | `file_id/file_type/storage_ref/local_path/status` DTO 和 readiness enum | gateway file resolver、fastQA/patent file runners、public-service file metadata | gateway tests 先生成 fixture，不替换 resolver | fixture-only rollback |
| `contracts/stream_event.py` | `metadata/progress/done/error/clarification` schema fixture | gateway、fastQA、highThinkingQA、patent、frontend | gateway + frontend SSE tests 先固定 | 保留服务本地 event mapper |
| `sse/frames.py` | `SSEFrameBuffer` 或等价 parser fixture | gateway `sse_frames.py`、frontend SSE parser、fastQA stream contract | gateway 先抽 tests，再抽代码 | 回滚到 gateway 本地实现 |
| `runtime/resource_registry.py` | LIFO close registry，不接管 app.state | patent/fastQA/highThinkingQA/gateway | patent bootstrap 先做 aliases-safe wrapper | app.state aliases 保留即可回滚 |
| `config/env_utils.py` | bool/int/list env parsing，service roots helper | gateway/fastQA/highThinkingQA/patent/public-service | tests-only adoption，再逐服务替换 | 保留旧 env_loader |

暂不进入 MVP：

- LLM/OpenAI-compatible client、embedding、rerank、HTTP pool：差异太大，第三轮只要求矩阵与 contract tests。
- retrieval/storage object resolver：public-service 与 QA backend 权威边界未完全冻结。
- gateway queue backend：业务 policy 还未抽出，不能先换库。

### 7. 第一个重构 Sprint 建议

| 任务名 | 涉及服务 | 目标 | 风险 | 必须先补测试 | 验收标准 | 回滚方式 |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| S1-1：创建 `agent_common` MVP 骨架与 contract fixture | cross-service | 建立 contracts/sse/runtime/config 空包和只读 fixture，暂不替换 live path | 低：可能引入路径/packaging 噪声 | import smoke；no-live-path-change check | 服务启动代码无 import 变更，contract fixtures 可被 tests 引用 | 删除包与新增 tests |
| S1-2：冻结 gateway SSE/quota/task 护栏 | gateway/frontend | 为 first chunk、done 注入、task replay/cancel、Redis fallback 增补 contract tests | 中：测试需 mock upstream/public-service | gateway SSE done/quota、task terminal persistence、public quota contract | 不改行为时 tests 全绿；失败指出真实缺口 | 回滚 tests |
| S1-3：highThinkingQA retired route closure tests | highThinkingQA | 固定只注册 ask/health，retired HTTP paths 404 | 低 | route surface + removed paths 404 | 所有 retired routes 状态表与 tests 一致 | 回滚 tests |
| S1-4：fastQA file route/placeholder safety tests | fastQA | 固定 `file_routes.py` live、legacy shim 兼容、`FASTQA_NOT_READY` 双语义 | 中：需覆盖 monkeypatch-heavy runner | PDF/hybrid execution_files、shim compatibility、not-ready golden events | 任何删除 shim/placeholder 会先红测 | 回滚 tests |
| S1-5：public-service boundary inventory + degraded health tests | public-service | 把 documents/retrieval/upload-processing 的 public vs QA execution 边界写成可验收 inventory | 中：需要 mock runtime/agent/model deps | public-service disabled retrieval/model deps health；documents compatibility proxy tests | 禁用 model deps 后 auth/quota/conversation health 不受影响 | 回滚 tests/inventory |
| S1-6：frontend API façade/path tests | frontend-vue | 冻结 `src/services/api.js`/`src/api/*` 现状，准备 façade 迁移 | 低到中：测试环境可能受 npm runner 影响 | ask path builder、task flag、localStorage projection、event code/message fallback | 不拆 store，先得到 public API snapshot | 回滚 tests |
| S1-7：patent app.state consumer + alias-safe lifecycle spike | patent | 只做 inventory 和 LifecycleManager unit test，不替换 bootstrap | 中：bootstrap eager resource 多 | app.state alias smoke、LIFO close、deprecated `/api/ask*` alias tests | 新 container 不破坏旧 app.state consumer | 删除 spike，保留旧 bootstrap |
