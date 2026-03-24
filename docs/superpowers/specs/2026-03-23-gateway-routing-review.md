# 2026-03-23 Gateway Routing Review

## 结论先行

当前 `gateway` 的实现已经把边界写死在分发层：

- 普通 QA：`turn_mode == "kb_only"`，保留用户请求的 mode，`thinking` 请求会继续走 highThinkingQA。
- 文件 QA：`turn_mode == "file_only"`，无论用户请求 `thinking` 还是 `patent`，都会被 gateway 改写为 `actual_mode = "fast"`，走 fastQA。
- 混合 QA：`turn_mode == "mixed"`，同样一律改写为 `actual_mode = "fast"`，走 fastQA。
- 因此，用户强调的边界在当前 gateway 中是成立的：文件 QA、混合 QA 都由 fastQA 处理，highThinkingQA 只承接自己的普通 QA。

最关键的落点在 [gateway/app/services/route_decision.py](../../gateway/app/services/route_decision.py) 的 `RouteDecisionService.decide()`：只要 `file_context.turn_mode in {"file_only", "mixed"}`，就强制 `actual_mode = "fast"`。

## 审阅范围

当前实现：

- `gateway/app/main.py`
- `gateway/app/routers/qa.py`
- `gateway/app/models/ask.py`
- `gateway/app/models/routing.py`
- `gateway/app/models/files.py`
- `gateway/app/services/file_context_resolver.py`
- `gateway/app/services/route_decision.py`
- `gateway/app/services/backend_registry.py`
- `gateway/app/core/config.py`
- `gateway/app/providers/conversation_files/public_http.py`
- `gateway/tests/test_route_decision.py`
- `gateway/tests/test_qa_proxy.py`

legacy source of truth：

- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/api.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/file_context/service.py`
- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/schemas.py`
- `/home/cqy/worktrees/fastapi-version/backend/tests/test_ask_gateway.py`

## 当前 gateway 分发链路

### 1. 应用装配

入口在 `create_app()`：

- `gateway/app/main.py:27-52`
- 关键装配：
  - `app.state.conversation_file_service = ConversationFileService(...)`
  - `app.state.file_context_resolver = FileContextResolver()`
  - `app.state.route_decision_service = RouteDecisionService()`
  - `app.state.backend_registry = BackendRegistry(settings)`
  - `app.state.proxy_service = ProxyService(settings)`

这说明当前 gateway 的 QA 分发链路是：

1. Router 收请求。
2. 查会话文件元数据。
3. 本地判断 `kb_only / file_only / mixed` 和 `kb_qa / pdf_qa / tabular_qa / hybrid_qa`。
4. 决定 `actual_mode`。
5. 代理到对应 backend 的 `/api/{actual_mode}/ask` 或 `/api/{actual_mode}/ask_stream`。

### 2. 请求入口

请求入口在 `gateway/app/routers/qa.py`：

- `ask_legacy()`：`/api/ask`、`/api/v1/ask`，见 `qa.py:304-307`
- `ask_stream_legacy()`：`/api/ask_stream`、`/api/v1/ask_stream`，见 `qa.py:310-313`
- `ask_mode()`：`/api/{mode}/ask`、`/api/v1/{mode}/ask`，见 `qa.py:316-321`
- `ask_stream_mode()`：`/api/{mode}/ask_stream`、`/api/v1/{mode}/ask_stream`，见 `qa.py:324-329`

`_legacy_mode()` 在 `qa.py:22-32` 兼容旧前端的 `mode` 字段：

- 优先使用 `requested_mode`
- 如果 `requested_mode` 是 `fast` 或未传，再回退到 `mode`
- 最终只允许 `fast/thinking/patent`

### 3. 当前请求字段

入参模型在 `gateway/app/models/ask.py:15-22`：

- `question`
- `conversation_id`
- `chat_history`
- `requested_mode`
- `pdf_context`
- `options`
- `mode`（legacy alias）

真正参与路由判断的字段主要是：

- 顶层：
  - `question`
  - `conversation_id`
  - `requested_mode`
  - `mode`
- `pdf_context` 子字段：
  - `selected_ids`
  - `newly_uploaded_ids`
  - `all_available_ids`
  - `last_focus_ids`
  - `last_turn_route`

当前实现不再接收 legacy schema 里的这些显式路由字段：

- `use_pdf`
- `pdf_path`
- `route_hint`
- `use_generation_driven`
- `n_results_per_claim`

这部分是和旧版的明显差异，见下文。

### 4. 会话文件元数据来源

`qa.py:35-48` 的 `_resolve()` 会先拉会话文件元数据，再做路由判断。

元数据来源：

- `gateway/app/providers/conversation_files/public_http.py:35-74`
- 当 `conversation_id` 存在时，请求 `GET /api/conversations/{conversation_id}/files`
- 会转发 `Authorization` 和 trace header，见 `public_http.py:76-86`

文件模型在 `gateway/app/models/files.py:9-49`，用于路由的关键字段：

- `file_id`
- `file_type`
- `file_name`
- `file_meta`
  - 尤其是表格列名 `columns`
- `file_status`
- `parse_status`
- `index_status`
- `processing_stage`
- `local_path`
- `storage_ref`

### 5. 文件上下文判定：普通 QA / 文件 QA / 混合 QA

核心在 `gateway/app/services/file_context_resolver.py:97-244`。

#### 5.1 普通 QA 判定

以下情况会回到 `kb_only + kb_qa`：

- `question` 为空，见 `file_context_resolver.py:123-124`
- 没有识别出文件意图，见 `file_context_resolver.py:172-173`
- 只有泛泛的“文献/文件”话题词，但没有强文件指向、上传语义、表格焦点、文件名焦点，见 `file_context_resolver.py:175-176`

这里的关键含义是：

- 即使前端带了 `pdf_context.selected_ids`
- 只要问题本身不像“问某个文件”
- 仍然会被当作普通 QA，而不是文件 QA

测试也明确覆盖了这一点：

- `gateway/tests/test_route_decision.py:20-31`
- 选中了文件，但问“磷酸铁锂电压范围是多少？”或“文献综述一般怎么写？”仍然保持 `kb_qa`，且 `actual_mode == "thinking"`

#### 5.2 文件 QA 判定

一旦命中下面任一条件，会进入 `_file_turn()`，形成 `file_only` 或 `mixed`：

- 显式编号引用：`#1`、`#2`，见 `file_context_resolver.py:142-152`
- 序数引用：`第一个文件`、`前两个文件`、`倒数第一个文件`，见 `file_context_resolver.py:154-170`
- 指示数量引用：`这三篇文献`，见 `file_context_resolver.py:163-170`
- 单数文件指代：`这篇文献`、`this paper`，见 `file_context_resolver.py:197-233`
- 复数文件指代：`这些文献`、`all files`，见 `file_context_resolver.py:178-185`
- 最新上传：`最新上传的文献`，见 `file_context_resolver.py:187-195`
- 表格焦点：表格词、列/字段/筛选/统计等操作词，或命中 `file_meta.columns`，见 `file_context_resolver.py:134` 和 `320-335`
- 文件名焦点：问题中直接出现文件名/去扩展名，见 `file_context_resolver.py:135` 和 `337-346`

文件类型和 route 的映射在 `_route_for_selection()`：

- `gateway/app/services/file_context_resolver.py:299-320`
- 规则：
  - 同时有 PDF 和表格 => `hybrid_qa`
  - 只有表格 => `tabular_qa`
  - 命中 `table_focus` => `tabular_qa`
  - 其他文件问答默认 => `pdf_qa`

#### 5.3 混合 QA 判定

混合 QA 不是单独一套路由入口，而是“文件问答 + 知识库校验/补充”的 turn mode。

判定逻辑：

- `gateway/app/services/file_context_resolver.py:133`
- `gateway/app/services/file_context_resolver.py:257-276`
- `gateway/app/services/file_context_resolver.py:389-394`

具体条件：

- 先识别出文件问答
- 同时 `_detect_mixed_intent()` 返回 `True`
- `_detect_mixed_intent()` 规则：
  - 直接命中 `_MIXED_HINTS`，比如“结合知识库”“knowledge base”
  - 或文本里既有知识库 token，又有“结合/参考/补充/验证/分析”等动作词

命中后结果为：

- `turn_mode = "mixed"`
- `allow_kb_verification = True`
- route 仍先按选中文件类型算出 `pdf_qa` / `tabular_qa` / `hybrid_qa`

### 6. 分发到 fastQA 还是 highThinkingQA

真正决定 backend 的逻辑在 `gateway/app/services/route_decision.py:12-42`。

关键代码语义：

- `requested_mode` 是用户原始请求 mode
- `actual_mode` 是 gateway 最终分发 mode
- 规则只有一条核心分叉：
  - `if file_context.turn_mode in {"file_only", "mixed"}: actual_mode = "fast"`

也就是：

- 普通 QA：保留 `requested_mode`
- 文件 QA：强制 fast
- 混合 QA：强制 fast

这就是本次要确认的边界。

### 7. 当前 route/source_scope 语义

`RouteDecisionService` 还会把文件类请求整理为更稳定的协议字段，见 `route_decision.py:44-106`。

#### 7.1 route 归一化

- 如果 `turn_mode == "mixed"` 且原始 route 是 `pdf_qa/tabular_qa/hybrid_qa`
- 则对外统一暴露为 `route = "hybrid_qa"`
- 见 `route_decision.py:44-47`

所以：

- “PDF + KB” 混合问答，对 fastQA 看到的是 `route=hybrid_qa` + `source_scope=pdf+kb`
- “表格 + KB” 混合问答，对 fastQA 看到的是 `route=hybrid_qa` + `source_scope=table+kb`

#### 7.2 source_scope 条件

见 `route_decision.py:49-69`：

- `pdf_qa` => `pdf`
- `tabular_qa` => `table`
- `hybrid_qa` 且 `turn_mode == mixed`：
  - 原始是 PDF 或选中文件族只有 PDF => `pdf+kb`
  - 原始是表格或选中文件族只有表格 => `table+kb`
  - 同时有 PDF 和表格 => `pdf+table+kb`
- `hybrid_qa` 且 `turn_mode == file_only` 且同时选中 PDF+表格 => `pdf+table`

`kb_enabled = bool(source_scope and "kb" in source_scope)`，见 `route_decision.py:19-20`。

### 8. gateway 发给下游 backend 的字段

下游 payload 由 `_normalized_payload()` 构造，见 `gateway/app/routers/qa.py:51-70`。

固定包含：

- `question`
- `conversation_id`
- `chat_history`
- `requested_mode`
- `actual_mode`
- `route`
- `source_scope`
- `turn_mode`
- `kb_enabled`
- `allow_kb_verification`
- `used_files`
- `execution_files`
- `selected_file_ids`
- `primary_file_id`
- `file_selection`
- `trace_id`
- `options`

代理路径由 `actual_mode` 决定：

- JSON：`qa.py:193-199` => `/api/{actual_mode}/ask`
- SSE：`qa.py:255-261` => `/api/{actual_mode}/ask_stream`

backend 名字和 URL 映射：

- `gateway/app/core/config.py:52-82`
- `FAST_BACKEND_BASE_URL`
- `THINKING_BACKEND_BASE_URL`
- `PATENT_BACKEND_BASE_URL`
- `gateway/app/services/backend_registry.py:19-41`

也就是说，当前部署中：

- fastQA 对应 `FAST_BACKEND_BASE_URL`
- highThinkingQA 对应 `THINKING_BACKEND_BASE_URL`

## 当前实现对应的判定矩阵

| 问题类型 | 典型信号 | file_context.route | turn_mode | actual_mode | backend |
| --- | --- | --- | --- | --- | --- |
| 普通 QA | 没有文件指向；只是一般知识问题 | `kb_qa` | `kb_only` | 保留用户请求 | `thinking` 或 `fast` |
| PDF 文件 QA | “总结这篇文献”/命中文件名/选中 PDF | `pdf_qa` | `file_only` | `fast` | fastQA |
| 表格文件 QA | “统计这个表格”/命中列名 | `tabular_qa` | `file_only` | `fast` | fastQA |
| PDF+表格文件 QA | 同时选中 PDF 和表格 | `hybrid_qa` | `file_only` | `fast` | fastQA |
| PDF 混合 QA | “结合知识库总结这篇文献” | 归一化后对外是 `hybrid_qa` | `mixed` | `fast` | fastQA |
| 表格混合 QA | “结合知识库分析这个表格” | 归一化后对外是 `hybrid_qa` | `mixed` | `fast` | fastQA |
| PDF+表格混合 QA | “结合知识库比较前两个文件” | `hybrid_qa` | `mixed` | `fast` | fastQA |

## 当前实现的测试证据

当前测试直接验证了边界：

- 普通 QA 保持 thinking：`gateway/tests/test_route_decision.py:20-31`
- 文件 QA 强制 fast：`gateway/tests/test_route_decision.py:34-38`
- 混合 QA 强制 fast：`gateway/tests/test_route_decision.py:66-99`
- 表格 QA 强制 fast：`gateway/tests/test_route_decision.py:145-155`
- PDF+表格文件 QA 仍走 fast：`gateway/tests/test_route_decision.py:178-200`
- 端到端代理到 `/api/fast/ask`：`gateway/tests/test_qa_proxy.py:106-132`
- 端到端表格问答代理到 fast：`gateway/tests/test_qa_proxy.py:246-290`
- 端到端 PDF+KB 混合问答代理仍是 fast：`gateway/tests/test_qa_proxy.py:293-335`
- 端到端 PDF+表格+KB 混合问答代理仍是 fast：`gateway/tests/test_qa_proxy.py:337-380`
- gateway 在本地短路文件澄清，不再把澄清请求打到下游：`gateway/tests/test_qa_proxy.py:631-651`

## legacy 对应实现

## 1. 旧版入口与总体结构

legacy 的入口是单个 ask gateway：

- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/api.py:27-60`

旧版没有单独的 gateway 进程把请求再分发到 fastQA/highThinkingQA；它是在同一套 backend 里：

1. 收 `/api/v1/ask` / `/api/v1/ask_stream`
2. `enrich_request()` 解析文件上下文
3. `stream_events()` 中按 `route_hint` 分发到 KB / PDF / tabular 分支

## 2. 旧版请求字段

legacy schema 在 `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/schemas.py:13-23`：

- `question`
- `chat_history`
- `use_pdf`
- `pdf_path`
- `trace_id`
- `conversation_id`
- `pdf_context`
- `use_generation_driven`
- `route_hint`
- `n_results_per_claim`

和当前 gateway 相比，legacy 多了：

- `use_pdf`
- `pdf_path`
- `use_generation_driven`
- `route_hint`
- `n_results_per_claim`

当前 gateway 多了：

- `requested_mode`
- `mode`
- `options`

## 3. 旧版文件上下文解析

旧版核心在 `/home/cqy/worktrees/fastapi-version/backend/app/modules/file_context/service.py:129-513`。

主流程和当前实现是同构的：

- 解析 `question`
- 结合 `pdf_context`
- 列出当前会话上传文件
- 推导 `selected_file_ids`
- 推导 `route_hint`
- 推导 `turn_mode`
- 推导 `allow_kb_verification`

关键输出：

- `route_hint`：`pdf_qa` / `tabular_qa` / `hybrid_qa` / `kb_qa`
- `turn_mode`：`kb_only` / `file_only` / `mixed`
- `allow_kb_verification`

其中最接近当前边界的规则在：

- route 分类：`file_context/service.py:445-452`
- 选中文件但没有文件焦点时退回 `kb_qa`：`file_context/service.py:454-465`
- `question_mode` 与 `turn_mode`：`file_context/service.py:467-480`
- `allow_kb_verification`：`file_context/service.py:482`

## 4. 旧版 ask gateway 如何执行不同 QA 类型

旧版 `AskGatewayService._default_enrich_request()` 在：

- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py:157-214`

它会：

- 先设置默认 `route_hint = kb_qa`
- 如果 `use_pdf + pdf_path` 成立，则直接强行进 `pdf_qa + file_only`
- 如果有 `conversation_id`，再调用 `resolve_request_file_context(...)`
- 把文件上下文结果写回 payload

真正的执行分流在 `_default_event_source()`：

- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py:530-564`

条件非常直接：

- `route_hint == "pdf_qa"` => `_dispatch_pdf()`
- `route_hint in {"tabular_qa", "hybrid_qa"}` => `_dispatch_tabular()`
- 否则 => `_dispatch_kb()`

也就是说，legacy 的本质边界是：

- 普通 QA 走 KB 主链路
- 文件 QA / 混合 QA 不走 KB 主链路，而走 PDF 或 tabular 专门分支
- 其中 `hybrid_qa` 在旧版并不是“独立的第三套引擎”，而是落到 tabular/hybrid 文件分支处理

## 5. legacy 测试证据

legacy 测试也明确体现同样的语义边界：

- enrich 后文件问答进入 `pdf_qa`：`test_ask_gateway.py:221-256`
- direct PDF 可以强制进入 `pdf_qa`：`test_ask_gateway.py:259-288`
- PDF 分支执行：`test_ask_gateway.py:292-342`
- tabular 分支执行：`test_ask_gateway.py:407-447`
- 选中文件但问题是普通知识问题时，会回退 KB，而不是强行跑文件分支：
  - 表格回退 KB：`test_ask_gateway.py:449-509`
  - PDF 回退 KB：`test_ask_gateway.py:511-566`
- 文件歧义会返回澄清：`test_ask_gateway.py:569-592`

## 当前 gateway 与 legacy 的对应关系

| 语义层 | legacy | current gateway |
| --- | --- | --- |
| 请求入口 | `ask_gateway/api.py` | `gateway/app/routers/qa.py` |
| 文件上下文判断 | `modules/file_context/service.py` | `services/file_context_resolver.py` |
| 路由结果字段 | `route_hint` / `turn_mode` / `allow_kb_verification` | `route` / `turn_mode` / `allow_kb_verification` / `source_scope` / `actual_mode` |
| 执行分发 | 同进程内 `_dispatch_kb/_dispatch_pdf/_dispatch_tabular` | gateway 只决定 backend，再代理到 `fast/thinking/patent` |
| highThinking 与 fast 分流 | 不存在独立 backend 分流 | `RouteDecisionService` 统一改写 `actual_mode` |

可以把当前实现理解成：

- 继承了 legacy 的“先判断是否文件任务/混合任务”的语义
- 但把“怎么执行”从单进程内部执行，改造成“发给哪个 backend”
- 新增了一个 legacy 没有的显式边界：`actual_mode`

## 与 legacy 的关键差异

### 差异 1：当前 gateway 新增了显式 backend 分流层

legacy 只有 `route_hint`，没有 `actual_mode`。

当前 gateway 新增了：

- `requested_mode`
- `actual_mode`
- `source_scope`
- `kb_enabled`
- `primary_file_id`
- `file_selection`

其中最重要的新增是 `actual_mode`，这让“文件 QA / 混合 QA 一律 fast”成为 gateway 的显式协议，而不是下游约定。

### 差异 2：当前 mixed 问答对外统一成 `route = hybrid_qa`

legacy 中：

- mixed 只体现在 `turn_mode = mixed`
- `route_hint` 仍可能是 `pdf_qa`、`tabular_qa`、`hybrid_qa`

当前中：

- 只要是 mixed 且底层是文件问答
- 对外统一成 `route = hybrid_qa`
- 再用 `source_scope` 区分 `pdf+kb / table+kb / pdf+table+kb`

这比 legacy 更稳定，也更适合跨 backend 协议。

### 差异 3：当前 gateway 删除了 direct PDF 快捷入口

legacy 支持：

- `use_pdf = true`
- `pdf_path = ...`
- 即使没有 conversation file metadata，也能强行进 `pdf_qa`
- 见 `ask_gateway/service.py:159-177` 和 `211-214`

当前 gateway 的 `AskRequest` 已经没有这两个字段，意味着：

- 当前分发逻辑几乎完全建立在 `question + pdf_context + conversation files metadata` 上
- 若历史前端或脚本依赖 `use_pdf/pdf_path` 直达 PDF 分支，当前 gateway 不兼容

### 差异 4：当前 gateway 的 `pdf_context` 兼容别名比 legacy 少

legacy 接受：

- `selected_ids` 或 `selected_file_ids`
- `newly_uploaded_ids` 或 `newly_uploaded_file_ids`
- `all_available_ids` 或 `all_available_file_ids`
- `last_focus_ids` 或 `last_focus_file_ids`

当前 gateway 只读取：

- `selected_ids`
- `newly_uploaded_ids`
- `all_available_ids`
- `last_focus_ids`
- `last_turn_route`

见 `gateway/app/services/file_context_resolver.py:111-120`。

如果仍有旧客户端/旧状态结构发的是 `*_file_ids`，当前 gateway 会忽略这些别名。

### 差异 5：当前 gateway 的文件选择语义更简化

legacy `file_context` 还会输出：

- `selection_semantic`
- `ready_file_ids`
- `pending_file_ids`
- `failed_file_ids`
- `primary_pdf_path`
- `primary_table_path`
- deleted/missing 编号的细粒度澄清消息

当前 gateway 保留的是更轻量的协议：

- `selected_file_ids`
- `used_files`
- `execution_files`
- `primary_file_id`
- `file_selection.strategy`
- `source_scope`

这对 gateway 分发足够，但比 legacy 少了很多“文件是否可执行”的上下文。

### 差异 6：当前 gateway 对 conversation file provider 的依赖更硬

legacy 在列会话文件失败时：

- 是 `logger.warning(...)`
- 然后继续按空文件列表解析
- 见 `file_context/service.py:157-163`

当前 gateway 则是：

- provider 抛错后，直接在 router 层返回 `503` 或 SSE error
- 见 `gateway/app/routers/qa.py:169-174`、`231-236`
- 对应 provider 实现在 `public_http.py:53-72`

这意味着当前 plain QA 只要带了 `conversation_id`，也可能被文件元数据服务故障拖死。

## 风险点

### 风险 1：plain QA 也会被 conversation file provider 故障阻断

当前 `_resolve()` 先查文件元数据，再做路由判断，见 `qa.py:35-48`。

副作用：

- 即使用户问的是普通 QA
- 只要请求带了 `conversation_id`
- 而 public file provider 失败
- gateway 就会直接返回 `CONVERSATION_FILE_PROVIDER_UNAVAILABLE`

这比 legacy 更脆弱。legacy 在同类情况下会降级为“按无文件上下文继续处理”。

### 风险 2：旧客户端若继续发 `selected_file_ids` 等 legacy alias，会丢失文件路由信息

当前 resolver 不读这些别名，见 `file_context_resolver.py:111-120`。

后果：

- 本该进入文件 QA / 混合 QA 的请求，可能被判成普通 QA
- 于是错误地留在 `thinking` backend，而不是被 gateway 改写到 fast

这对“边界正确性”是实质风险。

### 风险 3：失去 `use_pdf/pdf_path` 直达路径后，非会话型 PDF 问答兼容性下降

legacy 可以不依赖 conversation files 直接进 PDF 分支；当前 gateway 不行。

如果系统里仍有：

- 本地文件路径直传脚本
- 非会话态 PDF 问答入口
- 旧版前端残留逻辑

则现在可能无法触发文件 QA，只能落到普通 QA 或根本无法执行。

### 风险 4：当前 gateway 不再输出 ready/pending/failed 文件集合

legacy 会区分 `ready_file_ids/pending_file_ids/failed_file_ids`，当前没有。

这意味着：

- gateway 自己不判断文件是否已解析完成
- fastQA 只能靠 `execution_files` 里的原始状态字段自行兜底

如果 fastQA 端没有完整兜底，文件 QA 的失败会更晚暴露。

### 风险 5：混合 QA 对 fastQA 的协议要求比 legacy 更强

当前 mixed 会统一发送：

- `route = hybrid_qa`
- `source_scope = pdf+kb / table+kb / pdf+table+kb`
- `kb_enabled = true`

这要求 fastQA 正确消费这些字段；否则会出现：

- PDF+KB 被当纯 PDF
- 表格+KB 被当纯表格
- PDF+表格+KB 的组合语义丢失

### 风险 6：当前默认把“未知文件类型但有文件意图”的单文件问答偏向 `pdf_qa`

`_route_for_selection()` 的兜底是 `pdf_qa`，见 `file_context_resolver.py:312-320`。

如果：

- `available_files` 缺失或不全
- `file_type` 无法识别
- 又命中了单文件语义

则请求可能被按 PDF 文件 QA 送去 fastQA，而不是真正的表格/其他文件分支。

## 对用户强调边界的最终判断

结论明确：当前 gateway 已经实现了这条边界，而且边界位置就在 gateway 自己，不依赖下游 backend 猜测。

边界定义如下：

- `kb_only`：highThinkingQA 可以处理自己的普通 QA；如果用户请求 `thinking`，gateway 不会改写。
- `file_only`：全部改写到 fastQA。
- `mixed`：全部改写到 fastQA。

换句话说：

- highThinkingQA 不应该承接文件 QA。
- highThinkingQA 也不应该承接混合 QA。
- highThinkingQA 在 gateway 语义里只负责自己的普通 QA。

如果后续要继续固化这条边界，建议把它视为 gateway 对下游的正式契约：

- `actual_mode == "thinking"` 只应出现在 `turn_mode == "kb_only"` 的请求上。
- `turn_mode in {"file_only", "mixed"}` 的请求，`actual_mode` 必须始终是 `fast`。
