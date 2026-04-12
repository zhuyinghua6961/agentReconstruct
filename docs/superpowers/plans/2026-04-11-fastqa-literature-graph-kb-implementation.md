# FastQA Literature Graph KB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不影响当前 `fastQA` generation-driven `kb_qa` 主路径、且不触碰文件 `hybrid_qa` 语义的前提下，为普通 `kb_qa` 增加“文献图谱优先尝试，失败静默回退”的图谱问答能力。

**Architecture:** 实现分为五条收敛主线。第一条是 runtime 与配置主线，给 `fastQA` 增加可关闭、可观测、与 generation runtime 解耦的 Neo4j 运行时。第二条是 graph module 主线，新增 `graph_kb` 的判定、模板查询、确定性渲染与质量门槛。第三条是 `kb_qa` 集成主线，只在 `fastQA` 普通问答入口增加前置图谱尝试，不改 generation orchestrator 主结构。第四条是回退与回归主线，锁死“图谱失败不影响主链”以及“文件 `hybrid_qa` 语义不变”。第五条是 release gate，验证 health、配置、日志、静默回退与本机 Neo4j 联调行为。

**Tech Stack:** FastAPI, Python, pytest, Neo4j bootstrap, deterministic text rendering, fastQA router/runtime, env-based feature flags

---

## Source Documents

- Spec:
  - `docs/superpowers/specs/2026-04-11-fastqa-literature-graph-kb-design.md`
- Related discovery docs:
  - `docs/audit/知识图谱问答流程.md`
  - `docs/legacy_fastapi_normal_qa_pipeline.md`
- Reuse candidates:
  - `public-service/backend/app/integrations/neo4j/client.py`
  - `public-service/backend/app/modules/documents/reference_preview.py`
  - `public-service/backend/app/modules/documents/service.py`

## Current-State Implementation Notes

- 当前 `fastQA` 普通问答入口在：
  - `fastQA/app/routers/qa.py`
  - `fastQA/app/modules/qa_kb/service.py`
  - `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- 当前 `kb_qa` 只支持 generation-driven 主链，不支持旧图谱链。
- 当前 `hybrid_qa` 已经是文件混合问答语义，不得改写。
- `fastQA` 当前没有正式 Neo4j runtime，但 runtime/bootstrap 结构已经适合增量接入一个新组件。

## Workspace Conventions

### Repository Root

所有命令默认以仓库根目录为起点；不要把工作树绝对路径写死到执行命令里。

推荐先拿根目录：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
```

然后用 repo-relative 命令，例如：

```bash
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_health.py -q
```

### Verification Tiers

1. **Tier A: Repo-local verification**
   - 单元测试、契约测试、health 响应测试、router fallback 测试
   - 默认不依赖真实 Neo4j 服务
2. **Tier B: Service-backed verification**
   - 本机 Neo4j 实例联调
   - `FASTQA_GRAPH_KB_ENABLED=1` 的真实 health / fallback / success-path 观察
   - 只有 release gate 进入这一层

## Hard Rules

1. 不允许改写 `pdf_qa / tabular_qa / hybrid_qa` 的 route 语义。
2. 不允许把图谱能力做成新的 route、mode 或前端显式开关。
3. Phase 1 图谱答案必须走确定性渲染，不得引入新的 LLM 合成链路。
4. Phase 1 follow-up / 指代型问题一律 `skip`，直接回退 generation-driven。
5. 图谱失败、runtime 不可用、模板未命中、质量不足，都必须静默回退到现有 `kb_qa` 主链。
6. 每个功能 task 都按 TDD：红灯测试 -> 最小实现 -> 目标测试转绿 -> review -> commit。
7. `resource/config/services/fastQA/config.shared.env` 里的图谱开关默认必须保持关闭值，不能默认打开。

## File Structure Lock-In

### Runtime / Config Surface

- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/main.py`
- Modify: `fastQA/app/routers/health.py`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify: `resource/config/services/fastQA/config.env.example`

### Neo4j Integration

- Create: `fastQA/app/integrations/neo4j/client.py`

### Graph KB Module

- Create: `fastQA/app/modules/graph_kb/models.py`
- Create: `fastQA/app/modules/graph_kb/classifier.py`
- Create: `fastQA/app/modules/graph_kb/client.py`
- Create: `fastQA/app/modules/graph_kb/service.py`

### Router Integration

- Modify: `fastQA/app/routers/qa.py`

### Tests

- Create: `fastQA/tests/test_graph_kb_runtime.py`
- Create: `fastQA/tests/test_graph_kb_classifier.py`
- Create: `fastQA/tests/test_graph_kb_client.py`
- Create: `fastQA/tests/test_graph_kb_service.py`
- Create: `fastQA/tests/test_fastqa_kb_graph_integration.py`
- Modify: `fastQA/tests/test_health.py`
- Re-run regression tests:
  - `fastQA/tests/test_request_adapter.py`
  - `fastQA/tests/test_qa_routes_file_modes.py`
  - `fastQA/tests/test_qa_kb_service.py`

## Acceptance Targets

### A. Main-Path Safety

以下场景必须全部成立：

1. `FASTQA_GRAPH_KB_ENABLED=0` 时，`kb_qa` 行为与改动前一致
2. `FASTQA_GRAPH_KB_ENABLED=1` 但 Neo4j 不可用时，`kb_qa` 仍返回 generation-driven 答案
3. 图谱分类命中但查询失败时，`kb_qa` 仍返回 generation-driven 答案
4. 文件 `pdf_qa / tabular_qa / hybrid_qa` 行为无变化

### B. Phase-1 Graph Success Path

图谱成功路径至少满足：

1. `metadata.query_mode == "graph_kb"`
2. 至少存在一个 graph success-path `step` 事件
3. `done.route == "kb_qa"`
4. 若结果含 DOI，则 `done.references` 返回 DOI 列表
5. 成功答案来自确定性渲染，不依赖 `generation_runtime`

### C. Follow-Up Safety

以下问题必须直接 `skip` 图谱：

1. “它的 DOI 是什么？”
2. “那篇最高的是哪篇？”
3. “前者和后者有什么关系？”

验证要求：

- 图谱分类器返回 `skip`
- router 继续走 generation-driven 主链

### D. Health / Runtime Contract

实现后必须可在 `healthz` 中看到：

- `components.graph_kb`
- `graph_kb_enabled`
- `graph_kb_ready`

并满足：

- 图谱关闭时 `graph_kb` 为 `skipped`
- 图谱开启但连接失败时 `graph_kb` 为 `degraded`
- 图谱开启且可用时 `graph_kb` 为 `ok`

## Task 1: Freeze Graph-KB Runtime And Health Contract

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/main.py`
- Modify: `fastQA/app/routers/health.py`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify: `resource/config/services/fastQA/config.env.example`
- Test: `fastQA/tests/test_health.py`
- Test: `fastQA/tests/test_graph_kb_runtime.py`

- [ ] **Step 1: Write failing tests for graph-kb settings and health exposure**

Cover:
- `FASTQA_GRAPH_KB_ENABLED` default false
- health payload exposes `graph_kb_enabled`
- health payload exposes `graph_kb_ready`
- health payload exposes `components.graph_kb`
- readiness probe status still depends on generation runtime, not graph runtime

- [ ] **Step 2: Run targeted tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_health.py tests/test_graph_kb_runtime.py -q
```

Expected:
- FAIL because `graph_kb_*` config/state/health fields do not exist yet

- [ ] **Step 3: Add graph-kb settings and runtime state**

Implement:
- `FASTQA_GRAPH_KB_ENABLED`
- `FASTQA_GRAPH_KB_TIMEOUT_MS`
- `FASTQA_GRAPH_KB_MAX_ROWS`
- `FASTQA_GRAPH_KB_QUERY_LOGGING`
- `app.state.neo4j_client`
- `app.state.graph_kb_ready`
- `component_status["graph_kb"]`

Constraints:
- graph runtime failure must not change `generation_runtime_ready`
- `config.shared.env` default must be `FASTQA_GRAPH_KB_ENABLED=0`

- [ ] **Step 4: Update health payload and example config files**

Implement:
- `components.graph_kb`
- `graph_kb_enabled`
- `graph_kb_ready`
- config examples for all new env vars

- [ ] **Step 5: Re-run targeted tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_health.py tests/test_graph_kb_runtime.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/core/config.py fastQA/app/core/runtime.py fastQA/app/main.py fastQA/app/routers/health.py resource/config/services/fastQA/config.shared.env resource/config/services/fastQA/config.env.example fastQA/tests/test_health.py fastQA/tests/test_graph_kb_runtime.py
git commit -m "feat: add fastqa graph kb runtime contract"
```

## Task 2: Add Local Neo4j Bootstrap For FastQA

**Files:**
- Create: `fastQA/app/integrations/neo4j/client.py`
- Modify: `fastQA/app/core/runtime.py`
- Test: `fastQA/tests/test_graph_kb_runtime.py`

- [ ] **Step 1: Write failing tests for Neo4j bootstrap modes and degradation**

Cover:
- missing `NEO4J_URL` -> bootstrap skipped
- successful graph construction -> available and not degraded
- bootstrap exception -> degraded
- APOC-related exception -> degraded but connectivity-aware fallback result shape preserved

- [ ] **Step 2: Run targeted tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_runtime.py -q -k neo4j
```

Expected:
- FAIL because local Neo4j bootstrap module does not exist yet

- [ ] **Step 3: Implement local bootstrap by minimally adapting the public-service version**

Requirements:
- keep the implementation local to `fastQA`
- preserve read-only bootstrap and degraded-mode behavior
- do not import `public-service` runtime modules directly

- [ ] **Step 4: Wire bootstrap into fastQA runtime initialization**

Implement:
- graph runtime only initializes when `FASTQA_GRAPH_KB_ENABLED=1`
- `component_status["graph_kb"]` reflects skipped / degraded / ok
- `neo4j_client` stored on app state

- [ ] **Step 5: Re-run targeted tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_runtime.py -q -k neo4j
```

Expected:
- PASS

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/integrations/neo4j/client.py fastQA/app/core/runtime.py fastQA/tests/test_graph_kb_runtime.py
git commit -m "feat: add fastqa local neo4j bootstrap"
```

## Task 3: Implement Graph-KB Models And Conservative Classifier

**Files:**
- Create: `fastQA/app/modules/graph_kb/models.py`
- Create: `fastQA/app/modules/graph_kb/classifier.py`
- Test: `fastQA/tests/test_graph_kb_classifier.py`

- [ ] **Step 1: Write failing tests for graph decision and follow-up skip rules**

Cover:
- structured standalone question -> `try_graph`
- explanation / summary question -> `skip`
- pronoun-based follow-up -> `skip`
- previous-turn-dependent follow-up -> `skip`
- file-route context present -> `skip`
- decision payload includes `decision`, `reason`, and minimal explainability fields

- [ ] **Step 2: Run targeted tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_classifier.py -q
```

Expected:
- FAIL because graph_kb classifier/models do not exist yet

- [ ] **Step 3: Implement graph decision models**

Include:
- `GraphKbDecision`
- `GraphKbQueryPlan`
- `GraphKbExecutionResult`
- result/fallback fields needed by router integration

- [ ] **Step 4: Implement conservative classifier**

Rules:
- standalone structured lookup/count/rank/relation questions -> `try_graph`
- ambiguous follow-up questions -> `skip`
- wide semantic/explanatory questions -> `skip`
- conversation context only allowed to justify `skip`, not to reconstruct hidden query semantics

- [ ] **Step 5: Re-run targeted tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_classifier.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/modules/graph_kb/models.py fastQA/app/modules/graph_kb/classifier.py fastQA/tests/test_graph_kb_classifier.py
git commit -m "feat: add fastqa graph kb classifier"
```

## Task 4: Implement Template Planning, Whitelists, And Query Execution

**Files:**
- Create: `fastQA/app/modules/graph_kb/client.py`
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Test: `fastQA/tests/test_graph_kb_client.py`

- [ ] **Step 1: Write failing tests for template planning and whitelist enforcement**

Cover:
- `lookup_by_doi`
- `list_by_material`
- `compare_numeric_threshold`
- `rank_numeric_topn`
- `count_by_filter`
- `relation_exists`
- unknown property -> no plan
- unknown relation -> no plan
- result trimming respects `FASTQA_GRAPH_KB_MAX_ROWS`

- [ ] **Step 2: Run targeted tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_client.py -q
```

Expected:
- FAIL because graph_kb client/plans/whitelists do not exist yet

- [ ] **Step 3: Implement template planner and explicit whitelists**

Requirements:
- keep property whitelist and relation whitelist in code
- do not do schema discovery
- do not allow free-form Cypher generation

- [ ] **Step 4: Audit the real literature graph schema and lock the initial whitelist**

Do this before considering Task 4 complete.

Requirements:
- inspect the actual local Neo4j literature graph if available
- confirm the concrete property names and relation types used by the initial templates
- update the in-code property whitelist and relation whitelist to match the confirmed schema
- if the local graph is unavailable, record the exact blocker and stop rollout at Task 7 instead of guessing field names

Suggested verification commands:

```bash
cypher-shell -a "$NEO4J_URL" -u "$NEO4J_USERNAME" -p "$NEO4J_PASSWORD" "CALL db.schema.visualization()"
cypher-shell -a "$NEO4J_URL" -u "$NEO4J_USERNAME" -p "$NEO4J_PASSWORD" "MATCH (n) RETURN keys(n) AS keys LIMIT 20"
```

- [ ] **Step 5: Implement read-only query execution and result normalization**

Requirements:
- execute only predeclared Cypher templates
- normalize rows into deterministic internal result shape
- capture template id, row count, latency, and fallback reason

- [ ] **Step 6: Re-run targeted tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_client.py -q
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/modules/graph_kb/client.py fastQA/app/modules/graph_kb/models.py fastQA/tests/test_graph_kb_client.py
git commit -m "feat: add fastqa graph kb template client"
```

## Task 5: Implement Deterministic Rendering And Quality Gate

**Files:**
- Create: `fastQA/app/modules/graph_kb/service.py`
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Test: `fastQA/tests/test_graph_kb_service.py`

- [ ] **Step 1: Write failing tests for deterministic answer rendering and fallback**

Cover:
- DOI lookup renders stable title/journal/date/doi answer
- count query renders explicit numeric answer
- rank query renders ordered top-N answer
- relation query renders exists / not-exists answer
- rows without required fields -> `handled=False`
- answers with DOI values populate `references`
- rendering does not call any LLM or generation runtime

- [ ] **Step 2: Run targeted tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_service.py -q
```

Expected:
- FAIL because graph_kb service does not exist yet

- [ ] **Step 3: Implement deterministic rendering helpers**

Requirements:
- template-specific renderer functions
- pure deterministic formatting
- stable references extraction from DOI-bearing results

- [ ] **Step 4: Implement quality gate and `try_answer(...)` orchestration**

Requirements:
- `skip` short-circuits without querying Neo4j
- no-plan / empty-result / low-quality result returns `handled=False`
- success path returns answer text, references, metadata, and explainability fields

- [ ] **Step 5: Re-run targeted tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_service.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/modules/graph_kb/service.py fastQA/app/modules/graph_kb/models.py fastQA/tests/test_graph_kb_service.py
git commit -m "feat: add fastqa graph kb deterministic renderer"
```

## Task 6: Integrate Graph-KB Into `kb_qa` With Silent Fallback

**Files:**
- Modify: `fastQA/app/routers/qa.py`
- Modify: `fastQA/app/core/runtime.py`
- Test: `fastQA/tests/test_fastqa_kb_graph_integration.py`
- Regression Test: `fastQA/tests/test_qa_routes_file_modes.py`
- Regression Test: `fastQA/tests/test_request_adapter.py`
- Regression Test: `fastQA/tests/test_qa_kb_service.py`

- [ ] **Step 1: Write failing integration tests for graph success and silent fallback**

Cover:
- graph disabled -> generation path used
- graph enabled + runtime unavailable -> generation path used
- graph enabled + classifier skip -> generation path used
- graph enabled + graph success -> graph answer returned directly
- graph enabled + graph exception -> generation path used
- graph success path emits `metadata`, at least one graph `step`, `content`, and `done`
- returned route remains `kb_qa`
- success path `metadata.query_mode == "graph_kb"`

Do not over-specify:
- exact `step` event names should not be mandatory in tests
- but presence of at least one graph success-path `step` event is mandatory
- assert coarse compatibility, not every intermediate frame

- [ ] **Step 2: Run targeted tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_fastqa_kb_graph_integration.py -q
```

Expected:
- FAIL because router does not call graph_kb service yet

- [ ] **Step 3: Insert pre-generation graph attempt into `route == "kb_qa"`**

Requirements:
- keep existing conversation context building
- call graph service before `qa_kb_service.iter_answer_events(...)`
- on `handled=True`, emit compatible SSE / JSON answer path and return
- on `handled=False`, continue existing generation path unchanged

- [ ] **Step 4: Add router-level logging / observability fields**

Include:
- `graph_kb_attempted`
- `graph_kb_decision`
- `graph_kb_handled`
- `graph_kb_template`
- `graph_kb_result_count`
- `graph_kb_latency_ms`
- `graph_kb_fallback_reason`

- [ ] **Step 5: Re-run targeted integration tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_fastqa_kb_graph_integration.py -q
```

Expected:
- PASS

- [ ] **Step 6: Re-run regression suite to prove no route confusion**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_qa_routes_file_modes.py tests/test_request_adapter.py tests/test_qa_kb_service.py -q
```

Expected:
- PASS

- [ ] **Step 7: Re-run wrapper-path regression through current ask surfaces**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_fastqa_kb_graph_integration.py -q -k "ask or stream"
```

Expected:
- PASS with graph success path compatible with current `/api/ask` and `/api/v1/ask`

- [ ] **Step 8: Commit**

```bash
git add fastQA/app/routers/qa.py fastQA/app/core/runtime.py fastQA/tests/test_fastqa_kb_graph_integration.py fastQA/tests/test_qa_routes_file_modes.py fastQA/tests/test_request_adapter.py fastQA/tests/test_qa_kb_service.py
git commit -m "feat: add fastqa kb graph fallback integration"
```

## Task 7: Release Gate And Service-Backed Verification

**Files:**
- Modify if implementation reveals necessary drift: `docs/superpowers/specs/2026-04-11-fastqa-literature-graph-kb-design.md`
- Optional verification note if needed: `docs/superpowers/implementation/2026-04-11-fastqa-literature-graph-kb-verification.md`

- [ ] **Step 1: Run full repo-local fastQA verification for this feature set**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && PYTHONPATH=. pytest tests/test_graph_kb_runtime.py tests/test_graph_kb_classifier.py tests/test_graph_kb_client.py tests/test_graph_kb_service.py tests/test_fastqa_kb_graph_integration.py tests/test_health.py tests/test_qa_routes_file_modes.py tests/test_request_adapter.py tests/test_qa_kb_service.py -q
```

Expected:
- PASS

- [ ] **Step 2: Verify config-off behavior against a running fastQA instance**

Example:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && FASTQA_GRAPH_KB_ENABLED=0 PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8008
```

Then confirm:
- `/healthz` reports `graph_kb_enabled=false`
- normal `kb_qa` still works

- [ ] **Step 3: Verify config-on but no-Neo4j fallback behavior**

Example:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/fastQA" && FASTQA_GRAPH_KB_ENABLED=1 NEO4J_URL=bolt://127.0.0.1:9999 PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8008
```

Then confirm:
- `/healthz` reports `graph_kb` degraded
- `kb_qa` request still returns generation-driven answer instead of service failure

- [ ] **Step 4: Verify real Neo4j schema and whitelist against the local literature graph**

This is mandatory before rollout when local graph access is available.

Confirm:
- each initial property whitelist entry exists in the graph
- each initial relation whitelist entry exists in the graph
- each phase-1 template can map to confirmed fields instead of guessed names

If verification fails:
- do not mark rollout complete
- trim the whitelist/template inventory to the confirmed subset

- [ ] **Step 5: Verify real Neo4j success path if local instance is available**

Example prerequisites:
- local Neo4j started manually
- valid `NEO4J_URL / NEO4J_USERNAME / NEO4J_PASSWORD`

Run a structured standalone question such as:
- `某 DOI 是什么文献`
- `有哪些关于某材料的文献`
- `某属性最高的前 3 项是什么`

Then confirm:
- `query_mode=graph_kb`
- success path includes at least one `step` event before terminal completion
- answer is deterministic and structured
- DOI-bearing answers include `references`

- [ ] **Step 6: Review residual risks and document only if needed**

Record only real residual risks:
- actual property whitelist coverage still incomplete
- local Neo4j schema differs from expected template fields
- graph success path coverage is narrow by design

- [ ] **Step 7: Commit verification-only doc if created**

```bash
git add docs/superpowers/specs/2026-04-11-fastqa-literature-graph-kb-design.md docs/superpowers/implementation/2026-04-11-fastqa-literature-graph-kb-verification.md
git commit -m "docs: record fastqa graph kb verification"
```

## Review Checklist For Every Task

Before marking any task complete, the implementer must verify:

1. Tests were written before implementation
2. The exact task command ran and passed
3. `kb_qa` main-path fallback remained intact
4. No file-route semantics changed
5. Any reviewer findings were resolved before commit

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-11-fastqa-literature-graph-kb-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
