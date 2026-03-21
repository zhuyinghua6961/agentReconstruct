# fastapi-version 后端模块钻取笔记

基于仓库 `/home/cqy/worktrees/fastapi-version/backend/app` 的实际代码整理。

这份文档是在已有两份总览文档之外，继续按“模块”往下钻。目标不是画抽象架构图，而是回答下面这些更具体的问题：

- 每个模块到底负责什么
- 它的入口文件和核心对象是什么
- 它被谁调用、又调用谁
- 它会碰哪些状态：MySQL / Redis / 本地文件 / 对象存储 / 向量库 / Neo4j / LLM
- 它应归类为“公共能力 / 共享基础设施 / 问答入口 / 问答执行 / legacy 支撑”中的哪一种

## 0. 先看总装配关系

### 0.1 FastAPI 公开入口

`backend/app/main.py` 真实挂载的 router：

- `system`
- `auth`
- `admin_users`
- `quota`
- `conversation`
- `documents`
- `uploads`
- `ask_dispatch`
- `ask_gateway`

结论：

- 这个仓库本身已经是完整后端，不只是 `ask_gateway`
- 公开平台能力和问答入口能力是在同一个 FastAPI 进程内共存的

### 0.2 运行时装配中心

`backend/app/core/runtime.py` 的 `lifespan()` 会：

1. 创建 `AppRuntime`
2. 装配数据库、Redis、对象存储、检索绑定、agent
3. 启动 `UploadProcessingWorker`
4. 启动 `ChatJsonOutboxWorker`
5. 把 runtime 挂到 `app.state.runtime`

`AppRuntime` 是整个后端的公共依赖容器，持有：

- `db`
- `redis_client`
- `storage_backend`
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

这意味着当前系统不是“平台层”与“问答层”完全隔离，而是运行时强绑定在一个宿主进程里。

## 1. 模块分类总表

### 1.1 明确属于公共能力

- `auth`
- `admin_users`
- `quota`
- `conversation`
- `uploads`
- `documents`
- `storage`
- `system`

### 1.2 更适合归为共享基础设施

- `core/*`
- `integrations/*`
- `file_context`
- `retrieval`

### 1.3 更适合归为问答入口编排

- `ask_gateway`
- `ask_dispatch`

### 1.4 更适合归为问答执行层

- `qa_cache`
- `qa_kb`
- `qa_pdf`
- `qa_tabular`
- `generation_pipeline`

### 1.5 更适合归为 legacy / 兼容支撑层

- `microscopic_runtime`
- `microscopic_expert`
- `microscopic_search`
- `agents/*`
- `services/*`

## 2. 公共能力模块

## 2.1 `auth`

分类：
- 公共能力

主要文件：
- `backend/app/modules/auth/api.py`
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/auth/deps.py`
- `backend/app/modules/auth/schemas.py`

对外接口：
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/register`
- `GET /api/v1/auth/me`
- `PUT|POST /api/v1/auth/password`
- `POST /api/v1/auth/forgot-password/initiate`
- `POST /api/v1/auth/forgot-password/verify`
- `GET /api/v1/auth/security-questions`
- `PUT|POST /api/v1/auth/security-questions`

模块职责：
- 登录、注册、当前用户信息
- Bearer token 解析与认证上下文构造
- 修改密码
- 忘记密码与安全问题校验
- 账号启停状态校验
- 为其他模块提供统一认证依赖

核心内部关系：
- `api.py` 只做 HTTP 入参和响应映射
- `service.py` 处理密码策略、登录校验、token 生成、密码更新等业务
- `repository.py` 直接访问用户表与密码历史/安全问题数据
- `deps.py` 把 token 解析成 `AuthContext`，并区分普通用户与管理员

上游调用方：
- FastAPI 所有需要登录态的 API
- `quota`、`conversation`、`documents`、`admin_users` 等模块的依赖注入
- 前端登录页、个人中心、忘记密码页

下游依赖：
- `core/db.py`
- `core/errors.py`
- `core/deps.py` 中的 `AuthContext`

状态落点：
- MySQL：用户、密码、安全问题、密码历史
- 进程内：认证上下文

为什么明确属于公共能力：
- 所有页面、所有业务接口都依赖同一身份体系
- 与问答模式无关
- 不含具体 QA 领域逻辑

边界与耦合：
- 认证本身边界清晰
- 但密码/用户类型策略与 `admin_users` 有一定耦合，二者共用用户数据模型

## 2.2 `admin_users`

分类：
- 公共能力

主要文件：
- `backend/app/modules/admin_users/api.py`
- `backend/app/modules/admin_users/service.py`
- `backend/app/modules/admin_users/import_service.py`
- `backend/app/modules/admin_users/schemas.py`

对外接口：
- `GET /api/admin/users`
- `POST /api/admin/users`
- `PUT /api/admin/users/{user_id}/password`
- `GET /api/admin/users/{user_id}/password`
- `PUT /api/admin/users/{user_id}/status`
- `PUT /api/admin/users/{user_id}/type`
- `DELETE /api/admin/users/{user_id}`
- `POST /api/admin/users/batch-import`
- `GET /api/admin/users/import-template`

模块职责：
- 管理员用户列表、创建、删除、启停、类型切换
- 管理员重置密码
- Excel/CSV 批量导入用户
- 导入模板下载

核心内部关系：
- `service.py` 负责管理员用户管理主流程
- `import_service.py` 负责 Excel/CSV 解析、配额前置检查、批量导入
- 底层复用 `auth.repository` 的用户访问能力

上游调用方：
- 管理后台页面
- 管理员 API

下游依赖：
- `auth.repository`
- `quota.service`，导入 Excel 时会先做上传配额检查
- `core/db.py`

状态落点：
- MySQL：用户主数据
- 上传导入时的临时内存数据

为什么属于公共能力：
- 属于典型平台后台能力
- 不绑定任何问答模式

边界与耦合：
- 与 `auth` 高耦合，共享同一用户存储
- 批量导入时又横向依赖 `quota`，说明平台规则在这里已经集中生效

## 2.3 `quota`

分类：
- 公共能力

主要文件：
- `backend/app/modules/quota/api.py`
- `backend/app/modules/quota/service.py`
- `backend/app/modules/quota/repository.py`
- `backend/app/modules/quota/cache.py`
- `backend/app/modules/quota/deps.py`
- `backend/app/modules/quota/schemas.py`

对外接口：
- `GET /api/v1/quota/my`
- `GET /api/v1/quota/configs`
- `POST /api/v1/quota/configs`
- `PUT /api/v1/quota/configs/{quota_type}`
- `POST /api/v1/quota/reset/{target_user_id}/{quota_type}`
- `GET /api/v1/quota/users/{target_user_id}`

模块职责：
- 查询用户当前配额
- 管理员维护配额配置
- 重置用户配额
- 给其他业务模块提供 `require_quota()` / `finalize_quota()` 依赖
- 统一处理周期窗口：`daily / weekly / monthly / custom_days / none`

核心内部关系：
- `api.py` 只暴露配额管理接口
- `service.py` 是真正的规则中心，负责检查、授予、消耗、查询
- `repository.py` 持久化配额配置和用户使用记录
- `cache.py` 用 Redis 缓存配额配置与覆盖项
- `deps.py` 把配额检查变成可插拔的 FastAPI 依赖

上游调用方：
- `uploads`：上传 PDF / Excel 前的可选配额检查
- `documents`：查看 PDF、总结 PDF、文本翻译
- `admin_users.import_service`
- 其他所有未来需要计数限制的业务模块

下游依赖：
- MySQL repository
- Redis quota cache
- `auth` 身份上下文

状态落点：
- MySQL：配额配置、用户配额消耗
- Redis：配置缓存、覆盖缓存

为什么属于公共能力：
- 它已经服务多个不同业务面，不只是问答
- 典型横切规则应该集中在公共后端

边界与耦合：
- 模块边界很清晰
- 但配额类型字符串已经被多业务直接引用，后续拆分服务时需要统一枚举源

## 2.4 `conversation`

分类：
- 公共能力

主要文件：
- `backend/app/modules/conversation/api.py`
- `backend/app/modules/conversation/service.py`
- `backend/app/modules/conversation/repository.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/cache.py`
- `backend/app/modules/conversation/outbox.py`
- `backend/app/modules/conversation/outbox_worker.py`
- `backend/app/modules/conversation/upload_processing_worker.py`
- `backend/app/modules/conversation/schemas.py`

对外接口：
- `POST /api/v1/conversations`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`
- `PUT /api/v1/conversations/{conversation_id}/title`
- `POST /api/v1/conversations/{conversation_id}/messages`
- `DELETE /api/v1/conversations/{conversation_id}`
- `GET /api/v1/conversations/{conversation_id}/files`
- `GET /api/v1/conversations/{conversation_id}/files/{file_id}`
- `GET /api/v1/conversations/{conversation_id}/files/{file_id}/download`
- `DELETE /api/v1/conversations/{conversation_id}/files/{file_id}`

模块职责：
- 会话元数据管理
- 消息持久化
- 会话文件清单管理
- 上传文件状态跟踪
- 删除文件/删除会话时的资源清理
- 会话列表与详情缓存
- 会话 JSON 文档异步同步
- 为问答入口层提供消息持久化钩子

核心内部关系：
- `repository.py` 维护会话元数据、会话文件元数据这类结构化信息
- `json_store.py` 负责会话 JSON 文档的本地文件与对象存储同步
- `service.py` 是真正的聚合层，把 MySQL 元数据、JSON 消息文档、Redis 缓存、对象存储拼起来
- `outbox.py` 和 `outbox_worker.py` 负责 JSON 文档异步落远端
- `upload_processing_worker.py` 负责上传后解析任务
- `cache.py` 负责列表/详情缓存键、命中、失效和最近访问页

关键代码事实：
- `persist_user_request()` 与 `persist_assistant_summary()` 已被 `ask_gateway_service.register_defaults()` 注册为默认持久化钩子
- `get_latest_turn_context()` 已具备“读取最近一轮问答上下文”的能力
- `add_uploaded_file()` 把上传文件正式挂到会话

上游调用方：
- 前端聊天页
- `uploads`：上传成功后追加会话文件记录
- `ask_gateway`：用户提问和 assistant 摘要回写
- `runtime`：启动上传处理 worker、outbox worker

下游依赖：
- `core/db.py`
- `storage_service`
- Redis conversation cache
- 对象存储中的 conversation JSON 副本

状态落点：
- MySQL：会话、文件元数据、任务型记录
- Redis：列表缓存、详情缓存、最近访问页、调试信息
- 本地文件：会话 JSON 文档
- 对象存储：会话 JSON 远端镜像

为什么属于公共能力：
- 聊天记录与会话文件本身是平台主数据
- 后续不同问答模式都应共用同一会话真相源

边界与耦合：
- 这是当前系统最状态化的公共模块
- 它与 `uploads`、`storage`、`ask_gateway`、`qa_pdf` 之间存在明显耦合
- 其中 `upload_processing_worker` 把平台层和 PDF/解析层绑在一起，是后续拆服务时最值得单独切开的点

## 2.5 `uploads`

分类：
- 公共能力

主要文件：
- `backend/app/modules/uploads/api.py`

对外接口：
- `POST /api/v1/upload_pdf`
- `POST /api/v1/upload_excel`
- `POST /api/v1/clear_pdf`

模块职责：
- 接收 PDF / Excel / CSV 上传
- 做可选配额检查
- 保存本地文件
- 镜像到对象存储
- 绑定到会话文件记录
- 提交后台上传处理任务
- 维护 `runtime.current_pdf_path`

核心内部关系：
- 上传入口其实全部写在 `api.py`
- `_optional_quota_response()` 调用 `quota_service`
- `_save_upload_file()` 先本地保存，再调用 `storage_service.mirror_file()`
- `_persist_uploaded_file()` 调用 `conversation_service.add_uploaded_file()`，然后把任务提交给 `upload_processing_worker`
- `clear_pdf` 只清理 runtime 中的当前 PDF 指针

上游调用方：
- 前端聊天页上传面板

下游依赖：
- `quota.service`
- `conversation.service`
- `storage.service`
- `runtime.upload_processing_worker`

状态落点：
- 本地文件：`uploads/`
- 对象存储：上传镜像
- MySQL/会话元数据：通过 `conversation`
- 进程内：`runtime.current_pdf_path`

为什么属于公共能力：
- 文件上传是平台入口，不应由单个 QA 模式独占

边界与耦合：
- 上传入口本身属于公共能力
- 但 `clear_pdf` 明显是单进程 UI 辅助状态，不适合作为真正的平台文件能力
- 当前上传后处理逻辑与会话、PDF 解析链已经耦合

## 2.6 `documents`

分类：
- 公共能力

主要文件：
- `backend/app/modules/documents/api.py`
- `backend/app/modules/documents/service.py`
- `backend/app/modules/documents/reference_preview.py`
- `backend/app/modules/documents/translation_service.py`
- `backend/app/modules/documents/translator.py`
- `backend/app/modules/documents/cache.py`
- `backend/app/modules/documents/translation_cache_impl.py`
- `backend/app/modules/documents/schemas.py`

对外接口：
- `GET|HEAD /api/v1/view_pdf/{doi}`
- `POST /api/v1/summarize_pdf/{doi}`
- `GET /api/v1/extract_pdf_text/{doi}`
- `POST /api/v1/translate`
- `GET /api/v1/check_pdf/{doi}`
- `GET /api/v1/literature_content`
- `GET|POST /api/v1/reference_preview`

模块职责：
- DOI PDF 查看
- PDF 是否存在检查
- PDF 文本提取
- PDF 总结
- 文本翻译
- 文献详情读取
- 批量引用预览

核心内部关系：
- `service.py` 是聚合入口
- `reference_preview.py` 负责 DOI 清洗、元数据查询、引用预览项组装
- `translation_service.py` 与 `translation_cache_impl.py` 负责翻译调用与翻译缓存
- `api.py` 中 `view_pdf`、`summarize_pdf`、`translate` 都接入了配额依赖

关键代码事实：
- `view_pdf` 与 `check_pdf` 更偏“文档资产服务”
- `reference_preview` 会同时尝试走图谱/向量侧元数据来源
- `documents` 会复用底层 PDF 提取能力，不是纯静态文件服务

上游调用方：
- 前端 PDF 阅读器
- 引用预览面板
- 问答结果中的文献跳转与预览

下游依赖：
- `storage_service`
- 论文本地目录 `papers/`
- 运行时 agent
- `qa_pdf` 暴露的 PDF 提取能力
- 配额模块

状态落点：
- 本地文件：`papers/`
- 对象存储：PDF 归档
- 翻译缓存：本地缓存文件，必要时同步对象存储
- 运行时 agent / 图谱 / 向量元数据

为什么属于公共能力：
- 它提供的是文档资产服务，而不是某一种问答执行器
- 不同模式都可能复用“看原文 / 查摘要 / 看引用 / 翻译”

边界与耦合：
- 文档服务边界整体清晰
- 但它并没有完全独立于问答运行时，`literature_content` 与 `reference_preview` 仍依赖 agent 侧能力
- `documents` 与 `qa_pdf` 存在能力复用关系

## 2.7 `storage`

分类：
- 公共能力

主要文件：
- `backend/app/modules/storage/service.py`
- `backend/app/modules/storage/paper_storage.py`
- `backend/app/integrations/storage/base.py`
- `backend/app/integrations/storage/local.py`
- `backend/app/integrations/storage/minio.py`
- `backend/app/integrations/storage/factory.py`

对外职责：
- 本地 / MinIO 存储抽象
- 文件镜像上传
- 远端对象下载到本地
- DOI PDF 归档路径规则
- 删除文件与清理资源

上游调用方：
- `uploads`
- `conversation`
- `documents`
- 运行时初始化

下游依赖：
- `integrations/storage/*`

状态落点：
- 本地文件系统
- MinIO 或其他对象存储

为什么属于公共能力：
- 这是平台文件底座
- 所有文件类业务最终都应该走统一对象存储协议

边界与耦合：
- 模块边界比较标准
- 但 `paper_storage.py` 带有论文资产语义，说明当前存储层还混入了一些文献领域约定

## 2.8 `system`

分类：
- 公共能力

主要文件：
- `backend/app/modules/system/api.py`
- `backend/app/modules/system/service.py`
- `backend/app/modules/system/schemas.py`

对外接口：
- `GET /health`
- `GET /api/v1/background_status`
- `GET /api/v1/cache_debug/conversation`
- `GET /api/v1/kb_info`
- `POST /api/v1/refresh_kb`
- `POST /api/v1/clear_cache`

模块职责：
- 健康检查
- 后台 worker 状态查看
- conversation cache 调试
- 知识库信息查看
- 刷新知识库
- 清理答案缓存

核心内部关系：
- `service.py` 从 `AppRuntime` 读取各组件状态
- 同时把 conversation cache、向量库信息、agent 初始化状态汇总出来

上游调用方：
- 首页控制面板
- 运维/调试

下游依赖：
- `AppRuntime`
- Redis cache
- `retrieval`
- `agent`
- `conversation.cache`

状态落点：
- 无独立主数据
- 主要读取 runtime、Redis、向量库、worker 状态

为什么属于公共能力：
- 这是平台运维与调试入口，不属于某一问答模式

边界与耦合：
- `health` 边界清晰
- 但 `kb_info / refresh_kb / clear_cache` 明显伸进了问答执行侧，说明 system 模块当前兼具平台运维和 QA 运维双重角色

## 3. 共享基础设施模块

## 3.1 `core/*`

分类：
- 共享基础设施

关键文件：
- `core/config.py`
- `core/db.py`
- `core/runtime.py`
- `core/deps.py`
- `core/errors.py`
- `core/sse.py`
- `core/logging.py`

职责拆解：
- `config.py`：全局环境变量与设置源
- `db.py`：MySQL 连接与上下文
- `runtime.py`：全进程依赖装配中心
- `deps.py`：runtime / settings / 常见依赖获取
- `errors.py`：统一异常模型与异常处理器
- `sse.py`：SSE 响应封装
- `logging.py`：日志初始化

对上游的意义：
- 所有公开模块和问答模块都依赖它

边界判断：
- 这层不是业务公共能力，而是系统运行基座

## 3.2 `integrations/*`

分类：
- 共享基础设施

包含：
- `integrations/redis`
- `integrations/storage`
- `integrations/embedding`
- `integrations/vector_db`
- `integrations/neo4j`
- `integrations/llm`

职责：
- 与外部能力做适配，不承载业务规则

典型调用链：
- `runtime` -> 构建 Redis / storage / retrieval / LLM
- `quota` / `conversation` -> Redis
- `documents` / `uploads` -> storage
- `retrieval` / `agent` / `generation_pipeline` -> embedding / vector DB / Neo4j / LLM

边界判断：
- 这是“技术接入层”，不是公共业务能力

## 3.3 `file_context`

分类：
- 共享基础设施

主要文件：
- `backend/app/modules/file_context/service.py`
- `backend/app/modules/file_context/models.py`
- `backend/app/modules/file_context/parser.py`

职责：
- 根据问题文本、当前会话文件、当前 PDF 状态，推断这一轮该怎么选文件
- 判断更像 `kb_only / file_only / mixed`
- 计算 `route_hint`
- 生成 `used_files`、`execution_files`
- 产出 `allow_kb_verification`

上游调用方：
- `ask_gateway`

下游依赖：
- `conversation_service.list_uploaded_files()`

状态落点：
- 纯运行时推断，无独立持久化

边界判断：
- 它不是直接对用户暴露的公共能力
- 更像问答入口层可复用的共享决策组件

## 3.4 `retrieval`

分类：
- 共享基础设施

主要文件：
- `backend/app/modules/retrieval/service.py`
- `backend/app/modules/retrieval/models.py`

职责：
- 构造 embedding client
- 构造 vector DB client
- 构造 Neo4j client
- 统一生成 `RetrievalBindings`

上游调用方：
- `core/runtime.py`
- `system`
- `generation_pipeline`
- `agent`

下游依赖：
- `integrations/embedding/client.py`
- `integrations/vector_db/client.py`
- `integrations/neo4j/client.py`

状态落点：
- 向量库目录
- Neo4j 连接
- embedding 模型或 embedding API

边界判断：
- 它是检索基础设施，不是终端公共业务能力

## 4. 问答入口编排层

## 4.1 `ask_dispatch`

分类：
- 问答入口编排

主要文件：
- `backend/app/modules/ask_dispatch/api.py`
- `backend/app/modules/ask_dispatch/service.py`
- `backend/app/modules/ask_dispatch/repository.py`
- `backend/app/modules/ask_dispatch/events.py`
- `backend/app/modules/ask_dispatch/schemas.py`
- `backend/app/modules/ask_dispatch/worker.py`

对外接口：
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/{task_id}/events`
- `POST /api/v1/tasks/{task_id}/cancel`

职责：
- 创建异步问答任务
- 从 Redis 读取任务摘要
- 读取任务事件流
- 请求取消任务
- 为 worker 提供 claim / ack / append_event 能力

核心内部关系：
- `service.py` 是统一任务门面
- `repository.py` 直接落 Redis 队列、任务记录、事件流
- `events.py` 维护任务事件格式和终态推断

上游调用方：
- 前端轮询 / 事件读取
- 潜在的多模式 worker

下游依赖：
- Redis

状态落点：
- Redis：任务摘要、事件、取消状态、队列

为什么不算公共能力：
- 它解决的是“问答任务如何排队和追踪”
- 不是平台通用业务，而是 QA 入口控制面

边界与耦合：
- 边界相对清晰
- 但它与未来多模式执行器的耦合点会很多，是问答编排层而不是平台层

## 4.2 `ask_gateway`

分类：
- 问答入口编排

主要文件：
- `backend/app/modules/ask_gateway/api.py`
- `backend/app/modules/ask_gateway/service.py`
- `backend/app/modules/ask_gateway/helpers.py`
- `backend/app/modules/ask_gateway/streaming.py`
- `backend/app/modules/ask_gateway/limits.py`
- `backend/app/modules/ask_gateway/schemas.py`

对外接口：
- `POST /api/v1/ask`
- `POST /api/v1/ask_stream`

职责：
- 统一问答 HTTP / SSE 入口
- 并发限制
- 请求补全与规范化
- 文件上下文注入
- 路由到 KB QA / PDF QA / Tabular QA
- 流式事件规整
- 问答日志记录
- 会话消息回写

核心内部关系：
- `api.py` 处理请求和 SSE 输出
- `service.py` 是真正的编排核心
- `helpers.py` 负责答案清洗、日志、PDF 流式上下文加载
- `streaming.py` 负责流式事件标准化
- `limits.py` 负责并发槽位控制

关键代码事实：
- `register_defaults()` 已把 `conversation_service.persist_user_request` 和 `persist_assistant_summary` 设为默认钩子
- `enrich_request()` 会结合 `file_context` 重写 `route_hint`、`turn_mode`、`execution_files`
- 运行时会按请求分流到：
  - `qa_kb_service.iter_answer_events()`
  - `pdf_qa_service.iter_route_answer_events()`
  - `qa_tabular_service.iter_answer_events()`

上游调用方：
- 前端聊天输入

下游依赖：
- `conversation`
- `file_context`
- `qa_kb`
- `qa_pdf`
- `qa_tabular`
- `runtime` 中的 agent / llm / pdf bindings / redis

状态落点：
- 直接持久化不多
- 主要通过 `conversation` 回写消息
- 进程内：并发计数、流式任务状态

为什么不算公共能力：
- 它是问答入口，不是平台公共服务
- 一旦未来把 `fast / deep / patent` 等模式拆成不同执行器，`ask_gateway` 就会是公共入口编排层，而不是平台公共后端本身

边界与耦合：
- 当前耦合很强
- 一头接平台层的 conversation / uploads 状态
- 一头接执行层的 kb/pdf/tabular 具体问答器
- 这是系统最典型的“中枢编排层”

## 5. 问答执行层

## 5.1 `qa_cache`

分类：
- 问答执行层

主要文件：
- `backend/app/modules/qa_cache/stage1_cache.py`
- `backend/app/modules/qa_cache/stage2_cache.py`
- `backend/app/modules/qa_cache/pdf_cache.py`
- `backend/app/modules/qa_cache/singleflight.py`
- `backend/app/modules/qa_cache/metrics.py`

职责：
- Stage1 规划结果缓存
- Stage2 检索结果缓存
- PDF 文本缓存
- 单飞锁，避免并发重复计算

上游调用方：
- `qa_kb`
- `generation_pipeline`
- `qa_pdf`

下游依赖：
- Redis

状态落点：
- Redis：缓存键、锁、统计

边界判断：
- 纯问答执行优化能力，不属于平台公共能力

## 5.2 `qa_kb`

分类：
- 问答执行层

主要文件：
- `backend/app/modules/qa_kb/service.py`
- `backend/app/modules/qa_kb/models.py`
- `backend/app/modules/qa_kb/orchestrators/generation.py`
- `backend/app/modules/qa_kb/stages/planning.py`
- `backend/app/modules/qa_kb/stages/retrieval.py`
- `backend/app/modules/qa_kb/stages/pdf_loading.py`
- `backend/app/modules/qa_kb/stages/synthesis.py`
- `backend/app/modules/qa_kb/semantic_legacy.py`
- `backend/app/modules/qa_kb/semantic_common.py`
- `backend/app/modules/qa_kb/streaming.py`

职责：
- 作为知识库问答统一入口
- 兼容新 generation pipeline 与旧 semantic 路径
- 输出流式事件

关键代码事实：
- `resolve_pipeline_mode()` 决定走哪条路径
- `iter_answer_events()` 是 `ask_gateway` 进入 KB QA 的主入口
- `GenerationPipelineOrchestrator` 已成为新主链之一

上游调用方：
- `ask_gateway`

下游依赖：
- `generation_pipeline`
- `qa_cache`
- 旧的 semantic/agent/service 层

状态落点：
- Redis cache
- 向量库 / 图谱 / LLM / papers

边界判断：
- 这是明确的问答执行器，不属于公共后端

## 5.3 `qa_pdf`

分类：
- 问答执行层

主要文件：
- `backend/app/modules/qa_pdf/service.py`
- `backend/app/modules/qa_pdf/engine.py`
- `backend/app/modules/qa_pdf/pdf_extractor.py`
- `backend/app/modules/qa_pdf/streaming.py`
- `backend/app/modules/qa_pdf/common.py`
- `backend/app/modules/qa_pdf/models.py`
- `backend/app/modules/qa_pdf/web_bindings.py`
- `backend/app/modules/qa_pdf/llm_factory.py`

职责：
- PDF 单文档问答
- PDF 文本抽取
- 流式 PDF 回答
- 对 web 层暴露 PDF 处理 bindings

关键代码事实：
- `build_web_bindings()` 被 `runtime` 和 `ask_gateway` 使用
- `documents` 也会复用 PDF 提取能力

上游调用方：
- `ask_gateway`
- `runtime`
- `documents`
- `conversation.upload_processing_worker`

下游依赖：
- PDF 解析库
- LLM

状态落点：
- 本地 PDF 文件
- Redis PDF cache

边界判断：
- 这是文档问答执行器，不属于公共平台能力

## 5.4 `qa_tabular`

分类：
- 问答执行层

主要文件：
- `backend/app/modules/qa_tabular/service.py`
- `backend/app/modules/qa_tabular/planner.py`
- `backend/app/modules/qa_tabular/executor.py`
- `backend/app/modules/qa_tabular/renderer.py`
- `backend/app/modules/qa_tabular/workbook_loader.py`
- `backend/app/modules/qa_tabular/schema_profiler.py`
- `backend/app/modules/qa_tabular/models.py`

职责：
- Excel/CSV 结构化问答
- 工作簿加载
- schema 画像
- 查询规划
- 执行与结果渲染

上游调用方：
- `ask_gateway`

下游依赖：
- 本地 Excel/CSV 文件
- 可选 LLM 渲染

状态落点：
- 本地工作簿
- 运行时内存表格数据

边界判断：
- 明确属于问答执行器

## 5.5 `generation_pipeline`

分类：
- 问答执行层

主要文件：
- `backend/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- `backend/app/modules/generation_pipeline/runtime_bootstrap.py`
- `backend/app/modules/generation_pipeline/dependencies.py`
- `backend/app/modules/generation_pipeline/stage1_planning.py`
- `backend/app/modules/generation_pipeline/stage2_retrieval.py`
- `backend/app/modules/generation_pipeline/pdf_pipeline.py`
- `backend/app/modules/generation_pipeline/query_expander.py`
- `backend/app/modules/generation_pipeline/retrieval_validation.py`
- `backend/app/modules/generation_pipeline/reference_alignment.py`
- 以及 `synthesis_* / evidence_* / doi_* / rerank_service.py` 等辅助文件

职责：
- 生成驱动的 RAG 主链
- Stage1 规划
- Stage2 检索与验证
- PDF 证据加载
- 引用对齐
- 最终综合回答生成

上游调用方：
- `qa_kb`
- `runtime` agent bootstrap

下游依赖：
- retrieval bindings
- 向量库
- Neo4j
- LLM
- papers 本地 PDF

状态落点：
- 运行时内存
- 缓存
- 本地 papers
- 向量库 / 图谱 / LLM

边界判断：
- 这是当前知识问答主引擎之一，绝不是公共平台能力

## 6. legacy / 兼容支撑层

## 6.1 `microscopic_runtime`

分类：
- legacy / 兼容支撑

主要文件：
- `backend/app/modules/microscopic_runtime/bootstrap.py`
- `backend/app/modules/microscopic_runtime/path_utils.py`
- `backend/app/modules/microscopic_runtime/translator.py`
- `backend/app/modules/microscopic_runtime/translation_cache.py`
- `backend/app/modules/microscopic_runtime/embedding_client.py`

职责：
- 旧显微知识问答运行时兼容包装
- 路径、翻译、embedding 兼容

边界判断：
- 不是公共平台能力，更像旧链路适配层

## 6.2 `microscopic_expert` / `microscopic_search`

分类：
- legacy / 兼容支撑

职责：
- 旧领域专家检索与问答路径

边界判断：
- 领域执行逻辑，不属于公共能力

## 6.3 `agents/*`

分类：
- legacy / 兼容支撑

主要文件：
- `backend/app/agents/material_science_agent.py`
- `backend/app/agents/hybrid_query_agent.py`
- `backend/app/agents/dual_retrieval_agent.py`
- `backend/app/agents/commander_agent.py`
- `backend/app/agents/neo4j_two_stage_optimizer.py`

职责：
- 老的 agent 组织层
- 负责材料科学领域查询、双路检索、图谱路径等

上游调用方：
- `runtime` 初始化
- `system`
- `documents`
- 旧回答链

边界判断：
- 这是知识问答执行宿主，不属于公共平台能力

## 6.4 `services/*`

分类：
- legacy / 兼容支撑

内容特征：
- 双路检索
- commander routing
- semantic answer orchestrator
- graph query engine
- pdf loader
- bootstrap helpers

边界判断：
- 这是历史上逐步长出来的一组问答支撑服务
- 更像执行层内部库，而不是平台能力层

## 7. 关键依赖链，按真实代码看

## 7.1 登录链

- `auth.api`
- `auth.service`
- `auth.repository`
- `core.db`

结论：
- 纯公共能力链，边界最清晰

## 7.2 上传链

- `uploads.api`
- `quota.service` 可选配额检查
- `storage_service.mirror_file()`
- `conversation_service.add_uploaded_file()`
- `runtime.upload_processing_worker.submit()`

后续后台链：

- `UploadProcessingWorker`
- `pdf_web_bindings.extract_pdf_text`
- `conversation_service` 更新文件处理状态

结论：
- 入口是公共能力
- 后处理已经伸进 PDF 能力层

## 7.3 会话持久化链

用户发问时：

- `ask_gateway.api`
- `ask_gateway_service.persist_user_request()`
- `conversation_service.persist_user_request()`

assistant 回答结束时：

- `ask_gateway_service.persist_assistant_summary()`
- `conversation_service.persist_assistant_summary()`

底层再进入：

- `conversation.repository`
- `conversation.json_store`
- `conversation.outbox`
- `conversation.cache`

结论：
- `conversation` 已经是问答入口层的事实主数据底座

## 7.4 文档服务链

- `documents.api`
- `documents.service`
- `storage`
- `papers/`
- `qa_pdf` 提取能力
- 运行时 agent 的元数据查询能力

结论：
- `documents` 是公共文档资产服务
- 但并未完全脱离问答 runtime

## 7.5 KB 问答链

- `ask_gateway`
- `file_context`
- `qa_kb`
- `generation_pipeline`
- `retrieval`
- `vector_db / neo4j / llm`

结论：
- 这是完整的问答入口到执行链
- 不应归入公共服务

## 8. 最终边界判断

### 8.1 可以明确抽成“公共服务”的部分

- `auth`
- `admin_users`
- `quota`
- `conversation`
- `uploads`
- `documents`
- `storage`
- `system` 中偏平台的部分

### 8.2 应视为公共服务的共享底座

- `core/*`
- `integrations/*`
- `retrieval` 中纯绑定构建部分

### 8.3 不应算公共服务本体的部分

- `ask_gateway`
- `ask_dispatch`
- `file_context`
- `qa_cache`
- `qa_kb`
- `qa_pdf`
- `qa_tabular`
- `generation_pipeline`
- `agents/*`
- `services/*`
- `microscopic_*`

## 9. 当前代码里最值得注意的边界问题

- `conversation` 已经不是简单 CRUD，而是“会话元数据 + JSON 文档 + Redis 缓存 + 对象存储 + outbox worker”的复合状态中心。
- `uploads` 入口是公共的，但上传后的解析任务已经深入问答执行层。
- `documents` 是公共文档能力，但 `reference_preview` 和 `literature_content` 仍依赖 runtime agent。
- `system` 混合了平台健康检查和 QA 运维接口。
- `ask_gateway` 通过默认持久化钩子把平台会话层和问答执行层强绑定在一起。
- `runtime` 同时装配公共平台依赖和 QA 引擎依赖，说明当前仓库从部署形态上还是“单宿主混合后端”。

## 10. 对“公共能力”最稳妥的定义

如果后续要从这套代码里拆 `public-service`，最稳妥的公共能力边界应当是：

- 用户与权限：`auth`、`admin_users`
- 平台规则：`quota`
- 会话与消息主数据：`conversation`
- 文件上传与对象存储：`uploads`、`storage`
- 文档资产服务：`documents`
- 运行与健康：`system` 的平台部分

而下面这些更适合作为“挂在公共服务后的问答子系统”：

- `ask_gateway`
- `ask_dispatch`
- `file_context`
- `qa_*`
- `generation_pipeline`
- `agents/services/microscopic_*`

