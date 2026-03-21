# gateway 公共后端协议对齐说明

目的：
- 把 `/home/cqy/worktrees/gateway/docs/gateway_forwarding_protocol.md` 与 `/home/cqy/worktrees/gateway/docs/gateway_canonical_protocol_revision.md` 里已经明确属于 `public backend` 的协议部分，落到 `public-service` 目录。
- 不是重写协议。
- 是把协议中“公共后端应负责什么”与当前 `/home/cqy/worktrees/fastapi-version` 的真实代码一一对齐，明确哪些已经具备、哪些仍未接通、哪些虽然归属已定但实现仍缠在单体 runtime 里。

这份文档的直接用途：
- 作为后续“把公共能力抽成单独公共后端”时的协议边界基线。
- 作为 `gateway` 改造时的 public proxy 覆盖检查表。
- 作为当前已确认 bug / 契约偏差 / 迁移风险的补充记录。

---

## 1. 这份文档的输入依据

协议文档：
- `/home/cqy/worktrees/gateway/docs/gateway_forwarding_protocol.md`
- `/home/cqy/worktrees/gateway/docs/gateway_canonical_protocol_revision.md`

gateway 当前实现：
- `/home/cqy/worktrees/gateway/app/main.py`
- `/home/cqy/worktrees/gateway/app/routers/public_proxy.py`
- `/home/cqy/worktrees/gateway/app/services/route_table.py`
- `/home/cqy/worktrees/gateway/app/services/proxy.py`
- `/home/cqy/worktrees/gateway/app/core/trace.py`
- `/home/cqy/worktrees/gateway/app/providers/conversation_files/public_http.py`
- `/home/cqy/worktrees/gateway/tests/test_public_proxy.py`
- `/home/cqy/worktrees/gateway/tests/test_route_table.py`

当前 `fastapi-version` 公共能力实现：
- `backend/app/modules/auth/api.py`
- `backend/app/modules/conversation/api.py`
- `backend/app/modules/uploads/api.py`
- `backend/app/modules/documents/api.py`
- `backend/app/modules/documents/service.py`
- `backend/app/modules/quota/api.py`
- `backend/app/modules/admin_users/api.py`
- `backend/app/modules/system/api.py`
- `backend/app/modules/system/service.py`
- `backend/app/modules/auth/deps.py`

---

## 2. gateway 协议里已经冻结的公共后端边界

根据 `gateway_forwarding_protocol.md` 第 5.1 节和 `gateway_canonical_protocol_revision.md` 的 `Standard Public Backend Boundary`：

协议已明确把以下能力划给 `public backend`：
- auth
- conversations
- uploaded file metadata
- file upload / delete / download / preview
- translate
- summarize_pdf
- literature_content
- reference_preview
- quota
- admin
- kb_info
- refresh_kb
- clear_cache
- clear_pdf

这意味着：
- 这里已经不是“要不要归公共后端”的开放问题。
- 后续拆分时应该默认这些接口仍由公共后端承担。
- 即便当前 `public` 与 `fast` 暂时还是同一个物理进程，协议边界也不能再按“单体现状”回退。

同时，协议还明确了两条公共后端约束：

1. `gateway` 对 public route 只做透明代理。
- 转发 method、path、query、body。
- 保留鉴权头。
- 保留 PDF inline 响应头。
- 不重包成功响应，不重塑分页结构。

2. `gateway` 已经把会话文件元数据接口当成路由判断输入。
- 当前 `public_http` provider 会调用 `GET /api/conversations/{conversation_id}/files`。
- 这条接口返回的文件元数据，不再只是前端列表接口，而是 `gateway` 的路由前置依赖。

---

## 3. 当前 `fastapi-version` 中与协议对应的公共模块

按当前代码，协议中的公共后端能力主要映射到这些模块：

| 协议能力 | 当前模块 | 主要入口 |
| --- | --- | --- |
| auth | `backend/app/modules/auth` | `/api/auth/*`、`/api/v1/auth/*` |
| conversations / file metadata | `backend/app/modules/conversation` | `/api/conversations*`、`/api/v1/conversations*` |
| uploads / clear_pdf | `backend/app/modules/uploads` | `/upload_pdf`、`/upload_excel`、`/clear_pdf` 与 `/api/v1/*` |
| document preview / summarize / translate / literature / reference | `backend/app/modules/documents` | `/api/view_pdf*`、`/api/translate`、`/api/literature_content` 等 |
| quota | `backend/app/modules/quota` | `/api/quota/*`、`/api/v1/quota/*` |
| admin | `backend/app/modules/admin_users` | `/api/admin/*` |
| health / kb / cache | `backend/app/modules/system` | `/health`、`/api/kb_info`、`/api/refresh_kb`、`/api/clear_cache` |

但要注意：当前这些模块虽然“从业务归属上”属于公共能力，它们在实现上并不都已经适合直接独立成公共后端。

当前仍然明显缠着单体 runtime 的点包括：
- `documents.literature_content()` 和 `documents.reference_preview()` 依赖 `runtime.agent`
- `system.kb_info()` / `refresh_kb()` 依赖 `runtime.agent` 和 `runtime.init_agent`
- `system.clear_cache()` 直接操作 `runtime.answer_cache`
- `uploads.clear_pdf()` 直接操作 `runtime.current_pdf_path`

所以协议边界已经定了，但实现还没有完全适配这个边界。

---

## 4. canonical public route 与当前代码对齐矩阵

下面按协议分组，区分四种状态：
- `已对齐`：协议有，`fastapi-version` 有，`gateway` 也已接入
- `后端已有，gateway 未接`：协议有，后端已有，但 `public_proxy` / `route_table` 还没覆盖
- `协议 path 与当前后端 path 不兼容`：`gateway` 当前 canonical path 透传后会直接打到不存在的上游 path
- `协议归属已定，但实现边界未拆净`：路由存在，但底层实现还依赖单体 runtime 或 QA 侧对象

### 4.1 auth

| 协议路由 | `fastapi-version` | `gateway` 当前状态 | 结论 |
| --- | --- | --- | --- |
| `POST /api/auth/login` | 已有 | 已接 | 已对齐 |
| `POST /api/auth/register` | 已有 | 已接 | 已对齐 |
| `GET /api/auth/me` | 已有 | 已接 | 已对齐 |
| `PUT /api/auth/password` | 已有，且与 `POST` 共存 | `public_proxy.py` 只代理 `POST` | 后端已有，gateway 未接 |
| `POST /api/auth/password` | 已有 | 已接 | 已对齐 |
| `POST /api/auth/forgot-password/initiate` | 已有 | 已接 | 已对齐 |
| `POST /api/auth/forgot-password/verify` | 已有 | 已接 | 已对齐 |
| `GET /api/auth/security-questions` | 已有 | 已接 | 已对齐 |
| `PUT /api/auth/security-questions` | 已有，且与 `POST` 共存 | `public_proxy.py` 只代理 `GET, POST` | 后端已有，gateway 未接 |
| `POST /api/auth/security-questions` | 已有 | 已接 | 已对齐 |

补充说明：
- 当前后端 `backend/app/modules/auth/api.py` 同时保留 `/api/v1/*` 与 `/api/*`。
- `gateway` 侧还没有 `/api/v1/auth/*` 兼容入口。

### 4.2 conversations 与会话文件元数据

| 协议路由 | `fastapi-version` | `gateway` 当前状态 | 结论 |
| --- | --- | --- | --- |
| `GET /api/conversations` | 已有 | 已接 | 已对齐 |
| `POST /api/conversations` | 已有 | 已接 | 已对齐 |
| `GET /api/conversations/{conversation_id}` | 已有 | 已接 | 已对齐 |
| `DELETE /api/conversations/{conversation_id}` | 已有 | 已接 | 已对齐 |
| `POST /api/conversations/{conversation_id}/messages` | 已有 | 已接 | 已对齐 |
| `PUT /api/conversations/{conversation_id}/title` | 已有 | 未接 | 后端已有，gateway 未接 |
| `GET /api/conversations/{conversation_id}/files` | 已有 | 已接 | 已对齐，且已被 gateway 路由判断使用 |
| `GET /api/conversations/{conversation_id}/files/{file_id}` | 已有 | 已接 | 已对齐 |
| `DELETE /api/conversations/{conversation_id}/files/{file_id}` | 已有 | 已接 | 已对齐 |
| `GET /api/conversations/{conversation_id}/files/{file_id}/download` | 已有 | 已接 | 已对齐 |

对拆分最重要的事实：
- `gateway/app/providers/conversation_files/public_http.py` 已经真实依赖 `GET /api/conversations/{conversation_id}/files`。
- `gateway` 文件路由判断至少期待这些字段稳定存在：
  - `file_id`
  - `file_type`
  - `file_name`
- 当前 `conversation_service.list_uploaded_files()` 实际还会返回：
  - `file_status`
  - `parse_status`
  - `index_status`
  - `processing_stage`
  - `file_meta`
  - `file_no`
  - `display_no`

这意味着：
- 拆公共后端时，这条文件列表接口不能随意改 shape。
- 否则不只是前端列表会坏，`gateway` 的 file-aware 路由也会退化。

### 4.3 uploads / clear_pdf

| 协议路由 | `fastapi-version` | `gateway` 当前状态 | 结论 |
| --- | --- | --- | --- |
| `POST /api/upload_pdf` | 当前后端只有 `/upload_pdf` 和 `/api/v1/upload_pdf` | `public_proxy.py` 已注册 `/api/upload_pdf` 并原样透传 | 协议 path 与当前后端 path 不兼容 |
| `POST /api/upload_excel` | 当前后端只有 `/upload_excel` 和 `/api/v1/upload_excel` | `public_proxy.py` 已注册 `/api/upload_excel` 并原样透传 | 协议 path 与当前后端 path 不兼容 |
| `POST /api/clear_pdf` | 当前后端只有 `/clear_pdf` 和 `/api/v1/clear_pdf` | gateway 未接 | 协议 path 与当前后端 path 不兼容，且 gateway 未接 |

这里不是抽象风险，而是已确认的接口不兼容：
- `gateway` 现在如果向当前 `fastapi-version` public backend 透传 `/api/upload_pdf`，上游没有这个 path。
- 同理 `/api/upload_excel` 也一样。

`clear_pdf` 还存在更深一层边界问题：
- 当前实现只是清理单体 runtime 上的 `current_pdf_path`。
- 但 `gateway` 协议已经把文件上下文判断建立在会话文件元数据上，而不是单体进程中的“当前 PDF 指针”。
- 所以这条接口更像历史兼容能力，拆分时需要重新定义其存在价值。

### 4.4 documents

| 协议路由 | `fastapi-version` | `gateway` 当前状态 | 结论 |
| --- | --- | --- | --- |
| `POST /api/translate` | 已有 | 已接 | 已对齐 |
| `POST /api/summarize_pdf/{doi}` | 已有 | 已接 | 已对齐 |
| `GET /api/extract_pdf_text/{doi}` | 已有 | 已接 | 已对齐 |
| `GET /api/check_pdf/{doi}` | 已有 | 已接 | 已对齐 |
| `GET /api/view_pdf/{doi}` | 已有 | 已接 | 已对齐 |
| `HEAD /api/view_pdf/{doi}` | 已有 | 已接 | 已对齐 |
| `GET /api/literature_content` | 已有 | 未接 | 后端已有，gateway 未接 |
| `POST /api/reference_preview` | 已有 | 未接 | 后端已有，gateway 未接 |

补充事实：
- 当前后端 `reference_preview` 实际同时支持 `GET` 和 `POST`。
- 但协议当前只把 `POST /api/reference_preview` 列为 canonical route。
- 这说明 `GET /api/reference_preview` 更像兼容形态，是否保留应由后续 compatibility 策略明确，而不是继续隐式存在。

实现边界风险：
- `documents.literature_content()` 会直接使用 `agent.graph` 和 `agent.semantic_expert`。
- `documents.reference_preview()` 会把 `agent` 继续传给 `build_reference_preview_batch()`。
- 协议虽然已把这两类接口归给公共后端，但当前实现并不是纯公共基础设施实现，仍然夹带 QA / retrieval runtime 依赖。

### 4.5 quota

| 协议路由 | `fastapi-version` | `gateway` 当前状态 | 结论 |
| --- | --- | --- | --- |
| `GET /api/quota/my` | 已有 | 未接 | 后端已有，gateway 未接 |
| `GET /api/quota/configs` | 已有 | 未接 | 后端已有，gateway 未接 |
| `POST /api/quota/configs` | 已有 | 未接 | 后端已有，gateway 未接 |
| `PUT /api/quota/configs/{quota_type}` | 已有 | 未接 | 后端已有，gateway 未接 |
| `GET /api/quota/users/{user_id}` | 已有 | 未接 | 后端已有，gateway 未接 |
| `POST /api/quota/reset/{user_id}/{quota_type}` | 已有 | 未接 | 后端已有，gateway 未接 |

### 4.6 admin

| 协议路由 | `fastapi-version` | `gateway` 当前状态 | 结论 |
| --- | --- | --- | --- |
| `GET /api/admin/users` | 已有 | 未接 | 后端已有，gateway 未接 |
| `POST /api/admin/users` | 已有 | 未接 | 后端已有，gateway 未接 |
| `DELETE /api/admin/users/{user_id}` | 已有 | 未接 | 后端已有，gateway 未接 |
| `PUT /api/admin/users/{user_id}/password` | 已有 | 未接 | 后端已有，gateway 未接 |
| `PUT /api/admin/users/{user_id}/status` | 已有 | 未接 | 后端已有，gateway 未接 |
| `PUT /api/admin/users/{user_id}/type` | 已有 | 未接 | 后端已有，gateway 未接 |
| `POST /api/admin/users/batch-import` | 已有 | 未接 | 后端已有，gateway 未接 |
| `GET /api/admin/users/import-template` | 已有 | 未接 | 后端已有，gateway 未接 |

补充说明：
- 当前 `admin_users` 的路由前缀已经是 canonical `/api/admin/*`，不需要做 path 级重命名。
- 真正缺的是 `gateway` public proxy 覆盖。

### 4.7 system

| 协议路由 | `fastapi-version` | `gateway` 当前状态 | 结论 |
| --- | --- | --- | --- |
| `GET /api/health` | 当前后端只有 `/health` 和 `/api/v1/health` | `public_proxy.py` 已注册 `/api/health` 并原样透传 | 协议 path 与当前后端 path 不兼容 |
| `GET /api/kb_info` | 已有 | 已接 | 路由已对齐，但实现边界未拆净 |
| `POST /api/refresh_kb` | 已有 | 未接 | 后端已有，gateway 未接，且实现边界未拆净 |
| `POST /api/clear_cache` | 已有 | 未接 | 后端已有，gateway 未接，且实现边界未拆净 |

`/api/health` 是另一个已确认不兼容点：
- `gateway` 的 health probe 也会探测上游 `/api/health`。
- 当前 `fastapi-version` 实际并没有这个 path。
- 所以如果直接把当前后端挂到 `gateway` 的 `public` 角色上，`gateway /healthz` 里的 upstream probe 会得到错误结果。

---

## 5. `/api/v1/...`、query-token、trace header 兼容层现状

### 5.1 `/api/v1/...` 兼容层

协议已经写明：
- canonical frontend path 是 `/api/...`
- 迁移期 `gateway` 仍应承担 `/api/v1/...` 兼容入口

当前代码事实：
- `fastapi-version` 大量公共接口同时暴露 `/api/v1/...` 与 `/api/...`
- `gateway/app/routers/public_proxy.py` 完全没有注册 `/api/v1/...`

结论：
- `gateway` 当前还不能替代旧前端直接承接公共接口流量。
- 这不是“优化项”，而是迁移期兼容缺口。

### 5.2 `?token=` 浏览器预览/下载兼容

协议写明：
- 浏览器打开 PDF 预览和文件下载时，临时兼容 `?token=...`

当前代码事实：
- `backend/app/modules/auth/deps.py` 的 `get_bearer_token()` 同时接受 `Authorization` header 和 query 参数 `token`
- `gateway` proxy 会原样保留 query string

基于源码可以确认的结论：
- 对于已经被 `gateway` public proxy 注册的公共路由，只要请求 path 本身可达，`?token=` 会被透传到后端
- 但旧前端若访问的是 `/api/v1/view_pdf/...?...` 这类路径，仍会先卡在 `gateway` 缺少 `/api/v1/...` 入口，而不是卡在 token 透传

也就是说：
- query-token 透传能力在当前 `/api/...` 路由上基本具备
- 真正未完成的是 `gateway` 的 `/api/v1/...` 兼容入口

### 5.3 trace header 兼容

协议写明：
- canonical header: `X-Trace-Id`
- 兼容接受：`X-Trace-ID`、`X-Request-ID`

当前代码事实：
- `gateway/app/core/trace.py` 只从 `X-Trace-Id` 读取 trace id
- 没有接受 `X-Trace-ID` 或 `X-Request-ID`

结论：
- 协议文档已经冻结了 trace header 兼容规则
- 当前 `gateway` 实现尚未对齐

---

## 6. 当前实现里最需要单独澄清的边界项

### 6.1 `literature_content`

协议归属：
- 公共后端

当前实现事实：
- `backend/app/modules/documents/api.py` 暴露为公共文档接口
- `backend/app/modules/documents/service.py` 中 `literature_content()` 直接查 `agent.graph`
- 查不到时还会继续查 `agent.semantic_expert.collection`

结论：
- 协议归属没有问题。
- 但实现并不是纯“公共基础设施查询接口”，而是挂着 QA/retrieval runtime 的读操作。
- 后续拆分时要么给公共后端提供稳定的 retrieval read facade，要么把底层查询下沉成公共可调用的只读服务；不能只是“复制路由”。

### 6.2 `reference_preview`

协议归属：
- 公共后端

当前实现事实：
- `backend/app/modules/documents/api.py` 同时提供 `GET` 和 `POST`
- `documents_service.reference_preview()` 继续依赖 `agent`

结论：
- 这条接口应该继续归公共后端。
- 但底层引用预览生成逻辑目前并未彻底摆脱 QA runtime。
- 如果后续只按路由表拆文件，而不处理 `agent` 依赖，公共后端会被迫继续装载 QA 侧对象。

### 6.3 `kb_info / refresh_kb / clear_cache`

协议归属：
- 公共后端

当前实现事实：
- `system.kb_info()` 依赖 `runtime.agent`
- `system.refresh_kb()` 依赖 `runtime.agent` 和 `runtime.init_agent()`
- `system.clear_cache()` 直接清 `runtime.answer_cache`
- 这些接口当前还没有鉴权保护

结论：
- 这三条接口在协议上属于公共后端的“系统运维面”。
- 但当前实现其实仍在直接摸单体 QA runtime 内部对象。
- 所以后续拆分不能只搬 API，要先决定：
  - 公共后端是否真的持有这些 runtime
  - 还是改成调用 QA/backend admin facade
  - 还是把其中一部分从 public boundary 中降为 compatibility-only

在没有完成这一步前，这几条接口虽然“协议归属清楚”，但“实现归属并不干净”。

### 6.4 `clear_pdf`

协议归属：
- 公共后端

当前实现事实：
- 当前接口只有 `/clear_pdf` 与 `/api/v1/clear_pdf`
- 逻辑只是 `runtime.current_pdf_path = None`

结论：
- 这条接口显然是旧单体“当前 PDF 指针”时代的兼容产物。
- 与 `gateway` 当前的 conversation-file routing 模型并不完全同构。
- 拆公共后端时，应该把它视为“兼容接口待重定义”，而不是长期核心能力。

---

## 7. 已确认的 gateway 覆盖缺口与 bug / 风险

下面这些项都已经有明确源码依据，不是推测。

### 7.1 `gateway` 当前 public proxy 覆盖明显小于协议要求

当前 `public_proxy.py` / `route_table.py` 未覆盖的协议必需项包括：
- `PUT /api/auth/password`
- `PUT /api/auth/security-questions`
- `PUT /api/conversations/{conversation_id}/title`
- `GET /api/literature_content`
- `POST /api/reference_preview`
- 整个 `/api/quota/*`
- 整个 `/api/admin/*`
- `POST /api/refresh_kb`
- `POST /api/clear_cache`
- `POST /api/clear_pdf`
- 整个 `/api/v1/...` 兼容层

### 7.2 canonical path 与当前后端 path 已确认存在 3 处直连不兼容

已确认不兼容的 canonical path：
- `POST /api/upload_pdf`
- `POST /api/upload_excel`
- `GET /api/health`

当前后端实际提供的是：
- `/upload_pdf`、`/api/v1/upload_pdf`
- `/upload_excel`、`/api/v1/upload_excel`
- `/health`、`/api/v1/health`

这意味着：
- 即使 `gateway` 自己把这些 canonical route 注册出来了，当前 public backend 若直接指向 `fastapi-version`，也会因为上游 path 不存在而失败。

### 7.3 协议已定义 trace header 兼容，但 gateway 尚未实现

已确认：
- `trace_id_middleware` 只认 `X-Trace-Id`
- 不认 `X-Trace-ID`
- 不认 `X-Request-ID`

这属于：
- 文档已冻结，代码未对齐

### 7.4 public proxy 测试覆盖不足以证明协议已落地

`gateway/tests/test_public_proxy.py` 当前只覆盖了：
- `GET /api/conversations`
- `GET /api/view_pdf/{doi}`
- `POST /api/translate`

`gateway/tests/test_route_table.py` 只检查：
- route 是否已注册
- public / qa route table 是否不重叠

这意味着当前测试并没有覆盖：
- uploads canonical path 是否真能打到当前 public backend
- admin / quota / literature / reference / cache routes
- `/api/v1/...` compatibility
- trace header alias
- `?token=` 兼容路径

所以现在最多只能说：
- `gateway` 已有 public proxy 基础能力
- 不能说“公共协议已全面落地”

### 7.5 前端新调用面已经部分按 gateway canonical path 编写，但当前后端仍未完全匹配

当前前端代码里已经能看到面向 gateway canonical path 的调用：
- `frontend-vue/src/api/chat.js`
  - `POST /api/upload_pdf`
  - `POST /api/upload_excel`
  - `POST /api/clear_pdf`
- `frontend-vue/src/api/literature.js`
  - `GET /api/literature_content`
  - `POST /api/reference_preview`

这说明：
- 协议与前端调用面并不是完全脱节的未来设计。
- 一部分前端已经朝 canonical `/api/...` 收口。

但当前后端 / gateway 仍有两类缺口：
- `upload_pdf` / `upload_excel` / `clear_pdf` 与当前 `fastapi-version` path 不兼容
- `literature_content` / `reference_preview` 当前后端虽有实现，但 `gateway` 还未代理

所以这些问题会直接影响后续“前端只调用 gateway”的落地，不是纯内部重构问题。

---

## 8. 对后续“公共服务拆成独立后端”的直接影响

基于这轮协议对齐，后续实施时应把任务拆成两层，而不是混成一个“拆服务”动作。

### 8.1 第一层：协议对齐层

目标：
- 让 `gateway` 真正完整承接公共接口面

至少包括：
- 补齐 `public_proxy.py` 和 `route_table.py` 的 public routes
- 补 `/api/v1/...` compatibility routes
- 处理 canonical path 与现后端 path 的不兼容项
- 补 trace header alias
- 为 public proxy 增补覆盖测试

### 8.2 第二层：公共后端适配层

目标：
- 让当前公共模块真的能独立成一个 `public backend`

至少包括：
- 把 `literature_content` / `reference_preview` 对 `agent` 的依赖收口
- 重新定义 `kb_info / refresh_kb / clear_cache / clear_pdf` 的后端归属和调用链
- 决定 uploads 与 health 的 canonical path 由谁改：
  - 改当前公共后端实现去接受 `/api/...`
  - 或让 `gateway` 在迁移期做 path rewrite

### 8.3 文档层面的结论

到这一步，阻塞点已经不再是“gateway 协议不存在”。

当前真实情况是：
- `gateway` 协议已经存在，而且 public backend 边界已写清
- 当前主要问题是协议覆盖不完整、兼容层不完整、以及部分公共能力底层仍耦合单体 runtime

所以后续动工时，应该按“已有协议下的对齐与适配”推进，而不是重新设计公共服务协议。
