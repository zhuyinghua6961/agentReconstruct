# 配额管理统一设计 Spec

## 1. 目标

把当前系统的配额管理收口到一套用户能理解、后端能稳定执行、前后端展示一致的模型。

本次设计聚焦：

1. 明确普通用户、超级用户、管理员的配额语义
2. 明确问答、文件问答、查看原文、文档辅助能力的配额边界
3. 明确配额检查与记账时机
4. 明确 `gateway`、`public-service`、`fastQA`、`highThinkingQA` 的职责边界

不在本次设计范围内：

1. 计费系统
2. 充值、套餐、账单
3. 复杂的多租户配额模型
4. 基于 token、字数、时长的精细化资源计量
5. `patent` 模式问答链路的配额收口

说明：

- `patent` 路由当前不纳入本轮 quota model
- 本轮只覆盖普通 QA、文件 QA、查看原文、文档辅助四类能力
- 若后续 `patent` ask 链路恢复为正式能力，应单独补一轮 quota spec，而不是在本轮默认归桶

---

## 2. 当前事实基线

### 2.1 用户类型字段

当前系统里，配额判断的权威字段应当是 `user_type`，不是 `role`。

已确认码表：

- `user_type = 1`：管理员
- `user_type = 2`：超级用户
- `user_type = 3`：普通用户

当前 `public-service` 的配额免限判断已经按这套规则执行：

- `1 / 2` 免限
- `3` 受限

### 2.2 当前活跃配额中心

当前真正生效的配额中心在 `public-service`。

现有能力包括：

- 配额配置读取与管理
- 配额检查
- 配额递增
- Redis / MySQL 锁保护
- 管理员查看/重置用户配额

### 2.3 当前活跃配额问题

当前存在两个明显问题：

1. 前端可配置的 quota type 与后端真实消费点不完全一致
2. QA 主链配额尚未真正收口到统一模型

---

## 3. 设计原则

### 3.1 用户体验优先

用户只应感知少量、稳定、可解释的配额类别。

不能把内部路由细节直接暴露成一堆 quota type，否则：

- 用户难理解
- 管理后台难配置
- 后续前后端一致性难维护

### 3.2 用户类型判断统一看 `user_type`

配额免限与受限规则统一按 `user_type` 判断：

- `user_type = 1`：管理员，免限
- `user_type = 2`：超级用户，免限
- `user_type = 3`：普通用户，受限

`role` 只用于管理权限与界面语义，不作为普通/超级用户配额判断的主依据。

### 3.3 问答配额按用户可理解能力分组，不按内部 route 细分

内部可能有：

- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`
- `thinking_qa`

但配额模型不直接暴露这些内部执行细节，而是按用户理解的能力分成有限几类。

### 3.4 成功返回后才记账

本次设计明确采用：

- 失败请求不扣
- 成功请求才扣
- 流式问答不在入口扣
- 流式问答在成功完成后记账

这样更符合用户预期，也更容易解释。

### 3.5 配额控制中心仍放在 `public-service`

即使问答执行分散在 `fastQA`、`highThinkingQA`，配额中心仍应由 `public-service` 统一承接。

原因：

- 用户与管理员只需要理解一个权威配额中心
- 管理后台、用户配额展示、配额配置都应来自同一服务
- 避免每个 QA backend 各自维护一套配额状态

---

## 4. 目标配额模型

本次设计建议把当前系统收口到 4 个 quota type。

### 4.1 `ask_query`

语义：

- 普通问答配额
- 不带文件上下文的 QA 都算这里
- 不区分 `fastQA` / `highThinkingQA`

包含：

- `fast` 普通知识库问答
- `thinking` 普通知识型问答
- 不涉及上传文件上下文的普通会话问答

不包含：

- 任何进入文件问答链路的请求

### 4.2 `file_qa`

语义：

- 所有基于文件的问答配额
- 纯文件 QA 与混合 QA 共用同一个额度池

包含：

- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`
- “结合文件内容和知识库一起回答”的混合 QA
- 任何只要用了会话文件上下文的问答

不再单独拆：

- `pdf_qa` 配额
- `tabular_qa` 配额
- `hybrid_qa` 配额

### 4.3 `file_view`

语义：

- 查看原文配额

包含：

- DOI PDF 原文查看
- 会话上传文件下载/查看
- 与“打开原文/查看原文”直接等价的能力

### 4.4 `doc_assist`

语义：

- 文档辅助能力配额

包含：

- 翻译
- 原文总结
- `reference_preview`
- `literature_content`
- `extract_pdf_text`

不再细分成多个配额桶。

认证与兼容策略：

- 进入用户会话、已登录前端、管理员界面的文档辅助调用，统一按 `doc_assist` 计额
- 当前仍保留的匿名兼容直连接口，不纳入本轮用户配额模型
- 也就是说，本轮 `doc_assist` 的计额主体必须是已认证用户
- 匿名兼容调用在本轮保持可用，但不应被误记到某个用户名下
- 后续若决定收紧匿名入口，应另开一轮接口策略迁移，而不是在本轮配额改造里隐式改变行为

---

## 5. 路由与配额映射规则

### 5.1 QA 主链映射

对于问答主链，请求在 gateway 已完成路由决策后，再映射到配额类别：

- 不带文件上下文：`ask_query`
- 只要进入文件问答链路：`file_qa`

这里不区分：

- `fastQA` vs `highThinkingQA`
- `pdf_qa` vs `tabular_qa` vs `hybrid_qa`

因为这些是内部执行路径，不应直接暴露给配额模型。

### 5.2 文件查看映射

所有“查看原文 / 下载原文 / 打开 PDF”动作统一映射到 `file_view`。

### 5.3 文档辅助接口映射

以下接口统一映射到 `doc_assist`：

- 翻译
- 原文总结
- `reference_preview`
- `literature_content`
- `extract_pdf_text`

补充规则：

- 只有存在明确认证用户主体的调用才进入 `doc_assist` 配额检查与记账
- 当前匿名兼容调用不计入 `doc_assist`
- 因此，本轮“文档辅助能力纳入配额管理”的范围，实际是“已认证用户发起的文档辅助能力”

---

## 6. 记账语义

### 6.1 基本规则

统一采用：

- `precheck`：做可用性检查
- `finalize`：仅在成功结果成立后记账

### 6.2 同步接口

同步接口在满足以下条件时记账：

- HTTP 成功状态
- 返回 payload 语义成功

“payload 语义成功”的最低判定口径：

- 若响应体包含 `success` 字段，则必须 `success != false`
- 若响应体包含 `error` 字段，则必须 `error` 为空
- 若响应体同时出现 `success=false`、非空 `error`、明确失败 code，则视为失败，不记账
- 若响应体不是可解析 JSON，则由调用方按接口类型定义 success 判定，但不能仅凭 HTTP 200 就直接扣额

### 6.3 流式接口

流式问答不在进入请求时记账。

流式问答仅在满足以下条件时记账：

- 上游执行成功完成
- 产生了有效 `done` 事件
- 最终结果被判定为成功

不应在以下时刻扣费：

- 仅建立流连接
- 只收到中间 step/progress 事件
- 中途异常终止
- 超时失败

### 6.4 失败语义

统一原则：

- 配额检查失败：直接阻断
- 配额配置缺失：
  - strict 能力按 strict 规则处理
  - non-strict 能力可按现有放行策略执行
- 递增失败：
  - 对高价值用户体验路径优先采用 soft warning
  - 但必须有日志与指标

本轮进一步锁定：

- 对已经成功生成业务结果的用户面路径，若只是在 `finalize` / quota increment 阶段失败，应优先返回业务成功结果
- 此类失败默认按 soft warning 处理，不应因为记账失败把已成功的问答、查看原文、文档辅助结果整体改写成 5xx
- soft warning 至少应包含：
  - 结构化日志
  - 可观测指标
  - 供调用方识别的 warning / counted 标记
- 只有在安全性或一致性必须 fail-closed 的路径，才应显式定义为硬失败；本轮这 4 类用户面主能力默认不走这条路

补充锁定：

- 对已认证用户生效的 `doc_assist` 能力，本轮统一按 strict 处理
- 即：`summarize_pdf`、`translate`、已认证态的 `reference_preview`、`literature_content`、`extract_pdf_text`，都不应继续出现“同 bucket 下部分 strict、部分 non-strict”的分裂行为
- 若 `doc_assist` 配额配置缺失、inactive、后端不可用或检查失败，已认证用户调用都应按 strict 规则返回失败
- 匿名兼容调用不进入本轮配额模型，因此不参与这里的 strict/non-strict 讨论

---

## 7. 服务职责边界

### 7.1 `gateway`

负责：

- 接收前端请求
- 解析当前会话文件上下文
- 做 route decision
- 判定本轮属于普通 QA 还是文件 QA
- 作为 QA 主链的配额编排层，向 `public-service` 发起 ask-chain 的配额预检查与成功后记账
- 把标准化结果传给后端执行链路

不负责：

- 持久保存配额状态
- 自己维护配额配置

### 7.2 `public-service`

负责：

- 配额配置管理
- 配额检查与记账
- 用户配额查询与管理员配额管理
- 权威配额中心

### 7.3 `fastQA`

负责：

- 执行 fast 普通 QA
- 执行文件 QA
- 执行混合 QA
- 返回可用于 `gateway` 判定问答是否成功完成的执行结果
- 不作为 ask-chain 配额分类与编排中心

### 7.4 `highThinkingQA`

负责：

- 执行 thinking 普通 QA
- 返回可用于 `gateway` 判定问答是否成功完成的执行结果
- 不作为 ask-chain 配额分类与编排中心

不负责文件 QA 和混合 QA 的长期能力承接。

### 7.5 上传与历史兼容态

本轮目标 quota model 只有 4 个用户可见 bucket：

- `ask_query`
- `file_qa`
- `file_view`
- `doc_assist`

因此，现有上传相关 quota type 的兼容策略明确如下：

- `file_upload`：视为退役中的 legacy quota type
- 会话绑定上传在本轮不再进入用户可见 quota model
- 现存 `file_upload` 配置、历史 usage、管理员 reset 能力在兼容期内可保留，但不应继续作为主模型对外展示
- `excel_upload`：若管理员批量导入仍需内部节流，可暂保留为内部 legacy 能力
- `excel_upload` 不进入普通用户可见 quota model，也不应在 canonical UI 中继续暴露

---

## 8. 管理后台与用户侧展示要求

### 8.1 管理后台

管理后台应能配置并展示以下 4 个 quota type：

- `ask_query`
- `file_qa`
- `file_view`
- `doc_assist`

管理员不需要感知内部 route 细分。

补充要求：

- canonical 4 桶的收口权威在后端，不在前端
- 也就是说，`public-service` 返回给活跃管理后台和用户侧的 quota 配置/配额数据，应默认已经是 canonical 视图
- 这个后端权威收口不仅包括读取，也包括配置更新与管理员 reset 路径
- 管理员配额设置页面的“可新建 quota type 列表”“配置列表展示”“编辑入口”“reset 目标类型”，都必须与 canonical 4 桶协议一致
- 管理员界面不应继续把 `file_upload`、`pdf_summary`、`text_translate` 等 legacy 类型当作主配置入口
- 若兼容期内仍保留 legacy 数据，也应由后端负责把活跃管理后台所见视图收口成 canonical 结果，而不是让前端自行猜测和过滤
- canonical UI 只展示 4 个主 quota type
- legacy quota type 在兼容期内可以保留底层配置和历史数据，但不应在主界面创建入口或默认展示中继续出现

### 8.2 用户侧

普通用户看到的配额也应按这 4 类展示。

不应展示：

- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`
- `thinking_qa`

这些内部 route 维度不适合作为用户配额心智模型。

---

## 9. 风险与注意事项

### 9.1 `role` 与 `user_type` 混用风险

若继续在不同代码路径里混用 `role` 和 `user_type` 判断，会导致：

- 超级用户被误限额
- 普通用户被误放行
- 管理后台显示与后端判断不一致

### 9.2 QA 主链配额接入点选择不当风险

如果把问答配额散落到多个 backend 各自独立维护，会导致：

- 配额中心漂移
- 管理后台配置与真实执行不一致
- 多模式联调难以排障

### 9.3 流式问答记账点选择不当风险

如果在请求进入就记账，会导致：

- 失败流也被扣
- 用户体验差
- 投诉成本高

### 9.4 文档辅助接口过细拆分风险

如果把翻译、总结、文献辅助接口拆成太多 quota type，会导致：

- 后台配置复杂
- 用户理解成本高
- 前后端配置矩阵难维护

---

## 10. 设计结论

本次建议把配额模型统一收口为：

- `ask_query`
- `file_qa`
- `file_view`
- `doc_assist`

并统一采用：

- `user_type = 1/2` 免限
- `user_type = 3` 受限
- 成功返回后才记账
- ask-chain 配额由 `gateway` 编排，`public-service` 记账，QA backend 不单独承担 ask-chain 配额中心职责
- `doc_assist` 只对已认证用户调用计额，匿名兼容调用不在本轮配额模型内

这是当前系统里最稳、最容易解释、也最容易在 `gateway + public-service + fastQA + highThinkingQA` 四层边界内落地的方案。

---

## 11. Review Checklist

本轮 spec 自检结论：

- 已明确用户类型与字段码表
- 已明确 quota type 收口模型
- 已明确普通 QA / 文件 QA / 查看原文 / 文档辅助四类边界
- 已明确成功后记账规则
- 已明确不以 `role` 区分超级用户与普通用户
- 已明确混合 QA 归入 `file_qa`

待下一阶段在 implementation plan 中继续细化的点：

- QA 主链具体接入点选型
- 统一错误码和前端提示文案
