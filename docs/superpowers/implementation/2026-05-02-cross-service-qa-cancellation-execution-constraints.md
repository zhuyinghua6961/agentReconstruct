# Cross-Service QA Cancellation Execution Constraints

**Applies to:**
- Spec: `docs/superpowers/specs/2026-05-02-cross-service-qa-cancellation-design.md`
- Implementation plan: `docs/superpowers/plans/2026-05-02-cross-service-qa-cancellation-implementation.md`

**Status:** Execution gate. Read this before implementing. Do not start code changes until the user explicitly says to proceed.

## User Execution Constraints

- Do not commit during implementation.
- Ignore the per-task commit steps in the implementation plan for this execution pass. They remain useful as logical checkpoints only.
- If a test is blocked by sandbox restrictions, request escalation and run it once with elevated permissions. Do not repeatedly retry the same blocked command in the sandbox.
- After all implementation and verification work is complete, open one subagent for code review.
- Apply review findings locally.
- Reuse the same code-review subagent for re-review until it returns pass/approved.
- Do not open extra review subagents unless the user explicitly asks.
- Do not begin implementation from this document-writing step.

## Hard Design Constraints

- Use cooperative cancellation only; do not force-kill Python threads.
- Normal successful requests must not add Redis/status polling, extra upstream HTTP calls, quota calls, per-token persistent writes, or high-frequency spin loops.
- In-memory cancellation checks are acceptable at stage boundaries and while waiting on already-blocking operations.
- In-process idle waits must observe cancellation within 250ms.
- Best-effort generator/thread joins must wait no more than 500ms before logging and continuing cleanup.
- Blocking third-party SDK/HTTP calls may run until their current timeout, but canceled workers must not start later expensive work or commit/cache/finalize success.
- Legacy `ask_stream` cancellation is request abort via `AbortController`; do not add or depend on `/tasks/{id}/cancel` for legacy mode.
- Gateway must not directly mutate patent durable coordination keys.

## Gateway Constraints

- Gateway task cancellation must set the live runtime cancel event immediately after loading the live runtime entry and before any awaited progress flush, state-frame append, terminal side effect, quota side effect, or upstream abort.
- The live task cancel event must be cross-thread-safe, using `threading.Event`, not `asyncio.Event`.
- Gateway task open/read waits must be cancellation-aware without per-idle-tick Redis/status-store reads.
- `GatewayTaskCancelled` during upstream open/read must map to the existing canceled terminal outcome, not `_terminalize_failure()`.
- Gateway admission internals use `terminal_status="cancelled"`; public APIs normalize to `canceled`.
- `ProxyService.open_json_stream()` must close its `httpx.AsyncClient` on cancellation or any pre-handle exception.
- Late upstream frames after task cancellation must be ignored.
- Legacy proxy disconnect must abort the upstream handle and finalize quota unsuccessful; backend services own legacy assistant terminal persistence.

## Patent Constraints

- `ChatPersistenceService` is authoritative for durable patent cleanup.
- Patent cleanup and success commit must be guarded by `(conversation_id, trace_id, owner token if present)`.
- Ownership state comes from `prepared_turn["_state"]`; lock ownership comes from `LockHandle.token`.
- Guard cleanup, pending clear, inflight clear, lock release, result commit, overlay assistant write, `_accept_assistant_turn`, and `_accept_assistant_terminal_turn`.
- A stale trace must not clear, overwrite, terminalize, cache, or persist success for a newer trace.
- Define and use one concrete owner marker schema for identity/inflight markers.
- Owner-token claim, renew, compare-clear, guarded result commit, and call sites must be updated consistently.
- Preserve legacy `"1"` marker compatibility where needed for live rollout.
- `prepare_queue.get()` and `progress_queue.get()` must become cancellation-aware bounded waits or use sentinel wakeups.
- Prepare/progress queue cancellation latency tests must assert <=250ms observation.
- Prepare/execution worker cleanup tests must assert <=500ms join/cleanup cap.

## fastQA Constraints

- Preserve existing route-level `cancel_event` flow.
- Pass `should_cancel` into stage1 planning through `Stage1Planner`.
- Check cancellation before and after stage1 LLM work.
- Check cancellation after graph direct-answer attempts and before falling through to generation.
- Add route-level checks after `route_graph_kb_v2(...)`, after `try_graph_kb_answer(...)`, and before `_iter_graph_kb_events(...)` or generation fallthrough.
- Do not cache canceled stage results as successful cache entries.
- Do not emit final `done` after cancellation.
- Ensure disconnect cleanup is idempotent if the existing SSE path can call it more than once.

## highThinkingQA Constraints

- Use a local cancel event in legacy streaming mode as well as gateway-owned mode.
- Remove the existing `gateway_task_mode and` disconnect gate or equivalent so legacy browser aborts are observed promptly.
- Keep gateway-owned persistence semantics gated; only disconnect detection and cancel-event propagation become shared.
- Replace long blocking `future.result()` waits and `_call_with_wall_clock_timeout()` waits with bounded cancel-aware waits.
- `checker.py` and `sub_answerer.py` must not raise cancellation through a `with ThreadPoolExecutor(...)` context that blocks on `shutdown(wait=True)`.
- Use explicit executor lifecycle with `shutdown(wait=False, cancel_futures=True)` where safe.
- Skip assistant success persistence and success callbacks after cancellation.

## Verification Constraints

- Follow the implementation plan tests, but do not commit after individual tasks.
- Run focused tests for gateway, patent, fastQA, and highThinkingQA.
- If a verification command fails because of sandbox restrictions, request elevated permissions and rerun that command with escalation instead of retrying in the sandbox.
- Successful non-canceled stream event order and terminal behavior must remain unchanged.
- Manual cancel/re-ask verification must cover fastQA, highThinkingQA, and patent.
- The patent manual check must confirm no stale `durable patent turn is already in flight` after cancel and immediate re-ask.

## Review Gate

- After all implementation and verification work is complete, open one code-review subagent.
- Provide the reviewer with the spec, implementation plan, this constraints document, and the final diff.
- Fix all critical and important findings.
- Reuse the same subagent for re-review until approved/pass.
- Only after review pass should final status be reported as complete.
