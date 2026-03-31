# Frontend Long Conversation Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `frontend-vue` long conversations remain responsive through 20+ turns by first eliminating per-chunk whole-conversation work, then adding bounded history windowing only when P0 evidence says it is still required.

**Architecture:** The full message array in the store remains authoritative. P0 only removes waste in runtime-only state, stream-target lookup, memo computation, outline rebuilds, auto-scroll, and local persistence frequency. P1 introduces a stable-identity visible-message layer and hidden-history reveal flow so bounded rendering never breaks outline jumps, DOI interactions, steps toggling, or refresh semantics.

**Tech Stack:** Vue 3, Pinia, Vite, Node test runner, existing markdown/DOI rendering helpers.

---

## File Map

### Existing Files Likely To Modify

- `frontend-vue/src/views/Home.vue`
  - 主聊天视图，包含流式目标定位、消息渲染、大纲、滚动、隐藏历史交互
- `frontend-vue/src/stores/chatStore.js`
  - 本地持久化、消息规范化、刷新恢复边界
- `frontend-vue/src/utils/messageRenderMemo.js`
  - 历史消息 memo key 成本控制
- `frontend-vue/tests/markdown-rendering.test.js`
  - markdown / DOI / 表格渲染回归验证

### New Files To Create

- `frontend-vue/src/utils/streamingTarget.js`
  - 纯 helper，负责流式目标直达、索引失效回退、request id 命中
- `frontend-vue/src/utils/streamingTarget.test.js`
  - 覆盖流式目标 lookup 规则
- `frontend-vue/src/stores/chatPersistence.test.js`
  - 覆盖 reload-cycle、runtime-only 状态清洗和恢复矩阵
- `frontend-vue/src/utils/messageWindowing.js`
  - 纯 helper，负责可见窗口切片、隐藏历史展开、稳定身份映射
- `frontend-vue/src/utils/messageWindowing.test.js`
  - 覆盖窗口切片、批次展开、稳定身份映射
- `docs/superpowers/implementation/2026-03-31-frontend-long-conversation-performance-verification.md`
  - 固定 workload、trace 方法、before/after 记录模板、P0/P1 gate 结果

---

### Task 1: Lock The Verification Baseline

**Files:**
- Create: `docs/superpowers/implementation/2026-03-31-frontend-long-conversation-performance-verification.md`
- Modify: `docs/superpowers/specs/2026-03-31-frontend-long-conversation-performance-design.md` only if the baseline wording needs sync

- [x] **Step 1: Write the fixed workload**

Document a reproducible 30-turn workload that includes:
- markdown tables
- DOI links
- steps updates
- one scroll-away-then-continue-stream scenario
- one refresh-during-incomplete-answer scenario

- [x] **Step 2: Write the exact trace procedure**

Record:
- browser/devtools version
- profiler recording steps
- where to capture `p95 chunk script time`
- how to count `saveChats()` frequency
- how to record rendered-message count
- how to mark “tab responsive vs no response”

- [x] **Step 3: Write the P0/P1 gate checklist**

The document must include a yes/no checklist for:
- 20-turn workload has no browser no-response
- user can still type
- user can still scroll
- user can still click outline
- `saveChats()` frequency matches debounce expectations
- main hotspot is no longer whole-conversation render churn

- [x] **Step 4: Review the verification doc for executability**

Another worker should be able to follow it without extra clarification.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/implementation/2026-03-31-frontend-long-conversation-performance-verification.md docs/superpowers/specs/2026-03-31-frontend-long-conversation-performance-design.md
git commit -m "docs: add frontend long-conversation verification baseline"
```

### Task 2: Make Reload-Cycle Behavior Executable And Testable

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Create: `frontend-vue/src/stores/chatPersistence.test.js`

- [x] **Step 1: Extract or expose the smallest persistence/rehydrate helpers needed for tests**

If necessary, extract pure helpers from `chatStore.js` so reload semantics can be tested without mounting the whole app.

- [x] **Step 2: Write failing reload-cycle tests**

Cover all required spec cases:
- `stepsCollapsed` persists across refresh
- history-window / highlight / near-bottom / pendingAutoScroll do not persist
- stream-target runtime state does not persist
- mid-stream refresh results in `isStreaming=false` after reload
- unfinished assistant content still appears as static message content after reload
- `references` / `referenceLinks` / `doiLocations` / completed `steps` restore intact after reload
- full message-array order and message count remain unchanged after reload

- [x] **Step 3: Run the focused tests to confirm failure**

```bash
cd frontend-vue && npm test -- src/stores/chatPersistence.test.js
```

Expected: FAIL because the reload-cycle behavior is not fully enforced yet.

- [x] **Step 4: Implement the minimal persistence-boundary changes**

Only implement what is required so reload behavior matches the spec matrix:
- runtime-only fields stay out of persisted message data
- persisted content still restores correctly
- `stepsCollapsed` remains recoverable
- refresh never resumes a stale streaming session

- [x] **Step 5: Re-run the focused tests**

```bash
cd frontend-vue && npm test -- src/stores/chatPersistence.test.js
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/stores/chatStore.js frontend-vue/src/stores/chatPersistence.test.js
git commit -m "refactor: formalize frontend reload-cycle persistence semantics"
```

### Task 3: Extract And Optimize Stream-Target Resolution

**Files:**
- Create: `frontend-vue/src/utils/streamingTarget.js`
- Create: `frontend-vue/src/utils/streamingTarget.test.js`
- Modify: `frontend-vue/src/views/Home.vue`

- [x] **Step 1: Write failing tests for stream-target resolution**

Cover:
- request-id hit finds the correct assistant target
- cached target index resolves without reverse scan
- structural change invalidates cached index and falls back safely
- fallback scan still returns the last valid assistant target when needed

- [x] **Step 2: Run the focused tests to confirm failure**

```bash
cd frontend-vue && npm test -- src/utils/streamingTarget.test.js
```

Expected: FAIL because the helper does not exist yet.

- [x] **Step 3: Implement pure `resolveStreamingTarget` helper logic**

The helper must own:
- request-id matching
- cached target index fast path
- invalidation rules for stale indexes
- reverse-scan fallback

- [x] **Step 4: Wire the helper back into `Home.vue`**

Replace ad-hoc local target lookup with the helper while keeping behavior unchanged apart from the fast path.

- [x] **Step 5: Re-run focused tests**

```bash
cd frontend-vue && npm test -- src/utils/streamingTarget.test.js
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/utils/streamingTarget.js frontend-vue/src/utils/streamingTarget.test.js frontend-vue/src/views/Home.vue
git commit -m "perf: extract and optimize streaming target resolution"
```

### Task 4: Reduce Historical Memo Recompute Cost

**Files:**
- Modify: `frontend-vue/src/utils/messageRenderMemo.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/utils/messageRenderMemo.test.js`

- [x] **Step 1: Write failing memo tests**

Cover:
- completed historical messages keep a cheap stable render signature
- unrelated streaming updates do not force heavy recomputation for old messages
- active streaming message still changes memo key when content changes

- [x] **Step 2: Run the focused tests to confirm failure**

```bash
cd frontend-vue && npm test -- src/utils/messageRenderMemo.test.js
```

Expected: FAIL on the new memo coverage.

- [x] **Step 3: Implement the minimal memo optimization**

Do not change windowing yet. Only reduce historical message render-key work.

- [x] **Step 4: Re-run focused tests**

```bash
cd frontend-vue && npm test -- src/utils/messageRenderMemo.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/utils/messageRenderMemo.js frontend-vue/src/utils/messageRenderMemo.test.js frontend-vue/src/views/Home.vue
git commit -m "perf: reduce historical message memo recomputation"
```

### Task 5: Remove Unnecessary Outline, Scroll, And Persist Work During Streaming

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify or Create: small pure helpers if extraction is needed for tests
- Validate: `frontend-vue/tests/markdown-rendering.test.js`

- [x] **Step 1: Extract pure helpers if direct testing inside `Home.vue` is too entangled**

Possible helper areas:
- outline rebuild trigger decision
- near-bottom gating
- persistence debounce decision

- [x] **Step 2: Write failing tests for these behaviors**

Cover:
- assistant streaming updates do not rebuild outline state
- auto-scroll only runs when the user is near bottom
- streaming persistence uses coarse debounce and terminal force-flush
- reload after these changes still respects the persistence matrix from Task 2

- [x] **Step 3: Run focused tests and markdown regression to confirm the gap**

```bash
cd frontend-vue && npm test -- tests/markdown-rendering.test.js
```

Plus any newly added helper tests.

- [x] **Step 4: Implement the minimal P0 behavior changes**

Only implement:
- outline updates on structural events
- near-bottom scroll protection
- streaming persistence debounce increase
- explicit force persist on `done/error/abort`

- [x] **Step 5: Re-run focused tests and markdown regression**

```bash
cd frontend-vue && npm test -- tests/markdown-rendering.test.js
```

Expected: PASS and the new helper tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/stores/chatStore.js frontend-vue/tests/markdown-rendering.test.js
git commit -m "perf: reduce outline scroll and persist churn during streaming"
```

### Task 6: Run P0 Verification And Apply The Stop/Go Gate

**Files:**
- Update: `docs/superpowers/implementation/2026-03-31-frontend-long-conversation-performance-verification.md`
- Validate: `frontend-vue`

- [x] **Step 1: Run automated frontend checks**

```bash
cd frontend-vue && npm run build
```

Expected: PASS

Status note:

- 2026-03-31 已执行 `cd frontend-vue && npm run build`
- 2026-03-31 已执行 `cd frontend-vue && npm test`
- 结果：PASS

- [ ] **Step 2: Execute the fixed 10/20/30-turn profiling workload**

Record:
- `p95 chunk script time`
- `saveChats()` frequency
- rendered-message count
- whether the tab becomes non-responsive
- whether input/scroll/outline remain usable

- [ ] **Step 3: Write the measured result into the verification doc**

Explicitly record the dominant remaining hotspot.

- [ ] **Step 4: Apply the gate mechanically**

Stop at P0 only if all of the following are true under the fixed 20-turn workload:
- no browser “page unresponsive” event
- input remains usable
- scroll remains usable
- outline click remains usable
- `saveChats()` frequency matches the configured debounce expectation
- dominant hotspot is no longer whole-conversation render churn

If any item fails, proceed directly to Task 7.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/implementation/2026-03-31-frontend-long-conversation-performance-verification.md
git commit -m "docs: record frontend p0 verification gate"
```

### Task 7: Build Stable-Identity Windowing Utilities

**Files:**
- Create: `frontend-vue/src/utils/messageWindowing.js`
- Create: `frontend-vue/src/utils/messageWindowing.test.js`

 - [x] **Step 1: Write failing windowing tests**

Cover:
- each visible message carries `absoluteMessageIndex`
- expanding older history preserves stable identity
- hidden-target resolution returns the correct batch to reveal
- no returned structure depends on window-local business identity

- [x] **Step 2: Run the focused tests to confirm failure**

```bash
cd frontend-vue && npm test -- src/utils/messageWindowing.test.js
```

Expected: FAIL because the utility does not exist yet.

- [x] **Step 3: Implement the pure windowing utility**

The utility should only own:
- visible slice calculation
- hidden-history batch expansion math
- reveal-target resolution
- stable identity mapping

- [x] **Step 4: Re-run focused tests**

```bash
cd frontend-vue && npm test -- src/utils/messageWindowing.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/utils/messageWindowing.js frontend-vue/src/utils/messageWindowing.test.js
git commit -m "feat: add stable-identity message windowing utilities"
```

### Task 8: Switch Home View To Stable-Identity Visible Rendering

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Validate: `frontend-vue/src/utils/messageWindowing.test.js`

- [x] **Step 1: Add failing regression coverage for identity-based rendering**

Cover or manually script:
- render list entries use stable absolute identity
- `v-for key` is no longer window-local index
- `data-message-index` reflects original message identity
- steps toggle targets the original message, not the local window index

- [x] **Step 2: Run the focused tests to confirm the gap**

Use the windowing test file plus any extracted helper tests.

- [x] **Step 3: Implement only the render-list identity switch**

Do not add hidden-history reveal in this task. Only switch rendering and per-message identity wiring.

- [x] **Step 4: Re-run focused tests and build**

```bash
cd frontend-vue && npm run build
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/utils/messageWindowing.test.js
git commit -m "refactor: use stable identity for visible message rendering"
```

### Task 9: Add Hidden-History Reveal Flow

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Validate: `frontend-vue/src/utils/messageWindowing.test.js`

- [x] **Step 1: Add failing coverage for hidden-history reveal**

Cover or manually script:
- target in hidden history reveals the right batch
- reveal preserves `absoluteMessageIndex`
- reveal does not break current streaming message rendering

- [x] **Step 2: Run the focused tests to confirm the gap**

Use the windowing test file plus any extracted helper tests.

- [x] **Step 3: Implement only hidden-history reveal**

Do not rewire outline/DOI yet. Only add folded-history block, batch expansion, and reveal-first behavior.

- [x] **Step 4: Re-run focused tests and build**

```bash
cd frontend-vue && npm run build
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/utils/messageWindowing.test.js
git commit -m "feat: add hidden history reveal flow"
```

### Task 10: Rewire Outline, DOI, Steps, And Highlight To Reveal-First Flow

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/tests/markdown-rendering.test.js` if regression coverage is needed

- [x] **Step 1: Add failing regression coverage for identity-sensitive interactions**

Cover or manually script:
- outline click on hidden history reveals then scrolls to the correct original message
- DOI click still resolves against the correct original message
- steps expand/collapse still hits the correct original message after windowing
- highlight state follows stable identity, not local index

- [x] **Step 2: Run the focused tests to confirm the gap**

Use the windowing test file plus any extracted helper tests.

- [x] **Step 3: Implement the interaction rewiring**

Only implement:
- outline through reveal-first flow
- DOI through reveal-first flow
- steps/highlight through stable identity

- [x] **Step 4: Re-run focused tests and build**

```bash
cd frontend-vue && npm run build
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/utils/messageWindowing.test.js frontend-vue/tests/markdown-rendering.test.js
git commit -m "feat: reconnect long-conversation interactions to hidden history"
```

### Task 11: Final Verification And Release Decision

**Files:**
- Update: `docs/superpowers/implementation/2026-03-31-frontend-long-conversation-performance-verification.md`
- Validate: `frontend-vue`

- [x] **Step 1: Run the full frontend verification set**

```bash
cd frontend-vue && npm test -- src/stores/chatPersistence.test.js src/utils/streamingTarget.test.js src/utils/messageRenderMemo.test.js src/utils/messageWindowing.test.js tests/markdown-rendering.test.js
cd frontend-vue && npm run build
```

Expected: PASS

- [ ] **Step 2: Re-run the fixed workload in the final state**

Record whether:
- rendered-message count stays bounded after 30 turns
- tab remains responsive
- input/scroll/outline/DOI/steps/refresh recovery all still work

- [ ] **Step 3: Update the verification doc with final verdict**

Write:
- whether P1 is required in production
- whether a feature flag should remain
- whether P2 virtual-list research is still needed

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/implementation/2026-03-31-frontend-long-conversation-performance-verification.md
git commit -m "docs: finalize frontend long-conversation verification"
```

---

## Review Gates

- After Task 1, request review on verification baseline completeness.
- After Task 2, request review on reload-cycle persistence semantics.
- After Task 5, request review on P0 render/scroll/persist changes before running the stop/go gate.
- After Task 6, stop and decide with evidence whether P1 is still needed.
- After Task 8, request review on stable-identity visible rendering.
- After Task 10, request review on reveal-first outline/DOI/steps/highlight correctness.
- After Task 11, request final review before merge or rollout.

## Execution Notes

- Do not start P1 if Task 6 proves P0 already meets the stop condition.
- Prefer extracting small pure helpers from `Home.vue` whenever that is the only way to test behavior without mounting the full app.
- Do not let window-local message indexes leak into user interactions.
- Keep runtime-only fields out of persisted chat data.
