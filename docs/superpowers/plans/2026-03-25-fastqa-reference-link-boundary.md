# fastQA Reference Link Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口 fastQA 内部 `pdf_url/reference_links/pdf_links` 的重复实现，让所有 ask/ask_stream/preview 链路都通过统一门面构造引用链接，并消除 sync/stream contract drift。

**Architecture:** 延续上一轮 DOI 收口思路，把 `DOI -> pdf_url / links` 的纯构造能力统一到 `storage_service` 门面；`qa_kb`、`qa_pdf`、router、`reference_preview` 全部改为消费该门面。`storage_service.build_pdf_url()` 同时负责 DOI normalize 与 route-safe encode；router 对 outward done payload 始终从 normalized references 重建 links，不透传上游脏 links。

**Tech Stack:** Python, pytest, fastQA service modules, TDD refactor

---

### Task 1: 锁定统一链接门面的目标行为

**Files:**
- Modify: `fastQA/tests/test_documents_storage.py`
- Modify: `fastQA/app/modules/storage/service.py`

- [x] **Step 1: 写失败测试**

新增并锁定：
- `build_pdf_url("10.1/demo")` 保持常见 DOI 契约
- `build_pdf_url("(10.2%2Fdemo.pdf)")` 会先 normalize
- `build_pdf_url("10.2/demo?section#part")` 会做 route-safe encode
- `build_pdf_links()` 保持顺序与输出结构

- [x] **Step 2: 运行失败测试**

Run: `conda run -n agent pytest fastQA/tests/test_documents_storage.py -q`
Expected: 先看到 helper 缺失，再看到 normalize / encode 行为失败

- [x] **Step 3: 写最小实现**

实现结果：
- `storage_service.build_pdf_url()` 统一执行 `normalize_doi()` + segment encode
- `storage_service.build_pdf_links()` 统一调用 `build_pdf_url()`

- [x] **Step 4: 重新运行测试确认通过**

Run: `conda run -n agent pytest fastQA/tests/test_documents_storage.py -q`
Expected: PASS

### Task 2: 锁定 qa_kb / qa_pdf / router / preview 必须走统一门面

**Files:**
- Create: `fastQA/tests/test_reference_link_boundary.py`
- Modify: `fastQA/app/modules/qa_kb/streaming.py`
- Modify: `fastQA/app/modules/qa_pdf/common.py`
- Modify: `fastQA/app/routers/qa.py`
- Modify: `fastQA/app/modules/documents/reference_preview.py`

- [x] **Step 1: 写失败测试，锁定调用方走 storage 门面**

新增并锁定：
- `qa_kb` done event 使用 `storage_service.build_pdf_links()`
- `qa_pdf` done payload 使用 `storage_service.build_pdf_links()`
- `reference_preview_item` 使用 `storage_service.build_pdf_url()`
- router `_done_event()` 使用统一门面
- router `_collect_sync_result()` 使用统一门面

- [x] **Step 2: 运行定向测试确认失败**

Run:
- `conda run -n agent pytest fastQA/tests/test_reference_link_boundary.py -q`

Expected: FAIL，说明调用方仍在本地拼接 URL

- [x] **Step 3: 写最小实现，把调用方切到统一门面**

实现结果：
- `qa_kb/streaming.py` 删除本地 `build_reference_links()`
- `qa_pdf/common.py` 删除本地 `build_pdf_links()`
- `reference_preview.py` 的 `build_pdf_url()` 改调 `storage_service.build_pdf_url()`
- `routers/qa.py` 改调统一门面

- [x] **Step 4: 重新运行定向测试确认通过**

Run:
- `conda run -n agent pytest fastQA/tests/test_reference_link_boundary.py -q`

Expected: PASS

### Task 3: 锁定 router 最终出站契约与 pdf_qa 兼容性

**Files:**
- Modify: `fastQA/tests/test_reference_link_boundary.py`
- Modify: `fastQA/app/routers/qa.py`
- Verify: `fastQA/tests/test_qa_routes_file_modes.py`

- [x] **Step 1: 写失败测试，覆盖 stream/sync drift 风险**

新增并锁定：
- `pdf_qa` 的 sync ask 与 stream done 使用同一套 links
- stream done 遇到上游自带脏 `reference_links/pdf_links` 时，router 必须覆盖重建

- [x] **Step 2: 运行失败测试**

Run:
- `conda run -n agent pytest fastQA/tests/test_reference_link_boundary.py -q`

Expected: FAIL，stream path 还在保留上游 links

- [x] **Step 3: 写最小实现**

实现结果：
- router stream done 不再 `setdefault("reference_links"/"pdf_links")`
- 改为始终按 normalized references 重建 authoritative links

- [x] **Step 4: 重新运行测试确认通过**

Run:
- `conda run -n agent pytest fastQA/tests/test_reference_link_boundary.py -q`
- `conda run -n agent pytest fastQA/tests/test_qa_routes_file_modes.py -q`

Expected: PASS

### Task 4: 做边界回归并核对兼容性

**Files:**
- Test: `fastQA/tests/test_documents_storage.py`
- Test: `fastQA/tests/test_reference_link_boundary.py`
- Test: `fastQA/tests/test_qa_kb_models.py`
- Test: `fastQA/tests/test_qa_placeholder.py`
- Test: `fastQA/tests/test_documents.py`
- Test: `fastQA/tests/test_documents_router.py`
- Test: `fastQA/tests/test_qa_routes_file_modes.py`

- [x] **Step 1: 运行完整定向回归**

Run:
- `conda run -n agent pytest fastQA/tests/test_documents_storage.py -q fastQA/tests/test_reference_link_boundary.py -q`
- `conda run -n agent pytest fastQA/tests/test_qa_kb_models.py -q fastQA/tests/test_qa_placeholder.py -q fastQA/tests/test_documents.py -q fastQA/tests/test_documents_router.py -q fastQA/tests/test_qa_routes_file_modes.py -q`

Expected: 全部 PASS

- [x] **Step 2: 用可执行回归替代人工口头检查**

已锁定：
- `reference_links` 继续存在
- `pdf_links` 继续存在
- 常见 DOI 仍为 `/api/v1/view_pdf/<doi>`
- sync ask 与 stream done 一致
- `pdf_qa`、`kb_qa`、preview 三条链路一致

- [x] **Step 3: 更新文档和计划勾选状态**

记录：
- 统一出口放在 `fastQA/app/modules/storage/service.py`
- 被删除的重复函数：`qa_kb/streaming.build_reference_links`、`qa_pdf/common.build_pdf_links`
- 被收口的重复逻辑：router 出站 link 构造、preview URL 构造
