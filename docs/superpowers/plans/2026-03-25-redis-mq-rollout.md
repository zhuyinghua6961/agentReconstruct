# Redis MQ Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce Redis Streams based MQ for the stack's durable background work while keeping user-turn writes, routing, and interactive ask/SSE behavior synchronous.

**Architecture:** `public-service` is the long-term authority for conversation, upload, file, and document truth. The rollout starts by standardizing Redis stream helpers and moving authority-owned async flows in `public-service` onto Streams, then migrates only `highThinkingQA` background ingest orchestration and `fastQA` best-effort prewarm. `gateway` remains a thin synchronous proxy and may only gain an optional audit stream after the business flows are stable. Web `gunicorn` processes stay producer-only by default; MQ consumers must run in explicit worker processes or worker deployments.

**Tech Stack:** FastAPI, Redis Streams, Redis consumer groups, existing Redis key helpers, MySQL-backed authority state, pytest, service-to-service HTTP, existing worker/runtime startup hooks.

---

## Scope Split

This spec spans multiple subsystems, so implementation should be executed as one rollout program with four workstreams:

1. Shared Redis stream primitives and rollout flags
2. `public-service` authority async streams
3. `highThinkingQA` background ingest only, plus temporary legacy bridge handling where necessary
4. `fastQA` prewarm and optional `gateway` audit

Do not merge these into one giant branch without checkpoints. Each completed task below should leave the repo in a buildable, testable state.

## Non-Goals

- Do not move user-turn writes behind MQ.
- Do not move `gateway` routing, clarification, file-context resolution, or SSE passthrough behind MQ.
- Do not turn `fastQA` or `highThinkingQA` interactive `ask` / `ask_stream` into queue jobs.
- Do not introduce `highThinkingQA` as the long-term owner of file-QA, upload truth, file lifecycle truth, or document truth.
- Do not introduce Redis Pub/Sub as the durability mechanism for these flows.

## Shadow Mode Rule

For every new stream producer or worker in this plan, `shadow mode` means:

- producers emit the real versioned payload to Redis Streams, but the legacy business path remains authoritative
- consumers validate, materialize into isolated parity checks, or run side-by-side without removing the legacy path yet
- messages are still fully acknowledged or dead-lettered according to their own worker rules; `shadow` does not mean silently leaking pending entries
- cutover can happen only after payload parity, idempotency behavior, backlog metrics, and rollback toggles are all verified

## Deployment Topology Rule

- Web `gunicorn` worker processes are producer-only by default.
- MQ consumers must not start implicitly from generic FastAPI app startup or lifespan hooks, because those execute once per `gunicorn` worker process.
- Each MQ worker role needs its own explicit process role flag and startup path, or its own dedicated deployment.
- `public-service` assistant/json-sync consumers, `highThinkingQA` ingest, and `fastQA` prewarm should all be operable independently from the web API processes.
- `highThinkingQA` ingest additionally requires a single-active lease or leader guard; consumer-group membership alone is not enough because the service still preserves a single-running-job model.

## File Structure Map

### Existing files that are the main integration points

- `public-service/backend/app/integrations/redis/keys.py`
- `public-service/backend/app/integrations/redis/service.py`
- `public-service/backend/app/core/config.py`
- `public-service/backend/app/core/runtime.py`
- `public-service/backend/app/modules/conversation/internal_api.py`
- `public-service/backend/app/modules/conversation/authority_schemas.py`
- `public-service/backend/app/modules/conversation/service.py`
- `public-service/backend/app/modules/conversation/assistant_inbox.py`
- `public-service/backend/app/modules/conversation/outbox.py`
- `public-service/backend/app/modules/conversation/outbox_worker.py`
- `public-service/backend/app/modules/conversation/upload_processing_worker.py`
- `public-service/backend/tests/test_conversation_authority_api.py`
- `public-service/backend/tests/test_conversation_authority_integration.py`
- `public-service/backend/tests/test_conversation_assistant_inbox.py`
- `public-service/backend/tests/test_conversation_module.py`
- `public-service/backend/tests/test_live_public_service_integration.py`
- `fastQA/app/integrations/redis/keys.py`
- `fastQA/app/integrations/redis/service.py`
- `fastQA/app/core/config.py`
- `fastQA/app/core/runtime.py`
- `fastQA/app/services/chat_persistence.py`
- `fastQA/app/services/stream_contract.py`
- `fastQA/app/modules/storage/upload_materializer.py`
- `fastQA/tests/test_stream_contract.py`
- `fastQA/tests/test_upload_materializer.py`
- `fastQA/tests/test_chat_persistence.py`
- `fastQA/tests/test_redis_helpers.py`
- `fastQA/tests/test_redis_runtime.py`
- `highThinkingQA/server/services/redis_client.py`
- `highThinkingQA/server/services/chat_persistence.py`
- `highThinkingQA/server/services/ingest_service.py`
- `highThinkingQA/server/services/conversation/chat_json_outbox_worker.py`
- `highThinkingQA/server_fastapi/app.py`
- `highThinkingQA/server_fastapi/routers/ingest.py`
- `highThinkingQA/tests/test_chat_persistence.py`
- `highThinkingQA/tests/test_conversation_authority_client.py`
- `highThinkingQA/tests/test_background_persistence_dispatcher.py`
- `highThinkingQA/tests/test_chat_json_store.py`
- `gateway/app/core/config.py`
- `gateway/app/main.py`
- `gateway/app/services/route_decision.py`
- `gateway/app/services/conversation_persistence.py`
- `gateway/app/routers/qa.py`
- `gateway/tests/test_route_decision.py`
- `gateway/tests/test_qa_proxy.py`
- `gateway/tests/test_config.py`

### New files to create

- `public-service/backend/app/integrations/redis/streams.py`
- `public-service/backend/app/modules/conversation/stream_contracts.py`
- `public-service/backend/app/modules/conversation/stream_publishers.py`
- `public-service/backend/app/modules/conversation/assistant_stream_worker.py`
- `public-service/backend/app/modules/conversation/chat_json_stream_worker.py`
- `public-service/backend/tests/test_conversation_stream_contracts.py`
- `public-service/backend/tests/test_conversation_stream_workers.py`
- `fastQA/app/modules/storage/prewarm_streams.py`
- `fastQA/tests/test_prewarm_streams.py`
- `highThinkingQA/server/services/ingest_streams.py`
- `highThinkingQA/tests/test_ingest_streams.py`
- `gateway/app/services/route_audit_stream.py`
- `gateway/tests/test_route_audit_stream.py`

### Existing files likely to modify

- `public-service/backend/app/main.py`
- `public-service/backend/app/modules/conversation/__init__.py`
- `public-service/backend/app/modules/conversation/schemas.py`
- `public-service/backend/app/modules/conversation/repository.py`
- `fastQA/app/main.py`
- `highThinkingQA/server/runtime/ordered_task_dispatcher.py`
- `highThinkingQA/server/tools/run_chat_json_outbox_worker.py`
- `gateway/app/services/__init__.py`

---

## Phase 0: Foundations And Safety Rails

### Task 1: Shared Redis Stream Primitives And Rollout Flags

**Files:**
- Create: `public-service/backend/app/integrations/redis/streams.py`
- Modify: `public-service/backend/app/integrations/redis/keys.py`
- Modify: `public-service/backend/app/integrations/redis/service.py`
- Modify: `public-service/backend/app/core/config.py`
- Modify: `fastQA/app/integrations/redis/keys.py`
- Modify: `fastQA/app/integrations/redis/service.py`
- Modify: `fastQA/app/core/config.py`
- Modify: `highThinkingQA/server/services/redis_client.py`
- Test: `public-service/backend/tests/test_config_independence.py`
- Test: `fastQA/tests/test_redis_helpers.py`
- Test: `fastQA/tests/test_redis_runtime.py`
- Test: `highThinkingQA/tests/test_env_loader.py`

- [ ] **Step 1: Write failing tests for stream naming and rollout config invariants**

Test cases to add:
- stream names use explicit versioned suffixes
- consumer-group names stay service-scoped
- no new stream name duplicates the service prefix twice
- rollout flags distinguish `shadow`, `enabled`, and `worker_enabled`
- web API role and MQ worker role are explicitly separable under `gunicorn`
- consumer-group bootstrap is idempotent and chooses `$` or `0` explicitly by rollout mode
- `highThinkingQA` config surface does not expose any new upload/file-QA ownership flag

Run:
```bash
pytest public-service/backend/tests/test_config_independence.py fastQA/tests/test_redis_helpers.py fastQA/tests/test_redis_runtime.py highThinkingQA/tests/test_env_loader.py -v
```
Expected: FAIL because stream helper methods and rollout flags do not exist yet.

- [ ] **Step 2: Add shared stream key and consumer-group helpers**

Implementation notes:
- Add `stream()` and `consumer_group()` style helpers in the authority Redis integration.
- Mirror the naming behavior in `fastQA` and `highThinkingQA` without changing existing cache/lock semantics.
- Make consumer-group creation idempotent with `MKSTREAM`, tolerate `BUSYGROUP`, and expose the configured start offset (`$` for shadow-only new traffic, `0` for backlog-draining cutover) in config or status.
- Keep all names versioned from the first commit.

- [ ] **Step 3: Add rollout config flags and safe defaults**

Implementation notes:
- Add flags for producer shadow mode, producer cutover, and worker enablement.
- Add an explicit process-role split so web API startup does not automatically start MQ consumers in every `gunicorn` worker process.
- Default all new workers to off unless the existing flow is already authority-owned.
- Keep `highThinkingQA` limited to ingest-oriented and temporary bridge flags only.

- [ ] **Step 4: Run the foundation tests**

Run:
```bash
pytest public-service/backend/tests/test_config_independence.py fastQA/tests/test_redis_helpers.py fastQA/tests/test_redis_runtime.py highThinkingQA/tests/test_env_loader.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/integrations/redis/streams.py public-service/backend/app/integrations/redis/keys.py public-service/backend/app/integrations/redis/service.py public-service/backend/app/core/config.py fastQA/app/integrations/redis/keys.py fastQA/app/integrations/redis/service.py fastQA/app/core/config.py highThinkingQA/server/services/redis_client.py public-service/backend/tests/test_config_independence.py fastQA/tests/test_redis_helpers.py fastQA/tests/test_redis_runtime.py highThinkingQA/tests/test_env_loader.py
git commit -m "feat: add redis stream foundations and rollout flags"
```

---

## Phase 1: Public-Service Authority Streams First

### Task 2: Canonical Stream Contracts And Producers In Public-Service

**Files:**
- Create: `public-service/backend/app/modules/conversation/stream_contracts.py`
- Create: `public-service/backend/app/modules/conversation/stream_publishers.py`
- Modify: `public-service/backend/app/modules/conversation/authority_schemas.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
- Modify: `public-service/backend/app/modules/conversation/__init__.py`
- Test: `public-service/backend/tests/test_conversation_stream_contracts.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`
- Test: `public-service/backend/tests/test_conversation_authority_integration.py`

- [ ] **Step 1: Write failing tests for assistant-finalize and chat-json-sync envelopes**

Test cases to add:
- `assistant_finalize` requires `final_event.done_seen`, `answer_text`, `steps`, `references`, `used_files`, `timings`
- `chat_json_sync` requires `conversation_id`, `user_id`, `json_version`, `object_name`, `content_hash`
- idempotency keys are stable and deterministic
- malformed authority async payloads are rejected before publish

Run:
```bash
pytest public-service/backend/tests/test_conversation_stream_contracts.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py -v
```
Expected: FAIL because canonical stream contract builders do not exist yet.

- [ ] **Step 2: Add canonical stream contract builders**

Implementation notes:
- Put stream envelope shaping in one module, not inside route handlers.
- Reuse existing authority schema names where possible.
- Keep transport-only metadata optional.

- [ ] **Step 3: Add producer publishing hooks in the authority service**

Implementation notes:
- Publish `assistant_finalize` after authority acceptance, in shadow mode first.
- Publish `chat_json_sync` only from authority-owned document persistence paths.
- Do not publish from `gateway`.

- [ ] **Step 4: Run public-service contract tests**

Run:
```bash
pytest public-service/backend/tests/test_conversation_stream_contracts.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/conversation/stream_contracts.py public-service/backend/app/modules/conversation/stream_publishers.py public-service/backend/app/modules/conversation/authority_schemas.py public-service/backend/app/modules/conversation/service.py public-service/backend/app/modules/conversation/internal_api.py public-service/backend/app/modules/conversation/__init__.py public-service/backend/tests/test_conversation_stream_contracts.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py
git commit -m "feat: add canonical public-service stream producers"
```

### Task 3: Assistant Finalize Consumer Worker In Public-Service

**Files:**
- Create: `public-service/backend/app/modules/conversation/assistant_stream_worker.py`
- Modify: `public-service/backend/app/modules/conversation/assistant_inbox.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/core/runtime.py`
- Modify: `public-service/backend/app/main.py`
- Modify: `public-service/scripts/start_gunicorn.sh`
- Test: `public-service/backend/tests/test_conversation_assistant_inbox.py`
- Test: `public-service/backend/tests/test_conversation_stream_workers.py`
- Test: `public-service/backend/tests/test_live_public_service_integration.py`

- [ ] **Step 1: Write failing worker tests for duplicate, retry, and ack behavior**

Test cases to add:
- duplicate `assistant_finalize` deliveries do not duplicate assistant messages
- transient materialization failure leaves the message claimable
- terminal schema/state failure routes to DLQ
- successful processing ends with `XACK`
- repeated messages for the same `conversation_id` stay serialized under multi-consumer execution
- web `gunicorn` startup does not automatically start this consumer in every API worker process

Run:
```bash
pytest public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_stream_workers.py public-service/backend/tests/test_live_public_service_integration.py -v
```
Expected: FAIL because the stream worker does not exist yet.

- [ ] **Step 2: Implement the worker using existing materialization logic**

Implementation notes:
- Reuse the same business materialization path the inbox worker already trusts.
- Keep idempotency checks in business state, not only in Redis message IDs.
- Add DLQ emission for terminal failures.
- Use a per-`conversation_id` lease before materializing ordered assistant state so multi-consumer deployments do not reorder the same conversation.
- Expose this worker through an explicit worker entrypoint or process role, not generic web app startup.

- [ ] **Step 3: Wire the worker into runtime startup with rollout flags**

Implementation notes:
- Allow shadow consumption first.
- Keep the legacy inbox path available until parity is proven.
- Ensure the startup path is independent from the main web `gunicorn` process role.

- [ ] **Step 4: Run the assistant worker tests**

Run:
```bash
pytest public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_stream_workers.py public-service/backend/tests/test_live_public_service_integration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/conversation/assistant_stream_worker.py public-service/backend/app/modules/conversation/assistant_inbox.py public-service/backend/app/modules/conversation/service.py public-service/backend/app/core/runtime.py public-service/backend/app/main.py public-service/scripts/start_gunicorn.sh public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_stream_workers.py public-service/backend/tests/test_live_public_service_integration.py
git commit -m "feat: add public-service assistant stream worker"
```

### Task 4: Chat JSON Sync Stream Worker In Public-Service

**Files:**
- Create: `public-service/backend/app/modules/conversation/chat_json_stream_worker.py`
- Modify: `public-service/backend/app/modules/conversation/outbox.py`
- Modify: `public-service/backend/app/modules/conversation/outbox_worker.py`
- Modify: `public-service/backend/app/modules/conversation/json_store.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/core/runtime.py`
- Modify: `public-service/scripts/start_gunicorn.sh`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Test: `public-service/backend/tests/test_conversation_stream_workers.py`
- Test: `public-service/backend/tests/test_live_public_service_integration.py`

- [ ] **Step 1: Write failing tests for versioned JSON sync delivery**

Test cases to add:
- older `json_version` payloads are acknowledged as stale success
- object-storage retryable failures keep work pending
- terminal invalid payloads go to DLQ
- successful remote sync updates authority state exactly once
- repeated messages for the same `conversation_id` stay serialized under multi-consumer execution
- web `gunicorn` startup does not automatically start this consumer in every API worker process

Run:
```bash
pytest public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_stream_workers.py public-service/backend/tests/test_live_public_service_integration.py -v
```
Expected: FAIL because the stream consumer path does not exist yet.

- [ ] **Step 2: Implement JSON sync consumer logic**

Implementation notes:
- In phase 1, explicitly choose a DB-outbox-to-Redis-Stream bridge and keep the existing poller path available for rollback; do not replace the poller in the same task.
- Preserve version monotonicity checks.
- Reuse content-hash and object-name state already stored by authority persistence.
- Use a per-`conversation_id` lease before mutating ordered chat-JSON state so multi-consumer deployments do not reorder the same conversation.
- Expose this worker through an explicit worker entrypoint or process role, not generic web app startup.

- [ ] **Step 3: Wire the worker in shadow mode, then worker-enabled mode**

Implementation notes:
- Keep outbox polling available during phase 1.
- Emit metrics for backlog, pending age, throughput, and DLQ count.
- Ensure the startup path is independent from the main web `gunicorn` process role.

- [ ] **Step 4: Run the JSON sync tests**

Run:
```bash
pytest public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_stream_workers.py public-service/backend/tests/test_live_public_service_integration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/conversation/chat_json_stream_worker.py public-service/backend/app/modules/conversation/outbox.py public-service/backend/app/modules/conversation/outbox_worker.py public-service/backend/app/modules/conversation/json_store.py public-service/backend/app/modules/conversation/service.py public-service/backend/app/core/runtime.py public-service/scripts/start_gunicorn.sh public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_stream_workers.py public-service/backend/tests/test_live_public_service_integration.py
git commit -m "feat: add public-service chat json stream worker"
```

### Phase 1 Explicit Deferral: `file_process` / `file_cleanup` Streams

This rollout plan intentionally does **not** migrate `public-service/backend/app/modules/conversation/upload_processing_worker.py` onto Redis Streams yet.

That deferral is intentional, not accidental. Completion of this plan still leaves file-processing and file-cleanup on the legacy path until all of the following are true:

- canonical file-reference semantics are stable in `public-service`
- worker payloads can be expressed entirely with authority-owned fields such as `file_id`, `conversation_id`, `user_id`, `storage_ref`, and lifecycle status
- no residual `highThinkingQA` local file metadata is required for correctness
- delete/cleanup semantics are fully specified and testable

If those criteria become true, create a follow-up plan that migrates `stream:conversation:file_process:v1` and `stream:conversation:file_cleanup:v1` explicitly. Until then, treat `upload_processing_worker.py` as intentionally deferred legacy infrastructure.

---

## Phase 2: HighThinkingQA Background Work Only

### Task 5: HighThinkingQA Ingest Job Stream Orchestration

**Files:**
- Create: `highThinkingQA/server/services/ingest_streams.py`
- Modify: `highThinkingQA/server/services/ingest_service.py`
- Modify: `highThinkingQA/server_fastapi/routers/ingest.py`
- Modify: `highThinkingQA/server_fastapi/app.py`
- Modify: `highThinkingQA/scripts/start_fastapi_gunicorn.sh`
- Modify: `highThinkingQA/server_fastapi/gunicorn.conf.py`
- Test: `highThinkingQA/tests/test_ingest_streams.py`
- Test: `highThinkingQA/tests/test_env_loader.py`

- [ ] **Step 1: Write failing tests for durable ingest job submission and recovery**

Test cases to add:
- creating an ingest job enqueues a stream payload instead of daemon-thread-only state
- duplicate `job_id` delivery is idempotent
- single-running-job policy is preserved
- retryable ingest failures remain claimable
- terminal ingest failures mark the job failed and route the message to DLQ
- generic web `gunicorn` startup does not start ingest consumers in every API worker process
- a single-active lease with explicit token, TTL, heartbeat, and lease-loss behavior prevents concurrent ingest execution across replicas and worker processes

Run:
```bash
pytest highThinkingQA/tests/test_ingest_streams.py highThinkingQA/tests/test_env_loader.py -v
```
Expected: FAIL because Redis-stream orchestration for ingest does not exist yet.

- [ ] **Step 2: Implement producer and consumer orchestration for ingest**

Implementation notes:
- Keep the ingest API synchronous only for job creation and status response.
- Move heavy corpus work behind Redis Streams.
- Preserve current `PAPERS_DIR` based corpus semantics.
- Terminal ingest failures must update persisted job state to `failed` before DLQ acknowledgement.
- Run ingest consumption in an explicit worker process or deployment, not under generic FastAPI web startup.
- Enforce single-active ingest with a Redis lease using key `lock:mq:highthinkingqa:ingest:active`, owner token `<service>:<instance>:<pid>:<consumer>`, 30-second TTL, 10-second renew cadence, token-checked renewal, and mandatory abort-on-lease-loss behavior.

- [ ] **Step 3: Wire the ingest worker into service runtime**

Implementation notes:
- Keep rollout flags off by default.
- Do not touch upload routes, document routes, or ask execution.
- Keep web `gunicorn` startup producer-only unless an explicit ingest-worker role is selected.

- [ ] **Step 4: Run the ingest tests**

Run:
```bash
pytest highThinkingQA/tests/test_ingest_streams.py highThinkingQA/tests/test_env_loader.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/services/ingest_streams.py highThinkingQA/server/services/ingest_service.py highThinkingQA/server_fastapi/routers/ingest.py highThinkingQA/server_fastapi/app.py highThinkingQA/scripts/start_fastapi_gunicorn.sh highThinkingQA/server_fastapi/gunicorn.conf.py highThinkingQA/tests/test_ingest_streams.py highThinkingQA/tests/test_env_loader.py
git commit -m "feat: add highThinkingQA ingest stream orchestration"
```

### Task 6: HighThinkingQA Legacy Chat-JSON Bridge Guardrails

**Files:**
- Modify: `highThinkingQA/server/services/chat_persistence.py`
- Modify: `highThinkingQA/server/services/conversation/chat_json_outbox_worker.py`
- Modify: `highThinkingQA/server/tools/run_chat_json_outbox_worker.py`
- Modify: `highThinkingQA/server_fastapi/app.py`
- Test: `highThinkingQA/tests/test_chat_persistence.py`
- Test: `highThinkingQA/tests/test_chat_json_store.py`
- Test: `highThinkingQA/tests/test_background_persistence_dispatcher.py`

- [ ] **Step 1: Write failing tests for temporary bridge-only behavior**

Test cases to add:
- any emitted `chat_json_sync` payload is explicitly marked as residual bridge traffic
- bridge workers can be disabled independently from ask execution
- no new upload/file-QA worker is started in `highThinkingQA`

Run:
```bash
pytest highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_chat_json_store.py highThinkingQA/tests/test_background_persistence_dispatcher.py -v
```
Expected: FAIL because bridge-only rollout controls are not explicit yet.

- [ ] **Step 2: Implement bridge-only guardrails**

Implementation notes:
- Keep this path transitional.
- Document in code comments and config names that `highThinkingQA` is not the authority owner.
- Do not create any new upload/file stream family here.

- [ ] **Step 3: Run the bridge guardrail tests**

Run:
```bash
pytest highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_chat_json_store.py highThinkingQA/tests/test_background_persistence_dispatcher.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add highThinkingQA/server/services/chat_persistence.py highThinkingQA/server/services/conversation/chat_json_outbox_worker.py highThinkingQA/server/tools/run_chat_json_outbox_worker.py highThinkingQA/server_fastapi/app.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_chat_json_store.py highThinkingQA/tests/test_background_persistence_dispatcher.py
git commit -m "refactor: limit highThinkingQA chat json flow to bridge mode"
```

---

## Phase 3: FastQA Best-Effort Prewarm

### Task 7: FastQA Prewarm Stream For Uploaded Assets

**Files:**
- Create: `fastQA/app/modules/storage/prewarm_streams.py`
- Modify: `fastQA/app/modules/storage/upload_materializer.py`
- Modify: `fastQA/app/services/chat_persistence.py`
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/app/main.py`
- Modify: `fastQA/scripts/start_gunicorn.sh`
- Test: `fastQA/tests/test_prewarm_streams.py`
- Test: `fastQA/tests/test_upload_materializer.py`
- Test: `fastQA/tests/test_chat_persistence.py`

- [ ] **Step 1: Write failing tests for best-effort prewarm behavior**

Test cases to add:
- upload/file-change events can enqueue prewarm work
- duplicate prewarm deliveries are safe no-ops
- prewarm failure never breaks the request path
- request path still materializes synchronously on cache miss
- generic web `gunicorn` startup does not start prewarm consumers in every API worker process

Run:
```bash
pytest fastQA/tests/test_prewarm_streams.py fastQA/tests/test_upload_materializer.py fastQA/tests/test_chat_persistence.py -v
```
Expected: FAIL because the prewarm stream helper does not exist yet.

- [ ] **Step 2: Implement prewarm producer and consumer**

Implementation notes:
- Keep this flow best-effort only.
- Limit work to asset materialization, workbook/profile warmup, and similar derived compute.
- Do not move answer generation into MQ.
- Run prewarm consumption in an explicit worker process or deployment, not under generic web app startup.

- [ ] **Step 3: Run the prewarm tests**

Run:
```bash
pytest fastQA/tests/test_prewarm_streams.py fastQA/tests/test_upload_materializer.py fastQA/tests/test_chat_persistence.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add fastQA/app/modules/storage/prewarm_streams.py fastQA/app/modules/storage/upload_materializer.py fastQA/app/services/chat_persistence.py fastQA/app/core/runtime.py fastQA/app/main.py fastQA/scripts/start_gunicorn.sh fastQA/tests/test_prewarm_streams.py fastQA/tests/test_upload_materializer.py fastQA/tests/test_chat_persistence.py
git commit -m "feat: add fastQA prewarm stream"
```

---

## Phase 4: Optional Gateway Audit Only

### Task 8: Gateway Route-Decision Audit Stream

**Files:**
- Create: `gateway/app/services/route_audit_stream.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/app/core/config.py`
- Modify: `gateway/app/main.py`
- Test: `gateway/tests/test_route_audit_stream.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `gateway/tests/test_config.py`

- [ ] **Step 1: Write failing tests for observability-only audit events**

Test cases to add:
- audit event emission records route decision inputs and outputs
- audit publish failure never fails the user request
- gateway does not emit business-retry or authority-truth semantics through this stream

Run:
```bash
pytest gateway/tests/test_route_audit_stream.py gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_config.py -v
```
Expected: FAIL because the audit publisher does not exist yet.

- [ ] **Step 2: Implement audit-only stream emission**

Implementation notes:
- Emit after route decision is known.
- Never make downstream MQ success a request-path requirement.
- Keep payloads narrow and telemetry-oriented.

- [ ] **Step 3: Run gateway audit tests**

Run:
```bash
pytest gateway/tests/test_route_audit_stream.py gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_config.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add gateway/app/services/route_audit_stream.py gateway/app/services/route_decision.py gateway/app/routers/qa.py gateway/app/core/config.py gateway/app/main.py gateway/tests/test_route_audit_stream.py gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_config.py
git commit -m "feat: add gateway route audit stream"
```

---

## Final Verification Batch

### Task 9: Rollout Verification, Metrics, And Runbook Closure

**Files:**
- Modify: `docs/2026-03-25-redis-mq-architecture-spec.md`
- Modify: `docs/superpowers/plans/2026-03-25-redis-mq-rollout.md`
- Modify: `scripts/start_all.sh`
- Modify: `scripts/status_all.sh`
- Modify: `scripts/stop_all.sh`
- Test: `public-service/backend/tests/test_health.py`
- Test: `fastQA/tests/test_health.py`
- Test: `gateway/tests/test_health.py`

- [ ] **Step 1: Add failing checks or assertions for worker visibility and rollout status**

Checks to add:
- status script exposes which MQ workers are enabled
- health endpoints or logs expose worker startup failures
- status or logs expose whether the current process is a web API role or MQ worker role
- docs list rollback toggles per workstream

Run:
```bash
pytest public-service/backend/tests/test_health.py fastQA/tests/test_health.py gateway/tests/test_health.py -v
```
Expected: FAIL or remain incomplete until worker visibility is wired.

- [ ] **Step 2: Implement runbook and status visibility**

Implementation notes:
- Do not hide rollout state inside environment variables only.
- Surface worker names, stream names, enablement state, and current process role in ops-facing commands or logs.
- Record rollback order: `gateway` audit off first, `fastQA` prewarm off second, `highThinkingQA` ingest off third, `public-service` stream workers last.

- [ ] **Step 3: Run the final verification batch**

Run core rollout verification:
```bash
pytest public-service/backend/tests/test_conversation_stream_contracts.py public-service/backend/tests/test_conversation_stream_workers.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_live_public_service_integration.py public-service/backend/tests/test_health.py highThinkingQA/tests/test_ingest_streams.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_chat_json_store.py highThinkingQA/tests/test_background_persistence_dispatcher.py fastQA/tests/test_prewarm_streams.py fastQA/tests/test_upload_materializer.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_health.py gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_config.py gateway/tests/test_health.py -v
```
Expected: PASS.

If Task 8 was executed on this branch, also run:
```bash
pytest gateway/tests/test_route_audit_stream.py gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_config.py gateway/tests/test_health.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/2026-03-25-redis-mq-architecture-spec.md docs/superpowers/plans/2026-03-25-redis-mq-rollout.md scripts/start_all.sh scripts/status_all.sh scripts/stop_all.sh
git commit -m "docs: finalize redis mq rollout runbook"
```

---

## Execution Order Summary

1. Task 1 must land before any stream producer or consumer work.
2. Tasks 2 through 4 are the first required production work and should be completed before any `highThinkingQA`, `fastQA`, or `gateway` stream rollout.
3. Task 5 is the first valid `highThinkingQA` MQ task.
4. Task 6 is allowed only as a transitional bridge and must not expand `highThinkingQA` into file/upload ownership.
5. Task 7 is best-effort and must not block answer-path correctness.
6. Task 8 is optional and should not start until all authority-owned workers are stable.
7. Task 9 closes the rollout and documents rollback.

## Recommended Commit Boundaries

- One commit per task.
- Do not batch `public-service` worker cutovers with `highThinkingQA` or `fastQA`.
- Keep gateway audit isolated in its own commit or branch.

## Handoff Notes For Implementers

- Read `docs/2026-03-25-redis-mq-architecture-spec.md` before starting any task.
- Re-check `highThinkingQA` scope in `highThinkingQA/README.md` before touching any upload, document, or conversation-local code there.
- If a task requires broadening `highThinkingQA` into file-QA ownership, stop and revise the spec instead of coding through it.
