# fastapi-version 后端依赖关系详解

基于仓库 `/home/cqy/worktrees/fastapi-version/backend/app` 的实际代码整理。

目标：
- 把整个后端的真实分层和依赖关系梳理清楚
- 区分“FastAPI 公共后端层”和“问答执行层 / legacy agent 层”
- 明确每个模块向下依赖什么、谁在运行时装配它、谁在调用它

这份文档不是抽象设计稿，而是按当前代码实际组织出来的依赖图。

## 1. 总体结论

当前后端不是一个单纯的 `ask_stream` 服务，而是一个已经成型的多层系统：

### A. FastAPI 入口层
- `main.py`
- `core/*`
- 对外路由模块：`auth / admin_users / quota / conversation / documents / uploads / system / ask_dispatch / ask_gateway`

### B. 平台公共服务层
- `auth`
- `admin_users`
- `quota`
- `conversation`
- `documents`
- `uploads`
- `storage`
- `system`

### C. 问答入口编排层
- `ask_gateway`
- `ask_dispatch`
- `file_context`

### D. 问答执行层
- `qa_kb`
- `qa_pdf`
- `qa_tabular`
- `qa_cache`
- `generation_pipeline`

### E. legacy agent / service 层
- `agents/*`
- `services/*`
- `microscopic_expert`
- `microscopic_runtime`
- `microscopic_search`

### F. 外部集成层
- `integrations/redis`
- `integrations/storage`
- `integrations/embedding`
- `integrations/vector_db`
- `integrations/neo4j`
- `integrations/llm`

## 2. 代码总入口与启动顺序

### 2.1 FastAPI 应用入口

文件：
- `backend/app/main.py`

职责：
- 创建 FastAPI app
- 配置 CORS、日志、异常处理
- 绑定 lifespan
- 挂载所有公开 router

当前挂载的 router：
- `system`
- `auth`
- `admin_users`
- `quota`
- `conversation`
- `documents`
- `uploads`
- `ask_dispatch`
- `ask_gateway`

这说明：
- 当前对外的公共 API 已经集中在这个仓库中
- `ask_gateway` 只是其中一个模块，不是整个系统本身

### 2.2 lifespan 启动链

文件：
- `backend/app/core/runtime.py`

`lifespan()` 做的事情：
1. `create_runtime(settings)`
2. 把 runtime 挂到 `app.state.runtime`
3. `bootstrap_runtime_dependencies(runtime)`
4. 启动 `conversation_outbox_worker`
5. 应用结束时停止上传处理 worker 和 outbox worker

## 3. runtime 是整个后端的核心装配中心

文件：
- `backend/app/core/runtime.py`

`AppRuntime` 持有的核心状态：
- `settings`
- `db`
- `storage_backend`
- `redis_client`
- `embedding_client`
- `vector_db_client`
- `neo4j_client`
- `agent`
- `generation_runtime`
- `llm_client`
- `pdf_web_bindings`
- `upload_processing_worker`
- `conversation_outbox_worker`
- `answer_cache`
- `current_pdf_path`
- `component_status`

### 3.1 create_runtime() 做了什么

`create_runtime()` 在应用真正可用前先构造这些本地对象：
- MySQL `Database`
- `logs/`
- `uploads/`
- `papers/`
- `storage_backend`
- `pdf_web_bindings`
- `upload_processing_worker`
- `init_agent` 闭包

关键依赖：
- `storage_backend` 来自 `integrations/storage/factory.py`
- `pdf_web_bindings` 来自 `qa_pdf_service.build_web_bindings()`
- `upload_processing_worker` 依赖 `conversation_service` 和 `pdf_web_bindings.extract_pdf_text`

这意味着：
- runtime 在启动阶段就把上传后处理链和 PDF 解析能力接好了
- 会话层和 PDF 能力在进程启动时就已经产生耦合

### 3.2 bootstrap_runtime_dependencies() 做了什么

依次执行：
1. `_bootstrap_database`
2. `_bootstrap_redis`
3. `_bootstrap_storage`
4. `_bootstrap_upload_processing`
5. `_bootstrap_retrieval`
6. `_bootstrap_agent`

对应真实下游：
- database -> `core/db.py`
- redis -> `integrations/redis`
- storage -> `integrations/storage`
- upload_processing -> `conversation/upload_processing_worker.py`
- retrieval -> `modules/retrieval/service.py`
- agent -> `agents.MaterialScienceAgent` + `services/web_agent_bootstrap.py`

### 3.3 runtime 中最重要的两条装配线

#### 线 1：公共平台能力装配线
- MySQL
- Redis
- MinIO/local storage
- upload processing worker
- conversation outbox worker

#### 线 2：问答能力装配线
- retrieval bindings
- MaterialScienceAgent
- LLM client
- PDF web bindings

这两条线共存，说明当前仓库同时承担：
- 平台公共后端
- 问答执行宿主

## 4. core 层依赖关系

### 4.1 `core/config.py`

职责：
- 统一读取环境变量
- 生成 `Settings`
- 解析 MySQL / Redis / MinIO / SSE / ask concurrency 等配置

被谁依赖：
- `main.py`
- `core/db.py`
- `integrations/storage/factory.py`
- `modules/quota/service.py`
- `modules/ask_dispatch/service.py`
- `modules/conversation/service.py`
- 其他 repository/service

地位：
- 全系统配置源头

### 4.2 `core/db.py`

职责：
- MySQL 连接创建
- `connection()` 上下文
- `ping()`

被谁依赖：
- `auth.repository`
- `quota.repository`
- `conversation.repository`
- `conversation.outbox`

地位：
- 所有 DB repository 的共同底座

### 4.3 `core/errors.py`

职责：
- 统一 `AppError`
- FastAPI 全局异常处理注册
- 统一验证错误/内部错误响应

被谁依赖：
- `main.py`
- `auth/deps.py`
- `quota/deps.py`
- `core/deps.py`

### 4.4 `core/deps.py`

职责：
- runtime 注入
- settings 注入
- DB / storage / llm / embedding / vector_db / neo4j 获取
- 通用 auth/admin 判断

但需要注意：
- 真正项目中常用的认证依赖实际在 `modules/auth/deps.py`

### 4.5 `core/sse.py`

职责：
- 统一 SSE Response 封装

直接使用者：
- `ask_gateway/api.py`

## 5. 外部集成层依赖关系

## 5.1 Redis

文件：
- `integrations/redis/client.py`
- `integrations/redis/service.py`
- `integrations/redis/keys.py`
- `integrations/redis/locks.py`

职责：
- 根据 `Settings` 构造 Redis bindings
- 提供 `RedisService` 简单 JSON/TTL/prefix 能力
- 提供 key factory
- 提供 lock manager

上层调用者：
- `quota.service`
- `conversation.service`
- `system.service`
- `ask_dispatch.service`
- `ask_gateway.helpers`
- `qa_cache/*`
- `qa_kb.orchestrators.generation`

判断：
- Redis 在当前代码里同时承担：
  - cache
  - singleflight/lock
  - task queue / task event stream
  - debug / metrics 支撑

## 5.2 Storage

文件：
- `integrations/storage/base.py`
- `integrations/storage/local.py`
- `integrations/storage/minio.py`
- `integrations/storage/factory.py`

职责：
- 抽象 `StorageBackend`
- 支持 Local 和 MinIO 两种 backend
- factory 根据配置自动回退到 local

上层调用者：
- `modules/storage/service.py`
- `conversation/json_store.py`
- `conversation/outbox_worker.py`
- runtime 启动

判断：
- 当前所有文件体系最终都通过这一层落到本地或 MinIO

## 5.3 Embedding / Vector DB / Neo4j

文件：
- `integrations/embedding/client.py`
- `integrations/vector_db/client.py`
- `integrations/neo4j/client.py`

职责：
- embedding client 构造
- Chroma/vector DB 访问
- Neo4j 启动与降级探测

上层调用者：
- `modules/retrieval/service.py`

判断：
- `retrieval` 是这些集成的统一装配门面

## 5.4 LLM

文件：
- `integrations/llm/__init__.py`
- `integrations/llm/dashscope.py`

职责：
- 统一提供 DashScope native / compat client
- 消息归一化
- streaming/non-streaming transport

上层调用者：
- `qa_pdf.llm_factory`
- `generation_pipeline`
- `services/agent_initializers.py`

## 6. 路由层到 service 层的依赖总览

公开 router 模块都遵循类似结构：
- `api.py` 负责 HTTP 输入输出和依赖注入
- `service.py` 负责业务编排
- `repository.py` 负责 DB/Redis 持久化
- 某些模块还有 `cache.py` / `worker.py` / `json_store.py`

## 7. 平台公共服务模块详解

## 7.1 `auth`

文件：
- `modules/auth/api.py`
- `modules/auth/service.py`
- `modules/auth/repository.py`
- `modules/auth/deps.py`

依赖链：
- `auth/api.py`
  -> `auth/deps.py`
  -> `auth/service.py`
- `auth/service.py`
  -> `auth/repository.py`
- `auth/repository.py`
  -> `core/db.py`
  -> `core/config.py`

职责拆分：
- `api.py`：登录、注册、改密、forgot-password、安全问题等 HTTP 路由
- `service.py`：
  - token 生成/解析
  - 密码哈希与校验
  - 登录失败锁定
  - 密码复杂度和历史限制
  - 安全问题逻辑
- `repository.py`：
  - `users` 表读写
  - `password_history`
  - `security_questions`

它是全系统最上游的权限前置依赖。

## 7.2 `admin_users`

依赖链：
- `admin_users/api.py`
  -> `auth/deps.require_admin_context`
  -> `admin_users/service.py`
  -> `admin_users/import_service.py`
- `admin_users/service.py`
  -> `auth.repository.AuthRepository`
  -> `auth.service`
- `admin_users/import_service.py`
  -> `admin_users/service`
  -> `auth.service`
  -> `quota.deps`
  -> `quota.service`

特点：
- 管理员用户管理本质上复用了 auth repository
- 说明当前 `admin_users` 不是独立账号系统，而是 auth 的后台操作面

## 7.3 `quota`

依赖链：
- `quota/api.py`
  -> `auth/deps`
  -> `quota/service.py`
- `quota/deps.py`
  -> `auth/deps`
  -> `auth.service`
  -> `quota.service`
- `quota/service.py`
  -> `quota/repository.py`
  -> `quota/cache.py`
  -> `integrations/redis`
- `quota/repository.py`
  -> `core/db.py`

特点：
- `quota.api` 是配置与查询面
- `quota.deps` 是被其他路由复用的横切接线层
- `quota.service` 同时用 MySQL 和 Redis cache

被哪些模块复用：
- `ask_gateway/api.py`
- `documents/api.py`
- `conversation/api.py`
- `uploads/api.py`
- `admin_users/import_service.py`

## 7.4 `conversation`

文件很多，是平台最复杂的公共模块之一。

主依赖链：
- `conversation/api.py`
  -> `auth/deps`
  -> `quota.deps`（下载场景）
  -> `conversation/service.py`
- `conversation/service.py`
  -> `conversation/repository.py`
  -> `conversation/json_store.py`
  -> `conversation/outbox.py`
  -> `conversation/cache.py`
  -> `modules/storage/service.py`
  -> `integrations/redis`
- `conversation/json_store.py`
  -> `integrations/storage.factory`
- `conversation/outbox_worker.py`
  -> `conversation/outbox.py`
  -> `conversation/repository.py`
  -> `integrations/storage`
- `conversation/upload_processing_worker.py`
  -> `conversation/service.py`
  -> runtime 注入的 `extract_pdf_text_fn`

这个模块内部实际上有三套状态：

### 1. MySQL 元数据
- 会话主表
- message count
- 文件记录

### 2. JSON 权威/半权威文档
- `ConversationJsonStore`
- 每个会话有本地 JSON 文档
- 通过 outbox 同步到对象存储

### 3. Redis 缓存
- 会话列表缓存
- 会话详情缓存
- recent pages 记录

这意味着 `conversation` 不是简单 CRUD，而是：
- DB 元数据层
- JSON 文档层
- 缓存层
- 文件清理层
- 后台同步层

### 问答链路中的特殊地位

`conversation.service` 还承担：
- `persist_user_request()`
- `persist_assistant_summary()`

并且被 `ask_gateway_service.register_defaults(...)` 注册进去。

所以它既是公共会话模块，又是当前 ask 链路的持久化真相源。

## 7.5 `uploads`

依赖链：
- `uploads/api.py`
  -> `core/deps.get_runtime`
  -> `auth/deps.get_optional_auth_context`
  -> `auth.service`
  -> `quota.service`
  -> `conversation.service`
  -> `storage.service`

上传流程：
1. 解析 multipart
2. 可选用户配额检查
3. 保存到本地 `uploads/`
4. 镜像到对象存储
5. 把文件记录写入 `conversation`
6. 提交 `upload_processing_worker`

特点：
- 它没有独立 service.py，业务逻辑直接写在 API 模块里
- 但实际依赖已经连接了 quota、conversation、storage、runtime

## 7.6 `documents`

依赖链：
- `documents/api.py`
  -> `auth/deps`
  -> `quota.deps`
  -> `documents/service.py`
- `documents/service.py`
  -> `documents/reference_preview.py`
  -> `documents/translation_service.py`
  -> `qa_pdf.pdf_extractor`
  -> `storage.service`

职责性质：
- 论文原文与引用服务
- 不属于 ask 执行器
- 但会借用 `qa_pdf` 的 PDF 文本提取能力

这说明：
- `documents` 是公共文档服务
- 但它与 `qa_pdf` 在“PDF 抽取”层发生共享

## 7.7 `storage`

依赖链：
- `modules/storage/service.py`
  -> `integrations/storage.factory`
  -> `integrations/storage.minio`

它本身是 platform storage facade。

上游使用者：
- `uploads`
- `conversation`
- `documents`
- `generation_pipeline` 里的 paper loader
- `services/storage/paper_storage.py` compatibility wrapper

## 7.8 `system`

依赖链：
- `system/api.py`
  -> `core/deps.get_runtime`
  -> `auth/deps`（部分调试接口）
  -> `system/service.py`
- `system/service.py`
  -> `integrations/redis`
  -> `conversation.cache`
  -> `qa_cache.metrics`
  -> `retrieval.service`

特点：
- `system` 不是独立业务域
- 它是 runtime、cache、retrieval、worker 状态的观察窗

## 8. 问答入口层详解

## 8.1 `ask_gateway`

依赖链：
- `ask_gateway/api.py`
  -> `core/deps.get_runtime`
  -> `auth/deps.require_auth_context`
  -> `quota.deps.require_quota("ask_query")`
  -> `core/sse.sse_response`
  -> `ask_gateway/service.py`

`ask_gateway/service.py` 直接依赖：
- `conversation.service`
- `file_context.service`
- `qa_kb.service`
- `qa_pdf.service`
- `qa_tabular.service`
- `ask_gateway.helpers`
- `ask_gateway.streaming`
- `ask_gateway.limits`
- `integrations.redis`

职责：
- 请求 enrich
- 并发限制
- 会话消息落库
- route 决策后的执行分发
- SSE 统一事件输出
- 完成态 assistant summary 持久化

关键边界：
- 它是 ingress/orchestrator
- 它不是平台公共服务
- 也不是最终回答执行器

## 8.2 `file_context`

依赖链：
- `ask_gateway/service.py`
  -> `file_context.service.resolve_request_file_context`
- `file_context.service`
  -> `file_context.parser`
  -> `file_context.models`

职责：
- 根据问题内容、conversation 文件、pdf_context、当前 PDF 路径，产出：
  - `used_files`
  - `execution_files`
  - `route_hint`
  - `turn_mode`
  - `allow_kb_verification`
  - 是否需要澄清

它是 ask 路由前置解析器，不直接做回答。

## 8.3 `ask_dispatch`

依赖链：
- `ask_dispatch/api.py`
  -> `auth/deps`
  -> `ask_dispatch/service.py`
- `ask_dispatch/service.py`
  -> `integrations/redis`
  -> `ask_dispatch/repository.py`
  -> `ask_dispatch/events.py`
  -> `ask_dispatch/schemas.py`
- `ask_dispatch/repository.py`
  -> `integrations.redis.RedisService`

职责：
- task 入队
- task 查询
- event 读取
- cancel 标记
- worker claim/ack

当前状态：
- 已经是可用的 Redis task/event 基础层
- 但当前浏览器 ask 主入口还没真正走它

## 9. 问答执行层详解

## 9.1 `qa_kb`

依赖链：
- `qa_kb/service.py`
  -> `generation_pipeline` lazy export
  -> `qa_kb.md_expansion`
  -> `qa_kb.semantic_legacy`
  -> `qa_kb.models`
  -> `qa_kb.orchestrators.generation`
  -> `qa_kb.streaming`
  -> `integrations.redis`

运行逻辑：
- `QaKbService.iter_answer_events()` 决定走：
  - 新 generation-driven pipeline
  - 旧 semantic/precise legacy pipeline

也就是说：
- `qa_kb` 不是单一路径
- 它是一个兼容层，内部继续桥接“新问答链”和“旧问答链”

### 新链
- `GenerationPipelineOrchestrator`
  - `Stage1Planner`
  - `Stage2Retriever`
  - `Stage25MdExpansion`
  - `Stage3PdfLoader`
  - `Stage4Synthesizer`
- 结合 `qa_cache` 做 stage1/stage2 缓存和 singleflight

### 旧链
- `qa_kb.semantic_legacy`
- 最终仍依赖 legacy agent 相关能力

## 9.2 `qa_pdf`

依赖链：
- `qa_pdf/service.py`
  -> `qa_pdf.common`
  -> `qa_pdf.sidecar_client`
  -> `qa_pdf.engine`
  -> `qa_pdf.streaming`
  -> `qa_pdf.truncation`

职责：
- DOI direct PDF 查询
- 单 PDF 上传问答
- 多 PDF 合并问答
- sidecar 分发
- 本地 fallback

特点：
- `qa_pdf` 内部已经有“sidecar / local 双执行策略”
- `ask_gateway` 只是把请求交给它

## 9.3 `qa_tabular`

依赖链：
- `qa_tabular/service.py`
  -> `qa_tabular.executor`
  -> `qa_tabular.planner`
  -> `qa_tabular.renderer`
  -> `qa_tabular.schema_profiler`
  -> `qa_tabular.workbook_loader`
  -> `qa_pdf.common`（复用增量清洗）

职责：
- 读取 Excel/CSV
- profile schema
- plan query
- 执行单表/多表对比
- 渲染答案
- hybrid 模式下还会拉 PDF 证据

特点：
- 它不是单纯表格问答
- 在 `hybrid_qa` 下，还会消费 PDF 片段作为辅助证据

## 9.4 `qa_cache`

依赖链：
- `qa_cache/*`
  -> `integrations.redis`

职责：
- stage1/stage2 结果缓存
- PDF text cache
- singleflight
- metrics

被谁使用：
- `ask_gateway.helpers`
- `qa_kb.orchestrators.generation`
- `conversation.cache`
- `system.service`

## 10. generation_pipeline 是 KB 新链的核心实现

文件量很大，属于当前代码中最重的一层之一。

入口：
- `modules/generation_pipeline/__init__.py`
  -> lazy export `GenerationDrivenRAG`
- 实现类：
  - `generation_driven_rag_facade.py`

它内部依赖：
- `dependencies.py`
- `runtime_bootstrap.py`
- `query_expander.py`
- `stage1_planning.py`
- `stage2_retrieval.py`
- `pdf_pipeline.py`
- `context_loading.py`
- `reference_alignment.py`
- `retrieval_validation.py`
- `doi_validation.py`
- `doi_inserter.py`
- `audit_verification.py`
- `synthesis_streaming.py`
- `synthesis_postprocess.py`
- `vector_database_schema.py`
- `prompt_templates.py`
- `feature_flags.py`
- `text_processing.py`
- `integrations.llm`
- `modules.storage.paper_storage`
- `modules.microscopic_expert`

判断：
- 这是 KB generation-driven 新链的“大脑”
- `qa_kb` 通过 orchestrator 对它进行阶段化封装

## 11. legacy agent / services 层详解

这一层说明当前仓库并没有完全摆脱旧系统，而是把旧能力包进了新后端里。

## 11.1 `agents.MaterialScienceAgent`

文件：
- `agents/__init__.py`
- `agents/material_science_agent.py`

runtime 中实际 bootstrap 的 agent：
- `runtime._build_init_agent()`
  -> `from app.agents import MaterialScienceAgent`
  -> `services.web_agent_bootstrap.initialize_web_agents()`
  -> `MaterialScienceAgent()`

`MaterialScienceAgent` 依赖：
- `CommanderAgent`
- `DualRetrievalAgent`
- `Neo4jTwoStageOptimizer`
- `MicroscopicSemanticExpert`
- `services.agent_bootstrap_helpers`
- `services.material_science_agent_delegates`
- `services.pdf_loader`

这说明：
- 旧系统的 query / hybrid / semantic / community / direct_pdf 等能力仍聚合在这个 agent 上
- 新 FastAPI 后端并不是完全替换 legacy agent，而是把它纳入 runtime

## 11.2 `services/web_agent_bootstrap.py`

职责：
- 初始化 `MaterialScienceAgent`
- 预留 generation runtime

被谁使用：
- `core/runtime.py`

它是 runtime 与 legacy agent 的桥。

## 11.3 `services/agent_bootstrap_helpers.py`

职责：
- 解析 `papers_dir`
- 加载 prompt 模板
- 初始化 semantic expert
- 构造 agent runtime state

被谁使用：
- `agents/material_science_agent.py`

## 11.4 `modules/microscopic_expert.py`

职责：
- 初始化 Chroma
- 初始化 embedding model
- 初始化翻译器
- 做 rerank 和 semantic search

它依赖：
- `microscopic_runtime`
- `microscopic_search`
- `generation_pipeline.rerank_service`

判断：
- 这是较旧的语义专家实现，但仍然被 agent runtime 使用

## 11.5 `services/*`

`services` 目录里大量代码仍是 legacy 逻辑容器，典型包括：
- `material_science_agent/*`
- `semantic_answer_orchestrator/*`
- `query_execution/*`
- `hybrid_orchestrator/*`
- `routing_orchestrator.py`
- `graph_query_engine.py`
- `answer_synthesis.py`
- `pdf_loader.py`

这些服务层主要被：
- `agents/*`
- `material_science_agent_delegates.py`
- 一部分 generation / semantic 路径
调用。

判断：
- 当前仓库是“新 FastAPI 壳 + 旧问答核心”的混合体

## 12. 数据与存储依赖关系

### 12.1 MySQL

主要通过 repository 访问：
- `auth.repository`
- `quota.repository`
- `conversation.repository`
- `conversation.outbox`

### 12.2 Redis

主要被用于：
- 配额配置缓存
- 会话列表/详情缓存
- QA 阶段缓存
- PDF text cache
- singleflight lock
- ask dispatch task/event/cancel

### 12.3 本地文件系统

主要目录：
- `logs/`
- `uploads/`
- `papers/`
- 会话 JSON 文档本地副本

### 12.4 MinIO / object storage

主要用途：
- 上传文件镜像
- 会话 JSON 文档 outbox 同步
- DOI PDF 归档与下载

### 12.5 向量库 / Neo4j / embedding

主要通过 `retrieval.service` 统一装配，供：
- runtime 健康状态
- semantic expert
- generation-driven 检索链
使用。

## 13. 三条最关键的业务链路

## 13.1 `/api/v1/ask_stream`

链路：
1. `ask_gateway/api.py`
2. `auth/deps.require_auth_context`
3. `quota.deps.require_quota("ask_query")`
4. `ask_gateway_service.acquire_slot()`
5. `ask_gateway_service.enrich_request()`
6. `conversation_service.persist_user_request()`
7. `ask_gateway_service.stream_events()`
8. route 到：
   - `qa_kb`
   - `qa_pdf`
   - `qa_tabular`
9. `AskStreamTap` 汇总结果
10. `conversation_service.persist_assistant_summary()`
11. `release_slot()`

这是当前系统里最重要的“入口层 -> 执行层 -> 持久化层”贯通链。

## 13.2 文件上传

链路：
1. `uploads/api.py`
2. 可选 `auth` + `quota`
3. `storage_service.mirror_file()`
4. `conversation_service.add_uploaded_file()`
5. `upload_processing_worker.submit()`
6. worker 内部解析 PDF/Excel
7. `conversation_service.update_uploaded_file_processing_state()`

这条链说明：
- 上传文件并不是 ask 的附属功能
- 它已经有自己的平台化处理链和状态机

## 13.3 文档查看/翻译/总结

链路：
1. `documents/api.py`
2. 可选 `auth` + `quota`
3. `documents_service`
4. `storage_service.ensure_local_paper_pdf()`
5. `qa_pdf.pdf_extractor` 或 translation service

这条链说明：
- 文档服务是独立于 ask 的公共后端能力

## 14. 当前后端依赖的真实边界

## 14.1 已经比较清晰的边界

这些层次相对清晰：
- router -> service -> repository
- integrations 作为外部依赖适配层
- runtime 作为启动装配中心
- ask_gateway 作为 ingress
- conversation/quota/auth/documents 作为公共服务域

## 14.2 仍然比较混合的边界

主要混合点：
- 新 FastAPI 模块仍大量依赖 legacy agent/service
- `qa_kb` 同时维护新旧两条链
- `documents` 复用 `qa_pdf` 的 PDF 提取能力
- `uploads/api.py` 里直接承载较多业务逻辑，没有独立 service
- `conversation` 既是公共会话层，又承担 ask 持久化钩子

## 15. 用一句话概括整个依赖结构

当前后端的真实结构是：

`FastAPI 公共后端壳`
-> `runtime 统一装配 DB/Redis/Storage/Retrieval/Agent/Workers`
-> `公共服务模块处理用户、会话、文件、配额、文档、系统状态`
-> `ask_gateway 作为问答 ingress 分发到 qa_kb / qa_pdf / qa_tabular`
-> `这些执行器又继续桥接 generation_pipeline 和 legacy agent/services`

## 16. 对后续改造最重要的结论

1. 当前仓库已经是完整公共后端，不应把它误判为单一 fast QA 服务。
2. `ask_gateway` 只是挂在公共后端上的问答入口，不应和公共能力混在一起定义。
3. 当前最大的技术现实不是“有没有公共能力”，而是“公共后端已经成型，但问答执行层仍混合了新旧实现”。
4. 如果要做多模式网关，最合理的是保留当前公共后端，先改 ingress 和 adapter，不要先拆散 `auth / quota / conversation / uploads / documents / storage / system`。
