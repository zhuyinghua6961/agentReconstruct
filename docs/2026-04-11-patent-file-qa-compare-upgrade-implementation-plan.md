# Patent File-QA Compare Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current `Patent` multi-PDF compare answer contract with the approved five-section compare structure while preserving existing non-compare patent file-QA behavior.

**Architecture:** The compare upgrade stays inside the existing `patent` file-QA PDF path. Backend work changes compare prompting, compare eligibility bounds, compare fallback reshaping, and compare test contracts; frontend work remains on the shared Markdown rendering path and only verifies/render-tunes the new compare headings and lists. Streaming timing semantics stay unchanged: compare content may remain buffered, and the final compare-success step must still precede the first content chunk.

**Tech Stack:** Python, pytest, Vue 3, shared Markdown rendering in `frontend-vue`, Node test runner, Vite

---

## File Map

- Modify: [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
- Modify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Modify: [patent/server/patent/file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py)
- Modify: [patent/server/patent/executor.py](/home/cqy/worktrees/highThinking/patent/server/patent/executor.py)
- Test: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)
- Verify/Modify if needed: [frontend-vue/src/utils/index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js)
- Verify/Modify if needed: [frontend-vue/src/styles/main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css)
- Test: [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
- Test: [frontend-vue/tests/markdown-rendering.test.js](/home/cqy/worktrees/highThinking/frontend-vue/tests/markdown-rendering.test.js)
- Reference Spec: [docs/2026-04-11-patent-file-qa-compare-upgrade-spec.md](/home/cqy/worktrees/highThinking/docs/2026-04-11-patent-file-qa-compare-upgrade-spec.md)

## Task 1: Lock the New Compare Contract with Failing Tests

**Files:**
- Modify: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Modify: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Modify: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Rewrite compare prompt-contract assertions**

In [test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py):

- replace the old compare prompt assertions that expect `各自概要 / 相同点 / 差异点 / 总结` as the primary contract
- assert the new compare prompt requires:
  - `具体内容对比`
  - `研究方法差异`
  - `应用领域差异`
  - `相同点`
  - `总结`
- assert the prompt explicitly treats FastQA as style/reference only through the target structure, not by reintroducing the old Patent compare fallback
- add prompt-layer doc-count tests that:
  - `2 docs` requires the full rich compare structure
  - `3-4 docs` allows compact per-document bullets
  - `>4 docs` no longer promises the rich compare contract in prompt wording

- [ ] **Step 2: Rewrite route-level compare answer assertions**

In [test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py):

- replace compare assertions that still require `各自概要`
- add assertions that a repaired/normalized compare answer now contains the five target sections in order
- assert that two-document compare answers give each document a subheading inside:
  - `具体内容对比`
  - `研究方法差异`
  - `应用领域差异`
- add a failure assertion for `>4 docs`
- add a quality assertion that bad English/fragment compare output is not surfaced as the final compare answer

- [ ] **Step 3: Rewrite executor compare assertions without changing timing semantics**

In [test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py):

- keep the current requirement that compare success step is emitted before the first content chunk
- update final-answer assertions to the new five-section compare structure
- add assertions that compare still fails closed when:
  - only one PDF is readable
  - the model returns no usable compare answer
  - compare exceeds the rich-contract document bound
- defer `3-4 docs` compact compare success-shape assertions to Task 4, where the compare reshaper is actually replaced

- [ ] **Step 4: Run the targeted compare tests and confirm red state**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py -k "compare" -v
```

Expected: FAIL because the current compare prompt and compare fallback still emit the old contract.

- [ ] **Step 5: Commit the test-only red state**

```bash
git add patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "test: lock patent compare output contract"
```

## Task 2: Rework the Compare Prompt Contract

**Files:**
- Modify: [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
- Test: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)

- [ ] **Step 1: Replace compare prompt structure instructions**

In [pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py), update the `is_compare=True` prompt branch so it requests:

- `## 具体内容对比`
- `## 研究方法差异`
- `## 应用领域差异`
- `## 相同点`
- `## 总结`

It must also require:

- per-document subheadings in the first three sections for two-document compare
- compact but still per-document bullets for three- to four-document compare
- high-quality Chinese summary text
- no direct English abstract dumping
- explicit evidence-insufficient wording when a dimension is unsupported

- [ ] **Step 2: Encode compare document-count policy in prompt wording**

Still in [pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py):

- keep compare detection broad enough to support the existing route
- but make the prompt contract bounded:
  - rich compare for `2 docs`
  - compact compare for `3-4 docs`
  - no rich-contract promise for `>4 docs`

- [ ] **Step 3: Preserve non-compare prompt contracts**

Do not regress:

- single-document literature summary prompt
- non-summary single-PDF prompt
- hybrid non-compare PDF prompt

The compare prompt edits must remain isolated to the compare branch.

- [ ] **Step 4: Run prompt-focused tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_pdf_contract.py -k "compare or summary or non_compare" -v
```

Expected: compare tests pass with the new structure; existing non-compare contract tests remain green.

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/pdf_contract.py patent/tests/test_patent_pdf_contract.py
git commit -m "feat: update patent compare prompt contract"
```

## Task 3: Bound Compare Eligibility and Preserve Failure Semantics

**Files:**
- Modify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Modify if needed: [patent/server/patent/file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Add compare rich-contract bounds**

In [pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py), introduce a compare document-count guard for the new contract:

- allow rich compare for `2 docs`
- allow compact compare for `3-4 docs`
- if `>4 docs`, return explicit compare failure instead of pretending the rich compare template can still be satisfied

- [ ] **Step 2: Preserve existing failure-closed behavior**

Do not weaken:

- missing/unreadable document failure behavior
- compare context validation
- truncation-budget failure behavior

`>4 docs` should fit into the same explicit compare-unavailable family instead of silently degrading into an unbounded low-quality answer.

- [ ] **Step 3: Keep metadata and route behavior consistent**

Ensure the compare-bound failure path still returns stable metadata, including:

- `answer_mode`
- route/source-scope metadata
- compare error steps

- [ ] **Step 4: Run failure-mode and route tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_file_routes.py tests/test_patent_executor.py -k "compare and (unavailable or failure or bound or narrow)" -v
```

Expected: PASS with explicit failure for out-of-bound compare requests and no regression in existing compare-failure semantics. Compact `3-4 docs` success-shape assertions are intentionally deferred to Task 4.

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/server/patent/file_routes.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "feat: bound patent compare eligibility"
```

## Task 4: Replace Compare Fallback Shaping with the Approved Five-Section Contract

**Files:**
- Modify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Replace the old compare reshaper**

Refactor [`_ensure_compare_answer_structure()`](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py) so it no longer rewrites compare output into:

- `各自概要`
- `相同点`
- `差异点`
- `总结`

Instead, normalize compare output into:

- `具体内容对比`
- `研究方法差异`
- `应用领域差异`
- `相同点`
- `总结`

- [ ] **Step 2: Introduce evidence-driven per-document compare synthesis**

Do not keep using short raw snippet clipping as the final compare fallback.

For repaired compare output:

- derive per-document evidence from prepared PDF text
- synthesize Chinese bullets for:
  - core content
  - methods
  - application/domain context
- keep `相同点` and `总结` short

The implementation may stay deterministic and extractive internally, but the final emitted compare text must not be raw clipped English abstract fragments.

- [ ] **Step 3: Fail closed when quality cannot be repaired**

If the backend cannot regenerate a minimally acceptable Chinese compare answer from the extracted evidence:

- do not emit visibly bad English-fragment compare fallback
- return explicit compare failure instead

- [ ] **Step 4: Support compact compare mode for three to four documents**

Keep the same five top-level sections, but allow:

- shorter per-document bullets
- at most one or two bullets per document within `研究方法差异` and `应用领域差异`

This compaction must still preserve at least one distinguishing fact per document when evidence exists.

- [ ] **Step 5: Run compare route and fallback tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_file_routes.py tests/test_patent_executor.py -k "compare and (restructure or summary or quality or compact or four_doc or english)" -v
```

Expected: PASS with the new five-section compare contract and explicit failure when the quality bar cannot be met.

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "feat: upgrade patent compare fallback structure"
```

## Task 5: Preserve Compare Streaming Timing While Updating Compare Final Content

**Files:**
- Modify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Keep compare buffering semantics intact**

Do not make compare incremental streaming a goal in this task.

Maintain the current behavior where:

- compare answers may be buffered
- the final compare success step still arrives before the first compare content chunk

- [ ] **Step 2: Ensure every compare generation path converges to the new contract**

Verify parity across:

- direct string return from `answer_question_fn`
- generator/iterable model output
- client-based fallback paths

Each path must converge to the same five-section compare answer structure before content is emitted.

- [ ] **Step 3: Run compare streaming tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_executor.py -k "compare and (streaming or step_before_content or final_success)" -v
```

Expected: PASS with unchanged compare timing semantics and updated compare content structure.

- [ ] **Step 4: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/tests/test_patent_executor.py
git commit -m "fix: preserve patent compare streaming semantics"
```

## Task 6: Align Frontend Markdown Rendering with the New Compare Structure

**Files:**
- Verify/Modify if needed: [frontend-vue/src/utils/index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js)
- Verify/Modify if needed: [frontend-vue/src/styles/main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css)
- Test: [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
- Test: [frontend-vue/tests/markdown-rendering.test.js](/home/cqy/worktrees/highThinking/frontend-vue/tests/markdown-rendering.test.js)
- Verify: [frontend-vue/src/views/Home.vue](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue)

- [ ] **Step 1: Verify the compare answer still goes through the shared Markdown path**

Confirm the frontend path remains:

```text
answer_text
-> Home.vue getStreamingMessageHtml / getRenderedMessageHtml
-> shared Markdown render helpers
-> message HTML
```

Do not introduce a Patent-only compare render branch unless the new compare contract cannot be expressed through shared Markdown rendering.

- [ ] **Step 2: Add compare-specific rendering regression tests**

In [markdown-rendering.test.js](/home/cqy/worktrees/highThinking/frontend-vue/tests/markdown-rendering.test.js), add fixtures that verify:

- `具体内容对比`
- `研究方法差异`
- `应用领域差异`
- `相同点`
- `总结`

render in order

Also verify:

- per-document subheadings render as real headings
- nested bullets remain nested
- compare output does not depend on Markdown tables

- [ ] **Step 3: Keep frontend changes shared-surface safe**

If any compare-specific style refinement is needed:

- keep it inside shared `.message-content` Markdown presentation
- do not regress ordinary answer rendering
- do not add compare-table assumptions

- [ ] **Step 4: Run focused frontend verification**

Run:

```bash
cd /home/cqy/worktrees/highThinking/frontend-vue && node --test src/utils/answerSummary.test.js tests/markdown-rendering.test.js
cd /home/cqy/worktrees/highThinking/frontend-vue && npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/utils/index.js frontend-vue/src/styles/main.css frontend-vue/src/utils/answerSummary.test.js frontend-vue/tests/markdown-rendering.test.js
git commit -m "feat: align frontend with patent compare structure"
```

## Task 7: Final Cross-Verification

**Files:**
- Verify: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Verify: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Verify: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)
- Verify: [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
- Verify: [frontend-vue/tests/markdown-rendering.test.js](/home/cqy/worktrees/highThinking/frontend-vue/tests/markdown-rendering.test.js)

- [ ] **Step 1: Run the full targeted backend regression slice**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && PYTHONPATH=/home/cqy/worktrees/highThinking/patent pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py -k "summary or compare or hybrid or tabular" -v
```

Expected: PASS. Existing non-compare summary/hybrid/tabular assertions must remain green alongside the new compare assertions.

- [ ] **Step 2: Re-run frontend verification**

Run:

```bash
cd /home/cqy/worktrees/highThinking/frontend-vue && node --test src/utils/answerSummary.test.js tests/markdown-rendering.test.js
cd /home/cqy/worktrees/highThinking/frontend-vue && npm run build
```

Expected: PASS.

- [ ] **Step 3: Review final scope against the approved spec**

Confirm the implementation matches [docs/2026-04-11-patent-file-qa-compare-upgrade-spec.md](/home/cqy/worktrees/highThinking/docs/2026-04-11-patent-file-qa-compare-upgrade-spec.md), especially:

- five-section compare contract
- bounded document-count policy
- preserved non-compare contracts
- preserved compare step-before-content timing
- fail-closed behavior for unusable compare requests

- [ ] **Step 4: Commit the integrated change**

```bash
git add patent/server/patent/pdf_contract.py patent/server/patent/pdf_service.py patent/server/patent/file_routes.py patent/server/patent/executor.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py frontend-vue/src/utils/index.js frontend-vue/src/styles/main.css frontend-vue/src/utils/answerSummary.test.js frontend-vue/tests/markdown-rendering.test.js
git commit -m "feat: upgrade patent file compare answers"
```
