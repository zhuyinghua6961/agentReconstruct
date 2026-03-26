# Patent Phase 1 Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 `patent` FastAPI service scaffold under `patent/` only, with Gunicorn wrapping, strict patent ask protocol validation, durable-vs-ephemeral orchestration, Redis-based conversation locking and overlay infrastructure, and stub execution paths that satisfy the agreed sync/SSE contracts.

**Architecture:** The service reuses the operational shape of `highThinkingQA` for FastAPI, auth, and authority orchestration, while borrowing Redis bootstrap and helper patterns from `fastQA`. The service remains stateless across instances: `public-service` is the durability owner, Redis is used only for coordination/cache/overlay, and `patent` owns request validation, execution orchestration, and future retrieval extension points. Runnable durable rollout remains gated on external `gateway` and `public-service` changes; this plan only builds the service inside `patent/`.

**Tech Stack:** Python, FastAPI, Gunicorn, Pydantic, pytest, Redis client library, `conda` environment `agent`

---

## Constraints And References

**Hard constraints:**
- Only modify files under `patent/`
- Do not change `gateway/`, `public-service/`, `fastQA/`, or `highThinkingQA/`
- Keep durable rollout behind an internal feature/config gate until external dependencies are implemented elsewhere
- Default durable patent mode to disabled, and require explicit tests proving durable requests stay blocked until the gate is enabled in a safe environment
- Use `conda run -n agent ...` for test and verification commands
- Route pytest cache and temp output into `patent/` only, for example with `PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp`

**Primary references:**
- Spec: `patent/docs/2026-03-25-patent-phase1-service-design.md`
- Protocol overview: `docs/2026-03-24-patentqa-gateway-public-service-protocol.md`
- Field contract: `docs/2026-03-24-patentqa-field-contract.md`
- Reuse patterns only: `highThinkingQA/server_fastapi/`, `highThinkingQA/server/services/`, `fastQA/app/integrations/redis/`

## File Structure Map

**Files to create**
- `patent/pyproject.toml`
- `patent/config.py`
- `patent/config.shared.env.example`
- `patent/scripts/start.sh`
- `patent/scripts/test.sh`
- `patent/scripts/lint.sh`
- `patent/server_fastapi/__init__.py`
- `patent/server_fastapi/app.py`
- `patent/server_fastapi/gunicorn.conf.py`
- `patent/server_fastapi/errors.py`
- `patent/server_fastapi/http.py`
- `patent/server_fastapi/auth/__init__.py`
- `patent/server_fastapi/auth/deps.py`
- `patent/server_fastapi/routers/__init__.py`
- `patent/server_fastapi/routers/ask.py`
- `patent/server_fastapi/routers/health.py`
- `patent/server/__init__.py`
- `patent/server/errors/__init__.py`
- `patent/server/errors/codes.py`
- `patent/server/errors/core.py`
- `patent/server/schemas/__init__.py`
- `patent/server/schemas/request_models.py`
- `patent/server/schemas/response_models.py`
- `patent/server/schemas/authority_models.py`
- `patent/server/runtime/__init__.py`
- `patent/server/runtime/request_context.py`
- `patent/server/runtime/ordered_task_dispatcher.py`
- `patent/server/services/__init__.py`
- `patent/server/services/conversation_authority_client.py`
- `patent/server/services/chat_persistence.py`
- `patent/server/services/redis_client.py`
- `patent/server/services/execution_lock.py`
- `patent/server/services/execution_cache.py`
- `patent/server/services/ask_service.py`
- `patent/server/services/mode_profiles.py`
- `patent/server/patent/__init__.py`
- `patent/server/patent/cache_keys.py`
- `patent/server/patent/result_builder.py`
- `patent/server/patent/pipeline.py`
- `patent/server/patent/executor.py`
- `patent/tests/conftest.py`
- `patent/tests/fastapi_contract/test_ask_contract.py`
- `patent/tests/fastapi_contract/test_health_contract.py`
- `patent/tests/test_conversation_authority_client.py`
- `patent/tests/test_chat_persistence.py`
- `patent/tests/test_redis_runtime.py`
- `patent/tests/test_execution_lock.py`
- `patent/tests/test_execution_cache.py`
- `patent/tests/test_runtime_controls.py`
- `patent/tests/test_patent_executor.py`

**Files to modify**
- `patent/README.md`

## Task 1: Bootstrap Package, Config, And Scripts

**Files:**
- Create: `patent/pyproject.toml`
- Create: `patent/config.py`
- Create: `patent/config.shared.env.example`
- Create: `patent/scripts/start.sh`
- Create: `patent/scripts/test.sh`
- Create: `patent/scripts/lint.sh`
- Modify: `patent/README.md`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Write the failing health/config smoke test**

```python
from server_fastapi.app import create_app


def test_create_app_exposes_patent_runtime_defaults():
    app = create_app()
    assert app.state.service_name == "patent"
    assert "redis" in app.state.component_status
    assert "authority" in app.state.component_status
```

- [ ] **Step 2: Run the test to verify the scaffold is missing**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_health_contract.py::test_create_app_exposes_patent_runtime_defaults -q`
Expected: FAIL with import or module-not-found errors

- [ ] **Step 3: Create package metadata and runtime config**

Implement:
- `pyproject.toml` with FastAPI, Gunicorn, Pydantic, pytest, redis, httpx dependencies
- `config.py` with HTTP, Gunicorn, Redis, and authority settings
- `config.shared.env.example` documenting the env surface
- shell scripts for start/test/lint using `conda run -n agent ...`
- shell scripts must redirect pytest cache/tmp artifacts into `patent/.pytest_cache` and `patent/.tmp`
- README update describing Phase 1 scaffold scope and the external rollout gates

- [ ] **Step 4: Add the minimal app factory and runtime defaults**

Implement `server_fastapi.app:create_app()` with:
- `app.state.service_name = "patent"`
- `component_status` seeded with `redis`, `authority`, and `runtime`
- router registration stub

- [ ] **Step 5: Re-run the health/config smoke test**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_health_contract.py::test_create_app_exposes_patent_runtime_defaults -q`
Expected: PASS

- [ ] **Step 6: Checkpoint**

Record that package bootstrap, config defaults, and patent-only scripts are complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 2: Build Error Core, HTTP Helpers, Auth Dependency, And Health Readiness

**Files:**
- Create: `patent/server/errors/__init__.py`
- Create: `patent/server/errors/codes.py`
- Create: `patent/server/errors/core.py`
- Create: `patent/server_fastapi/errors.py`
- Create: `patent/server_fastapi/http.py`
- Create: `patent/server_fastapi/auth/__init__.py`
- Create: `patent/server_fastapi/auth/deps.py`
- Create: `patent/server_fastapi/routers/__init__.py`
- Create: `patent/server_fastapi/routers/health.py`
- Modify: `patent/server_fastapi/app.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Write failing tests for auth and readiness**

```python
def test_health_returns_503_when_runtime_not_ready():
    ...


def test_durable_patent_auth_requires_authorization_header():
    ...


def test_durable_mode_is_disabled_by_default():
    ...


def test_health_returns_503_when_durable_mode_is_enabled_without_ready_dependencies():
    ...
```

- [ ] **Step 2: Run the failing tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_health_contract.py -q`
Expected: FAIL because health/auth behavior is not implemented

- [ ] **Step 3: Implement the error model and FastAPI exception handlers**

Implement:
- explicit API error type
- patent-specific error codes such as `TOKEN_MISSING`, `PROTOCOL_MISMATCH`, `AUTHORITY_UNAVAILABLE`, `PATENT_BUSY`
- FastAPI exception registration

- [ ] **Step 4: Implement auth dependency and readiness-aware health router**

Implement:
- forwarded auth parsing and durable auth requirement
- `user_id` derivation hook surface
- durable feature gate defaults wired into config and app state
- `/api/health` returning `200` for liveness and `503` for readiness failure when durable mode is enabled but Redis/authority/runtime are not ready
- router registration in the app factory

- [ ] **Step 5: Re-run the health/auth tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_health_contract.py -q`
Expected: PASS

- [ ] **Step 6: Checkpoint**

Record that auth, readiness, and FastAPI error handling are complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 3: Implement Request/Response Schemas And Patent Contract Validation

**Files:**
- Create: `patent/server/schemas/__init__.py`
- Create: `patent/server/schemas/request_models.py`
- Create: `patent/server/schemas/response_models.py`
- Create: `patent/server/schemas/authority_models.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write failing contract tests for request parsing and response shape**

```python
def test_patent_request_rejects_non_kb_only_payload():
    ...


def test_sync_success_shape_matches_patent_contract():
    ...


def test_stream_events_require_seq_and_ts():
    ...
```

- [ ] **Step 2: Run those tests and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL because request/response models do not exist yet

- [ ] **Step 3: Implement strict ingress schemas**

Implement validation for:
- `requested_mode=patent`
- `actual_mode=patent`
- `route=kb_qa`
- `turn_mode=kb_only`
- empty file payload requirements
- `conversation_id` coercion to positive int or `None`
- durable-vs-ephemeral mode classification

- [ ] **Step 4: Implement response and authority helper models**

Implement:
- wrapped sync response model
- SSE event helper models with `seq` and `ts`
- authority request/response helper models matching the design

- [ ] **Step 5: Re-run the contract tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 6: Checkpoint**

Record that the patent protocol schemas are complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 4: Implement The Authority Client And Durable Feature Gate

**Files:**
- Create: `patent/server/services/conversation_authority_client.py`
- Modify: `patent/config.py`
- Modify: `patent/server/schemas/authority_models.py`
- Test: `patent/tests/test_conversation_authority_client.py`

- [ ] **Step 1: Write failing authority client tests**

```python
def test_user_write_uses_patent_mode_contract():
    ...


def test_context_snapshot_uses_patent_query_contract():
    ...


def test_assistant_accept_uses_patent_idempotency_key():
    ...


def test_durable_authority_mode_is_blocked_when_feature_gate_is_off():
    ...
```

- [ ] **Step 2: Run the authority client tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_conversation_authority_client.py -q`
Expected: FAIL because the client is missing

- [ ] **Step 3: Implement the authority client**

Implement:
- request header construction
- user-write call
- context-snapshot call
- assistant-async call
- exact `conversation_id:trace_id:operation` idempotency keys
- durable feature/config gate that makes production durable mode explicit instead of assumed
- a blocked-by-default durable path contract that refuses durable execution when rollout prerequisites are not satisfied locally

- [ ] **Step 4: Re-run the authority client tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_conversation_authority_client.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Record that the authority client and durable feature gate are complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 5: Implement Redis Bootstrap, Conversation Lock, Turn Dedupe, And Overlay Storage

**Files:**
- Create: `patent/server/services/redis_client.py`
- Create: `patent/server/services/execution_lock.py`
- Create: `patent/server/services/execution_cache.py`
- Create: `patent/server/patent/cache_keys.py`
- Modify: `patent/server_fastapi/app.py`
- Test: `patent/tests/test_redis_runtime.py`
- Test: `patent/tests/test_execution_lock.py`
- Test: `patent/tests/test_execution_cache.py`

- [ ] **Step 1: Write failing Redis runtime and lock tests**

```python
def test_bootstrap_redis_sets_component_status():
    ...


def test_conversation_lock_rejects_second_owner():
    ...


def test_turn_dedupe_key_uses_conversation_and_trace():
    ...


def test_pending_overlay_roundtrip_and_expiry():
    ...


def test_atomic_release_rejects_wrong_owner():
    ...


def test_lease_renewal_failure_marks_lock_unusable():
    ...


def test_durable_lock_path_fails_when_redis_unavailable():
    ...


def test_same_turn_retry_converges_on_conversation_and_trace_identity():
    ...


def test_stream_lease_renewal_success_extends_owner_lifetime():
    ...


def test_stream_lease_renewal_failure_forces_terminal_abort():
    ...
```

- [ ] **Step 2: Run the Redis-related tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_redis_runtime.py patent/tests/test_execution_lock.py patent/tests/test_execution_cache.py -q`
Expected: FAIL because Redis helpers are missing

- [ ] **Step 3: Implement Redis bootstrap and key builders**

Implement:
- config-driven Redis bindings
- URL redaction
- app-state component status
- namespaced keys for conversation lock, turn dedupe, inflight state, overlay, and future caches

- [ ] **Step 4: Implement conversation lock semantics**

Implement:
- conversation-level lock keyed by `conversation_id`
- owner token acquisition
- atomic compare-and-delete release
- lease renewal hook surface for streams
- explicit failure result when lease renewal is lost mid-stream
- clear distinction between `busy` and `redis unavailable`

- [ ] **Step 5: Implement execution cache and overlay helpers**

Implement:
- per-turn dedupe helper keyed by `conversation_id + trace_id`
- pending-assistant overlay put/get/delete helpers
- convergence cleanup helper keyed by assistant trace id
- overlay cleanup behavior that drops stale overlay once authority snapshot converges

- [ ] **Step 6: Re-run the Redis-related tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_redis_runtime.py patent/tests/test_execution_lock.py patent/tests/test_execution_cache.py -q`
Expected: PASS

- [ ] **Step 7: Checkpoint**

Record that Redis bootstrap, locking, dedupe, and overlay helpers are complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 6: Implement Chat Persistence Orchestration And Context Merge Logic

**Files:**
- Create: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/execution_cache.py`
- Test: `patent/tests/test_chat_persistence.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_durable_flow_orders_user_write_snapshot_execute_accept():
    ...


def test_assistant_accept_failure_blocks_success():
    ...


def test_overlay_merges_when_authority_snapshot_lags():
    ...


def test_duplicate_finalization_is_not_reported_twice_for_same_turn():
    ...


def test_overlay_cleanup_runs_after_authority_converges():
    ...


def test_retry_after_user_write_before_accept_converges_on_same_turn():
    ...


def test_distinct_trace_same_conversation_is_rejected_while_inflight():
    ...


def test_missing_trace_id_is_generated_once_and_reused_for_same_turn():
    ...
```

- [ ] **Step 2: Run the orchestration tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_chat_persistence.py -q`
Expected: FAIL because chat orchestration is not implemented

- [ ] **Step 3: Implement durable-vs-ephemeral orchestration**

Implement:
- durable feature gate behavior
- durable flow ordering: conversation lock -> turn dedupe/inflight claim -> user write -> snapshot read -> execution -> assistant accept -> overlay update -> cleanup -> release
- duplicate-finalization protection for same `conversation_id + trace_id`
- same-trace retry convergence and distinct-trace same-conversation rejection while inflight
- one-time generated `trace_id` reuse when the request arrives without a trace id
- ephemeral flow skipping authority calls
- context merge between authority snapshot and best-effort overlay
- overlay cleanup once authority converges on the assistant trace id
- explicit failure when durable mode is disabled or prerequisites are not satisfied

- [ ] **Step 4: Re-run the orchestration tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_chat_persistence.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Record that chat persistence orchestration is complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.


## Task 7: Implement Runtime Request Context And Concurrency Controls

**Files:**
- Create: `patent/server/runtime/__init__.py`
- Create: `patent/server/runtime/request_context.py`
- Create: `patent/server/runtime/ordered_task_dispatcher.py`
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/server_fastapi/routers/health.py`
- Test: `patent/tests/test_runtime_controls.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Write failing runtime control tests**

```python
def test_stream_slot_limit_rejects_overload():
    ...


def test_runtime_releases_stream_slot_after_completion():
    ...


def test_health_exposes_configured_concurrency_state():
    ...
```

- [ ] **Step 2: Run the runtime control tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_runtime_controls.py patent/tests/fastapi_contract/test_health_contract.py -q`
Expected: FAIL because runtime controls are not implemented

- [ ] **Step 3: Implement request context and dispatcher utilities**

Implement:
- trace-id load/generate/reuse helpers
- ordered task dispatcher or equivalent local runtime coordination primitive
- per-process ask-stream slot limiter
- app-state exposure of configured concurrency controls

- [ ] **Step 4: Wire runtime controls into the app factory and health output**

Implement:
- runtime bootstrap in `server_fastapi.app`
- overload rejection path
- health exposure of current concurrency configuration and availability state

- [ ] **Step 5: Re-run the runtime control tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_runtime_controls.py patent/tests/fastapi_contract/test_health_contract.py -q`
Expected: PASS

- [ ] **Step 6: Checkpoint**

Record that runtime request context and concurrency controls are complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 8: Implement Stub Patent Executor, Result Builder, And Ask Service

**Files:**
- Create: `patent/server/services/ask_service.py`
- Create: `patent/server/services/mode_profiles.py`
- Create: `patent/server/patent/__init__.py`
- Create: `patent/server/patent/result_builder.py`
- Create: `patent/server/patent/pipeline.py`
- Create: `patent/server/patent/executor.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write failing executor and stream sequencing tests**

```python
def test_stub_executor_returns_deterministic_patent_payload():
    ...


def test_stream_done_is_emitted_only_after_accept_success():
    ...
```

- [ ] **Step 2: Run the executor and contract tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL because ask service and executor are missing

- [ ] **Step 3: Implement the patent execution boundary**

Implement:
- stub mode profile
- deterministic executor output
- step builder and final result builder
- ask service that emits sync payloads and ordered SSE frames with `seq` and `ts`
- service-layer handling for timeout/runtime errors

- [ ] **Step 4: Re-run the executor and contract tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Record that the stub patent execution service is complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 9: Implement Ask Routers, App Wiring, And Gunicorn Wrapper

**Files:**
- Create: `patent/server_fastapi/routers/ask.py`
- Create: `patent/server_fastapi/gunicorn.conf.py`
- Modify: `patent/server_fastapi/routers/__init__.py`
- Modify: `patent/server_fastapi/app.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Write failing route-level tests for path aliases and ask orchestration**

```python
def test_patent_route_aliases_all_dispatch_to_patent_ask():
    ...


def test_ephemeral_sync_ask_returns_success_without_authority_calls():
    ...


def test_durable_stream_busy_conversation_returns_busy_error():
    ...


def test_durable_request_is_blocked_when_rollout_gate_is_off():
    ...


def test_stream_renewal_failure_emits_terminal_error_not_done():
    ...


def test_ephemeral_request_still_runs_when_durable_redis_path_is_unavailable():
    ...
```

- [ ] **Step 2: Run the route-level tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py -q`
Expected: FAIL because routes are not fully wired

- [ ] **Step 3: Implement route wiring and Gunicorn wrapper**

Implement:
- all ask/ask_stream route aliases
- auth dependency integration
- app-state hook wiring for ask service, authority client, and Redis helpers
- route-level durable blocking behavior when rollout gate is off
- Gunicorn config mirroring the `highThinkingQA` deployment pattern

- [ ] **Step 4: Re-run the route-level tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Record that ask routes and Gunicorn wiring are complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 10: Full Patent-Only Verification

**Files:**
- Test: `patent/tests/`
- Verify: `patent/scripts/test.sh`
- Verify: `patent/scripts/start.sh`

- [ ] **Step 1: Run the focused patent test suite**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests -q`
Expected: PASS

- [ ] **Step 1.1: Verify durable mode is blocked by default**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py -k "durable_request_is_blocked_when_rollout_gate_is_off or durable_mode_is_disabled_by_default or health_returns_503_when_durable_mode_is_enabled_without_ready_dependencies" -q`
Expected: PASS

- [ ] **Step 2: Run a compile/import sanity check**

Run: `conda run -n agent python -m compileall patent`
Expected: PASS with no syntax errors

- [ ] **Step 3: Run a local app bootstrap check**

Run: `conda run -n agent python -c "from server_fastapi.app import create_app; app = create_app(); print(app.state.service_name)"`
Expected: `patent`

- [ ] **Step 4: Verify the script entrypoints**

Run: `bash patent/scripts/test.sh`
Expected: patent-only tests run under `conda` `agent`, with cache/tmp artifacts confined to `patent/.pytest_cache` and `patent/.tmp`

- [ ] **Step 5: Checkpoint**

Record that patent-only verification is complete. Do not commit in this plan, because `.git/` is outside the patent-only write scope.

## Task 11: Post-Implementation Review Notes

**Files:**
- Verify: `patent/docs/2026-03-25-patent-phase1-service-design.md`
- Verify: `patent/docs/2026-03-25-patent-phase1-service-implementation-plan.md`

- [ ] **Step 1: Compare implementation against the approved design**

Check:
- durable rollout stays feature-gated and disabled by default
- no patent-local transcript ownership exists
- conversation-level lock and turn-level dedupe/inflight claim are both present in the durable flow
- atomic lock release and lease-renewal failure behavior are covered by tests
- same-trace retry convergence and distinct-trace same-conversation rejection are covered by tests
- stream `done` waits for assistant accept
- health readiness returns `503` when durable mode cannot safely run

- [ ] **Step 2: Document any still-open rollout dependencies in the final handoff**

Must mention:
- gateway patent direct-persistence disablement
- public-service authority schema/allowlist extension
- gateway preserves forwarded auth and trace behavior expected by patent
- gateway file/mixed compatibility rewrite
- gateway keeps file/mixed patent turns on compatibility routing until patent explicitly owns them
- metadata policy for compatibility-routed patent file turns

- [ ] **Step 3: Final checkpoint if documentation changed during verification**

Record the final handoff notes inside `patent/docs/` only. Do not commit in this plan, because `.git/` is outside the patent-only write scope.
