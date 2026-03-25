# fastQA DOI Normalization Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口 fastQA 内部重复的 DOI 规范化实现，让 documents 域统一通过 storage service 入口消费 DOI 规范化能力。

**Architecture:** 保持 storage 域继续拥有真实 paper 资产逻辑与 DOI 规范化；在 storage service 增加 `normalize_doi()` 门面，并移除 documents service 的本地重复实现。只做边界收口，不动 generation pipeline 的活链路。

**Tech Stack:** Python, pytest, fastQA service modules, TDD refactor

---

### Task 1: 建立 storage DOI 门面测试

**Files:**
- Modify: `fastQA/tests/test_documents_storage.py`
- Modify: `fastQA/app/modules/storage/service.py`

- [x] **Step 1: 写失败测试，要求 `storage_service.normalize_doi()` 存在并对齐 paper_storage 语义**
- [x] **Step 2: 运行 `pytest fastQA/tests/test_documents_storage.py -q` 确认失败**
- [x] **Step 3: 在 `storage/service.py` 增加最小门面实现**
- [x] **Step 4: 重新运行 `pytest fastQA/tests/test_documents_storage.py -q` 确认通过**

### Task 2: 锁定 documents 必须走 storage 门面

**Files:**
- Modify: `fastQA/tests/test_documents.py`
- Modify: `fastQA/app/modules/documents/service.py`

- [x] **Step 1: 写失败测试，验证 documents service 通过 `storage_service.normalize_doi()` 处理 DOI**
- [x] **Step 2: 运行 `pytest fastQA/tests/test_documents.py -q` 确认失败**
- [x] **Step 3: 删除 documents 本地 `normalize_doi()`，切换到 storage 门面**
- [x] **Step 4: 重新运行 `pytest fastQA/tests/test_documents.py -q` 确认通过**

### Task 3: 定向回归

**Files:**
- Test: `fastQA/tests/test_documents_storage.py`
- Test: `fastQA/tests/test_documents.py`
- Optional check: `fastQA/tests/test_documents_router.py`

- [x] **Step 1: 运行 documents/storage 定向回归**
- [x] **Step 2: 检查没有误动 generation pipeline 活链路**
- [x] **Step 3: 更新计划勾选状态并总结结果**
