# fastQA Stage2 DashScope 热连接优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `fastQA` 的 Stage2 增加 chat 热 lane 池、rerank 热 session 池、后台保温与本地并发门控，在不更换模型与 rerank 策略的前提下，把 DashScope 上游调用从“频繁命中冷连接”改为“优先命中热连接”。

**Architecture:** 保留现有 `shared_llm_http_pool` 作为通用 LLM transport，同时新增两个仅面向 Stage2 的 worker 内池：`ChatHotLanePool` 与 `RerankHotSessionPool`。`ChatHotLanePool` 对外暴露与现有代码兼容的 `OpenAICompatClient` lease，而不是让 Stage2 直接操作裸 `httpx.Client`；`RerankHotSessionPool` 对外暴露独占 `requests.Session` lease。Stage2 的外部上游调用不再只依赖 claim 线程并发，而是受 lane ready 数量与 gate 限制；当 `ready_lanes == 0` 时必须 fail-open 回退旧路径，不能把 gate 算成 `0`。池对象在 worker 启动后后台低并发预热，并通过 keep-warm 与 stop-event/shutdown 回收保持热态。

**Tech Stack:** FastAPI, gunicorn multi-worker runtime, httpx, requests.Session, Python threading/locks, existing `GenerationDrivenRAG`, existing Stage2 retrieval pipeline, pytest

**Spec:** `fastQA/docs/stage2_dashscope_hot_connection_spec.md`

---

## 0. 文件地图

### 新增文件

- Create: `fastQA/app/integrations/llm/hot_lane_pool.py`
- Create: `fastQA/app/integrations/llm/rerank_session_pool.py`
- Create: `fastQA/tests/test_llm_hot_lane_pool.py`
- Create: `fastQA/tests/test_rerank_session_pool.py`
- Create: `fastQA/tests/test_stage2_hot_connection_runtime.py`

### 修改文件

- Modify: `fastQA/app/integrations/llm/__init__.py`
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/routers/health.py`
- Modify: `fastQA/app/main.py`
- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
- Modify: `fastQA/app/modules/generation_pipeline/rerank_service.py`
- Modify: `fastQA/app/modules/microscopic_expert.py`
- Modify: `fastQA/tests/test_generation_stage2_retrieval.py`
- Modify: `fastQA/tests/test_microscopic_search.py`
- Modify: `fastQA/tests/test_microscopic_expert.py`
- Modify: `fastQA/tests/test_health.py`
- Modify: `fastQA/tests/test_generation_runtime_shared_pool.py`

### 可选文档更新

- Modify: `fastQA/docs/README.md`

---

## 1. 实施原则

1. **保持当前行为可回退**
   - 新增所有能力必须有 feature flag
   - 池初始化失败必须回退到当前实现

2. **先补 contracts，再补热池，再做路由切换**
   - 避免先改 Stage2 主链路，再补底层结构

3. **先让 rerank 有复用，再做 chat 热 lane**
   - 当前 rerank 是完全裸 `requests.post(...)`
   - 这是收益最高、实现最直观的第一刀

4. **并发门控晚于热池上线**
   - 先有可用的 ready lane 统计，再引入 gate

5. **gate 必须与现有 Stage2 并发策略对齐**
   - claim 线程并发仍由 `QA_STAGE2_PARALLEL_WORKERS` / dynamic workers 控制
   - gate 只限制外部上游调用
   - `ready_lanes == 0` 时 bypass gate，绝不允许把主链路压成 `0`

6. **生命周期必须完整**
   - 后台 warm-up / keepalive 线程必须有 stop event
   - shutdown 时必须先停线程，再关 client/session

7. **TDD**
   - 每个阶段先补 failing tests，再实现

---

## 2. 任务拆解

### Task 1: 增加配置项与 health contracts

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/routers/health.py`
- Create: `fastQA/tests/test_stage2_hot_connection_runtime.py`
- Modify: `fastQA/tests/test_health.py`

- [ ] **Step 1: 写配置与 health 的失败测试**

测试至少覆盖：

```python
def test_settings_expose_stage2_hot_pool_flags():
    settings = get_settings()
    assert hasattr(settings, "stage2_chat_hot_pool_enabled")
    assert hasattr(settings, "stage2_rerank_hot_pool_enabled")
    assert hasattr(settings, "stage2_chat_hot_lane_count")
    assert hasattr(settings, "stage2_rerank_hot_lane_count")


def test_health_payload_contains_stage2_hot_pool_components(client):
    payload = client.get("/api/health").json()
    assert "stage2_chat_hot_pool" in payload["components"]
    assert "stage2_rerank_hot_pool" in payload["components"]
```

- [ ] **Step 2: 跑失败测试**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_stage2_hot_connection_runtime.py fastQA/tests/test_health.py -q`

Expected:

- FAIL，缺少配置字段与 health 组件

- [ ] **Step 3: 在 `config.py` 中新增配置项**

至少新增：

- `stage2_chat_hot_pool_enabled`
- `stage2_rerank_hot_pool_enabled`
- `stage2_chat_hot_lane_count`
- `stage2_rerank_hot_lane_count`
- `stage2_chat_warmup_enabled`
- `stage2_rerank_warmup_enabled`
- `stage2_chat_warm_interval_seconds`
- `stage2_rerank_warm_interval_seconds`
- `stage2_chat_hot_keepalive_expiry_seconds`
- `stage2_chat_warm_timeout_seconds`
- `stage2_rerank_warm_timeout_seconds`
- `stage2_bootstrap_warm_max_parallel`
- `stage2_bootstrap_warm_jitter_seconds`
- `stage2_chat_gate_max_in_flight`
- `stage2_rerank_gate_max_in_flight`
- `stage2_warm_jitter_seconds`
- `stage2_lane_degraded_after_seconds`

默认值要求：

- `stage2_chat_warm_timeout_seconds >= 420`
- `stage2_rerank_warm_timeout_seconds >= 420`
- 原因是当前文档证据里首次冷路径 warm 调用约为 `269s - 270s`

- [ ] **Step 4: 在 `runtime.py` 中增加空的 hot pool component status**

要求：

- app 启动时即存在
- 默认可显示 `enabled`、`ready_lanes`、`total_lanes`
- 即使未初始化成功也不报错

- [ ] **Step 5: 更新 health 输出**

要求：

- `components.stage2_chat_hot_pool`
- `components.stage2_rerank_hot_pool`

- [ ] **Step 6: 重新跑测试直到通过**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_stage2_hot_connection_runtime.py fastQA/tests/test_health.py -q`

Expected:

- PASS

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/core/config.py fastQA/app/core/runtime.py fastQA/app/routers/health.py fastQA/tests/test_stage2_hot_connection_runtime.py fastQA/tests/test_health.py
git commit -m "feat: add stage2 hot pool runtime contracts"
```

---

### Task 2: 实现 Chat Hot Lane Pool

**Files:**
- Create: `fastQA/app/integrations/llm/hot_lane_pool.py`
- Modify: `fastQA/app/integrations/llm/__init__.py`
- Create: `fastQA/tests/test_llm_hot_lane_pool.py`

- [ ] **Step 1: 写失败测试**

测试至少覆盖：

```python
def test_chat_hot_lane_pool_builds_configured_lane_count():
    pool = ChatHotLanePool(lane_count=3, ...)
    assert pool.total_lanes == 3


def test_chat_hot_lane_pool_lease_is_exclusive():
    with pool.lease_lane(trace_label="claim_1") as lane:
        assert lane.in_flight == 1
        assert lane.client is not None
    assert pool.snapshot()["busy_lanes"] == 0


def test_chat_hot_lane_pool_tracks_ready_and_degraded_state():
    pool.mark_ready(0)
    pool.mark_degraded(1, "boom")
    snapshot = pool.snapshot()
    assert snapshot["ready_lanes"] == 1
    assert snapshot["degraded_lanes"] == 1
```

- [ ] **Step 2: 跑失败测试**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_llm_hot_lane_pool.py -q`

Expected:

- FAIL，缺少 `ChatHotLanePool`

- [ ] **Step 3: 实现 `ChatHotLanePool`**

设计要求：

- lane 内部维护独立 `httpx.Client`
- 对外暴露与当前 Stage2 兼容的 `OpenAICompatClient`
- compat client 必须通过现有 `build_chat_completions_client(..., http_client=lane_http_client)` 构造
- 每 lane：
  - `max_connections=1`
  - `max_keepalive_connections=1`
  - 独立 `keepalive_expiry_seconds`
- 支持：
  - `warm_lane()`
  - `lease_lane()`
  - `snapshot()`
  - `close()`
- lane 状态：
  - `cold`
  - `warming`
  - `ready`
  - `degraded`
- lease 语义：
  - 必须是 context manager
  - context manager 必须 yield lane handle 或 `None`
  - 单 lane 同时只允许 `1` 个 in-flight request
  - 没有 ready lane 时 yield `None`，由上层 fail-open 回退

- [ ] **Step 4: 导出到 `app/integrations/llm/__init__.py`**

- [ ] **Step 5: 跑测试直到通过**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_llm_hot_lane_pool.py -q`

Expected:

- PASS

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/integrations/llm/hot_lane_pool.py fastQA/app/integrations/llm/__init__.py fastQA/tests/test_llm_hot_lane_pool.py
git commit -m "feat: add stage2 chat hot lane pool"
```

---

### Task 3: 将 Chat Hot Lane Pool 接到 runtime，并在 Stage2 query generation 中使用

**Files:**
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
- Modify: `fastQA/tests/test_generation_runtime_shared_pool.py`
- Modify: `fastQA/tests/test_generation_stage2_retrieval.py`

- [ ] **Step 1: 写失败测试**

测试至少覆盖：

```python
def test_generation_runtime_bootstrap_initializes_chat_hot_pool_when_enabled():
    runtime = ...
    bootstrap_generation_runtime(runtime)
    assert runtime.stage2_chat_hot_pool is not None


def test_stage2_query_generation_uses_leased_chat_lane():
    result = run_stage2_targeted_retrieval(...)
    assert lane_pool.lease_called is True
    assert lane_pool.used_trace_labels == ["claim_1"]
```

- [ ] **Step 2: 跑失败测试**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_generation_stage2_retrieval.py -q`

Expected:

- FAIL，runtime 和 Stage2 尚未接入 hot lane

- [ ] **Step 3: runtime 初始化 chat hot pool**

要求：

- 每 worker 启动时创建 `stage2_chat_hot_pool`
- 后台 warm-up，不阻塞 app readiness
- health 里可见 `ready_lanes`

- [ ] **Step 4: `GenerationDrivenRAG` 接受 stage2 chat hot pool 依赖**

要求：

- 不影响 Stage1 / Stage4 现有 generic shared pool 路径
- 仅 Stage2 的 `_generate_ai_query` 走 hot lane lease
- 上层仍调用 `client.chat.completions.create(...)`
- 没有 ready lane 时自动回退 `self.client`

- [ ] **Step 5: 在 `stage2_retrieval.py` 中加入 lane lease 日志**

示例：

```text
stage2 chat lane lease trace_label=claim_3 lane=2 ready=true
```

- [ ] **Step 6: 跑测试直到通过**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_generation_stage2_retrieval.py -q`

Expected:

- PASS

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/core/runtime.py fastQA/app/modules/generation_pipeline/runtime_bootstrap.py fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py fastQA/app/modules/generation_pipeline/stage2_retrieval.py fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_generation_stage2_retrieval.py
git commit -m "feat: route stage2 query generation through chat hot lanes"
```

---

### Task 4: 实现 Rerank Session Pool

**Files:**
- Create: `fastQA/app/integrations/llm/rerank_session_pool.py`
- Create: `fastQA/tests/test_rerank_session_pool.py`
- Modify: `fastQA/app/integrations/llm/__init__.py`

- [ ] **Step 1: 写失败测试**

测试至少覆盖：

```python
def test_rerank_session_pool_builds_configured_lane_count():
    pool = RerankSessionPool(lane_count=3, ...)
    assert pool.total_lanes == 3


def test_rerank_session_pool_leases_ready_lane():
    with pool.lease_lane(trace_label="claim_1") as lane:
        assert lane.session is not None
        assert lane.in_flight == 1
```

- [ ] **Step 2: 跑失败测试**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_rerank_session_pool.py -q`

Expected:

- FAIL，缺少 `RerankSessionPool`

- [ ] **Step 3: 实现 `RerankSessionPool`**

要求：

- 每 lane 一个 `requests.Session`
- 支持：
  - `warm_lane()`
  - `lease_lane()`
  - `snapshot()`
  - `close()`
- 维护与 chat pool 相同的健康状态语义
- 单 lane 同时只允许 `1` 个请求占用
- 没有 ready lane 时 yield `None`，由上层 fail-open 回退

- [ ] **Step 4: 导出到 `app/integrations/llm/__init__.py`**

- [ ] **Step 5: 跑测试直到通过**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_rerank_session_pool.py -q`

Expected:

- PASS

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/integrations/llm/rerank_session_pool.py fastQA/app/integrations/llm/__init__.py fastQA/tests/test_rerank_session_pool.py
git commit -m "feat: add stage2 rerank session pool"
```

---

### Task 5: 将 rerank 接到 session pool，并增加后台 warm-up / keepalive

**Files:**
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/generation_pipeline/rerank_service.py`
- Modify: `fastQA/app/modules/microscopic_expert.py`
- Modify: `fastQA/tests/test_microscopic_search.py`
- Modify: `fastQA/tests/test_microscopic_expert.py`
- Modify: `fastQA/tests/test_stage2_hot_connection_runtime.py`

- [ ] **Step 1: 写失败测试**

测试至少覆盖：

```python
def test_microscopic_expert_leases_rerank_session_when_pool_available():
    result = expert._rerank_documents(...)
    assert pool.lease_called is True


def test_runtime_starts_background_warmup_for_rerank_pool():
    bootstrap_generation_runtime(runtime)
    assert runtime.stage2_rerank_hot_pool is not None
    assert runtime.component_status["stage2_rerank_hot_pool"]["enabled"] is True
```

- [ ] **Step 2: 跑失败测试**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_microscopic_search.py fastQA/tests/test_microscopic_expert.py fastQA/tests/test_stage2_hot_connection_runtime.py -q`

Expected:

- FAIL，rerank service 尚未接入 pool

- [ ] **Step 3: runtime 初始化 rerank hot pool**

要求：

- 和 chat pool 一样走后台 warm-up
- health 输出 `ready_lanes`
- 后台线程必须可停止，并挂到 `close_generation_runtime(...)`

- [ ] **Step 4: 打通 rerank pool 的依赖注入路径**

要求：

- runtime 持有 `stage2_rerank_hot_pool`
- `GenerationDrivenRAG` 接受并保留该依赖
- `MicroscopicSemanticExpert` 接受 `rerank_session_pool`
- 真正发 rerank 请求的位置在 `MicroscopicSemanticExpert._rerank_documents(...)` 内部进行 lane lease

- [ ] **Step 5: `rerank_service.py` 支持使用已 lease 的 Session**

要求：

- 不改变现有 endpoint 和 payload 结构
- 如果收到已 lease `Session`，则用该 `Session.post(...)`
- 无可用 lane 时回退当前 `requests.post(...)`

- [ ] **Step 6: 加入后台 keepalive / rewarm 逻辑**

要求：

- 使用 `warm_interval_seconds`
- 加入 jitter
- lane stale 时标记 degraded
- 启动期增加：
  - `bootstrap_warm_max_parallel`
  - `bootstrap_warm_jitter_seconds`
- 首次 bootstrap warm timeout 不得短于 `420s`，除非后续重新拿到更短的 provider 冷路径证据
- shutdown 时 stop event + join + close

- [ ] **Step 7: 跑测试直到通过**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_microscopic_search.py fastQA/tests/test_microscopic_expert.py fastQA/tests/test_stage2_hot_connection_runtime.py -q`

Expected:

- PASS

- [ ] **Step 8: Commit**

```bash
git add fastQA/app/core/runtime.py fastQA/app/modules/generation_pipeline/runtime_bootstrap.py fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py fastQA/app/modules/generation_pipeline/rerank_service.py fastQA/app/modules/microscopic_expert.py fastQA/tests/test_microscopic_search.py fastQA/tests/test_microscopic_expert.py fastQA/tests/test_stage2_hot_connection_runtime.py
git commit -m "feat: route rerank through hot sessions"
```

---

### Task 6: 增加 Stage2 chat / rerank 本地并发门控

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/stage2_retrieval.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/microscopic_expert.py`
- Modify: `fastQA/tests/test_generation_stage2_retrieval.py`
- Modify: `fastQA/tests/test_microscopic_expert.py`

- [ ] **Step 1: 写失败测试**

测试至少覆盖：

```python
def test_stage2_chat_gate_uses_ready_lane_count():
    result = run_stage2_targeted_retrieval(...)
    assert gate_limit == 2


def test_stage2_rerank_gate_uses_ready_lane_count():
    result = run_stage2_targeted_retrieval(...)
    assert rerank_gate_limit == 3


def test_stage2_bypasses_gate_when_no_ready_lanes():
    result = run_stage2_targeted_retrieval(...)
    assert gate_bypassed is True
    assert fallback_path_used is True


def test_rerank_gate_wraps_only_upstream_rerank_call():
    result = expert._rerank_documents(...)
    assert rerank_gate_used is True
    assert embedding_or_chroma_not_gated is True
```

- [ ] **Step 2: 跑失败测试**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_microscopic_expert.py -q`

Expected:

- FAIL，当前还没有 gate

- [ ] **Step 3: 为 chat 调用增加 gate**

要求：

- gate limit = `min(configured_limit, ready_chat_lanes, effective_parallel_workers)`
- 记录 gate wait 日志

- [ ] **Step 4: 为 rerank 调用增加 gate**

要求：

- gate limit = `min(configured_limit, ready_rerank_lanes, effective_parallel_workers)`
- 记录 gate wait 日志
- 接入点必须是实际 rerank 上游调用边界
- gate 应放在 `MicroscopicSemanticExpert._rerank_documents(...)` 内部，包住 lane lease + rerank HTTP 请求
- 不要把 gate 包在整个 semantic search、embedding 或 Chroma 查询外面

- [ ] **Step 5: 增加 fail-open 回退**

要求：

- gate / pool 异常不影响主链路
- `ready_lanes == 0` 时 bypass gate + 直接回退旧路径
- claim 线程并发仍由现有 `QA_STAGE2_PARALLEL_WORKERS` / dynamic workers 决定

- [ ] **Step 6: 跑测试直到通过**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_microscopic_expert.py -q`

Expected:

- PASS

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/modules/generation_pipeline/stage2_retrieval.py fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py fastQA/app/modules/microscopic_expert.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_microscopic_expert.py
git commit -m "feat: add stage2 upstream concurrency gates"
```

---

### Task 7: 完善 lifecycle / observability，并将 shared keepalive 长窗口作为可选实验

**Files:**
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/main.py`
- Modify: `fastQA/app/routers/health.py`
- Modify: `fastQA/tests/test_health.py`
- Modify: `fastQA/tests/test_generation_runtime_shared_pool.py`
- Modify: `fastQA/tests/test_stage2_hot_connection_runtime.py`
- Optional: `resource/config/services/fastQA/config.shared.env`

- [ ] **Step 1: 写失败测试**

测试至少覆盖：

```python
def test_health_exposes_ready_lane_counts():
    payload = client.get("/api/health").json()
    assert "ready_lanes" in payload["components"]["stage2_chat_hot_pool"]


def test_close_generation_runtime_stops_hot_pool_threads(runtime):
    close_generation_runtime(runtime)
    assert runtime.stage2_chat_hot_pool is None
    assert runtime.stage2_rerank_hot_pool is None
```

- [ ] **Step 2: 跑失败测试**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_health.py fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_stage2_hot_connection_runtime.py -q`

Expected:

- FAIL，health 聚合字段与 shutdown 生命周期尚未完整反映最终设计

- [ ] **Step 3: 完善 lifecycle 与 health 聚合语义**

要求：

- pool-level health 字段使用聚合语义：
  - `last_any_warm_success_at`
  - `last_any_error_at`
  - `last_error_summary`
- `close_generation_runtime(...)` 负责 stop event + join + close
- 与 `app.main` 的 lifespan shutdown 钩子兼容

- [ ] **Step 4: 将 shared keepalive 长窗口作为可选 canary 实验记录**

要求：

- 不把 `FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS=3600` 写成主路径必选项
- 如需试验，单独修改 `resource/config/services/fastQA/config.shared.env`
- 保持可独立回退

- [ ] **Step 5: 跑测试直到通过**

Run: `eval "$(conda shell.bash hook)" && conda activate agent && pytest fastQA/tests/test_health.py fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_stage2_hot_connection_runtime.py -q`

Expected:

- PASS

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/core/runtime.py fastQA/app/main.py fastQA/app/routers/health.py fastQA/tests/test_health.py fastQA/tests/test_generation_runtime_shared_pool.py fastQA/tests/test_stage2_hot_connection_runtime.py
git commit -m "chore: finalize stage2 hot pool lifecycle and observability"
```

---

### Task 8: 联调与性能回归验证

**Files:**
- Modify: `fastQA/docs/README.md` (optional)

- [ ] **Step 1: 重启 `fastQA` 并等待 hot lane 进入 ready**

Run:

```bash
source scripts/_service_common.sh && run_service_script fastQA stop && run_service_script fastQA start
```

Expected:

- `fastQA` 正常启动
- `/api/health` 能看到 hot pool 组件

- [ ] **Step 2: 用 live log 验证 lane warm-up**

Run:

```bash
rg -n "stage2 chat lane warm|stage2 rerank lane warm|stage2 hot pool summary" resource/logs/dev/fastQA/fastqa-app.log | tail -n 50
```

Expected:

- 出现 lane warm success 日志

- [ ] **Step 3: 连续打两轮 `kb_qa` 请求，验证新日志**

Run:

```bash
curl -sS -X POST http://127.0.0.1:8008/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Ti掺杂对磷酸铁锂倍率性能的影响是什么？","route":"kb_qa","requested_mode":"fast","kb_enabled":true}' >/tmp/fastqa_stage2_probe_1.json
curl -sS -X POST http://127.0.0.1:8008/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Ti掺杂对磷酸铁锂倍率性能的影响是什么？","route":"kb_qa","requested_mode":"fast","kb_enabled":true}' >/tmp/fastqa_stage2_probe_2.json
rg -n "stage2 chat lane lease|stage2 rerank lane lease|stage2 claim timing|stage2 semantic search timing|Stage2 timing summary" resource/logs/dev/fastQA/fastqa-app.log | tail -n 120
```

Expected:

- claim 日志里能看到 lane lease
- `ai_query_ms` 和 `rerank_ms` 相比当前基线显著下降

- [ ] **Step 4: 做直连对照验证**

Run:

```bash
eval "$(conda shell.bash hook)" && conda activate agent && python - <<'PY'
import os
import time
import requests

api_key = str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()
if not api_key:
    raise SystemExit("DASHSCOPE_API_KEY is required")

chat_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
rerank_url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

chat_payload = {
    "model": "deepseek-v3.1",
    "messages": [{"role": "user", "content": "请简要回答：磷酸铁锂倍率性能受哪些因素影响？"}],
    "stream": False,
    "max_tokens": 32,
}
rerank_payload = {
    "model": "qwen3-vl-rerank",
    "input": {
        "query": "Ti掺杂对磷酸铁锂倍率性能的影响",
        "documents": ["Ti掺杂可改善电子导电性", "该文只讨论测试设备，不涉及材料性能"],
    },
    "parameters": {"return_documents": False, "top_n": 2},
}

def run_seq(label, session, url, payload, count=5, timeout=420):
    print(f"\\n== {label} ==")
    for i in range(1, count + 1):
        started = time.monotonic()
        resp = session.post(url, headers=headers, json=payload, timeout=timeout)
        elapsed_ms = (time.monotonic() - started) * 1000
        print(f"{label} request_{i}: status={resp.status_code} elapsed_ms={elapsed_ms:.1f}")
        resp.raise_for_status()

with requests.Session() as chat_session:
    run_seq("chat", chat_session, chat_url, chat_payload)

with requests.Session() as rerank_session:
    run_seq("rerank", rerank_session, rerank_url, rerank_payload)
PY
```

Expected:

- 热 lane 行为接近“同 session 后续请求”的快路径

- [ ] **Step 5: 汇总验证结果并记录到文档或 PR 描述**

- [ ] **Step 6: Commit**

```bash
git add fastQA/docs/README.md
git commit -m "docs: record stage2 hot connection rollout validation"
```

---

## 3. 风险控制

### 3.1 功能开关

必须支持以下独立开关：

- `FASTQA_STAGE2_CHAT_HOT_POOL_ENABLED`
- `FASTQA_STAGE2_RERANK_HOT_POOL_ENABLED`
- `FASTQA_STAGE2_CHAT_WARMUP_ENABLED`
- `FASTQA_STAGE2_RERANK_WARMUP_ENABLED`

### 3.2 回退顺序

若上线后异常：

1. 先关 `FASTQA_STAGE2_RERANK_HOT_POOL_ENABLED`
2. 再关 `FASTQA_STAGE2_CHAT_HOT_POOL_ENABLED`
3. 再关 gate，恢复纯旧路径
4. 如果此前单独启用了 shared keepalive canary，再恢复 `FASTQA_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS`

### 3.3 验证重点

必须重点验证：

- 8 workers 下 lane 总数是否可控
- health 是否准确反映 ready lane 数
- worker 重启后是否会长时间停留在 cold/degraded
- 没有热 lane 时是否能自动回退

---

## 4. 完成标准

满足以下条件才算完成：

1. chat 与 rerank 都具备 worker 内 hot lane / hot session 能力
2. hot lane 状态可在 health 中查看
3. Stage2 外部并发受 ready lane 数量门控
4. 所有优化都可单独关闭回退
5. 端到端日志能清楚反映：
   - lane warm success
   - lane lease
   - gate wait
   - claim timing
6. `ready_lanes == 0` 时不会阻断主链路
7. worker shutdown 时 hot pool 线程和会话能被正确回收
8. 与当前基线相比，Stage2 的 `ai_query_ms` 和 `rerank_ms` 呈现明显下降趋势

---

## 5. 执行建议

推荐执行顺序：

1. Task 1
2. Task 4
3. Task 5
4. Task 2
5. Task 3
6. Task 6
7. Task 7
8. Task 8

理由：

- 先补 runtime contracts
- 再优先修复 rerank 无 session 复用
- 最后把 chat 热 lane 与 gate 接进主链路
