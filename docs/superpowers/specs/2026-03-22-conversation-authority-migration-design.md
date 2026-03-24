# Conversation Authority Migration Design

> Status: approved design draft pending final user review
> Date: 2026-03-22
> Scope status: sections 1-38 are the current normative design unless explicitly superseded below

## 1. Background

Current conversation authority is split:
- In gateway-based frontend flows, `public-service` already acts as the public conversation authority for some paths.
- `fastQA` and `highThinkingQA` still directly depend on legacy root `server.services.conversation.conversation_service` for message persistence and multi-turn context reads.
- This leaves the system with mixed read/write paths, duplicated persistence responsibilities, and incomplete service extraction.

The migration goal is to make `public-service` the single conversation authority for the gateway-based system, while keeping answer streaming performance stable.

## 2. Migration Goal

This migration covers the full conversation path, not just assistant writeback.

Target outcome:
- `user` message persistence is unified to `public-service`
- `assistant` message persistence is unified to `public-service`
- multi-turn conversation context reads are unified to `public-service`
- `fastQA` and `highThinkingQA` no longer directly call legacy root `conversation_service`
- `gateway` remains a thin request-entry and routing layer

This phase is intended as a gradual migration, but the target architecture is a single authority model for both reads and writes.

## 3. Approved Architecture Boundary

### 3.1 gateway responsibilities
`gateway` should remain thin.

`gateway` is responsible for:
- receiving frontend requests
- authenticating / forwarding auth context
- deciding route/mode/backend target
- forwarding the ask request to the selected QA backend
- propagating conversation identifiers and trace identifiers

`gateway` is not responsible for:
- conversation authority
- assistant summary aggregation for persistence
- persistence retry orchestration
- conversation event storage
- conversation state buffering for later relay to `public-service`

Reasoning:
- If persistence events flow back through `gateway`, it becomes a conversation orchestrator and async event relay, which conflicts with the desired thin-layer boundary.
- The more conversation payload `gateway` must hold, the more it becomes a business service instead of a dispatch layer.

### 3.2 fastQA / highThinkingQA responsibilities
The QA backends are responsible for:
- executing the ask request
- producing streamed answer events
- producing final assistant summary payloads after `done`
- reading conversation context from `public-service`
- writing conversation persistence events directly to `public-service`

The QA backends are not responsible for:
- being the final conversation authority
- writing directly to legacy root `conversation_service`
- owning long-term conversation storage

### 3.3 public-service responsibilities
`public-service` becomes the single conversation authority.

It is responsible for:
- persisting user messages
- persisting assistant messages / assistant summaries
- refreshing conversation summaries
- serving conversation detail and list views
- serving multi-turn context snapshots for QA backends
- maintaining Redis caches for conversation reads
- maintaining JSON mirror / object-storage mirror
- enforcing idempotency and consistency of conversation writes

## 4. Approved Event Direction

The approved direction is:
- ask request path: `frontend -> gateway -> QA backend`
- persistence path: `QA backend -> public-service`

Rejected direction:
- `QA backend -> gateway -> public-service`

Reason for rejection:
- that would force `gateway` to temporarily own assistant content, steps, references, timings, file selection, retry state, and idempotency state
- that would make `gateway` heavier and blur service boundaries

Therefore, persistence events should not return to `gateway`.

## 5. Approved Phase Scope

This design is for full conversation authority migration, not assistant-only migration.

Included in scope:
- user message write path migration
- assistant message write path migration
- conversation context read path migration
- removal of direct QA dependency on legacy conversation authority
- performance-preserving async assistant persistence model

This spec intentionally fixes the architecture, authority boundaries, contract shape, migration sequencing, and failure semantics needed for implementation planning.
Exact code layout, metric names, retry constants, and table/module names remain implementation-plan details.

## 6. Current Recommended Operating Model

The currently approved operating model is:
- `gateway` forwards the ask request only
- QA backends interact directly with `public-service` for conversation authority operations
- `public-service` owns conversation reads and writes
- assistant persistence should remain asynchronous to avoid delaying streamed answer completion

## 7. Design Status

The remainder of this document records the currently approved design decisions gathered in this session.
Where later sections define more specific canonical schemas or stricter rules, those later sections take precedence over earlier conceptual sections.
In particular:
- section 27 is the canonical contract schema definition
- sections 34-38 are the canonical overlay, failure, and rollout rules


## 8. End-to-End Request Lifecycle

This section defines the target timing model for a gateway-based ask request after migration.

### 8.1 user message timing model: synchronous before execution
Approved model:
1. frontend sends ask request to `gateway`
2. `gateway` resolves route and forwards request to `fastQA` or `highThinkingQA`
3. the selected QA backend validates the authority preconditions
4. the QA backend synchronously persists the `user` message to `public-service`
5. the QA backend synchronously reads the current conversation context snapshot from `public-service`
6. the QA backend begins execution and streams answer events back through `gateway`

Reasoning:
- The current user turn must already exist in the authority store before context assembly.
- If the current user turn is not persisted before execution, context can become inconsistent: the model is answering a question that is not yet reflected in the authority snapshot.
- User write payload is small and structurally simple, so synchronous cost is acceptable and justified by correctness.

### 8.2 assistant message timing model: asynchronous after `done`
Approved model:
1. QA backend streams answer content and intermediate steps
2. QA backend emits final `done` event to the frontend path
3. QA backend assembles a complete assistant persistence payload locally
4. QA backend schedules an asynchronous persistence job targeted at `public-service`
5. `public-service` accepts the event, deduplicates it, persists it, refreshes summary/cache state, and marks the event completed

Reasoning:
- Assistant payload is much larger than user payload.
- Assistant persistence often includes metadata such as steps, timings, references, file selection, used files, and trace identifiers.
- Blocking final SSE completion on assistant writeback would couple frontend latency to `public-service` write-path latency.
- Asynchronous assistant persistence preserves streaming responsiveness while still preserving durability through retries and idempotency.

### 8.3 conversation context read model
Approved model:
- QA backends no longer call legacy root `conversation_service.get_conversation_context_snapshot(...)`
- QA backends read context exclusively from `public-service`
- `public-service` becomes the single source of truth for:
  - recent turns
  - conversation summary
  - last-turn route / focus file information if needed for multi-mode continuity

### 8.4 lifecycle correctness guarantees
The target lifecycle guarantees are:
- the current user message is persisted before the model executes
- the context snapshot used for the request is sourced from the same authority that stores the conversation
- assistant persistence never delays normal answer completion to the frontend
- assistant persistence is retryable without duplicate writes

## 9. Conversation Authority Contracts

This section defines the minimal contract boundaries between QA backends and `public-service`.

### 9.1 required authority operations
At minimum, `public-service` must expose three authority-facing capabilities for QA backends:
- persist user turn
- fetch conversation context snapshot
- persist assistant turn

Optional but recommended additional capability:
- persist assistant turn as an asynchronous accepted job with a delivery status handle

### 9.2 persist user turn contract
Purpose:
- guarantee that the current user turn is present in authority before context assembly

Characteristics:
- synchronous
- small payload
- low-latency target
- strict success/failure semantics

Canonical schema note:
- section 27 is the canonical user-write request/response schema for implementation
- this section is conceptual and must not be treated as a competing field list

Success contract:
- returns canonical message identifier
- guarantees the user turn is durably visible to the immediately following snapshot read

Failure contract:
- if authority rejects the write, QA backend must fail fast before execution starts
- no ask execution should proceed on top of an authority write failure for the current user turn

### 9.3 fetch conversation context snapshot contract
Purpose:
- provide a single normalized context payload usable by both `fastQA` and `highThinkingQA`

Required output structure:
- `conversation_id`
- `user_id`
- `recent_turns`
- `summary`
- `updated_at`
- optional `last_turn_route`
- optional `last_focus_file_ids`
- optional `trace_id` from the last assistant turn

Normalization rules:
- recent turns should already be de-duplicated and ordered
- summary should already be normalized into a stable dict shape
- message budgets should be explicit: either fully pre-budgeted by `public-service` or clearly left to the QA side

Recommended design choice:
- `public-service` should return a normalized raw snapshot, while QA retains final model-specific budgeting

Reasoning:
- authority should own ordering and canonicalization
- QA should still control prompt-size and profile-specific truncation decisions

### 9.4 persist assistant turn contract
Purpose:
- write the assistant result after `done`
- refresh conversation summary and caches

Characteristics:
- asynchronous from the QA backend point of view
- idempotent
- retryable
- safe to execute multiple times without duplicate messages

Canonical schema note:
- section 27 is the canonical assistant-accept request/response schema for implementation
- section 36 defines the canonical payload scope rules
- this section is conceptual and must not be treated as a competing field list

Recommended response semantics:
- `202 Accepted` for enqueue/accept semantics
- response includes acceptance id / event id / idempotency key echo

## 10. API Shape Recommendation

This design does not require the exact final URLs to be fixed now, but it does require the API responsibilities to be explicit.

### 10.1 recommended endpoint set
Recommended internal authority-facing endpoints:
- `POST /internal/conversations/{conversation_id}/messages/user`
- `GET /internal/conversations/{conversation_id}/context-snapshot`
- `POST /internal/conversations/{conversation_id}/messages/assistant-async`

Alternative acceptable shape:
- one generic `POST /internal/conversation-events`
- one `GET /internal/conversations/{conversation_id}/context-snapshot`

Recommendation:
- prefer explicit endpoints over one overloaded generic event endpoint for phase 1

Reasoning:
- easier to reason about and secure
- simpler validation
- cleaner observability by operation type
- lower ambiguity during migration

### 10.2 why assistant should use an async-accept endpoint
Recommended behavior of `assistant-async` endpoint:
- validate payload shape
- compute/check idempotency key
- enqueue or store a durable pending event
- return `202 Accepted`
- do not force the QA backend to wait for final DB/cache completion

Reasoning:
- keeps QA backend latency predictable
- lets `public-service` own retry, deduplication, and summary refresh ordering
- isolates authority write amplification from the ask critical path

### 10.3 security model
Recommended security for QA-to-public-service traffic:
- internal service-to-service auth only
- do not reuse browser-facing auth contracts directly
- require trusted internal token or network-limited internal route
- always propagate `user_id`, `conversation_id`, and `trace_id`
- log caller service name (`fastQA` vs `highThinkingQA`)

### 10.4 versioning model
Recommended migration-safe versioning:
- add new internal authority endpoints without immediately removing old routes
- gate QA backends behind feature flags for:
  - user write target
  - context read target
  - assistant write target
- remove legacy direct calls only after cutover validation

## 11. Idempotency and Deduplication Design

This section is mandatory because assistant persistence is asynchronous.

### 11.1 user turn idempotency
User turn duplicates can happen because of:
- client retry
- gateway retry
- QA backend retry after transient network failure

Recommended user idempotency key candidates:
- `conversation_id + trace_id + role=user`
- or explicit `request_id` issued by gateway and forwarded end-to-end

Recommendation:
- introduce an explicit request-scoped idempotency key generated before QA execution starts
- persist it with the message metadata

### 11.2 assistant turn idempotency
Assistant duplicates can happen because of:
- QA backend retry after `public-service` timeout
- worker redelivery
- process crash after partial enqueue
- repeated callback on the same final state

Recommended assistant idempotency key:
- `conversation_id + trace_id + role=assistant`

Why this is the preferred key:
- one ask execution should produce exactly one final assistant persisted turn
- `trace_id` is already propagated end-to-end
- it is stable across retries

### 11.3 public-service deduplication behavior
Required public-service semantics:
- if idempotency key is new: accept and persist
- if idempotency key already completed: return success-equivalent acknowledgement
- if idempotency key already pending: return accepted/pending acknowledgement
- never append a second assistant message for the same idempotency key

### 11.4 summary refresh rules
Required rule:
- summary refresh must happen at most once for the final accepted assistant turn
- duplicate assistant events must not repeatedly append messages or drift summary state

Implementation-neutral recommendation:
- maintain a durable inbound event table keyed by idempotency key
- track states such as `accepted`, `processing`, `completed`, `failed`, `dead_letter`

## 12. Async Delivery, Retry, and Failure Handling

### 12.1 recommended ownership split
Recommended ownership split:
- QA backends own short-lived local delivery attempts
- `public-service` owns durable authority-side processing state

This avoids making QA the long-term event broker.

### 12.2 recommended assistant delivery model
Recommended phase-1 model:
- QA backend posts assistant payload to `public-service` async-accept endpoint
- if request succeeds with `202`, responsibility transfers to `public-service`
- if request fails before acceptance is known, QA retries locally with the same idempotency key

### 12.3 local retry policy on QA side
QA side should have a bounded lightweight retry policy:
- small number of immediate retries for transport errors
- exponential backoff with jitter
- strict upper bound so the QA service does not become an event queue

Recommended QA-side purpose:
- bridge transient network issues only
- not act as a durable broker

### 12.4 durable retry policy on public-service side
`public-service` should own durable retries after acceptance:
- persist accepted inbound event record
- worker consumes pending events
- retries on DB/cache/storage transient errors
- dead-letter after bounded attempts

This keeps authority-consistency logic in the authority service.

### 12.5 failure semantics
#### user write failure
- fail the ask before model execution
- return a retriable authority error to the client path
- do not attempt context fetch or QA execution

#### context read failure
- fail the ask before model execution
- return a retriable authority error

#### assistant async accept failure
- QA backend retries with same idempotency key
- if bounded retries are exhausted, log an authority write failure with full trace context
- do not retract the answer already streamed to the user
- expose recovery tooling to replay failed assistant persistence attempts

### 12.6 recovery tooling
Recommended recovery tools:
- replay failed assistant persistence by idempotency key / trace_id
- list pending assistant persistence jobs
- inspect dead-lettered events
- re-drive dead-letter events after issue resolution

## 13. Migration Stages and Cutover Strategy

This migration should be incremental and reversible.

### Stage 0: authority-readiness preparation
Goals:
- add required `public-service` internal endpoints
- add idempotency model
- add inbound event durability and worker processing
- add observability and diagnostics
- keep QA backends on legacy read/write path

### Stage 1: preparation and optional shadow validation
Goals:
- add `public-service` internal endpoints, auth, canonical schema handling, and diagnostics
- optionally shadow user-write requests or compare snapshots for validation only
- keep the legacy path authoritative for execution correctness

Purpose:
- prepare the new authority path without violating the same-authority read-after-write rule during live execution

Important rule:
- stage 1 must not become a production mode where user writes are authoritative in `public-service` while context reads still come from legacy during the same ask execution

### Stage 2: paired cutover of user write and context read behind flags
Goals:
- QA backends synchronously write the current user turn to `public-service`
- QA backends fetch the execution snapshot from `public-service` in the same request lifecycle
- assistant persistence remains legacy or controlled-mixed during validation

Purpose:
- make the first real authority cutover internally consistent
- preserve the required `user write -> snapshot read` same-authority guarantee

### Stage 3: assistant async persistence migration behind flags
Goals:
- QA backends stop writing assistant turns to legacy root service
- QA backends asynchronously post assistant payloads to `public-service`
- `public-service` becomes authority for final assistant writeback

### Stage 4: Redis overlay continuity rollout
Goals:
- enable the Redis pending-assistant overlay only after assistant async accept and materialization are stable
- preserve smooth immediate follow-up UX on top of the now-stable authority path

### Stage 5: legacy dependency retirement
Goals:
- remove direct imports of legacy root `conversation_service` from QA backends
- remove remaining legacy conversation read/write paths from active execution
- keep migration flags only for the bounded rollback window

### Stage 6: cleanup and contract stabilization
Goals:
- remove dead compatibility paths
- stabilize internal contracts
- document authority ownership as final architecture

### recommended cutover rule
Do not cut over all three authority capabilities at once in production.
Recommended order:
1. preparation / optional shadow validation
2. paired `user write + context read` cutover
3. assistant async persistence
4. Redis overlay continuity

Reasoning:
- keeps the first real authority cutover internally consistent
- isolates correctness risk
- makes failures easier to localize
- avoids introducing UX continuity bridge logic before the durable authority path is healthy

## 14. Performance, Observability, and Testing Requirements

### 14.1 performance goals
This migration is successful only if:
- assistant async persistence does not materially delay `done`
- user synchronous persistence remains low-latency enough not to dominate pre-answer startup time
- context snapshot fetch remains fast enough for multi-turn asks
- Redis cache hit rate on `public-service` improves repeated context and conversation reads

### 14.2 required observability fields
Every relevant log and metric should include:
- `trace_id`
- `conversation_id`
- `user_id`
- caller service (`fastQA` / `highThinkingQA`)
- route / mode
- idempotency key
- authority operation type

### 14.3 minimum metrics
Recommended minimum metrics:
- user message authority write latency
- context snapshot fetch latency
- assistant async accept latency
- assistant event completion latency
- assistant event retry count
- assistant dead-letter count
- authority-side dedup hit count
- conversation cache hit/miss rates

### 14.4 required tests
The implementation plan should include at least:
- unit tests for idempotency behavior
- unit tests for assistant duplicate event handling
- unit tests for context snapshot normalization
- integration tests for `fastQA -> public-service` user write flow
- integration tests for `highThinkingQA -> public-service` user write flow
- integration tests for assistant async accept and eventual persistence
- failure-path tests for transient public-service unavailability
- rollback-flag tests for legacy vs new path selection

### 14.5 operational acceptance criteria
Before removing legacy direct calls, verify:
- no duplicate assistant messages under retry
- conversation detail returned by `public-service` matches expected frontend rendering shape
- multi-turn context quality does not regress
- assistant persistence backlog remains bounded
- no material increase in answer tail latency

## 15. Final Recommended Design Summary

The recommended phase-1 target architecture is:
- `gateway` remains a thin routing layer only
- `fastQA` and `highThinkingQA` become pure execution services plus direct authority clients
- `public-service` becomes the sole conversation read/write authority
- user message persistence is synchronous before execution
- context snapshot is read from the same authority before execution
- assistant persistence is asynchronous after `done`
- idempotency and durable retry are owned by `public-service`

This design preserves streaming responsiveness, reduces split-brain conversation behavior, and moves the architecture toward an actually independent service boundary instead of a partially extracted facade.

## 16. Assistant Async Ingress Storage Design

This section fixes the durable acceptance model for assistant persistence.

### 16.1 approved choice
Approved choice:
- `public-service` accepts assistant persistence over HTTP
- accepted assistant events are durably recorded in MySQL
- `public-service` background workers process the durable inbox table

Rejected alternative:
- Redis-only stream/list as the durable authority ingress layer

### 16.2 why MySQL is the correct durable ingress layer
Reasons for choosing MySQL inbound events:
- the conversation authority already lives around MySQL-backed conversation state
- deduplication and authority persistence can share the same transactional storage model
- replay, audit, dead-letter inspection, and operational debugging are easier in SQL
- the system already treats Redis as cache/lock infrastructure, not final authority storage
- a durable SQL inbox matches the migration goal of making `public-service` the authority, not just a transient relay

### 16.3 why Redis should not be the authority inbox
Redis remains useful for:
- cache
- lease / lock
- coordination
- short-lived acceleration structures

Redis should not be the primary durable assistant ingress queue for this migration because:
- it weakens authority auditability
- it introduces another persistence semantics layer into a system already centered on MySQL authority
- replay and operator tooling become less straightforward
- it risks turning a cache/coordination layer into a source-of-truth queue layer

### 16.4 recommended inbox table purpose
The inbox table should represent assistant persistence events that have been accepted by `public-service` but may not yet be fully applied to conversation state.

This table is not the final message store.
Its purpose is to:
- durably accept the event
- track processing state
- provide idempotency control
- support retries and dead-letter handling
- make recovery tooling possible

### 16.5 recommended inbox table semantic fields
Recommended conceptual fields:
- `id`
- `conversation_id`
- `user_id`
- `trace_id`
- `idempotency_key`
- `source_service` (`fastQA` / `highThinkingQA`)
- `route`
- `payload_json`
- `status`
- `attempt_count`
- `accepted_at`
- `processing_started_at`
- `completed_at`
- `last_error`
- `next_retry_at`

Recommended statuses:
- `accepted`
- `processing`
- `completed`
- `failed_retryable`
- `dead_letter`

### 16.6 uniqueness and deduplication constraints
Recommended durable uniqueness:
- unique index on `idempotency_key`

Behavior:
- first insert creates the event row
- repeated submission with same key returns accepted/already-completed semantics
- worker processing must never create a second assistant message for the same idempotency key

### 16.7 worker behavior
Recommended worker lifecycle:
1. fetch pending event rows eligible for processing
2. atomically claim one row or a small batch
3. mark rows `processing`
4. apply assistant message persistence to the authority conversation model
5. refresh conversation summary
6. refresh/invalidate relevant Redis cache keys
7. mark row `completed`

If a transient error happens:
- increment attempt count
- write `last_error`
- compute `next_retry_at`
- mark row `failed_retryable`

If attempts exceed threshold:
- mark row `dead_letter`

### 16.8 transaction boundary recommendation
Recommended authority-side boundary:
- event acceptance should be durable before returning `202`
- final assistant message append and conversation summary refresh should be applied in a controlled worker transaction boundary
- if message append succeeds but cache refresh partially fails, the system should still preserve durable authority state and retry cache repair separately if needed

### 16.9 acceptance semantics from QA backend point of view
From the QA backend perspective, success means:
- `public-service` durably accepted the assistant event into MySQL inbox storage
- the QA backend no longer owns long-lived retry responsibility

This is the key transfer-of-responsibility point.
Once accepted, `public-service` owns completion.

## 17. Recommended Public-Service Internal Components

To support the design above, `public-service` should gain or extend these internal units.

### 17.1 assistant ingress API layer
Responsibility:
- validate assistant async payloads
- authenticate internal caller
- compute/validate idempotency key
- write accepted event to MySQL inbox table
- return `202 Accepted`

### 17.2 assistant inbox repository
Responsibility:
- create inbox event row
- claim pending rows for worker execution
- transition event states
- enforce unique idempotency keys
- expose dead-letter and replay helpers

### 17.3 assistant persistence worker
Responsibility:
- consume durable inbox rows
- append assistant messages to conversation authority
- refresh summary
- refresh caches
- emit metrics and structured logs

### 17.4 recovery and diagnostics tooling
Responsibility:
- inspect inbox rows by trace id / conversation id / status
- replay failed events
- list dead-letter events
- expose operator diagnostics for stuck processing

## 18. Design Implication for QA Backends

Choosing MySQL inbox ownership inside `public-service` means:
- `fastQA` and `highThinkingQA` stay thin on the delivery side
- they only need a lightweight async HTTP client and bounded retry policy
- they do not need durable local outbox tables for phase 1
- they do not need to become queue managers

This keeps the migration aligned with the service-boundary goal:
- QA services produce answers
- `public-service` owns authority persistence and durable delivery completion


## 19. User Turn Synchronous Authority Write Design

This section fixes the handling of the current user turn before QA execution.

### 19.1 approved choice
Approved choice:
- the QA backend synchronously calls `public-service` to persist the current user turn before model execution starts
- this is a direct authority write, not an async inbox event

Rejected alternative:
- user turn first enters a queue/inbox and is later materialized by a worker

### 19.2 why user write must be synchronous
The current user turn must be visible to the same authority that will serve the context snapshot for the request.

If user write were asynchronous, the system could observe this incorrect ordering:
1. request reaches QA backend
2. user event is only queued, not yet materialized
3. QA backend fetches context snapshot
4. snapshot does not include the current user question
5. model executes on stale authority state

That ordering is unacceptable because it breaks the semantic guarantee that the current turn is part of the authoritative conversation before reasoning begins.

### 19.3 required ordering guarantee
Required ordering for each ask execution:
1. persist current user turn to authority
2. fetch authority-backed context snapshot
3. execute QA pipeline
4. emit final assistant `done`
5. asynchronously persist assistant turn through accepted inbox flow

This ordering is mandatory for both `fastQA` and `highThinkingQA`.

### 19.4 recommended user write endpoint semantics
Recommended behavior of the internal user-write endpoint:
- validate internal caller
- validate `conversation_id` / `user_id` ownership assumptions passed from the routed request
- perform direct authority write to conversation message store
- make the message immediately visible to subsequent context snapshot reads
- return a canonical message identifier and authority timestamp

Recommended response shape should include at least:
- `success`
- `conversation_id`
- `message_id`
- `trace_id`
- `created_at`

### 19.5 user write payload shape
Minimum user write payload:
- `conversation_id`
- `user_id`
- `content`
- `trace_id`
- `route`
- `requested_mode`
- `actual_mode`
- `source_service`
- `idempotency_key`

Optional migration-useful payload fields:
- `gateway_request_id`
- `turn_mode`
- `last_turn_route_hint`
- `selected_file_ids`

### 19.6 idempotency for user writes
Even though user writes are synchronous, they still require deduplication because retries can happen.

Recommended user idempotency key:
- `conversation_id + trace_id + role=user`

Required semantics:
- repeated submission with the same key must not append duplicate user messages
- repeated submission should return a success-equivalent response with the canonical message id if the original write already committed

### 19.7 failure handling for user writes
Required behavior:
- if synchronous user write fails, the QA backend must not begin execution
- if authority write status is unknown because of timeout/transport ambiguity, the QA backend must retry using the same idempotency key
- if retries are exhausted without confirmed acceptance, the request should fail before answer generation

This is intentionally stricter than assistant handling because user write correctness is a prerequisite for context correctness.

## 20. Context Snapshot Read Design

This section defines how QA backends should consume authority-backed context after the user turn is persisted.

### 20.1 approved source of truth
Approved source of truth:
- context snapshots used by `fastQA` and `highThinkingQA` should be read from `public-service`
- legacy root `conversation_service.get_conversation_context_snapshot(...)` is no longer the target source after cutover

### 20.2 required snapshot properties
A valid context snapshot should provide:
- canonical ordering of recent turns
- current conversation summary
- current conversation metadata timestamp/version
- enough normalized structure for both QA backends to apply model-specific budgeting safely

The snapshot should be authoritative, not reconstructed independently inside each QA backend.

### 20.3 budgeting responsibility split
Recommended split:
- `public-service` owns canonical message ordering, normalization, and de-duplication
- QA backends own final prompt-budget trimming for model execution

Reasoning:
- canonicalization belongs with authority
- model-token-budget policy belongs with the execution service because `fastQA` and `highThinkingQA` may differ in prompt constraints and profile behavior

### 20.4 snapshot freshness requirement
Required freshness guarantee:
- the snapshot fetched immediately after synchronous user write must reflect that just-written user turn

This can be achieved by:
- direct authority write followed by snapshot read from the same authority source
- avoiding eventual-consistency-only read paths for the immediate post-write snapshot

### 20.5 cache usage on public-service side
`public-service` may use Redis to accelerate conversation detail/list and snapshot construction, but the immediate post-user-write snapshot must still satisfy freshness.

Recommended rule:
- invalidate or refresh relevant snapshot inputs during synchronous user write
- if necessary, bypass stale cache for the immediate post-write read path


## 21. Service-to-Service Authentication and Trust Model

This section defines how QA backends authenticate to `public-service` and how user identity context is propagated safely.

### 21.1 approved choice
Approved choice:
- QA backends call `public-service` using an internal service-to-service authentication mechanism
- QA backends also propagate user identity context and request context fields
- `public-service` validates both the trusted caller and the user-scoped payload

This is a dual-validation model.

### 21.2 rejected simpler models
Rejected model A:
- rely only on user token passthrough

Reason for rejection:
- weakens service-boundary trust separation
- makes internal authority operations look like browser-facing calls
- complicates internal authorization semantics

Rejected model B:
- rely only on shared internal service token with no user context verification

Reason for rejection:
- too much trust placed in the caller payload
- weaker guarantees around user/conversation binding correctness
- poorer auditability of who the message is actually being persisted for

### 21.3 required trust components
For QA-to-public-service internal calls, each request should carry:
- internal service authentication credential
- `source_service` identity (`fastQA` or `highThinkingQA`)
- `trace_id`
- `user_id`
- `conversation_id`
- route/mode context

Write operations must additionally carry a write idempotency key.
Read operations do not require a write idempotency key.

Recommended optional propagated fields:
- original gateway request id
- caller instance identifier / hostname / worker id
- selected file ids / route hints if relevant to future diagnostics

### 21.4 public-service validation responsibilities
`public-service` should validate:
- the internal service credential is valid
- the caller service is authorized to use the internal conversation authority endpoints
- the `user_id` and `conversation_id` relationship is valid according to authority data
- the request payload shape is valid for the operation type
- write-operation idempotency keys are well formed and consistent with the request when the operation is a write

### 21.5 separation of concerns
Recommended separation:
- service credential proves the caller is a trusted backend service
- payload user context identifies which user/conversation the authority operation targets
- `public-service` still independently verifies that the conversation belongs to that user

This avoids a trust model where QA services can blindly write arbitrary messages to arbitrary conversations without authority-side verification.

### 21.6 recommended header and payload semantics
Implementation-neutral recommendation:
- internal auth should be carried in dedicated internal headers
- user context should be carried in request payload or explicit identity headers
- `trace_id` should be a first-class propagated field visible in both logs and persistence metadata

Examples of conceptual separation:
- internal service auth: `X-Internal-Service-*`
- user/request context: `user_id`, `conversation_id`, `trace_id`, route, mode, idempotency key

The exact header names can be fixed during implementation planning.

### 21.7 anti-spoofing rule
A QA backend must not be allowed to create or append conversation messages for a user/conversation pair that `public-service` cannot validate.

Required rule:
- `public-service` must verify ownership of `conversation_id` against `user_id`
- `public-service` must not treat caller-supplied `user_id` as sufficient on its own

### 21.8 auditability requirements
Every accepted or rejected authority call should be traceable by:
- `trace_id`
- `source_service`
- `user_id`
- `conversation_id`
- operation type (`user_write`, `context_snapshot`, `assistant_async_accept`)
- result (`accepted`, `rejected`, `duplicate`, `completed`, `failed`)

## 22. Public-Service Authorization Rules for Authority Endpoints

This section defines the expected authorization behavior for the new internal endpoints.

### 22.1 user write authorization rule
To accept a user write, `public-service` must verify:
- the caller is a trusted QA backend
- the conversation exists
- the conversation belongs to the supplied `user_id`
- the request is idempotent with respect to its user-write key

### 22.2 context snapshot authorization rule
To return a context snapshot, `public-service` must verify:
- the caller is a trusted QA backend
- the conversation exists
- the conversation belongs to the supplied `user_id`
- the request carries valid request-scoped identity metadata such as `conversation_id`, `user_id`, and `trace_id` for the operation being performed

### 22.3 assistant async accept authorization rule
To accept an assistant event, `public-service` must verify:
- the caller is a trusted QA backend
- the conversation exists
- the conversation belongs to the supplied `user_id`
- the payload idempotency key is valid
- the payload indicates a final assistant event (`done_seen=true` or equivalent finality requirement)

### 22.4 source-service policy
Recommended policy:
- `fastQA` may call internal authority endpoints for fast-mode executions
- `highThinkingQA` may call internal authority endpoints for thinking-mode executions
- `public-service` should log source-service usage and reject unknown callers

This is not meant to hard-code mode ownership forever, but it provides strong operational clarity during migration.


## 23. Compatibility, Feature Flags, and Rollback Strategy

This section defines how the migration should be controlled in production and how rollback should work without turning the system into a long-lived dual-write architecture.

### 23.1 migration control principle
The migration must be:
- staged
- observable
- reversible
- narrow in blast radius

The migration must not rely on permanent dual-write as the steady-state model.
Permanent dual-write would increase ambiguity, debugging cost, and long-tail consistency problems.

### 23.2 required feature flags
The design requires separate controls for:
- the paired authority execution base (`user write + context read`)
- assistant durable write target
- overlay continuity enablement

Recommended logical flags:
- `conversation_execution_authority_target`
- `conversation_assistant_write_target`
- `conversation_overlay_enabled`

Recommended values for `conversation_execution_authority_target`:
- `legacy`
- `public_service`
- optional `shadow_public_service` for validation-only preparation

Meaning of `shadow_public_service`:
- still use the current primary path for correctness
- additionally call or compare the new path for diagnostics without making it authoritative
- only use this when explicitly needed for confidence building

Important invariant:
- production execution must not run with `user write` and `context read` pointing at different authorities in the same ask lifecycle
- the execution authority base is a coupled validity unit

### 23.3 service-specific flag control
Flags should be independently controllable per QA backend:
- `fastQA`
- `highThinkingQA`

Reasoning:
- `fastQA` and `highThinkingQA` differ in latency profile, payload size, and operational behavior
- one service may be ready to cut over before the other
- rollback should not require global reversal if only one backend shows issues

Recommended control granularity:
- global default
- per-service override
- environment-specific override (`dev`, `staging`, `prod`)

Important rule:
- service-specific overrides must still respect the validity invariant of the coupled execution authority base

### 23.4 recommended cutover order
Recommended cutover order is:
1. preparation / optional shadow validation
2. paired production cutover of the execution authority base (`user write + context read` together)
3. assistant write target
4. overlay continuity enablement

This should still happen separately for each QA backend.

Recommended rollout order by service:
1. `fastQA` in development/staging
2. `fastQA` in production canary
3. `fastQA` in wider production
4. `highThinkingQA` in development/staging
5. `highThinkingQA` in production canary
6. `highThinkingQA` in wider production

Reasoning:
- `fastQA` usually has simpler and more frequent traffic, making it a better early proving ground
- `highThinkingQA` has longer-running ask behavior and more expensive retries, so it should cut later after the authority path is proven
- the first real production authority cutover must switch the execution authority base as one coupled unit, never as separate live flips

### 23.5 per-stage acceptance gates
Before moving from one stage to the next, the following checks should pass.

#### Gate A: prerequisites before enabling the coupled `public_service` execution authority base
Verify:
- internal auth works end-to-end
- idempotent user write works under retry
- user write latency is acceptable
- snapshot shape is compatible with both QA backends
- message ordering matches expected multi-turn semantics
- conversation summary is correctly returned
- the current user turn is visible immediately after synchronous write
- no unexpected increase in ask startup failures

Important rule:
- Gate A is one prerequisite bundle for enabling the coupled execution authority base
- it is not permission to enable `user write` and `context read` separately in production

#### Gate B: before enabling `public_service` assistant writes
Verify:
- `202 Accepted` path is stable
- inbox worker completion rate is healthy
- no duplicate assistant turns under retry
- no unacceptable backlog growth
- no material increase in answer tail latency

#### Gate C: before enabling overlay continuity
Verify:
- assistant async accept and materialization are already stable
- overlay duplicate suppression works correctly
- stale overlay reuse is not observed
- degraded Redis behavior falls back cleanly to authority-only execution

### 23.6 rollback philosophy
Rollback must be targeted, not global by default.

If one authority capability regresses, rollback only that capability first, while respecting the coupled execution-authority invariant.

Examples:
- if assistant async processing fails but execution authority is healthy, roll back only `conversation_assistant_write_target`
- if the execution authority base regresses, roll back `conversation_execution_authority_target` as one coupled unit
- if one service regresses, roll back only that service's override

### 23.7 rollback matrix
Recommended rollback matrix:

#### Case 1: execution authority base regression
Symptoms:
- ask startup failures
- duplicate/missing user turns
- stale or malformed context snapshots
- elevated authority timeout rate before execution

Rollback:
- set `conversation_execution_authority_target=legacy` as one coupled rollback
- do not leave production execution in a split state where user writes and context reads target different authorities

#### Case 2: assistant async persistence regression
Symptoms:
- missing assistant turns in conversation detail
- growing inbox backlog
- duplicate assistant messages
- dead-letter volume increases

Rollback:
- set `conversation_assistant_write_target=legacy`
- keep the execution authority base on `public_service` if healthy

#### Case 3: overlay continuity regression
Symptoms:
- duplicated immediate-turn context
- stale overlay reuse
- continuity degradation or Redis-specific instability

Rollback:
- disable `conversation_overlay_enabled`
- keep execution authority and assistant durable write unchanged if healthy

#### Case 4: service-specific regression
Symptoms:
- only `fastQA` or only `highThinkingQA` misbehaves on the new authority path

Rollback:
- disable the relevant capability flags only for the affected service
- keep the other service on the new path if healthy

### 23.8 compatibility window
A temporary compatibility window is required while rollout flags are staged.

During this window:
- new `public-service` internal endpoints exist alongside legacy QA-side direct calls
- QA code contains compatibility routing for legacy vs `public-service`
- observability compares old/new behavior at the capability level

Important invariant:
- the compatibility window may include mixed capabilities across stages
- but it must not include a production execution state where current-turn user write and execution snapshot read target different authorities in the same ask

This window must be time-bounded.
It should not become a permanent mixed architecture.

### 23.9 shadow mode guidance
Shadow mode should be used sparingly.

It is acceptable only when:
- confidence is needed before a risky cutover
- the shadow path is strictly non-authoritative
- the team is prepared to compare payloads and outcomes

It is not acceptable as a long-term operating mode because:
- it increases cost
- it increases code complexity
- it creates mental-model drift about which path is real

### 23.10 cutover completion criteria
The migration is considered complete only when:
- both QA backends use `public-service` for user writes
- both QA backends use `public-service` for context reads
- both QA backends use `public-service` for assistant async persistence
- no direct QA import/use of legacy root `conversation_service` remains in the active path
- rollback flags can be retired after the stabilization window

## 24. Detailed Failure and Recovery Scenarios

This section expands the rollback plan into concrete failure scenarios.

### 24.1 QA cannot reach public-service for user write
Expected behavior:
- retry bounded times with same idempotency key
- if still unresolved, fail ask before execution
- log structured failure with trace context

Operational response:
- inspect authority availability
- inspect service auth failures
- roll back user write target if issue is systemic

### 24.2 QA writes user turn successfully but snapshot fetch fails
Expected behavior:
- do not execute the ask
- return retriable failure
- avoid partially executing with uncertain context

Operational response:
- inspect snapshot endpoint
- inspect cache freshness logic and DB fallback behavior
- roll back context read target if needed

### 24.3 assistant event accepted but worker processing fails transiently
Expected behavior:
- user already received answer
- event remains durable in MySQL inbox
- worker retries until success or dead-letter threshold

Operational response:
- inspect worker logs and backlog metrics
- fix downstream issue
- replay dead-letter events if needed

### 24.4 assistant accept endpoint times out from QA perspective
Expected behavior:
- QA backend cannot assume failure or success
- QA retries using same idempotency key
- `public-service` deduplication guarantees single final assistant turn

Operational response:
- inspect whether inbox row exists for the idempotency key
- verify duplicate suppression

### 24.5 cache refresh fails after assistant persistence succeeds
Expected behavior:
- durable authority state remains committed
- cache repair can happen by invalidation, lazy rebuild, or follow-up retry
- do not roll back committed message write because of cache-only failure

Operational response:
- inspect Redis health
- inspect cache invalidation path
- rebuild affected conversation caches if needed

## 25. Operational Diagnostics and Support Requirements

This section defines the minimal operator-facing support needed to run the migration safely.

### 25.1 required diagnostic views
Operators should be able to inspect:
- user authority write success/failure by service
- context snapshot latency and error rate by service
- assistant inbox accepted/pending/completed/dead-letter counts
- assistant backlog age distribution
- duplicate suppression count by idempotency key category

### 25.2 required traceability workflow
Given a `trace_id`, operators should be able to answer:
- which QA backend handled the request
- whether the user turn was written to authority
- whether the context snapshot was fetched from authority
- whether the assistant event was accepted
- whether the assistant event completed
- whether any retries or dead-letter transitions occurred

### 25.3 required replay support
The system should support replay by at least:
- inbox event id
- idempotency key
- trace id
- conversation id

Replay must preserve idempotency semantics and must not create duplicate assistant turns.

### 25.4 required log structure
Recommended structured log dimensions:
- `trace_id`
- `source_service`
- `authority_operation`
- `conversation_id`
- `user_id`
- `idempotency_key`
- `event_status`
- `attempt_count`
- `latency_ms`
- `error_code`
- `error_message`

## 26. Design Recommendations for Implementation Planning

The eventual implementation plan should decompose the migration into at least these workstreams:
- `public-service` internal API and auth model
- `public-service` assistant inbox schema and worker
- `public-service` context snapshot normalization contract
- `fastQA` authority client integration
- `highThinkingQA` authority client integration
- compatibility flags and rollout controls
- observability, diagnostics, replay tooling, and tests

These workstreams are intentionally separated so that migration tasks can proceed incrementally without mixing protocol design, authority storage, and QA integration into one change set.


## 27. Internal Contract Schemas

This section makes the authority contracts concrete enough for implementation planning.

The goal is not to freeze every field name forever.
The goal is to freeze:
- which side owns each field
- which fields are mandatory for correctness
- which fields are optional diagnostics
- which response guarantees QA backends may rely on

### 27.1 shared request metadata contract
Every QA-to-`public-service` authority call should carry the following common metadata:
- `trace_id`
- `conversation_id`
- `user_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`

Write operations must additionally carry:
- `idempotency_key`

Recommended optional common metadata:
- `gateway_request_id`
- `session_id`
- `selected_file_ids`
- `last_turn_route_hint`
- `frontend_request_started_at`
- `qa_request_received_at`

Contract rule:
- shared metadata should be propagated unchanged across authority operations whenever meaningful
- write-only metadata such as `idempotency_key` should not be forced onto read-only operations
- `public-service` should persist the canonical subset needed for audit and diagnostics

### 27.2 user write request schema
Recommended minimum request body:

```json
{
  "trace_id": "trc_xxx",
  "conversation_id": "conv_xxx",
  "user_id": "user_xxx",
  "source_service": "fastQA",
  "route": "kb_qa",
  "requested_mode": "fast",
  "actual_mode": "fast",
  "idempotency_key": "conv_xxx:trc_xxx:user",
  "message": {
    "role": "user",
    "content": "..."
  },
  "context_hints": {
    "selected_file_ids": [],
    "last_turn_route_hint": "kb_qa"
  }
}
```

Required semantics:
- `message.role` must be `user`
- `message.content` is the exact user-visible prompt content that should appear in conversation history
- `public-service` must treat this write as authoritative conversation append, not as a transient staging row

Recommended synchronous response:

```json
{
  "success": true,
  "conversation_id": "conv_xxx",
  "message_id": "msg_xxx",
  "trace_id": "trc_xxx",
  "idempotency_key": "conv_xxx:trc_xxx:user",
  "created_at": "2026-03-22T12:34:56Z",
  "deduped": false
}
```

Required guarantees:
- once success is returned, an immediate context snapshot read must be able to see the user turn
- if `deduped=true`, the response must still reference the canonical already-written message id

### 27.3 context snapshot response schema
Recommended minimum response body:

```json
{
  "conversation_id": "conv_xxx",
  "user_id": "user_xxx",
  "snapshot_version": 17,
  "updated_at": "2026-03-22T12:34:57Z",
  "summary": {
    "short_summary": "...",
    "memory_facts": [],
    "open_threads": []
  },
  "recent_turns": [
    {
      "message_id": "msg_1",
      "role": "user",
      "content": "...",
      "created_at": "2026-03-22T12:20:00Z",
      "trace_id": "trc_prev"
    }
  ],
  "conversation_state": {
    "last_turn_route": "kb_qa",
    "last_focus_file_ids": [],
    "last_assistant_trace_id": "trc_prev_assistant"
  }
}
```

Required semantics:
- `recent_turns` must already be in canonical order
- `summary` may be empty but must keep a stable shape
- `snapshot_version` should monotonically advance when authority-visible conversation state changes
- QA backends may trim the returned payload for prompt budget, but must not reorder it

### 27.4 assistant async accept request schema
Recommended minimum request body:

```json
{
  "trace_id": "trc_xxx",
  "conversation_id": "conv_xxx",
  "user_id": "user_xxx",
  "source_service": "fastQA",
  "route": "kb_qa",
  "requested_mode": "fast",
  "actual_mode": "fast",
  "idempotency_key": "conv_xxx:trc_xxx:assistant",
  "final_event": {
    "done_seen": true,
    "answer_text": "...",
    "steps": [],
    "references": [],
    "used_files": [],
    "timings": {}
  }
}
```

Required semantics:
- the payload represents the final assistant turn only
- partial stream chunks are out of scope for this migration
- `answer_text` is the canonical persisted assistant content shown in conversation history
- `steps`, `references`, `used_files`, and `timings` are attached metadata, not separate message rows

Recommended response:

```json
{
  "accepted": true,
  "event_id": "evt_xxx",
  "trace_id": "trc_xxx",
  "idempotency_key": "conv_xxx:trc_xxx:assistant",
  "status": "accepted"
}
```

Required guarantee:
- once this response is returned, durable responsibility shifts to `public-service`

### 27.5 schema evolution rules
Required rules for future compatibility:
- additive fields are allowed
- removal or semantic repurposing of required fields requires versioned rollout
- QA backends must ignore unknown response fields
- `public-service` should reject malformed required fields explicitly rather than silently dropping them

### 27.6 canonical schema precedence rule
For implementation planning and code work, section 27 is the single canonical field-level contract.
If earlier conceptual sections describe payloads using different field names or flatter shorthand, section 27 takes precedence.

### 27.7 canonical assistant payload shape clarification
The canonical assistant accept payload shape is:
- shared top-level metadata (`trace_id`, `conversation_id`, `user_id`, `source_service`, `route`, `requested_mode`, `actual_mode`, `idempotency_key`)
- one `final_event` object containing the durable assistant-turn payload

Inside `final_event`, the canonical user-visible answer field is `answer_text`.
Any earlier mention of top-level `content`, `query_mode`, `reference_links`, `pdf_links`, `doi_locations`, or `file_selection` should be interpreted as conceptual payload content that must be normalized into the canonical schema and payload-scope rules, not as competing top-level fields.

### 27.8 canonical user-write shape clarification
The canonical user-write payload shape is the nested `message` structure defined in section 27.2.
Any earlier mention of top-level `content` or `source` should be interpreted as conceptual shorthand only.

## 28. Internal Authentication Header Contract

The system already decided on dual validation.
This section fixes how that separation should look conceptually.

### 28.1 header categories
Each internal authority request should carry three categories of information:
- service authentication headers
- tracing headers
- business payload fields

Recommended conceptual split:
- service auth headers prove the caller service identity
- tracing headers carry cross-service correlation
- payload carries the user-scoped business request

### 28.2 recommended conceptual headers
Recommended conceptual internal headers:
- `X-Internal-Service-Name`
- `X-Internal-Service-Signature` or `Authorization: Bearer <internal-token>`
- `X-Internal-Request-Timestamp`
- `X-Trace-Id`
- `X-Gateway-Request-Id` if available

These names are placeholders.
The exact header names can be finalized in implementation planning.

What matters now:
- internal auth data must not be mixed with browser-facing user auth contracts
- trace propagation must be first-class
- service identity must be explicit in every call

### 28.3 validation sequence inside public-service
Recommended validation order:
1. validate internal auth credential
2. validate request freshness / anti-replay window if used
3. parse and validate payload
4. validate `conversation_id` and `user_id` ownership
5. if the operation is a write, validate idempotency key shape and operation consistency
6. execute operation

Reasoning:
- reject unauthenticated traffic early
- reject malformed business payload before touching authority state
- never trust the caller on ownership without authority-side verification

### 28.4 rejected shortcut
Rejected shortcut:
- trust `gateway`-forwarded user identity so completely that QA-to-`public-service` calls skip conversation ownership verification

Reason for rejection:
- it creates a privileged blind-write path
- it weakens auditability
- it makes later debugging of cross-user contamination much harder

## 29. Consistency Model and Ordering Guarantees

This section defines what "correct" means across user write, context read, assistant acceptance, and assistant materialization.

### 29.1 authority consistency model
The design target is:
- strong enough consistency for the current user turn and immediate context snapshot
- eventual consistency for assistant materialization after `202 Accepted`

In other words:
- `user write -> context snapshot` must behave like a read-after-write sequence
- `assistant accepted -> conversation detail/list refresh` may complete shortly after the frontend has already received the final answer
- Redis overlay use for immediate follow-up UX may occur before durable materialization, but this overlay is explicitly non-authoritative and must be invalidated if async acceptance ultimately fails

### 29.2 required ordering guarantees
Guaranteed order per ask execution:
1. user turn authority append
2. authority snapshot read
3. QA execution
4. frontend `done`
5. assistant event acceptance
6. assistant materialization
7. summary/cache refresh

Not guaranteed:
- the conversation list/detail UI reflecting the assistant immediately at the exact millisecond of `done`

Guaranteed eventually:
- once assistant event processing completes, conversation detail/list and future snapshots converge to the persisted assistant turn

### 29.3 acceptable temporary visibility gap
A short gap between frontend `done` and authority-visible assistant turn is acceptable if:
- the answer has already been shown to the user in the active tab
- the persistence event is durably accepted
- retries are automatic
- operator diagnostics can confirm completion state

The gap is not acceptable if:
- accepted events are routinely lost
- the user refreshes and the assistant turn disappears indefinitely
- multi-turn follow-up requests regularly miss the just-produced assistant turn because materialization lag is excessive

### 29.4 context snapshot rule during assistant backlog
If an assistant event for the previous turn is accepted but not yet materialized, the next ask should follow a deterministic rule.

Recommended rule:
- only materialized assistant turns appear in authority snapshots
- accepted-but-not-materialized assistant events are not injected ad hoc into snapshot responses

Reasoning:
- snapshot should reflect canonical authority state, not a mix of committed rows and in-flight queue state
- this keeps the read model simple and auditable

Implication:
- assistant materialization latency must remain low enough that normal multi-turn use does not frequently outrun it

## 30. Summary Refresh and Derived State Rules

Conversation authority is not only raw message append.
It also owns summary refresh and derived conversation state.

### 30.1 summary ownership
`public-service` should own:
- conversation summary refresh
- last-turn route metadata
- last-focus file metadata if stored in conversation state
- conversation list preview text

QA backends should not attempt to maintain parallel summary state after cutover.

### 30.2 summary refresh trigger rules
Recommended refresh triggers:
- successful user turn append may mark summary as dirty
- successful assistant materialization should trigger summary refresh evaluation

Recommended default:
- refresh summary after assistant materialization, not after every user write

Reasoning:
- summary quality usually benefits from seeing both the question and the answer together
- this avoids unnecessary summary churn during failed or abandoned asks

### 30.3 summary failure handling
If assistant materialization succeeds but summary refresh fails:
- assistant message append remains committed
- event processing may be marked partially complete or completed-with-repair-needed
- summary repair should be retried separately

Required rule:
- summary failure must not roll back a successfully persisted assistant message

### 30.4 derived state repair path
Recommended repairable derived state:
- summary
- conversation list preview
- cached snapshot blobs
- cached conversation detail JSON

These are repairable because the source-of-truth message rows still exist.

## 31. Service-by-Service Responsibility Changes

This section makes the migration boundary explicit for each service.

### 31.1 gateway after migration
`gateway` keeps:
- frontend request entry
- auth propagation
- route selection
- backend forwarding
- SSE proxying

`gateway` must not gain:
- conversation persistence tables
- assistant event queues
- replay tooling for conversation authority
- summary recomputation logic

### 31.2 fastQA after migration
`fastQA` keeps:
- ask execution
- streaming steps and final answer
- route-specific QA logic
- local assembly of final assistant persistence payload

`fastQA` loses:
- direct dependency on legacy conversation authority
- direct user/assistant authority writes outside the new internal client
- private assumptions that conversation storage is local in-process

### 31.3 highThinkingQA after migration
`highThinkingQA` keeps:
- long-running reasoning execution
- streaming steps and final answer
- local assembly of final assistant persistence payload

`highThinkingQA` loses:
- direct dependency on legacy conversation authority
- any special-case local summary persistence path
- any direct conversation snapshot read from legacy root service

### 31.4 public-service after migration
`public-service` gains or becomes authoritative for:
- internal conversation authority APIs
- idempotent user message append
- context snapshot serving
- durable assistant ingress
- assistant materialization worker
- summary/cache repair logic
- replay and diagnostics tooling

## 32. Migration Non-Goals and Guardrails

To keep the migration bounded, the following are explicit non-goals for this phase.

### 32.1 non-goals
This migration does not require:
- changing normal QA reasoning logic
- redesigning frontend chat rendering
- moving answer streaming itself into `public-service`
- making `gateway` an orchestration bus
- introducing distributed transactions across services

### 32.2 guardrails
Required guardrails during implementation:
- no permanent dual-write steady state
- no new persistence authority in `gateway`
- no best-effort user write before execution
- no assistant write path that can create duplicate visible turns under normal retry
- no dependence on Redis as the final durable inbox

## 33. Implementation Readiness Checklist for Planning

Before moving from design to implementation planning, the team should treat the following as fixed inputs unless the design is reopened.

### 33.1 fixed architecture decisions
Fixed:
- `gateway` stays thin
- QA backends talk directly to `public-service`
- user write is synchronous
- context read is from `public-service`
- assistant write is async-accepted by `public-service`
- durable assistant inbox is MySQL-backed
- trust model is dual validation

### 33.2 items intentionally left to implementation plan
To be decided in the implementation plan, not reopened at the architecture level:
- exact module/file layout in each service
- concrete DB migration files and table names
- exact retry backoff constants
- exact metric names
- exact HTTP path names and header names
- exact rollout environment order and canary size

### 33.3 readiness condition
This design is ready to move into implementation planning once the internal contradictions identified in review are resolved and the canonical schema and rollout rules are treated as fixed.
After those corrections, the implementation plan should treat sections 27 and 34-38 as the authoritative basis for execution planning.

## 34. Pending Assistant Overlay Design For UX Continuity

This section defines the approved exception path used to preserve smooth multi-turn UX while keeping `public-service` as the only durable authority.

### 34.1 approved choice
Approved choice:
- after final answer streaming completes, the QA backend may publish a short-lived `pending assistant overlay`
- the overlay is stored in Redis, not only in process memory
- the overlay exists only to preserve continuity for immediate follow-up asks before assistant materialization completes in `public-service`

Rejected alternatives:
- process-memory-only overlay
- gateway-held overlay
- no overlay at all

Reasons for rejection:
- process memory is too fragile in multi-worker / multi-instance deployments
- gateway-held overlay would make `gateway` heavier and blur service boundaries
- no overlay would degrade immediate follow-up UX and conflict with the approved "smooth output and user experience first" requirement

### 34.2 role of the overlay
The overlay is not authority state.
It is a short-lived continuity layer.

Its only responsibilities are:
- preserve the just-produced assistant turn for immediate follow-up asks
- bridge the gap between frontend-visible `done` and durable assistant materialization in `public-service`
- maintain multi-instance consistency for that short-lived continuity window

The overlay must not be treated as:
- durable conversation history
- a replacement for `public-service` message storage
- a source of truth for reload, refresh, or cross-device history views

### 34.3 user-visible behavior
Approved user-visible rule:
- once the user sees the assistant answer completed in the current conversation, they may immediately ask a follow-up question
- the system should automatically include the pending assistant overlay in the next-turn context when needed
- the user should not have to wait for background persistence to complete before continuing the conversation

This is the primary UX rule for this design.

### 34.3a overlay and async accept relationship
Approved rule:
- the overlay exists to protect smooth UX and may be used for immediate follow-up continuity before durable materialization completes
- however, the overlay remains conditional state, not durable truth
- if assistant async acceptance ultimately fails beyond bounded retry or is explicitly rejected, the overlay must be invalidated and treated as failed continuity state

This preserves the UX-first goal without pretending the overlay is durable authority.

### 34.4 read path composition rule
For the next ask in the same active conversation, QA context assembly follows this order:
1. read the authority snapshot from `public-service`
2. check Redis for a pending assistant overlay associated with the same conversation
3. if a valid overlay exists and is not yet materialized in authority, append it as the newest assistant turn for prompt assembly
4. continue normal QA execution

This means the effective execution context becomes:
- authority snapshot
- plus at most one valid pending assistant overlay per conversation turn gap

### 34.5 authority and overlay boundary
Required boundary rule:
- `public-service` snapshot responses remain pure authority state only
- the overlay is merged by QA backends after snapshot retrieval, never by `public-service`

Reasoning:
- preserves `public-service` as a clean, auditable authority read model
- keeps pending continuity logic local to execution services
- avoids mixing queue state into durable snapshot semantics

### 34.6 storage and keying model
Recommended Redis key semantics:
- one overlay entry per conversation per outstanding assistant turn
- key must include at least `conversation_id`
- key should also include `trace_id` or a monotonic turn identifier to avoid accidental overwrite ambiguity

Recommended stored fields:
- `conversation_id`
- `user_id`
- `trace_id`
- `source_service`
- `route`
- `requested_mode`
- `actual_mode`
- `assistant_content`
- `steps`
- `references`
- `used_files`
- `created_at`
- `expires_at`
- `materialization_status`

The overlay payload should be only as large as needed to reconstruct the immediate assistant turn for context usage.
It should not become a second full conversation store.

### 34.7 overlay TTL rule
Approved TTL principle:
- the overlay should be short-lived but long enough to cover realistic immediate follow-up behavior

Recommended initial design target:
- Redis TTL in the range of tens of seconds to a few minutes
- exact value to be fixed in implementation planning after considering assistant materialization latency and follow-up usage patterns

The TTL must satisfy:
- long enough to bridge normal immediate follow-up asks
- short enough to self-heal if materialization confirmation is missed

### 34.8 overlay invalidation rule
The overlay must be invalidated when either of the following happens:
- `public-service` confirms assistant materialization for the same turn
- Redis TTL expires

Recommended additional invalidation condition:
- a newer assistant overlay for the same conversation supersedes the older one in a strictly ordered way

Required rule:
- QA must not keep appending stale overlays indefinitely across many turns

### 34.9 overlay use condition
The overlay should be used only when all conditions hold:
- same `conversation_id`
- same user-scoped request context
- overlay is not expired
- overlay corresponds to the most recent assistant turn gap
- authority snapshot does not already contain the same assistant turn

This last rule is critical.
If authority already contains the assistant turn, the overlay must be ignored.

### 34.10 duplicate suppression and convergence
To prevent duplicate assistant context in the next ask, QA backends must compare:
- overlay trace/turn identity
- authority snapshot most recent assistant identity

If they match semantically, the overlay is suppressed.

Required convergence rule:
- once materialization completes, all future asks converge back to pure authority snapshot behavior for that turn

### 34.11 failure handling
If Redis overlay write fails after the user has already received the answer:
- assistant async persistence to `public-service` still proceeds
- the user may temporarily lose immediate follow-up continuity for that single gap
- the system should degrade gracefully to authority-only snapshot behavior
- this is a UX degradation, not an authority corruption event

If Redis is unavailable more broadly:
- do not block answer streaming
- do not block assistant persistence acceptance
- record degraded-mode diagnostics
- fall back to no-overlay behavior temporarily

### 34.12 multi-instance consistency requirement
The overlay exists specifically to preserve continuity across:
- multiple QA workers
- multiple QA instances
- non-sticky load balancing

Therefore the overlay must not depend on process-local memory for correctness.
Redis is the shared short-lived continuity layer.

### 34.13 relationship to frontend behavior
The frontend does not need to carry pending assistant overlay state as an authority protocol feature.

The frontend may continue to display the answer it already received.
But the continuity mechanism for the next ask is backend-driven:
- QA backends retrieve overlay from Redis
- QA backends merge it into prompt assembly when needed

This keeps protocol complexity out of the frontend while still preserving smooth user experience.

### 34.14 design intent summary
This overlay design intentionally separates three layers:
- frontend visible answer state
- Redis short-lived continuity overlay
- `public-service` durable authority state

This is more complex than a pure authority-only model, but it is the right tradeoff because the approved priority is:
- user experience first
- smooth answer continuation
- while still preserving a single durable authority in `public-service`

## 35. Approved Overlay Operating Rules

This section records the concrete operating decisions for the Redis-backed pending assistant overlay.

### 35.1 overlay write timing
Approved rule:
- write the overlay when the final assistant answer is stable and the service is about to emit `done`

Rejected timing:
- too early during partial streaming
- too late only after `done` in a delayed best-effort background path

Reasoning:
- writing too early risks storing an unstable final turn
- writing too late creates a larger continuity gap right after the user sees the completed answer

### 35.2 overlay cardinality per conversation
Approved rule:
- at most one latest pending overlay is active per `conversation_id`

This is a deliberate simplification for phase 1.
It keeps the bridge layer bounded and reduces duplicate-merge risk.

### 35.3 cross-mode usability
Approved rule:
- the overlay is conversation-scoped, not mode-scoped
- if the same conversation continues through `fastQA` or `highThinkingQA`, the next ask may consume the same overlay when valid

Reasoning:
- the user experiences one conversation, not two backend-specific histories
- cross-mode follow-up continuity is required for smooth UX

### 35.4 merge priority rule
Approved rule:
- always fetch authority snapshot first
- only then conditionally append one valid overlay to the end of the execution context

Authority remains the base.
Overlay is only the continuity patch.

### 35.5 Redis degradation rule
Approved rule:
- if Redis overlay storage or retrieval fails, do not fail the ask
- do not block answer streaming
- do not block assistant persistence to `public-service`
- degrade to authority-only behavior and record diagnostics

This is a UX degradation path, not a hard conversation-authority failure.

### 35.6 assistant persistence failure after user-visible answer
Approved rule:
- if the answer has already been streamed to the user, the frontend-visible answer is not rolled back
- `public-service` acceptance failure or post-accept processing failure is handled through retry, replay, and diagnostics
- the user experience should not regress into visible answer retraction

### 35.7 convergence identity
Approved rule:
- overlay convergence and duplicate suppression use stable turn identity
- preferred identity fields are `trace_id` plus conversation-scoped assistant-turn semantics
- TTL is only a fallback cleanup mechanism, not the primary convergence signal

### 35.8 initial TTL target
Approved initial target:
- design around a 3-minute overlay TTL

This is an operating default, not a permanently frozen constant.
It may be adjusted during implementation validation if measured assistant materialization latency or user follow-up patterns justify it.

### 35.9 frontend visibility of pending persistence
Approved phase-1 rule:
- the frontend does not need an explicit `pending persistence` UI state for the overlay
- the backend continuity mechanism remains internal in phase 1

This keeps UI complexity down while the core migration is stabilized.

### 35.10 refresh and cross-device behavior
Approved rule:
- refresh, reopen, or cross-device views do not consume Redis overlay state
- these views use only `public-service` durable authority state
- overlay continuity is only for active in-flight conversational continuity on backend ask execution

### 35.11 shared schema rule across QA backends
Approved rule:
- `fastQA` and `highThinkingQA` must use the same Redis overlay schema, keying rules, and convergence rules

Reasoning:
- reduces protocol drift
- preserves cross-mode continuity
- simplifies migration and observability

### 35.12 phase-1 intent
The overlay system in phase 1 is intentionally narrow:
- one pending latest assistant bridge per conversation
- one shared backend schema
- one short-lived continuity purpose
- no attempt to become a second distributed message store

## 36. Approved Assistant Persistence Payload Scope

This section records what the QA backends must and must not send when persisting the final assistant turn to `public-service`.

### 36.1 payload design goal
Approved goal:
- the persisted assistant payload must be sufficient for `public-service` to serve a durable, reload-safe, frontend-usable completed assistant turn

The payload is not only for text storage.
It is the durable representation of one completed assistant turn.

### 36.2 final answer text
Approved rule:
- persist the final assistant answer text in full

Rejected alternative:
- store only a summary while leaving the full answer only in QA-local state

Reasoning:
- `public-service` must become the real conversation authority
- authority cannot depend on QA-local copies of the completed answer

### 36.3 steps payload
Approved rule:
- persist normalized, frontend-relevant steps
- do not persist every internal transient object or raw implementation detail

This means the payload should preserve what the frontend needs to replay or render a completed answer experience, but should avoid shipping internal noise that has no durable product value.

### 36.4 references payload
Approved rule:
- persist normalized structured references
- this should include identifiers and renderable link metadata such as DOI, title, source type, and usable link targets when available

Rejected alternative:
- rely only on inline markdown citation text inside the answer body

Reasoning:
- structured references are needed for reload-safe rendering, link actions, source inspection, and future contract stability

### 36.5 used files payload
Approved rule:
- persist normalized `used_files` metadata for file QA and hybrid QA turns

Reasoning:
- file provenance is part of the durable explanation of how the answer was produced
- later diagnostics and UI behavior may depend on it

### 36.6 timings payload
Approved rule:
- persist a lightweight normalized timing summary
- do not persist every ultra-fine internal timing detail by default

The purpose is operational observability and user-facing continuity where needed, not complete internal profiling storage.

### 36.7 raw model request and response
Approved rule:
- do not persist raw model request payloads or raw provider response payloads in the conversation authority event by default

Reasoning:
- too large
- poor fit for the conversation authority boundary
- higher privacy and operational risk
- not required for durable frontend replay of the completed assistant turn

### 36.8 internal reasoning or thinking draft
Approved rule:
- do not persist internal reasoning draft or hidden thinking content in the authority assistant payload

Reasoning:
- it is not needed for durable conversation rendering
- it increases storage and compliance complexity
- it weakens the intended separation between user-visible conversation history and internal generation machinery

### 36.9 route and mode metadata
Approved rule:
- persist `route`, `requested_mode`, and `actual_mode`

Reasoning:
- multi-mode conversations require durable traceability of how a turn was produced
- this is useful both for diagnostics and for future product behavior

### 36.10 overlay state exclusion
Approved rule:
- do not persist Redis overlay state as part of the durable assistant history model
- only the final materialized assistant turn becomes authority state

Reasoning:
- overlay is a continuity bridge, not a durable business record
- authority history must stay clean and convergent

### 36.11 payload sufficiency standard
Approved standard:
- after a page refresh or a later history load, `public-service` should be able to reconstruct the completed assistant turn well enough for the frontend to render the durable result correctly

That includes, at minimum:
- final answer text
- renderable steps shape
- structured references
- file usage metadata when applicable
- normalized mode and route metadata
- lightweight timing summary

### 36.12 phase-1 payload philosophy
Phase 1 should avoid both extremes:
- not too thin: otherwise `public-service` is not a true conversation authority
- not too heavy: otherwise the assistant event becomes an oversized dump of all internal runtime state

The approved design target is a product-meaningful completed assistant turn, not a raw internal execution archive.

## 37. Approved End-to-End Failure Semantics

This section records the approved operational behavior for failures across user write, authority read, overlay continuity, and assistant persistence.

### 37.1 user write failure before execution
Approved rule:
- if synchronous user-turn authority write fails, the ask must fail before QA execution starts
- no answer generation may proceed on top of an unconfirmed current user turn

Reasoning:
- current-turn authority correctness is a prerequisite for correct multi-turn reasoning

### 37.2 user write success but snapshot read failure
Approved rule:
- if user write succeeds but authority snapshot read fails, the ask must fail before QA execution starts
- do not proceed with an authority-write-success plus authority-read-failure split state

Rejected default behavior:
- automatic ad hoc fallback to a different snapshot source during the request path

Reasoning:
- runtime fallback across different authority semantics would make debugging and correctness much harder
- cutover and rollback must remain explicit through flags, not hidden through opportunistic fallback logic

### 37.3 assistant already streamed but async accept fails
Approved rule:
- once the assistant answer has been streamed to the user, the frontend-visible answer is not retracted
- QA retries assistant async accept using the same idempotency key within bounded retry limits
- after bounded retries are exhausted, the system records degraded persistence diagnostics and relies on replay/recovery workflows

This is a persistence degradation event, not a reason to revoke the user-visible completed answer.

### 37.4 assistant async accept succeeded but worker processing fails
Approved rule:
- once `public-service` has accepted the assistant event, durable responsibility transfers to `public-service`
- worker-side failures are handled through retry, dead-letter, and replay inside `public-service`
- the responsibility does not move back to QA backends

### 37.5 Redis overlay write failure
Approved rule:
- if Redis overlay write fails after the answer is completed, the current answer remains valid and visible
- assistant persistence to `public-service` still proceeds
- the only degraded behavior is possible loss of immediate follow-up continuity for that short gap

### 37.6 Redis overlay read failure on follow-up ask
Approved rule:
- if Redis overlay cannot be read, do not block the follow-up ask
- degrade to authority-only context assembly
- record degraded continuity diagnostics

This is a continuity degradation, not a hard request failure.

### 37.7 authority convergence beats overlay TTL
Approved rule:
- if `public-service` authority already contains the assistant turn, ignore Redis overlay immediately even if TTL has not expired
- TTL is fallback cleanup only

### 37.8 multiple rapid follow-up asks
Approved rule:
- in phase 1, each ask uses at most:
  - authority snapshot
  - plus one latest valid pending assistant overlay
- the system must not accumulate an unbounded chain of pending overlays into prompt assembly

Reasoning:
- keeps continuity logic bounded
- prevents Redis bridge state from turning into a parallel conversation history queue

### 37.9 conflict resolution
Approved rule:
- when overlay state and authority state appear inconsistent, authority wins
- overlay is only a bridge patch and must never override durable authority state

### 37.10 user-facing error surface
Approved rule:
- user-visible errors should remain simple and product-oriented, such as unavailable or retriable request failure
- detailed failure reasons belong in structured logs, metrics, diagnostics views, replay tooling, and operator workflows

Reasoning:
- preserves clean UX
- keeps internal authority and delivery complexity out of normal user-facing surfaces

### 37.11 phase-1 resilience principle
Phase 1 resilience is based on the following priority order:
1. do not corrupt authority state
2. do not break smooth answer output
3. degrade continuity gracefully when Redis bridge features fail
4. preserve replayability and diagnostics for eventual repair

## 38. Approved Implementation Phasing And Acceptance Model

This section records the approved migration sequencing and acceptance model for implementation planning.

### 38.1 phase-1 minimum closed loop
Approved rule:
- phase 1 targets `fastQA` as the first full closed-loop migration

Reasoning:
- reduces blast radius
- validates the authority protocol on the simpler and usually higher-volume path first
- makes failures easier to localize before expanding to `highThinkingQA`

### 38.2 highThinkingQA onboarding timing
Approved rule:
- `highThinkingQA` should adopt the same authority protocol after `fastQA` is stable on the new path

This does not mean delaying protocol design.
It means delaying production cutover of the second QA backend until the first is stable.

### 38.3 public-service build order
Approved rule:
- build `public-service` internal authority API for user write and context snapshot before building the assistant inbox and worker migration path

Reasoning:
- current-turn correctness and authority-backed context are the prerequisite foundations
- assistant async persistence should be layered on top after the authority read/write base is stable

### 38.4 overlay rollout order
Approved rule:
- enable Redis overlay continuity only after assistant async accept and materialization are fundamentally stable

Reasoning:
- overlay should improve UX, not obscure core authority bugs
- separating these rollouts makes debugging materially easier

### 38.5 feature-flag granularity
Approved rule:
- keep at least these controls switchable:
  - `execution_authority_target` for the coupled `user write + context read` base
  - `assistant_write_target`
  - `overlay_readwrite_enabled`

Reasoning:
- precise rollback
- precise canarying
- lower blast radius during staged rollout
- preserves the invariant that execution user write and execution snapshot read must not split across authorities in production

### 38.6 phase-1 acceptance standard
Approved standard:
- phase 1 is accepted only when all of the following are true for `fastQA`:
  - the ask path is functionally closed-loop on the new authority path
  - no duplicate visible user or assistant turns appear under retry
  - immediate follow-up UX remains smooth
  - page refresh and later history load show correct durable history from `public-service`
  - degraded cases are diagnosable through logs and metrics

### 38.7 phase-1 scope limit on cross-mode continuity
Approved rule:
- phase 1 does not require full production cutover of fast/thinking cross-mode continuity behavior
- phase 1 primarily proves `fastQA` authority migration correctness

The shared protocol and schema should still be designed with cross-mode support in mind.
But first-stage acceptance does not require full multi-backend production rollout.

### 38.8 phase-2 target
Approved rule:
- phase 2 applies the same authority protocol, Redis overlay schema, convergence rules, and observability model to `highThinkingQA`

This is explicitly not a second custom design.
It is an adoption of the same migration contract.

### 38.9 rollback principle
Approved rule:
- rollback should first happen at the capability level, not by immediately reverting the entire service wholesale

Examples:
- disable overlay while keeping authority read/write on
- roll back assistant async persistence while keeping user write and snapshot read on if they are healthy
- roll back only `highThinkingQA` while keeping `fastQA` on the new path if it is healthy

### 38.10 implementation plan decomposition rule
Approved rule:
- the implementation plan should be decomposed primarily by service boundary and protocol responsibility, not as one monolithic cross-stack change

Recommended workstreams:
- `public-service` authority API and auth
- `public-service` assistant inbox and worker
- `fastQA` authority client integration
- `fastQA` Redis overlay integration
- feature flags, observability, and tests
- `highThinkingQA` migration onto the same protocol

### 38.11 definition of migration success
The migration is only considered successful when:
- `public-service` is the single durable conversation authority for the migrated path
- UX remains smooth for active follow-up questioning
- multi-instance deployments remain consistent
- replay and recovery are possible for assistant persistence failures
- no hidden gateway-owned persistence responsibility has been introduced
