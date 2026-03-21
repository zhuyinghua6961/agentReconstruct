# documents 接口、鉴权与配额

对应代码：
- `backend/app/modules/documents/api.py`
- `backend/app/modules/documents/schemas.py`
- `backend/app/modules/quota/deps.py`
- `backend/tests/test_documents.py`
- `backend/tests/test_quota.py`

## 1. 对外接口总表

当前公开接口：

- `GET|HEAD /api/v1/view_pdf/{doi:path}`
- `POST /api/v1/summarize_pdf/{doi:path}`
- `GET /api/v1/extract_pdf_text/{doi:path}`
- `POST /api/v1/translate`
- `GET /api/v1/check_pdf/{doi:path}`
- `GET /api/v1/literature_content`
- `GET /api/v1/reference_preview`
- `POST /api/v1/reference_preview`

同时也保留 `/api/...` 兼容路径。

## 2. route contract 不是统一 schema-first 风格

这个模块没有统一的 response schema。

只有两个入参模型：
- `TranslateRequest`
  - `texts: list[Any]`
- `ReferencePreviewRequest`
  - `dois_text: str`
  - `doi_list: list[str]`
  - `max_items: int | None`

其他接口大多是 path/query 参数 + service dict 直接回 JSON。

这意味着真正的契约要看：
- `api.py`
- `service.py`
- 前端消费逻辑

## 3. 鉴权策略是不统一的

### 3.1 强制登录并计 quota

- `summarize_pdf`
  - `require_auth_context`
  - `require_quota("pdf_summary", strict_config=True)`
- `translate`
  - `require_auth_context`
  - `require_quota("text_translate", strict_config=True)`

### 3.2 表面 optional，实际也要登录

- `view_pdf`
  - 代码参数里有 `get_optional_auth_context`
  - 但同时挂了 `require_quota("file_view")`

而 `require_quota()` 内部强依赖：
- `require_auth_context`

所以 `view_pdf` 真实仍然要求登录。

### 3.3 公开读取型接口

当前没有登录依赖的包括：
- `extract_pdf_text`
- `check_pdf`
- `literature_content`
- `reference_preview`

这意味着 documents 模块在安全边界上不是一刀切的。

## 4. quota 使用方式

### 4.1 `view_pdf`

quota 类型：
- `file_view`

行为：
- 先 precheck
- 成功返回 `FileResponse` 或 HEAD `Response` 后再 `finalize_quota()`
- 如果 service 返回错误 JSON，则 `finalize_quota()` 会跳过计数

### 4.2 `summarize_pdf`

quota 类型：
- `pdf_summary`

特殊点：
- `strict_config=True`

含义：
- 如果 quota 配置缺失，会直接抛 `QUOTA_CONFIG_MISSING`，返回 `503`
- 不会像非 strict 模式那样把缺配置视为“允许”

### 4.3 `translate`

quota 类型：
- `text_translate`

同样：
- `strict_config=True`

这代表这两个接口把 quota 配置当作硬前置条件。

## 5. finalize 计数语义

从 `quota.deps.finalize_quota()` 看，只有以下情况才会真的记 quota：

- grant 存在
- quota config 是 active
- 最终结果应该计数

“应该计数”的判断规则：
- 响应状态码不能 >= 400
- 如果是 JSON 且 `success=false` 或有 `error`，也不会计数

因此：
- `view_pdf` 找不到文件时不会扣 `file_view`
- `summarize_pdf`/`translate` 失败时不会 finalize 计数

这和 `uploads` 的预扣语义明显不同。

## 6. 各接口的真实返回风格

### 6.1 `view_pdf`

成功：
- `GET` 返回 `FileResponse`
- `HEAD` 返回轻量 `Response`

失败：
- JSON 错误，通常 `404/500`

### 6.2 `summarize_pdf`

成功：
- `{"doi": ..., "summary": ...}`

失败：
- `404`
- `500`
- `503`

### 6.3 `translate`

成功：
- `success: true`
- 顶层 `translations`
- `data.translations`

失败：
- 参数非法 `400`
- 翻译服务禁用 `503`
- 全部非空片段都失败 `502`

### 6.4 `literature_content`

很多业务失败也返回 `200`：
- 缺 DOI
- agent 未初始化
- 未找到文献
- 查询异常

### 6.5 `reference_preview`

始终是聚合结果风格：
- `items`
- `count`
- `requested_count`
- `max_items`
- `truncated`

即使单个 DOI 查不到元数据，也不会整体失败。

## 7. `reference_preview` 的 GET 与 POST 其实不是完全等价

### 7.1 GET

接收：
- `dois` repeated query params
- `dois_text`
- `max_items`

### 7.2 POST

接收：
- `dois_text`
- `doi_list`
- `max_items`

这里要注意：
- 后端 POST schema 里没有 `doi`
- 只有 `doi_list`

如果前端发的是错误字段名，后端不会报 schema 错，而是会把列表当空处理。

## 8. 当前最重要的接口层结论

- `documents` 内部同时存在严格鉴权接口和开放接口
- `view_pdf` 看起来像 optional auth，实际上仍然强制登录
- `summarize_pdf`/`translate` 对 quota 配置是 hard requirement
- `literature_content` 的错误语义明显偏“业务 JSON + 200”
- `reference_preview` 的 POST 契约非常容易因为字段名偏差而悄悄失效

所以这块如果以后要整理成真正公共服务，第一步不是重写逻辑，而是先统一接口语义。
