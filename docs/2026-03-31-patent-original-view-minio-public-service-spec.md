# Patent Original View MinIO + Public-Service Spec

## 文档状态

- 最后更新：2026-03-31
- 状态：spec only
- 目标：把专利“查看原文”从当前 `patentQA` 本地资源读取方案，收敛为 `MinIO + public-service + gateway + patentQA` 的统一协议
- 适用读者：`patentQA`、`public-service`、`gateway`、前端、运维、对象存储接入方

---

## 1. 结论先行

专利原文查看采用以下职责边界：

- `patentQA`
  - 负责专利域定位逻辑
  - 负责生成 `original_links`
  - 负责决定 `section`、`claim_number`、`paragraph_id`
  - 不直接对前端提供原文内容
  - 不直接从 MinIO 向前端返回文件流
- `public-service`
  - 负责专利原文查看 HTTP 服务
  - 负责从 MinIO 读取 PDF / JSON / 附图资源
  - 负责把结构化内容、文档流、redirect 统一返回给前端
- `gateway`
  - 负责统一外部入口
  - 负责代理 `/api/patent/original/{canonical_patent_id}`
  - 继续归入 `document-proxy` 家族
- `MinIO`
  - 负责存放专利原文对象
  - 使用共用 bucket，靠前缀区分专利资源

这意味着：

- 聊天持久化仍然由 `public-service` 做
- 专利原文“查看服务”也由 `public-service` 做
- 但专利原文“定位规则”仍然属于 `patentQA`

---

## 2. 为什么这样拆

### 2.1 和现有论文查看链路保持一致

现有 paper/DOI 的查看原文能力本来就归 `public-service` 的 documents/public backend 能力。

因此专利若也要提供稳定的原文查看入口，最稳妥的方式不是让 `patentQA` 自己再做一套文档服务，而是沿用：

- `QA service` 负责生成引用与 viewer 句柄
- `public-service` 负责实际文档查看

### 2.2 避免把对象存储读取塞进 QA 服务

MinIO 读取、文档流式返回、`HEAD`、缓存头、`Content-Type`、附件资源读取，这些都属于“文档资产服务”问题，不属于 QA 检索/回答问题。

若把这些都塞进 `patentQA`：

- `patentQA` 会同时承担 QA 编排和文档服务两类责任
- 后续 paper / patent / 其他域都可能重复造轮子

### 2.3 避免把专利域规则污染进公共服务

但另一方面，专利原文查看的 section / anchor 规则又明显是专利域能力：

- `canonical_patent_id`
- `section=claim|description|abstract|figure|fulltext`
- `claim_number`
- `paragraph_id`
- figure section-only

这些不适合让 `public-service` 来“推断”。它只应当消费明确的 viewer contract。

因此最终边界应为：

- `patentQA` 决定“看哪一段”
- `public-service` 决定“怎么把这段内容取出来并返回”

---

## 3. 总体架构

```text
Frontend
  -> gateway
    -> public-service
      -> MinIO

Frontend
  -> gateway
    -> patentQA
      -> retrieval / synthesis
      -> produce original_links
      -> durable transcript via public-service
```

端到端分两条链：

### 3.1 问答链

1. 前端调用 `gateway -> patentQA`
2. `patentQA` 完成检索、回答、引用生成
3. `patentQA` 生成：
   - `references`
   - `reference_objects`
   - `reference_links`
   - `original_links`
4. `patentQA` sync / SSE `done` 直接返回这些字段
5. durable 场景下，`patentQA` 再通过 authority client 把这些字段写给 `public-service`

### 3.2 原文查看链

1. 前端点击 `viewer_uri`
2. 请求进入 `gateway`
3. `gateway` 代理到 `public-service`
4. `public-service` 根据 `canonical_patent_id + section + anchor` 从 MinIO 取资源
5. `public-service` 返回：
   - 结构化 JSON
   - html/text
   - PDF/stream
   - redirect

`patentQA` 不在查看链路上做文档流式转发。

---

## 4. MinIO 存储模型

### 4.1 Bucket 选择

采用共用 bucket，不新增专利专用 bucket。

专利用固定前缀：

```text
patent/originals/{canonical_patent_id}/...
```

### 4.2 目录组织

每个专利以 `canonical_patent_id` 为唯一主目录：

```text
patent/originals/{canonical_patent_id}/manifest.json
patent/originals/{canonical_patent_id}/structured/claims.json
patent/originals/{canonical_patent_id}/structured/description.json
patent/originals/{canonical_patent_id}/structured/bibliography.json
patent/originals/{canonical_patent_id}/figures/summary/*
patent/originals/{canonical_patent_id}/figures/fulltext/*
patent/originals/{canonical_patent_id}/fulltext/original.pdf
```

其中：

- `claims.json`
  - 对应 `权利要求.json`
- `description.json`
  - 对应 `说明书.json`
- `bibliography.json`
  - 对应 `著录项目.json`
- `figures/summary/*`
  - 对应 `摘要附图`
- `figures/fulltext/*`
  - 对应 `全文附图`
- `fulltext/original.pdf`
  - 对应原始全文 PDF

### 4.3 `manifest.json` 最小模型

每个专利目录下必须有 `manifest.json`，用于让 `public-service` 不必猜资源存在性。

最小字段：

```json
{
  "canonical_patent_id": "CN123456789A",
  "title": "一种用于...的方法",
  "provider": "patent_source_x",
  "original_version": "2026-03-31T12:00:00Z#sha256:abcd",
  "country": "CN",
  "kind_code": "A",
  "publication_number": "CN123456789A",
  "application_number": "202310000001.0",
  "objects": {
    "structured": {
      "claims": "patent/originals/CN123456789A/structured/claims.json",
      "description": "patent/originals/CN123456789A/structured/description.json",
      "bibliography": "patent/originals/CN123456789A/structured/bibliography.json"
    },
    "figures": {
      "summary": {
        "primary_object": "patent/originals/CN123456789A/figures/summary/figure-001.png",
        "ordered_objects": [
          "patent/originals/CN123456789A/figures/summary/figure-001.png"
        ]
      },
      "fulltext": {
        "primary_object": "patent/originals/CN123456789A/figures/fulltext/figure-001.png",
        "ordered_objects": [
          "patent/originals/CN123456789A/figures/fulltext/figure-001.png"
        ]
      }
    },
    "fulltext_pdf": "patent/originals/CN123456789A/fulltext/original.pdf"
  },
  "availability": {
    "claims": true,
    "description": true,
    "abstract": true,
    "figure": true,
    "fulltext_pdf": true
  }
}
```

其中：

- `original_version`
  - 是专利原文对象集的稳定版本号
  - 必须在任一结构化 JSON、figure 资源或 PDF 更新时变化
  - 作为 `public-service` cache key、`ETag`、失效判断的统一来源
- `figures.*.primary_object`
  - 是对应 figure source 的稳定首选对象
  - `public-service` 在 section-only `figure` 请求下必须优先使用它
- `figures.*.ordered_objects`
  - 用于记录完整顺序
  - 若 `primary_object` 缺失，才允许回退到 `ordered_objects[0]`

### 4.4 结构化对象最小 schema

为了让 `public-service` 在不依赖 `patentQA` 内部代码的前提下稳定返回 `json/html/text`，MinIO 中的结构化对象必须有固定 schema。

#### 4.4.1 `claims.json`

最小形态：

```json
{
  "canonical_patent_id": "CN123456789A",
  "section": "claim",
  "section_label": "权利要求",
  "claims": [
    {
      "claim_number": 1,
      "label": "权利要求1",
      "text": "一种用于...的方法，其特征在于...",
      "html": "<p>一种用于...的方法，其特征在于...</p>"
    }
  ]
}
```

规则：

- `claim_number` 必须稳定、唯一、从 1 开始
- `section=claim&claim_number=N` 时，`public-service` 直接按 `claim_number` 查找
- 若 `claim_number` 缺失或未命中，则退化为 claim section 级内容，返回整段 claims 列表

#### 4.4.2 `description.json`

最小形态：

```json
{
  "canonical_patent_id": "CN123456789A",
  "section": "description",
  "section_label": "说明书",
  "paragraphs": [
    {
      "paragraph_id": "p-001",
      "label": "段落1",
      "text": "本发明涉及...",
      "html": "<p>本发明涉及...</p>"
    }
  ]
}
```

规则：

- `paragraph_id` 必须是稳定字符串，不得依赖临时 chunk id
- `section=description&paragraph_id=...` 时，`public-service` 直接按 `paragraph_id` 查找
- 若 `paragraph_id` 缺失或未命中，则退化为 description section 级内容，返回整段 paragraphs 列表

#### 4.4.3 `bibliography.json`

最小形态：

```json
{
  "canonical_patent_id": "CN123456789A",
  "section": "abstract",
  "title": "一种用于...的方法",
  "abstract_text": "本发明公开了...",
  "abstract_html": "<p>本发明公开了...</p>",
  "bibliography": {
    "publication_number": "CN123456789A",
    "application_number": "202310000001.0",
    "country": "CN",
    "kind_code": "A"
  }
}
```

规则：

- `section=abstract` 时只需要返回单对象，不存在 claim/paragraph 锚点

#### 4.4.4 section 级 fallback 统一规则

`public-service` 必须支持两级返回：

- 锚点级
  - 单个 claim / paragraph
- section 级
  - 整个 claim list / description list / abstract object

如果锚点不存在但 section 资源存在：

- 不直接报 `ANCHOR_NOT_FOUND`
- 先按 section 级 fallback 返回
- 仅在调用方显式要求严格锚点且无回退策略时，才返回 `ANCHOR_NOT_FOUND`

### 4.5 不允许的做法

不允许：

- 用 `patent_dir` 绝对路径当运行时权威路径
- 让 `public-service` 通过本地文件系统直接扫目录猜对象
- 让前端直接拼接 MinIO object key

---

## 5. `patentQA` 侧职责

### 5.1 必须保留在 `patentQA` 的能力

`patentQA` 负责：

- `canonical_patent_id` 确定
- `section_type -> original section` 映射
- `chunk_index -> claim_number / paragraph_id` 映射
- 生成 caller-facing `original_links`
- 生成 `reference_links`
- 生成 `viewer_uri`

### 5.2 `viewer_uri` 生成规则

`patentQA` 生成的 `viewer_uri` 必须始终指向 gateway 暴露的 public 文档入口：

```text
/api/patent/original/{canonical_patent_id}?section=...
```

允许的参数：

- `section = abstract | claim | description | figure | fulltext`
- `claim_number`
- `paragraph_id`
- `format = html | json | text | redirect`

不允许：

- 返回 `public-service` 内网地址
- 返回 MinIO 内网地址
- 返回对象 key
- 返回本地文件路径

### 5.3 `original_links` 生成规则

`patentQA` 只负责生成定位句柄，不负责读取对象内容。

示例：

```json
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
```

### 5.4 和 durable transcript 的关系

`patentQA` 继续通过 authority client 把以下字段写给 `public-service`：

- `metadata`
- `references(list[dict])`
- `reference_objects`
- `reference_links`
- `original_links`

也就是说，`public-service` 同时扮演：

- durable transcript owner
- patent original-view service owner

但这两者是两条不同子能力，不能混为一谈。

---

## 6. `public-service` 侧职责

### 6.1 对外接口归属

专利原文查看应归入 `public-service` 的 documents/document-view 家族，而不是 `patentQA` 自己对前端暴露。

对外公开路径：

- `GET /api/patent/original/{canonical_patent_id}`
- `HEAD /api/patent/original/{canonical_patent_id}`
- `GET /api/v1/patent/original/{canonical_patent_id}`
- `HEAD /api/v1/patent/original/{canonical_patent_id}`

### 6.2 `public-service` 的最小责任

`public-service` 必须负责：

- 解析 query 参数
- 读取 MinIO `manifest.json`
- 根据 `section + claim_number + paragraph_id + format` 定位对象
- 返回内容或 redirect
- 管控 `Content-Type`、`Cache-Control`、`ETag`
- 支持 `GET` 与 `HEAD`

### 6.3 不该由 `public-service` 推断的事情

`public-service` 不应负责：

- 从 chunk metadata 推断 claim/paragraph 锚点
- 推断 `canonical_patent_id`
- 从问答结果倒推 section
- 参与 QA 检索逻辑

它只消费明确的查看请求。

---

## 7. 查看接口协议

### 7.1 Request

路径参数：

- `canonical_patent_id`

query：

- `section = abstract | claim | description | figure | fulltext`
- `claim_number`
- `paragraph_id`
- `format = html | json | text | redirect`

### 7.2 响应类型规则

#### `section=claim`

- 默认优先返回结构化内容
- 来源：`structured/claims.json`
- 若 `claim_number` 可定位，则返回对应 claim
- 否则返回 claim section 级内容

#### `section=description`

- 默认优先返回结构化内容
- 来源：`structured/description.json`
- 若 `paragraph_id` 可定位，则返回对应 paragraph
- 否则返回 description section 级内容

#### `section=abstract`

- 默认优先返回结构化内容
- 来源：`structured/bibliography.json`

#### `section=figure`

- 固定为 section-only
- 来源优先级固定为：
  - `figures.summary.primary_object`
  - `figures.fulltext.primary_object`
- 不支持 `figure_id`
- 不支持 `figure_name`
- 不承诺具体图号精确定位
- `public-service` 必须在响应中带出 `figure_source = summary | fulltext`
- `public-service` 必须在响应中带出 `served_object_key`

#### `section=fulltext`

- 优先返回 PDF/stream
- 来源：`fulltext/original.pdf`
- 若本地对象不存在，允许返回 provider redirect

### 7.3 `format` 规则

- `format=json`
  - 返回结构化 JSON
  - 适用于 `claim / description / abstract / figure`
- `format=html`
  - 返回 html 包装内容
- `format=text`
  - 返回纯文本
- `format=redirect`
  - 返回 redirect_url 或直接 302
  - 主要用于 `fulltext` 或 provider-only 场景

默认策略：

- `claim / description / abstract / figure` 默认 `json`
- `fulltext` 默认文档流或 redirect

### 7.4 响应模型

#### 结构化内容

```json
{
  "success": true,
  "canonical_patent_id": "CN123456789A",
  "title": "一种用于...的方法",
  "provider": "patent_source_x",
  "section": "claim",
  "section_label": "权利要求1",
  "content_format": "json",
  "content": {},
  "trace_id": "req_xxx"
}
```

#### 跳转

```json
{
  "success": true,
  "canonical_patent_id": "CN123456789A",
  "provider": "patent_source_x",
  "redirect_url": "https://provider.example/patent/CN123456789A",
  "trace_id": "req_xxx"
}
```

#### 文档流

- `Content-Type: application/pdf`
- body 为 PDF stream

### 7.5 `HEAD` 语义

`HEAD` 只用于：

- 检查资源是否可用
- 检查响应类型
- 检查缓存头

`HEAD` 不返回正文，但必须和 `GET` 共享同一定位逻辑。

---

## 8. `gateway` 侧要求

### 8.0 对既有协议的 supersede 说明

本文件显式替换 [2026-03-30-patentqa-delivery-spec.md](/home/cqy/worktrees/highThinking/docs/2026-03-30-patentqa-delivery-spec.md) 第 11.3 节中关于专利 original-view upstream 的归属定义。

旧定义：

- `gateway document-proxy -> patentQA`

新定义：

- `gateway document-proxy -> public-service`

以下内容保持不变：

- caller-facing `viewer_uri` 仍然是 `/api/patent/original/{canonical_patent_id}?section=...`
- 路由仍归 `document-proxy`
- 仍然要求 `GET` / `HEAD`
- 仍然要求文档透传语义

被替换的只有 upstream owner 和 backend 归属。

### 8.1 路由归属

这条链路继续归入 `document-proxy`，不是 QA ask proxy。

路由：

- `/api/patent/original/{canonical_patent_id}`
- `/api/v1/patent/original/{canonical_patent_id}`

### 8.2 upstream 变更

原方案是：

- `gateway -> patentQA`

现方案改为：

- `gateway -> public-service`

但 caller-facing `viewer_uri` 不变，仍然是：

```text
/api/patent/original/{canonical_patent_id}?section=...
```

### 8.3 透传语义

必须保留：

- auth headers
- redirect
- html/json/text/pdf/stream body
- `Content-Type`
- `Cache-Control`
- `ETag`

不进入 QA ask quota finalize。

### 8.4 backend/header 归属

对这条路径，gateway ownership table 必须明确：

- route family = `document-proxy`
- upstream service = `public-service`
- `X-Gateway-Backend = public`
- 不再使用旧的 `X-Gateway-Backend = patent`

这是对先前 `patentQA` upstream 方案的正式替换，不允许两套语义并存。

---

## 9. 缓存建议

### 9.1 `patentQA` 侧

`patentQA` 不缓存原文对象本体，只缓存：

- `original_links`
- `canonical_patent_id + section + anchor` 定位结果

### 9.2 `public-service` 侧

`public-service` 可缓存：

- `manifest.json`
- 结构化 JSON
- figure prefix 索引结果
- PDF object metadata

推荐 cache key：

```text
patent:original:{canonical_patent_id}:{section}:{claim_number}:{paragraph_id}:{format}:{original_version}
```

其中：

- `original_version` 来自 `manifest.json`
- `ETag` 也应从 `original_version` 派生
- 任何结构化 JSON / figure / PDF 变化都必须导致 `original_version` 变化

### 9.3 MinIO 侧

对象存储作为权威源，不作为对话真相源。

不得用 MinIO 替代：

- durable transcript
- authority replay
- conversation state

---

## 10. 错误面

### 10.1 必须区分的错误

- `PATENT_NOT_FOUND`
  - `canonical_patent_id` 不存在
- `ORIGINAL_NOT_AVAILABLE`
  - manifest 存在，但对应 section 无资源
- `ANCHOR_NOT_FOUND`
  - claim/paragraph 锚点不存在
- `OBJECT_STORE_UNAVAILABLE`
  - MinIO 不可用
- `PROVIDER_REDIRECT_ONLY`
  - 无法本地查看，只能跳 provider

### 10.2 错误处理原则

- 能退到 section 级别查看时，不直接报错
- 能退到 `fulltext` 时，不直接报错
- 只有确实无可展示资源时才返回失败

---

## 11. rollout 顺序

### 11.0 切流前置 gate

在 production 切换 `viewer_uri` 之前，以下前置条件必须全部完成：

1. MinIO backfill 完成
2. 每个 `canonical_patent_id` 都生成 `manifest.json`
3. 结构化 JSON schema 完成一致性校验
4. local archive 与 MinIO 对象完成 corpus parity 检查
5. `public-service` original-view API 完成联调
6. `public-service` 已支持 `ETag` / `Cache-Control` / `original_version` 关联校验
7. cache revalidation 已按 `manifest.original_version` 跑通

任何一项未完成，都不得切换 production viewer path。

### 11.1 cutover 动作

当前置 gate 全部完成后，按以下顺序切流：

1. gateway upstream 从 `patentQA` 切到 `public-service`
2. `patentQA` production `viewer_uri` / `original_links` 切到新的公共文档入口实现
3. 验证 `GET` / `HEAD`、json/html/text/pdf/redirect 返回一致
4. 灰度确认后再全量放开
### Phase A

先完成对象模型与接口协议：

- MinIO object key
- `manifest.json`
- 结构化 JSON schema 固化
- `original_version` / `ETag` / `Cache-Control` 规则固化
- `public-service` 专利 original-view API
- `gateway` route proxy
- MinIO backfill + manifest generation + parity validation
- pre-cutover 联调完成

### Phase B

再完成 durable transcript 端到端：

- `original_links` durable accept
- transcript replay
- context snapshot 透传兼容

### Phase C

再做体验增强：

- figure HTML viewer
- fulltext inline PDF viewer
- provider redirect fallback

---

## 12. 最终建议

最终推荐的系统边界是：

- `patentQA`
  - 负责问答和原文定位
- `public-service`
  - 负责 durable transcript
  - 负责专利原文查看服务
- `gateway`
  - 负责统一对外路由
- `MinIO`
  - 负责专利原文对象存储

这套边界既和现有 paper 的查看原文能力一致，又避免把专利域定位逻辑硬塞进共享服务。
