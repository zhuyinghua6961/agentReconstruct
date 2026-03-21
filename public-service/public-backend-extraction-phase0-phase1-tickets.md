# 公共后端拆分原子票据

这份文档是在 `public-backend-extraction-task-list.md` 基础上继续细拆，只覆盖最前面的两段：
- Phase 0：开工前冻结事项
- Phase 1：公共后端骨架

目的：
- 把“可以直接建票开工”的任务拆到更原子。
- 每张票都尽量指向具体代码、输出物、前置依赖和验收条件。

协议基线补充：
- `/home/cqy/worktrees/public-service/gateway-public-backend-protocol-alignment.md`

使用约束：
- Phase 0 / Phase 1 不再包含“重新定义 gateway 公共服务协议”这类任务。
- 后续票据应建立在“协议已存在，当前要做的是对齐与适配”这个前提上。

## A. Phase 0 票据

## A1. 边界冻结

### P0-001 路由清单导出

目标：
- 从当前 FastAPI app 导出完整路由表，作为边界冻结输入。

涉及代码：
- `backend/app/main.py`
- 各模块 `api.py`

输出物：
- 路由总表
- 路由字段至少包含：
  - path
  - methods
  - module owner
  - 当前依赖项
  - 前端调用方
  - 是否含 legacy 别名

前置依赖：
- 无

验收：
- 能覆盖 `main.py` 里当前全部 router
- `ask_gateway` / `ask_dispatch` / `system` / `documents` / `uploads` 的边界路由没有遗漏

### P0-002 路由归属初判

目标：
- 基于 `P0-001` 把所有路由先分成：
  - 公共后端
  - 网关后端
  - 待裁剪

重点模块：
- `documents`
- `system`
- `uploads`

输出物：
- 路由归属初版表

前置依赖：
- `P0-001`

验收：
- 不存在未归类路由
- 待裁剪项必须附理由

### P0-003 路由归属终版冻结

目标：
- 形成可执行的路由 owner 终版。

输出物：
- 路由归属终版表
- 每条路由的迁移目的地

前置依赖：
- `P0-002`

验收：
- 可直接指导后续 app 拆分

## A2. 数据边界冻结

### P0-010 MySQL 表归属清单

目标：
- 明确核心表归属和读写 owner。

必须覆盖：
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

涉及代码：
- `auth/repository.py`
- `quota/repository.py`
- `conversation/repository.py`
- `conversation/outbox.py`

输出物：
- 表归属表
- 每张表的 owner service
- 读方列表
- 写方列表

验收：
- 没有“网关和公共后端都直接写”的未解释场景

### P0-011 文件与对象存储归属清单

目标：
- 明确文件目录、对象前缀的 owner。

必须覆盖：
- `uploads/`
- `papers/`
- `data/conversations/`
- `uploads/pdf/*`
- `uploads/excel/*`
- `papers/*`
- `conversations/*`

涉及代码：
- `uploads/api.py`
- `storage/service.py`
- `storage/paper_storage.py`
- `conversation/json_store.py`

输出物：
- 文件路径与对象前缀归属表

验收：
- 未来迁移时知道哪些数据要挪，哪些只做兼容映射

### P0-012 Redis 与进程内状态归属清单

目标：
- 明确 Redis key 与 runtime 状态的 owner。

必须覆盖：
- quota cache
- conversation list/detail/recent pages
- ask dispatch streams
- qa cache
- `current_pdf_path`
- `answer_cache`

涉及代码：
- `quota/cache.py`
- `conversation/cache.py`
- `ask_dispatch/repository.py`
- `system/service.py`
- `ask_gateway/service.py`

输出物：
- Redis/runtime 状态归属表

验收：
- 进程内状态与公共事实明确分离

## A3. 协作协议冻结

### P0-020 既有 gateway 协议采用矩阵

目标：
- 把 gateway 已定义协议与 fastapi-version 当前实现逐项对齐。

必须覆盖：
- 鉴权校验
- quota precheck/finalize
- conversation message persistence
- conversation file listing
- latest turn context
- upload binding

协议依据：
- `/home/cqy/worktrees/gateway/docs/gateway_forwarding_protocol.md`
- `/home/cqy/worktrees/gateway/docs/gateway_canonical_protocol_revision.md`
- `/home/cqy/worktrees/gateway/app/routers/qa.py`

涉及代码：
- `ask_gateway/api.py`
- `ask_gateway/service.py`
- `file_context/service.py`

输出物：
- 协议采用矩阵
- 当前实现差距矩阵

验收：
- 每个当前本地函数调用都能映射到既有 gateway 协议
- 每个协议项都能标记为：
  - 已在 gateway 实现
  - 协议已定义但未实现
  - fastapi-version 尚未适配

### P0-021 内部 client/SDK 初版接口草案

目标：
- 在既有 gateway 协议基础上，为 fastapi-version/public backend 适配最小内部 client。

输出物：
- `AuthClient`
- `QuotaClient`
- `ConversationClient`
- 可选 `DocumentsClient`

每个 client 至少要有：
- 方法名
- 请求/响应结构
- 幂等要求
- 超时和失败语义

前置依赖：
- `P0-020`

验收：
- 可以直接指导后续网关适配实现

### P0-022 认证策略冻结

目标：
- 定义拆分后网关如何完成登录态与用户状态校验。

候选方案：
- 共用签名 secret + 用户状态回查
- 公共后端 introspection
- 公共后端 `me` 校验代理

涉及代码：
- `auth/deps.py`
- `auth/service.py`
- `ask_gateway/api.py`

验收：
- 网关不直连 `users` 表仍能正确鉴权

### P0-023 quota 协议冻结

目标：
- 定义 `ask_query` 的 precheck/finalize 远程协议。

必须明确：
- precheck 时机
- finalize 时机
- SSE 中断是否计费
- busy / cancel / exception 是否计费

涉及代码：
- `quota/deps.py`
- `ask_gateway/api.py`

验收：
- 可直接指导网关侧 quota client 实现

## A4. Schema 基线冻结

### P0-030 `auth` schema 基线

目标：
- 输出 `users/password_history/user_security_questions` 的规范 DDL 草案。

涉及代码：
- `auth/repository.py`
- `auth/service.py`

验收：
- 足以支持现有 auth/admin 语义

### P0-031 `conversation` schema 基线

目标：
- 输出 `conversations/conversation_messages/conversation_files/conversation_json_outbox` 规范 DDL 草案。

涉及代码：
- `conversation/repository.py`
- `conversation/outbox.py`

验收：
- 足以支持当前 JSON 主文档 + outbox 语义

### P0-032 quota schema 规范化

目标：
- 在现有 quota migration 基础上补成完整初始化基线。

涉及代码：
- `quota/repository.py`
- `database/migrations/*quota*.sql`

验收：
- 新环境可一次性初始化 quota 相关表与默认配置

## A5. 前端冻结

### P0-040 API base 方案冻结

目标：
- 定义前端双 base URL 方案。

必须明确：
- 公共后端 base
- 网关后端 base
- 哪些模块走哪边

涉及代码：
- `frontend-vue/src/api/http.js`
- `frontend-vue/src/services/api.js`

验收：
- 不是停留在“以后支持双后端”，而是给出明确 env 和调用归属

### P0-041 token key 方案冻结

目标：
- 确定唯一 token/user 存储 key。

涉及代码：
- `frontend-vue/src/api/http.js`
- `frontend-vue/src/services/auth.js`
- `frontend-vue/src/router/index.js`
- `frontend-vue/src/features/auth/composables/useAuthSession.js`

验收：
- 后续前端改造不会继续维护两套登录态

### P0-042 现存契约 bug 清单转工单

目标：
- 将已确认 bug 分配到前端票、后端票、协作票。

必须覆盖：
- `admin_users` 导入结果字段
- `documents.reference_preview` body 字段
- uploads 错误 HTTP 语义

验收：
- 每个 bug 都有 owner

## A6. 测试冻结

### P0-050 测试归属表

目标：
- 按公共后端、网关后端、协作测试三类重排现有测试。

涉及代码：
- `backend/tests/*`

验收：
- 测试迁移顺序清楚

### P0-051 运行时前提差异清单

目标：
- 列出哪些测试目前默认依赖“同一个 runtime 同时承载公共和网关”。

重点文件：
- `test_system.py`
- `test_uploads.py`
- `test_documents.py`

验收：
- 后续知道哪些测试要重写，不只是迁文件

### P0-052 gateway 协议测试映射表

目标：
- 把 `/home/cqy/worktrees/gateway/tests` 中已有协议测试和我们当前仓库需要补的协作测试对应起来。

必须覆盖：
- `test_public_proxy.py`
- `test_qa_proxy.py`
- `test_provider_factory.py`

验收：
- 不会重复设计已经在 gateway 仓库里验证过的协议行为
- 能识别哪些测试还需要在 public backend 侧补齐

## B. Phase 1 票据

## B1. 公共后端 app 骨架

### P1-001 独立公共 app 入口

目标：
- 新建只挂公共模块的 app 入口。

涉及代码：
- `backend/app/main.py`

输出物：
- 新 app 创建函数
- 新 router 注册清单

验收：
- 不挂 `ask_gateway`
- 不挂 `ask_dispatch`

### P1-002 公共 runtime 最小化

目标：
- 从 `AppRuntime` 拆出公共 runtime。

必须保留：
- DB
- Redis
- Storage
- upload processing
- conversation outbox

必须移出：
- agent
- retrieval
- generation runtime
- answer cache
- current pdf path

涉及代码：
- `core/runtime.py`

验收：
- 公共后端启动不触发 agent bootstrap

### P1-003 公共 worker 生命周期

目标：
- 确定公共 worker 的启动、停止、health 汇报方式。

涉及代码：
- `core/runtime.py`
- `conversation/outbox_worker.py`
- `conversation/upload_processing_worker.py`

验收：
- worker 生命周期不再依赖网关 app

## B2. 配置骨架

### P1-010 公共配置分层

目标：
- 把公共后端与网关后端配置拆开。

涉及代码：
- `core/config.py`
- `documents/service.py`

验收：
- 公共后端配置文件不再要求问答链专属参数

### P1-011 documents provider 配置归位

目标：
- 给 `summarize_pdf/translate` 相关 provider 配置单独归位。

涉及代码：
- `documents/service.py`
- `documents/translation_service.py`

验收：
- 部署时知道这些 secret 属于公共后端还是其他服务

## B3. 第一批优先模块

### P1-020 auth 抽离最小闭环

目标：
- 让公共后端先独立提供 auth。

必须包含：
- login
- register
- me
- password change
- forgot password
- security questions

验收：
- 前端认证页面能只连公共后端

### P1-021 admin_users 抽离最小闭环

目标：
- 管理后台用户管理切到公共后端。

前置依赖：
- `P1-020`

验收：
- 管理员用户管理不再打问答后端

### P1-022 quota 抽离最小闭环

目标：
- 公共后端独立提供 quota 管理与查询。

验收：
- 用户中心和管理台 quota 页面可只连公共后端

## B4. 第一批协作适配

### P1-030 网关 auth client 雏形

目标：
- 网关先完成鉴权协作适配。

前置依赖：
- `P0-022`

验收：
- 网关 ask 接口不再依赖本地 `auth.deps`

### P1-031 网关 quota client 雏形

目标：
- 网关先完成 ask_query quota 协作适配。

前置依赖：
- `P0-023`

验收：
- 网关 ask 接口不再依赖本地 quota 表

### P1-032 前端双 base URL 雏形

目标：
- 先完成前端基础调用层分流。

涉及代码：
- `frontend-vue/src/api/http.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/services/auth.js`
- `frontend-vue/src/services/admin.js`

验收：
- auth/admin/quota 已可单独切向公共后端

## C. 建议开工顺序

建议先建这些票：

1. `P0-001` 到 `P0-003`
2. `P0-010` 到 `P0-023`
3. `P0-030` 到 `P0-032`
4. `P0-040` 到 `P0-052`
5. `P1-001` 到 `P1-011`
6. `P1-020` 到 `P1-032`

只有这批票结束后，才建议进入 `conversation/uploads/storage/documents/system` 的重拆阶段。
