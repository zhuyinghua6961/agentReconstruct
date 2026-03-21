# Chat History Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix chat history corruption, unstable list behavior, and inconsistent conversation persistence across the gateway frontend and public-service backend.

**Architecture:** The frontend remains responsible for transient UI state only, but it must stop mutating the wrong conversation during streaming and must render the sidebar from one coherent style system. The backend remains the persistence authority, but list ordering and detail read/write paths need to be stabilized so list/detail stay consistent.

**Tech Stack:** Vue 3 + Pinia + Vite, FastAPI, public-service conversation module, Redis-backed cache, JSON document store.

---

## File Map

- Modify: `gateway/frontend-vue/src/views/Home.vue`
  - Disable or safely gate conversation switching during streaming.
  - Prevent sidebar interactions from corrupting the active stream target.
  - Fix active/pinned visual state and sidebar interaction affordances.
- Modify: `gateway/frontend-vue/src/stores/chatStore.js`
  - Stabilize client-side chat merging and de-duplication.
  - Avoid stale local draft duplication after server sync.
  - Keep current chat selection and loaded detail consistent.
- Modify: `gateway/frontend-vue/src/styles/main.css`
  - Remove or scope conflicting legacy sidebar/history styles so they stop fighting `Home.vue`.
- Test/verify: `gateway/frontend-vue` build
  - `npm run build`
- Modify: `public-service/backend/app/modules/conversation/repository.py`
  - Stabilize conversation list ordering.
- Modify: `public-service/backend/app/modules/conversation/service.py`
  - Stop trusting stale detail cache as write source.
  - Tighten message_count/detail refresh consistency.
- Test: `public-service/backend/tests/test_conversation_module.py`
  - Add regression coverage for stable ordering and write/read consistency.

## Task 1: Frontend Streaming Safety And Sidebar Rendering

**Files:**
- Modify: `gateway/frontend-vue/src/views/Home.vue`
- Modify: `gateway/frontend-vue/src/styles/main.css`
- Verify: `cd gateway/frontend-vue && npm run build`

- [ ] Identify the exact sidebar interactions that must be blocked or redirected during streaming.
- [ ] Bind stream updates to the originating conversation, or block switching while a stream is active.
- [ ] Ensure pinned and active visual states are not visually ambiguous.
- [ ] Remove or neutralize legacy global sidebar/history CSS rules that conflict with `Home.vue`.
- [ ] Build the gateway frontend and confirm the bundle still succeeds.

## Task 2: Frontend Conversation Merge And Local Draft Hygiene

**Files:**
- Modify: `gateway/frontend-vue/src/stores/chatStore.js`
- Verify: `cd gateway/frontend-vue && npm run build`

- [ ] Replace the current local/server merge strategy with deterministic de-duplication.
- [ ] Drop obsolete `temp_*` drafts once a synced conversation exists for the same working chat.
- [ ] Keep `currentChatId`, loaded detail, and local storage persistence aligned after sync or switch.
- [ ] Stop preserving empty `temp_*` conversations just because they are the currently selected draft.
- [ ] Prevent stale async `switchChat()` responses from overwriting the most recent selection.
- [ ] Do not re-append incomplete local assistant tails to synced conversations unless the stream session still matches.
- [ ] Replace global history-row syncing indicators with per-chat syncing state.
- [ ] Build the gateway frontend and confirm no store/runtime syntax regressions.

## Task 3: Backend Conversation Ordering And Write-Path Truth Source

**Files:**
- Modify: `public-service/backend/app/modules/conversation/repository.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Test: `public-service/backend/tests/test_conversation_module.py`

- [ ] Make conversation list ordering stable under equal `updated_at` values.
- [ ] Stop using cached detail snapshots as the preferred write source for `add_message` and title/file mutations.
- [ ] Preserve consistent `message_count` and detail payload after writes.
- [ ] Make conversation activity updates explicitly advance `conversations.updated_at` on write paths rather than assuming schema behavior.
- [ ] Avoid stale detail cache reads when cache freshness cannot be proven against the current conversation row.
- [ ] Prevent title regression on concurrent rename + write operations by preserving document truth or reloading row state inside the write lock.
- [ ] Add regression tests covering stable sort order and no stale-cache overwrite on write.
- [ ] Add regression coverage for title preservation, cache freshness, and explicit activity timestamp updates where feasible.
- [ ] Run the targeted backend test file.

## Task 4: End-To-End Verification

**Files:**
- Verify runtime behavior only; no planned new code files.

- [ ] Build `gateway/frontend-vue` fresh after all frontend changes.
- [ ] Run targeted `public-service` conversation module tests fresh after backend changes.
- [ ] Re-check the final diff for any unexpected unrelated edits.
- [ ] Summarize fixed issues, residual risks, and any manual verification still needed.
