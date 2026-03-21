# system 依赖图与 schema/真实返回差异

对应代码：
- `backend/app/modules/system/service.py`
- `backend/app/modules/system/schemas.py`
- `backend/app/core/runtime.py`
- `backend/tests/test_system.py`
- `backend/tests/test_redis_runtime.py`

## 1. system 几乎不持久化任何数据，它只读 runtime 和外部状态

这个模块自己不写库、不写缓存配置。

它主要读取：

- `AppRuntime` 内存状态
- Redis
- 本地日志目录
- retrieval runtime
- agent graph
- QA cache metrics

所以它是一个典型的“聚合视图模块”，不是数据拥有者。

## 2. 对 runtime 的依赖非常深

`SystemService` 直接读这些 runtime 字段：

- `component_status`
- `agent`
- `generation_runtime`
- `vector_db_client`
- `redis_client`
- `settings.redis_key_prefix`
- `conversation_outbox_thread`
- `conversation_outbox_status`
- `upload_processing_worker`
- `current_answer_context`
- `logs_dir`
- `answer_cache`
- `init_agent`

这意味着 system service 实际上是 runtime 的一个外部观测和控制面，而不是简单业务模块。

## 3. Redis 不是直接 raw client，而是包成带 prefix 的 `RedisService`

`_redis_service(runtime)` 会用：

- `RedisService.from_prefix(client=runtime.redis_client, key_prefix=runtime.settings.redis_key_prefix)`

因此 system 不直接拼裸 key，而是复用平台 Redis key prefix 约束。

`test_redis_runtime.py` 也固定了：

- 默认 key prefix 是 `agentcode`
- redis status 会进入 runtime component_status

所以 system 的 cache debug 能力也建立在统一 prefix 体系上。

## 4. `qa_cache` metrics 是全局快照，不是 per-user 状态

`snapshot_cache_metrics()` 返回的是整个进程当前 metrics 聚合。

这意味着：

- health/background_status 看到的是全局 QA cache 行为
- 不是某个请求、某个用户、某个 conversation 的局部视图

因此它更适合运维面板，不太适合作为面向普通用户的状态 API。

## 5. schema 文件与真实返回明显脱节

例如：

`HealthResponse` 只定义：

- `status`
- `agent_initialized`
- `timestamp`

但实际 `build_health()` 还返回：

- `generation_runtime_initialized`
- `vector_db_initialized`
- `storage_backend`
- `components`
- `qa_cache`

`BackgroundStatusData` 只定义：

- 当前答案预览
- 最新 background file

但实际返回还包括：

- `conversation_outbox`
- `upload_processing`
- `qa_cache`

所以 `schemas.py` 在这里更像：

- 残留的简化模型

而不是：

- 可依赖的准确 contract

## 6. 真实 contract 主要由测试在守

`test_health_contract()` 固定了：

- `status=degraded` 的计算逻辑
- `storage_backend` 暴露
- `qa_cache.config` 必须存在

`test_background_status_contract()` 固定了：

- outbox / upload_processing / qa_cache 字段

`test_conversation_cache_debug_contract()` 固定了：

- conversation cache debug 的核心字段

也就是说，在这个模块里测试比 schema 更接近事实契约。

## 7. system 把多个子系统粘成了一个观测接口层

它横跨：

- runtime 基础组件
- retrieval
- QA cache
- conversation cache
- upload processing

这让它很方便，但也带来一个后果：

- 模块边界天然偏混合

这就是为什么当前总述里必须把它标成“混合模块”。

## 8. 如果后续要继续拆 system，至少要先按依赖面拆

比较自然的拆法会是：

- 平台健康与基础组件
- 后台 worker 与 cache 调试
- QA runtime 运维

而不是按当前文件直接一整个搬走。
