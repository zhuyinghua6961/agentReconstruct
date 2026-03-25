# Llama 体系在文件 QA / 混合 QA 上的可借鉴点整理

日期：2026-03-25
范围：LlamaIndex / LlamaCloud 官方公开文档与当前系统（gateway + fastQA + public-service）对照
目的：提炼可直接借鉴到当前系统设计中的模式，避免照搬不适合生产环境的实现

---

## 1. 结论摘要

Llama 体系里最值得借鉴的，不是某一个“文件 QA 模块”的源码，而是它的几层职责划分：

1. Router 先决定该用哪个数据源/哪个工具
2. 多文档问题先做文档级筛选，再做 chunk 级检索
3. 复杂混合问题先拆子问题，再分别交给不同 query engine/tool
4. 表格问题被当作独立执行工具，而不是 PDF 问答的附属能力
5. 解析层和问答层解耦，复杂文件先结构化，再进入问答

对当前系统最重要的启发是：

1. `selected_ids` 应是候选池，不应直接等于最终执行集
2. `gateway` 应承担“意图判断 + 文件收缩”的职责
3. `fastQA` 更适合承担执行层，而不是承担过多的路由补救逻辑
4. 混合 QA 应由问题语义显式触发，而不是由“候选文件里恰好同时有 PDF 和表格”自动触发

---

## 2. 调研范围与参考资料

本次主要参考 LlamaIndex / LlamaCloud 官方文档：

1. Router Retriever
- https://docs.llamaindex.ai/en/stable/examples/retrievers/router_retriever/

2. SubQuestionQueryEngine
- https://docs.llamaindex.ai/en/stable/api_reference/query_engine/sub_question/

3. RecursiveRetriever
- https://docs.llamaindex.ai/en/stable/api_reference/retrievers/recursive/

4. DocumentSummaryIndex
- https://docs.llamaindex.ai/en/stable/api_reference/indices/document_summary/

5. PandasQueryEngine
- https://docs.llamaindex.ai/en/stable/examples/query_engine/pandas_query_engine/

6. LlamaCloud / LlamaParse
- https://docs.cloud.llamaindex.ai/

说明：
- 本文以官方文档公开的能力边界和推荐模式为依据
- 不对非官方博客、第三方教程做结论性引用
- 以下结论带有针对当前系统架构的工程化解释

---

## 3. Llama 体系的核心模式

### 3.1 Router 负责“选工具/选数据源”，不是直接吞掉所有上下文

LlamaIndex 的 Router Retriever / Router Query Engine 强调的是：
- query 到来后，先在多个候选 retriever / query engine 中做选择
- 可以选一个，也可以选多个
- 选择依据来自 query 本身和 tool metadata，而不是仅凭用户上传了什么

对当前系统的借鉴：
- `gateway` 的角色应更接近 Router
- `selected_ids`、`all_available_ids`、`last_focus_ids` 只是候选上下文
- 最终执行集应由“问题语义 + 文件类型 + 会话状态”共同决定
- 不应该机械地把“当前被选中的所有文件”都交给执行层

这和这次修复表格 QA bug 的方向是一致的：
- `selected_ids` 只是候选池
- “这个表格”应先收缩到表格子集，再决定执行集

### 3.2 多文档问答先做文档级筛选，再做 chunk 级检索

LlamaIndex 的 DocumentSummaryIndex 强调：
- 每个 document 先有一个 summary/profile
- query 到来时先判断哪些 document 值得进入下一层
- 然后再从这些 document 里拿 node/chunk 做更细粒度检索

这套模式比“对所有 chunks 直接做平铺检索”更适合：
- 多篇 PDF 对比
- 多篇文献总结
- 一次会话里持续上传多个 PDF

对当前系统的借鉴：
- 上传 PDF 后，除了 chunk 索引外，建议补一个 document profile
- profile 至少包含：
  - title
  - abstract/summary
  - keywords
  - topic tags
  - 文件类型和可回答问题类型
- 这样在 `gateway` 或 planner 层就可以先做文档级筛选

### 3.3 复杂问题先拆子问题，再分发给不同工具

SubQuestionQueryEngine 的思路是：
- 如果 query 本身是复合问题
- 不直接把所有上下文拼进一个 prompt
- 而是先拆成多个 sub-questions
- 每个子问题分配给更适合的 tool/query engine
- 最后再综合答案

这特别适合混合 QA：
- 表格 + PDF
- PDF + KB
- 表格 + PDF + KB
- 多 PDF 比较 + KB 补充

对当前系统的借鉴：
- 混合 QA 最终不应只是“把多源证据拼到一个 prompt”
- 更稳的做法是：
  1. 先判断主任务类型
  2. 拆成若干子任务
  3. 子任务分别调度给 tabular、pdf retrieval、kb retrieval
  4. 最后统一综合

### 3.4 RecursiveRetriever 体现“先命中上层，再进入下层”

RecursiveRetriever 的核心思想是层级化：
- 先命中更粗粒度的节点
- 再递归进入更细粒度的内容

对文件 QA 的意义：
- PDF 不应只是平铺 chunk
- 更好的结构是：document -> section/page -> chunk
- 表格也不应只是“大字符串/全表文本”
- 更好的结构是：file -> sheet -> row range / column slice

对当前系统的借鉴：
- 你们现在已经有 PDF chunk、表格执行这两条线
- 下一步如果要进一步提高引用准确性和混合 QA 质量，应该补层级 metadata，而不是继续只堆 chunk

### 3.5 表格问题在 Llama 体系里是独立工具，不是 PDF QA 的附属

PandasQueryEngine 的定位非常明确：
- 表格问答先转成 dataframe 操作
- 执行得到真实结果
- 再把结果转成自然语言回答

这和当前 `tabular_qa` 的方向是一致的，说明这条路是对的：
- 表格 QA 应继续是“执行优先”
- 不应退化成“把 CSV 全贴给 LLM 总结”

但官方也明确有安全提醒：
- PandasQueryEngine 依赖 `eval`
- 生产环境必须做严格沙箱

对当前系统的借鉴：
- 可以借鉴“表格 QA 是独立执行工具”这个模式
- 但不能照搬其直接执行方式
- 当前系统更适合继续沿“白名单执行 / 受控执行器 / 明确操作集合”方向演进

### 3.6 LlamaCloud / LlamaParse 更强调“先解析结构化，再问答”

LlamaCloud / LlamaParse 的重点不是问答 prompt，而是：
- 文件解析
- 版面保留
- 表格提取
- 图表解析
- 页面/块级结构化输出

对当前系统的借鉴：
- 文件解析层应继续和问答层解耦
- 如果以后想把 PDF QA、图表 QA、表格 QA、混合 QA 做强，解析层的结构信息必须更完整

---

## 4. 与当前系统的对应关系

### 4.1 gateway 的职责更像 Llama 的 Router

当前系统中：
- `gateway` 接前端请求
- 从 public-service 拉会话文件
- 判断 `kb_qa / pdf_qa / tabular_qa / hybrid_qa`
- 归一化后转发到 fastQA / highThinkingQA

这和 Llama 的 Router 思路是最接近的部分。

建议保持：
- `gateway` 负责意图判断和文件范围收缩
- `fastQA` 负责执行
- `public-service` 负责会话与文件元数据权威存储

不建议把这部分重新下沉到 fastQA 去“补路由错误”，否则职责会重新耦合。

### 4.2 fastQA 更像执行层，不应承担过多的候选收缩逻辑

当前 fastQA 已经承担：
- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

这些都属于执行层能力。

从 Llama 的分层看，更合理的对应是：
- `gateway`: Router / Planner-lite
- `fastQA`: Query engine / Tool executor
- `public-service`: 文件与会话 authority + 元数据仓库

### 4.3 public-service 对应的是文件和会话权威层，而不是问答引擎

LlamaCloud 中很重要的一点是把文件解析和索引前置到服务层。

你们系统里 public-service 也适合承担这些：
- 上传文件元数据
- 文件状态推进
- MinIO/object storage 镜像
- 会话 JSON 权威存储
- 后续如果加强，可以继续承接 document profile / parser outputs

---

## 5. 当前系统最值得借鉴的具体改造方向

### 5.1 为上传文件增加 document profile

建议为每个 PDF / 表格补充 document-level profile，而不是只有 file metadata。

至少可以包含：
- `file_id`
- `file_type`
- `title`
- `short_summary`
- `keywords`
- `topic_tags`
- `language`
- `structured_capabilities`
  - 例如：`supports_pdf_retrieval`、`supports_tabular_execution`

作用：
- 让 gateway/router 层先选 document，再选 chunk/tool
- 让多文档比较问题更稳
- 让混合 QA 的 planning 更容易做

### 5.2 混合 QA 先拆子问题，不直接大杂烩

适合的形式：

1. `table -> explain with pdf`
- 子问题 1：表格中发生了什么
- 子问题 2：文献对该现象的机制解释是什么
- 子问题 3：综合输出

2. `pdf -> verify with kb`
- 子问题 1：该文献的核心结论是什么
- 子问题 2：知识库中是否有补充或冲突证据
- 子问题 3：综合输出

3. `pdf + table + kb`
- 子问题 1：表格执行
- 子问题 2：文献证据检索
- 子问题 3：知识库补充
- 子问题 4：统一综合

### 5.3 对多文件上传问题增加“文档级先筛选”

当前多文件上传后，如果全部直接进入执行层，会增加：
- 延迟
- 干扰
- 路由放大错误
- 无关证据污染

建议未来演进：
- 多 PDF 会话：先选相关 document，再做 retrieval
- 多表格会话：先选目标 table，再做 execution
- 混合会话：先选主任务，再决定需不需要副数据源

### 5.4 保留文件层级结构，不只保留平铺证据

建议逐步补强：

1. PDF
- document id
- section title
- page number
- paragraph index
- chunk id

2. 表格
- workbook/file id
- sheet name
- row range
- selected columns
- aggregation / filter / sort plan

这些信息对以下功能都有直接帮助：
- 前端展示证据
- DOI/原文跳转
- 引用后验校验
- 混合 QA 综合时的来源对齐

---

## 6. 哪些地方不要直接照搬

### 6.1 不建议直接照搬 PandasQueryEngine 的生产执行方式

原因：
- 官方自己就提醒了执行风险
- 直接 `eval` 在生产环境不可接受

当前系统更适合：
- 受控 DSL
- 白名单操作
- 受限执行器
- 明确日志和回放能力

### 6.2 不建议过早把所有问题都做成开放式 agent 决策

Llama 的某些 agent 模式更自由，但你们系统当前要求：
- 路由可解释
- 日志可定位
- 文件范围可还原
- 前后端联调可复现

因此更适合：
- 规则优先
- planner 有限化
- tool 边界清晰
- 每一步可落日志

### 6.3 不建议让混合 QA 由“文件类型混合”自动触发

这是这次 bug 暴露出来的关键问题。

更合理的触发原则应是：
- 问题语义明确要求混合
- 或 planner 判断单一来源不足以完成任务

而不是：
- 候选里刚好同时存在 PDF 和 table
- 就自动升级成 hybrid

---

## 7. 对当前系统的建议落点

### 7.1 适合放在 gateway 的能力

1. 问题意图判断
2. 单文件/多文件收缩
3. 混合 QA 触发判断
4. 文档级候选筛选
5. 后续可扩展为轻量 planner

### 7.2 适合放在 fastQA 的能力

1. `kb_qa` 检索与生成
2. `pdf_qa` 文件检索与引用
3. `tabular_qa` 执行与回答
4. `hybrid_qa` 多源执行与综合
5. 各阶段缓存与性能优化

### 7.3 适合放在 public-service 的能力

1. 文件 authority
2. 会话 authority
3. 文件元数据与状态推进
4. 对象存储镜像
5. 后续 document profile / parser outputs 的持久化

---

## 8. 与本次表格 QA bug 的直接关系

本次 bug 本质上就是：
- 系统把 `selected_ids` 当成了执行集
- 没有先做单表格指代收缩
- 因此旧 PDF 残留被放大为混合执行

而 Llama 体系最值得借鉴的地方，正好能解释为什么这是不合理的：
- Router 应先决定用哪个 tool / 哪些 source
- 单目标问题应优先收缩执行范围
- 混合任务应由 query 语义明确触发
- 多源数据只是候选，不应自动都进入执行层

因此这次修复方向与主流成熟模式是一致的。

---

## 9. 建议的后续工作顺序

### 第一阶段：继续稳定当前规则路由

1. 完善单表格、单 PDF、多 PDF、多表格的收缩规则
2. 把更多“this table / 这个表格 / 该表 / 最新上传表格”语义补全
3. 把 user message 的原始 `pdf_context/context_hints` 一并持久化，便于排障

### 第二阶段：补 document profile

1. 每个上传文件增加 summary/profile
2. 多文档问题先做文档级筛选
3. 优化多文件比较和会话持续问答

### 第三阶段：引入轻量 sub-question planner

1. 对复杂混合问题拆子任务
2. 子任务分配给 `tabular_qa / pdf_qa / kb_qa`
3. 最终统一综合

### 第四阶段：补层级化证据结构

1. PDF 的 section/page/chunk 层级
2. 表格的 sheet/row-range/column 层级
3. 前端证据展示与跳转能力增强

---

## 10. 最终判断

Llama 体系最值得当前系统借鉴的是以下组合：

1. Router 的职责边界
2. DocumentSummary 的文档级筛选思想
3. SubQuestion 的混合任务分解方式
4. RecursiveRetriever 的层级化检索思想
5. 表格作为独立执行工具的定位

最不该照搬的是：

1. 直接把表格执行暴露给开放式 `eval`
2. 过早做全自由 agent 路由
3. 把“候选文件混合”误当成“问题本身要求混合”

对你们当前系统来说，正确方向不是“把 Llama 全套搬过来”，而是把它的成熟分层思想映射到现有三层架构：

- `gateway` 负责 Router / 轻量 Planner
- `fastQA` 负责 Query Engine / Tool Executor
- `public-service` 负责文件与会话 authority + 解析结果持久化

