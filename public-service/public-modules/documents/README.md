# documents 细拆索引

对应代码：
- `backend/app/modules/documents/api.py`
- `backend/app/modules/documents/service.py`
- `backend/app/modules/documents/reference_preview.py`
- `backend/app/modules/documents/translation_service.py`
- `backend/app/modules/documents/translator.py`
- `backend/app/modules/documents/cache.py`
- `backend/app/modules/documents/translation_cache_impl.py`
- `backend/app/modules/documents/schemas.py`
- `backend/app/modules/storage/service.py`
- `backend/app/modules/quota/deps.py`
- `frontend-vue/src/api/literature.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/components/PdfReader.vue`
- `frontend-vue/src/features/references/composables/useReferenceInspector.js`

本目录把 `documents` 再拆成 5 个视角：

- `01-api-auth-and-quota.md`
  说明接口、鉴权、quota、HTTP 语义和 route contract。
- `02-pdf-asset-access-and-summary.md`
  说明 papers 目录、PDF 访问、文本提取、HEAD/GET 行为和摘要流程。
- `03-translation-and-cache.md`
  说明翻译服务、translator、MinIO+本地缓存、失败语义和返回结构。
- `04-literature-content-and-reference-preview.md`
  说明文献详情、引用预览、DOI 归一化、graph/chroma fallback 和运行时依赖。
- `05-frontend-and-compat-notes.md`
  说明前端调用、token 透传、字段偏差和兼容问题。

总体判断：
- 这是公共文档资产子系统，不只是 PDF 下载接口。
- 但它同时耦合 storage、qa_pdf、runtime agent 和 OpenAI-compatible provider。
- 其中 `translation` 和 `reference_preview` 都是值得单独抽离的子能力。

当前已确认问题：
- `view_pdf` 看起来是 optional auth，实际上仍被 quota 依赖强制要求登录。
- `reference_preview` 的 POST body 字段与前端调用不一致。
- 工具类接口的错误契约风格在模块内部并不统一。
