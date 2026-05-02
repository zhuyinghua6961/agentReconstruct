# Cross-Service QA Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make canceling or disconnecting a QA turn stop useful backend work promptly across gateway task mode, gateway legacy proxy mode, fastQA, highThinkingQA, and patent, without adding measurable overhead to successful non-canceled requests.

**Architecture:** Use cooperative cancellation only. Gateway task mode owns explicit user cancel intent with an in-memory live cancel event, legacy proxy mode treats downstream request abort as cancel, and backend services translate disconnects into local cancel events that guard stage execution, cache writes, terminal persistence, and durable cleanup. Patent durable turn cleanup remains authoritative inside `ChatPersistenceService` and must be guarded by trace/owner ownership checks.

**Tech Stack:** Python, FastAPI/Starlette streaming responses, asyncio/anyio, threading events, synchronous generator bridges, Redis-backed task/durable state stores, pytest.

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-05-02-cross-service-qa-cancellation-design.md`
- Relevant existing partial patent edits: inspect current working tree before implementation; do not overwrite unrelated user changes.

## Global Constraints

- Do not force-kill Python threads.
- Do not add normal-path Redis/status polling, extra upstream HTTP calls, quota calls, or per-token persistent writes.
- Idle cancellation waits may check only in-memory events at 50-250ms intervals.
- Best-effort thread/generator joins must wait no more than 500ms.
- Already-running third-party SDK/HTTP calls may finish on their current timeout, but canceled workers must not start later expensive work or commit/cache/finalize success.
- Gateway legacy mode must not invent `/tasks/{id}/cancel`; frontend cancel remains `AbortController.abort()` on the active `ask_stream` request.
- Gateway must not directly mutate patent durable coordination keys.
- Patent durable cleanup must compare `(conversation_id, trace_id, owner token if present)` before clear/finalize/commit.

## File Map

### Gateway

- Modify: `gateway/app/services/qa_tasks.py`
  - Add live runtime cancellation event support with a cross-thread-safe `threading.Event`.
  - Set cancel event from `QATaskService.cancel_task()` even before upstream handle registration.
  - Race upstream open and upstream reads against the cancel event.
  - Ignore late upstream frames after terminal cancellation.
- Modify: `gateway/app/services/proxy.py`
  - Add or expose a small cancellation-aware read/open helper only if it belongs beside `StreamingProxyHandle`.
  - Keep `StreamingProxyHandle.abort()` idempotent.
- Modify: `gateway/app/routers/qa.py`
  - Ensure legacy proxy generator aborts upstream when downstream closes and quota finalizes unsuccessful.
- Test: `gateway/tests/test_task_api.py`
- Test: `gateway/tests/test_qa_proxy.py`

### Patent

- Modify: `patent/server_fastapi/routers/ask.py`
  - Ensure stream close/disconnect sets local cancel event before closing source iterator.
  - Keep gateway-owned and legacy paths using the same local cancellation primitive.
- Modify: `patent/server/services/ask_service.py`
  - Make prepare/progress queue waits cancellation-aware.
  - Abort prepared durable state if cancellation arrives before or during execution.
  - Prevent success finalize after cancel.
- Modify: `patent/server/services/chat_persistence.py`
  - Centralize ownership-guarded cleanup/finalization for durable pending/inflight/lock state.
- Modify: `patent/server/services/execution_cache.py`
  - Add trace-guarded helpers only if existing methods are not already atomic enough.
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/stages/synthesis.py`
- Modify: `patent/server/patent/answering.py`
  - Complete existing `should_cancel` propagation and guard stage/cache/commit paths.
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/test_execution_cache.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`
- Test: `patent/tests/test_patent_stage4_synthesis.py`

### fastQA

- Modify: `fastQA/app/routers/qa.py`
  - Preserve existing `cancel_event` route flow; ensure late `done` and persistence are skipped after cancel.
- Modify: `fastQA/app/core/sse.py`
  - Ensure disconnect callback runs once and producer cleanup does not double-release route limiters.
- Modify: `fastQA/app/modules/generation_pipeline/stage1_planning.py`
  - Accept and check `should_cancel` before and after the stage1 LLM call.
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
  - Pass `should_cancel` into stage1 implementation.
- Modify: `fastQA/app/modules/qa_kb/stages/planning.py`
  - Accept `should_cancel` and forward it to `runtime.stage1_pre_answer_and_planning`.
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
  - Guard cache writes for stage1/stage2/stage25/stage3 after cancellation.
  - Guard stream terminal `done` emission after cancellation.
- Test: `fastQA/tests/test_stream_contract.py`
- Test: `fastQA/tests/test_generation_stage1_planning.py`
- Test: `fastQA/tests/test_qa_generation_orchestrator.py`
- Test: `fastQA/tests/test_qa_cache.py`

### highThinkingQA

- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
  - Use a local cancel event in legacy streaming mode too, not only gateway-owned mode.
  - Set cancel before closing sync stream source and before producer join.
  - Skip assistant success persistence after cancel.
- Modify: `highThinkingQA/server/services/ask_service.py`
  - Keep bounded queue waits and ensure stream terminal `done` is skipped after cancel.
- Modify: `highThinkingQA/agent_core/graph.py`
  - Replace long unbounded `future.result()` waits and `_call_with_wall_clock_timeout()` waits with bounded loops that check `cancel_event`.
  - Check cancellation before starting subsequent expensive work.
- Modify: `highThinkingQA/agent_core/checker.py`
  - Replace blocking checker slice `future.result()` waits or prove they are always covered by a cancel-aware outer timeout loop.
- Modify: `highThinkingQA/agent_core/sub_answerer.py`
  - Replace blocking async bridge `future.result()` waits with cancel-aware waits or expose a cancel-aware wrapper used by `graph.py`.
- Test: `highThinkingQA/tests/test_ask_service_executor.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`
- Test: `highThinkingQA/tests/test_run_agent_overlap.py`

## Implementation Tasks

### Task 1: Gateway Live Cancel Event Foundation

**Files:**
- Modify: `gateway/app/services/qa_tasks.py`
- Test: `gateway/tests/test_task_api.py`

- [ ] **Step 1: Add failing test for cancel before upstream handle exists**

Add a test in `gateway/tests/test_task_api.py` that creates a live runtime entry with no `handle` and a `threading.Event`, calls `cancel_task()`, and asserts the event is set. The test should not require an upstream backend.

Expected behavior:
- `cancel_task()` returns the canceled task summary.
- The live runtime cancel event is set even when `_live_runtime_handle(entry)` is `None`.
- No exception is raised by `_abort_live_stream()`.

- [ ] **Step 2: Run the failing test**

Run:

```bash
cd gateway && pytest tests/test_task_api.py -k "cancel and live" -v
```

Expected: FAIL because live runtime entries do not yet expose or set a cancel event.

- [ ] **Step 3: Add live runtime cancel helpers**

In `gateway/app/services/qa_tasks.py`, add helpers close to `_live_runtime_handle()`:

```python
def _live_runtime_cancel_event(entry: Any) -> Any:
    if isinstance(entry, dict):
        return entry.get("cancel_event")
    return None

def _set_live_runtime_cancelled(entry: Any) -> None:
    cancel_event = _live_runtime_cancel_event(entry)
    if cancel_event is not None and hasattr(cancel_event, "set"):
        cancel_event.set()
```

Use a `threading.Event` inside `GatewayTaskExecutor._execute_async()` live runtime entries. This matters because `cancel_task()` can run in a different request thread/event loop than the task worker created by `anyio.run()`. Do not use `asyncio.Event` as the shared live cancellation primitive.

Initialize the `threading.Event` before upstream open starts. If the runtime cannot be fully populated before open without larger refactoring, register a pre-open live entry with:

```python
live_runtime = {
    "handle": None,
    "cancel_event": threading.Event(),
    "lock": threading.RLock(),
    ...
}
```

Then update only the `handle` and progress fields after open. Keep the same event object for the lifetime of the task.

- [ ] **Step 4: Set cancel event from cancel_task before abort**

In `QATaskService.cancel_task()`, after reading `live_runtime` and before `_abort_live_stream(task_id)`, call `_set_live_runtime_cancelled(live_runtime)`.

Set the event immediately after `_get_live_runtime(task_id)` returns and before any awaited cleanup, including `_flush_live_progress()`. In the current code there is an awaited progress flush before upstream abort; cancellation intent must be visible to the worker before that await so an in-process idle wait can observe cancel within 250ms. Do not add Redis reads here beyond the existing task record checks.

- [ ] **Step 5: Run task API tests**

Run:

```bash
cd gateway && pytest tests/test_task_api.py -k "cancel" -v
```

Expected: PASS.

- [ ] **Step 6: Commit gateway cancel foundation**

Commit only gateway files touched by this task:

```bash
git add gateway/app/services/qa_tasks.py gateway/tests/test_task_api.py
git commit -m "fix: add gateway task live cancel signal"
```

### Task 2: Gateway Cancellation-Aware Upstream Open and Read

**Files:**
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/proxy.py`
- Test: `gateway/tests/test_task_api.py`

- [ ] **Step 1: Add failing test for cancel while upstream open is pending**

In `gateway/tests/test_task_api.py`, fake `proxy_service.open_json_stream()` so it blocks on an async event. Start `_execute_async()` in a background task, call `cancel_task()`, unblock nothing, and assert the worker returns canceled within 250ms after the cancel event is set.

Expected behavior:
- Cancel API returns immediately.
- Worker does not wait for `open_json_stream()` to complete if the await is still cancellable.
- If a canceled open task later produces a handle, that late handle is aborted by a done callback.
- `GatewayTaskCancelled` raised during open is mapped to the existing canceled terminal outcome, not `_terminalize_failure()`.
- No upstream frames are appended after cancel.

- [ ] **Step 2: Add failing test for cancel while body_iter is blocked**

Use a fake `StreamingProxyHandle` whose `body_iter()` yields one running frame, then blocks before the next chunk. Cancel the task and assert:
- handle `abort()` is called once.
- worker returns canceled within 250ms.
- a late `done` frame released after cancellation does not change terminal state to completed.

- [ ] **Step 3: Run failing tests**

Run:

```bash
cd gateway && pytest tests/test_task_api.py -k "cancel and (open or body_iter or late_done)" -v
```

Expected: FAIL on the new timing/late-frame assertions.

- [ ] **Step 4: Implement cancellable open helper**

In `GatewayTaskExecutor`, add an async helper shaped like:

```python
async def _await_with_cancel(self, awaitable, *, cancel_event: threading.Event, request_id: str, label: str):
    task = asyncio.create_task(awaitable)
    while not task.done():
        if cancel_event.is_set():
            task.add_done_callback(
                lambda done_task: self._cleanup_cancelled_open_result(done_task, request_id=request_id)
            )
            task.cancel()
            raise GatewayTaskCancelled(label)
        await asyncio.sleep(0.05)
    return await task
```

Use an internal exception/class or sentinel local to `qa_tasks.py`; do not expose it through public API.

Catch `GatewayTaskCancelled` around the existing `open_json_stream()` await before the generic `except Exception` branch. The current code terminalizes all open exceptions as failure; cancel-during-open must instead return the already-canceled terminal outcome, for example:

```python
try:
    handle = await self._await_with_cancel(
        self.proxy_service.open_json_stream(...),
        cancel_event=cancel_event,
        request_id=request_id,
        label="open",
    )
except GatewayTaskCancelled:
    terminalized = self._terminalized_execution_outcome(request_id)
    return terminalized or AdmissionExecutionOutcome(outcome="completed", terminal_status="cancelled")
except Exception as exc:
    ...
```

If HTTP client handshake cannot be interrupted once entered, keep existing configured timeout as the upper bound and document that in the helper comment and test name. Do not `await` a canceled open task indefinitely in the cancel path. If it completes later with a `StreamingProxyHandle`, the done callback must abort that handle and close client/upstream resources.

Also harden `ProxyService.open_json_stream()` itself. It currently creates an `httpx.AsyncClient` before awaiting `client.send(...)`; if the coroutine is canceled or raises before returning a `StreamingProxyHandle`, the client must be closed in `except BaseException`/`finally` before re-raising. This cleanup is required even when task-level open racing returns promptly, otherwise cancel-before-open can leak an upstream client/socket.

- [ ] **Step 5: Implement cancellable read helper**

Avoid `async for chunk in handle.body_iter()` in task mode. Convert to explicit `anext()` calls checked against the cross-thread `threading.Event`:

```python
iterator = handle.body_iter().__aiter__()
while True:
    try:
        chunk = await self._next_chunk_with_cancel(iterator, cancel_event=cancel_event, request_id=request_id)
    except StopAsyncIteration:
        break
    except GatewayTaskCancelled:
        await handle.abort()
        terminalized = self._terminalized_execution_outcome(request_id)
        return terminalized or AdmissionExecutionOutcome(outcome="completed", terminal_status="cancelled")
    ...
```

The read helper should create a task for `anext(iterator)` and check `cancel_event.is_set()` every 50ms while the read is pending. If canceled, cancel the pending read task, abort the handle, and return the canceled outcome. The helper must not call `queue_store.get_request()`, `relay_store.*`, quota APIs, or Redis lock APIs on each idle tick. It may wait on the in-memory event and the upstream read task.

Important spelling constraint: gateway admission internals currently accept terminal statuses `completed`, `failed`, `cancelled`, and `expired`. Public task APIs normalize `cancelled` to `canceled`. Do not return `terminal_status="canceled"` from `AdmissionExecutionOutcome` unless admission normalization is intentionally updated and tested.

- [ ] **Step 6: Guard terminal success after cancel**

Before handling upstream `done`, before `terminal_task_assistant(... terminal_status="completed")`, and before quota success finalization, check:
- live cancel event
- `_terminalized_execution_outcome(request_id)`

If canceled won, return the canceled outcome and do not finalize quota as success.

- [ ] **Step 7: Run gateway task tests**

Run:

```bash
cd gateway && pytest tests/test_task_api.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit gateway read/open cancellation**

```bash
git add gateway/app/services/qa_tasks.py gateway/app/services/proxy.py gateway/tests/test_task_api.py
git commit -m "fix: stop gateway task workers on cancellation"
```

### Task 3: Gateway Legacy Proxy Disconnect Semantics

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/app/services/proxy.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [ ] **Step 1: Add failing legacy disconnect test**

In `gateway/tests/test_qa_proxy.py`, simulate a streaming response where downstream iteration is closed before upstream `done`. Assert:
- `StreamingProxyHandle.abort()` is called.
- quota finalization is called with `success=False`.
- no task cancel endpoint is involved.

- [ ] **Step 2: Run failing test**

Run:

```bash
cd gateway && pytest tests/test_qa_proxy.py -k "disconnect or abort" -v
```

Expected: FAIL if upstream abort is not guaranteed on generator close.

- [ ] **Step 3: Harden legacy generator finally block**

In `gateway/app/routers/qa.py`, ensure every legacy streaming path around `handle.body_iter()` has a `finally` block that:
- marks the stream unsuccessful unless a valid upstream `done` was observed.
- awaits `handle.abort()` or closes upstream/client idempotently.
- finalizes quota with `success=False` for disconnect/cancel.

Do not persist backend-owned assistant terminal state here; backend legacy services own canceled terminal persistence.

- [ ] **Step 4: Run proxy tests**

Run:

```bash
cd gateway && pytest tests/test_qa_proxy.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit legacy proxy cancellation**

```bash
git add gateway/app/routers/qa.py gateway/app/services/proxy.py gateway/tests/test_qa_proxy.py
git commit -m "fix: abort legacy qa proxy streams on disconnect"
```

### Task 4: Patent Durable Cleanup and Queue Cancellation

**Files:**
- Modify: `patent/server_fastapi/routers/ask.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/execution_cache.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/test_execution_cache.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Preserve current partial patent edits**

Before editing, inspect current diffs:

```bash
git diff -- patent/server_fastapi/routers/ask.py patent/server/services/ask_service.py patent/server/services/chat_persistence.py patent/server/services/execution_cache.py
```

Expected: understand existing partial cancel propagation; do not revert it.

- [ ] **Step 2: Add failing ownership tests for durable cleanup**

In `patent/tests/test_chat_persistence.py` and/or `patent/tests/test_execution_cache.py`, add tests for:
- trace A abort cannot clear pending/inflight for trace B.
- trace A finalize failure cannot release trace B lock.
- trace A late success cannot accept assistant terminal success after trace A was canceled.
- trace A late success cannot call `_accept_assistant_turn`, `_accept_assistant_terminal_turn`, `set_turn_result`, or `set_overlay_assistant` after ownership no longer matches.
- inflight renewal succeeds when the marker stores the new owner token instead of legacy `"1"`.
- stale or mismatched owner token cannot renew a trace's inflight marker.

Use existing fake Redis/cache helpers. Assert current trace remains present after stale cleanup attempts.

- [ ] **Step 3: Add failing cancel/re-ask contract test**

In `patent/tests/fastapi_contract/test_ask_contract.py`, add a test that:
- starts a durable stream.
- cancels or closes it while prepare/progress is waiting.
- immediately starts a new durable turn in the same conversation.
- asserts the second turn is not rejected with `durable patent turn is already in flight`.

- [ ] **Step 4: Add failing queue wait latency tests**

In `patent/tests/fastapi_contract/test_ask_contract.py` or `patent/tests/test_chat_persistence.py`, add explicit tests for:
- cancellation while `prepare_queue.get()` is idle wakes the stream within 250ms.
- cancellation while `progress_queue.get()` is idle wakes the stream within 250ms.
- prepare and execution worker cleanup/join waits are capped at 500ms; if the worker remains alive, cleanup logs and continues.

Use fake queues/workers where possible so the tests are deterministic and do not wait for real LLM/network calls.

- [ ] **Step 5: Run failing patent durable tests**

Run:

```bash
cd patent && pytest tests/test_chat_persistence.py tests/test_execution_cache.py tests/fastapi_contract/test_ask_contract.py -k "cancel or pending or inflight or durable" -v
```

Expected: FAIL on stale cleanup or blocked next turn.

- [ ] **Step 6: Centralize guarded cleanup in ChatPersistenceService**

In `patent/server/services/chat_persistence.py`, make `abort_turn()`, `finalize_turn()`, terminal accept, pending clear, inflight clear, result commit, and lock release use the same ownership check:

```python
runtime_state = prepared_turn.get("_state") if isinstance(prepared_turn.get("_state"), dict) else {}
lock_handle = runtime_state.get("lock_handle")
expected = {
    "conversation_id": runtime_state["conversation_id"],
    "trace_id": runtime_state["trace_id"],
    "lock_token": getattr(lock_handle, "token", None),
}
```

Current repository shape:
- durable conversation/trace ownership lives in `prepared_turn["_state"]`, not top-level `prepared_turn["conversation_id"]`.
- distributed lock ownership lives in the `LockHandle` stored under `_state["lock_handle"]`.
- `ExecutionCache.clear_turn_identity()` and `clear_turn_inflight()` currently delete trace-keyed markers without checking a stored owner value.
- `ExecutionCache.set_turn_result()` currently writes `turn-result:{conversation_id}:{trace_id}` without verifying that the same turn still owns the durable slot.

Implementation must make these operations repository-real and ownership guarded:
- use a concrete marker schema for identity/inflight ownership, for example JSON `{"trace_id": trace_id, "owner": lock_handle.token}` or a stable text token equal to `lock_handle.token`; choose one schema and use it consistently in claim, renew, compare-clear, and guarded commit helpers.
- update `claim_turn_identity(..., owner_token=...)` and `mark_turn_inflight(..., owner_token=...)` or add parallel owner-aware methods; do not silently keep writing `"1"` for newly claimed durable turns.
- add compare-and-clear helpers for identity and inflight markers; stale trace/owner cleanup must return false/no-op.
- update `ExecutionCache.renew_turn_inflight()` to compare against the same stored owner token, and update `ChatPersistenceService` renewal loop call sites to pass `lock_handle.token`.
- preserve compatibility for any legacy `"1"` markers if existing tests or rollout require old live keys to renew/clear during deployment.
- add a guarded turn-result commit helper that checks the current pending/inflight/identity owner before writing success.
- add a guarded overlay assistant helper or ownership check immediately before `set_overlay_assistant()`.
- verify `_state.conversation_id`, `_state.trace_id`, and `lock_handle.token` still match live durable ownership immediately before `_accept_assistant_turn`, `_accept_assistant_terminal_turn`, `set_turn_result`, and `set_overlay_assistant`.
- make `_cleanup_runtime_state()`, `abort_turn()`, `finalize_turn()`, and terminal accept share the same guarded helpers instead of unconditional deletes.

Each cleanup operation must no-op or report controlled failure when stored state does not match `expected`. Do not do unconditional `delete`/`clear` on conversation-level or trace-level durable keys when a stored owner token is available.

- [ ] **Step 7: Make prepare/progress waits cancellation-aware**

In `patent/server/services/ask_service.py`:
- replace indefinite `queue.get()` calls with `get(timeout=0.05..0.2)`.
- on `queue.Empty`, check `cancel_event.is_set()`.
- if canceled before `prepare_turn()` returns, remember cancellation and abort the prepared turn immediately when the worker eventually returns it.
- if canceled after prepare, call `ChatPersistenceService.abort_turn(prepared_turn)` once.
- cap prepare and execution worker join/cleanup waits at 500ms; log and continue if a worker remains alive.

Cancellation should produce terminal status `canceled`, not `failed`.

- [ ] **Step 8: Router cleanup ordering**

In `patent/server_fastapi/routers/ask.py`, ensure generator close/disconnect does:
1. set `stream_cancel_event`.
2. best-effort close source iterator.
3. rely on `anyio.to_thread.run_sync(... abandon_on_cancel=True)` cleanup semantics for the router-level generator; the router currently has no explicit producer thread to join.
4. log close failures without blocking response cleanup.

Put the 500ms best-effort worker/thread join requirement in `patent/server/services/ask_service.py`, where prepare/execution worker threads and queues are owned.

- [ ] **Step 9: Run durable patent tests**

Run:

```bash
cd patent && pytest tests/test_chat_persistence.py tests/test_execution_cache.py tests/fastapi_contract/test_ask_contract.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit patent durable cancellation**

```bash
git add patent/server_fastapi/routers/ask.py patent/server/services/ask_service.py patent/server/services/chat_persistence.py patent/server/services/execution_cache.py patent/tests/test_chat_persistence.py patent/tests/test_execution_cache.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "fix: release patent durable turns on cancel"
```

### Task 5: Patent Execution Stage Cancellation

**Files:**
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/stages/synthesis.py`
- Modify: `patent/server/patent/answering.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`
- Test: `patent/tests/test_patent_stage4_synthesis.py`
- Test: `patent/tests/test_patent_kb_service.py`

- [ ] **Step 1: Add failing tests for stage cancellation**

Add tests asserting:
- stage1 planning checks `should_cancel` before starting LLM.
- stage2/stage3 return canceled or stop before fanout/PDF loading when `should_cancel()` is true.
- stage4 streaming stops when `should_cancel()` flips during chunk emission.
- canceled stage outputs are not cached as success.

- [ ] **Step 2: Run failing tests**

Run:

```bash
cd patent && pytest tests/test_patent_generation_orchestrator.py tests/test_patent_stage4_synthesis.py tests/test_patent_kb_service.py -k "cancel" -v
```

Expected: FAIL where cancellation is not yet propagated or cache writes are unguarded.

- [ ] **Step 3: Complete `should_cancel` propagation**

Thread `should_cancel` through runtime, executor, service, orchestrator, and synthesis using existing function signatures where partial edits already exist.

Add checks:
- before LLM planning/decomposition.
- before retrieval fanout.
- before evidence/PDF loading.
- before final synthesis LLM call.
- inside stream chunk loops.
- after expensive calls return and before cache/write/finalize.

- [ ] **Step 4: Normalize canceled result payloads**

Use one consistent internal canceled marker for patent stages, for example:

```python
{"success": False, "canceled": True, "error": "cancelled"}
```

Then make orchestrator/service code treat this as terminal canceled, not retriable failed, and skip success cache writes.

- [ ] **Step 5: Run patent generation tests**

Run:

```bash
cd patent && pytest tests/test_patent_generation_orchestrator.py tests/test_patent_stage4_synthesis.py tests/test_patent_kb_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit patent stage cancellation**

```bash
git add patent/server/patent/runtime.py patent/server/patent/executor.py patent/server/patent/kb_service.py patent/server/patent/orchestrators/generation.py patent/server/patent/stages/synthesis.py patent/server/patent/answering.py patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_stage4_synthesis.py patent/tests/test_patent_kb_service.py
git commit -m "fix: stop patent generation stages after cancel"
```

### Task 6: fastQA Cancellation Gaps

**Files:**
- Modify: `fastQA/app/routers/qa.py`
  - Preserve existing `cancel_event` route flow; ensure graph direct-answer attempts, generation fallthrough, late `done`, and persistence are skipped after cancel.
- Modify: `fastQA/app/core/sse.py`
- Modify: `fastQA/app/modules/generation_pipeline/stage1_planning.py`
- Modify: `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- Modify: `fastQA/app/modules/qa_kb/stages/planning.py`
- Modify: `fastQA/app/modules/qa_kb/orchestrators/generation.py`
- Test: `fastQA/tests/test_stream_contract.py`
- Test: `fastQA/tests/test_generation_stage1_planning.py`
- Test: `fastQA/tests/test_qa_generation_orchestrator.py`
- Test: `fastQA/tests/test_qa_cache.py`

- [ ] **Step 1: Add failing fastQA stream cancel test**

In `fastQA/tests/test_stream_contract.py`, simulate `ask_stream` disconnect through the SSE iterator and assert:
- the route cancel event is set.
- no final `done` is emitted after cancel.
- limiter release happens once.

- [ ] **Step 2: Add failing stage1 cancel test**

In `fastQA/tests/test_generation_stage1_planning.py`, pass `should_cancel=lambda: True` to stage1 and assert the fake LLM client is not called.

Also add a second test where cancellation flips after LLM returns and assert the result is marked canceled and is not cacheable.

- [ ] **Step 3: Add failing cache guard test**

In `fastQA/tests/test_qa_generation_orchestrator.py` or `fastQA/tests/test_qa_cache.py`, assert stage cache write functions are not called when `should_cancel()` is true after a stage computes a result.

- [ ] **Step 4: Add failing graph direct-answer cancel test**

In `fastQA/tests/test_stream_contract.py` or `fastQA/tests/test_qa_routes_file_modes.py`, exercise the route-level graph path in `fastQA/app/routers/qa.py` and assert:
- if `should_cancel()` becomes true after `route_graph_kb_v2(...)`, no `_iter_graph_kb_events(...)` direct-answer stream is emitted.
- if `should_cancel()` becomes true after `try_graph_kb_answer(...)`, the route does not emit direct-answer events.
- if graph routing returns no direct answer and cancellation is set, the route does not fall through into generation/stage1.

This covers the spec requirement to check cancellation after graph direct-answer attempts and before falling through to generation.

- [ ] **Step 5: Run failing fastQA tests**

Run:

```bash
cd fastQA && pytest tests/test_stream_contract.py tests/test_generation_stage1_planning.py tests/test_qa_generation_orchestrator.py tests/test_qa_cache.py -k "cancel or cancelled" -v
```

Expected: FAIL on stage1 propagation/cache guard gaps.

- [ ] **Step 6: Pass `should_cancel` into stage1**

Update:
- `fastQA/app/modules/generation_pipeline/stage1_planning.py`
- `fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py`
- `fastQA/app/modules/qa_kb/stages/planning.py`

`Stage1Planner.run()` currently does not accept `should_cancel`; add it explicitly and forward it when `runtime.stage1_pre_answer_and_planning` supports the kwarg. Stage1 must check cancellation before building/starting LLM work and after response parsing before returning success.

- [ ] **Step 7: Guard graph direct-answer and generation fallthrough**

In `fastQA/app/routers/qa.py`, add `should_cancel()` checks:
- immediately after `route_graph_kb_v2(...)` returns and before direct-answer `_iter_graph_kb_events(...)`.
- immediately after `try_graph_kb_answer(...)` returns and before direct-answer `_iter_graph_kb_events(...)`.
- before falling through from graph routing to generation/stage1.

If canceled, stop the iterator without emitting `done` or direct-answer content, and do not start later expensive generation work.

- [ ] **Step 8: Guard orchestrator cache and terminal success**

In `fastQA/app/modules/qa_kb/orchestrators/generation.py`:
- check `should_cancel()` before each `_run_stage*`.
- check after each stage returns before `cache_stage*_result()`.
- in streaming flow, skip `done` emission if canceled.

Do not add persistent status reads to these checks.

- [ ] **Step 9: Harden SSE cleanup only if needed**

In `fastQA/app/core/sse.py`, make disconnect cleanup idempotent. If the existing `on_disconnect` path can run more than once, guard callback execution with a local boolean/lock.

- [ ] **Step 10: Run fastQA focused tests**

Run:

```bash
cd fastQA && pytest tests/test_stream_contract.py tests/test_generation_stage1_planning.py tests/test_qa_generation_orchestrator.py tests/test_qa_cache.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit fastQA cancellation**

```bash
git add fastQA/app/routers/qa.py fastQA/app/core/sse.py fastQA/app/modules/generation_pipeline/stage1_planning.py fastQA/app/modules/generation_pipeline/generation_driven_rag_facade.py fastQA/app/modules/qa_kb/stages/planning.py fastQA/app/modules/qa_kb/orchestrators/generation.py fastQA/tests/test_stream_contract.py fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_qa_generation_orchestrator.py fastQA/tests/test_qa_cache.py
git commit -m "fix: honor fastqa stream cancellation"
```

### Task 7: highThinkingQA Cancellation Gaps

**Files:**
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Modify: `highThinkingQA/server/services/ask_service.py`
- Modify: `highThinkingQA/agent_core/graph.py`
- Modify: `highThinkingQA/agent_core/checker.py`
- Modify: `highThinkingQA/agent_core/sub_answerer.py`
- Test: `highThinkingQA/tests/test_ask_service_executor.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`
- Test: `highThinkingQA/tests/test_run_agent_overlap.py`

- [ ] **Step 1: Add failing legacy disconnect test**

In `highThinkingQA/tests/test_ask_router_summary_persistence.py`, exercise the legacy stream path without gateway-owned persistence and close the stream early. Assert:
- local cancel event reaches `stream_ask_events()`.
- assistant success persistence is not called.
- canceled terminal persistence is written once when this backend owns persistence.

- [ ] **Step 2: Add failing bounded future wait test**

In `highThinkingQA/tests/test_run_agent_overlap.py` or `highThinkingQA/tests/test_ask_service_executor.py`, fake futures that never complete and set `cancel_event`. Cover:
- direct/decompose/retrieval future waits in `graph.py`.
- `_call_with_wall_clock_timeout()` checker/reviser calls, which currently may wait up to 60s.
- checker slice futures in `checker.py`.
- async pre-answer bridge in `sub_answerer.py`.

Assert each in-process idle wait exits within 250ms from cancellation.

- [ ] **Step 3: Run failing highThinking tests**

Run:

```bash
cd highThinkingQA && pytest tests/test_ask_service_executor.py tests/test_ask_router_summary_persistence.py tests/test_run_agent_overlap.py -k "cancel or disconnect" -v
```

Expected: FAIL where legacy mode lacks cancel event or `future.result()` blocks.

- [ ] **Step 4: Use cancel event in legacy router path**

In `highThinkingQA/server_fastapi/routers/ask.py`, create a `threading.Event()` for both gateway-owned and legacy streaming paths. Keep gateway-owned persistence behavior unchanged; only the cancellation signal should be shared.

On downstream disconnect:
- set cancel event.
- set producer stop event.
- close source iterator best-effort.
- join/cleanup within 500ms.

Current router code checks disconnect only under `if gateway_task_mode and await request.is_disconnected():`. Remove that `gateway_task_mode and` gate or equivalent so legacy streams also observe browser aborts promptly. Gateway-owned persistence behavior stays gated; disconnect detection and cancel-event setting do not.

- [ ] **Step 5: Replace unbounded future waits**

In `highThinkingQA/agent_core/graph.py`, replace direct `future.result()` waits that can block during direct/decompose/retrieval batches and `_call_with_wall_clock_timeout()` waits used for checker/reviser with a helper:

```python
def _result_with_cancel(future, *, cancel_event, poll_seconds=0.05):
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("cancelled")
        try:
            return future.result(timeout=poll_seconds)
        except concurrent.futures.TimeoutError:
            continue
```

Use the repo's existing cancel exception/result convention if one already exists; otherwise add a small local cancellation exception such as `AgentCancelledError` in `graph.py` and map it to the existing canceled error payload used by `ask_service.py` and the router. Do not leave the snippet with an undefined `CancelledError`.

Also update `checker.py` and `sub_answerer.py` blocking `future.result()` paths to accept a cancellation predicate or route them through a bounded helper. These files currently use `with concurrent.futures.ThreadPoolExecutor(...)`; do not raise cancellation from inside a context manager that will call `shutdown(wait=True)` on exit. Use explicit executor lifecycle on cancel, with `shutdown(wait=False, cancel_futures=True)` where safe, so the caller can observe cancel within 250ms even if the in-flight SDK/async bridge keeps running until its own timeout.

If a path cannot accept cancellation without a larger API change, the plan implementer must add a test proving it is not reachable after `cancel_event` is set.

- [ ] **Step 6: Guard persistence after cancel**

In router/service completion callbacks, check the cancel event immediately before:
- `_persist_summary_once()`
- `_persist_terminal_once(terminal_status="done")`
- any cache/success completion callback

Canceled terminal persistence may happen once for backend-owned legacy mode.

- [ ] **Step 7: Run highThinking focused tests**

Run:

```bash
cd highThinkingQA && pytest tests/test_ask_service_executor.py tests/test_ask_router_summary_persistence.py tests/test_run_agent_overlap.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit highThinking cancellation**

```bash
git add highThinkingQA/server_fastapi/routers/ask.py highThinkingQA/server/services/ask_service.py highThinkingQA/agent_core/graph.py highThinkingQA/agent_core/checker.py highThinkingQA/agent_core/sub_answerer.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_run_agent_overlap.py
git commit -m "fix: honor thinking stream cancellation"
```

### Task 8: Cross-Service Verification and Manual QA

**Files:**
- No runtime files unless previous tasks expose missing coverage.
- Optional docs update: this plan or a short verification note under `docs/superpowers/implementation/`.

- [ ] **Step 1: Run gateway focused verification**

Run:

```bash
cd gateway && pytest tests/test_task_api.py tests/test_qa_proxy.py -v
```

Expected: PASS.

- [ ] **Step 2: Run patent focused verification**

Run:

```bash
cd patent && pytest tests/test_chat_persistence.py tests/test_execution_cache.py tests/fastapi_contract/test_ask_contract.py tests/test_patent_generation_orchestrator.py tests/test_patent_stage4_synthesis.py tests/test_patent_kb_service.py -v
```

Expected: PASS.

- [ ] **Step 3: Run fastQA focused verification**

Run:

```bash
cd fastQA && pytest tests/test_stream_contract.py tests/test_generation_stage1_planning.py tests/test_qa_generation_orchestrator.py tests/test_qa_cache.py -v
```

Expected: PASS.

- [ ] **Step 4: Run highThinking focused verification**

Run:

```bash
cd highThinkingQA && pytest tests/test_ask_service_executor.py tests/test_ask_router_summary_persistence.py tests/test_run_agent_overlap.py -v
```

Expected: PASS.

- [ ] **Step 5: Run frontend build only if frontend cancel behavior changed**

No frontend changes are expected. If implementation discovers legacy cancel does not already abort `ask_stream` with `AbortController`, update `frontend-vue/` and run:

```bash
cd frontend-vue && npm run build
```

Expected: PASS.

- [ ] **Step 6: Manual cancel/re-ask checks**

Start the stack:

```bash
bash scripts/start_all.sh
```

Verify in one browser conversation:
- fastQA: ask, cancel while generating, ask again immediately.
- highThinkingQA: ask, cancel while thinking, ask again immediately.
- patent: ask durable patent QA, cancel before answer, ask again immediately.

Expected:
- no `durable patent turn is already in flight` after cancel.
- UI shows canceled/idle state and accepts next turn.
- backend logs show cancellation cleanup and no late successful assistant terminal from canceled trace.

- [ ] **Step 7: Final git status**

Run:

```bash
git status --short
```

Expected: only intended files are modified or committed. Do not commit unrelated files such as local docs, spreadsheets, or pre-existing user changes unless explicitly requested.

## Review Checklist

- [ ] Gateway task cancel sets live in-memory event before aborting upstream handle.
- [ ] Gateway task open/read waits are cancel-aware without per-tick Redis/status polling.
- [ ] Gateway legacy disconnect aborts upstream and finalizes quota unsuccessful.
- [ ] Backend legacy cancel contract remains request abort, not task cancel endpoint.
- [ ] Patent cleanup is centralized in `ChatPersistenceService`.
- [ ] Patent cleanup/finalize paths are trace/owner guarded.
- [ ] fastQA stage1 accepts and checks `should_cancel`.
- [ ] fastQA/highThinking/patent skip success cache writes and assistant success persistence after cancel.
- [ ] Bounded waits observe cancellation within 250ms where they are in-process waits.
- [ ] Best-effort thread/generator joins are capped at 500ms.
- [ ] Successful non-canceled streams keep existing event order and terminal behavior.
