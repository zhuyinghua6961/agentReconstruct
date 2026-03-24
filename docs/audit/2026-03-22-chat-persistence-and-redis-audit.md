# 2026-03-22 聊天持久化与 Redis 细粒度审查

## 审查目标
- 当前聊天记录持久化到底是谁在做：`fastQA` / `highThinkingQA` / `gateway` / `public-service`
- `Redis` 是否只是“接了配置”，还是已经真实生效
- `public-service` 是否在承担 Redis 缓存与锁的工作
- 当前架构下，哪些链路仍然绕过了 `public-service`

## 结论先行

### 1. 聊天记录持久化不是单一答案，要分链路看
- `gateway` 架构下，**权威持久化是 `public-service`**。
- `fastQA` 直连执行链里，**并不是 `public-service` 在直接落库**，而是 `fastQA` 通过本地 hook 调用了仓库根目录旧版 `server.services.conversation.conversation_service`。
- `highThinkingQA` 当前也是同样模式：**直接调用旧版 `conversation_service` 做持久化**，不是走 `public-service`。
- 所以如果问题是“当前系统整体上谁是聊天持久化权威”，答案是：
  - **走 gateway 前端主链时：`public-service` 是权威**。
  - **直打 `fastQA` / `highThinkingQA` 服务时：仍然依赖旧版 monolith 的 `conversation_service`**。

### 2. `public-service` 的 Redis 不是摆设，已经在真实承担缓存和分布式锁职责
- `public-service/config.shared.env` 默认 `REDIS_ENABLED=1`。
- `public-service/backend/app/core/runtime.py` 启动时会真实 bootstrap Redis，并把状态写进 runtime health。
- `public-service` 当前明确使用 Redis 做：
  - 会话列表缓存
  - 会话详情缓存
  - 最近访问分页记录
  - 会话 JSON 写入分布式锁
  - 上传处理 worker 分布式锁
  - quota 配置/覆盖缓存
  - quota 并发 lease / lock
  - 系统诊断中对 Redis key 的可视化检查

### 3. `fastQA` 有 Redis 缓存代码，而且接到了主问答链，但默认提交配置里是关闭的
- `fastQA` 启动时会 bootstrap Redis runtime。
- `fastQA` 的普通 `kb_qa` generation-driven 主链已经把 `redis_service` 传进 orchestrator。
- 已接好的 Redis 用途：
  - Stage1 cache
  - Stage2 cache
  - Stage1/Stage2 singleflight 分布式锁
  - PDF 文本缓存
- 但 `resource/config/services/fastQA/config.shared.env` 里默认是 `REDIS_ENABLED=0`。
- 也就是说：**代码能力存在，但按当前提交态默认配置，fastQA Redis 缓存默认不生效，除非 secret env / 运行时环境覆盖打开。**

### 4. `highThinkingQA` 基本没有 Redis 集成
- 没发现 `highThinkingQA` 的 Redis 配置项。
- 没发现 `highThinkingQA` 的 Redis client/bootstrap。
- 当前 `highThinkingQA` 的缓存主要是：
  - `lru_cache`（prompt template / chroma client / collection）
  - 本地文件缓存（parsed markdown cache）
  - 进程内字典缓存（translation cache）
- 这意味着：**highThinkingQA 当前没有跨进程、跨 worker、跨实例共享的 Redis 缓存层。**

---

## 一、聊天记录持久化职责归属

### A. gateway 主链：由 public-service 做权威持久化
核心证据：`gateway/app/services/conversation_persistence.py`

该文件的头部注释已经写明：
- `Persist QA conversation turns into the public-service authority.`

实际行为：
- `persist_user_message(...)` 通过 `_add_message(...)` 调用 `public-service` 会话接口写入用户消息。
- `persist_assistant_summary(...)` 在 `done_seen=True` 后，把 assistant 内容与 summary metadata 一并写给 `public-service`。
- `extract_stream(...)` / `_apply_sse_frame(...)` 会在流式过程中汇总：
  - `assistant_content`
  - `query_mode`
  - `references`
  - `reference_links`
  - `pdf_links`
  - `doi_locations`
  - `route`
  - `used_files`
  - `timings`
  - `trace_id`
  - `file_selection`
  - `steps`

结论：
- **在 gateway 模式下，聊天记录的最终写入目标是 `public-service`。**
- gateway 只是收集 SSE 结果并转写，不是最终权威存储。

### B. public-service：真正的 authority
核心证据：
- `public-service/backend/app/modules/conversation/service.py`
- `public-service/backend/app/modules/conversation/json_store.py`

`ConversationService.add_message(...)` 的行为：
- 先校验 `role/content`
- 在会话锁下加载或 bootstrap conversation document
- 追加 message payload 到 JSON document
- assistant message 会额外展开：
  - `query_mode`
  - `references`
  - `reference_links`
  - `pdf_links`
  - `doi_locations`
  - `steps`
  - `done_seen`
- 持久化 JSON 文档
- 更新 MySQL message_count
- 刷新主列表缓存与详情缓存

`ConversationJsonStore.write_document(...)` 的行为：
- 先写本地 JSON
- 再尝试把 conversation JSON 镜像上传到对象存储
- 返回 `storage_ref` / `content_hash` / `size_bytes` / `sync_status`

因此 `public-service` 的持久化不是单层：
- **MySQL：结构化权威索引 / 会话与消息主记录**
- **本地 JSON：可恢复的详细会话文档副本**
- **对象存储镜像：远端同步副本**

### C. fastQA：当前并不直接调用 public-service
核心证据：
- `fastQA/app/main.py`
- `fastQA/app/services/chat_persistence.py`

`fastQA/app/main.py`：
- 启动时把 `persist_user_message` / `persist_assistant_summary` 挂到 `app.state` hook。

`fastQA/app/services/chat_persistence.py`：
- `_get_conversation_service()` 直接 import：
  - `server.services.conversation.conversation_service`
- `persist_user_message(...)` / `persist_assistant_summary(...)` 最终都调用旧版 `conversation_service.add_message(...)`
- assistant 还会调用 `refresh_conversation_summary(...)`
- 可选择 async dispatcher，但**目标仍是旧版 conversation service**

结论：
- **fastQA 当前直连路径不是把聊天记录交给 public-service。**
- 它是“服务内 hook -> 旧版 monolith conversation_service”。
- 如果部署时没有那套旧版 `server/...` 运行依赖，这条路径会天然耦合。

### D. highThinkingQA：当前也不是 public-service 在持久化
核心证据：
- `highThinkingQA/server_fastapi/routers/ask.py`
- `highThinkingQA/server_fastapi/routers/conversation.py`

`highThinkingQA/server_fastapi/routers/ask.py`：
- import 的仍然是：
  - `from server.services.conversation.conversation_service import conversation_service`
- `_persist_user_message_if_needed(...)` 调用 `conversation_service.add_message(...)`
- `_persist_assistant_message_if_needed(...)` 调用 `conversation_service.add_message(...)` + `refresh_conversation_summary(...)`
- 只是比旧版收集了更多 metadata：
  - `reference_links`
  - `pdf_links`
  - `doi_locations`
  - `route`
  - `used_files`
  - `timings`
  - `trace_id`
  - `file_selection`

`highThinkingQA/server_fastapi/routers/conversation.py` 也直接挂旧版 conversation CRUD。

结论：
- **highThinkingQA 当前聊天持久化仍依赖旧版根目录 conversation_service。**
- 并没有迁到 `public-service` 作为统一 authority。

---

## 二、聊天上下文读取是谁在做

### highThinking / highThinkingQA 的问答上下文读取
核心证据：
- `server/services/conversation_context_service.py`
- `highThinkingQA/server/services/conversation_context_service.py`

两边逻辑一致：
- `build_conversation_context(...)` 最终都会调用：
  - `server.services.conversation.conversation_service.get_conversation_context_snapshot(...)`

结论：
- **highThinking 与 highThinkingQA 的多轮上下文读取，同样绑定旧版 conversation_service。**
- 这进一步证明 highThinkingQA 还没有从“旧版 conversation authority”中真正解耦。

---

## 三、Redis 是否生效、是否在使用中

## A. public-service：明确生效，明确在用

### 1. 配置层
核心证据：`public-service/config.shared.env`

默认配置：
- `REDIS_ENABLED=1`
- `REDIS_HOST=127.0.0.1`
- `REDIS_PORT=6379`
- `REDIS_DB=0`
- `REDIS_KEY_PREFIX=public_service`

这说明提交态不是“预留接口”，而是默认开启。

### 2. 启动装配层
核心证据：`public-service/backend/app/core/runtime.py`

`_bootstrap_redis(runtime)`：
- 调 `build_redis_bindings(settings=runtime.settings)`
- 构造 `RedisService.from_prefix(...)`
- 把结果写入 `runtime.redis_service`
- 在 `component_status['redis']` 中记录：
  - enabled
  - available
  - library_available
  - url
  - key_prefix

说明：
- 不仅尝试连接，还把健康状态接入 runtime 健康面板。

### 3. 会话缓存层
核心证据：
- `public-service/backend/app/modules/conversation/cache.py`
- `public-service/backend/app/modules/conversation/service.py`

缓存内容：
- conversation list cache
- conversation detail cache
- recent pages cache
- list/detail version key
- cache hit touch / freshness grace

调用位置：
- `list_conversations(...)`：读 Redis 命中则直接返回；未命中则回源 DB + 回填 Redis
- `get_conversation_detail(...)`：优先走 Redis detail cache，失效后回源并重建
- `add_message(...)` / 会话更新流程：刷新缓存、失效缓存

结论：
- **public-service 的 Redis 会话缓存是真用在主请求链上的。**

### 4. 会话 JSON 分布式锁
核心证据：`public-service/backend/app/modules/conversation/json_store.py`

行为：
- `ConversationJsonStore` 持有 `RedisLockManager`
- `conversation_lock(...)` 先尝试分布式锁，再叠加本地线程锁 + 文件锁
- 写文档前后会检查 lease 健康

结论：
- **Redis 在 public-service 里不仅做缓存，还做跨 worker 的写入互斥。**

### 5. 上传处理 worker 锁
核心证据：`public-service/backend/app/modules/conversation/upload_processing_worker.py`

行为：
- worker 初始化时持有 `RedisLockManager`
- 在文件处理任务中用于避免重复处理 / 并发碰撞
- 同时结合 MySQL named lock 作为兼容/降级手段

### 6. quota 缓存与租约
核心证据：
- `public-service/backend/app/modules/quota/service.py`
- `public-service/backend/app/modules/quota/cache.py`
- `public-service/backend/app/modules/quota/deps.py`

Redis 用途：
- quota config cache
- active/all config cache
- user override cache
- quota lease / distributed lock

结论：
- **public-service 明确承担了 Redis 缓存与锁的公共职责。**
- 回答用户第二问：**是的，public-service 正在做 Redis 缓存这部分工作，而且不止会话，还覆盖 quota 与 worker 协调。**

## B. fastQA：有完整 Redis 代码路径，但默认配置关闭

### 1. 启动与 runtime 装配
核心证据：
- `fastQA/app/main.py`
- `fastQA/app/core/runtime.py`
- `fastQA/app/integrations/redis/client.py`

行为：
- `create_app()` 启动时调用 `bootstrap_redis(app.state)`
- `bootstrap_redis(...)` 会创建：
  - `app.state.redis_bindings`
  - `app.state.redis_client`
  - `app.state.redis_service`
- 同时记录 runtime component_status

### 2. 问答主链的 Redis 使用点
核心证据：
- `fastQA/app/modules/qa_kb/service.py`
- `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- `fastQA/app/modules/qa_cache/stage1_cache.py`
- `fastQA/app/modules/qa_cache/stage2_cache.py`
- `fastQA/app/modules/qa_cache/singleflight.py`
- `fastQA/app/modules/qa_cache/pdf_cache.py`

已接好的缓存点：
- Stage1 结果缓存
- Stage2 结果缓存
- Stage1 singleflight lock
- Stage2 singleflight lock
- PDF 文本缓存

也就是说：
- **fastQA 的普通 kb_qa generation-driven 主链，Redis 不是“规划中”，而是代码已经串进去了。**

### 3. 但默认配置是关闭的
核心证据：`resource/config/services/fastQA/config.shared.env`

默认值：
- `REDIS_ENABLED=0`

这决定了：
- 提交态下如果没有额外 secret env/运行时覆盖，`build_redis_bindings(...)` 会返回 disabled
- runtime status 会把 redis 标成 `skipped`
- stage1/stage2/pdf cache 逻辑仍会收到 `redis_service`，但 `available=False` 时会直接跳过缓存

结论：
- **fastQA 的 Redis“能力存在”，但“当前默认配置下通常不生效”。**
- 如果线上/本机另有环境变量覆盖，则另当别论；就代码仓当前共享配置而言，默认是关的。

## C. highThinkingQA：没有 Redis 主缓存层
核心证据：
- 对 `highThinkingQA` 全目录检索 `redis` / `REDIS` 无结果
- `highThinkingQA/config.shared.env` 与 `resource/config/services/highThinkingQA/config.shared.env` 均无 Redis 配置

已确认存在的缓存/复用形式：
- `agent_core/llm_client.py` 的 prompt template `lru_cache`
- `ingest/vector_store.py` 的 chroma client / collection `lru_cache`
- `ingest/pipeline.py` 的 parsed markdown 文件缓存
- `server/services/documents_service.py` 的进程内 translation dict cache

结论：
- **highThinkingQA 当前没有 Redis 层。**
- 也就没有跨进程共享的会话级 / 检索级 / 推理级缓存。

---

## 四、当前架构上的关键事实

### 事实 1
`public-service` 已经是 gateway 架构下的 conversation authority。

### 事实 2
`fastQA` / `highThinkingQA` 仍然保留对旧版 `server.services.conversation.conversation_service` 的直接耦合。

### 事实 3
`public-service` 的 Redis 正在真实承担公共缓存和锁；这部分不是空壳。

### 事实 4
`fastQA` 的 Redis 虽然已接入主链代码，但默认配置关闭，容易造成“代码看起来支持、实际运行未启用”的认知偏差。

### 事实 5
`highThinkingQA` 目前没有 Redis 架构层接入；其聊天持久化和上下文读取仍依赖旧版 conversation service。

---

## 五、对用户问题的直接回答

### 问题 1：现在的聊天记录持久化这部分是 fastQA 在做还是 public-service 在做？
- 如果是现在前端经 `gateway` 的主链：**`public-service` 在做权威持久化**。
- 如果是 `fastQA` / `highThinkingQA` 直打自己的服务：**不是 public-service，而是它们调用旧版 `conversation_service` 在做。**

### 问题 2：Redis 缓存是否生效并在使用中，public-service 在做 Redis 缓存这部分的工作吗？
- `public-service`：**是，明确生效并在使用中。**
- `fastQA`：**有代码支持，但默认共享配置关闭，是否当前实例生效要看运行时覆盖。**
- `highThinkingQA`：**没有 Redis 主缓存层。**

---

## 六、风险与后续建议
- [high] `fastQA` / `highThinkingQA` 会话持久化仍依赖旧版 monolith `conversation_service`，没有完全统一到 `public-service`
- [high] `highThinkingQA` 多轮上下文读取仍读取旧版 conversation snapshot，不是 `public-service`
- [medium] `fastQA` 的 Redis 缓存虽然已接入，但默认配置关闭，容易造成“代码看起来支持、实际运行未启用”的认知偏差
- [medium] `public-service` 已承担 authority 与缓存职责，但 mode-specific QA 服务没有完全收口到它，架构边界还不彻底
