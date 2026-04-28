# 知识图谱（Neo4j）问答专题

本文**仅**描述以 **Neo4j 知识图谱**为核心的问答链路：路由、何时命中图谱、查询与合成步骤。不涉及生成驱动 RAG、专利双库等（见其它文档）。

---

## 1. 代码入口与依赖

| 组件 | 文件 | 说明 |
|------|------|------|
| 统一入口 | [`main.py`](../main.py) — `MaterialScienceAgent.smart_query` | 含 DOI 直读分支与 Commander 路由 |
| 路由决策 | [`commander_agent.py`](../commander_agent.py) — `CommanderAgent.analyze_question` | 返回 `precise` / `hybrid` / `community` / 默认语义等 |
| 图谱执行 | [`main.py`](../main.py) — `query`、`_generate_cypher_query`、`_execute_cypher_query`、`_synthesize_answer` | NL → Cypher → Neo4j → 自然语言答案 |
| 图连接 | `langchain_community.graphs.Neo4jGraph` | 通过环境变量连接 Bolt |

**环境变量（示例）**：`NEO4J_URL`、`NEO4J_USERNAME`、`NEO4J_PASSWORD`。

---

## 2. `smart_query` 处理顺序

实现见 `MaterialScienceAgent.smart_query`（约 1964 行起）。

1. **问题中出现标准 DOI**（正则匹配 `10.\d+/...`）  
   → **`query_pdf_directly`**：读 `papers/` 下 PDF，LLM 基于全文作答。  
   → **不使用 Neo4j、不使用 Chroma 向量检索主路径**。

2. **无 DOI**  
   → **`commander.analyze_question(user_question)`** 得到 `decision`，再分支：

| `decision` | 后续调用 | 是否主要用 Neo4j |
|------------|----------|------------------|
| `precise` | `query` | **是**（图谱精确查询） |
| `hybrid` | `dual_hybrid_query`（`use_dual_retrieval=True` 且语义专家可用）或 `hybrid_query` | **是 + 向量**（混合） |
| `community` | `semantic_search(..., force_broad=True)` | **否**（社区路径已关闭，实为宽泛语义） |
| 其它（默认） | `semantic_search`，语义专家不可用时回退 `query_hybrid` | **默认否**（Chroma 为主） |

`precise` 分支返回里会将 **`query_mode`** 标为 **「知识图谱（精确查询）」**。

---

## 3. 什么样的问题更容易路由到图谱（`precise` / `hybrid`）？

逻辑在 **`CommanderAgent`**：维护 **Neo4j 数值属性**、**图结构属性**、**精确关键词**、**语义关键词**等列表（见 `commander_agent.py` 中 `neo4j_numeric_attributes`、`neo4j_graph_attributes`、`precise_keywords`、`semantic_keywords` 等）。

**概括（与实现优先级一致，见代码内顺序）**：

- **混合 `hybrid`**：`HybridQueryAgent.is_hybrid_question` 为真时 —— 问题同时带 **数值/比较/筛选** 与 **分析、趋势、对比、特点** 等需求。
- **精确 `precise`** 常见情形：  
  - 含 **大于、小于、最高、最低、统计** 等 **精确关键词**，且命中 **压实密度、比容量、粒径** 等 **数值类图谱属性**；  
  - 或 **图谱非数值属性** + **有哪些、哪些、含有** 等 **列举/过滤** 语气；  
  - 或 **仅数值属性** 导向的查询；  
  - 或出现 **LFP、LiFePO4、NCM** 等 **实体关键词**（具体以 `analyze_question` 内规则为准）。
- **语义 `semantic`**：含 **如何、为什么、影响、方法** 等 **语义类关键词** 时，往往 **优先于**「仅属性 + 列举」走语义分支。

**注意**：`community` 在 `smart_query` 中被映射为 **宽泛语义搜索**，**不是** Neo4j 社区子图专家路径。

---

## 4. 应用场景与示例：什么样的问题会「触发图谱」？

**场景**：需要从**结构化库**里做**过滤、排序、极值、统计**或**按材料/工艺条件列举**，而不是泛泛谈「机理、为什么、怎么做」——这类需求更适合走 **Neo4j + Cypher**，再可选叠加 PDF 片段合成自然语言答案。

**示例问题（示意，便于理解路由；实际以 `CommanderAgent` 判定为准）**：

> **「在数据库中，磷酸铁锂（LiFePO4）相关材料里，放电容量最高的前 5 条记录分别是哪些？请给出对应文献标识。」**

**为何容易命中图谱路径**：

- 含 **极值/排序** 类意图（「最高」「前 5 条」），与 **`precise_keywords`** 中的「最高、最大、top」等一致；
- 含 **数值类性能指标**「放电容量」，与 **`neo4j_numeric_attributes`** 中的 `discharge_capacity` /「比容量」等方向一致；
- 含 **材料实体**「磷酸铁锂 / LiFePO4」，有利于走 **`precise`** 而非纯语义泛泛检索。

**调用链（概念上）**：`smart_query` → `analyze_question` → **`decision = precise`** → **`query`** → 生成 Cypher → **Neo4j 返回表格式结果** → **`_synthesize_answer`** 生成用户可读答案。

若同一问题还强调「与 NCM 对比的趋势、机理差异」等**开放分析**，可能被 **`HybridQueryAgent`** 判为 **`hybrid`**，从而 **Neo4j 与 Chroma 联合**。

---

## 5. 知识图谱精确查询：`query` 流水线

对自然语言问题，典型步骤为：

1. **`_generate_cypher_query`**：LLM 根据 schema/提示生成 **Cypher**（相关模板见 `system_prompt.txt` 等）。
2. **`_validate_cypher_query`**：校验含 `MATCH`、禁止危险写操作等。
3. **`_execute_cypher_query`**：`self.graph.query(cypher)` 访问 **Neo4j**。
4. **`_synthesize_answer`**：将结构化结果与（可选）**`papers/{doi}.pdf`** 片段一并交给 LLM 生成最终回答。

输出中常含 **`cypher_query`、`raw_data`、`result_count`、`final_answer`** 等字段，便于调试与展示。

---

## 6. 混合与双召回

- **`hybrid_query`**：多阶段流程 —— 常用 **Neo4j/Cypher 做条件筛选**，再对子问题或结果做 **语义分析**（细节见 `main.py` 内实现与日志）。
- **`dual_hybrid_query`**：  
  - 一路对子问题执行 **`query`（Neo4j）**；  
  - 另一路 **`semantic_search`（Chroma）**；  
  - 由 **`DualRetrievalAgent`** 融合后再综合答案。

两者均 **同时使用 Neo4j 与向量库**（在专家初始化成功的前提下）。

---

## 7. 辅助能力（非主问答必经）

- **`Neo4jTwoStageOptimizer`**：`main.py` 初始化时挂载，用于部分场景下优化 Neo4j 查询。
- **`web_app.py`** 中 **`verify_doi_in_database` / 文献信息查询** 等可结合 Neo4j 校验 DOI，属 API 辅助逻辑。

---

## 8. 与 Web 前端默认行为的关系（仅作边界说明）

当前 Web 流式对话在常见路径上 **默认走生成驱动 RAG**，**不经过** 上述 `smart_query` → Commander → Neo4j 分支。  
若要在产品界面体验 **本专题所述图谱问答**，需通过 **`agent.smart_query(...)`**、或未来若开放的 **非生成驱动** 开关/API。

---

## 9. 延伸阅读

更偏「流程图 + 环境变量表」的并列说明见 [知识图谱问答流程.md](./知识图谱问答流程.md)（可与本文对照；**本文刻意只保留 KG 主题**）。

---

*实现以仓库代码为准；若 `CommanderAgent.analyze_question` 规则调整，请同步更新第 3、4 节。*
