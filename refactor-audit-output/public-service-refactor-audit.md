# public-service 重构审计文档

> 状态：已完成第一轮只读审计。本文档只记录基于代码阅读得到的证据，审计产物位于独立目录，不修改业务代码。

## 1. 审计范围

- 已阅读目录：`public-service/backend/app/`、`public-service/backend/tests/`、`public-service/public-modules/`、`public-service/backend-dependency-map.md`。
- 已阅读关键文件：`backend/app/main.py`、`backend/app/core/runtime.py`、`backend/app/modules/conversation/`、`quota/`、`documents/`、`storage/`、`retrieval/`、`auth/`、`system/`、`uploads/`、`admin_users/`。
- 未覆盖或需要本地进一步验证的范围：未运行测试；`/api/v1/literature_content`、`/api/v1/reference_preview`、`/api/admin/model-status` 的真实前端/运维调用量需要日志或调用图确认。

## 2. 当前 live path

### 2.1 服务入口

- app factory / main entry：`public-service/backend/app/main.py:create_app()`。
- router 注册位置：`DEFAULT_ROUTERS` 注册 `system`、`auth`、`admin_users`、`departments`、`personnel`、`quota`、`conversation`、`conversation_internal`、`documents`、`uploads`。
- lifespan/startup/shutdown：FastAPI 使用 `app.core.runtime.lifespan`，`create_runtime()` 里启动 DB/Redis/storage/services/retrieval/upload-processing。

关键证据：

```python
DEFAULT_ROUTERS: tuple[APIRouter, ...] = (
    system_router,
    auth_router,
    admin_users_router,
    departments_router,
    personnel_router,
    quota_router,
    conversation_router,
    conversation_internal_router,
    documents_router,
    uploads_router,
)
```

```python
agent: Any | None = None
generation_runtime: Any | None = None
vector_db_client: Any | None = None
vector_collection: Any | None = None
neo4j_client: Any | None = None
answer_cache: dict[str, Any] = field(default_factory=dict)
current_answer_context: str = ""
```

### 2.2 对外接口路径

| 接口路径 | 方法 | 所在文件 | 当前职责 | 是否 active |
|---|---|---|---|---|
| `/` | GET | `backend/app/main.py` | service index | active live path |
| `/health`, `/api/health`, `/api/v1/health` | GET | `modules/system/api.py` | health/status | active live path |
| `/api/background_status`, `/api/v1/background_status` | GET | `modules/system/api.py` | background status | active live path |
| `/api/admin/model-status` | GET | `modules/system/api.py` | model endpoint inventory | active live path |
| `/api/admin/model-status/test` | POST | `modules/system/api.py` | embedding/rerank/model probe | active live path / QA ops overlap |
| `/api/kb_info`, `/api/v1/kb_info` | GET | `modules/system/api.py` | KB/vector/graph info | active live path / QA ops overlap |
| `/api/refresh_kb`, `/api/v1/refresh_kb` | POST | `modules/system/api.py` | KB refresh | active live path / QA ops overlap |
| `/api/clear_cache`, `/api/v1/clear_cache` | POST | `modules/system/api.py` | cache clear | active live path |
| `/api/v1/auth/login`, `/api/auth/login` | POST | `modules/auth/api.py` | login | active live path |
| `/api/v1/auth/register`, `/api/auth/register` | POST | `modules/auth/api.py` | register | active live path |
| `/api/v1/auth/me`, `/api/auth/me` | GET | `modules/auth/api.py` | current user | active live path |
| `/api/v1/auth/password`, `/api/auth/password` | PUT/POST | `modules/auth/api.py` | password change | active live path |
| `/api/v1/conversations`, `/api/conversations` | GET/POST | `modules/conversation/api.py` | conversation list/create | active live path |
| `/api/v1/conversations/{conversation_id}`, `/api/conversations/{conversation_id}` | GET/DELETE | `modules/conversation/api.py` | detail/delete | active live path |
| `/api/v1/conversations/{conversation_id}/messages` | POST | `modules/conversation/api.py` | message persistence | active live path |
| `/api/v1/conversations/{conversation_id}/files` | GET | `modules/conversation/api.py` | conversation file list | active live path |
| `/api/v1/conversations/{conversation_id}/files/{file_id}` | GET/DELETE | `modules/conversation/api.py` | file detail/delete | active live path |
| `/api/v1/conversations/{conversation_id}/files/{file_id}/download` | GET | `modules/conversation/api.py` | file download authority | active live path |
| `/internal/conversations/{conversation_id}/messages/user` | POST | `modules/conversation/internal_api.py` | gateway/backend internal user turn | active live path |
| `/internal/conversations/{conversation_id}/context-snapshot` | POST | `modules/conversation/internal_api.py` | execution context | active live path |
| `/internal/conversations/{conversation_id}/tasks/{task_id}/create-turn` | POST | `modules/conversation/internal_api.py` | gateway task turn create | active live path |
| `/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-progress` | POST | `modules/conversation/internal_api.py` | task progress persistence | active live path |
| `/internal/conversations/{conversation_id}/tasks/{task_id}/assistant-terminal` | POST | `modules/conversation/internal_api.py` | task terminal persistence | active live path |
| `/internal/quota/grants/precheck` | POST | `modules/quota/api.py` | internal quota grant precheck | active live path |
| `/internal/quota/grants/{grant_id}/finalize` | POST | `modules/quota/api.py` | internal quota finalize | active live path |
| `/api/v1/quota/my`, `/api/quota/my` | GET | `modules/quota/api.py` | user quota | active live path |
| `/api/v1/quota/configs`, `/api/quota/configs` | GET/POST | `modules/quota/api.py` | quota config | active live path |
| `/api/v1/view_pdf/{doi:path}`, `/api/view_pdf/{doi:path}` | GET | `modules/documents/api.py` | PDF view | active live path |
| `/api/v1/summarize_pdf/{doi:path}`, `/api/summarize_pdf/{doi:path}` | POST | `modules/documents/api.py` | PDF summary / LLM-like processing | active live path / boundary overlap |
| `/api/v1/extract_pdf_text/{doi:path}`, `/api/extract_pdf_text/{doi:path}` | GET | `modules/documents/api.py` | PDF text extraction | active live path / boundary overlap |
| `/api/v1/literature_content`, `/api/literature_content` | GET | `modules/documents/api.py` | retrieval-enriched literature content | active live path / boundary overlap |
| `/api/v1/reference_preview`, `/api/reference_preview` | GET | `modules/documents/api.py` | graph/vector reference preview | active live path / boundary overlap |
| `/api/upload_pdf`, `/api/v1/upload_pdf`, `/upload_pdf` | POST | `modules/uploads/api.py` | upload and mirror PDF | active live path |
| `/api/upload_excel`, `/api/v1/upload_excel`, `/upload_excel` | POST | `modules/uploads/api.py` | upload and mirror Excel | active live path |
| `/api/clear_pdf`, `/api/v1/clear_pdf`, `/clear_pdf` | POST | `modules/uploads/api.py` | clear runtime current PDF | active live path / compatibility |

### 2.3 核心调用链

```text
gateway/public frontend
  -> public-service auth/quota/conversation/documents/uploads/system routes
  -> runtime holds DB/Redis/storage services
  -> conversation service owns message/file metadata and internal task persistence
  -> quota service owns grant precheck/finalize
  -> storage service owns MinIO/local storage refs and download materialization

boundary overlap:
  public-service startup -> _bootstrap_retrieval()
  -> retrieval_service.build_bindings()
  -> VectorDbClient + Chroma + optional Neo4j
  -> documents/system APIs expose KB/model/retrieval operations
```

## 3. 发现的重构点

### R-001：retrieval runtime 混入公共权威服务

- 严重程度：P0
- 类型：边界越界 / QA retrieval runtime
- 代码位置：
  - `public-service/backend/app/modules/retrieval/service.py`
  - `RetrievalService.build_bindings()`
  - `public-service/backend/app/core/runtime.py`
  - `_bootstrap_retrieval()`
- 接口路径：
  - `/api/v1/kb_info`
  - `/api/v1/refresh_kb`
  - `/api/v1/literature_content`
  - `/api/v1/reference_preview`
- 关键代码片段：

```python
from app.integrations.neo4j import bootstrap_neo4j
from app.integrations.vector_db import VectorDbClient, bootstrap_chroma_collection

if include_neo4j and runtime.neo4j_url:
    neo4j_client = bootstrap_neo4j(...)

return RetrievalBindings(
    runtime=runtime,
    vector_db_client=VectorDbClient(...),
    chroma=bootstrap_chroma_collection(...),
    neo4j_client=neo4j_client,
)
```

- 当前问题：`public-service` 应作为 auth/quota/conversation/message/file metadata/storage authority/system status，但这里直接初始化 Vector DB、Chroma collection、可选 Neo4j，越过公共权威服务边界。
- 建议重构方式：迁出 retrieval runtime 到 fastQA/highThinkingQA/patent 或独立 `retrieval-service`。public-service 仅保留 document metadata 和 retrieval status DTO/proxy。
- 是否可抽共享包：只抽 retrieval DTO/status contract，不抽 Chroma/Neo4j client。
- 建议目标模块：`packages/agent_common/retrieval/contracts.py`；运行时放 QA/retrieval-service。
- 设计模式建议：Ports and Adapters。
- 影响范围：KB 运维、documents literature/reference preview、health。
- 风险：高。迁出会改变 `/kb_info`、`literature_content`、`reference_preview` 行为和依赖位置。
- 测试计划：新增 public-service startup test 断言 public-only 模式不 import/vector/neo4j；QA/retrieval service 增加 retrieval contract integration。
- 是否可立即删除：否。
- 删除或迁移前置条件：迁出或代理 `/api/*kb*` 和 documents retrieval enrichment。

### R-002：`PublicServiceRuntime` 启动即装配 QA/检索/PDF worker

- 严重程度：P0
- 类型：runtime 装配越界 / 生命周期混乱
- 代码位置：
  - `public-service/backend/app/core/runtime.py`
  - `_build_init_agent()`
  - `_build_pdf_text_extractor()`
  - `create_runtime()`
- 接口路径：
  - 全局启动路径
  - `/health`
  - `/api/v1/background_status`
  - documents/uploads routes
- 关键代码片段：

```python
def _build_init_agent(runtime: PublicServiceRuntime) -> Callable[[], bool]:
    def _init_agent() -> bool:
        include_neo4j = bool(str(os.getenv("NEO4J_URL", "") or "").strip())
        bindings = retrieval_service.build_bindings(...)
        runtime.vector_db_client = bindings.vector_db_client
        runtime.vector_collection = bindings.chroma.collection
        runtime.neo4j_client = bindings.neo4j_client
        runtime.agent = _build_agent_adapter(...)
        runtime.generation_runtime = None
```

```python
_bootstrap_retrieval(runtime)
_bootstrap_upload_processing(runtime)
```

- 当前问题：公共服务启动时装配 retrieval、PDF text extractor、upload-processing worker，不是公共元数据职责，也可能让公共服务可用性被向量库/图数据库/PDF 依赖拖垮。
- 建议重构方式：拆 `PlatformRuntime` 与 `QaCompatibilityRuntime`；后者迁出或 feature flag 默认关闭。
- 是否可抽共享包：runtime health schema 可抽。
- 建议目标模块：`backend/app/core/platform_runtime.py`、独立 QA runtime。
- 设计模式建议：Composition Root 分层。
- 影响范围：启动耗时、health、documents/uploads processing。
- 风险：高。禁用后 KB 和 PDF-processing APIs 行为变化。
- 测试计划：`APP_PUBLIC_ONLY=1` 启动 smoke；health component status contract；upload-processing degraded tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：明确 system KB 运维接口归属。

### R-003：documents 模块混入 PDF 抽取、LLM 摘要、Neo4j/Chroma 查询

- 严重程度：P1
- 类型：边界越界 / document-processing 混入 metadata authority
- 代码位置：
  - `public-service/backend/app/modules/documents/api.py`
  - `public-service/backend/app/modules/documents/service.py`
  - `summarize_pdf()`
  - `extract_pdf_text()`
  - `literature_content()`
  - `reference_preview()`
- 接口路径：
  - `/api/v1/summarize_pdf/{doi:path}`
  - `/api/v1/extract_pdf_text/{doi:path}`
  - `/api/v1/literature_content`
  - `/api/v1/reference_preview`
- 关键代码片段：

```python
import fitz  # type: ignore
doc = fitz.open(str(pdf_path))
text = str(page.get_text("text") or "")
```

```python
if graph is not None:
    query = """
    MATCH (n)
    WHERE n.doi = $doi OR n.material_name = $doi OR n.material_name CONTAINS $doi
    ...
    """
    result = graph.run(query, doi=doi).data()

if collection is not None:
    search_result = collection.get(where={"doi": doi})
```

- 当前问题：documents 不只是 metadata/asset authority，还执行 PDF 解析、摘要、图谱/向量检索增强。
- 建议重构方式：拆成 `documents-metadata`、`document-assets`、`document-processing`、`retrieval-enrichment`；public-service 保留前两者。
- 是否可抽共享包：可抽 `document-id-normalization`、`reference-preview DTO`。
- 建议目标模块：PDF/LLM/Chroma/Neo4j 到 QA 或 document-processing service。
- 设计模式建议：CQRS，asset query 与 processing command 分离。
- 影响范围：文献预览、PDF 摘要、翻译文档、引用预览。
- 风险：中高。前端可能直接依赖 `/api/extract_pdf_text`。
- 测试计划：documents contract tests 固定旧接口；新 adapter 兼容转发。
- 是否可立即删除：否。
- 删除或迁移前置条件：产品确认 documents public API 是否允许执行型能力。

### R-004：`storage/service.py` 职责过杂

- 严重程度：P1
- 类型：巨型模块 / storage boundary
- 代码位置：
  - `public-service/backend/app/modules/storage/service.py`
  - `StorageService`
- 接口路径：
  - conversation file download/delete
  - upload mirror
  - documents PDF/original asset routes
- 关键代码片段：

```python
class StorageService:
    def normalize_patent_id(value: str) -> str: ...
    def normalize_doi(value: str) -> str: ...
    def build_paper_object_name(cls, doi: str) -> str: ...
    def build_patent_original_manifest_object_name(cls, canonical_patent_id: str) -> str: ...
```

```python
def ensure_local_paper_pdf(...):
    lock = self._get_paper_download_lock(local_path)
    with lock:
        backend = get_storage_backend(project_root=project_root)
        if isinstance(backend, MinIOStorageBackend):
            backend.download_file(...)
```

- 当前问题：同一 service 混合 DOI normalize、patent id normalize、object naming、MinIO/local backend、paper PDF cache/download lock、download proxy、资源清理。
- 建议重构方式：拆 `IdentifierNormalizer`、`ObjectNamePolicy`、`StorageBackendFacade`、`PaperAssetCache`、`FileDownloadResolver`、`ResourceCleanupService`。
- 是否可抽共享包：normalizer/object-name policy 可抽。
- 建议目标模块：`storage/identifiers.py`、`storage/object_names.py`、`storage/paper_assets.py`、`storage/downloads.py`。
- 设计模式建议：Strategy、Facade。
- 影响范围：documents、uploads、conversation。
- 风险：高。对象名兼容变化会导致已有 MinIO/local 文件不可读。
- 测试计划：golden tests 覆盖 DOI/patent/object_name/storage_ref/download fallback。
- 是否可立即删除：否。
- 删除或迁移前置条件：固定 storage_ref 和 object naming 契约。

### R-005：conversation 模块承担过多权威职责和 QA/patent 展示逻辑

- 严重程度：P1
- 类型：巨型模块 / 边界混杂 / 持久化一致性风险
- 代码位置：
  - `public-service/backend/app/modules/conversation/service.py`
  - `conversation/api.py`
  - `conversation/internal_api.py`
- 接口路径：
  - `/api/v1/conversations*`
  - conversation file list/detail/download/delete
  - `/internal/conversations/*`
- 关键代码片段：

```python
_PATENT_ID_INLINE_CITATION_RE = re.compile(...)
_PATENT_INLINE_REFERENCE_RE = re.compile(...)

def _normalize_user_visible_patent_citations(...):
    rendered = _PATENT_ID_INLINE_CITATION_RE.sub(...)
```

```python
if self._should_use_legacy_conversation_fallback():
    legacy_messages = self._repo.list_messages(...)
    legacy_files = self._repo.list_uploaded_files(...)
```

- 当前问题：conversation 同时做 CRUD、message persistence、JSON 主文档、缓存、uploaded file metadata authority、file download authority、active task 回显、assistant inbox/outbox、专利引用文本规范化、legacy 表回填。
- 建议重构方式：拆 `ConversationAuthority`、`MessageStore`、`ConversationFileAuthority`、`TaskEcho/AssistantInbox`、`PatentCitationRenderer`。
- 是否可抽共享包：message/file/task schema 和 patent citation renderer 可抽，但 renderer 不应留在 conversation core。
- 建议目标模块：conversation 内部分层；patent renderer 移到 patentQA 或 shared presentation。
- 设计模式建议：Aggregate、Domain Service、Adapter。
- 影响范围：会话详情、列表缓存、内部 authority 写入、文件下载。
- 风险：高。JSON/DB/cache/storage 双写一致性容易回归。
- 测试计划：conversation authority API contract、file delete/download compensation、patent citation renderer golden tests。
- 是否可立即删除：否。
- 删除或迁移前置条件：确定 conversation files/JSON/DB 哪个是唯一事实源。

### R-006：system 模块混入 QA 运维控制面

- 严重程度：P1
- 类型：边界越界 / QA ops
- 代码位置：
  - `public-service/backend/app/modules/system/api.py`
  - `public-service/backend/app/modules/system/service.py`
- 接口路径：
  - `/api/v1/kb_info`
  - `/api/v1/refresh_kb`
  - `/api/v1/clear_cache`
  - `/api/admin/model-status`
  - `/api/admin/model-status/test`
- 关键代码片段：

```python
@router.get("/api/kb_info")
@router.get("/api/v1/kb_info")
async def kb_info(...):
    payload, status_code = system_service.build_kb_info(runtime)
```

```python
"id": "fastqa_embedding",
"kind": "embedding",
"base_url": _first_env("QA_EMBEDDING_BASE_URL", "EMBEDDING_API_URL"),
...
"id": "highthinkingqa_embedding",
"kind": "embedding",
```

- 当前问题：system status 不只是公共服务 health，还包含 KB、QA cache、embedding/rerank/model endpoint 探测。
- 建议重构方式：保留 `/health`、`/background_status`、`/cache_debug/conversation`；迁出 KB/model-status 到 QA ops service 或 gateway admin ops。
- 是否可抽共享包：health/component status schema 可抽。
- 建议目标模块：`qa-ops` 或 gateway admin ops。
- 设计模式建议：Operational Facade 按 bounded context 分离。
- 影响范围：admin 面板、运维脚本。
- 风险：中。运维页面可能缺字段。
- 测试计划：system route surface tests 更新；QA ops tests 覆盖 embedding/rerank probe。
- 是否可立即删除：否。
- 删除或迁移前置条件：明确 admin model-status 使用方。

### R-007：quota 基本符合 precheck/grant/finalize，但内部/本地 dependency 两套模型并存

- 严重程度：P2
- 类型：契约重复 / quota 计数风险
- 代码位置：
  - `public-service/backend/app/modules/quota/api.py`
  - `quota/deps.py`
  - `quota/service.py`
- 接口路径：
  - `/internal/quota/grants/precheck`
  - `/internal/quota/grants/{grant_id}/finalize`
  - public quota configs
- 关键代码片段：

```python
@router.post("/internal/quota/grants/precheck")
def precheck_internal_quota_grant(...):
    return _respond(quota_service.create_internal_quota_grant(...), ok_status=200)

@router.post("/internal/quota/grants/{grant_id}/finalize")
def finalize_internal_quota_grant(...):
    return _respond(quota_service.finalize_internal_quota_grant(...), ok_status=200)
```

```python
def finalize_quota(grant: QuotaGrant | None, *, result: Any, status_code: int | None = None):
    if grant is None:
        return None
    if not should_count_result(result=result, status_code=status_code):
        return None
    incremented = quota_service.increment_quota(...)
```

- 当前问题：internal gateway grant 模型和本地 dependency grant/finalize 模型并存，语义可能分叉。
- 建议重构方式：统一为一种 grant lifecycle；HTTP internal 协议和本地 dependency 共用同一 grant store。
- 是否可抽共享包：quota grant contract 可抽。
- 建议目标模块：`packages/agent_common/contracts/quota.py`，public-service 保留实现。
- 设计模式建议：Reservation/Lease。
- 影响范围：documents、conversation download、gateway ask。
- 风险：中高。重复计数或漏计。
- 测试计划：并发 precheck/finalize、失败不计数、idempotent finalize、gateway internal token。
- 是否可立即删除：否。
- 删除或迁移前置条件：gateway 侧完全切到 internal grant 协议。

### R-008：README/迁移文档与实际代码不一致

- 严重程度：P2
- 类型：文档漂移 / archive vs live 混淆
- 代码位置：
  - `public-service/backend/README.md`
  - `public-service/public-modules/README.md`
  - `public-service/backend-dependency-map.md`
- 接口路径：
  - 全局文档面
- 关键代码片段：

```markdown
当前不包含：
- gateway 调度接入
- `admin_users`、`documents` 的真实业务实现
- 任何 QA 执行逻辑
```

- 当前问题：实际代码已包含 documents 真实实现、retrieval/vector/Chroma/Neo4j/PDF/LLM 摘要等执行逻辑。`public-modules/README.md` 还引用当前文件树不存在或已漂移的 uploads/storage 文档。
- 建议重构方式：更新 current code inventory；把 historical baseline、计划文档、live contract 分开。
- 是否可抽共享包：否。
- 建议目标模块：`public-service/docs/current-runtime-inventory.md`。
- 设计模式建议：ADR + live inventory。
- 影响范围：后续迁移任务拆分。
- 风险：中。按旧文档执行会误删 live path 或遗漏越界代码。
- 测试计划：docs link/file existence check。
- 是否可立即删除：否。
- 删除或迁移前置条件：确认哪些文档作为历史基线保留。

## 4. 可抽共享能力清单

| 能力 | 当前重复位置 | 建议共享模块 | 迁移优先级 |
| -- | ------ | ------ | ----- |
| AuthContext / auth response contract | `modules/auth/deps.py`、gateway auth client | `packages/agent_common/contracts/auth.py` | P2 |
| Quota grant precheck/finalize schema | `modules/quota/schemas.py`、gateway `quota_proxy.py` | `packages/agent_common/contracts/quota.py` | P1 |
| Conversation authority DTO | `conversation/authority_schemas.py`、QA service authority clients | `packages/agent_common/contracts/conversation_authority.py` | P1 |
| Storage ref parser | `storage/service.py` | `packages/agent_common/storage/storage_ref.py` | P2 |
| DOI/patent id normalization | `storage/service.py`、documents reference preview、patent | `packages/agent_common/files/identifiers.py` | P2 |
| Patent citation renderer | `conversation/service.py`、patent/frontend markdown | `packages/agent_common/presentation/patent_citations.py` | P3 |
| Health/component status schema | `core/runtime.py`、`system/service.py` | `packages/agent_common/runtime/component_status.py` | P2 |
| Retrieval bindings DTO | `retrieval/service.py` | `packages/agent_common/retrieval/contracts.py` | P1 |
| PDF text extraction | `documents/service.py`、upload processing | independent `document-processing` service | P2 |

## 5. 可清理遗留代码清单

| 代码位置 | 当前状态 | 是否注册 | 是否被引用 | 建议处理 |
| ---- | ---- | ---- | ----- | ---- |
| `/api/...` 与 `/api/v1/...` 双路径 | active live path / compatibility | 是 | 是 | 保留并标 canonical/compat |
| `/upload_pdf`, `/upload_excel`, `/clear_pdf` 裸路径 | active live path / compatibility | 是 | 需要前端确认 | 标记 deprecated but still referenced |
| `conversation_legacy_fallback_enabled` | deprecated but still referenced | 不适用 | 是 | 先迁移 JSON/DB 事实源再删 |
| legacy conversation messages/files 回填 | deprecated but still referenced | 不适用 | 是 | 迁移完历史数据后删除 |
| `modules/_stubs.py::not_implemented_response` | scaffold / placeholder | 未见注册 | unknown | 若无 imports，可归档 |
| `modules/retrieval` | active live path | 无 router，但 runtime 引用 | 是 | 迁出，不直接删 |
| `qa_cache.metrics` in system | active live path | system router 间接暴露 | 是 | 迁到 QA ops |
| storage legacy paper helper 文档 | archive / historical baseline / unknown | 不适用 | 文档引用 | 文档清理时核对 |
| `generation_runtime` runtime 字段 | active runtime field, execution not observed | 不适用 | runtime 状态引用 | 标记 unknown，进一步验证 |
| Chroma/vector DB runtime | active live path | runtime 引用 | 是 | 迁出 retrieval |
| Neo4j runtime | active live path when `NEO4J_URL` set | runtime 引用 | 是 | 迁出 retrieval |
| PDF extraction via fitz | active live path | documents/upload processing 引用 | 是 | 迁出 document-processing |

## 6. 接口与契约风险

- gateway -> backend contract：public-service 是 auth/quota/conversation authority；gateway 内部依赖 quota grant 和 conversation task APIs，需要 contract tests。
- frontend -> gateway contract：public-service 仍暴露 `/api` 与 `/api/v1` 双路径、裸 upload paths；下线需先前端路径清单。
- backend -> public-service contract：QA services 通过 internal conversation context/assistant terminal APIs 写入，需要共享 SDK。
- internal token/auth headers：internal quota 只允许 `X-Internal-Service-Name: gateway`，应统一 client。
- SSE event schema：public-service 本身不做 QA SSE，但 conversation task terminal/progress payload 存储 stream summary。
- task event schema：internal conversation task APIs 和 gateway active_task summary shape 需统一。

## 7. 测试计划

- 单元测试：storage identifier/object-name、conversation service split、quota grant lifecycle、documents metadata vs processing。
- contract test：auth/quota/conversation authority/storage_ref/document asset APIs。
- stream/SSE test：public-service 不直接 stream QA；文件下载/translate_document streaming proxy 需保留 first-byte/headers。
- integration smoke test：gateway -> public-service auth/quota/conversation/uploads。
- backward compatibility test：`/api`、`/api/v1`、裸 upload paths。
- failure/cancel/retry test：quota finalize idempotency、upload persist failure cleanup、conversation task terminal retry。
- persistence test：conversation JSON/DB/cache/storage consistency、legacy fallback migration。
- quota/auth test：internal token、user token、quota precheck/finalize/local dependency。
- file route test：conversation file list/detail/download/delete、MinIO/local fallback。

## 8. 建议重构顺序

1. P0：先定义 public-service 边界 inventory，标出 auth/quota/conversation/storage/system 保留，retrieval/doc-processing/QA ops 迁出。
2. P0：为 retrieval runtime 增加 public-only 禁用开关和启动 contract test。
3. P1：拆 storage identifier/object-name/download/cache。
4. P1：拆 conversation authority/message/file/task echo/patent citation renderer。
5. P1：把 system KB/model-status 迁到 QA ops 或 gateway admin ops。
6. P2：统一 quota internal grant 和 local dependency grant lifecycle。
7. P2：更新 README/文档，区分 live inventory 和 historical baseline。

## 9. 需要进一步确认的问题

1. gateway 当前是否已经只 proxy public-service 的 auth/quota/conversation，还是仍有本地依赖。
2. `/api/v1/literature_content` 和 `/api/v1/reference_preview` 是否仍被前端/QA 服务使用。
3. `public-modules/uploads/` 是否应存在但未提交，还是 README 过期。
4. storage 文档中的 `paper_storage.py` 是否来自历史基线。
5. `generation_runtime` 是否只是预留字段。
6. `documents` 的 PDF 摘要/翻译是否被产品定义为公共能力；若是，应改名为 document-processing authority。

## 10. 第二轮深度补充

### 10.1 本轮只读命令与覆盖状态

本轮按要求执行并基于结果分析：

- `find public-service -type f`：确认目录内混有 `data/runtime/data/conversations/*.json|*.lock`、`.runtime/*.log`、`__pycache__`、`.pytest_cache`，代码与运行态数据同处 `public-service/` 扫描范围；真实代码集中在 `backend/app/`、`backend/tests/`，文档集中在 `public-modules/`、`backend-dependency-map.md`、`backend/README.md`。
- `find public-service -type f \( -name "*.py" -o -name "*.js" -o -name "*.vue" \) -print0 | xargs -0 wc -l | sort -nr | head -50`：前 50 共计 `51240 total`，最大代码文件是 `conversation/service.py` 3566 行、`quota/service.py` 1590 行、`system/service.py` 1251 行，说明 conversation/quota/system 是重构高风险核心。
- `rg "APIRouter|@router|app.include_router|path:|fetch|axios|EventSource" public-service`：确认 `main.py:25-35` 注册 10 个 router；`documents/api.py`、`conversation/api.py`、`conversation/internal_api.py`、`quota/api.py`、`system/api.py`、`uploads/api.py`、`auth/api.py`、`admin_users/api.py`、`departments/api.py`、`personnel/api.py` 均为 live API 面。
- `rg "deprecated|legacy|fallback|scaffold|placeholder|NOT_READY|not ready|shim|compat|TODO|FIXME|shadow|archive|obsolete|retired|rollout" public-service`：确认 legacy/fallback/compat 不是纯文档词，代码中有 `conversation_legacy_fallback_enabled`、`PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK`、department legacy users、quota legacy aliases、LLM retired aliases、conversation authority placeholder。
- `rg "app\.state|request\.app\.state" public-service`：确认 `main.py:54` 写 `app.state.settings`；`runtime.py` 将 runtime/auth/quota/conversation/redis 服务挂到 `app.state`；`documents/api.py:27-33` 从 request app state 取 runtime/agent。
- `rg "LLM_|EMBEDDING_|RERANK|REDIS|MINIO|NEO4J|VECTOR_DB|AUTH|TOKEN|RESOURCE_ROOT|RUNTIME_ROOT|STATE_ROOT" public-service`：确认配置里包含 LLM、embedding、rerank、Redis、MinIO、Neo4j、Vector DB、internal token。
- `rg "OpenAI|openai|embedding|rerank|auth_headers|httpx|stream|SSE|api_key|Bearer" public-service`：确认 documents 直接 `OpenAI`，system 直接 probe chat/embedding/rerank endpoints，documents `translate_document` 支持 SSE。
- `rg "requested_mode|actual_mode|source_scope|turn_mode|execution_files|selected_file_ids|primary_file_id|gateway-owned|X-Gateway" public-service`：确认 internal conversation contract 持有 `requested_mode/actual_mode/selected_file_ids`，但 `source_scope/primary_file_id/X-Gateway` 未在 live code 命中。

补充覆盖状态：

- 已覆盖 `backend/app/main.py`、`backend/app/core/`、`backend/app/modules/`、`backend/tests/`、`backend-dependency-map.md`、`public-modules/`、`scripts/`、`backend/README.md`。
- 要求项 `public-service/README.md` 在当前工作区不存在；`find public-service -maxdepth 2 -type f -iname '*readme*'` 只发现 `public-service/backend/README.md`、`public-service/public-modules/README.md`、`.pytest_cache/README.md`。

### 10.2 第一轮结论复核

第一轮 R-001 到 R-008 仍成立，并有更强证据：

- R-001/R-002 confirmed：`runtime.py:281-358` 启动时执行 `_bootstrap_retrieval()`，`retrieval/service.py:48-80` 构造 `VectorDbClient`、Chroma collection、可选 Neo4j。
- R-003 confirmed：`documents/api.py:163-294` 暴露 PDF 摘要、PDF 文本抽取、翻译、文献内容、引用预览；`documents/service.py` 内直接使用 OpenAI/fitz/storage/retrieval runtime。
- R-004 confirmed：`storage/service.py:21-405` 同时做 DOI/patent id normalize、object naming、MinIO/local resolver、paper cache、download redirect/proxy/local、cleanup。
- R-005 confirmed：`conversation/service.py:34-164` 做专利引用清洗，`service.py:1212-1313` 做 JSON/DB/object storage 同步，`internal_api.py:223-584` 做 task runtime authority。
- R-006 confirmed：`system/service.py:189-255` 包含 LLM、intent、fastQA embedding、highThinkingQA embedding、rerank endpoint inventory；`system/api.py:53-70` 暴露 KB refresh。
- R-007 confirmed：`quota/api.py:131-157` 提供 internal grant API；`quota/deps.py:127-201` 同时保留本地 dependency precheck/finalize。
- R-008 amended：`backend/README.md:65-69` 说“当前不包含 admin_users/documents 真实业务实现、任何 QA 执行逻辑”，但 live code 已注册 admin_users/documents，且 runtime/system/documents 持有 QA/retrieval/LLM 能力。顶层 `public-service/README.md` 不存在。

### 10.3 公共权威边界判定

| 能力 | 判定 | 代码证据 | 说明 |
|---|---|---|---|
| auth | public-service authority | `main.py:13-16,25-35` 注册 auth；`auth/api.py:33-169` login/register/me/password/security questions；`runtime.py:225-227` wiring auth service | 保留。 |
| quota | public-service authority | `quota/api.py:60-157` user/admin/internal grant；`quota/service.py:959-1105` check/increment；`1364-1573` internal grant | 保留，但 gateway 应走 internal grant。 |
| conversation | public-service authority | `conversation/api.py:73-233` CRUD/message/file；`internal_api.py:223-584` gateway/QA authority hooks | 保留，但内部应分层。 |
| message | public-service authority | `conversation/api.py:131-148` public append；`internal_api.py:223-256,310-397` internal append/assistant accept；`service.py:1774+` authority writes | 保留。 |
| file metadata | public-service authority | `conversation/api.py:163-233` file list/detail/download/delete；`uploads/api.py:225-335` upload persists file metadata | 保留。 |
| document metadata | partial authority | `documents/api.py:102-160,229-233` PDF/original/check asset metadata；`documents/service.py` also processing | 保留 asset/metadata；迁出 processing/retrieval。 |
| storage authority | public-service authority | `storage/service.py:175-189` storage_ref parse；`322-402` cleanup/download resolver；runtime storage bootstrap `runtime.py:160-189` | 保留 storage ref/object authority。 |
| system status | partial authority | `system/api.py:21-35` health/background；`83-95` cache debug | 保留 public health/cache debug；迁出 KB/model probe。 |
| admin/users | public-service authority | `admin_users/api.py:65-258` user CRUD/import/status/type/password | 保留。 |
| departments | public-service authority | `departments/api.py:68-332` department tree/CRUD/status/import/users | 保留；legacy-users 属兼容面。 |
| personnel | public-service authority | `personnel/api.py:65-268` personnel CRUD/status/import/bindings | 保留。 |

越界证据：

- retrieval/vector db：`retrieval/service.py:7-9` import Neo4j/vector DB；`48-80` build bindings；`runtime.py:281-358` 启动并记录 retrieval component。
- Neo4j：`config.py:142-145,248-255` Settings 持有 Neo4j；`runtime.py:283-291` `NEO4J_URL` set 时加载；`retrieval/service.py:60-68` bootstrap。
- embedding/rerank：`system/service.py:223-255` 暴露 fastQA/highThinkingQA embedding 与 rerank config；`287-323` 直接 HTTP probe。
- PDF extraction：`runtime.py:361-383` `fitz.open()` 构造 upload-processing extractor；`documents/api.py:176-186` 暴露 extract PDF text。
- QA helper/generation runtime/legacy agent：`runtime.py:69-75` runtime 字段含 `agent/generation_runtime/vector_collection/current_answer_context`；`runtime.py:274-299` 构造 knowledge compatibility adapter；`backend-dependency-map.md:36-57,169-178` 明确历史 FastAPI 后端含 QA 执行层与 legacy agent/service 层。

### 10.4 Conversation 深挖

| 切片 | 证据 | 结论 |
|---|---|---|
| API | `conversation/api.py:73-233` public CRUD/message/file；`internal_api.py:223-584` internal user/assistant/task/context | API 层已分 public/internal，但 service 层仍巨型。 |
| service | `service.py:167-197` 初始化 repo/json_store/outbox/redis；`1315-1440` CRUD；`1616-1722` detail/context；`1774+` authority write | ConversationService 是事实上的 aggregate/service/orchestrator。 |
| repository | `repository.py:67-121` conversation CRUD；`123-214` chat_json index；`216-260` message count | DB 表保存 conversation index 与 chat JSON sync metadata。 |
| models/schemas | `authority_schemas.py:9-17` source_service/requested/actual mode；`24-32` selected_file_ids；`75-155` assistant event；`task_schemas.py:14-61` task runtime payload | internal contract 已是 gateway/QA 协议，不只是 CRUD。 |
| outbox | `outbox.py:14-19` table `conversation_json_outbox`；`65-114` enqueue；`136-186` claim；`231-282` done/retry/dead | JSON object storage mirror 有后台补偿。 |
| file handling | `api.py:163-233` list/detail/download/delete；`service.py:923-1089` normalize/response files；`storage/service.py:362-402` resolver | file metadata 与 download authority 在 public-service。 |
| active task payload | `internal_api.py:400-584` assistant-start/create-turn/progress/terminal/rollback；`task_schemas.py:22-61` status/seq/timings/failure | public-service 记录 gateway/QA task 回显状态。 |
| message persistence | `api.py:131-148` public append；`internal_api.py:223-256,310-397` internal append/assistant accept；`service.py:967-1029` response message cleanup | 消息在 JSON 主文档中持久化，并同步 DB message_count/cache。 |
| 专利引用清洗 | `service.py:34-40` regex；`307-330` normalize；`967-1004` assistant message response cleanup | 属 patent presentation 越界，建议迁出。 |
| JSON store | `json_store.py:41-49` base/object prefix；`91-128` Redis+thread+fcntl lock；`188-225` write local+object storage；`260-310` remote restore | JSON 是主要 conversation document，DB 是 index，Redis 是 cache/lock。 |
| DB | `repository.py:51-65` dynamic select chat_json fields；`123-214` sync metadata；`216-260` counters | DB 负责 authority index/counters/outbox，不应承载 QA runtime。 |
| Redis | `service.py:407-420` lazy Redis; `cache.py` list/detail cache；`json_store.py:67-79` distributed lock | Redis 是 cache/lock，不是最终事实源。 |

Conversation CRUD/API 清单：

- Create/list/detail/title/delete：`POST/GET/PUT/DELETE /api[v1]/conversations*` -> `ConversationService` -> DB conversations + JSON document + Redis cache。
- Message append：`POST /api[v1]/conversations/{id}/messages` -> `add_message()`；internal user/assistant/task hooks -> authority-specific methods；均写 JSON document。
- File list/detail/download/delete：`GET/DELETE /api[v1]/conversations/{id}/files*` -> list/get/resolve/remove uploaded files；download 可 redirect/proxy/local。
- Active task 回显：`/internal/conversations/{id}/tasks/{task_id}/assistant-*` 和 `create-turn/rollback-create` 维护 placeholder、terminal/progress、active task binding。
- JSON/DB/Redis 职责：JSON = 会话全文主文档；DB = conversation index、chat_json sync metadata、outbox；Redis = cache、lock、patent runtime owner check。

### 10.5 Quota 深挖

| 切片 | 证据 | 结论 |
|---|---|---|
| internal APIs | `quota/api.py:131-157` only `/internal/quota/grants/precheck` and `/internal/quota/grants/{grant_id}/finalize`；`test_route_surface.py:49-57` 断言不暴露 public grant path | gateway 应只调用 internal quota API。 |
| precheck | `quota/service.py:1364-1511` create grant；`1400-1413` strict config/exceeded；`1447-1501` decision lock + pending reservation | 预检查是 reservation，不只是 read。 |
| grant store | `service.py:425-449` Redis pending with file fallback；`493-505` finalized result with fallback | Redis 不可用时落 `data_root/quota_grants`。 |
| finalize | `service.py:1513-1573` finalization lock、idempotent prior、increment on success、persist result、delete pending | finalize 失败会使 pending reservation 残留到 TTL/cleanup，可能短期误拒。 |
| override | `service.py:819-826` get override cached；`922-934` multi-period override rewrite | override 影响 daily/weekly/monthly limit。 |
| multi-window | `service.py:107-129` buckets；`912-957` windows；`959-1051` check all windows allowed；`1057-1105` increment window(s) | 有多窗口扣减语义，gateway 不应重实现。 |
| Redis lock/renewal | `service.py:516-648` decision/finalize locks + renew；`deps.py:76-118` local dependency Redis/MySQL lock | 两套锁模型并存。 |
| local deps | `quota/deps.py:127-201` FastAPI dependency precheck/finalize；documents/conversation download 使用 | public-service 内部路由仍走本地 dependency，gateway 应避免绕过 internal grant。 |

判断：gateway 应只调用 `/internal/quota/grants/precheck` 与 `/internal/quota/grants/{grant_id}/finalize`，不应读取 `/api/quota/my` 后本地判定，也不应模拟 multi-window/override/pending reservation。风险点是 finalize 网络失败或 service 返回 `DB_UNAVAILABLE` 时，`pending` reservation 不会立刻删除；`cleanup_pending_internal_quota_grants()` 仅在 startup 清 pending（`runtime.py:238-249`），运行中依赖 TTL/文件清理。

### 10.6 Storage/Retrieval 深挖

| 切片 | 证据 | 结论 |
|---|---|---|
| DOI normalize | `storage/service.py:27-52` URL decode、去 doi 前缀、去 `.pdf`、`10.*` 下划线转斜杠 | 可保留为 shared identifier helper。 |
| patent id normalize | `storage/service.py:22-24,78-84` uppercase patent id + `patent/originals/{id}` naming | 可保留 object naming policy，但 patent processing 不应在 storage core。 |
| object naming policy | `storage/service.py:69-75` `papers/{doi}.pdf`；`78-84` patent original manifest | public-service 可以权威化 storage ref/object policy。 |
| MinIO/local resolver | `storage/service.py:175-189` parse `minio://`/`local://`；`362-402` redirect/proxy/local download | 保留为 storage authority。 |
| paper cache/mirror | `storage/service.py:210-320` paper_exists/ensure_local_paper_pdf/download/mirror | 介于 asset authority 与 retrieval legacy，需裁剪为 asset read-through cache。 |
| Chroma/Neo4j init | `retrieval/service.py:48-80` build Chroma/Neo4j；`runtime.py:302-358` startup | 越界，public-service 最多保留 retrieval readiness/read-only status。 |
| retrieval status/readiness | `system/api.py:53-70` kb_info/refresh_kb；`runtime.py:340-358` component status | 可保留 degraded/readiness DTO，但 refresh/init 应迁出。 |

### 10.7 Router/API 完整表

| 路径/方法 | 文件行号 | 入参模型 | service | 外部依赖 | 持久化/鉴权/quota/SSE | 测试覆盖 |
|---|---|---|---|---|---|---|
| `GET /` | `main.py:66-87` | none | inline | none | no auth/quota | unknown |
| `GET /health`, `/api/health`, `/api/v1/health` | `system/api.py:21-25` | none | `system_service.build_health` | app.state.runtime | no auth/quota | `test_health.py`, `test_system_module.py:172-189` |
| `GET /api/background_status`, `/api/v1/background_status` | `system/api.py:28-35` | none | `build_background_status` | runtime/outbox/worker | admin auth | `test_system_module.py:23,172-221` |
| `GET /api/admin/model-status`; `POST /api/admin/model-status/test` | `system/api.py:38-50` | `ModelStatusTestRequest` | `build_model_status`, `test_model_status_endpoint` | LLM/embedding/rerank HTTP env | admin auth; no quota | `test_model_status.py` |
| `GET /api/kb_info`, `/api/v1/kb_info`; `POST /api/refresh_kb`, `/api/v1/refresh_kb` | `system/api.py:53-70` | none | `build_kb_info`, `refresh_kb` | vector DB/Neo4j/agent runtime | admin auth | `test_system_module.py:100-156,205-213` |
| `POST /api/clear_cache`, `/api/v1/clear_cache`; `GET /api/cache_debug/conversation`, `/api/v1/cache_debug/conversation` | `system/api.py:73-95` | optional `conversation_id` | `clear_cache`, `build_conversation_cache_debug` | Redis/cache metrics | admin/auth | `test_system_module.py:84,505` |
| Auth `/api[v1]/auth/login|register|me|department|personnel-binding|username|password|forgot-password/*|security-questions` | `auth/api.py:33-169` | auth schemas | `auth_service` | MySQL | bearer/query token via deps for protected routes | `test_auth_module.py`, `test_route_surface.py` |
| Admin users `/api/admin/users*` | `admin_users/api.py:65-258` | admin user schemas, multipart import | `admin_users_service`, import service | MySQL, spreadsheet helpers | admin auth | `test_admin_users_module.py`, `test_route_surface.py` |
| Departments `/api/admin/departments*` | `departments/api.py:68-332` | department schemas, multipart import | `department_service`, import service | MySQL, spreadsheet helpers | admin auth | `test_departments_module.py`, `test_route_surface.py` |
| Personnel `/api/admin/personnel*` | `personnel/api.py:65-268` | personnel schemas, multipart import | `personnel_service`, import service | MySQL, spreadsheet helpers | admin auth | `test_personnel_module.py`, `test_route_surface.py` |
| Quota public `/api[v1]/quota/my|configs|reset|users/{id}` | `quota/api.py:60-128` | quota schemas | `quota_service` | MySQL/Redis cache | auth/admin auth | `test_quota_module.py`, `test_route_surface.py` |
| Quota internal `/internal/quota/grants/precheck|{grant_id}/finalize` | `quota/api.py:131-157` | `InternalQuotaGrant*` | `quota_service` | MySQL/Redis/file fallback | internal token, gateway-only | `test_quota_module.py`, `test_route_surface.py:49-57` |
| Conversation public `/api[v1]/conversations*` | `conversation/api.py:73-233` | conversation schemas | `conversation_service` | MySQL/Redis/JSON/object storage | auth; file download quota; no SSE | `test_conversation_module.py`, `test_route_surface.py` |
| Conversation internal `/internal/conversations/{id}/messages/*|context-snapshot|tasks/*` | `internal_api.py:223-584` | authority/task schemas | `conversation_service` | MySQL/Redis/JSON/object storage | internal token/source policy | `test_conversation_authority_api.py`, `test_conversation_task_runtime.py`, integration tests |
| Documents PDF/original/check `/api[v1]/view_pdf`, `/patent/original`, `/check_pdf` | `documents/api.py:102-160,229-233` | path/query | `documents_service` | storage/local/MinIO | auth + file_view quota except check | `test_documents_module.py`, `test_patent_original_view_module.py` |
| Documents processing `/summarize_pdf`, `/extract_pdf_text`, `/translate`, `/translate_document`, `/literature_content`, `/reference_preview` | `documents/api.py:163-294` | `Translate*`, `ReferencePreviewRequest` | `documents_service` | OpenAI/fitz/runtime agent/vector/Neo4j/storage | auth optional for some; doc_assist quota; `translate_document` SSE on Accept | `test_documents_module.py`, `test_patent_original_view_module.py` |
| Uploads `/api/upload_pdf`, `/api/v1/upload_pdf`, `/upload_pdf`, excel variants, clear_pdf | `uploads/api.py:336-361` | multipart | upload handlers + `conversation_service` | local storage/MinIO/upload worker | optional auth; file metadata persist; no user-visible upload quota | `test_uploads_module.py`, `test_route_surface.py` |

### 10.8 Legacy/deprecated/scaffold 引用验证

| 引用 | router 注册 | import | script | test | frontend/gateway 调用 | 结论 |
|---|---|---|---|---|---|---|
| `/api` 与 `/api/v1` 双路径 | yes，多数 API 双 decorator | yes | n/a | yes，route_surface/docs/upload tests | unknown | active compat。 |
| 裸 `/upload_pdf|upload_excel|clear_pdf` | yes `uploads/api.py:336-360` | yes via router | unknown | `test_uploads_module.py:50-55` | unknown | active compat。 |
| `conversation_legacy_fallback_enabled` | no route | `config.py:159,270`; `service.py:191,356-357,1233-1237,1653` | no | `test_conversation_module.py` fallback tests | unknown | disabled by default but live flag。 |
| department `legacy-users` | yes `departments/api.py:222-230` | service/repository | no | `test_departments_module.py`, `test_route_surface.py:37` | unknown | active admin compat。 |
| quota legacy aliases | no route | `quota/service.py:117-135,193-202` | no | `test_quota_module.py` | unknown | active compatibility mapping。 |
| OpenAI retired aliases | no route | `documents/service.py`, `translator.py`, `system/service.py` env fallbacks | no | `test_documents_module.py`, `test_model_status.py` | unknown | active env compat。 |
| retrieval runtime | no router | `runtime.py:33,281-358` | start script loads graph env | `test_system_module.py` | unknown | live startup dependency。 |
| `generation_runtime` | no route | runtime field `runtime.py:70,296` | no | unknown | unknown | field only observed; execution unknown。 |
| backend docs claim no QA execution | n/a | n/a | n/a | n/a | n/a | contradicted by code evidence。 |

### 10.9 测试覆盖补充和缺口

覆盖：

- route surface：`test_route_surface.py` 覆盖关键 public/internal paths，并断言 quota grant 不暴露到 `/api`。
- auth/admin/departments/personnel/quota/conversation/documents/uploads/system 均有模块测试文件。
- conversation authority：`test_conversation_authority_api.py`、`test_conversation_task_runtime.py`、`test_conversation_assistant_inbox.py` 覆盖 source policy、idempotency、terminal/progress、patent citation normalization。
- quota：`test_quota_module.py` 覆盖 internal token、grant fallback、legacy aliases、canonical buckets、多窗口行为。
- documents：`test_documents_module.py`、`test_patent_original_view_module.py` 覆盖 PDF/original/translate/reference/SSE。

缺口：

- 未运行测试，本轮只读审计不验证实际通过状态。
- 缺 public-only startup contract：断言不初始化 Chroma/Neo4j/OpenAI/fitz。
- 缺 gateway -> public-service internal quota/conversation 的跨服务 contract suite。
- 缺 finalize 网络失败/超时导致 pending grant 残留的端到端回归。
- 缺 JSON/DB/Redis/object storage 四方一致性 chaos 测试。
- 缺前端/gateway 调用图验证，`/literature_content`、`/reference_preview`、裸 upload path 使用方 unknown。
- 缺 scripts smoke test；`scripts/start_gunicorn.sh` 会加载 resource shared model/graph env（`start_gunicorn.sh:20-29`），可能隐式开启 retrieval/model status 依赖。

### 10.10 新增重构点

以下 `R-009` 至 `R-018` 均为第二轮深度补充，所属服务均为 `public-service`。每个条目的 `当前状态` 以对应接口路径和调用链为准：auth/quota/conversation/storage/admin public authority 路径为 `active live path`；retrieval/documents/system model ops 为 `active live path but boundary-overlapping`；文档漂移为 `archive/doc drift`。本节所有条目均按第二轮模板补充代码位置、行号范围、接口路径、当前调用链、关键代码片段、目标结构、迁移步骤、兼容/回滚、测试计划、风险和阻塞项。

### R-009：拆分 public-service public-only runtime 与 QA/retrieval runtime

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path but boundary-overlapping

- 严重程度：P0
- 类型：边界越界 / startup blast radius
- 代码位置和行号范围：`public-service/backend/app/core/runtime.py:54-90,281-358,424-458`
- 接口路径：`/health`、`/api/v1/kb_info`、`/api/v1/refresh_kb`、所有 startup path
- 当前调用链：`create_app()` -> lifespan -> `create_runtime()` -> `_bootstrap_retrieval()` -> `retrieval_service.build_bindings()` -> Chroma/Neo4j/agent adapter
- <=40 行关键片段：

```python
def _bootstrap_retrieval(runtime: PublicServiceRuntime) -> None:
    runtime.init_agent = _build_init_agent(runtime)
    success = bool(runtime.init_agent())
    chroma_available = runtime.vector_collection is not None
    _set_component_status(runtime, "retrieval", ...)
```

- 目标结构：`PublicAuthorityRuntime` 只装配 DB/Redis/storage/auth/quota/conversation/admin/system health；`QaRetrievalRuntime` 迁到 QA/retrieval service，public-service 只保留 readiness proxy DTO。
- 迁移步骤：加 public-only config；先让 `_bootstrap_retrieval` 受 flag 控制且默认 off；迁出 `retrieval/service.py` 到 QA；system KB routes 改代理或 410/compat；删除 runtime agent/vector 字段。
- 兼容/回滚：保留 `PUBLIC_SERVICE_ENABLE_RETRIEVAL_COMPAT=1` 可临时启用旧路径；gateway/admin panel 可回退到旧 KB endpoints。
- 测试计划：unit 测 runtime flag；contract 测 health component schema；router 测 KB routes compat；stream 测无影响；integration 测 startup 不访问 Chroma/Neo4j；regression 测现有 `test_system_module.py`。
- 风险：KB admin 能力短期缺失；health 字段变化。
- 阻塞项：确认 `/api/kb_info`、`/api/refresh_kb` 的当前使用方。

### R-010：将 system model-status/test 迁出 public authority

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path but boundary-overlapping

- 严重程度：P1
- 类型：QA ops / upstream credential boundary
- 代码位置和行号范围：`system/api.py:38-50`、`system/service.py:189-323`
- 接口路径：`GET /api/admin/model-status`、`POST /api/admin/model-status/test`
- 当前调用链：admin route -> `system_service` -> env LLM/embedding/rerank specs -> `_http_post_json()` direct upstream probe
- <=40 行关键片段：

```python
{
    "id": "fastqa_embedding",
    "kind": "embedding",
    "base_url": _first_env("QA_EMBEDDING_BASE_URL", "EMBEDDING_API_URL"),
}
```

- 目标结构：public-service health 只报自身依赖；QA model/embedding/rerank status 移到 QA ops 或 gateway admin ops。
- 迁移步骤：定义 `ModelEndpointStatus` contract；新服务实现 probe；public-service route 改 gateway proxy/compat；移除 upstream credential reading。
- 兼容/回滚：保留旧 route behind compat flag，响应加 `deprecated: true`。
- 测试计划：unit 测 endpoint normalization 迁移；contract 测 status schema；router 测 admin auth；stream n/a；integration 测 QA ops probe；regression 跑 `test_model_status.py` 迁移版。
- 风险：运维面板失去模型检测；secret 暴露边界变化。
- 阻塞项：确认 admin UI 调用是否经 gateway。

### R-011：统一 quota internal grant 与本地 dependency grant

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path

- 严重程度：P1
- 类型：计费一致性 / duplicate lifecycle
- 代码位置和行号范围：`quota/api.py:131-157`、`quota/deps.py:127-201`、`quota/service.py:1364-1573`
- 接口路径：`/internal/quota/grants/precheck`、`/internal/quota/grants/{grant_id}/finalize`、documents/conversation local quota routes
- 当前调用链：gateway -> internal grant；public-service routes -> `require_quota()` local precheck/finalize；两者分别锁定/扣减。
- <=40 行关键片段：

```python
def finalize_quota(grant, *, result, status_code=None):
    if not should_count_result(result=result, status_code=status_code):
        return None
    return quota_service.increment_quota(...)
```

- 目标结构：所有计费路径都用同一 `QuotaReservationService`，本地 dependency 也创建 grant_id 并 finalize。
- 迁移步骤：抽 `QuotaGrantStore`；把 local dependency 改为 internal grant service method；保留 API wrapper；清理 MySQLNamedLock/Redis lock 双实现。
- 兼容/回滚：保留旧 dependency behind env；finalize 失败软降级仅对非扣费成功响应附 warning。
- 测试计划：unit 测 reservation store；contract 测 precheck/finalize idempotency；router 测 internal token；stream 测 SSE 成功/失败计数；integration 测 gateway ask；regression 跑 quota/documents/download tests。
- 风险：重复扣费或漏扣费。
- 阻塞项：gateway 侧 finalize 重试策略未确认。

### R-012：为 quota finalize 失败增加运行中补偿

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path

- 严重程度：P1
- 类型：consistency / quota availability
- 代码位置和行号范围：`quota/service.py:425-505,781-799,1513-1573`、`runtime.py:238-249`
- 接口路径：`/internal/quota/grants/{grant_id}/finalize`
- 当前调用链：finalize success -> increment -> persist finalized -> delete pending；startup cleanup 只清 pending
- <=40 行关键片段：

```python
self._persist_internal_quota_grant_result(...)
self._delete_internal_quota_grant(grant_id=normalized_grant_id, finalized=False)
if finalize_lock_renewer.lost:
    return {"success": False, "code": "DB_UNAVAILABLE"}
```

- 目标结构：运行中 pending grant sweeper + idempotent finalize recovery，区分 expired pending、unknown outcome、counted finalized。
- 迁移步骤：增加后台 quota grant sweeper；pending payload 写 request trace/source；finalize 失败返回可重试 code；gateway client 做 retry。
- 兼容/回滚：保留 TTL 自然过期；sweeper 可关闭。
- 测试计划：unit 测 pending expiry；contract 测 finalize retry；router 测 lock timeout；stream 测中断不计数；integration 测 Redis down/file fallback；regression 测 startup cleanup。
- 风险：误清理导致可用额度短期偏大或偏小。
- 阻塞项：需确认业务对“成功响应但计费失败”的处理策略。

### R-013：拆出 ConversationFileAuthority 与 DownloadResolver

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path

- 严重程度：P1
- 类型：conversation/file/storage 职责混合
- 代码位置和行号范围：`conversation/api.py:163-233`、`conversation/service.py:923-1089,359-405`、`storage/service.py:362-402`
- 接口路径：`/api/v1/conversations/{conversation_id}/files*`
- 当前调用链：route -> `conversation_service` -> JSON files + DB legacy fallback -> `storage_service.resolve_download()` -> Redirect/FileResponse
- <=40 行关键片段：

```python
payload, status_code, download = conversation_service.resolve_uploaded_file_download(...)
if mode == "redirect":
    return RedirectResponse(url=target, status_code=302)
return FileResponse(path=target, filename=file_name)
```

- 目标结构：Conversation core 只管 file metadata；Storage/Asset service 负责 download materialization；router 只做 response adapter。
- 迁移步骤：新增 `ConversationFileAuthority`；迁移 list/detail/delete；新增 `FileDownloadResolver`；保留 route shape。
- 兼容/回滚：旧 service 方法代理到新类；headers/status 保持。
- 测试计划：unit 测 file state transitions；contract 测 list/detail/download/delete schema；router 测 quota warning；stream 测 FileResponse/redirect；integration 测 MinIO/local；regression 跑 uploads/conversation tests。
- 风险：delete cleanup、display_no、legacy fallback 回归。
- 阻塞项：确定文件元数据唯一事实源 JSON vs DB。

### R-014：迁出 patent citation renderer

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path but boundary-overlapping

- 严重程度：P2
- 类型：patent presentation 越界
- 代码位置和行号范围：`conversation/service.py:34-164,247-330,967-1004`
- 接口路径：conversation detail、internal task terminal/progress
- 当前调用链：assistant message read/terminal write -> `_should_normalize_patent_citations()` -> `_normalize_user_visible_patent_citations()`
- <=40 行关键片段：

```python
if role_text == "assistant" and self._should_normalize_patent_citations(...):
    content_text = self._normalize_user_visible_patent_citations(content_text, trim=True, ...)
```

- 目标结构：patentQA 或 shared presentation 包输出已规范化 answer；conversation authority 只存/render neutral content。
- 迁移步骤：抽 golden renderer；在 patentQA terminal event 前清洗；conversation 保留 compat read-normalizer 一段时间。
- 兼容/回滚：保留 read-time normalizer behind flag，遇到老消息仍可清洗。
- 测试计划：unit golden tests；contract 测 terminal payload 不含重复 citation；router 测 detail response；stream 测 task progress/terminal；integration 测 patentQA write；regression 跑 patent normalization tests。
- 风险：历史消息显示变化。
- 阻塞项：确认前端是否也做 patent citation 渲染。

### R-015：将 documents processing 从 document metadata/asset authority 中拆出

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path but boundary-overlapping

- 严重程度：P1
- 类型：document-processing/LLM/retrieval 越界
- 代码位置和行号范围：`documents/api.py:163-294`、`documents/service.py`、`documents/translator.py`
- 接口路径：`/api/v1/summarize_pdf/*`、`/extract_pdf_text/*`、`/translate*`、`/literature_content`、`/reference_preview`
- 当前调用链：documents route -> `documents_service` -> storage/PDF fitz/OpenAI/runtime agent/vector/Neo4j -> response/SSE
- <=40 行关键片段：

```python
if "text/event-stream" in accept_header:
    result = documents_service.stream_translate_document(...)
    return _patent_original_response(result=dict(result or {}), head_only=False)
```

- 目标结构：public-service documents 只保留 view/check/original asset；processing routes 转到 document-processing/QA。
- 迁移步骤：列出 frontend/gateway consumers；定义 processing service API；public route 转发；迁出 OpenAI/fitz/retrieval dependencies。
- 兼容/回滚：保留 `/api`/`/api/v1` compatibility proxy；失败回退旧 in-process handler。
- 测试计划：unit 测 DOI/patent asset lookup；contract 测 processing proxy schema；router 测 auth/quota；stream 测 translate_document SSE first bytes；integration 测 OpenAI service；regression 跑 documents tests。
- 风险：SSE headers、quota finalize、auth optional 行为变化。
- 阻塞项：确认 processing 是否产品上归 public capability。

### R-016：将 storage identifier/object naming 抽成稳定 contract

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path

- 严重程度：P2
- 类型：共享规则分散风险
- 代码位置和行号范围：`storage/service.py:21-84,175-189,210-320`
- 接口路径：documents PDF/original、conversation file download、uploads mirror
- 当前调用链：documents/uploads/conversation -> `storage_service` static helpers -> MinIO/local
- <=40 行关键片段：

```python
def normalize_doi(value: str) -> str:
    text = unquote(text).strip()
    text = re.sub(r"^doi\s*[:=]\s*", "", text, flags=re.IGNORECASE)
    if text.lower().endswith(".pdf"):
        text = text[:-4]
```

- 目标结构：`storage_ref`、DOI normalize、patent id normalize、object naming policy 独立 contract；storage service 只实现 backend IO。
- 迁移步骤：抽纯函数模块；用 tests 固定行为；替换 documents/storage/conversation 调用；保留旧方法代理。
- 兼容/回滚：旧 `StorageService.normalize_*` 保留一版。
- 测试计划：unit 参数化 normalization；contract object name schema；router documents/view_pdf；stream original fulltext；integration MinIO/local；regression documents/upload tests。
- 风险：对象 key 改变导致老文件找不到。
- 阻塞项：确认 MinIO 已存对象命名全集。

### R-017：把 upload-processing worker 从 public-service startup 中裁剪

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：active live path but boundary-overlapping

- 严重程度：P1
- 类型：PDF parsing/background execution 越界
- 代码位置和行号范围：`runtime.py:361-421,455-456`、`conversation/upload_processing_worker.py`
- 接口路径：`/api/v1/upload_pdf`、`/api/v1/upload_excel`
- 当前调用链：startup -> `_build_pdf_text_extractor()` import fitz -> `UploadProcessingWorker` -> `recover_pending_upload_processing_tasks()` -> parse/index states
- <=40 行关键片段：

```python
extractor, extra = _build_pdf_text_extractor()
runtime.upload_processing_worker = UploadProcessingWorker(
    conversation_service=runtime.conversation_service,
    extract_pdf_text_fn=extractor,
    redis_service=runtime.redis_service,
)
```

- 目标结构：public-service upload 只登记 file metadata 与 storage ref；processing worker 迁到 document-processing/QA indexing worker。
- 迁移步骤：新增 file processing queue contract；upload 只 enqueue external event；迁移 recovery scanner；保留 processing status read model。
- 兼容/回滚：flag 回退 in-process worker。
- 测试计划：unit file state machine；contract queue payload；router upload response；stream n/a；integration worker consumes queued file；regression upload_processing tests。
- 风险：上传后状态从 ready 变慢，前端轮询语义变化。
- 阻塞项：确定 PDF/Excel index 的目标服务。

### R-018：修正 live inventory 文档与脚本边界

- 来源：第二轮深度补充
- 所属服务：public-service
- 当前状态：archive/doc drift

- 严重程度：P2
- 类型：文档漂移 / ops surprise
- 代码位置和行号范围：`backend/README.md:10-69`、`scripts/start_gunicorn.sh:20-29`、`backend-dependency-map.md:14-57`
- 接口路径：全局
- 当前调用链：scripts 加载 shared infrastructure/model/graph env -> app startup 可能装配 retrieval/model endpoints；README 却说不含 QA 执行逻辑
- <=40 行关键片段：

```bash
PUBLIC_SERVICE_SHARED_ENV_FILES="$RESOURCE_DIR/config/shared/infrastructure.shared.env:...:graph.secret.env"
load_env_files_preserving_process_env "$PUBLIC_SERVICE_ENV_FILES"
```

- 目标结构：`docs/live-public-service-inventory` 与历史迁移文档分离；scripts public-only 默认不加载 model/graph secret，除非 compat flag。
- 迁移步骤：更新 README 当前状态；脚本拆 public-only env 与 compat env；增加 docs link check。
- 兼容/回滚：保留现有 env load 顺序 behind `PUBLIC_SERVICE_ENABLE_COMPAT_ENV=1`。
- 测试计划：unit env loader；contract n/a；router health no QA deps；stream n/a；integration start script dry-run；regression config independence tests。
- 风险：部署环境依赖旧 env 自动加载。
- 阻塞项：运维确认 resource shared env 的职责归属。

## 第三轮证据闭环补充

> 本轮只做只读证据闭环，并仅追加本文档。未修改 `public-service/` 下源码、配置、测试、脚本、README 或依赖文件。

### 1. 第二轮未确认项复核

### V-301：三类待确认 API 仍处于 live 调用面

- 验证目标：确认 `/api/v1/literature_content`、`/api/v1/reference_preview`、`/api/admin/model-status` 是否仍被前端、gateway、后端和脚本调用。
- 只读命令：`rg -n "literature_content|reference_preview|model-status|refresh_kb|kb_info" public-service/backend/app public-service/backend/tests gateway/app gateway/tests frontend-vue/src fastQA highThinkingQA patent --glob '!**/dist/**' --glob '!**/.pytest_cache/**' --glob '!**/__pycache__/**'`；`rg -n "literature_content|reference_preview|model-status|refresh_kb|kb_info" scripts --glob '*.sh' --glob '*.py' --glob '*.js' --glob '*.vue' --glob '!**/docs/**' --glob '!**/tests/**'`。
- 证据：
  - 前端：`frontend-vue/src/api/literature.js:37-50` 直接调用 `/api/literature_content` 和 POST `/api/reference_preview`；`frontend-vue/src/features/references/composables/useReferenceInspector.js:38,107` 调用 `getLiteratureContent()` / `getReferencePreview()`；`frontend-vue/src/services/admin.js:147-160` 调用 `/model-status` 和 `/model-status/test`；`frontend-vue/src/views/AdminDashboard.vue:214,264` 触发 model-status 获取和测试。
  - gateway：`gateway/app/routers/public_proxy.py:251-252,263-264` 将 literature/reference/model-status 纳入 public proxy；`gateway/app/services/route_table.py:49-50,62-63` 将它们纳入 route table；`gateway/tests/test_public_proxy.py:535-539` 固定 `/api/v1/reference_preview` 转发；`gateway/tests/test_route_table.py:98-100` 固定 model-status 路由。
  - public-service 后端：`documents/api.py:236-295` 注册 literature/reference GET/POST；`system/api.py:38-50` 注册 model-status；`test_documents_module.py:805-823` 固定前端 `doi` payload；`test_model_status.py:25-34` 固定 model-status route + admin guard。
  - 脚本：顶层 lifecycle shell 脚本未见直接 curl 调用这些 API；但 `scripts/gateway/app/routers/public_proxy.py:246-252` 与 `scripts/gateway/app/services/route_table.py:44-50` 存在一份 gateway 代码副本，同样保留 kb/reference/literature proxy。
- 判定：三类 API 均不是 dead code。`reference_preview` 同时有前端、gateway、public-service 测试证据；`literature_content` 有前端与 gateway proxy 证据；`model-status` 有 admin 前端、gateway route table/proxy、public-service route 测试证据。shell lifecycle 脚本未直接调用，但脚本目录内的 gateway 副本仍保留这些路由。
- 风险等级：high。
- 后续动作：迁出/删除前必须先改 gateway route table/proxy 与前端调用点，并保留兼容代理或 410/feature flag。

### V-302：retrieval/service.py 的 Chroma/Neo4j 属于 active startup

- 验证目标：确认 `retrieval/service.py` 中 Chroma/Neo4j 是否仍由 public-service 启动链装配。
- 只读命令：`rg -n "VectorDbClient|bootstrap_chroma|neo4j|embedding|rerank|fitz|extract_pdf" public-service/backend/app public-service/backend/tests`；`nl -ba public-service/backend/app/core/runtime.py | sed -n '260,470p'`；`nl -ba public-service/backend/app/modules/retrieval/service.py | sed -n '1,95p'`。
- 证据：
  - `runtime.py:424-456` 的 `create_runtime()` 顺序调用 `_bootstrap_retrieval(runtime)`，没有 feature flag。
  - `_bootstrap_retrieval()` 在 `runtime.py:302-358` 调用 `runtime.init_agent()` 并设置 `retrieval` / `agent` component status。
  - `_build_init_agent()` 在 `runtime.py:281-299` 调用 `retrieval_service.build_bindings()`，写入 `runtime.vector_db_client`、`runtime.vector_collection`、`runtime.neo4j_client`。
  - `retrieval/service.py:7-8` import `bootstrap_neo4j`、`VectorDbClient`、`bootstrap_chroma_collection`；`service.py:60-79` 在 `include_neo4j` 且有 `NEO4J_URL` 时 bootstrap Neo4j，并无条件构造 `VectorDbClient` 与 Chroma collection。
  - `runtime.py:283` 用原始 `NEO4J_URL` 判断是否 include Neo4j，未使用 `PUBLIC_SERVICE_NEO4J_URL` 命名空间；`config.py:248-255` 另有 namespaced/fallback settings，二者语义存在漂移。
- 判定：Chroma/VectorDb 是 active startup；Neo4j 是有条件 active startup，只要进程环境存在 `NEO4J_URL` 就会启动。该模块不能直接删除，只能先切 public-only startup 或迁出到 QA/retrieval service。
- 风险等级：critical。
- 后续动作：先增加 public-only 启动护栏测试，再迁出 retrieval runtime。

### V-303：documents 的摘要/翻译/抽取是 live 公共 API 面，但能力性质属于执行侧

- 验证目标：确认 PDF 摘要、翻译、抽取到底是公共能力还是 QA 执行能力。
- 只读命令：`rg -n "summarize_pdf|extract_pdf_text|translate_document|translate\\(" public-service/backend/app/modules/documents public-service/backend/tests/test_documents_module.py frontend-vue/src gateway/app gateway/tests --glob '!**/dist/**'`。
- 证据：
  - API live 面：`documents/api.py:163-226` 注册 `summarize_pdf`、`extract_pdf_text`、`translate`、`translate_document`，并使用 `doc_assist` quota；`gateway/app/routers/public_proxy.py:244-255` 代理 translate、translate_document、summarize_pdf、extract_pdf_text。
  - 摘要执行：`documents/service.py:604-676` 使用 `OpenAI` chat completion 生成 PDF 摘要；`service.py:532-569` 直接 `fitz.open()` 抽取 PDF 文本。
  - 翻译执行：`documents/translator.py:39-47,74-90` 从 `LLM_*` 环境构造 OpenAI-compatible client；`translation_service.py:32-94` 批量调用 translator；`documents/service.py:737-829` 生成 `text/event-stream` 翻译流。
  - 前端 live 使用：`frontend-vue/src/services/api.js:790-881` 调用 `/translate`、`/translate_document`、`/summarize_pdf`；`frontend-vue/src/components/PdfReader.vue:662,702,808` 使用摘要、选中文本翻译和文档流式翻译。
  - 测试护栏：`test_documents_module.py:514-597` 覆盖 translate_document quota 和 SSE；`test_documents_module.py:671-685` 覆盖 extract_pdf_text 鉴权/配置失败。
- 判定：产品/API 层它们已作为 public-service live 公共 API 暴露；实现性质上它们依赖 LLM、fitz、SSE 和 doc_assist quota，属于 document-processing/QA 执行能力。重构前不能直接删，只能以兼容代理迁到 document-processing/QA。
- 风险等级：high。
- 后续动作：把 asset/metadata 留在 public-service，将摘要/抽取/翻译迁出为 processing service，并保留 SSE first-byte/headers contract。

### V-304：upload-processing worker 应迁出 public-service，但迁出前需保留状态读模型

- 验证目标：确认 `UploadProcessingWorker` 是否应迁出 public-service。
- 只读命令：`rg -n "UploadProcessingWorker|recover_pending|processing_stage|parse_status|index_status" public-service/backend/app public-service/backend/tests`。
- 证据：
  - 启动链：`runtime.py:386-421` 构造 `UploadProcessingWorker`，传入 PDF extractor 和 conversation_service；`runtime.py:455-456` 启动时调用 `_bootstrap_upload_processing()`。
  - 恢复链：`runtime.py:403-407` 启动时调用 `conversation_service.recover_pending_upload_processing_tasks(worker=worker)`；`conversation/service.py:721-790` 扫描 pending/uploaded/parsing/indexing 文件并重新 `worker.submit()`。
  - 执行链：`upload_processing_worker.py:281-356` 将文件状态从 `parsing` -> `parsed` -> `indexing` -> `ready`，PDF 走 `_parse_pdf()`，Excel 走 `_parse_table()`，最终写 `index_mode=deferred`。
  - 状态模型：`conversation/service.py:192-194` 定义 `parse_status/index_status/processing_stage` 集合；`uploads/api.py:325-333` upload 后持久化文件元数据。
  - 测试护栏：`test_conversation_module.py:2735-2762` 固定 PDF 解析与 `index_status=ready`；`2765-2778` 固定 legacy PDF 错误文本视为失败；`2781-2807` 固定 state persist 失败时不解析。
- 判定：worker 是后台执行/解析能力，应迁出 public-service；但 file metadata、状态字段和恢复语义已经是前端/会话可见 contract，迁出前需保留 `parse_status/index_status/processing_stage` read model 和 queue/worker contract。
- 风险等级：high。
- 后续动作：先定义 `UploadProcessingJob` 事件和状态机 contract，再将解析/index worker 移出。

### V-305：conversation JSON/DB/Redis 三者事实源边界可推断但未被代码显式封装

- 验证目标：确认 conversation service 中 JSON store / DB / Redis 三者唯一事实源不清的问题。
- 只读命令：`nl -ba public-service/backend/app/modules/conversation/json_store.py | sed -n '1,325p'`；`nl -ba public-service/backend/app/modules/conversation/repository.py | sed -n '40,230p'`；`nl -ba public-service/backend/app/modules/conversation/cache.py | sed -n '1,140p'`；`nl -ba public-service/backend/app/modules/conversation/service.py | sed -n '1210,1335p'`。
- 证据：
  - JSON store：`json_store.py:132-162` 定义 chatlog document 的 `messages/files/runtime` 主体；`164-180` 优先读 local JSON，缺失才 remote restore；`188-225` 写 local JSON 并 mirror object storage；`260-304` local/remote mismatch 时偏向 remote copy。
  - DB：`repository.py:51-65` 动态选择 `chat_json_*` 索引字段；`123-173` 写 chat_json local/storage/hash/version/sync_status；`216-230` 维护 `message_count`。
  - Redis：`json_store.py:67-79,91-128` 用 Redis lock + 本地/fcntl lock；`cache.py:56-132` 构建 list/detail cache key；`conversation/service.py:701-719` 刷新 list cache。
  - 同步/补偿：`conversation/service.py:1253-1313` `_persist_document_and_index()` 先写 JSON，再更新 DB index；若 `sync_status != ok` 则入 `conversation_json_outbox`；`outbox.py:14-20,65-114` 使用 `conversation_json_outbox` 表。
- 判定：实际语义可推断为 JSON document 是会话正文主事实源，DB 是索引/计数/同步元数据，Redis 是 cache/lock；但 `ConversationService` 仍同时持有三者，缺少独立 authority 类和 contract test 来强制事实源边界。
- 风险等级：high。
- 后续动作：重构前先写 contract：读取正文必须来自 JSON，DB 仅作为 index，Redis 仅缓存/锁；再拆 `ConversationDocumentStore`、`ConversationIndexRepository`、`ConversationCache`。

### V-306：patent citation renderer 仍在 conversation live 写/读路径，应迁出但需保留兼容读

- 验证目标：确认 patent citation renderer 是否应移出 conversation。
- 只读命令：`rg -n "patent.*citation|normalize.*patent|patent_id" public-service/backend/app/modules/conversation public-service/backend/tests`。
- 证据：
  - renderer 实现：`conversation/service.py:34-40` 定义 `patent_id` inline regex；`95-164` 处理 publication number 和尾部 citation 去重；`253-330` 判断和执行 user-visible patent citation normalization。
  - live 路径：`service.py:990-1004` 在 assistant response message 准备时清洗；`2383,2481,2757,2902-2906` 在 internal/task/terminal 等路径继续调用 normalizer。
  - 测试护栏：`test_conversation_module.py:1075-1123` 固定 detail 隐藏 raw `patent_id=`；`1127-1173` 固定尾部重复专利引用去重；`test_conversation_authority_api.py:564-622` 固定 gateway-owned task runtime 写入前清洗；`test_conversation_authority_integration.py:507-620` 固定集成路径可读 citation。
- 判定：该能力是 patent presentation/postprocess，不属于 conversation authority core；但它已在 user-visible storage/read path 上保障历史和 gateway-owned task 展示，迁出时必须保留一段 read-time compatibility normalizer。
- 风险等级：medium。
- 后续动作：先抽 golden renderer 到 patent/shared presentation；patentQA 写 terminal event 前清洗，conversation 保留兼容读 flag。

### V-307：quota internal grant 与本地 dependency grant 生命周期重复

- 验证目标：确认 internal grant 与本地 dependency grant 是否重复生命周期。
- 只读命令：`nl -ba public-service/backend/app/modules/quota/api.py | sed -n '120,165p'`；`nl -ba public-service/backend/app/modules/quota/deps.py | sed -n '1,215p'`；`nl -ba public-service/backend/app/modules/quota/service.py | sed -n '1360,1585p'`。
- 证据：
  - internal grant API：`quota/api.py:131-157` 暴露 `/internal/quota/grants/precheck` 和 `/internal/quota/grants/{grant_id}/finalize`，只允许 gateway internal caller。
  - internal reservation：`quota/service.py:1364-1511` 创建 pending grant，并把 completed usage + pending reservations 合并进 effective usage；`1513-1573` finalize 时 idempotent 检查、increment、persist finalized、delete pending。
  - 本地 dependency：`quota/deps.py:127-149` 本地 `precheck_quota()` 直接 `check_quota()` 并持有 Redis/MySQL lock；`179-197` `finalize_quota()` 成功响应后直接 `increment_quota()`。
  - 本地使用：`documents/api.py:169,195,208` 使用 `require_quota("doc_assist")`；`documents/api.py:183,243,264,285` optional auth 时手动 `precheck_quota()`；conversation file download 也使用本地 quota dependency。
- 判定：存在两套生命周期：gateway 使用 reservation grant；public-service 内部 route 使用本地 lock + check/increment。二者共用底层 `QuotaService` 但不是同一个 grant store/状态机。
- 风险等级：high。
- 后续动作：把本地 dependency 改成同一 `QuotaReservationService` 内部调用，使所有扣费路径都有 grant_id、pending、finalized、idempotency。

### V-308：quota finalize 有重试/idempotency和启动清理，但缺运行中补偿 sweeper

- 验证目标：确认 quota finalize 失败是否有补偿机制。
- 只读命令：`rg -n "cleanup_pending_internal_quota_grants|_persist_internal_quota_grant_result|_delete_internal_quota_grant|pending" public-service/backend/app/modules/quota/service.py public-service/backend/app/core/runtime.py public-service/backend/tests/test_quota_module.py`。
- 证据：
  - pending/finalized store：`quota/service.py:425-449` 存取 pending grant，Redis 不可用时落文件；`493-505` 存 finalized result；`483-490` 删除 pending/finalized。
  - finalize 语义：`quota/service.py:1528-1536` 先查 finalized 以支持幂等；`1545-1559` success 时 increment；`1561-1569` persist finalized 后 delete pending。
  - 临时失败重试：`test_quota_module.py:769-793` 固定第一次 `increment_quota` 返回 `DB_UNAVAILABLE` 时 pending 保留，第二次 finalize 可成功且只 increment 一次。
  - 启动清理：`runtime.py:235-249` 在 `_bootstrap_services()` 中调用 `cleanup_pending_internal_quota_grants()`；`quota/service.py:781-799` 清理全部 pending；`test_quota_module.py:995-1015` 固定 startup-style cleanup 可释放 reservation capacity。
- 判定：不是完全没有补偿；当前已有 finalize retry/idempotency 与启动时 pending cleanup。但未发现运行中周期性 sweeper 或 gateway finalize 网络失败后的自动恢复队列，pending 可能在 TTL/重启前占用额度。
- 风险等级：medium。
- 后续动作：增加运行中 sweeper + gateway retry/backoff contract，并明确成功响应但扣费失败的业务策略。

### V-309：public-service scripts 默认加载 model/graph env

- 验证目标：确认 public-service scripts 是否默认加载 model/graph env。
- 只读命令：`nl -ba public-service/scripts/start_gunicorn.sh | sed -n '1,120p'`；`nl -ba scripts/_service_common.sh | sed -n '1,130p'`；`rg -n "public-service|start_gunicorn|PUBLIC_SERVICE_ENV_FILES|model-endpoints|graph" scripts/start_all.sh scripts/_service_common.sh scripts/restart_all.sh scripts/status_all.sh scripts/stop_all.sh`。
- 证据：
  - `public-service/scripts/start_gunicorn.sh:20-29` 默认将 `resource/config/shared/model-endpoints.*` 与 `resource/config/shared/graph.*` 放入 `PUBLIC_SERVICE_ENV_FILES` 并加载。
  - 顶层 `scripts/_service_common.sh:17-19` 的 `shared_env_files()` 同样包含 `model-endpoints.shared.env`、`model-endpoints.secret.env`、`graph.shared.env`、`graph.secret.env`。
  - `scripts/_service_common.sh:120-122` 启动 public-service 时默认把 `shared_env_files` 注入 `PUBLIC_SERVICE_ENV_FILES` 后调用 `public-service/scripts/start_gunicorn.sh`。
  - 结合 V-302，`graph.secret.env` 若提供 `NEO4J_URL`，启动链会 active bootstrap Neo4j；结合 V-303/V-310，model env 被 model-status/translation/summary 使用。
- 判定：默认脚本会加载 model/graph env；这会把 QA/model/retrieval 依赖带入 public-service 进程启动和 admin status。
- 风险等级：high。
- 后续动作：拆 public-only env 与 compat env；默认不加载 model/graph，除非 `PUBLIC_SERVICE_ENABLE_QA_COMPAT_ENV=1`。

### V-310：public-modules 与 backend 实际目录存在漂移

- 验证目标：确认 `public-modules` 与 `backend/app/modules` 是否漂移。
- 只读命令：`find public-service/public-modules -maxdepth 3 -type f | sort`；`find public-service/public-modules -maxdepth 2 -type d | sort`；`find public-service/backend/app/modules -maxdepth 2 -type f -name "*.py" | sort`；`nl -ba public-service/backend/app/main.py | sed -n '1,45p'`。
- 证据：
  - backend live router：`main.py:13-22` import `admin_users/auth/conversation/departments/documents/personnel/quota/system/uploads` routers；`main.py:25-35` 注册这些 routers。
  - backend actual modules：`backend/app/modules/departments/*`、`personnel/*`、`retrieval/*`、`qa_cache/*`、`storage/*`、`documents/*`、`uploads/*` 均存在。
  - public-modules 文档目录：`find public-service/public-modules -maxdepth 2 -type d` 只列出 `admin_users/auth/conversation/documents/quota/storage/system`，没有 `departments/`、`personnel/`、`retrieval/`、`qa_cache/`、`uploads/` 子目录。
  - `public-modules/README.md:47-52` 列出 `uploads/README.md` 和 uploads 子文档，但 `find public-service/public-modules -maxdepth 2 -type d` 未发现 `public-modules/uploads` 目录；同时 `public-modules/README.md:80-90` 指向 `/home/cqy/worktrees/public-service/...` 旧路径，不是当前 `/home/cqy/worktrees/highThinking/public-service/...`。
- 判定：存在实际漂移。不能用 `public-modules` 作为最终事实源；重构前必须以 `backend/app/main.py` router 注册、`backend/app/modules` 代码、gateway proxy 和前端调用作为 live inventory。
- 风险等级：medium。
- 后续动作：更新 live inventory 文档时补齐 departments/personnel/retrieval/qa_cache/uploads 状态，并修正旧路径；但本轮按约束不修改 public-service 文档。

### 2. dead-code / legacy 引用闭环

| 对象 | live/legacy 判定 | 删除/迁出安全判定 | 证据 |
|---|---|---|---|
| `/api/v1/literature_content` | live compatibility/API | 不可删除；可迁为 gateway -> document-processing/QA proxy | `frontend-vue/src/api/literature.js:37-39`、`gateway/app/routers/public_proxy.py:251`、`documents/api.py:236-252`、`test_documents_module.py:714-797` |
| `/api/v1/reference_preview` GET/POST | live canonical/compat 混合 | 不可删除；迁出时必须保留 POST `doi` payload 兼容 | `frontend-vue/src/api/literature.js:42-50`、`gateway/app/routers/public_proxy.py:252`、`documents/api.py:255-295`、`test_documents_module.py:805-823` |
| `/api/admin/model-status` 与 `/test` | live admin ops | 不可删除；应迁到 QA ops/gateway admin ops 或保留兼容代理 | `frontend-vue/src/services/admin.js:147-160`、`frontend-vue/src/views/AdminDashboard.vue:214,264`、`gateway/app/services/route_table.py:62-63`、`system/api.py:38-50` |
| `retrieval/service.py` | active startup dependency | 不可删除；先 public-only flag，再迁出 | `runtime.py:302-358,424-456`、`retrieval/service.py:60-79` |
| `generation_runtime` 字段 | legacy/placeholder field | 可延后清理；需先确认无外部 health/status 依赖 | `runtime.py:296` 置 `None`，本轮未见执行调用 |
| `UploadProcessingWorker` | live background execution | 不可删除；可迁出 worker，但保留状态模型和恢复语义 | `runtime.py:386-421`、`conversation/service.py:721-790`、`upload_processing_worker.py:281-356` |
| patent citation normalizer | live presentation compatibility | 不可直接删除；先迁出 renderer，并保留 read-time compat | `conversation/service.py:34-164,253-330,990-1004,2902-2906`、相关 tests |
| quota local dependency grant | live internal implementation | 不可删除；应与 internal grant 合并生命周期 | `quota/deps.py:127-197`、`quota/api.py:131-157` |
| `public-modules/uploads/*` 文档索引 | doc drift | 不涉及代码删除；文档需修正 | `public-modules/README.md:47-52` 引用存在，`find public-service/public-modules -maxdepth 2 -type d` 未发现目录 |

### 3. live path 调用链闭环

#### public-service 边界闭环表

| 能力 | 当前 live path | 边界判定 | 保留/迁出策略 | 证据 |
|---|---|---|---|---|
| auth | frontend/gateway -> public-service auth routes -> DB | public authority | 保留 | `main.py:13-15,25-35`、auth router 注册 |
| quota public/admin | frontend/admin -> `/api/v1/quota/*` -> quota service | public authority | 保留；统一 grant 生命周期 | `quota/api.py:60-157`、`quota/service.py:1364-1573` |
| conversation/message/task | gateway/QA -> internal conversation APIs；frontend -> public conversation APIs | public authority | 保留；拆分 document/index/cache/file/task | `main.py:15-16,32-33`、`conversation/internal_api.py:223-584`、`conversation/service.py:1212-1313` |
| file metadata/upload binding | upload routes -> conversation file metadata -> JSON/DB/storage ref | public authority | 保留 metadata；迁出 parse/index worker | `uploads/api.py:325-355`、`conversation/service.py:192-194,3394-3458` |
| storage ref/object policy | documents/uploads/conversation -> storage_service | public authority | 保留 object naming/storage ref；抽纯 contract | `storage/service.py:21-84,175-189,362-402` |
| document asset access | `/view_pdf`、`/check_pdf`、`/patent/original` | public asset authority | 保留 | `documents/api.py:102-160,229-233` |
| document processing | `/summarize_pdf`、`/extract_pdf_text`、`/translate*` | execution/document-processing | 迁出或代理 | `documents/api.py:163-226`、`documents/service.py:604-829` |
| literature/reference enrichment | `/literature_content`、`/reference_preview` -> agent graph/Chroma | retrieval/QA enrichment | 迁出或代理；保留 compatibility route | `documents/api.py:236-295`、`documents/service.py:841-964`、`reference_preview.py:82-164` |
| system health/cache debug | `/health`、`/background_status`、conversation cache debug | public ops | 保留 | `system/api.py:21-35,83-95` |
| KB/model ops | `/kb_info`、`/refresh_kb`、`/admin/model-status` | QA/retrieval/model ops | 迁到 QA ops/gateway admin ops | `system/api.py:38-70`、`system/service.py:189-323,1015-1082` |
| departments/personnel/admin_users | admin routers | public authority | 保留并补文档 inventory | `main.py:13,17,19,28-30` |

#### 删除/迁出安全判定

| 候选项 | 现状 | 安全判定 | 前置条件 |
|---|---|---|---|
| 删除 literature/reference routes | 前端和 gateway 仍调用 | 不安全 | 前端改到新 API；gateway proxy 改到新服务；public-service 保留兼容代理或返回明确 deprecation |
| 删除 model-status route | admin UI 和 gateway route table 仍依赖 | 不安全 | QA ops route 上线；admin UI 改调用；gateway route table 更新 |
| 禁用 retrieval startup | 当前 create_runtime 无条件调用 | 不安全直接禁用 | 增加 public-only flag + health schema 兼容；KB routes 代理或 degraded 明确化 |
| 迁出 documents processing | 前端 PDF reader 使用摘要/翻译/SSE | 可迁但需代理 | 目标 service 支持 auth/quota/SSE；public route 保留一版代理 |
| 迁出 upload-processing worker | 状态字段是会话文件 contract | 可迁但需状态读模型 | queue contract、worker consumer、recovery scanner、状态字段兼容 |
| 移除 patent renderer | 测试固定 user-visible 清洗 | 不安全直接移除 | patent/shared renderer golden tests；conversation read-time compat flag |
| 合并 quota local dependency | 本地 routes 仍用 deps.py | 可迁 | 抽 `QuotaReservationService`；所有本地 dependency 返回 grant_id 并 finalize |
| 清理 public-modules 漂移 | 文档漂移，不影响 runtime | 安全但本轮禁止 | 另开 docs 任务，基于 live inventory 更新 |

### 4. 测试护栏闭环

本轮未运行 `pytest --collect-only public-service/backend/tests`。原因：仓库中已存在 `public-service/backend/.pytest_cache/`、`public-service/backend/tests/__pycache__/`、`public-service/data/runtime/data/conversations/*.json|*.lock` 等运行态文件；在“只读排查、不运行会写文件命令”的硬约束下，pytest collection 可能刷新 `.pytest_cache` 或导入时触发 runtime side effect，因此仅做静态测试文件证据收集。

测试护栏清单：

| 护栏 | 已有证据 | 缺口 |
|---|---|---|
| route surface | `test_route_surface.py:20-43` 固定 `/api/reference_preview`、`/api/kb_info` 等 route；`test_model_status.py:25-34` 固定 model-status admin guard | 缺迁出后 gateway/public-service 双端 contract |
| gateway proxy | `gateway/tests/test_public_proxy.py:535-539` 固定 `/api/v1/reference_preview` 转发；`gateway/tests/test_route_table.py:98-100` 固定 model-status route table | 缺 literature_content、kb_info、refresh_kb、model-status 代理回归组合测试 |
| documents processing/SSE | `test_documents_module.py:514-597` 覆盖 translate_document quota 和 SSE；`671-685` 覆盖 extract_pdf_text strict quota/config | 缺迁出后 SSE first-byte/header proxy test |
| retrieval degraded contract | `test_documents_module.py:826-835` 固定 reference_preview runtime 缺失时 dependency payload；`714-728` 固定 literature runtime 缺失 | 缺 public-only startup 不加载 Chroma/Neo4j 的 contract |
| upload-processing state | `test_conversation_module.py:2735-2807` 覆盖 parse/index ready、错误、state persist fail | 缺外部 worker queue contract 和 recovery scanner 迁出测试 |
| quota grant lifecycle | `test_quota_module.py:769-793` 覆盖 finalize 临时失败重试；`995-1015` 覆盖 cleanup 释放 pending capacity | 缺运行中 sweeper、gateway finalize 网络失败补偿测试 |
| conversation JSON/DB/cache | `conversation/json_store.py`、`repository.py`、`cache.py` 有实现证据；已有 conversation module tests 覆盖大量读写 | 缺明确“JSON 正文主事实源、DB index、Redis cache/lock”的 contract test |
| patent citation renderer | `test_conversation_module.py:1075-1173`、`test_conversation_authority_api.py:564-622`、`test_conversation_authority_integration.py:507-620` | 缺 renderer 抽出后的 shared golden tests 和旧消息兼容读测试 |

### 5. 可实施重构任务拆分

### TASK-301：冻结 public-service live route 与调用方 contract

- 目标：在任何迁出前固定当前 live route、gateway proxy、前端调用 contract，防止误删。
- 范围：`/literature_content`、`/reference_preview`、`/kb_info`、`/refresh_kb`、`/admin/model-status`、documents processing routes。
- 前置条件：确认 gateway 是 canonical ingress；确认前端不直连 public-service。
- 实施步骤：补 gateway proxy contract；补 frontend API service structure tests；标注 canonical/compat route；为迁出 route 增加 deprecation metadata。
- 验证命令：`pytest gateway/tests/test_public_proxy.py gateway/tests/test_route_table.py`；`npm run build`；public-service route tests。
- 回滚策略：保留旧 proxy 表和旧 public-service routes。
- 验收标准：所有 live route 都有调用方和测试护栏；删除清单中无 unknown。

### TASK-302：引入 public-only runtime 并迁出 retrieval startup

- 目标：让 public-service 默认只启动 public authority 依赖，不装配 Chroma/Neo4j/agent。
- 范围：`core/runtime.py`、`modules/retrieval/*`、`system kb_info/refresh_kb`、documents literature/reference enrichment。
- 前置条件：确定 KB/model ops 目标归属为 QA ops 或 retrieval-service。
- 实施步骤：新增 `PUBLIC_SERVICE_ENABLE_RETRIEVAL_COMPAT`；默认关闭 `_bootstrap_retrieval()`；KB routes 改代理/degraded；把 `retrieval/service.py` 迁到 QA/retrieval service；保留 response schema。
- 验证命令：public-only startup smoke；system route tests；documents degraded tests；gateway proxy tests。
- 回滚策略：打开 compat flag 恢复旧 in-process retrieval。
- 验收标准：默认启动不访问 Chroma/Neo4j，`/health` 正常，旧 KB/reference/literature route 有兼容响应。

### TASK-303：拆分 documents asset authority 与 document-processing service

- 目标：public-service 保留 document asset/metadata，PDF 摘要、抽取、翻译、SSE 迁到 processing service。
- 范围：`documents/api.py`、`documents/service.py`、`translator.py`、`translation_service.py`、gateway proxy、frontend PdfReader。
- 前置条件：processing service 支持 auth/quota、SSE、PDF asset 读取、错误码兼容。
- 实施步骤：定义 processing API；public-service route 先代理；迁移 OpenAI/fitz/translation cache；保留 `/api` 与 `/api/v1`；对 SSE 禁止 buffering。
- 验证命令：documents module tests；gateway translate_document proxy test；frontend PdfReader structure/build；SSE header/stream test。
- 回滚策略：compat flag 切回 in-process handler。
- 验收标准：前端摘要/翻译/流式翻译无 contract 变化，public-service 默认无 LLM/fitz 硬依赖。

### TASK-304：迁出 upload-processing worker 并保留 file 状态读模型

- 目标：把 PDF/Excel parse/index worker 移到外部 document-processing/QA indexing worker，public-service 只维护 metadata/status。
- 范围：`conversation/upload_processing_worker.py`、`conversation/service.py` recovery/update state、`uploads/api.py` persist flow。
- 前置条件：定义 `UploadProcessingJob` queue payload；目标 worker 可回写 `parse_status/index_status/processing_stage`。
- 实施步骤：upload 后 enqueue external job；保留 `update_uploaded_file_processing_state()` authority；迁移 recovery scanner；删除 in-process executor 默认启动。
- 验证命令：upload tests；conversation worker tests 改为 queue/state tests；integration worker consumes queued file。
- 回滚策略：开启 in-process worker compat flag。
- 验收标准：上传响应字段不变，pending/recovery 不丢，解析失败状态可见。

### TASK-305：拆分 conversation authority 的事实源边界

- 目标：把 JSON 主文档、DB 索引、Redis cache/lock 从巨型 `ConversationService` 中明确分层。
- 范围：`conversation/service.py`、`json_store.py`、`repository.py`、`cache.py`、`outbox.py`。
- 前置条件：补 contract tests 固定 JSON/DB/Redis 职责。
- 实施步骤：引入 `ConversationDocumentAuthority`、`ConversationIndexRepository`、`ConversationCachePolicy`；文件 metadata 拆为 `ConversationFileAuthority`；保留旧 service facade。
- 验证命令：conversation module/authority/task/runtime tests；JSON remote restore/outbox tests；cache invalidation tests。
- 回滚策略：旧 `ConversationService` facade 继续代理新类，逐步切回。
- 验收标准：正文读写唯一经 JSON document，DB 不被当正文事实源，Redis 不参与最终一致性判断。

### TASK-306：统一 quota grant 生命周期并增加运行中补偿

- 目标：internal HTTP grant 和本地 dependency grant 共用同一 reservation/finalize store，并补运行中 sweeper。
- 范围：`quota/api.py`、`quota/deps.py`、`quota/service.py`、documents/conversation quota 使用点、gateway finalize client。
- 前置条件：定义成功响应但扣费失败的业务策略；确认 gateway finalize retry/backoff。
- 实施步骤：抽 `QuotaReservationService`；本地 dependency 也创建 grant_id；finalize 写 finalized 幂等结果；新增 pending sweeper；gateway 失败重试。
- 验证命令：quota module tests；documents quota tests；gateway internal quota contract；并发 finalize tests。
- 回滚策略：保留旧 local dependency behind env。
- 验收标准：所有扣费路径可追踪 grant_id，pending 不会长期占用额度，重复 finalize 不重复扣费。

### TASK-307：迁出 patent citation renderer

- 目标：把 patent citation presentation 从 conversation core 移到 patentQA/shared presentation。
- 范围：`conversation/service.py` patent normalizer、patent QA terminal event、frontend patent rendering。
- 前置条件：确认前端是否另有 patent citation 渲染；抽 golden cases。
- 实施步骤：创建 shared renderer；patentQA 写入 terminal/progress 前清洗；conversation 保留 read-time compat normalizer；逐步关闭 write-time normalizer。
- 验证命令：patent renderer golden tests；conversation authority tests；patent integration tests。
- 回滚策略：恢复 conversation read/write normalizer flag。
- 验收标准：新消息由 patent side 清洗，历史消息仍可读，conversation core 不含 patent-specific renderer。

### TASK-308：修正 live inventory 与 public-modules 漂移

- 目标：让重构文档以当前 live code 为基线，避免误删 active path。
- 范围：`public-service/public-modules` 文档、backend live inventory、scripts env 边界文档。
- 前置条件：本轮禁止改 public-service 文档，需另开文档任务。
- 实施步骤：补 departments/personnel/retrieval/qa_cache/uploads 状态；修正旧 `/home/cqy/worktrees/public-service` 路径；标注 `public-modules/uploads` 缺失；新增 docs link/file existence check。
- 验证命令：docs link check；`find public-service/public-modules` 与 `find public-service/backend/app/modules` diff check。
- 回滚策略：保留历史文档为 archive，新增 current inventory。
- 验收标准：文档不再把不存在目录或旧 worktree 路径当 live fact。

### 6. 不可立即处理项与阻塞原因

| 阻塞项 | 原因 | 解锁证据/决策 |
|---|---|---|
| 直接删除 literature/reference/model-status | 前端、gateway、public-service tests 均有 live 证据 | 新服务上线、gateway/前端切流、compat route 验证 |
| 立即禁用 retrieval startup | `create_runtime()` 无条件 `_bootstrap_retrieval()`；KB/reference/literature 依赖 runtime agent | public-only flag、KB ops 归属、degraded schema 测试 |
| documents processing 归属 | API 是 public live 面，但实现是 LLM/PDF/SSE 执行能力 | 产品确认“文档公共能力”是 asset/metadata 还是 processing；目标 service contract |
| upload-processing 迁出 | 状态字段已进入会话文件 metadata 和恢复逻辑 | queue contract、外部 worker、状态回写 API |
| conversation 唯一事实源 | 代码可推断但未显式封装；贸然拆分会破坏 JSON/DB/cache/outbox 一致性 | 先补 contract tests 与分层 facade |
| quota finalize 补偿策略 | 现有 retry/idempotency/启动清理不足以覆盖运行中网络失败 | gateway retry 策略、业务对计费失败的处理策略、sweeper 设计 |
| scripts env 边界 | 默认加载 model/graph 可能是现有部署依赖 | 运维确认 public-only env 与 compat env 切换窗口 |
| public-modules 漂移修正 | 本轮硬约束禁止修改 public-service 文档 | 单独 docs 任务 |

### 7. 最终进入重构前检查清单

- [ ] `V-301` 涉及 API 的前端/gateway/public-service 调用方全部有 contract test。
- [ ] `V-302` public-only startup 测试通过：默认不访问 Chroma/Neo4j/agent，`/api/health` 仍可用。
- [ ] `V-303` document-processing 目标服务支持摘要、抽取、翻译、`translate_document` SSE、auth/quota/error schema。
- [ ] `V-304` 上传后 `parse_status/index_status/processing_stage` 状态读模型保持兼容。
- [ ] `V-305` conversation 事实源 contract 明确：JSON 正文、DB index/counter/sync metadata、Redis cache/lock。
- [ ] `V-306` patent citation renderer 有 shared golden tests，conversation 保留历史兼容读。
- [ ] `V-307` quota 本地 dependency 与 internal grant 合并到同一 reservation/finalize store。
- [ ] `V-308` quota pending grant 有运行中 sweeper 或 gateway retry 补偿，不只依赖启动清理。
- [ ] `V-309` scripts 默认 env 拆分为 public-only 与 QA compat，两者有启动 smoke。
- [ ] `V-310` live inventory 以 `backend/app/main.py`、`backend/app/modules`、gateway proxy、frontend API 为最终事实，不以漂移 README 作最终事实。

本轮新增验证项：`V-301`、`V-302`、`V-303`、`V-304`、`V-305`、`V-306`、`V-307`、`V-308`、`V-309`、`V-310`。

本轮新增任务卡：`TASK-301`、`TASK-302`、`TASK-303`、`TASK-304`、`TASK-305`、`TASK-306`、`TASK-307`、`TASK-308`。
