# Patent File Routing Design

**Date:** 2026-04-02

## Scope

本设计定义文件相关 QA 在 `fast`、`thinking`、`patent` 三种请求模式下的统一路由与执行归属。

本设计覆盖：

1. `gateway` 文件路由结果与 backend 选择规则
2. `fastQA` 与 `patent` 的文件路由合同对齐
3. `patent` 从 `kb_only` 扩展到文件 QA / 混合 QA 的协议边界
4. `thinking` 模式在文件场景继续复用 `fastQA` 的规则
5. 现有前端问答入口与现有 `gateway` 文件判定逻辑的保留策略

本设计不覆盖：

1. 前端页面、按钮、请求路径改版
2. `gateway` 文件意图判定器重写
3. `highThinkingQA` 新增文件执行能力
4. `public-service` authority 协议重构
5. `patent` 专用知识库的底层检索算法细节

---

## Goal

建立一套不复制文件判定逻辑的 mode-aware 文件路由体系：

1. `gateway` 继续作为唯一文件意图判定中心
2. 文件 QA / 混合 QA 的判定语义在所有 mode 下保持一致
3. `fast` 与 `thinking` 的文件相关请求继续落到 `fastQA`
4. `patent` 的文件相关请求改为落到 `patent`
5. `patent` 自己拥有 `pdf_qa`、`tabular_qa`、`hybrid_qa` 执行能力，并使用自己的知识库与执行链路

最终目标：

- 不再出现“所有文件问答都被压成 `actual_mode=fast`”这一条一刀切规则
- `patent` 可以独立承接普通问答、文件问答、混合问答
- 文件判定规则只有一份，不在 `patent` 再复制一套
- `fastQA` 与 `patent` 消费同一份文件路由合同，但各自执行自己的知识底座

---

## Problem Statement

当前系统已经有统一的文件判定与文件路由合同，但 backend 选择规则过于粗糙。

已确认的现状是：

1. 前端三个按钮只负责传递 `requested_mode`
2. 前端无论选择 `fast`、`thinking`、`patent`，都会把文件上下文一起交给后端
3. `gateway` 已经能正确判定 `kb_qa`、`pdf_qa`、`tabular_qa`、`hybrid_qa`
4. 但只要命中 `file_only` 或 `mixed`，`gateway` 就会把 `actual_mode` 强制改成 `fast`
5. 这意味着 `patent` 目前接不到文件 QA / 混合 QA 的正式执行流
6. 同时 `patent` Phase 1 的 ingress / egress schema 仍被锁死为 `kb_only`

这带来三个核心问题：

1. backend 归属和文件判定被耦合在一起，导致 `patent` 无法独立承接文件问答
2. 如果直接在 `patent` 再写一套文件判定，会造成规则漂移和维护负担
3. `patent` 当前协议只表达了“专利普通问答”，没有表达“专利文件问答”

---

## Current State

以下是本设计基于现有代码和文档确认的事实。

### 1. 前端入口已经满足本设计要求

前端主问答页保留三个 mode 按钮：

- `fast`
- `thinking`
- `patent`

发送请求时：

1. 路径按 mode 进入 `/fast`、`/thinking`、`/patent`
2. body 中继续携带 `requested_mode`
3. 文件上下文仍通过 `pdf_context` 透传

因此本设计不要求前端新增第四套文件入口，也不要求页面改版。

### 2. `gateway` 已经是唯一文件判定中心

`gateway` 当前已经负责：

1. 基于问题文本和文件上下文判定是否使用文件
2. 决定 `route`
3. 决定 `turn_mode`
4. 决定 `source_scope`
5. 生成 `execution_files`、`selected_file_ids`、`file_selection`

这套逻辑已经是系统里最合理的判定中心，本设计明确不复制它。

### 3. `fastQA` 已经拥有完整文件 route family

`fastQA` 当前已经消费以下 route：

1. `kb_qa`
2. `pdf_qa`
3. `tabular_qa`
4. `hybrid_qa`

并且它要求 `gateway` 显式传入文件路由合同，而不是自己重新做文件意图识别。

### 4. `highThinkingQA` 仍然不是文件执行器

本设计保持已有产品策略：

- `thinking` 模式命中普通问答时，继续走 `highThinkingQA`
- `thinking` 模式命中文件问答或混合问答时，继续落到 `fastQA`

这条策略不改变。

### 5. `patent` 当前仍是 Phase 1 `kb_only`

`patent` 现在的 request schema、response schema、mode profile、ask router 都锁定为：

- `requested_mode=patent`
- `actual_mode=patent`
- `route=kb_qa`
- `turn_mode=kb_only`
- `source_scope=kb`
- 所有文件字段为空

因此本设计的关键之一，就是把 `patent` 从单一路由扩展为完整 route family。

---

## Design Summary

本设计采用“共享判定，分 mode 执行”的结构。

### 第一层：共享文件判定

由 `gateway` 保留唯一一套文件判定逻辑，继续输出：

- `route`
- `turn_mode`
- `source_scope`
- `selected_file_ids`
- `primary_file_id`
- `execution_files`
- `file_selection`
- `allow_kb_verification`

这一层与 `requested_mode` 解耦，不因为请求是 `patent` 就改成另一套文件判定规则。

### 第二层：mode-aware backend 选择

在拿到标准化文件路由结果之后，再根据 `requested_mode` 选择执行 backend。

规则如下：

1. `requested_mode=fast`
   - `kb_qa` -> `fastQA`
   - 文件 QA / 混合 QA -> `fastQA`

2. `requested_mode=thinking`
   - `kb_qa` -> `highThinkingQA`
   - 文件 QA / 混合 QA -> `fastQA`

3. `requested_mode=patent`
   - `kb_qa` -> `patent`
   - 文件 QA / 混合 QA -> `patent`

### 第三层：route family 共享，执行底座分离

`fastQA` 与 `patent` 共享相同的外部 route family：

- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

但两者不共享知识库，也不要求强行共享内部执行代码。

产品语义保持一致：

- “PDF 文件问答”在两个 backend 中都表示“基于 PDF 原文回答”
- “表格问答”在两个 backend 中都表示“基于表格执行问答或操作”
- “混合问答”在两个 backend 中都表示“组合型数据源问答”，其合法组合由共享 route contract 明确定义

执行底座保持分离：

- `fastQA` 使用现有 `fastQA` 知识库与链路
- `patent` 使用自己的专利知识库与自己的执行链路

---

## Core Principles

## 1. 文件判定只有一份

文件意图识别、显式文件选择、歧义澄清、`source_scope` 推导，只保留在 `gateway`。

禁止在 `patent` 再写第二套“用户到底是不是要问文件”的判定器。

## 2. backend 选择晚于文件判定

先判定本轮是不是 `pdf_qa`、`tabular_qa`、`hybrid_qa`，再决定由谁执行。

不要把“文件判定”和“backend 归属”揉成一个规则块，否则扩展第三个执行 backend 时会继续膨胀。

## 3. `requested_mode` 决定执行域，不重写文件语义

`requested_mode` 的职责是选择知识域与执行 backend：

- `fast` -> 通用 fast 域
- `thinking` -> 普通问答走 thinking 域，但文件问答仍复用 fast 文件域
- `patent` -> 专利域

`requested_mode` 不应该改变“什么叫 PDF QA、什么叫混合 QA”。

## 4. route contract 应对 backend 一视同仁

`gateway` 发给 `fastQA` 的文件路由合同，和发给 `patent` 的文件路由合同应保持同一结构。

这样可以避免：

1. `gateway` 维护两套文件协议
2. 前端需要理解 backend 差异
3. 后续审计和日志无法统一

## 5. `patent` 扩容的是执行能力，不是判定逻辑

`patent` 的新增工作重点应当是：

1. 扩 ingress / egress schema
2. 扩 mode profile
3. 扩 route dispatch
4. 扩文件 / 混合执行器

而不是把 `gateway` 的文件判定规则移植进去。

---

## Routing Policy Matrix

统一路由矩阵如下。

| requested_mode | route / turn_mode | actual_mode | target backend |
|---|---|---|---|
| `fast` | `kb_qa` / `kb_only` | `fast` | `fastQA` |
| `fast` | `pdf_qa` / `file_only` | `fast` | `fastQA` |
| `fast` | `tabular_qa` / `file_only` | `fast` | `fastQA` |
| `fast` | `hybrid_qa` / `mixed` | `fast` | `fastQA` |
| `thinking` | `kb_qa` / `kb_only` | `thinking` | `highThinkingQA` |
| `thinking` | `pdf_qa` / `file_only` | `fast` | `fastQA` |
| `thinking` | `tabular_qa` / `file_only` | `fast` | `fastQA` |
| `thinking` | `hybrid_qa` / `mixed` | `fast` | `fastQA` |
| `patent` | `kb_qa` / `kb_only` | `patent` | `patent` |
| `patent` | `pdf_qa` / `file_only` | `patent` | `patent` |
| `patent` | `tabular_qa` / `file_only` | `patent` | `patent` |
| `patent` | `hybrid_qa` / `mixed` | `patent` | `patent` |

这个矩阵取代当前“一旦涉及文件，一律 `actual_mode=fast`”的规则。

---

## Shared Route Contract

本设计保留现有文件路由合同的主体结构。

### Required routing fields

所有文件相关请求仍由 `gateway` 显式下发以下字段；这也是 `fastQA` 与 `patent` 必须共同接受的权威 request contract：

1. `requested_mode`
2. `actual_mode`
3. `route`
4. `turn_mode`
5. `source_scope`
6. `kb_enabled`
7. `allow_kb_verification`
8. `selected_file_ids`
9. `primary_file_id`
10. `execution_files`
11. `file_selection`

`patent` 只能消费这份 canonical contract，并在本地做字段校验；不能再基于问题文本或文件集合重新推导文件意图、重新 canonicalize `source_scope`，也不能把 route family 收敛回 `kb_qa`。

### Route family meanings

#### `kb_qa`

只使用 backend 自己的知识域。

#### `pdf_qa`

只使用选中的 PDF 文件，不引入知识库补充。

#### `tabular_qa`

只使用选中的表格文件，不引入知识库补充。

#### `hybrid_qa`

`hybrid_qa` 的唯一规范定义如下：

- 它表示“组合型数据源问答”
- 它既可以表示“文件 + backend 知识库”
- 也可以表示“多文件族联合执行但不带知识库”

允许的 `source_scope` 只有以下四种：

1. `pdf+kb`
2. `table+kb`
3. `pdf+table`
4. `pdf+table+kb`

不在以上列表中的组合都不属于合法 `hybrid_qa`。

`source_scope` 应继续沿用现有 canonical serialization 规则，使用稳定的小写 token 顺序表达组合范围，例如：

- `pdf+kb`
- `table+kb`
- `pdf+table`
- `pdf+table+kb`

`gateway`、`fastQA`、`patent` 都不应各自发明不同的拼接顺序或别名。

### `allow_kb_verification`

本布尔值继续保留，但语义受 `route + source_scope` 约束。

在文件相关 turn 中：

- 若 `route=hybrid_qa` 且 `source_scope` 包含 `kb`，则表示允许 backend 将自身知识库并入该轮执行
- 若 `route=pdf_qa` 或 `tabular_qa`，则表示纯文件执行，不应借该字段绕开 route contract

也就是说，混合语义以 `hybrid_qa` 为主表达，不再依赖单独的布尔值偷渡复杂路由。

---

## Gateway Design Changes

`gateway` 的变更重点不是重写文件判定，而是拆开“判定”与“backend 选择”。

### 1. 保留现有 file context resolver

以下逻辑不变：

1. 显式文件引用解析
2. last focus 复用
3. selected ids 处理
4. mixed intent 检测
5. clarification 行为
6. `route / turn_mode / source_scope` 推导

### 2. 替换当前 `actual_mode` 一刀切规则

当前规则：

- 只要 `turn_mode in {file_only, mixed}`，就把 `actual_mode` 设为 `fast`

新规则：

1. 先生成标准化文件路由结果
2. 再根据 `requested_mode + route` 映射成 `actual_mode` 与目标 backend

### 3. backend 选择策略应显式建模

`gateway` 内部应将以下两个问题分开：

1. 本轮问答是什么 route
2. 这个 route 在当前 `requested_mode` 下该交给谁执行

推荐内部设计：

- route decision service 继续只负责路由结果
- 新增或重构 backend selection policy，专门负责 `requested_mode + route -> actual_mode + service target`

是否引入新的内部 helper 名称不是本设计关注点，但职责边界必须清楚。

---

## Patent Service Expansion

`patent` 必须从当前 Phase 1 `kb_only` 服务，扩展为完整 route family 服务。

### 1. Ingress schema expansion

`patent` request parser 需要从当前的单一路由限制，扩展为支持：

1. `route=kb_qa`
2. `route=pdf_qa`
3. `route=tabular_qa`
4. `route=hybrid_qa`

并接受与这些 route 对应的共享合同字段：

1. `turn_mode`
2. `source_scope`
3. `kb_enabled`
4. `allow_kb_verification`
5. `execution_files`
6. `selected_file_ids`
7. `primary_file_id`
8. `file_selection`

其中：

- `kb_enabled` 与 `allow_kb_verification` 不是 backend 私有补丁，而是共享 route contract 的组成部分
- `patent` 不得通过“接受但不建模”规避这些字段

### 2. Response schema expansion

`patent` sync response 和 SSE event schema 也需要放开：

1. `route` 不再只允许 `kb_qa`
2. `source_scope` 不再只允许 `kb`
3. `used_files` 允许非空
4. `file_selection` 允许非空

### 3. Mode profile expansion

`patent` 不应再只有单个 `PatentModeProfile(kb_qa)`。

需要支持：

1. `patent kb_qa`
2. `patent pdf_qa`
3. `patent tabular_qa`
4. `patent hybrid_qa`

这里可以是单 profile + route-aware dispatch，也可以是多个 profile；本设计不强制具体代码组织，但要求对外语义完整。

### 4. Executor expansion

`patent` 需要拥有自己的 route dispatch：

1. `kb_qa` -> 现有专利 KB 链路
2. `pdf_qa` -> 专利 PDF 文件链路
3. `tabular_qa` -> 专利表格链路
4. `hybrid_qa` -> 专利混合链路

执行输入仍以 `gateway` 下发的 route contract 为准，而不是再次基于问题文本推测。

---

## Patent Execution Parity Requirements

`patent` 与 `fastQA` 的对齐目标是“用户可见语义一致”，不是“内部实现必须拷贝一份”。

### 1. PDF QA parity

`patent pdf_qa` 应与现有 `fastQA pdf_qa` 对齐以下产品语义：

1. 以上传的 PDF 原文为主数据源
2. 结果包含文件使用痕迹与引用输出
3. 支持单 PDF 与多 PDF 的已选文件执行边界

### 2. Tabular QA parity

`patent tabular_qa` 应支持与现有表格问答一致的用户语义：

1. 基于表格字段或行列执行问答
2. 输出结构化步骤与必要的结果表示

### 3. Hybrid QA parity

`patent hybrid_qa` 应支持与现有 `fastQA` 同一层级的混合语义：

1. `pdf+kb`
2. `table+kb`
3. `pdf+table`
4. `pdf+table+kb`

其中 “知识库” 明确指 `patent` 自己的知识库，而不是 `fastQA` 的知识库。

### 4. Mixed semantics remain backend-local

虽然 route contract 共享，但“混合执行时知识库怎么参与”是 backend 内部实现域：

- `fastQA` 使用现有 fast 知识域
- `patent` 使用专利知识域

因此两个 backend 的内部 prompt、检索召回、证据排序可以不同，但 route 语义必须一致。

---

## Persistence And Metadata

本设计不重构 authority 主流程，但要求 mode 元信息在文件场景下保持一致。

### 1. `fast` / `thinking` 文件请求

对于 `requested_mode=thinking` 且文件相关的请求：

1. `requested_mode` 应保留为 `thinking`
2. `actual_mode` 应落为 `fast`
3. 持久化与日志中应能解释这是“thinking 请求经 compatibility routing 进入 fast 文件执行”

### 2. `patent` 文件请求

对于 `requested_mode=patent` 且文件相关的请求：

1. `requested_mode=patent`
2. `actual_mode=patent`
3. 不再走现有 “patent file/mixed compatibility rewrite to fast” 逻辑

这意味着后续 authority / metadata 处理不应再把 patent 文件 turn 伪装成 fast turn。

需要一起更新的层包括：

1. authority request / response schema 中的 `route / source_scope / actual_mode`
2. durable chat persistence 写入的 turn metadata
3. sync response builder 与 SSE metadata / done event 中暴露的 `route / source_scope / used_files / file_selection`

---

## Error Handling Expectations

统一规则如下：

1. 文件澄清仍由 `gateway` 决定并返回
2. 文件不存在、未就绪、处理失败仍沿用现有路由错误语义
3. `fastQA` 与 `patent` 都应对不合法 route contract 做显式失败，而不是静默降级
4. `patent` 对文件 route contract 的校验风格应与 `fastQA` 保持一致：缺关键字段就拒绝，不自己猜

---

## Migration Strategy

采用分阶段迁移。

### Rollout safety rule

在 `patent` 完整具备文件 ingress schema、response schema、route dispatch、文件执行链之前：

1. 生产流量不得把 `requested_mode=patent` 的文件相关请求正式切入 `patent`
2. Phase 1 和 Phase 2 可以先完成内部重构、测试、feature gate 与非生产联调
3. 只有在 Phase 3 完成并验证通过后，才允许打开 `patent` 文件流量

也就是说，本设计允许先做架构准备，但不允许在 `patent` 尚未具备文件执行能力时提前切流。

### Phase 1: 路由政策重构

1. 保留 `gateway` 文件判定逻辑
2. 重构 `gateway` backend 选择策略
3. 为 `requested_mode=patent` 文件流量预留 mode-aware backend 选择能力
4. 默认仍由 feature gate 或等价 rollout 开关阻止生产流量提前进入 `patent`
5. mode-aware backend 选择与 gateway 侧 patent file gate 必须在同一 rollout batch 落地，不能单独先放出“切到 patent”而不同时带上 gate

### Phase 2: `patent` 协议扩容

1. request schema 扩容
2. response / event schema 扩容
3. mode profile 扩容
4. route-aware dispatch scaffold 落地，但此时仍可不打开生产文件流量

### Phase 3: `patent` 文件执行链落地

1. `pdf_qa`
2. `tabular_qa`
3. `hybrid_qa`
4. 在验证完成后打开 `requested_mode=patent` 文件流量

### Phase 4: compatibility cleanup

在 `patent` 正式拥有文件执行能力之后：

1. 删除或收敛旧的 patent file-to-fast compatibility 假设
2. 更新文档与测试，确保没有残留“patent 文件 turn 必须转 fast”的旧契约

---

## Alternatives Considered

### 方案 A：在 `patent` 再写一套文件判定器

不采用。

原因：

1. 判定规则会漂移
2. 两套规则难以保持一致
3. `gateway` 作为统一路由中心的职责会被破坏

### 方案 B：所有 mode 的文件问答仍统一落 `fastQA`

不采用。

原因：

1. 不满足 `patent` 自己承接文件问答的目标
2. `patent` 无法建立独立知识域
3. 会继续把 patent 文件能力压成兼容层，而不是一等能力

### 方案 C：让 `thinking` 文件问答切到 `highThinkingQA`

本设计明确不采用。

原因：

1. 当前产品策略不是这样
2. 用户已明确要求 `thinking` 文件场景继续落 `fastQA`
3. 这会扩大实现面，不是本轮目标

---

## Acceptance Criteria

当以下条件同时成立时，本设计可视为完成落地目标：

1. 前端无需改动，仍沿用现有三个按钮和现有请求路径
2. `gateway` 文件判定逻辑保持单一来源
3. `requested_mode=thinking` 的文件相关请求继续路由到 `fastQA`
4. `requested_mode=patent` 的文件相关请求路由到 `patent`
5. `patent` 对外支持 `kb_qa`、`pdf_qa`、`tabular_qa`、`hybrid_qa`
6. `fastQA` 与 `patent` 都消费统一的文件路由合同
7. 系统中不再存在“所有文件相关请求必定 `actual_mode=fast`”这一全局规则

---

## Open Questions Deferred To Planning

以下问题需要在 implementation plan 中进一步拆解，但不阻塞本 spec：

1. `gateway` 内部 backend selection policy 的具体文件落点
2. `patent` route dispatch 应使用单 executor 还是按模块拆分
3. `patent` 是否复用 `fastQA` 的部分文件处理 helper，还是完全自建
4. `patent hybrid_qa` 中知识库参与的具体 prompt / retrieval 细节
5. authority 层对 `requested_mode=thinking` 且 `actual_mode=fast` 的 metadata 呈现细节
