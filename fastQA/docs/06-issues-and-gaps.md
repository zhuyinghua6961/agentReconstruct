# 发现的问题与差距总表

## 1. 总结视角

从当前代码与实探结果看，系统并不是“功能缺失”，而是进入了一个典型的中后期阶段：

- 路径很多
- 兼容层很多
- 外部依赖很多
- 每条链路都能跑
- 但不同链路的结构化程度差异很大

最明显的风险集中在四类问题：

1. 图谱 schema 与应用假设不匹配
2. prompt 约束强于结构约束
3. gateway 任务链路复杂，运行态收敛依赖补偿逻辑
4. 系统对外部服务与本地资源的耦合度高

## 2. 差距矩阵

| 主题 | 现状 | 影响 | 主要文件 |
| --- | --- | --- | --- |
| graph_kb 能力边界 | 仅支持 5 个硬编码模板 | 无法承担通用图谱问答 | `fastQA/app/modules/graph_kb/` |
| Neo4j schema 形态 | 字段桶 + 字符串编码，不是干净实体图 | 数值过滤、排序、多条件组合极难做稳 | live probe + `graph_kb/client.py` |
| graph_kb 配置面 | shared env 只有开关，没有连接参数 | 配置面与运行面分离，定位问题时不直观 | `resource/config/services/fastQA/config.shared.env`、`app/core/runtime.py` |
| generation RAG stage1 | JSON 解析失败即退化为“只有 deep_answer” | 检索深度下降但不易从接口表面看出 | `stage1_planning.py` |
| generation stage4 | 大量质量要求依赖 prompt | 结构化可信度受模型服从度影响 | `synthesis_streaming.py` |
| qa_pdf grounding | 严格基于 PDF 的规则主要靠 prompt 自律 | 模型若越界，系统缺少强约束拦截 | `qa_pdf/prompting.py`、`engine.py` |
| qa_pdf 多文档 | 多文档主要靠拼接与均衡截断 | 文献间证据权重控制较弱 | `qa_pdf/service.py`、`truncation.py` |
| qa_tabular planner | 规则化匹配 sheet/column/filter | 可解释但容易被模糊提问击穿 | `qa_tabular/planner.py` |
| qa_tabular hybrid evidence | PDF 证据靠轻量 lexical scoring | 混合证据的召回与排序精度有限 | `qa_tabular/service.py` |
| gateway 调度链 | create/admission/relay/persist/quota 强耦合 | 运行态复杂，排障难度高 | `gateway/app/services/qa_tasks.py` |
| relay / persistence 收敛 | 已设计 pending 补偿 | 说明外部依赖抖动是现实问题 | `qa_tasks.py` |
| 前端接入 | task 模式与 legacy `ask_stream` 双路径并存 | 维护与心智负担增加 | `frontend-vue/src/services/api.js`、`Home.vue` |

## 3. 按优先级理解这些问题

### 3.1 高优先级结构性差距

#### A. graph_kb 与真实图谱 schema 的偏差

这是当前最结构性的差距，因为它不是 prompt 或小 bug 能解决的，而是：

- schema 不适配应用查询方式
- 查询模板也只覆盖很窄的意图

结果就是 `graph_kb` 很难演进成可靠的通用检索层。

#### B. gateway 任务链路的运行态复杂度

它已经具备：

- queue
- admission
- lease
- relay
- progress sync
- terminal sync
- quota finalize
- reconciliation

这类链路的主要风险不在“写不出来”，而在“线上出问题时很难证明哪个环节先失效”。

### 3.2 中优先级能力性差距

#### C. PDF / hybrid 的证据约束仍偏软

`qa_pdf` 和 `hybrid_qa` 都非常强调“不要补知识”“表格优先”“KB 只做验证”，但本质上仍然是通过 prompt 训导模型，而不是结构化硬约束生成。

#### D. 表格规划器的自然语言弹性有限

`qa_tabular` 的优势是：

- 结果真实
- 路径可解释
- 不依赖模型猜数

但劣势是：

- 模糊问法容易澄清失败
- 复杂复合问句支持有限
- 列名别名能力仍以规则为主

### 3.3 中低优先级运维性差距

#### E. 配置与运行面分离

shared env 只看得到 graph_kb 开关，看不到 Neo4j 连接参数；同样，很多真正决定运行态的关键项分布在本地环境与多个服务中。问题定位需要同时横跨：

- shared config
- local env
- gateway
- fastQA
- public-service

#### F. 前端双 transport 共存

前端已经明显朝 task/replay 模式迁移，但 legacy `ask_stream` 仍在。只要双路径都保留：

- 测试面翻倍
- 状态语义容易漂移
- 新人理解成本更高

## 4. 哪些差距已经在代码里暴露出“补丁味”

以下现象说明系统已经在用补丁适应复杂现实：

1. `graph_kb/service.py` 里大量字符串清洗与字段拼接解析
2. `qa_pdf/engine.py` 的多级 fallback 与 generic phrase warning
3. `qa_tasks.py` 里的 `progress_sync_pending` / `terminal_sync_pending`
4. `qa_tabular/service.py` 自己做 lightweight PDF evidence scoring，而不是统一复用 generation 检索框架
5. `request_adapter.py` 中 route / source_scope / turn_mode 的大量契约校验

这些补丁本身不是错误，但说明系统已经有明显的“多链路粘合层”特征。

## 5. 对理解整个系统最关键的结论

1. `fastQA` 不是单一 RAG 服务，而是四条问答链路的编排入口。
2. `gateway` 不是简单网关，而是任务调度器、SSE 中继器、持久化协作者。
3. `graph_kb` 当前更像一个“少数意图的加速捷径”，不是主检索底座。
4. `qa_pdf` 与 `qa_tabular` 的设计哲学不同：前者强调“文本证据约束”，后者强调“真实执行结果优先”。
5. 整个系统最稳的一条链路其实不是图谱，而是“规则/执行器先算出结构化结果，再让 LLM 做语言表述”的表格链路。

