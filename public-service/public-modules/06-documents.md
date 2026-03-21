# documents 模块代码细读

模块路径：
- `backend/app/modules/documents/api.py`
- `backend/app/modules/documents/service.py`
- `backend/app/modules/documents/reference_preview.py`
- `backend/app/modules/documents/translation_service.py`
- `backend/app/modules/documents/translator.py`
- `backend/app/modules/documents/cache.py`
- `backend/app/modules/documents/translation_cache_impl.py`
- `backend/app/modules/documents/schemas.py`

关联代码：
- `backend/app/modules/storage/service.py`
- `backend/app/modules/quota/deps.py`
- `backend/app/modules/qa_pdf/pdf_extractor.py`
- `frontend-vue/src/api/literature.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/components/PdfReader.vue`
- `frontend-vue/src/features/references/composables/useReferenceInspector.js`
- `frontend-vue/src/features/references/composables/useReferencePanelState.js`
- `backend/tests/test_documents.py`

模块定位：
- 明确属于公共能力
- 负责文档资产读取、PDF 查看、PDF 摘要、文本翻译、文献详情、引用预览
- 但内部同时耦合了 storage、quota、qa_pdf、runtime agent、OpenAI-compatible 翻译/摘要调用

## 1. 结论先说

`documents` 不是单一“文档下载模块”，而是一个文档资产子系统，内部至少包含四块能力：
- PDF 资产访问
- PDF 工具型处理
- 文本翻译与翻译缓存
- 文献元数据/引用预览

它属于公共能力没有问题，但实现上并未完全解耦：
- PDF 资产依赖 storage
- 摘要和翻译绕过统一 LLM runtime 直接调 OpenAI-compatible client
- 文献详情和引用预览依赖 runtime agent 的图谱/向量检索能力

## 2. 深拆文档索引

本次已把 `documents` 再细分为子文档，放在：
- `/home/cqy/worktrees/public-service/public-modules/documents/README.md`
- `/home/cqy/worktrees/public-service/public-modules/documents/01-api-auth-and-quota.md`
- `/home/cqy/worktrees/public-service/public-modules/documents/02-pdf-asset-access-and-summary.md`
- `/home/cqy/worktrees/public-service/public-modules/documents/03-translation-and-cache.md`
- `/home/cqy/worktrees/public-service/public-modules/documents/04-literature-content-and-reference-preview.md`
- `/home/cqy/worktrees/public-service/public-modules/documents/05-frontend-and-compat-notes.md`

这份 `06-documents.md` 保留为总览。

## 3. 当前最重要的代码事实

### 3.1 `view_pdf` 表面上是 optional auth，实际上仍然要求登录

`api.py` 里 `view_pdf()` 同时依赖：
- `get_optional_auth_context`
- `require_quota("file_view")`

而 `require_quota()` 内部依赖的是：
- `require_auth_context`

所以真实行为是：
- 没 token 仍会 401/403
- 不是公开匿名下载接口

前面的 optional auth 在这里不改变真实门槛。

### 3.2 `documents` 的错误语义并不统一

几个例子：

- `view_pdf` 真正找不到 PDF 会返回 `404`
- `summarize_pdf` 出错通常返回 `500/503/404`
- `translate` 参数错误是 `400`，全失败是 `502`
- `literature_content` 缺 DOI / agent 未初始化 / 文献没找到，很多都仍然返回 `200`
- `reference_preview` 即使找不到元数据也会返回正常项，只是字段为空

也就是说：
- 这个模块内的错误语义是分散的，不是统一风格

### 3.3 前端和后端的 `reference_preview` 契约存在实际偏差

后端 `ReferencePreviewRequest` 定义的是：
- `dois_text`
- `doi_list`
- `max_items`

但 `frontend-vue/src/api/literature.js` 当前 POST 的是：
- `{ doi: values, max_items }`

这意味着：
- 从这条前端调用看，POST body 字段名与后端 schema 不一致
- 后端会拿不到 `doi_list`
- 接口可能返回空 `items`

这是一个真实的契约偏差，不是推测。

## 4. 模块内部可分成哪几层

### 4.1 API 层

`api.py` 负责：
- 路由定义
- quota 依赖
- HEAD/GET 区分
- 从 request 里取 runtime.agent

### 4.2 DocumentsService

`service.py` 负责：
- `papers/` 根目录解析
- 调 storage 确保本地 PDF
- 调 qa_pdf 提取正文
- 直接调 OpenAI-compatible client 做摘要
- 把翻译委托给 translation_service
- 调 runtime agent 获取文献信息
- 调 reference_preview helper 组装预览

### 4.3 Translation 子系统

翻译子系统包括：
- `translation_service.py`
- `translator.py`
- `translation_cache_impl.py`
- `cache.py`

它本质上是：
- translator 单例
- 本地 + MinIO 双层缓存
- OpenAI-compatible provider

### 4.4 Reference preview 子系统

`reference_preview.py` 负责：
- DOI 标准化
- 数量裁剪
- 稳定顺序去重
- 图谱 / Chroma 元数据查询
- 拼 `pdf_exists` 与 `pdf_url`

## 5. 为什么它属于公共能力

- PDF 查看、可用性检查、全文提取、摘要、翻译、引用预览都不绑定某一种问答模式
- 后续不管是 ask_gateway、引用面板、PDF 阅读器还是独立工具页都可以复用

## 6. 当前边界上的几个问题

- 文档接口的鉴权策略不统一
- 工具能力有的走 quota finalize，有的完全开放
- 文献详情与引用预览依赖 runtime agent，不能算纯文档服务
- 摘要和翻译没有统一走平台 LLM runtime
- 前端调用面存在字段不对齐和 token 兼容差异

所以它已经是公共能力，但还不是“边界干净”的公共文档服务。

## 7. 当前已确认问题与迁移修复点

- `P2` `view_pdf()` 在接口签名上同时声明了 `get_optional_auth_context` 和 `require_quota("file_view")`。但 `require_quota()` 内部仍强依赖 `require_auth_context`，所以这条接口看起来像 optional auth，实际上仍要求登录。这是明确的接口语义误导。
- `P1` `reference_preview` 的 POST body 当前前后端不一致：
  - 后端 schema 接收 `dois_text / doi_list / max_items`
  - 前端 `frontend-vue/src/api/literature.js` 提交的是 `{ doi: values, max_items }`
- `P2` `literature_content` 等工具接口在多类业务失败场景下仍返回 `200 + payload error`，而 `view_pdf / summarize_pdf / translate` 又大量使用真正的 HTTP 状态码；同一模块内部契约风格不统一。
- 抽成独立公共后端前，至少需要先统一：
  - PDF 查看接口到底是不是必须登录
  - reference preview 的真实输入字段
  - 工具类接口是否继续维持 `200 + error payload`
