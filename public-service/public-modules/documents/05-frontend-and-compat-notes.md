# documents 前端调用面与兼容性备注

对应代码：
- `frontend-vue/src/api/literature.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/components/PdfReader.vue`
- `frontend-vue/src/features/references/composables/useReferenceInspector.js`
- `frontend-vue/src/features/references/composables/useReferencePanelState.js`
- `frontend-vue/src/api/http.js`

## 1. 前端对 documents 的消费并不统一

至少有两套调用面：

- `src/api/literature.js`
  - 更偏新的轻量封装
- `src/services/api.js`
  - 更偏 UI 适配层

这会带来：
- token 读取方式差异
- URL 构造方式差异
- payload 兼容方式差异

## 2. `view_pdf` URL 构造存在两套策略

### 2.1 `api/literature.js`

`buildPdfViewUrl(doi)`：
- 逐段 encode DOI path
- 如果有 token，则把 token 挂到 query 上

### 2.2 `services/api.js`

`viewPdf(doi)`：
- 整个 DOI 一次性 `encodeURIComponent`
- 不自动拼 token

另外：
- `PdfReader.vue`
- `useReferencePanelState.js`

又各自做了一层 token 追加兼容。

这说明前端对 `view_pdf` 的访问方式目前并未完全统一。

## 3. 为什么前端会把 token 放到 query 里

原因很现实：
- PDF 预览通常是浏览器直接加载 URL 或 iframe/object
- 这种场景不容易附带自定义 Authorization header

所以前端会把 token 附到：
- `?token=<...>`

而后端 `get_bearer_token()` 也确实支持 query token。

## 4. `translate` 的前端已经做了双层兼容

`services/api.js` 在解析翻译返回时会同时兼容：
- 顶层 `translations`
- `data.translations`

并重新规范成：
- `success`
- `translations`
- `count`
- `data.translations`

这说明前端已经显式适配了 documents 翻译的“双层返回”。

## 5. `reference_preview` 的前端 POST 字段目前和后端 schema 不一致

这是当前最值得写清楚的问题。

`frontend-vue/src/api/literature.js` 现在 POST：
- `{ doi: values, max_items: maxItems }`

但后端 `ReferencePreviewRequest` 实际接收：
- `dois_text`
- `doi_list`
- `max_items`

结果是：
- 前端发出去的 `doi` 字段不会映射到后端 schema
- 后端会把 `doi_list` 当空列表
- 很可能直接返回空 `items`

而 `useReferenceInspector.js` 又是实际在调用这条 API。

所以这不是死代码问题，而是真实契约偏差。

## 6. `getLiteratureContent()` 和 `getReferencePreview()` 的错误处理风格不同

前端 `api/http.js` 的 `getJson/postJson` 只会在：
- HTTP 非 2xx

时抛错。

但 documents 后端里：
- `literature_content()` 业务失败很多仍返回 `200`
- `reference_preview()` 即使没数据也返回 `200`

所以前端只能靠 payload 内容自行判断是否业务失败。

`useReferenceInspector.js` 也确实是这么做的：
- `payload?.error` 就抛异常

## 7. PdfReader 对 documents 的依赖很深

`PdfReader.vue` 会：
- `checkPdfAvailability(doi)`
- 构造 `view_pdf` URL
- 调 `api.translate([manualText])`
- 处理摘要/翻译展示

说明 documents 不只是被引用面板用，还直接服务 PDF 阅读器。

## 8. references 面板的调用链

调用链是：

- `useReferencePanelState`
  - 负责 URL 组装与 token 追加
- `useReferenceInspector`
  - 调 `getLiteratureContent`
  - 调 `getReferencePreview`

这里能看出两个现实问题：

- token 追加逻辑有重复
- preview 请求字段名存在不一致

## 9. 兼容层里的 token key 也不统一

可以看到前端同时兼容：
- `token`
- `agentcode.auth.token.v1`

documents 相关调用也是如此。

这说明 documents 的前端消费面还带着明显的历史兼容包袱。

## 10. 当前最值得注意的前后端偏差

- `reference_preview` POST body 字段名不一致
- `view_pdf` URL 构造方式不只一种
- token 传递既有 header 也有 query
- `literature_content` 业务错误用 `200`，前端必须读 payload 判断

这些问题在“代码能跑”的层面可能被掩盖，但在整理公共能力边界时必须记下来。
