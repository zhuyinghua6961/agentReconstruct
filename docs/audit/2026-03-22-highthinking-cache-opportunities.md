# 2026-03-22 highThinking / highThinkingQA 缓存机会点审查

## 审查目标
- 旧版 highThinking 与新版 highThinkingQA 从提问到输出答案的中间过程，哪些地方可以用 Redis
- 当前已经存在什么缓存
- 哪些缓存只是进程内，哪些是跨 worker 可复用
- 哪些点最值得优先做

## 结论先行
- 旧版 highThinking 和当前 `highThinkingQA` **基本没有 Redis 级缓存层**。
- 当前已有缓存主要是：
  - 进程内 `lru_cache`
  - 本地文件缓存
  - 进程内 dict cache
- 这些缓存对单 worker 有用，但对 gunicorn 多 worker / 多实例基本没有共享价值。
- 如果要提升 highThinking 的多轮问答吞吐和重复问题响应速度，**最值得引入 Redis 的位置不是最终答案整段缓存，而是中间稳定产物缓存**：
  - 会话上下文快照
  - rewrite 结果
  - 子问题分解
  - 子问题预回答
  - 向量检索结果
  - checker/reviser 循环的中间结果（可选）

---

## 一、当前已经存在的缓存

### A. prompt template 缓存
核心证据：`highThinkingQA/agent_core/llm_client.py`

已有：
- `_load_prompt_template_cached(...)` 使用 `lru_cache(maxsize=32)`

性质：
- 进程内缓存
- 只减少重复读 prompt 文件
- 不共享到其他 worker

价值判断：
- 有价值，但收益很小

### B. Chroma client / collection 缓存
核心证据：`highThinkingQA/ingest/vector_store.py`

已有：
- `_get_chroma_client_cached(...)`
- `_get_default_collection_cached(...)`

性质：
- 进程内缓存
- 减少重复创建 Chroma client / collection handle
- 不缓存检索结果本身

价值判断：
- 属于连接/对象复用，不是业务结果缓存

### C. parsed markdown 文件缓存
核心证据：`highThinkingQA/ingest/pipeline.py`

已有：
- `get_parsed_cache_path(...)`
- `skip_parsed` 时直接复用已解析 markdown

性质：
- 本地文件缓存
- 适合 ingestion/OCR 场景
- 不解决在线问答的跨实例共享

价值判断：
- 对文献入库有用，属于离线缓存

### D. translation cache
核心证据：`highThinkingQA/server/services/documents_service.py`

已有：
- `self._translation_cache: dict[str, str]`

性质：
- 进程内 dict cache
- 单进程有效

---

## 二、当前没有 Redis 的关键阶段

对 `highThinkingQA` 全目录检索 `redis` / `REDIS` 无结果。

直接结论：
- 当前 highThinkingQA 没有 Redis bootstrap
- 没有 Redis client
- 没有 RedisService
- 没有阶段缓存 key / TTL / invalidation 体系

这意味着：
- 相同问题跨 worker 不复用
- 相同 conversation context 跨实例不复用
- 相同检索结果重复调用 embedding + Chroma query
- checker/reviser 循环每次都从头跑

---

## 三、按问答链路拆缓存机会

## 1. 会话上下文快照缓存
入口：
- `server/services/conversation_context_service.py`
- `highThinkingQA/server/services/conversation_context_service.py`

当前行为：
- 每次 ask 都会调用 `conversation_service.get_conversation_context_snapshot(...)`
- 读取最近消息、摘要、拼接 multi-turn context

适合缓存的原因：
- 同一 conversation 连续问答时，server snapshot 变化频率并没有高到每一步都必须重读 DB/JSON
- 可以用 `(user_id, conversation_id, detail_version)` 做 key

建议缓存内容：
- 归一化 recent_turns
- summary
- context budget 后的最终 context payload

优先级：`high`

## 2. rewrite 结果缓存
入口：
- `rewrite_question(...)`
- `AskService._prepare_execution(...)`

当前行为：
- 每次问答都会对当前问题 + recent_turns + summary 做 rewrite

适合缓存的原因：
- 输入稳定时，rewrite 输出高度可复用
- 成本低于完整回答，但仍然是一轮 LLM 调用

建议 key：
- `hash(raw_question + recent_turns + summary)`

优先级：`high`

## 3. 子问题分解缓存
入口：
- `agent_core.graph.run_agent(...)` 内部 stage1/step1
- 旧版/新版都存在“直接回答 + 查询分解 / 子问题组织”阶段

适合缓存的原因：
- 对相同问题、多次重试、前端断线重连都很有价值
- 输出结构稳定：子问题列表 + 可能的直接回答草稿

建议缓存内容：
- sub_questions
- decomposition metadata
- direct answer draft（如果该阶段已产出）

优先级：`high`

## 4. 子问题预回答缓存
入口：
- `step2` 子问题预回答流水线

适合缓存的原因：
- 子问题通常比原问题更标准化
- 预回答是高成本 LLM 调用
- 对重复检索、checker 修订后的重跑很有帮助

建议 key：
- `hash(sub_question + runtime profile + corpus version)`

优先级：`high`

## 5. 向量检索结果缓存
入口：
- `retriever/vector_retriever.py`
- `highThinkingQA/retriever/vector_retriever.py`

适合缓存的原因：
- embedding + Chroma query 是稳定纯函数型操作
- 同一 query 在知识库版本不变时结果高度可复用

建议缓存内容：
- sub_question embedding（可选）
- top_k retrieved chunks
- distance / metadata

建议 key：
- `hash(query + top_k + collection_name + corpus_version)`

优先级：`high`

## 6. checker 结果缓存
入口：
- `highThinkingQA/agent_core/checker.py`

适合缓存的原因：
- checker 输入是：
  - 当前答案草稿
  - retrieved_chunks
- 当 revise 回合重复运行时，部分中间判断可复用

局限：
- 答案内容一变，命中率就下降
- key 会比较大

优先级：`medium`

## 7. reviser 结果缓存
入口：
- `agent_core/reviser.py`

适合缓存的原因：
- 某些重复失败问题会出现“同一批 issues + 同一答案”反复修订

局限：
- 命中率比 rewrite / retrieval 低
- 更像容错优化，而不是主收益点

优先级：`medium`

## 8. 最终答案整段缓存
入口：
- `execute_ask(...)` / `stream_ask_events(...)`

适合缓存的原因：
- 对完全重复问题可以省去整条链

局限：
- 多轮上下文影响大
- 易受 prompt / profile / corpus 更新影响
- 缓存失效策略复杂

建议：
- 只作为最外层可选优化，不应该优先于中间阶段缓存

优先级：`low-medium`

---

## 四、为什么应该优先缓存中间阶段，而不是最终答案
- 最终答案强依赖上下文、rewrite、随机性、checker/reviser 回合，稳定命中率不高
- 中间产物更结构化，更容易做版本键
- 中间缓存可以为：
  - 流式重连
  - checker/revise 重跑
  - 相似问题重复调用
  - 并发重复请求
  提供更稳定收益

这点和 `fastQA` 当前做法是一致的：
- `fastQA` 优先缓存 Stage1/Stage2，而不是只缓存 final answer

---

## 五、旧版 highThinking 与新版 highThinkingQA 的缓存差异

### 共同点
- 都没有 Redis 业务缓存
- 都依赖本地/进程内轻缓存
- 都没有 `stage1_cache` / `stage2_cache` 这种结构化 Redis 组件

### 新版 highThinkingQA 比旧版新增的仍然不是 Redis
新增更多是：
- 更丰富的 FastAPI 包装层
- 更丰富的 done metadata
- 更完整的 SSE 总结字段

但这些都没有改变缓存层现状。

---

## 六、public-service 的 Redis 能不能直接帮 highThinkingQA
短答案：**不能直接替代。**

原因：
- `public-service` 的 Redis 主要服务于：
  - 会话缓存
  - 会话 JSON 锁
  - quota
  - upload worker
- `highThinkingQA` 当前问答主链并不通过 `public-service` 去执行业务中间阶段
- 因而 `public-service` 的 Redis 不会天然缓存：
  - rewrite
  - decomposition
  - retrieval
  - checker/reviser

如果要让 highThinkingQA 享受 Redis 结果缓存，有两条路：
- 在 `highThinkingQA` 内自己接 Redis
- 或把这部分阶段能力上收到统一中间层/公共服务

---

## 七、推荐的落地优先级

### 第一优先级
- 会话上下文快照缓存
- rewrite 结果缓存
- 子问题分解缓存
- 子问题预回答缓存
- 向量检索结果缓存

### 第二优先级
- checker 结果缓存
- reviser 结果缓存

### 第三优先级
- 最终答案整段缓存

---

## 八、对用户问题的直接回答

### 问题：highThinking 的从提问到输出答案的中间所有过程，能不能有可以使用 Redis 缓存的地方？
- **能，而且很多。**
- 最值得做的不是最终答案缓存，而是：
  - context snapshot
  - rewrite
  - 子问题分解
  - 子问题预回答
  - 检索结果

### 问题：当前有在用 Redis 吗？
- **highThinking / highThinkingQA 主问答链没有。**
- 目前只有进程内/文件级缓存，没有 Redis 分布式缓存层。
