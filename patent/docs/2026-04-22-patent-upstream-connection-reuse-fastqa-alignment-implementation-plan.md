# Patent Upstream Connection Reuse FastQA Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `patent`'s upstream HTTP reuse from a minimal shared client into a measurable, rollback-safe transport layer, and only then conditionally add planning hot lanes, gates, and warm-up if the measured post-upgrade behavior justifies them.

**Architecture:** Phase 1 and Phase 2 are mandatory and must land first: centralize shared-pool config ownership, enrich the shared pool runtime object, and add caller-side transport helpers so timeout depth and pool metrics are real instead of nominal. Phase 3 and later are conditional: only add planning hot lanes, gates, and warm-up if the measured worker-level planning contention still exists after the base shared-pool upgrade.

**Tech Stack:** Python, FastAPI, httpx, pytest, existing `patent` bootstrap/health contract tests, existing `patent` LLM clients, conda environment `agent`.

---

## Constraints And References

**Hard constraints:**

- Only modify files under `patent/`
- Do not import runtime code from `fastQA/`
- All new resources must be owned by `patent`
- Any new pool, gate, or warm-up scheduler must be attached to `app.state`
- Any new pool, gate, or warm-up scheduler must close cleanly on bootstrap failure and lifespan shutdown
- Preserve current `patent` request behavior unless the change is explicitly behind a config gate
- Preserve stage4 citation sanitization and allowed patent ID filtering
- Preserve existing streamed answer behavior where it already exists
- Preserve PDF route/source-scope prompt behavior
- Preserve hybrid `file_over_kb` precedence semantics
- All test commands during implementation must run with escalated permissions, never in the sandbox
- All implementation-time Python and pytest commands must use the `agent` conda environment

**Primary references:**

- Spec: `patent/docs/2026-04-22-patent-upstream-connection-reuse-fastqa-alignment-spec.md`
- Current shared pool: `patent/server/patent/upstream_http.py`
- Current bootstrap: `patent/server_fastapi/app.py`
- Current health route: `patent/server_fastapi/routers/health.py`
- Planning client: `patent/server/patent/runtime.py`
- Stage4 answer builder: `patent/server/patent/answering.py`
- PDF client: `patent/server/patent/pdf_service.py`
- Tabular client: `patent/server/patent/tabular_service.py`
- Hybrid client: `patent/server/patent/hybrid_synthesis.py`
- Existing shared-pool tests: `patent/tests/test_patent_upstream_http.py`
- Existing bootstrap injection tests: `patent/tests/fastapi_contract/test_ask_contract.py`
- Existing health/lifecycle tests: `patent/tests/fastapi_contract/test_health_contract.py`
- Reference-only fastQA transport modules:
  - `fastQA/app/integrations/llm/shared_http_pool.py`
  - `fastQA/app/integrations/llm/openai_compat.py`
  - `fastQA/app/integrations/llm/hot_lane_pool.py`
  - `fastQA/app/integrations/llm/upstream_gate.py`
  - `fastQA/app/core/runtime.py`

## File Structure Map

**Files to modify in mandatory phases:**

- `patent/config.py`
- `patent/config.shared.env.example`
- `patent/server/patent/upstream_http.py`
- `patent/server/patent/runtime.py`
- `patent/server/patent/answering.py`
- `patent/server/patent/pdf_service.py`
- `patent/server/patent/tabular_service.py`
- `patent/server/patent/hybrid_synthesis.py`
- `patent/server_fastapi/app.py`
- `patent/server_fastapi/routers/health.py`
- `patent/tests/test_patent_upstream_http.py`
- `patent/tests/test_patent_stage4_synthesis.py`
- `patent/tests/test_patent_generation_orchestrator.py`
- `patent/tests/test_patent_hybrid_synthesis.py`
- `patent/tests/test_patent_file_routes.py`
- `patent/tests/test_patent_tabular_service.py`
- `patent/tests/fastapi_contract/test_ask_contract.py`
- `patent/tests/fastapi_contract/test_health_contract.py`

**Files likely to create in mandatory phases:**

- `patent/tests/test_patent_upstream_config.py`
- `patent/server/patent/upstream_transport.py`
- `patent/tests/test_patent_upstream_transport.py`

**Files likely to create in conditional phases:**

- `patent/server/patent/planning_hot_pool.py`
- `patent/server/patent/upstream_gate.py`
- `patent/tests/test_patent_planning_hot_pool.py`
- `patent/tests/test_patent_upstream_gate.py`

**Existing files likely to modify in conditional phases:**

- `patent/tests/test_patent_stage1_planning.py`
- `patent/tests/test_patent_retrieval_service.py`

**Files intentionally not modified unless a later approved scope explicitly expands:**

- any file under `fastQA/`
- `patent/server/patent/retrieval_service.py`
- `patent/server/patent/stages/retrieval.py`
- `patent/server/patent/file_routes.py`
- frontend code

## Verification Discipline

All implementation-time test commands must use escalated permissions and the `agent` conda environment. Use the same cache/temp routing pattern throughout:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest ...
```

The following invariants must remain true after every task:

- shared-pool bootstrap still degrades to private clients if bootstrap fails
- app shutdown still closes app-owned resources cleanly
- health still reports core runtime state correctly
- stage4 citation filtering is unchanged
- PDF route/source-scope behavior is unchanged
- hybrid `file_over_kb` precedence is unchanged

## Task 1: Centralize Shared-Pool Config Ownership

**Files:**

- Modify: `patent/config.py`
- Modify: `patent/config.shared.env.example`
- Modify: `patent/server/patent/upstream_http.py`
- Create: `patent/tests/test_patent_upstream_config.py`
- Modify: `patent/tests/test_patent_upstream_http.py`

- [ ] **Step 1: Add failing tests for centralized shared-pool settings**

Add tests that prove:

- `get_settings()` reads new `PATENT_LLM_HTTP_*` timeout fields
- the config example documents the new fields
- the provider can be constructed from centralized settings instead of ad hoc env only

Suggested test names:

```python
def test_get_settings_reads_patent_shared_http_timeout_fields(monkeypatch): ...
def test_config_shared_env_example_documents_patent_shared_http_timeout_defaults(): ...
def test_shared_upstream_provider_can_be_built_from_settings(): ...
```

- [ ] **Step 2: Run the targeted config tests and verify failure**

Run:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_upstream_config.py patent/tests/test_patent_upstream_http.py -q -k "shared_http_timeout_fields or shared_http_timeout_defaults or built_from_settings"
```

Expected:

- FAIL because `patent/config.py` does not yet model these settings and the provider is still env-owned

- [ ] **Step 3: Implement centralized shared-pool config ownership**

Implement:

- add a dedicated settings block or equivalent fields in `patent/config.py`
- add the new `PATENT_LLM_HTTP_*` defaults to `patent/config.shared.env.example`
- update `PatentSharedUpstreamHttpProvider` so it can be built from centralized settings
- preserve an env-backed compatibility path only if needed for tests or bootstrap transition

- [ ] **Step 4: Re-run the targeted config tests**

Run the same command as Step 2.

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/config.py patent/config.shared.env.example patent/server/patent/upstream_http.py patent/tests/test_patent_upstream_config.py patent/tests/test_patent_upstream_http.py
git commit -m "feat: centralize patent shared http config"
```

## Task 2: Upgrade The Shared Pool Runtime Object

**Files:**

- Modify: `patent/server/patent/upstream_http.py`
- Modify: `patent/tests/test_patent_upstream_http.py`

- [ ] **Step 1: Add failing provider tests for snapshot and metric semantics**

Add tests that prove:

- provider snapshot exposes `shared_client_id`, `pid`, `bootstrap_source`, connection limits, and keepalive expiry
- provider can record `pool_wait_ms`
- provider can increment `pool_timeout_count`
- `close()` makes the client unavailable without exploding on repeated close

Suggested test names:

```python
def test_shared_upstream_provider_snapshot_exposes_runtime_metadata(monkeypatch): ...
def test_shared_upstream_provider_records_pool_wait_and_timeout(monkeypatch): ...
def test_shared_upstream_provider_close_is_idempotent(monkeypatch): ...
```

- [ ] **Step 2: Run the targeted provider tests and verify failure**

Run:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_upstream_http.py -q -k "snapshot_exposes_runtime_metadata or records_pool_wait_and_timeout or close_is_idempotent"
```

Expected:

- FAIL because the provider currently exposes no snapshot or metric recording methods

- [ ] **Step 3: Implement the richer shared-pool runtime object**

Implement in `patent/server/patent/upstream_http.py`:

- shared config object
- shared client construction with full timeout depth
- pool metadata attachment to the client
- `snapshot()`
- `record_pool_wait(...)`
- `record_pool_timeout(...)`
- idempotent close behavior

- [ ] **Step 4: Re-run the targeted provider tests**

Run the same command as Step 2.

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/upstream_http.py patent/tests/test_patent_upstream_http.py
git commit -m "feat: enrich patent shared http provider"
```

## Task 3: Add Patent-Local Transport Helpers And Adapt All LLM Callers

**Files:**

- Create: `patent/server/patent/upstream_transport.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server/patent/hybrid_synthesis.py`
- Create: `patent/tests/test_patent_upstream_transport.py`

- [ ] **Step 1: Add failing transport-helper tests**

Add tests that prove:

- caller-side helpers can derive request-level timeout objects without collapsing everything back to one scalar timeout
- shared-pool callers can record pool wait and pool timeout metrics from the dispatch path
- shared/private ownership metadata is available to callers

Suggested test names:

```python
def test_build_request_timeout_preserves_connect_read_write_and_pool_dimensions(): ...
def test_transport_helper_records_pool_metrics_against_shared_provider(): ...
def test_transport_helper_reports_shared_vs_private_client_ownership(): ...
```

- [ ] **Step 2: Add failing caller tests for transport usage**

Add or extend tests around the five caller classes so they prove:

- they no longer flatten the transport contract to a single scalar timeout
- they still accept an injected shared client
- they still degrade correctly when using private clients

Suggested locations:

- `patent/tests/test_patent_upstream_transport.py`
- existing caller-specific tests if already present

- [ ] **Step 3: Run the transport-focused tests and verify failure**

Run:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_upstream_transport.py -q
```

Expected:

- FAIL because no shared helper exists and callers still pass scalar `timeout=...`

- [ ] **Step 4: Implement the patent-local transport helper**

Implement in `patent/server/patent/upstream_transport.py`:

- a helper or mixin for request-level timeout construction
- shared/private ownership inspection
- pool wait and timeout recording hooks
- any small common request-dispatch helpers needed by multiple callers

- [ ] **Step 5: Adapt the five LLM callers**

Adapt:

- `PatentPlanningClient`
- `PatentAnswerBuilder`
- `PatentPdfAnswerClient`
- `PatentTabularAnswerClient`
- `PatentHybridSynthesisClient`

Required result:

- full transport depth is preserved
- request-level metrics can update the shared provider
- shared/private ownership remains visible in logs
- existing public constructor behavior remains compatible

- [ ] **Step 6: Re-run the transport-focused tests**

Run the same command as Step 3, plus any caller-targeted test files that were extended.

Expected:

- PASS

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/upstream_transport.py patent/server/patent/runtime.py patent/server/patent/answering.py patent/server/patent/pdf_service.py patent/server/patent/tabular_service.py patent/server/patent/hybrid_synthesis.py patent/tests/test_patent_upstream_transport.py
git commit -m "feat: standardize patent upstream transport"
```

## Task 4: Expose Shared-Pool State In Bootstrap, Health, And Lifecycle

**Files:**

- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/server_fastapi/routers/health.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Add failing health and lifecycle tests**

Add tests that prove:

- `create_app()` exposes a `shared_llm_pool` component
- health returns the shared-pool snapshot fields
- bootstrap failure still degrades to private clients and reports the right shared-pool state
- lifespan shutdown closes the shared provider and any new transport-owned resources cleanly

Suggested test names:

```python
def test_create_app_exposes_shared_llm_pool_component(monkeypatch): ...
def test_health_exposes_shared_llm_pool_snapshot(monkeypatch): ...
def test_shared_pool_status_is_degraded_when_provider_bootstrap_fails(monkeypatch): ...
def test_lifespan_shutdown_closes_shared_llm_pool_resources(monkeypatch): ...
```

- [ ] **Step 2: Run the targeted health/bootstrap tests and verify failure**

Run:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py -q -k "shared_llm_pool or shared_pool_status or closes_shared_llm_pool_resources"
```

Expected:

- FAIL because `patent` does not yet expose a first-class `shared_llm_pool` component

- [ ] **Step 3: Implement bootstrap and health wiring**

Implement:

- app-state component defaults for `shared_llm_pool`
- bootstrap-time status updates using the provider snapshot
- degraded/skipped state handling when shared pool is disabled or bootstrap fails
- health response exposure for the shared-pool component

- [ ] **Step 4: Implement lifecycle ownership and cleanup**

Implement:

- any new transport-owned resources must be attached to `app.state`
- bootstrap exception cleanup must close them
- lifespan shutdown must close them
- shutdown must remain tolerant of already-closed resources

- [ ] **Step 5: Re-run the targeted health/bootstrap tests**

Run the same command as Step 2.

Expected:

- PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server_fastapi/app.py patent/server_fastapi/routers/health.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat: expose patent shared llm pool health"
```

## Task 5: Mandatory Phase Verification And Decision Gate

**Files:**

- No new production files required
- Reuse modified test files from Tasks 1-4

- [ ] **Step 1: Run the mandatory-phase targeted suite**

Run:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_upstream_config.py patent/tests/test_patent_upstream_http.py patent/tests/test_patent_upstream_transport.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py patent/tests/test_patent_stage4_synthesis.py patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_hybrid_synthesis.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_tabular_service.py -q
```

Expected:

- PASS

- [ ] **Step 2: Record the rollout checkpoint**

Document the following before touching any optional phase:

- shared-pool status is visible in health
- shared/private ownership is visible in logs
- planning and answer callers preserve timeout depth
- bootstrap degradation and shutdown cleanup are green
- stage4 citation filtering and streamed answer behavior are still green
- PDF route/source-scope behavior is still green
- hybrid `file_over_kb` precedence is still green

- [ ] **Step 3: Make the go/no-go decision for hot lanes**

Only proceed to Task 6 and later if observed worker-level planning traffic still shows:

- repeated cold-start latency on planning/query-generation calls
- shared-pool wait/timeout pressure attributable to the planning path
- evidence that the enhanced shared pool alone is not sufficient

If these signals are absent, stop after Task 5 and ship only the mandatory phases.

- [ ] **Step 4: Commit**

```bash
git commit --allow-empty -m "chore: checkpoint patent shared upstream phase"
```

## Task 6: Conditional Planning Hot Pool

**Precondition:** Task 5 explicitly concluded that planning hot lanes are justified.

**Files:**

- Create: `patent/server/patent/planning_hot_pool.py`
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/server_fastapi/routers/health.py`
- Modify: `patent/server/patent/runtime.py`
- Create: `patent/tests/test_patent_planning_hot_pool.py`
- Modify: `patent/tests/test_patent_stage1_planning.py`
- Modify: `patent/tests/test_patent_retrieval_service.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Add failing hot-pool tests**

Add tests that prove:

- planning hot lanes create lane-local clients
- snapshot exposes `total_lanes`, `ready_lanes`, `warming_lanes`, `degraded_lanes`
- `PatentRuntime.stage1_pre_answer_and_planning()` actually dispatches through the hot pool when enabled
- stage2 query generation in the retrieval path actually dispatches through the hot pool when enabled
- disabling the hot pool falls back to the existing shared-pool planning path
- health exposes the planning hot-pool component
- bootstrap failure closes the hot pool and its lane-local clients
- lifespan shutdown closes the hot pool and its lane-local clients

- [ ] **Step 2: Run the targeted hot-pool tests and verify failure**

Run:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_planning_hot_pool.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_retrieval_service.py patent/tests/fastapi_contract/test_health_contract.py -q -k "planning_hot_pool or hot_pool"
```

Expected:

- FAIL because the hot pool does not exist yet

- [ ] **Step 3: Implement the planning hot pool**

Implement:

- lane-local clients for planning traffic only
- snapshot/status model
- bootstrap ownership in `app.state`
- runtime injection for planning/query-generation only
- bootstrap-failure cleanup for the hot pool and its lane-local clients
- lifespan shutdown cleanup for the hot pool and its lane-local clients

- [ ] **Step 4: Re-run the targeted hot-pool tests**

Run the same command as Step 2.

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/planning_hot_pool.py patent/server_fastapi/app.py patent/server_fastapi/routers/health.py patent/server/patent/runtime.py patent/tests/test_patent_planning_hot_pool.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_retrieval_service.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat: add patent planning hot pool"
```

## Task 7: Conditional Planning Upstream Gate

**Precondition:** Task 6 landed and lane-readiness contention, not just general latency, is the remaining issue.

**Files:**

- Create: `patent/server/patent/upstream_gate.py`
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/server/patent/runtime.py`
- Create: `patent/tests/test_patent_upstream_gate.py`
- Modify: `patent/tests/test_patent_stage1_planning.py`
- Modify: `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Add failing gate tests**

Add tests that prove:

- gate enforces configured concurrency
- gate can derive effective concurrency from ready lanes
- cancelled waits exit cleanly
- disabling the gate falls back to ungated operation
- stage1 planning enters the gate when enabled
- stage2 query generation enters the gate when enabled
- stage1 and stage2 bypass the gate when it is disabled

- [ ] **Step 2: Run the targeted gate tests and verify failure**

Run:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_upstream_gate.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_retrieval_service.py -q -k "upstream_gate or enters_the_gate or bypass_the_gate"
```

Expected:

- FAIL because the gate does not exist yet

- [ ] **Step 3: Implement the gate**

Implement:

- a narrow planning-only gate
- dynamic limit support derived from ready hot lanes
- cancellation-aware wait exit
- runtime/bootstrap wiring
- live stage1 planning integration
- live stage2 query-generation integration

- [ ] **Step 4: Re-run the targeted gate tests**

Run the same command as Step 2.

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/upstream_gate.py patent/server_fastapi/app.py patent/server/patent/runtime.py patent/tests/test_patent_upstream_gate.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_retrieval_service.py
git commit -m "feat: add patent planning upstream gate"
```

## Task 8: Conditional Warm-Up Scheduler

**Precondition:** Task 6 landed, and either Task 7 landed or measurements still justify warming even without a gate.

**Files:**

- Modify: `patent/config.py`
- Modify: `patent/config.shared.env.example`
- Modify: `patent/server/patent/planning_hot_pool.py`
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/server_fastapi/routers/health.py`
- Modify: `patent/tests/test_patent_planning_hot_pool.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Add failing warm-up tests**

Add tests that prove:

- bootstrap warm starts immediately when enabled
- keepalive warm updates snapshot fields
- active warm windows are honored
- degraded stale lanes can be refreshed
- shutdown stops the scheduler cleanly
- bootstrap failure stops the scheduler cleanly and closes any warm-up-started pool resources

- [ ] **Step 2: Run the targeted warm-up tests and verify failure**

Run:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_planning_hot_pool.py patent/tests/fastapi_contract/test_health_contract.py -q -k "bootstrap_warm or keepalive_warm or warm_window or shutdown_stops or bootstrap_failure"
```

Expected:

- FAIL because warm-up scheduling is not implemented yet

- [ ] **Step 3: Implement the warm-up scheduler**

Implement:

- config-gated bootstrap warm
- scheduled keepalive warm
- active warm window support
- jitter and degraded-lane refresh
- clean shutdown of the scheduler thread(s)
- bootstrap exception cleanup so scheduler threads/resources do not survive a failed app bootstrap
- ownership remains app-managed through the hot-pool resource so bootstrap cleanup and lifespan shutdown both close the same object graph

- [ ] **Step 4: Re-run the targeted warm-up tests**

Run the same command as Step 2.

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add patent/config.py patent/config.shared.env.example patent/server/patent/planning_hot_pool.py patent/server_fastapi/app.py patent/server_fastapi/routers/health.py patent/tests/test_patent_planning_hot_pool.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat: add patent planning hot pool warmup"
```

## Task 9: Final Verification And Handoff

**Files:**

- Reuse all touched files

- [ ] **Step 1: Run the final targeted suite for the phases that actually landed**

Mandatory minimum:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_upstream_config.py patent/tests/test_patent_upstream_http.py patent/tests/test_patent_upstream_transport.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py patent/tests/test_patent_stage4_synthesis.py patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_hybrid_synthesis.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_tabular_service.py -q
```

If conditional phases landed, add:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_planning_hot_pool.py patent/tests/test_patent_upstream_gate.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_retrieval_service.py -q
```

Expected:

- PASS

- [ ] **Step 2: Capture rollout notes**

Record:

- which phases landed
- which phases were intentionally skipped by the Task 5 decision gate
- required restart/redeploy flags
- health fields to inspect after rollout

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "chore: hand off patent upstream reuse rollout"
```
