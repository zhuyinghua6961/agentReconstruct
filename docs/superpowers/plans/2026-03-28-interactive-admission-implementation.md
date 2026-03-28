# Interactive Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Redis-backed interactive admission so the stack can hold a configurable number of live QA executions, queue overflow requests immediately, and preserve current direct streaming behavior after admission.

**Architecture:** `gateway` remains the request entry point, route-resolution owner, and queued-request API owner. Admission is implemented as a Redis-backed control-plane layer with shared slot leases, persisted execution snapshots, explicit queued-request state, and delayed attach for queued streams. `fastQA` and `highThinkingQA` remain the execution backends; their existing process-local limits stay as local safety rails only, while cluster-wide concurrency moves into Redis-coordinated admission ownership.

**Tech Stack:** FastAPI, Redis, Redis Streams and/or lease keys for admission control, pytest, existing gateway proxy flow, existing QA backend ask and ask-stream contracts.

---

## Scope Guardrails

- This plan implements the current kickoff override from [interactive-admission-kickoff-decisions.md](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-27-interactive-admission-kickoff-decisions.md).
- This plan does not start the broader background-stream MQ rollout first.
- This plan does not replace already-admitted token streaming with queue polling.
- This plan does not make `patent` generally executable. `patent` remains high-tier only when readiness says the deployed path is real and authority-compatible.
- This plan assumes the first production default is:
  - global ceiling `10`
  - `fast == patent` high tier
  - `thinking` low tier with starvation protection
  - queued request TTL `15 minutes`
  - post-admit attach retention `10 minutes`

## Deployment Parameter Rule

Interactive admission parameters must be externalized as environment variables.

Implementation must not rely on local shell-only defaults or hardcoded Docker image values for:

- Redis connection settings used by `gateway`
- global admission ceiling
- backend-specific admission ceilings
- queued retention
- post-admit attach retention
- dispatcher role enablement
- worker role selection

For container deployment, these values should be supplied through Docker Compose `environment` or `env_file`, consistent with [2026-03-25-docker-deployment-guide.md](/home/cqy/worktrees/highThinking/docs/2026-03-25-docker-deployment-guide.md).

## Docker Topology Requirement

If admission is deployed in Docker or Compose, `gateway` must be split into separate runtime roles:

- `gateway-web`
- `gateway-admission-worker`

They may share the same image and env file, but they must not run as the same container process by default.

This preserves the core rule that web-serving `gunicorn` workers remain producer-only while the admission dispatcher runs as a dedicated worker role.

## File Structure Map

### Existing files that are the main integration points

- `gateway/app/core/config.py`
- `gateway/app/integrations/redis/__init__.py`
- `gateway/app/integrations/redis/keys.py`
- `gateway/app/integrations/redis/service.py`
- `gateway/app/main.py`
- `gateway/app/models/ask.py`
- `gateway/app/routers/qa.py`
- `gateway/app/routers/health.py`
- `gateway/app/services/backend_registry.py`
- `gateway/app/services/proxy.py`
- `gateway/app/services/route_decision.py`
- `gateway/docs/gateway_forwarding_protocol.md`
- `gateway/pyproject.toml`
- `gateway/scripts/start_gunicorn.sh`
- `gateway/scripts/status_gunicorn.sh`
- `gateway/scripts/stop_gunicorn.sh`
- `docs/2026-03-25-docker-deployment-guide.md`
- `gateway/tests/test_config.py`
- `gateway/tests/test_health.py`
- `gateway/tests/test_qa_proxy.py`
- `gateway/tests/test_route_decision.py`
- `fastQA/app/core/config.py`
- `fastQA/app/main.py`
- `fastQA/app/routers/health.py`
- `fastQA/tests/test_health.py`
- `highThinkingQA/config.py`
- `highThinkingQA/server_fastapi/routers/ask.py`
- `highThinkingQA/server_fastapi/routers/health.py`
- `highThinkingQA/tests/fastapi_migration/test_fastapi_ask_contract.py`
- `highThinkingQA/tests/test_env_loader.py`
- `scripts/_service_common.sh`
- `scripts/start_all.sh`
- `scripts/status_all.sh`
- `scripts/stop_all.sh`

### New files to create

- `gateway/app/services/execution_admission.py`
- `gateway/app/services/execution_event_relay.py`
- `gateway/app/services/execution_queue_status.py`
- `gateway/scripts/start_admission_worker.sh`
- `gateway/scripts/status_admission_worker.sh`
- `gateway/scripts/stop_admission_worker.sh`
- `gateway/tests/test_execution_admission.py`
- `gateway/tests/test_execution_event_relay.py`
- `gateway/tests/test_execution_queue_status.py`
- `gateway/tests/test_redis_runtime.py`

### Design responsibilities

- `execution_admission.py`: queue-tier mapping, slot lease orchestration, queued snapshot contract, readiness checks, and dispatcher-facing admission logic
- `execution_queue_status.py`: queued-request state persistence, status reads, cancellation rules, expiry handling, and JSON result materialization lookup
- `execution_event_relay.py`: delayed-attach stream frame storage, sequence numbering, replay window rules, and cross-instance resume semantics
- `gateway/app/integrations/redis/*`: gateway Redis dependency bootstrap, prefixed key helpers, and shared Redis runtime bindings for admission ownership
- `gateway/app/routers/qa.py`: immediate-admit vs queued-admit branching, queued metadata response, attach path handoff
- `gateway/app/services/proxy.py`: integration point for immediate execution and delayed attach relay emission
- `gateway/app/models/ask.py`: request and queued-response schema additions
- `gateway/scripts/*admission_worker.sh` plus top-level scripts: dedicated dispatcher worker entrypoint and lifecycle management outside normal web workers
- `gateway/app/routers/health.py` plus scripts: operational visibility for dispatcher role, backlog, slot counts, and retention policy

## Task Ordering

Tasks are ordered to produce a working slice after each checkpoint:

1. Admission foundation and config
2. Dispatcher and queued-state ownership
3. Delayed attach and result retrieval
4. Multi-instance visibility, docs, and rollout verification

---

### Task 1: Admission Foundation And Config Surface

**Files:**
- Create: `gateway/tests/test_execution_admission.py`
- Create: `gateway/tests/test_redis_runtime.py`
- Create: `gateway/app/integrations/redis/__init__.py`
- Create: `gateway/app/integrations/redis/keys.py`
- Create: `gateway/app/integrations/redis/service.py`
- Create: `gateway/scripts/start_admission_worker.sh`
- Create: `gateway/scripts/status_admission_worker.sh`
- Create: `gateway/scripts/stop_admission_worker.sh`
- Modify: `gateway/app/core/config.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/app/services/backend_registry.py`
- Modify: `gateway/pyproject.toml`
- Modify: `gateway/scripts/start_gunicorn.sh`
- Modify: `gateway/scripts/status_gunicorn.sh`
- Modify: `gateway/scripts/stop_gunicorn.sh`
- Modify: `docs/2026-03-25-docker-deployment-guide.md`
- Modify: `scripts/_service_common.sh`
- Modify: `scripts/start_all.sh`
- Modify: `scripts/status_all.sh`
- Modify: `scripts/stop_all.sh`
- Modify: `fastQA/app/core/config.py`
- Modify: `highThinkingQA/config.py`
- Modify: `fastQA/tests/test_health.py`
- Modify: `highThinkingQA/tests/fastapi_migration/test_fastapi_ask_contract.py`
- Modify: `gateway/tests/test_config.py`

- [ ] **Step 1: Write failing tests for admission config and scheduler role boundaries**

Test cases to add:
- gateway runtime can bootstrap Redis bindings with service-local prefixing
- gateway package declares the Redis dependency needed for shared admission state
- `interactive_execution_max_concurrent` defaults to `10` and is operator-overridable
- backend-specific admission ceilings exist for `fast_or_patent` and `thinking`
- gateway admission env config can be supplied without relying on local shell launch state
- local backend limits are not treated as the global concurrency source of truth
- `gateway` web startup does not automatically self-elect into the dispatcher role
- dedicated admission worker startup exists separately from `gateway` web startup
- `fastQA` and `highThinkingQA` health or contract surfaces still expose their local safety-rail limits distinctly from global admission limits
- patent readiness remains conditional rather than assumed executable by config alone

Run:
```bash
pytest gateway/tests/test_redis_runtime.py gateway/tests/test_execution_admission.py gateway/tests/test_config.py fastQA/tests/test_health.py highThinkingQA/tests/fastapi_migration/test_fastapi_ask_contract.py -v
```
Expected: FAIL because the admission config surface and scheduler-role separation do not exist yet.

- [ ] **Step 2: Implement the config and process-role foundation**

Implementation notes:
- Add gateway Redis runtime support before admission logic exists.
- Follow the repository pattern of service-local Redis key helpers rather than hardcoding raw keys in the dispatcher.
- Add explicit config for:
  - global ceiling `interactive_execution_max_concurrent`
  - backend-specific ceilings for `fast_or_patent` and `thinking`
  - queued retention and attach retention defaults
  - dispatcher-role enablement
- Add a dedicated admission worker entrypoint under `gateway/scripts/` and top-level lifecycle wiring for local development and ops scripts.
- Document the Compose deployment expectation that `gateway-web` and `gateway-admission-worker` run as separate services using the same image plus different role env.
- Keep web API startup producer-only by default.
- Make the future dispatcher role explicit in config and startup wiring, but do not yet implement the full queue lifecycle here.
- Preserve the distinction between local backend limits and global admission limits in code comments, config naming, and health outputs.

- [ ] **Step 3: Run the foundation config tests**

Run:
```bash
pytest gateway/tests/test_redis_runtime.py gateway/tests/test_execution_admission.py gateway/tests/test_config.py fastQA/tests/test_health.py highThinkingQA/tests/fastapi_migration/test_fastapi_ask_contract.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add gateway/tests/test_redis_runtime.py gateway/tests/test_execution_admission.py gateway/app/integrations/redis/__init__.py gateway/app/integrations/redis/keys.py gateway/app/integrations/redis/service.py gateway/scripts/start_admission_worker.sh gateway/scripts/status_admission_worker.sh gateway/scripts/stop_admission_worker.sh gateway/app/core/config.py gateway/app/main.py gateway/app/services/backend_registry.py gateway/pyproject.toml gateway/scripts/start_gunicorn.sh gateway/scripts/status_gunicorn.sh gateway/scripts/stop_gunicorn.sh docs/2026-03-25-docker-deployment-guide.md scripts/_service_common.sh scripts/start_all.sh scripts/status_all.sh scripts/stop_all.sh fastQA/app/core/config.py highThinkingQA/config.py fastQA/tests/test_health.py highThinkingQA/tests/fastapi_migration/test_fastapi_ask_contract.py gateway/tests/test_config.py gateway/tests/test_execution_admission.py
git commit -m "feat: add interactive admission config foundations"
```

### Task 2: Admission Dispatcher And Queued State Ownership

**Files:**
- Create: `gateway/app/services/execution_admission.py`
- Create: `gateway/app/services/execution_queue_status.py`
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/app/models/ask.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/services/proxy.py`
- Modify: `gateway/tests/test_execution_admission.py`
- Modify: `gateway/tests/test_qa_proxy.py`
- Modify: `gateway/tests/test_route_decision.py`
- Create: `gateway/tests/test_execution_queue_status.py`

- [ ] **Step 1: Write failing tests for immediate admit, queued admit, and persisted snapshot rules**

Test cases to add:
- requests below the current ceiling are admitted immediately and stay on the current direct execution path
- requests above the current ceiling return queued metadata immediately instead of waiting in-process
- queued requests expose a dedicated status read path by `request_id`
- queued requests expose a dedicated cancel path while still cancellable
- queued execution records retain `request_id`, `trace_id`, `conversation_id`, `user_id`, `requested_mode`, `actual_mode`, `route`, `target_backend`, `backend_capacity_key`, `transport_kind`, `enqueued_at`, and `execution_snapshot`
- queued requests execute later from the persisted snapshot rather than recomputing route or file selection
- scheduler tiering uses `actual_mode`, not merely `requested_mode`
- backend-specific ceilings can block `thinking` even when the global total still has room
- patent readiness failure prevents enqueue or admission budget consumption
- provisional admission followed by backend busy or readiness failure releases the slot and requeues or fails deterministically
- queued requests are cancellable while still in queue

Run:
```bash
pytest gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py -v
```
Expected: FAIL because queued-state ownership and dispatcher behavior do not exist yet.

- [ ] **Step 2: Implement the dispatcher and queue-state contract**

Implementation notes:
- `gateway` must finish route decision before building the queued snapshot.
- Persist a normalized execution snapshot or a stable reference to it before delayed execution is possible.
- Admission validity requires both the global ceiling and the backend-specific ceiling.
- Keep `requested_mode` and `actual_mode` together throughout queue storage and downstream authority callbacks.
- Add explicit queued-request APIs for:
  - status read
  - queue-phase cancellation
- Readiness must be checked before enqueue or admit, and revalidated again at admission time after long queue waits.
- Cancellation support in this task is queue-phase only.
- Do not yet implement delayed stream relay frames here beyond the status or stub hooks needed for the next task.

- [ ] **Step 3: Run the dispatcher and queued-state tests**

Run:
```bash
pytest gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add gateway/app/services/execution_admission.py gateway/app/services/execution_queue_status.py gateway/app/routers/qa.py gateway/app/models/ask.py gateway/app/services/route_decision.py gateway/app/services/proxy.py gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py
git commit -m "feat: add queued interactive admission ownership"
```

### Task 3: Delayed Attach Relay And JSON Result Retrieval

**Files:**
- Create: `gateway/app/services/execution_event_relay.py`
- Modify: `gateway/app/services/proxy.py`
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/app/models/ask.py`
- Modify: `gateway/docs/gateway_forwarding_protocol.md`
- Modify: `gateway/tests/test_qa_proxy.py`
- Create: `gateway/tests/test_execution_event_relay.py`
- Modify: `gateway/tests/test_execution_queue_status.py`

- [ ] **Step 1: Write failing tests for delayed attach and resume semantics**

Test cases to add:
- queued stream requests return attach metadata instead of hanging the original request
- immediate-admit stream requests still use current direct passthrough behavior
- queued status responses expose attach metadata only when the request is attachable
- delayed-attach relay frames carry monotonic sequence numbers
- reconnecting clients can resume from the last acknowledged sequence
- relay-backed attach works across instances within the configured attach retention window
- delayed JSON requests expose terminal result fetch by `request_id`
- completed queued requests retain terminal frames or result materialization through the configured retention window
- attach expiry or missing relay state produces deterministic terminal status rather than silent hangs

Run:
```bash
pytest gateway/tests/test_execution_event_relay.py gateway/tests/test_execution_queue_status.py gateway/tests/test_qa_proxy.py -v
```
Expected: FAIL because delayed attach relay and delayed JSON retrieval do not exist yet.

- [ ] **Step 2: Implement delayed attach and result retrieval**

Implementation notes:
- Keep current direct backend streaming untouched for immediate-admit flows.
- Use `request_id` as the join key for delayed attach.
- Relay frames must be stored with monotonic sequence numbers.
- Resume semantics must use "last acknowledged sequence" rather than restarting from frame zero.
- JSON requests should use terminal result retrieval instead of the stream relay.
- Respect the kickoff retention defaults:
  - queued retention `15 minutes`
  - post-admit attach retention `10 minutes`

- [ ] **Step 3: Run the delayed attach and retrieval tests**

Run:
```bash
pytest gateway/tests/test_execution_event_relay.py gateway/tests/test_execution_queue_status.py gateway/tests/test_qa_proxy.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add gateway/app/services/execution_event_relay.py gateway/app/services/proxy.py gateway/app/routers/qa.py gateway/app/models/ask.py gateway/docs/gateway_forwarding_protocol.md gateway/tests/test_execution_event_relay.py gateway/tests/test_execution_queue_status.py gateway/tests/test_qa_proxy.py
git commit -m "feat: add delayed attach lifecycle for queued requests"
```

### Task 4: Multi-Instance Verification, Health Visibility, And Runbook Sync

**Files:**
- Modify: `gateway/app/routers/health.py`
- Modify: `highThinkingQA/server_fastapi/routers/health.py`
- Modify: `gateway/tests/test_health.py`
- Modify: `highThinkingQA/tests/test_env_loader.py`
- Modify: `scripts/start_all.sh`
- Modify: `scripts/status_all.sh`
- Modify: `scripts/stop_all.sh`
- Modify: `docs/2026-03-25-redis-mq-architecture-spec.md`
- Modify: `docs/superpowers/plans/2026-03-25-redis-mq-rollout.md`
- Modify: `gateway/tests/test_execution_admission.py`
- Modify: `gateway/tests/test_execution_queue_status.py`
- Modify: `gateway/tests/test_execution_event_relay.py`

- [ ] **Step 1: Write failing tests and checks for operational visibility**

Checks to add:
- health or status exposes the configured global ceiling
- health or status exposes backend-specific admission ceilings
- health or status exposes queue backlog, admitted count, active slot leases, and oldest queued age
- health or status exposes queued retention and attach retention policy
- status scripts show whether the dispatcher role is enabled
- docs explain why multiple `gunicorn` workers do not multiply the global limit
- docs explain how Docker Compose should inject admission env vars and split `gateway-web` from `gateway-admission-worker`
- docs explain rollback order for queued admission without breaking already-admitted streams

Run:
```bash
pytest gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_execution_event_relay.py gateway/tests/test_health.py highThinkingQA/tests/test_env_loader.py -v
```
Expected: FAIL or remain incomplete until visibility and runbook wiring are added.

- [ ] **Step 2: Implement multi-instance verification and docs sync**

Implementation notes:
- Keep local backend limits visible as local safety rails distinct from the global admission ceiling.
- Surface enough queue metrics to diagnose backlog and slot leakage.
- Update the main MQ spec and rollout plan so they point to the executed kickoff path and final operator-visible defaults.
- Keep script changes limited to visibility and process-role orchestration needed for admission.

- [ ] **Step 3: Run the admission verification batch**

Run:
```bash
pytest gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_execution_event_relay.py gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py gateway/tests/test_config.py gateway/tests/test_health.py fastQA/tests/test_health.py highThinkingQA/tests/test_env_loader.py highThinkingQA/tests/fastapi_migration/test_fastapi_ask_contract.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add gateway/app/routers/health.py highThinkingQA/server_fastapi/routers/health.py gateway/tests/test_health.py highThinkingQA/tests/test_env_loader.py scripts/start_all.sh scripts/status_all.sh scripts/stop_all.sh docs/2026-03-25-redis-mq-architecture-spec.md docs/superpowers/plans/2026-03-25-redis-mq-rollout.md gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_execution_event_relay.py
git commit -m "docs: finalize interactive admission runbook and visibility"
```

---

## Final Verification Batch

After Task 4, run the full admission-focused batch once more from a clean working tree:

```bash
pytest gateway/tests/test_execution_admission.py gateway/tests/test_execution_queue_status.py gateway/tests/test_execution_event_relay.py gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py gateway/tests/test_config.py gateway/tests/test_health.py fastQA/tests/test_health.py highThinkingQA/tests/test_env_loader.py highThinkingQA/tests/fastapi_migration/test_fastapi_ask_contract.py -v
```

Expected: PASS.

## Recommended Commit Boundaries

- One commit per task.
- Do not mix delayed-attach relay work into the admission foundation commit.
- Keep docs and operational visibility isolated in the last task.
- If a task broadens into background-stream MQ work, stop and split it back out.

## Handoff Notes For Implementers

- Read [interactive-admission-kickoff-decisions.md](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-03-27-interactive-admission-kickoff-decisions.md) before touching code.
- Re-check [2026-03-25-redis-mq-architecture-spec.md](/home/cqy/worktrees/highThinking/docs/2026-03-25-redis-mq-architecture-spec.md) for the authoritative queue payload and multi-instance constraints.
- Preserve current route resolution, file selection, and immediate-admit streaming semantics.
- If an implementation path tries to solve cluster concurrency with only per-process semaphores or `gunicorn` worker counts, stop and revise the design.
- If an implementation path starts requiring background-stream rollout prerequisites that are unrelated to admission, stop and surface that dependency explicitly instead of quietly expanding scope.
