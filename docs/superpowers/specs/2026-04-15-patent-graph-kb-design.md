# Patent Graph KB Design

**Date:** 2026-04-15

## Scope

本文记录 `patent` 普通问答接入知识图谱能力前的现状、已知约束、当前可确认的 Neo4j 资产情况，以及后续图谱结构核查的结论边界。

本设计当前只覆盖：

- `patent` 普通问答 `kb_qa` 现状梳理
- `patent` 若接入图谱能力的推荐挂接层
- 用户提供的本地图谱路径 `/home/cqy/neo4j/neo4j-community-test/neo4j-community-5.26.7/`
- 基于本地目录、CSV、Neo4j store 元信息、日志的离线只读观察
- 基于专利实例 live 查询得到的在线 schema 观察
- 图谱资产与 `patent` 目标之间的结构匹配度评估

本设计当前不覆盖：

- 直接实现 `patent` 图谱问答
- 新建独立 `graph_qa` route
- 把 fastQA 文献图谱模板直接照搬到 `patent`
- 未经真实 schema 验证就假设 Neo4j 中已经存在“专利号 / claim / paragraph / IPC / applicant”等专利节点模型

---

## Goal

在不改动现有 `patent` staged QA 主链的前提下，明确三件事：

1. `patent` 当前普通问答链路是否已经具备图谱前置能力
2. 用户提供的 Neo4j 资产是否真的是“专利图谱”，还是实际上仍然是文献/材料图谱
3. 如果未来要给 `patent` 增加 fastQA 类似的图谱能力，应该挂在哪一层，以及数据模型必须满足什么条件

---

## Current Patent QA State

当前 `patent` 普通问答仍然是 patent-native staged retrieval，不是图谱问答。

核心入口链路：

- [`patent/server_fastapi/routers/ask.py`](/home/cqy/worktrees/highThinking/patent/server_fastapi/routers/ask.py)
- [`patent/server/services/ask_service.py`](/home/cqy/worktrees/highThinking/patent/server/services/ask_service.py)
- [`patent/server/patent/executor.py`](/home/cqy/worktrees/highThinking/patent/server/patent/executor.py)
- [`patent/server/patent/kb_service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/kb_service.py)
- [`patent/server/patent/orchestrators/generation.py`](/home/cqy/worktrees/highThinking/patent/server/patent/orchestrators/generation.py)

`kb_qa` 下的真实执行顺序是：

1. `stage1` 深度预回答与检索规划
2. `stage2` patent-native targeted retrieval
3. `stage25` MD 扩展占位，当前默认跳过
4. `stage3` 专利证据与表格组装
5. `stage4` 基于证据合成答案

关键位置：

- [`PatentKbService.run()`](/home/cqy/worktrees/highThinking/patent/server/patent/kb_service.py#L38)
- [`PatentGenerationOrchestrator.run()`](/home/cqy/worktrees/highThinking/patent/server/patent/orchestrators/generation.py#L399)
- [`PatentRuntime.stage2_targeted_retrieval()`](/home/cqy/worktrees/highThinking/patent/server/patent/runtime.py#L339)
- [`PatentRetrievalService.targeted_retrieve()`](/home/cqy/worktrees/highThinking/patent/server/patent/retrieval_service.py#L363)

结论：

- 当前 `patent` 没有 Neo4j bootstrap
- 当前 `patent` 没有 graph preflight
- 当前 `patent` 没有 Cypher 查询层
- 当前 `patent` 的“结构化能力”来自专利 archive、双库向量检索和 lexical fallback，不是图数据库

---

## Patent-Native Constraints

`patent` 不能直接复制 fastQA 文献图谱问答的语义。

关键约束来自：

- [`docs/2026-04-02-patentqa-fastqa-full-pipeline-migration-spec.md`](/home/cqy/worktrees/highThinking/docs/2026-04-02-patentqa-fastqa-full-pipeline-migration-spec.md)
- [`docs/2026-03-30-patentqa-delivery-spec.md`](/home/cqy/worktrees/highThinking/docs/2026-03-30-patentqa-delivery-spec.md)

已经明确的约束：

1. patent 的主检索单位应是“专利”，不是论文
2. patent 的主落地单位应是“claim / description paragraph / table snippet”，不是 paper abstract
3. stage 1 不应退化为 paper-style 的 “one claim -> one retrieval query -> merge”
4. 即便未来有图谱，图谱层也更适合作为 `kb_qa` 前置薄层，而不是嵌进 `stage3` / `stage4`

当前更合理的图谱挂接点：

- 首选：[`patent/server/patent/kb_service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/kb_service.py#L38)
- 次选：[`patent/server/patent/executor.py`](/home/cqy/worktrees/highThinking/patent/server/patent/executor.py#L164)

不建议的挂接点：

- [`patent/server/patent/orchestrators/generation.py`](/home/cqy/worktrees/highThinking/patent/server/patent/orchestrators/generation.py#L399) 内部阶段深插

---

## Provided Neo4j Asset

用户提供的本地图谱路径：

- `/home/cqy/neo4j/neo4j-community-test/neo4j-community-5.26.7/`

目录级别已确认存在：

- `bin/`
- `conf/`
- `data/databases/neo4j`
- `data/databases/system`
- `data/transactions`
- `import/knowledge_graph_triples.csv`
- `logs/`

离线只读观察结果：

1. 这是一个完整的 Neo4j 5.26.7 安装目录，不只是导入文件夹
2. 默认图数据库名是 `neo4j`
3. store 存在且规模不小，`data/databases/neo4j` 约 1.4G
4. `import/knowledge_graph_triples.csv` 约 86M，约 915,916 行

离线 store 元信息历史快照（在实例未运行时通过 `neo4j-admin database info` 采集）：

- `databaseName = neo4j`
- `inUse = false`
- `storeFormat = record-aligned-1.1`
- `lastCommittedTransaction = 672480`
- `recoveryRequired = false`

运行配置与会话内状态观察：

- `server.bolt.listen_address = 0.0.0.0:8687`
- `server.http.listen_address = 0.0.0.0:8474`
- 日志显示该实例在 `2026-04-15 04:46 UTC` 再次完成启动并提供 Bolt / HTTP
- 在本次调查会话内，`neo4j status` 曾出现过“未运行”和“运行中”两种状态
- 当实例处于运行状态时，重新执行 `neo4j-admin database info neo4j` 会因为 `database in use` 失败

因此：

- `inUse = false` 只应视为历史离线快照
- 本文不应被用作该实例“当前是否在线”的权威状态记录

---

## Known Graph Structure

### 1. 只读资产观察

`import/knowledge_graph_triples.csv` 的列为：

- `head`
- `relation`
- `tail`

CSV 前几行样本仍然呈现文献/材料三元组风格，例如：

- `10.1039/c4ra15767b,TYPE,Article`
- `10.1039/c4ra15767b,HAS_TITLE,...`
- `10.1039/c4ra15767b,REPORTS_ON,...`

这说明导入源里至少包含过一套“文献 / 材料 / 属性”三元组资产，因此不能只根据 `import/` 目录就把该实例判定为专利 property graph。

结论：

- `CSV 导入源` 与 `Neo4j 最终 property graph` 必须分开判断
- 真实结构必须以 live schema 为准

### 2. Live Query 取证方式

本次 live schema 观察使用的是正在运行的专利实例：

- Bolt: `127.0.0.1:8687`
- 用户: `neo4j`

主要使用的只读查询类型包括：

- `CALL db.labels()`
- `CALL db.relationshipTypes()`
- `CALL db.schema.nodeTypeProperties()`
- `CALL db.schema.relTypeProperties()`
- `SHOW CONSTRAINTS`
- `SHOW INDEXES`
- `MATCH ... RETURN count(...)`
- 围绕 `Patent`、`ProcessStep`、`MaterialRole`、`ExperimentTable`、`TableRow`、`Measurement` 的结构采样查询

因此，下文中所有节点数、边数、覆盖率、字段覆盖和活跃 label 结论，均属于 live 查询观察，不是仅凭离线 CSV 推断。

### 3. Live Schema 验证结果

本次实际连接的是用户指定的专利 Neo4j 实例：

- Bolt: `127.0.0.1:8687`
- HTTP: `127.0.0.1:8474`

live schema 只读查询结果显示：

- 总节点数约 `1,492,699`
- 总边数约 `2,875,327`
- 当前有活跃节点的 label 只有 `21` 个

活跃 labels 为：

- `Patent`
- `IPC`
- `IPCPrefix`
- `InventivePoint`
- `TechnicalSolution`
- `TechnicalProblem`
- `ProtectionScope`
- `ClaimStepLabel`
- `ProcessStep`
- `StepTemplate`
- `MaterialRole`
- `Material`
- `ExperimentTable`
- `TableRow`
- `Measurement`
- `PerformanceFact`
- `ApplicationScenario`
- `Atmosphere`
- `Person`
- `Organization`
- `EmbodimentInsight`

`CALL db.labels()` 仍会返回很多 lower-case / fastQA 风格 label token，例如：

- `doi`
- `title`
- `raw_materials`
- `process`
- `testing`
- `Article`
- `Process`
- `Testing`
- `RawMaterial`
- `__Chunk__`
- `__Document__`

但本次 live count 查询中这些 label 都是 `0` 节点。代表性的零计数 label 已确认包括：

- `doi = 0`
- `title = 0`
- `raw_materials = 0`
- `process = 0`
- `testing = 0`
- `Article = 0`
- `Process = 0`
- `Testing = 0`
- `RawMaterial = 0`
- `__Chunk__ = 0`
- `__Document__ = 0`

因此可以确认：

- fastQA 文献图谱那套 `:doi / :process / :testing / :raw_materials` 查询模板不能直接复用到该实例
- 这些 label 更像历史 token / 旧 schema 残留，不是当前活跃专利子图

### 4. Patent 主节点模型

`Patent` 节点当前字段为：

- `patent_id`
- `title`
- `abstract`
- `application_date`
- `publication_date`
- `ipc_main`
- `patent_type`
- `legal_status`
- `source_file`
- `stub`
- `gid`
- `_labels`

关键确认结果：

1. 专利图谱主键不是 DOI，而是 `Patent.patent_id`
2. `patent_id` 是 mandatory 字段
3. `39517 / 39517` 个 `Patent` 节点都带有 `patent_id`
4. `count(distinct patent_id) = 39517`，当前看是唯一的
5. schema 中不存在 `publication_number`
6. schema 中不存在 `application_number`

这意味着未来专利图谱查询的主入口应是：

- `MATCH (p:Patent {patent_id: $patent_id})`

而不是：

- `MATCH (d:doi {name: $doi})`

`Patent` 样例值也符合标准专利号格式，例如：

- `CN100355122C`
- `US6720110B2`
- `WO2004045007A2`
- `JP2001307726A`

### 5. Patent 节点覆盖分层

`Patent` 节点一共 `39517` 个，但它们并不是同一层完整度。

当前图谱里存在两类 Patent：

1. `stub = NULL` 的“完整专利”节点
2. `stub = TRUE` 的“引用占位 / 部分展开”节点

统计结果：

- `stub = NULL`：`10692`
- `stub = TRUE`：`28825`

字段覆盖：

- `source_file` 非空：`13530`
- `title` 非空：`13529`
- `abstract` 非空：`13529`
- `application_date` 非空：`13530`
- `publication_date` 非空：`13530`

分层细看：

- `stub = NULL` 且有 `title`：`10691`
- `stub = NULL` 且无 `title`：`1`
- `stub = TRUE` 且有 `title`：`2838`
- `stub = TRUE` 且无 `title`：`25987`

这说明：

1. 图里确实存在一层“完整可回答”的专利节点
2. 也存在大量仅用于引用网络承接的占位专利节点
3. `stub = TRUE` 不等于“完全没元数据”，其中约 `2838` 个仍有标题/日期/来源文件
4. 未来图谱问答不能把所有 `Patent` 节点都当作同等质量证据源

### 6. Patent 主干关系

当前所有活跃 relationship types 为：

- `ADDRESSES`
- `CITES_PATENT`
- `CLAIM_INCLUDES_STEP`
- `CLASSIFIED_AS`
- `CO_OCCURS_WITH`
- `HAS_AGENCY`
- `HAS_APPLICANT`
- `HAS_APPLICATION_SCENARIO`
- `HAS_EMBODIMENT_INSIGHT`
- `HAS_EXPERIMENT_TABLE`
- `HAS_INVENTIVE_POINT`
- `HAS_INVENTOR`
- `HAS_MATERIAL_ROLE`
- `HAS_MEASUREMENT`
- `HAS_PERFORMANCE_FACT`
- `HAS_PROCESS_STEP`
- `HAS_ROW`
- `INSTANCE_OF`
- `IN_IPC_SUBCLASS`
- `NEXT_STEP`
- `OPTION_INCLUDES`
- `PROPOSES`
- `PROTECTION_INCLUDES`
- `USES_ATMOSPHERE`

其中 `Patent` 出边覆盖最核心的是：

- `CLASSIFIED_AS -> IPC`
- `IN_IPC_SUBCLASS -> IPCPrefix`
- `HAS_APPLICANT -> Organization`
- `HAS_AGENCY -> Organization`
- `HAS_INVENTOR -> Person`
- `CITES_PATENT -> Patent`
- `USES_ATMOSPHERE -> Atmosphere`
- `ADDRESSES -> TechnicalProblem`
- `PROPOSES -> TechnicalSolution`
- `HAS_APPLICATION_SCENARIO -> ApplicationScenario`
- `HAS_INVENTIVE_POINT -> InventivePoint`
- `HAS_PERFORMANCE_FACT -> PerformanceFact`
- `PROTECTION_INCLUDES -> ProtectionScope`
- `CLAIM_INCLUDES_STEP -> ClaimStepLabel`
- `HAS_PROCESS_STEP -> ProcessStep`
- `HAS_MATERIAL_ROLE -> MaterialRole`
- `HAS_EXPERIMENT_TABLE -> ExperimentTable`

按“有多少专利带该结构”统计：

- `CLASSIFIED_AS`：`13530` patents
- `IN_IPC_SUBCLASS`：`13530` patents
- `HAS_APPLICANT`：`13530` patents
- `USES_ATMOSPHERE`：`13530` patents
- `HAS_INVENTOR`：`13529` patents
- `HAS_AGENCY`：`12345` patents
- `HAS_INVENTIVE_POINT`：`12489` patents
- `ADDRESSES`：`12488` patents
- `PROPOSES`：`12489` patents
- `HAS_APPLICATION_SCENARIO`：`12487` patents
- `PROTECTION_INCLUDES`：`12482` patents
- `HAS_PERFORMANCE_FACT`：`12435` patents
- `HAS_PROCESS_STEP`：`12282` patents
- `HAS_MATERIAL_ROLE`：`12064` patents
- `CLAIM_INCLUDES_STEP`：`12062` patents
- `CITES_PATENT`：`9218` patents
- `HAS_EXPERIMENT_TABLE`：`7230` patents
- `HAS_EMBODIMENT_INSIGHT`：`1125` patents

这说明结构化覆盖不是全量，但已经形成一个很强的专利子集。

### 7. 更深一层的可遍历子图

#### 7.1 工艺步骤链

存在稳定的工艺链：

- `Patent -> HAS_PROCESS_STEP -> ProcessStep`
- `ProcessStep -> INSTANCE_OF -> StepTemplate`
- `ProcessStep -> NEXT_STEP -> ProcessStep`

其中：

- `ProcessStep` 共 `64091`
- `StepTemplate` 共 `59538`
- `NEXT_STEP` 共 `51809`

`StepTemplate` 不是噪音节点，而是在做步骤模板抽象。样例：

- `前驱体制备`
- `固液分离`
- `干燥`
- `配料混合`
- `煅烧`

`ProcessStep` 节点本身带专利内具体参数：

- `name`
- `order`
- `operation`
- `params_json`

因此，步骤链同时具备：

1. 专利内顺序结构
2. 跨专利步骤模板归一化

#### 7.2 原料角色链

存在稳定的原料链：

- `Patent -> HAS_MATERIAL_ROLE -> MaterialRole`
- `MaterialRole -> OPTION_INCLUDES -> Material`

其中：

- `MaterialRole` 共 `60670`
- `Material` 共 `37705`
- `OPTION_INCLUDES` 共 `205366`

`MaterialRole` 节点保存：

- `role`
- `type`
- `ratio`
- `note`

`Material` 节点保存：

- `name`
- `material_type`
- `canonical_key`

这层结构说明图谱不是只记录“某专利用了什么材料”，而是记录：

- 材料在专利里的角色
- 某个角色可接受的候选材料
- 部分比例和备注条件

#### 7.3 实验表格链

存在稳定的实验数据链：

- `Patent -> HAS_EXPERIMENT_TABLE -> ExperimentTable`
- `ExperimentTable -> HAS_ROW -> TableRow`
- `TableRow -> HAS_MEASUREMENT -> Measurement`

其中：

- 带实验表格的专利约 `7230`
- `ExperimentTable` 共 `15365`
- `TableRow` 共 `128310`
- `Measurement` 共 `698092`

这是当前图里最强的结构化数值证据链，适合做：

- 表格列举
- 对比实验
- 样本/实施例/对比例维度的性能提取

#### 7.4 技术语义叶子链

存在一批从 `Patent` 直接挂出的语义叶子节点：

- `TechnicalProblem`
- `TechnicalSolution`
- `ApplicationScenario`
- `InventivePoint`
- `PerformanceFact`
- `ProtectionScope`
- `ClaimStepLabel`
- `Person`
- `Organization`
- `IPC`
- `IPCPrefix`

这批节点的特点是：

1. 从 `Patent` 指过去的入边明确存在
2. 节点自身通常只有文本/分类字段
3. 本次 outgoing 检查中这些节点都没有继续向外展开

因此它们应被视为：

- 终端事实槽位
- 枚举/列举型回答素材

而不是继续深层遍历的结构主干。

### 8. 关系属性与语义补强

大多数关系没有属性，但以下几类关系带属性：

- `CO_OCCURS_WITH.weight`
- `HAS_INVENTIVE_POINT.category`
- `HAS_MATERIAL_ROLE.role`
- `HAS_PERFORMANCE_FACT.category`
- `PROTECTION_INCLUDES.kind`

这说明图谱语义不是完全靠节点表达，部分关系也携带类型约束。

尤其是：

- `Material -[:CO_OCCURS_WITH {weight}]-> StepTemplate`

共 `1,014,076` 条，表示跨专利统计层的“材料 - 步骤模板”共现强度。这不是某一篇专利内的局部事实，而更像全库模式统计层。

### 9. 分类分布与领域范围

从 Top IPC、Top applicant 和标题样本看，该图谱虽然是专利图谱，但领域非常集中。

Top IPC 样本：

- `H01M10/0525`：`7593`
- `H01M4/58`：`5448`
- `H01M4/62`：`4570`
- `H01M4/36`：`3161`
- `C01B25/45`：`2785`

Top applicant 样本：

- `宁德时代新能源科技股份有限公司`：`405`
- `合肥国轩高科动力能源有限公司`：`238`
- `广东邦普循环科技有限公司`：`236`
- `湖南邦普循环科技有限公司`：`220`
- `比亚迪股份有限公司`：`158`

标题样本：

- `一种提高磷酸铁锂大电流放电性能的方法`
- `磷酸基锂离子电池的制备方法及其制备的电池`
- `锂离子电池正极材料及其制备方法`

结合标题样本、IPC 分布、申请人分布和性能事实样本，可以较可信地推断：

- 该图谱是“专利导向”
- 但主题高度集中在锂电池 / 磷酸铁锂 / 正极材料工艺与性能
- 它不是通用全领域专利图谱

该判断仍然属于基于 live 分布样本的领域推断，不应被理解为完整的官方数据边界声明。

### 10. Mixed-Schema Assessment

当前更准确的判断是：

1. `import/knowledge_graph_triples.csv` 保留了文献材料三元组来源痕迹
2. 但 live Neo4j 中实际活跃的 property graph 已经是专利导向 schema
3. fastQA 文献图谱依赖的 `doi / process / testing / raw_materials` 活跃节点在该实例中为 `0`
4. 因此，这不是“把文献图谱稍改一下就能给 patent 复用”的情况
5. 正确做法应该是：把它视为一套专利中心、遍历驱动、部分覆盖的电池材料领域专利图谱

---

## Current Assessment

截至当前分析时点，更可信的判断是：

1. `patent` 现有普通问答仍然没有图谱层
2. 用户提供的 `8687` 专利实例，live schema 已确认是专利导向图谱，不是 DOI 图谱
3. 图谱主入口是 `Patent.patent_id`，不是 DOI
4. 图谱回答模式应以 `Patent` 为中心做 traversal，而不是 flat property lookup
5. 图谱覆盖是“强子集覆盖”，不是所有专利都具备完整步骤、材料、表格、性能链
6. 图中存在大量 `stub` 专利节点，因此图谱结果需要区分“完整专利证据”与“引用占位节点”
7. 基于 Top IPC、Top applicant 与标题样本推断，该图谱更像“锂电/磷酸铁锂专利图谱”，不是全领域专利图谱
8. 因此，这套资产可以作为 `patent` 图谱能力底座候选，但不能直接套 fastQA 文献图谱模板

---

## Recommended Direction

如果未来要给 `patent` 增加图谱能力，建议遵循以下方向：

### 1. 复用 fastQA 的“前置薄层 + 失败回退”模式，但不要复用其 DOI Cypher 模板

优点：

- 与 `patent` 现有 staged QA 兼容
- 可以把图谱用于 patent candidate resolve、结构化列举、关系查询、步骤/材料/表格直接抽取
- 失败时容易静默回退到现有 `stage1-4`

缺点：

- 需要专门为该 schema 写新的 query planner
- 需要处理 `stub` 节点和图谱部分覆盖

结论：

- 推荐

### 2. 后续专利图谱查询必须围绕 `Patent` 及其 traversal 编写

最核心的 traversal 入口应是：

- `Patent -> CLASSIFIED_AS / IN_IPC_SUBCLASS`
- `Patent -> HAS_APPLICANT / HAS_AGENCY / HAS_INVENTOR`
- `Patent -> ADDRESSES / PROPOSES / HAS_APPLICATION_SCENARIO`
- `Patent -> HAS_INVENTIVE_POINT / HAS_PERFORMANCE_FACT`
- `Patent -> PROTECTION_INCLUDES / CLAIM_INCLUDES_STEP`
- `Patent -> HAS_PROCESS_STEP -> INSTANCE_OF / NEXT_STEP`
- `Patent -> HAS_MATERIAL_ROLE -> OPTION_INCLUDES -> Material`
- `Patent -> HAS_EXPERIMENT_TABLE -> HAS_ROW -> HAS_MEASUREMENT`
- `Patent -> CITES_PATENT -> Patent`

### 3. 图谱层适合作为 `kb_qa` 前置路由，不适合深插到 stage3/stage4

建议保持此前结论不变：

- 首选挂接点：[`patent/server/patent/kb_service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/kb_service.py#L38)
- 次选挂接点：[`patent/server/patent/executor.py`](/home/cqy/worktrees/highThinking/patent/server/patent/executor.py#L164)

不建议：

- 把 graph traversal 深插到 [`patent/server/patent/orchestrators/generation.py`](/home/cqy/worktrees/highThinking/patent/server/patent/orchestrators/generation.py#L399) 阶段内部

### 4. 图谱问答必须内建回退条件

以下情况应优先回退到现有 staged QA：

- 图中没有命中 `Patent.patent_id`
- 命中专利节点但只是薄 `stub`
- 用户问题要求 paragraph-level 原文证据，但当前图里没有 paragraph 节点
- 用户问题需要全量文本推理，而不是结构关系抽取
- 问题超出当前基于样本推断的电池材料领域专利图谱覆盖范围

### 5. 数据层面的一个现实约束

虽然 `patent_id` 当前看是唯一主键，但数据库里尚未看到 `Patent.patent_id` 的显式 index / uniqueness constraint。

当前 `Patent` 上已有的是：

- `gid` uniqueness / range index

而不是：

- `patent_id` uniqueness / range index

这意味着未来如果要高频按 `patent_id` 查询，数据层最好补上对应索引。

---

## Immediate Next Step

schema 核查已经完成。下一步应该进入“query spec”阶段，而不是继续猜 schema：

1. 定义哪些问题属于 graph-first：
   - 专利号直查
   - IPC / 申请人 / 发明人 / 机构关系查询
   - 技术问题 / 技术方案 / 应用场景列举
   - 工艺步骤 / 原料角色 / 实验表格 / 性能事实抽取
   - 引证网络查询
2. 定义哪些问题属于 staged QA fallback：
   - 需要专利原文长段推理
   - 需要 paragraph / claim 原文锚点
   - 图谱未覆盖的主题或薄 `stub` 节点
3. 基于 `patent_id` 和 traversal，写一版专利图谱查询设计，而不是复用 fastQA 的 DOI 模板

---

## Status

当前文档状态：

- 已完成：`patent` 现状梳理
- 已完成：本地图谱目录与只读资产分析
- 已完成：CSV 级别结构统计
- 已完成：真实 Neo4j schema 在线核查
- 已完成：`Patent.patent_id` 主键确认
- 已完成：活跃专利子图、叶子槽位、stub 分层分析
- 已完成：判断该图谱与 `patent` 的可复用边界
- 下一步：如果需要，实现前先补一份“patent graph query spec”
