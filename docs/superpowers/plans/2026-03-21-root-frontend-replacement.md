# Root Frontend Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy root `frontend-vue` with the current `gateway/frontend-vue`, keep the legacy UI as a local ignored backup, and align docs/scripts to the new root frontend location.

**Architecture:** Treat the current gateway frontend as the canonical UI. Move the legacy root frontend out of the tracked app path into a repository-local ignored backup directory, then promote the gateway frontend into the root `frontend-vue` path without changing its proxy target (`8101`). Update only the path references that control real usage and maintenance.

**Tech Stack:** Vue 3, Vite, FastAPI gateway, shell scripts, repository docs.

---

### Task 1: Inventory and backup contract

**Files:**
- Modify: `.gitignore`
- Create: `archive/frontend-vue-legacy/` (ignored local backup)
- Reference: `frontend-vue/`
- Reference: `gateway/frontend-vue/`

- [ ] **Step 1: Add ignored local backup path**
- [ ] **Step 2: Verify the ignored backup path does not get staged**
- [ ] **Step 3: Move the existing root `frontend-vue` into `archive/frontend-vue-legacy/`**

### Task 2: Promote the gateway frontend to the root

**Files:**
- Move: `gateway/frontend-vue/` -> `frontend-vue/`
- Delete: `gateway/frontend-vue/`

- [ ] **Step 1: Remove runtime-only directories from the move scope if needed (`node_modules`, `dist`, `.runtime`)**
- [ ] **Step 2: Move the canonical gateway frontend into the root `frontend-vue/` path**
- [ ] **Step 3: Confirm root `frontend-vue` now contains the gateway app files and proxy config**

### Task 3: Update operational references

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `gateway/README.md`
- Modify: `gateway/AGENTS.md`
- Modify: docs that explicitly describe `gateway/frontend-vue` as the active frontend

- [ ] **Step 1: Update root docs to say the active frontend is root `frontend-vue` on `5173`**
- [ ] **Step 2: Update gateway docs to describe frontend ownership after the move**
- [ ] **Step 3: Update any migration docs that would now mislead maintenance work**

### Task 4: Verify the moved frontend

**Files:**
- Verify: `frontend-vue/package.json`
- Verify: `frontend-vue/vite.config.js`

- [ ] **Step 1: Run `npm run build` from root `frontend-vue`**
- [ ] **Step 2: Verify Vite still proxies `/api` to `http://127.0.0.1:8101`**
- [ ] **Step 3: Check `git status` to confirm backup remains untracked/ignored and the move is represented correctly**
