# Patent 配额管理收口设计

## 1. 目标

把 `patent` 相关能力正式纳入现有 quota 体系，补齐当前专利问答链路没有进入统一配额闭环的缺口，同时保持现有用户可见的 canonical quota 模型不扩桶、不分叉。

本次设计要解决的问题：

1. 让 `patent` `ask` / `ask_stream` 真正进入 quota `precheck -> finalize/abort` 闭环
2. 明确 `patent kb_qa`、`pdf_qa`、`tabular_qa`、`hybrid_qa` 各自应当归入哪个 canonical quota bucket
3. 明确 patent 原文查看、文档辅助、文献辅助等能力在 quota 体系里的归属边界
4. 保持前端、管理端、缓存、配置、接口返回都继续围绕 4 个 canonical quota type 工作

不在本次设计范围内：

1. 新增 patent 专属 quota 类型
2. 改造配额计费模型
3. 改变 `patent` 请求协议、前端交互路径或公开 API 路由
4. 把匿名兼容调用强行改成必须登录

---

## 2. 当前事实基线

### 2.1 当前 canonical quota 模型

当前系统的 canonical quota type 只有 4 个：

1. `ask_query`
2. `file_qa`
3. `file_view`
4. `doc_assist`

`public-service` 是 quota 权威中心，前端 quota 展示和归一化逻辑也都围绕这 4 个 bucket 展开。

当前 alias 关系已经包含：

- `kb_qa`、`thinking_qa` -> `ask_query`
- `pdf_qa`、`tabular_qa`、`hybrid_qa` -> `file_qa`
- `file_view` -> `file_view`
- `pdf_summary`、`text_translate`、`reference_preview`、`literature_content`、`extract_pdf_text` -> `doc_assist`

这意味着 patent 路由本身并不缺少可复用的 quota bucket，缺的是 ask 链路接入。

因此对 `public-service` 而言，本轮大方向不是新增 canonical bucket 或重写 alias 逻辑，而是：

1. 以现有 alias / canonical 逻辑为准绳
2. 补齐 patent 相关入口的回归测试
3. 只在实现时发现某个入口没有落到既有 canonical 归一化路径上时，再做最小必要修补

### 2.2 当前 patent ask 链路的 quota 缺口

当前 `gateway` 在 QA 主链里会根据 route decision 判定 `ask_query` 或 `file_qa`，并对 fast / thinking 请求执行：

1. quota precheck
2. 上游 ask/ask_stream 调用
3. 成功后 finalize
4. 失败时 abort

但现状里 `gateway` 对 `requested_mode == patent` 或 `actual_mode == patent` 的请求直接跳过 quota 分类，因此：

1. `patent /api/patent/ask` 不消耗 `ask_query` 或 `file_qa`
2. `patent /api/patent/ask_stream` 不消耗 `ask_query` 或 `file_qa`
3. patent 问答虽然已经是真实功能，但没有进入统一配额管理

### 2.3 当前 patent 相关能力里已经纳入 quota 的部分

当前并不是所有 patent 相关能力都缺 quota。

已经在 quota 内的能力包括：

1. patent 原文查看 `/api/patent/original/...`：使用 `file_view`
2. DOI / PDF 原文查看：使用 `file_view`
3. 会话文件下载：使用 `file_view`
4. `summarize_pdf`、`translate`、`translate_document`：使用 `doc_assist`
5. `extract_pdf_text`、`literature_content`、`reference_preview`：使用 `doc_assist`

所以本轮设计的核心不是“发明一套 patent quota 模型”，而是“让 patent ask 链路接入现有模型，并把相关边界写清楚”。

### 2.4 当前 patent ask 的真实路由类型

`patent` ask 请求当前已经有明确的协议路由：

1. `kb_qa`
2. `pdf_qa`
3. `tabular_qa`
4. `hybrid_qa`

对应的 `source_scope` 与执行语义已经在 `patent` 请求协议里锁定，不是占位字段。

因此 quota 映射可以直接基于现有 route contract，而不是靠问题文本重新猜测。

---

## 3. 设计决策

### 3.1 不新增 patent 专属 quota bucket

本次设计明确采用“复用现有 bucket”的方案，不新增：

- `patent_qa`
- `patent_file_qa`
- `patent_doc_assist`

原因：

1. 当前 canonical quota 模型已经能表达 patent 能力边界
2. 新增 patent bucket 会带来前端展示、后台配置、缓存、排序、兼容别名、历史数据迁移等整套扩容成本
3. 从用户视角，`patent kb_qa` 仍然是问答，`patent pdf_qa/tabular_qa/hybrid_qa` 仍然是文件问答，不需要按 backend 名称单独拆账
4. 本轮要解决的是“真实功能没有被计额”，不是“重做产品配额分类”

### 3.2 patent ask 的 canonical quota 映射

本次设计锁定以下映射：

1. `patent kb_qa` -> `ask_query`
2. `patent pdf_qa` -> `file_qa`
3. `patent tabular_qa` -> `file_qa`
4. `patent hybrid_qa` -> `file_qa`

解释：

- `kb_qa` 是不带文件执行上下文的知识问答，应与普通问答共享 `ask_query`
- `pdf_qa` / `tabular_qa` / `hybrid_qa` 都属于文件驱动或文件混合驱动的问答，应统一落在 `file_qa`
- `hybrid_qa` 即使同时使用文件与知识库，也不单独拆 bucket，仍归类为文件问答

### 3.3 patent 原文与文档辅助的归属保持不变

本次设计不改变已在 quota 体系里的 patent 相关入口：

1. patent 原文查看继续归 `file_view`
2. 文献辅助、摘要、翻译、抽取、引用预览继续归 `doc_assist`

这意味着本轮的核心增量只在 patent ask 主链，不在这些已接好的 public-service 能力上重新造轮子。

### 3.4 quota 权威仍然只在 public-service

`patent` 服务本身不成为新的 quota authority。

继续保持：

1. `public-service` 负责 quota 配置、检查、记账、grant 生命周期
2. `gateway` 负责在 ask 主链里做 quota orchestration
3. `patent` backend 只负责业务执行，不单独维护一套 user quota 状态

这样可以保证：

1. 前端和管理端只读一个 quota authority
2. fast / thinking / patent 三条 QA 链路的 quota 语义一致
3. quota 配置不会按 backend 分裂

---

## 4. 行为设计

### 4.1 gateway 对 patent ask 的 quota 闭环

本次设计要求 `gateway` 对 patent ask 和 fast / thinking 一样执行完整 quota 闭环：

1. 根据 route decision 判定 canonical `quota_type`
2. 若存在正整数 `user_id`，先向 `public-service` 做 `precheck`
3. precheck 通过后再转发到 patent backend
4. sync ask 只有在业务成功响应时才 `finalize(success=true)`
5. stream ask 只有在成功完成时才 `finalize(success=true)`
6. 上游失败、非成功完成、连接异常、业务错误时执行 abort / failed finalize

这里“业务成功”沿用当前 gateway 已有的 ask 主链判定原则，而不是为 patent 单独发明新规则。

### 4.2 patent sync ask 的计额规则

`/api/patent/ask` 只有在以下条件都满足时才计额：

1. HTTP 状态不是错误
2. 响应体是有效业务成功 payload
3. `success` 不是 `false`
4. 没有非空 `error`

否则：

1. 不记账
2. 已拿到的 grant 必须被 abort

quota finalize 失败时：

1. 业务成功结果仍然返回给用户
2. 响应里附带 quota warning / counted 状态
3. 日志保留 finalize 失败信息

这里必须复用 `gateway` 现有 sync ask 的 quota 注入机制，而不是为 patent 设计新的响应形状。当前既有机制是把 canonical quota 结果挂到响应体的 `quota` 字段上。

### 4.3 patent stream ask 的计额规则

`/api/patent/ask_stream` 只有在流真正成功完成时才计额。

本次设计不重新定义“成功完成”的语义，直接复用当前 `gateway` ask_stream 的既有判定：只有当流里存在合法的 SSE `done` 终态事件时，才视为成功完成并允许 `finalize(success=true)`。

必须覆盖的失败场景包括：

1. 上游连接失败
2. HTTP 错误响应
3. SSE error event
4. 流提前中断
5. 没有合法成功终态

这些情况都不允许消耗 quota，并且 grant 必须被释放。

quota finalize 失败时：

1. 不能把已经成功完成的业务流强行改成 5xx
2. 只能把 quota 结果追加到既有 `done` 事件的 `quota` 字段里，或以 warning 形式附着在该终态事件上
3. 必须记录结构化日志方便排查

这里同样必须复用 `gateway` 现有 stream ask 的 quota 注入机制，而不是为 patent 发明新的 SSE terminal event 结构。

### 4.4 patent file route gate 与 quota 的关系

当前系统已有 patent file route gate。

本次设计要求：

1. `patent kb_qa` 不受 patent file gate 影响
2. `pdf_qa` / `tabular_qa` / `hybrid_qa` 若被 gate 挡住，不应消耗 quota
3. gate-off 的 JSON / SSE 显式错误响应必须发生在成功记账之前

也就是说，quota 必须跟着真实执行链路走，不能在 feature gate 之前提前扣减。

### 4.5 无效 user_id 与匿名路径

对 ask 主链：

1. 只有正整数 `user_id` 才进入 quota 调用
2. 缺失、空值、非法 `user_id` 不执行 quota precheck

对 patent 原文 / 文档辅助等 public-service 路径：

1. 已有认证用户调用继续按现有 quota 规则执行
2. 现有匿名兼容路径维持当前行为，不在本轮隐式改策略

---

## 5. 接口与数据模型约束

### 5.1 不改公开 API 路径

本次设计不新增也不改动以下公开路径：

1. `/api/patent/ask`
2. `/api/patent/ask_stream`
3. `/api/patent/original/...`
4. 现有 quota 管理与 quota 查询接口

### 5.2 不改 patent ask 请求协议

本次设计不改动 patent ask 请求中的：

1. `requested_mode`
2. `actual_mode`
3. `route`
4. `source_scope`
5. `turn_mode`
6. `execution_files`
7. `selected_file_ids`

quota 分类直接消费现有 route contract，不增加新的前端协议字段。

### 5.3 quota 返回仍然使用 canonical type

本次设计后，patent ask 成功响应中的 quota 相关信息仍然只暴露 canonical 类型：

1. `ask_query`
2. `file_qa`

而不是暴露 `kb_qa`、`pdf_qa`、`hybrid_qa` 这类内部执行 route。

### 5.4 前端 quota 模型不扩容

前端与管理端仍然只处理 4 个 canonical quota type：

1. `ask_query`
2. `file_qa`
3. `file_view`
4. `doc_assist`

不会新增 patent 专属卡片、排序项或配置项。

---

## 6. 测试与验收标准

### 6.1 gateway 侧验收

必须覆盖以下场景：

1. patent `kb_qa` sync 请求命中 `ask_query` precheck / finalize
2. patent `pdf_qa` / `tabular_qa` / `hybrid_qa` sync 请求命中 `file_qa` precheck / finalize
3. patent sync 非成功响应不记账，grant 会 abort
4. patent stream 成功完成才 finalize
5. patent stream 错误、中断、上游失败时不记账且 grant 会 abort
6. patent file gate off 时不消耗 quota
7. 缺失或非法 `user_id` 时不触发 quota 调用

### 6.2 public-service / quota 侧验收

必须覆盖以下场景：

1. 既有 alias 归一化继续保持：`kb_qa` -> `ask_query`
2. 既有 alias 归一化继续保持：`pdf_qa` / `tabular_qa` / `hybrid_qa` -> `file_qa`
3. patent 原文入口继续归 `file_view`
4. patent 文档辅助入口继续归 `doc_assist`
5. `get_user_quotas`、配置列表、管理端可编辑 quota type 仍只暴露 4 个 canonical bucket

这里默认以回归测试和边界核对为主，不预设 `public-service` 一定需要新增 quota bucket 代码；只有当实现阶段确认某个 patent 入口没有走到既有 canonical 归一化路径时，才做最小补丁。

### 6.3 前端侧验收

必须满足：

1. quota 归一化后 patent ask 显示为 `ask_query` 或 `file_qa`
2. 前端 quota 页面不会出现新的 patent quota 类型
3. `frontend-vue` 构建通过

---

## 7. 实施边界与风险控制

### 7.1 这次必须实现真实计额

本次设计不是搭壳：

1. 不能只在前端显示 patent 也属于某个 quota
2. 不能只补枚举或 alias 而 ask 主链仍然不走 precheck/finalize
3. 不能只加测试桩或占位路由

真正完成的标准是：

1. patent ask 的实际成功调用会真实消耗 quota
2. patent ask 的失败调用不会误扣 quota
3. patent 原文和文档辅助维持当前已生效的 quota 归属
4. 用户可见的 sync 响应和 stream `done` 事件能看到正确的 canonical `quota.quota_type`
5. 不能只靠 mock、占位字段或只验证内部函数被调用就宣称完成，必须有能证明真实记账/不记账结果的回归证据

### 7.2 不制造第二套 quota 语义

实现时必须避免两套口径并存：

1. 不能让 backend 内部按 canonical bucket 记账，而前端另行按 patent bucket 展示
2. 不能让 gateway 把 patent 当 `file_qa`，而 public-service 又把 patent 当新类型存储
3. 不能让管理端配置的 quota type 与实际执行消耗的 quota type 不一致

### 7.3 保持兼容 rollout

本次设计不要求历史 quota 数据迁移，也不要求停机切换。

目标是：

1. 让 patent ask 从“未纳管”变成“按现有 canonical 规则纳管”
2. 不破坏现有 quota 查询、管理、前端展示和已接入的文档/原文能力

---

## 8. 最终锁定决策

本次设计最终锁定如下：

1. patent 相关能力纳入现有 quota 体系，不新增 patent 专属 quota bucket
2. `patent kb_qa` 归 `ask_query`
3. `patent pdf_qa`、`tabular_qa`、`hybrid_qa` 归 `file_qa`
4. patent 原文查看继续归 `file_view`
5. patent 文档辅助、文献辅助、抽取、翻译、摘要继续归 `doc_assist`
6. quota authority 仍然只在 `public-service`
7. gateway 负责 patent ask 主链的 quota orchestration
8. 只有真实成功完成的 patent ask / ask_stream 才记账
9. 本次改造必须接入真实功能闭环，不能以占位或空壳交付
