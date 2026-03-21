# Conversation 迁移核对清单

目的：
- 这份清单只服务于 `public-service/backend` 当前正在迁移的 `conversation` 模块。
- 迁移顺序必须是：
  - 先读 `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/*`
  - 再抽功能列表
  - 再迁移
  - 最后按清单逐项核对

来源：
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/api.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/service.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/repository.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/json_store.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/cache.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/outbox.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/storage/service.py`
- `/home/cqy/worktrees/public-service/public-modules/04-conversation.md`

---

## 1. API 面

- `POST /api/conversations`
- `POST /api/v1/conversations`
- `GET /api/conversations`
- `GET /api/v1/conversations`
- `GET /api/conversations/{conversation_id}`
- `GET /api/v1/conversations/{conversation_id}`
- `PUT /api/conversations/{conversation_id}/title`
- `PUT /api/v1/conversations/{conversation_id}/title`
- `POST /api/conversations/{conversation_id}/messages`
- `POST /api/v1/conversations/{conversation_id}/messages`
- `DELETE /api/conversations/{conversation_id}`
- `DELETE /api/v1/conversations/{conversation_id}`
- `GET /api/conversations/{conversation_id}/files`
- `GET /api/v1/conversations/{conversation_id}/files`
- `GET /api/conversations/{conversation_id}/files/{file_id}`
- `GET /api/v1/conversations/{conversation_id}/files/{file_id}`
- `GET /api/conversations/{conversation_id}/files/{file_id}/download`
- `GET /api/v1/conversations/{conversation_id}/files/{file_id}/download`
- `DELETE /api/conversations/{conversation_id}/files/{file_id}`
- `DELETE /api/v1/conversations/{conversation_id}/files/{file_id}`

约束：
- 全部接口都要求认证
- 下载接口额外挂 `require_quota("file_view")`
- 下载接口成功后要 `finalize_quota`
- 下载返回值不一定是 JSON，可能是：
  - `302 redirect`
  - `FileResponse`

---

## 2. 核心能力清单

### 2.1 会话基础能力

- 创建会话
- 分页列出会话
- 获取会话详情
- 更新会话标题
- 添加消息
- 删除会话

### 2.2 消息能力

- 支持 `user` / `assistant` 两类角色
- assistant 消息支持 metadata 扩展：
  - `query_mode`
  - `references`
  - `steps`
  - `done_seen`
- `get_latest_turn_context` 能从最近一条 assistant 消息里抽：
  - `route`
  - `used_files`
  - `trace_id`

### 2.3 文件元数据能力

- 为会话登记上传文件
- 列出文件
- 获取单文件元数据
- 软删除文件
- 文件状态管理：
  - `file_status`
  - `parse_status`
  - `index_status`
  - `processing_stage`
  - `last_error`
  - `file_meta`

### 2.4 文件下载能力

- 根据 `storage_ref/local_path` 解析下载方式
- 支持：
  - 本地文件直接返回
  - 对象存储重定向下载
  - 对象存储代理下载到临时文件后返回

### 2.5 JSON 主文档能力

- 每个会话维护一份 JSON 主文档
- 默认结构固定：
  - `meta`
  - `messages`
  - `files`
  - `runtime`
- 主消息读写以 JSON 文档为准
- 旧表 `conversation_messages/conversation_files` 主要作为兼容回填源

### 2.6 缓存能力

- conversation list cache
- conversation detail cache
- 最近访问分页记录
- 版本键失效机制
- detail cache 命中 touch TTL

### 2.7 远端镜像 / outbox 能力

- JSON 本地落盘后尝试镜像到对象存储
- 同步失败时写 outbox 重试任务
- outbox 表不存在时主流程不报错，只退化为本地/DB

### 2.8 删除清理补偿能力

- 删除文件不是硬删，而是 JSON 里打 `deleted`
- 删除后会尝试清理：
  - 对象存储资源
  - 本地文件
- 清理失败会写回 `file_meta.cleanup_*`
- 后续 detail/list 读路径会继续补偿重试

---

## 3. Repository / 存储依赖

数据库表：
- `conversations`
- `conversation_messages`
- `conversation_files`
- `conversation_json_outbox`

`conversations` 可选索引列：
- `chat_json_local_path`
- `chat_json_storage_ref`
- `chat_json_hash`
- `chat_json_size_bytes`
- `chat_json_version`
- `chat_json_updated_at`
- `chat_json_sync_status`

文件与对象存储依赖：
- 本地 JSON：`data/conversations/<user_id>/<conversation_id>.json`
- 远端对象名：`conversations/<user_id>/<conversation_id>.json`
- local/minio storage backend

缓存依赖：
- Redis conversation list/detail cache

---

## 4. 当前迁移要求

迁移 `conversation` 时必须保证：
- 不丢 `/api` 与 `/api/v1` 双入口
- 不丢 JSON 主文档作为主真相源的语义
- 不丢旧表兼容回填逻辑
- 不丢文件软删除与 cleanup pending 语义
- 不丢下载的 redirect/proxy/local 三种模式
- 不丢 quota `file_view` 钩子
- 不丢 Redis list/detail cache 和 recent pages 行为
- 不把 outbox 缺失误报成主流程失败

完成标准：
- 模块可以在 `public-service/backend` 内独立工作
- 剩余阻塞只应是：
  - gateway 侧还未接入调度
  - 其他未迁模块还没调用它
