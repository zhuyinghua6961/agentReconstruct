# PatentQA 交付级详细规格说明

## 文档状态

- 最后更新：2026-03-30
- 文档目标：定义 `patentQA` 从“当前已有 Phase 1 基础设施”走到“可正常提供普通专利问答、可持久化、可查看专利原文、具备缓存和多实例一致性”的完整交付规格
- 当前阶段：spec only，不写代码
- 适用读者：`gateway`、`patentQA`、`public-service`、前端、检索/数据平台、联调与上线 reviewer

本文是交付规格，不是概念设计文档。它必须回答以下问题：

1. `patentQA` 首期到底做什么，不做什么
2. `gateway -> patentQA -> public-service` 端到端普通问答如何流转
3. 在还没有向量数据库的前提下，首期检索 MVP 如何定义成可直接实现的 contract
4. “查看专利原文”应如何围绕专利号而不是 DOI 设计
5. Redis、多实例一致性、缓存、幂等、overlay、性能预算分别如何落地
6. 后续接手专利系统的人看完就知道该实现哪些接口、数据模型、缓存规则和验收标准

---

## 1. 结论先行

### 1.1 首期定位

`patentQA` 首期是独立 QA backend，但只承接 `mode=patent` 下的普通问答：

- `requested_mode = patent`
- `actual_mode = patent`
- `route = kb_qa`
- `turn_mode = kb_only`
- `source_scope = kb`

它不承接：

- 文件问答
- 表格问答
- 混合问答
- 专利文件上传解析
- 向量检索依赖下的复杂召回

### 1.2 首期目标能力

首期 `patentQA` 必须具备以下可交付能力：

1. 能被 `gateway` 作为 `patent` backend 正常调用
2. 能执行普通专利问答
3. 能在 durable 会话下把聊天持久化委托给 `public-service`
4. 能输出适配前端的 sync / SSE 响应
5. 能让用户查看专利原文，但原文定位主键是专利 canonical id，而不是 DOI
6. 能在多实例部署下保证同一会话不双写、不双执行、不乱序 accept
7. 能使用 Redis 提供执行缓存、检索缓存、原文缓存、overlay、锁与幂等
8. 在尚未接入向量数据库时，仍然能以“能力受限但可上线”的方式交付普通专利问答 MVP

### 1.3 首期最重要的边界

必须严格坚持以下边界：

- `gateway` 负责统一入口、模式路由、标准化 payload、对外代理专利原文查看接口
- `patentQA` 负责普通专利问答执行、原文查看编排、运行时控制
- `public-service` 负责 durable transcript 真相源与上下文快照
- Redis 只做协调和缓存，不做 durable transcript 真相源
- 专利原文查看的一切链接、缓存和引用都以专利 canonical id 为主键

### 1.4 必须提前写清楚的系统兼容边界

虽然本 spec 的实现重点是 `kb_only` 普通专利问答，但系统层面还必须定义一个兼容事实：

- 当 `requested_mode=patent` 且 `turn_mode in {file_only, mixed}` 时，当前 owner 仍是 `fastQA`

为了避免 durable conversation 在跨 QA owner 切换时断裂，首期必须同时规定兼容 contract：

- `gateway` 把这类 turn 转发给 `fastQA` 时，必须把 authority-facing 执行 mode tuple 重写为：
  - `requested_mode = fast`
  - `actual_mode = fast`
- 同时保留非权威元数据：
  - `options.mode_origin.requested_mode = patent`
  - `options.mode_origin.compatibility_route = true`
- `public-service` durable transcript 侧，兼容 turn 的 authority 校验与持久化均以重写后的 `fast/fast` tuple 为准
- `patentQA` 本身不执行这些 turn，但本 spec 必须明确它们的 durable continuity 不能成为系统黑洞

原因很简单：当前 `public-service` 仍不接受 `fastQA + requested_mode=patent` 的 authority 写入，若不做这条 rewrite 规则，专利会话一旦离开 `kb_only`，durable 聊天就会断。

---

## 2. 当前事实与目标态区分

### 2.1 当前已存在的事实

当前仓库中已经成立的事实：

- `gateway` 已预留 `patent` mode 路由入口
- `patent/` 目录里已经有独立 FastAPI 服务骨架
- `patent/` 已有 durable / ephemeral 双路径、authority client、Redis 执行缓存、执行锁、overlay、Gunicorn 包装
- `public-service` 是后续新 QA durable transcript 的 owner，这一点已在既有协议文档中确认
- `fastQA` 的普通问答链路已经证明：authority-first 上下文装配 + Redis 缓存 + 流式 done 后持久化 这一套形态是可行的

### 2.2 当前尚未具备的能力

当前还没有真正打通的能力：

- 真实专利召回、排序、引用抽取
- 基于专利 canonical id 的原文查看端到端协议
- `public-service` 对 `source_service=patentQA` 的正式 allowlist / schema 放行
- 前端的专利引用对象展示与原文跳转协议
- 无向量库前提下的首期检索数据源定义
- `gateway` 对专利原文查看的代理入口
- `patentQA` 与 `fastQA` / `highThinkingQA` 的 shared overlay 统一

### 2.3 本文定义的目标态

本文定义的目标态不是“一步到位的最终专利平台”，而是“首期可上线、可扩展、不返工的普通专利问答系统”。

这个目标态具备三条原则：

1. 先把普通问答、持久化、原文查看、缓存和一致性做对
2. 不等待向量数据库到位，先用结构化检索 + 词法检索 + 精确号查询交付 MVP
3. 所有协议和模型都要为未来的专利专用检索链留扩展位，而不是把首期实现写死

---

## 3. 方案比较

### 3.1 方案 A：完全复用 fastQA 普通问答骨架，在 patentQA 内替换检索与引用模型

这是推荐方案。

做法：

- `gateway` 仍统一调用 `/api/patent/ask` 与 `/api/patent/ask_stream`
- `patentQA` 的 ask 生命周期与 `fastQA` 普通问答保持同构
- 专利检索、原文引用、缓存 key 设计放在 `patent/` 内独立演进
- durable transcript 仍委托 `public-service`

优点：

- 与现有 QA 基座一致，接入成本最低
- 可以直接复用 `fastQA` 已证明有效的请求适配、上下文装配、Redis 协调和 SSE 契约思路
- 后续向量检索接入时只需替换 `patentQA` 内部 pipeline，不需要改跨服务协议

缺点：

- 首期为了保持同构，会保留一部分通用 QA 协议字段，看起来不够“专利专用”

### 3.2 方案 B：先做一个极简专利聊天服务，等向量库到位后再重写为完整 QA

不推荐。

优点：

- 首期实现最省事

缺点：

- 很容易形成临时协议
- durable、原文查看、Redis、一致性设计大概率要重做
- 前端和 gateway 要经历两轮适配

### 3.3 方案 C：现在就定义最终专利专用协议，与现有 QA 基座明显分叉

不推荐。

优点：

- 域模型理论上最纯粹

缺点：

- 当前没有向量库，也没有最终检索方案，过早专用化很容易设计过度
- 会破坏现有 `gateway / public-service / QA service` 的接入一致性
- reviewer 和后续开发者理解成本更高

### 3.4 结论

采用方案 A。

也就是：

- 跨服务协议尽量与现有普通 QA 基座保持一致
- `patentQA` 只在检索、引用、原文查看、缓存 key、领域模型这些地方做专利化扩展

---

## 4. 首期交付范围

### 4.1 In Scope

首期必须交付：

- `gateway -> patentQA` 的普通问答链路
- durable 与 ephemeral 双模式
- `public-service` authority 接口接入
- 会话上下文读取与历史合并
- sync ask 与 stream ask
- Redis 锁、幂等、inflight、execution cache、retrieval cache、original cache、shared overlay
- 专利引用对象与原文跳转协议
- 专利原文查看接口与缓存设计
- Gunicorn 多 worker 部署模型
- readiness / health / rollout gate
- 错误面与可观测性
- 与 `fastQA` / `highThinkingQA` 共享 overlay contract

### 4.2 Out of Scope

首期明确不交付：

- 文件/混合专利问答由 `patentQA` 自己执行
- 专利上传与解析链路
- 向量库建库与 embedding 召回
- 法律意见型高风险答案审查系统
- 专利家族图谱等复杂知识图谱功能

### 4.3 首期上线标准

达到以下标准才算“可上线”：

- 普通专利问答能在 sync 和 stream 模式下稳定返回
- durable ask 能完成 user write -> snapshot read -> execute -> assistant accept 闭环
- 原文查看可以通过 canonical patent id 正常打开或读取专利原文
- 多实例下同一 `conversation_id + trace_id` 不会重复执行
- Redis 不可用时 durable 明确失败、ephemeral 仍可运行
- authority 不可用时 durable 明确失败、不会错误返回 done
- 前端可以对回答中的每个专利引用执行稳定跳转
- 跨 `patentQA -> fastQA` owner 切换时，上下文连续性不丢失

---

## 5. 总体架构

### 5.1 服务拓扑

```text
Frontend
  -> gateway
    -> patentQA
      -> public-service
      -> patent retrieval source
      -> patent original source
      -> redis
```

其中：

- `patent retrieval source`
  - 首期不是向量数据库
  - 首期最小可用形态必须是“专利目录索引 + 全文检索索引”
- `patent original source`
  - 负责根据 canonical patent id 返回原文内容或原文链接
  - 可以与检索源相同，也可以分离

### 5.2 核心边界

#### Gateway

负责：

- 标准 ask 接口
- 鉴权透传
- trace 透传
- mode 路由
- 普通 / 文件 / 混合的 route 决策
- 代理转发
- 对外暴露且代理 `/api/patent/original/{canonical_patent_id}`
- 对 compatibility-routed patent file/mixed turns 执行 `requested_mode/actual_mode` rewrite

不负责：

- 专利检索执行
- durable transcript 真相源
- 专利原文解析

#### PatentQA

负责：

- 接收 `mode=patent` 的普通问答请求
- 请求协议校验
- durable 生命周期控制
- 上下文组装
- 专利检索编排
- 专利原文引用编排
- 专利原文查看接口
- 对 `requested_mode=patent` 且 `actual_mode=patent` 的 caller-facing sync / SSE 最终协议负责，直接产出 gateway 对外 contract
- 输出统一 SSE / sync response
- Redis 协调与缓存
- 遵守 shared overlay contract

不负责：

- durable transcript 最终落库
- conversation canonical state 真相源
- 文件/混合专利 turn
- 对外直接作为前端公开入口

#### Public-Service

负责：

- durable user turn write
- context snapshot read
- assistant async accept
- canonical transcript 最终物化
- conversation state / summary 真相源

#### Patent Data Layer

负责：

- 按专利号、公开公告号、申请号查询元数据
- 按 query 检索候选专利
- 按 canonical patent id 返回原文定位信息或正文内容
- 为 `patentQA` 生成可引用的专利证据片段

---

## 6. Canonical Patent Identifier 规格

### 6.1 必须定义一个唯一 canonical key

首期必须定义一个统一的专利 canonical key，命名为：

- `canonical_patent_id`

后续所有这些对象都必须以它为主键：

- `reference_objects`
- `original_links`
- 原文查看路由
- Redis original cache
- 检索命中结果

### 6.2 canonical_patent_id 规则

首期强制使用：

- `canonical_patent_id = normalized_publication_number`

原因：

- 对外展示最稳定
- 与原文查看场景更直接对应
- 比 application number 更适合用户点击和引用

### 6.3 归一化规则

`normalized_publication_number` 规则必须固定：

1. 转大写
2. 去掉空格、连字符、下划线
3. 保留国家码、数字主体、kind code
4. 不允许输出本地 provider 私有格式

示例：

- `CN 123456789 A` -> `CN123456789A`
- `us-2023-0123456-a1` -> `US20230123456A1`

### 6.4 alternate id resolution 规则

系统允许以下输入：

- `publication_number`
- `patent_number`
- `application_number`

但输出与缓存一律使用 `canonical_patent_id`。

并且：

- 任何 identifier 都不得绕过 `patent_identity_registry`
- “格式看起来合法” 不等于 “可以直接成为 canonical id”

解析顺序必须为：

1. 如果输入是 publication number，先做语法归一化，得到 `normalized_publication_number_candidate`
2. 用该 candidate 去 `patent_identity_registry` 查 active 记录
3. 若命中且只有一条 active 记录，输出其 `canonical_patent_id`
4. 若输入是 patent number 或 application number，则先经 `patent_identity_registry` 映射到 publication number，再输出对应 `canonical_patent_id`
5. 若无 active 记录，返回 `PATENT_NOT_FOUND`
6. 若命中多条 active 记录，返回澄清错误，不得生成临时 pseudo id

### 6.5 必须存在的 identity registry 最小模型

首期检索数据源中必须至少有一张 identity registry，最小字段：

- `canonical_patent_id`
- `publication_number`
- `application_number`
- `patent_number_aliases[]`
- `country`
- `kind_code`
- `language`
- `provider_primary_key`
- `is_active`
- `updated_at`

这张表是 exact lookup、原文查看和缓存 key 稳定性的基础。

---

## 7. Gateway -> PatentQA 协议

### 7.1 首期只接受普通问答

发送到 `patentQA` 的请求必须满足：

- `requested_mode = patent`
- `actual_mode = patent`
- `route = kb_qa`
- `turn_mode = kb_only`
- `used_files = []`
- `execution_files = []`
- `selected_file_ids = []`
- `primary_file_id = null`
- `allow_kb_verification = false`

不满足时，`patentQA` 应明确返回协议错误，而不是降级执行。

### 7.2 Canonical 请求体

```json
{
  "question": "这件专利主要解决了什么问题？",
  "conversation_id": 123,
  "chat_history": [],
  "requested_mode": "patent",
  "actual_mode": "patent",
  "route": "kb_qa",
  "source_scope": "kb",
  "turn_mode": "kb_only",
  "kb_enabled": true,
  "allow_kb_verification": false,
  "used_files": [],
  "execution_files": [],
  "selected_file_ids": [],
  "primary_file_id": null,
  "file_selection": {},
  "trace_id": "req_xxx",
  "options": {
    "patent_scope": {
      "country": "CN",
      "language": "zh-CN"
    }
  }
}
```

### 7.3 身份边界

`patentQA` durable 身份必须来自 forwarded `Authorization` 解析。

因此：

- ask body 中的 `user_id` 不是 authority source of truth
- 首期 canonical ask body 不要求 `user_id`
- 即使 gateway 因通用 schema 透传了 `user_id`，`patentQA` 也必须把它视为非权威诊断字段，不得覆盖 auth 解析结果

### 7.4 兼容路由到 fastQA 的 patent turn contract

对于 `requested_mode=patent` 且 `turn_mode in {file_only, mixed}` 的请求，系统必须遵守：

- 上游执行 owner：`fastQA`
- authority-facing mode tuple：`requested_mode=fast`、`actual_mode=fast`
- gateway -> fastQA upstream 兼容元数据：
  - `options.mode_origin.requested_mode=patent`
  - `options.mode_origin.route_owner=fastQA`
  - `options.mode_origin.compatibility_route=true`
- durable provenance 持久化元数据：
  - authority user write 的 `context_hints` 增加 `mode_origin_requested_mode=patent`、`mode_origin_execution_backend=fastQA`、`compatibility_route=true`
  - authority assistant final event 的 `metadata.mode_origin` 增加同名字段
  - `public-service` 必须 materialize 并 replay 这些 provenance 字段

并且这不是“概念字段”，而是 authority contract 必须真的新增的 schema：

- authority user write schema 的 `context_hints` 必须正式新增：
  - `mode_origin_requested_mode`
  - `mode_origin_execution_backend`
  - `compatibility_route`
- authority assistant final event schema 必须正式新增：
  - `metadata.mode_origin.requested_mode`
  - `metadata.mode_origin.execution_backend`
  - `metadata.mode_origin.compatibility_route`
- `patentQA / fastQA` outbound authority client 必须显式序列化这些字段
- `public-service` materializer 必须把这些字段落到 durable transcript，并在 transcript/detail/context replay 时原样返回

其中 compatibility route 的 authority 映射 owner 必须明确指定给 `fastQA`：

- `fastQA` request adapter / persistence adapter 必须读取 inbound `options.mode_origin.*`
- `fastQA` 在 user write 前，必须把它映射为 authority `context_hints.mode_origin_*`
- `fastQA` 在 assistant accept 前，必须把它映射为 authority `final_event.metadata.mode_origin`
- `fastQA` conversation authority client 也必须同步扩展，能够发送：
  - user write `context_hints.mode_origin_*`
  - assistant final event `metadata.mode_origin`
- 如果 `fastQA` 未完成这层映射，则 compatibility route 不得宣称 durable provenance 完整

这条规则属于系统 rollout 强约束，即使 `patentQA` 首期自己不实现文件/混合 QA，也必须在 spec 中明确。

并且 `gateway` 还必须承担 caller-facing metadata 恢复责任：

- 发往 `fastQA` 的 upstream payload 用 `fast/fast`
- 返回给前端的 sync body、SSE `metadata`、`error`、`done` 事件中，必须恢复为：
  - `requested_mode = patent`
  - `actual_mode = fast`
  - `metadata.mode_origin.requested_mode = patent`
  - `metadata.mode_origin.compatibility_route = true`
  - `metadata.mode_origin.execution_backend = fastQA`

也就是说：

- authority/upstream contract 使用 `fast/fast`
- caller-facing contract 使用 `patent/fast`
- provenance 的 durable 真相源位置是 `context_hints.mode_origin_*` 与 `metadata.mode_origin.*`，而不是 transient `options`

并且 compatibility route 的 caller-facing contract owner 也必须单独固定：

- `gateway` 是 compatibility-routed `patent -> fastQA` 的 caller-facing contract owner
- `gateway` 必须接收 upstream `fastQA` 响应后再做 caller-facing rewrite
- 该路径不使用本文件第 16 章定义的“normal patent flat contract”，而是继续沿用现有 file/mixed QA 的 gateway 对外 contract
- 但 `gateway` 必须强制重写以下 caller-facing 字段：
  - sync success response：
    - `requested_mode = patent`
    - `actual_mode = fast`
    - 若存在 `data.metadata`，则其中同名字段也必须同步重写
    - `data.metadata.mode_origin.*` 必须补齐
  - SSE `metadata` / `error` / `done`：
    - 注入或重写 `requested_mode = patent`
    - 注入或重写 `actual_mode = fast`
    - 注入 `metadata.mode_origin.requested_mode = patent`
    - 注入 `metadata.mode_origin.execution_backend = fastQA`
    - 注入 `metadata.mode_origin.compatibility_route = true`
- `query_mode`、`references`、`reference_objects`、`reference_links`、`pdf_links`、`used_files`、`timings` 继续沿用 upstream `fastQA` 结果，不由 gateway 二次发明
- 若 upstream `fastQA` 返回 wrapped sync body，则 gateway 在兼容链路上保持 wrapped shape，仅做 mode/provenance rewrite
- 若 upstream `fastQA` 返回 SSE 事件，则 gateway 保持事件家族不变，仅做 mode/provenance rewrite

---

## 8. Patent Data Layer 最小可用 contract

### 8.1 首期不能只说“接某个数据源”

首期必须把数据源 contract 定死到“可以直接实现”的程度。最小数据层必须由三部分组成：

1. `patent_identity_registry`
2. `patent_catalog_index`
3. `patent_original_store`

缺一不可。

### 8.2 `patent_identity_registry` 最小字段

- `canonical_patent_id`
- `publication_number`
- `application_number`
- `patent_number_aliases[]`
- `country`
- `kind_code`
- `language`
- `provider_primary_key`
- `is_active`
- `updated_at`

用途：

- exact id resolve
- 原文查看主键统一
- cache key 统一

### 8.3 `patent_catalog_index` 最小字段

- `canonical_patent_id`
- `title`
- `abstract`
- `claims_text`
- `description_text`
- `applicant_names[]`
- `inventor_names[]`
- `ipc_codes[]`
- `cpc_codes[]`
- `priority_date`
- `publication_date`
- `language`
- `country`
- `status`
- `updated_at`

用途：

- metadata search
- fulltext lexical search
- evidence selection

### 8.4 `patent_original_store` 最小字段

- `canonical_patent_id`
- `provider`
- `fulltext_format`
- `original_version`
- `fulltext_content` 或 `redirect_url`
- `claims_structured[]`
- `description_structured[]`
- `abstract_text`
- `figure_section_available`
- `updated_at`

用途：

- 原文查看
- 精细定位到 claim / paragraph
- 回答证据引用

### 8.5 基于当前资源布局的首期落地约束

当前仓库已经有一套可直接挂载到 `patentQA` 的资源布局，首期实现必须按该布局设计，而不是继续抽象成“任意可替换数据源”：

1. `resource/patentQA/vector_db_patent_abstracts`
2. `resource/patentQA/vector_db_patent_chunks`
3. `resource/patentQA/__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_`

三者职责必须固定如下：

- `vector_db_patent_abstracts`
  - patent-level 粗召回库
  - 一条向量对应一个 `patent_id`
  - 元数据最少可得到：
    - `patent_id`
    - `kind`
    - `source_json`
- `vector_db_patent_chunks`
  - chunk-level 细召回库
  - 一条向量对应一个专利片段
  - 元数据最少可得到：
    - `patent_id`
    - `source_file`
    - `json_stem`
    - `chunk_index`
    - `chunk_size`
    - `chunk_overlap`
    - `patent_dir`
- `__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_`
  - 原文与结构化专利源
  - 每个专利目录至少允许存在：
    - `权利要求.json`
    - `说明书.json`
    - `著录项目.json`
    - `*.pdf`
    - 附图目录

因此首期 `patentQA` 不能把“检索库”和“原文查看源”混为一谈：

- 检索只读取两个 Chroma 库
- 原文查看只读取专利目录和其结构化 JSON / PDF
- 原文查看失败不得反向影响检索命中
- 检索命中不得假设一定存在 DOI、paper page、section_name 这类 `fastQA` 论文域字段

---

## 9. PatentQA 内部问答流水线规格

### 9.1 总原则

`patentQA` 的内部流水线要“形似 fastQA 普通问答链，语义改成专利域”。

保留这些层次：

1. 请求适配
2. 会话上下文构建
3. 检索规划
4. 候选召回
5. 原文片段装载
6. 最终答案合成
7. 引用对象与原文跳转对象构建

### 9.2 Stage 0: Query Normalize

输入：

- `question`
- `conversation_context`
- `options.patent_scope`

输出：

- `normalized_query`
- `mentioned_identifiers[]`
- `intent_type`
- `country_filter`
- `language_filter`

必须完成的事情：

- 抽取显式专利号、公开公告号、申请号
- 识别“查看原文”“看权利要求 1”“看说明书背景技术”这类 intent
- 生成用于缓存的 `normalized_query_key_input`

### 9.3 Stage 1: Retrieval Planning

输出：

- `intent_type`
  - `patent_lookup`
  - `patent_compare`
  - `patent_summary`
  - `patent_original_view`
  - `general_patent_qa`
- `retrieval_mode`
  - `exact_id`
  - `abstract_vector`
  - `chunk_vector`
  - `abstract_chunk_hybrid`
  - `metadata_lexical`
  - `fulltext_lexical`
  - `hybrid_no_vector`
- `target_patent_ids[]`
- `fallback_order[]`

其中枚举的 canonical 解释必须固定如下：

- `exact_id`
  - 显式专利号 / 申请号 / 公开公告号命中
- `abstract_vector`
  - 只完成 abstract patent-level 召回
- `chunk_vector`
  - 只完成 chunk-level 召回
- `abstract_chunk_hybrid`
  - abstract recall + chunk recall + fusion 的完整双库流程
- `metadata_lexical`
  - 无向量条件下的 metadata fallback
- `fulltext_lexical`
  - 无向量条件下的 fulltext fallback
- `hybrid_no_vector`
  - metadata + fulltext 的无向量组合路径

后续所有缓存键、日志、metrics、caller-facing `metadata.retrieval_backend` 都必须以上述 canonical 枚举为准，不允许在其他章节再发明第二套主枚举。

### 9.4 Stage 2: Candidate Retrieval

首期 retrieval 行为必须是规范性的，不允许“各自理解”。顺序固定如下：

1. `exact_id`
   - 如果 Stage 0 抽取到 identifier，先做归一化，再走 identity registry resolve
   - 只有命中唯一 active 记录时才能返回 `canonical_patent_id`
2. 若两个专利向量库均可用：
   - 先走 `abstract_vector`
   - 再走 `chunk_vector`
   - 最终产出 `abstract_chunk_hybrid`
3. 若双库资源不可用：
   - 退化到 `metadata_lexical`
   - 必要时升级到 `fulltext_lexical`
   - 最终产出 `hybrid_no_vector`
4. `fallback`
   - 若完全无命中，返回明确无结果或要求澄清

### 9.5 评分与排序规则

首期必须固定如下分数定义：

- `exact_id_score = 1.00`
- `metadata_score = 0.40 * title_match + 0.25 * abstract_match + 0.10 * applicant_inventor_match + 0.15 * ipc_cpc_match + 0.10 * phrase_coverage`
- `fulltext_score = 0.60 * claims_match + 0.40 * description_match`

各项分数均归一化到 `[0, 1]`。

最终排序规则：

1. exact id 命中优先于任何 vector / lexical 命中
2. 其后按对应 score 降序
3. 若 snapshot 中存在 `last_focus_patent_numbers`，优先该列表命中；若该字段缺失，则跳过此排序因子，不得用本地临时状态替代
4. 若仍相同，优先 `publication_date` 更新者

### 9.6 Retrieval fan-out 与 top-N 限制

为控制性能和结果稳定性，首期强制：

- `abstract_vector` 最多召回 20 条 patent 候选
- `chunk_vector` 最多召回 30 条 chunk 候选
- `metadata_lexical` 最多召回 20 条候选
- `fulltext_lexical` 最多召回 30 条候选
- 最终进入证据装载的专利最多 5 个
- 单个专利最多装载 1 段摘要、2 个 claim 片段、2 个说明书片段
- 最终全局证据片段总数最多 12 个

### 9.7 Deterministic 跳转与澄清规则

必须固定以下阈值：

- metadata path 成功：
  - `top1.metadata_score >= 0.75`
  - 且 `top1 - top2 >= 0.15`
- abstract+chunk hybrid path 成功：
  - `top1.abstract_score >= 0.60`
  - 且 `top1.chunk_score >= 0.60`
  - 且 fusion 后 `top1 - top2 >= 0.08`
- metadata -> fulltext 升级：
  - `top1.metadata_score < 0.55`
  - 或 metadata 候选数 `< 3`
- metadata clarification：
  - `top1 - top2 < 0.05`
  - 且 top1 与 top2 属于不同 `canonical_patent_id`
- fulltext path 成功：
  - `top1.fulltext_score >= 0.65`
  - 且 `top1 - top2 >= 0.10`
- fulltext clarification：
  - `top1 - top2 < 0.03`
  - 且 top1 与 top2 属于不同 `canonical_patent_id`
- 全部低于成功阈值且不满足澄清条件：
  - 返回 `PATENT_NOT_FOUND`

### 9.8 Stage 3: Evidence Loading

对每个入选专利，证据装载必须输出统一 `PatentEvidence`：

```json
{
  "canonical_patent_id": "CN123456789A",
  "title": "一种用于...的方法",
  "abstract_text": "...",
  "claims": [
    {"claim_number": 1, "text": "..."}
  ],
  "description_snippets": [
    {"paragraph_id": "p-0012", "text": "..."}
  ],
  "provider": "patent_source_x",
  "original_available": true,
  "updated_at": "2026-03-30T00:00:00Z"
}
```

### 9.9 Stage 4: Answer Synthesis

基于：

- 当前问题
- conversation context
- `PatentEvidence[]`

输出：

- `answer_text`
- `steps`
- `references`
- `reference_objects`
- `reference_links`
- `original_links`
- `timings`

### 9.10 Stage 5: Result Packaging

必须包装成对前端稳定的统一协议，并带上：

- `route`
- `query_mode`
- `requested_mode`
- `actual_mode`
- `source_scope`
- `trace_id`

---

## 10. 无向量数据库前提下的 MVP 检索策略

### 10.1 首期必须实现的三种 retrieval modes

#### Mode A: Exact Patent Lookup

触发条件：

- query 中显式出现专利号 / 公开公告号 / 申请号

行为：

- 只走 identity resolve
- 只有唯一 active 记录时才命中
- 不做大范围召回

#### Mode B: Metadata Lexical Search

触发条件：

- 没有显式专利号
- 用户问主题、技术方案、申请人、IPC/CPC

行为：

- 先查 `title`、`abstract`、`applicant_names`、`inventor_names`、`ipc_codes`、`cpc_codes`
- 取 top 20
- 按 `metadata_score` 排序
- 仅当满足 9.7 的成功阈值时进入 evidence loading

#### Mode C: Fulltext Lexical Search

触发条件：

- metadata path 满足 9.7 的升级条件

行为：

- 搜索 `claims_text`、`description_text`
- 取 top 30
- 按 `fulltext_score` 排序
- 仅当满足 9.7 的成功阈值时进入 evidence loading

### 10.2 明确的 fallback 顺序

必须固定为：

1. exact patent id
2. metadata lexical
3. fulltext lexical
4. clarification or no-result

### 10.3 不允许的首期模糊写法

以下说法在实现中都不允许：

- “接任何可用专利数据源都行”
- “召回多少看情况”
- “score 逻辑后面再定”
- “原文查看时再临时解析 patent number”
- “差不多就走 fulltext”

因为这些说法会导致不同实现彼此不兼容。

### 10.4 为后续向量检索预留的扩展位

首期必须预留：

- `retrieval_backend = exact_id | metadata_lexical | fulltext_lexical | hybrid_no_vector | vector_hybrid`
- `retrieval_version`
- `retrieval_cache_key_version`

这样后续接向量库时：

- 不需要重写 response 协议
- 只需要替换 Stage 2 与部分缓存 key 规则

### 10.5 基于 `fastQA` 骨架的专利双库检索设计

`patentQA` 的检索流程要“形似 `fastQA` 普通问答骨架”，但不能复用其 DOI / paper 语义。

当 `resource/patentQA/vector_db_patent_abstracts` 与 `resource/patentQA/vector_db_patent_chunks` 两个向量库都已可用时，10.5-10.7 的双库检索设计应优先于 10.1-10.4 的无向量 MVP 流程；10.1-10.4 仅作为双库资源缺失时的降级路径。

必须保留的骨架层次：

1. query normalize
2. recall
3. evidence packaging
4. answer synthesis
5. final response / durable mapping

但 recall 层必须改成专利域专用的两阶段检索：

#### Stage 2A: Abstract Recall

输入：

- `normalized_question`
- `intent_type`
- 可选 `country_filter`
- 可选 `language_filter`

数据源：

- `resource/patentQA/vector_db_patent_abstracts`

行为：

- 生成 query embedding
- 召回 patent-level topN 候选
- 输出的候选必须至少带：
  - `patent_id`
  - `abstract_score`
  - `kind`
  - `source_json`

用途：

- 缩小候选专利集合
- 不直接作为最终 evidence 输出

#### Stage 2B: Chunk Recall

输入：

- `normalized_question`
- `candidate_patent_ids[]`

数据源：

- `resource/patentQA/vector_db_patent_chunks`

行为：

- 生成 query embedding
- 优先在 `candidate_patent_ids[]` 范围内召回 chunk
- 若底层不支持 server-side patent filter，则允许：
  - 全库召回 topM
  - 再按 `candidate_patent_ids[]` 做 post-filter 与 rerank

输出的 chunk 候选必须至少带：

- `patent_id`
- `chunk_score`
- `source_file`
- `json_stem`
- `chunk_index`
- `patent_dir`

其中 `patent_dir` 在当前资源下只能视为“建库时附带的来源元数据”，不得视为 original-view 的权威路径，因为现有 chunk 库中该字段可能仍指向历史机器上的绝对路径。

#### Stage 2C: Fusion

最终 evidence 排序必须综合：

- exact id resolve 命中
- abstract recall score
- chunk recall score
- `source_file` 类型权重

首期固定规则：

- “保护范围 / 权利要求 / 覆盖什么”类问题：
  - `权利要求.json` 权重高于 `说明书.json`
- “实施例 / 制备流程 / 技术效果 / 背景技术”类问题：
  - `说明书.json` 权重高于 `权利要求.json`
- exact patent id 命中时：
  - 优先只在该 patent 内做 chunk 检索
  - 不得再把其他专利混入 top evidence

#### Stage 2D: Evidence Packaging

最终进入回答阶段的不是“原始 Chroma 记录”，而是标准化后的 `PatentEvidence` / `reference_objects`。

必须显式完成：

- `patent_id -> canonical_patent_id` 统一
- `source_file -> section_type` 映射
- `chunk_index -> paragraph_id / claim_number / anchor` 映射
- `canonical_patent_id -> current archive root` 建立关联

其中 original-view 资源定位必须明确：

- 权威原文根目录是 `resource/patentQA/__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_`
- 路径拼接必须基于 `canonical_patent_id`
- `patent_dir` 只可用于诊断、审计、建库回溯，不得直接用于运行时打开文件

其中 `section_type` 首期至少支持：

- `abstract`
- `claim`
- `description`
- `figure`

`section_type=figure` 的使用规则必须固定：

- 首期允许它进入 `PatentEvidence` / `reference_objects` / `original_links`
- 但首期不要求向量检索一定能召回 figure-level chunk
- 当 intent 是 `patent_original_view` 且用户目标明确是“看附图/看图示”时，允许直接构建 `section_type=figure` 的引用对象
- 此时 anchor 必须保持 section-only 语义，不得伪造 `claim_number` 或 `paragraph_id`

### 10.6 首期推荐 retrieval backend 定义

当两个专利向量库均可用时，推荐 backend 定义固定为：

- `exact_id`
- `abstract_vector`
- `chunk_vector`
- `abstract_chunk_hybrid`

兼容之前的非向量位定义时，必须满足：

- caller-facing `metadata.retrieval_backend` 允许继续使用规范枚举
- 内部实现可以把双库流程统一映射到 `vector_hybrid`
- 但日志 / metrics 中应能区分：
  - abstract recall hit
  - chunk recall hit
  - hybrid fusion hit

### 10.7 与 `fastQA` 共享和不共享的部分

可以复用 `fastQA` 的：

- `/ask` sync/SSE 外层 contract
- AskService / ResultBuilder / PersistenceService 的分层方式
- Redis lock / inflight / execution cache / durable accept-before-success 骨架

不能复用 `fastQA` 的：

- DOI 作为主键的引用模型
- `doi/title/section_name/chunk_index` 的 RetrievedChunk 结构
- paper PDF/page 定位语义
- 单库 chunk-only recall 假设

因此 `patentQA` 必须单独定义：

- `PatentRetrievedChunk`
- `PatentRecallCandidate`
- `PatentEvidence`
- `PatentOriginalLocator`

---

## 11. 专利原文查看规格

### 11.1 与现有 DOI / PDF 体系的根本区别

`fastQA` 里的很多原文定位思路围绕 DOI / PDF。`patentQA` 不能照搬。

专利域中原文查看的唯一稳定主键必须是：

- `canonical_patent_id`

其他 id 只是入参别名，不得进入最终链接和缓存主键。

### 11.2 用户目标

前端层面，用户对“查看原文”的需求至少有三种：

1. 打开整篇专利原文
2. 打开特定结构位置
   - 摘要
   - 权利要求
   - 说明书
   - 附图说明
   - 特定 claim / paragraph
3. 在回答引用处点击跳转到原文定位

### 11.3 对外查看入口唯一性要求

前端公开入口必须唯一：

- `gateway` 代理的 `/api/patent/original/{canonical_patent_id}`
- `gateway` 代理的 `/api/v1/patent/original/{canonical_patent_id}`

因此：

- `viewer_uri` 必须始终指向 `gateway` 路径
- 前端不得直接使用 `patentQA` 本地地址
- `patentQA` 本地同路径仅用于联调、灰度和内部验证，不属于前端公开 contract

并且 gateway 侧的实现归属必须固定：

- 该路由不属于现有 `public-proxy -> public backend` 家族，也不属于 QA ask 路由族
- 必须新增独立的 gateway `document-proxy` 路由家族，专门承接“文档/原文查看类”透传
- gateway route ownership table 必须把：
  - `/api/patent/original/{canonical_patent_id}`
  - `/api/v1/patent/original/{canonical_patent_id}`
  归入 `document-proxy` 类目
- gateway 至少必须支持 `GET` 与 `HEAD`
- upstream target 固定为 `patentQA` 的 original-view endpoint
- gateway -> patentQA 推荐直接复用同相对路径：
  - `/api/patent/original/{canonical_patent_id}`
  - `/api/v1/patent/original/{canonical_patent_id}`
- `document-proxy` 的透传语义必须对齐现有 `/api/view_pdf/{doi}`：
  - 认证头透传
  - redirect / html / json / text / streaming body 原样透传
  - `Content-Type`、`Cache-Control`、`ETag` 等响应头原样透传
  - 不走 QA ask quota finalize 逻辑
- 但 backend 归属必须是：
  - `X-Gateway-Backend: patent`
  - target backend = `patentQA`
- 这是一条对现有 `public-proxy` 规则的显式例外，不得继续复用 `public backend + X-Gateway-Backend: public` 语义

### 11.4 查看接口 query 参数

- `section = abstract | claim | description | figure | fulltext`
- `claim_number`
- `paragraph_id`
- `format = html | json | text | redirect`

其中首期 figure contract 必须固定为 section-only：

- 不新增 `figure_id`
- 不新增 `figure_name`
- 不新增其他 figure selector
- `section=figure` 只表示“打开附图区域”

### 11.5 原文查看响应模型

#### 情况 A：返回结构化正文

```json
{
  "success": true,
  "canonical_patent_id": "CN123456789A",
  "title": "一种用于...的方法",
  "provider": "patent_source_x",
  "section": "claim",
  "section_label": "权利要求1",
  "content_format": "html",
  "content": "<div>...</div>",
  "trace_id": "req_xxx"
}
```

#### 情况 B：返回跳转地址

```json
{
  "success": true,
  "canonical_patent_id": "CN123456789A",
  "provider": "patent_source_x",
  "redirect_url": "https://provider.example/patent/CN123456789A",
  "trace_id": "req_xxx"
}
```

### 11.6 `reference_objects` 强制模型

`reference_objects[*]` 必须至少包含以下字段：

- `source_type = patent`
- `canonical_patent_id`
- `publication_number`
- `application_number`，若无可为 `null`
- `country`
- `kind_code`
- `title`
- `section_type`
- `section_label`
- `anchor`
- `snippet`
- `provider`
- `original_available`
- `viewer_uri`

示例：

```json
{
  "source_type": "patent",
  "canonical_patent_id": "CN123456789A",
  "publication_number": "CN123456789A",
  "application_number": "202310000001.0",
  "country": "CN",
  "kind_code": "A",
  "title": "一种用于...的方法",
  "section_type": "claim",
  "section_label": "权利要求1",
  "anchor": {
    "claim_number": 1,
    "paragraph_id": null,
    "offset_start": null,
    "offset_end": null
  },
  "snippet": "一种用于...的方法，其特征在于...",
  "provider": "patent_source_x",
  "original_available": true,
  "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1"
}
```

### 11.7 `references`、`reference_links`、`original_links` 强制不变量

必须满足以下不变量：

1. `references` 必须是唯一的 `canonical_patent_id` 列表
2. 每个 `reference_object` 必须归属于某个 `canonical_patent_id`
3. 每个被引用的 `canonical_patent_id` 至少必须有一个 `original_link`
4. `viewer_uri` 能内部查看时，`original_link.type = original_view`
5. provider 只支持外跳时，`original_link.type = provider_redirect`
6. sync response 与 SSE `done` 的 `references/reference_objects/reference_links/original_links` 结构必须一致

### 11.8 `original_links` 强制模型

`original_links[*]` 必须包含：

- `type`
- `label`
- `canonical_patent_id`
- `section`
- `claim_number`
- `paragraph_id`
- `viewer_uri`
- `redirect_url`

其中：

- `viewer_uri` 与 `redirect_url` 至少有一个非空
- `type=original_view` 时 `viewer_uri` 必须非空，`redirect_url` 必须为 `null`
- `type=provider_redirect` 时 `redirect_url` 必须非空，`viewer_uri` 必须为 `null`
- 精确 claim 定位时，`claim_number` 必须非空
- 精确 paragraph 定位时，`paragraph_id` 必须非空
- 若只是打开 `abstract / figure / fulltext` 或无法精确锚定到 claim / paragraph，则允许 `claim_number=null` 且 `paragraph_id=null`

### 11.9 原文查看与回答引用的关系

回答中的引用对象必须始终能映射到原文查看对象。

如果当前证据无法精确定位段落，也至少要能打开到该专利的全文页。

### 11.10 当前专利原文资源的定位规则

基于当前 `resource/patentQA/__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_` 的数据布局，首期 original-view 不能照搬 `fastQA` 的 DOI / PDF 打开方式，必须按专利目录解析：

#### 11.10.1 原文源优先级

优先级固定为：

1. 结构化 JSON
   - `权利要求.json`
   - `说明书.json`
   - `著录项目.json`
2. 本地 PDF
3. provider redirect

并且必须固定 original root 解析规则：

- 运行时权威原文根目录是 `resource/patentQA/__磷酸铁锂__AND__制备___NOT__废旧__已提取归档_`
- 进入具体专利目录时，只允许使用 `canonical_patent_id`
- chunk 库 metadata 中的 `patent_dir` 只能作为非权威回溯信息，不得直接作为文件打开路径

#### 11.10.2 section 到源文件的映射

- `section=claim`
  - 优先读取 `权利要求.json`
- `section=description`
  - 优先读取 `说明书.json`
- `section=abstract`
  - 优先读取 `著录项目.json` 或摘要字段
- `section=figure`
  - 优先读取 `摘要附图` 或 `全文附图`
- `section=fulltext`
  - 优先进入本地 PDF viewer 或 provider redirect

#### 11.10.3 original-view 锚点规则

首期必须支持两类精细锚点：

- `claim_number`
- `paragraph_id`

但当前 chunk 库里原始元数据只有 `chunk_index`，没有天然稳定的 `paragraph_id`。因此首期必须显式设计一层 `chunk -> original locator` 解析，而不是直接把 chunk metadata 暴露给前端：

- `权利要求.json` 命中时：
  - 若能解析 claim 序号，则输出 `claim_number`
  - 否则退化为该专利 claim 全文页
- `说明书.json` 命中时：
  - 若结构化 JSON 可稳定产出段落编号，则输出 `paragraph_id`
  - 否则退化为 description section viewer，不得伪造 paragraph id
- `section=figure` 命中时：
  - 首期固定为 figure section viewer
  - 不返回 `claim_number`
  - 不返回 `paragraph_id`
  - 不承诺打开具体图号或具体附图文件
  - Redis anchor 固定使用 `section:figure`

#### 11.10.4 原文查看与检索解耦要求

必须明确：

- 检索 evidence 可以来自 chunk 库
- original-view 的真实内容必须来自原文目录
- `viewer_uri` 只是“定位句柄”，不是向量库内容地址
- 不得把 Chroma document 内容直接当作 authoritative original content 返回给前端
- `section=figure` 的 viewer 也必须来自原文目录中的附图资源，而不是来自向量库 document

---

## 12. 会话上下文与持久化规格

### 12.1 Durable owner

Durable transcript owner 必须是 `public-service`。

`patentQA` 不得：

- 把 Redis 当 durable transcript store
- 自己维护 canonical conversation timeline
- 绕过 authority 直接把 durable transcript 写到本地文件或 DB

### 12.2 PatentQA 需要的 authority 调用

沿用现有新 QA 接入基座：

1. `POST /internal/conversations/{conversation_id}/messages/user`
2. `GET /internal/conversations/{conversation_id}/context-snapshot`
3. `POST /internal/conversations/{conversation_id}/messages/assistant-async`

### 12.3 Durable 上下文装配

`patentQA` 应仿照 `fastQA` 普通问答模式构建 context：

- `authority_chat_history`
- `authority_summary`
- `authority_conversation_state`
- `request_chat_history`
- `current_question`

合并规则：

1. 以 authority snapshot 为主
2. request chat_history 只作为兼容输入
3. 通过 overlap 去重避免历史重复
4. 当前 question 不重复拼进 recent turns
5. 保留最近 N 轮、总字符预算受控

### 12.4 建议扩展的 conversation_state 字段

为了更好支持专利问答，建议 `public-service` 后续允许这些 state 字段进入 snapshot：

- `last_turn_route`
- `last_focus_patent_numbers`
- `last_patent_country`
- `last_original_view_target`
- `last_assistant_trace_id`

其中最重要的是：

- `last_focus_patent_numbers`

若该字段在当前 snapshot 中不存在，则排序阶段必须跳过 patent focus boost，只按 score 和 publication_date 排序；若要做 durable patent 生产放量，则应把该字段视为 `public-service` rollout 必备项。

### 12.5 Assistant accept 的持久化内容

必须明确区分 authority request envelope 与 authority `final_event`：

- `AuthorityAssistantAsyncRequest` 外层 envelope 继续承载：
  - `trace_id`
  - `source_service`
  - `route=kb_qa`
  - `requested_mode=patent`
  - `actual_mode=patent`
  - `idempotency_key`
- `final_event` 只承载 assistant 最终结果对象

并且必须明确区分 external response 与 authority payload：

- external response 中的 `references` 是 `list[string]`，值为唯一 `canonical_patent_id` 列表
- authority `final_event.references` 继续使用 `list[dict]` 兼容形态，每项最少为 `{"canonical_patent_id": "...", "source_type": "patent"}`
- authority `final_event.metadata` 必须是对象，至少允许：
  - `query_mode`
  - `source_scope`
  - `mode_origin`
- `reference_objects`、`reference_links`、`original_links` 继续保持对象数组

因此 durable assistant `final_event` 至少必须包含：

- `answer_text`
- `steps`
- `metadata`
- `references`
- `reference_objects`
- `reference_links`
- `original_links`
- `used_files=[]`
- `timings`

### 12.6 Public-Service rollout 需要补齐的 schema

`public-service` 为支持 `patentQA` durable 落地，至少要补：

- allowlist 放行 `source_service=patentQA`
- authority schema 放行 `requested_mode=patent`、`actual_mode=patent`
- authority user write `context_hints` 正式新增：
  - `mode_origin_requested_mode`
  - `mode_origin_execution_backend`
  - `compatibility_route`
- authority assistant final event 正式新增：
  - `metadata`
  - `references(list[dict])`
  - `reference_objects`
  - `reference_links`
  - `original_links`
- durable materializer 与 transcript/detail read path 必须持久化并 replay `original_links` 与 `metadata.mode_origin.*`
- durable user message materializer 也必须持久化并 replay 新增的 `context_hints.mode_origin_*`
- durable assistant message 落库时，`original_links` 必须同时进入：
  - assistant message root payload
  - assistant message metadata
- transcript/detail/list read path 必须能把 `original_links` 从 durable transcript 中读回
- context snapshot 至少保持对新增 patent state 字段的透传兼容

没有完成以上 schema / materializer / replay 三层改造之前，不得声称 `original_links` 或 compatibility provenance 已经 durable 化。

### 12.7 Patent authority outbound model 也必须同步扩展

这不是只有 `public-service` 一侧的事情。`patentQA` 自身 outbound authority model 也必须同步支持：

- user write `context_hints.mode_origin_*`
- assistant final event `metadata`
- `original_links`
- authority 兼容形态的 `references(list[dict])`
- 专利域 `reference_objects`
- 专利域 `reference_links`

否则即使 `public-service` 放行，`patentQA` 也发不出去。

这里必须明确到模型和 client 责任：

- `AuthorityContextHints` 模型必须能表示 `mode_origin_requested_mode`、`mode_origin_execution_backend`、`compatibility_route`
- `AuthorityAssistantFinalEvent` 模型必须能表示 `metadata`、`reference_objects`、`reference_links`、`original_links`
- `ConversationAuthorityClient.write_user_turn()` 必须显式发送新增 `context_hints`
- `ConversationAuthorityClient.accept_assistant_turn_async()` 必须显式发送：
  - `final_event.metadata`
  - `final_event.references(list[dict])`
  - `final_event.reference_objects`
  - `final_event.reference_links`
  - `final_event.original_links`
- `patentQA` 内部 summary/result -> authority final event 的适配层必须负责把 caller-facing flat contract 映射成 authority payload

### 12.7.1 `original_links` 与 provenance 的端到端强制链路

以下链路缺一不可：

1. `patentQA` execution result 产出 `original_links`
2. `patentQA` caller-facing sync / SSE `done` 直接返回 `original_links`
3. `patentQA` authority outbound model / client 把 `original_links` 写入 `assistant-async final_event`
4. `public-service` authority schema 接受该字段
5. `public-service` durable materializer 将其落入 assistant message root payload 与 metadata
6. `public-service` transcript/detail/list read path replay 该字段
7. 下一轮 snapshot / detail 消费方能读回相同 provenance 与 original link 信息

兼容路由 provenance 的 durable 化也必须走同样的完整链路：

1. gateway 写入 compatibility route 的 `mode_origin`
2. `fastQA` request/persistence adapter 把 inbound `options.mode_origin.*` 映射为：
   - user write `context_hints.mode_origin_*`
   - assistant final event `metadata.mode_origin`
3. `fastQA` authority client 把这些字段发给 `public-service`
4. `public-service` durable transcript 落库
5. transcript/detail replay 时原样可见

少任何一环，都视为 contract 未完成。

### 12.8 Shared Overlay Contract

跨 QA backend 的 overlay 必须是 conversation-scoped，而不是 backend-scoped。

因此首期必须统一：

- overlay key family：`pending:conversation:assistant:{user_id}:{conversation_id}`
- overlay payload schema：
  - `trace_id`
  - `route`
  - `assistant_content`
- merge 规则：
  - 若 snapshot 已收敛到该 assistant trace，则清除 overlay
  - 若 chat_history 中已含同 trace assistant，则不重复追加
  - 否则在 authority history 末尾追加一条 assistant overlay
- convergence 判定：
  - `conversation_state.last_assistant_trace_id == overlay.trace_id`
  - 或 `recent_turns` 中已有同 trace assistant
- overlay TTL：统一为 `1800s`

### 12.9 Overlay rollout 约束

当前 `patent` 本地 overlay key 与 `fastQA` 的 shared pending overlay key 家族不一致，且 `highThinkingQA` 仍存在 file-backed overlay 实现。要支持 `patentQA <-> fastQA <-> highThinkingQA` 的 conversation continuity，三者都必须迁移到 shared overlay contract。

换句话说：

- overlay 不能继续是 patent-local 语义
- `highThinkingQA` 也不能继续保留 file-backed overlay 作为生产 continuity 机制
- 必须统一变成 QA fleet 共享的 Redis overlay 语义

---

## 13. Redis 设计规格

### 13.1 Redis 的定位

Redis 在 `patentQA` 中承担五类职责：

1. 执行互斥
2. 幂等去重
3. 运行中状态标记
4. 执行结果缓存
5. 检索与原文缓存

不承担 durable transcript 真相源职责。

### 13.2 必须具备的 key 类别

建议至少保留以下 key 空间：

- `coord:conversation-lock:{conversation_id}`
- `coord:turn-identity:{conversation_id}:{trace_id}`
- `coord:inflight:{conversation_id}:{trace_id}`
- `coord:pending-turn:{conversation_id}`
- `exec:result:{conversation_id}:{trace_id}`
- `exec:cache:{normalized_request_key}`
- `retrieval:cache:{normalized_query_key}`
- `original:cache:{canonical_patent_id}:{section}:{anchor}:{format}:{original_version}`
- `pending:conversation:assistant:{user_id}:{conversation_id}`
- `negative:patent-resolve:{raw_identifier}`
- `negative:retrieval:{normalized_query_key}`

其中 `anchor` 必须归一化为：

- `claim:<n>`
- `paragraph:<id>`
- `section:abstract`
- `section:description`
- `section:figure`
- `fulltext`

规则补充：

- `section=claim` 且存在 `claim_number` 时，必须使用 `claim:<n>`
- `section=description` 且存在 `paragraph_id` 时，必须使用 `paragraph:<id>`
- `section=abstract` 且无更细锚点时，必须使用 `section:abstract`
- `section=description` 且无更细锚点时，必须使用 `section:description`
- `section=figure` 且无更细锚点时，必须使用 `section:figure`
- `section=fulltext` 时，必须使用 `fulltext`
- 不允许不同实现自定义其他 anchor token

### 13.3 `normalized_request_key` 组成规则

必须包含：

- `question_normalized`
- `conversation_id`
- `canonical_patent_id[]`，如果已解析到
- `retrieval_mode`
- `patent_scope.country`
- `patent_scope.language`
- `context_hash`
- `request_contract_version`
- `retrieval_version`

### 13.4 `normalized_query_key` 组成规则

必须包含：

- `question_normalized`
- `retrieval_mode`
- `country_filter`
- `language_filter`
- `top_k`
- `catalog_index_version`
- `retrieval_version`

当 `patentQA` 已切到双向量库检索时，`catalog_index_version` 不得只表示单一 catalog，而必须能覆盖两个向量索引版本。允许两种实现：

1. 单字段组合值
   - `catalog_index_version = abstract:<v1>|chunk:<v2>`
2. 两个显式字段再做组合归一
   - `abstract_index_version`
   - `chunk_index_version`

无论哪种实现，效果都必须等价：

- abstract 库重建会导致 retrieval cache 失效
- chunk 库重建也会导致 retrieval cache 失效
- 不能因为只改了 chunk 库而继续复用旧 abstract-only cache key

并且 `retrieval_mode` 在 cache key 中的取值必须继续使用 9.3 定义的 canonical 枚举：

- 双库完整路径：`abstract_chunk_hybrid`
- 仅 abstract 路径：`abstract_vector`
- 仅 chunk 路径：`chunk_vector`
- 无向量降级路径：`metadata_lexical` / `fulltext_lexical` / `hybrid_no_vector`

### 13.5 各缓存的用途与写入规则

#### Conversation Lock

- 用于同一 `conversation_id` 的 durable 串行执行
- 仅 durable 使用
- 成功获取后必须定期续租

#### Turn Identity

- 用于相同 `conversation_id + trace_id` 幂等
- claim 成功后同一 trace 不能重复执行

#### Inflight Marker

- 用于跨实例可见的“该 turn 正在执行”状态
- 开始执行前写入，结束或 abort 时清理

#### Pending Turn

- 标记已 user write / snapshot read 但尚未 assistant accept 的 conversation
- 用于 crash recovery 与避免状态漂移

#### Execution Result Cache

- 只能在 assistant accept 成功后写入 success 结果
- accept 失败不得写 success cache
- sync 重试和 stream 重连可以读取

#### Retrieval Cache

- 缓存候选专利列表与证据摘要
- 只有 Stage 2 成功完成后才能写入
- 不缓存带有运行时异常的半成品

#### Original Cache

- 缓存原文结构化内容或 redirect 信息
- key 必须包含 `format`
- 原文查看接口与问答证据装载都可以读取
- provider 成功返回后写入

#### Negative Cache

- 仅缓存明确的“未找到”或“provider 404”
- 不缓存超时、网络错误、临时 unavailable

### 13.6 TTL 建议

建议值如下：

- conversation lock: 60s，可续租
- turn inflight: 60s，可续租
- pending turn: 5min
- execution result: 10min 到 1h
- retrieval cache: 10min 到 6h
- original cache: 1h 到 24h
- shared overlay: 1800s
- negative identifier cache: 5min
- negative retrieval cache: 2min

### 13.7 失效与刷新规则

必须明确：

- `catalog_index_version` 变化时，retrieval cache 自动失效
- `original_version` 变化时，original cache 自动失效
- `request_contract_version` 或 `retrieval_version` 变化时，execution/retrieval cache 自动失效
- overlay 只在 authority snapshot 已收敛到对应 assistant trace 时清除

### 13.8 stale-read 策略

首期策略必须简单明确：

- execution result cache：不允许 stale fallback
- retrieval cache：允许读有效 TTL 内结果，不允许过期后继续返回
- original cache：允许软过期后后台刷新，但前提是 provider 明确可用且内容稳定
- negative cache：严格短 TTL，不允许长期遮蔽真实数据更新

### 13.9 多实例一致性要求

必须保证：

- 锁和 inflight 的续租是 compare-and-renew 语义，而不是盲目覆盖
- 获取不到 conversation lock 时，返回明确 busy / conflict
- 已完成 accept 的结果才能写 execution result cache
- assistant accept 失败时不能缓存 success result

---

## 14. 性能与多实例部署规格

### 14.1 部署形态

建议部署形态：

- FastAPI + Gunicorn + Uvicorn workers
- 水平扩容多实例
- 所有实例共享 Redis
- 所有实例共享 `public-service`

### 14.2 Gunicorn 要求

Gunicorn 负责：

- worker 进程管理
- 请求超时
- keepalive
- `max_requests` 与抖动，降低长时间运行后的内存漂移风险

### 14.3 并发控制

需要两层并发控制：

1. 进程内 ask 并发限制
2. conversation 级 durable 锁

### 14.4 首期性能预算假设

为了让 P95 指标可执行，首期必须按以下预算设计：

- durable preflight
  - user write + snapshot read 总预算：350ms
- Stage 0 normalize
  - 20ms
- exact id resolve
  - 80ms
- metadata lexical search
  - 500ms
- fulltext lexical search
  - 900ms
- evidence loading
  - 600ms
- answer synthesis
  - 900ms
- assistant accept
  - 250ms
- original view cache hit
  - 80ms
- original view provider fetch
  - 1200ms

### 14.5 P95 目标必须区分 hit / miss

首期验收目标：

- exact patent lookup，cache miss：P95 < 2.8s
- metadata/fulltext 问答，cache miss：P95 < 4.5s
- retrieval cache hit：P95 < 2.2s
- original view cache hit：P95 < 250ms
- original view provider fetch：P95 < 1.5s

### 14.6 退化策略

当依赖变慢时，必须按以下顺序退化：

1. 保留 exact id resolve
2. 缩小 metadata/fulltext fan-out
3. 减少 evidence 数量
4. 保留原文跳转对象
5. 若仍失败，返回明确错误，不返回伪引用

---

## 15. 端到端问答流程

### 15.1 Sync 普通专利问答

```text
Frontend
  -> gateway /api/patent/ask
  -> patentQA
     1. 校验请求协议
     2. 判定 durable / ephemeral
     3. durable: 写 user turn 到 public-service
     4. durable: 读 context snapshot
     5. 合并 request chat_history + authority snapshot
     6. Stage 0 normalize
     7. Stage 1 planning
     8. Stage 2 retrieval
     9. Stage 3 evidence loading
    10. Stage 4 synthesis
    11. Stage 5 result packaging
    12. durable: accept assistant final event
    13. 返回 sync JSON
```

### 15.2 Stream 普通专利问答

```text
Frontend
  -> gateway /api/patent/ask_stream
  -> patentQA
     1. 校验请求协议
     2. durable: 写 user turn
     3. durable: 读 snapshot
     4. 发 metadata
     5. 发 thinking / step / content
     6. 生成 final references / original links
     7. durable: accept assistant final event
     8. accept 成功后才发 done
```

### 15.3 Durable 核心顺序要求

顺序必须固定：

1. `write_user_turn`
2. `read_context_snapshot`
3. `execute`
4. `accept_assistant_turn_async`
5. `done` / sync success

必须保证：

- `assistant accept` 失败时不能返回成功
- stream 模式下不能先发 `done` 再做 accept
- sync 模式下不能先返回 `success=true` 再异步 accept

### 15.4 跨 QA owner 的 continuity 要求

当一个 durable patent conversation 在下一轮被 `gateway` 改路由到 `fastQA` 时，必须保证：

- authority transcript 仍然连续
- shared overlay 仍然生效
- 下一轮上下文能看到上一轮 assistant
- compatibility-routed turn 不会因为 mode tuple 不兼容而失败

---

## 16. 响应协议规格

### 16.0 Caller-Facing Contract Owner

必须先固定 owner：

- 对 `requested_mode=patent` 且 `actual_mode=patent` 的普通专利问答，最终 caller-facing sync response 与 SSE contract 由 `patentQA` 直接产出
- `gateway` 在该链路上只做透传，不负责把 `patentQA` 的 wrapped/nested payload 改写成 flat payload
- 因此 `patentQA` 自身 response model、result builder、SSE done builder 必须直接实现本章定义的 flat contract
- `gateway` 只允许在 compatibility-routed `patent -> fastQA` 场景下，恢复 caller-facing `requested_mode=patent` 与 `metadata.mode_origin.*`

### 16.1 Sync ask response 强制字段

sync 成功响应必须包含：

- `success`
- `final_answer`
- `query_mode`
- `route`
- `requested_mode`
- `actual_mode`
- `source_scope`
- `timings`
- `references`
- `reference_objects`
- `reference_links`
- `original_links`
- `metadata`
  - compatibility route 时必须包含 `metadata.mode_origin.*`
- `trace_id`
- `used_files`
- `file_selection`

示例：

```json
{
  "success": true,
  "final_answer": "...",
  "query_mode": "patent_kb_qa",
  "route": "kb_qa",
  "requested_mode": "patent",
  "actual_mode": "patent",
  "source_scope": "kb",
  "timings": {},
  "references": ["CN123456789A"],
  "reference_objects": [
    {
      "source_type": "patent",
      "canonical_patent_id": "CN123456789A",
      "publication_number": "CN123456789A",
      "application_number": null,
      "country": "CN",
      "kind_code": "A",
      "title": "一种用于...的方法",
      "section_type": "claim",
      "section_label": "权利要求1",
      "anchor": {"claim_number": 1, "paragraph_id": null},
      "snippet": "一种用于...的方法，其特征在于...",
      "provider": "patent_source_x",
      "original_available": true,
      "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1"
    }
  ],
  "reference_links": [
    {
      "type": "original_view",
      "label": "查看权利要求1",
      "canonical_patent_id": "CN123456789A",
      "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1",
      "redirect_url": null
    }
  ],
  "original_links": [
    {
      "type": "original_view",
      "label": "查看权利要求1",
      "canonical_patent_id": "CN123456789A",
      "section": "claim",
      "claim_number": 1,
      "paragraph_id": null,
      "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1",
      "redirect_url": null
    }
  ],
  "metadata": {},
  "trace_id": "req_xxx",
  "used_files": [],
  "file_selection": {}
}
```

### 16.2 SSE events

至少支持：

- `metadata`
- `thinking`
- `step`
- `content`
- `error`
- `done`

### 16.3 SSE `done` 强制字段

`done` 事件必须包含与 sync response 同构的这些字段：

- `final_answer`
- `query_mode`
- `route`
- `requested_mode`
- `actual_mode`
- `source_scope`
- `timings`
- `references`
- `reference_objects`
- `reference_links`
- `original_links`
- `metadata`
- `used_files`
- `file_selection`
- `trace_id`

### 16.4 `reference_links` 强制模型

`reference_links[*]` 必须包含：

- `type`
- `label`
- `canonical_patent_id`
- `viewer_uri`
- `redirect_url`

其中：

- `viewer_uri` 与 `redirect_url` 至少有一个非空
- `type=original_view` 时 `viewer_uri` 必须非空，`redirect_url` 必须为 `null`
- `type=provider_redirect` 时 `redirect_url` 必须非空，`viewer_uri` 必须为 `null`

---

## 17. 错误面规格

### 17.1 必须区分的错误类型

- `PROTOCOL_MISMATCH`
- `TOKEN_INVALID`
- `AUTHORITY_UNAVAILABLE`
- `DURABLE_MODE_DISABLED`
- `RUNTIME_NOT_READY`
- `DEPENDENCY_NOT_READY`
- `TURN_IN_FLIGHT`
- `PATENT_NOT_FOUND`
- `ORIGINAL_SOURCE_UNAVAILABLE`
- `RETRIEVAL_UNAVAILABLE`
- `INTERNAL_ERROR`

### 17.2 原文查看相关错误

必须单独区分：

- 专利号不存在
- 专利存在但原文缺失
- 原文 provider 不可用
- anchor 无法定位
- 仅支持 redirect，不支持结构化正文

### 17.3 Durable 失败原则

durable ask 出现以下问题时必须失败：

- user write 失败
- snapshot read 失败
- accept assistant 失败
- Redis prerequisites 不 ready
- conversation lock 获取失败

不能以“回答已经生成了”为理由返回伪成功。

---

## 18. 可观测性与审计规格

### 18.1 日志最少字段

每次 ask 最少要记录：

- `trace_id`
- `conversation_id`
- `user_id`
- `requested_mode`
- `actual_mode`
- `route`
- `persistence_mode`
- `retrieval_mode`
- `canonical_patent_ids`
- `cache_hit_flags`
- `accept_status`
- `latency_ms`
- `compatibility_route_used`

### 18.2 指标建议

建议最少提供：

- ask QPS
- ask latency P50/P95/P99
- durable preflight latency
- retrieval cache hit rate
- original cache hit rate
- execution cache hit rate
- authority failure count
- original source failure count
- lock conflict count
- compatibility-routed patent turn count

### 18.3 审计重点

需要可追踪：

- 某次回答引用了哪些 `canonical_patent_id`
- 用户点击的原文查看目标是什么
- assistant accept 是否成功
- 某个 trace 是否命中过缓存或重放结果
- 该 turn 是否经过 compatibility route

---

## 19. rollout 依赖与阶段计划

### 19.1 Phase A：当前已具备的基础设施确认

已基本具备：

- FastAPI 服务骨架
- authority client
- Redis 基础设施
- durable 生命周期骨架
- Gunicorn

### 19.2 Phase B：普通专利问答 MVP

必须补齐：

- `patent_identity_registry`
- `patent_catalog_index`
- `patent_original_store`
- 专利引用对象模型
- 专利原文查看接口
- sync / stream 结果中的 `original_links`

### 19.3 Phase C：durable 正式放量

必须补齐：

- `public-service` allowlist / schema 放行 `patentQA`
- `public-service` assistant final event schema 接受 `references(list[dict])`、`reference_objects`、`reference_links`、`original_links` 等专利扩展字段
- `patentQA` outbound authority model 同步支持 `original_links` 与 `metadata.mode_origin.*`
- `gateway` 代理专利原文查看接口
- 前端接入专利原文查看
- `gateway` 对 compatibility-routed patent file/mixed turns 执行 `fast/fast` authority rewrite
- `highThinkingQA` 从 file-backed overlay 迁移到 shared Redis overlay
- QA fleet 统一迁移到 shared overlay contract
- gateway route 与 quota 策略最终确认

### 19.4 Phase D：向量检索增强

后续再做：

- 向量库接入
- hybrid retrieval
- rerank
- 更细粒度 anchor 定位

---

## 20. 具体实施约束

### 20.1 后续实现者必须遵守

- 只在 `patent/` 内实现专利服务逻辑
- 不把 durable transcript 逻辑搬回 `gateway` 或 `patentQA`
- 不用 Redis 代替 `public-service`
- 不把原文查看继续设计成 DOI / PDF-first
- 不在首期提前做文件/混合协议
- 不允许前端直接拼接 `patentQA` 本地地址作为 `viewer_uri`

### 20.2 可扩展但不应首期实现的保留位

可预留但不强制实现：

- `family_id`
- `jurisdiction`
- `legal_status`
- `cited_by`
- `patent_similarity_score`
- `vector_score`
- `rerank_score`

---

## 21. 交付验收清单

达到以下条件可判定这份 spec 覆盖到可落地实现层面：

- 后续开发者能明确知道 `patentQA` 首期只做普通问答
- 后续开发者能明确知道 durable transcript 必须交给 `public-service`
- 后续开发者能明确知道问答主链路与 `fastQA` 普通问答保持同构
- 后续开发者能明确知道 compatibility-routed patent turn 的 durable 元数据必须走 `fast/fast` rewrite
- 后续开发者能明确知道“专利原文查看”必须以 `canonical_patent_id` 设计，并且 `viewer_uri` 必须走 gateway
- 后续开发者能明确知道没有向量库时该如何实现 MVP 检索
- 后续开发者能明确知道 `references/reference_objects/reference_links/original_links` 的 mandatory contract
- 后续开发者能明确知道 Redis 里每类 key 的组成、写入条件、失效和负缓存策略
- reviewer 能明确检查多实例一致性、accept 顺序、shared overlay 和缓存边界

---

## 22. 最终结论

`patentQA` 的正确首期方向不是等待专利检索终局方案成熟，也不是先写一个临时聊天服务，而是：

- 以现有 QA 基座为协议底座
- 以 `fastQA` 普通问答链路为结构模板
- 以 `public-service` 为 durable transcript owner
- 以 Redis 为多实例协调与缓存层
- 以 `canonical_patent_id` 原文查看替代 DOI / PDF 溯源思路
- 在没有向量库的前提下，用 exact lookup + metadata lexical + fulltext lexical 交付一个可以上线的普通专利问答 MVP
- 同时为 compatibility-routed patent turn 和 shared overlay 写清楚系统级 contract，避免会话一跨 owner 就断

这条路径的好处是：

- 首期就能形成可用系统
- 后续接向量库不会推翻协议
- 后续扩文件/混合专利能力也不会推翻 durable 边界
