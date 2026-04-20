# fastQA 系统文档

本文档集用于整理 `fastQA` 当前实现、`gateway -> fastQA` 调度链路、RAG/LLM 调用链路、`qa_pdf` / `qa_tabular` 文件问答链路，以及 `graph_kb` 与实际 Neo4j schema 的差距。

范围说明：

- 基于当前仓库代码静态分析。
- 结合此前已完成的一次 Neo4j 实图探查结果。
- 仅做分析与文档化，不包含任何业务代码修改建议落地。
- 出于安全原因，文档不记录 Neo4j 明文密码；仅描述配置来源与 schema 结论。

推荐阅读顺序：

1. `01-system-overview.md`
2. `02-gateway-task-relay.md`
3. `03-rag-planner-retriever-llm.md`
4. `04-graph-kb-and-neo4j-gap.md`
5. `05-pdf-and-tabular-pipelines.md`
6. `06-issues-and-gaps.md`

文档索引：

| 文件 | 主题 | 重点内容 |
| --- | --- | --- |
| `01-system-overview.md` | 系统总览 | 服务边界、模块职责、总调用图、端到端数据流、外部依赖 |
| `02-gateway-task-relay.md` | Gateway 调度与中继 | `create_task`、admission、relay store、SSE 回放、持久化同步 |
| `03-rag-planner-retriever-llm.md` | RAG / Planner / Retriever / LLM | `kb_qa` 的 generation-driven RAG 主链路、上下文装配、提示词与答案合成 |
| `04-graph-kb-and-neo4j-gap.md` | 图谱模块与实际 schema 差距 | `graph_kb` 模板能力、真实标签/关系形态、当前覆盖不到的查询场景 |
| `05-pdf-and-tabular-pipelines.md` | `qa_pdf` / `qa_tabular` | prompt、截断、执行器、混合问答、证据装配、答案合成 |
| `06-issues-and-gaps.md` | 问题与差距总表 | 架构性问题、实现限制、配置与运行态偏差、优先级视角的总结 |

