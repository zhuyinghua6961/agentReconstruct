# Patent Phase 1 Service Design

## Status

- Date: 2026-03-25
- Scope: design only, no retrieval implementation yet
- Write scope: `patent/` directory only
- Goal: define the `patent` service contract and runtime shape so the service can own patent `kb_qa`, `pdf_qa`, `tabular_qa`, and `hybrid_qa` inside `patent/`

## Goal

Build an independent `patent` FastAPI service that can serve as the future `patent` execution backend behind gateway, while keeping patent retrieval logic stubbed for now.

Current service must support:

- patent `kb_qa`
- patent `pdf_qa`
- patent `tabular_qa`
- patent `hybrid_qa`
- sync and stream ask endpoints
- durable chat flow through `public-service` authority APIs
- ephemeral asks when no durable conversation is present
- multi-instance-safe execution semantics
- Gunicorn deployment wrapper
- Redis-based execution infrastructure for dedupe, locking, and future retrieval caching

Current non-goals:

- real patent retrieval strategy
- patent-native citation model
- ownership of transcript durability inside `patent`

## Non-Goals

This design does not:

- require `gateway` routing changes in order to make the `patent/` service scaffold internally coherent
- require `public-service` schema or whitelist changes in order to scaffold the service code inside `patent/`
- change the existing `thinking + file route -> fastQA` compatibility rule
- define how patent retrieval ranking, recall, rerank, or grounding will work
- define patent ingestion or indexing pipelines

However, runnable durable patent rollout still depends on external changes outside `patent/`. Those rollout dependencies are listed explicitly later in this document.

## Design Summary

The service should follow a `highThinking-first` shape:

- FastAPI app structure, auth dependency style, sync/stream response handling, and authority-persistence orchestration should align with `highThinkingQA`
- Redis runtime, lock manager, cache facade, and cache-key discipline should borrow from `fastQA`
- the service should remain stateless across instances, with `public-service` as the durability owner and Redis as the coordination/cache layer

This gives Phase 1 the right operational boundary:

- `public-service` owns canonical transcript state
- Redis owns transient coordination state
- `patent` owns protocol validation, execution orchestration, and future retrieval pipeline integration

## Confirmed External Contracts

The service design assumes the already-written protocol documents are the source of truth:

- `docs/2026-03-24-patentqa-gateway-public-service-protocol.md`
- `docs/2026-03-24-patentqa-field-contract.md`

The most important confirmed Phase 1 rules are:

- gateway sends patent traffic only for `requested_mode=patent`
- gateway routes patent `file_only` / `mixed` file turns to fastQA via `actual_mode=fast` (same compatibility rule as thinking); patent-local file route code remains frozen in-repo but is not the default production execution path
- gateway is the only file-intent and route authority
- `patent` now accepts `kb_only`, `file_only`, and `mixed` patent turns through the shared canonical contract
- durable patent transcript ownership belongs to `public-service`, not the patent service
- `patent` must authenticate forwarded browser auth and derive `user_id` locally before authority writes
- `assistant-async` success must happen before sync success or stream `done`


## External Rollout Dependencies

This document distinguishes two states clearly.

### Patent-only scaffold state

In this state, code inside `patent/` may be implemented and tested in isolation, with fake authority and fake gateway behavior.

This state does not imply that durable patent traffic is safe to route in production.

### Runnable durable rollout state

Durable patent traffic must not be enabled until all of the following external dependencies are satisfied:

- `gateway` disables direct conversation persistence for `actual_mode=patent` so patent authority writes do not double-persist the same turn
- `public-service` extends its authority schema literals and allowlist to accept `source_service=patentQA` and `requested_mode=actual_mode=patent`
- `gateway` preserves forwarded auth and trace behavior expected by the patent service
- `gateway` routes `requested_mode=patent` file-aware turns to the patent backend
- gateway and patent file-route gates ship in the same rollout batch and now default to enabled, while explicit `false` remains the emergency close path

Until those dependencies land, durable patent mode inside the patent service should be treated as feature-flagged or test-only.

## Service Shape

The new service should live fully under `patent/`.

Recommended top-level layout:

```text
patent/
├── README.md
├── pyproject.toml
├── config.py
├── config.shared.env.example
├── docs/
│   └── 2026-03-25-patent-phase1-service-design.md
├── scripts/
│   ├── start.sh
│   ├── test.sh
│   └── lint.sh
├── server_fastapi/
│   ├── __init__.py
│   ├── app.py
│   ├── gunicorn.conf.py
│   ├── errors.py
│   ├── http.py
│   ├── auth/
│   │   ├── __init__.py
│   │   └── deps.py
│   └── routers/
│       ├── __init__.py
│       ├── ask.py
│       └── health.py
├── server/
│   ├── __init__.py
│   ├── errors/
│   │   ├── __init__.py
│   │   ├── codes.py
│   │   └── core.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── request_models.py
│   │   ├── response_models.py
│   │   └── authority_models.py
│   ├── runtime/
│   │   ├── __init__.py
│   │   ├── request_context.py
│   │   └── ordered_task_dispatcher.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── ask_service.py
│   │   ├── chat_persistence.py
│   │   ├── conversation_authority_client.py
│   │   ├── redis_client.py
│   │   ├── execution_cache.py
│   │   ├── execution_lock.py
│   │   └── mode_profiles.py
│   └── patent/
│       ├── __init__.py
│       ├── executor.py
│       ├── pipeline.py
│       ├── cache_keys.py
│       └── result_builder.py
└── tests/
    ├── conftest.py
    ├── fastapi_contract/
    │   ├── test_ask_contract.py
    │   └── test_health_contract.py
    ├── test_conversation_authority_client.py
    ├── test_chat_persistence.py
    ├── test_redis_runtime.py
    ├── test_execution_cache.py
    ├── test_execution_lock.py
    └── test_patent_executor.py
```

## Architecture Decision

### Chosen approach

Use `highThinkingQA` as the HTTP and authority-persistence template, then add a patent-local execution layer and a Redis infrastructure layer.

Important limit of this reuse:

- the patent service should copy the structural shape of `highThinkingQA`, not its current soft-fail persistence timing
- patent durable asks must perform user write, snapshot read, and assistant accept inline on the request path
- patent durable asks must convert assistant-accept failure into sync failure or terminal SSE `error` before `done`
- background persistence that only logs assistant-finalization failures is explicitly not acceptable for patent durable mode

### Why this is the right fit

- `highThinkingQA` already models the authority-first transcript lifecycle we want for durable patent chats
- `fastQA` already models useful Redis abstractions for locks and caches
- the combined shape avoids coupling durable conversation state to any single patent instance
- this keeps the later retrieval implementation isolated to `server/patent/` without forcing another service rewrite

### Rejected alternatives

Reuse `fastQA` as the main template:

- rejected because `fastQA` is structurally optimized around the fast knowledge domain, while `patent` must stay authority-first and patent-runtime-local even after it owns file-aware routes

Create a much smaller custom service from scratch:

- rejected because it would diverge from existing operational patterns, especially around auth, FastAPI error surfaces, and deployment

## Request Lifecycle

### Durable sync ask

1. gateway forwards `/api/patent/ask` to the patent backend
2. patent auth dependency validates forwarded auth and derives `user_id`
3. request parser validates protocol invariants:
   - `requested_mode=patent`
   - `actual_mode=patent`
   - `route` is one of `kb_qa / pdf_qa / tabular_qa / hybrid_qa`
   - `turn_mode` matches the canonical route contract
   - file-aware turns consume the forwarded canonical file payload without re-inferring intent
4. conversation mode is determined:
   - durable if `conversation_id` can coerce to positive int
   - ephemeral otherwise
5. for durable requests:
   - acquire a conversation-level execution lock in Redis keyed by `conversation_id`
   - acquire per-turn dedupe identity keyed by `conversation_id + trace_id`
   - write user turn through authority API
   - read context snapshot through authority API
6. patent executor runs stub pipeline using merged authority context
7. for durable requests:
   - submit assistant final event through authority API
   - release lock
8. return wrapped sync response

### Durable stream ask

1. validate/authenticate exactly as sync
2. durable path performs user write and snapshot read before execution starts
3. stream `metadata`, `step`, and `content` events
4. when final answer is ready, submit assistant final event through authority API
5. emit `done` only after assistant submit succeeds
6. on authority failure, emit terminal `error` instead of `done`

### Ephemeral ask

If `conversation_id` is absent or invalid:

- skip all authority calls
- skip durable lock ownership keyed by conversation id
- still use request-local dedupe/cache helpers if useful
- return the same protocol shape, but with no transcript side effects

## Component Responsibilities

### `server_fastapi/app.py`

- create FastAPI app
- bootstrap config and runtime state
- bootstrap Redis state
- register routers and exception handlers
- expose component status for health checks
- install concurrency controls such as ask-stream slot semaphores

### `server_fastapi/gunicorn.conf.py`

- wrap the ASGI app in the same deployment shape used by `highThinkingQA`
- configure bind, workers, threads, timeout, keepalive, `max_requests`, and jitter from config
- provide a stable process model for horizontal deployment

### `server_fastapi/auth/deps.py`

- parse forwarded auth context
- reject missing/invalid auth for durable patent requests
- derive `user_id`
- keep logic local to the patent service because gateway does not currently inject `user_id` into the normalized body

### `server_fastapi/routers/ask.py`

- expose:
  - `POST /api/ask`
  - `POST /api/v1/ask`
  - `POST /api/patent/ask`
  - `POST /api/v1/patent/ask`
  - stream equivalents
- parse request
- run persistence preflight for durable asks
- delegate to `ask_service`
- map service errors to sync JSON or terminal SSE error frames

### `server_fastapi/routers/health.py`

- expose app, redis, and authority-readiness status
- expose worker/process configuration useful in deployment debugging
- include enough detail to verify instance readiness behind Gunicorn
- distinguish liveness from readiness:
  - liveness may remain `200` while the process is booted
  - readiness must return `503` when the instance cannot safely serve durable patent asks
- Phase 1 readiness should be considered failed when:
  - durable mode is enabled but authority configuration is missing
  - durable mode is enabled but Redis lock infrastructure is unavailable
  - the app has not completed runtime bootstrap

### `server/schemas/request_models.py`

- define ingress request model
- validate mode/path consistency
- validate patent Phase 1 invariants
- normalize `conversation_id`, `trace_id`, and `options`
- keep this file strict so protocol mismatch fails early

### `server/services/conversation_authority_client.py`

- implement the three authority calls:
  - write user turn
  - read context snapshot
  - accept assistant turn async
- always preserve `requested_mode=patent` and `actual_mode=patent`
- use authority idempotency keys from the protocol
- treat `public-service` as the only durability authority

### `server/services/chat_persistence.py`

- orchestrate durable write order
- expose helpers used by ask routes and ask service
- own the rule that assistant accept is a hard success precondition for patent mode
- translate authority failures into service-layer exceptions
- keep durable turn identity aligned with authority idempotency keys so retries converge on the same logical turn

### `server/services/redis_client.py`

- bootstrap Redis bindings and a Redis service wrapper
- redact Redis URLs in health output
- gracefully degrade when Redis is disabled or unavailable
- publish component status into app state

### `server/services/execution_lock.py`

- expose distributed lock helpers
- provide owner-token-based release semantics
- require TTLs to avoid orphaned locks
- key by stable execution identity, not by process-local state

### `server/services/execution_cache.py`

- expose JSON cache helper for request dedupe and future retrieval cache
- keep cache namespace patent-specific
- cache only transient execution artifacts, never canonical chat transcript
- host the distributed pending-assistant overlay used to bridge authority eventual consistency for immediate follow-up turns

### `server/services/ask_service.py`

- orchestrate request execution after HTTP parsing is complete
- merge authority context into patent execution context
- produce sync response payloads
- produce ordered SSE events with `seq` and `ts`
- coordinate final assistant persistence before success completion

### `server/patent/executor.py`

- own the future execution boundary for patent answering
- for Phase 1, return deterministic stubbed output with traceable metadata
- hide retrieval details behind an execution interface so later retrieval work does not affect HTTP or persistence layers

### `server/patent/pipeline.py`

- define future pipeline stages
- for now, materialize a small in-memory stage graph:
  - normalize request
  - consult cache facade
  - build stub reasoning steps
  - return answer payload

### `server/patent/cache_keys.py`

- centralize Redis key conventions
- key families should support:
  - request dedupe
  - execution lock
  - retrieval cache
  - pipeline stage cache
  - future hot-key throttling

## Redis Design

Redis is not the transcript store.

Redis should be introduced for execution infrastructure only.

### Durable turn identity

Phase 1 must define one stable durable turn identity and reuse it across:

- Redis execution lock key
- Redis inflight coordination key
- authority user-write idempotency
- authority assistant-accept idempotency
- execution logs and diagnostic events

Recommended rule:

- for durable asks, the stable turn identity is `conversation_id + trace_id`
- the lock key must therefore be derived from `conversation_id` and `trace_id`, not from question text or request-body hashes alone
- question hash, route, and future retrieval profile markers may appear in cache keys, but not as the primary durable execution identity
- if gateway ever omits `trace_id`, patent must generate one once per request and reuse that same generated value everywhere downstream

This keeps retries and partial-failure recovery aligned with the authority contract, which is already idempotent by `conversation_id:trace_id:operation`.

### Phase 1 Redis responsibilities

- request dedupe
- conversation-level distributed execution lock for durable asks
- per-turn dedupe identity tracking
- future retrieval cache facade
- future pipeline stage cache facade
- ephemeral execution coordination state
- pending-assistant overlay for post-accept, pre-materialization conversation continuity

### Redis must not own

- canonical transcript
- authority snapshot truth
- durable assistant final message state as the long-term source of truth
- anything that cannot be safely lost and reconstructed

### Key design rules

All keys should be namespaced:

- `patent:{env}:exec:conversation-lock:{conversation-id}`
- `patent:{env}:exec:turn:{conversation-id}:{trace-id}`
- `patent:{env}:exec:cache:{normalized-request-key}`
- `patent:{env}:retrieval:cache:{normalized-query-key}`
- `patent:{env}:coord:inflight:{conversation-id}:{trace-id}`
- `patent:{env}:overlay:assistant:{user-id}:{conversation-id}`

The durable execution design must separate two identities:

- conversation-level serialization identity: `conversation_id`
- turn-level idempotency identity: `conversation_id + trace_id`

The first prevents concurrent distinct turns from racing on the same conversation. The second lets retries converge on the same logical turn.

Cache keys may include enough input identity to prevent cross-request pollution, for example:

- `requested_mode`
- `route`
- `question` hash
- durable `conversation_id` if present
- future retrieval profile or corpus version markers

### Lock semantics

For durable asks, the conversation-level lock should be acquired before authority user write.

Reason:

- multiple patent instances behind Gunicorn or behind a load balancer must not process two distinct durable turns for the same conversation concurrently
- durable patent completion needs a single execution owner for the conversation while a turn is in flight
- the turn-level dedupe key alone is not sufficient, because different trace ids on the same conversation would still race

Lock rules:

- durable asks require a conversation-level lock keyed by `conversation_id`
- the lock TTL is mandatory
- release must verify owner token
- release must be atomic compare-and-delete, not a separate `GET` then `DELETE` sequence
- long-running streams must renew the lease before TTL expiry
- lease renewal failure during durable execution must terminate the request as degraded multi-instance safety, not continue silently
- inability to get the conversation lock should fail fast with a retriable busy error in Phase 1, not silently double-run and not queue invisibly
- if Redis is unavailable, the service should surface degraded execution state explicitly rather than pretending multi-instance safety still exists
- the turn-level key should be recorded alongside the conversation lock so retries of the same turn can be recognized without allowing a second distinct turn to start

### Cache semantics

Phase 1 should only cache cheap, transient execution artifacts:

- stub execution output
- normalized future retrieval results
- future rerank stage output
- pending-assistant overlay entries

Cache writes should never be required for transcript correctness, but the pending-assistant overlay is required for best-effort continuity between assistant accept and later public-service materialization.

Overlay rules:

- write overlay only after assistant async accept succeeds
- overlay key should be per `user_id + conversation_id`
- overlay payload should include at least `trace_id`, `route`, `assistant_content`, and a short expiry
- context loading should merge authority snapshot with the freshest converged overlay when present
- overlay must be removed once authority snapshot catches up to the same assistant trace id

If Redis is down:

- ephemeral request may still run
- durable request may still run only if the durable lock policy is satisfied; overlay loss must be treated as degraded continuity, not transcript loss
- no cached acceleration is used
- health should show degraded Redis state

## Multi-Instance Consistency

The service must be designed to run with multiple Gunicorn workers and later multiple replicas.

### Consistency boundary

- `public-service` is the source of truth for durable conversation history
- Redis is the source of truth for transient coordination
- individual patent workers are disposable and stateless

### Required safety properties

- no patent instance stores transcript state locally as the canonical source
- authority writes remain idempotent by trace and conversation id
- conversation-level lock serializes durable execution per `conversation_id`
- turn-level dedupe identity uses the same durable turn identity as authority idempotency
- distributed locking prevents both duplicate retries of the same durable turn and concurrent distinct turns on the same conversation
- assistant finalization only occurs once per successful durable turn path
- pending-assistant overlay is distributed and converges away after public-service materialization catches up
- request trace id is preserved across gateway, patent, and authority logs

### Failure posture

If Redis is unavailable during durable requests, the service must choose explicit behavior.

Recommended Phase 1 behavior:

- if durable conversation locking is required and Redis is unavailable, fail fast with a retriable server error
- if another durable patent ask is already in flight for the same conversation, reject the new one as busy in Phase 1
- if request is ephemeral, continue without durable conversation lock
- if cache operations fail after lock acquisition, continue execution and log degraded cache state

This keeps correctness ahead of availability for durable multi-instance semantics.

## Performance Design

Phase 1 performance work should focus on predictable behavior rather than absolute speed.

### Concurrency

- cap active ask-stream executions per process
- keep one place where concurrency limits are configured
- use Gunicorn worker count plus per-process concurrency guard to bound load

### Memory

- do not accumulate the full event stream in memory
- keep only finalization summary fields needed for assistant persistence:
  - final answer
  - steps
  - references
  - used files
  - timings
  - trace id
- streamed chunks should pass through incrementally

### Timeouts

- authority client timeouts must be explicit
- stream read timeout must be explicit
- Redis connect and socket timeouts must be explicit
- Gunicorn timeout must exceed expected authority plus execution budget, but should remain finite

### Gunicorn concerns

The service should ship with Gunicorn configuration from day one.

Reason:

- it is the expected deployment wrapper already used elsewhere in the repo
- worker lifecycle controls help contain memory leaks and stuck execution paths
- max-request cycling is useful once real retrieval is added

Recommended config surface:

- `GUNICORN_BIND_HOST`
- `GUNICORN_BIND_PORT`
- `GUNICORN_WORKER_CLASS`
- `GUNICORN_WORKERS`
- `GUNICORN_THREADS`
- `GUNICORN_TIMEOUT`
- `GUNICORN_KEEPALIVE`
- `GUNICORN_MAX_REQUESTS`
- `GUNICORN_MAX_REQUESTS_JITTER`

## Error Handling

The patent service should align with the patent protocol docs.

### Main error families

- auth precondition failures
- protocol mismatch failures
- authority user-write failures
- authority snapshot-read failures
- authority assistant-accept failures
- Redis conversation-lock acquisition failures
- upstream execution timeout or runtime failure

### Durable path hard requirements

For durable patent asks, the following are hard preconditions or hard postconditions:

- user auth-derived `user_id`
- authority user write success
- authority context snapshot success
- authority assistant async accept success

If any of these fail:

- sync ask must not return success
- stream ask must not emit `done`

This is intentionally stricter than the current `highThinkingQA` soft-fail persistence timing. Patent durable mode must not copy that soft-fail behavior.

## Response Contract

### Sync

The sync response should follow the flat patent contract:

```json
{
  "success": true,
  "final_answer": "...",
  "query_mode": "patent_hybrid_qa",
  "route": "hybrid_qa",
  "requested_mode": "patent",
  "actual_mode": "patent",
  "source_scope": "pdf+kb",
  "timings": {},
  "references": [],
  "reference_objects": [],
  "reference_links": [],
  "original_links": [],
  "metadata": {
    "conversation_id": 123
  },
  "used_files": [],
  "file_selection": {},
  "trace_id": "req_xxx"
}
```

### Stream

The stream contract should emit JSON SSE frames with:

- `metadata`
- optional `step`
- `content`
- terminal `done` or `error`

Every frame should carry:

- `seq`
- `ts`

The service should not emit `done` until assistant accept succeeds on durable paths.

## Testing Strategy

Testing should stay entirely under `patent/tests/` for this phase.

### Contract tests

- ask route mode support
- sync flat response shape
- stream `metadata/content/done` ordering
- stream error contract
- protocol mismatch rejection for invalid canonical payloads

### Authority tests

- user write request shape
- context snapshot request shape
- assistant async request shape
- exact idempotency key construction
- failure propagation for each authority step
- feature-flagged durable mode behavior when external patent authority support is not yet enabled

### Redis tests

- bootstrap with disabled Redis
- bootstrap with fake available Redis
- conversation-lock acquire/release with owner token
- atomic compare-and-delete release semantics
- lease renewal success and failure semantics for long-running streams
- same-conversation competing request rejection
- turn-level dedupe identity behavior for retries
- cache put/get JSON helper semantics
- degraded runtime behavior when Redis fails

### Multi-instance safety tests

At least one test set should simulate concurrent durable requests and prove:

- only one execution owner succeeds for a given conversation while another durable turn is in flight
- retry of the same `conversation_id + trace_id` converges on the same durable turn identity
- duplicate execution is rejected or retried safely
- no duplicate assistant finalization is reported from the service layer
- lock expiry mid-stream does not allow a second owner to finalize the same durable turn
- crash or retry after user-write but before assistant-accept converges on the same durable turn identity
- immediate follow-up turn sees either converged authority state or the Redis pending-assistant overlay for the prior accepted assistant turn

### FastAPI tests

The test harness should follow repo reality.

If direct ASGI transport is unstable, test route callables or streaming consumers using the same approach already used elsewhere in the repo.

## Implementation Phases Inside `patent/`

### Phase A: service shell

- config
- app factory
- Gunicorn config
- health route
- error registry
- auth dependency

### Phase B: protocol and authority shell

- request/response schemas
- authority client
- feature flag for durable patent mode enablement
- chat persistence orchestration
- sync/stream ask endpoints

### Phase C: Redis infrastructure

- Redis runtime bootstrap
- cache facade
- lock helper
- degraded runtime behavior

### Phase D: patent execution stub

- executor interface
- deterministic stub result builder
- stream event emitter

### Phase E: tests

- contract tests
- authority tests
- Redis tests
- multi-instance safety tests

## Open Decisions Kept Explicit

These items should remain open after Phase 1 scaffold lands:

- exact retrieval pipeline stages
- Redis usage for retrieval result reuse vs. corpus versioning
- patent-native citation object format
- retry semantics for assistant-accept failures beyond request-time failure

## Review Checklist

A reviewer should reject the design if any of the following is missing:

- transcript durability is owned anywhere other than `public-service`
- Redis is used as canonical transcript storage
- multi-instance duplicate execution is not addressed
- assistant accept is not a hard success gate for durable patent asks
- Gunicorn is omitted from the deployment design
- the design confuses patent-only scaffold work with production-ready rollout dependencies
- the design requires changing directories outside `patent/` to make the service skeleton itself coherent

## Next Step

If this design is approved, the implementation plan should create the service in `patent/` only, with the first executable milestone being:

- a bootable Gunicorn-wrapped FastAPI service
- passing contract tests for sync and stream stub asks
- durable vs ephemeral orchestration tests using fake authority and fake Redis
