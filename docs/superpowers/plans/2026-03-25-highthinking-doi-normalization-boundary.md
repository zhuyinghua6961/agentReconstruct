# highThinkingQA DOI Normalization Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `highThinkingQA` 的 ask 主链路对 `server/storage/paper_storage.py` 的 DOI 规范化依赖迁移到本服务内的纯工具模块，同时保持行为不变。

**Architecture:** 本次不做跨服务共享包，只在 `highThinkingQA` 内新增纯 DOI 工具模块，让 `ask_service` 依赖它；`paper_storage.py` 继续保留 legacy 文件资产职责。通过定向测试锁定行为，避免引用格式、reference link 和前端答案适配发生回归。

**Tech Stack:** Python, pytest, FastAPI service code, local pure utility refactor

---

### Task 1: 建立 DOI 纯工具测试基线

**Files:**
- Create: `highThinkingQA/tests/test_doi_utils.py`
- Reference: `highThinkingQA/server/storage/paper_storage.py`
- Reference: `public-service/backend/app/modules/storage/service.py`

- [x] **Step 1: 写失败测试，覆盖 DOI 规范化核心样例**
- [x] **Step 2: 运行 `pytest highThinkingQA/tests/test_doi_utils.py -q` 确认失败**
- [x] **Step 3: 新增最小 `server/utils/doi.py` 实现**
- [x] **Step 4: 重新运行 `pytest highThinkingQA/tests/test_doi_utils.py -q` 确认通过**

### Task 2: 让 ask_service 改用纯 DOI 工具

**Files:**
- Modify: `highThinkingQA/server/services/ask_service.py`
- Modify: `highThinkingQA/tests/test_ask_service_executor.py`
- Reference: `highThinkingQA/server/storage/paper_storage.py`

- [x] **Step 1: 先给 `ask_service` 行为补足/锁定失败测试**
- [x] **Step 2: 运行 ask 相关定向测试确认红灯**
- [x] **Step 3: 把 `ask_service` import 改到 `server/utils/doi.py`**
- [x] **Step 4: 重新运行 ask 相关定向测试确认绿灯**

### Task 3: 核对 legacy 文件资产路径未被破坏

**Files:**
- Reference: `highThinkingQA/server/storage/paper_storage.py`
- Reference: `highThinkingQA/tests/test_documents_service.py`

- [x] **Step 1: 运行 `pytest highThinkingQA/tests/test_documents_service.py -q`**
- [x] **Step 2: 确认 legacy 文档服务样例仍通过**

### Task 4: 全量定向回归

**Files:**
- Test: `highThinkingQA/tests/test_doi_utils.py`
- Test: `highThinkingQA/tests/test_ask_service_executor.py`
- Test: `highThinkingQA/tests/test_documents_service.py`

- [x] **Step 1: 运行三组定向测试**
- [x] **Step 2: 检查无额外 import/path 回归**
- [x] **Step 3: 更新计划勾选状态并总结结果**
