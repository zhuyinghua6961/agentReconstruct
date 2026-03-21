# conversation 模块代码细读

模块路径：
- `backend/app/modules/conversation/api.py`
- `backend/app/modules/conversation/service.py`
- `backend/app/modules/conversation/repository.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/cache.py`
- `backend/app/modules/conversation/outbox.py`
- `backend/app/modules/conversation/outbox_worker.py`
- `backend/app/modules/conversation/upload_processing_worker.py`
- `backend/app/modules/conversation/schemas.py`

模块定位：
- 明确属于公共能力
- 也是当前公共能力里状态最重、跨存储最多、与问答运行态耦合最深的模块

## 1. 结论先说

`conversation` 不是“聊天记录 CRUD”模块，而是一个会话聚合子系统。它同时管理：
- 会话元数据
- 消息主数据
- 会话内上传文件主数据
- 会话 JSON 主文档
- Redis 列表/详情缓存
- 本地 JSON 与对象存储镜像同步
- 同步失败 outbox 重试
- 上传文件解析/索引状态
- gateway/ask_stream 的消息持久化钩子

因此它是当前公共能力里最适合继续拆分细读的模块。

## 2. 深拆文档索引

本次已把 `conversation` 再细分为子文档，放在：
- `/home/cqy/worktrees/public-service/public-modules/conversation/README.md`
- `/home/cqy/worktrees/public-service/public-modules/conversation/01-api-and-contracts.md`
- `/home/cqy/worktrees/public-service/public-modules/conversation/02-data-model-and-json-store.md`
- `/home/cqy/worktrees/public-service/public-modules/conversation/03-cache-and-read-path.md`
- `/home/cqy/worktrees/public-service/public-modules/conversation/04-outbox-and-remote-sync.md`
- `/home/cqy/worktrees/public-service/public-modules/conversation/05-upload-processing-state-machine.md`
- `/home/cqy/worktrees/public-service/public-modules/conversation/06-gateway-hooks-and-write-path.md`

这份 `04-conversation.md` 保留为总览，细节以下面几块为主：
- 接口与返回契约
- 数据模型与 JSON 主文档
- 读路径、缓存、兼容回填
- 远端镜像与 outbox
- 上传文件状态机
- ask_stream/gateway 持久化钩子

## 3. 核心判断

### 3.1 真正的主写路径已经切到 JSON 文档

从 `ConversationService` 的实际实现看：
- `add_message()` 主写入路径是 JSON 文档，不再调用 `ConversationRepository.add_message()`
- `get_conversation_detail()`、`list_uploaded_files()`、`get_uploaded_file()` 都优先从 JSON 文档和缓存返回
- `conversation_messages` 与 `conversation_files` 主要承担兼容回填和结构化补位角色

也就是说：
- `conversations` 表仍然是会话索引和权限边界
- JSON 才是消息和文件聚合态的主文档
- 旧表更像迁移期保底来源，而不是当前唯一真相源

### 3.2 它是双层持久化，不是单库模型

当前会话数据至少同时分布在：
- MySQL `conversations`
- 本地 JSON：`data/conversations/<user_id>/<conversation_id>.json`
- 远端对象存储镜像：`conversations/<user_id>/<conversation_id>.json`

如果 `conversations` 表存在可选列，还会记录：
- `chat_json_local_path`
- `chat_json_storage_ref`
- `chat_json_hash`
- `chat_json_size_bytes`
- `chat_json_version`
- `chat_json_updated_at`
- `chat_json_sync_status`

这说明 `conversation` 已经不是普通关系型模块，而是“DB 索引 + JSON 文档 + 对象存储镜像”的混合模型。

### 3.3 读写并不是事务一致，而是补偿一致

几个关键事实：
- 写 JSON 后再更新 `conversations.chat_json_*`
- 上传文件时先插 `conversation_files`，再写 JSON
- JSON 同步失败时通过 outbox 补偿
- 删除文件时先改 JSON 状态，再做资源清理，再把清理结果回写

所以这里没有跨存储强事务，只有：
- 单会话锁
- 最终一致
- 同步失败重试
- 删除清理补偿

## 4. 对外接口总表

接口由 `api.py` 提供，全部要求 `require_auth_context`：
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

额外事实：
- 下载接口额外挂了 `require_quota("file_view")`
- 成功后会 `finalize_quota()`
- 下载返回可能不是 JSON，而是 `302 redirect` 或 `FileResponse`

## 5. 模块内部的几个子系统

### 5.1 Repository 子系统

`ConversationRepository` 管理：
- `conversations`
- `conversation_messages`
- `conversation_files`

但要强调：
- `conversation_messages.add_message()` 目前不是 service 主写路径
- `conversation_files` 仍被主流程使用，因为上传文件先落结构化行再写 JSON
- `conversations` 负责用户边界、列表索引、标题、`message_count` 以及 `chat_json_*` 同步索引

### 5.2 JSON 文档子系统

`ConversationJsonStore` 管理：
- 本地路径计算
- 对象名计算
- 单会话并发锁
- JSON 原子写
- 远端镜像上传
- 远端恢复本地

默认 JSON 结构固定为：
- `meta`
- `messages`
- `files`
- `runtime`

其中：
- `meta.schema_version = "chatlog.v1"`
- `runtime.last_request_id / last_latency_ms / last_error` 当前更多是预留字段

### 5.3 缓存子系统

`cache.py` 管理：
- 会话列表缓存
- 会话详情缓存
- 最近访问过的列表分页参数
- 基于版本键的失效策略

这不是删 key 的缓存模型，而是“改版本号，让旧 key 自然失效”的模型。

### 5.4 远端同步子系统

远端同步分两段：
- 正常写入时，`write_document()` 会立即尝试上传对象存储
- 如果失败，再用 `conversation_json_outbox` 做异步补偿

如果 outbox 表不存在：
- enqueue 会直接返回 0
- 同步失败不会报错中断主流程
- 会话只保留本地 JSON / DB 索引状态

### 5.5 上传文件处理子系统

`UploadProcessingWorker` 管理：
- PDF/表格解析
- 处理状态推进
- 失败状态标记
- 去重并发控制

状态链路是：
- `uploaded -> parsing -> parsed -> indexing -> ready`
- 任一步失败直接到 `failed`

### 5.6 Gateway 持久化钩子

`persist_user_request()` 和 `persist_assistant_summary()` 把 ask_stream 运行态回写到会话中。

因此 `conversation` 不只是“被前端调用”，还被问答运行链路直接调用。

## 6. 当前最值得注意的实现事实

- `load_document()` 每次读取都会先尝试从远端下载临时副本，再决定是否覆盖本地
- 如果远端和本地 hash 不一致，代码显式优先远端副本
- `add_message()` 会优先用 detail cache 反构造文档，再写 JSON
- `add_uploaded_file()` 是“先 DB，后 JSON”，跨存储不是事务
- 删除会话只删 `conversations` 行和本地 JSON，不会显式清远端 JSON、旧消息表、旧文件表、outbox 记录
- 删除文件是软删除，文件条目仍留在 JSON `files` 中，`include_deleted=true` 才会完整看见
- 删除文件清理失败后，会在后续 detail/list 读路径里继续补偿重试，最多受 `DELETED_FILE_CLEANUP_RECONCILE_LIMIT` 控制
- `get_latest_turn_context()` 会从最近一条 assistant 消息的 metadata 中提取 `route`、`used_files`、`trace_id`

## 7. 建议如何继续拆

如果后面继续往下拆，这个模块优先看下面几个交界面：
- `conversation` 与 `uploads` 的职责边界
- `conversation` 与 `documents` 的文件消费边界
- `conversation` 与 gateway ask_stream 的调用链
- `conversation_files` 旧表何时还能成为真相源

这些在当前代码里都已经不是简单分层，而是运行时协作关系。

## 8. 当前已确认问题与迁移修复点

- `P2` `delete_conversation()` 当前只删除 `conversations` 主表记录、删除本地 JSON 文件并刷新缓存，没有显式清理：
  - 远端对象存储里的 conversation JSON 镜像
  - `conversation_messages / conversation_files` 历史行
  - 会话下上传文件对应的本地文件和对象存储资产
- 这意味着现在的“删除会话”更接近删除主索引入口，而不是完整资产回收；如果直接按当前语义抽到公共后端，会把存储残留和副表残留一起带过去。
- `P3` 当前读写链是 MySQL、JSON、本地文件、对象存储、Redis 之间的补偿一致，不是单事务一致；这不是单点 bug，但属于明确的迁移风险，后续拆公共后端时必须按“最终一致 + 补偿恢复”去设计，而不能误判成单库事务服务。

## 9. 为什么它是优先拆分对象

- 会话和消息是平台主数据
- 上传文件与会话绑定也是平台主数据
- 后续任何问答模式都应该复用这一套会话主存储
- 但它当前已经深度耦合 `storage`、`uploads`、`ask_gateway`、`qa_pdf`
- `upload_processing_worker` 把公共会话层和 PDF/表格解析链绑在一起
- JSON 文档与 MySQL 兼容表并存，说明它仍处在演进中的过渡形态

如果后续要拆公共服务，`conversation` 一定是最先需要单独收敛边界的模块之一。
