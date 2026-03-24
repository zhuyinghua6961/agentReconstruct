# 2026-03-23 public-service authority/persistence 链路审阅

## 1. Baseline

- Legacy source of truth: `/home/cqy/worktrees/fastapi-version/backend`
- Target module: `public-service/backend/app/modules/conversation/*`，以及与之直接耦合的 `core/runtime.py`、`modules/uploads/api.py`、`modules/storage/service.py`、`integrations/storage/*`、`integrations/redis/*`
- 审阅边界：只读代码，不修改业务实现；legacy 视为只读基线
- 本文重点：
  - public-service 当前会话持久化主链路
  - authority internal API
  - assistant inbox / 异步物化
  - context snapshot
  - 与 fastQA 已迁移消费侧的对应关系
  - 对 highThinkingQA 迁移可复用能力

## 2. 结论先行

### 2.1 当前 public-service 的 authority 真相层

`public-service` 当前已经把会话 authority 从 legacy 的“DB message/file 行 + JSON 镜像”推进为“JSON chat document 为真相层，MySQL conversation 行为索引/元数据，Redis 为缓存/锁，对象存储为远端镜像”。

对 authority internal API 来说，当前稳定主链路是：

1. `fastQA` 调 internal API 写 user turn
2. `public-service` 在 JSON 文档内直接追加 user message，并更新 `conversations.message_count`
3. `fastQA` 在执行前读 context snapshot
4. `fastQA` 在 done 后异步提交 assistant final event
5. `public-service` 先把 assistant final event 入 inbox（复用 `conversation_messages` 表承载队列）
6. runtime 常驻 worker 再把该 event 物化进 JSON chat document
7. fastQA 通过本地 Redis overlay 覆盖这段最终一致性窗口

这条链路已经能支撑 fastQA 的 authority 迁移，但还不是“完全闭环、可无脑复用到 highThinkingQA”的状态。

### 2.2 最重要的三点判断

1. `fastQA` 已经迁移到 authority client 模式，用户写入、上下文读取、assistant 异步提交三件事都已经依赖 `public-service`。
2. `public-service` 的 assistant async 物化目前是“接受请求后最终一致”，不是“请求返回即已持久化可见”；fastQA 通过 pending overlay 解决用户侧读后不一致窗口。
3. `highThinkingQA` 可以直接复用 fastQA 的 authority client / persistence hook / overlay 设计，但如果它仍依赖本地 summary 语义，则还缺一个关键能力：`public-service` 的 context snapshot summary 目前是空壳，不等价于 highThinkingQA 现有本地 `refresh_conversation_summary` 产物。

## 3. Feature Inventory Summary

- Main flows:
  - 公网 conversation CRUD + file CRUD
  - internal authority user write
  - internal authority context snapshot read
  - internal authority assistant async accept
  - upload 元数据持久化 + 异步文件物化
  - chat JSON 远端镜像 outbox 重试
- Side paths:
  - legacy conversation fallback（默认关闭）
  - Redis detail/list cache
  - object storage download proxy / redirect
  - 本地 JSON 从对象存储反向恢复
- Background flows:
  - `conversation_outbox_worker`
  - `authority_assistant_inbox_worker`
  - `upload_processing_worker`
- Compatibility paths:
  - legacy `/api/*` 与 `/api/v1/*` 双路由
  - legacy DB rows -> JSON bootstrap
  - legacy conversation/files fallback（受配置控制）
- Performance-sensitive paths:
  - detail/list Redis cache
  - JSON 文件热读写
  - upload worker 异步解析
  - outbox 避免在请求路径重试对象存储上传
- Streaming-sensitive paths:
  - authority assistant 写入不在执行请求热路径落盘，而是转异步 inbox
  - fastQA 通过 overlay 补齐“assistant 已生成但 authority 尚未物化”的读窗口

## 4. Dependency Slice Read

### 4.1 public-service

- App/router:
  - `public-service/backend/app/main.py`
  - `public-service/backend/app/modules/conversation/api.py`
  - `public-service/backend/app/modules/conversation/internal_api.py`
  - `public-service/backend/app/modules/uploads/api.py`
- Service/repository/store:
  - `public-service/backend/app/modules/conversation/service.py`
  - `public-service/backend/app/modules/conversation/repository.py`
  - `public-service/backend/app/modules/conversation/json_store.py`
  - `public-service/backend/app/modules/conversation/cache.py`
  - `public-service/backend/app/modules/conversation/outbox.py`
- Worker/runtime:
  - `public-service/backend/app/modules/conversation/outbox_worker.py`
  - `public-service/backend/app/modules/conversation/assistant_inbox.py`
  - `public-service/backend/app/modules/conversation/upload_processing_worker.py`
  - `public-service/backend/app/core/runtime.py`
- Storage/redis:
  - `public-service/backend/app/modules/storage/service.py`
  - `public-service/backend/app/integrations/storage/minio.py`
  - `public-service/backend/app/integrations/redis/keys.py`
- Contracts/tests:
  - `public-service/backend/app/modules/conversation/authority_schemas.py`
  - `public-service/backend/tests/test_conversation_authority_api.py`
  - `public-service/backend/tests/test_conversation_assistant_inbox.py`
  - `public-service/backend/tests/test_conversation_authority_integration.py`

### 4.2 legacy baseline

- `fastapi-version/backend/app/modules/conversation/api.py`
- `fastapi-version/backend/app/modules/conversation/service.py`
- `fastapi-version/backend/app/modules/conversation/repository.py`
- `fastapi-version/backend/app/modules/conversation/json_store.py`
- `fastapi-version/backend/app/modules/conversation/outbox.py`
- `fastapi-version/backend/app/modules/conversation/outbox_worker.py`
- `fastapi-version/backend/app/modules/conversation/upload_processing_worker.py`
- `fastapi-version/backend/app/core/runtime.py`

### 4.3 fastQA / highThinkingQA 消费侧

- `fastQA/app/services/conversation_authority_client.py`
- `fastQA/app/services/chat_persistence.py`
- `fastQA/app/services/pending_overlay.py`
- `fastQA/app/main.py`
- `highThinkingQA/server_fastapi/routers/ask.py`
- `highThinkingQA/server/services/conversation_context_service.py`
- `highThinkingQA/server/services/conversation/conversation_service.py`
- `highThinkingQA/config.py`

## 5. 路由与契约清单

### 5.1 公网 conversation 路由

文件：`public-service/backend/app/modules/conversation/api.py`

- `POST /api/v1/conversations` 与 `POST /api/conversations`
  - 创建会话
  - auth: `require_auth_context`
- `GET /api/v1/conversations` 与 `GET /api/conversations`
  - 分页列会话
  - auth: `require_auth_context`
- `GET /api/v1/conversations/{conversation_id}` 与 `GET /api/conversations/{conversation_id}`
  - 会话详情
  - auth: `require_auth_context`
- `PUT /api/v1/conversations/{conversation_id}/title` 与 `PUT /api/conversations/{conversation_id}/title`
  - 改标题
  - auth: `require_auth_context`
- `POST /api/v1/conversations/{conversation_id}/messages` 与 `POST /api/conversations/{conversation_id}/messages`
  - 直接追加 user/assistant message
  - auth: `require_auth_context`
- `DELETE /api/v1/conversations/{conversation_id}` 与 `DELETE /api/conversations/{conversation_id}`
  - 删除会话
  - auth: `require_auth_context`
- `GET /api/v1/conversations/{conversation_id}/files` 与 `GET /api/conversations/{conversation_id}/files`
  - 列文件
- `GET /api/v1/conversations/{conversation_id}/files/{file_id}` 与 `GET /api/conversations/{conversation_id}/files/{file_id}`
  - 取文件元数据
- `GET /api/v1/conversations/{conversation_id}/files/{file_id}/download` 与 `GET /api/conversations/{conversation_id}/files/{file_id}/download`
  - 下载文件
  - quota: `require_quota("file_view")`
- `DELETE /api/v1/conversations/{conversation_id}/files/{file_id}` 与 `DELETE /api/conversations/{conversation_id}/files/{file_id}`
  - 软删除文件并清理资源

这些公网路由与 legacy conversation API 基本同构；public-service 没把原有 conversation CRUD 删掉，而是在其旁边新增 authority internal API。

### 5.2 authority internal API 路由

文件：`public-service/backend/app/modules/conversation/internal_api.py`

- `POST /internal/conversations/{conversation_id}/messages/user`
  - 作用：authority user turn 直接写入
  - body: `AuthorityUserWriteRequest`
  - caller auth: `X-Internal-Service-Name` + `X-Internal-Service-Token`
- `GET /internal/conversations/{conversation_id}/context-snapshot`
  - 作用：读 authority context snapshot
  - query: `user_id`, `trace_id`, `source_service`, `route`, `requested_mode`, `actual_mode`
  - body: 无 idempotency key
- `POST /internal/conversations/{conversation_id}/messages/assistant-async`
  - 作用：接受 assistant final event，但不保证同步物化
  - body: `AuthorityAssistantAsyncRequest`
  - 返回 `202 accepted`

### 5.3 internal caller 约束

- token 环境变量：`PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`
- 无 token 时，只有 `APP_ENV=test` 才退化成 `authority-test-token`
- `_ALLOWED_SOURCE_SERVICE_MODES` 固定约束：
  - `fastQA -> fast`
  - `highThinkingQA -> thinking`
- internal API 不依赖浏览器用户态 auth，而依赖 service-to-service token

### 5.4 幂等键契约

internal API 强制校验幂等键格式：

- user write: `{conversation_id}:{trace_id}:user`
- assistant async: `{conversation_id}:{trace_id}:assistant`

fastQA client 当前也是这样构造：

- `ConversationAuthorityClient._idempotency_key()`

这意味着 highThinkingQA 迁移时不需要重新设计 idempotency 规则，直接复用即可。

## 6. 当前 authority/persistence 分层模型

### 6.1 真实数据层次

按当前实现，public-service 会话链路中的数据层职责如下：

- JSON chat document
  - 真相层
  - 保存 `meta/messages/files/runtime`
  - user/assistant turn 与文件处理状态都以 JSON 为准
- MySQL `conversations`
  - 会话索引层
  - 保存 `title`, `message_count`, `chat_json_*` 索引字段
- MySQL `conversation_files`
  - 上传文件元数据表
  - public-service 仍写入并保留 compatibility/fallback
- MySQL `conversation_messages`
  - 对 authority 路径不再是主真相层
  - 但被复用了两次：
    - legacy fallback 读取旧消息
    - authority assistant inbox 队列表
- MySQL `conversation_json_outbox`
  - JSON 对象存储同步失败时的 retry outbox
- Redis
  - conversation list/detail cache
  - conversation distributed lock
  - upload processing distributed lock
  - fastQA pending overlay
- 对象存储（MinIO 或 local backend）
  - JSON 远端镜像
  - 上传文件对象存储副本

### 6.2 这和 legacy 的差异

legacy baseline 已经有 JSON chat document + outbox + upload worker，但没有以下 public-service authority 增量：

- internal authority API
- service-to-service caller policy
- authority user write 直写 JSON
- context snapshot 专用 contract
- assistant async inbox / worker
- fastQA authority client 作为正式消费方
- 细化后的 detail cache freshness 判断与 legacy fallback 开关

换句话说，public-service 并不是简单搬运 legacy conversation 模块，而是在 legacy 的 JSON-first persistence 基础上额外加了一层 authority ingress/egress 协议。

## 7. 详细链路审阅

### 7.1 会话创建 / 常规持久化主链

入口：`ConversationService.create_conversation()`

链路：

1. `ConversationRepository.create_conversation()` 插入 MySQL `conversations`
2. 在 `ConversationJsonStore.conversation_lock()` 保护下构建默认文档
3. `ConversationJsonStore.write_document()`：
   - 原子写本地 JSON
   - 计算 hash / size
   - 尝试上传对象存储
4. `ConversationRepository.update_chat_json_index()` 更新 `chat_json_local_path/chat_json_storage_ref/chat_json_hash/chat_json_size_bytes/chat_json_version/chat_json_sync_status/chat_json_updated_at`
5. 如对象存储同步失败，则 `ConversationOutboxRepository.enqueue_task()` 入 outbox
6. 更新 Redis detail/list cache

这一点和 legacy 主体一致，public-service 没改掉已有“本地 JSON 先成功、对象存储后补偿”的策略。

### 7.2 会话详情 / 列表读取链

#### 7.2.1 list

入口：`ConversationService.list_conversations()`

- 先读 Redis list cache
- miss 后查 MySQL `conversations`
- 写回 Redis cache
- 同时更新 recent-pages 记录，给 cache debug / refresh 用

#### 7.2.2 detail

入口：`ConversationService.get_conversation_detail()`

- 先查 MySQL `conversations`
- 再读 Redis detail cache
- 用 `_is_detail_cache_payload_fresh()` 校验缓存是否仍可信
  - 标题是否一致
  - DB `message_count` 是否已超过 cached `message_count`
  - `updated_at` 是否在 grace window 内
- stale 时失效 detail cache
- 在 conversation lock 下：
  - 加载本地 JSON；本地没有时尝试从对象存储拉回；仍没有则视配置走 legacy fallback bootstrap
  - 触发 deleted file cleanup reconcile
- 构建 detail payload，回填 Redis detail cache

#### 7.2.3 detail freshness 的实际语义

detail cache 的 freshness 不是单纯 TTL，而是“TTL + 与 DB conversation row 对齐检查”。

优点：

- 比 legacy 直接命中缓存更稳
- 能避免 DB `message_count` 已前进但缓存仍旧的明显错读

局限：

- 它仍然依赖 `conversations` 行上的 `title/message_count/updated_at` 作为 freshness 锚点
- 真正的 authority 内容还是 JSON 文档，不是 DB row

### 7.3 authority user write 链路

入口：

- HTTP: `POST /internal/conversations/{conversation_id}/messages/user`
- service: `ConversationService.add_authority_user_message()`

链路：

1. internal API 校验：
   - path/body `conversation_id` 一致
   - service token 正确
   - `source_service` 与 mode policy 正确
   - idempotency key 必须等于 `{conversation_id}:{trace_id}:user`
2. service 读会话 row
3. 在 `conversation_lock` 下加载 JSON 文档
4. 遍历现有 JSON `messages`，通过 `metadata.idempotency_key` 做 user 消息幂等去重
5. 未命中时直接在 JSON 文档里追加 user message：
   - `message_id`
   - `role=user`
   - `status=done`
   - `metadata.trace_id/source_service/route/requested_mode/actual_mode/idempotency_key/context_hints`
6. `_persist_document_and_index()` 持久化 JSON + 更新 `chat_json_*` 索引
7. `set_message_count(..., touch_updated_at=False)` 对齐 DB `message_count`
8. 刷新 detail/list cache

注意：

- 这条 authority user write 不会往 `conversation_messages` 插 MySQL row
- 因此 authority 语义上已经把 JSON 文档视为 message 主来源
- MySQL 只保留计数和 JSON 索引，不再充当 turn 真相表

### 7.4 authority context snapshot 链路

入口：

- HTTP: `GET /internal/conversations/{conversation_id}/context-snapshot`
- service: `ConversationService.get_conversation_context_snapshot()`

链路：

1. internal API 校验 caller token 与 source/mode policy
2. service 读取 conversation row
3. 在 `conversation_lock` 下加载/恢复/必要时 bootstrap JSON 文档
4. 如 bootstrap 发生，则补写 JSON 并同步 `message_count`
5. 同时刷新 detail cache
6. `_build_context_snapshot_payload()` 返回 authority contract：
   - `conversation_id`
   - `user_id`
   - `snapshot_version`
   - `updated_at`
   - `summary`
   - `recent_turns`
   - `conversation_state`

#### 7.4.1 snapshot_version 来源

- 优先 `conversations.chat_json_version`
- 回退到 `meta.message_count`

因此 `snapshot_version` 更接近“JSON 文档版本号”，不是单纯消息数。

#### 7.4.2 recent_turns 来源

- 全部来自 JSON 文档 `messages`
- message 输出字段：
  - `message_id`
  - `role`
  - `content`
  - `created_at`
  - `trace_id`

#### 7.4.3 conversation_state 来源

- `last_turn_route`
- `last_focus_file_ids`
- `last_assistant_trace_id`

构造规则：

- 反向扫描最后一个 assistant message
- 取其 metadata 中的：
  - `route`
  - `trace_id`
  - `used_files[].file_id`

这正是 fastQA 需要的“最近一次 assistant 走了哪条路、关注了哪些文件”。

#### 7.4.4 summary 当前状态

`_build_authority_summary()` 目前固定返回：

- `short_summary: ""`
- `memory_facts: []`
- `open_threads: []`

即：contract 已经预留，但 summary 还没有真实计算逻辑。

### 7.5 authority assistant async 接收与物化链路

#### 7.5.1 接收阶段

入口：

- HTTP: `POST /internal/conversations/{conversation_id}/messages/assistant-async`
- service: `ConversationService.accept_authority_assistant_async()`

链路：

1. internal API 校验 path/body conversation_id、一致的 source/mode policy、assistant 幂等键格式
2. service 读取 conversation row
3. 加载 JSON 文档，仅用来检查 JSON 中是否已经存在同 idempotency_key 的 assistant message
4. 如 JSON 已存在，则直接返回 `accepted + deduped=true`
5. 否则 `ConversationRepository.enqueue_authority_assistant_task()` 入 inbox

#### 7.5.2 inbox 的实际承载

这里没有新建独立 `assistant_inbox` 表，而是复用 `conversation_messages`：

- `role='assistant'`
- `content=final_event.answer_text`
- `metadata_json` 中打上：
  - `authority_assistant_async=true`
  - `assistant_async_state=pending`
  - `trace_id`
  - `source_service`
  - `route`
  - `requested_mode`
  - `actual_mode`
  - `idempotency_key`
  - `final_event`
  - `accepted_at`
  - `processing_started_at`
  - `materialized_message_id`
  - `last_error`

也就是说，`conversation_messages` 在 public-service 里现在承担两种角色：

- legacy message fallback 数据来源
- authority assistant 异步 inbox 队列

#### 7.5.3 物化阶段

runtime 启动 `AuthorityAssistantInboxWorker`：

- boot: `core/runtime.py -> _start_authority_assistant_inbox_worker()`
- loop: `_run_authority_assistant_inbox_loop()`
- worker body: `modules/conversation/assistant_inbox.py`

物化流程：

1. `claim_pending_authority_assistant_tasks(limit)` 扫描所有 inbox rows
2. 仅把 `assistant_async_state=pending` 的 row 改成 `processing`
3. worker 对每个 task 调 `ConversationService.materialize_authority_assistant_task()`
4. service 在 `conversation_lock` 下再次做 assistant idempotency 检查
5. 若 JSON 中还没有该 assistant 消息，则真正把 assistant message 追加进 JSON 文档
6. 更新 message_count，刷新 detail/list cache
7. repo 把 inbox row 标记成 `done`，并记录 `materialized_message_id`

#### 7.5.4 物化后 assistant message 进入 JSON 的字段

写入 JSON 时，assistant metadata 包含：

- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `idempotency_key`
- `used_files`
- `references`
- `steps`
- `timings`
- `done_seen=true`

这使得后续 context snapshot / latest turn context 都能从 JSON 中恢复 route 和 file focus。

### 7.6 upload / 文件物化链路

这一部分不是 authority internal API 本身，但它直接决定 context snapshot 和 assistant `used_files` 的可用性，因此属于同一 persistence 审阅范围。

入口：`public-service/backend/app/modules/uploads/api.py`

链路：

1. upload route 先把原文件保存到本地 upload 目录
2. 同时尝试对象存储镜像，生成 `storage_ref`
3. `_persist_uploaded_file()` 调 `ConversationService.add_uploaded_file()`
4. service：
   - `conversation_files` 插入文件元数据行
   - conversation JSON `files[]` 里追加 file record
   - 初始状态：
     - `parse_status=uploaded`
     - `index_status=pending`
     - `processing_stage=uploaded`
5. runtime 中的 `UploadProcessingWorker.submit()` 异步解析文件
6. worker 解析后回写 JSON `files[].file_meta/parse_status/index_status/processing_stage`

#### 7.6.1 UploadProcessingWorker 的关键点

- 锁：
  - 优先 Redis 分布式锁
  - 无 Redis 时可退回 MySQL named lock
  - test 环境允许 unsafe fallback
- 文件解析：
  - PDF: 依赖 `fitz`，只做抽取预览
  - Excel/CSV: 解析列、样例行
- 不做真正索引构建，只把状态推进到：
  - `parse_status=parsed`
  - `index_status=ready`
  - `file_meta.index_mode=deferred`
  - `file_meta.index_note=runtime_query_indexing`

这意味着当前文件物化是“解析元数据 ready”，不是“离线向量索引 fully materialized”。

## 8. repository / JSON / cache / DB / object storage 交互矩阵

### 8.1 MySQL `conversations`

职责：

- 会话主键与用户归属
- 标题、message_count、created_at、updated_at
- JSON 索引字段：
  - `chat_json_local_path`
  - `chat_json_storage_ref`
  - `chat_json_hash`
  - `chat_json_size_bytes`
  - `chat_json_version`
  - `chat_json_updated_at`
  - `chat_json_sync_status`

### 8.2 MySQL `conversation_files`

职责：

- 文件基础元数据持久化
- public-service 仍以它作为：
  - fallback 数据源
  - 下载解析数据源

但文件处理状态的真相层已经转到 JSON `files[]`。

### 8.3 MySQL `conversation_messages`

当前有两类用途：

- legacy message fallback
- authority assistant async inbox

对 authority user write 与 authority assistant materialized message，本体都不再写这里。

### 8.4 MySQL `conversation_json_outbox`

职责：

- JSON mirror 同步失败补偿
- 字段包含：
  - `conversation_id`
  - `user_id`
  - `json_version`
  - `local_path`
  - `object_name`
  - `content_hash`
  - `status`
  - `attempt_count`
  - `next_retry_at`
  - `processing_started_at`
  - `last_error`

### 8.5 本地 JSON 文档

文件：`ConversationJsonStore`

默认路径：`CHAT_JSON_BASE_DIR` 下 `/{user_id}/{conversation_id}.json`

文档结构：

- `meta`
- `messages`
- `files`
- `runtime`

关键能力：

- 原子写临时文件再 `os.replace`
- 本地 lock file + 进程内 lock
- Redis distributed lock
- 本地不存在时可从对象存储拉回远端副本

### 8.6 Redis

职责分两类。

#### 8.6.1 cache

- list cache version key
- detail cache version key
- recent pages key
- detail cache 支持 hit 时 touch TTL
- detail cache 用 `cache_meta.cached_at` 参与 freshness 判断

#### 8.6.2 lock / overlay

- conversation 文档锁
- upload processing 文件级锁
- fastQA pending overlay（消费侧，不在 public-service 内）

### 8.7 对象存储

#### 8.7.1 JSON mirror

`ConversationJsonStore.write_document()`：

- 先本地写 JSON
- 再 `storage_backend.upload_file()` 上传 `conversations/{user_id}/{conversation_id}.json`
- 成功则 `sync_status=ok`
- 失败则保留本地成功状态，并进入 outbox retry

#### 8.7.2 上传文件对象存储

upload route 保存用户文件时也会尝试镜像到对象存储；下载时：

- MinIO 可直接签名跳转
- 或服务端下载到临时文件再 proxy 返回
- 删除文件时同时清理对象存储与本地文件

## 9. runtime / worker / operability

### 9.1 runtime 启动顺序

`public-service/backend/app/core/runtime.py`

启动时：

1. 初始化目录
2. bootstrap database
3. bootstrap redis
4. bootstrap storage
5. bootstrap auth/quota/conversation service
6. bootstrap retrieval
7. bootstrap upload processing worker
8. lifespan 启动：
   - `conversation_outbox_worker`
   - `authority_assistant_inbox_worker`

### 9.2 worker 列表

- `conversation_outbox_worker`
  - 负责 JSON mirror retry
- `authority_assistant_inbox_worker`
  - 负责 assistant async 最终物化
- `upload_processing_worker`
  - 负责 PDF/Excel 解析与文件状态推进

### 9.3 health / status

runtime 保存：

- `component_status`
- `health_flags`
- `conversation_outbox_status`
- `authority_assistant_inbox_status`

其中 assistant inbox status 还会 probe：

- `backlog`
- `processing`
- `failed`
- `enabled`

这对迁移上线后的观测非常重要，因为 authority assistant 路径本质上不是同步提交，而是依赖后台 worker 持续健康。

## 10. 幂等性与最终一致性

### 10.1 user write 幂等

去重位置：JSON 文档 `messages[*].metadata.idempotency_key`

语义：

- 同一 `conversation_id + trace_id + user`
- 多次提交只保留一个 user turn
- 返回 `deduped=true/false`

### 10.2 assistant async 幂等

分两层：

1. 接收阶段
   - JSON 已有 assistant message 时，直接 `deduped=true`
   - inbox 入队时，repo 还会再次扫描 inbox rows 按 idempotency_key 去重
2. 物化阶段
   - `materialize_authority_assistant_task()` 在 JSON 文档内再次按 idempotency_key 检查

这保证了多次 accept 或多 worker 并发 claim 时，不会把 assistant answer 物化两次。

### 10.3 outbox 最终一致性

JSON mirror 写对象存储采用“本地成功优先、远端补偿重试”：

- 请求路径不阻塞在重试
- outbox worker 用指数退避 + 抖动
- 版本号和 content hash 防止旧版本覆盖新版本
- 若发现 task version 落后于 conversation 当前 `chat_json_version`，直接标记 stale/done

这是当前 persistence 设计里最成熟的一段最终一致性逻辑。

### 10.4 assistant async 最终一致性

assistant authority 路径的可见性语义是：

- `accept_assistant_async` 返回 `202 accepted`
- 这只代表 inbox 接收成功
- assistant message 何时进入 snapshot，要看 inbox worker 什么时候跑到它

因此这是一个显式最终一致性窗口，不是同步可见。

### 10.5 fastQA 如何补这个窗口

`fastQA/app/services/chat_persistence.py` 在 assistant done 后：

1. 先把最新 assistant answer 写入 Redis pending overlay
2. 再异步调用 public-service `assistant-async`
3. 下次读 context snapshot 时：
   - 如果 snapshot 还没收敛到该 trace_id
   - `pending_overlay.merge_pending_assistant_overlay()` 会把 Redis 里的 assistant answer 临时拼到 chat history
4. 一旦 snapshot 已含该 trace_id，则清 overlay

这说明 fastQA 侧已经认知到 public-service assistant persistence 是最终一致，不是同步一致。

## 11. 与 legacy 的对应关系

### 11.1 已保留的 legacy 能力

public-service 保留了 legacy 中这些核心持久化能力：

- 会话 CRUD 公网 API
- JSON 文档落地
- JSON 对象存储镜像
- outbox retry 机制
- upload processing worker
- file metadata + download + cleanup
- Redis list/detail cache
- legacy DB fallback / bootstrap 思路

### 11.2 public-service 新增能力

相对于 legacy，public-service 新增或明显扩展了：

- internal authority API contract
- source service + mode policy
- user write 幂等键检查
- assistant async accept + inbox materialization
- context snapshot 专用模型
- detail cache freshness 判断更严格
- runtime 对 inbox/outbox 的显式健康探针
- legacy fallback 改成配置开关 `PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK`

### 11.3 迁移边界判断

这意味着：

- 如果只看 persistence substrate，public-service 已经覆盖 legacy conversation 模块的大部分公共能力
- 如果看“给执行服务使用的 authority contract”，public-service 已经超出 legacy，开始承担跨服务 authority 平台角色

## 12. 与 fastQA 已迁移能力的对应关系

### 12.1 fastQA 当前已经迁走的能力

fastQA 已把以下能力外包给 public-service：

- authority user write
- authority context snapshot read
- authority assistant async accept

对应代码：

- `fastQA/app/services/conversation_authority_client.py`
- `fastQA/app/services/chat_persistence.py`
- `fastQA/app/main.py`

### 12.2 fastQA 仍保留在本地的能力

- persistence hook 安装点
- ordered dispatcher（按 `conversation:{user_id}:{conversation_id}` 串行化）
- pending overlay Redis 逻辑
- 请求侧 payload 规整、file hint 规整

### 12.3 fastQA 与 public-service 的契约配合点

#### user write

fastQA 调：

- `write_user_turn()`

携带：

- `selected_file_ids`
- `last_turn_route_hint`

public-service 存进 JSON `metadata.context_hints`。

#### context snapshot

fastQA 调：

- `read_context_snapshot()`

并把返回值组装成：

- `chat_history`
- `conversation_state`
- `summary`
- `snapshot_version`
- `pending_overlay`

#### assistant async

fastQA 调：

- `accept_assistant_turn_async()`

只提交最终 done 事件，不提交中间增量。

### 12.4 这说明 fastQA 迁移已经达到什么程度

已经达到：

- authority 读写分离到 public-service
- fastQA 不再自己决定会话最终状态
- fastQA 只保留“执行态临时补丁”而不是主持久化真相

这就是 highThinkingQA 可直接复用的迁移目标形态。

## 13. 对 highThinkingQA 迁移可复用的能力

### 13.1 可直接复用的 contract

highThinkingQA 可直接复用以下 contract，不需要重新设计：

- internal auth headers
  - `X-Internal-Service-Name`
  - `X-Internal-Service-Token`
- source_service 命名
  - `highThinkingQA`
- mode policy
  - `requested_mode=thinking`
  - `actual_mode=thinking`
- idempotency key 规则
  - user: `{conversation_id}:{trace_id}:user`
  - assistant: `{conversation_id}:{trace_id}:assistant`
- context snapshot contract
  - `recent_turns`
  - `conversation_state`
  - `summary`
  - `snapshot_version`

### 13.2 可直接复用的实现模式

highThinkingQA 几乎可以按 fastQA 同样的分层迁移：

1. 执行前
   - 调 authority snapshot 取 context
2. 请求进入时
   - 把 user turn 发给 public-service
3. stream 完成后
   - 把 assistant final event 发给 public-service async accept
4. 本地使用 overlay 隐藏最终一致性窗口
5. persistence hook 仍由本服务安装，但实际 authority 真相层在 public-service

### 13.3 可直接搬用的 fastQA 代码资产

从代码结构看，以下 fastQA 能力可以近乎原样移植到 highThinkingQA：

- `conversation_authority_client.py`
  - 只需默认 `service_name` 改成 `highThinkingQA`
- `chat_persistence.py`
  - user write / snapshot read / assistant async accept 的包装方式
- `pending_overlay.py`
  - overlay 收敛判定完全可复用
- ordered dispatcher key 设计
  - `conversation:{user_id}:{conversation_id}`

### 13.4 highThinkingQA 迁移前需要补的点

#### A. summary 语义对齐

highThinkingQA 当前本地 `build_conversation_context()` 依赖：

- `get_conversation_context_snapshot()` 返回的 `messages`
- `summary`

且本地 conversation service 还支持：

- `refresh_conversation_summary()`
- `build_conversation_summary()`

而 public-service 当前 authority snapshot 的 `summary` 是空壳，因此如果 highThinkingQA 的 rewrite / ask 逻辑依赖 summary 质量，则需要先补一条：

- 要么 public-service 生成真实 summary
- 要么 highThinkingQA 迁移时暂时降级为仅使用 `recent_turns`

#### B. overlay 不能省

如果 highThinkingQA 采用和 fastQA 一样的 assistant async accept，就必须一起迁移 overlay；否则用户紧接着发下一轮时，会看到 assistant 最新回答在 snapshot 中暂时不可见。

#### C. local conversation_service 要从 authority 写入里退场

highThinkingQA 当前 `server_fastapi/routers/ask.py` 还直接调用本地 `conversation_service.add_message()`。迁移后这部分不能和 public-service authority 并存写主链，否则会出现双写与真相层漂移。

## 14. Findings

### 14.1 [high] assistant inbox 失败任务是终态，当前没有真正 retry / requeue

- Category: correctness / consistency / operability
- Files:
  - `public-service/backend/app/modules/conversation/repository.py`
  - `public-service/backend/app/modules/conversation/assistant_inbox.py`
- Why it matters:
  - `AuthorityAssistantInboxWorker.run_once()` 对失败任务调用 `mark_authority_assistant_task_failed()`
  - repository 只会 `claim_pending_authority_assistant_tasks()`，不会重新 claim `failed`
  - 结果是 assistant async 只要一次物化失败，就会永久停在 failed backlog 中，除非人工修数据
- Expected behavior for a durable authority service:
  - 至少应有 retry/backoff/requeue，或显式 dead-letter 机制
- Current behavior:
  - worker summary 里把这类失败记为 `retry`，但实际上没有后续 retry 路径
- Impact:
  - 某次短暂 DB/JSON/storage/lock 抖动即可导致 assistant turn 永远不可见
  - fastQA overlay 只能临时遮挡，TTL 到期后该 answer 仍会从 authority 消失
- Suggested follow-up:
  - 为 assistant inbox 增加真正的 pending/processing/failed/dead 状态机和 next_retry_at/backoff
  - 或至少允许 failed 被后台 requeue

### 14.2 [medium] context snapshot 的 summary contract 已存在，但实现仍是空壳

- Category: feature-loss / migration-gap
- Files:
  - `public-service/backend/app/modules/conversation/service.py`
  - `public-service/backend/app/modules/conversation/authority_schemas.py`
  - `highThinkingQA/server/services/conversation/conversation_service.py`
- Why it matters:
  - public-service 对外承诺 snapshot 包含 `summary`
  - 但 `_build_authority_summary()` 固定返回空结构
  - highThinkingQA 现有本地 conversation service 会刷新并消费真实 summary
- Legacy expectation:
  - legacy baseline 没有 internal authority snapshot contract，但 highThinkingQA 当前本地实现已经把 summary 当作有效上下文
- Current migrated behavior:
  - fastQA 现在能容忍空 summary
  - highThinkingQA 若直接切到 authority snapshot，context richness 会下降
- Impact:
  - thinking 模式的 rewrite / 多轮理解可能退化
- Suggested follow-up:
  - 在 public-service 增加 summary materialization 逻辑，或给 highThinkingQA 明确降级策略

### 14.3 [medium] 如果开启 legacy fallback，assistant inbox placeholder 可能在无 JSON 的旧会话里提前泄露为已可见消息

- Category: consistency
- Files:
  - `public-service/backend/app/modules/conversation/service.py`
  - `public-service/backend/app/modules/conversation/repository.py`
- Why it matters:
  - assistant async inbox 复用了 `conversation_messages`
  - `_load_or_bootstrap_document()` 在 legacy fallback 开启且本地 JSON 缺失时，会从 `repo.list_messages()` 把 DB rows 全量拉入 JSON bootstrap
  - `repo.list_messages()` 不会过滤 `authority_assistant_async=true` 的 inbox rows
- Current severity note:
  - 默认 `PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK=false`，所以这是条件性风险，不是默认常驻问题
- Impact:
  - 对尚未生成 JSON 的历史会话，如果先收到 assistant async accept 再读 snapshot，可能在 worker 物化前就看到 placeholder answer
- Suggested follow-up:
  - legacy fallback bootstrap 时显式过滤 authority inbox rows
  - 或 accept 阶段先强制 bootstrap/persist 空 JSON

### 14.4 [low] authority 安全边界目前只有共享静态 token，没有更细粒度 caller 隔离

- Category: security / operability
- Files:
  - `public-service/backend/app/modules/conversation/internal_api.py`
  - `fastQA/app/services/conversation_authority_client.py`
- Why it matters:
  - 当前只校验共享 `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`
  - `X-Internal-Service-Name` 更多是策略路由，不是独立密钥域
- Impact:
  - 若某内部服务 token 泄露，则它可伪装成受允许 caller
- Suggested follow-up:
  - 若后续多服务接入，可考虑 per-service token、mTLS，或 gateway 内网鉴权

## 15. Missing Features Checklist

- `[high] [open]` conversation/assistant-inbox: assistant failed tasks do not actually retry
  files:
  `public-service/backend/app/modules/conversation/repository.py`, `public-service/backend/app/modules/conversation/assistant_inbox.py`
  impact:
  transient failure can permanently hide assistant turns from authority state
  follow-up:
  add retry/backoff/dead-letter semantics

- `[medium] [open]` conversation/context-snapshot: summary contract has no materialized content
  files:
  `public-service/backend/app/modules/conversation/service.py`
  impact:
  highThinkingQA cannot get parity with its current local summary-based context flow
  follow-up:
  implement summary generation or document explicit downgrade

## 16. Performance Risks Checklist

- `[medium] [open]` conversation/assistant-async: consumers must absorb eventual consistency window themselves
  files:
  `public-service/backend/app/modules/conversation/assistant_inbox.py`, `fastQA/app/services/pending_overlay.py`
  impact:
  without overlay, immediate next-turn reads can miss latest assistant answer
  follow-up:
  require overlay in every migrated execution service, or move to stronger synchronous commit semantics

- `[low] [open]` conversation/inbox-claim: inbox claim is scan-and-update over `conversation_messages`, not dedicated queue table semantics
  files:
  `public-service/backend/app/modules/conversation/repository.py`
  impact:
  multi-instance deployments may do redundant claims/work, though idempotency limits duplicate materialization
  follow-up:
  consider dedicated inbox table or SQL claim primitive

## 17. Other Review Risks Checklist

- `[medium] [open]` conversation/legacy-fallback: authority inbox rows can bleed into bootstrap if fallback is enabled on old conversations
  category:
  consistency
  files:
  `public-service/backend/app/modules/conversation/service.py`, `public-service/backend/app/modules/conversation/repository.py`
  impact:
  assistant may appear visible before worker materialization in migration edge cases
  follow-up:
  filter inbox rows during fallback bootstrap

- `[low] [open]` conversation/internal-auth: shared static token for all internal callers
  category:
  security
  files:
  `public-service/backend/app/modules/conversation/internal_api.py`
  impact:
  coarse-grained trust boundary across internal services
  follow-up:
  move toward per-service credentials or network-level identity

## 18. Acceptance Status

- Feature parity baseline created: yes
- Inventory checked after migration: yes, for authority/persistence slice
- Critical risks resolved: none identified at critical
- High risks resolved: no
- Performance acceptable: conditionally yes for fastQA, because overlay exists
- Streaming acceptable: yes for fastQA current contract, because assistant persistence intentionally moved off hot path
- Ready for gateway integration: for fastQA path yes; for highThinkingQA path not yet at full parity because summary and inbox retry still存在缺口

## 19. 迁移判断：对 highThinkingQA 的实际建议

### 19.1 可以直接照搬的部分

- authority client contract
- dispatcher key 设计
- pending overlay 机制
- user write / snapshot read / assistant async accept 三段式 hook
- service rollout config 结构（highThinkingQA 已经有与 fastQA 对齐的 rollout settings）

### 19.2 迁移前必须先决定的两件事

1. `summary` 是否必须保真
   - 如果必须，则应先在 public-service 做 summary materialization
   - 如果可降级，则可以先迁 recent_turns + overlay
2. assistant async 失败是否允许人工补偿
   - 如果不能接受人工补偿，则必须先补 inbox retry/dead-letter

### 19.3 最稳妥的 highThinkingQA 迁移落点

建议目标形态：

1. 执行前用 public-service snapshot 作为唯一 authority 读取源
2. 请求进入即 authority user write
3. done 后 assistant async accept
4. 本地保留 overlay 直到 snapshot 收敛
5. 停止在 highThinkingQA 本地 `conversation_service.add_message()` 上继续做主写
6. 等 public-service summary 真正 materialize 后，再切掉高思考模式对本地 summary 的依赖

## 20. 总结

- `public-service` 已经承接了 fastQA 所需的 authority/persistence 主链路，核心真相层是 JSON document，不再是 `conversation_messages`。
- `assistant-async` 当前是“accepted 后最终一致物化”，这不是 bug，而是设计选择；真正的风险在于它还没有可靠 retry，失败后会卡死在 failed inbox。
- `fastQA` 已通过 authority client + pending overlay 完成迁移闭环，说明 public-service 已具备跨服务 authority 平台雏形。
- `highThinkingQA` 可以复用 fastQA 的大部分迁移模式，但不能忽略两个缺口：
  - `summary` 目前仍是空壳
  - assistant inbox 失败没有自动补偿
