# storage 对话 JSON、文件下载与清理链

对应代码：
- `backend/app/modules/storage/service.py`
- `backend/app/modules/conversation/json_store.py`
- `backend/app/modules/conversation/outbox_worker.py`
- `backend/app/modules/conversation/service.py`
- `backend/tests/test_storage.py`
- `backend/tests/test_real_dependencies_optional.py`

## 1. conversation JSON 已经深度依赖 storage backend

`ConversationJsonStore.write_document()` 在本地落盘后会继续：

- `backend.upload_file(local_json, conversations/<user>/<conversation>.json, application/json)`

并返回：

- `local_path`
- `storage_ref`
- `content_hash`
- `size_bytes`
- `sync_status`

所以 chat JSON 不是只保存在本地，也不是只在 DB 里留索引，而是明确走了 storage backend 镜像。

## 2. JSON store 的 remote/local 关系是“本地主写，远端镜像，读时远端可回补”

写入时：

- 先 `_atomic_write_json(local)`
- 再尝试 upload 到 backend

上传失败时：

- 只 warning
- `sync_status` 可能是 `sync_failed`

读取时：

- `load_document()` 会先 `_sync_local_from_remote_if_needed()`
- 下载远端副本到 `.remote.tmp`
- 如果本地缺失，直接用远端覆盖本地
- 如果本地和远端 hash 不同，优先远端

这说明 chat JSON 的一致性策略是：

- 本地写入不中断
- 远端作为可恢复副本
- 读路径上远端有权纠正本地

## 3. outbox worker 也会单独把 chat JSON 再上传一次

`conversation/outbox_worker.py` 会：

- 拿本地 chat json 文件
- 按 object_name 上传
- 成功后更新 conversation 索引里的 `chat_json_storage_ref`

所以 conversation JSON 远端同步不是单一路径：

- JSON store 写入时会尽力 mirror
- outbox worker 又提供了可靠补偿同步

这与 storage 的“镜像失败不阻断”设计是吻合的。

## 4. `resolve_download()` 是通用文件下载决策器

输入：

- `file_row`
- `project_root`
- `use_proxy`
- `expires_seconds`

输出模式有三种：

- `redirect`
- `proxy_file`
- `local_file`

判断顺序：

1. 如果 `storage_ref` 是 `minio://...`
2. 且 `use_proxy = false` -> 返回预签名 URL redirect
3. 且 `use_proxy = true` -> 先下载到 temp file，再返回 proxy_file
4. 如果 `storage_ref` 是 `local://...` -> 本地文件直读
5. 最后 fallback 到 `local_path`

所以下载行为不是写死的，它会被环境变量和 ref 类型共同决定。

## 5. 对 MinIO 下载，proxy 模式会落临时文件

proxy 模式下：

- `mkstemp(prefix="fastapi-storage-", suffix=<ext>)`
- backend 下载到 temp path
- 返回 `{"mode": "proxy_file", "target": temp_path, ...}`

然后由 conversation API 上层通过 `BackgroundTask` 删除 temp file。

因此 storage service 只负责“制造一个代理下载文件”，不负责完整生命周期回收。

## 6. `local://` 和 `local_path` 是两层本地兜底

如果 `storage_ref` 是：

- `local://<path>`

会优先取这个路径。

如果没有合法 `storage_ref`，但 `file_row.local_path` 有值：

- 再退回 `local_path`

这解释了为什么很多上传/会话文件记录会同时保留：

- `storage_ref`
- `local_path`

因为读取路径会综合两者，而不是只信其中一个。

## 7. `cleanup_resources()` 也是组合清理，不是只删一种位置

输入来自 `file_row`：

- `storage_ref`
- `local_path`

行为：

- 如果 `storage_ref` 是 `minio://...`，尝试删对象
- 无论如何，如果 `local_path` 有值，再尝试删本地文件

返回结构记录：

- `storage_attempted`
- `storage_deleted`
- `local_attempted`
- `local_deleted`
- `errors`

所以文件删除不是单一 boolean，而是多落点的清理审计。

## 8. 对 `local://` 不会额外走“对象删除”

这点很重要。

`cleanup_resources()` 只对：

- `scheme == minio`

做 `delete_object()`。

如果是：

- `local://...`

它不会把这个视作对象层删除，而是主要依赖 `local_path` 去删本地文件。

因此 local backend 和 cleanup 之间并不是完全对称设计。

## 9. 测试与真实依赖测试固定了几个关键事实

`test_storage.py` 固定了：

- `parse_storage_ref()` 解析规则
- MinIO redirect 模式的返回
- local fallback 模式的返回
- `cleanup_resources()` 会同时删对象和本地文件

`test_real_dependencies_optional.py` 固定了：

- 真实 MinIO roundtrip 下，chat JSON 写入会得到 `minio://...`
- 删除本地后可从远端恢复回来

这说明 conversation JSON 的远端可恢复能力不是假设，而是测试明确覆盖的契约。

## 10. 这部分是 storage 最“横切”的公共能力

如果说论文 PDF 那部分偏领域化，这部分就是更纯粹的平台能力：

- 持久化镜像
- 下载决策
- 代理临时文件
- 本地/远端资源清理

上传、对话、文件下载都最终落在这条链上。
