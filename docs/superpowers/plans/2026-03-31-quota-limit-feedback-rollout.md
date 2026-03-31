# Quota Limit Feedback Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make quota-related failures render as unified, structured feedback across chat ask flows and document-assist surfaces, while keeping `public-service` as the quota authority and limiting backend changes to payload pass-through only.

**Architecture:** `public-service` remains the source of quota failure data. `gateway` only normalizes the streaming precheck error surface so sync JSON and SSE error frames expose the same top-level `code / error / message / trace_id / data` shape. `frontend-vue` adds one quota-error formatter plus one reusable `QuotaLimitCard` component, then mounts that shared path in `Home.vue` and `PdfReader.vue`. For view-PDF specifically, the frontend changes from “check then iframe URL” to a single authenticated `GET /api/view_pdf/{doi}` load that yields either a PDF blob or a JSON error.

**Tech Stack:** FastAPI, Vue 3, Vite, gateway proxy layer, existing quota normalization helpers, existing frontend unit tests and structure tests.

---

## File Map

### Gateway

- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_qa_proxy.py`

### Frontend Shared Quota UI

- Create: `frontend-vue/src/services/quota-error-formatting.js`
- Create: `frontend-vue/src/services/quota-error-formatting.test.js`
- Create: `frontend-vue/src/components/QuotaLimitCard.vue`
- Create: `frontend-vue/src/components/QuotaLimitCard.structure.test.js`
- Modify: `frontend-vue/src/utils/routingStatus.js`
- Modify: `frontend-vue/src/utils/routingStatus.test.js`

### Frontend Chat Surface

- Modify: `frontend-vue/src/views/Home.vue`
- Test: `frontend-vue/src/views/Home.structure.test.js`

### Frontend Document-Assist Surfaces

- Modify: `frontend-vue/src/api/literature.js`
- Create: `frontend-vue/src/api/literature.test.js`
- Modify: `frontend-vue/src/components/PdfReader.vue`
- Modify: `frontend-vue/src/components/PdfReader.structure.test.js`
- Create: `frontend-vue/src/utils/pdfReaderOpenFlow.js`
- Create: `frontend-vue/src/utils/pdfReaderOpenFlow.test.js`

### Existing Reuse Targets

- Reference: `frontend-vue/src/services/quota-normalization.js`
- Reference: `frontend-vue/src/views/UserProfile.vue`
- Reference: `docs/superpowers/specs/2026-03-31-quota-limit-feedback-design.md`

---

## Lock Decisions

1. `public-service` remains the only quota authority; this rollout does not change quota calculation or config semantics.
2. `gateway` only normalizes the streaming precheck error payload; it must not start generating final Chinese UX copy.
3. Frontend formatter consumes top-level `data` as the quota-detail source for both sync JSON and SSE error frames.
4. `QUOTA_EXCEEDED` maps to `quota_exceeded`; blocking system-side quota failures map to `system_unavailable`.
5. The system-side bucket includes current known blocking codes:
   - `QUOTA_CONFIG_MISSING`
   - `QUOTA_INTERNAL_UNAVAILABLE`
   - `QUOTA_INTERNAL_INVALID_RESPONSE`
   - `DB_UNAVAILABLE`
   - `QUOTA_LOCK_TIMEOUT`
   - `QUOTA_LOCK_UNAVAILABLE`
   - `QUOTA_CHECK_ERROR`
   - `QUOTA_GRANT_ERROR`
6. View-PDF must move to a single authenticated `GET /api/view_pdf/{doi}` request:
   - PDF success -> convert to `Blob URL` and mount in iframe
   - JSON failure -> render quota card or ordinary error
   - do not do `check_pdf` + second iframe request in the main open flow
7. This rollout covers:
   - chat ask failures
   - `PdfReader` summary/translation/view-PDF failures
   - active document-assist UI only; dormant reference-panel code stays out of this rollout
8. Finalize-stage soft warnings such as `QUOTA_INCREMENT_ERROR` are out of scope for this blocking-failure UX rollout.

---

## Rollout Order

1. Gateway payload alignment
2. Frontend quota formatter
3. Shared card component
4. Chat surface integration
5. Document-assist integration
6. Verification and review closure

Reason:

- The frontend formatter should target one stable payload shape before it is wired into multiple surfaces.
- The card component should exist before any surface switches away from ad-hoc strings.
- View-PDF needs its transport change designed and implemented before `PdfReader` can claim quota-card parity with summary/translation.

---

### Task 1: Align Gateway Sync/Stream Quota Failure Payloads

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [ ] **Step 1: Write or extend failing gateway tests for stream precheck errors**

Cover these cases:
- stream precheck `QUOTA_EXCEEDED` carries top-level `data`
- stream precheck `QUOTA_CONFIG_MISSING` carries top-level `data`
- stream precheck `QUOTA_INTERNAL_UNAVAILABLE` still exposes `code / error / message / trace_id`
- route metadata frame still arrives before the error frame

- [ ] **Step 2: Run the focused gateway tests to confirm the gap**

Run:

```bash
cd gateway && pytest tests/test_qa_proxy.py -k quota -q
```

Expected: failure because `_quota_precheck_error_stream()` does not yet emit top-level `data`.

- [ ] **Step 3: Implement the minimal SSE payload alignment**

In `gateway/app/routers/qa.py`:
- keep existing metadata frame
- keep existing `code / error / message / trace_id`
- add top-level `data` from the precheck payload when present
- do not rewrite sync JSON precheck behavior

- [ ] **Step 4: Re-run the focused gateway tests**

Run:

```bash
cd gateway && pytest tests/test_qa_proxy.py -k quota -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/app/routers/qa.py gateway/tests/test_qa_proxy.py
git commit -m "fix: align gateway quota precheck error payloads"
```

---

### Task 2: Add a Shared Frontend Quota Error Formatter

**Files:**
- Create: `frontend-vue/src/services/quota-error-formatting.js`
- Create: `frontend-vue/src/services/quota-error-formatting.test.js`
- Modify: `frontend-vue/src/utils/routingStatus.js`
- Modify: `frontend-vue/src/utils/routingStatus.test.js`

- [ ] **Step 1: Write failing formatter tests**

Cover:
- `QUOTA_EXCEEDED` -> `quota_exceeded`
- each blocking system code -> `system_unavailable`
- formatter reads quota detail from top-level `data`
- formatter falls back gracefully when `data` is missing
- chat feature title resolves to `普通问答` vs `文件问答`
- document-assist feature titles can be injected explicitly

- [ ] **Step 2: Run the focused formatter tests to confirm failure**

Run:

```bash
cd frontend-vue && npm test -- src/services/quota-error-formatting.test.js src/utils/routingStatus.test.js
```

Expected: FAIL because the formatter module does not exist yet and `routingStatus` does not recognize quota cards.

- [ ] **Step 3: Implement the formatter and minimal routing-status integration**

Rules:
- reuse helpers from `quota-normalization.js`
- produce one normalized card model
- do not hardcode copy inside `Home.vue`
- keep non-quota routing errors on the existing markdown path

- [ ] **Step 4: Re-run the focused frontend tests**

Run:

```bash
cd frontend-vue && npm test -- src/services/quota-error-formatting.test.js src/utils/routingStatus.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/services/quota-error-formatting.js frontend-vue/src/services/quota-error-formatting.test.js frontend-vue/src/utils/routingStatus.js frontend-vue/src/utils/routingStatus.test.js
git commit -m "feat: add shared quota error formatting"
```

---

### Task 3: Build the Shared `QuotaLimitCard` Component

**Files:**
- Create: `frontend-vue/src/components/QuotaLimitCard.vue`
- Create: `frontend-vue/src/components/QuotaLimitCard.structure.test.js`

- [ ] **Step 1: Write a structure test for the card component**

Assert the component exposes:
- headline area
- description area
- usage summary slot or block
- optional windows list region
- action button or link to `/profile`
- variant class for `quota_exceeded` vs `system_unavailable`

- [ ] **Step 2: Run the structure test to confirm failure**

Run:

```bash
cd frontend-vue && npm test -- src/components/QuotaLimitCard.structure.test.js
```

Expected: FAIL because the component does not exist yet.

- [ ] **Step 3: Implement the component**

Constraints:
- keep styling self-contained
- support empty `usageSummary` / `windows`
- keep the component presentation-only
- no API calls inside the component

- [ ] **Step 4: Re-run the structure test**

Run:

```bash
cd frontend-vue && npm test -- src/components/QuotaLimitCard.structure.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/components/QuotaLimitCard.vue frontend-vue/src/components/QuotaLimitCard.structure.test.js
git commit -m "feat: add reusable quota limit card"
```

---

### Task 4: Integrate the Card into Chat Ask Failures

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/views/Home.structure.test.js`

- [ ] **Step 1: Add failing chat-structure coverage**

Cover:
- quota failures render the shared card instead of markdown text
- non-quota routing errors still render the existing markdown block
- existing route metadata merge path remains intact

- [ ] **Step 2: Run the focused chat tests**

Run:

```bash
cd frontend-vue && npm test -- src/views/Home.structure.test.js src/utils/routingStatus.test.js
```

Expected: FAIL because `Home.vue` does not yet mount `QuotaLimitCard`.

- [ ] **Step 3: Implement chat integration**

In `Home.vue`:
- preserve the current SSE and sync metadata merge logic
- detect when the normalized error result is a quota card model
- render `QuotaLimitCard` inline for that message
- keep existing markdown render path for all other failures

- [ ] **Step 4: Re-run focused chat tests**

Run:

```bash
cd frontend-vue && npm test -- src/views/Home.structure.test.js src/utils/routingStatus.test.js
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/views/Home.structure.test.js
git commit -m "feat: show quota limit cards in chat failures"
```

---

### Task 5: Integrate Document-Assist Surfaces

**Files:**
- Modify: `frontend-vue/src/api/literature.js`
- Create: `frontend-vue/src/api/literature.test.js`
- Modify: `frontend-vue/src/components/PdfReader.vue`
- Modify: `frontend-vue/src/components/PdfReader.structure.test.js`
- Create: `frontend-vue/src/utils/pdfReaderOpenFlow.js`
- Create: `frontend-vue/src/utils/pdfReaderOpenFlow.test.js`

- [ ] **Step 1: Add failing tests for `view_pdf` transport and `PdfReader` behavior**

Cover:
- `PdfReader` can render the shared quota card for summary/translation/view-PDF failures
- view-PDF open flow no longer depends on `check_pdf` as the primary gate
- `api/literature.js` helper distinguishes PDF success from JSON failure
- blob URLs are revoked on close or replacement
- JSON error results can be mapped into quota-card state instead of generic fallback

- [ ] **Step 2: Run the focused tests to confirm failure**

Run:

```bash
cd frontend-vue && npm test -- src/api/literature.test.js src/utils/pdfReaderOpenFlow.test.js src/components/PdfReader.structure.test.js
```

Expected: FAIL because the helper does not exist yet and `PdfReader` still uses string-based quota errors and the old open flow.

- [ ] **Step 3: Implement the `view_pdf` transport change**

In `frontend-vue/src/api/literature.js`:
- add a helper that performs authenticated `GET /api/view_pdf/{doi}`
- return either:
  - `{ ok: true, blobUrl, contentType }`
  - or `{ ok: false, errorPayload }`
- centralize auth token handling here rather than duplicating it inside `PdfReader`

- [ ] **Step 4: Extract a pure `PdfReader` open-flow helper**

In `frontend-vue/src/utils/pdfReaderOpenFlow.js`:
- accept the latest `view_pdf` load result and previous blob URL
- decide whether the next UI state is `pdf_ready`, `quota_error`, or ordinary error
- revoke replaced blob URLs via injectable `revokeObjectURL`
- expose a close/reset path that revokes the current blob URL

- [ ] **Step 5: Implement `PdfReader` quota-card integration**

In `PdfReader.vue`:
- replace `buildQuotaErrorMessage()` string-only flow with normalized card flow
- switch openReader to single-request PDF loading
- delegate branching/revoke behavior to `pdfReaderOpenFlow.js`
- preserve existing non-quota “PDF 不存在” fallback for real missing-file errors
- ensure blob URLs are revoked on close / replacement

- [ ] **Step 6: Re-run focused document-assist tests**

Run:

```bash
cd frontend-vue && npm test -- src/api/literature.test.js src/utils/pdfReaderOpenFlow.test.js src/components/PdfReader.structure.test.js src/services/quota-error-formatting.test.js
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend-vue/src/api/literature.js frontend-vue/src/api/literature.test.js frontend-vue/src/utils/pdfReaderOpenFlow.js frontend-vue/src/utils/pdfReaderOpenFlow.test.js frontend-vue/src/components/PdfReader.vue frontend-vue/src/components/PdfReader.structure.test.js
git commit -m "feat: unify document-assist quota limit feedback"
```

---

### Task 6: Full Verification and Rollout Closure

**Files:**
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `frontend-vue/src/services/quota-error-formatting.test.js`
- Test: `frontend-vue/src/utils/routingStatus.test.js`
- Test: `frontend-vue/src/components/QuotaLimitCard.structure.test.js`
- Test: `frontend-vue/src/views/Home.structure.test.js`
- Test: `frontend-vue/src/components/PdfReader.structure.test.js`
- Test: `frontend-vue/src/api/literature.test.js`
- Test: `frontend-vue/src/utils/pdfReaderOpenFlow.test.js`

- [ ] **Step 1: Run the full targeted backend/frontend verification set**

Run:

```bash
cd gateway && pytest tests/test_qa_proxy.py -q
cd /home/cqy/worktrees/highThinking/frontend-vue && npm test -- src/services/quota-error-formatting.test.js src/utils/routingStatus.test.js src/components/QuotaLimitCard.structure.test.js src/views/Home.structure.test.js src/components/PdfReader.structure.test.js src/api/literature.test.js src/utils/pdfReaderOpenFlow.test.js
```

Expected: PASS

- [ ] **Step 2: Run the frontend production build**

Run:

```bash
cd frontend-vue && npm run build
```

Expected: build succeeds without introducing unresolved imports or Vue template errors.

- [ ] **Step 3: Manual regression checklist**

Verify:
- chat `QUOTA_EXCEEDED` shows the card
- chat system-side quota failure shows the system card
- summary/translation failures in `PdfReader` show the same visual component
- view-PDF success still opens the document
- view-PDF quota failure shows the card instead of a blank iframe

- [ ] **Step 4: Commit**

```bash
git add gateway/tests/test_qa_proxy.py frontend-vue/src/services/quota-error-formatting.test.js frontend-vue/src/utils/routingStatus.test.js frontend-vue/src/components/QuotaLimitCard.structure.test.js frontend-vue/src/views/Home.structure.test.js frontend-vue/src/components/PdfReader.structure.test.js frontend-vue/src/api/literature.test.js frontend-vue/src/utils/pdfReaderOpenFlow.test.js
git commit -m "test: verify quota limit feedback rollout"
```

---

## Review Gates

1. After Task 1, request review focused on gateway payload shape only.
2. After Tasks 2-3, request review focused on formatter contract and card API only.
3. After Tasks 4-5, request review focused on chat/PdfReader integration and regression risk.
4. Do not start Task 6 until the Task 4-5 review is clear of blocking issues.

---

## Completion Criteria

This rollout is complete only when all of the following are true:

1. Sync JSON and SSE precheck quota failures expose one frontend-consumable `data` shape.
2. Chat ask failures render the shared quota card for blocking quota failures.
3. `PdfReader` uses single-request `GET view_pdf` loading and renders the same quota card on failure.
4. Targeted tests pass and `npm run build` succeeds.
