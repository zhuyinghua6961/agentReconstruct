# Interactive Admission Kickoff Decisions

**Date:** 2026-03-27
**Status:** Approved decisions for phase-1 implementation planning
**Scope:** `gateway`, `fastQA`, `highThinkingQA`, `public-service`, `patent`
**Related Docs:**
- `docs/2026-03-25-redis-mq-architecture-spec.md`
- `docs/superpowers/plans/2026-03-25-redis-mq-rollout.md`

## 1. Purpose

This document freezes the implementation kickoff decisions for queue-backed interactive admission.

It does not replace the broader Redis MQ architecture spec. It narrows the current execution slice so implementation can start without reopening already-settled product and scheduling questions.

This document also acts as a current kickoff override for rollout sequencing. The older rollout plan still documents the broader multi-phase MQ program, but the current approved start point is to implement the interactive-admission workstream first. That sequencing change is intentional and should be treated as newer than the earlier phase ordering in the rollout plan.

## 2. Current Kickoff Scope

Phase 1 implements only interactive execution admission.

For the current implementation kickoff, work starts from interactive execution admission rather than from the earlier background-stream tasks in the broader MQ rollout plan. The immediate problem to solve is burst control for interactive QA traffic when real LLM capacity is lower than incoming request rate.

Target scenario:

- cluster-safe interactive execution capacity: `10`
- incoming burst: `50`
- first `10` requests continue immediately
- remaining `40` requests enter queue-backed admission

## 3. Core Product Decisions

The following decisions are fixed for phase 1:

- The first implementation slice is `interactive admission`, not the complete Redis background-worker rollout.
- If current admitted executions are below the configured ceiling, the request continues on the current direct `ask` or `ask_stream` path.
- If current admitted executions have reached the configured ceiling, the request returns `queued` immediately.
- Queued requests must not hold the original `ask` or `ask_stream` HTTP connection open.
- Once a request is admitted, token delivery remains the current direct backend stream path; admission must not degrade already-running stream performance.
- Frontend state changes after `queued` are observed through a separate status path, not the original request connection.

## 4. Request Lifecycle

### 4.1 Submission Path

1. Client submits interactive request.
2. `gateway` completes normal request validation, file-context resolution, and route decision.
3. `gateway` persists a normalized execution snapshot or a stable reference to that snapshot before delayed execution is possible.
4. Admission control evaluates both the global ceiling and backend-specific capacity for the routed executor.
5. If capacity exists, request is admitted immediately and continues through the current backend execution path.
6. If capacity does not exist, `gateway` creates a queued request record and returns queued metadata immediately.

### 4.2 Queued Path

Queued requests use a decoupled lifecycle:

- original submission request returns immediately with `status=queued`
- frontend stores `request_id`
- frontend polls a dedicated status endpoint using `request_id`
- when state changes to `admitted`, `executing`, or `streaming`, frontend attaches using a new request

Queued requests are durable for a bounded time window and are not tied to the lifetime of the original browser connection.

Queued admission must execute from the persisted normalized snapshot. Once a request has been queued, route decision and file selection must not be recomputed later from live mutable state.

### 4.3 Execution Path

Once admitted:

- scheduler grants a shared execution slot
- if queue delay has been long enough that deployment state may have changed, backend readiness is revalidated before execution starts
- backend execution starts using the existing routed backend
- streamed requests attach to a relay-backed or equivalent delayed-attach path if they were not immediate-admit requests
- JSON requests use a delayed terminal-result fetch path instead of keeping the original request open
- if a backend rejects immediately after provisional slot grant, the scheduler must release the slot and requeue or fail deterministically rather than leaking slot ownership

## 5. User-Facing Behavior

Phase-1 frontend behavior is fixed as follows:

- immediate-admit request: user sees current normal answer behavior
- queued request: user sees an explicit queue state, not an indefinitely spinning answer bubble
- when work starts: frontend transitions from queued state to processing state
- when generation starts: frontend transitions into the normal answer rendering path
- on timeout or expiry: frontend shows terminal queue failure state

Recommended simplified UI state mapping:

- `queued` -> "排队中"
- `admitted` -> "已开始处理"
- `executing` or `streaming` -> "正在生成"
- `completed` -> normal finished answer view
- `failed`, `cancelled`, `expired` -> terminal error or cancelled view

Phase 1 does not promise accurate queue rank or accurate ETA in the frontend.

## 6. Queue State Contract

The minimum queued-request status contract for phase 1 is:

- `request_id`
- `status`
- `queue_tier`
- `created_at`
- `expires_at`
- `admitted_at`
- `started_at`
- `stream_attach_url` or `result_fetch_url`
- `cancel_allowed`

The minimum queued execution record must also retain:

- `trace_id`
- `conversation_id`
- `user_id`
- `requested_mode`
- `actual_mode`
- `route`
- `target_backend`
- `backend_capacity_key`
- `transport_kind`
- `enqueued_at`
- `execution_snapshot` or a stable storage-backed reference to that snapshot

Status values retained for implementation and APIs:

- `queued`
- `admitted`
- `executing`
- `streaming`
- `completed`
- `failed`
- `cancelled`
- `expired`

Rules:

- `stream_attach_url` is only meaningful for stream-capable requests after admission
- `result_fetch_url` is used for delayed JSON requests after completion or when terminal materialization is available
- `cancel_allowed` is true only while the request is still in the cancellable queue phase

## 7. Concurrency And Scheduling

### 7.1 Global Ceiling

The cluster-wide execution ceiling is fixed as:

- config name: `interactive_execution_max_concurrent`
- env name: `INTERACTIVE_EXECUTION_MAX_CONCURRENT`
- initial default: `10`

This limit is deployment-wide. It must not be inferred from local process counts, local semaphores, or `gunicorn` worker counts.

### 7.2 Backend-Specific Ceilings

Admission must also enforce backend-specific ceilings in addition to the global ceiling.

Phase-1 required groups:

- `fast_or_patent`
- `thinking`

Rules:

- scheduler admission is valid only when both the global ceiling and the selected backend-group ceiling allow execution
- `thinking` must not be configured above the currently verified downstream capacity for that backend family
- exact config names can be finalized in implementation planning, but the existence of backend-specific admission ceilings is not optional

### 7.3 Priority Policy

Phase-1 priority policy is fixed as:

- `fast == patent` high tier
- `thinking` low tier

Additional rule:

- `thinking` must not starve indefinitely

The fairness mechanism does not need a final algorithm name in this document, but the implementation must provide a central scheduler policy that preserves low-tier progress under sustained high-tier backlog.

### 7.4 Patent Gating

`patent` is not treated as a pure placeholder name anymore. The repository already contains a standalone phase-1 scaffold under `patent/`.

However, phase-1 admission still applies this hard rule:

- if deployed patent execution is still placeholder, disabled, or not authority-compatible end-to-end, patent requests must fail readiness before enqueue or admission
- unready patent traffic must not consume high-tier queue backlog or shared slot budget

### 7.5 Tier Selection Source Of Truth

Tier selection follows `actual_mode`, not merely `requested_mode`.

This means rerouted file-QA requests that were originally requested as `thinking` but execute as `fast` still use the high tier.

## 8. Multi-Instance And Gunicorn Constraints

Phase-1 implementation must preserve these deployment rules:

- multi-instance correctness comes from Redis-backed shared admission state
- web `gunicorn` workers must not become the source of truth for global concurrency
- local backend semaphores remain process-local safety rails only
- admission scheduler must run as a dedicated worker role or dedicated deployment
- web-serving processes remain producer-only for admission submission and attachment APIs

The shared admission state must support:

- slot ownership
- lease expiry or reclaim after worker death
- queue visibility
- cross-instance attach or result retrieval

## 9. Attachment And Retention Decisions

### 9.1 Queue Retention

Queued requests remain retrievable after client disconnect.

Fixed defaults:

- `queued_ttl = 15 minutes`
- `post_admit_attach_ttl = 10 minutes`

These values are current kickoff defaults approved for this implementation slice. They should be exposed as operator-visible retention settings in status, health, or runbook output rather than treated as hidden hardcoded protocol constants.

Meaning:

- a request can stay queued across page refreshes or short disconnects for up to 15 minutes
- after admission and before the client reattaches, the attachable stream buffer or equivalent delayed-access record remains available for up to 10 minutes

After TTL expiry, the request enters `expired`.

### 9.2 Client Disconnect

Client disconnect does not immediately remove a queued request.

Queued work is removed only by:

- explicit user cancellation while still cancellable
- TTL expiry
- normal successful completion
- terminal failure

## 10. Cancellation Semantics

Phase 1 guarantees queue-phase cancellation only.

Fixed rules:

- `queued` requests are cancellable
- `admitted but not yet executing` may also be treated as cancellable if the implementation can do so before execution ownership is committed
- once the request is executing or streaming, phase 1 does not guarantee hard cancellation of the underlying LLM work
- frontend may stop listening after execution starts, but phase 1 does not promise deterministic backend interruption

The implementation target should optimize for safe queue removal first, not for force-killing in-flight generation.

## 11. Transport Decisions

Phase-1 transport behavior is fixed as follows:

- queued state uses status polling first
- phase 1 does not require queue-status SSE or WebSocket
- admitted stream requests attach through a separate request using `request_id`
- delayed JSON requests use terminal result fetch rather than a long-hanging original request
- delayed-attach stream frames must carry monotonic sequence numbers so reconnecting clients can resume from the last acknowledged frame rather than restarting from the beginning
- relay-backed delayed attach must work across instances for the lifetime of the configured attach retention window

This means the system explicitly separates:

- submission
- queue status
- stream attach
- terminal result fetch

## 12. Non-Goals For Phase 1

Phase 1 does not require the following:

- complete background Redis MQ rollout for `assistant_finalize`, `chat_json_sync`, ingest, or prewarm
- exact queue rank in the frontend
- exact estimated wait time in the frontend
- guaranteed hard cancellation of already-running LLM work
- moving current backend token generation behind Redis polling
- deriving cluster concurrency from local worker counts

## 13. Implementation Entry Guidance

When implementation planning starts, the first executable task group for the current kickoff should map to:

- admission queue contract and global slot arbiter
- queued-request status contract
- delayed stream attach and delayed JSON result retrieval
- multi-instance visibility and runbook verification

The broader MQ rollout remains valid, but the current approved kickoff intentionally starts from the admission tasks instead of from the older background-stream ordering.

## 14. Remaining Non-Blocking Open Points

The following items are intentionally left to implementation planning rather than product re-decision:

- exact endpoint names for status, attach, and result fetch
- exact Redis key names and relay key layout
- exact fairness algorithm shape, as long as low-tier starvation is prevented
- exact status polling interval used by the frontend
- whether `admitted but not yet executing` is exposed as a distinct user-visible label or folded into a broader "已开始处理" state

These are implementation details, not unresolved product decisions.
