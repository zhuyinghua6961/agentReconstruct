# QA Stage Cache Design

**Date:** 2026-03-24

## Scope
- `fastQA` 普通 `kb_qa` 问答阶段缓存
- `highThinkingQA` 普通 thinking 问答阶段缓存
- 不包含聊天记录 authority/cache，这部分继续由 `public-service` 负责

## Verified Implementation Snapshot
- 已完成并通过测试的范围：
  - `fastQA`: `stage1`、`stage2`、`stage2.5`、`stage3`
  - `highThinkingQA`: `direct_answer`、`decompose`、`retrieve`
- 已验证测试：
  - `fastQA` 相关缓存与 orchestrator/health 测试 `24 passed`
  - `highThinkingQA` 相关缓存与运行时测试 `27 passed`
  - 合并回归 `58 passed`
- 当前仍未做：
  - `fastQA stage4` 跨请求缓存
  - `highThinkingQA` 的 `subanswer / synthesize / check / revise` 跨请求缓存

## Design Summary
- 阶段缓存继续由各 QA 服务各自实现，各自直连 Redis。
- `public-service` 不承接问答阶段缓存，只承接会话 authority、会话列表/详情/上下文快照等公共缓存。
- 两个 QA 服务共享同一个 Redis 基础设施，但 key 空间、TTL、singleflight、失效策略按服务隔离。

## Boundary
### `public-service`
- 会话列表缓存
- 会话详情缓存
- context snapshot 缓存
- pending assistant overlay
- authority 持久化后的版本推进

### `fastQA`
- `stage1`
- `stage2`
- `stage2.5` (`md_expansion`)
- `stage3` (`pdf chunk loading`)
- 后续 `stage4` 仅保留局部请求内缓存，本轮不做完整跨请求阶段缓存

### `highThinkingQA`
- `direct_answer`
- `decompose`
- `retrieve`
- 预留 `subanswer / synthesize / check / revise`
- 本轮先不上最终答案整段缓存

## Why This Split
- 阶段缓存 key 强依赖各自问答引擎内部 prompt/model/runtime flags，不适合放进 `public-service`
- 阶段缓存是热路径，不应增加 `QA -> public-service -> Redis -> QA` 的额外 hop
- 各服务后续演进不会强耦合 `public-service`

## fastQA Plan
### Existing State
- 已有 Redis 基础设施
- 已有 `stage1/stage2` cache + singleflight
- 现已将 Redis 默认开启，阶段缓存可实际生效
- `stage2.5/stage3` 原先没有正式阶段缓存

### Changes
1. 启用 `fastQA` Redis 默认配置
2. 保留现有 `stage1/stage2` key 设计
3. 新增 `stage25_cache`
- key 绑定：`QA_CACHE_EPOCH + KB_DATA_EPOCH + route_hint + runtime model + md runtime flags hash + question hash + doi set hash + retrieval results hash`
- TTL: 1800s
- 结果要求可 JSON 序列化
4. 新增 `stage3_cache`
- key 绑定：`QA_CACHE_EPOCH + PAPERS_DATA_EPOCH(or KB_DATA_EPOCH fallback) + route_hint + max_chunks_per_doi + doi set hash`
- TTL: 1800s
- 缓存 `doi -> chunks`
5. `stage25/stage3` 接入 `singleflight`
6. 健康接口继续暴露 Redis status，用于验证缓存是否在线

### Implemented Details
- `stage25` 与 `stage3` 都已在 `GenerationPipelineOrchestrator.run()` 与 `stream()` 中接入 cache + singleflight。
- `fastQA` Redis 默认配置位于 `resource/config/services/fastQA/config.shared.env`，当前 `REDIS_ENABLED=1`。
- `stage25` 实际缓存内容为：
  - `enabled`
  - `applied`
  - `md_chunks_by_doi`
  - `stats`
- `stage3` 实际缓存内容为 `dict[doi, list[chunks]]`。

## highThinkingQA Plan
### Existing State
- 原先没有 Redis bootstrap
- 原先没有阶段缓存基础设施
- 只有进程内 `lru_cache` / 本地文件缓存

### Changes
1. 新增 Redis config/bootstrap
2. 在 health 暴露 Redis 组件状态
3. 新增 `stage_cache` 模块
- `RedisService`
- key factory
- metrics
- singleflight
- `direct_answer_cache`
- `decompose_cache`
- `retrieve_cache`
4. `agent_core.graph.run_agent()` 接入缓存
- `direct_answer` 包一层缓存函数
- `decompose` 包一层缓存函数
- `vector_retriever.batch_retrieve()` 内对每个 query 做缓存读取/写入
5. 首轮不缓存 `stream` 事件序列，不缓存最终整段答案

### Implemented Details
- Redis bootstrap 并未改 `highThinkingQA/config.py`，而是在 `highThinkingQA/server/services/redis_client.py` 中直接解析环境变量。
- `server_fastapi/app.py` 启动时调用 `bootstrap_redis_state(app.state)`，把 Redis 绑定状态写入 `app.state.component_status`。
- `server_fastapi/routers/health.py` 始终返回 HTTP 200，并在 `components.redis` 中暴露 `ok/skipped/degraded` 状态。
- `graph.py` 已接入：
  - `get_or_compute_direct_answer(...)`
  - `get_or_compute_decompose(...)`
- `retrieve` 采用按 query 的 key 设计，key 实际绑定：
  - `HT_QA_CACHE_EPOCH`
  - `CHROMA_PERSIST_DIR`
  - `CHROMA_COLLECTION_NAME`
  - `top_k`
  - normalized query hash
- `direct_answer` key 额外绑定：
  - model
  - `enable_thinking`
  - `direct_answer.txt` prompt hash
- `decompose` key 额外绑定：
  - model
  - `enable_thinking`
  - `num_sub_questions`
  - `decompose.txt` prompt hash
- 当前 `retrieve` 已按 query 接入 `get_or_compute_retrieve_query(...)`。
- Redis 可用时：走 per-query cache + singleflight，避免同一 query 并发 miss 重复计算。
- Redis 不可用时：仍保留原有批量 `embed_texts + batch_query_collection` 路径，维持无缓存场景下的吞吐优化。

## TTL And Invalidation
### Shared Principles
- 使用 epoch + 运行时签名而不是主动删除大批 key
- 通过 prompt/model/flag hash 保证配置切换自动失效

### fastQA
- `stage1`: 3600s
- `stage2`: 1800s
- `stage25`: 1800s
- `stage3`: 1800s
- 失效依赖 `QA_CACHE_EPOCH`、`KB_DATA_EPOCH`、`PAPERS_DATA_EPOCH`、运行时 flags

### highThinkingQA
- `direct_answer`: 3600s
- `decompose`: 21600s
- `retrieve`: 1800s
- 失效依赖 `HT_QA_CACHE_EPOCH`、向量库路径/collection/top_k、model/prompt hash

## Testing Strategy
### fastQA
- 新增 `stage25/stage3` cache 单测
- 新增 orchestrator 命中缓存单测
- 新增 Redis 启用/health 验证

### highThinkingQA
- 新增 Redis bootstrap 单测
- 新增 `direct_answer/decompose/retrieve` cache roundtrip 单测
- 新增 `run_agent()` 命中缓存时跳过底层 stage 调用的单测

## Rollout
1. 先落基础设施与测试
2. 接入 `fastQA`
3. 接入 `highThinkingQA`
4. 提权跑测试
5. 看 health/logs 验证

## Known Gaps
- 设计阶段里曾考虑过 batch-key retrieval cache，但实际已经修正为 per-query cache，因为 batch key 会受异步完成顺序和 partial flush 时机影响，不稳定。
