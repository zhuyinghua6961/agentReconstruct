# Cross-Service QA Cancellation Design

**Goal:** Make user cancellation stop backend QA execution promptly across gateway task mode, legacy streaming proxy mode, fastQA, highThinkingQA, and patent QA without adding measurable overhead to successful non-canceled requests.

**Status:** Draft

---

## Background

The frontend can cancel an active QA turn, but cancellation does not reliably stop backend work. The most visible symptom was in patent mode: canceling one durable turn and immediately asking again can return `durable patent turn is already in flight`. The same execution shape exists in fastQA and highThinkingQA: outer HTTP streams can close, while inner worker threads or upstream LLM calls continue until they produce a chunk, hit a timeout, or reach a cooperative cancellation check.

The current system has two active streaming paths:

- **Gateway task mode** via `/api/v1/tasks`: gateway owns task status, relays events, and can mark a task canceled.
- **Legacy ask_stream proxy mode** via `/api/{mode}/ask_stream`: gateway streams an upstream backend response directly to the browser with quota handling.

Both paths must propagate cancellation to the selected backend and release backend-local runtime state. This design focuses on cancellation correctness and resource release, not on changing answer generation behavior.

## Current Evidence

### Gateway Task Mode

`gateway/app/services/qa_tasks.py` tracks active task streams and `/tasks/{task_id}/cancel` calls `_abort_live_stream()`. That only works after the upstream stream handle has been registered. If cancellation happens while opening the upstream stream, or while the worker is blocked waiting for `handle.body_iter()` to produce a chunk, the task can be marked canceled while backend execution continues.

The worker checks task terminal state after receiving chunks. It does not currently race upstream reads against a cancellation event.

### Gateway Legacy Proxy Mode

`gateway/app/routers/qa.py` uses `StreamingProxyHandle.body_iter()` to forward upstream bytes. If the downstream client disconnects, the generator is closed by ASGI, and quota is finalized in the error/finally path. The path does not maintain a first-class cancellation handle comparable to task mode, so cancellation depends on closing the upstream response and backend request disconnect behavior.

### fastQA

`fastQA/app/routers/qa.py` creates a `cancel_event` and passes `should_cancel=cancel_event.is_set` through route dispatch. `fastQA/app/core/sse.py` sets the event on disconnect and tries to stop a sync producer thread.

The limitation is that the sync producer may already be blocked inside `next(iterator)` or inside a downstream LLM/HTTP call. Some stages accept `should_cancel`, but stage1 planning and several non-streaming LLM calls do not check cancellation while blocked.

### highThinkingQA

`highThinkingQA/server_fastapi/routers/ask.py` already propagates a `cancel_event` in gateway-owned mode and polls a queue every 50ms. `highThinkingQA/server/services/ask_service.py` passes the event to `run_agent`.

The inner agent checks cancellation at stage boundaries and while iterating some streaming output. It still blocks on synchronous future waits and OpenAI SDK calls that do not accept a cancellation token. Non-gateway legacy streaming has weaker cancellation propagation.

### patent QA

`patent/server_fastapi/routers/ask.py` has partial changes that pass a `cancel_event` to `AskService.stream_ask`. `patent/server/services/ask_service.py` has partial changes to propagate `should_cancel` to execution stages. These changes are currently uncommitted and insufficient for the original durable busy symptom because blocking queue waits and generator close races can still delay cleanup.

Patent durable state is stricter than fastQA/highThinkingQA: if `pending_turn` for a conversation remains set under an old trace, the next turn is rejected as busy.

## Design Goals

- Canceling a task or disconnected stream must stop useful backend work as soon as practical.
- Backend-local runtime state must be released on cancellation, including patent durable `pending_turn`, inflight markers, locks, stream slots, and quota grants.
- Normal successful requests must not pay extra Redis reads, extra network calls, or high-frequency polling overhead.
- Existing public SSE contracts remain compatible: canceled streams emit or persist canceled terminal status where applicable.
- Existing task replay semantics remain intact.
- Cancellation must be idempotent and race-safe with completion.

## Non-Goals

- Force-killing Python threads.
- Interrupting an already in-flight third-party LLM HTTP request at the TCP/socket level in every client implementation.
- Rewriting all LLM clients or replacing OpenAI SDK usage.
- Changing model prompts, retrieval logic, ranking behavior, or answer quality.
- Removing legacy `ask_stream`.

## Proposed Approach

Use a layered cooperative cancellation model with a single cross-service contract:

1. **Gateway owns user intent to cancel.** It records terminal canceled state, aborts any live upstream stream handle, and exposes cancellation state to workers.
2. **Gateway workers race upstream reads against cancellation.** Workers must not wait indefinitely for the next upstream chunk after a task is already terminal canceled.
3. **Backend routers translate disconnect/abort into service-local cancel events.** Each backend passes that event into execution code and closes generators safely.
4. **Backend services poll blocking queues with bounded waits.** Queue waits must wake quickly on cancellation without busy-spinning.
5. **Execution stages honor `should_cancel` before expensive work, after expensive work, and inside stream/chunk loops.**
6. **Terminalization is guarded.** A canceled turn must not later commit a successful answer from a stale worker.

This approach is intentionally cooperative. It avoids normal-path overhead and matches Python's runtime constraints.

## Architecture

### Gateway Cancellation Coordinator

Gateway task execution should keep a small live runtime entry for each admitted task:

- upstream stream handle, once available
- cancel event
- progress snapshot
- flush hook
- request metadata needed for terminal side effects

`cancel_task()` already updates task status first. It must set the live runtime cancel event immediately after loading the live runtime entry and before any awaited progress flush, state-frame append, terminal side effect, quota side effect, or upstream abort. If no handle exists yet, the event still gives the worker a local signal once it reaches a cancellation check.

The task worker should check the cancel event:

- before opening the upstream stream
- while waiting for upstream stream establishment
- immediately after opening but before registering it
- during the upstream read loop
- before terminalizing success or failure

### Interruptible Upstream Read Loop

The gateway worker should avoid a plain `async for chunk in handle.body_iter()` loop for task execution. It also should not await upstream stream establishment in a way that hides cancellation. Wrap upstream open and reads in helpers that can race:

- upstream stream establishment
- next upstream chunk
- task cancel event
- existing lease renewal cadence, without adding persistent polling

The helper should use low-frequency waits, for example 100-250ms. This is only active while waiting for upstream bytes and should not add persistent storage work when chunks arrive normally. Cancellation checks must be in-memory checks against the live runtime cancel event. Lease renewal should reuse the existing worker cadence and must not add extra Redis/status-store reads per idle wait tick.

If cancel wins before the upstream handle exists, the worker must skip opening or abandon the open attempt when the async client API allows it, then return the existing canceled terminal outcome. If cancel wins after the handle exists, the worker aborts the handle and returns the existing canceled terminal outcome.

Open/connect waits must also be bounded. The preferred implementation races `proxy_service.open_json_stream(...)` against the live cancel event and cancels/abandons the open task when cancel wins. If the HTTP client cannot be interrupted during a connect/read handshake, the maximum delay is the existing configured upstream connect/read timeout for the current open attempt; no subsequent backend work may start after cancellation.

Implementation requirement: the idle race loop may not call `queue_store.get_request()`, `relay_store.*`, Redis lock APIs, or quota APIs on each idle tick. Persistent state checks must happen only at existing transition points or at the current lease-renewal cadence. Any proposal to add persistent polling must include a measured performance budget and is out of scope for the first implementation.

### Backend Router Contract

Every backend `ask_stream` route should have an explicit local cancel event:

- **gateway-owned task mode:** monitor request disconnect and set the event; this covers gateway aborting the upstream response.
- **legacy stream mode:** closing the HTTP stream is the required frontend cancel mechanism. If the downstream client disconnects, gateway aborts the upstream handle, the backend router observes request disconnect or generator close, sets the event, and closes the source iterator/generator. Legacy mode does not have a separate durable `/cancel` contract.

For sync generators running in producer threads, router code should not rely on `generator.close()` alone. It should set the cancellation event first, then close the generator best-effort, then join with a short timeout and log if the worker remains alive.

Legacy frontend contract: the UI cancel button must abort the active `ask_stream` HTTP request with its `AbortController`. It must not call `/api/v1/tasks/{id}/cancel` because no task id exists in legacy mode. Gateway legacy proxy mode only owns upstream abort and quota finalization; backend services own any legacy chat assistant terminal persistence.

### Service-Level Queue Waits

Any service implementation that streams from a worker thread through a queue must avoid indefinite `queue.get()` while cancellation can be set externally. Use bounded waits around 50-200ms, or a sentinel inserted when cancellation is requested.

This applies to:

- patent `prepare_queue.get()`
- patent execution `progress_queue.get()`
- highThinking stream queue if future changes reintroduce indefinite waits
- fastQA sync producer patterns if they gain service-local queues

### Execution-Stage Cancellation

Each backend should add `should_cancel` checks at stage boundaries that precede expensive work:

- before stage1 LLM planning/decomposition
- before retrieval fanout
- before PDF/evidence loading
- before final synthesis LLM request
- inside streaming chunk loops
- after an expensive call returns, before caching or committing results

For blocking third-party SDK calls, the practical guarantee is "do not start the next expensive call after cancellation" and "do not commit/call terminal success after cancellation." Interrupting an already running SDK request remains best-effort through closing upstream HTTP streams and reasonable timeouts.

### Terminalization Guard

Each backend should treat cancellation as terminal. Once cancellation is observed:

- do not emit `done`
- do not cache canceled stage results as successful cache entries
- do not persist a successful assistant answer
- release runtime state
- persist canceled terminal status where that backend owns terminal persistence

For patent durable turns, `ChatPersistenceService` is authoritative for clearing `pending_turn`, inflight markers, turn identity, and conversation locks. Gateway may cancel a task and close the upstream request, but it must not directly delete patent durable coordination keys. Patent cleanup happens through `abort_turn()`, `accept_assistant_terminal_turn(... terminal_status=\"canceled\")`, or finalize failure handling inside the patent service.

Patent cleanup and success commit must be ownership-guarded. Every abort, pending clear, inflight clear, terminal accept, non-terminal assistant accept, overlay assistant write, result commit, and finalize path must compare the stored trace/ownership token before mutating durable state. A canceled worker for trace A must never clear, overwrite, or terminalize trace B if the user starts a new turn before trace A fully exits.

The required ownership rule is compare-and-swap by `(conversation_id, trace_id, owner token if present)`. `clear_pending_turn`, `clear_turn_inflight`, result commit, overlay assistant write, assistant accept, terminal accept, and lock release must be no-ops or controlled failures when the stored trace/owner does not match the runtime state being cleaned or committed. `abort_turn`, finalize, success commit, and terminal accept share the same rule; none may perform unconditional cleanup or success writes.

## Service-Specific Design

### gateway

Gateway should be the first fix target because it benefits all backends.

- Add cancel event to live runtime entries before the upstream stream opens, so cancellation during upstream connection establishment has a place to land.
- Let `/tasks/{task_id}/cancel` set the event even if the upstream handle is absent.
- Make upstream stream establishment cancellation-aware by racing the open await against the live runtime cancel event, or document and test fallback to the configured HTTP open timeout when the client cannot interrupt the handshake.
- Replace the task worker's blocking upstream read loop with a cancellation-aware iterator.
- In legacy proxy mode, define cancel as client stream closure. Ensure downstream disconnect calls `handle.abort()` and finalizes quota as unsuccessful. Backend-owned chat persistence remains responsible for any assistant terminal record in legacy mode.
- Keep task terminalization idempotent: if cancel races with done, whichever terminal state is already stored wins.
- Ignore late upstream frames after terminal state exists for the task.
- Do not introduce per-idle-tick queue/status-store reads in the task worker. Cancellation must be driven by the live runtime event and existing terminal transition checks.

### fastQA

fastQA should keep its existing `cancel_event` flow and add missing checks.

- Preserve the `sse_response(... on_disconnect=...)` model.
- Avoid relying on sync producer `stop_event` alone.
- Add `should_cancel` to stage1 planning and any wrapper that starts non-streaming LLM calls.
- Check cancellation after any graph direct-answer attempt and before falling through to generation.
- Prevent stage cache writes for canceled stage payloads.

### highThinkingQA

highThinkingQA already has the most complete outer stream cancellation.

- Use the same cancellation behavior for legacy stream mode where possible, not only gateway-owned mode.
- Change long `future.result()` waits in `run_agent` to bounded waits that check `cancel_event`.
- Pass cancellation into stream-oriented LLM helpers where practical.
- Guard final completion callback and assistant persistence after cancellation.
- Ensure stale completions after cancellation cannot write assistant success or cache successful answers.

### patent

Patent needs both the generic execution cancellation and durable-state cleanup.

- Finish the partial `cancel_event` propagation through router, `AskService`, executor, orchestrator, runtime, and synthesis.
- Replace indefinite queue waits with cancellation-aware bounded waits or sentinel wakeups.
- Ensure cancellation before `prepare_turn` completes still aborts the prepared state when it eventually returns.
- Ensure cancellation after `prepare_turn` but before finalize clears pending/inflight/lock.
- Require trace/ownership-token compare-and-swap for every durable cleanup and finalize operation.
- Ensure a late canceled worker cannot clear or overwrite a newer turn's pending/inflight/lock/result state.
- Ensure a late canceled worker cannot persist a successful terminal answer, cache successful stage output, or finalize quota as success after cancellation is observed.
- Prevent canceled stage payloads from being cached.

## Performance Constraints

Normal successful requests must not add:

- extra Redis reads on every SSE frame
- extra upstream HTTP calls
- per-token persistent storage writes
- high-frequency spin loops

Acceptable overhead:

- checking an in-memory `Event.is_set()` at stage boundaries
- bounded queue waits where the code is already waiting for a worker result
- wrapping upstream reads in an async race that sleeps 100-250ms only while idle and checks only in-memory cancellation state
- best-effort close/join work only on cancellation or stream cleanup

The implementation must include tests or measurements proving successful stream event order and completion behavior are unchanged.

Target cancellation observation latency for in-process waits is 250ms or less. Already-running third-party SDK/HTTP calls may run until their current read/connect timeout or until the client stream is closed; the required guarantee is that they do not start subsequent expensive work or commit successful terminal state after cancellation.

Acceptance bounds:

- Gateway `/tasks/{id}/cancel` response should return within the existing cancel API budget and must not wait for backend worker exit.
- Gateway task worker should observe the live cancel event within 250ms while idle-waiting for upstream bytes.
- Gateway task worker should observe cancel within 250ms while waiting to start/open upstream if the HTTP open await has not entered an uninterruptible client handshake. If already inside an uninterruptible handshake, the tolerated delay is the configured upstream open/connect/read timeout for that single attempt.
- Backend queue waits and router producer loops should observe local cancel events within 250ms.
- Best-effort generator/thread joins should wait no more than 500ms before logging and continuing cleanup.
- Blocking third-party SDK calls are allowed to exceed 250ms only for the current in-flight call; all post-call commit/cache/finalize paths must still reject success after cancellation.

## Data Flow

### Task Cancel Flow

1. Frontend calls `POST /api/v1/tasks/{task_id}/cancel`.
2. Gateway marks task canceled in queue/status store.
3. Gateway loads the live runtime entry and sets its cancel event if present, before any awaited cleanup or side effect.
4. Gateway flushes any available live progress and appends the canceled state frame.
5. Gateway aborts upstream handle if present.
6. Gateway terminalizes the assistant placeholder as canceled and finalizes quota as unsuccessful.
7. Worker observes terminal/cancel event, stops reading upstream, releases lease, and returns canceled outcome.
8. Backend request disconnects or receives cancel event and releases backend-local runtime state.

### Legacy Stream Disconnect Flow

1. Browser aborts the stream request. In legacy mode this is the explicit cancel mechanism.
2. Gateway streaming generator exits.
3. Gateway aborts upstream handle and finalizes quota as unsuccessful.
4. Backend router observes disconnect or generator close, sets local cancel event, and closes source iterator.
5. Backend execution stages stop cooperatively and avoid successful terminal commits.

## Error Handling

- Cancellation should map to canceled terminal status, not failed, when the request was explicitly canceled or disconnected.
- Cancellation cleanup errors should be logged and should not mask the public canceled task state.
- If a backend cannot be interrupted immediately, gateway still returns canceled to the frontend and prevents stale task events from changing the terminal state.
- If a backend later emits error/done after cancel, gateway must ignore it for that task because terminal state already exists.
- In legacy proxy mode, gateway may be gone by the time late backend events are produced. Therefore backend services must also guard late `done`, cache writes, assistant success persistence, and quota-success finalization after their local cancel event is set.
- For backend-owned persistence, canceled terminal persistence may be written once. A later success terminal from the same stale worker must be rejected or skipped.

## Testing Strategy

### Gateway Tests

- Cancel while upstream stream handle is registered but `body_iter()` is blocked before the next chunk.
- Cancel before upstream stream handle registration completes.
- Cancel races with upstream done; terminal state remains whichever won first.
- Legacy proxy disconnect aborts upstream handle and finalizes quota unsuccessful.
- Late upstream `done` or `error` after task cancellation is ignored and cannot change terminal state.
- Legacy proxy cancel via request abort has no `/tasks/{id}/cancel` dependency and leaves backend-owned terminal persistence responsible for canceled assistant state.
- Successful task stream remains unchanged.

### fastQA Tests

- Disconnect sets cancel event and generation stages observe it.
- Stage1 planning receives and checks cancellation before LLM call.
- Canceled stage results are not cached.
- Late stage result after cancel does not emit `done`, write cache success, or persist assistant success.
- Sync producer cleanup does not release limiter twice.

### highThinkingQA Tests

- Gateway-owned disconnect passes cancel event to executor and stops without done.
- Legacy disconnect also sets cancel event.
- `run_agent` bounded future waits react to cancel while direct/decompose futures are pending.
- Completion callback is skipped after cancellation.
- Late agent completion after cancel cannot persist assistant success.

### patent Tests

- Cancel while waiting for gateway-owned prepare releases durable pending state when prepare later returns.
- Cancel while waiting for progress queue releases pending/inflight/lock.
- Cancel during synthesis prevents successful finalize and does not cache canceled stage output.
- Late completion from trace A after trace B starts cannot clear trace B pending/inflight/lock or commit trace A as success.
- Immediate next turn after cancel no longer fails with stale `durable patent turn is already in flight`.

## Rollout Plan

1. Add gateway cancellation-aware worker behavior first.
2. Harden patent durable cleanup because it has the user-visible busy failure.
3. Fill fastQA and highThinkingQA stage-level cancellation gaps.
4. Run service-specific test suites.
5. Manually verify cancel/re-ask in one conversation for fast, thinking, and patent modes.

## Open Questions

- Should upstream HTTP client timeouts be reduced for streaming calls that have no first byte after cancel-capable proxying is added?
