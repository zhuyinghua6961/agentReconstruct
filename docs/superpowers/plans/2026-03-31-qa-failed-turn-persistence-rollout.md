# QA Failed Turn Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `fastQA` 与 `highThinkingQA` 在问答失败或取消时也能通过 `public-service` 持久化 assistant terminal turn，并在前端刷新后稳定恢复显示。

**Architecture:** 以 `public-service` 为 authority，不让 `gateway` 接管持久化。先新增 `assistant-terminal-async` internal contract 与失败态 read model，再让 `fastQA`/`highThinkingQA` 改为发 terminal event，最后补前端对 `failed/canceled` 历史消息的读取与展示。流式 phase 1 不新增 SSE `type="canceled"`，仍沿用 `type="error"` + cancel code，避免三端同时改 transport 枚举。

**Tech Stack:** FastAPI, Pydantic, MySQL/JSON mirror, Vue 3, node:test, pytest, httpx

---

## File Map

### `public-service`

- Modify: `public-service/backend/app/modules/conversation/authority_schemas.py`
  - 新增 terminal assistant request/response schema，保留旧 success-only schema 兼容。
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
  - 新增 `/internal/conversations/{conversation_id}/messages/assistant-terminal-async`。
- Modify: `public-service/backend/app/modules/conversation/service.py`
  - 新增 terminal accept/materialize 路径；修正 detail/read surface 对 `failed/canceled` 的输出。
- Modify: `public-service/backend/app/modules/conversation/repository.py`
  - 复用 existing assistant inbox row，但补 terminal status / failure metadata / convergence 规则。
- Modify: `public-service/backend/app/modules/conversation/assistant_inbox.py`
  - 确认 worker 对 terminal event 的 materialization、retry、dead 行为不丢 failure terminal 语义。
- Test: `public-service/backend/tests/test_conversation_authority_api.py`
- Test: `public-service/backend/tests/test_conversation_assistant_inbox.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Test: `public-service/backend/tests/test_route_surface.py`

### `fastQA`

- Modify: `fastQA/app/services/conversation_authority_client.py`
  - 新增 terminal assistant authority client 调用，保留旧 success-only方法兼容。
- Modify: `fastQA/app/services/chat_persistence.py`
  - 新增 `persist_assistant_terminal(...)`；success path 逐步改走 terminal path；failed/canceled path 新增 authority write。
- Modify: `fastQA/app/routers/qa.py`
  - 去掉 runtime exception synthetic `done` 的成功歧义；定义 failure-side durability ordering；在 sync/stream fail/cancel 分支发 terminal persistence。
- Possibly modify: `fastQA/app/services/stream_contract.py`
  - 如需要，为 terminal summary 增加 `terminal_status/failure_*` 聚合字段。
- Test: `fastQA/tests/test_conversation_authority_client.py`
- Test: `fastQA/tests/test_chat_persistence.py`
- Test: `fastQA/tests/test_qa_placeholder.py`
- Test: `fastQA/tests/test_stream_contract.py`
- Test: `fastQA/tests/test_qa_route_aliases.py`

### `highThinkingQA`

- Modify: `highThinkingQA/server/services/conversation_authority_client.py`
  - 新增 terminal assistant authority client 调用。
- Modify: `highThinkingQA/server/services/chat_persistence.py`
  - 新增 `persist_assistant_terminal(...)`，保留 legacy path 兼容。
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
  - 在 sync/stream fail/cancel 场景显式发 failed/canceled terminal persistence；保证 authority accept 在 terminal error 输出前完成。
- Test: `highThinkingQA/tests/test_conversation_authority_client.py`
- Test: `highThinkingQA/tests/test_chat_persistence.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`

### `frontend-vue`

- Modify: `frontend-vue/src/services/api.js`
  - 保留 `status`、`terminal_status`、`failure_*`、`done_seen` 等服务端返回字段。
- Modify: `frontend-vue/src/stores/chatStore.js`
  - `normalizeMessage()` 保留 failed/canceled message status，避免刷新后把失败消息当普通 done message 丢信息。
- Modify: `frontend-vue/src/views/Home.vue`
  - 对空内容 failed/canceled assistant message 显示最小失败壳；已有内容则按正常 markdown 渲染并叠加状态信息。
- Test: `frontend-vue/src/services/api.structure.test.js`
- Test: `frontend-vue/src/stores/chatPersistence.test.js`
- Test: `frontend-vue/src/views/Home.structure.test.js`
- Possibly add: `frontend-vue/src/stores/chatStore.failed-terminal.test.js`

### `gateway`

- No production code changes expected in phase 1.
- If contract tests需要，Modify/Test: `gateway/tests/test_qa_proxy.py`
  - 只补“stream cancel remains `type=error` envelope with cancel code”的 contract regression。

---

### Task 1: Lock Public-Service Terminal Contract

**Files:**
- Modify: `public-service/backend/app/modules/conversation/authority_schemas.py`
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`
- Test: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: Write failing API contract tests for terminal assistant accept**

Add tests covering:
- success `done`
- `failed` with empty `answer_text`
- `failed` with partial `answer_text`
- `canceled` with cancel message
- canonical `idempotency_key = {conversation_id}:{trace_id}:assistant`
- old `/assistant-async` path still requires `done_seen=true` and non-empty `answer_text`

Example assertions to add in `test_conversation_authority_api.py`:

```python
def test_authority_accepts_failed_terminal_event(client):
    response = client.post(
        "/internal/conversations/12/messages/assistant-terminal-async",
        json={
            "conversation_id": 12,
            "user_id": 7,
            "trace_id": "trace-1",
            "source_service": "fastQA",
            "route": "kb_qa",
            "requested_mode": "fast",
            "actual_mode": "fast",
            "idempotency_key": "12:trace-1:assistant",
            "terminal_event": {
                "terminal_status": "failed",
                "done_seen": False,
                "answer_text": "",
                "failure": {"stage": "llm_stream", "message": "timeout", "retriable": True},
            },
        },
    )
    assert response.status_code == 202
```

- [ ] **Step 2: Run the focused API tests to verify they fail**

Run:
```bash
conda run --no-capture-output -n agent pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_route_surface.py -q
```
Expected: fail because terminal endpoint/schema does not exist yet.

- [ ] **Step 3: Add terminal request/response schemas**

Implement in `authority_schemas.py`:
- `AuthorityAssistantTerminalFailure`
- `AuthorityAssistantTerminalEvent`
- `AuthorityAssistantTerminalAsyncRequest`
- Validation rules:
  - `done` => `done_seen=True`, `answer_text` non-empty
  - `failed` => `done_seen=False`, `failure.message` and `failure.retriable` required
  - `canceled` => `done_seen=False`, `failure` optional; if present, `retriable=False`
  - `canceled` without explicit `failure` must still validate and be normalized later into a minimal cancel message during materialization

- [ ] **Step 4: Add internal API route**

Implement `POST /internal/conversations/{conversation_id}/messages/assistant-terminal-async` in `internal_api.py`.

Requirements:
- enforce source service policy
- enforce existing canonical assistant idempotency key format
- delegate to new conversation service terminal accept method
- keep legacy `/assistant-async` route untouched

- [ ] **Step 5: Re-run focused API tests**

Run:
```bash
conda run --no-capture-output -n agent pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_route_surface.py -q
```
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/app/modules/conversation/authority_schemas.py public-service/backend/app/modules/conversation/internal_api.py public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_route_surface.py
git commit -m "feat: add authority assistant terminal contract"
```

### Task 2: Implement Public-Service Terminal Storage and Read Model

**Files:**
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/app/modules/conversation/repository.py`
- Modify: `public-service/backend/app/modules/conversation/assistant_inbox.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Test: `public-service/backend/tests/test_conversation_assistant_inbox.py`
- Test: `public-service/backend/tests/test_conversation_authority_integration.py`
- Test: `public-service/backend/tests/test_route_surface.py`

- [ ] **Step 1: Write failing repository/service tests for failed/canceled materialization**

Cover:
- enqueue terminal failed event
- materialize to `status=failed`
- `detail` returns `status`, `terminal_status`, `failure_*`
- `recent_turns` includes failed/canceled messages
- context projection excludes failed/canceled assistant turns from LLM chat history
- conversation list / summary preview stays aligned with failed/canceled terminal state
- failed/canceled materialization refreshes `message_count`, `updated_at`, and list cache
- duplicate terminal event idempotency
- missing `failure_stage` normalizes to `unknown`
- canceled without explicit `failure` still materializes with a minimal cancel message
- canceled with a partial `failure` object still normalizes `retriable=False` and preserves explicit cancel metadata
- `failed -> done` upgrade convergence

- [ ] **Step 2: Run focused public-service tests to confirm failure**

Run:
```bash
conda run --no-capture-output -n agent pytest public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_route_surface.py -q
```
Expected: fail on missing terminal-state behavior.

- [ ] **Step 3: Extend repository metadata and convergence rules**

In `repository.py`:
- keep reusing assistant inbox rows in `conversation_messages`
- store terminal metadata:
  - `terminal_status`
  - `failure_stage`
  - `failure_code`
  - `failure_message`
  - `retriable`
- keep inbox processing state separate from business terminal state
- enforce convergence:
  - `done > failed > canceled`
  - preserve canonical idempotency key across old/new paths

- [ ] **Step 4: Implement service accept/materialize path**

In `service.py`:
- add `accept_authority_assistant_terminal_async(...)`
- add terminal materialization logic for `done/failed/canceled`
- if `canceled` arrives without a `failure` object, synthesize the minimum stable cancel payload during materialization rather than rejecting the request
- ensure detail/json mirror/cache read surfaces preserve `status=failed/canceled`
- ensure `recent_turns` exposes terminal status
- ensure LLM-facing context projection filters failed/canceled assistant turns
- ensure conversation list preview / summary, `message_count`, `updated_at`, and list cache refresh stay consistent with terminal status

- [ ] **Step 5: Update inbox worker behavior**

In `assistant_inbox.py`:
- materialize terminal event payloads without assuming success-only semantics
- keep retry/dead handling intact
- ensure failed materialization logs do not erase original terminal business state

- [ ] **Step 6: Run focused tests and then broader public-service conversation suite**

Run:
```bash
conda run --no-capture-output -n agent pytest public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_route_surface.py -q
```
Then:
```bash
conda run --no-capture-output -n agent pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_route_surface.py -q
```
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add public-service/backend/app/modules/conversation/service.py public-service/backend/app/modules/conversation/repository.py public-service/backend/app/modules/conversation/assistant_inbox.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_authority_integration.py
git commit -m "feat: materialize failed qa terminal turns"
```

### Task 3: Migrate fastQA to Terminal Persistence

**Files:**
- Modify: `fastQA/app/services/conversation_authority_client.py`
- Modify: `fastQA/app/services/chat_persistence.py`
- Modify: `fastQA/app/routers/qa.py`
- Possibly modify: `fastQA/app/services/stream_contract.py`
- Test: `fastQA/tests/test_conversation_authority_client.py`
- Test: `fastQA/tests/test_chat_persistence.py`
- Test: `fastQA/tests/test_stream_contract.py`
- Test: `fastQA/tests/test_qa_placeholder.py`

- [ ] **Step 1: Write failing fastQA tests for terminal failed/canceled persistence**

Cover:
- authority client posts to `/assistant-terminal-async`
- sync/stream success path also persists `done` through the new terminal contract
- stream failure before first chunk persists `failed` with empty content
- stream failure after partial content persists `failed` with partial content
- explicit cancel persists `canceled`
- runtime exception no longer resolves via synthetic `done`
- success path still uses canonical assistant idempotency key
- authority accept failure records `terminal_persistence_unconfirmed` while preserving the original execution error returned to the client

- [ ] **Step 2: Run focused fastQA tests to verify failure**

Run:
```bash
conda run --no-capture-output -n agent pytest fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_qa_placeholder.py -q
```
Expected: fail on missing terminal path.

- [ ] **Step 3: Add authority client terminal method**

Implement in `conversation_authority_client.py`:
- `accept_assistant_turn_terminal_async(...)`
- payload must use canonical key `{conversation_id}:{trace_id}:assistant`
- keep `accept_assistant_turn_async(...)` for compat while new code migrates

- [ ] **Step 4: Add fastQA terminal persistence helper**

Implement in `chat_persistence.py`:
- `persist_assistant_terminal(...)`
- `terminal_status` parameter (`done|failed|canceled`)
- `failure` payload parameter
- maintain pending overlay policy only for successful/done terminal messages unless explicitly needed
- add explicit `terminal_persistence_unconfirmed` reporting/logging helper for authority accept failure

- [ ] **Step 5: Refactor router terminal handling**

In `routers/qa.py`:
- stop treating runtime exception path as synthetic success `done`
- collect terminal summary separately from stream transport events
- enforce ordering:
  - authority terminal accept first
  - then emit final `error` frame/JSON to client
- keep stream transport phase 1 semantics:
  - `failed` => `type="error"`
  - `canceled` => `type="error"` + cancel code
- if authority terminal accept itself fails:
  - still return the original execution error to the client
  - emit/record `terminal_persistence_unconfirmed`
  - do not silently swallow the accept failure in logs/metrics

- [ ] **Step 6: Re-run focused fastQA tests and broader regression set**

Run:
```bash
conda run --no-capture-output -n agent pytest fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_stream_contract.py fastQA/tests/test_qa_placeholder.py -q
```
Then:
```bash
conda run --no-capture-output -n agent pytest fastQA/tests/test_qa_route_aliases.py fastQA/tests/test_request_adapter.py -q
```
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/services/conversation_authority_client.py fastQA/app/services/chat_persistence.py fastQA/app/routers/qa.py fastQA/app/services/stream_contract.py fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_stream_contract.py fastQA/tests/test_qa_placeholder.py
git commit -m "feat: persist fastqa failed terminal turns"
```

### Task 4: Migrate highThinkingQA to Terminal Persistence

**Files:**
- Modify: `highThinkingQA/server/services/conversation_authority_client.py`
- Modify: `highThinkingQA/server/services/chat_persistence.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Test: `highThinkingQA/tests/test_conversation_authority_client.py`
- Test: `highThinkingQA/tests/test_chat_persistence.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`

- [ ] **Step 1: Write failing highThinkingQA tests for terminal failed/canceled persistence**

Cover:
- terminal authority client call
- sync success persists `done` through the terminal contract
- stream success persists `done` through the terminal contract
- stream exception persists `failed`
- sync exception persists `failed`
- explicit stop/cancel persists `canceled`
- authority accept completes before terminal error output path returns
- authority accept failure records `terminal_persistence_unconfirmed` while preserving the original execution error

- [ ] **Step 2: Run focused highThinkingQA tests to verify failure**

Run:
```bash
conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py -q
```
Expected: fail on missing terminal path.

- [ ] **Step 3: Add authority client terminal method**

Implement in `server/services/conversation_authority_client.py` mirroring fastQA terminal client behavior.

- [ ] **Step 4: Add terminal persistence helper**

Implement in `server/services/chat_persistence.py`:
- `persist_assistant_terminal(...)`
- maintain legacy local path only where still needed for compat
- route all authority-backed success/failure terminal writes through canonical key
- add explicit `terminal_persistence_unconfirmed` reporting/logging helper for authority accept failure

- [ ] **Step 5: Refactor ask router terminal paths**

In `server_fastapi/routers/ask.py`:
- success keeps `done`
- exception path persists `failed`
- explicit cancel path persists `canceled`
- enforce authority accept before terminal error JSON/SSE leaves the service
- if authority terminal accept itself fails:
  - still return the original execution error to the client
  - emit/record `terminal_persistence_unconfirmed`
  - do not blur it into a generic backend execution error

- [ ] **Step 6: Re-run focused and broader highThinkingQA tests**

Run:
```bash
conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py -q
```
Then:
```bash
conda run --no-capture-output -n agent pytest highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_conversation_context_service.py -q
```
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add highThinkingQA/server/services/conversation_authority_client.py highThinkingQA/server/services/chat_persistence.py highThinkingQA/server_fastapi/routers/ask.py highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py
git commit -m "feat: persist highthinking failed terminal turns"
```

### Task 5: Frontend Failed-Terminal Read and Rendering

**Files:**
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Test: `frontend-vue/src/services/api.structure.test.js`
- Test: `frontend-vue/src/stores/chatPersistence.test.js`
- Test: `frontend-vue/src/views/Home.structure.test.js`
- Create/Test: `frontend-vue/src/stores/chatStore.failed-terminal.test.js`

- [ ] **Step 1: Write failing frontend tests for failed/canceled persisted messages**

Cover:
- service/store keeps `status`, `terminal_status`, `failure_message`, `retriable`, `done_seen`
- refresh from server detail preserves failed assistant message
- empty-content failed message renders visible fallback shell
- partial-content failed message renders markdown content plus failure status context
- canceled message renders as terminal error-state, not loading-state

- [ ] **Step 2: Run focused frontend tests to verify failure**

Run:
```bash
cd frontend-vue && npm test -- src/services/api.structure.test.js src/stores/chatPersistence.test.js src/views/Home.structure.test.js
```
Expected: fail until status/failure fields are preserved and rendered.

- [ ] **Step 3: Preserve terminal fields in API and store normalization**

In `api.js` and `chatStore.js`:
- keep `status`
- keep `metadata.terminal_status`
- keep `metadata.failure_message`
- keep `metadata.retriable`
- keep `done_seen`
- normalize failed/canceled messages as complete terminal messages, not in-progress messages

- [ ] **Step 4: Add minimum failure shell in Home.vue**

Implement rendering rule:
- if assistant `content` non-empty => render markdown normally
- if assistant `status in {failed, canceled}` and `content` empty => render minimal shell with:
  - status label
  - `failure_message`
  - retryability if available
- do not regress existing streaming/error/quota card paths

- [ ] **Step 5: Re-run focused tests and production build**

Run:
```bash
cd frontend-vue && npm test -- src/services/api.structure.test.js src/stores/chatPersistence.test.js src/views/Home.structure.test.js src/stores/chatStore.failed-terminal.test.js
```
Then:
```bash
cd frontend-vue && npm run build
```
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/services/api.js frontend-vue/src/stores/chatStore.js frontend-vue/src/views/Home.vue frontend-vue/src/services/api.structure.test.js frontend-vue/src/stores/chatPersistence.test.js frontend-vue/src/views/Home.structure.test.js frontend-vue/src/stores/chatStore.failed-terminal.test.js
git commit -m "feat: render persisted failed qa turns"
```

### Task 6: Integration and Rollout Verification

**Files:**
- Modify/Test as needed: `gateway/tests/test_qa_proxy.py`
- Modify: `docs/superpowers/implementation/2026-03-31-qa-failed-turn-persistence-verification.md`

- [ ] **Step 1: Add cross-service regression tests where needed**

Minimum checks:
- legacy success-only endpoint still works
- stream cancel still uses `type="error"` envelope with cancel code
- failed terminal turn survives refresh through authority detail
- failed/canceled assistant turns do not enter LLM context projection
- authority accept failure is observable as `terminal_persistence_unconfirmed`
- duplicate terminal event idempotency holds under old/new endpoint coexistence

- [ ] **Step 2: Run backend verification suites**

Run:
```bash
conda run --no-capture-output -n agent pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_module.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_route_surface.py -q
```
Run:
```bash
conda run --no-capture-output -n agent pytest fastQA/tests/test_conversation_authority_client.py fastQA/tests/test_chat_persistence.py fastQA/tests/test_qa_placeholder.py highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py -q
```
Expected: pass.

- [ ] **Step 3: Run frontend verification**

Run:
```bash
cd frontend-vue && npm test -- src/services/api.structure.test.js src/stores/chatPersistence.test.js src/stores/chatStore.failed-terminal.test.js src/views/Home.structure.test.js
```
Run:
```bash
cd frontend-vue && npm run build
```
Expected: pass.

- [ ] **Step 4: Manual verification checklist**

Verify manually:
- `fastQA` success -> 刷新后仍是正常 `done` assistant turn
- `fastQA` 失败前无输出 -> 刷新后看到 failed assistant shell
- `fastQA` 失败前有 partial output -> 刷新后看到 partial answer + failed status
- `highThinkingQA` success -> 刷新后仍是正常 `done` assistant turn
- `highThinkingQA` 失败 -> 刷新后看到 failed assistant turn
- explicit stop/cancel -> 刷新后看到 canceled assistant turn
- 会话列表 preview / 时间 / message_count 与 detail 一致
- 下一轮问答时，失败 assistant turn 不污染传给 LLM 的上下文

- [ ] **Step 5: Write verification note**

Create/update:
- `docs/superpowers/implementation/2026-03-31-qa-failed-turn-persistence-verification.md`

Include:
- commands run
- pass/fail counts
- manual verification notes
- known limitations for phase 1 (`terminal_persistence_unconfirmed`, disconnect cancellation not fully covered)

- [ ] **Step 6: Final commit**

```bash
git add gateway/tests/test_qa_proxy.py docs/superpowers/implementation/2026-03-31-qa-failed-turn-persistence-verification.md
git commit -m "test: verify qa failed turn persistence rollout"
```

## Notes for Execution

- Do not widen gateway responsibility during implementation.
- Do not change assistant idempotency key format.
- Do not add a new SSE `type="canceled"` event in phase 1.
- Do not let `fastQA` keep the old synthetic-success `done` semantics on runtime exception paths.
- Keep `recent_turns` as authority truth, but ensure LLM-facing projection filters failed/canceled assistant turns.
- Preserve backward compatibility for legacy success-only `/assistant-async` callers until both QA backends are migrated.
