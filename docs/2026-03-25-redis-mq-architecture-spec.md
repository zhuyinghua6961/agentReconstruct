# Redis MQ Architecture Specification

> **Status:** Draft in progress. This document is being written incrementally while code discovery continues.
> **Scope:** `gateway`, `fastQA`, `highThinkingQA`, `public-service`
> **Date:** 2026-03-25

## 1. Goal

Define a detailed Redis-based MQ architecture for the current monorepo so that:

- existing synchronous request paths remain correct
- long-running and side-effect-heavy work can be decoupled safely
- current in-process worker and outbox patterns can evolve into durable queue-backed workers
- each service gets a clear producer/consumer contract, idempotency rule, retry policy, and rollout path

This spec is intentionally implementation-oriented and based on the current codebase, not a greenfield redesign.

## 2. Primary Decision

The message queue layer should use Redis Streams with Consumer Groups.

This spec explicitly does not use Redis Pub/Sub for business-critical flows, and does not treat Redis Lists as the primary queue abstraction.

### 2.1 Why Redis Streams

- the repository already depends on Redis in multiple services
- Streams provide durable append-only event logs
- Consumer Groups provide pending-entry tracking and multi-consumer coordination
- `XAUTOCLAIM` gives a practical recovery path for stuck consumers
- this matches the existing outbox/inbox/retry semantics already present in `public-service` and `highThinkingQA`

### 2.2 Why Not Pub/Sub

- no durable backlog
- disconnected consumers miss messages permanently
- not suitable for assistant finalization, chat JSON sync, upload processing, or ingest jobs

### 2.3 Why Not Lists As The Main Queue

- insufficient native visibility into pending messages
- weak multi-consumer coordination
- dead-letter and retry logic become application-defined too early

## 3. Architectural Principles

### 3.1 Preserve Synchronous Truth Paths

The following paths remain synchronous:

- `gateway` route decision, file-context resolution, clarification, and SSE passthrough
- `public-service` auth and quota enforcement
- authority user-turn write and context snapshot read
- current interactive `ask` and `ask_stream` execution paths in `fastQA` and `highThinkingQA`

MQ should be introduced around side effects, background processing, retries, projection, and prewarming, not around current request correctness boundaries.

### 3.2 At-Least-Once Delivery, Application-Level Idempotency

Every consumer must assume duplicate delivery.

Redis Streams will provide at-least-once delivery semantics. Business correctness must come from application-level idempotency keys and state transitions.

### 3.3 Stable Business References, Not Ephemeral Local Paths

Cross-process messages must not rely on machine-local temporary paths as the primary source of truth.

Preferred payload references:

- `conversation_id`
- `user_id`
- `file_id`
- `trace_id`
- `json_version`
- `storage_ref`
- `object_name`
- `content_hash`
- stable configuration-derived identifiers

Local paths may be included only as optional optimization hints.

### 3.4 Per-Entity Ordering Where Needed

Global ordering is unnecessary.

Ordering should be preserved only for entities that require it:

- by `conversation_id` for assistant finalization and chat JSON progression
- by `file_id` for upload processing and cleanup
- by `job_id` for ingest orchestration

Phase-1 ordering mechanism is explicit:

- ordered streams may still use one shared Redis Stream plus one Consumer Group
- before mutating business state, the consumer must acquire a short-lived per-entity lease keyed by the ordered business id such as `lock:mq:conversation:<conversation_id>` or `lock:mq:ingest:<job_id>`
- if the lease cannot be acquired, the consumer must not process the message optimistically; it should leave the entry pending or retry later so the already-active owner preserves serialization
- success and terminal-failure paths must release the per-entity lease
- test coverage must include multi-consumer ordering for repeated messages on the same ordered entity

### 3.5 Incremental Migration

The migration should start by wrapping existing outbox/worker semantics, not by replacing all existing side effects at once.

## 4. Non-Goals

- replacing current `ask_stream` SSE with queue polling
- moving gateway forwarding itself behind MQ
- making auth or quota eventually consistent
- introducing Kafka-class event governance before the repository needs it

## 5. Document Structure

This spec will be completed in stages:

1. Core Redis Streams model and shared conventions
2. `public-service` stream contracts
3. `highThinkingQA` stream contracts
4. `fastQA` stream contracts
5. `gateway` audit and optional side-effect stream contracts
6. rollout, observability, failure handling, and testing strategy

## 6. Initial Candidate Streams

These are the current top-level candidate streams and will be specified in detail later in this document.

- `stream:conversation:assistant_finalize:v1`
- `stream:conversation:chat_json_sync:v1`
- `stream:conversation:file_process:v1`
- `stream:conversation:file_cleanup:v1`
- `stream:corpus:ingest_job:v1`
- `stream:fast:prewarm_asset:v1`
- `stream:gateway:route_decision_audit:v1`

## 7. Shared Message Envelope

All streams should use a consistent logical envelope, regardless of exact Redis field encoding.

```json
{
  "event_id": "uuid",
  "schema_version": 1,
  "event_type": "conversation.assistant.finalize",
  "source_service": "fastQA",
  "trace_id": "trace-xxx",
  "occurred_at": "2026-03-25T10:00:00+08:00",
  "entity_type": "conversation",
  "entity_key": "conversation:123",
  "payload": {}
}
```

## 8. Shared Delivery Semantics

- producer writes with `XADD`
- consumer reads with `XREADGROUP`
- successful processing ends with `XACK`
- stuck work is recovered with `XAUTOCLAIM`
- unrecoverable work is copied to a dedicated dead-letter stream and then acknowledged

## 9. Open Discovery Items

The following sections still need code-backed expansion:

- exact Redis key naming and prefix strategy per service
- whether to share one Redis deployment for cache + MQ or split logical DB/prefixes
- exact cutover point for `gateway` persistence hooks versus direct producer calls from QA services
- exact retirement path for residual `highThinkingQA` upload handling while converging canonical upload/file-processing ownership into `public-service`

## 10. Shared Redis Naming And Isolation Rules

### 10.1 Current Codebase Observations

The repository already uses Redis in three distinct ways:

- cache payload storage via JSON or scalar keys
- distributed or semi-distributed locking
- lightweight worker coordination and pending overlays

`public-service` and `fastQA` both already expose first-class Redis key factories with `cache()`, `lock()`, and `stream()` helpers; `fastQA` additionally exposes `pending()`. `highThinkingQA` currently exposes a key factory with `cache()` and `lock()` helpers, but not a first-class `stream()` helper. The MQ spec should normalize these naming capabilities across services while preserving existing prefixes during phase 1.

Code evidence:

- [public-service Redis key factory](/home/cqy/worktrees/highThinking/public-service/backend/app/integrations/redis/keys.py)
- [public-service Redis service](/home/cqy/worktrees/highThinking/public-service/backend/app/integrations/redis/service.py)
- [public-service Redis locks](/home/cqy/worktrees/highThinking/public-service/backend/app/integrations/redis/locks.py)
- [fastQA Redis client/bindings](/home/cqy/worktrees/highThinking/fastQA/app/integrations/redis/client.py)
- [fastQA singleflight lock use](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_cache/singleflight.py)
- [highThinkingQA Redis bindings and key factory](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/redis_client.py)

### 10.2 Prefix Model

Redis keys must remain prefixed by the existing per-service `redis_key_prefix` setting.

The prefix should continue to identify the deployment or workspace, not the message type. Stream semantics belong after the prefix.

Current default service prefixes in code today:

- `public-service`: `agentcode`
- `fastQA`: `fastqa`
- `highThinkingQA`: `highthinkingqa`

Examples:

- `agentcode:stream:conversation:assistant_finalize:v1`
- `agentcode:stream:conversation:chat_json_sync:v1`
- `fastqa:stream:qa:prewarm_asset:v1`
- `highthinkingqa:stream:corpus:ingest_job:v1`
- `fastqa:cache:qa_stage1:...`
- `agentcode:lock:file_process:123`

### 10.3 Reserved Top-Level Redis Namespaces

The spec reserves the following namespace layout under each prefix:

- `cache:` for cached data payloads
- `lock:` for mutexes and renewable leases
- `stream:` for Redis Streams
- `pending:` for short-lived overlay or transient projection keys, not for stream pending state
- `group:` only for derived configuration or metadata keys when needed, not the stream payload itself
- `dlq:` for dead-letter mirrors if a dedicated stream prefix is preferred
- `metric:` for queue health counters or gauges if Redis-backed metrics are needed

### 10.4 Stream Naming Rule

Stream names should follow this pattern:

`<prefix>:stream:<domain>:<action>:v<version>`

Examples:

- `agentcode:stream:conversation:assistant_finalize:v1`
- `agentcode:stream:conversation:file_process:v1`
- `agentcode:stream:gateway:route_decision_audit:v1`

Rules:

- use domain terms from the business model, not implementation details
- use one stream per event family, not one per consumer
- add explicit version suffixes so payload evolution can be rolled out safely
- never encode hostnames or ephemeral worker ids into stream names

### 10.5 Consumer Group Naming Rule

Consumer group names should follow this pattern:

`cg:<service>:<capability>`

Examples:

- `cg:public-service:assistant`
- `cg:public-service:json-sync`
- `cg:public-service:file-process`
- `cg:highthinkingqa:ingest`
- `cg:fastqa:prewarm`

Rules:

- the group should describe the logical consumption role
- different deployment replicas of the same logical worker must share the same group
- an individual process instance should use a unique consumer name inside the group

### 10.6 Consumer Name Rule

Consumer names should follow this pattern:

`<service>-<capability>-<instance>`

Examples:

- `public-service-assistant-pod-3`
- `highthinkingqa-ingest-worker-1`
- `fastqa-prewarm-hostA-2`

The consumer name should be operationally unique and disposable.

### 10.7 Deployment Topology Rule

Redis Streams consumer-group semantics are compatible with multi-instance deployment, but deployment topology must still be explicit.

Required deployment rules:

- multiple service replicas may share the same logical consumer group
- web-serving `gunicorn` worker processes must not implicitly become MQ consumers just because FastAPI startup hooks run in every worker process
- MQ consumers should run in dedicated worker processes or dedicated worker deployments, not inside every API-serving `gunicorn` worker by default
- if a service temporarily colocates API and MQ roles on the same host, enable them through an explicit worker-role flag and process entrypoint, not through generic web app startup
- consumer names should incorporate an operationally unique instance identifier and process identity so duplicated process launches are visible immediately

Initial rollout recommendation:

- `gateway`, `fastQA`, `highThinkingQA`, and `public-service` web `gunicorn` processes stay producer-only by default
- `public-service` assistant/json-sync consumers run in separate worker processes or deployments
- `highThinkingQA` ingest runs in a separate worker process or deployment
- `fastQA` prewarm runs in a separate worker process or deployment if enabled

### 10.8 Isolation Between Cache, Lock, And MQ

The spec assumes Redis may initially be shared by cache and MQ, but the namespaces must remain logically isolated.

Required isolation rules:

- no stream may reuse a cache key pattern
- no lock key may be derived from a stream name directly without a `lock:` segment
- stream consumers must not rely on cache TTL expiration as a correctness mechanism
- queue backlog monitoring must not read cache keys as a proxy for business progress

Recommended deployment rule for phase 1:

- same Redis deployment is acceptable
- same logical DB is acceptable if namespace prefixes are strictly enforced
- if queue traffic becomes materially heavy, move Streams to a separate logical DB or dedicated Redis deployment before touching application semantics

### 10.9 Shared Failure-Handling Model

All queue consumers should implement the same logical lifecycle:

1. read entries with `XREADGROUP`
2. decode and validate envelope
3. if the stream is ordered, acquire the required per-entity lease before mutating business state
4. check idempotency store or business state
5. execute side effect or state transition
6. on success, `XACK`
7. on transient failure, leave pending and rely on retry/claim policy
8. on terminal failure, copy the message to a dead-letter stream, then `XACK`
9. release any per-entity lease on every exit path

### 10.10 Pending Recovery Model

The default recovery model should use `XAUTOCLAIM`.

Suggested defaults for phase 1:

- idle threshold: 60 seconds for lightweight jobs
- idle threshold: 300 seconds for upload processing and JSON sync
- max claim batch: 50 to 200 depending on worker type
- claim loop should emit metrics for claimed, retried, dead-lettered, and stale-skipped work

For single-active workloads, add a separate leader or lease guard instead of relying on Consumer Group fairness. This is especially required for `highThinkingQA` ingest while the service still enforces a single-running-job model.

### 10.11 Consumer-Group Bootstrap Rule

Consumer-group bootstrap must be idempotent and rollout-mode aware.

Required rules:

- the first worker that needs a group should call `XGROUP CREATE <stream> <group> <offset> MKSTREAM`
- `BUSYGROUP` must be treated as successful concurrent bootstrap, not as a fatal startup error
- shadow-mode groups that should observe only newly produced traffic must start at `$`
- cutover groups that must drain existing backlog must start at `0`
- the chosen start offset is part of rollout config and must be visible in logs or status output
- startup tests must cover concurrent bootstrap and repeated process restart

### 10.11 Dead-Letter Model

Each business stream should have a paired dead-letter stream:

- `agentcode:stream:conversation:assistant_finalize:dlq:v1`
- `agentcode:stream:conversation:chat_json_sync:dlq:v1`
- `agentcode:stream:conversation:file_process:dlq:v1`

Dead-letter entries must include:

- original envelope
- `failed_at`
- `failed_by`
- `failure_class`
- `failure_message`
- `attempt_count`

### 10.12 Metrics To Standardize

Every queue worker should emit at least these counters or gauges:

- produced messages
- consumed messages
- successful messages
- duplicate-skipped messages
- stale-skipped messages
- retried messages
- dead-lettered messages
- pending backlog size
- oldest pending idle milliseconds
- processing latency histogram

### 10.13 Relationship To Existing Lock Usage

Current code already uses Redis locks for cache singleflight and upload-processing leases. Those patterns should remain separate from stream delivery.

Redis locks should be used only for:

- per-file exclusive processing
- per-conversation exclusive materialization when the business state requires it
- singleflight around cache fill

Redis Streams should not be treated as a substitute for exclusive business locks.

## 11. Public-Service And Gateway Boundary Specification

### 11.1 Boundary Summary

`public-service` is the authoritative owner of conversation truth, auth, quota, and the long-term public conversation/file API surface. `gateway` is not the authority; it is a routing and proxy layer with compatibility-era persistence side effects still present on some paths.

Current-state reality and target-state intent are different and must both be represented in this spec:

- target state: authority writes are owned by `public-service`, and execution services publish authority-shaped events directly or through the authority acceptance API
- current state: `gateway` still persists user and assistant messages for non-`thinking` execution paths through the compatibility public message API, while `highThinkingQA` already contains a closer-to-target producer shape for thinking execution side effects only
- current state: `highThinkingQA` still contains residual local upload registration and local chat-json/outbox behavior from the copied migration closure; this is transitional compatibility debt, not long-term ownership of file/upload/document truth

This means the Redis MQ design must preserve the following hard rule:

- authority truth remains owned by `public-service`
- `gateway` may emit side-effect or audit events, but must not remain the long-term durable producer of conversation truth once rollout is complete
- rollout sections must distinguish current producers from target producers explicitly

Code evidence:

- [gateway QA routing path](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py)
- [gateway conversation persistence side effects](/home/cqy/worktrees/highThinking/gateway/app/services/conversation_persistence.py)
- [public-service authority internal API](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py)
- [public-service assistant async acceptance](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py)

### 11.2 Stream: `stream:conversation:assistant_finalize:v1`

**Purpose**

Carry assistant final-answer events from QA services into the authoritative conversation system for asynchronous materialization.

**Why this stream exists**

The current authority contract already distinguishes between:

- synchronous user-turn writes
- asynchronous assistant final-event acceptance

The existing internal endpoint `POST /internal/conversations/{conversation_id}/messages/assistant-async` returns `202 accepted`, and `ConversationService.accept_authority_assistant_async()` enqueues an assistant task rather than immediately materializing the message. That is already queue semantics in application form.

**Producer**

Phase 1 producer options:

- keep `fastQA` and `highThinkingQA` producing via the existing HTTP internal API, and let `public-service` bridge accepted tasks into Redis Streams internally
- or let `fastQA` and `highThinkingQA` publish directly once security and rollout concerns are settled

Preferred phase-1 producer:

- `public-service` internal API acceptance path remains the write boundary
- `public-service` publishes to Redis Stream after validating source, conversation existence, and idempotency contract

**Consumer**

- consumer group: `cg:public-service:assistant`
- worker role: authority assistant materializer
- logical owner: `public-service`

**Required payload fields**

- `conversation_id`
- `user_id`
- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `idempotency_key`
- `final_event.done_seen`
- `final_event.answer_text`
- `final_event.steps`
- `final_event.references`
- `final_event.used_files`
- `final_event.timings`

**Idempotency key**

- business key: `conversation_id + trace_id + assistant`
- concrete contract already exists as `"{conversation_id}:{trace_id}:assistant"`

**Ordering rule**

- preserve order by `conversation_id`
- assistant materialization must not overtake the corresponding user-turn for the same trace

**Failure handling**

- validation failures are terminal and should route to DLQ
- missing conversation may be treated as terminal unless rollout explicitly allows short delayed retries
- duplicate assistant events should be acknowledged as no-op success

**Current code evidence**

- [public-service internal assistant async API](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py#L199)
- [public-service assistant acceptor](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1439)
- [public-service task materializer](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1515)
- [fastQA authority client](/home/cqy/worktrees/highThinking/fastQA/app/services/conversation_authority_client.py#L190)

### 11.3 Stream: `stream:conversation:chat_json_sync:v1`

**Purpose**

Synchronize locally persisted conversation JSON documents to object storage after initial write failure or deferred sync.

**Why this stream exists**

Current code in both `public-service` and `highThinkingQA` already follows the same retry pattern:

1. write chat JSON locally
2. update DB index with local path, storage ref, hash, size, version, sync status
3. if remote sync is not `ok`, enqueue retry work into a DB outbox

That implementation similarity is current-state evidence only. The target architecture still keeps conversation/document truth in `public-service`, while `highThinkingQA` local JSON/outbox logic is migration-era residue to be bridged or retired.

This is the strongest current candidate for Redis Streams because the code already models:

- versioned tasks
- retry lifecycle
- stale version skipping
- content hashing

**Producer**

- `public-service` conversation document persistence path
- if `highThinkingQA` still emits this event family during migration, it should do so only as a temporary bridge into the same `public-service`-owned contract

**Consumer**

- consumer group: `cg:public-service:json-sync`
- worker role: chat JSON sync worker

**Required payload fields**

- `conversation_id`
- `user_id`
- `json_version`
- `object_name`
- `content_hash`
- `staging_ref` or durable source reference for the JSON blob
- optional `local_path_hint`
- `initial_sync_status`

**Idempotency key**

- `conversation_id + json_version`

**Ordering rule**

- preserve order by `conversation_id`
- consumers may safely mark older versions as stale if a newer synchronized version already exists

**Failure handling**

- missing local hint is not fatal if `staging_ref` is resolvable
- stale version after successful upload should be acknowledged as stale success, not failure
- repeated upload failure moves to DLQ after configured attempts

**Current code evidence**

- [public-service JSON write and outbox enqueue](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L882)
- [public-service outbox repository](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/outbox.py#L65)
- [public-service outbox worker](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/outbox_worker.py#L85)

### 11.4 Gateway Synchronous Boundary

The following gateway path must remain synchronous and outside Redis MQ:

- fetch conversation file metadata
- resolve file context
- compute route decision
- decide clarification versus execution
- forward request to the selected backend
- preserve SSE passthrough for `ask_stream`

**Reason**

This path determines the current request's semantics and is explicitly constrained by the existing gateway protocol docs. Introducing queue indirection here would change correctness, latency, and stream semantics rather than merely decoupling side effects.

**Current code evidence**

- [gateway request resolution](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L39)
- [gateway route decision service](/home/cqy/worktrees/highThinking/gateway/app/services/route_decision.py)
- [gateway proxy streaming](/home/cqy/worktrees/highThinking/gateway/app/services/proxy.py#L123)
- [gateway forwarding protocol](/home/cqy/worktrees/highThinking/gateway/docs/gateway_forwarding_protocol.md)

### 11.5 Optional Stream: `stream:gateway:route_decision_audit:v1`

**Purpose**

Emit non-authoritative route-decision audit events for observability.

**Producer**

- `gateway` after `_resolve()` and route decision are complete

**Consumer**

- audit/metrics worker only

**Required payload fields**

- `trace_id`
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `selected_file_ids`
- `primary_file_id`
- `provider_name`

**Idempotency key**

- `trace_id + requested_mode + actual_mode + route`

**Ordering rule**

- no strong ordering requirement

**Important limitation**

This stream must never become a source of truth for request execution. It is telemetry only.

## 12. HighThinkingQA Specification

### 12.1 Boundary Summary

`highThinkingQA` currently has two distinct asynchronous patterns:

- assistant persistence dispatch after interactive completion
- durable-like retry behavior around chat JSON object-storage sync
- in-memory job orchestration for ingest

Its long-term role is still thinking-mode QA execution only. The copied upload/chat-json/conversation code in this repo slice is migration residue, not evidence that `highThinkingQA` should own file-QA, upload truth, or document truth.

This service is therefore a strong Redis Streams candidate for backgroundization, but not for replacing the current interactive ask/streaming contract.

Code evidence:

- [highThinking ask router](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/ask.py)
- [highThinking chat persistence](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py)
- [highThinking conversation outbox repository](/home/cqy/worktrees/highThinking/highThinkingQA/server/repositories/conversation_outbox_repository.py)
- [highThinking ingest service](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/ingest_service.py)

### 12.2 Stream: `stream:conversation:assistant_finalize:v1`

`highThinkingQA` should publish the same logical assistant-finalize event family as `fastQA`, but the canonical contract must match the existing authority async API.

**Producer**

- `persist_assistant_summary()` after `done_seen == true`

**Consumer**

- `public-service` authority assistant materializer group in the canonical rollout

**Required payload fields**

- `conversation_id`
- `user_id`
- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `idempotency_key`
- `final_event.done_seen = true`
- `final_event.answer_text`
- `final_event.steps`
- `final_event.references`
- `final_event.used_files`
- `final_event.timings`

Optional transport metadata may include QA-local projection helpers, but the authority consumer must not require them for correctness.

**Idempotency key**

- `conversation_id + trace_id + assistant`

**Ordering rule**

- preserve order by `conversation_id`

**Failure recovery**

- duplicate publish is acceptable if downstream authority materialization is idempotent
- publish failure should retain current overlay-based UX protection until rollout is complete

**Current code evidence**

- [highThinking ask persistence hook](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/ask.py#L173)
- [highThinking assistant persistence dispatcher](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L464)

### 12.3 Stream: `stream:conversation:chat_json_sync:v1`

`highThinkingQA` should emit the same logical chat-JSON-sync event contract as `public-service` only as a temporary bridge while residual local persistence code still exists. The target owner of conversation/document sync remains `public-service`.

**Producer**

- residual `highThinkingQA` conversation document persistence path when `sync_status != ok`

**Consumer**

- temporary bridge consumer: `cg:highthinkingqa:json-sync` only while the copied closure remains active
- target consumer/worker: a `public-service`-owned conversation-sync worker once conversation/document truth is fully converged

**Required payload fields**

- `conversation_id`
- `user_id`
- `json_version`
- `object_name`
- `content_hash`
- `staging_ref` or durable blob reference
- optional `local_path_hint`
- `initial_sync_status`

**Idempotency key**

- `conversation_id + json_version`

**Ordering rule**

- preserve order by `conversation_id`
- stale earlier versions must be skipped safely

**Failure recovery**

- use pending recovery via `XAUTOCLAIM`
- move terminal failures to DLQ
- treat stale-version completion as successful skip

**Current code evidence**

- [highThinking outbox enqueue path](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/conversation_service.py#L442)
- [highThinking outbox repository](/home/cqy/worktrees/highThinking/highThinkingQA/server/repositories/conversation_outbox_repository.py#L32)
- [highThinking outbox worker](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/chat_json_outbox_worker.py)

### 12.4 Stream: `stream:corpus:ingest_job:v1`

The current `IngestService` is a clear candidate for queue-backed job orchestration.

**Why this stream exists**

`IngestService.create_ingest_job()` currently creates an in-memory job object and launches a daemon thread. This means job durability, crash recovery, and cross-instance execution do not exist yet.

**Producer**

- ingest API path via `create_ingest_job()`

**Consumer**

- `cg:highthinkingqa:ingest`
- dedicated ingest orchestrator worker

**Required payload fields**

- `job_id`
- `parse_method`
- `skip_parsed`
- `max_papers`
- `start`
- `end`
- request metadata such as `submitted_by` if later needed

**Idempotency key**

- `job_id`

**Ordering rule**

- no global ordering required
- optional subtask fan-out may later order by paper or DOI, not by whole stream

**Failure recovery**

- retry transient OCR/vector-store failures
- on terminal failure, persist job status as failed and move envelope to DLQ
- if downstream work later fans out per paper, paper-level dedupe should use DOI or source document identity

**Single-active lease protocol**

- lease key: `lock:mq:highthinkingqa:ingest:active`
- owner token: `<service>:<instance>:<pid>:<consumer>`
- initial TTL: 30 seconds
- renew cadence: every 10 seconds while work is active
- renewal must verify the current owner token before extending the TTL
- if the owner loses the lease or fails renewal, it must stop starting new ingest steps, mark the local run as aborted-by-lease-loss, and leave the stream message pending for the next valid owner to reclaim
- a resumed owner must re-check persisted job state before continuing work

**Current code evidence**

- [highThinking ingest service](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/ingest_service.py)
- [highThinking ingest pipeline](/home/cqy/worktrees/highThinking/highThinkingQA/ingest/pipeline.py)

### 12.5 Interactive Ask Boundary That Must Stay Synchronous

The current `ask` and `ask_stream` execution contract must remain synchronous for the interactive API.

The following steps must not be pushed behind Redis MQ in the current API surface:

- request parsing and auth enforcement
- conversation-context loading
- question rewrite
- stream creation and SSE emission
- incremental `content` and `step` emission
- final `done` event generation

**Reason**

The current implementation depends on in-process event emission and immediate client-visible streaming semantics. Replacing this with queue polling would be a product and protocol change, not an internal refactor.

## 13. FastQA Specification

### 13.1 Boundary Summary

`fastQA` should use Redis MQ primarily for side effects and prewarming, not for the current answer-generation request path.

Current rollout state must be called out explicitly:

- by default, `fastQA` does not enable authority-backed chat persistence in local code defaults
- on current non-`thinking` paths, `gateway` still performs compatibility persistence into `public-service`
- therefore `fastQA` assistant-finalize streaming should be treated as a target-state MQ contract, not assumed current-state default behavior

The strongest candidates are:

- assistant finalization into authority
- asset prewarming for uploaded files, paper PDFs, PDF text, and workbook/profile derivation

Code evidence:

- [fastQA router](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py)
- [fastQA chat persistence](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py)
- [fastQA upload materializer](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/upload_materializer.py)
- [fastQA PDF pipeline](/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/pdf_pipeline.py)
- [fastQA workbook loader](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py)

### 13.2 Stream: `stream:conversation:assistant_finalize:v1`

`fastQA` should publish the same assistant-finalize event family as `highThinkingQA` in the target rollout, but the canonical payload must match the existing authority async contract.

**Producer**

- target state: stream wrapper finalization after `done_seen == true`
- current state caveat: this is not yet the default fast-path producer because `gateway` still performs compatibility persistence for non-`thinking` paths

**Consumer**

- authoritative conversation materializer in `public-service`

**Required payload fields**

- `conversation_id`
- `user_id`
- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `idempotency_key`
- `final_event.done_seen = true`
- `final_event.answer_text`
- `final_event.steps`
- `final_event.references`
- `final_event.used_files`
- `final_event.timings`

Optional extension fields such as `file_selection`, `source_scope`, or `reference_objects` may be included for downstream analytics or projection, but `public-service` authority materialization must not require them.

**Idempotency key**

- `conversation_id + trace_id + assistant`

**Ordering rule**

- preserve order by `conversation_id`

**Current code evidence**

- [fastQA stream summary and persistence](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L918)
- [fastQA assistant persistence](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L322)

### 13.3 Stream Family: `stream:fast:prewarm_asset:v1`

This stream family should cover prewarming only. It should not be required for a request to succeed.

Subtypes may be represented either by one `asset_kind` field or separate streams later if backlog isolation becomes necessary.

Suggested `asset_kind` values:

- `uploaded_file_materialize`
- `paper_pdf_localize`
- `pdf_text_extract`
- `workbook_profile`

**Producer**

- `fastQA` request path after route resolution identifies future-use assets
- optional follow-up producer from completed stage results such as DOI discovery

**Consumer**

- `cg:fastqa:prewarm`
- prewarm worker local to `fastQA`

**Required payload fields**

Common fields:

- `asset_kind`
- `trace_id`
- `requested_mode`
- `actual_mode`

Kind-specific examples:

- uploaded file materialize: `file_id`, `storage_ref`, `status_updated_at`, `file_type`
- paper PDF localize: `doi`, `papers_epoch`
- PDF text extract: `file_id`, `file_signature`, `max_pages`, `exclude_references`
- workbook profile: `file_id`, `file_signature`, `file_type`

**Idempotency key**

- `asset_kind + stable_signature`

**Ordering rule**

- no global ordering required
- if the same asset is enqueued repeatedly, later duplicates should collapse via idempotency or cache hit

**Relationship to cache/singleflight**

This stream complements the existing cache/singleflight model.

- queue-driven prewarm reduces miss frequency
- current request path still keeps synchronous fallback on cache miss
- singleflight remains necessary to prevent duplicate expensive computation when prewarm has not completed in time

### 13.4 FastQA Ask Boundary That Must Stay Synchronous

The current `ask` and `ask_stream` generation path must remain synchronous.

The following remain outside MQ:

- request adaptation and validation
- authority user-turn preflight when required by current route behavior
- conversation-context loading
- file-context decision and clarification behavior
- the actual answer-generation stream

**Reason**

The route currently emits direct JSON or SSE semantics with stage events and streamed content. Redis MQ can support prewarm and write-behind, but should not replace the primary request execution contract.

## 14. Refined Boundary Contracts From Code Review

This section tightens the earlier design using direct code-backed constraints from all four service reviews.

### 14.1 Contract: `authority.user.write.v1` Is A Synchronous Boundary

This contract must remain synchronous.

It is the boundary that makes the current user turn visible to the authority conversation model before downstream context reads and assistant finalization.

**Producer**

- target state: the canonical producer should be the actual QA execution service entry point
- current state: `gateway` still produces compatibility user-message writes for non-`thinking` execution paths, while `highThinkingQA` is closer only in producer shape for thinking execution side effects and is not the target owner of upload/file/document truth
- rollout must therefore model `gateway` as the phase-0 compatibility producer and `fastQA` / `highThinkingQA` as the target producers

**Consumer**

- `public-service` internal authority user-write endpoint and service logic

**Required fields**

- `conversation_id`
- `user_id`
- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `idempotency_key`
- `message.role = user`
- `message.content`
- `context_hints`

**Idempotency key**

- exact contract: `"{conversation_id}:{trace_id}:user"`

**Ordering rule**

- must complete before the corresponding assistant finalization for the same `trace_id`
- if the request path reads context snapshot after user write, the context read must observe the just-written user turn

**Why it stays synchronous**

This is not background side-effect data. It is the correctness boundary for the current conversation state seen by the live request.

**Code evidence**

- [public-service authority source policy](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py#L86)
- [public-service authority idempotency enforcement](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py#L107)
- [public-service append user message endpoint](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py#L113)
- [public-service add authority user message](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1333)
- [fastQA authority user write path](/home/cqy/worktrees/highThinking/fastQA/app/services/conversation_authority_client.py#L126)
- [highThinking context loading path](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation_context_service.py#L137)

### 14.2 Contract: `authority.assistant.finalized.v1` Is The Canonical Async Authority Event

This contract should be the main business stream of the first MQ rollout.

**Producer**

- target state: `fastQA` after stream completion and summary finalization, and `highThinkingQA` after `done_seen == true`
- current state: fast-path compatibility persistence may still be emitted by `gateway` rather than `fastQA` directly
- during phase 1, direct publish may still be mediated by `public-service` internal acceptance API

**Consumer**

- `public-service` authority assistant materializer
- consumer group: `cg:public-service:assistant`

**Required fields**

- `conversation_id`
- `user_id`
- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `idempotency_key`
- `final_event.done_seen = true`
- `final_event.answer_text`
- `final_event.steps`
- `final_event.references`
- `final_event.used_files`
- `final_event.timings`

**Idempotency key**

- exact contract: `"{conversation_id}:{trace_id}:assistant"`

**Ordering rule**

- must occur after `authority.user.write.v1` for the same trace
- same conversation should be processed FIFO where practical

**Why it can be asynchronous**

The answer is only authoritative after `done`. The current public-service code already models this path as `202 accepted` plus worker materialization, which is exactly the business shape of a queue consumer.

**Code evidence**

- [public-service assistant async endpoint](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/internal_api.py#L199)
- [public-service assistant async acceptor](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1439)
- [public-service assistant inbox enqueue](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/repository.py#L388)
- [public-service assistant worker](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/assistant_inbox.py#L58)
- [public-service assistant task materializer](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/conversation/service.py#L1515)
- [fastQA assistant finalization path](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L322)
- [highThinkingQA assistant finalization path](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L464)

### 14.3 Contract: `public.conversation_files.lookup.v1` Is A Synchronous Query Boundary

This contract must remain synchronous.

**Producer**

- `gateway` QA request resolution path

**Consumer**

- `public-service` conversation files list/detail APIs

**Required request fields**

- `conversation_id`
- auth headers
- `X-Trace-Id`

**Required response fields**

At minimum:

- `file_id`
- `file_type`
- `file_name`

Recommended because current route logic benefits from them:

- `file_status`
- `parse_status`
- `index_status`
- `processing_stage`
- `file_meta.columns`
- `display_no`
- `file_no`

**Why it stays synchronous**

`gateway` route decision, file selection, clarification, and mode override all depend on this response immediately. This is request input, not post-processing.

**Code evidence**

- [gateway resolve path](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L39)
- [gateway public HTTP file provider](/home/cqy/worktrees/highThinking/gateway/app/providers/conversation_files/public_http.py#L35)
- [gateway forwarding protocol file sections](/home/cqy/worktrees/highThinking/gateway/docs/gateway_forwarding_protocol.md#L550)

### 14.4 Contract: `public.auth_and_quota.request.v1` Stays Pure Synchronous Passthrough

This spec intentionally does not define an MQ contract for gateway-to-public auth or quota requests.

**Why**

These calls are part of current request admission and token semantics. Delaying them behind a queue would break immediate visibility guarantees.

**Code evidence**

- [public-service auth service register/login/token paths](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/auth/service.py#L321)
- [public-service quota check path](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/quota/service.py#L325)
- [gateway passthrough forwarding](/home/cqy/worktrees/highThinking/gateway/app/services/proxy.py#L92)

### 14.5 Contract: `gateway.qa.audit.v1` Is Telemetry Only

This optional stream is valid, but it must remain outside authority truth handling.

**Producer**

- `gateway` clarification generation
- `gateway` upstream status and stream error generation
- `gateway` final downstream response normalization

**Consumer**

- observability or audit worker only

**Required fields**

- `trace_id`
- `conversation_id` when available
- `requested_mode`
- `actual_mode`
- `route`
- `backend`
- `event_kind`
- `status_code`
- `retriable`
- optional `selected_file_ids`
- optional `file_selection`

**Idempotency key**

- `trace_id + event_kind`

**Why it can be asynchronous**

These events describe gateway-generated semantics and diagnostics. They are not conversation authority state.

**Code evidence**

- [gateway clarification JSON](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L105)
- [gateway clarification stream](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L121)
- [gateway upstream stream error](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L139)
- [gateway upstream status error](/home/cqy/worktrees/highThinking/gateway/app/routers/qa.py#L155)

## 15. HighThinkingQA Detailed Stream And Boundary Design

### 15.1 Contract: `ask.user_turn.write` Remains Synchronous In HighThinkingQA

`highThinkingQA` currently may dispatch persistence through an in-process ordered dispatcher, but architecturally the user turn is still a synchronous truth boundary.

**Reason**

The same request later loads and merges conversation context. If the user turn were moved to durable async-only submission, the current request could read stale context.

**Code evidence**

- [highThinking ask user persist hook](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/ask.py#L173)
- [highThinking user persistence path](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/chat_persistence.py#L403)
- [highThinking ordered dispatcher](/home/cqy/worktrees/highThinking/highThinkingQA/server/runtime/ordered_task_dispatcher.py#L13)
- [highThinking context builder](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation_context_service.py#L137)

### 15.2 Contract: `conversation.json.sync_outbox` Maps Cleanly To Redis Streams

This is the clearest legacy path to bridge out of residual `highThinkingQA` local document-sync behavior and into a `public-service`-owned contract.

**Producer**

- conversation document persistence path when `sync_status != ok`

**Consumer**

- temporary bridge consumer: `cg:highthinkingqa:json-sync` while residual local document-sync code still exists
- target consumer: `public-service` conversation-sync worker once document truth is fully converged

**Required fields**

- `conversation_id`
- `user_id`
- `json_version`
- `local_path` or `staging_ref`
- `object_name`
- `content_hash`
- `last_error`

**Idempotency key**

- `conversation_id + json_version`

**Ordering rule**

- version-monotonic per conversation
- stale versions must be acknowledged as stale success

**Failure recovery**

- reclaim stuck processing
- exponential backoff
- dead-letter after max attempts
- allow post-upload stale detection

**Code evidence**

- [highThinking conversation JSON persist and enqueue](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/conversation_service.py#L410)
- [highThinking JSON store](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/chat_json_store.py#L122)
- [highThinking outbox repository](/home/cqy/worktrees/highThinking/highThinkingQA/server/repositories/conversation_outbox_repository.py#L32)
- [highThinking outbox worker](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/chat_json_outbox_worker.py#L138)

### 15.3 Contract: `upload.file_registered` Is Synchronous, But It Must Stop There

The upload API should define a synchronous registration boundary, not silently turn into an ingest job submission path.

This section documents the current residual `highThinkingQA` upload registration path for migration analysis only. It does not mean `highThinkingQA` should remain the long-term upload/file authority. The target owner of upload registration, file metadata truth, and conversation file-list truth should be `public-service`.

**Producer**

- upload endpoints in `server_fastapi/routers/upload.py`

**Consumer**

- conversation file metadata persistence via `conversation_service.add_uploaded_file()`

**Required fields**

- `user_id`
- `conversation_id`
- `file_type`
- `file_name`
- `local_path`
- `storage_ref`
- `content_type`
- `size_bytes`
- `uploaded_at`

**Idempotency key**

- current stable business key after persistence is `conversation_id + file_id`
- future API contract should add `client_upload_id` for retry-safe registration

**Ordering rule**

- local save
- object-storage mirror
- DB row insert
- chat JSON file-list update
- response

**Why this is not the ingest stream**

The current response explicitly returns upload registration state such as `parse_status=uploaded` and `index_status=pending`; it does not run the ingest pipeline on conversation files. This path should be treated as residual registration behavior, not as proof that `highThinkingQA` owns long-term file-QA responsibility.

**Code evidence**

- [highThinking upload router](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/upload.py#L60)
- [highThinking upload response construction](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/upload.py#L88)
- [highThinking add uploaded file](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/conversation/conversation_service.py#L903)
- [highThinking repository file persistence](/home/cqy/worktrees/highThinking/highThinkingQA/server/repositories/conversation_repository.py#L312)

### 15.4 Stream: `stream:corpus:ingest_job:v1` Must Remain Separate From Upload Registration

**Producer**

- ingest API via `create_ingest_job()`

**Consumer**

- `cg:highthinkingqa:ingest`

**Required fields**

- `job_id`
- `parse_method`
- `skip_parsed`
- `max_papers`
- `start`
- `end`
- request source metadata

**Idempotency key**

- `job_id`

**Ordering rule**

- job-level serialization should be preserved initially because the current service enforces a single running job model

**Why it must stay separate from upload**

`run_pipeline()` operates on the global papers corpus under `PAPERS_DIR`, not on per-conversation uploaded files.

**Code evidence**

- [highThinking ingest API](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/ingest.py#L32)
- [highThinking ingest service](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/ingest_service.py#L42)
- [highThinking pipeline corpus path](/home/cqy/worktrees/highThinking/highThinkingQA/ingest/pipeline.py#L203)

### 15.5 Why `ask` And `ask_stream` Must Not Become Queue Jobs

The current interactive ask path is a synchronous HTTP contract with direct SSE semantics.

It must not be replaced by Redis queue polling for the current API surface because:

- it creates and holds the live stream in-process
- it releases concurrency slots only after stream cleanup
- it performs context load before answer generation
- current execution context is built from conversation history/summary rather than uploaded-file authority inputs
- `used_files` is returned as assistant metadata, and current code does not make `highThinkingQA` the owner of file-selection or file-QA truth
- assistant persistence is explicitly a post-`done` side effect, not the transmission mechanism itself

**Code evidence**

- [highThinking stream builder](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/ask.py#L229)
- [highThinking ask_stream endpoints](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/ask.py#L400)
- [highThinking assistant persistence guard](/home/cqy/worktrees/highThinking/highThinkingQA/server_fastapi/routers/ask.py#L204)

## 16. FastQA Detailed Stream And Boundary Design

### 16.1 Contract: `authority.user_turn.preflight` Remains Synchronous

`fastQA` performs user-turn persistence before main execution when authority-backed context semantics are in play. This remains a synchronous correctness boundary.

**Reason**

The same request may immediately read authority context snapshot. `pending_overlay` only compensates assistant eventual consistency and cannot replace synchronous user-turn visibility.

**Code evidence**

- [fastQA ask user persist hook](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L1006)
- [fastQA context load path](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L233)
- [fastQA user persistence](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L285)
- [fastQA authority read path](/home/cqy/worktrees/highThinking/fastQA/app/services/conversation_authority_client.py#L126)

### 16.2 Stream: `stream:conversation:assistant_finalize:v1`

This is the first MQ candidate inside `fastQA`, but it must be described as a target-state contract rather than a current default producer path.

**Producer**

- target state: `_wrap_stream_with_tap()` after stream completion and summary collection
- current state caveat: non-`thinking` persistence may still be emitted by `gateway` until the compatibility write path is removed

**Consumer**

- `public-service` authority assistant materializer

**Required fields**

- `conversation_id`
- `user_id`
- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `idempotency_key`
- `final_event.done_seen = true`
- `final_event.answer_text`
- `final_event.steps`
- `final_event.references`
- `final_event.used_files`
- `final_event.timings`

**Optional extension fields**

- `file_selection`
- `source_scope`
- `reference_objects`

**Idempotency key**

- `conversation_id + trace_id + assistant`

**Ordering rule**

- FIFO by `(user_id, conversation_id)`
- must occur after user-turn preflight for the same trace

**Important read-side rule**

- keep `pending_overlay` semantics: write the overlay first, then let authority persistence converge asynchronously

**Code evidence**

- [fastQA stream wrapper](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L918)
- [fastQA assistant persistence](/home/cqy/worktrees/highThinking/fastQA/app/services/chat_persistence.py#L322)
- [fastQA ordered dispatcher](/home/cqy/worktrees/highThinking/fastQA/app/services/ordered_dispatcher.py#L22)
- [fastQA pending overlay](/home/cqy/worktrees/highThinking/fastQA/app/services/pending_overlay.py#L62)

### 16.3 Stream: `stream:fast:prewarm_asset:v1` With `asset_kind=workbook_profile`

This prewarm flow should materialize uploaded tabular files and build workbook/profile state before first heavy use.

**Producer**

- file selected for `tabular_qa` or `hybrid_qa`
- upload completion or file-list changes where proactive warmup is acceptable

**Consumer**

- `cg:fastqa:prewarm`

**Required fields**

- `asset_kind = workbook_profile`
- `file_id`
- `file_name`
- `file_type`
- `storage_ref`
- optional `local_path`
- `status_updated_at`

**Idempotency key**

- `build_file_signature()` result, or an equivalent stable signature before local materialization

**Ordering rule**

- de-dup only per file; no conversation-wide FIFO required

**Relationship to existing cache**

The current workbook cache is local-process memory only. Stream-driven prewarm improves miss rates, but the request path must retain synchronous fallback because answer planning and execution need an in-memory workbook object immediately.

**Code evidence**

- [fastQA materialize uploaded file](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/upload_materializer.py#L99)
- [fastQA workbook signature and cache](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py#L64)
- [fastQA workbook load](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/workbook_loader.py#L214)
- [fastQA tabular answer path](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_tabular/service.py#L382)

### 16.4 Stream: `stream:fast:prewarm_asset:v1` With `asset_kind=pdf_text_extract`

This flow should precompute PDF text and populate the existing PDF text cache.

**Required fields**

- `asset_kind = pdf_text_extract`
- `file_id` or stable PDF identity
- `storage_ref` or `local_path`
- `file_name`
- `max_pages`
- `exclude_references`

**Idempotency key**

- the same signature used by `build_pdf_text_cache_key()` / `build_pdf_text_lock_key()`

**Relationship to existing cache/singleflight**

This is a classic prewarm-only queue. The request path must still synchronously compute on miss because `pdf_qa` and `hybrid_qa` need the extracted text immediately.

**Code evidence**

- [fastQA PDF content loader](/home/cqy/worktrees/highThinking/fastQA/app/services/file_qa_helpers.py#L78)
- [fastQA PDF cache](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_cache/pdf_cache.py#L36)
- [fastQA singleflight](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_cache/singleflight.py#L40)
- [fastQA PDF route path](/home/cqy/worktrees/highThinking/fastQA/app/services/file_routes.py#L211)

### 16.5 Stream: `stream:fast:prewarm_asset:v1` With `asset_kind=kb_stage3_pdf_chunks`

This is the highest-value KB prewarm candidate because its cache key is more reusable than stage25.

**Required fields**

- `asset_kind = kb_stage3_pdf_chunks`
- `dois`
- `max_chunks_per_doi`
- `route_hint`
- runtime location context when needed for paper resolution

**Idempotency key**

- the same composite key used by `build_stage3_cache_key()` / `build_stage3_lock_key()`

**Relationship to existing cache/singleflight**

This queue should reduce miss frequency, but request-time synchronous fallback remains mandatory because stage4 synthesis depends on the chunks immediately.

**Code evidence**

- [fastQA generation stage3 path](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/orchestrators/generation.py#L302)
- [fastQA stage3 pipeline](/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/pdf_pipeline.py#L174)
- [fastQA stage3 cache](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_cache/stage3_cache.py#L55)

### 16.6 Why FastQA Main Ask Must Not Become A Queue Job

The current `ask` and `ask_stream` path must stay synchronous because:

- it returns direct JSON or SSE in the current request
- it has immediate branch decisions for clarification, errors, and limiter release
- KB, PDF, and tabular paths all have true request-time serial dependencies
- the system already isolates appropriate async concerns into cache prewarm and write-behind finalization

**Code evidence**

- [fastQA ask endpoints](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L944)
- [fastQA ask_stream endpoints](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L1006)
- [fastQA runtime branch points](/home/cqy/worktrees/highThinking/fastQA/app/routers/qa.py#L683)
- [fastQA generation pipeline](/home/cqy/worktrees/highThinking/fastQA/app/modules/qa_kb/orchestrators/generation.py#L384)

## 17. Prefix, Namespace, And Pending-State Refinements

### 17.1 Current Prefix Defaults In Code

The spec must preserve current service defaults during phase 1:

- `public-service`: `agentcode`
- `fastQA`: `fastqa`
- `highThinkingQA`: `highthinkingqa`

These are naming defaults only. They must not be read as proof that all three services are already at the same rollout stage for authority writes or MQ ownership.

**Code evidence**

- [public-service REDIS_KEY_PREFIX default](/home/cqy/worktrees/highThinking/public-service/backend/app/core/config.py#L234)
- [fastQA REDIS_KEY_PREFIX default](/home/cqy/worktrees/highThinking/fastQA/app/core/config.py#L249)
- [highThinkingQA REDIS_KEY_PREFIX default](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/redis_client.py#L145)

### 17.2 Reserved Namespace Set

The effective reserved namespace set should be:

- `cache:`
- `lock:`
- `stream:`
- `pending:`

`pending:` is reserved for short-lived overlays or transient projection helpers, not for Redis Streams pending-entry tracking.

**Code evidence**

- [public-service key factory](/home/cqy/worktrees/highThinking/public-service/backend/app/integrations/redis/keys.py)
- [fastQA key factory](/home/cqy/worktrees/highThinking/fastQA/app/integrations/redis/keys.py)
- [fastQA pending overlay key design](/home/cqy/worktrees/highThinking/fastQA/app/services/pending_overlay.py#L35)

### 17.3 `pending:*` Must Not Mirror Consumer-Group Pending State

Redis Streams pending truth must remain the Consumer Group PEL.

Do not create shadow `pending:` keys for every message. Reserve `pending:` for short-lived UX or projection overlays such as assistant convergence hints.

### 17.4 New MQ Keys Must Not Repeat Service Name After Prefix

Some current cache keys in `highThinkingQA` repeat the service name after the prefix. That is tolerable for legacy cache keys but should not be copied into new MQ stream names.

**Code evidence**

- [highThinking stage cache repeated service naming](/home/cqy/worktrees/highThinking/highThinkingQA/server/services/stage_cache.py#L172)

## 18. Rollout Strategy

### 18.1 Phase 0: Keep Current Behavior, Add Stream Producers In Shadow Mode

Goals:

- preserve current request and worker behavior
- start emitting Redis Stream messages alongside existing in-process dispatch or DB outbox paths
- validate payload shape, consumer lag, and idempotency logic without changing user-visible semantics

Recommended first targets:

- `authority.assistant.finalized.v1`
- `conversation.json.sync_outbox`

Rules:

- existing in-process or DB-backed worker remains primary
- stream consumers run in shadow mode and record what they would do
- compare produced payloads with existing task payloads
- shadow consumers must not be attached implicitly to every `gunicorn` web worker; they need an explicit worker-role entrypoint or separate deployment

### 18.2 Phase 1: Move Public-Service Async Workers To Redis Streams

Migrate these first:

- assistant inbox worker
- chat JSON sync worker
- upload file-processing worker if and only if file reference semantics are stable enough

Why first:

- `public-service` already owns the authority truth boundary
- these flows already look like queues in business terms
- consumers can remain inside `public-service` as a service boundary while still running in dedicated worker processes rather than inside every web `gunicorn` worker

### 18.3 Phase 2: Move HighThinkingQA Background Work To Redis Streams

Migrate these next:

- chat JSON sync
- ingest job orchestration
- optional derived follow-up work on authority-owned file references, but not ownership of upload truth, file lifecycle truth, or file-QA responsibility

Rules:

- do not change `ask` or `ask_stream` API semantics
- only move durable background work
- do not attach ingest consumers to generic FastAPI web startup under `gunicorn`; use a dedicated worker process or deployment
- preserve single-active ingest execution with an explicit lease or leader guard in addition to consumer-group membership

### 18.4 Phase 3: Add FastQA Prewarm Streams

Add `stream:fast:prewarm_asset:v1` after public-service authority flows are stable.

Rules:

- prewarm remains best-effort
- request path keeps synchronous fallback
- cache and singleflight stay in place

### 18.5 Phase 4: Optional Gateway Audit Stream

Only after core business flows are stable:

- add `gateway.qa.audit.v1`
- keep it observability-only
- do not let gateway become the owner of truth data or business retries

## 19. Failure Handling And Operational Rules

### 19.1 Delivery Count Policy

Suggested initial limits:

- assistant finalization: 10 attempts
- chat JSON sync: 20 attempts
- upload processing: 10 attempts
- ingest job orchestration: 5 attempts before operator review
- prewarm asset: 3 to 5 attempts depending on cost

### 19.2 Terminal Failure Classes

A message should be considered terminal and moved to DLQ when it fails for one of these classes:

- schema validation failure
- missing required identity fields
- invalid `source_service`
- impossible business state transition
- permanent not-found that business rules do not allow retrying
- stale version where reprocessing is no longer meaningful

### 19.3 Transient Failure Classes

A message should remain pending or be retried when the failure is transient:

- Redis temporary connection error during downstream cache or lock use
- object storage timeout
- temporary authority service unavailability
- vector store timeout
- lock or lease contention

### 19.4 Idempotency Storage Rule

Consumers must not rely only on Redis stream message IDs for idempotency.

Preferred idempotency sources:

- existing business keys already enforced by public-service authority APIs
- conversation message metadata containing `idempotency_key`
- DB uniqueness or current-state checks by `conversation_id`, `trace_id`, `json_version`, or `file_id`

### 19.5 Backlog Monitoring Rule

Each worker deployment must expose:

- current stream length
- pending count
- stale pending count
- DLQ length
- oldest unacked idle age
- successful throughput
- error throughput

No rollout should proceed to the next phase without backlog visibility.

## 20. Testing And Verification Strategy

### 20.1 Producer Tests

For each producer contract, tests should verify:

- required fields exist
- idempotency key is stable
- version suffix is correct
- no local-only ephemeral data is required for correctness

### 20.2 Consumer Tests

For each consumer contract, tests should verify:

- duplicate delivery is a no-op success
- stale messages are skipped correctly
- terminal invalid messages route to DLQ
- transient failures can be claimed and retried
- success leads to `XACK`

### 20.3 Boundary Tests That Must Continue To Pass

The following behavior classes must remain unchanged through MQ rollout:

- gateway route decision and clarification behavior
- gateway SSE passthrough behavior
- public-service auth and quota correctness
- authority user-turn immediate visibility
- `fastQA` and `highThinkingQA` interactive ask semantics

### 20.4 Rollout Gate Criteria

A phase is not complete until all of the following are true:

- producer payloads are stable and versioned
- idempotency keys are documented and tested
- DLQ policy is implemented
- backlog metrics are visible
- fallback path is defined
- rollback path is defined

## 21. Recommended Initial Implementation Order

If implementation starts tomorrow, the recommended order is:

1. Define a shared Redis Stream helper layer and naming utilities without changing business flow semantics.
2. Convert `public-service` assistant inbox from DB-polled inbox worker semantics to Redis Stream consumption while preserving the same business materialization code.
3. Convert `public-service` chat JSON sync from DB outbox polling to Redis Stream consumption or a DB-outbox-to-Stream bridge.
4. Add `highThinkingQA` ingest job stream orchestration.
5. Add `fastQA` prewarm stream and keep all request-path fallbacks.
6. Add gateway audit stream only after the business flows are stable.

## 22. Current Recommendation Summary

The safest first production-worthy Redis MQ rollout is:

- keep user-turn writes synchronous
- make assistant finalization the first canonical business stream
- move existing JSON sync retry loops behind Streams next
- keep interactive ask execution out of MQ
- keep web `gunicorn` processes producer-only by default and run MQ consumers in explicit worker processes or deployments
- keep `highThinkingQA` scoped to thinking execution and derived background work, not long-term upload/file/document ownership
- use Streams for prewarm and background durability, not for request admission or live streaming transport
