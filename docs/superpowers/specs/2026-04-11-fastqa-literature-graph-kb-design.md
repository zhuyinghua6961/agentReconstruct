# FastQA Literature Graph KB Design

**Date:** 2026-04-11

## Scope

本设计定义如何在现有 `fastQA` 普通知识库问答链路中，低侵入接入“文献知识图谱问答”能力。

本设计只覆盖：

- `fastQA` 的普通问答路由 `kb_qa`
- 文献知识图谱
- 图谱优先尝试、失败静默回退到当前 generation-driven 主链
- 后端内部能力接入、运行时、日志、测试与回退策略

本设计明确不覆盖：

- 专利图谱
- 新增独立后端
- 新增前端显式开关
- 修改当前文件问答路由 `pdf_qa / tabular_qa / hybrid_qa`
- 恢复文档中“图谱 + 向量双召回 hybrid”全套经典链路
- 第一阶段开放式自然语言任意 Cypher 生成

---

## Goal

在不破坏当前 `fastQA` `kb_qa` 主路径的前提下，为“适合结构化图谱回答”的文献问题增加一个前置图谱处理分支：

1. 问题明显适合图谱时，优先尝试图谱回答
2. 图谱回答成功时，直接输出图谱答案
3. 图谱不可用、不适合、未命中或执行失败时，静默回退到当前 generation-driven 向量主链
4. 对用户不新增 route、不新增模式、不新增显式 UI 开关
5. 不得影响文件侧 `hybrid_qa` 的既有语义和实现

最终目标：

- 保持当前 `kb_qa` 为主力链路
- 在少量结构化问题上获得更强的图谱回答能力
- 将图谱能力接入成本和回滚成本控制在很低的范围内

---

## Non-Goals

本设计不包含：

1. 将文档里的 `CommanderAgent -> precise / semantic / hybrid / dual_hybrid` 全量恢复到现网 `fastQA`
2. 让 `gateway` 负责图谱 vs 向量的细分路由
3. 让 `hybrid_qa` 重新解释为“图谱 + 语义混合问答”
4. 在第一阶段直接接入 LLM 自由生成任意 Cypher
5. 在第一阶段做图谱和向量结果融合排序
6. 改写当前 generation orchestrator 五阶段主结构
7. 对前端协议增加新的外显交互字段

---

## Background

当前代码和文档存在两套不同语义，必须先明确边界。

### 1. 当前线上 `fastQA` 普通问答主链

当前 `fastQA` 的 `kb_qa` 实际走的是 generation-driven 问答链，而不是旧版经典图谱链。

入口链路：

- [`fastQA/app/main.py`](/home/cqy/worktrees/highThinking/fastQA/app/main.py)
- [`fastQA/app/routers/qa.py`](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py)
- [`fastQA/app/modules/qa_kb/service.py`](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/service.py)
- [`fastQA/app/modules/qa_kb/orchestrators/generation.py`](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/orchestrators/generation.py)

其中 [`QaKbService.iter_answer_events()`](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/service.py) 目前只支持 generation-driven 模式；当 `pipeline_mode` 非 generation-driven 时，会直接报不支持。

### 2. 文档中的经典图谱语义

[`知识图谱问答流程.md`](/home/cqy/worktrees/highThinking/docs/audit/知识图谱问答流程.md) 描述的是经典 `MaterialScienceAgent` 路径：

- `precise`：图谱精确查询
- `semantic`：向量语义搜索
- `hybrid`：图谱 + 向量混合
- `dual_hybrid`：双路召回融合

这套语义与当前 `fastQA` 现网主链不一致，不能直接假设已经存在。

### 3. 当前 `hybrid_qa` 的真实语义

当前 `fastQA` 和 `gateway` 中的 `hybrid_qa` 已经有严格定义，表示“文件混合问答”：

- `pdf+kb`
- `table+kb`
- `pdf+table`
- `pdf+table+kb`

关键位置：

- [`fastQA/app/services/request_adapter.py`](/home/cqy/worktrees/highThinking/fastQA/app/services/request_adapter.py)
- [`fastQA/app/routers/qa.py`](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py)
- [`fastQA/app/services/file_routes.py`](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py)
- [`fastQA/app/modules/qa_tabular/service.py`](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py)
- [`gateway/app/services/route_decision.py`](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py)

因此，“图谱混合问答”不能复用 `hybrid_qa` 这个 route 名称，也不能改写其含义。

### 4. 当前 Neo4j 资产状态

本机存在文献图谱数据目录：

- `/home/cqy/neo4j/neo4j-community-5.26.7/data/databases/neo4j`
- `/home/cqy/neo4j/neo4j-community-test/neo4j-community-5.26.7/data/databases/neo4j`

数据体量约 620MB，说明文献图谱数据大概率已导入。

当前仓库中存在可复用的 Neo4j bootstrap 与最小图查询能力，主要位于：

- [`public-service/backend/app/integrations/neo4j/client.py`](/home/cqy/worktrees/highThinking/public-service/backend/app/integrations/neo4j/client.py)
- [`public-service/backend/app/modules/retrieval/service.py`](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/retrieval/service.py)
- [`public-service/backend/app/modules/documents/service.py`](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/service.py)
- [`public-service/backend/app/modules/documents/reference_preview.py`](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/documents/reference_preview.py)

但当前 `fastQA` 没有正式启用独立的图谱问答 runtime。

---

## Requirements

### Functional Requirements

1. 普通 `kb_qa` 请求支持静默图谱优先尝试
2. 仅当问题适合图谱且图谱结果质量达标时，返回图谱答案
3. 图谱未处理时，必须继续当前 generation-driven 主链
4. 图谱能力仅作用于文献图谱
5. 不新增 `graph_qa`、`graph_mode` 或其它新 route
6. 不影响 `pdf_qa / tabular_qa / hybrid_qa`

### Safety Requirements

1. 任何图谱异常都不得中断当前 `kb_qa` 主路径
2. 图谱运行时未配置或未启动时必须静默回退
3. 不得把图谱混合语义嫁接到文件 `hybrid_qa`
4. 第一阶段不允许执行自由写入型 Cypher
5. 第一阶段只支持白名单模板查询

### Product Requirements

1. 前端不展示新的显式图谱开关
2. 用户提普通问题时，仍然只是在普通问答模式下提问
3. 回答形态可在内部标记 `query_mode=graph_kb`，但不要求前端新增特殊展示

---

## Design Summary

本设计采用“`kb_qa` 前置图谱尝试 + 主链静默回退”的低侵入方案。

整体流程：

1. 请求按现有方式进入 `fastQA` `kb_qa`
2. 在进入 generation-driven 主链前，调用 `graph_kb_service.try_answer(...)`
3. 图谱分支完成以下判断：
   - 图谱 runtime 是否可用
   - 当前问题是否适合图谱
   - 是否存在可执行的白名单模板查询
   - 查询结果是否满足最小质量门槛
4. 若图谱成功处理，则直接以现有 SSE 合约返回答案
5. 若任一环节失败或不适合，则继续当前 `qa_kb_service.iter_answer_events(...)`

该设计的核心是：

- 图谱分支是“增强能力”，不是“替代主链”
- generation-driven 仍然是 `kb_qa` 的默认主力路线
- 图谱对外是无感存在，对内是可观测、可禁用、可回滚的薄层
- 第一阶段图谱答案不依赖 LLM 合成，而采用确定性渲染

---

## Alternatives Considered

### Option A: 恢复文档原始经典图谱总路由

做法：

- 在 `kb_qa` 主入口恢复 `precise / semantic / hybrid / dual_hybrid`
- 用经典 Commander 语义统一接管普通问答

优点：

- 与历史文档最一致

缺点：

- 对当前 `fastQA` 主链侵入很大
- 会弱化 generation-driven 主路径
- 风险最高，不符合“绝对不能影响当前 kbqa 主路径”

结论：

- 不采用

### Option B: `kb_qa` 前置图谱尝试

做法：

- 仅在 `route == "kb_qa"` 内部增加图谱尝试分支

优点：

- 侵入小
- 失败回退简单
- 不影响文件路由

缺点：

- 与经典文档不是完全同构

结论：

- 采用

### Option C: 将图谱逻辑嵌入 generation orchestrator 某阶段

做法：

- 在五阶段 generation 内插入图谱检索

优点：

- 单入口不变

缺点：

- 让图谱逻辑和 generation 流程深度耦合
- 失败边界变复杂
- 后续调试和回滚困难

结论：

- 不采用

---

## Architecture

建议在 `fastQA` 内新增一组独立模块：

- `fastQA/app/modules/graph_kb/models.py`
- `fastQA/app/modules/graph_kb/client.py`
- `fastQA/app/modules/graph_kb/classifier.py`
- `fastQA/app/modules/graph_kb/service.py`

以及在 `fastQA` runtime 中补最小图谱 bootstrap 支撑：

- `fastQA/app/integrations/neo4j/client.py`
- `fastQA/app/core/runtime.py` 中新增图谱 runtime 初始化和状态

职责划分如下。

### `graph_kb/models.py`

定义内部数据结构：

- `GraphKbDecision`
- `GraphKbExecutionResult`
- `GraphKbQueryPlan`
- `GraphKbRuntimeStatus`

要求：

- 明确区分 `skip / attempted / handled / fallback_reason`
- 明确保留 `cypher_template_id`、`result_count`、`latency_ms`

### `graph_kb/classifier.py`

负责将普通问题判定为：

- `skip`
- `try_graph`

第一阶段不引入 LLM router，只使用保守规则判断。

判定原则：

- 开放解释、方法综述、因果分析、宽泛总结问题 -> `skip`
- 实体过滤、比较、统计、排名、列举、关系确认 -> `try_graph`

### `graph_kb/client.py`

负责：

- Neo4j 只读查询执行
- 白名单模板到具体 Cypher 的绑定
- 查询超时与结果集裁剪

第一阶段只允许模板化查询，不允许自由拼装任意 Cypher。

### `graph_kb/service.py`

对 `kb_qa` 提供统一入口：

- `try_answer(question, conversation_context, runtime, logger, ...)`

返回：

- `handled=True` 且包含答案
- 或 `handled=False` 且附带回退原因

第一阶段该 service 还负责：

- 将结构化图查询结果渲染为最终答案文本
- 组装 `references`
- 产出图谱成功路径的 SSE 事件

这里的“渲染”是确定性格式化，不是 LLM 合成。

---

## Runtime Design

### Runtime Bootstrap Strategy

`fastQA` 需要新增最小 Neo4j runtime，但不直接复用 `public-service` 模块文件路径。

原因：

1. [`fastQA/app/README.md`](/home/cqy/worktrees/highThinking/fastQA/app/README.md) 明确要求不要把 `public-service` 模块直接塞进来
2. `fastQA` 应保持自己的 runtime 边界
3. 图谱能力未来可能只在 `fastQA` 内做演化

因此本设计采用“等价迁移最小 bootstrap 逻辑”的方式：

- 参考 [`public-service/backend/app/integrations/neo4j/client.py`](/home/cqy/worktrees/highThinking/public-service/backend/app/integrations/neo4j/client.py)
- 在 `fastQA` 本地新增最小 `bootstrap_neo4j(...)`
- 只保留只读连接与降级能力

### Runtime State

在 [`fastQA/app/core/runtime.py`](/home/cqy/worktrees/highThinking/fastQA/app/core/runtime.py) 中增加：

- `neo4j_client`
- `graph_kb_ready`
- `component_status["graph_kb"]`

状态要求：

- 未配置 `NEO4J_URL` -> `skipped`
- 配置但连接失败 -> `degraded`
- 连接成功 -> `ok`

图谱 runtime 的失败不得影响 `generation_runtime_ready`。

---

## Query Strategy

### Phase 1: Template-Only Queries

第一阶段查询必须走白名单模板，不做开放式 Cypher 生成。

支持的问题类型：

1. DOI 对应文献元数据查询
2. 材料名 / 实体名对应的文献或属性列举
3. 数值比较
   - 大于
   - 小于
   - 最高
   - 最低
   - Top N
4. 计数统计
5. 关系存在性确认

每类问题对应固定模板，例如：

- `lookup_by_doi`
- `list_by_material`
- `compare_numeric_threshold`
- `rank_numeric_topn`
- `count_by_filter`
- `relation_exists`

### Phase 1 Schema Prerequisites

第一阶段不要求完整 schema 自动发现，但要求实现前明确以下最小图谱字段前提：

1. 文献节点或可返回文献信息的节点必须能稳定提供以下至少一部分字段：
   - `doi`
   - `title`
   - `journal`
   - `publication_date` 或 `date`
   - `material_name`
2. 数值比较类模板只能访问显式白名单数值属性
3. 关系确认类模板只能访问显式白名单关系类型

实现约束：

- 数值属性白名单和关系类型白名单必须写在代码中显式配置
- 未进入白名单的字段和关系，不得被 Phase 1 模板查询使用
- rollout 前必须基于实际图谱 schema 完成这份白名单校验

### Phase 1 Answer Rendering Contract

第一阶段图谱答案生成必须采用确定性渲染，不使用 LLM，也不依赖当前 `generation_runtime`。

具体要求：

1. `graph_kb_service` 根据模板类型将结构化结果渲染为稳定文本
2. 渲染逻辑必须是纯函数式或等价的确定性逻辑，输入相同结果应输出相同答案
3. 不新增模型依赖，不新增图谱专用 prompt
4. 若结构化结果不足以形成稳定答案，则视为质量不达标并回退到 generation-driven 主链

模板建议输出形态：

- `lookup_by_doi`
  - 返回文献标题、期刊、日期、DOI
- `list_by_material`
  - 返回命中的文献或属性列表
- `compare_numeric_threshold`
  - 返回满足阈值条件的实体数量和代表项
- `rank_numeric_topn`
  - 返回排序前 N 项及其数值
- `count_by_filter`
  - 返回明确计数结果
- `relation_exists`
  - 返回存在 / 不存在及匹配到的关系摘要

这样做的原因：

1. 避免在第一阶段把图谱能力和 LLM/runtime 耦合
2. 明确图谱路径的延迟、失败模式和测试边界
3. 保持“图谱失败即可安全回退”的简单模型

### Why Template-Only First

第一阶段不做自由 Cypher 的原因：

1. 用户明确担心破坏现有主链
2. 模板查询更容易做安全收敛
3. 对现有文献图谱是否稳定可用尚未完全验证
4. 先证明图谱问答值得保留，再考虑更强表达能力

### Future Phase Explicitly Deferred

以下能力明确延期，不属于本设计：

- LLM 生成 Cypher
- 图谱 schema 自动 introspection
- 图谱和向量双路融合排序
- 文档原始 `hybrid / dual_hybrid` 恢复

---

## Decision Policy

### Decision Inputs

图谱判定仅使用：

- 当前问题文本
- 必要的 conversation context
- 图谱 runtime 可用性

不依赖：

- `gateway` 新 route
- 前端新字段
- 文件上下文推断图谱模式

这里的 `conversation context` 在 Phase 1 中只允许用于“安全判定是否应跳过图谱”，不允许用于做复杂上下文补全后的图查询执行。

允许使用的上下文信息仅包括：

- 当前轮问题是否包含明显代词或省略
- 最近一轮是否是文件 route
- 最近一轮是否已经展示多个候选实体导致当前问题可能存在指代歧义

不允许的上下文用途：

- 通过历史对话自动补齐省略实体后强行执行图查询
- 根据多轮上下文拼接新的图查询语义
- 将 follow-up 问题解释为图谱查询模板输入

### Conservative Rule Set

以下类型默认 `skip`：

- “为什么”
- “如何”
- “有什么意义”
- “总结一下”
- “介绍一下”
- “综述”
- “机制”
- “趋势分析”
- “方法对比”

以下类型允许 `try_graph`：

- “有哪些”
- “哪个最高/最低”
- “大于/小于/超过/低于”
- “前 5 个”
- “多少篇”
- “是否存在”
- “A 和 B 有什么关系”
- “某 DOI 是什么文献”

设计原则：

- 宁可少命中图谱，也不能误伤当前主链
- 图谱判定只能保守扩张，不能激进接管

### Follow-Up Question Rule

Phase 1 对 follow-up 问题采用严格保守策略。

以下问题一律 `skip`，直接回到 generation-driven 主链：

1. 依赖“它 / 这个 / 那篇 / 上面那个 / 前者 / 后者”等指代词才能理解的问题
2. 依赖上一轮候选列表才能确定查询对象的问题
3. 需要把历史多轮上下文拼装后才完整的问题

换言之，Phase 1 只处理“当前轮自身就足够完整”的 standalone 问题。

这样可以避免：

- 图谱分类器错误理解上下文
- 模板规划因省略语义做错查询
- 在多轮追问里误伤当前主力向量链

---

## `kb_qa` Integration Point

图谱分支只接入一个地方：

- [`fastQA/app/routers/qa.py`](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py) 的 `route == "kb_qa"` 分支

推荐接入方式：

1. 保持现有 conversation context 构造逻辑不变
2. 在创建 `QaKbRequest` 后、调用 [`qa_kb_service.iter_answer_events()`](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/service.py) 前调用 `graph_kb_service.try_answer(...)`
3. 若 `handled=True`，直接产出 SSE 事件并 `return`
4. 若 `handled=False`，继续原有 generation-driven 路径

这样可以保证：

- 与当前 `kb_qa` 主链耦合最小
- 回滚时只需移除一个前置分支
- generation orchestrator 本身不被图谱逻辑污染

---

## SSE Contract

图谱分支必须复用当前 `fastQA` SSE 合约，不得发明一套新流协议。

最低要求：

1. 发 `metadata`
2. 发必要的 `step`
3. 发 `content`
4. 发 `done`

建议事件语义：

- `metadata.query_mode = "graph_kb"`
- `step = graph_decision`
- `step = graph_query`
- `step = graph_synthesis`

但无论图谱是否命中，最终客户端感知仍应与当前 `kb_qa` 流式体验兼容。

### References Contract

若图谱结果中存在 DOI，则图谱成功路径应尽量填充 `references`，其规则如下：

1. 能稳定提取 DOI 时，`done.references` 返回 DOI 列表
2. 不要求第一阶段额外构造复杂 `reference_objects`
3. 若模板结果没有 DOI，但答案本身成立，可返回空 `references`

该规则用于保持与当前 `kb_qa` 的最低引用兼容性，同时避免在第一阶段扩展过多外围格式逻辑。

---

## Fallback Policy

以下情况统一回退到 generation-driven 主链：

1. Neo4j 未配置
2. Neo4j 连接失败
3. 图谱分类为 `skip`
4. 没有匹配到模板
5. 查询超时
6. 查询抛异常
7. 查询结果为空
8. 查询结果低于最小质量阈值
9. 图谱答案合成失败

回退原则：

- 不向用户显式报图谱失败
- 不改变当前 `kb_qa` 的错误语义
- 只在日志与埋点中记录回退原因

---

## Result Quality Gate

图谱查询成功不代表可以直接返回给用户。

必须有最小质量门槛：

1. 结果条数大于 0
2. 关键字段存在
3. 可生成稳定回答
4. 统计型问题必须返回明确数值
5. 实体型问题必须返回明确实体或关系

若不满足质量门槛，则视为 `handled=False`，转回 generation-driven 主链。

---

## Boundary With File `hybrid_qa`

这是本设计最严格的边界之一。

必须明确：

1. `hybrid_qa` 仍然只表示文件混合问答
2. `source_scope=pdf+kb/table+kb/pdf+table/pdf+table+kb` 的现有语义完全保持不变
3. 图谱能力不新增 route，不重用 `hybrid_qa` 命名
4. 图谱分支只在普通 `kb_qa` 内部静默尝试
5. `pdf_qa / tabular_qa / hybrid_qa` 的测试结果必须与改动前一致

换句话说：

- “图谱增强”是 `kb_qa` 的内部实现细节
- “文件混合问答”是现有独立 route 语义

两者不能共享命名，也不能共享对外 contract。

---

## Observability

建议记录以下字段：

- `graph_kb_attempted`
- `graph_kb_decision`
- `graph_kb_handled`
- `graph_kb_template`
- `graph_kb_result_count`
- `graph_kb_latency_ms`
- `graph_kb_fallback_reason`

这些字段应进入：

- 应用日志
- 如现有有统计钩子，可进入轻量计数指标

观测目标：

1. 图谱命中率
2. 图谱成功率
3. 图谱平均延迟
4. 主要回退原因

---

## Configuration

建议新增以下环境变量：

- `FASTQA_GRAPH_KB_ENABLED=0|1`
- `FASTQA_GRAPH_KB_TIMEOUT_MS`
- `FASTQA_GRAPH_KB_MAX_ROWS`
- `FASTQA_GRAPH_KB_QUERY_LOGGING=0|1`

以及沿用：

- `NEO4J_URL`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`

默认策略：

- `FASTQA_GRAPH_KB_ENABLED=0` 时完全关闭图谱分支
- 未配置 Neo4j 连接时视为 `skipped`

这样 rollout 和回滚都只需要改配置，无需立即回退代码。

---

## Testing Strategy

测试重点不是“图谱答得多聪明”，而是“绝不破坏当前主链”。

### Unit Tests

1. classifier
   - 结构化问题 -> `try_graph`
   - 开放解释问题 -> `skip`
2. template planning
   - 合法问题命中正确模板
   - 无法匹配时返回 `no_template`
3. quality gate
   - 空结果回退
   - 不完整结果回退
   - 合格结果允许直接返回

### Integration Tests

1. `kb_qa` 在 graph 功能关闭时行为不变
2. `kb_qa` 在 Neo4j 不可用时静默回退到 generation-driven
3. `kb_qa` 在图谱命中时可直接返回 `graph_kb` 答案
4. 图谱合成失败时回退到 generation-driven

### Regression Tests

1. `pdf_qa` 无变化
2. `tabular_qa` 无变化
3. `hybrid_qa` 无变化
4. 文件 route 的 `source_scope` 合法性无变化

---

## Rollout Plan

建议采用三步 rollout。

### Step 1: 暗接入

- 代码接入但默认关闭 `FASTQA_GRAPH_KB_ENABLED`
- 验证不影响当前行为

### Step 2: 本机与测试环境打开

- 连接文献 Neo4j
- 只验证结构化问题命中与静默回退

### Step 3: 小范围启用

- 在真实流量中观察命中率、回退率、错误率
- 若出现异常，直接关闭 `FASTQA_GRAPH_KB_ENABLED`

---

## Rollback Strategy

回滚要求必须简单直接：

1. 先通过 `FASTQA_GRAPH_KB_ENABLED=0` 关闭功能
2. 若仍需彻底移除，再删除 `kb_qa` 前置图谱调用
3. 不需要动 generation-driven 主链代码

该设计保证图谱功能是一个可独立关停的增强层，而不是主链结构的一部分。

---

## File Targets For Implementation Planning

预计会涉及以下文件：

### New Files

- `fastQA/app/modules/graph_kb/models.py`
- `fastQA/app/modules/graph_kb/client.py`
- `fastQA/app/modules/graph_kb/classifier.py`
- `fastQA/app/modules/graph_kb/service.py`
- `fastQA/app/integrations/neo4j/client.py`

### Modified Files

- [`fastQA/app/core/runtime.py`](/home/cqy/worktrees/highThinking/fastQA/app/core/runtime.py)
- [`fastQA/app/routers/qa.py`](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py)
- [`fastQA/app/routers/health.py`](/home/cqy/worktrees/highThinking/fastQA/app/routers/health.py)

### Test Files

- `fastQA/tests/test_graph_kb_classifier.py`
- `fastQA/tests/test_graph_kb_service.py`
- `fastQA/tests/test_fastqa_kb_graph_fallback.py`
- 以及必要的现有 route regression tests

---

## Final Decision

本设计确认采用以下方案：

1. 文献图谱能力只接入 `fastQA` 的 `kb_qa`
2. 接入方式为“前置图谱尝试 + generation-driven 静默回退”
3. 不新增 route，不新增 mode，不新增前端显式图谱开关
4. 不改现有文件 `hybrid_qa` 的定义与实现
5. 第一阶段只支持保守规则判定 + 白名单模板图查询
6. 图谱增强默认可通过配置关闭

该方案是当前代码状态下最符合产品目标和安全边界的低侵入接入方式。
