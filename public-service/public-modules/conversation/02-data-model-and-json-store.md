# conversation 数据模型与 JSON 主文档

对应代码：
- `backend/app/modules/conversation/repository.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/service.py`

## 1. 数据不是只存在数据库

这个模块至少有四层数据形态：

- `conversations`
  - 会话索引、用户归属、标题、`message_count`、`chat_json_*` 索引字段
- `conversation_messages`
  - 旧消息表，当前主要用于缺失 JSON 时回填
- `conversation_files`
  - 文件结构化记录，上传文件时仍会插入
- 会话 JSON 文档
  - 当前消息与文件聚合态的主文档

再加一层镜像：
- 对象存储中的 conversation JSON 远端副本

## 2. `conversations` 表现在承担什么角色

`ConversationRepository` 对 `conversations` 的操作包括：
- 创建会话
- 修改标题
- 查询详情
- 列表分页
- `message_count` 设置/增量
- 删除会话
- 更新 `chat_json_*` 索引字段

关键点：
- 读取详情时会动态探测列是否存在
- 只有表里真的有这些可选列，才会写入：
  - `chat_json_local_path`
  - `chat_json_storage_ref`
  - `chat_json_hash`
  - `chat_json_size_bytes`
  - `chat_json_version`
  - `chat_json_updated_at`
  - `chat_json_sync_status`

因此这个模块对库结构有兼容性要求，但不是强依赖所有扩展列都存在。

## 3. `conversation_messages` 的地位已经降级

Repository 里仍然保留：
- `add_message()`
- `list_messages()`

但从 `ConversationService.add_message()` 的主路径看：
- 现在新增消息时不会调用 repository 的 `add_message()`
- 主写路径是 JSON 文档
- 详情读取时如果 JSON 丢失，才会用 `list_messages()` 回填

所以：
- `conversation_messages` 更像 legacy/兼容数据源
- 不是当前新增消息的主真相源

## 4. `conversation_files` 仍在主路径里

与消息不同，文件写入目前还是双写起步：

- 先 `ConversationRepository.add_uploaded_file()`
- 再把文件项补进 JSON `files`

旧表保存的字段只有：
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

注意：
- 旧表里没有 `parse_status / index_status / processing_stage / file_meta / deleted_at`
- 这些 richer 状态都只存在 JSON 文档里

因此旧表不能完整表达当前文件状态机。

## 5. JSON 文档是怎样的结构

`ConversationJsonStore.build_default_document()` 固定生成：

- `meta`
- `messages`
- `files`
- `runtime`

其中 `meta` 字段有：
- `schema_version`
- `conversation_id`
- `user_id`
- `title`
- `created_at`
- `updated_at`
- `message_count`
- `last_message_at`

固定版本：
- `schema_version = "chatlog.v1"`

`runtime` 初始字段有：
- `last_request_id`
- `last_latency_ms`
- `last_error`

当前 service 几乎不依赖 `runtime`，更多像预留运行态槽位。

## 6. JSON `messages` 的真实结构

`ConversationService` 写入的消息项包含：

- `message_id`
- `role`
- `content`
- `created_at`
- `status`
- `metadata`

在 assistant 消息上还可能带镜像字段：
- `query_mode`
- `references`
- `steps`
- `done_seen`

这些值本质上也来自 `metadata`，只是为了兼容读取会在顶层再复制一份。

### 6.1 message id 规则

内部 message id 采用：
- `m_000001`
- `m_000002`

生成规则：
- 扫描现有消息最大编号
- 再递增

注意：
- service 对外返回时会转成整数 id
- 但 JSON 存储格式本身是字符串 id

## 7. JSON `files` 的真实结构

service 维护的文件项字段远多于旧表：

- `file_no`
- `file_id`
- `file_type`
- `file_name`
- `local_path`
- `storage_ref`
- `content_type`
- `size_bytes`
- `uploaded_at`
- `file_status`
- `parse_status`
- `index_status`
- `processing_stage`
- `status_updated_at`
- `last_error`
- `file_meta`
- `deleted_at`
- `deleted_by`

几个关键字段：

- `file_status`
  - `active` / `deleted`
- `parse_status`
  - `uploaded` / `parsing` / `parsed` / `failed`
- `index_status`
  - `pending` / `indexing` / `ready` / `failed`
- `processing_stage`
  - `uploaded` / `parsing` / `parsed` / `indexing` / `ready` / `failed`

### 7.1 `file_no` 与 `display_no`

文档里只存 `file_no`。

返回前会再派生：
- `display_no`

区别：
- `file_no`
  - 初始顺序号，删除后不改变
- `display_no`
  - 只对当前 active 文件连续编号

这说明前端看到的“第几个文件”与内部稳定编号不是一回事。

## 8. 单会话并发保护怎么做

`ConversationJsonStore.conversation_lock()` 组合了两层锁：

- 进程内 `threading.Lock`
- 基于 `.lock` 文件的 `fcntl.flock`

锁粒度：
- `(user_id, conversation_id)`

作用：
- 避免同一会话在多线程/多请求里并发修改 JSON

注意：
- 这只保护单会话 JSON 修改顺序
- 并不提供 MySQL + 本地文件 + 对象存储的跨存储原子事务

## 9. 本地 JSON 路径与远端对象名

环境变量：
- `CHAT_JSON_BASE_DIR`
  - 默认 `data/conversations`
- `CHAT_JSON_STORAGE_PREFIX`
  - 默认 `conversations`

因此默认落盘/镜像规则是：
- 本地：`data/conversations/<user_id>/<conversation_id>.json`
- 远端：`conversations/<user_id>/<conversation_id>.json`

## 10. 读取时远端比本地更“可信”

`load_document()` 的实际流程不是简单读本地，而是：

1. 尝试把远端对象下载到临时文件
2. 解析远端 JSON
3. 如果本地不存在，直接用远端覆盖本地
4. 如果本地存在，比较本地和远端 hash
5. hash 不一致时，显式优先远端副本并覆盖本地

这个策略非常关键，因为它意味着：
- 远端镜像在实现上并不只是备份
- 某些场景下远端被视为更可信版本

## 11. 写入时是“先本地，后远端”

`write_document()` 的顺序：

1. 原子写本地 JSON
2. 计算 hash 与大小
3. 尝试上传对象存储
4. 返回：
   - `local_path`
   - `storage_ref`
   - `content_hash`
   - `size_bytes`
   - `sync_status`

`sync_status` 可能是：
- `ok`
- `local_only`
- `sync_failed`

一个细节：
- 如果上传失败，但调用方传入了旧的 `storage_ref_hint`，最终 `sync_status` 可能停在 `local_only`
- 如果没有任何 `storage_ref` 可用且上传失败，才会标成 `sync_failed`

## 12. 这是一个混合真相源系统

综合来看，当前真相源关系可以概括成：

- 会话存在性与归属边界，以 `conversations` 为准
- 消息/文件聚合态，以 JSON 文档为准
- 旧消息表/旧文件表，作为回填兜底来源
- 远端对象存储副本，在读取冲突时可能反过来覆盖本地

所以这不是简单主从，而是：
- DB 索引
- JSON 聚合
- 远端镜像
- 兼容旧表

四者共同组成当前 `conversation` 数据模型。
