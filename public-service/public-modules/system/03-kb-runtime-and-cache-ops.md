# system 知识库运行时与缓存运维接口

对应代码：
- `backend/app/modules/system/service.py`
- `backend/app/modules/retrieval/service.py`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/api/chat.js`

## 1. `kb_info` 已经不是纯 system 信息，而是 QA 子系统运维视图

`build_kb_info()` 会查两类东西：

- Chroma / vector DB 文档数
- Neo4j 节点数

如果 `runtime.agent` 不存在：

- 返回 `success = false`
- `message = Agent系统未初始化`
- 但仍带 `chromadb_size`
- HTTP 仍是 `200`

所以这不是简单的“平台系统状态”，而是问答知识库子系统状态。

## 2. Chroma 数量优先走 runtime vector client，失败再退 retrieval service

`_chromadb_count()` 的顺序：

1. 取 agent 里的 semantic collection
2. 如果 `runtime.vector_db_client` 有值，优先直接 count
3. 如果失败，再 build retrieval runtime config
4. 最后用 `retrieval_service.get_vector_count(...)`

这说明 system service 对 retrieval 的耦合比较深，不只是读一个 runtime flag。

## 3. Neo4j 数量依赖 runtime.agent.graph.query()

只要 `runtime.agent` 存在，就会尝试：

- `MATCH (n) RETURN count(n) as count`

失败时：

- `neo4j_connected = False`
- `node_count = 0`

但整个接口仍然返回 `200`，且 payload 可以是 `success = true`。

所以这里的 `success` 更接近：

- “接口调用成功拿到一份状态”

而不是：

- “所有底层依赖都正常”

## 4. `refresh_kb` 是直接触发 runtime.init_agent()

逻辑非常短：

- agent 不存在 -> 失败
- `runtime.init_agent` 不存在 -> 失败
- `runtime.init_agent()` 返回 truthy -> 成功
- 否则失败

这说明当前所谓“刷新知识库”本质上不是细粒度刷新，而是：

- 重新初始化整个 agent/runtime

这是典型的 QA 子系统运维动作，不是平台系统接口。

## 5. `clear_cache` 只清 answer_cache

`clear_cache()` 的动作只有：

- `runtime.answer_cache.clear()`

不会清：

- Redis conversation cache
- QA stage1/stage2 cache
- pdf text cache
- retrieval/vector cache

所以这个接口名字虽然叫 `clear_cache`，但真实范围非常窄。

更准确地说，它清的是：

- 进程内 answer_cache

## 6. 这些接口的失败几乎都“业务失败但 HTTP 200”

这一点在 system 里特别重要。

例如：

- agent 未初始化
- refresh 失败
- clear_cache 异常
- kb_info 异常

都主要靠 payload 里的：

- `success`
- `message`

来表达，而不是靠 HTTP 状态码。

所以调用方不能只看 `response.ok`。

## 7. 前端已有两套轻量调用面

`frontend-vue/src/services/api.js`：

- `getKbInfo()`

`frontend-vue/src/api/chat.js`：

- `getKbInfo()`
- `refreshKb()`
- `clearCache()`

这说明 QA 页面/聊天页已经把这些接口当成可直接调用的系统操作，而不是仅供管理员使用的后台命令。

## 8. 这部分为什么不适合直接归入“平台 system”

因为它依赖的核心对象是：

- `runtime.agent`
- `runtime.vector_db_client`
- retrieval runtime
- Neo4j / Chroma

这些都属于 QA/RAG 子系统，不是通用平台底座。

所以如果以后要把公共能力拆服务，这一块更像：

- QA runtime admin endpoints

而不是：

- 通用 system endpoints

## 9. `clear_cache` 名称和实际行为之间存在认知风险

从接口名看，调用方很容易以为它会清很多缓存。

但当前真实行为只影响：

- `runtime.answer_cache`

这在文档里必须写清楚，否则会误判该接口的运维价值。
