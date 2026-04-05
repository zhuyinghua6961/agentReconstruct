# Multi-Chat Background Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support up to 5 concurrent background-generating chats within the current frontend instance while keeping one active generation per chat, preserving current backend contracts, and aligning thinking-service capacity defaults.

**Architecture:** Replace the current single global stream runtime with a per-chat busy-runtime map in the frontend. Keep stream targeting local to `(chatId, clientStreamRequestId)`, prevent server-detail overwrite for locally busy synced chats, and scope UI locks to the active chat rather than the whole app. Backend changes are limited to capacity defaults and do not change ask/ask_stream payloads or persistence contracts.

**Tech Stack:** Vue 3, Pinia, Vite, Node test runner, FastAPI config modules, existing SSE streaming helpers.

---

## Delivery Rule

This plan must produce real working functionality, not a placeholder shell.

For every task below, "done" means:

- the code path is wired into the real runtime, not just added as an unused helper or mock
- the user-visible behavior is backed by real request, stream, abort, and state transitions
- tests prove the functional behavior, not only structure or copy

Explicitly not acceptable:

- adding badges, buttons, or flags without real per-chat stream isolation behind them
- keeping any shared single-stream buffer or abort controller that would break true concurrency
- simulating stop by only mutating message text
- simulating the 5-chat limit by disabling UI without actually blocking request dispatch
- leaving old single-stream code as the effective runtime while adding a parallel abstraction that is never truly exercised

---

## File Map

### Existing Files To Modify

- `frontend-vue/src/stores/chatStore.js`
  - Replace global single-stream assumptions with per-chat busy runtime helpers and busy-aware switch behavior.
- `frontend-vue/src/views/Home.vue`
  - Rework send/stop flow, sidebar status/actions, per-chat busy guards, and busy-safe chat switching.
- `frontend-vue/src/stores/chatPersistence.js`
  - Keep new busy runtime state out of persistence.
- `frontend-vue/src/views/Home.structure.test.js`
  - Lock UI structure for busy badges, stop actions, and busy delete restrictions.
- `frontend-vue/src/stores/chatPersistence.test.js`
  - Verify runtime state is not persisted.
- `frontend-vue/src/stores/chatStore.failed-terminal.test.js`
  - Extend where needed for canceled or terminal behavior continuity.
- `frontend-vue/src/services/api.js`
  - Keep concurrent `askStream()` usage contract explicit if helper changes are needed.
- `highThinkingQA/config.py`
  - Raise thinking-service stream and executor defaults.
- `highThinkingQA/config.shared.env`
  - Align default runtime env values.
- `resource/config/services/highThinkingQA/config.shared.env`
  - Align mirrored shared config.
- `gateway/app/core/config.py`
  - Align gateway admission defaults for thinking-mode concurrency when admission is enabled.
- `gateway/tests/test_config.py`
  - Lock gateway admission default expectations.
- `config.shared.env`
  - If deployment reads gateway env from the repo root shared env, align documented env defaults there as well.

### New Files To Create

- `frontend-vue/src/stores/chatStore.concurrent-streaming.test.js`
  - Cover per-chat busy state, 5-chat cap, same-chat single-flight, and busy switch behavior.
- `frontend-vue/src/utils/chatBusyRuntime.js`
  - Optional pure helper for busy-runtime map operations if extracting from `chatStore.js` improves clarity.
- `frontend-vue/src/utils/chatBusyRuntime.test.js`
  - Pure tests for capacity counting, same-chat rules, and lifecycle transitions.
- `docs/superpowers/implementation/2026-04-04-multi-chat-background-streaming-verification.md`
  - Verification log for frontend regression, build, and backend/config checks.

---

### Task 1: Lock Per-Chat Busy Runtime Semantics In Tests

**Files:**
- Create: `frontend-vue/src/stores/chatStore.concurrent-streaming.test.js`
- Optional Create: `frontend-vue/src/utils/chatBusyRuntime.js`
- Optional Test: `frontend-vue/src/utils/chatBusyRuntime.test.js`
- Modify: `frontend-vue/src/stores/chatStore.js`

- [ ] **Step 1: Write failing store-level tests for busy runtime semantics**

Cover:
- starting one busy chat marks only that chat busy
- different chats can be busy simultaneously
- the same chat cannot start a second busy runtime
- `activeBusyCount` includes `dispatching` and `streaming`
- the 6th busy attempt is rejected locally
- finishing or stopping a chat frees one capacity slot

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
cd frontend-vue && npm test -- src/stores/chatStore.concurrent-streaming.test.js
```

Expected: FAIL because per-chat busy runtime helpers do not exist yet.

- [ ] **Step 3: Implement the minimal busy-runtime state model**

Implement in `chatStore.js` or an extracted helper:
- per-chat busy runtime map
- selectors: `isChatBusy`, `isChatStreaming`, `activeBusyCount`, `hasBusyCapacity`
- lifecycle methods: start, mark streaming, finish, stop, clear
- ensure the new per-chat runtime becomes the actual runtime source of truth rather than a shadow structure beside the old global single-stream logic

- [ ] **Step 4: Re-run the focused tests**

Run:

```bash
cd frontend-vue && npm test -- src/stores/chatStore.concurrent-streaming.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/stores/chatStore.js frontend-vue/src/stores/chatStore.concurrent-streaming.test.js frontend-vue/src/utils/chatBusyRuntime.js frontend-vue/src/utils/chatBusyRuntime.test.js
git commit -m "refactor(frontend): add per-chat busy streaming runtime"
```

### Task 2: Make Persistence And Reload Ignore Busy Runtime State

**Files:**
- Modify: `frontend-vue/src/stores/chatPersistence.js`
- Modify: `frontend-vue/src/stores/chatPersistence.test.js`
- Modify: `frontend-vue/src/stores/chatStore.js`

- [ ] **Step 1: Write failing persistence tests for busy runtime exclusion**

Cover:
- per-chat busy runtime records are never serialized
- `streamRequestId` remains sanitized as runtime-only state
- refresh clears all busy runtime state
- static assistant content still survives reload

- [ ] **Step 2: Run the focused tests to verify failure**

Run:

```bash
cd frontend-vue && npm test -- src/stores/chatPersistence.test.js
```

Expected: FAIL on new busy-runtime persistence assertions.

- [ ] **Step 3: Implement the minimal persistence boundary changes**

Ensure:
- busy runtime map is runtime-only
- no `AbortController`, pending frame handle, or per-chat runtime map leaks into localStorage
- existing persisted chat content behavior remains unchanged

- [ ] **Step 4: Re-run the focused tests**

Run:

```bash
cd frontend-vue && npm test -- src/stores/chatPersistence.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/stores/chatPersistence.js frontend-vue/src/stores/chatPersistence.test.js frontend-vue/src/stores/chatStore.js
git commit -m "test(frontend): lock busy runtime persistence boundary"
```

### Task 3: Rework Home Send/Stop Flow For Multi-Chat Concurrency

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/services/api.js` only if request helper changes are needed
- Modify: `frontend-vue/src/utils/streamingTarget.js` if targeting hooks need extension

- [ ] **Step 1: Write failing UI-structure tests for multi-chat busy behavior**

Add assertions in `Home.structure.test.js` for:
- new chat button no longer globally disabled by unrelated busy chats
- sidebar renders busy badge plus stop affordance per chat
- send button behavior is scoped to current chat
- delete affordance is disabled or hidden for busy chats

- [ ] **Step 2: Run the structure tests to verify failure**

Run:

```bash
cd frontend-vue && npm test -- src/views/Home.structure.test.js
```

Expected: FAIL because current template still assumes one global stream.

- [ ] **Step 3: Implement per-chat send and stop logic in `Home.vue`**

Implement:
- local capacity check before dispatch
- same-chat single-flight check
- per-chat stop for current chat and sidebar action
- busy records created before dispatch and cleaned on done/error/stop
- no global history lock across chats
- migrate the full set of current single-stream runtime variables into per-chat runtime state:
  - abort controller
  - streaming chat id
  - target message index
  - pending content buffer
  - flush frame or timer handle
- ensure chunk buffering and flush scheduling are isolated per chat so concurrent streams cannot interleave content or route chunks to the wrong assistant message
- remove or neutralize the old single-stream runtime path so the shipped page is genuinely multi-chat capable rather than partially dual-wired

- [ ] **Step 4: Re-run the structure tests**

Run:

```bash
cd frontend-vue && npm test -- src/views/Home.structure.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/services/api.js frontend-vue/src/utils/streamingTarget.js frontend-vue/src/views/Home.structure.test.js
git commit -m "feat(frontend): support multi-chat background streaming UI"
```

### Task 4: Prevent Busy Synced Chats From Being Overwritten On Switch

**Files:**
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/stores/chatStore.concurrent-streaming.test.js`
- Modify: `frontend-vue/src/views/Home.vue` if polling-triggered refresh guards need to live there

- [ ] **Step 1: Write failing tests for busy chat switching**

Cover:
- switching to a synced idle chat still refreshes from server
- switching to a synced busy chat skips server-detail overwrite
- after busy state clears, normal refresh may resume
- local in-flight assistant message survives chat switching
- file-status polling refresh does not overwrite messages for a busy chat
- file add/remove follow-up detail refresh does not overwrite messages for a busy chat

- [ ] **Step 2: Run the focused tests to verify failure**

Run:

```bash
cd frontend-vue && npm test -- src/stores/chatStore.concurrent-streaming.test.js
```

Expected: FAIL because current `switchChat()` always refreshes synced chat detail.

- [ ] **Step 3: Implement busy-aware `switchChat()` behavior**

Implement:
- render local messages immediately when target chat is busy
- skip server detail fetch while target chat is locally busy
- keep existing idle-chat refresh behavior unchanged
- apply the same busy-safe rule to all conversation-detail refresh paths, including `refreshCurrentChatFiles()` and file-upload or file-remove follow-up refreshes, so no detail-sync path can replace `chat.messages` for a locally busy chat

- [ ] **Step 4: Re-run the focused tests**

Run:

```bash
cd frontend-vue && npm test -- src/stores/chatStore.concurrent-streaming.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/stores/chatStore.js frontend-vue/src/stores/chatStore.concurrent-streaming.test.js frontend-vue/src/views/Home.vue
git commit -m "fix(frontend): avoid overwriting busy chats on server refresh"
```

### Task 5: Lock Busy Chat Deletion And Background Isolation

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/views/Home.structure.test.js`
- Modify: `frontend-vue/src/stores/chatStore.concurrent-streaming.test.js`

- [ ] **Step 1: Write failing tests for delete and isolation edge cases**

Cover:
- busy chats cannot be deleted
- deleting an idle chat while other chats are busy does not disturb busy runtime state
- background chunks do not force current-chat auto-scroll changes

- [ ] **Step 2: Run the targeted tests to verify failure**

Run:

```bash
cd frontend-vue && npm test -- src/views/Home.structure.test.js src/stores/chatStore.concurrent-streaming.test.js
```

Expected: FAIL on new deletion and isolation assertions.

- [ ] **Step 3: Implement the minimal UI and state guards**

Implement:
- disable or hide delete affordance for busy chats
- preserve busy runtime records when other chats are deleted
- ensure current-chat-only scroll behavior remains intact
- verify these guards operate against real busy runtime state from active requests, not a synthetic UI-only status

- [ ] **Step 4: Re-run the targeted tests**

Run:

```bash
cd frontend-vue && npm test -- src/views/Home.structure.test.js src/stores/chatStore.concurrent-streaming.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/views/Home.structure.test.js frontend-vue/src/stores/chatStore.concurrent-streaming.test.js
git commit -m "fix(frontend): lock busy chat delete and isolation rules"
```

### Task 6: Align Thinking-Service Capacity Defaults

**Files:**
- Modify: `highThinkingQA/config.py`
- Modify: `highThinkingQA/config.shared.env`
- Modify: `resource/config/services/highThinkingQA/config.shared.env`
- Modify: `gateway/app/core/config.py`
- Modify: `gateway/tests/test_config.py`
- Optional Modify: `config.shared.env`

- [ ] **Step 1: Write a failing config contract test or assertion**

If focused config tests exist, extend them. Otherwise add small targeted tests under `highThinkingQA/tests/` and `gateway/tests/` that assert:
- `ASK_STREAM_MAX_CONCURRENT >= 5`
- `ASK_EXECUTOR_MAX_WORKERS >= 5`
- gateway admission defaults do not leave `thinking_max_concurrent` at 2 when this rollout is enabled

- [ ] **Step 2: Run the focused config test to verify failure**

Run:

```bash
pytest highThinkingQA/tests -k "config and concurrent" -q
pytest gateway/tests/test_config.py -q
```

Expected: FAIL until defaults are raised or the targeted test coverage is added.

- [ ] **Step 3: Raise the defaults**

Update:
- Python config defaults
- checked-in shared env defaults
- mirrored resource config defaults
- gateway admission defaults in `gateway/app/core/config.py`
- gateway config tests in `gateway/tests/test_config.py`
- if the deployment path reads gateway env from the repo-root `config.shared.env`, align the documented env defaults there too

- [ ] **Step 4: Re-run the focused config test**

Run:

```bash
pytest highThinkingQA/tests -k "config and concurrent" -q
pytest gateway/tests/test_config.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/config.py highThinkingQA/config.shared.env resource/config/services/highThinkingQA/config.shared.env gateway/app/core/config.py gateway/tests/test_config.py config.shared.env highThinkingQA/tests
git commit -m "chore(runtime): align streaming concurrency defaults"
```

### Task 7: Run Regression And Verification

**Files:**
- Create: `docs/superpowers/implementation/2026-04-04-multi-chat-background-streaming-verification.md`

- [ ] **Step 1: Write the verification note**

Document:
- exact frontend test commands run
- exact backend test commands run
- exact gateway config test commands run
- whether `npm run build` passes
- any known residual limits, especially cross-tab and post-refresh caveats

- [ ] **Step 2: Run frontend regression tests**

Run:

```bash
cd frontend-vue && npm test -- src/views/Home.structure.test.js src/stores/chatPersistence.test.js src/stores/chatStore.concurrent-streaming.test.js src/utils/streamingTarget.test.js
```

Expected: PASS

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd frontend-vue && npm run build
```

Expected: PASS

- [ ] **Step 4: Run backend targeted verification**

Run:

```bash
pytest highThinkingQA/tests -k "config or ask_service_executor or ask_router_summary_persistence" -q
pytest gateway/tests/test_config.py -q
```

Expected: PASS, or document exactly which unrelated failures remain.

- [ ] **Step 5: Record results in the verification note**

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/implementation/2026-04-04-multi-chat-background-streaming-verification.md
git commit -m "docs: record multi-chat background streaming verification"
```

---

## Notes For Implementers

- Do not introduce backend payload changes unless a later review explicitly approves them.
- Do not claim a cross-tab or cross-device 5-way cap anywhere in UI copy or tests.
- Keep route navigation away from `Home` outside the guarantee; the feature only promises background survival during in-page chat switching.
- Preserve current terminal error, canceled, and done rendering semantics unless tests prove a necessary adjustment.
- Do not stop at wiring structure tests. Before claiming completion, verify actual concurrent request execution, actual stop behavior, and actual local 6th-request blocking against the real runtime.
