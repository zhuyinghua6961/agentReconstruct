# 公共后端拆分任务清单

基于仓库 `/home/cqy/worktrees/fastapi-version` 继续读源码后的详细实施清单。

目标不是泛泛地说“把公共能力拆出去”，而是把当前代码里真正要动的边界、顺序、阻塞项、模块任务、网关适配任务和前端适配任务拆成可执行事项。

这份清单服务于一个明确目标：
- 把当前仓库里的公共能力抽成一个单独的公共后端。
- 不是拆成 8 个微服务。
- 不是只写概念设计。
- 是为后续真正动工提供任务分解和顺序。

协议对齐补充文档：
- `/home/cqy/worktrees/public-service/gateway-public-backend-protocol-alignment.md`

这份补充文档额外明确了两件事：
- `gateway` 的公共后端协议已经存在，不再需要从零定义。
- 当前真正阻塞实施的重点，是 `gateway` public proxy 覆盖不全、canonical path 与当前后端 path 不完全兼容，以及部分公共能力实现仍缠着单体 runtime。

## 1. 这次继续读代码后确认的关键事实

### 1.1 当前 FastAPI 公开入口并不是纯网关

代码依据：
- `backend/app/main.py`

当前同一个 FastAPI 进程里同时挂了：
- `system`
- `auth`
- `admin_users`
- `quota`
- `conversation`
- `documents`
- `uploads`
- `ask_dispatch`
- `ask_gateway`

这说明当前系统不是“网关调用公共服务”，而是“公共能力和问答入口混在一个进程里”。

### 1.2 当前 runtime 把公共能力装配线和问答装配线绑在一起

代码依据：
- `backend/app/core/runtime.py`

`create_runtime()` / `bootstrap_runtime_dependencies()` 当前会一起装配：
- MySQL
- Redis
- storage backend
- upload processing worker
- conversation outbox worker
- retrieval bindings
- agent
- LLM client

这意味着：
- 现在不是只有 API 路由混在一起。
- 连启动时的 runtime 依赖都还没有拆层。

### 1.3 `ask_gateway` 直接依赖公共模块，不是通过稳定接口调用

代码依据：
- `backend/app/modules/ask_gateway/api.py`
- `backend/app/modules/ask_gateway/service.py`

已确认的直接耦合：
- `ask_gateway/api.py` 直接使用 `require_auth_context`
- `ask_gateway/api.py` 直接使用 `require_quota("ask_query")`
- `ask_gateway/service.py` 直接 import `conversation_service`
- 默认持久化钩子直接注册为：
  - `conversation_service.persist_user_request`
  - `conversation_service.persist_assistant_summary`

这意味着后续只要把 `conversation/auth/quota` 从当前进程移走，`ask_gateway` 就会立刻失效，必须先补调用适配层。

### 1.4 `file_context` 不是纯算法模块，它依赖会话文件事实

代码依据：
- `backend/app/modules/file_context/service.py`
- `backend/app/modules/file_context/parser.py`

它依赖：
- conversation 文件列表
- `current_pdf_path`
- 最近轮次文件上下文

也就是说：
- 网关端并不只是“发问题”。
- 它会在问答前读取公共后端里的会话文件主数据。

### 1.5 `generation_pipeline` 仍然直接走 legacy `paper_storage`

代码依据：
- `backend/app/modules/generation_pipeline/context_loading.py`
- `backend/app/modules/generation_pipeline/pdf_pipeline.py`
- `backend/app/modules/storage/paper_storage.py`

而 `documents/reference_preview` 已经走的是：
- `backend/app/modules/storage/service.py`

这说明：
- storage 虽然已经在做统一抽象，但论文 PDF 访问还没有彻底收口。
- 如果后续只迁 `storage_service` 而不处理 legacy helper，网关执行层还会保留旧直连路径。

### 1.6 前端虽然已有 `VITE_API_BASE_URL`，但调用面并没有真正统一

代码依据：
- `frontend-vue/src/api/http.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/services/auth.js`
- `frontend-vue/src/services/admin.js`
- `frontend-vue/src/api/chat.js`
- `frontend-vue/src/api/conversation.js`
- `frontend-vue/src/api/literature.js`

已确认现状：
- 新 `src/api/*` 调用面支持 `VITE_API_BASE_URL`
- 老 `src/services/*` 仍大量写死相对路径
- token 读取仍同时存在：
  - `agentcode.auth.token.v1`
  - `token`

这意味着：
- 就算后端先拆出来，前端也不能只改一个环境变量就完成切换。
- 前端必须同步做“公共后端 base / 网关 base / token 存储规范”收口。

### 1.7 当前数据库迁移基线并不完整

代码依据：
- `backend/database/migrations/*`
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/conversation/repository.py`
- `backend/app/modules/conversation/outbox.py`

已确认现状：
- `backend/database/migrations` 里当前只看到了 quota 相关 SQL
- `auth` 和 `conversation` 依赖的核心表/列主要靠 repository 运行时兼容判断：
  - `users`
  - `password_history`
  - `user_security_questions`
  - `conversations`
  - `conversation_messages`
  - `conversation_files`
  - `conversation_json_outbox`
  - `users.is_first_login`
  - `users.must_set_security_questions`
  - `users.password_updated_at`
  - `users.failed_login_attempts`
  - `users.locked_until`
  - `conversations.chat_json_*`

这意味着：
- 抽公共后端前，必须先把“规范 schema 基线”补出来。
- 否则迁移会继续依赖“线上库长什么样就兼容什么样”的现状。

### 1.8 `ask_dispatch` 实际上已经比 `ask_gateway` 更独立，优先级应更低

代码依据：
- `backend/app/modules/ask_dispatch/api.py`
- `backend/app/modules/ask_dispatch/service.py`
- `backend/app/modules/ask_dispatch/repository.py`
- `backend/app/modules/ask_dispatch/worker.py`

已确认现状：
- `ask_dispatch` 主要依赖：
  - `require_auth_context`
  - Redis stream / key-value
- 它没有像 `ask_gateway` 一样直接 import `conversation_service`、`quota_service`、`storage_service`

这意味着：
- `ask_dispatch` 虽然仍应留在网关/问答后端。
- 但它不是当前拆公共后端的最高风险点。
- 真正的优先级仍然是 `ask_gateway` 与 `conversation/auth/quota` 的解耦。

### 1.9 `documents` 里有一部分能力不是“纯公共只读”，而是直接消耗外部 LLM 配置

代码依据：
- `backend/app/modules/documents/service.py`

已确认现状：
- `summarize_pdf()` 直接读取：
  - `OPENAI_API_KEY`
  - `OPENAI_BASE_URL`
- 并直接用 `OpenAI()` client 发请求
- `translate()` 也不是纯静态能力，而是走 `documents_translation_service`

这意味着：
- 如果把这部分放到公共后端，公共后端本身就要持有独立的 LLM/provider 凭据和调用配额。
- 这不是不能做，但必须在任务清单里显式列为配置与部署成本，而不能当成普通文档 CRUD。

### 1.10 agent 侧 PDF 加载除了 `generation_pipeline` 之外，还存在 `services/pdf_loader.py` shim 链

代码依据：
- `backend/app/services/pdf_loader.py`
- `backend/app/services/storage/paper_storage.py`

已确认现状：
- agent 侧 PDF 内容补全还会经过：
  - `app.services.pdf_loader`
  - `app.services.storage.paper_storage`
  - 最终再落到 `modules/storage/paper_storage.py`

这意味着：
- storage legacy 清理不能只扫 `generation_pipeline/*`。
- `services/*` 下的兼容 shim 也必须纳入替换范围。

### 1.11 测试边界也会跟着拆分，不只是业务代码要迁

代码依据：
- `backend/tests/test_system.py`
- `backend/tests/test_documents.py`
- `backend/tests/test_uploads.py`

已确认现状：
- 现有测试里有不少断言默认建立在“公共能力与问答 runtime 同进程”的前提上，例如：
  - `create_runtime()` 会初始化 upload worker
  - `system` 仍可读取 `current_answer_context`
  - `uploads.clear_pdf()` 直接清 `runtime.current_pdf_path`

这意味着：
- 后续不是简单搬测试文件。
- 需要把测试也拆成：
  - 公共后端测试
  - 网关后端测试
  - 两端协作集成测试

## 2. 目标拆分边界

## 2.1 目标中的公共后端

计划归入公共后端的能力：
- `auth`
- `admin_users`
- `quota`
- `conversation`
- `uploads`
- `documents`
- `storage`
- `system` 中真正的平台公共部分

## 2.2 仍留在网关/问答后端的部分

计划保留在问答后端的能力：
- `ask_gateway`
- `ask_dispatch`
- `file_context`
- `retrieval`
- `qa_kb`
- `qa_pdf`
- `qa_tabular`
- `generation_pipeline`
- `agents/*`
- `services/*`
- `microscopic_*`

## 2.3 边界上必须做二次判定的部分

这几块虽然今天放在公共模块里，但拆分时必须先定归属：

- `documents.literature_content`
  现状依赖 `runtime.agent`
- `documents.reference_preview`
  现状依赖 graph / Chroma 元数据与 papers 存储
- `system.kb_info`
  现状依赖 `runtime.agent` 和 retrieval
- `system.refresh_kb`
  现状直接调用 `runtime.init_agent()`
- `system.clear_cache`
  现状清的是进程内 `runtime.answer_cache`
- `uploads.clear_pdf`
  现状清的是网关单进程 `runtime.current_pdf_path`

这几项不能机械搬运，必须先做归属判断：
- 要么留在网关后端
- 要么改造成公共后端与网关之间的协作接口
- 要么拆成两个端各自一半

## 3. 真正开工前的最小门槛

只有下面这些阻塞项完成，才算真正具备“开始拆公共后端”的条件。

### T0-01 路由归属表冻结

要做什么：
- 把现有所有公开路由分成：
  - 公共后端保留
  - 网关后端保留
  - 待裁剪后再决定

必须覆盖：
- `main.py` 当前全部 router
- 各模块 legacy `/api/...` 和 `/api/v1/...` 双路径

输出物：
- 一份路由归属清单
- 每条路由的 owner
- 前端调用方
- 网关内部调用方

完成标准：
- 没有“到时候再看”的灰区路由
- `documents/system/uploads` 中的边界路由已明确归属

### T0-02 数据归属表冻结

要做什么：
- 按表、列、对象存储前缀、Redis key 前缀、运行时状态，定义谁属于公共后端，谁属于网关后端。

至少要覆盖：
- MySQL 表
  - `users`
  - `password_history`
  - `user_security_questions`
  - `quota_configs`
  - `user_quota_usage`
  - `user_quota_overrides`
  - `conversations`
  - `conversation_messages`
  - `conversation_files`
  - `conversation_json_outbox`
- 本地目录
  - `uploads/`
  - `papers/`
  - `data/conversations/`
- 对象存储前缀
  - `uploads/pdf/*`
  - `uploads/excel/*`
  - `papers/*`
  - `conversations/*`
- Redis key
  - quota cache
  - conversation list/detail/recent pages
  - qa cache
- 进程内状态
  - `runtime.current_pdf_path`
  - `runtime.answer_cache`

完成标准：
- 每个状态落点都有 owner
- 没有公共后端和网关后端共同写同一份状态但没有协议的地方

### T0-03 对齐既有 gateway 协议并冻结采用范围

要做什么：
- 不是从零设计协议，而是以 `/home/cqy/worktrees/gateway` 中已经存在的 gateway 协议文档和当前实现为权威输入，明确本次公共后端拆分采用哪些部分、哪些地方还需要补实现。

必须定清楚的协作面：
- token 校验 / 当前用户状态校验
- `ask_query` quota 预检查与 finalize
- 会话创建与消息持久化
- 会话文件列表读取
- 最近一轮上下文读取
- 上传文件挂接
- 文档预览与 PDF 资产读取

现有协议依据：
- `/home/cqy/worktrees/gateway/docs/gateway_forwarding_protocol.md`
- `/home/cqy/worktrees/gateway/docs/gateway_canonical_protocol_revision.md`
- `/home/cqy/worktrees/gateway/app/routers/qa.py`
- `/home/cqy/worktrees/gateway/app/providers/conversation_files/public_http.py`

建议输出：
- 一份“gateway 协议采用清单”
- 一份“当前 fastapi-version 与 gateway 协议差距清单”

完成标准：
- 不再把“协议尚未定义”当成阻塞理由
- 能说明每个原本的本地函数调用，拆后如何映射到既有 gateway 协议
- `ask_gateway` 不再依赖“直接 import 公共模块 service”被视为实施目标，而不是协议定义目标

### T0-04 规范 schema 基线冻结

要做什么：
- 为公共后端补齐规范 migration 基线，不再依赖 repository 的运行时探测来猜线上 schema。

至少要产出：
- `users` 规范字段清单
- `password_history` 规范建表
- `user_security_questions` 规范建表
- `conversations` 规范字段清单
- `conversation_messages` 规范建表
- `conversation_files` 规范建表
- `conversation_json_outbox` 规范建表
- quota 三张表的规范版定义

完成标准：
- 能从空库初始化公共后端需要的核心 schema
- 不再需要“先有一个历史库才能跑起来”

### T0-05 前端双后端接入方案冻结

要做什么：
- 明确前端如何同时访问：
  - 公共后端
  - 网关后端

至少要定：
- 公共 API base env 名称
- 网关 API base env 名称
- SSE `ask_stream` 走哪边
- 上传、会话、文档、管理后台走哪边
- token 存储唯一 key

完成标准：
- 不能继续依赖“所有 API 共享一个 base URL”
- 不能继续保留两套 token key 不收口

### T0-06 当前已确认问题的修复策略冻结

要做什么：
- 把 `99-known-issues-and-risks.md` 中的已确认问题分成：
  - 拆分前必须先修
  - 拆分过程中顺手修
  - 拆分后可继续优化

最少要先定下来的必须先修项：
- `system` 未鉴权运维动作
- `uploads` 失败响应但文件已落盘/可能已扣额
- `admin_users` 批量导入副作用缺失
- `admin_users` 导入结果字段不一致
- `documents.reference_preview` 字段不一致
- `auth` 安全问题静默成功

完成标准：
- 每个已确认问题都有处理时机
- 不再把现存 bug 当成“迁移后再看”

### T0-07 测试归属表冻结

要做什么：
- 把现有测试按“公共后端 / 网关后端 / 两端协作”重新分组。

至少要覆盖：
- `test_system.py`
- `test_documents.py`
- `test_uploads.py`
- `test_auth.py`
- `test_admin_users.py`
- `test_quota.py`
- `test_conversation.py`
- `test_conversation_outbox_worker.py`
- `test_real_http_e2e_optional.py`
- `test_real_dependencies_optional.py`

完成标准：
- 没有继续依赖“单一 runtime 同时承载公共与问答能力”的测试前提
- 后续迁移时知道哪些测试应先迁、哪些测试要重写

### T0-08 gateway 协议实现覆盖审计

要做什么：
- 逐项审计 `/home/cqy/worktrees/gateway` 当前实现到底覆盖了多少协议要求。

已确认事实：
- `qa.py` 已经实现：
  - mode 路由
  - gateway-owned route decision
  - 规范化 ask payload
  - SSE passthrough
- `public_proxy.py` 目前只代理了公共协议中的一部分路由
- `gateway_forwarding_protocol.md` 文档自己也明确写了：
  - 多个协议要求路由仍是 `required next`
  - `/api/v1/...` 兼容仍未完成

必须审计：
- 现有 public proxy 覆盖率
- `/api/...` 与 `/api/v1/...` 兼容覆盖率
- query token 兼容覆盖率
- upstream `401/403/timeout` 归一化覆盖率

完成标准：
- 能把“协议已存在”和“实现已覆盖”严格区分
- 后续实施时不会误以为 gateway 这一侧已经全部就绪

## 4. 第一阶段任务：架构冻结与公共后端骨架

### T1-01 建立独立公共后端应用骨架

要做什么：
- 新建独立公共后端工程或子应用入口
- 只挂载公共路由
- 去掉 `ask_gateway/ask_dispatch`

当前代码依据：
- `backend/app/main.py`

完成标准：
- 独立 app 可以启动
- 路由只包含公共模块
- 不依赖问答 agent 初始化才能起来

### T1-02 构建最小公共 runtime

要做什么：
- 从当前 `AppRuntime` 拆出公共 runtime

公共 runtime 初始只保留：
- `settings`
- `db`
- `storage_backend`
- `redis_client`
- `upload_processing_worker`
- `conversation_outbox_worker`
- 公共 health/component status

需要显式移出公共 runtime 的内容：
- `agent`
- `generation_runtime`
- `llm_client`
- `embedding_client`
- `vector_db_client`
- `neo4j_client`
- `answer_cache`
- `current_pdf_path`
- `init_agent`

当前代码依据：
- `backend/app/core/runtime.py`

完成标准：
- 公共后端启动不再初始化 retrieval 和 agent
- 公共后端 worker 启停仍可用

### T1-03 决定 worker 部署形态

要做什么：
- 为下面两个 worker 定部署方式：
  - `conversation_outbox_worker`
  - `upload_processing_worker`

需要二选一：
- 继续由公共后端进程内后台线程承载
- 拆成独立 worker 进程

当前代码依据：
- `runtime.py` 中 `_start_conversation_outbox_worker()`
- `runtime.py` 中 `_stop_upload_processing_worker()`

完成标准：
- worker 的宿主明确
- 部署方式明确
- health/status 采集路径明确

### T1-04 公共后端配置模型收口

要做什么：
- 从当前 `Settings` 中拆出公共后端真正需要的配置
- 把问答链专属配置与公共配置分开

当前代码依据：
- `backend/app/core/config.py`

建议拆成两类：
- 公共后端配置
  - MySQL
  - Redis
  - MinIO
  - CORS
  - SSE heartbeat
  - worker config
  - documents 的 OpenAI / translator provider 配置
- 网关后端配置
  - agent/bootstrap
  - retrieval
  - generation runtime
  - QA cache

完成标准：
- 公共后端 `.env` 不再包含问答执行专属必填项

## 5. 第二阶段任务：schema 与数据边界

### T2-01 `auth` 数据模型定稿

要做什么：
- 定义公共后端中 `users` 的规范字段

当前代码已经实际依赖的字段：
- `id`
- `username`
- `password_hash`
- `role`
- `status`
- `user_type`
- `is_first_login`
- `must_set_security_questions`
- `password_updated_at`
- `failed_login_attempts`
- `locked_until`
- `created_at`
- `updated_at`

当前代码依据：
- `backend/app/modules/auth/repository.py`

必须同时补齐：
- `password_history`
- `user_security_questions`

完成标准：
- 能清楚区分“必选字段”和“历史兼容字段”
- 新公共后端不再继续靠 `has_column()` 驱动核心语义

### T2-02 `quota` 数据模型定稿

要做什么：
- 固化 quota 三张表的公共后端规范 schema

当前已存在迁移：
- `quota_configs`
- `user_quota_usage`
- `user_quota_overrides`

还需要明确：
- 多窗口字段是否保留当前设计
- `excel_upload` / `ask_query` / `file_upload` / `file_view` / `pdf_summary` / `text_translate` 的配置初始化方式

完成标准：
- 从空库启动后，基础 quota config 可初始化
- 不依赖人工补 SQL

### T2-03 `conversation` 数据模型定稿

要做什么：
- 定义 `conversations` 规范列
- 定义 `conversation_messages`
- 定义 `conversation_files`
- 定义 `conversation_json_outbox`

当前 `conversations` 实际依赖列：
- `id`
- `user_id`
- `title`
- `message_count`
- `created_at`
- `updated_at`
- `chat_json_local_path`
- `chat_json_storage_ref`
- `chat_json_hash`
- `chat_json_size_bytes`
- `chat_json_version`
- `chat_json_updated_at`
- `chat_json_sync_status`

当前代码依据：
- `backend/app/modules/conversation/repository.py`
- `backend/app/modules/conversation/outbox.py`

完成标准：
- 迁移基线可直接建立完整 conversation 主存储
- 不再依赖“线上表正好带这些列”

### T2-04 本地文件与对象存储路径规范定稿

要做什么：
- 固化以下路径规范，不再把路径规则散落在各 service 内：

必须覆盖：
- `papers/<doi_filename>.pdf`
- `uploads/pdf/<timestamp>_<filename>`
- `uploads/excel/<timestamp>_<filename>`
- `conversations/<user_id>/<conversation_id>.json`
- 本地 `uploads/`
- 本地 `papers/`
- 本地 `data/conversations/`

当前代码依据：
- `backend/app/modules/storage/service.py`
- `backend/app/modules/storage/paper_storage.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/uploads/api.py`

完成标准：
- 对象 key 和本地路径规范可文档化
- 新老路径兼容策略明确

## 6. 第三阶段任务：8 个公共模块的拆分任务

## 6.1 `auth`

### T3-auth-01 抽出独立 auth API、service、repository

要做什么：
- 将当前 `auth` 模块完整迁入公共后端
- 保留现有登录、注册、改密、忘记密码、安全问题接口

完成标准：
- 公共后端可独立提供 auth 全接口

### T3-auth-02 修复安全问题静默成功

要做什么：
- 把 `user_security_questions` 缺失场景改成显式失败

当前代码依据：
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/repository.py`

完成标准：
- 不再出现“显示设置成功但实际没写入”

### T3-auth-03 设计网关侧 token 校验方案

要做什么：
- 网关不能再直接通过 DB 校验用户状态
- 需要决定：
  - 共用签名 secret + 用户状态回查接口
  - 或公共后端提供 introspection/me 校验接口

这是拆分阻塞项，不是后续优化。

完成标准：
- 网关可以不直连 `users` 表也完成鉴权

### T3-auth-04 前端 token 存储规范收口

要做什么：
- 统一 token/user 存储 key
- 清理 `token/user` 与 `agentcode.auth.*` 双轨问题

当前代码依据：
- `frontend-vue/src/api/http.js`
- `frontend-vue/src/services/auth.js`
- `frontend-vue/src/router/index.js`
- `frontend-vue/src/features/auth/composables/useAuthSession.js`

完成标准：
- 全站只保留一套 auth session key

## 6.2 `admin_users`

### T3-admin-01 抽出独立后台用户管理接口

要做什么：
- 将 `admin_users` 迁入公共后端
- 继续复用公共 `auth` 用户主数据

完成标准：
- 管理后台用户管理接口不再依赖问答后端

### T3-admin-02 收口单建用户、批量导入、重置密码三条路径

要做什么：
- 把三条路径的副作用统一成同一套状态机

必须统一的副作用：
- `is_first_login`
- `must_set_security_questions`
- `password_history`
- `failed_login_attempts / locked_until` 清理策略

完成标准：
- 不再依赖“某条路径刚好走 repository、某条路径刚好走 service”

### T3-admin-03 保留并明确管理员初始口令规则

要做什么：
- 在实现里明确保留业务口径：
  - 管理员新增用户
  - 管理员批量导入新用户
  发放的是初始登录口令，不套用用户后续自行改密规则。

这项不是要改成更强限制，而是要把规则写死并和副作用统一。

完成标准：
- 文档、实现、接口行为一致

### T3-admin-04 修复导入结果契约

要做什么：
- 对齐前端结果弹窗和后端明细字段

当前已确认偏差：
- 后端 `reason`
- 前端 `message`
- 前端还读取 `user_id`

完成标准：
- 导入结果页字段统一
- 失败记录下载字段统一

## 6.3 `quota`

### T3-quota-01 抽出独立 quota 管理与依赖接口

要做什么：
- 将 quota 配置、查询、重置、检查、计数迁入公共后端

完成标准：
- 公共后端成为 quota 唯一真相源

### T3-quota-02 为网关定义远程 quota 协议

要做什么：
- 当前网关 ask 接口直接用本地 `require_quota/finalize_quota`
- 拆分后要改成网关 -> 公共后端的 precheck/finalize 协议

至少要覆盖：
- `ask_query`
- SSE 成功计数时机
- 并发失败/中断时是否计数

完成标准：
- `ask_stream` 不需要本地 quota 表也能工作

### T3-quota-03 修复 uploads 特例路径

要做什么：
- 统一 `uploads` 的 quota 语义
- 统一豁免规则

完成标准：
- 上传与其他公共模块使用同一 quota 接入规范

## 6.4 `conversation`

### T3-conv-01 抽出 conversation 主存储与缓存子系统

要做什么：
- 迁出：
  - API
  - service
  - repository
  - json_store
  - cache
  - outbox
  - upload processing 状态机相关持久化

完成标准：
- 会话、消息、文件、JSON 主文档全部由公共后端持有

### T3-conv-02 修复删除会话语义

要做什么：
- 定义删除会话到底是：
  - 只删主索引
  - 还是完整资产回收

建议拆分为明确策略：
- 软删除/归档
- 完整清理

至少要处理：
- 远端 JSON
- `conversation_messages`
- `conversation_files`
- 文件资产
- outbox 残留

完成标准：
- 删除语义清晰且实现一致

### T3-conv-03 为网关定义 conversation 内部客户端

要做什么：
- 用内部 client/SDK 替代当前直接 import `conversation_service`

必须覆盖的调用：
- 创建会话
- 追加用户消息
- 追加 assistant 汇总
- 读取会话文件列表
- 获取最近一轮上下文

当前代码依据：
- `ask_gateway/service.py`
- `file_context/service.py`

完成标准：
- 网关侧不再直接链接 conversation Python 实现

### T3-conv-04 决定上传处理 worker 的归属

要做什么：
- 当前 `UploadProcessingWorker` 依赖：
  - conversation_service
  - PDF 文本提取 bindings

需要决定：
- 它归公共后端
- 还是拆为公共后端 worker

完成标准：
- 上传后处理链不依赖问答网关进程存活

## 6.5 `uploads`

### T3-upload-01 从控制器逻辑改造成稳定上传服务

要做什么：
- 把当前 `api.py` 里揉在一起的上传逻辑收口成清晰服务层

完成标准：
- 上传链条可被公共后端单独维护

### T3-upload-02 修复“先落盘/扣额，后报错”

要做什么：
- 调整顺序为：
  - 先校验 auth / conversation context / 文件类型
  - 再做可计数的 quota precheck
  - 再落盘和镜像
  - 再挂接 conversation

完成标准：
- 不再出现失败响应但文件已保存/已扣额

### T3-upload-03 清理 `runtime.current_pdf_path` 旧兼容路径

要做什么：
- 当前 `clear_pdf` 和 `ask_gateway._default_enrich_request()` 还依赖单进程 `current_pdf_path`

需要决定：
- 彻底移除
- 或只保留在网关后端作为过渡兼容

完成标准：
- 公共后端不再承担网关单实例 UI 状态

### T3-upload-04 统一错误 HTTP 语义

要做什么：
- 把业务错误从 `200 + error payload` 收口成明确状态码策略

完成标准：
- 前端不需要靠猜 payload 才知道是否失败

## 6.6 `documents`

### T3-doc-01 拆分 pure public docs 与 QA-coupled docs

要做什么：
- 对 `documents` 内部能力再分层：
  - 纯文档资产服务
  - 仍依赖问答 runtime 的能力

至少要逐项判断：
- `view_pdf`
- `check_pdf`
- `extract_pdf_text`
- `summarize_pdf`
- `translate`
- `literature_content`
- `reference_preview`

完成标准：
- 每条 documents 路由都有明确归属

### T3-doc-02 修复 `view_pdf` 鉴权语义

要做什么：
- 决定它到底是不是匿名可访问
- 不再保留“代码签名上 optional，实际运行时强制登录”的状态

完成标准：
- 接口定义与实际行为一致

### T3-doc-03 修复 `reference_preview` 前后端字段

要做什么：
- 对齐 POST body schema

当前偏差：
- 后端 `doi_list`
- 前端 `doi`

完成标准：
- POST 契约统一

### T3-doc-04 统一文档模块错误语义

要做什么：
- 收口 `200 + error payload` 与真正 HTTP 错误码混用问题

完成标准：
- 文档模块的错误风格可稳定文档化

### T3-doc-05 明确 documents 的 provider/secret 归属

要做什么：
- 决定 `summarize_pdf`、`translate` 这些能力放到公共后端后，provider 凭据和配额由谁管理。

必须明确：
- 公共后端是否直接持有 OpenAI-compatible provider 凭据
- 是否允许公共后端直接出网调用 provider
- 是否要改成经由统一 LLM 网关

完成标准：
- documents 相关配置不再只是“代码能跑”，而是部署时可解释

## 6.7 `storage`

### T3-storage-01 将 `storage_service` 定为唯一正式入口

要做什么：
- 明确公共后端只认 `storage_service`
- legacy `paper_storage` 只做过渡兼容

完成标准：
- 新代码不再新增 `paper_storage` 调用点

### T3-storage-02 清理网关执行层中的旧 helper 直连

要做什么：
- 替换：
  - `generation_pipeline/context_loading.py`
  - `generation_pipeline/pdf_pipeline.py`
  - `services/pdf_loader.py`
  - `services/storage/paper_storage.py`
  等处对 `modules/storage/paper_storage.py` 的直接依赖

需要决定：
- 网关继续通过共享 SDK 直连对象存储
- 还是改调公共后端文档/存储接口

完成标准：
- 论文 PDF 访问路径收口

### T3-storage-03 固化 `storage_ref` 契约

要做什么：
- 明确 `minio://...` 与 `local://...` 的正式语义
- 明确 local backend 只是引用包装，不是假对象存储

完成标准：
- 下载/清理/镜像都按同一契约实现

## 6.8 `system`

### T3-system-01 重新划分 system 能力边界

要做什么：
- 将 `system` 拆成：
  - 真正的平台健康与公共 worker 状态
  - QA runtime 运维动作

当前高耦合项：
- `kb_info`
- `refresh_kb`
- `clear_cache`

完成标准：
- 公共后端 system 不再承担问答 runtime 专属控制面

### T3-system-02 修复未鉴权运维接口

要做什么：
- 给 `kb_info / refresh_kb / clear_cache` 加明确权限策略
- 或把它们移出公共后端

完成标准：
- 未登录调用不再能执行敏感运维动作

### T3-system-03 收口 schema 与真实返回体

要做什么：
- 补 `response_model` 或重新整理返回 schema

完成标准：
- system 契约可直接对外文档化

## 7. 第四阶段任务：网关后端改造

### T4-01 替换 `ask_gateway` 的本地 auth 依赖

要做什么：
- 网关 ask 接口不再直接使用当前本地 `require_auth_context`

需要落地：
- 网关鉴权中间层
- 与公共后端的用户状态校验机制

完成标准：
- 网关无本地 `users` 表也能完成登录态校验

### T4-02 替换 `ask_gateway` 的本地 quota 依赖

要做什么：
- 把 `require_quota("ask_query") + finalize_quota()` 改成内部服务调用

完成标准：
- `ask_query` quota 由公共后端统一结算

### T4-03 替换会话持久化钩子

要做什么：
- 替换当前默认注册的：
  - `persist_user_request`
  - `persist_assistant_summary`

改造成：
- 公共后端 conversation client

完成标准：
- `ask_gateway.register_defaults()` 不再直接绑定本地 conversation service

### T4-04 替换文件上下文读取

要做什么：
- `file_context` 需要的 conversation 文件列表不再来自本地 service，而来自公共后端 client

完成标准：
- 问答前文件选择逻辑仍可运行

### T4-05 决定 `current_pdf_path` 过渡策略

要做什么：
- 网关如果还要保留老式“当前 PDF fallback”，需要把它显式标注为网关本地兼容态，而不是公共事实。

完成标准：
- 公共事实和网关本地兼容状态不再混淆

## 8. 第五阶段任务：前端改造

### T5-01 拆分前端公共 API base 与网关 API base

要做什么：
- 定义两个环境变量，而不是继续共用一个：
  - `PUBLIC_API_BASE_URL`
  - `GATEWAY_API_BASE_URL`

现状依据：
- `src/api/http.js` 只有一个 `VITE_API_BASE_URL`
- `src/services/*` 还有写死相对路径

完成标准：
- 前端可分别指向两个后端

### T5-02 统一 token 存储和读取

要做什么：
- 前端全站只保留一套 token/user key

完成标准：
- 下载 URL builder、路由守卫、页面初始化、service/api 层都读同一套 key

### T5-03 将公共能力调用全部切到公共后端

必须覆盖：
- 登录/注册/个人中心
- 管理后台用户
- quota 管理
- conversation
- uploads
- documents
- system 中保留的公共部分

完成标准：
- 这些功能都不再打到网关后端

### T5-04 将问答与任务接口留在网关后端

必须覆盖：
- `ask_stream`
- `tasks/*`

完成标准：
- 前端已形成“公共 API”与“问答 API”两条调用线

### T5-05 修复现存前后端契约偏差

至少包括：
- `admin_users` 导入结果字段
- `documents.reference_preview` body 字段
- 上传错误 HTTP 语义
- 文档/工具接口状态码语义

完成标准：
- 前端不再为历史契约 bug 做特殊兜底

## 9. 第六阶段任务：测试、迁移、上线

### T6-01 补齐公共后端契约测试

要做什么：
- 为公共后端独立补：
  - auth API contract tests
  - admin users contract tests
  - quota contract tests
  - conversation contract tests
  - uploads contract tests
  - documents contract tests
  - system contract tests

完成标准：
- 不依赖网关后端也能回归公共 API

### T6-02 补齐网关对公共后端 client 的集成测试

要做什么：
- 验证网关经由内部 client 调公共后端时的关键链路：
  - ask 前 quota precheck
  - ask 后 finalize
  - 用户消息持久化
  - assistant 汇总持久化
  - 文件上下文选择

完成标准：
- 网关-公共后端协作链可测试

### T6-02A 重写 runtime 前提已经变化的旧测试

要做什么：
- 重写那些默认“公共能力和问答 runtime 同进程”的测试。

至少包括：
- `system` 读取 `current_answer_context / answer_cache` 的旧断言
- `uploads.clear_pdf` 操作单 runtime 状态的旧断言
- `create_runtime()` 自动具备 agent/retrieval 能力的旧预期

完成标准：
- 测试不会继续把拆分前的运行时前提写死

### T6-03 制作数据迁移与回滚方案

要做什么：
- 说明如何迁：
  - MySQL schema
  - Redis key
  - 对象存储前缀
  - 本地 JSON / 上传目录

完成标准：
- 可回滚
- 可灰度

### T6-04 灰度切流方案

要做什么：
- 设计切流顺序：
  1. 先切 auth/admin/quota
  2. 再切 conversation/uploads/documents
  3. 最后切 ask_gateway 对公共后端的依赖

完成标准：
- 有明确灰度顺序
- 不是一次性全量替换

## 10. 可以并行做的任务组

可以并行的组：
- schema 基线整理 与 前端 API base 设计
- 公共后端骨架搭建 与 路由归属清单
- `auth/admin/quota` 抽离 与 `conversation/uploads` 设计
- documents/system 边界裁剪 与 storage legacy 调用点清理

不建议并行的组：
- 在网关协作协议未冻结前，直接改 `ask_gateway`
- 在 schema 基线未冻结前，直接迁 `conversation`
- 在前端 token key 未统一前，切 auth/public base

## 11. 建议的实际开工顺序

建议顺序如下：

1. 先完成 `T0-01` 到 `T0-06`
2. 再做 `T1-01` 到 `T1-04`
3. 先落 `auth/admin/quota`，因为这三块边界最清楚
4. 再落 `conversation/uploads/storage`，因为这三块共享文件和会话主数据
5. 然后处理 `documents/system` 的边界裁剪
6. 最后改 `ask_gateway/file_context/generation_pipeline` 的适配层
7. 前端切双 base URL 和统一 token key
8. 最后做灰度切流

## 12. 达到什么程度才算“可以正式开始大规模动工”

最低标准不是“任务都写出来了”，而是下面 6 项都完成：

- 路由归属表已经冻结
- 数据归属表已经冻结
- 网关与公共后端的协作协议已经冻结
- 公共后端 schema 基线已经冻结
- 前端双 base URL 和单 token key 方案已经冻结
- 当前已确认问题的处理时机已经冻结

满足这 6 项之后，才适合真正进入大规模拆分实现。

在这之前直接开拆，最容易踩的坑是：
- 拆到一半才发现 `ask_gateway` 还在本地吃 conversation service
- 拆到一半才发现网关根本没有新的 auth/quota 校验路径
- 拆到一半才发现前端只有一个 base URL，无法同时连两个后端
- 拆到一半才发现公共表没有规范 migration 基线
