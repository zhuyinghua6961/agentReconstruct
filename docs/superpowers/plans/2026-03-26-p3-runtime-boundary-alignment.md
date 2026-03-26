# P3 Runtime Boundary Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口 `P3-3`、`P3-5`、`P3-6`，让 `fastQA` 与 `highThinkingQA` 的问答阶段缓存 TTL 与 key 语义对齐，并移除 `gateway` 在 QA ask 路径上的聊天持久化兼容代理。

**Architecture:** 这轮不碰 `P3-1/P3-2/P3-4`。`fastQA` 与 `highThinkingQA` 继续各自直连 Redis 做阶段缓存，但 key 语义收敛成更平的 `<prefix>:cache|lock:<capability>:...` 形式，并把相同能力的 TTL 收敛到共享默认值。`gateway` 在 ask/ask_stream 路径去掉 user/assistant 代理持久化，让最终 authority 写入只发生在 `fastQA` / `highThinkingQA -> public-service` 活链路上。

**Tech Stack:** FastAPI, Python, Redis, pytest

---

### Task 1: 锁定 TTL 和 key 目标语义

**Files:**
- Read: `docs/superpowers/specs/2026-03-24-qa-stage-cache-design.md`
- Read: `docs/superpowers/specs/2026-03-23-highthinkingqa-persistence-migration-spec.md`
- Create: `docs/audit/2026-03-26-p3-runtime-boundary-notes.md`

- [ ] **Step 1: 记录当前真实 TTL**
- [ ] **Step 2: 决定本轮统一后的 TTL 目标**
- [ ] **Step 3: 记录 key 命名收敛规则与 gateway 去代理边界**

### Task 2: 先写失败测试，锁定 gateway 不再做 QA 持久化代理

**Files:**
- Modify: `gateway/tests/test_qa_proxy.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [ ] **Step 1: 把现有“gateway 会持久化”的断言改成失败测试**
- [ ] **Step 2: 新增 thinking/fast/file/hybrid 都不触发 gateway persistence 的断言**
- [ ] **Step 3: 运行定向测试确认失败**

Run: `conda run -n agent pytest gateway/tests/test_qa_proxy.py -q`
Expected: FAIL because gateway still persists user/assistant messages today

### Task 3: 实现 gateway ask 路径去代理持久化

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Possibly Modify: `gateway/app/services/conversation_persistence.py`
- Modify: `gateway/tests/test_qa_proxy.py`

- [ ] **Step 1: 去掉 `_proxy_ask/_proxy_ask_stream` 中的 user persistence 调用**
- [ ] **Step 2: 去掉 sync ask / stream done 后的 assistant persistence 调用**
- [ ] **Step 3: 保留纯分发、SSE 透传、错误转换行为不变**
- [ ] **Step 4: 跑 gateway 定向测试**

Run: `conda run -n agent pytest gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py -q`
Expected: PASS

### Task 4: 先写失败测试，锁定 Redis key 语义和 TTL 对齐

**Files:**
- Modify: `fastQA/tests/test_redis_helpers.py`
- Modify: `highThinkingQA/tests/test_stage_cache_runtime.py`
- Create: `fastQA/tests/test_qa_cache_ttl_contract.py`
- Create: `highThinkingQA/tests/test_stage_cache_ttl_contract.py`

- [ ] **Step 1: 为 fastQA key factory 和阶段 TTL 写失败测试**
- [ ] **Step 2: 为 highThinkingQA key factory 和阶段 TTL 写失败测试**
- [ ] **Step 3: 运行定向测试确认失败**

Run: `conda run -n agent pytest fastQA/tests/test_redis_helpers.py highThinkingQA/tests/test_stage_cache_runtime.py fastQA/tests/test_qa_cache_ttl_contract.py highThinkingQA/tests/test_stage_cache_ttl_contract.py -q`
Expected: FAIL because current key naming and TTL defaults are still divergent

### Task 5: 实现 fastQA / highThinkingQA TTL 与 key 收口

**Files:**
- Modify: `fastQA/app/integrations/redis/keys.py`
- Modify: `fastQA/app/modules/qa_cache/stage1_cache.py`
- Modify: `fastQA/app/modules/qa_cache/stage2_cache.py`
- Modify: `fastQA/app/modules/qa_cache/stage25_cache.py`
- Modify: `fastQA/app/modules/qa_cache/stage3_cache.py`
- Modify: `fastQA/app/modules/qa_cache/pdf_cache.py`
- Modify: `fastQA/app/services/pending_overlay.py`
- Modify: `highThinkingQA/server/services/redis_client.py`
- Modify: `highThinkingQA/server/services/stage_cache.py`
- Modify: `highThinkingQA/server/services/chat_persistence.py`
- Modify: related tests above

- [ ] **Step 1: 把 key shape 收敛到更平的 capability-first 形式**
- [ ] **Step 2: 对齐 shared TTL 原则，至少收敛 direct/decompose/retrieve 与 fastQA 对应阶段默认值**
- [ ] **Step 3: 保持 epoch/hash 失效语义不变，只减少命名层级和策略漂移**
- [ ] **Step 4: 跑 fast/high 定向测试**

Run: `conda run -n agent pytest fastQA/tests/test_redis_helpers.py fastQA/tests/test_qa_cache_stage1.py fastQA/tests/test_qa_cache_stage2.py fastQA/tests/test_qa_cache_stage25.py fastQA/tests/test_qa_cache_stage3.py highThinkingQA/tests/test_stage_cache_runtime.py highThinkingQA/tests/test_stage_cache_behavior.py -q`
Expected: PASS

### Task 6: 跑跨服务回归并更新文档

**Files:**
- Modify: `docs/audit/2026-03-26-p3-runtime-boundary-notes.md`
- Modify: `docs/audit/2026-03-25-alignment-priority-roadmap.md`
- Modify: `docs/superpowers/plans/2026-03-26-p3-runtime-boundary-alignment.md`

- [ ] **Step 1: 跑跨服务定向回归**

Run: `conda run -n agent pytest gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py fastQA/tests/test_redis_helpers.py fastQA/tests/test_qa_cache_stage1.py fastQA/tests/test_qa_cache_stage2.py fastQA/tests/test_qa_cache_stage25.py fastQA/tests/test_qa_cache_stage3.py highThinkingQA/tests/test_stage_cache_runtime.py highThinkingQA/tests/test_stage_cache_behavior.py -q`
Expected: PASS

- [ ] **Step 2: 更新 P3 状态文档**
- [ ] **Step 3: 提交本轮 P3 改动**

```bash
git add gateway fastQA highThinkingQA docs/audit/2026-03-26-p3-runtime-boundary-notes.md docs/audit/2026-03-25-alignment-priority-roadmap.md docs/superpowers/plans/2026-03-26-p3-runtime-boundary-alignment.md
git commit -m "refactor: align p3 cache and authority runtime boundaries"
```
