# Patent File QA Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `patent` file QA and hybrid QA genuinely usable by aligning the PDF/file pipeline with `fastQA`, including compare-aware multi-PDF behavior, unified synthesis, streaming/steps parity, and regression coverage.

**Architecture:** Keep `gateway` and frontend unchanged. Repair `patent` in place by first tightening the PDF QA contract around prompt rules, compare detection, multi-document formatting, truncation, fallback, and streaming semantics, then replacing hybrid shell composition with unified evidence synthesis plus explicit source-precedence rules. Where direct code sharing with `fastQA` is impractical, lock parity with targeted contract tests.

**Tech Stack:** Python, FastAPI service contracts, pytest, patent executor/file route services, `fastQA` PDF/tabular modules as reference contracts.

---

## File Map

- Modify: `patent/server/patent/pdf_service.py`
  Purpose: patent PDF QA orchestration, prompt construction, multi-PDF formatting, truncation, streaming/fallback behavior, step metadata.
- Create: `patent/server/patent/pdf_contract.py`
  Purpose: isolate compare detection, multi-document formatting, compare-aware prompt building, parity helpers, and fallback text rules so `pdf_service.py` does not keep growing.
- Modify: `patent/server/patent/file_routes.py`
  Purpose: replace hybrid shell composition with unified synthesis flow and conflict-aware source handling.
- Modify: `patent/server/patent/tabular_service.py`
  Purpose: expose the evidence/context needed by hybrid unified synthesis instead of only returning a standalone answer fragment.
- Modify: `patent/server/patent/executor.py`
  Purpose: remove forbidden file-first-plus-downstream-KB append behavior and preserve final stream ordering/result shape after the PDF/hybrid behavior changes.
- Modify: `patent/server/patent/kb_service.py`
  Purpose: expose KB evidence/context in a form that can participate in one unified synthesis path instead of only post hoc answer appending.
- Modify: `patent/tests/test_patent_file_routes.py`
  Purpose: unit-level regressions for PDF and hybrid file routing behavior.
- Modify: `patent/tests/test_patent_executor.py`
  Purpose: executor-level regressions for streamed content ordering and final payloads.
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
  Purpose: HTTP/stream contract regressions for PDF compare and hybrid routes.
- Create: `patent/tests/test_patent_pdf_contract.py`
  Purpose: direct parity tests for compare detection, prompt structure, multi-document truncation invariants, and fallback behavior.

## Task Order

1. PDF prompt parity and compare-branch contract
2. Multi-PDF truncation guarantees
3. PDF streaming/steps/failure-state parity
4. Hybrid unified synthesis and source precedence
5. End-to-end contract hardening for compare and hybrid routes

---

### Task 1: PDF Prompt Parity And Compare-Branch Contract

**Files:**
- Create: `patent/server/patent/pdf_contract.py`
- Modify: `patent/server/patent/pdf_service.py`
- Create: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/test_patent_file_routes.py`

- [ ] **Step 1: Write the failing contract tests**

Add tests for:

```python
def test_summary_prompt_matches_fastqa_contract_shape():
    ...


def test_non_summary_prompt_matches_fastqa_contract_shape():
    ...


def test_include_kb_prompt_uses_fastqa_boundary_text():
    ...


def test_prompt_contract_parity_uses_fastqa_snapshot_for_summary_non_summary_and_kb_boundary():
    ...


def test_compare_prompt_uses_compare_specific_structure_without_breaking_fastqa_constraints():
    ...


def test_compare_detection_accepts_implicit_compare_requests():
    assert is_compare_question("这两篇有什么异同") is True
    assert is_compare_question("分别讲了什么") is True
    assert is_compare_question("哪篇效果更好") is True


def test_compare_detection_does_not_trigger_for_single_file_question():
    assert is_compare_question("请总结第一篇文献", selected_pdf_count=2) is False


def test_multi_pdf_format_uses_stable_document_headers():
    formatted = format_multi_pdf_sections(
        [
            {"label": "paper-a.pdf", "text": "Abstract A. Results A."},
            {"label": "paper-b.pdf", "text": "Abstract B. Results B."},
        ]
    )
    assert "==== 文献 1: paper-a.pdf ====" in formatted
    assert "==== 文献 2: paper-b.pdf ====" in formatted


def test_compare_fallback_refuses_to_pretend_single_doc_summary_is_success():
    text = build_compare_failure_message(
        question="对比这两篇文献",
        available_docs=["paper-a.pdf"],
        missing_docs=["paper-b.pdf"],
    )
    assert "无法完成完整比较" in text
    assert "文档要点如下" not in text
```

Also add route-level tests showing two selected PDFs preserve both labels in loaded content and do not silently degrade into the old extractive fallback.

For `test_compare_prompt_uses_compare_specific_structure_without_breaking_fastqa_constraints()`, assert at minimum that the compare prompt explicitly contains:

1. compared document count and filenames
2. instructions to summarize each document separately
3. required compare dimensions: theme/goal, method, result/evidence, conclusion/contribution
4. explicit same-points, difference-points, and final-conclusion structure
5. evidence-insufficiency wording when one document is incomplete
6. the original `fastQA` core constraints forbidding generic knowledge and forbidding KB from replacing file-grounded claims
7. summary, non-summary, and `include_kb=true` prompt skeletons are locked by parity snapshot/contract assertions against `fastQA`

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -k "compare or multi_pdf or fallback or kb or summary or prompt"
```

Expected: FAIL because the new helpers and behaviors do not exist yet.

- [ ] **Step 3: Implement the minimal PDF contract helpers**

Create `patent/server/patent/pdf_contract.py` with:

1. compare-intent detection covering explicit and implicit compare wording plus negative cases
2. multi-PDF section formatting using stable headers compatible with the `fastQA` multi-doc truncation contract
3. compare-aware prompt builder that mirrors `fastQA` constraints while adding the required comparison structure
4. fallback builders that return explicit failure text instead of shell summaries for compare failures

This task is not done unless one of the following is true and proven by tests:

1. `patent` directly uses shared helpers whose output is already covered by `fastQA`
2. `patent` keeps a local implementation, but parity snapshot/contract tests compare its summary/non-summary/KB-boundary outputs against `fastQA`

Update `patent/server/patent/pdf_service.py` to use the new helpers for:

1. prompt construction
2. multi-PDF text formatting
3. compare-aware answer mode selection
4. removal of the current extractive compare degradation path

- [ ] **Step 4: Run the targeted tests to green**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -k "compare or multi_pdf or fallback or kb or summary or prompt"
```

Expected: PASS.

- [ ] **Step 5: Commit task 1**

```bash
git add patent/server/patent/pdf_contract.py patent/server/patent/pdf_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py
git commit -m "feat: align patent pdf qa compare contract"
```

---

### Task 2: Multi-PDF Truncation Guarantees

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/test_patent_file_routes.py`

- [ ] **Step 1: Write the failing truncation tests**

Add tests for:

1. each compared document retains a stable file identifier in final model input
2. each compared document retains at least one summary/introduction slice
3. each compared document retains at least one results/discussion/conclusion slice
4. low-value front matter does not displace compare-relevant evidence from later documents
5. when these minima cannot be met, the compare path fails explicitly instead of generating
6. truncation output remains contract-compatible with `fastQA/app/modules/qa_pdf/truncation.py` for the shared multi-doc structure

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -k "truncation or compare_budget or minimum_context"
```

Expected: FAIL because truncation minima and explicit insufficiency handling are not implemented yet.

- [ ] **Step 3: Implement minimal truncation guarantees**

Update `patent/server/patent/pdf_service.py` to:

1. use `fastQA`-compatible multi-doc headers as truncation anchors
2. enforce per-document minimum context slices before generation
3. prioritize theme/method/result/conclusion evidence over low-value front matter
4. expose a detectable failure path when budget is insufficient
5. prove truncation parity against `fastQA/app/modules/qa_pdf/truncation.py` via explicit contract tests, not only local invariants

- [ ] **Step 4: Run the targeted tests to green**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py -k "truncation or compare_budget or minimum_context"
```

Expected: PASS.

- [ ] **Step 5: Commit task 2**

```bash
git add patent/server/patent/pdf_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py
git commit -m "feat: harden patent multi-pdf truncation"
```

---

### Task 3: PDF Streaming, Steps, And Failure-State Parity

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write the failing streaming/steps tests**

Add tests for:

1. metadata and step frames arrive before content chunks
2. file responses retain `dispatch` and `context_ready` in the required step spine at both top-level `steps` and `metadata.steps`
3. compare route emits the required compare-specific step names and state transitions
4. `metadata.steps` equals top-level `steps` in sync responses
5. stream and sync results end with the same final step states
6. compare failure emits failure step before terminal event
7. compare failure does not leave a misleading success-looking partial answer body
8. PDF runtime/streaming-step behavior stays contract-compatible with `fastQA/app/modules/qa_pdf/engine.py` for first metadata, step ordering, and terminal-state consistency

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -k "compare or stream or step or metadata"
```

Expected: FAIL because compare-specific stream ordering and step-state parity are not implemented yet.

- [ ] **Step 3: Implement minimal streaming/step changes**

Update `patent/server/patent/pdf_service.py` so PDF compare generation:

1. emits metadata/steps before answer chunks
2. preserves the full required step spine, including `dispatch` and `context_ready`, when file-route steps are merged back into both top-level `steps` and `metadata.steps`
3. carries the required compare-specific steps and error states
4. keeps `metadata.steps` and top-level `steps` aligned
5. turns compare/truncation failure into explicit failure state, not misleading partial success
6. for compare streams, allows buffering answer text until compare success is confirmed when that is necessary to avoid leaking misleading partial compare bodies on mid-stream failure
7. proves runtime/stream parity against `fastQA/app/modules/qa_pdf/engine.py` via explicit contract tests where direct helper reuse is not possible

- [ ] **Step 4: Run the targeted tests to green**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -k "compare or stream or step or metadata"
```

Expected: PASS.

- [ ] **Step 5: Commit task 3**

```bash
git add patent/server/patent/pdf_service.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: align patent pdf streaming steps"
```

---

### Task 4: Hybrid Unified Synthesis, Real KB Handoff, And Source Precedence

**Files:**
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write the failing hybrid tests**

Add tests for:

1. `pdf+kb` returns a unified answer instead of a shell plus downstream note
2. `pdf+table+kb` returns a unified synthesized answer, not `PDF 部分 / 表格部分`
3. unified synthesis receives `source_scope` and KB citation/explanation fields
4. KB cannot silently replace PDF/table claims when sources disagree
5. when PDF is silent, KB cannot be relabeled as a PDF conclusion
6. hybrid stream content equals final `answer_text`
7. hybrid metadata/steps arrive before content chunks
8. hybrid `metadata.steps` equals top-level `steps`
9. hybrid stream and sync results end with the same final step states
10. hybrid emits required evidence-prep and final-synthesis steps with valid state transitions
11. hybrid failure emits failure step before terminal event and does not leave a misleading success-looking body
12. `executor.py` no longer appends `Patent KB participation:` after a finished file answer for `pdf+kb` / `pdf+table+kb`
13. unified synthesis input fields are locked by contract tests against the required `fastQA` field set: PDF evidence, tabular execution result, KB evidence, KB reference instruction, and `source_scope`
14. hybrid responses retain the full required step spine, including `dispatch` and `context_ready`, in both top-level `steps` and `metadata.steps`

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -k "hybrid or unified or precedence or source_scope or kb or step or stream or failure"
```

Expected: FAIL because the current implementation still shells together file sections and lacks enforceable precedence/conflict handling.

- [ ] **Step 3: Implement minimal unified synthesis**

Update `patent/server/patent/file_routes.py`, `patent/server/patent/tabular_service.py`, `patent/server/patent/executor.py`, and `patent/server/patent/kb_service.py` so hybrid generation:

1. passes PDF evidence, tabular execution results, KB evidence, KB citation/explanation context, and `source_scope` into a single synthesis path
2. removes the current shell text and the current downstream-KB append path in `executor.py`
3. enforces PDF/table precedence over KB for file-grounded claims
4. states conflicts explicitly when sources disagree
5. preserves matching stream/sync answer text and steps
6. for unified local synthesis paths, emits final answer chunks before the final success terminal step even when the answer body is assembled locally rather than token-streamed from a downstream model
7. proves the hybrid synthesis input contract matches the `fastQA`-required field set via direct contract tests

- [ ] **Step 4: Run the targeted tests to green**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -k "hybrid or unified or precedence or source_scope or kb or step or stream or failure"
```

Expected: PASS.

- [ ] **Step 5: Commit task 4**

```bash
git add patent/server/patent/file_routes.py patent/server/patent/tabular_service.py patent/server/patent/executor.py patent/server/patent/kb_service.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: unify patent hybrid file synthesis"
```

---

### Task 5: End-To-End Contract Hardening

**Files:**
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write the failing end-to-end regressions**

Add adversarial compare/hybrid fixtures covering all acceptance scenarios:

1. single PDF summary in patent mode
2. two-PDF compare with distinct facts from both documents
3. compare failure when one selected PDF is empty or unreadable
4. PDF + KB hybrid unified answer
5. PDF + table + KB hybrid unified answer
6. negative compare case where multiple PDFs are selected but the question targets only one file
7. prompt snapshot/contract parity assertions for summary, non-summary, compare, and `include_kb=true`
8. final model input or debug artifact proves both compared documents reached generation
9. compare answer follows `各自概要 + 相同点 + 差异点 + 总结`
10. non-compare PDF QA still preserves `fastQA` summary/non-summary behavior and `include_kb=true` verification-only boundaries
11. file and hybrid responses retain `dispatch` and `context_ready` in both top-level `steps` and `metadata.steps`
12. Task 3/Task 4 touched modules cannot silently drop `context_ready` during executor/HTTP response assembly even if the step is produced outside `pdf_service.py`

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py -k "adversarial or distinct_facts or empty_pdf or snapshot or summary or hybrid or compare or negative"
```

Expected: FAIL until all acceptance paths and parity checks are wired through.

- [ ] **Step 3: Finish the minimal code/test adjustments**

Patch any remaining executor or contract-shape mismatches so:

1. both documents materially contribute to compare answers
2. final metadata exposes the repaired answer modes
3. stream and sync paths stay contract-compatible
4. acceptance cases match the spec matrix exactly

- [ ] **Step 4: Run the full targeted regression suite**

Run:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py
```

Expected: PASS.

- [ ] **Step 5: Commit task 5**

```bash
git add patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "test: harden patent file qa regressions"
```

---

## Review Loop Per Task

After each task:

1. Run the task verification command
2. Dispatch a code-review subagent with the task goal, touched files, and relevant spec sections
3. If issues are found, fix them one by one
4. Re-run the same tests
5. Re-dispatch the reviewer until it passes
6. Only then move to the next task

## Final Verification

Before claiming the feature is done:

```bash
cd /home/cqy/worktrees/highThinking/patent && ./scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py
```

And, if task scope expands beyond the targeted suite, run the broader patent test sweep required by the resulting diff.

All verification commands in this plan must be run in the approved privileged environment when needed; do not claim the suite passed from an unapproved sandbox-only run.
