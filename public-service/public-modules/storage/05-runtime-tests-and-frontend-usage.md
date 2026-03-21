# storage runtime 启动、测试覆盖与前端消费面

对应代码：
- `backend/app/core/runtime.py`
- `backend/tests/test_storage.py`
- `backend/tests/test_real_dependencies_optional.py`
- `backend/tests/test_integration_upload_flow.py`
- `frontend-vue/src/services/api.js`

## 1. storage 是 runtime 启动时就会探测的基础组件

`core/runtime.py` 的 `_bootstrap_storage()` 会：

1. 取 `runtime.storage_backend` 或 `get_storage_backend(...)`
2. 识别 backend name
3. 如果是 MinIO，额外做 bucket health probe
4. 把结果写入 runtime component status

状态可能是：

- `ok`
- `degraded`
- `missing`

因此 storage 不是某个功能请求时临时初始化，而是应用启动健康检查的一部分。

## 2. runtime 对 MinIO 和 local 的健康语义不同

local backend：

- backend 能构造出来基本就算 ready

MinIO backend：

- 除了 backend 实例存在
- 还会尝试 `client.bucket_exists(bucket)`

失败时：

- component status 记为 `degraded`
- 但进程不会因此直接启动失败

所以 storage 运行时哲学依旧是：

- 尽量降级，不轻易阻断主程序

## 3. 单元测试主要覆盖 storage service 的基础契约

`test_storage.py` 关注的是：

- factory 的 local/minio 选择
- `parse_storage_ref()` 的 scheme 解析
- `mirror_file()` 成功返回
- `cleanup_resources()` 的多落点删除
- `resolve_download()` 的 redirect/local fallback
- paper helper 的缓存与下载逻辑

这批测试说明当前 storage 最被重视的不是复杂对象策略，而是：

- ref 解析
- fallback 行为
- 文件生命周期

## 4. 真实依赖测试覆盖了 MinIO roundtrip + chat JSON restore

`test_real_dependencies_optional.py` 里有一条很关键的真实集成测试：

- 真实 backend 必须是 `MinIOStorageBackend`
- `ConversationJsonStore.write_document()` 后得到 `minio://...`
- 删除本地文件后仍能从远端恢复 chat JSON

这基本证明：

- storage 和 conversation 的远端恢复链是被当作核心能力验证的

## 5. 集成上传测试把 storage 当成文件元数据字段来源

上传相关集成测试里，文件对象普遍保留：

- `local_path`
- `storage_ref`

这与前端 `services/api.js` 的 normalize 逻辑一致。

也就是：

- storage 并不直接暴露给前端一个 SDK
- 而是通过文件记录字段渗透到前端消费面

## 6. 前端主要把 `storage_ref` 当“可回传的文件位置字段”

`frontend-vue/src/services/api.js` 里：

- `normalizeUploadedFile()` 会保存 `storage_ref`
- `asPdfList()` 会优先用 `storage_ref || local_path`
- `asExcelList()` 也会优先用 `storage_ref || local_path`
- 上传成功回包同样会把 `storage_ref` 存到 document/file payload

所以前端对 storage 的理解不是：

- “知道怎么解析 minio/local 协议”

而是：

- “拿到一个可表示文件位置的字段，优先展示/透传它”

## 7. 前端并不会直接解释 `minio://` 或 `local://`

当前前端代码里没有看到：

- 自己解析 `storage_ref` scheme
- 自己组装对象存储 URL

它更多只是：

- 把 `storage_ref` 和 `local_path` 保存在文件对象里
- 在后续调用下载或会话接口时继续交给后端处理

这意味着 scheme 解释权仍然牢牢掌握在后端 storage 层。

## 8. storage 的前端可见面其实很薄

从前端角度看，storage 暴露出来的几乎只有：

- 文件对象里有 `storage_ref`
- 某些上传返回也有 `storage_ref`

真正复杂的：

- redirect/proxy/local file 决策
- MinIO 预签名 URL
- 本地缓存恢复

全都留在后端。

## 9. 这部分说明 storage 是“后端型公共能力”

与 auth/quota 不同，storage 不是一个前后端都强感知的能力中心。

它更像：

- 后端底座
- 前端只消费它投射出来的文件位置字段

所以拆服务时，前端改造压力通常会小于 auth/quota，但后端调用收口压力会更大。
