# Gateway QA Routing Design

**Date:** 2026-03-30

## Scope

本设计定义 `gateway` 的统一 QA 路由体系，覆盖以下问答类型：

- 普通问答 `kb_qa`
- PDF 文件问答 `pdf_qa`
- 表格问答 `tabular_qa`
- 混合问答 `hybrid_qa`

本设计同时覆盖以下执行模式的路由入口：

- `fast`
- `thinking`
- `patent`

但模式与路由是两个维度：

- `mode` 决定后端执行风格
- `route` 决定问答所使用的数据源与执行链路

本设计只讨论路由，不直接修改以下内容：

- `fastQA` 内部检索实现
- `highThinkingQA` 内部 prompt / citation / synthesis 细节
- `public-service` 的 conversation authority 协议
- 前端消息展示样式

---

## Goal

建立一套稳定、可解释、低误判的路由系统，使 `gateway` 能在以下场景中正确判定本轮问答：

1. 是否是普通问答还是文件相关问答
2. 如果是文件相关问答，使用哪类文件
3. 是否需要混合问答，即文件 + 知识库
4. 当前请求应发往哪个后端执行
5. 何时需要澄清，而不是草率误路由

最终目标：

- 普通问题不会因为会话里存在文件而被错误路由到文件 QA
- 文件 QA 不再依赖脆弱的单词级硬编码规则
- 混合 QA 通过明确语义进入，不再通过模糊布尔开关表达
- 路由结果可通过日志和 reason code 解释

---

## Non-Goals

本设计不包含：

1. 用 LLM 全量替代所有路由逻辑
2. 让 `gateway` 直接做 retrieval 或读取全文内容
3. 让 `gateway` 接管 conversation persistence
4. 重写 `fastQA` 的文件执行链
5. 重写 `highThinkingQA` 为文件问答执行器

---

## Background

当前系统已经基本形成如下职责边界：

- `gateway`
  - 接收前端 ask 请求
  - 读取会话文件元数据
  - 解析文件上下文
  - 决定 route 和 actual backend

- `fastQA`
  - 执行 `kb_qa`
  - 执行 `pdf_qa`
  - 执行 `tabular_qa`
  - 执行 `hybrid_qa`

- `highThinkingQA`
  - 执行 thinking 普通问答
  - 不负责文件问答

- `patent` backend
  - 执行 patent 普通问答
  - 不负责文件问答

当前路由存在的核心问题：

1. 路由规则过硬，单词级命中会触发错误链路
2. “会话里存在文件”和“本轮想用文件”没有被严格区分
3. `allow_kb_verification` 的语义过弱，不足以表达完整混合路由
4. `gateway` 虽然已经是路由中心，但 route contract 还不够制度化

---

## External References

本设计参考了成熟开源项目的思路，但不机械照搬实现。

### Dify

采用 workflow / retrieval node / if-else 分支的方式控制检索范围和执行分支，而不是依赖单个脆弱关键词。

参考：

- https://docs.dify.ai/en/use-dify/nodes/knowledge-retrieval
- https://docs.dify.ai/en/use-dify/nodes/ifelse

### Haystack

将路由显式建模为独立组件：

- `ConditionalRouter`
- `LLMMessagesRouter`
- `TransformersZeroShotTextRouter`
- `MetadataRouter`
- `FileTypeRouter`

参考：

- https://docs.haystack.deepset.ai/docs/routers
- https://docs.haystack.deepset.ai/docs/conditionalrouter
- https://docs.haystack.deepset.ai/docs/transformerszeroshottextrouter
- https://docs.haystack.deepset.ai/docs/llmmessagesrouter

### LlamaIndex

偏向使用 selector / router / tool choice / sub-question decomposition，而不是把复杂混合问题硬塞进一个单一路由判断。

参考：

- https://docs.llamaindex.ai/en/stable/api_reference/query_engine/router/
- https://docs.llamaindex.ai/en/stable/api_reference/query_engine/sub_question/

### Open WebUI

更强调先定义知识与文件作用域，再让模型决定是否使用检索，而不是仅靠单词命中决定链路。

参考：

- https://docs.openwebui.com/features/ai-knowledge/knowledge/

---

## Design Summary

本设计采用两层路由：

### 第一层：规则优先

由 `gateway` 基于显式上下文做 deterministic routing：

- 显式文件指代
- 显式文件型动作
- 明确文件指代
- 明确混合意图
- 文件编号 / 序号 / last focus
- 当前会话文件元数据

这一层只处理“确定性强”的请求。

### 第二层：轻量分类器兜底

只在歧义场景调用轻量文本分类器或小模型 router。

分类器输入：

- 问题文本
- 当前会话摘要文件上下文
- 选择状态
- 最近一轮 route 状态

分类器输出：

- `route`
- `turn_mode`
- `source_scope`
- `confidence`
- `reason_codes`

分类器不是主路由器，而是歧义裁决器。

---

## Core Principles

## 1. `file exists` 不等于 `use file now`

会话中存在文件只表示“这些文件可用”，不表示本轮问答必须使用文件。

默认策略：

- 如果问题没有明确文件指代
- 没有显式文件选择
- 轻量分类器也没有判定为文件相关

则默认走 `kb_qa`

这是整个设计最重要的产品原则。

## 2. 显式信号优先于弱语义猜测

优先级由高到低：

1. 显式文件型动作、明确指代、明确混合语义
2. 结构化上下文状态
3. 轻量分类器结果
4. 弱关键词命中

弱关键词不允许直接决定 route。

## 3. 混合 QA 是一级语义，不是附属布尔开关

凡是同时需要：

- 文件 + 知识库
- PDF + 表格
- PDF + 表格 + 知识库

都统一表达为 `hybrid_qa`

不再依赖“单一路由 + 一个补充布尔值”表达复杂语义。

## 4. 路由必须可解释

每一次路由结果都必须能给出：

- route
- turn_mode
- source_scope
- selected_file_ids
- strategy
- reason_codes
- confidence

方便日志、排障和后续策略优化。

## 5. 无法确定时优先澄清或退回普通 QA

规则：

- 强歧义的文件问答，优先澄清
- 不明确是否要用文件的问题，优先 `kb_qa`

不允许为了“看起来聪明”而强行进入错误文件链路。

---

## Terminology

### `route`

最终执行链路：

- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

### `turn_mode`

本轮数据源类型：

- `kb_only`
- `file_only`
- `mixed`

规范映射如下：

| `source_scope` | `turn_mode` |
| --- | --- |
| `kb` | `kb_only` |
| `pdf` | `file_only` |
| `table` | `file_only` |
| `pdf+table` | `file_only` |
| `pdf+kb` | `mixed` |
| `table+kb` | `mixed` |
| `pdf+table+kb` | `mixed` |

### `source_scope`

明确本轮实际数据源组合：

- `kb`
- `pdf`
- `table`
- `pdf+kb`
- `table+kb`
- `pdf+table`
- `pdf+table+kb`

### `strategy`

表示本次文件选择是怎么来的：

- `explicit_selection`
- `explicit_ref`
- `ordinal_ref`
- `single_candidate`
- `last_focus`
- `latest_upload`
- `plural_scope`
- `classifier_resolved`
- `clarify_required`
- `none`

### `reason_codes`

路由解释码，可多值：

- `EXPLICIT_SELECTED_FILES`
- `EXPLICIT_FILE_REF`
- `EXPLICIT_TABLE_REF`
- `EXPLICIT_PDF_REF`
- `EXPLICIT_MIXED_INTENT`
- `LAST_FOCUS_REUSE`
- `LATEST_UPLOAD_REUSE`
- `ONLY_ONE_READY_FILE`
- `MULTIPLE_FILES_NEED_CLARIFICATION`
- `CLASSIFIER_FILE_QA`
- `CLASSIFIER_HYBRID_QA`
- `NO_FILE_INTENT`
- `FALLBACK_TO_KB`

---

## Target Responsibilities

## Gateway owns

`gateway` 负责：

1. 收集当前会话文件元数据
2. 解释前端 `pdf_context`
3. 判定是否进入文件域
4. 选择 route / turn_mode / source_scope
5. 确定 `selected_file_ids` / `execution_files`
6. 判定是否需要澄清
7. 决定 `actual_mode`

说明：

- `execution_files` 是 gateway 在路由期确定的输入文件集合
- `used_files` 不是 gateway 拥有的路由期字段，而是 downstream 执行后回报的实际使用遥测

## Gateway does not own

`gateway` 不负责：

1. 文件检索
2. 文本切片
3. 表格执行
4. prompt 拼接
5. LLM 生成
6. 对文件内容做深理解

## fastQA owns

`fastQA` 只负责执行已经被确定好的：

- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

## highThinkingQA owns

`highThinkingQA` 只负责 `thinking` 的普通问答执行，不承担文件 QA。

## patent backend owns

`patent` backend 只负责 `patent` 普通问答执行，不承担文件 QA。

---

## Route Taxonomy

## 1. `kb_qa`

使用条件：

- 当前问题不要求使用文件
- 文件只是会话背景，不是本轮证据源
- 或用户问题明确是普通知识性问题

约束：

- `turn_mode=kb_only`
- `source_scope=kb`
- `execution_files=[]`

## 2. `pdf_qa`

使用条件：

- 本轮明确针对 PDF 文件
- 不需要表格执行
- 不需要知识库同时参与

约束：

- 至少有 1 个 PDF 文件
- `turn_mode=file_only`
- `source_scope=pdf`

## 3. `tabular_qa`

使用条件：

- 本轮明确针对表格
- 问题需要表格执行/字段理解/行列筛选/聚合
- 不需要知识库同时参与

约束：

- 至少有 1 个表格文件
- `turn_mode=file_only`
- `source_scope=table`

## 4. `hybrid_qa`

使用条件：

- 文件 + 知识库
- PDF + 表格
- 表格 + 知识库
- PDF + 表格 + 知识库

约束：

- `turn_mode=file_only` 当 `source_scope=pdf+table`
- `turn_mode=mixed` 当 `source_scope=pdf+kb`、`table+kb`、`pdf+table+kb`
- `source_scope` 必须是：
  - `pdf+kb`
  - `table+kb`
  - `pdf+table`
  - `pdf+table+kb`

---

## Routing Pipeline

## Step 0: Collect Inputs

输入来源：

- `question`
- `requested_mode`
- `conversation_id`
- `pdf_context`
  - `selected_ids`
  - `newly_uploaded_ids`
  - `all_available_ids`
  - `last_focus_ids`
  - `last_turn_route`
- 会话文件元数据
  - `file_id`
  - `file_type`
  - `parse_status`
  - `index_status`
  - `processing_stage`
  - `file_meta`

## Step 1: Normalize File Universe

生成候选文件集合：

- `all_non_deleted_files`
- `ready_files`
- `ready_pdf_files`
- `ready_table_files`
- `selected_ready_files`
- `last_focus_ready_files`
- `newly_uploaded_ready_files`

并建立两个解析宇宙：

1. `reference_resolution_universe`
- 当前会话所有未删除文件
- 按 `display_no -> file_no -> file_id` 排序
- 用于解析 `#1`、`第 1 个文件`、`前 3 个文件`

2. `execution_resolution_universe`
- `reference_resolution_universe` 中的 ready 子集
- 用于真正生成 `execution_files`

要求：

- 删除状态文件不得进入候选集合
- 非 ready 文件不应默认进入执行文件集合
- 但可用于澄清提示和状态提示

## Step 2: Detect Explicit Intent

显式信号分两类：

### A. 显式使用意图

- `#编号`
- “这篇文献 / 这篇论文 / 这个文件 / 该文献”
- “这个表格 / 这张表 / this table”
- “第 N 个文件 / 前 N 个文件 / 后 N 个文件”
- “最新上传 / 刚上传”
- “结合这篇文献和知识库 / 结合文件和知识库”
- “总结 / 对比 / 解释 / 分析 / 结合文件内容”等明确文件型动作

### B. 显式作用域上下文

- 复选框选中文件

`selected_ids` 的语义是“这些文件可参与本轮”，不是“本轮必须进入文件 QA”。

如果存在显式使用意图，则优先按显式意图构造 route。

如果只有显式作用域上下文而没有显式使用意图：

- 不能直接进入文件 route
- 只进入歧义检测和分类器层

## Step 3: Rule-Based Strong Routing

以下情况直接路由，不进入分类器：

强规则优先级固定如下，命中后立即停止后续判断：

1. 明确混合意图
2. 明确对象解析成功的单文件/多文件引用
3. 显式多选 + 明确文件型动作
4. 强表格操作意图

### A. 显式多选文件 + 明确文件型动作

只有在以下两者同时满足时，本条规则才生效：

1. `selected_ids` 非空
2. 问题存在明确文件型动作或明确跨文件要求

并且：

- `selected_ids` 解析出的目标文件必须在 `execution_resolution_universe` 中至少有 1 个可执行文件
- 如果显式文件意图存在，但解析后的可执行集合为空，则不得继续 direct route，而必须进入 Step 6 的非路由状态响应

生效后：

- 多个 PDF -> `pdf_qa`
- 多个表格 -> `tabular_qa`
- PDF + 表格 -> `hybrid_qa`
- 任意文件 + 明确知识库意图 -> `hybrid_qa`

### B. 明确单文件指代

- 单个 PDF -> `pdf_qa`
- 单个表格 -> `tabular_qa`

### C. 明确混合意图

如：

- “结合这篇文献和知识库”
- “参考这个表格并结合知识库”
- “结合这几篇论文和知识库”

直接进入 `hybrid_qa`

### D. 明确表格操作意图

只有在以下两者同时满足时，才能进入 `tabular_qa`：

1. 有表格候选文件
2. 命中强表格操作模式

强表格操作模式示例：

- “按某列筛选”
- “统计均值”
- “按字段分组”
- “找最大值”
- “输出前 10 行”
- “这个表格里”

注意：

- 单字级 `列`、`行`、`表` 不允许作为强规则
- “列出”“进行”“表明”等词不能触发表格路由

## Step 4: Ambiguity Detection

以下场景视为歧义：

1. 会话里有文件，但问题没有明确文件意图
2. 问题出现弱文件相关词，但没有明确对象
3. 当前候选文件类型与问题指代不一致
4. 文件相关性存在，但不能确定是 `pdf_qa`、`tabular_qa` 还是 `hybrid_qa`
5. 只有 `selected_ids`，但缺少明确文件使用意图

只有在歧义场景才调用轻量分类器。

## Step 5: Lightweight Classifier Routing

分类器输入应尽量小：

- `question`
- 当前是否有已选文件
- 当前 ready PDF 数量
- 当前 ready 表格数量
- 最近一轮 route
- 最近焦点文件类型摘要
- 文件名摘要
- 列名摘要，仅限表格且长度受限

分类器输出：

```json
{
  "route": "kb_qa|pdf_qa|tabular_qa|hybrid_qa",
  "turn_mode": "kb_only|file_only|mixed",
  "source_scope": "kb|pdf|table|pdf+kb|table+kb|pdf+table|pdf+table+kb",
  "confidence": 0.0,
  "reason_codes": ["CLASSIFIER_FILE_QA"]
}
```

分类器规则：

- 高置信度才覆盖规则默认值
- 低置信度不允许强行推到文件 QA
- 对无明确文件意图的问题，低置信度默认回 `kb_qa`

## Step 6: Clarification Policy

满足以下情况应澄清而不是猜：

1. 明确说“这篇文献/这个文件”，但当前有多个候选文件
2. 明确说“这个表格”，但当前有多个候选表格
3. 指代编号无法解析
4. 用户要求对比多文件，但候选范围不明确

满足以下情况应返回非路由状态响应，而不是继续 ask：

1. 用户存在明确文件意图，但解析到的目标文件仍在 `uploaded/parsing/indexing`
2. 用户存在明确文件意图，但解析到的目标文件处于 `failed`
3. 用户存在明确文件意图，但目标已删除或不存在
4. 用户存在明确文件意图，但解析后的 `execution_files` 为空

状态响应约定：

- `processing`
  - `code=FILE_NOT_READY`
  - `retriable=true`
  - 返回文件状态摘要

- `failed`
  - `code=FILE_PROCESSING_FAILED`
  - `retriable=false`
  - 返回失败原因摘要

- `deleted/missing`
  - `code=FILE_NOT_FOUND`
  - `retriable=false`
  - 返回目标不存在提示

澄清响应必须返回：

- `needs_clarification=true`
- 候选文件列表摘要
- 建议用户明确文件编号或重新勾选

## Step 7: Final Route Normalization

标准化输出：

- `route`
- `turn_mode`
- `source_scope`
- `selected_file_ids`
- `execution_files`
- `strategy`
- `reason_codes`
- `confidence`
- `needs_clarification`
- `classifier_used`

---

## Strong Rule Catalog

## 1. Explicit Selection Rules

### Rule R1

如果 `selected_ids` 非空：

- 只表示“这些文件可用于本轮”
- 不再自动等于“本轮必须用文件”

进一步规则：

- 如果问题存在显式文件/表格/混合指代 -> 进入文件链路
- 如果问题存在显式文件型动作且 selection 能提供明确作用域 -> 进入文件链路
- 如果问题没有文件意图 -> 默认进入歧义层，不直接强推文件 QA

这是本设计相对当前实现的关键变化。

### Rule R2

如果用户手动勾选文件，且问题是明显文件型动作：

- “总结这篇文献”
- “分析这个表格”
- “对比这三篇文献”

则直接进入文件或混合路由。

## 2. Explicit Reference Rules

### Rule R3

支持以下引用方式：

- `#1`
- `第 1 个文件`
- `前 3 个文件`
- `后 2 个文件`
- `倒数第 1 个文件`
- “这 3 篇文献”

编号与序号统一基于 `reference_resolution_universe`：

- 当前会话所有未删除文件
- 按 `display_no -> file_no -> file_id` 排序

执行时再映射到 `execution_resolution_universe`：

- 如果解析出的目标 ready，则进入执行
- 如果解析出的目标 non-ready，则进入状态响应分支

### Rule R4

如果引用能唯一解析，则直接构造文件作用域。

### Rule R5

如果引用不能唯一解析，则澄清。

## 3. Last Focus Rules

### Rule R6

`last_focus_ids` 只能在以下条件下复用：

1. 当前问题有显式代词指代，如“这篇”“这个表格”“上面那个文件”
2. 最近一轮 route 也是文件相关
3. `last_focus_ids` 在当前会话文件列表中仍有效

### Rule R7

不能仅仅因为存在 `last_focus_ids`，就把普通问题自动拉入文件 QA。

## 4. Latest Upload Rules

### Rule R8

只有命中“最新上传 / 刚上传 / latest uploaded”这类明确语义时，才复用最新上传文件。

### Rule R9

上传后自动填充示例提示词不应反向影响后续所有普通问答的 route。

## 5. Table Rules

### Rule R10

表格路由必须满足：

1. 有表格候选文件
2. 问题文本存在强表格意图，或用户明确选择表格对象

### Rule R11

以下词不允许单独触发表格 QA：

- `列`
- `行`
- `表`

原因：

- “列出”“进行”“表明”均会误伤

### Rule R12

列名命中只可作为弱证据，不能单独决定 `tabular_qa`。

列名命中仅在以下前提下生效：

1. 已明确是表格相关问题
2. 当前有单个或已明确选定的表格

## 6. Mixed Rules

### Rule R13

出现明确混合意图时，直接进入 `hybrid_qa`：

- “结合这篇文献和知识库”
- “结合这个表格和知识库”
- “结合文献内容和知识库解释”

### Rule R14

如果同时选中了 PDF 和表格，即使问题未提知识库，也可以进入 `hybrid_qa`

前提：

- 问题内容明确要求跨文件类型联合分析
- 或显式多选场景要求对比/联合回答

### Rule R15

“会话里同时存在 PDF 和表格”本身不应自动进入 `hybrid_qa`

---

## Classifier Design

## 1. Why a Classifier Exists

仅用规则无法稳定处理以下问题：

- “为什么厚电极在高电流密度下会有严重浓差极化”
- “单体电压显示 0V 但总电压正常，可能原因是什么”
- “结合这篇文献解释一下这个现象”

这些请求可能同时具有：

- 普通知识问答语义
- 文件背景
- 混合问答需求

分类器负责处理这些歧义样本。

## 2. Recommended Model Classes

### Zero-shot text classifier

可选：

- `facebook/bart-large-mnli`
- `MoritzLaurer/ModernBERT-base-zeroshot-v2.0`
- `MoritzLaurer/ModernBERT-large-zeroshot-v2.0`

适用：

- 英文或中英混合
- 路由标签数量有限
- 快速验证标签设计

### Small LLM router

可选：

- `Qwen2.5-1.5B-Instruct`
- `Qwen2.5-3B-Instruct`

适用：

- 中文为主
- 输出结构化 JSON
- 需要结合简短文件上下文判断

推荐：

- 第一阶段优先使用小模型 router
- 因为当前场景是中文、多轮、文件指代和混合 QA

## 3. Classifier Labels

分类标签不是单独 `route`，而是三元组：

- `route`
- `turn_mode`
- `source_scope`

推荐标签集合：

- `kb_qa / kb_only / kb`
- `pdf_qa / file_only / pdf`
- `tabular_qa / file_only / table`
- `hybrid_qa / mixed / pdf+kb`
- `hybrid_qa / mixed / table+kb`
- `hybrid_qa / file_only / pdf+table`
- `hybrid_qa / mixed / pdf+table+kb`

## 4. Confidence Policy

建议阈值：

- `>= 0.80`：可直接采用分类结果
- `0.60 - 0.79`：仅在与规则层不冲突时采用
- `< 0.60`：回退规则默认值

默认回退规则：

- 没有明确文件意图 -> `kb_qa`
- 明确文件指代但文件不唯一 -> 澄清

---

## State Reuse Policy

## 1. Selected Files

`selected_ids` 的语义应改为：

- 用户声明这些文件“可参与本轮”
- 不是绝对强制进入文件 QA
- 只提供作用域，不独立构成文件使用意图

## 2. Last Focus

`last_focus_ids` 的语义应改为：

- 只在代词回指或延续文件话题时作为候选增强
- 不能跨越到普通知识问答里自动生效

## 3. Last Turn Route

`last_turn_route` 只作为弱状态，不允许单独强制继承本轮 route。

正确用法：

- 在“这篇/这个/上面那个”代词型问题中辅助选择作用域

错误用法：

- 上一轮是 `tabular_qa`，下一轮普通问题自动继续 `tabular_qa`

---

## Actual Backend Decision

## 1. Requested Mode vs Actual Mode

规则：

- `kb_qa`:
  - `fast` -> `fastQA`
  - `thinking` -> `highThinkingQA`
  - `patent` -> `patent` backend

- 文件相关：
  - `pdf_qa`
  - `tabular_qa`
  - `hybrid_qa`

统一落到 `fastQA`

即：

- 用户前端选择 `thinking`
- 但问题被路由成文件 QA
- `actual_mode` 必须改成 `fast`

- 用户前端选择 `patent`
- 但问题被路由成文件 QA
- `actual_mode` 也必须改成 `fast`

同时保留：

- `requested_mode=thinking`
- `actual_mode=fast`

这保证用户体验和后端职责边界一致。

## 2. Backend Contract Requirements

下游执行请求必须显式包含：

- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `source_scope`
- `selected_file_ids`
- `execution_files`
- `file_selection`
- `route_reasons`
- `route_confidence`
- `classifier_used`

`fastQA` 不得再自行重算 route。

---

## Logging And Observability

每一轮 ask，`gateway` 必须输出统一路由日志：

- `trace_id`
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `source_scope`
- `strategy`
- `selected_file_ids`
- `candidate_pdf_count`
- `candidate_table_count`
- `reason_codes`
- `classifier_used`
- `classifier_confidence`
- `needs_clarification`

示例语义：

```text
gateway route decision trace_id=... requested_mode=thinking actual_mode=fast route=hybrid_qa turn_mode=mixed source_scope=pdf+kb strategy=explicit_selection selected_file_ids=[12] reason_codes=[EXPLICIT_SELECTED_FILES,EXPLICIT_MIXED_INTENT] classifier_used=false
```

---

## Metrics

建议增加：

- `gateway_route_total{route=...}`
- `gateway_route_classifier_total{used=true|false}`
- `gateway_route_clarification_total`
- `gateway_route_override_total{requested_mode=...,actual_mode=...}`
- `gateway_route_fallback_kb_total`

误判治理相关：

- `gateway_route_posthoc_correction_total`
- `gateway_route_user_retry_after_file_route_total`

---

## Acceptance Criteria

该设计视为达标，必须满足：

1. 普通问题不会因为会话里有 PDF 或表格而默认进入文件 QA
2. “列出 / 进行 / 表明”这类词不会误触发表格 QA
3. 有明确文件指代时，能稳定进入正确文件 route
4. 文件 + 知识库语义稳定进入 `hybrid_qa`
5. `thinking` 模式下的文件问答会正确转发到 `fastQA`
6. 日志能解释每次 route 是怎么得出的
7. 无法确定时优先澄清，而不是错误执行

---

## Rollout Plan

### Phase 1

- 冻结 route contract
- 引入 `reason_codes / confidence / classifier_used`
- 修正当前硬规则误判

### Phase 2

- 接入轻量分类器，仅处理歧义样本
- 加入 classifier 日志和指标

### Phase 3

- 通过真实线上样本迭代规则表和分类标签
- 建立误判回归样本集

---

## Key Decisions

### Decision 1

采用“规则优先，歧义时轻量分类器二判”。

### Decision 2

默认策略为：

- 有文件存在，不等于本轮使用文件
- 无明确文件意图时，默认 `kb_qa`

### Decision 3

混合 QA 作为一级 route，统一表达所有多源问答。

### Decision 4

弱关键词不允许直接触发文件 route，尤其是单字级规则。
