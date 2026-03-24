# QA Stage Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `fastQA` 与 `highThinkingQA` 实现可工作的 Redis 问答阶段缓存，并让当前运行栈能够实际启用这些缓存。

**Architecture:** `public-service` 继续负责会话 authority 相关缓存；`fastQA` 与 `highThinkingQA` 各自维护自己的阶段缓存模块、key 设计与 singleflight。`fastQA` 复用现有 Redis 基础设施并补齐 `stage25/stage3`；`highThinkingQA` 新增 Redis/bootstrap/cache 层并先接入 `direct_answer/decompose/retrieve`。

**Tech Stack:** FastAPI, gunicorn, Redis, pytest, conda `agent` env

---

## Current Status Summary
- 已完成实现与验证：
  - `fastQA`: `stage25/stage3` cache + orchestrator wiring + Redis 默认启用
  - `highThinkingQA`: Redis bootstrap + `direct_answer/decompose/retrieve` cache + retrieve per-query singleflight
- 已跑验证：
  - `fastQA` 定向测试 `24 passed`
  - `highThinkingQA` 定向测试 `27 passed`
  - 合并回归 `58 passed`
- 当前剩余差口：
  - 文档之外暂无阻塞性失败

### Task 1: fastQA 阶段缓存补齐与启用

**Files:**
- Create: `fastQA/app/modules/qa_cache/stage25_cache.py`
- Create: `fastQA/app/modules/qa_cache/stage3_cache.py`
- Modify: `fastQA/app/modules/qa_cache/__init__.py`
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Test: `fastQA/tests/test_qa_cache_stage25.py`
- Test: `fastQA/tests/test_qa_cache_stage3.py`
- Test: `fastQA/tests/test_qa_generation_orchestrator.py`
- Test: `fastQA/tests/test_redis_runtime.py`
- Test: `fastQA/tests/test_health.py`

- [x] Step 1: 写 `stage25/stage3` failing tests
- [x] Step 2: 提权运行定向 pytest，确认测试覆盖新行为
- [x] Step 3: 实现 `stage25/stage3` cache key/read/write
- [x] Step 4: 在 orchestrator 中接入 `stage25/stage3` cache + singleflight
- [x] Step 5: 打开 `fastQA` 默认 Redis 开关
- [x] Step 6: 提权运行 `conda run --no-capture-output -n agent pytest fastQA/tests/test_qa_cache_stage25.py fastQA/tests/test_qa_cache_stage3.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_redis_runtime.py fastQA/tests/test_health.py -q`
- [x] Result: `24 passed in 0.11s`

### Task 2: highThinkingQA Redis 基础设施

**Files:**
- Create: `highThinkingQA/server/services/redis_client.py`
- Create: `highThinkingQA/server/services/stage_cache.py`
- Modify: `highThinkingQA/server_fastapi/app.py`
- Modify: `highThinkingQA/server_fastapi/routers/health.py`
- Modify: `highThinkingQA/tests/conftest.py`
- Modify: `highThinkingQA/requirements.txt`
- Modify: `resource/config/services/highThinkingQA/config.shared.env`
- Test: `highThinkingQA/tests/test_stage_cache_runtime.py`

- [x] Step 1: 写 highThinking Redis/bootstrap failing tests
- [x] Step 2: 提权运行 `conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_stage_cache_runtime.py -q`
- [x] Step 3: 实现 Redis config/bootstrap/service/health 暴露
- [x] Step 4: 提权运行 `conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_stage_cache_runtime.py -q`
- [x] Result: runtime 相关测试已包含在 `26 passed`

### Task 3: highThinkingQA 首批阶段缓存接入

**Files:**
- Modify: `highThinkingQA/agent_core/graph.py`
- Modify: `highThinkingQA/retriever/vector_retriever.py`
- Test: `highThinkingQA/tests/test_stage_cache_behavior.py`
- Test: `highThinkingQA/tests/test_run_agent_overlap.py`
- Test: `highThinkingQA/tests/test_stage_model_selection.py`
- Test: `highThinkingQA/tests/test_prompt_and_retrieval_optimizations.py`

- [x] Step 1: 写 `direct_answer/decompose/retrieve` cache failing tests
- [x] Step 2: 提权运行 `conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_stage_cache_behavior.py -q`
- [x] Step 3: 实现 `direct_answer/decompose/retrieve` cache 接入
- [x] Step 4: 提权运行 `conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_stage_cache_runtime.py highThinkingQA/tests/test_stage_cache_behavior.py highThinkingQA/tests/test_run_agent_overlap.py highThinkingQA/tests/test_stage_model_selection.py highThinkingQA/tests/test_prompt_and_retrieval_optimizations.py -q`
- [x] Result: `27 passed in 1.08s`
- [x] Follow-up: 已把 `retrieve` singleflight helper 接进 `batch_retrieve()` 的 Redis-enabled 路径；Redis-disabled 路径继续保留批量检索优化

### Task 4: 集成验证与文档回写

**Files:**
- Modify: `docs/superpowers/specs/2026-03-24-qa-stage-cache-design.md`
- Modify: `docs/superpowers/plans/2026-03-24-qa-stage-cache-implementation.md`

- [x] Step 1: 提权运行 `conda run --no-capture-output -n agent pytest fastQA/tests/test_qa_cache.py fastQA/tests/test_qa_cache_stage1.py fastQA/tests/test_qa_cache_stage2.py fastQA/tests/test_qa_cache_stage25.py fastQA/tests/test_qa_cache_stage3.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_redis_runtime.py fastQA/tests/test_health.py highThinkingQA/tests/test_stage_cache_runtime.py highThinkingQA/tests/test_stage_cache_behavior.py highThinkingQA/tests/test_run_agent_overlap.py highThinkingQA/tests/test_stage_model_selection.py highThinkingQA/tests/test_prompt_and_retrieval_optimizations.py -q`
- [x] Step 2: 检查 Redis health/status 暴露语义
- [x] Step 3: 更新文档中的实际完成范围、TTL、已知缺口
- [x] Result: `58 passed in 0.91s`

## Notes
- `public-service` 仍不承接 QA 阶段缓存；它只承接会话 authority 与公共缓存。
- `highThinkingQA` 当前 health 语义是“服务存活返回 200，同时在 payload 中暴露 Redis 组件状态”，不是“Redis 异常就把健康检查打挂”。
- `highThinkingQA` 当前 retrieval cache 是稳定的 per-query key，不是 batch key。
