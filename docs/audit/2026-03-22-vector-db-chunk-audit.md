# 2026-03-22 向量数据库与 Chunk 元数据细粒度审查

## 审查目标
- 当前使用的向量数据库是什么
- 每条检索结果中的 chunk 是否保存了原文片段文本
- 是否保存了 DOI / 标题 / section / 页码 / 段号 / chunk 序号 / source file 等定位信息
- 当前系统里“精确定位到原文位置”的能力来自哪里

## 结论先行
- 当前系统主用的向量库是 **Chroma**。
- Chroma 的 `documents` 字段里**直接保存 chunk 文本本身**；SQLite 中可见为 `chroma:document` 元数据镜像。
- 但不同子系统的 metadata schema 不同，且**普遍缺少精确位置字段**。
- 旧版 / 新版 `highThinking` 这套向量 chunk 主要保存：
  - `doi`
  - `title`
  - `section_name`
  - `chunk_index`
  - `total_chunks`
  - `token_count`
- **没有看到页码、段号、原 PDF 页内偏移、章节层级树、源文件路径等更强定位信息。**
- `fastQA` 主向量库 schema 又是另一套，主要保存：
  - `doi`
  - `title`
  - `source_file`
  - `chunk_id`
  - `data_quality`
- 这套也**没有 page / paragraph / section_name 等精确位置字段**。
- `fastQA` 的 MD 扩展库更接近“Markdown 片段库”，样本中有：
  - `document_name`
  - `filename`
  - `chunk_id`
  - `is_full_document`
  - `chroma:document`
- 代码里允许读取 `page` / `source_doi` 等字段，但当前抽样未看到这些字段普遍存在。
- 所以当前“原文片段定位”主要是：
  - `highThinking` 依赖 `section_name + chunk_index`
  - `fastQA` 主检索依赖 `doi/source_file/chunk_id`
  - 真正到页码级别的证据，更多来自 **Stage3/文件链单独重新读取 PDF**，而不是来自向量库元数据本身。

---

## 一、当前向量数据库类型

### A. highThinking / highThinkingQA
核心证据：
- `ingest/vector_store.py`
- `highThinkingQA/ingest/vector_store.py`
- `highThinkingQA/config.shared.env`
- `resource/config/services/highThinkingQA/config.shared.env`
- 实际文件：`vectordb/chroma.sqlite3`

实现明确使用：
- `chromadb.PersistentClient`
- `get_or_create_collection(...)`
- collection name: `lfp_papers`

配置与路径：
- 旧版默认 `CHROMA_PERSIST_DIR=vectordb`
- 新版 service 配置指向 `../../../../vectordb`

结论：
- **highThinking 与 highThinkingQA 使用同一类 Chroma 持久化向量库。**

### B. fastQA
核心证据：
- `resource/config/services/fastQA/config.shared.env`
- 实际文件：
  - `resource/fastqa/vector_database/chroma.sqlite3`
  - `resource/fastqa/vector_database_md/chroma.sqlite3`

说明：
- `fastQA` 不是单一向量库，而是至少有：
  - 主知识库向量库 `vector_database`
  - MD 扩展向量库 `vector_database_md`
  - 另外还有 `vector_database_pdf`、community/topic index 等外围资源

---

## 二、highThinking / highThinkingQA 的 chunk 存储结构

## A. 写入逻辑
核心证据：
- `ingest/chunker.py`
- `highThinkingQA/ingest/chunker.py`
- `ingest/vector_store.py`
- `highThinkingQA/ingest/vector_store.py`

`Chunk` dataclass 字段：
- `text`
- `doi`
- `title`
- `section_name`
- `chunk_index`
- `total_chunks`
- `token_count`

`Chunk.to_metadata()` 输出：
- `doi`
- `title`
- `section_name`
- `chunk_index`
- `total_chunks`
- `token_count`

写入 Chroma 时：
- `documents.append(chunk.text)`
- `metadatas.append(chunk.to_metadata())`
- `id = {doi}__chunk_{chunk_index}`

结论：
- **原文片段文本本身直接保存在 Chroma documents。**
- metadata 只保存“轻量定位信息”。

## B. SQLite 实际抽样结果
核心证据：`vectordb/chroma.sqlite3`

实际 metadata keys 统计：
- `chroma:document`
- `chunk_index`
- `doi`
- `section_name`
- `title`
- `token_count`
- `total_chunks`

抽样 row（id=1）显示：
- `doi = 10.1002/adem.201801281`
- `section_name = Preamble`
- `chunk_index = 0`
- `token_count = 1742`
- `total_chunks = 4`
- `chroma:document` 中能看到实际正文文本

说明：
- SQLite 中的 `embedding_metadata` 把文档文本镜像成 `chroma:document`
- chunk 的正文不是“引用到外部文件”，而是实际存进了向量库

## C. highThinking 检索时实际读取哪些字段
核心证据：
- `retriever/vector_retriever.py`
- `highThinkingQA/retriever/vector_retriever.py`

`RetrievedChunk` 只暴露：
- `text`
- `doi`
- `title`
- `section_name`
- `chunk_index`
- `distance`

没有：
- `page`
- `paragraph_index`
- `heading_path`
- `source_file`
- `pdf_offset`

结论：
- **highThinking 问答链拿到的是“文本片段 + section 名 + chunk 序号”，不是页码级引用。**

---

## 三、fastQA 主向量库的 chunk 结构

## A. SQLite 实际抽样结果
核心证据：`resource/fastqa/vector_database/chroma.sqlite3`

实际 metadata keys：
- `chroma:document`
- `chunk_id`
- `data_quality`
- `doi`
- `source_file`
- `title`

抽样 row（id=1）显示：
- `doi = 10.1002_aenm.202101712`
- `chunk_id = 0`
- `data_quality = high`
- `source_file = 10.1002_aenm.202101712_embedding.json`
- `title = 10.1002_aenm.202101712`
- `chroma:document` 中保存 chunk 文本

## B. 这套 schema 的特点
特点：
- 保存了来源文件名 `source_file`
- 保存了一个 `chunk_id`
- 保存了数据质量标签 `data_quality`
- **没有看到 `page` / `section_name` / `paragraph` / `total_chunks`**

这说明 fastQA 主向量库更像：
- 面向检索召回的“语料片段库”
- 但不是强结构化的“论文位置索引”

## C. fastQA 主链如何消费这些 metadata
从代码检索结果看：
- 文件链/混合链经常只取 `doi`、`title`、有时取 `section`
- generation-driven 后处理会消费 `metadatas`
- `reference_alignment.py`、`doi_inserter.py`、`rerank_service.py` 等模块都把 metadata 当成轻量辅助信息，而不是可靠定位坐标

结论：
- **fastQA 主向量库的 metadata 不足以独立支持“精确到第几页/第几段”的原文定位。**

---

## 四、fastQA MD 扩展库的 chunk 结构

## A. SQLite 抽样结果
核心证据：`resource/fastqa/vector_database_md/chroma.sqlite3`

抽样 `id=1` 的 metadata keys：
- `chroma:document`
- `chunk_id`
- `document_name`
- `filename`
- `is_full_document`

示例值：
- `document_name = 10.1016_j.apenergy.2016.01.096`
- `filename = 10.1016_j.apenergy.2016.01.096.md`
- `is_full_document = False`

## B. 代码期望 vs 实际抽样
核心证据：`fastQA/app/modules/generation_pipeline/md_expansion.py`

代码支持读取：
- `doi`
- `DOI`
- `source_doi`
- `document_name`
- `page`
- `chunk_id`

但当前实际抽样至少说明：
- `document_name` / `filename` 确实存在
- `chunk_id` 存在
- `page`、`doi`、`source_doi` 在当前样本上**没有直接出现**

因此当前更准确的说法是：
- **MD 扩展库代码层允许更丰富字段，但当前样本数据至少有一部分是在依赖 `document_name/filename` 反推 DOI，页码字段不稳定甚至可能缺失。**

## C. 对 Stage2.5 的直接影响
`md_expansion.py` 最终产出给后续链路的 chunk 结构里，会补成：
- `doi`
- `text`
- `page`
- `chunk_id`
- `distance`
- `source`

但其中的 `page` 可能只是：
- 来自 metadata 的真实页码
- 或者没有时默认 `0`

所以：
- **fastQA Stage2.5 的 MD 证据能提供补充文本，但未必能稳定提供真实页码。**

---

## 五、是否存储了“这个 chunk 在原文中的位置”

## A. highThinking / highThinkingQA
已确认有：
- `section_name`
- `chunk_index`
- `total_chunks`

未确认有：
- 页码
- 段号
- heading path
- 原 PDF 偏移
- source markdown file path

结论：
- **有弱位置描述，没有强位置坐标。**

## B. fastQA 主向量库
已确认有：
- `chunk_id`
- `source_file`
- `doi`

未确认有：
- 页码
- section 名
- paragraph index
- 文内 offset

结论：
- **比 highThinking 还弱，更偏“来源文件 + chunk 编号”而不是原文位置。**

## C. fastQA MD 扩展库
已确认稳定看到：
- `document_name`
- `filename`
- `chunk_id`
- `is_full_document`

代码支持但当前样本未证实稳定存在：
- `page`
- `doi`
- `source_doi`

结论：
- **位置字段不稳定，不能把它当成严格页码索引。**

---

## 六、当前系统“精确原文定位”真正依赖什么

### 1. highThinking
不是依赖向量库页码，而是依赖：
- 检索拿回 `text + doi + section_name + chunk_index`
- checker/synthesizer 做引用一致性校验
- 最终引用更像“DOI + 文段匹配”，不是“第 N 页第 M 段”

### 2. fastQA
精确证据更多来自单独 PDF 读取链：
- `pdf_pipeline.py` 会重新打开 PDF
- 逐页提取文本
- 生成 paragraph chunk，并显式带 `page`
- `reference_alignment.py` 会消费这些 PDF chunks 做 DOI 对齐和证据格式化

也就是说：
- **fastQA 想拿到页码级证据，主要不是靠主向量库 metadata，而是靠 Stage3 临时重新解析 PDF。**

---

## 七、对用户问题的直接回答

### 问题：检索到的每一条 chunks，里面是否存在这个 chunk 本身？
- **是。** 当前 Chroma `documents` / `chroma:document` 中直接保存了该 chunk 的文本片段。

### 问题：是否存储了这个片段的位置信息，比如在第几段、哪个章节等等？
- **部分有，但整体不完整。**
- `highThinking`：有 `section_name` 和 `chunk_index`，没有稳定页码/段号。
- `fastQA` 主向量库：只有 `doi/source_file/chunk_id/title/data_quality`，位置信息更弱。
- `fastQA` MD 库：代码支持 `page` 等字段，但当前抽样未证明稳定存在，至少有些数据是靠 `document_name/filename` 反推来源。

### 最关键的实话
- 当前向量库更像“可召回的文本片段库”，不是“高精度原文位置索引库”。
- 如果目标是稳定支持“第几页、第几段、哪个章节”的审计级引用，当前 schema 还不够。

---

## 八、风险与建议
- [high] 当前 chunk metadata 普遍缺少强位置坐标，难以做稳定的页码级可追踪引用
- [medium] fastQA 主库与 highThinking 主库 schema 不一致，增加跨模式统一引用/展示的难度
- [medium] fastQA MD 库代码期望字段与实际样本字段存在落差，容易出现 `page=0`、DOI 反推等弱化行为
- [medium] 当前“查看原文 / 句子对齐 / DOI 插入”的可靠性更多依赖后续 PDF 重解析，而不是向量库本身
