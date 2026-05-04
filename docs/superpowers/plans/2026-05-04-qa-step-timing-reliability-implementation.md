# QA Step Timing Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve QA processing step timings across live streaming, recoverable task terminal persistence, replay, and history reload for fastQA, highThinkingQA, and patent QA.

**Architecture:** Keep existing QA backend timing producers unchanged and repair the contract at the gateway/public-service persistence boundary plus frontend normalization/display. The terminal source of truth is `done.timings`; frontend metadata can show partial timings but terminal timings replace it when present.

**Tech Stack:** Vue 3, Vite, Pinia, Node `node:test`, FastAPI, Pydantic, pytest, httpx test transports.

---

## Spec Reference

- Spec: `docs/superpowers/specs/2026-05-04-qa-step-timing-reliability-design.md`

## File Map

- Modify: `frontend-vue/src/utils/stageTimings.js`
  - Add a testable step-to-timing duration helper for generation-stage and highThinking mappings.
- Modify: `frontend-vue/src/utils/stageTimings.test.js`
  - Cover highThinking visible step mapping, Step 5 priority, and existing timing family behavior.
- Modify: `frontend-vue/src/utils/routingStatus.js`
  - Preserve `timings` and `stage_timings_ms` from metadata events as `metadata.timings`.
- Modify: `frontend-vue/src/utils/routingStatus.test.js`
  - Cover metadata timing preservation.
- Modify: `frontend-vue/src/views/Home.vue`
  - Use the timing helper from `stageTimings.js`; preserve `skipped` as terminal in live steps.
- Modify: `frontend-vue/src/services/api.js`
  - Preserve `skipped` in normalized API message steps.
- Modify: `frontend-vue/src/stores/chatStore.js`
  - Preserve `skipped` in persisted/local message step normalization.
- Modify: `frontend-vue/src/views/Home.structure.test.js`
  - Lock in Home usage of the timing helper and skipped status support if behavioral tests cannot import the SFC functions directly.
- Modify: `frontend-vue/src/services/api.structure.test.js`
  - Lock in skipped status support in API normalization if the normalizer remains private.
- Modify: `frontend-vue/src/stores/chatPersistence.test.js`
  - Add a persisted message with `status: "skipped"` and timings to prove reload keeps both.
- Modify: `gateway/app/services/qa_tasks.py`
  - Capture `done.timings`, pass it to terminal persistence, and include it in terminal sync retry payloads.
- Modify: `gateway/app/services/conversation_persistence.py`
  - Add optional `timings` to `terminal_task_assistant()` payload.
- Modify: `gateway/tests/test_task_api.py`
  - Cover direct terminal write, queued terminal sync fallback, and retry sync with timings.
- Modify: `public-service/backend/app/modules/conversation/task_schemas.py`
  - Add optional terminal request `timings` field.
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
  - Forward terminal request timings to the conversation service.
- Modify: `public-service/backend/app/modules/conversation/service.py`
  - Store `metadata.timings` on terminal task assistant messages.
- Modify: `public-service/backend/tests/test_conversation_task_runtime.py`
  - Cover service terminal timings persistence and idempotency.
- Modify: `public-service/backend/tests/test_conversation_authority_api.py`
  - Cover internal task terminal API accepting timings.

## Task 1: Frontend Timing Metadata and Step Mapping

**Files:**
- Modify: `frontend-vue/src/utils/stageTimings.js`
- Modify: `frontend-vue/src/utils/stageTimings.test.js`
- Modify: `frontend-vue/src/utils/routingStatus.js`
- Modify: `frontend-vue/src/utils/routingStatus.test.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/views/Home.structure.test.js`

- [ ] **Step 1: Add failing stage timing tests**

Append these tests to `frontend-vue/src/utils/stageTimings.test.js`:

```js
import {
  getStepTimingDurationLabel,
} from './stageTimings.js'

test('maps highThinking visible step keys to detailed timing keys', () => {
  const message = {
    timings: {
      step1_parallel: 3.2,
      step2_pre_answer: 1.1,
      step3_retrieval: 7.8,
      step4_synthesis: 11.4,
      step5_check_revise: 2.5,
      total: 26.0,
    },
  }

  assert.equal(getStepTimingDurationLabel(message, { step: 'step1', title: '阶段1' }), '3.2s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step2', title: '阶段2' }), '1.1s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step3', title: '阶段3' }), '7.8s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step4', title: '阶段4' }), '11.4s')
})

test('prefers specific highThinking step5 totals before aggregate fallback', () => {
  const message = {
    timings: {
      step5_check_revise: 9.0,
      step5_check_total: 3.0,
      step5_revise_total: 6.0,
    },
  }

  assert.equal(getStepTimingDurationLabel(message, { step: 'step5_check', title: '阶段5A' }), '3.0s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step5_revise', title: '阶段5B' }), '6.0s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step5', title: '阶段5' }), '9.0s')
})

test('maps generation stage titles and keys to stage timing labels', () => {
  const message = {
    metadata: {
      timings: {
        stage1: 1000,
        stage25: 2500,
      },
    },
  }

  assert.equal(getStepTimingDurationLabel(message, { step: 'stage1', title: '阶段一' }), '1.0s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'stage25', title: '阶段二点五' }), '2.5s')
})
```

- [ ] **Step 2: Add failing routing metadata test**

Append to `frontend-vue/src/utils/routingStatus.test.js`:

```js
test('mergeRoutingMetadata preserves timing metadata for live stage display', () => {
  const fromStageTimings = mergeRoutingMetadata(
    { route: 'kb_qa' },
    { stage_timings_ms: { stage1: 1000, stage2: 2000 } }
  )
  assert.deepEqual(fromStageTimings.timings, { stage1: 1000, stage2: 2000 })
  assert.deepEqual(fromStageTimings.stage_timings_ms, { stage1: 1000, stage2: 2000 })

  const fromTimings = mergeRoutingMetadata(
    { timings: { stage1: 1000 } },
    { timings: { stage1: 1000, stage2: 2000 } }
  )
  assert.deepEqual(fromTimings.timings, { stage1: 1000, stage2: 2000 })
})
```

- [ ] **Step 3: Run frontend timing tests and confirm failure**

Run:

```bash
cd frontend-vue && npm run test -- src/utils/stageTimings.test.js src/utils/routingStatus.test.js
```

Expected: FAIL because `getStepTimingDurationLabel` is not exported and routing metadata drops timing fields.

- [ ] **Step 4: Implement timing helper in `stageTimings.js`**

Add an exported helper:

```js
const GENERATION_STEP_KEY_BY_TITLE = {
  阶段一: 'stage1',
  阶段二: 'stage2',
  阶段二点五: 'stage25',
  阶段2点5: 'stage25',
  '阶段2.5': 'stage25',
  阶段三: 'stage3',
  阶段四: 'stage4',
}

const THINKING_TIMING_KEYS_BY_STEP = {
  step1: ['step1_parallel'],
  step2: ['step2_pre_answer'],
  step3: ['step3_retrieval'],
  step4: ['step4_synthesis'],
  step5: ['step5_check_revise', 'step5_check_total', 'step5_revise_total'],
  step5_check: ['step5_check_total', 'step5_check_revise'],
  step5_revise: ['step5_revise_total', 'step5_check_revise'],
}

function normalizeStepTitleForTiming(step = {}) {
  const rawTitle = String(step?.title || '').trim()
  const rawMessage = String(step?.message || step?.content || '').trim()
  const title = rawTitle || rawMessage.split(/[：:]/)[0] || ''
  return title.replace(/\s+/g, '')
}

export function getStepTimingDurationLabel(message, step = {}) {
  const stepKey = String(step?.step || '').trim()
  const title = normalizeStepTitleForTiming(step)
  const candidateKeys = [
    stepKey,
    GENERATION_STEP_KEY_BY_TITLE[title],
    ...(THINKING_TIMING_KEYS_BY_STEP[stepKey] || []),
  ].filter(Boolean)
  const model = getMessageStageTimingModel(message)
  const entriesByKey = new Map()
  model.entries.forEach((timing) => {
    entriesByKey.set(timing.key, timing)
    entriesByKey.set(String(timing.label || '').replace(/\s+/g, ''), timing)
  })
  const entry = candidateKeys.map((key) => entriesByKey.get(key)).find(Boolean)
  return entry?.durationLabel || ''
}
```

Keep existing exports and tests passing.

- [ ] **Step 5: Preserve timing metadata in `routingStatus.js`**

Inside `mergeRoutingMetadata()`, add:

```js
  if (next.stage_timings_ms && typeof next.stage_timings_ms === 'object' && !Array.isArray(next.stage_timings_ms)) {
    metadata.stage_timings_ms = { ...next.stage_timings_ms }
    metadata.timings = { ...next.stage_timings_ms }
  }
  if (next.timings && typeof next.timings === 'object' && !Array.isArray(next.timings)) {
    metadata.timings = { ...next.timings }
  }
```

Place this before returning metadata. `next.timings` intentionally wins if both are present.

- [ ] **Step 6: Wire Home to the helper**

Modify the import in `frontend-vue/src/views/Home.vue`:

```js
import { getMessageStageTimingModel, getStepTimingDurationLabel as getStepTimingDurationLabelFromModel } from '../utils/stageTimings'
```

Replace the local `getStepTimingDurationLabel(msg, step)` body with:

```js
function getStepTimingDurationLabel(msg, step) {
  return getStepTimingDurationLabelFromModel(msg, {
    ...step,
    title: getStepTitle(step),
    message: step?.message,
  })
}
```

Update `frontend-vue/src/views/Home.structure.test.js` to assert the aliased import:

```js
assert.match(source, /getStepTimingDurationLabel\s+as\s+getStepTimingDurationLabelFromModel/)
```

- [ ] **Step 7: Run frontend tests for this task**

Run:

```bash
cd frontend-vue && npm run test -- src/utils/stageTimings.test.js src/utils/routingStatus.test.js src/views/Home.structure.test.js
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend-vue/src/utils/stageTimings.js frontend-vue/src/utils/stageTimings.test.js frontend-vue/src/utils/routingStatus.js frontend-vue/src/utils/routingStatus.test.js frontend-vue/src/views/Home.vue frontend-vue/src/views/Home.structure.test.js
git commit -m "fix: stabilize frontend QA step timing display"
```

## Task 2: Preserve Skipped Step Status in Frontend Normalization

**Files:**
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/services/api.structure.test.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/stores/chatPersistence.test.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/views/Home.structure.test.js`

- [ ] **Step 1: Add failing store persistence test**

In `frontend-vue/src/stores/chatPersistence.test.js`, add a skipped step to an existing persistence fixture or append this focused assertion near the existing timings preservation test:

```js
test('chat store preserves skipped QA steps with timings', () => {
  const store = createChatStore()
  store.chats = [
    {
      id: 'chat-skipped',
      title: 'Skipped timing',
      messages: [
        { role: 'user', content: 'q' },
        {
          role: 'assistant',
          content: 'a',
          steps: [{ step: 'stage25', title: '阶段二点五', status: 'skipped', message: '阶段二点五：已跳过MD原文扩展' }],
          metadata: { timings: { stage25: 0 } },
        },
      ],
    },
  ]
  store.currentChatId = 'chat-skipped'

  const restored = store.currentMessages[1]
  assert.equal(restored.steps[0].status, 'skipped')
  assert.deepEqual(restored.metadata.timings, { stage25: 0 })
})
```

If `createChatStore()` is not the helper used in this file, adapt to the file's existing harness instead of adding a new harness.

- [ ] **Step 2: Add structure assertions for private normalizers**

In `frontend-vue/src/services/api.structure.test.js`, assert API normalization recognizes skipped:

```js
assert.match(source, /\['skipped',\s*'skip',\s*'skipping'\]\.includes\(raw\)\)\s*return 'skipped'/)
```

In `frontend-vue/src/views/Home.structure.test.js`, assert Home normalization recognizes skipped and has styling:

```js
assert.match(source, /return 'skipped'/)
assert.match(source, /step-icon-skipped/)
assert.match(source, /normalizeStepStatus\(steps\[activeIdx\]\.status\) === 'processing'/)
```

- [ ] **Step 3: Run frontend tests and confirm failure**

Run:

```bash
cd frontend-vue && npm run test -- src/stores/chatPersistence.test.js src/services/api.structure.test.js src/views/Home.structure.test.js
```

Expected: FAIL because skipped currently falls back to processing.

- [ ] **Step 4: Implement skipped normalization**

In all three frontend normalizers, add skipped before success/error fallback:

```js
if (['skipped', 'skip', 'skipping'].includes(raw)) return 'skipped'
```

Files:

- `frontend-vue/src/services/api.js::normalizeStepStatus`
- `frontend-vue/src/stores/chatStore.js::normalizeStepStatus`
- `frontend-vue/src/views/Home.vue::normalizeStepStatus`

- [ ] **Step 5: Make skipped terminal in Home summaries**

Update `getStepOverview(msg)` in `Home.vue`:

```js
const skipped = steps.filter((step) => normalizeStepStatus(step?.status) === 'skipped').length
if (error > 0) return `失败 ${error} · 完成 ${success}${skipped ? ` · 跳过 ${skipped}` : ''}`
if (processing > 0) return `进行中 ${processing} · 完成 ${success}${skipped ? ` · 跳过 ${skipped}` : ''}`
return skipped > 0 ? `已完成 ${success} · 跳过 ${skipped}` : `已完成 ${success}`
```

Add CSS near existing step icon colors:

```css
.step-icon-skipped {
  color: #64748b;
}
```

Update the `done` finalization branch in `Home.vue` so the active step is only forced to success when it is still processing:

```js
if (eventState.activeStepKey) {
  const activeIdx = steps.findIndex((step) => step.step === eventState.activeStepKey)
  if (activeIdx >= 0 && normalizeStepStatus(steps[activeIdx].status) === 'processing') {
    steps[activeIdx] = { ...steps[activeIdx], status: 'success', updatedAt: new Date().toISOString() }
  }
}
```

Leave the existing loop that converts remaining processing steps to success, because it already checks `normalizeStepStatus(step.status) === 'processing'`. With skipped normalization in place, skipped steps will not match that branch.

- [ ] **Step 6: Run frontend skipped tests**

Run:

```bash
cd frontend-vue && npm run test -- src/stores/chatPersistence.test.js src/services/api.structure.test.js src/views/Home.structure.test.js
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend-vue/src/services/api.js frontend-vue/src/services/api.structure.test.js frontend-vue/src/stores/chatStore.js frontend-vue/src/stores/chatPersistence.test.js frontend-vue/src/views/Home.vue frontend-vue/src/views/Home.structure.test.js
git commit -m "fix: preserve skipped QA process steps"
```

## Task 3: Gateway Terminal Timing Propagation

**Files:**
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/conversation_persistence.py`
- Modify: `gateway/tests/test_task_api.py`

- [ ] **Step 1: Add failing direct terminal write assertion**

In `gateway/tests/test_task_api.py::test_admission_worker_executes_task_stream_updates_progress_and_finalizes_quota`, change the upstream done frame to include timings:

```python
b'data: {"type":"done","final_answer":"hello","query_mode":"fast","route":"kb_qa","trace_id":"req_worker_stream","timings":{"stage1":1000,"stage2":2000}}\n\n'
```

After existing call assertions, add:

```python
terminal_calls = [payload for path, payload in calls if path == "/internal/conversations/91/tasks/req_worker_stream/assistant-terminal"]
assert terminal_calls
assert terminal_calls[-1]["timings"] == {"stage1": 1000, "stage2": 2000}
```

- [ ] **Step 2: Add failing terminal sync fallback assertion**

In `test_admission_worker_marks_terminal_sync_pending_when_post_done_side_effect_fails`, change the upstream done frame:

```python
b'data: {"type":"done","final_answer":"ok","query_mode":"fast","route":"kb_qa","trace_id":"side-effect","timings":{"stage1":111}}\n\n'
```

Add:

```python
assert stored["terminal_sync_payload"]["timings"] == {"stage1": 111}
```

- [ ] **Step 3: Add failing terminal sync retry assertion**

In `test_get_task_retries_completed_terminal_sync_after_post_done_failure`, change the upstream done frame:

```python
b'data: {"type":"done","final_answer":"ok","query_mode":"fast","route":"kb_qa","trace_id":"terminal-repair","timings":{"stage4":444}}\n\n'
```

Add:

```python
assert terminal_calls[-1]["timings"] == {"stage4": 444}
```

Also extend `test_get_task_reconciles_completed_terminal_sync_with_success_quota` queue payload:

```python
"timings": {"stage2": 222},
```

and assert:

```python
assert calls[0][1]["timings"] == {"stage2": 222}
```

- [ ] **Step 4: Run gateway tests and confirm failure**

Run:

```bash
pytest gateway/tests/test_task_api.py::test_admission_worker_executes_task_stream_updates_progress_and_finalizes_quota gateway/tests/test_task_api.py::test_admission_worker_marks_terminal_sync_pending_when_post_done_side_effect_fails gateway/tests/test_task_api.py::test_get_task_retries_completed_terminal_sync_after_post_done_failure gateway/tests/test_task_api.py::test_get_task_reconciles_completed_terminal_sync_with_success_quota -q
```

Expected: FAIL because terminal payloads do not include timings.

- [ ] **Step 5: Add optional timings to gateway conversation persistence**

In `gateway/app/services/conversation_persistence.py::terminal_task_assistant`, add the parameter:

```python
timings: dict[str, Any] | None = None,
```

Add to payload:

```python
"timings": dict(timings or {}),
```

- [ ] **Step 6: Capture and forward timings in `qa_tasks.py`**

In the `event_type == "done"` branch, after `answer_text`:

```python
done_timings = payload.get("timings")
if not isinstance(done_timings, dict):
    done_timings = {}
```

Pass `timings=dict(done_timings)` to:

- the immediate `terminal_task_assistant()` call;
- both `_queue_terminal_sync_update()` calls in the completed branch.

Update `_queue_terminal_sync_update()` signature:

```python
timings: dict[str, Any] | None = None,
```

and add to `terminal_sync_payload`:

```python
"timings": dict(timings or {}),
```

In `_sync_terminal_record()`, pass:

```python
timings=dict(sync_payload.get("timings") or {}),
```

to `terminal_task_assistant()`.

- [ ] **Step 7: Leave failure/cancel paths timing-safe**

For failure, cancel, expired terminalization, pass no timings or `{}` unless the local code already has a valid terminal timing snapshot. Do not block terminalization on missing timings.

- [ ] **Step 8: Run gateway tests**

Run:

```bash
pytest gateway/tests/test_task_api.py::test_admission_worker_executes_task_stream_updates_progress_and_finalizes_quota gateway/tests/test_task_api.py::test_admission_worker_marks_terminal_sync_pending_when_post_done_side_effect_fails gateway/tests/test_task_api.py::test_get_task_retries_completed_terminal_sync_after_post_done_failure gateway/tests/test_task_api.py::test_get_task_reconciles_completed_terminal_sync_with_success_quota -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add gateway/app/services/qa_tasks.py gateway/app/services/conversation_persistence.py gateway/tests/test_task_api.py
git commit -m "fix: preserve task terminal timings in gateway"
```

## Task 4: Public-Service Task Terminal Timing Persistence

**Files:**
- Modify: `public-service/backend/app/modules/conversation/task_schemas.py`
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
- Modify: `public-service/backend/app/modules/conversation/service.py`
- Modify: `public-service/backend/tests/test_conversation_task_runtime.py`
- Modify: `public-service/backend/tests/test_conversation_authority_api.py`

- [ ] **Step 1: Add failing service persistence assertions**

In `public-service/backend/tests/test_conversation_task_runtime.py::test_task_assistant_terminal_clears_active_task_and_finalizes_same_placeholder`, call terminal with timings:

```python
terminal = service.terminal_authority_task_assistant(
    user_id=7,
    conversation_id=conversation_id,
    task_id="task_004",
    terminal_status="expired",
    last_seq=7,
    timings={"stage1": 1000},
)
```

Add:

```python
assert assistant["metadata"]["timings"] == {"stage1": 1000}
```

In `test_task_assistant_conflicting_second_terminal_does_not_override_first_terminal`, use different timings in the first and second terminal calls and assert the first terminal timings remain in the message metadata.

- [ ] **Step 2: Add failing internal API assertion**

In `public-service/backend/tests/test_conversation_authority_api.py::test_internal_task_terminal_logs_task_id`, add request timings:

```python
"timings": {"stage4": 444},
```

After the response, fetch conversation detail through the harness service and assert:

```python
detail = service.get_conversation_detail(user_id=7, conversation_id=conversation_id)
assistant = detail["data"]["messages"][-1]
assert assistant["metadata"]["timings"] == {"stage4": 444}
```

- [ ] **Step 3: Run public-service tests and confirm failure**

Run:

```bash
pytest public-service/backend/tests/test_conversation_task_runtime.py::test_task_assistant_terminal_clears_active_task_and_finalizes_same_placeholder public-service/backend/tests/test_conversation_task_runtime.py::test_task_assistant_conflicting_second_terminal_does_not_override_first_terminal public-service/backend/tests/test_conversation_authority_api.py::test_internal_task_terminal_logs_task_id -q
```

Expected: FAIL because terminal task schema/service do not accept timings.

- [ ] **Step 4: Add timings to task terminal schema**

In `AuthorityTaskAssistantTerminalRequest`, add:

```python
timings: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 5: Forward timings through internal API**

In `internal_api.py::terminal_task_assistant()`, pass:

```python
timings=payload.timings,
```

to `terminal_authority_task_assistant()`.

- [ ] **Step 6: Store timings in service terminalization**

In `service.py::terminal_authority_task_assistant()`, add parameter:

```python
timings: dict[str, Any] | None = None,
```

After steps are stored:

```python
timings_payload = dict(timings or {})
if timings_payload:
    metadata["timings"] = timings_payload
```

Do not remove existing timings on empty repeated terminal requests. Existing early return for already-terminal messages should remain unchanged so first terminal write wins.

- [ ] **Step 7: Run public-service tests**

Run:

```bash
pytest public-service/backend/tests/test_conversation_task_runtime.py::test_task_assistant_terminal_clears_active_task_and_finalizes_same_placeholder public-service/backend/tests/test_conversation_task_runtime.py::test_task_assistant_conflicting_second_terminal_does_not_override_first_terminal public-service/backend/tests/test_conversation_authority_api.py::test_internal_task_terminal_logs_task_id -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add public-service/backend/app/modules/conversation/task_schemas.py public-service/backend/app/modules/conversation/internal_api.py public-service/backend/app/modules/conversation/service.py public-service/backend/tests/test_conversation_task_runtime.py public-service/backend/tests/test_conversation_authority_api.py
git commit -m "fix: persist task terminal timings in public service"
```

## Task 5: Cross-Service Verification

**Files:**
- No production files unless preceding tasks reveal a missed integration bug.

- [ ] **Step 1: Run focused frontend tests**

Run:

```bash
cd frontend-vue && npm run test -- src/utils/stageTimings.test.js src/utils/routingStatus.test.js src/stores/chatPersistence.test.js src/services/api.structure.test.js src/views/Home.structure.test.js
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd frontend-vue && npm run build
```

Expected: PASS and Vite emits a production build.

- [ ] **Step 3: Run focused gateway tests**

Run:

```bash
pytest gateway/tests/test_task_api.py::test_admission_worker_executes_task_stream_updates_progress_and_finalizes_quota gateway/tests/test_task_api.py::test_admission_worker_marks_terminal_sync_pending_when_post_done_side_effect_fails gateway/tests/test_task_api.py::test_get_task_retries_completed_terminal_sync_after_post_done_failure gateway/tests/test_task_api.py::test_get_task_reconciles_completed_terminal_sync_with_success_quota -q
```

Expected: PASS.

- [ ] **Step 4: Run focused public-service tests**

Run:

```bash
pytest public-service/backend/tests/test_conversation_task_runtime.py::test_task_assistant_terminal_clears_active_task_and_finalizes_same_placeholder public-service/backend/tests/test_conversation_task_runtime.py::test_task_assistant_conflicting_second_terminal_does_not_override_first_terminal public-service/backend/tests/test_conversation_authority_api.py::test_internal_task_terminal_logs_task_id -q
```

Expected: PASS.

- [ ] **Step 5: Inspect git diff**

Run:

```bash
git diff --stat
git diff -- frontend-vue/src gateway/app public-service/backend/app gateway/tests public-service/backend/tests
```

Expected: only files listed in this plan changed, with no generated build output tracked.

- [ ] **Step 6: Commit verification-only fixes if needed**

If Task 5 required any fixes, add only the files changed by those verification fixes. Example:

```bash
git add frontend-vue/src/utils/stageTimings.js
git commit -m "test: verify QA step timing reliability"
```

If no fixes were needed, do not create an empty commit.
