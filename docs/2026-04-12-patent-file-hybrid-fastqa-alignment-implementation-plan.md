# Patent File/Hybrid QA FastQA Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align `Patent` file-summary behavior to the approved `FastQA`-like spec for `pdf_qa + pdf` and `hybrid_qa + pdf+table` only, while keeping KB-including hybrid paths, standalone `tabular_qa`, compare, and ordinary QA unchanged.

**Architecture:** Keep the current `Patent` route split intact and implement the alignment through a small shared summary-formatting helper plus targeted changes in the PDF summary path and the file-only `pdf+table` hybrid synthesis path. Do not change `FastQA`, do not inflate prompt/context budgets, and do not let the shared KB merge path inherit the new file-only hybrid behavior by accident. Frontend work is limited to Markdown rendering/tests and message-content styles needed to preserve chapter hierarchy, nested list readability, and the secondary note presentation.

**Tech Stack:** Python, FastAPI service modules under `patent/server/patent`, pytest, Vue 3, shared Markdown rendering in `frontend-vue`, node test, CSS

---

## File Map

- Create: [patent/server/patent/summary_formatting.py](/home/cqy/worktrees/highThinking/patent/server/patent/summary_formatting.py)
  Purpose: own shared literature-summary note text, degraded-answer detection, heading coverage checks, support-point extraction thresholds, and preserve/repair/fallback gate predicates used by in-scope summary formatters.
- Create: [patent/tests/test_patent_summary_formatting.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_summary_formatting.py)
  Purpose: unit-test the deterministic predicates and routing thresholds so `pdf_service.py` and `file_routes.py` do not drift into separate heuristics.
- Modify: [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
  Purpose: keep the prompt compact while adding only the missing contract requirements for `局限性`, evidence-gap wording, and Markdown structure in the single-PDF summary path.
- Modify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
  Purpose: replace the current compression-first summary wrapper with preserve/light-repair/conservative-repair/fallback behavior for `pdf_qa + pdf`, backed by the shared helper.
- Modify: [patent/server/patent/file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py)
  Purpose: isolate the in-scope `hybrid_qa + pdf+table` summary synthesis from the shared KB merge path and make the file-only hybrid answer read like one integrated summary instead of stitched evidence bullets.
- Modify only if hybrid tests prove it necessary: [patent/server/patent/tabular_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/tabular_service.py)
  Purpose: minimally improve table-side evidence text for `route_hint=hybrid_qa` and `source_scope=pdf+table` without changing standalone `tabular_qa` behavior or contracts.
- Modify: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
  Purpose: prompt-contract assertions for the in-scope PDF summary path.
- Modify: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
  Purpose: file-route structure, source-scope isolation, and hybrid synthesis assertions.
- Modify: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)
  Purpose: integration and streaming parity for `pdf_qa + pdf`, `hybrid_qa + pdf+table`, and out-of-scope no-regression checks for `pdf+table+kb`.
- Modify: [frontend-vue/src/utils/index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js)
  Purpose: only if needed to keep normalized Markdown headings/nested lists consistent between streaming and final render paths.
- Modify: [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
  Purpose: guard the approved chapter hierarchy, nested list rendering, `局限性` block, and secondary note rendering.
- Modify: [frontend-vue/src/styles/main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css)
  Purpose: tighten chapter spacing, nested list readability, and note/`局限性` visual separation without redesigning the page.
- Reference only: [docs/2026-04-12-patent-file-hybrid-fastqa-alignment-spec.md](/home/cqy/worktrees/highThinking/docs/2026-04-12-patent-file-hybrid-fastqa-alignment-spec.md)
  Purpose: approved implementation boundary and acceptance baseline.

## Guardrails

- Do not modify `FastQA`.
- Do not modify `Patent` ordinary QA or KB-only QA codepaths.
- Do not change compare-mode behavior in this plan.
- Do not redesign standalone `tabular_qa`; only touch `tabular_service.py` if a failing `pdf+table` hybrid test proves the file-only hybrid synthesis cannot get adequate table-side evidence otherwise.
- Do not change gateway route selection, file-selection validation, or rollout gates.
- Keep prompt/context budget in the current class; no few-shot expansion and no raw-context inflation.
- Keep the file evidence boundary strict. Missing support must be expressed as evidence-gap wording, not guessed content.
- Treat [patent/tests/test_patent_summary_formatting.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_summary_formatting.py) as the owner of deterministic helper rules.
- Treat [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py) as the owner of source-scope and route-level structure assertions.
- Treat [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py) as the owner of streaming and integration parity assertions.

### Task 1: Lock the In-Scope Contract and Out-of-Scope Guards in Tests

**Files:**
- Create: [patent/tests/test_patent_summary_formatting.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_summary_formatting.py)
- Modify: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Modify: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Modify: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Add failing unit tests for the deterministic summary predicates**

Cover at minimum:

```python
assert is_degraded_summary_answer("未拿到可读的 PDF") is True
assert is_degraded_summary_answer("## 研究目的和背景\n- 原文给出了研究动机。") is False
assert extract_support_points("短句", min_chars=10) == []
assert extract_support_points("- 足够长的模型要点。", min_chars=10) == ["足够长的模型要点。"]
assert count_primary_summary_headings(answer_with_four_headings) == 4
```

- [ ] **Step 2: Tighten the PDF prompt-contract tests around the approved scope**

In [test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py), assert that in-scope summary prompts:

```python
assert "## 研究目的和背景" in prompt
assert "## 研究方法/实验设计" in prompt
assert "## 主要发现和结果" in prompt
assert "## 结论和意义" in prompt
assert "## 局限性" in prompt
assert "PDF中未提及" in prompt
```

Also assert the prompt does not grow into a long few-shot/spec wall.

- [ ] **Step 3: Add failing route-level tests for the preserve/repair/fallback gates**

In [test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py), add cases for:

```python
assert answer.count("## ") >= 5
assert "## 局限性" in answer
assert "注*" in answer
```

Cover:
- preserve path: already-good model-authored chaptered answer stays mostly intact
- light repair path: 3-of-4 chapters or legacy four-block answer gets repaired instead of fully rebuilt
- conservative repair path: sparse but usable answer becomes chaptered with evidence-gap wording
- fallback path: degraded/empty answer falls back to evidence-bound structure

- [ ] **Step 4: Add failing source-scope isolation tests for hybrid summary**

In [test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py) and [test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py), lock these expectations:

```python
assert aligned_pdf_table_answer.startswith("## 研究目的和背景")
assert "PDF 原文证据：" not in aligned_pdf_table_answer
assert "表格执行结果：" not in aligned_pdf_table_answer
assert unchanged_pdf_table_kb_answer_uses_existing_behavior is True
```

The exact no-regression assertion for `pdf+table+kb` should mirror current output behavior rather than inventing a new desired format.

- [ ] **Step 5: Add streaming parity tests for in-scope summary answers**

Ensure streaming and final answers converge for:
- `pdf_qa + pdf` summary
- `hybrid_qa + pdf+table` summary

and do not accidentally change:
- non-summary PDF answers
- `hybrid_qa + pdf+table+kb`

- [ ] **Step 6: Run the targeted backend tests and confirm the new assertions fail first**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_summary_formatting.py tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py -k "summary or hybrid or formatting" -v
```

Expected: FAIL because the preserve/repair/fallback predicates and the `pdf+table` isolation behavior are not implemented yet.

- [ ] **Step 7: Commit the red test baseline**

```bash
git add patent/tests/test_patent_summary_formatting.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "test: lock patent file summary alignment scope"
```

### Task 2: Introduce the Shared Summary-Formatting Helper

**Files:**
- Create: [patent/server/patent/summary_formatting.py](/home/cqy/worktrees/highThinking/patent/server/patent/summary_formatting.py)
- Test: [patent/tests/test_patent_summary_formatting.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_summary_formatting.py)

- [ ] **Step 1: Implement the shared constants and deterministic predicates**

Create helpers for:

```python
LITERATURE_SUMMARY_NOTE = "注*：..."
PRIMARY_SUMMARY_HEADINGS = (...)
DEGRADED_MARKERS = (...)

def is_degraded_summary_answer(text: str) -> bool: ...
def extract_support_points(text: str, *, max_items: int, min_chars: int) -> list[str]: ...
def count_primary_summary_headings(text: str) -> int: ...
def has_legacy_four_block_structure(text: str) -> bool: ...
```

- [ ] **Step 2: Encode the gate decisions centrally**

Add one shared routing entrypoint, for example:

```python
def classify_summary_answer(answer: str, *, prepared_text: str) -> Literal["preserve", "light_repair", "conservative_repair", "fallback"]:
    ...
```

The implementation must follow the approved thresholds:
- degraded markers from the spec
- model-answer support points at `min_chars=10`
- prepared-evidence support points at `min_chars=12`

- [ ] **Step 3: Keep the helper narrowly scoped**

Do not move compare logic or ordinary four-block logic into this module. This file only owns the in-scope literature-summary support rules.

- [ ] **Step 4: Run the helper tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_summary_formatting.py -v
```

Expected: PASS with deterministic predicate coverage only.

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/summary_formatting.py patent/tests/test_patent_summary_formatting.py
git commit -m "feat: add shared patent summary formatting rules"
```

### Task 3: Rework the Single-PDF Summary Path to Preserve Good Answers

**Files:**
- Modify: [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
- Modify: [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Test: [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Keep the prompt compact and add only the missing summary requirements**

In [pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py), update only the non-compare summary branch so it explicitly requires:
- `## 局限性`
- evidence-gap wording when a chapter lacks support
- Markdown headings/lists only

Do not add few-shot examples or long repeated warnings.

- [ ] **Step 2: Replace the current summary wrapper with gate-based normalization**

In [pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py), make `_ensure_literature_summary_structure()` or its replacement call the shared helper and branch:

```python
mode = classify_summary_answer(answer, prepared_text=prepared_pdf_text)
if mode == "preserve":
    ...
elif mode == "light_repair":
    ...
elif mode == "conservative_repair":
    ...
else:
    ...
```

- [ ] **Step 3: Preserve model-authored detail before touching prepared evidence**

Implementation rules:
- preserve chapter bodies that already exist and have usable content
- only fill missing chapters from prepared PDF evidence when needed
- inject `局限性` and `注*` if absent
- do not collapse a rich answer down to a few keyword-picked bullets

- [ ] **Step 4: Keep fallback strict and evidence-bound**

Fallback should still use extracted facts only, but it must now emit:

```markdown
## 研究目的和背景
## 研究方法/实验设计
## 主要发现和结果
## 结论和意义
## 局限性
注*：...
```

- [ ] **Step 5: Preserve streaming/final parity**

Make sure the streaming branch and final branch both pass through the same summary normalizer outcome so the final answer does not diverge from what was streamed.

- [ ] **Step 6: Run focused PDF tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -k "pdf and summary" -v
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_executor.py -k "pdf and summary and stream" -v
```

Expected: PASS for prompt contract, preserve/repair/fallback cases, and streaming parity. Non-summary PDF tests remain green.

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/pdf_contract.py patent/server/patent/pdf_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "feat: preserve rich patent pdf summaries"
```

### Task 4: Isolate `pdf+table` File-Only Hybrid Summary Alignment

**Files:**
- Modify: [patent/server/patent/file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py)
- Modify only if needed: [patent/server/patent/tabular_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/tabular_service.py)
- Test: [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py)
- Test: [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py)

- [ ] **Step 1: Split the in-scope file-only summary branch from the shared hybrid synthesizer**

In [file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py), add an explicit `source_scope == "pdf+table"` summary path that does not change the existing KB-merge flow.

Recommended shape:

```python
if is_summary_question(question) and source_scope == "pdf+table":
    return synthesize_file_only_literature_summary(...)
return existing_hybrid_synthesizer(...)
```

- [ ] **Step 2: Synthesize one integrated file-only answer**

The new `pdf+table` summary must:
- read as one literature summary, not as prefixed evidence buckets
- preserve PDF-backed method/result logic
- merge useful table evidence where it strengthens the file-side answer
- include `局限性` and `注*`

- [ ] **Step 3: Leave `pdf+table+kb` behavior unchanged**

Do not modify [executor.py](/home/cqy/worktrees/highThinking/patent/server/patent/executor.py) synthesis behavior unless a test shows a hard blocker. The default implementation target is isolation inside [file_routes.py](/home/cqy/worktrees/highThinking/patent/server/patent/file_routes.py), not a rewrite of the shared merge pipeline.

- [ ] **Step 4: Only if tests require it, minimally refine table-side evidence text for `pdf+table`**

If the file-only hybrid tests still fail because the table-side inputs are too thin, make the smallest possible change in [tabular_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/tabular_service.py) scoped to:
- `route_hint == "hybrid_qa"`
- `source_scope == "pdf+table"`

Do not change standalone `tabular_qa` outputs or prompt contracts.

- [ ] **Step 5: Run focused hybrid tests**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_file_routes.py tests/test_patent_executor.py -k "hybrid and (summary or source_scope or kb)" -v
```

Expected:
- `pdf+table` summary tests pass with the new aligned structure
- `pdf+table+kb` tests still match their pre-existing behavior
- non-summary hybrid tests remain green

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/file_routes.py patent/server/patent/tabular_service.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py
git commit -m "feat: isolate patent file-only hybrid summaries"
```

### Task 5: Tighten Frontend Markdown Rendering for the New Summary Shape

**Files:**
- Modify only if needed: [frontend-vue/src/utils/index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js)
- Modify: [frontend-vue/src/utils/answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js)
- Modify: [frontend-vue/src/styles/main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css)

- [ ] **Step 1: Add failing frontend tests for the final approved structure**

Extend [answerSummary.test.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/answerSummary.test.js) to cover:
- `## 局限性` rendering as a normal chapter heading
- nested list rendering under `研究方法/实验设计`
- `注*` rendering as a secondary note paragraph
- streaming and final render parity for the same Markdown

- [ ] **Step 2: Only adjust Markdown normalization if tests expose a render mismatch**

In [index.js](/home/cqy/worktrees/highThinking/frontend-vue/src/utils/index.js), keep changes surgical:
- do not invent new syntax
- do not special-case backend strings beyond what the tests require
- keep streaming and final formatting paths aligned

- [ ] **Step 3: Refine styles without redesigning the chat UI**

In [main.css](/home/cqy/worktrees/highThinking/frontend-vue/src/styles/main.css):
- ensure chapter headings remain visually distinct
- keep nested lists readable
- keep `局限性` visually consistent with other H2 chapters
- keep the note visually secondary

- [ ] **Step 4: Run focused frontend tests and build**

Run:

```bash
cd /home/cqy/worktrees/highThinking/frontend-vue && node --test src/utils/answerSummary.test.js
cd /home/cqy/worktrees/highThinking/frontend-vue && npm run build
```

Expected: summary rendering tests pass and the frontend build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/utils/index.js frontend-vue/src/utils/answerSummary.test.js frontend-vue/src/styles/main.css
git commit -m "feat: polish patent summary markdown rendering"
```

### Task 6: Run Final Verification Across the Approved Scope

**Files:**
- Reference: [docs/2026-04-12-patent-file-hybrid-fastqa-alignment-spec.md](/home/cqy/worktrees/highThinking/docs/2026-04-12-patent-file-hybrid-fastqa-alignment-spec.md)
- Reference: [docs/2026-04-12-patent-file-hybrid-fastqa-alignment-implementation-plan.md](/home/cqy/worktrees/highThinking/docs/2026-04-12-patent-file-hybrid-fastqa-alignment-implementation-plan.md)

- [ ] **Step 1: Run the full targeted backend suite**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_summary_formatting.py tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py -v
```

Expected: PASS.

- [ ] **Step 2: Re-run the frontend verification**

Run:

```bash
cd /home/cqy/worktrees/highThinking/frontend-vue && node --test src/utils/answerSummary.test.js
cd /home/cqy/worktrees/highThinking/frontend-vue && npm run build
```

Expected: PASS.

- [ ] **Step 3: Run no-regression spot checks for explicit out-of-scope paths**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && pytest tests/test_patent_file_routes.py tests/test_patent_executor.py -k "compare or tabular or kb or non_summary" -v
```

Expected: PASS, demonstrating that out-of-scope behavior remains stable.

- [ ] **Step 4: Record verification notes**

Capture:
- which exact routes were verified
- whether `tabular_service.py` had to change
- whether `pdf+table+kb` remained unchanged
- whether frontend changes were CSS-only or required Markdown normalization

- [ ] **Step 5: Commit the final verified slice**

```bash
git add patent frontend-vue docs/2026-04-12-patent-file-hybrid-fastqa-alignment-implementation-plan.md
git commit -m "feat: align patent file summaries with fastqa behavior"
```
