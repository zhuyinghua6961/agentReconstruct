# Patent Phase 1 Verification And Handoff

## Status

- Date: 2026-03-26
- Scope: `patent/` only
- Phase: Task 10 and Task 11 closeout
- Current service state: patent Phase 1 scaffold is locally verified and review-approved inside `patent/`

## Verification Summary

### Commands run

1. Focused patent suite:

```bash
conda run -n agent bash patent/scripts/test.sh tests -q
```

Result: `124 passed in 2.58s`

2. Durable rollout safety subset:

```bash
conda run -n agent bash patent/scripts/test.sh \
  tests/fastapi_contract/test_ask_contract.py \
  tests/fastapi_contract/test_health_contract.py \
  -k "durable_request_is_blocked_when_rollout_gate_is_off or durable_mode_is_disabled_by_default or health_returns_503_when_durable_mode_is_enabled_without_ready_dependencies" \
  -q
```

Result: `3 passed, 55 deselected in 0.43s`

3. FastAPI contract subset after final review fixes:

```bash
conda run -n agent bash patent/scripts/test.sh \
  tests/fastapi_contract/test_ask_contract.py \
  tests/fastapi_contract/test_health_contract.py \
  -q
```

Result: `58 passed in 0.99s`

4. Compile sanity:

```bash
conda run -n agent python -m compileall patent
```

Result: pass

5. Local app bootstrap:

```bash
conda run -n agent python -c "import os,sys; sys.path.insert(0, os.path.abspath('patent')); from server_fastapi.app import create_app; app = create_app(); print(app.state.service_name)"
```

Result: printed `patent`

6. Script entrypoint check:

```bash
bash patent/scripts/test.sh
```

Result: full patent suite passed under `conda run -n agent pytest -o cache_dir=...` with `TMPDIR` confined to `patent/.tmp`

7. Lint:

```bash
conda run -n agent bash patent/scripts/lint.sh
```

Result: pass

### Verification note

The Task 10 plan text uses raw `pytest --cache-dir=...`, but the current pytest in the `agent` environment rejects that flag. The checked-in `patent/scripts/test.sh` already uses the working equivalent:

```bash
pytest -o cache_dir="$ROOT_DIR/.pytest_cache"
```

That script was used for the passing Task 10 verification runs.

## Implementation Vs Design Check

### Durable rollout remains gated and disabled by default

- Durable ask routes block at router level when the rollout gate is off in `patent/server_fastapi/routers/ask.py`.
- Health reports `503` when durable mode is requested but cannot safely run in `patent/server_fastapi/routers/health.py`.
- Durable-off contract coverage exists in `patent/tests/fastapi_contract/test_ask_contract.py` and `patent/tests/fastapi_contract/test_health_contract.py`.

### Patent does not own canonical transcript durability

- Durable transcript writes go through `ConversationAuthorityClient` into `public-service` authority APIs.
- Patent uses Redis only for coordination, dedupe, inflight markers, cache, and overlay.
- No patent-local canonical transcript store exists.

### Durable flow includes both conversation lock and turn-level dedupe/inflight claim

In `patent/server/services/chat_persistence.py` the durable flow performs:

- conversation lock acquisition through `ExecutionLockManager`
- turn identity claim for same-trace dedupe
- inflight claim for same-conversation execution exclusion
- pending-turn marker advancement for crash-safe retries

### Atomic release and lease-renewal behavior are covered

- Redis compare helpers are registered in `patent/server/services/redis_client.py`.
- Atomic release and renew behavior are exercised in `patent/tests/test_execution_lock.py` and `patent/tests/test_redis_runtime.py`.
- Long-running durable runtime-guard renewal success and failure paths are exercised in `patent/tests/test_chat_persistence.py` and terminal SSE error behavior is covered in `patent/tests/fastapi_contract/test_ask_contract.py`.

### Same-trace retry convergence and distinct-trace rejection are covered

- Same-trace convergence and replay behavior are covered in `patent/tests/test_chat_persistence.py`.
- Distinct-trace same-conversation rejection while inflight is covered in `patent/tests/test_chat_persistence.py`.

### Stream `done` waits for assistant accept

- `AskService` refuses `done` until assistant accept succeeds.
- Contract coverage exists for assistant-accept failure, missing accept signal, and renewal failure in `patent/tests/fastapi_contract/test_ask_contract.py`.

### Health readiness returns `503` when durable mode cannot safely run

- Runtime, Redis, and authority readiness are merged into durable readiness in `patent/server_fastapi/routers/health.py`.
- Dynamic runtime degradation after startup is covered by contract tests in `patent/tests/fastapi_contract/test_health_contract.py`.

## Final Review Outcome

Task 9 review loop ended in approval after targeted re-review. The final adjudication outcome was:

- no blocker-level findings remain for JWT salt handling, stream body trace propagation, health-vs-ask ordering differences, or lifecycle cleanup/rebootstrap
- current patent Phase 1 code under `patent/` is ready for the next implementation stage

## Open External Rollout Dependencies

Durable patent traffic still must not be enabled in production until the following non-patent changes land outside this directory:

1. `gateway` disables direct conversation persistence for `actual_mode=patent` so patent authority writes do not double-persist the same turn.
2. `public-service` extends authority schema literals and allowlists to accept `source_service=patentQA` and `requested_mode=actual_mode=patent`.
3. `gateway` preserves the forwarded auth and trace behavior expected by patent.
4. `gateway` implements the patent file/mixed compatibility rewrite.
5. `gateway` keeps file/mixed patent turns on compatibility routing until patent explicitly owns them.
6. The team chooses the metadata policy for compatibility-routed patent file turns, especially whether requested patent intent is preserved in persisted metadata.

## Current Handoff Position

What is ready now:

- patent-only FastAPI service scaffold
- gunicorn wrapper and config surface
- strict Phase 1 patent protocol validation
- durable-vs-ephemeral orchestration
- Redis coordination, lock, inflight, cache, and overlay infrastructure
- authority client integration
- multi-instance safety checks and tests

What is intentionally still stubbed:

- patent retrieval strategy
- patent-native citation/ranking model
- file-aware and mixed patent execution ownership
- gateway and public-service rollout work
