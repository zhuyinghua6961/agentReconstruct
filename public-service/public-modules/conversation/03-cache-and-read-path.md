# conversation 读路径、缓存与兼容回填

对应代码：
- `backend/app/modules/conversation/cache.py`
- `backend/app/modules/conversation/service.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/repository.py`

## 1. 读路径不是单一来源

`conversation` 的读取顺序通常是：

1. Redis cache
2. JSON 主文档
3. legacy 表回填
4. 必要时把回填结果再补写回 JSON

因此读取本身也带有“修复状态”的副作用。

## 2. 列表缓存怎么做

列表相关函数：
- `get_cached_conversation_list()`
- `cache_conversation_list()`
- `invalidate_conversation_list_cache()`
- `note_conversation_list_access()`
- `get_recent_conversation_list_pages()`

环境变量：
- `CONVERSATION_LIST_CACHE_TTL_SECONDS`
  - 默认 60，最小 10
- `CONVERSATION_LIST_RECENT_PAGES_TTL_SECONDS`
  - 默认 900，最小 60
- `CONVERSATION_LIST_RECENT_PAGES_LIMIT`
  - 默认 8，范围 1..20

### 2.1 失效策略不是删 key，而是版本号

列表 cache key 包含：
- `user_id`
- list cache version
- `page`
- `page_size`

失效时不是直接删除所有分页 key，而是改：
- `conversation:list:version:<user_id>`

这样旧 key 会自然过期。

### 2.2 它会记录“最近访问过哪些分页”

`note_conversation_list_access()` 会记录最近访问的 `(page, page_size)` 组合。

作用：
- 当会话发生写操作后，`_refresh_primary_list_cache()` 不只刷新 `(1,20)`
- 还会把最近访问过的分页一起回填

这是一个比较少见但很实际的细节，说明作者想减少“第一页之外”的缓存失效抖动。

## 3. 详情缓存怎么做

详情相关函数：
- `get_cached_conversation_detail()`
- `cache_conversation_detail()`
- `invalidate_conversation_detail_cache()`

环境变量：
- `CONVERSATION_DETAIL_CACHE_TTL_SECONDS`
  - 默认 30，最小 10
- `CONVERSATION_DETAIL_CACHE_TOUCH_ON_HIT`
  - 默认开启

### 3.1 detail hit 时可能会续命

如果 `CONVERSATION_DETAIL_CACHE_TOUCH_ON_HIT` 开启：
- 详情缓存命中后会执行 `expire()`
- 相当于热点会话会延长 TTL

同时还会打缓存指标：
- `conversation_detail.cache_hit`
- `conversation_detail.cache_touch`

## 4. `get_conversation_detail()` 的真实链路

这是整个模块的关键读路径。

完整流程：

1. 先读 detail cache
2. 未命中则查 `conversations`
3. 进入单会话锁
4. 读取 JSON 文档
5. JSON 不存在时，从 legacy 消息表和文件表回填
6. 如果是回填产生的文档，立即写回 JSON，并同步 `message_count`
7. 针对已删除文件做补偿清理重试
8. 构造聚合响应
9. 再次把 `message_count` 校正回 `conversations`
10. 写入 detail cache

这里反映出几个设计点：

- 详情读取本身可能改库
- `message_count` 以聚合结果为准，必要时会回写 DB
- 删除文件的清理补偿不是异步 worker，而是挂在读路径上

## 5. cached detail 不只是加速，还会反向参与写入

`ConversationService._build_document_from_cached_detail()` 会把 detail cache 中的数据重新拼成 JSON 文档结构。

它被用在：
- `add_message(..., prefer_cached_detail=True)`
- `add_uploaded_file(..., prefer_cached_detail=True)`
- `list_uploaded_files(...)`
- `get_uploaded_file(...)`

也就是说：
- 写路径有时并不是“先读磁盘 JSON 再改”
- 而是“先读 detail cache，反构造 document，再继续写”

好处：
- 减少一次磁盘/远端读

代价：
- 缓存内容事实上成了写入的基底之一
- 如果缓存结构与真实文档结构偏差过大，会影响后续写回

## 6. 兼容回填逻辑

`_load_or_bootstrap_document()` 的逻辑是：

- 优先用 cached detail 反构建文档
- 否则读 JSON
- JSON 没有时，读：
  - `conversation_messages`
  - `conversation_files`
- 然后标准化成 `chatlog.v1` 结构

这里的标准化包括：

- 消息：
  - DB 整数 id -> `m_000001`
  - `metadata_json` -> `metadata`
- 文件：
  - 旧表缺少的状态字段自动补默认值
  - `file_status=active`
  - `parse_status=uploaded`
  - `index_status=pending`
  - `processing_stage=uploaded`

所以只要旧表还在，这个模块就有能力在 JSON 丢失时重新自举出会话文档。

## 7. 文件读取也有多级 fallback

### 7.1 `list_uploaded_files()`

顺序：

1. detail cache 派生
2. 调 `get_conversation_detail()`
3. 自己读 JSON 文档
4. JSON 没文件时再 fallback 旧表

### 7.2 `get_uploaded_file()`

顺序：

1. detail cache 中找
2. `get_conversation_detail()` 中找
3. 读 JSON 文档中找
4. 最后再查 `conversation_files`

这说明：
- 单文件详情也不是结构化表直查优先
- JSON 和 detail cache 才是主读取路径

## 8. 已删除文件的清理补偿挂在读路径上

`_reconcile_deleted_file_cleanup()` 会在详情/文件列表读取时执行。

触发条件：
- 文件 `file_status=deleted`
- `cleanup_pending=true`
  或
- 还没有 `cleanup_last_attempt_at`

限制：
- 每次最多处理 `DELETED_FILE_CLEANUP_RECONCILE_LIMIT`
  - 默认 3
  - 最大 20

补偿时会再次调用：
- `storage_service.cleanup_resources()`

然后把结果写回 `file_meta`：
- `cleanup_attempt_count`
- `cleanup_last_attempt_at`
- `cleanup_pending`
- `cleanup_error`
- `cleanup_storage_deleted`
- `cleanup_local_deleted`

这意味着：
- 删除文件后的资源回收并不完全依赖首次删除动作
- 后续普通读取也可能顺手触发补偿清理

## 9. `message_count` 是派生值，不完全信数据库

几个地方都在修正 `message_count`：

- 详情回填后
- 详情读取完成后
- 新增消息后

说明当前实现里：
- `conversations.message_count` 更像索引/摘要字段
- 真正的消息总数仍以聚合后的 JSON `messages` 长度为准

## 10. 缓存失效与刷新策略

会触发 list/detail 刷新的写操作包括：
- 创建会话
- 改标题
- 新增消息
- 新增文件
- 删除文件
- 文件状态更新
- 删除会话

刷新方式不是统一暴力失效：

- detail
  - 先失效版本，再写最新 payload
- list
  - 先失效版本，再回填首页和最近访问分页

因此这是“带预热的版本化缓存”，不是简单删除后等待下次命中。

## 11. 这条读路径的本质

综合起来，`conversation` 读路径有三个特点：

- 读取会做修复
  - 缺 JSON 会回填
  - 删除文件清理失败会补偿
- 缓存不只是加速
  - 还参与后续写入基底
- 返回是聚合视图
  - 而不是单表直接出参

所以如果以后要拆服务，这里最难拆的不是 API，而是这条“读取即修复”的聚合链路。
