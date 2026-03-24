# Conversation Authority Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate gateway-stack conversation authority from legacy QA-local persistence to `public-service`, while keeping `gateway` thin, preserving smooth answer UX, and introducing a Redis-backed pending-assistant continuity bridge.

**Architecture:** `public-service` becomes the sole durable conversation authority. `fastQA` is the first closed-loop migration target: it synchronously writes the current user turn and reads the context snapshot from `public-service`, then asynchronously submits the completed assistant turn to a MySQL-backed assistant inbox in `public-service`. Only after the durable assistant path is accepted does `fastQA` enable a Redis pending-overlay layer for smooth immediate follow-up UX. `highThinkingQA` adopts the same authority protocol only after `fastQA` phase-1 acceptance is complete, and only then adds its own Redis integration and overlay path using the same schema and convergence rules.

**Tech Stack:** FastAPI, existing public-service conversation module, MySQL, Redis, pytest, service-to-service HTTP, existing QA routers/services, SSE streaming.

---

## File Structure Map

### Existing files that are the main integration points

- `public-service/backend/app/modules/conversation/api.py`
- `public-service/backend/app/modules/conversation/schemas.py`
- `public-service/backend/app/modules/conversation/service.py`
- `public-service/backend/app/modules/conversation/repository.py`
- `public-service/backend/app/modules/conversation/outbox.py`
- `public-service/backend/app/modules/conversation/outbox_worker.py`
- `public-service/backend/app/core/runtime.py`
- `public-service/backend/app/core/config.py`
- `public-service/backend/tests/test_conversation_module.py`
- `public-service/backend/tests/test_route_surface.py`
- `public-service/backend/tests/test_live_public_service_integration.py`
- `public-service/backend/tests/test_health.py`
- `public-service/backend/tests/test_system_module.py`
- `fastQA/app/main.py`
- `fastQA/app/core/config.py`
- `fastQA/app/services/chat_persistence.py`
- `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- `fastQA/app/modules/qa_kb/service.py`
- `fastQA/app/routers/qa.py`
- `fastQA/tests/test_chat_persistence.py`
- `fastQA/tests/test_qa_generation_orchestrator.py`
- `fastQA/tests/test_qa_kb_service.py`
- `fastQA/tests/test_env_loader.py`
- `fastQA/tests/test_redis_runtime.py`
- `highThinkingQA/server_fastapi/routers/ask.py`
- `highThinkingQA/server/services/ask_service.py`
- `highThinkingQA/server/services/conversation_context_service.py`
- `highThinkingQA/server_fastapi/app.py`
- `highThinkingQA/server/runtime/request_context.py`
- `highThinkingQA/server/database/migrations`
- `highThinkingQA/tests/test_ask_router_summary_persistence.py`
- `highThinkingQA/tests/test_ask_service_executor.py`
- `highThinkingQA/tests/test_env_loader.py`
- `highThinkingQA/requirements.txt`

### New files to create

- `public-service/backend/app/modules/conversation/internal_api.py`
- `public-service/backend/app/modules/conversation/authority_schemas.py`
- `public-service/backend/app/modules/conversation/assistant_inbox.py`
- `public-service/backend/app/modules/conversation/assistant_inbox_worker.py`
- `public-service/backend/app/modules/conversation/assistant_inbox_ops.py`
- `public-service/backend/sql/conversation_assistant_inbox.sql`
- `public-service/backend/tests/test_conversation_authority_api.py`
- `public-service/backend/tests/test_conversation_assistant_inbox.py`
- `public-service/backend/tests/test_conversation_authority_integration.py`
- `fastQA/app/services/conversation_authority_client.py`
- `fastQA/app/services/pending_overlay.py`
- `fastQA/tests/test_conversation_authority_client.py`
- `fastQA/tests/test_pending_overlay.py`
- `highThinkingQA/server/services/conversation_authority_client.py`
- `highThinkingQA/server/integrations/redis/__init__.py`
- `highThinkingQA/server/integrations/redis/client.py`
- `highThinkingQA/server/integrations/redis/service.py`
- `highThinkingQA/server/services/pending_overlay.py`
- `highThinkingQA/tests/test_conversation_authority_client.py`
- `highThinkingQA/tests/test_pending_overlay.py`
- `highThinkingQA/tests/test_phase2_authority_integration.py`

### Existing files likely to modify

- `public-service/backend/app/main.py`
- `public-service/backend/app/modules/conversation/__init__.py`
- `public-service/backend/app/modules/conversation/cache.py`
- `public-service/backend/app/integrations/redis/keys.py`
- `fastQA/app/core/runtime.py`
- `fastQA/app/integrations/redis/keys.py`
- `fastQA/app/integrations/redis/service.py`
- `fastQA/app/services/stream_contract.py`
- `highThinkingQA/config.py`
- `highThinkingQA/server_fastapi/http.py`
- `highThinkingQA/server_fastapi/app.py`
- `highThinkingQA/server/runtime/request_context.py`
- `highThinkingQA/requirements.txt`

---

## Phase 1: fastQA First Closed Loop

### Task 1: Rollout Controls And Safety Invariants Before Any Cutover

**Files:**
- Modify: `public-service/backend/app/core/config.py`
- Modify: `fastQA/app/core/config.py`
- Modify: `highThinkingQA/config.py`
- Modify: `public-service/backend/tests/test_config_independence.py`
- Modify: `fastQA/tests/test_env_loader.py`
- Modify: `highThinkingQA/tests/test_env_loader.py`

- [ ] **Step 1: Write failing config tests for coupled execution-authority flags**

Test cases to add:
- execution authority is one coupled flag, not separate production toggles for `user write` and `context read`
- assistant write and overlay remain independent flags
- invalid split-authority production config is rejected or normalized away

Run:
```bash
pytest public-service/backend/tests/test_config_independence.py fastQA/tests/test_env_loader.py highThinkingQA/tests/test_env_loader.py -v
```
Expected: FAIL because the new rollout config surface does not exist yet.

- [ ] **Step 2: Add rollout config surfaces and guardrails**

Implementation notes:
- Add one coupled execution-authority target flag.
- Add assistant-write target and overlay enablement flags separately.
- Refuse any production state where current-turn user write and context read point to different authorities.

- [ ] **Step 3: Run rollout-config tests**

Run:
```bash
pytest public-service/backend/tests/test_config_independence.py fastQA/tests/test_env_loader.py highThinkingQA/tests/test_env_loader.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add public-service/backend/app/core/config.py fastQA/app/core/config.py highThinkingQA/config.py public-service/backend/tests/test_config_independence.py fastQA/tests/test_env_loader.py highThinkingQA/tests/test_env_loader.py
git commit -m "feat: add coupled execution authority rollout controls"
```

### Task 2: Public-Service Internal Authority Contracts And Internal Auth

**Files:**
- Create: `public-service/backend/app/modules/conversation/internal_api.py`
- Create: `public-service/backend/app/modules/conversation/authority_schemas.py`
- Modify: `public-service/backend/app/modules/conversation/api.py`
- Modify: `public-service/backend/app/modules/conversation/schemas.py`
- Modify: `public-service/backend/app/main.py`
- Modify: `public-service/backend/app/modules/conversation/__init__.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`
- Test: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: Write failing authority-route and internal-auth tests for the exact section-27 contract**

Test cases to add:
- user-write route accepts nested `message` payload and returns `message_id`, `trace_id`, `idempotency_key`, `deduped`
- context-snapshot route returns `snapshot_version`, `summary`, `recent_turns`, and `conversation_state`
- assistant-async route accepts `final_event.answer_text` and returns `202 Accepted`
- malformed required fields are rejected
- read route does not require `idempotency_key`
- write routes reject missing or malformed `idempotency_key`
- trusted internal caller headers are required
- unknown caller is rejected
- spoofed `user_id` / `conversation_id` combinations are rejected by authority-side ownership checks
- invalid `source_service` policy is rejected

Run:
```bash
pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_route_surface.py -v
```
Expected: FAIL because routes, schemas, and internal-auth matrix do not exist yet.

- [ ] **Step 2: Add canonical authority schemas and explicit internal auth dependency**

Implementation notes:
- Put the exact section-27 fields in `authority_schemas.py`.
- Keep write-only idempotency fields out of read schemas.
- Encode malformed-field rejection in schema validation, not only in service logic.
- Keep internal auth separate from browser auth.

- [ ] **Step 3: Add internal authority API routes and authorization checks**

Implementation notes:
- Add a dedicated `internal_api.py` router.
- Require trusted caller identity plus payload-scoped `user_id` / `conversation_id` verification.
- Enforce `source_service` policy in the internal authority layer.

- [ ] **Step 4: Run public-service contract/auth tests**

Run:
```bash
pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_route_surface.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/conversation/internal_api.py public-service/backend/app/modules/conversation/authority_schemas.py public-service/backend/app/modules/conversation/api.py public-service/backend/app/modules/conversation/schemas.py public-service/backend/app/main.py public-service/backend/app/modules/conversation/__init__.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_route_surface.py
git commit -m "feat: add internal conversation authority API contracts"
```

### Task 3: Public-Service Coupled Execution Authority Base

**Files:**
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/modules/conversation/repository.py`
- Modify: `public-service/backend/app/modules/conversation/cache.py`
- Modify: `public-service/backend/app/core/runtime.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Test: `public-service/backend/tests/test_conversation_authority_integration.py`
- Test: `public-service/backend/tests/test_live_public_service_integration.py`

- [ ] **Step 1: Write failing tests for coupled user-write plus snapshot-read behavior**

Test cases to add:
- successful user write is immediately visible to the next context snapshot
- duplicate user write with same idempotency key does not append duplicate messages
- snapshot ordering and summary shape are canonical
- ownership validation rejects wrong `user_id` / `conversation_id`
- malformed snapshot state never falls back to legacy inside the same request path

Run:
```bash
pytest public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_live_public_service_integration.py -v
```
Expected: FAIL on missing canonical authority behavior.

- [ ] **Step 2: Add user-write idempotency handling and canonical snapshot assembly**

Implementation notes:
- Implement explicit authority user-write behavior, not only browser `add_message` semantics.
- Return canonical `message_id` on dedupe acknowledgement.
- Keep final prompt budgeting out of `public-service`.

- [ ] **Step 3: Update cache behavior for freshness**

Implementation notes:
- Immediate post-write snapshot must reflect the just-written user turn.
- Invalidate or bypass stale cache on the synchronous user-write path.

- [ ] **Step 4: Run execution-authority tests**

Run:
```bash
pytest public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_live_public_service_integration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/app/modules/conversation/service.py public-service/backend/app/modules/conversation/repository.py public-service/backend/app/modules/conversation/cache.py public-service/backend/app/core/runtime.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_live_public_service_integration.py
git commit -m "feat: implement coupled execution authority base"
```

### Task 4: Public-Service Assistant Inbox, Schema, And Recovery Tooling

**Files:**
- Create: `public-service/backend/app/modules/conversation/assistant_inbox.py`
- Create: `public-service/backend/app/modules/conversation/assistant_inbox_worker.py`
- Create: `public-service/backend/app/modules/conversation/assistant_inbox_ops.py`
- Create: `public-service/backend/sql/conversation_assistant_inbox.sql`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/core/runtime.py`
- Modify: `public-service/backend/app/modules/conversation/outbox.py`
- Modify: `public-service/backend/app/modules/conversation/outbox_worker.py`
- Modify: `public-service/backend/tests/test_health.py`
- Modify: `public-service/backend/tests/test_system_module.py`
- Test: `public-service/backend/tests/test_conversation_assistant_inbox.py`
- Test: `public-service/backend/tests/test_conversation_module.py`

- [ ] **Step 1: Write failing tests for assistant async acceptance, worker completion, and recovery ops**

Test cases to add:
- `assistant-async` returns `202 Accepted`
- duplicate assistant event by same idempotency key is deduped
- accepted event is materialized exactly once
- summary refresh happens after assistant materialization
- cache repair failure does not roll back persisted assistant message
- dead-lettered event can be listed
- replay by event id / trace id does not create duplicates
- worker health and backlog are visible from runtime/system surfaces

Run:
```bash
pytest public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_health.py public-service/backend/tests/test_system_module.py -v
```
Expected: FAIL because assistant inbox flow, schema artifact, and recovery tooling do not exist yet.

- [ ] **Step 2: Add the concrete assistant-inbox schema/bootstrap artifact**

Implementation notes:
- Create a distinct MySQL assistant-ingress schema artifact in `public-service/backend/sql/conversation_assistant_inbox.sql`.
- Do not overload the existing JSON/object-storage outbox as the durable assistant ingress table.
- Keep accepted / processing / completed / failed_retryable / dead_letter semantics explicit.

- [ ] **Step 3: Add durable inbox repository, worker, and ops tooling**

Implementation notes:
- Add replay, pending/dead-letter inspection, and stuck-processing diagnostics.
- Keep this responsibility inside `public-service`.
- Make clear in code and plan that JSON/object-storage outbox and assistant inbox are distinct responsibilities.

- [ ] **Step 4: Wire worker lifecycle and runtime health**

Implementation notes:
- Start/stop worker from `core/runtime.py`.
- Expose worker health, backlog, and replayability status in runtime/system surfaces.

- [ ] **Step 5: Run assistant-inbox and recovery tests**

Run:
```bash
pytest public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_health.py public-service/backend/tests/test_system_module.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/conversation/assistant_inbox.py public-service/backend/app/modules/conversation/assistant_inbox_worker.py public-service/backend/app/modules/conversation/assistant_inbox_ops.py public-service/backend/sql/conversation_assistant_inbox.sql public-service/backend/app/modules/conversation/service.py public-service/backend/app/core/runtime.py public-service/backend/app/modules/conversation/outbox.py public-service/backend/app/modules/conversation/outbox_worker.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_health.py public-service/backend/tests/test_system_module.py
git commit -m "feat: add assistant inbox and recovery tooling"
```

### Task 5: FastQA Authority Client Migration

**Files:**
- Create: `fastQA/app/services/conversation_authority_client.py`
- Modify: `fastQA/app/services/chat_persistence.py`
- Modify: `fastQA/app/main.py`
- Modify: `fastQA/app/modules/qa_kb/service.py`
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Modify: `fastQA/app/routers/qa.py`
- Test: `fastQA/tests/test_conversation_authority_client.py`
- Test: `fastQA/tests/test_chat_persistence.py`
- Test: `fastQA/tests/test_qa_generation_orchestrator.py`
- Test: `public-service/backend/tests/test_conversation_authority_integration.py`

- [ ] **Step 1: Write failing tests for fastQA authority client and fail-fast ask semantics**

Test cases to add:
- sync user-write call uses canonical schema and handles dedupe success
- snapshot read uses canonical read contract without `idempotency_key`
- assistant async accept uses canonical assistant payload with `final_event.answer_text`
- malformed contract responses are rejected
- user-write failure fails the ask before execution
- snapshot-read failure fails the ask before execution
- raw model payloads / hidden thinking are not included in assistant durable payload

Run:
```bash
pytest fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_qa_generation_orchestrator.py public-service/backend/tests/test_conversation_authority_integration.py -v
```
Expected: FAIL because client and fail-fast authority behavior do not exist yet.

- [ ] **Step 2: Add a public-service authority client module**

Implementation notes:
- Hide internal endpoint URLs, auth headers, and request serialization inside the client.
- Enforce the canonical section-27 contract and section-36 payload exclusions.
- Keep retry behavior bounded; do not build a QA-side durable outbox.

- [ ] **Step 3: Replace legacy user-write, assistant-write, and context-read hooks**

Implementation notes:
- `fastQA/app/services/chat_persistence.py` should stop importing legacy `conversation_service` in the active path.
- Route sync user write, sync snapshot read, and async assistant accept through the authority client.
- Preserve the invariant that user write and snapshot read use the same authority base.

- [ ] **Step 4: Run fastQA authority migration tests**

Run:
```bash
pytest fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_qa_generation_orchestrator.py public-service/backend/tests/test_conversation_authority_integration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add fastQA/app/services/conversation_authority_client.py fastQA/app/services/chat_persistence.py fastQA/app/main.py fastQA/app/modules/qa_kb/service.py fastQA/app/modules/qa_kb/orchestrators/generation.py fastQA/app/routers/qa.py fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_qa_generation_orchestrator.py public-service/backend/tests/test_conversation_authority_integration.py
git commit -m "feat: migrate fastqa to public-service authority client"
```

### Task 6: Phase-1 Assistant Durability Acceptance Gate Before Overlay

**Files:**
- Modify: `public-service/backend/app/core/runtime.py`
- Modify: `fastQA/app/main.py`
- Create: `public-service/backend/tests/test_conversation_authority_integration.py`
- Modify: `public-service/backend/tests/test_live_public_service_integration.py`
- Modify: `fastQA/tests/test_chat_persistence.py`
- Modify: `fastQA/tests/test_qa_generation_orchestrator.py`

- [ ] **Step 1: Write failing automated cross-service acceptance tests for fastQA without overlay**

Test cases to add:
- `fastQA -> public-service` first-turn user write + snapshot read + assistant async accept closed loop
- assistant async accept eventually materializes exactly one durable assistant turn
- transient `public-service` unavailability causes fail-fast pre-execution failure on user-write/snapshot-read operations
- assistant async accept transient failure retries with same idempotency key and does not retract visible answer
- rollback flag path selection keeps execution authority coupled and assistant/overlay independent

Run:
```bash
pytest public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_live_public_service_integration.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_qa_generation_orchestrator.py -v
```
Expected: FAIL until the full fastQA durable chain is complete.

- [ ] **Step 2: Add missing runtime/logging hooks needed by the cross-service tests**

Implementation notes:
- Emit `trace_id`, `conversation_id`, `user_id`, `source_service`, operation type, and result status across authority boundaries.
- Expose assistant inbox backlog/health via runtime status.
- Distinguish authority failures from Redis overlay degradations.

- [ ] **Step 3: Run fastQA durable-path acceptance suites**

Run:
```bash
pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_live_public_service_integration.py public-service/backend/tests/test_health.py public-service/backend/tests/test_system_module.py -v
pytest fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_qa_generation_orchestrator.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit the pre-overlay phase-1 acceptance state**

```bash
git add public-service/backend/app/core/runtime.py fastQA/app/main.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_live_public_service_integration.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_qa_generation_orchestrator.py
git commit -m "feat: complete fastqa durable authority acceptance gate"
```

### Task 7: FastQA Redis Pending Overlay Rollout And Acceptance

**Files:**
- Create: `fastQA/app/services/pending_overlay.py`
- Modify: `fastQA/app/integrations/redis/keys.py`
- Modify: `fastQA/app/integrations/redis/service.py`
- Modify: `fastQA/app/services/chat_persistence.py`
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Modify: `fastQA/app/services/stream_contract.py`
- Test: `fastQA/tests/test_pending_overlay.py`
- Test: `fastQA/tests/test_qa_generation_orchestrator.py`
- Test: `fastQA/tests/test_redis_runtime.py`
- Test: `public-service/backend/tests/test_conversation_authority_integration.py`

- [ ] **Step 1: Write failing tests for Redis pending overlay semantics**

Test cases to add:
- overlay is written when final answer is stable and just before `done`
- overlay stores only the minimal final-turn payload
- next ask merges at most one latest valid overlay after authority snapshot
- overlay is ignored immediately once authority already contains the same assistant turn
- Redis failure degrades to authority-only without failing ask

Run:
```bash
pytest fastQA/tests/test_pending_overlay.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_redis_runtime.py public-service/backend/tests/test_conversation_authority_integration.py -v
```
Expected: FAIL because overlay module does not exist yet.

- [ ] **Step 2: Add Redis overlay keying and helper module**

Implementation notes:
- Use one latest pending overlay per conversation.
- Include `trace_id`, `conversation_id`, `user_id`, route/mode metadata, minimal final assistant payload, and expiry.
- Default TTL target: 3 minutes.
- Keep Redis non-authoritative.

- [ ] **Step 3: Wire overlay write and merge points into fastQA ask execution**

Implementation notes:
- Overlay work starts only after Task 6 acceptance is complete.
- Do not block streaming if Redis is degraded.
- Merge overlay only after authority snapshot retrieval and only when authority has not already converged.

- [ ] **Step 4: Run fastQA overlay acceptance suites**

Run:
```bash
pytest fastQA/tests/test_pending_overlay.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_redis_runtime.py public-service/backend/tests/test_conversation_authority_integration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add fastQA/app/services/pending_overlay.py fastQA/app/integrations/redis/keys.py fastQA/app/integrations/redis/service.py fastQA/app/services/chat_persistence.py fastQA/app/modules/qa_kb/orchestrators/generation.py fastQA/app/services/stream_contract.py fastQA/tests/test_pending_overlay.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_redis_runtime.py public-service/backend/tests/test_conversation_authority_integration.py
git commit -m "feat: add fastqa redis pending overlay"
```

---

## Phase 2: highThinkingQA Adoption After fastQA Stability

### Task 8: HighThinkingQA Authority Client Migration And Phase-2 Durable Acceptance

**Files:**
- Create: `highThinkingQA/server/services/conversation_authority_client.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Modify: `highThinkingQA/server/services/conversation_context_service.py`
- Modify: `highThinkingQA/server/services/ask_service.py`
- Modify: `highThinkingQA/server_fastapi/app.py`
- Test: `highThinkingQA/tests/test_conversation_authority_client.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`
- Test: `highThinkingQA/tests/test_ask_service_executor.py`
- Create: `highThinkingQA/tests/test_phase2_authority_integration.py`

- [ ] **Step 1: Write failing tests for highThinkingQA authority migration and durable phase-2 acceptance**

Test cases to add:
- sync user-write path calls `public-service` authority API
- context snapshot is loaded from authority client instead of legacy service
- completed assistant turn is sent to assistant async accept
- ask path remains smooth when assistant persistence degrades
- user-write failure and snapshot-read failure fail before execution starts
- hidden thinking / raw model payloads stay out of the durable assistant payload
- `highThinkingQA -> public-service` durable closed loop matches the same authority semantics as fastQA
- assistant async accept ambiguity retries with the same idempotency key and still materializes exactly one assistant turn
- rollback-flag path selection behaves correctly for the coupled execution-authority base and independent assistant-write flag
- observability and degraded-diagnostics parity match the fastQA durable-path acceptance gate before overlay rollout

Run:
```bash
pytest highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_phase2_authority_integration.py -v
```
Expected: FAIL because authority client and phase-2 acceptance tests do not exist yet.

- [ ] **Step 2: Add a highThinkingQA authority client using the same protocol**

Implementation notes:
- Mirror the same canonical schema and retry semantics as fastQA.
- Do not fork the protocol.
- Keep the implementation local to `highThinkingQA/server/services`.

- [ ] **Step 3: Replace legacy ask-router persistence hooks and context reads**

Implementation notes:
- `server_fastapi/routers/ask.py` should stop using legacy `conversation_service` in the active migrated path.
- `conversation_context_service.py` should load the snapshot from `public-service`.
- Preserve local prompt budgeting and merge behavior on the highThinking side.

- [ ] **Step 4: Run highThinkingQA durable-path acceptance suites**

Run:
```bash
pytest highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_phase2_authority_integration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/services/conversation_authority_client.py highThinkingQA/server_fastapi/routers/ask.py highThinkingQA/server/services/conversation_context_service.py highThinkingQA/server/services/ask_service.py highThinkingQA/server_fastapi/app.py highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_phase2_authority_integration.py
git commit -m "feat: migrate highthinkingqa to public-service authority client"
```

### Task 9: HighThinkingQA Redis Integration, Overlay, And Shared-Schema Acceptance

Prerequisite:
- do not start this task until Task 8 durable-path acceptance has passed, including exactly-once assistant materialization, bounded retry behavior, rollback-flag path selection, and degraded-diagnostics parity.

**Files:**
- Create: `highThinkingQA/server/integrations/redis/__init__.py`
- Create: `highThinkingQA/server/integrations/redis/client.py`
- Create: `highThinkingQA/server/integrations/redis/service.py`
- Create: `highThinkingQA/server/services/pending_overlay.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Modify: `highThinkingQA/server/runtime/request_context.py`
- Modify: `highThinkingQA/server_fastapi/app.py`
- Modify: `highThinkingQA/config.py`
- Modify: `highThinkingQA/requirements.txt`
- Test: `highThinkingQA/tests/test_pending_overlay.py`
- Test: `highThinkingQA/tests/test_ask_service_executor.py`
- Test: `highThinkingQA/tests/test_phase2_authority_integration.py`

- [ ] **Step 1: Write failing tests for highThinkingQA Redis overlay behavior and shared-schema acceptance**

Test cases to add:
- overlay is written only from the final stable assistant turn
- overlay is merged after authority snapshot for immediate follow-up
- overlay convergence suppresses duplicates when authority already materialized the turn
- Redis degradation does not fail the ask
- overlay schema and convergence behavior match the fastQA contract
- shared cross-mode overlay assumptions are covered at the contract level

Run:
```bash
pytest highThinkingQA/tests/test_pending_overlay.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_phase2_authority_integration.py -v
```
Expected: FAIL because Redis integration and overlay modules do not exist yet.

- [ ] **Step 2: Add highThinkingQA Redis integration surfaces**

Implementation notes:
- Add an explicit Redis client/service layer for highThinkingQA instead of hiding Redis calls inside `pending_overlay.py`.
- Add the necessary Redis dependency to `requirements.txt`.
- Keep the Redis integration minimal and dedicated to shared overlay semantics.

- [ ] **Step 3: Add highThinking overlay module with the same schema as fastQA**

Implementation notes:
- Use the same Redis keying and convergence rules.
- Do not fork semantics between QA backends.
- Keep the frontend unaware of overlay internals.

- [ ] **Step 4: Run phase-2 overlay acceptance suites**

Run:
```bash
pytest highThinkingQA/tests/test_pending_overlay.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_phase2_authority_integration.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/integrations/redis/__init__.py highThinkingQA/server/integrations/redis/client.py highThinkingQA/server/integrations/redis/service.py highThinkingQA/server/services/pending_overlay.py highThinkingQA/server_fastapi/routers/ask.py highThinkingQA/server/runtime/request_context.py highThinkingQA/server_fastapi/app.py highThinkingQA/config.py highThinkingQA/requirements.txt highThinkingQA/tests/test_pending_overlay.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_phase2_authority_integration.py
git commit -m "feat: add highthinkingqa redis overlay integration"
```

### Task 10: Legacy Retirement And Stabilization

**Files:**
- Modify: `fastQA/app/services/chat_persistence.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Modify: `highThinkingQA/server/services/conversation_context_service.py`
- Modify: `docs/superpowers/specs/2026-03-22-conversation-authority-migration-design.md` (only if implementation proves a spec gap)
- Modify: `docs/superpowers/plans/2026-03-22-conversation-authority-migration.md` (checklist status only during execution)

- [ ] **Step 1: Write failing regression tests that legacy active-path imports are gone**

Test cases to add:
- fastQA active authority path no longer imports or calls legacy `conversation_service`
- highThinkingQA active authority path no longer imports or calls legacy `conversation_service` for ask persistence or snapshot reads
- compatibility flags can be retired without changing runtime behavior

Run:
```bash
pytest fastQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_ask_service_executor.py -v
```
Expected: FAIL until legacy active-path dependencies are retired.

- [ ] **Step 2: Remove remaining active-path legacy dependencies and compatibility leftovers**

Implementation notes:
- Retire active-path legacy conversation authority usage in both QA services.
- Keep only bounded rollback or migration-only compatibility logic if still required.
- Stabilize the final contract surface.

- [ ] **Step 3: Run stabilization suites**

Run:
```bash
pytest fastQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_ask_service_executor.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add fastQA/app/services/chat_persistence.py highThinkingQA/server_fastapi/routers/ask.py highThinkingQA/server/services/conversation_context_service.py
git commit -m "refactor: retire legacy conversation authority paths"
```

### Task 11: Final Verification And Integration Handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-03-22-conversation-authority-migration-design.md` (only if implementation reveals a spec mismatch)
- Modify: `docs/superpowers/plans/2026-03-22-conversation-authority-migration.md` (mark completed steps only during execution)

- [ ] **Step 1: Run the complete targeted verification set**

Run:
```bash
pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_live_public_service_integration.py public-service/backend/tests/test_health.py public-service/backend/tests/test_system_module.py -v
pytest fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_pending_overlay.py fastQA/tests/test_qa_generation_orchestrator.py -v
pytest highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_pending_overlay.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_phase2_authority_integration.py -v
```
Expected: PASS.

- [ ] **Step 2: Run stack-level smoke verification**

Run:
```bash
bash scripts/stop_all.sh
bash scripts/start_all.sh
bash scripts/status_all.sh
```
Expected:
- all required services start cleanly
- no split-authority config is active
- logs show authority client calls and assistant inbox worker health

- [ ] **Step 3: Manual smoke checklist**

Verify manually:
- create a conversation and ask a first-turn fastQA question
- immediately ask a follow-up before assistant materialization completes
- refresh and verify durable history comes from `public-service`
- repeat the same closed-loop verification for highThinkingQA after phase 2
- verify Redis outage degrades continuity without failing the ask
- verify replay of a failed assistant inbox event does not create duplicate assistant turns
- verify highThinking rollback-flag behavior keeps execution authority coupled and leaves overlay independently disableable
- verify highThinking degraded cases are diagnosable with the same authority/overlay signals as fastQA

- [ ] **Step 4: Commit final integration changes**

```bash
git add -A
git commit -m "feat: complete conversation authority migration"
```
