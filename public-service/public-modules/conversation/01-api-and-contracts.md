# conversation 接口与契约

对应代码：
- `backend/app/modules/conversation/api.py`
- `backend/app/modules/conversation/schemas.py`
- `backend/app/modules/conversation/service.py`

## 1. 路由面

公开接口共 10 个，全部要求 `require_auth_context`：

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

同时保留了 `/api/...` 与 `/api/v1/...` 双路径。

## 2. 入参契约非常宽

`schemas.py` 只定义了很薄的一层：

- `CreateConversationRequest`
  - 只有 `title`
- `UpdateConversationTitleRequest`
  - 只有 `title`
- `AddConversationMessageRequest`
  - `message.role`
  - `message.content`
  - `message.metadata`

这里没有做严格枚举或复杂嵌套校验，真正的业务校验在 `ConversationService`：
- `role` 只接受 `user` / `assistant`
- `content` 不能为空
- 创建会话时标题为空会回退成 `New Conversation`
- 更新标题时标题为空会返回 `VALIDATION_ERROR`

## 3. 返回模型不是 Pydantic schema，而是 service dict

`api.py` 的 `_respond()` 只是把 `conversation_service` 返回的 dict 原样转成 JSONResponse。

这意味着：
- 文档返回结构必须以 `service.py` 为准
- 这里没有独立的响应 schema 约束
- 契约更像“约定式 dict”，而不是强类型 API contract

## 4. 状态码规则

`ConversationService.status_code_for()` 的规则如下：

- `success=true` 时，用路由声明的 `ok_status`
- `VALIDATION_ERROR` -> `400`
- `NOT_FOUND` / `FILE_UNAVAILABLE` -> `404`
- `DB_UNAVAILABLE` -> `503`
- 其他错误统一 -> `500`

这比 `uploads`、`documents` 更标准一些，没有大量“业务失败仍返回 200”。

## 5. 每个接口的真实语义

### 5.1 创建会话

`POST /conversations`

行为：
- 先写 `conversations`
- 再创建默认 JSON 文档
- 刷新 detail cache 和 list cache

成功返回：
- `conversation_id`
- `user_id`
- `title`
- `message_count`
- `created_at`
- `updated_at`

注意：
- 即使前端不传标题，也会落成 `New Conversation`

### 5.2 会话列表

`GET /conversations?page=&page_size=`

行为：
- 优先读 Redis list cache
- 命中后还会记录最近访问分页参数
- 未命中才查库并写回缓存

限制：
- `page >= 1`
- `1 <= page_size <= 100`

返回项仅含会话摘要：
- `conversation_id`
- `user_id`
- `title`
- `message_count`
- `created_at`
- `updated_at`

### 5.3 会话详情

`GET /conversations/{conversation_id}`

行为：
- 优先读 Redis detail cache
- 缓存未命中时读取 JSON 主文档
- JSON 不存在时从旧表回填并补写 JSON

返回比列表多很多：
- `messages`
- `uploaded_files`
- `uploaded_files_all`
- `pdf_files`
- `excel_files`

注意：
- `uploaded_files` 只含 `file_status=active`
- `uploaded_files_all` 同时包含已删除文件

### 5.4 标题修改

`PUT /conversations/{conversation_id}/title`

行为：
- 先更新 `conversations.title`
- 再在 JSON 文档里同步 `meta.title`
- 刷新 detail/list cache

注意：
- 空标题会直接返回 `title_required`

### 5.5 添加消息

`POST /conversations/{conversation_id}/messages`

行为：
- 只接受 `user` / `assistant`
- 在单会话锁内追加到 JSON `messages`
- 重新计算 `message_count`
- 刷新 list/detail cache

返回：
- `message_id`
- `conversation_id`

注意：
- 对外返回的 `message_id` 是整数
- JSON 文档内部实际存的是 `m_000001` 这种字符串

### 5.6 删除会话

`DELETE /conversations/{conversation_id}`

行为：
- 删除 `conversations` 行
- 尝试删本地 JSON 文件
- 失效 detail cache，刷新 list cache

注意：
- 不会显式删除远端 JSON 对象
- 不会级联清理 `conversation_messages`、`conversation_files`
- 不会清理 outbox 表中的历史记录

### 5.7 文件列表

`GET /conversations/{conversation_id}/files?include_deleted=`

行为：
- 优先从 detail cache 派生
- 再 fallback 到详情接口
- 最后才直接读 JSON / 旧表

返回：
- `files`

注意：
- `include_deleted=false` 时只返回活动文件
- `include_deleted=true` 时能看到软删除文件和清理元数据

### 5.8 文件详情

`GET /conversations/{conversation_id}/files/{file_id}`

行为：
- 优先从 detail cache 中找
- 删除文件会被当成 `NOT_FOUND`
- JSON 找不到时才 fallback 旧表

注意：
- 软删除文件不会暴露给普通读取接口

### 5.9 文件下载

`GET /conversations/{conversation_id}/files/{file_id}/download`

这是最特殊的接口。

额外依赖：
- `require_quota("file_view")`
- `finalize_quota()`

返回形态有三种：
- 业务错误 JSON
- `302 redirect`
- `FileResponse`

下载模式来自 `storage_service.resolve_download()`：
- `redirect`
- `proxy_file`
- `local_file`

其中：
- `proxy_file` 会在响应完成后删除临时代理文件

### 5.10 删除文件

`DELETE /conversations/{conversation_id}/files/{file_id}`

行为：
- 只做软删除
- 立即尝试资源清理
- 把清理结果回写到 `file_meta`
- 刷新缓存

返回：
- `file_status=deleted`
- `already_deleted`
- `cleanup_pending`
- `cleanup_error`

## 6. 文件对象的真实返回结构

service 最终暴露给前端的文件项包含：

- `id`
- `conversation_id`
- `user_id`
- `file_type`
- `file_name`
- `local_path`
- `storage_ref`
- `content_type`
- `size_bytes`
- `created_at`
- `file_no`
- `display_no`
- `file_status`
- `parse_status`
- `index_status`
- `processing_stage`
- `status_updated_at`
- `last_error`
- `file_meta`
- `deleted_at`
- `deleted_by`

其中两个编号要区分：
- `file_no`
  - 原始顺序号，删除后不重排
- `display_no`
  - 只针对 active 文件连续编号

## 7. 这份接口契约和常规 REST 的差异

- 响应 schema 没有独立定义，依赖 service dict 约定
- 下载接口不是统一 JSON
- 消息、文件明细是聚合出来的，不是单表直出
- 软删除文件在不同接口中的可见性不同
- 内部 JSON 使用字符串 message id，但对外简化成整数

因此前端如果要稳定依赖这个模块，最好按 service 的真实返回结构做，而不要只看 `schemas.py`。
