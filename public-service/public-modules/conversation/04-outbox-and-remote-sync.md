# conversation 远端镜像、同步补偿与 outbox

对应代码：
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/outbox.py`
- `backend/app/modules/conversation/outbox_worker.py`
- `backend/app/modules/conversation/service.py`

## 1. 正常写入链路

每次 `ConversationService._persist_document_and_index()` 持久化文档时，都会调用：
- `ConversationJsonStore.write_document()`

这个过程包含：

1. 原子写本地 JSON
2. 计算 hash 与 size
3. 尝试上传对象存储镜像
4. 返回写入结果
5. 更新 `conversations.chat_json_*`
6. 如果同步失败，再尝试写入 outbox

所以 conversation JSON 的同步不是独立任务，而是主写链路的一部分。

## 2. `chat_json_*` 索引字段怎么更新

主写完成后会把以下信息写回 `conversations`：

- `chat_json_local_path`
- `chat_json_storage_ref`
- `chat_json_hash`
- `chat_json_size_bytes`
- `chat_json_version`
- `chat_json_updated_at`
- `chat_json_sync_status`

版本号规则：
- 每次 `_persist_document_and_index()` 都取当前行版本 `+1`

这套版本号是 outbox 判断任务是否陈旧的关键依据。

## 3. `sync_status` 的语义

从 `write_document()` 看，可能出现：

- `ok`
  - 本次上传成功
- `local_only`
  - 当前只有本地可确认，远端状态不完全确定
- `sync_failed`
  - 本次上传失败且没有可复用的 `storage_ref`

注意：
- `local_only` 并不代表没有远端副本
- 它可能只是“本次上传失败，但历史 `storage_ref_hint` 还在”

## 4. outbox 表是可选能力

`ConversationOutboxRepository` 启动时会检查：
- `conversation_json_outbox` 表是否存在

如果表不存在：
- `enqueue_task()` 返回 0
- `claim_due_tasks()` 返回空
- `mark_*()` 都直接返回 0

这意味着：
- outbox 是增强能力，不是硬依赖
- 没有这张表，主流程也能继续跑
- 只是同步失败时不会有异步补偿

## 5. 什么时候会入 outbox

`_persist_document_and_index()` 中只有在 `sync_status != "ok"` 时才尝试入队。

入队内容包括：
- `conversation_id`
- `user_id`
- `json_version`
- `local_path`
- `object_name`
- `content_hash`
- `last_error`

一个关键点：
- 入队是在 `conversations.chat_json_version` 已经更新之后发生的
- outbox worker 后续会用这个版本号与会话当前版本比对

## 6. outbox 的状态机

`conversation_json_outbox` 的状态包括：

- `pending`
- `processing`
- `failed`
- `done`
- `dead`

状态流大致是：

- 新任务 -> `pending`
- 被 worker 抢到 -> `processing`
- 上传成功 -> `done`
- 上传失败但还能重试 -> `failed`
- 重试上限耗尽或任务非法 -> `dead`

### 6.1 入队是 UPSERT 风格

`enqueue_task()` 用了：
- `ON DUPLICATE KEY UPDATE`

这意味着如果表上有相应唯一键：
- 同一个逻辑任务会被覆盖成最新版本
- 状态会重置为 `pending`

## 7. worker 怎么跑

`ChatJsonOutboxWorker.run_once()` 做几件事：

1. 回收超时的 `processing` 任务
2. 抢占一批到期任务
3. 顺序处理每个任务
4. 统计结果：
   - `done`
   - `retry`
   - `dead`
   - `stale`
   - `skipped`

环境变量：
- `OUTBOX_WORKER_BATCH_SIZE`
- `OUTBOX_WORKER_POLL_INTERVAL_MS`
- `OUTBOX_MAX_ATTEMPTS`
- `OUTBOX_RETRY_BASE_SECONDS`
- `OUTBOX_RETRY_MAX_SECONDS`
- `OUTBOX_PROCESSING_TIMEOUT_SECONDS`

## 8. 什么叫 stale

worker 会把以下情况标记为 `stale`，并直接 `mark_done()`：

- 对应会话已经不存在
- 会话当前 `chat_json_version` 比任务版本更新
- 上传成功后更新 `chat_json_sync_status` 时发现版本已落后

这很重要，因为它说明 outbox 不是“每个失败任务都必须成功”，而是：
- 只保证最新版本最终尽量同步
- 老版本任务可以自然作废

## 9. 内容 hash 只是校验，不是阻断条件

如果任务里带了 `expected_hash`：
- worker 会重新算本地文件 hash
- 不一致时只打 warning 日志
- 不会因此阻断上传

所以 hash 的作用更像诊断，而不是一致性硬门槛。

## 10. 重试退避策略

失败时 `_retry_or_dead()` 逻辑：

- `attempt_count + 1 >= max_attempts`
  - 直接 `dead`
- 否则计算指数退避时间
- 写回 `next_retry_at`

退避策略：
- base * `2^(attempt-1)`
- 上限受 `OUTBOX_RETRY_MAX_SECONDS` 控制
- 再附带 `0.2` 抖动比例

## 11. 远端恢复本地的语义

除了“本地写后同步远端”，还有反方向的“远端恢复本地”：

- `load_document()` 每次读时都会尝试下载远端临时副本
- 如果远端内容可解析且与本地不一致，就用远端覆盖本地

因此整个系统的同步关系不是单向的：

- 写入时：本地 -> 远端
- 读取时：远端可能 -> 本地

这也是为什么要记录 `chat_json_hash` 和 `chat_json_version`，因为本地与远端实际上存在双向校准。

## 12. 这套补偿模型的边界

它能解决的：
- 对象存储临时失败
- worker 中断后的 `processing` 任务回收
- 旧版本失败任务自动作废

它不能解决的：
- 跨存储严格事务一致
- 已经删除会话后残留的远端对象统一回收
- outbox 表不存在时的自动补偿

所以这是一个“尽量保持最新 JSON 镜像可恢复”的补偿系统，而不是完整的数据治理系统。
