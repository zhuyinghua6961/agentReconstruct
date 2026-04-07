# Refresh-Survivable QA Tasks Design

**Date:** 2026-04-06

## Summary

This document upgrades the current QA execution model from request-bound streaming into gateway-managed background tasks that:

- survive full page refresh
- support durable queued admission before LLM execution starts
- enforce both per-user active-task caps and a deployment-wide LLM concurrency ceiling
- preserve direct live streaming after admission instead of replacing streaming with queue polling

The shipped feature must be real end-to-end behavior:

- a QA request becomes a gateway-managed task with an explicit `task_id`
- if LLM capacity is available, the task is admitted and starts execution immediately
- if LLM capacity is full, the task enters a durable queued state instead of dying with the page
- the next page instance can discover queued or running work from conversation data
- the page can reattach with `last_seq`, replay missed state/content events, and continue live streaming
- stop after refresh cancels the real queued or running task rather than only clearing local UI state

This design applies to authenticated QA ask flows only. It covers:

- ordinary QA
- file QA
- hybrid QA
- routed execution through `fast`, `thinking`, and `patent`

This phase does not expand to document-assist endpoints such as translation, literature preview, or reference preview. Those may also use LLMs, but they are not part of this rollout.

This document supersedes the earlier "refresh-safe for QA tasks only after creation" design by making the task lifecycle admission-aware and by integrating the existing Redis admission model into the public task contract.

### Explicit Overrides Of Older Admission Decisions

This document intentionally overrides the older polling-oriented admission kickoff decisions where they conflict with refresh-survivable task behavior.

Specifically, this document supersedes the older decision set by requiring that:

- queued, admitted, and running recovery use the task events endpoint rather than a polling-only queued-status model
- the public normalized task statuses are `queued`, `admitted`, `running`, `completed`, `failed`, `canceled`, and `expired`
- real running-task cancellation is required, not queue-phase-only cancellation
- any legacy/raw admission status spelled `cancelled` is normalized to public `canceled`

---

## Implementation Integrity

This feature must be implemented as real working behavior, not as a shell, placeholder, or demo-only approximation.

Explicitly disallowed:

- UI-only "recoverable" badges without a real server-owned task that continues after refresh
- fake `task_id` creation that still binds execution lifetime to the original page request
- replay APIs that return only snapshots while claiming event-level recovery
- local-only placeholder messages that are not real persisted conversation messages
- stop buttons that only change frontend state without canceling the actual queued or running task
- concurrency checks that only update UI state but do not protect the real task path
- queue-full behavior that still persists half-created conversation state without a real task
- tests that only assert new field names or route shapes while the underlying task still dies on refresh or bypasses admission

Required standard:

- refreshing the page must leave the real task alive, whether it is queued or running
- reopening the same conversation must recover the actual queued/running task rather than silently starting a new one
- the persisted conversation must contain the real placeholder/terminal assistant message used by the task path
- cancel after refresh must stop the real queued/running work
- verification must demonstrate true end-to-end queueing, replay, recovery, and cancel behavior rather than mocked or no-op substitutes

---

## Goals

- Support refresh-safe survival for running and queued QA tasks.
- Keep the per-user cap as "at most 5 real active tasks", where `queued + admitted + running` all count.
- Add a deployment-wide LLM admission ceiling so local DeepSeek-style deployments can protect real execution capacity.
- Keep queue wait distinct from execution: queued tasks do not occupy LLM execution slots.
- Preserve current streaming event semantics once a task is admitted and execution begins.
- Make current conversation list/detail payloads sufficient for task discovery by exposing `active_task`.
- Keep old `ask` / `ask_stream` routes working during rollout.

## Non-Goals

- No attempt to make gateway process restarts perfectly lossless for already-running upstream streams.
- No expansion to non-QA long-running tasks or document-assist endpoints in this phase.
- No support for multiple simultaneous active ask tasks inside the same conversation.
- No accurate queue rank or ETA promises in the frontend.
- No removal of the legacy `ask` / `ask_stream` APIs in this phase.
- No new full operator console beyond extending existing admission inspection surfaces.
- No hot-reload support for admission configuration in this phase.

## Patent Rollout Gate Status

`patent` remains a rollout gate for this feature. It is not considered enabled just because the gateway task path now supports `actual_mode="patent"`.

### What Exists In This Repository

- Gateway config and backend registry already accept `PATENT_BACKEND_BASE_URL` and resolve a `patent` backend target.
- The patent service exposes `/api/ask_stream`, `/api/v1/ask_stream`, `/api/patent/ask_stream`, and `/api/v1/patent/ask_stream`.
- The patent service also exposes `/api/health` and versioned health aliases.

### Why The Gate Is Still Not Open

Local code and verification show the backend contract needed by refresh-survivable gateway-owned tasks is still incomplete:

- The patent request model derives persistence purely from `conversation_id`. With a non-null conversation id it always enters durable persistence mode; there is no `X-Gateway-Owned-Persistence: 1` bypass equivalent in the patent request/router path.
- The patent streaming router does not monitor client disconnect and does not pass a live cancel signal into execution. The current stream path acquires a slot, iterates a generator, and releases the slot in `finally`, but there is no gateway-driven cancel contract equivalent to the fast/highThinking task path.
- Several patent runtime interfaces already expose `should_cancel`, but the current router / ask service integration does not wire a real disconnect-driven cancel callback through that boundary.
- In the current local environment, patent health is not ready for rollout verification: targeted health checks returned `503 Service Unavailable`, so the deployment gate is not satisfied even before gateway-owned persistence parity is considered.

### Local Verification Evidence

Targeted verification run on 2026-04-06:

- `PYTHONPATH=/home/cqy/worktrees/highThinking/patent conda run --no-capture-output -n agent pytest -q /home/cqy/worktrees/highThinking/patent/tests/fastapi_contract/test_ask_contract.py::test_patent_route_aliases_all_dispatch_to_patent_ask /home/cqy/worktrees/highThinking/patent/tests/fastapi_contract/test_ask_contract.py::test_patent_request_normalizes_conversation_id_and_mode_classification -p no:cacheprovider`
  Result: both checks passed, confirming route aliases exist and `conversation_id` still forces durable persistence mode.
- `PYTHONPATH=/home/cqy/worktrees/highThinking/patent conda run --no-capture-output -n agent pytest -q /home/cqy/worktrees/highThinking/patent/tests/test_runtime_controls.py::test_health_exposes_configured_concurrency_state /home/cqy/worktrees/highThinking/patent/tests/fastapi_contract/test_health_contract.py::test_versioned_health_route_returns_ok_by_default -p no:cacheprovider`
  Result: both checks failed with `503`, confirming the local patent backend is not rollout-ready in this environment.

### Release Decision

For this refresh-survivable task rollout:

- `fast` and `thinking` are in scope for real enablement.
- `patent` must stay marked as rollout-gate-pending.
- No local mock or placeholder patent behavior should be added to claim parity.

---

## Current State

Current behavior is intentionally not refresh-safe and not globally admission-aware:

- the frontend keeps live stream ownership in page-local runtime state
- refresh clears busy runtime state and no live SSE is restored
- `fastQA` already reacts to disconnect cancellation
- `highThinkingQA` still executes ask streams as request-bound HTTP work rather than a detached gateway-owned task
- `gateway` already contains Redis-backed admission primitives:
  - request status storage
  - queue state and slot leases
  - event relay frame storage with monotonic sequence numbers
  - operator inspection routes under `/api/admission/...`
- current admission is infra-facing and not yet the public task contract for shipped QA asks
- current refresh-survivable design does not yet model `queued` and `admitted` as first-class user-visible task states

Those existing gateway primitives are the right foundation for this feature, but they must now become part of the user-facing task lifecycle instead of staying a hidden control-plane subsystem.

---

## Chosen Approach

### Option A: Keep refresh-survivable tasks but start LLM work immediately

Rejected because:

- it does not solve the new deployment-wide LLM ceiling requirement
- local model deployments would still be overrun during bursts
- queued work would remain request-bound or process-local

### Option B: Move the entire stream behind MQ

Rejected because:

- it would degrade already-admitted live streaming
- it conflicts with the existing rule that admission and live token transport are separate concerns
- it introduces unnecessary complexity into current SSE behavior

### Option C: Gateway-managed admission-aware tasks

Chosen because:

- the same task identity can represent queued, admitted, running, and terminal work
- the existing gateway admission, relay, and slot-lease infrastructure can be reused
- per-user caps, global LLM slots, recovery, replay, cancel, and conversation enrichment all remain centralized
- once admitted, execution can keep current direct streaming semantics

---

## Fixed Product Decisions

The following decisions are fixed for this implementation:

1. Refresh recovery must auto-resume streaming; it is not "final result only".
2. The design target covers all authenticated QA ask flows, including ordinary QA, file QA, and hybrid QA.
3. Recovery uses incremental replay via `last_seq` when available.
4. A refreshed-page stop action must cancel the real queued or running task.
5. The same conversation may have only one active task at a time.
6. The same account may reattach from any page or device.
7. `queued`, `admitted`, and `running` all count toward the per-user active-task cap.
8. Only admitted/running tasks consume global LLM execution slots.
9. The UI auto-recovers the current conversation and shows "queued / recoverable" or "generating / recoverable" badges for other conversations.
10. Multiple pages may watch the same task simultaneously.
11. Recovery failure degrades to conversation truth rather than leaving the task hanging in UI.
12. Running-task accounting is per real task, not per subscriber or per page.
13. The public API uses explicit `task_id`; gateway may map that 1:1 to its internal `request_id`.
14. The task API becomes admission-aware and exposes `queued` and `admitted` explicitly rather than compressing them into a generic `pending`.
15. `fast` and `patent` are high-priority admission work; `thinking` is low-priority but must not starve.
16. Queue rank and ETA are not promised in this phase.
17. This phase covers only authenticated task-backed QA APIs, not anonymous compatibility routes.

### Scope Boundary During Rollout

Until legacy QA routes are internally redirected into the new task path, the following guarantees apply only to `POST /api/v1/tasks` and its related task endpoints:

- cross-refresh survival
- per-user active-task cap
- same-conversation active-task guard
- global LLM admission ceiling
- queued recovery, queued expiry, and queued cancellation
- replay and reattach behavior

Legacy `ask` / `ask_stream` routes remain compatibility paths during rollout and may bypass these guarantees until they are cut over to create the same gateway task records.

---

## Architecture

### Ownership

`gateway` owns the QA task lifecycle:

- task creation
- same-conversation active-task guard
- per-user active-task cap enforcement
- deployment-wide LLM admission
- high/low-tier queueing
- background worker execution
- downstream SSE consumption
- frame relay persistence
- task replay and reattach
- task cancellation
- public task APIs

`public-service` remains the source of truth for durable conversation data:

- user message persistence
- assistant placeholder message
- queued/running progress snapshots
- terminal assistant turn
- conversation `active_task_id` binding

`fastQA`, `highThinkingQA`, and the external patent backend remain execution backends:

- they still emit the existing stream event family
- they must honor disconnect-driven cancellation from gateway-owned worker connections
- for task-backed gateway executions, they must not persist conversation user/assistant turns themselves

### Storage Split

Gateway hot task state and event relay live in Redis:

- task summary record
- admission queue record
- sequence counter
- replayable frames
- cancel flag
- worker heartbeat / ownership marker
- global and tier-specific slot leases

Conversation ownership remains in public-service durable storage:

- `active_task_id`
- user turn
- assistant placeholder message bound to the task
- partial assistant content and step snapshots
- final terminal assistant state

### Public Identity

Externally the system exposes `task_id`.

Internally gateway may store the same string as the existing admission `request_id`. The feature must not maintain separate identities for "task" and "request".

### Canonical Task Record and Admission Mapping

The new task system does not create a second Redis lifecycle model beside the existing gateway admission stores.

Canonical rule:

- one user-facing task equals one underlying gateway admission `request` record plus its queue status, relay frames, and lease metadata

API boundary:

- `/api/v1/tasks/*` is the user-facing normalized contract
- `/api/admission/*` remains the operator-facing raw control-plane view

Implementation rule:

- the task API wraps and normalizes the existing admission/request record instead of storing a second task record elsewhere
- running-task cancel extends the current queue-only cancel semantics by adding live cancel intent plus worker-owned upstream abort
- once the feature flag is enabled, the task path becomes the shipped admission-aware QA path

---

## Global LLM Admission Model

### Admission Boundary

This design intentionally puts queue-backed control at the execution entrance, not inside the live stream.

Rules:

- queueing happens before backend LLM execution begins
- once a task is admitted, current direct backend `ask_stream` execution remains the live transport path
- queued tasks do not occupy LLM slots while waiting
- reconnecting, replaying, or multi-page watching does not consume additional LLM slots

### Covered Request Classes

This phase covers all authenticated task-backed QA asks that will enter LLM execution through the new task API, including:

- ordinary QA
- file QA
- hybrid QA

Route-tier assignment follows `actual_mode`, not merely `requested_mode`.

This means file-QA requests routed to fast execution still use the high tier, while file-QA requests routed to thinking still use the low tier.

### Capacity And Priority

Required configurable knobs:

- deployment-wide LLM ceiling: `INTERACTIVE_EXECUTION_MAX_CONCURRENT`
- high-tier ceiling: `INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT`
- low-tier ceiling: `INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT`
- per-user active-task cap: `INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE`
- low-tier guaranteed progress floor: `INTERACTIVE_EXECUTION_THINKING_MIN_SLOTS`
- queued-task TTL: `INTERACTIVE_QUEUED_TTL_SECONDS`
- queued backlog ceiling: `INTERACTIVE_QUEUE_MAX_SIZE`

Recommended initial defaults for this rollout:

- deployment-wide LLM ceiling: `20`
- high-tier ceiling: `20`
- low-tier ceiling: `5`
- per-user active-task cap: `5`
- low-tier guaranteed progress floor: `1`
- queued-task TTL: `900` seconds
- queued backlog ceiling: `200`

Scheduling rules:

- `fast == patent` high tier
- `thinking` low tier
- low tier must not starve indefinitely
- a single global slot pool is used, with central scheduling deciding which tier receives the next admission
- admission must satisfy both the global ceiling and the selected tier ceiling
- the low-tier guaranteed progress floor does not replace the low-tier ceiling; it only prevents starvation within the allowed global/tier capacity
- existing gateway settings for `fast_or_patent_max_concurrent` and `thinking_max_concurrent` remain first-class controls in this rollout rather than being removed

### Queue Capacity

The queue itself is bounded.

Rules:

- if the queue still has capacity, a new task may be created in `queued` state
- if the queue is full, creation fails fast with overloaded / queue-full semantics
- queue-full failures do not create a task, do not persist conversation state, and do not leave half-created placeholders

### Patent Readiness Gate

Patent execution participates in the high tier only when the configured backend is actually ready for this contract.

Rules:

- if the patent backend is placeholder, disabled, or missing task-contract parity, patent readiness must fail before enqueue or admission
- unready patent requests must not consume queue backlog or slot budget

---

## Public API Contract

The new frontend path uses explicit task APIs under gateway.

### 1. `POST /api/v1/tasks`

Creates a QA task and either admits it immediately or places it into the durable queue.

Request body:

- `conversation_id: number` required
- `question: string` required
- `requested_mode: "fast" | "thinking" | "patent"` required
- `chat_history: []` optional, kept only for compatibility with current routing behavior
- `pdf_context: object | null` optional, unchanged from current ask payload contract

Behavior on success:

- reject if the same conversation already has an active queued/admitted/running task
- reject if the user already has 5 active tasks under the configured cap
- fail fast if the selected backend is not executable in the current deployment
- persist the user turn
- create the assistant placeholder message in public-service
- bind `active_task_id` onto the conversation
- create the gateway task record
- either admit the task immediately or enqueue it durably

Behavior on failure:

- if creation fails before a durable task exists, gateway must roll back any partially created user turn / placeholder / `active_task_id`
- queue-full / overloaded failures do not persist a user turn or assistant placeholder

Success response fields:

- `task_id`
- `conversation_id`
- `assistant_message_id`
- `status`
- `requested_mode`
- `actual_mode`
- `route`
- `queue_tier`
- `last_seq: 0`
- `events_url`
- `cancel_url`
- `created_at`
- `expires_at` when status is `queued`

Notes:

- `status` may be `queued`, `admitted`, or `running` depending on timing
- `assistant_message_id` is the externally stable string `message_id` used in conversation detail payloads; it is never the numeric row id

### 2. `GET /api/v1/tasks/{task_id}`

Returns the gateway task summary.

Response fields:

- `task_id`
- `conversation_id`
- `assistant_message_id`
- `status`
- `requested_mode`
- `actual_mode`
- `route`
- `queue_tier`
- `created_at`
- `expires_at`
- `admitted_at`
- `started_at`
- `updated_at`
- `finished_at`
- `last_seq`
- `cancel_allowed`
- `replay_available`
- `terminal`
- `error`

### 3. `GET /api/v1/tasks/{task_id}/events?after_seq=<n>`

SSE endpoint used for first attach, queued-state observation, and refresh recovery.

Rules:

- if `after_seq=0`, the endpoint may replay the full retained frame set before switching to live tailing
- if `after_seq>0`, the endpoint must replay only frames with `seq > after_seq`
- queued/admitted state transitions are represented as state events and use the same monotonic `seq` space as later content events
- once caught up, the endpoint stays attached and streams newly appended frames
- the endpoint returns a terminal completion and closes when the task is terminal and no newer frames remain

Each event keeps current payload semantics and additionally includes:

- `task_id`
- `conversation_id`
- `assistant_message_id`
- `seq`

Queued/admission state events are lightweight control-plane events. They do not emit fake content frames.

### 4. `POST /api/v1/tasks/{task_id}/cancel`

Cancels a queued or running task.

Rules:

- any subscriber page for the same authenticated user may cancel
- cancel is idempotent
- queued cancel removes the task from the queue and terminalizes it as `canceled`
- running cancel sets the gateway cancel flag, terminates the worker-owned upstream stream, and emits a terminal canceled event
- cancel on a terminal task returns the existing terminal state rather than a second cancellation

### Legacy API Compatibility

Existing `POST /api/v1/{mode}/ask` and `POST /api/v1/{mode}/ask_stream` stay available during rollout.

Rules:

- legacy routes keep current behavior
- legacy routes do not gain refresh survival or admission queue guarantees in this phase
- the new frontend path behind feature flag switches to `POST /api/v1/tasks`
- if a later rollout phase needs the same guarantees on legacy routes, those routes must internally create the same gateway task records rather than implementing a second execution model

---

## Task State Model

Gateway task statuses are:

- `queued`
- `admitted`
- `running`
- `completed`
- `failed`
- `canceled`
- `expired`

State rules:

- `queued` means the task exists durably, has conversation truth, but has not yet acquired an LLM execution slot
- `admitted` means the scheduler has granted an execution slot and handed ownership toward a worker
- `running` starts once the worker has begun downstream execution
- `completed`, `failed`, `canceled`, and `expired` are terminal
- `expired` is the terminal state for queued tasks that aged out before admission; it is not merely a relay-GC label

Each task record stores at least:

- `task_id`
- `user_id`
- `conversation_id`
- `assistant_message_id`
- `requested_mode`
- `actual_mode`
- `route`
- `queue_tier`
- `status`
- `created_at`
- `expires_at`
- `admitted_at`
- `started_at`
- `updated_at`
- `finished_at`
- `last_seq`
- `cancel_requested`
- `terminal_event_type`
- `terminal_code`
- `terminal_message`

Retention rules:

- while queued or running, task and relay TTLs must be renewed so they do not expire mid-flight
- after terminalization, task summary and replay frames remain available for a bounded TTL
- the recommended initial retained TTL after terminalization is 30 minutes for all terminal states

---

## Conversation Contract Changes

Conversation list/detail payloads exposed through gateway must include:

- `active_task: null | { task_id, status, requested_mode, actual_mode, route, queue_tier, created_at, expires_at, started_at, updated_at, cancel_allowed, replay_available, last_seq }`

### Binding Rules

Public-service persists `active_task_id` on the conversation record or conversation metadata.

Gateway enriches list/detail responses by:

1. reading `active_task_id` from conversation truth
2. reading the live task summary from gateway Redis
3. returning `active_task = null` if the bound task is missing or already beyond replay retention

This enrichment must work on both:

- `GET /api/conversations*`
- `GET /api/v1/conversations*`

### Assistant Placeholder Rules

Successful task creation must create a real assistant placeholder message immediately, even when the task starts in `queued`.

The placeholder is bound to `task_id` and is the only assistant message that may receive progress or state updates for that task.

The placeholder message metadata must include:

- `task_id`
- `status: queued | admitted | running | completed | failed | canceled | expired`
- `done_seen`
- `terminal_status`
- `last_seq`
- `requested_mode`
- `actual_mode`
- `route`
- `queue_tier`

The placeholder creation flow returns the stable string `assistant_message_id`; task APIs and replay events must always use that same identifier for frontend message binding.

### Progress Sync Rules

During execution, gateway batches progress into public-service for the bound placeholder message:

- queued/admitted/running status changes
- appended assistant content
- normalized steps
- `last_seq`
- latest task status

References, `doi_locations`, and heavier terminal-only metadata may be deferred until the terminal event.

### Required Public-Service Internal APIs

Current authority assistant async APIs are terminal/final-event oriented and are not sufficient for refresh-survivable progress synchronization. This design therefore requires new internal conversation write contracts keyed by `task_id`.

Required endpoints:

- `POST /internal/conversations/{conversation_id}/tasks/{task_id}/assistant-start`
- `POST /internal/conversations/{conversation_id}/tasks/{task_id}/assistant-progress`
- `POST /internal/conversations/{conversation_id}/tasks/{task_id}/assistant-terminal`

Contract rules:

- `assistant-start` creates or idempotently returns the real placeholder message and binds `active_task_id`
- `assistant-progress` updates only the bound placeholder message, accepts queued/admitted/running state updates plus incremental content append plus normalized steps plus `last_seq`, and refreshes conversation cache state
- `assistant-terminal` finalizes the placeholder as `completed`, `failed`, `canceled`, or `expired`, writes terminal metadata, clears `active_task_id`, and refreshes cache state
- all three endpoints are idempotent by `(conversation_id, task_id)`
- public-service schemas must distinguish stable external `assistant_message_id` from internal numeric row ids

### Terminal Rules

On `completed`, `failed`, `canceled`, or `expired`:

- gateway writes terminal state into the same placeholder message
- gateway clears `active_task_id` from the conversation
- conversation detail/list thereafter show `active_task = null`

---

## Worker And Scheduler Execution Model

### Submission And Queue Flow

1. Gateway validates the request, resolves file context, and determines `actual_mode` and `route`.
2. Gateway checks same-conversation and per-user active-task limits.
3. Gateway checks backend readiness.
4. Gateway persists user turn + assistant placeholder + `active_task_id`.
5. Gateway creates the durable task record.
6. If LLM capacity is available, the task is admitted immediately.
7. If LLM capacity is full but queue capacity remains, the task enters `queued`.
8. If queue capacity is also full, creation fails without durable conversation state.

### Scheduler Rules

Implementation should reuse and extend:

- `execution_admission.py`
- `execution_event_relay.py`
- `execution_queue_status.py`
- `execution_slot_leases.py`

The feature should not create a second unrelated scheduler/status subsystem.

Scheduling rules:

- central scheduler decides admission across all gateway instances
- scheduler/dispatcher ownership remains a dedicated gateway admission-worker role or deployment; normal web-serving gateway processes remain producer-only for task submission, reads, and attach/cancel APIs
- high tier is preferred over low tier
- low tier must still make progress according to the configured minimum-slot guarantee
- slot ownership must be represented with renewable Redis leases
- if a worker dies, lease expiry must allow safe reclamation

### Worker Flow

1. Claim `admitted` task.
2. Mark it `running` and start worker heartbeat renewal.
3. Open downstream ask stream to the selected QA backend using the task-execution contract described below.
4. For each downstream event:
   - normalize it
   - assign monotonic `seq`
   - append it to Redis relay
   - update task summary
   - batch-sync assistant placeholder progress to public-service
5. On terminal event:
   - persist terminal conversation state
   - mark task terminal
   - release the LLM slot immediately
   - keep relay available for the retained TTL

### Backend Execution Contract For Task-Backed Runs

Current `fastQA` and `highThinkingQA` ask paths still persist conversation state themselves. The task-backed gateway worker must therefore call downstream execution in a mode that disables QA-service-owned conversation persistence.

Fixed contract:

- gateway task workers continue to use the current mode ask-stream routes for execution shape compatibility
- gateway adds `X-Gateway-Task-Execution: 1`
- gateway adds `X-Gateway-Owned-Persistence: 1`
- when those headers are present, downstream QA services must:
  - execute normally
  - emit the same stream event family
  - skip user-turn persistence
  - skip assistant final persistence
  - skip assistant terminal persistence

This contract applies to:

- `fastQA`
- `highThinkingQA`
- the external patent backend before patent rollout is enabled

Without this contract, the feature is invalid because gateway-owned placeholder/progress writes would race with backend-owned terminal writes and create duplicate or contradictory conversation history.

### Quota Lifecycle

Quota follows the existing gateway/public-service grant model and is bound to task creation, not to task subscription.

Rules:

- `POST /api/v1/tasks` performs the same quota precheck/create-grant step currently used by `ask` / `ask_stream`
- the resulting task record stores the quota `grant_id`
- queued/running recovery, detail reads, and event subscriptions never mutate quota
- terminal `completed` finalizes the stored grant with `success=true`
- terminal `failed`, `canceled`, or `expired` abort the stored grant
- if task creation fails after quota grant creation but before durable task creation, gateway aborts the grant before returning failure
- refresh recovery and multi-subscriber attach never create a second quota reservation

---

## Cancellation Design

This feature requires real queued/running-task cancellation, not just disconnecting the browser page.

### Gateway Cancellation

`POST /api/v1/tasks/{task_id}/cancel` must:

- set task cancel intent in Redis
- notify the owning scheduler/worker when relevant
- remove a queued task from the queue or abort a running task's upstream stream
- emit a terminal cancellation event
- finalize the placeholder message as `canceled`

### Downstream Service Requirements

All three QA services must treat gateway disconnect or canceled upstream stream ownership as cancellation.

Explicitly:

- `fastQA` may continue to use its existing disconnect-aware cancellation behavior
- `highThinkingQA` must gain disconnect-aware cancellation semantics equivalent to `fastQA`
- `patentQA` must also honor the canceled upstream stream and stop generation

The shipped feature is not acceptable if cancel only changes gateway task state while the real downstream ask keeps running to completion.

---

## Frontend Behavior

### Send Path

The new frontend path:

1. ensures the conversation already has a real `conversation_id`
2. calls `POST /api/v1/tasks`
3. binds to the assistant placeholder message returned by the server
4. subscribes to `/api/v1/tasks/{task_id}/events?after_seq=0`

If the response is `queued`, the frontend shows explicit queue state rather than pretending generation has started.

### Recovery Path

On page load or conversation switch:

- the current conversation detail is loaded
- if `active_task` exists, the page auto-recovers that task
- recovery works for `queued`, `admitted`, and `running`
- the frontend reads the locally persisted `last_seq` for that `task_id` if available
- if no local cursor exists, recovery uses `after_seq=0`

Local cursor persistence rules:

- store only minimal per-task replay cursor state in local storage
- do not treat local storage as the source of truth for task discovery
- if local cursor is missing or stale, gateway replay remains authoritative

### Sidebar Behavior

For conversations other than the current one:

- show "queued / recoverable" or "generating / recoverable" status when `active_task` is present
- do not auto-open their SSE subscription until the user switches into that conversation
- once opened, auto-recover using the same task endpoint

### Multiple Subscribers

Multiple pages may subscribe to the same task concurrently.

Rules:

- the task executes once
- every subscriber may read frames
- any subscriber for the same user may cancel
- terminalization is broadcast by the shared relay, not by page ownership

### UX Simplifications

This phase does not promise:

- accurate queue rank
- accurate ETA
- a manual resume button for the current conversation

The frontend should instead rely on explicit state labels such as:

- `queued` -> "排队中"
- `admitted` -> "已开始处理"
- `running` -> "正在生成"

---

## Failure and Recovery Rules

### Refresh During Queued Or Running Task

- task keeps existing in gateway state
- new page instance discovers `active_task`
- frontend reattaches using `last_seq`
- queued/admitted state events replay if missing
- if already running, missed frames replay and live stream continues

### Missing Relay Frames

If replay is no longer available but conversation detail already contains newer durable message state:

- frontend degrades to conversation truth
- frontend clears local recovery state for that task
- no fake "still generating" or "still queued" UI may remain

### Queue Expiry

If a queued task reaches its TTL before admission:

- task transitions to terminal `expired`
- gateway writes `expired` into the placeholder message
- gateway clears `active_task_id`
- the stored quota grant is aborted

### Gateway Restart

Gateway restart handling in this phase is best-effort, not seamless.

Required guarantee:

- task records and relay state survive in Redis
- frontend does not hang forever on stale queued/running state
- startup recovery or stale-task sweeping must converge old work to either:
  - resumed ownership when still possible, or
  - explicit terminal failure / expiration with operator-visible diagnostics

Not guaranteed in this phase:

- lossless continuation of an already-open downstream stream across gateway process restart

### Public-Service Write Failure During Progress

- task streaming must continue if intermediate progress sync fails
- gateway retries bounded progress sync writes
- terminal write has higher priority than intermediate progress sync
- if terminal conversation write fails after task terminalization, task summary must surface that failure for operator diagnosis

---

## Rollout

Rollout is feature-flagged.

Required flags:

- gateway backend flag enabling the admission-aware task API and worker path
- frontend flag switching Home ask flow from legacy `ask_stream` to task APIs

Rollout rules:

- new task creation is disabled when the feature flag is off
- existing queued/running tasks created before rollback must still be allowed to complete
- legacy routes remain available until rollout completes and the new path is stable

Recommended rollout order:

1. deploy gateway/public-service/backend support behind flags
2. verify queue visibility, slot visibility, replay correctness, and cancel behavior in dev/test
3. enable for selected accounts or non-prod environments
4. switch frontend flag for broader rollout

### Patent Backend Rollout Gate

This repository does not contain a local `patentQA/` implementation. Patent support in this spec therefore depends on the external backend behind `PATENT_BACKEND_BASE_URL` satisfying the same stream and cancel contract as `fastQA` and `highThinkingQA`.

Rollout rule:

- `fast` and `thinking` may ship once the local repositories satisfy the task + admission contract
- `patent` is enabled under the same feature set only after the external patent backend is verified to support:
  - gateway-worker-owned ask stream execution
  - disconnect-driven cancellation
  - terminal event parity with the shared event contract

---

## Minimal Operator Visibility

No new full console is required in this phase, but the system must expose enough diagnostics to debug stuck or failed admission/recovery.

Existing `/api/admission/...` inspection routes should be reused or extended so operators can inspect at least:

- task summary by `task_id`
- queue status and queue tier
- current total slot usage
- high-tier and low-tier slot usage
- queue lengths
- oldest queued-task age
- last persisted `last_seq`
- relay frame counts
- terminal result payload
- cancel flag / ownership state
- worker heartbeat freshness

Operators must also have a minimal control-plane ability to cancel stuck queued or running tasks.

The public user-facing task API is separate from these operator inspection routes.

---

## Gateway Conversation Enrichment Cutover

Current gateway conversation routes are generic pass-through proxy routes. This feature requires gateway-owned enrichment for conversation list/detail reads.

Required cutover:

- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`

Behavior:

1. gateway fetches the current public-service payload
2. gateway reads `active_task_id` from that conversation payload or bound metadata
3. gateway joins live task summary from the task/admission store
4. gateway returns enriched `active_task`

All other conversation routes may remain pass-through in this phase.

---

## Acceptance Criteria

- A queued or running QA task survives full page refresh.
- Reopening the same account on another page can reattach to the same queued or running task.
- Reattachment uses `task_id` and replay cursor semantics rather than starting a duplicate ask.
- Under the new `/api/v1/tasks` path, the same conversation cannot create a second active task while one is already queued or running.
- Under the new `/api/v1/tasks` path, the same user cannot exceed 5 active queued/admitted/running tasks across all conversations combined.
- Only admitted/running tasks count against the deployment-wide LLM ceiling.
- Queue wait does not consume an additional quota slot or a second LLM slot.
- Queue-full failures do not leave persisted half-created conversation state.
- Sidebars show queued/running recoverable state for non-current conversations with `active_task`.
- The current conversation auto-recovers without a manual "resume" button.
- `stop` after refresh cancels the real queued or running task.
- Assistant placeholder messages are real persisted conversation messages, not frontend-only shells.
- Conversation detail/list include `active_task` when a task is still live.
- Replay frames are retained for the configured terminal TTL.
- Missing replay data degrades cleanly to conversation truth rather than leaving stale busy UI.
- Legacy `ask` / `ask_stream` routes keep working during rollout.

---

## Test Plan

### Gateway

- create task success path across `fast` and `thinking`, plus an external integration gate for `patent`
- same-conversation second create rejected while a queued or running task exists
- per-user active-task cap enforced on `queued + admitted + running`
- deployment-wide LLM slot ceiling enforced on admitted/running work only
- queue-full creation fails without persisting conversation state
- event relay uses monotonic `seq` across state events and content events
- `after_seq` reattach replays only missing frames
- queued refresh recovery resumes queue-state observation
- multi-subscriber reattach sees the same frame sequence
- cancel transitions queued/running task to terminal canceled and stops further execution
- stale queued/running task after simulated worker loss converges to explicit recovery or terminalization

### Public-Service

- task creation binds `active_task_id`
- assistant placeholder message exists immediately after task creation, even when queued
- `assistant-start`, `assistant-progress`, and `assistant-terminal` are idempotent by `(conversation_id, task_id)`
- progress sync updates placeholder status, content, steps, and `last_seq`
- terminal write clears `active_task_id`
- `expired` terminalization writes through the same placeholder instead of deleting it
- under the task path, conversation history contains exactly one persisted user turn and one bound assistant placeholder/terminal message
- conversation detail/list surface queued/running `active_task` enrichment correctly through gateway

### QA Services

- disconnect-driven cancel stops real downstream work for `fast` and `thinking`, plus an external integration gate for `patent`
- canceled task does not later emit a contradictory successful terminal event
- when `X-Gateway-Owned-Persistence: 1` is present, QA execution skips its current conversation persistence hooks

### Frontend

- send path uses task creation when flag enabled
- queued creation renders explicit queue state rather than fake generation
- conversation list/detail reads used by the shipped frontend receive `active_task` on both `/api/...` and `/api/v1/...` aliases
- refresh recovers current conversation automatically for queued and running tasks
- recovery from saved `last_seq` continues without duplicating already-rendered frames
- no saved cursor falls back to `after_seq=0`
- non-current conversations show recoverable badges for queued and running work
- switching into a recoverable conversation auto-attaches
- refreshed stop button cancels the task and updates the message terminal state
- conversation detail fallback replaces stale recovery state when replay is unavailable

### Test Execution Policy

Verification is part of the feature, not optional follow-up work.

Rules:

- the implementer must run the relevant automated tests for every touched subsystem before claiming the feature works
- if a required test command needs elevated permissions, the implementer must request elevation and run it
- if the environment does not allow the required elevation, the implementer must stop and explicitly report which verification step is blocked and why
- lack of permission to run required tests is a blocker to claiming the feature is complete
- manual spot-checks may supplement automated coverage but may not replace the required test runs
