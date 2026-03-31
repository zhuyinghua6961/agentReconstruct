# Upload Selection Behavior Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a newly uploaded file immediately become the only selected file in the frontend upload panel.

**Architecture:** Keep the behavior isolated to the frontend file-selection utility so PDF and Excel uploads share one rule. Update the utility tests first, then switch upload handling to reuse the new utility behavior without touching backend routing.

**Tech Stack:** Vue 3, Vite, Node test runner

---

### Task 1: File selection utility

**Files:**
- Modify: `frontend-vue/src/utils/fileSelection.js`
- Test: `frontend-vue/src/utils/fileSelection.test.js`

- [ ] Write failing tests for upload replacing previous selections
- [ ] Run the file selection tests and confirm failure
- [ ] Implement minimal utility change
- [ ] Run file selection tests and broader frontend tests
