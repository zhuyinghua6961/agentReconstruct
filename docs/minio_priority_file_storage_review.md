# 文件存储与 MinIO 优先级审查

## 范围

- 审查对象：
  - `gateway/`
  - `public-service/`
  - `fastQA/`
  - `highThinkingQA/`
- 审查目标：
  - 判断系统中文件相关路径是否满足“MinIO 优先，本地仅作备份/缓存/保险”的目标
- 重点覆盖：
  - `papers`
  - 上传文件
  - 原文查看 / 下载
  - 聊天记录 JSON 加载与保存
  - 翻译缓存、全文总结缓存、解析缓存等派生文件
- 约束：
  - 本文档基于只读代码审查
  - 未修改任何业务代码

## 上一轮结论回顾

上一轮对“文件上传 + 文件问答/混合问答是否只是空壳”的审查结论是：

- 不是空壳。
- 真实链路已经存在：
  - 前端提交文件上下文
  - `gateway` 从 `public-service` 拉取会话文件元数据并做路由判定
  - `public-service` 持久化文件元数据并处理上传后的后台任务
  - `fastQA` 执行 `pdf_qa / tabular_qa / hybrid_qa`
- 但分布式闭环仍不完整：
  - `fastQA` 对上传文件执行仍主要依赖 `local_path`
  - `storage_ref` 尚未成为执行态文件物化的主入口

## 总结结论

当前系统并没有整体达到“MinIO 是唯一主存储，本地只是保险副本”的一致模型。

可以分成三类：

1. 已接近目标
- 上传文件持久化
- 上传文件下载 / 原文下载
- `papers` 的对象存储回源

2. 部分接近，但仍然是“双写/双读/本地优先”
- `public-service` 聊天 JSON
- `public-service` 翻译缓存

3. 明显不符合目标
- `fastQA` 上传文件执行链
- `highThinkingQA` 翻译缓存
- `highThinkingQA` / root ingest 的解析 Markdown 缓存

## 风险排序

### High

1. `fastQA` 上传文件问答执行仍以 `local_path` 为前提，不是 `storage_ref` / MinIO 优先
- 这意味着跨实例、重启后、执行节点漂移时，文件问答闭环不可靠。
- 表格问答直接要求 `local_path` 存在，否则报错。
- PDF 文件问答与混合问答也仍然依赖本地路径。
- 参考：
  - [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L221)
  - [workbook_loader.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py#L186)

2. 聊天 JSON 持久化模型不统一，而且都不是严格的 MinIO 主存储模型
- `public-service` 是本地优先读取、本地优先写入、失败后 outbox 补同步。
- `highThinkingQA` 是远端优先回拉修正本地，再本地写入并镜像远端。
- 两套策略不一致，会导致分布式一致性预期混乱。
- 参考：
  - [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L164)
  - [outbox_worker.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/outbox_worker.py#L194)
  - [chat_json_store.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/chat_json_store.py#L107)

3. `public-service` translation cache 不是 MinIO 主存储，而是本地快照 + MinIO 快照合并
- 读时本地和远端都参与，写时本地先写再上传 MinIO。
- 没有对象版本控制，多实例更新时会出现最后写入覆盖。
- 参考：
  - [translation_cache_impl.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/translation_cache_impl.py#L128)
  - [translation_cache_impl.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/translation_cache_impl.py#L219)
  - [translation_cache_impl.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/translation_cache_impl.py#L250)

### Medium

1. `papers` 更像“MinIO 远端权威 + 本地执行副本”，不是纯远端直读
- `paper_exists()` 会优先看 MinIO。
- 但实际 `view_pdf`、总结、句子提取等读取都需要先物化到本地。
- 这在架构上可以接受，但要明确它不是“无本地依赖”。
- 参考：
  - [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L75)
  - [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L88)
  - [paper_storage.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/paper_storage.py#L169)
  - [paper_storage.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/storage/paper_storage.py#L79)

2. `public-service` 的 state/runtime 根路径仍然分裂
- 配置默认数据根是 `/tmp/public-service`。
- gunicorn 的 pid/log 又写在项目内 `.runtime/`。
- 与仓库 `resource/state`、`resource/runtime` 契约不完全一致。
- 参考：
  - [config.py](/home/cqy/worktrees/highThinking/public-service/backend/app/core/config.py#L14)
  - [config.py](/home/cqy/worktrees/highThinking/public-service/backend/app/core/config.py#L133)
  - [start_gunicorn.sh](/home/cqy/worktrees/highThinking/public-service/scripts/start_gunicorn.sh#L6)
  - [README.md](/home/cqy/worktrees/highThinking/resource/README.md#L9)

3. 解析 Markdown 缓存完全是本地文件缓存，没有 MinIO 参与
- root `ingest/` 与 `highThinkingQA/ingest/` 都会把 OCR/解析结果落到本地 `.md`。
- 没有版本戳、没有失效机制、没有远端同步。
- 参考：
  - [pipeline.py](/home/cqy/worktrees/highThinking/highThinkingQA/ingest/pipeline.py#L43)
  - [pipeline.py](/home/cqy/worktrees/highThinking/highThinkingQA/ingest/pipeline.py#L89)

### Low

1. `summary` 和 `reference preview` 基本不是缓存系统
- 它们主要是实时计算。
- MinIO 只参与 PDF 是否存在、PDF 下载/物化，不参与结果缓存。
- 这不违背“MinIO 优先存储”的目标，但会造成重复计算。
- 参考：
  - [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/service.py#L187)
  - [reference_preview.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/reference_preview.py#L138)
  - [reference_preview.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/documents/reference_preview.py#L121)

2. `fastQA` 配置里定义了多类 JSON/summary/cache 目录，但活跃实现很少
- 当前明确在用的是 `topic_index_path`。
- `json/`、`json_normalized/`、`json_summary/`、`translation_cache/` 等目录更多像历史契约或预留配置。
- 参考：
  - [config.py](/home/cqy/worktrees/highThinking/fastQA/app/core/config.py#L207)
  - [config.py](/home/cqy/worktrees/highThinking/fastQA/app/core/config.py#L214)
  - [context_loading.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/context_loading.py#L14)

## 场景矩阵

| 场景 | 当前主路径 | 是否 MinIO 优先 | 结论 |
| --- | --- | --- | --- |
| 上传文件保存 | 先落本地，再镜像对象存储；镜像失败则拒绝 | 部分是 | 接近目标，MinIO 成功是持久化前提 |
| 上传文件下载 | 优先走 `storage_ref=minio://...`，失败才退本地 | 是 | MinIO-first |
| `papers` 存在性检查 | 优先查 MinIO，再看本地 | 是 | MinIO-first for existence |
| `papers` 实际阅读 | 先物化到本地，再由本地文件服务 | 否 | 远端权威，本地执行副本 |
| `public-service` 聊天 JSON 读取 | 本地先读，缺失才回拉远端 | 否 | local-first |
| `public-service` 聊天 JSON 写入 | 本地先写，再上传远端，失败后 outbox | 否 | local-first write + remote mirror |
| `highThinkingQA` 聊天 JSON 读取 | 先尝试远端回拉覆盖，再读本地 | 部分是 | remote-first repair，但不统一 |
| `highThinkingQA` 聊天 JSON 写入 | 本地先写，再上传远端 | 否 | local-first write |
| `public-service` 翻译缓存 | 本地 JSON + MinIO JSON 合并 | 否 | dual-source merge |
| `highThinkingQA` 翻译缓存 | 仅进程内 dict | 否 | memory-only |
| PDF summary | 每次实时计算 | 不适用 | 无结果缓存 |
| reference preview | 每次实时查询 | 不适用 | 无结果缓存 |
| 解析 Markdown 缓存 | 本地 `.md` | 否 | local-only |
| `fastQA` 上传文件执行 | 依赖 `local_path` | 否 | 不满足目标 |

## 逐项审查

### 1. 上传文件

`public-service` 上传时先把文件保存到本地上传目录，然后立即镜像到对象存储；如果镜像失败，会清理本地文件并直接拒绝请求。这说明它虽然不是“先写 MinIO 再写本地”，但在持久化语义上已经把 MinIO 成功作为硬条件。

参考：
- [api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/uploads/api.py#L152)
- [api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/uploads/api.py#L177)
- [api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/uploads/api.py#L193)
- [api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/uploads/api.py#L275)

判断：
- 可视为“本地执行副本 + MinIO 持久化硬约束”
- 这是当前最接近目标的路径之一

### 2. 上传文件下载 / 查看原文件

下载解析逻辑会先看 `storage_ref`。如果是 `minio://...`，优先生成直链或代理下载；只有对象存储路径不可用时，才回退到 `local://...` 或 `local_path`。这一条是明确的 MinIO-first。

参考：
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L185)
- [file_delivery_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/storage/file_delivery_service.py#L44)

判断：
- 下载链路已经符合“MinIO 优先，本地兜底”

### 3. `papers`

`papers` 相关逻辑普遍是：

- 存在性判断优先查 MinIO
- 若本地已有，则直接用本地副本
- 若本地没有，则从 MinIO 拉取到本地
- 后续 PDF 阅读、总结、句子提取都基于本地文件执行

`public-service` 是这样，`fastQA` 也是这样，`highThinkingQA` 也是这样。

参考：
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L75)
- [storage/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/storage/service.py#L88)
- [paper_storage.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/paper_storage.py#L150)
- [paper_storage.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/paper_storage.py#L169)
- [paper_storage.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/storage/paper_storage.py#L56)
- [paper_storage.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/storage/paper_storage.py#L79)

判断：
- `papers` 不是纯本地优先
- 但也不是“远端直接读”
- 更准确的说法是：MinIO 是远端权威，本地是执行态物化副本

### 4. 聊天记录 JSON

#### `public-service`

读取：
- 先读本地 JSON
- 本地不存在或损坏时，才尝试从远端下载恢复

写入：
- 先原子写本地 JSON
- 再上传远端
- 上传失败则保留本地副本，并交由 outbox 异步补偿

参考：
- [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L164)
- [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L182)
- [json_store.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/json_store.py#L254)
- [outbox_worker.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/outbox_worker.py#L194)

判断：
- 这是标准的 local-first read / local-first write + remote mirror
- 不符合“MinIO 主存储，本地仅保险”的严格目标

#### `highThinkingQA`

读取：
- 每次先尝试把远端对象同步到本地临时文件
- 如果远端内容与本地不同，会优先远端覆盖本地

写入：
- 仍然是先写本地，再镜像远端

参考：
- [chat_json_store.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/chat_json_store.py#L107)
- [chat_json_store.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/chat_json_store.py#L122)

判断：
- 比 `public-service` 更偏向远端修正
- 但仍然不是纯远端主写模型
- 更严重的问题是两套服务对同类数据的策略并不一致

### 5. 翻译缓存

#### `public-service`

翻译缓存是一个本地 `translations.json` 加远端 MinIO `translations.json` 的合并系统：

- 读启动时：本地 + 远端合并
- 定时刷新：远端合并到内存并回写本地
- 保存时：本地 + 远端 + 内存三方合并，再写本地，再上传 MinIO

参考：
- [translation_cache_impl.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/translation_cache_impl.py#L117)
- [translation_cache_impl.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/translation_cache_impl.py#L128)
- [translation_cache_impl.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/translation_cache_impl.py#L219)
- [translation_cache_impl.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/translation_cache_impl.py#L250)

判断：
- 这是 dual-source merge，不是 MinIO-first
- 多实例下有最后写入覆盖风险

#### `highThinkingQA`

仅使用进程内字典缓存翻译结果，不落盘，也不进 MinIO。

参考：
- [documents_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/documents_service.py#L26)
- [documents_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/documents_service.py#L113)

判断：
- 完全不符合 MinIO-first
- 甚至不属于持久化缓存

### 6. Summary / Reference Preview

#### Summary

`public-service` 和 `highThinkingQA` 的 PDF 总结都属于现场计算：

- 确保本地 PDF 可读
- 抽正文
- 调 LLM
- 直接返回结果

没有发现专门的总结结果缓存。

参考：
- [service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/service.py#L187)
- [documents_service.py](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/documents_service.py#L179)

#### Reference Preview

`reference preview` 同样以实时查询为主：

- 查图数据库/向量库元数据
- 同步判断 `pdf_exists`
- 返回预览信息

没有发现预览结果缓存文件。

参考：
- [reference_preview.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/reference_preview.py#L138)
- [reference_preview.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/documents/reference_preview.py#L121)

判断：
- 它们的主要问题不是“MinIO 不优先”
- 而是没有结果缓存，重复请求成本高

### 7. 解析缓存 / 派生文件

`highThinkingQA/ingest` 的 OCR 解析结果会直接写到本地 `parsed_markdown/*.md`，用于 `skip_parsed` 场景直接复用。本质上是纯本地缓存。

参考：
- [pipeline.py](/home/cqy/worktrees/highThinking/highThinkingQA/ingest/pipeline.py#L43)
- [pipeline.py](/home/cqy/worktrees/highThinking/highThinkingQA/ingest/pipeline.py#L51)
- [pipeline.py](/home/cqy/worktrees/highThinking/highThinkingQA/ingest/pipeline.py#L89)

判断：
- 纯本地缓存
- 没有 MinIO
- 没有失效控制

### 8. `fastQA` 的上传文件执行链

这是当前最需要明确标红的地方。

普通文件问答虽然已经接线，但执行层仍然不是“从 `storage_ref` 物化文件再执行”，而是直接从 `local_path` 拿文件：

- PDF 分支先从 `execution_files` 中挑 PDF，再取 `local_path`
- 表格分支 `load_workbook()` 明确要求 `local_path`
- 这意味着一旦执行节点没有共享到该本地路径，问答就断

参考：
- [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L221)
- [file_routes.py](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L233)
- [workbook_loader.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py#L186)

判断：
- 对上传文件执行而言，当前并不是 MinIO-first
- 这是文件问答分布式化的核心阻塞点

### 9. 资源根路径契约

仓库 `resource/README.md` 约定了：

- `resource/state/` 放可变持久态
- `resource/runtime/` 放 pid/log/temp

`fastQA` 和 `highThinkingQA` 新配置整体已经在往这个约定靠。

但 `public-service` 仍然有明显分裂：

- 数据根默认是 `/tmp/public-service`
- gunicorn runtime 写在项目内 `.runtime/`

参考：
- [README.md](/home/cqy/worktrees/highThinking/resource/README.md#L9)
- [config.py](/home/cqy/worktrees/highThinking/public-service/backend/app/core/config.py#L14)
- [start_gunicorn.sh](/home/cqy/worktrees/highThinking/public-service/scripts/start_gunicorn.sh#L6)

判断：
- 路径契约仍未完全统一
- 这会影响运维认知和跨环境部署一致性

## 最终判断

如果标准是：

> 上传文件、论文 PDF、聊天 JSON、各种缓存都应以 MinIO/对象存储为主来源，本地只作执行时副本、临时缓存或兜底保险。

那么当前系统只做到了部分满足。

已经做得比较对的部分：

- 上传文件持久化
- 上传文件下载
- `papers` 的远端存在性检查和回源

仍然明显不满足的部分：

- `fastQA` 上传文件执行仍靠 `local_path`
- `public-service` 聊天 JSON 是 local-first
- `public-service` translation cache 是双端 merge，不是远端权威
- `highThinkingQA` translation cache 和 ingest cache 基本都是本地/内存优先

一句话判断：

> 现在的对象存储已经接入了“上传、下载、papers 回源”这些主路径，但系统整体还没有统一成“MinIO 是主存储，本地只是保险副本”的单一存储语义。最大的缺口仍然是聊天 JSON 和 fastQA 上传文件执行链。
