# QA 原文与表格 MinIO-only 运行时改造 Spec

## 文档状态

- 最后更新：2026-05-18
- 状态：spec only
- 范围：`fastQA`、`highThinkingQA`、`patent`、`gateway`、`public-service`
- 目标：把问答运行时需要读取的原文、上传文件、专利结构化原文和专利表格收敛到 MinIO 权威读取，不再把本地文件树作为数据源或兜底源

## 1. 背景

三个问答后端在回答过程中都会在某些阶段读取“原文”或“原文 + 表格”：

- `fastQA`
  - DOI / paper PDF 原文读取
  - 上传 PDF 文件问答
  - 上传表格文件问答
  - PDF + 表格 + KB 混合问答
- `highThinkingQA`
  - 活跃问答路径主要使用向量库 chunk，不在每轮问答中重读原文
  - 旧文档查看、下载、翻译等路径仍有本地物化读取
- `patent`
  - 专利 KB 回答会引用专利原文定位信息
  - 专利表格增强会加载本地归档目录中的 `*_tables.json`
  - 专利文件问答和表格问答仍依赖上传文件的 `local_path`

前置排查结论是：当前系统不是统一的 MinIO-only。更准确地说，部分链路已经 MinIO-first，部分链路是本地优先 + MinIO 兜底，部分链路仍是 local-only。

这份 spec 定义后续目标态：运行时读原文和表格时以 MinIO 对象为唯一权威。允许为了第三方解析库创建临时 scratch 文件，但临时文件不能成为数据源，也不能在 MinIO 失败时作为业务兜底。

## 2. 已完成的前置数据状态

本 spec 基于以下已完成的数据准备：

- MinIO API：`127.0.0.1:9101`
- MinIO bucket：`agentcode`
- `papers/`
  - 已发现 7,153 个 PDF 对象
- `uploads/`
  - 已发现 260 个对象
  - 其中 PDF 220 个，Excel/CSV 40 个
- `patent/originals/`
  - 已发现 14,006 个 manifest
  - bibliography、claims、description、fulltext PDF、figures 已存在
  - patent 表格补传已完成

专利表格补传结果：

- 本地专利归档目录：14,006 个
- 本地 `*_tables.json`：9,581 个
- 表格数量：16,267
- 行数：139,167
- 空表格文件：2,011
- JSON 解析失败：0
- MinIO `structured/tables.json`：9,581 个
- MinIO 表格对象总大小：32,440,440 bytes
- 补传后 dry-run：`uploaded_object_count=0`，`skipped_object_count=19162`，`failed_count=0`

注意：表格补传后的 MinIO JSON 是规范化 JSON，不保证和本地文件逐字节一致，但语义内容保持一致。后续运行时应以 MinIO 对象为准。

## 3. 目标

### 3.1 主目标

运行时凡是需要读取用户上传文件、paper PDF、专利结构化原文、专利全文 PDF、专利结构化表格，都必须从 MinIO 读取。

### 3.2 本地文件规则

允许的本地文件：

- 解析库要求文件路径时创建的临时文件
- 有明确生命周期的 runtime scratch/cache
- 由 MinIO 对象内容生成、并带有对象版本或 digest 校验的执行副本

不允许的本地文件：

- 作为原文权威数据源的 `local_path`
- MinIO 对象缺失时继续读取旧本地归档
- 上传文件 MinIO 读取失败时退回本地上传目录
- 专利表格 MinIO 缺失时退回 `*_tables.json`

### 3.3 用户可见目标

- 单机、本地开发、多实例部署中的问答结果读取语义一致
- 执行节点不需要提前拥有本地文件树
- MinIO 缺对象时暴露明确错误，而不是悄悄给出基于旧本地文件的答案
- 原文/表格引用中的版本与 MinIO manifest 的 `original_version` 对齐

## 4. 非目标

本 spec 不处理以下内容：

- 聊天 JSON 的 MinIO 主存储改造
- 翻译缓存、摘要缓存、解析 Markdown 缓存的主存储改造
- 向量库、Chroma、Neo4j、embedding index 的迁移
- deploy 目录、Docker 镜像、外部运维脚本改造
- MinIO bucket 重新规划或迁移到独立 bucket
- 修改现有 citation 展示样式

## 5. 术语

### 5.1 MinIO-only

业务读取只信任 MinIO 对象。MinIO 不可用、对象不存在、manifest 不合法时，业务返回明确错误或降级为“无该证据”，但不能从本地旧文件树补读。

### 5.2 Scratch file

从 MinIO bytes 生成的临时执行文件。它的存在是为了兼容需要文件路径的解析库。scratch file 不参与业务兜底，不是数据权威，不应被跨请求长期复用，除非带有对象 digest 或 `original_version` 校验。

### 5.3 `storage_ref`

上传文件和公共文件上下文中的对象存储引用。目标态中，执行文件必须携带有效的 `storage_ref`，典型格式：

```text
minio://agentcode/uploads/...
```

### 5.4 Patent original manifest

专利原文对象集的索引文件：

```text
patent/originals/{CANONICAL_PATENT_ID}/manifest.json
```

它是专利结构化原文、全文 PDF、附图、表格的唯一入口。

## 6. MinIO 对象契约

### 6.1 paper PDF

paper PDF 使用现有前缀：

```text
papers/{...}.pdf
```

运行时要求：

- DOI / paper 原文读取从 `papers/` 读取 bytes
- 如果解析库要求路径，可写 scratch file
- scratch file 缓存键必须由 bucket + object name + object stat 生成
- 本地 `papers` 目录不再作为 fallback

### 6.2 上传文件

上传文件使用现有 `storage_ref`：

```text
minio://agentcode/uploads/{...}
```

执行 payload 中每个文件至少需要：

```json
{
  "file_id": "123",
  "file_name": "example.xlsx",
  "file_type": "excel",
  "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "storage_ref": "minio://agentcode/uploads/example.xlsx",
  "size": 12345,
  "sha256": "optional"
}
```

目标态规则：

- `storage_ref` 是执行必需字段
- `local_path` 只允许作为历史元数据透传，不允许用于读取
- `storage_ref` 为空时，gateway 或执行后端必须拒绝该文件参与问答
- `storage_ref=local://...` 不符合 MinIO-only 运行时要求

### 6.3 专利原文

专利原文使用现有前缀：

```text
patent/originals/{CANONICAL_PATENT_ID}/manifest.json
patent/originals/{CANONICAL_PATENT_ID}/structured/bibliography.json
patent/originals/{CANONICAL_PATENT_ID}/structured/claims.json
patent/originals/{CANONICAL_PATENT_ID}/structured/description.json
patent/originals/{CANONICAL_PATENT_ID}/structured/tables.json
patent/originals/{CANONICAL_PATENT_ID}/fulltext/original.pdf
patent/originals/{CANONICAL_PATENT_ID}/figures/...
```

每个可查看或可增强的专利必须有 `manifest.json`。其中，只有“有结构化表格”的专利才必须声明表格对象；没有结构化表格的专利可以省略 `objects.structured.tables`，或显式设置 `availability.tables=false`。

```json
{
  "objects": {
    "structured": {
      "bibliography": "patent/originals/CN.../structured/bibliography.json",
      "claims": "patent/originals/CN.../structured/claims.json",
      "description": "patent/originals/CN.../structured/description.json",
      "tables": "patent/originals/CN.../structured/tables.json"
    }
  },
  "availability": {
    "bibliography": true,
    "claims": true,
    "description": true,
    "tables": true,
    "fulltext_pdf": true
  },
  "original_version": "..."
}
```

目标态规则：

- `patent` 加载专利表格时必须从 manifest 读取 `objects.structured.tables`
- manifest 缺失或非法时，专利原文查看返回“原文不可用”；专利 KB 检索仍可继续，但不能获得该专利的表格增强
- `availability.tables=false` 或 `objects.structured.tables` 缺失时，该专利视为“无结构化表格”
- `availability.tables=true` 但表格对象不存在或无法解析时，该专利视为“表格对象不可用”，记录诊断并跳过表格增强
- bibliography、claims、description、fulltext PDF、figures 同样以 manifest 为入口
- `original_version` 进入 cache key、引用元数据和诊断日志

## 7. 统一读取接口

新增或收敛一个对象读取抽象。第一阶段在各服务内实现即可，不要求抽公共包；命名可按服务习惯调整，但能力和错误语义应一致。

建议接口：

```python
class ObjectBytesReader:
    def read_bytes(self, storage_ref: str) -> bytes: ...
    def read_json(self, storage_ref: str) -> dict | list: ...
    def stat(self, storage_ref: str) -> ObjectStat: ...
    def materialize_temp(self, storage_ref: str, *, suffix: str) -> Path: ...
```

要求：

- `read_bytes()` 返回 MinIO 原始 bytes，不做文本转码
- `read_json()` 在 bytes 基础上按 UTF-8 解析 JSON
- `materialize_temp()` 只能由 MinIO bytes 生成临时文件
- 所有方法都应打出 object name、bucket、etag/size、trace id
- 不应接受裸 `local_path`

### 7.1 读 MinIO bytes 是否损失内容

不会。bytes 是对象存储中的原始字节序列。只要上传对象完整，读取 bytes 与读取同一文件的本地字节内容等价。

可能出现差异的点不在 bytes 读取本身，而在后续解析：

- PDF 解析器是否要求 seekable stream 或真实文件路径
- Excel 解析器是否依赖文件后缀判断格式
- CSV 编码识别是否从文件名或本地 locale 推断
- JSON 补传是否规范化了格式、空白和 key 顺序

解决方式：

- PDF/Excel 需要路径时，用 MinIO bytes 写 scratch file
- scratch file 保留原始后缀
- CSV 编码和分隔符用现有解析策略，不依赖本地路径
- JSON 结构化内容按语义字段比较，不按原始文本比较

## 8. 服务改造规格

### 8.1 gateway

gateway 是文件上下文权威。

目标改造：

- `execution_files` 必须包含 `storage_ref`
- 对 file-aware route，如果参与执行的文件缺少有效 `minio://` storage_ref，gateway 应返回协议错误或要求用户重新上传
- `local_path` 可以保留在模型中用于兼容，但不能作为“可执行性”判断条件
- 路由诊断中增加：
  - `missing_storage_ref_count`
  - `minio_storage_ref_count`
  - `local_only_file_count`

验收：

- 只有 `local_path`、没有 `storage_ref` 的上传文件不会进入 `fastQA` 或 `patent` 执行
- 有 `storage_ref`、没有本地文件的上传文件可以正常进入执行

### 8.2 public-service

public-service 继续作为上传文件元数据和专利原文查看服务的权威。

目标改造：

- 上传成功必须保证 MinIO 对象存在
- 对外返回文件上下文时，`storage_ref` 必须稳定携带
- 专利原文查看已经走 `PatentOriginalStore` + MinIO manifest，应补齐 `tables` 的 manifest schema 校验
- 原文查看返回中保留 `original_version`

非本次目标：

- 聊天 JSON local-first/outbox 改造
- 翻译缓存改造

### 8.3 fastQA

fastQA 是本次改造的主要执行侧。

当前风险：

- 上传文件执行链仍会优先使用可读 `local_path`
- MinIO 读取失败后可能保留空 `local_path` 并让下游按旧逻辑失败
- 表格加载器以路径为主
- DOI paper PDF 会物化到本地再读取

目标改造：

- 引入 MinIO-only 上传文件解析入口
- PDF QA 从 `storage_ref` 读取 bytes 或 scratch file
- tabular QA 从 `storage_ref` 读取 bytes，并使用 `BytesIO` 或 scratch file 交给 pandas/openpyxl
- hybrid QA 中 PDF、table、KB 的 source usage 必须记录 MinIO object
- DOI paper PDF 从 MinIO `papers/` 读取，不从本地 `papers` fallback

必要行为：

- `storage_ref` 缺失：执行前失败，错误类型为 protocol/file_context_error
- MinIO object missing：执行前失败，错误类型为 file_unavailable
- MinIO timeout：执行前失败或可重试，错误类型为 storage_unavailable
- 本地同名文件存在时也不能被读取

### 8.4 highThinkingQA

highThinkingQA 活跃问答路径不强制每轮加载原文，但文档查看和历史兼容路径仍应统一语义。

目标改造：

- paper 原文查看、下载、翻译相关读取从 MinIO bytes/scratch file 获取
- 本地 paper 目录仅作为 scratch/cache，不作为 fallback
- 旧接口若只提供本地路径，应返回明确不可执行错误

非目标：

- 改写 active thinking QA 的 Chroma chunk 检索
- 改写 highThinkingQA 聊天 JSON 存储模型

### 8.5 patent

patent 改造分两类：专利 KB 原文/表格，以及上传文件问答。

#### 8.5.1 专利 KB 原文/表格

当前风险：

- `archive_loader.load_tables()` 直接读取本地归档目录中的 `*_tables.json`
- 表格增强运行时依赖本地归档文件树

目标改造：

- 新增 `PatentOriginalMinioLoader`
- 通过 `patent/originals/{id}/manifest.json` 加载结构化对象
- `load_tables(canonical_patent_id)` 从 `objects.structured.tables` 读取
- 输出仍转换为现有 `PatentTableSupplement`
- manifest 缺失或非法时，`load_tables()` 返回空表格列表并记录 `original_manifest_unavailable`
- `availability.tables=false` 或 `objects.structured.tables` 缺失时，`load_tables()` 返回空表格列表并记录 `tables_unavailable`
- `availability.tables=true` 但 table 对象缺失、下载失败或 JSON 解析失败时，`load_tables()` 返回空表格列表并记录 `tables_object_unavailable`
- 所有上述情况都不能回退本地 `*_tables.json`

#### 8.5.2 专利上传文件问答

当前风险：

- 专利文件契约和 tabular service 会读取 `local_path`

目标改造：

- 上传 PDF 和表格均从 `storage_ref` 读取
- 表格 compare 和 schema profiler 支持 bytes/BytesIO 或 scratch file
- `local_path` 只作为历史字段，不作为执行输入

## 9. 错误处理规格

| 场景 | 目标行为 | 是否允许本地兜底 |
| --- | --- | --- |
| `storage_ref` 缺失 | 协议错误，提示重新上传或刷新文件元数据 | 否 |
| `storage_ref` scheme 非 `minio://` | 协议错误 | 否 |
| MinIO bucket/object 不存在 | 文件不可用，列出 file_id/object | 否 |
| MinIO 超时或连接失败 | 存储不可用，可重试 | 否 |
| 专利 original view 的 manifest 缺失或非法 | 专利原文不可用 | 否 |
| 专利 KB 表格增强的 manifest 缺失或非法 | 该专利无表格增强，记录诊断 | 否 |
| `availability.tables=false` 或 `objects.structured.tables` 缺失 | 该专利无结构化表格 | 否 |
| `availability.tables=true` 但表格对象缺失 | 表格对象不可用，跳过表格增强并记录诊断 | 否 |
| 表格 JSON 无法解析 | 表格对象损坏，跳过表格增强并记录诊断 | 否 |
| PDF/Excel 解析器要求路径 | 从 MinIO bytes 写 scratch file 后解析 | 不适用 |

## 10. 诊断与日志

每次读取原文/表格时记录：

- service：`fastQA` / `highThinkingQA` / `patent` / `public-service`
- trace id / conversation id / task id
- source family：`paper_pdf` / `upload_pdf` / `upload_table` / `patent_structured` / `patent_table`
- bucket
- object name
- object size
- etag 或 sha256
- manifest original_version
- 是否写入 scratch file
- scratch file path 只在 debug 日志中出现
- 禁止记录敏感 MinIO 密钥

关键指标：

- `qa_original_minio_read_total`
- `qa_original_minio_read_failed_total`
- `qa_original_local_fallback_attempt_total`
- `qa_original_scratch_materialize_total`
- `qa_original_storage_ref_missing_total`
- `patent_tables_minio_loaded_total`
- `patent_tables_minio_missing_total`

其中 `qa_original_local_fallback_attempt_total` 在目标态应长期为 0。

## 11. 缓存策略

允许缓存，但缓存不能改变数据权威：

- 缓存 key 必须包含 bucket + object name + object stat
- object stat 的首选版本键为 MinIO etag + size
- 如果 `sha256` 元数据已经存在，应把 sha256 也纳入缓存 key
- 如果 etag 不可用，则读取 bytes 后计算 sha256，并用 bucket + object name + size + sha256 作为 scratch cache key
- 专利结构化对象缓存 key 必须包含 `original_version`
- 上传文件 scratch cache 必须能被清理
- cache miss 后必须回源 MinIO
- cache hit 前必须确认版本匹配

不要求为历史上传文件补齐 sha256 字段。sha256 是可选增强，不是本轮迁移的前置条件。

不允许：

- 按文件名命中旧本地文件
- 按 `local_path` 命中执行文件
- MinIO 失败后使用过期 cache 当作成功结果，除非后续另有离线容灾 spec 明确允许
- 旧 local fallback 只允许作为显式 rollback code path，不属于目标态验收范围

## 12. 分阶段实施建议

### Phase 0：数据确认

已完成：

- MinIO 对象盘点
- patent table backfill
- backfill dry-run 验证

仍建议补一条自动化校验：

- 比较本地 `*_tables.json` 数量与 MinIO `structured/tables.json` 数量
- 抽样比较 JSON 语义等价

### Phase 1：统一 MinIO bytes reader

- 在各服务内建立最小读取抽象
- 支持 `read_bytes`、`read_json`、`materialize_temp`
- 单元测试使用 fake client，不访问真实 MinIO

### Phase 2：fastQA 上传文件执行改造

- PDF QA 改为 `storage_ref` 读取
- tabular QA 改为 `storage_ref` 读取
- hybrid QA 共享同一个文件读取入口
- 删除或旁路 local-first materializer

### Phase 3：patent 专利 KB 表格 loader 改造

- 新增 MinIO manifest loader
- 替换 `archive_loader.load_tables`
- 保持 `PatentTableSupplement` 下游接口不变
- 增加 manifest/table 缺失测试

### Phase 4：patent 上传文件执行改造

- file contract 要求 `storage_ref`
- tabular service 支持 MinIO bytes/scratch
- 本地路径存在但 MinIO 缺失时必须失败

### Phase 5：highThinkingQA 旧文档路径收敛

- paper/document view 从 MinIO 获取
- 本地文件只保留 scratch/cache 角色

### Phase 6：gateway/public-service 强约束

- gateway 拒绝 local-only execution files
- public-service 文件上下文确保返回 `storage_ref`
- 观察指标确认 local fallback attempt 为 0

## 13. 测试验收

### 13.1 单元测试

每个服务至少覆盖：

- `storage_ref` 存在且本地文件不存在，执行成功
- `storage_ref` 缺失且本地文件存在，执行失败
- MinIO object missing 且本地文件存在，执行失败
- MinIO bytes 与本地样本解析结果语义一致
- 解析器 path-only 时 scratch file 来自 MinIO bytes
- JSON table 规范化后语义等价

### 13.2 集成测试

建议场景：

- 上传 PDF 后删除本地上传目录，`fastQA` PDF QA 仍成功
- 上传 XLSX 后删除本地上传目录，`fastQA` tabular QA 仍成功
- 上传 PDF + XLSX 后删除本地上传目录，hybrid QA 仍成功
- patent KB 表格增强在没有本地归档目录时仍能加载 MinIO tables
- MinIO 删除某个 `structured/tables.json` 后，该专利只缺表格，不影响 claims/description 原文查看

### 13.3 回归测试

需要保留：

- 现有 file-aware routing contract 测试
- `public-service` patent original view 测试
- `patent` retrieval/table supplement 测试
- `fastQA` PDF/table/hybrid QA 测试
- 不访问真实外部 LLM 的离线测试隔离

## 14. 发布与回滚

建议引入严格模式开关用于灰度：

```text
QA_ORIGINAL_MINIO_ONLY=true
FASTQA_UPLOAD_MINIO_ONLY=true
PATENT_ORIGINAL_MINIO_ONLY=true
HIGHTHINKING_ORIGINAL_MINIO_ONLY=true
```

灰度策略：

1. dev 环境默认开启 strict MinIO-only
2. staging 开启 strict MinIO-only，并观察缺对象指标
3. production 开启 strict MinIO-only，同时打开 shadow 诊断，只记录“如果旧路径存在是否可 fallback”，但不实际 fallback
4. 指标稳定后关闭 production shadow 诊断，保留 strict MinIO-only

回滚策略：

- 功能开关可以恢复旧运行时代码路径
- 但回滚期间必须记录所有本地读取，并标记为 legacy fallback
- 回滚不应删除已补传的 MinIO 对象

最终状态：

- strict MinIO-only 成为默认行为
- legacy local fallback 代码删除或只保留在显式迁移工具中
- legacy local fallback 不能作为长期双路径运行方案

## 15. 验收标准

本 spec 视为完成的标准：

- 三个问答后端运行时不再把 `local_path` 作为原文/表格读取入口
- 上传文件只有 MinIO 对象存在时才能参与问答
- patent 表格增强从 MinIO `structured/tables.json` 加载
- 专利原文查看、引用 metadata、cache key 使用 manifest `original_version`
- 本地归档目录移走后，MinIO 数据完整的问答路径仍可运行
- MinIO 对象缺失时，不会悄悄读本地同名文件
- 自动化测试覆盖 MinIO-only 成功路径和本地兜底禁止路径

## 16. 已定实施默认值

1. strict MinIO-only 在 dev 和 staging 默认开启。
2. production 目标态同样开启 strict MinIO-only；灰度期只允许 shadow 诊断，不允许实际本地兜底。
3. path-only 解析库的 scratch file 默认放在各服务现有 runtime root 下的 `object-cache/`，实施时不新增跨服务共享目录。
4. `availability.tables=false` 与缺少 `objects.structured.tables` 统一解释为“无结构化表格”。
5. 上传文件不要求补齐 sha256；scratch/cache 默认使用 MinIO etag + size，etag 不可用时再计算 bytes sha256。
