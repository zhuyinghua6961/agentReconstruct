# Patent File-QA Literature Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `patent` file-QA literature-summary answers from the current four-block wrapper into the approved academic summary structure for summary/file-summary scenarios only, without changing ordinary QA, and align the frontend Markdown rendering/styles so the new structure has the intended visual hierarchy.

**Architecture:** Keep the existing `patent` file-QA routing split intact and scope the backend change to formatter/prompt/synthesis layers inside the file-QA chain. Single-PDF summary, table-summary, hybrid synthesis, and multi-PDF compare should each detect summary-mode explicitly, emit the new chaptered Markdown structure, and preserve conservative evidence boundaries when source text is sparse. On the frontend, keep the current chat page structure and streaming pipeline intact, but tighten Markdown normalization/tests and chapter/list styling so the new backend structure is rendered with clear heading hierarchy and readable spacing.

**Tech Stack:** Python, FastAPI service modules under `patent/server/patent`, Vue 3 frontend under `frontend-vue`, Markdown output contracts, pytest, node test

---

## File Map

- [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
  Purpose: single-PDF summary prompt contract, compare prompt contract, summary detection, compare fallback helpers
- [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
  Purpose: wraps model output and fallback output for PDF summary/compare routes; current `_ensure_fastqa_pdf_summary_structure()` is the main single-PDF formatter choke point
- [patent/server/patent/tabular_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/tabular_service.py)
  Purpose: summary detection for `tabular_qa`, tabular prompt generation, tabular fallback formatter
- [patent/server/patent/file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py)
  Purpose: hybrid file-answer synthesis; current `synthesize_patent_hybrid_answer()` still emits `结论 / 证据 / 对比 / 限制`
- [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
  Purpose: unit coverage for PDF prompt contracts and compare prompt contracts
- [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
  Purpose: primary route-level structure contract for file-QA outputs and prompt wrapping; this file should own route-facing summary structure assertions
- [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)
  Purpose: executor orchestration, streaming behavior, and end-to-end route integration
- [patent/tests/test_patent_stage4_synthesis.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_stage4_synthesis.py)
  Purpose: ordinary-QA regression guard; use for verification only, not as an implementation target
- [frontend-vue/src/utils/index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js)
  Purpose: final and streaming Markdown formatting entrypoints used by chat rendering
- [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
  Purpose: focused Markdown heading/list normalization coverage for summary-like answers
- [frontend-vue/src/styles/main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css)
  Purpose: shared Markdown presentation styles for chat message content, including heading hierarchy and list spacing
- [docs/2026-04-11-patent-file-qa-literature-summary-requirements.md](/home/cqy/worktrees/highThinking/docs/2026-04-11-patent-file-qa-literature-summary-requirements.md)
  Purpose: approved requirements baseline; implementation must not exceed this scope

## Guardrails

- Do not modify ordinary QA files such as [patent/server/patent/answering.py](/home/cqy/worktrees/highThinking/patent/server/patent/answering.py) or [patent/server/patent/stages/synthesis.py](/home/cqy/worktrees/highThinking/patent/server/patent/stages/synthesis.py).
- Do not change gateway route selection, source-scope validation, or frontend layout in this task.
- Do not change gateway route selection or source-scope validation in this task.
- Frontend work is limited to Markdown rendering/tests and message-content styles; do not redesign the page layout, toolbar, routing flow, or message container structure.
- Keep non-summary file QA behavior stable where the requirement explicitly excludes it.
- Keep the evidence boundary strict: when source material is thin, reduce bullet count and say `PDF中未提及` or equivalent instead of inventing missing sections.
- Use standard Markdown headings and list markers (`##`, `-`) rather than raw `•`.
- Treat [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py) as the primary owner of route-level structure assertions. Treat [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py) as the owner of executor orchestration and streaming parity assertions.
- Treat [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js) as the owner of Markdown summary rendering regressions; avoid burying frontend rendering checks inside backend tests.

### Task 1: Lock the New Output Contract in Tests First

**Files:**
- Modify: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Modify: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Modify: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Add failing prompt-contract assertions for single-PDF summary mode**

Add assertions that summary-mode PDF prompts require the new chaptered contract instead of the old four-block wrapper. The test should explicitly check for:

```python
assert "研究目的和背景" in prompt
assert "研究方法/实验设计" in prompt
assert "主要发现和结果" in prompt
assert "结论和意义" in prompt
assert "严格基于文件原文" in prompt
assert "## 结论" not in prompt
```

- [ ] **Step 2: Add failing route-level assertions for summary-mode outputs**

Add route-level tests for:

```python
assert "## 研究目的和背景" in answer
assert "## 研究方法/实验设计" in answer
assert "## 主要发现和结果" in answer
assert "## 结论和意义" in answer
assert "注*" in answer
```

Cover at least:
- single-PDF summary
- tabular summary fallback
- `pdf+table` hybrid summary
- `pdf+table+kb` hybrid summary

Put the route-facing structure assertions primarily in [test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py); keep [test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py) focused on integration and streaming parity.

- [ ] **Step 3: Add non-regression tests for excluded paths**

Add or update tests so these remain unchanged:

```python
assert "## 结论" in answer
assert "## 证据" in answer
assert "## 对比" in answer
assert "## 限制" in answer
```

Cover at least:
- non-summary single-PDF question
- non-summary tabular question
- non-summary hybrid question

- [ ] **Step 4: Add failing compare-mode tests for per-document structured summaries**

Update compare prompt/output tests so multi-PDF compare requires:

```python
assert "逐篇给出文献概要" in prompt
assert "研究目的和背景" in prompt
assert "研究方法/实验设计" in prompt
assert "主要发现和结果" in prompt
assert "结论和意义" in prompt
```

At the route/output level, require each document summary to be chaptered while the final compare section still includes shared/different/conclusion content.

This includes rewriting the existing compare structure assertions in [test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py), not only the prompt-contract tests.

- [ ] **Step 5: Add failing summary streaming regression tests**

Update or add executor tests covering the current summary streaming branch in [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py) so summary-mode streaming now converges on the new chaptered structure instead of the old `## 结论` auto-prefix path.

- [ ] **Step 6: Run the targeted tests and confirm they fail before implementation**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py -k "summary or compare or hybrid or tabular" -v
```

Expected: FAIL because prompts/formatters still emit the old structure.

- [ ] **Step 7: Commit the test-only red state**

```bash
git add patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "test: lock patent file summary output contract"
```

### Task 2: Rework Single-PDF Summary Prompt and Formatter

**Files:**
- Modify: [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
- Modify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Test: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Change the summary-mode PDF prompt contract only**

In [pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py), change the `is_summary=True and is_compare=False` branch so it requests:

```text
## 研究目的和背景
## 研究方法/实验设计
## 主要发现和结果
## 结论和意义
注*：...
```

Prompt rules must also say:
- use standard Markdown bullets
- keep original terms like `XRD`, `TOF-SIMS`
- if the PDF lacks evidence for a chapter, say so explicitly and keep the chapter short
- do not reintroduce `## 结论 / 证据 / 对比 / 限制` for summary-mode

- [ ] **Step 2: Split summary-mode formatting from non-summary formatting**

In [pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py), replace the current “always coerce to four blocks” behavior for summary-mode with a dedicated literature-summary formatter. Keep non-summary and hybrid-subtask non-summary behavior on the existing four-block path.

Recommended shape:

```python
if is_summary_question(question) and not compare_mode:
    answer = _ensure_literature_summary_structure(...)
else:
    answer = _ensure_fastqa_four_block_structure(...)
```

- [ ] **Step 3: Make fallback summaries obey the new structure**

When the model returns nothing, `build_extractive_fallback_summary()` and the summary formatter should produce the new four academic chapters plus the note, using conservative extracted facts only. Missing chapters should say the PDF did not mention enough evidence.

- [ ] **Step 4: Preserve streaming behavior**

Update the summary streaming branch so summary-mode no longer auto-prefixes `## 结论\n`. It should either stream raw chaptered Markdown if the model already returns it, or defer until the wrapped academic summary is ready.

- [ ] **Step 5: Rewrite and run route-level + streaming tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -k "pdf and (summary or compare)" -v
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_executor.py::test_executor_pdf_streaming_generator_emits_content_before_final_success tests/test_patent_executor.py::test_executor_pdf_streaming_generator_partial_heading_opening_keeps_stream_final_parity tests/test_patent_executor.py::test_executor_pdf_streaming_generator_whitespace_only_first_chunk_keeps_stream_final_parity -v
```

Expected: PASS for new single-PDF summary assertions, compare prompt assertions, and summary streaming parity; non-summary PDF tests remain green.

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/pdf_contract.py patent/server/patent/pdf_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "feat: upgrade patent pdf summary structure"
```

### Task 3: Rework `tabular_qa` Summary-Only Formatting Without Touching Normal Table QA

**Files:**
- Modify: [patent/server/patent/tabular_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/tabular_service.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Keep the current summary gate narrow**

Use the existing summary/file-summary path only. Do not expand this task into all table questions. The implementation should continue to branch on the current summary detector and leave field extraction / numeric lookup questions alone.

- [ ] **Step 2: Change summary-mode prompt wording**

In `_build_patent_tabular_prompt()`, when `_is_summary_question(question)` is true:
- request the same academic chapter layout
- tell the model to be conservative for chapters table evidence cannot support
- explicitly forbid turning field lookup questions into literature-style summaries

For non-summary paths, keep the current four-block/table-answer contract.

- [ ] **Step 3: Replace summary-mode table fallback wrapping**

Update `_ensure_fastqa_table_summary_structure()` so summary-mode emits:

```markdown
## 研究目的和背景
## 研究方法/实验设计
## 主要发现和结果
## 结论和意义
注*：...
```

Rules:
- use table evidence as the only factual source
- if background/method cannot be supported from the table, keep the chapter but state the evidence gap
- do not apply this wrapper to non-summary table questions

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_file_routes.py tests/test_patent_executor.py -k "tabular" -v
```

Expected: summary-mode tabular tests pass with the new chaptered structure; non-summary tabular tests still pass with the old behavior.

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular_service.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "feat: scope patent tabular summaries to chaptered output"
```

### Task 4: Upgrade Hybrid Summary Synthesis While Preserving File Precedence

**Files:**
- Modify: [patent/server/patent/file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Make hybrid synthesis summary-aware**

In `synthesize_patent_hybrid_answer()`, detect summary-mode from `question` and branch:

```python
if is_summary_question(question):
    return _synthesize_literature_summary(...)
return _synthesize_four_block_answer(...)
```

Import and reuse the existing summary detector rather than inventing a broader route-wide heuristic.

- [ ] **Step 2: Preserve evidence precedence by source scope**

The summary-mode synthesis must:
- treat PDF and table as first-class file evidence in `pdf+table`
- treat KB as supplementary validation/background only in `pdf+table+kb`
- never write KB-only facts as if they were PDF/table facts

Expected output responsibilities:
- `研究目的和背景`: prefer PDF + KB-validated background only when already grounded in files
- `研究方法/实验设计`: prefer PDF methods and any table-derived experimental setup details
- `主要发现和结果`: merge PDF findings and table metrics as peer evidence
- `结论和意义`: synthesize across file evidence first, then mention KB corroboration if present

- [ ] **Step 3: Keep non-summary hybrid answers on the old structure**

Explicitly preserve the current `结论 / 证据 / 对比 / 限制` synthesis for non-summary hybrid questions so ordinary file QA is not silently reformatted.

- [ ] **Step 4: Add route-level regression tests**

In [test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py), cover:
- `pdf+table` summary returns chaptered academic structure and contains both PDF and table evidence
- `pdf+table+kb` summary returns chaptered structure and keeps KB as supplementary corroboration
- non-summary `pdf+table` and `pdf+table+kb` still keep the old four-block structure

In [test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py), rewrite the wrapped subanswer assertions so hybrid PDF/table child outputs are validated against their new summary-mode structure and their old non-summary guardrails.

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_file_routes.py tests/test_patent_executor.py -k "hybrid" -v
```

Expected: summary-mode hybrid tests pass with chaptered output; non-summary hybrid tests remain green.

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/file_routes.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "feat: add chaptered patent hybrid summaries"
```

### Task 5: Upgrade Multi-PDF Compare Prompts and Fallbacks

**Files:**
- Modify: [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
- Modify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Test: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Change compare prompt structure**

Update the compare branch in `build_patent_pdf_answer_prompt()` so it requests:
- a structured per-document summary for each selected PDF
- each per-document summary to cover the four academic dimensions when evidence exists
- then a final compare section with `相同点 / 差异点 / 总结`

Do not require every per-document chapter to be long; require conservative contraction when evidence is sparse.

- [ ] **Step 2: Upgrade compare fallback shaping**

Update `_ensure_compare_answer_structure()` so the fallback no longer emits only:

```text
各自概要：
相同点：
差异点：
总结：
```

Instead, each document inside `各自概要` should be expanded into the chaptered literature-summary format, and the compare summary should remain separate.

This step must also rewrite the compare route assertions already present in [test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py), because they currently encode the old compare output contract.

- [ ] **Step 3: Keep compare failure semantics intact**

Do not weaken `build_compare_failure_message()` or `validate_compare_context()`. If compare context is incomplete, still fail closed instead of pretending the structured compare succeeded.

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py -k "compare" -v
```

Expected: compare prompt/output tests pass, failure-mode tests still fail closed as before.

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/pdf_contract.py patent/server/patent/pdf_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "feat: structure patent multi-pdf compare summaries"
```

### Task 6: Frontend Markdown Rendering and Summary Styling Alignment

**Files:**
- Modify: [frontend-vue/src/utils/index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js)
- Modify: [frontend-vue/src/styles/main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css)
- Test: [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
- Test: [frontend-vue/tests/markdown-rendering.test.js](/home/cqy/worktrees/highThinking/frontend-vue/tests/markdown-rendering.test.js)
- Verify: [frontend-vue/src/views/Home.vue](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue)
- Verify: [frontend-vue/src/utils/streamingRender.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/streamingRender.js)

- [ ] **Step 1: Verify current frontend already consumes the new backend contract as Markdown**

Confirm the chat rendering path still consumes `answer_text` as Markdown for both streaming and terminal states, and do not add a patent-specific rendering branch unless a concrete bug requires it. The intended path remains:

```text
answer_text
-> Home.vue getStreamingMessageHtml / getRenderedMessageHtml
-> createStreamingHtmlRenderer / formatAnswer
-> formatStreamingAnswer / formatAnswer
-> marked
-> message HTML mounted by v-html
```

`streamingRender.js` remains part of the verification scope even if no edits are needed there, because streaming parity depends on that wrapper still using the shared Markdown formatter path.

- [ ] **Step 2: Add rendering regression tests for the new literature-summary chapter structure**

In [answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js), add focused coverage that a summary like:

```markdown
## 研究目的和背景
- ...

## 研究方法/实验设计
- ...

## 主要发现和结果
- ...

## 结论和意义
- ...

注*：...
```

is rendered consistently in both `formatStreamingAnswer()` and `formatAnswer()`.

The tests should also confirm:
- chapter headings become real heading tags instead of leaking raw Markdown
- nested bullet lists remain nested lists
- the standalone `注*：...` line remains a paragraph instead of collapsing into a heading
- compare-mode per-document subheadings plus chapter blocks render in order

Test split:
- keep the single-document literature-summary fixture assertions in [answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
- add the compare fixture assertions in [markdown-rendering.test.js](/home/cqy/worktrees/highThinking/frontend-vue/tests/markdown-rendering.test.js) so ordered rendering of repeated document subheadings and chapter blocks is covered separately from the generic normalization tests
- exercise both `formatStreamingAnswer()` and `formatAnswer()` in each file where the fixture is introduced

- [ ] **Step 3: Tighten message-content styles for the new summary hierarchy**

In [main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css), refine existing Markdown styles so the new backend structure gets the intended visual hierarchy:
- `h2` remains the primary chapter title style with blue text and bottom border
- nested lists have clear indentation and spacing
- paragraphs/lists immediately following chapter headings have compact top spacing
- the note line is visually secondary but still readable

Do not redesign the page; keep this scoped to `.message-content` Markdown presentation.

If the note line cannot be styled reliably with existing generic selectors, add a lightweight, generic post-process in [index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js) that tags a standalone trailing `注*：...` paragraph with a reusable class (for example `.message-note`) before the HTML is returned. Do not add a patent-specific rendering branch in `Home.vue`; keep this as shared Markdown post-processing so streaming and final rendering stay aligned.

- [ ] **Step 4: Verify against actual chat mounting path**

Confirm [Home.vue](/home/cqy/worktrees/highThinking/frontend-vue/src/views/Home.vue) still routes streaming and final content through the shared render helpers without a special-case path that would bypass the Markdown tests, and confirm [streamingRender.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/streamingRender.js) still delegates terminal rendering to `formatAnswer()` and non-terminal rendering to `formatStreamingAnswer()`.

- [ ] **Step 5: Run focused frontend tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/frontend-vue && node --test src/utils/answerSummary.test.js tests/markdown-rendering.test.js
```

Expected: PASS.

- [ ] **Step 6: Optional build verification**

Run:

```bash
cd /home/cqy/worktrees/highThinking/frontend-vue && npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend-vue/src/utils/index.js frontend-vue/src/utils/answerSummary.test.js frontend-vue/src/styles/main.css
git commit -m "feat: style patent literature summary markdown rendering"
```

### Task 7: Full Verification and Handoff

**Files:**
- Modify: none expected
- Verify: [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
- Verify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Verify: [patent/server/patent/tabular_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/tabular_service.py)
- Verify: [patent/server/patent/file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py)
- Verify: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Verify: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Verify: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)
- Verify: [patent/tests/test_patent_stage4_synthesis.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_stage4_synthesis.py)
- Verify: [frontend-vue/src/utils/index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js)
- Verify: [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
- Verify: [frontend-vue/src/styles/main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css)

- [ ] **Step 1: Run the full targeted regression suite**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_pdf_contract.py tests/test_patent_executor.py tests/test_patent_file_routes.py -v
cd /home/cqy/worktrees/highThinking/frontend-vue && node --test src/utils/answerSummary.test.js tests/markdown-rendering.test.js
```

Expected: PASS.

- [ ] **Step 2: Verify excluded-path invariants separately**

Re-run narrow guards to confirm non-summary file QA stayed stable and ordinary QA still passes untouched:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_executor.py -k "non_summary or file_route or tabular_route or hybrid_route" -v
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_stage4_synthesis.py -v
cd /home/cqy/worktrees/highThinking/frontend-vue && npm run build
```

Expected: PASS.

- [ ] **Step 3: Review final behavioral checklist**

Confirm manually from test fixtures or local responses:
- summary single-PDF answers use four academic chapters plus note
- summary `tabular_qa` answers use the same chapters conservatively
- summary `hybrid_qa` treats PDF/table as primary evidence and KB as supplementary
- compare answers provide structured per-document outlines plus compare summary
- non-summary file QA still uses the old four-block structure
- ordinary QA files remain untouched
- frontend renders chapter headings, lists, compare outlines, and note text with clear hierarchy
- no patent-specific frontend branch was added unless required by a concrete rendering bug

- [ ] **Step 4: Commit final verification state**

```bash
git add patent/server/patent/pdf_contract.py patent/server/patent/pdf_service.py patent/server/patent/tabular_service.py patent/server/patent/file_routes.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py frontend-vue/src/utils/index.js frontend-vue/src/utils/answerSummary.test.js frontend-vue/src/styles/main.css
git commit -m "feat: upgrade patent file literature summary outputs"
```
