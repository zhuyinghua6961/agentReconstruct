# documents 文献详情与引用预览

对应代码：
- `backend/app/modules/documents/service.py`
- `backend/app/modules/documents/reference_preview.py`
- `backend/app/modules/storage/service.py`
- `frontend-vue/src/features/references/composables/useReferenceInspector.js`

## 1. 这两块能力并不纯文档化

虽然接口挂在 `documents` 下，但：
- `literature_content()` 依赖 runtime agent 的 graph / semantic_expert
- `reference_preview()` 也依赖 agent 的 graph / Chroma 元数据

所以它们更像：
- 文档资产的元数据查询层
- 而不是独立于 QA runtime 的纯文档服务

## 2. `literature_content()` 的真实查询顺序

顺序是：

1. 参数 `doi` 为空 -> 直接返回错误 JSON，状态码 `200`
2. agent 不存在 -> 错误 JSON，`200`
3. 先查 `agent.graph`
4. graph 没结果时，再查 `agent.semantic_expert.collection`
5. 还没有 -> `未找到该文献`，`200`

所以它不是严格的“找不到就 404”接口。

## 3. graph 查询的条件并不严格

graph 查询语句是：
- `WHERE n.material_name CONTAINS $doi`

而不是严格只查 `n.doi = $doi`。

这意味着：
- 命中条件相对宽松
- 在图谱侧更像模糊匹配

如果查到 graph 结果，返回内容来自 node 属性并经过 `format_material_content()` 格式化成 HTML 片段。

## 4. graph 命中和向量库命中的返回结构不完全一致

### 4.1 graph 命中

返回：
- `title`
- `authors`
- `journal`
- `publication_date`
- `abstract`
- `content`

其中 `content` 是 HTML 格式的分组内容块。

### 4.2 向量库命中

返回：
- `title`
- `authors`
- `journal`
- `publication_date`
- `abstract`
- `content`

但这里的 `content` 是文档原文文本，不是 HTML 格式块。

也就是说：
- 相同字段名下，返回内容形态可能完全不同

这是一个很容易被前端忽略的事实。

## 5. `reference_preview()` 的职责更稳定

它不返回全文内容，而是返回一批 DOI 的最小预览信息。

返回结构：
- `items`
- `count`
- `requested_count`
- `max_items`
- `truncated`

每个 item 至少包含：
- `doi`
- `title`
- `journal`
- `publication_date`
- `source`
- `pdf_exists`
- `pdf_url`

## 6. DOI 归一化规则

`reference_preview.py` 先做：
- 从 `dois_text` 按逗号拆分
- 合并 `doi_list`
- 去空白
- 按输入顺序去重
- 截断到 `max_items`

默认：
- `30`

上限：
- `100`

因此：
- 不会重排
- 不会做复杂 DOI 标准化修复
- 只做轻量去重和截断

## 7. 每个 DOI 的元数据查询顺序

对每个 DOI：

1. 先 `query_graph_reference_metadata()`
2. graph 没结果再 `query_chroma_reference_metadata()`
3. 最后再拼：
   - `pdf_exists`
   - `pdf_url`

source 会标成：
- `neo4j`
- `chromadb`
- 或默认 `unknown`

## 8. graph 与 chroma 的查询条件

### 8.1 graph

条件是：
- `n.material_name CONTAINS $doi OR n.doi = $doi`

比 `literature_content()` 的 graph 条件更宽一点。

### 8.2 chroma

调用：
- `collection.get(where={"doi": doi})`

这里要求向量库元数据里显式有 DOI 字段。

## 9. `pdf_exists` 的计算不是纯本地判断

`build_reference_preview_item()` 会调用：
- `storage_service.paper_exists()`

而 paper_exists 在 MinIO backend 下会：
- 先看对象存储
- 再 fallback 本地文件

所以 `pdf_exists=true` 的语义是：
- 平台认为此 DOI 的 PDF 可用
- 不一定当前本地已经有副本

## 10. `pdf_url` 的生成规则

`build_pdf_url()` 会把 DOI 按 `/` 分段编码，拼成：
- `/api/v1/view_pdf/<encoded doi path>`

这和 `frontend-vue/src/services/api.js` 里直接 `encodeURIComponent(doi)` 的做法不是同一策略。

当前前端实际上存在两套 URL 构造方式。

## 11. 这两块能力为什么还算“公共”

即使依赖 runtime agent，它们依然是公共能力，因为：
- 引用面板
- 文献详情侧边栏
- 预览下载入口
- 参考文献选择面

都可以复用这些接口。

但要强调：
- 它们不是纯 storage/document service
- 而是“文档资产 + 检索元数据”的混合层

## 12. 当前最需要注意的行为差异

- `literature_content()` 很多错误也返回 `200`
- graph 命中和向量库命中的 `content` 形态不同
- `reference_preview()` 对单个 DOI 缺失元数据很宽容，不会整体失败
- `pdf_exists` 表示“平台可获得”，不是“本地立即存在”

这些都会直接影响前端展示和调用方的错误处理方式。
