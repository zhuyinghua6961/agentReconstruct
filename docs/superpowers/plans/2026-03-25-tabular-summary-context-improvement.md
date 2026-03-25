# Tabular Summary Context Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve `summary`-mode tabular QA so the LLM receives structured whole-table statistics instead of only a 5-row sample.

**Architecture:** Keep the current full-table execution path unchanged. Enrich the `summary` execution result with structured table statistics and render those statistics into the LLM prompt while preserving bounded prompt size.

**Tech Stack:** Python, pytest, pandas-backed tabular executor, existing fastQA tabular renderer.

---

### Task 1: Lock current deficiency with tests

**Files:**
- Modify: `fastQA/tests/test_qa_tabular.py` or nearest tabular test file
- Test: `fastQA/tests/test_qa_tabular.py`

- [ ] Step 1: Add failing tests for summary context/statistics
- [ ] Step 2: Run focused pytest to verify RED
- [ ] Step 3: Implement minimal executor/renderer changes
- [ ] Step 4: Re-run focused pytest to verify GREEN
- [ ] Step 5: Run adjacent tabular tests

### Task 2: Enrich summary execution payload

**Files:**
- Modify: `fastQA/app/modules/qa_tabular/executor.py`

- [ ] Step 1: Add structured whole-table summary fields for `summary`
- [ ] Step 2: Keep sample rows bounded but representative enough for prompt usage
- [ ] Step 3: Preserve existing non-summary operations untouched

### Task 3: Render better summary context to LLM

**Files:**
- Modify: `fastQA/app/modules/qa_tabular/renderer.py`

- [ ] Step 1: Add summary-specific rendering for whole-table stats
- [ ] Step 2: Explicitly mark rows as samples, not full data
- [ ] Step 3: Keep prompt concise and bounded

### Task 4: Verify and document

**Files:**
- Modify: `docs/audit/2026-03-25-tabular-summary-context-improvement-spec.md`

- [ ] Step 1: Run focused tests
- [ ] Step 2: Run adjacent tabular tests
- [ ] Step 3: Update spec with implemented scope if needed
