# 原始系统架构摘要

本文只总结两套原始系统，不包含后续 `gateway`、`public-service`、`fastQA`、`highThinkingQA` 等拆分结果。

## 1. `fastapi-version`

### 系统定位
`fastapi-version` 是一套完整可独立运行的前后端业务系统，问答只是其中的核心模块之一，不是单独的 QA 后端。

### 前端情况
- 技术栈：`Vue 3 + Vite + Pinia + Vue Router`
- 页面完整：`Login`、`ForgotPassword`、`Home`、`UserProfile`、`AdminDashboard`、`QuotaManagement`
- 主页面 `Home` 已经串起完整业务流程：
  - 会话列表与恢复
  - 流式问答
  - PDF / Excel 上传
  - 文件列表与状态轮询
  - PDF 阅读
  - 知识库状态展示

### 后端情况
- 技术栈：`FastAPI`
- 模块边界清晰，主要包括：
  - `auth`：登录、注册、改密、安全问题、找回密码
  - `conversation`：会话、消息、会话文件、下载
  - `uploads`：PDF / Excel 上传、对象存储镜像、会话关联
  - `documents`：PDF 预览、翻译、总结、参考文献预览
  - `quota`：用户/管理员配额
  - `admin_users`：后台用户管理与批量导入
  - `ask_gateway`：统一问答入口
  - `ask_dispatch`：任务查询、事件、取消
  - `system`：健康检查、缓存、知识库状态

### 数据与依赖
- 强依赖：`MySQL`、`MinIO`、`Redis`
- 运行目录完整：`uploads/`、`papers/`、`vector_database/`、`data/conversations/`
- 会话层不是单一存储，包含 `DB + JSON store + cache + outbox`

### 问答架构
- 对外入口是单一的 `/api/v1/ask`、`/api/v1/ask_stream`
- 系统内部再按场景分流，如 `kb_qa`、`pdf_qa`、`tabular_qa`、`hybrid_qa`
- 本质是“单体业务系统内部分流”，不是外部多后端模式路由

### 闭环验证
- 前端 `Home` 页通过 `services/api.js` 调会话、上传、文档和问答接口，说明主页面不是静态壳子，而是完整业务页面
- 后端 `main.py` 已同时挂载 `auth / conversation / uploads / documents / quota / admin_users / ask_gateway / ask_dispatch / system`
- 因此前后端是闭环对齐的：页面能力与 HTTP 模块基本一一对应

## 2. 原始 `highThinking`

### 系统定位
原始 `highThinking` 也是一套完整、可独立运行的前后端系统，不只是问答内核。只是它的系统重心更明显地放在“高质量问答链路”上。

### 前端情况
- 前端技术栈与 `fastapi-version` 基本一致：`Vue 3 + Vite + Pinia + Vue Router`
- 页面结构也基本完整：登录、找回密码、主问答页、个人中心、管理后台、配额管理
- 说明它并不是纯后端实验项目，而是当时就具备完整前端承载能力的系统

### 后端情况
- 技术栈：`FastAPI`
- HTTP 层模块也较完整，包含：
  - `auth`
  - `admin`
  - `conversation`
  - `documents`
  - `upload`
  - `quota`
  - `system`
  - `ingest`
  - `ask`
- 这说明原始 `highThinking` 当时已经具备完整业务能力，不只是“回答问题接口”
- 其中 `ask` 仍然是系统主轴，负责：
  - 请求解析与鉴权
  - SSE 流式输出
  - 会话消息持久化
  - 调用 `server/services/ask_service.py`

### 数据与依赖
- 强依赖：`MySQL`、`MinIO`
- 本地运行目录明显围绕问答知识库展开：`papers/`、`vectordb/`、`uploads/`、`prompts/`
- 会话层同样不是纯内存，包含数据库、JSON 会话存储和 outbox 相关实现

### 问答架构
- 核心链路在 `agent_core/graph.py`
- 主流程是典型的多阶段高质量 QA：
  - 直接回答
  - 查询分解
  - 子问题预回答
  - 向量检索
  - 综合生成
  - Checker / Reviser 引用验证与修订
- 对外虽然已经是 HTTP 服务，但系统实质上是“围绕高质量推理问答构建的专用后端”

### 与 `fastapi-version` 的关系
- 原始 `highThinking` 不是运行时通过 HTTP 去调用 `fastapi-version` 才变完整
- 更准确的理解是：
  - 它自己当时已经是一套完整系统
  - 但前端页面、HTTP 路由层、用户/会话/文件等业务壳子，与 `fastapi-version` 高度同构
  - 两者最核心的差异主要在问答内核，即 `agent_core + ask_service + graph` 这一套高质量推理链路

### 闭环验证
- 前端 `frontend-vue/src/router/index.js` 已声明完整页面：`Home / Login / ForgotPassword / UserProfile / AdminDashboard / QuotaManagement`
- 前端 `frontend-vue/src/services/api.js`、`auth.js`、`admin.js`、`quota.js` 已分别调用：
  - 会话接口
  - 问答接口
  - 上传接口
  - 文档接口
  - 认证接口
  - 管理接口
  - 配额接口
- 后端 `server_fastapi/routers` 下确实存在对应路由：
  - `ask.py`
  - `conversation.py`
  - `upload.py`
  - `documents.py`
  - `auth.py`
  - `admin.py`
  - `quota.py`
  - `system.py`
  - `ingest.py`
- 问答链路也不是空转：
  - `server_fastapi/routers/ask.py` 负责 HTTP/SSE 与消息持久化
  - `server/services/ask_service.py` 负责模式适配与事件流
  - `agent_core/graph.py` 负责多阶段高质量推理流程
- 会话与文件存储也有真实实现：
  - `server/services/conversation/conversation_service.py`
  - `server/storage/storage_factory.py`
  - `server/storage/minio_backend.py`
- 因此原始 `highThinking` 当时确实已经是一套完整闭环系统，不是仅靠别的后端补全业务能力

## 3. 两套原始系统的关系

- `fastapi-version` 更像主业务系统：
  - 公共能力更全
  - 文件/用户/配额/管理后台更完整
  - 问答是系统中的一个核心模块
- 原始 `highThinking` 也是完整系统：
  - 也有完整前后端壳子和业务能力
  - 但系统重心更偏高质量、慢但准的推理式问答
  - 问答链路复杂度明显高于 `fastapi-version`

## 4. 汇报口径

如果用于当前阶段汇报，可以直接概括为：

- `fastapi-version`：完整主业务系统，包含公共基础设施和多类业务能力。
- 原始 `highThinking`：同样是完整可运行系统，但核心竞争力更集中在高质量问答链路本身。
