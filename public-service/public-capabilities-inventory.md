# fastapi-version 公共能力清单

基于仓库 `/home/cqy/worktrees/fastapi-version` 的前后端实际代码整理。

整理目标：
- 识别当前仓库中已经成型、且可被不同问答模式复用的公共能力
- 区分公共能力、公共基础设施、问答入口层、问答执行层
- 为后续把当前仓库定位成 `public backend + QA gateway` 提供模块清单

## 判断标准

本清单将“公共能力”定义为同时满足以下特征的模块：
- 不绑定单一 QA 执行链
- 可被 `fast / deep / patent` 等多模式复用
- 主要承载用户、会话、文件、配额、文档、系统运行等平台职责
- 当前前端页面或公共后端入口已经直接依赖

不纳入“公共能力”的部分：
- `ask_gateway` 这类问答入口编排层
- `qa_kb / qa_pdf / qa_tabular` 这类问答执行器
- `generation_pipeline`、agent/orchestrator 等领域回答逻辑

## 一、明确属于公共能力的后端模块

### 1. `auth`

后端入口：
- `backend/app/modules/auth/api.py`
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/deps.py`

核心职责：
- 登录、注册、当前用户信息
- Bearer token 解析与认证上下文构建
- 修改密码、密码强度校验、密码历史约束
- 忘记密码流程
- 安全问题读写
- 账号状态检查，停用账号拦截

前端直接使用点：
- `frontend-vue/src/api/auth.js`
- `frontend-vue/src/services/auth.js`
- `frontend-vue/src/router/index.js`
- `frontend-vue/src/views/Login.vue`
- `frontend-vue/src/views/ForgotPassword.vue`
- `frontend-vue/src/views/UserProfile.vue`
- `frontend-vue/src/features/auth/composables/useAuthSession.js`

为什么属于公共能力：
- 所有后续模式、所有后台页面、所有会话与上传接口都依赖统一身份体系
- 它不包含任何 QA 领域逻辑
- 它已经是整站级别的统一入口能力

### 2. `admin_users`

后端入口：
- `backend/app/modules/admin_users/api.py`
- `backend/app/modules/admin_users/service.py`
- `backend/app/modules/admin_users/import_service.py`

核心职责：
- 管理员查看用户列表
- 创建用户
- 重置用户密码
- 启用/停用用户
- 切换用户类型
- 删除用户
- 批量导入用户与导入模板下载

前端直接使用点：
- `frontend-vue/src/services/admin.js`
- `frontend-vue/src/views/AdminDashboard.vue`
- `frontend-vue/src/components/BatchImportDialog.vue`
- `frontend-vue/src/components/ImportResultDialog.vue`

为什么属于公共能力：
- 这是平台后台能力，不属于任何问答模式
- 后续无论挂多少 QA 系统，都应共用一套账号管理

### 3. `quota`

后端入口：
- `backend/app/modules/quota/api.py`
- `backend/app/modules/quota/service.py`
- `backend/app/modules/quota/repository.py`
- `backend/app/modules/quota/cache.py`
- `backend/app/modules/quota/deps.py`

核心职责：
- 用户配额查询
- 管理员配额配置查看、创建、更新
- 用户配额重置
- 统一配额检查与计数递增
- 多周期窗口支持：`daily / weekly / monthly / custom_days / none`
- Redis 缓存配额配置与覆盖项

当前被复用的业务面：
- `ask_query`
- `file_upload`
- `file_view`
- `pdf_summary`
- `text_translate`

前端直接使用点：
- `frontend-vue/src/api/quota.js`
- `frontend-vue/src/services/quota.js`
- `frontend-vue/src/views/QuotaManagement.vue`
- `frontend-vue/src/views/UserProfile.vue`
- `frontend-vue/src/features/controls/composables/useQuotaAdmin.js`

为什么属于公共能力：
- 配额是横切规则，已经同时作用于问答、上传、查看 PDF、总结、翻译
- 它天然应位于公共后端，而不是分散到各个 QA 系统里

### 4. `conversation`

后端入口：
- `backend/app/modules/conversation/api.py`
- `backend/app/modules/conversation/service.py`
- `backend/app/modules/conversation/repository.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/cache.py`
- `backend/app/modules/conversation/outbox.py`
- `backend/app/modules/conversation/outbox_worker.py`
- `backend/app/modules/conversation/upload_processing_worker.py`

核心职责：
- 创建、列出、读取、删除会话
- 修改会话标题
- 消息持久化
- 已上传文件元数据持久化
- 会话详情缓存与列表缓存
- 文件删除后的资源清理
- 会话文件下载解析
- 上传文件处理状态更新
- 问答链路中的用户消息/assistant 汇总消息持久化

关键代码事实：
- `persist_user_request()` 和 `persist_assistant_summary()` 已被 `ask_gateway` 注册为默认持久化钩子
- `get_latest_turn_context()` 可为后续多模式链路提供最近一轮的上下文信息

前端直接使用点：
- `frontend-vue/src/api/conversation.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/stores/chatStore.js`
- `frontend-vue/src/features/chat/composables/useChatSession.js`
- `frontend-vue/src/features/controls/composables/useConversationFileActions.js`
- `frontend-vue/src/views/Home.vue`

为什么属于公共能力：
- 聊天记录持久化本身就是平台主数据
- 不同 QA 模式不应该各自维护独立会话真相源
- 上传文件与会话绑定也已经在这里统一落库

### 5. `uploads`

后端入口：
- `backend/app/modules/uploads/api.py`

核心职责：
- PDF 上传
- Excel/CSV 上传
- 上传时做可选配额检查
- 本地保存文件
- 镜像到对象存储
- 把文件挂接到会话文件记录
- 提交后台上传处理任务
- 清理当前 runtime 的 PDF 上下文

前端直接使用点：
- `frontend-vue/src/api/chat.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/stores/chatStore.js`
- `frontend-vue/src/features/controls/composables/useKnowledgeWorkspace.js`
- `frontend-vue/src/features/controls/composables/useConversationFileActions.js`
- `frontend-vue/src/views/Home.vue`

为什么属于公共能力：
- 文件上传本身是平台文件入口，不属于某一种 QA 回答器
- 后续任何模式只要要消费用户上传文件，都应共用这层

备注：
- `clear_pdf` 更偏当前单实例 UI 状态辅助，不应视作公共后端的权威文件服务能力

### 6. `documents`

后端入口：
- `backend/app/modules/documents/api.py`
- `backend/app/modules/documents/service.py`
- `backend/app/modules/documents/reference_preview.py`
- `backend/app/modules/documents/translation_service.py`
- `backend/app/modules/documents/translator.py`
- `backend/app/modules/documents/cache.py`
- `backend/app/modules/documents/translation_cache_impl.py`

核心职责：
- DOI PDF 查看
- PDF 存在性检查
- 提取 PDF 文本
- 全文总结
- 文本翻译
- 文献详情读取
- 批量引用预览

前端直接使用点：
- `frontend-vue/src/api/literature.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/components/PdfReader.vue`
- `frontend-vue/src/features/references/composables/useReferenceInspector.js`
- `frontend-vue/src/features/references/composables/useReferencePanelState.js`
- `frontend-vue/src/views/Home.vue`

为什么属于公共能力：
- 这些是文档与论文资产服务，不是回答执行器
- 引用预览、原文查看、翻译、总结都可能被不同模式的回答结果复用

### 7. `storage`

后端入口：
- `backend/app/modules/storage/service.py`
- `backend/app/integrations/storage/*`

核心职责：
- 本地/MinIO 存储抽象
- 上传文件镜像
- DOI PDF 归档对象名规则
- 对象下载到本地
- 下载 URL/代理下载解析
- 文件删除清理

被谁复用：
- `uploads`
- `conversation`
- `documents`

为什么属于公共能力：
- 这是标准的平台文件存储层
- 不应让不同 QA 系统分别维护自己的对象存储协议

### 8. `system`

后端入口：
- `backend/app/modules/system/api.py`
- `backend/app/modules/system/service.py`

核心职责：
- 健康检查
- 后台 worker 状态
- 会话缓存调试
- 知识库信息
- 刷新知识库
- 清理答案缓存

前端直接使用点：
- `frontend-vue/src/api/chat.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/features/controls/composables/useKnowledgeWorkspace.js`
- `frontend-vue/src/views/Home.vue`

为什么属于公共能力：
- 这是整站运行与运维面
- 即使未来存在多个 QA 模式，也仍需要一个统一系统面板与健康入口

## 二、明确属于公共基础设施的共享模块

这些模块不一定直接对外暴露页面级 API，但它们是公共能力的底座。

### 1. `core/runtime`

关键文件：
- `backend/app/core/runtime.py`

职责：
- 启动并挂载数据库、Redis、存储、检索、agent、后台 worker
- 暴露 runtime 状态给系统接口与问答入口

判断：
- 这是整个公共后端的进程级装配层

### 2. `core/deps` 与 `auth/deps`

关键文件：
- `backend/app/core/deps.py`
- `backend/app/modules/auth/deps.py`

职责：
- 统一 runtime 注入
- 统一 auth context 注入
- 管理员/登录校验依赖
- 支持 header/query token 两种解析方式

判断：
- 这是公共 API 的统一依赖层

### 3. `file_context`

关键文件：
- `backend/app/modules/file_context/service.py`

职责：
- 根据问题、会话文件、PDF 上下文解析 `used_files / execution_files / route_hint / turn_mode`

判断：
- 它不是平台 UI 功能，但它是多问答模式都可能复用的共享业务解析器
- 更适合被定义为“共享业务基础设施”，而不是某个 QA 模式私有逻辑

### 4. `retrieval`

关键文件：
- `backend/app/modules/retrieval/service.py`

职责：
- embedding 客户端、向量库、Neo4j 运行时绑定

判断：
- 它是知识检索底座
- 当前更偏共享基础设施，而不是最终面向用户的公共服务模块

### 5. `ask_dispatch`

关键文件：
- `backend/app/modules/ask_dispatch/api.py`
- `backend/app/modules/ask_dispatch/service.py`
- `backend/app/modules/ask_dispatch/repository.py`
- `backend/app/modules/ask_dispatch/worker.py`

职责：
- 任务入队
- 任务查询
- 事件读取
- 取消请求
- Redis task/event/cancel 基础抽象

判断：
- 这是“未来多模式问答网关”的公共任务平台基础设施
- 当前前端主问答链路尚未切到这里
- 适合标记为“已存在的公共任务底座”，但不应误判为已完成的 mode gateway

## 三、不属于公共能力的部分

这些部分应从公共能力清单中排除：

### 1. `ask_gateway`

原因：
- 它是问答入口与 SSE 编排层
- 虽然会调用公共能力，但它本身属于 QA ingress，而不是平台公共服务

关键文件：
- `backend/app/modules/ask_gateway/api.py`
- `backend/app/modules/ask_gateway/service.py`

### 2. `qa_kb / qa_pdf / qa_tabular`

原因：
- 它们是回答执行器
- 负责 KB、PDF、表格问答的具体领域逻辑

关键文件：
- `backend/app/modules/qa_kb/service.py`
- `backend/app/modules/qa_pdf/service.py`
- `backend/app/modules/qa_tabular/service.py`

### 3. `generation_pipeline` 与各类 agent/orchestrator`

原因：
- 它们是问答引擎内部实现
- 属于模式执行层，而不是平台公共能力

## 四、按“公共后端”视角的建议模块分层

如果把当前仓库收敛成 `public backend + QA gateway`，更合理的分层是：

### A. 平台公共服务层
- `auth`
- `admin_users`
- `quota`
- `conversation`
- `uploads`
- `documents`
- `storage`
- `system`

### B. 公共基础设施层
- `core/runtime`
- `core/deps`
- `auth/deps`
- `file_context`
- `retrieval`
- `ask_dispatch`

### C. 问答入口层
- `ask_gateway`

### D. 问答执行层
- `qa_kb`
- `qa_pdf`
- `qa_tabular`
- `generation_pipeline`
- agents/orchestrators

## 五、结论

基于当前代码现状，这个仓库里已经明显存在一套可独立识别的公共能力集合，至少包括：
- 认证与账号体系
- 管理后台
- 配额管理
- 会话与消息持久化
- 文件上传与会话文件管理
- 文档/PDF/引用/翻译/总结服务
- 存储抽象
- 系统运行与健康接口

因此，把当前仓库简单理解为“fast 问答服务”是不准确的。

更准确的理解应是：
- 当前仓库已经是一个公共后端
- `ask_gateway` 只是挂在这个公共后端上的问答入口
- 多模式改造时，优先升级问答入口，不应先拆散上述公共能力
