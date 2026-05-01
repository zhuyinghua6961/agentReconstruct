# Frontend QA Stage Timing Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each QA backend stage timing in the Vue chat UI, with a compact summary visible in the existing "处理过程" header and a detailed stage list when expanded.

**Architecture:** Keep backend contracts unchanged and normalize existing `timings` payloads on the frontend. Add a focused timing utility for unit conversion, labels, summaries, and message extraction; then use it from message normalization and `Home.vue` rendering.

**Tech Stack:** Vue 3, Vite, Pinia, Node `node:test`, existing SSE and conversation persistence payloads.

---

## Requirements

- Use existing backend timing data only:
  - `fastQA` and `patent` KB flows emit `timings` with `stage1`, `stage2`, `stage25`, `stage3`, `stage4` in milliseconds.
  - `highThinkingQA` emits `timings` with `step*` and `total` in seconds.
- Default UI should be lightweight:
  - Collapsed and expanded "处理过程" header shows total duration and slowest stage when timings exist.
  - Expanded panel shows per-stage rows with stage label, short description, and formatted duration.
- Preserve historical messages:
  - Read timings from `message.timings`, `message.metadata.timings`, and `message.metadata.stage_timings_ms`.
  - Normalize conversation detail data and store data so new and persisted messages behave consistently.
- Do not change gateway or backend protocol.
- Do not add heavy debug panels. Supplemental info should be limited to route/query mode/trace if already present.
- Do not commit during execution unless the user explicitly asks for a commit.

## File Map

- Create: `frontend-vue/src/utils/stageTimings.js`
  - Owns timing extraction, unit conversion, label mapping, formatting, ordering, summary construction.
- Create: `frontend-vue/src/utils/stageTimings.test.js`
  - Unit tests for patent/fastQA millisecond timings, highThinking second timings, unknown keys, and message extraction.
- Modify: `frontend-vue/src/services/api.js`
  - During `normalizeMessage`, preserve timings in `metadata.timings` and expose normalized raw timings as `message.timings`.
- Modify: `frontend-vue/src/stores/chatStore.js`
  - Mirror the same normalization for locally restored or live-updated messages.
- Modify: `frontend-vue/src/views/Home.vue`
  - Import timing helpers.
  - Render the process panel when either steps or timings exist.
  - Render compact timing summary in `.steps-header`.
  - Render detailed timing rows in expanded `.processing-steps`.
  - Render optional tiny context line for trace/mode when useful.
- Modify: `frontend-vue/src/services/api.structure.test.js`
  - Assert API normalization preserves `timings`.
- Modify: `frontend-vue/src/stores/chatPersistence.test.js` or add focused store test if cleaner.
  - Assert store-normalized messages retain timings in metadata and top-level.
- Modify: `frontend-vue/src/views/Home.structure.test.js`
  - Assert Home imports timing helper and contains timing summary/detail CSS hooks.

## Data Model

`normalizeStageTimings(rawTimings, context)` returns:

```js
{
  hasTimings: true,
  family: 'generation-stage' | 'thinking-step' | 'generic',
  totalMs: 78811.4,
  totalLabel: '1m18.8s',
  slowest: {
    key: 'stage4',
    label: '阶段四',
    description: '答案生成',
    durationMs: 50524,
    durationLabel: '50.5s',
  },
  entries: [
    {
      key: 'stage1',
      label: '阶段一',
      description: '问题规划与检索词生成',
      durationMs: 12054,
      durationLabel: '12.1s',
      displayOrder: 10,
    },
  ],
}
```

`getMessageStageTimingModel(message)` reads raw timings in priority order:

1. `message.timings`
2. `message.metadata.timings`
3. `message.metadata.stage_timings_ms`

The helper must ignore non-numeric, negative, `NaN`, and infinite values.

## Stage Label Rules

Generation-stage timings, used by fastQA and patent:

| Key | Label | Description | Unit |
| --- | --- | --- | --- |
| `stage1` | 阶段一 | 问题规划与检索词生成 | milliseconds |
| `stage2` | 阶段二 | 向量检索与重排 | milliseconds |
| `stage25` | 阶段二点五 | 证据筛选与上下文压缩 | milliseconds |
| `stage3` | 阶段三 | 提示词构建 | milliseconds |
| `stage4` | 阶段四 | 答案生成 | milliseconds |

Thinking-step timings, used by highThinkingQA:

| Key | Label | Description | Unit |
| --- | --- | --- | --- |
| `step1_parallel` | Step 1 | 直答与拆解并行 | seconds |
| `step2_pre_answer` | Step 2 | 初步回答 | seconds |
| `step3_retrieval` | Step 3 | 检索补充 | seconds |
| `step4_synthesis` | Step 4 | 综合生成 | seconds |
| `step5_check_revise` | Step 5 | 检查与修订 | seconds |
| `step5_check_total` | 检查累计 | 质量检查累计 | seconds |
| `step5_revise_total` | 修订累计 | 修订生成累计 | seconds |
| `total` | 总耗时 | 全流程总耗时 | seconds |

Generic fallback:

- Keys ending with `_ms` are milliseconds.
- Keys ending with `_s` are seconds.
- Unknown numeric keys default to milliseconds and use the raw key as label.
- If a payload has explicit `total`, use it as total; otherwise sum visible entries.

## Task 1: Add Timing Utility and Tests

**Files:**
- Create: `frontend-vue/src/utils/stageTimings.js`
- Create: `frontend-vue/src/utils/stageTimings.test.js`

- [ ] **Step 1: Write failing tests for generation-stage timings**

Test cases:

```js
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatStageDuration,
  getMessageStageTimingModel,
  normalizeStageTimings,
} from './stageTimings.js'

test('normalizes patent and fastQA stage timings as milliseconds', () => {
  const model = normalizeStageTimings({
    stage1: 12054,
    stage2: 16210,
    stage25: 8.9,
    stage3: 24.5,
    stage4: 50524,
  })

  assert.equal(model.hasTimings, true)
  assert.equal(model.family, 'generation-stage')
  assert.equal(model.totalLabel, '1m18.8s')
  assert.equal(model.slowest.key, 'stage4')
  assert.equal(model.slowest.durationLabel, '50.5s')
  assert.deepEqual(model.entries.map((entry) => entry.key), ['stage1', 'stage2', 'stage25', 'stage3', 'stage4'])
  assert.equal(model.entries[1].description, '向量检索与重排')
})
```

- [ ] **Step 2: Write failing tests for highThinking timings**

Test cases:

```js
test('normalizes highThinking step timings as seconds', () => {
  const model = normalizeStageTimings({
    step1_parallel: 3.2,
    step2_pre_answer: 1.1,
    step3_retrieval: 7.8,
    step4_synthesis: 11.4,
    step5_check_revise: 2.5,
    total: 26.0,
  })

  assert.equal(model.family, 'thinking-step')
  assert.equal(model.totalMs, 26000)
  assert.equal(model.totalLabel, '26.0s')
  assert.equal(model.slowest.key, 'step4_synthesis')
  assert.equal(model.entries.find((entry) => entry.key === 'step3_retrieval').description, '检索补充')
})
```

- [ ] **Step 3: Write failing tests for formatting and extraction**

Test cases:

```js
test('formats durations for ms, seconds, and minutes', () => {
  assert.equal(formatStageDuration(8.9), '9ms')
  assert.equal(formatStageDuration(1200), '1.2s')
  assert.equal(formatStageDuration(78811), '1m18.8s')
})

test('extracts timings from message metadata fallbacks', () => {
  const model = getMessageStageTimingModel({
    metadata: {
      stage_timings_ms: { stage2: 1000 },
    },
  })

  assert.equal(model.hasTimings, true)
  assert.equal(model.entries[0].durationLabel, '1.0s')
})

test('ignores invalid timing values', () => {
  const model = normalizeStageTimings({
    stage1: 'bad',
    stage2: -1,
    stage3: Number.POSITIVE_INFINITY,
    stage4: 20,
  })

  assert.deepEqual(model.entries.map((entry) => entry.key), ['stage4'])
})
```

- [ ] **Step 4: Run tests and confirm failure**

Run: `cd frontend-vue && npm run test -- src/utils/stageTimings.test.js`

Expected: FAIL because `stageTimings.js` does not exist.

- [ ] **Step 5: Implement `stageTimings.js`**

Implementation notes:

- Keep the file dependency-free.
- Export `formatStageDuration`, `normalizeStageTimings`, `getMessageStageTimingModel`.
- Sort known stage keys by table order and unknown keys alphabetically after known keys.
- Exclude `total` from `slowest` comparison so the slowest stage is a real stage.
- Use one decimal place for seconds and minute-second labels.
- Return an empty stable model when there are no valid entries:

```js
{
  hasTimings: false,
  family: 'generic',
  totalMs: 0,
  totalLabel: '',
  slowest: null,
  entries: [],
}
```

- [ ] **Step 6: Run utility tests**

Run: `cd frontend-vue && npm run test -- src/utils/stageTimings.test.js`

Expected: PASS.

## Task 2: Preserve Timings Through Message Normalization

**Files:**
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/services/api.structure.test.js`
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/stores/chatPersistence.test.js`

- [ ] **Step 1: Add failing API normalization assertions**

In an existing normalize-message oriented test in `api.structure.test.js`, or a new focused test if direct helper access already exists, assert the source contains timing normalization logic:

```js
assert.match(source, /const timings = item\?\.timings \?\? metadata\?\.timings \?\? metadata\?\.stage_timings_ms/)
assert.match(source, /metadata\.timings = \{ \.\.\.timings \}/)
assert.match(source, /\.\.\.\(metadata\.timings \? \{ timings: metadata\.timings \} : \{\}\)/)
```

If `api.structure.test.js` already validates by source scanning, keep that pattern to avoid exposing private helpers solely for tests.

- [ ] **Step 2: Add failing store persistence assertion**

Add a message fixture with:

```js
metadata: {
  timings: { stage1: 10, stage2: 20 },
}
```

Assert the restored or prepared assistant message keeps:

```js
assert.deepEqual(message.metadata.timings, { stage1: 10, stage2: 20 })
assert.deepEqual(message.timings, { stage1: 10, stage2: 20 })
```

- [ ] **Step 3: Run targeted tests and confirm failure**

Run:

```bash
cd frontend-vue && npm run test -- src/services/api.structure.test.js src/stores/chatPersistence.test.js
```

Expected: FAIL because top-level `timings` is not normalized yet.

- [ ] **Step 4: Implement API normalization**

In `frontend-vue/src/services/api.js` `normalizeMessage()`:

```js
const timings = item?.timings ?? metadata?.timings ?? metadata?.stage_timings_ms
if (timings && typeof timings === 'object' && !Array.isArray(timings)) {
  metadata.timings = { ...timings }
}
```

In the returned message:

```js
...(metadata.timings ? { timings: metadata.timings } : {}),
```

Do not mutate the original `item.metadata` object.

- [ ] **Step 5: Implement store normalization**

Mirror the same logic in `frontend-vue/src/stores/chatStore.js` `normalizeMessage()`.

Use the same priority:

```js
const timings = message?.timings ?? metadata?.timings ?? metadata?.stage_timings_ms
```

Return top-level `timings` only when the normalized metadata has timings.

- [ ] **Step 6: Run targeted normalization tests**

Run:

```bash
cd frontend-vue && npm run test -- src/services/api.structure.test.js src/stores/chatPersistence.test.js
```

Expected: PASS.

## Task 3: Render Timing Summary and Details in Home.vue

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/views/Home.structure.test.js`

- [ ] **Step 1: Add failing Home structure assertions**

In `Home.structure.test.js`, add assertions that the source:

```js
assert.match(source, /getMessageStageTimingModel/)
assert.match(source, /getTimingContextLabel/)
assert.match(source, /hasProcessPanel/)
assert.match(source, /stage-timing-summary/)
assert.match(source, /stage-timing-list/)
assert.match(source, /stage-timing-duration/)
```

- [ ] **Step 2: Run Home structure test and confirm failure**

Run: `cd frontend-vue && npm run test -- src/views/Home.structure.test.js`

Expected: FAIL because timing UI hooks are absent.

- [ ] **Step 3: Import timing helper**

In `Home.vue`, import:

```js
import { getMessageStageTimingModel } from '../utils/stageTimings.js'
```

- [ ] **Step 4: Add view helper functions**

Add near existing step helpers:

```js
function getStageTimingModel(msg) {
  return getMessageStageTimingModel(msg)
}

function getStageTimingSummary(msg) {
  const model = getStageTimingModel(msg)
  if (!model.hasTimings) return ''
  if (model.slowest) {
    return `总耗时 ${model.totalLabel} · 最慢 ${model.slowest.label} ${model.slowest.durationLabel}`
  }
  return `总耗时 ${model.totalLabel}`
}

function getTimingContextLabel(msg) {
  const metadata = msg?.metadata && typeof msg.metadata === 'object' ? msg.metadata : {}
  const route = String(metadata.route || metadata.query_mode || msg?.queryMode || '').trim()
  const traceId = String(metadata.trace_id || msg?.traceId || '').trim()
  const parts = []
  if (route) parts.push(route)
  if (traceId) parts.push(`trace ${traceId}`)
  return parts.join(' · ')
}

function hasProcessPanel(msg) {
  const steps = Array.isArray(msg?.steps) ? msg.steps : []
  return steps.length > 0 || getStageTimingModel(msg).hasTimings
}
```

- [ ] **Step 5: Expand the process panel guard**

Change the existing panel guard from:

```vue
<div v-if="entry.message.steps && entry.message.steps.length > 0" class="steps-panel">
```

to:

```vue
<div v-if="hasProcessPanel(entry.message)" class="steps-panel">
```

This is required for historical or recovered messages that have timings but no explicit `steps`.

- [ ] **Step 6: Render compact summary in the steps header**

Inside `.steps-meta`, before or after existing overview:

```vue
<span v-if="getStageTimingSummary(entry.message)" class="stage-timing-summary">
  {{ getStageTimingSummary(entry.message) }}
</span>
```

Keep existing step overview and collapsed step summary intact.

- [ ] **Step 7: Render expanded timing list**

Inside expanded `.processing-steps`, after the `v-for` step list:

```vue
<div v-if="getStageTimingModel(entry.message).hasTimings" class="stage-timing-list">
  <div
    v-for="timing in getStageTimingModel(entry.message).entries"
    :key="timing.key"
    class="stage-timing-item"
  >
    <div class="stage-timing-main">
      <span class="stage-timing-label">{{ timing.label }}</span>
      <span v-if="timing.description" class="stage-timing-description">{{ timing.description }}</span>
    </div>
    <span class="stage-timing-duration">{{ timing.durationLabel }}</span>
  </div>
  <div v-if="getTimingContextLabel(entry.message)" class="stage-timing-context">
    {{ getTimingContextLabel(entry.message) }}
  </div>
</div>
```

If repeated helper calls are a concern during review, keep the simpler version first; Vue rendering cost is negligible compared with current message rendering, and this can be optimized later with a computed projection if needed.

- [ ] **Step 8: Add compact CSS**

Add styles near existing `.steps-*` styles:

```css
.stage-timing-summary {
  background: #f1f5f9;
  color: #334155;
  border: 1px solid rgba(148, 163, 184, 0.24);
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  white-space: nowrap;
}

.stage-timing-list {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(148, 163, 184, 0.18);
  display: grid;
  gap: 6px;
}

.stage-timing-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.stage-timing-main {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.stage-timing-label {
  color: #0f172a;
  font-weight: 600;
  white-space: nowrap;
}

.stage-timing-description,
.stage-timing-context {
  color: #64748b;
}

.stage-timing-description {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-timing-duration {
  color: #334155;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.stage-timing-context {
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

Add or adjust a mobile rule if existing responsive CSS causes header crowding:

```css
@media (max-width: 640px) {
  .steps-header {
    align-items: flex-start;
  }

  .steps-meta {
    flex-wrap: wrap;
  }
}
```

- [ ] **Step 9: Run Home structure test**

Run: `cd frontend-vue && npm run test -- src/views/Home.structure.test.js`

Expected: PASS.

## Task 4: Full Frontend Verification

**Files:**
- No new files unless failures require test updates.

- [ ] **Step 1: Run targeted timing and message tests**

Run:

```bash
cd frontend-vue && npm run test -- \
  src/utils/stageTimings.test.js \
  src/services/api.structure.test.js \
  src/stores/chatPersistence.test.js \
  src/views/Home.structure.test.js
```

Expected: PASS.

- [ ] **Step 2: Run full frontend test suite**

Run: `cd frontend-vue && npm run test`

Expected: PASS.

- [ ] **Step 3: Build frontend**

Run: `cd frontend-vue && npm run build`

Expected: PASS, Vite production build completes without template or CSS errors.

- [ ] **Step 4: Manual verification with existing backend**

Start frontend if needed:

```bash
cd frontend-vue && npm run dev
```

Use an existing patent/fastQA request and confirm:

- During streaming, the existing processing steps still update.
- After `done`, "处理过程" header shows `总耗时 ... · 最慢 ...`.
- Expanded panel shows `阶段一` through `阶段四` when those timing keys exist.
- Historical conversation reload still shows timing data.

## Review Checklist

- No backend files changed.
- No gateway files changed.
- Timing utility has deterministic unit rules; no magnitude-based guessing for known backend families.
- Invalid timing values are ignored.
- Existing step rendering remains intact when no timings are present.
- Header text does not overflow badly on narrow screens.
- Full frontend tests and build pass before final response.
