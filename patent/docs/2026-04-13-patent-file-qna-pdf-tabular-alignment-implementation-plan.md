# Patent File Q&A PDF/Tabular Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `patent` file-Q&A so the PDF path uses a FastQA-like local extractor and the tabular path uses a structure-first local pipeline, without modifying `fastQA` or widening scope beyond `patent` file-Q&A.

**Architecture:** Phase A introduces a new `patent`-local PDF extraction module and rewires only file-Q&A PDF consumers to use it, while leaving non-file paths and `stage3` evidence loading untouched. Phase B introduces a new `patent/server/patent/tabular/` pipeline with loader, profiler, planner, executor, and renderer layers, then keeps `PatentTabularService` as the orchestration boundary consumed by `tabular_qa` and file-scoped `hybrid_qa`.

**Tech Stack:** Python, PyMuPDF, CSV/XML/ZIP parsing, pytest, existing `patent` file-route orchestration, existing `patent` answer-generation clients.

---

## Constraints And References

**Hard constraints:**
- Only modify files under `patent/`
- Do not modify any file under `fastQA/`
- Do not import runtime code from `fastQA/`
- Do not change `patent` ordinary non-file asks
- Do not change `patent` `kb_qa`
- Do not change `gateway/`, `public-service/`, or `highThinkingQA/`
- Do not widen Phase A into `patent/server/patent/stages/evidence_loading.py` or any other non-file-Q&A fallback path
- Execute all pytest commands outside the sandbox with escalated permissions when implementation starts

**Primary references:**
- Spec: `patent/docs/2026-04-13-patent-file-qna-pdf-tabular-alignment-spec.md`
- Current PDF service: `patent/server/patent/pdf_service.py`
- Current file-route orchestrator: `patent/server/patent/file_routes.py`
- Current tabular service: `patent/server/patent/tabular_service.py`
- Current PDF contracts: `patent/server/patent/pdf_contract.py`
- FastQA PDF reference only: `fastQA/app/modules/qa_pdf/pdf_extractor.py`
- FastQA tabular references only:
  - `fastQA/app/modules/qa_tabular/workbook_loader.py`
  - `fastQA/app/modules/qa_tabular/schema_profiler.py`
  - `fastQA/app/modules/qa_tabular/planner.py`
  - `fastQA/app/modules/qa_tabular/executor.py`
  - `fastQA/app/modules/qa_tabular/renderer.py`
  - `fastQA/app/modules/qa_tabular/service.py`

## File Structure Map

**Files to create**
- `patent/server/patent/pdf_extraction.py`
- `patent/server/patent/tabular/__init__.py`
- `patent/server/patent/tabular/workbook_loader.py`
- `patent/server/patent/tabular/schema_profiler.py`
- `patent/server/patent/tabular/planner.py`
- `patent/server/patent/tabular/executor.py`
- `patent/server/patent/tabular/renderer.py`
- `patent/tests/test_patent_pdf_extraction.py`
- `patent/tests/test_patent_tabular_workbook_loader.py`
- `patent/tests/test_patent_tabular_schema_profiler.py`
- `patent/tests/test_patent_tabular_planner.py`
- `patent/tests/test_patent_tabular_executor_renderer.py`

**Files to modify**
- `patent/server/patent/pdf_service.py`
- `patent/server/patent/tabular_service.py`
- `patent/server/patent/file_routes.py`
- `patent/tests/test_patent_executor.py`
- `patent/tests/test_patent_file_routes.py`
- `patent/tests/test_patent_pdf_contract.py`
- `patent/tests/fastapi_contract/test_ask_contract.py`

**Files explicitly out of scope**
- `patent/server/patent/stages/evidence_loading.py`
- everything under `fastQA/`
- any `patent` KB-only or ordinary non-file QA module

## Verification Discipline

All implementation-time test commands below must be run with escalated permissions. Use the same cache/temp routing pattern everywhere:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest ...
```

Phase A must be shippable on its own before Phase B starts.

## Task 1: Add A Patent-Local PDF Extractor Module

**Files:**
- Create: `patent/server/patent/pdf_extraction.py`
- Create: `patent/tests/test_patent_pdf_extraction.py`

- [ ] **Step 1: Write failing extractor unit tests**

```python
def test_extract_pdf_text_preserves_page_boundaries_and_metadata():
    result = extract_pdf_text(
        "/tmp/mock.pdf",
        max_pages=3,
        pdf_support=True,
        fitz_module=fake_fitz,
        logger=fake_logger,
        traceback_module=traceback,
    )
    assert "标题: Sample Title" in result
    assert "--- 第 1 页 ---" in result
    assert "--- 第 2 页 ---" in result
    assert "Second page paragraph" in result


def test_extract_pdf_text_excludes_reference_tail_when_signal_is_strong():
    kept = exclude_references_section(
        [(1, "正文内容"), (2, "References\n10.1000/1\n10.1000/2\n10.1000/3\nhttps://a\nhttps://b\nhttps://c")],
        fake_logger,
    )
    assert kept == [(1, "正文内容")]


def test_extract_pdf_text_keeps_suspected_reference_page_when_signal_is_weak():
    kept = exclude_references_section([(1, "正文"), (2, "References\none citation only")], fake_logger)
    assert len(kept) == 2
```

- [ ] **Step 2: Run the extractor test file and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_extraction.py -q`
Expected: FAIL because `pdf_extraction.py` does not exist yet

- [ ] **Step 3: Implement the local extractor module**

Implement in `patent/server/patent/pdf_extraction.py`:
- `exclude_references_section(...)`
- `extract_pdf_text(...)`
- default `max_pages=50`
- optional `exclude_references=True`
- metadata retention for title/author when present
- page-delimited assembly using `--- 第 N 页 ---`
- dependency-injected `fitz_module`, `logger`, and `traceback_module` for testability

Do not import from `fastQA`; copy only the behavior shape into local `patent` code.

- [ ] **Step 4: Re-run the extractor unit tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_extraction.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Record that Phase A now has a `patent`-local extractor implementation, but no file-Q&A service is wired to it yet.

## Task 2: Rewire File-Q&A PDF Loading Without Touching Stage3

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Add failing service-level tests for Phase A scope and downstream reachability**

```python
def test_pdf_service_default_extractor_uses_new_file_qna_extractor(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr("server.patent.pdf_service.extract_file_qa_pdf_text", lambda path, max_pages=50: called.setdefault("max_pages", max_pages) or "A")
    service = PatentPdfService()
    service._load_pdf_documents(execution_files=[fake_pdf_item(tmp_path / "a.pdf")])
    assert called["max_pages"] == 50


def test_pdf_service_keeps_injected_extract_pdf_text_fn_contract(tmp_path):
    service = PatentPdfService(extract_pdf_text_fn=lambda path, max_pages=10: "Injected")
    docs = service._load_pdf_documents(execution_files=[fake_pdf_item(tmp_path / "a.pdf")])
    assert docs[0]["text"] == "Injected"


def test_multi_pdf_compare_pipeline_keeps_tail_sections_reachable_after_preparation():
    prepared = service._prepare_answer_input(
        question="比较两篇文献的方法和结论",
        pdf_text=formatted_text_with_long_front_matter_and_tail_sections,
        pdf_documents=docs,
        selected_file_labels=["paper-a.pdf", "paper-b.pdf"],
        available_file_labels=["paper-a.pdf", "paper-b.pdf"],
        compare_mode=True,
    )
    assert "Results" in prepared["prepared_pdf_text"]
    assert "Conclusion" in prepared["prepared_pdf_text"]
```

- [ ] **Step 2: Run only the Phase A regression targets and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_extraction.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_executor.py -q`
Expected: FAIL because `PatentPdfService` still uses the embedded 10-page whitespace-collapsing extractor

- [ ] **Step 3: Rewire `PatentPdfService` to the new extractor, but keep legacy non-file hooks isolated**

Implement in `patent/server/patent/pdf_service.py`:
- import the new local extractor module
- add a file-Q&A-specific default extractor entrypoint, for example `extract_file_qa_pdf_text(...)`
- change `PatentPdfService.__init__` to use that file-Q&A entrypoint as the default injected function
- raise file-Q&A default page budget from `10` to a FastQA-like budget
- keep `PatentPdfService(extract_pdf_text_fn=...)` fully supported
- keep the legacy static `_extract_pdf_text(...)` compatibility surface untouched so `stage3` fallback paths do not change in this phase

- [ ] **Step 4: Extend route and contract coverage**

Update focused tests so they prove:
- single-PDF summary uses the richer extractor output
- multi-PDF compare keeps per-document headers and tail evidence
- file-only `hybrid_qa` still receives PDF evidence context after the extractor change
- `/api/ask` and `/api/ask_stream` keep the same outer payload shape for `pdf_qa` and file-only `hybrid_qa`

- [ ] **Step 5: Re-run the Phase A verification set**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_extraction.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/pdf_extraction.py patent/server/patent/pdf_service.py patent/tests/test_patent_pdf_extraction.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: align patent file qna pdf extraction"
```

## Task 3: Add Tabular Workbook Loading And Profiling Layers

**Files:**
- Create: `patent/server/patent/tabular/__init__.py`
- Create: `patent/server/patent/tabular/workbook_loader.py`
- Create: `patent/server/patent/tabular/schema_profiler.py`
- Create: `patent/tests/test_patent_tabular_workbook_loader.py`
- Create: `patent/tests/test_patent_tabular_schema_profiler.py`

- [ ] **Step 1: Write failing unit tests for workbook normalization and profile generation**

```python
def test_load_workbook_cached_reads_csv_into_sheet_rows(tmp_path):
    workbook = load_workbook_cached(path=str(tmp_path / "metrics.csv"), file_name="metrics.csv", file_type="csv")
    assert workbook["sheets"][0]["sheet_name"] == "Sheet1"
    assert workbook["sheets"][0]["rows"][0]["column_a"] == "header value"


def test_profile_workbook_marks_numeric_and_text_columns():
    profile = profile_workbook(sample_workbook)
    sheet = profile["sheets"][0]
    assert "Capacity" in sheet["numeric_columns"]
    assert "Material" in sheet["text_columns"]
```

- [ ] **Step 2: Run the new unit files and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_tabular_workbook_loader.py patent/tests/test_patent_tabular_schema_profiler.py -q`
Expected: FAIL because the new tabular modules do not exist yet

- [ ] **Step 3: Implement the loader and profiler layers**

Implement in `patent/server/patent/tabular/workbook_loader.py`:
- CSV/XLS/XLSX/XLSM loading
- normalized workbook payload with file metadata and sheet list
- sheet row dictionaries with stable column names
- thin local caching if needed, but no external shared runtime

Implement in `patent/server/patent/tabular/schema_profiler.py`:
- per-sheet column inventory
- numeric/text/date-like heuristics
- row counts and sample values
- missingness summaries sufficient for plan construction and rendering

- [ ] **Step 4: Re-run the loader/profiler unit tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_tabular_workbook_loader.py patent/tests/test_patent_tabular_schema_profiler.py -q`
Expected: PASS

- [ ] **Step 5: Checkpoint**

Record that the workbook/profile layers exist, but `PatentTabularService` still does not consume them.

## Task 4: Add Tabular Planning And Execution Layers

**Files:**
- Create: `patent/server/patent/tabular/planner.py`
- Create: `patent/server/patent/tabular/executor.py`
- Create: `patent/tests/test_patent_tabular_planner.py`
- Create: `patent/tests/test_patent_tabular_executor_renderer.py`

- [ ] **Step 1: Write failing planner and executor tests**

```python
def test_plan_tabular_query_prefers_metric_and_filter_columns_from_profile():
    plan = plan_tabular_query(question="比较不同材料的容量均值", profile=sample_profile)
    assert plan["operation"] in {"aggregate", "compare"}
    assert "Capacity" in plan["metric_columns"]
    assert plan["group_by"] == "Material"


def test_execute_tabular_plan_returns_rows_and_summary_stats():
    result = execute_tabular_plan(workbook=sample_workbook, plan=sample_plan)
    assert result["row_count"] > 0
    assert "summary_stats" in result
```

- [ ] **Step 2: Run the planner/executor tests and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_tabular_planner.py patent/tests/test_patent_tabular_executor_renderer.py -q`
Expected: FAIL because planner and executor implementations do not exist yet

- [ ] **Step 3: Implement local plan and execution behavior**

Implement in `patent/server/patent/tabular/planner.py`:
- question-to-sheet matching
- column hint resolution
- filter extraction
- structured operation plans for summary, aggregate, compare, and row lookup cases

Implement in `patent/server/patent/tabular/executor.py`:
- single-workbook execution for the supported plan types
- stable result object containing rows, matched sheet name, and summary stats
- deterministic empty-result shape for rendering fallback

- [ ] **Step 4: Re-run planner/executor tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_tabular_planner.py patent/tests/test_patent_tabular_executor_renderer.py -q`
Expected: PASS for the planner/executor-targeted tests

- [ ] **Step 5: Checkpoint**

Record that `patent` now has structured tabular planning/execution modules, but rendering and service orchestration still need rewiring.

## Task 5: Add Tabular Renderer And Rewire `PatentTabularService`

**Files:**
- Create: `patent/server/patent/tabular/renderer.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/test_patent_tabular_executor_renderer.py`

- [ ] **Step 1: Write failing service-level tests for structure-first tabular execution**

```python
def test_tabular_service_builds_table_execution_context_from_plan_result(tmp_path):
    service = PatentTabularService(answer_question_fn=fake_answer)
    result = service.execute(contract=sample_table_contract(tmp_path), include_kb=False)
    assert result["metadata"]["answer_mode"] == "table_execution_summary"
    assert "匹配工作表" in result["metadata"]["table_evidence_context"]


def test_tabular_service_keeps_outer_payload_shape_for_tabular_route(tmp_path):
    result = service.execute(contract=sample_table_contract(tmp_path), include_kb=False)
    assert result["handler"] == "tabular"
    assert result["route"] == "tabular_qa"
    assert result["query_mode"] == "patent_tabular_qa"
```

- [ ] **Step 2: Run tabular service and route regressions and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_tabular_executor_renderer.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL because `PatentTabularService` still builds answers from lightweight extracted table text

- [ ] **Step 3: Implement renderer output and service orchestration**

Implement in `patent/server/patent/tabular/renderer.py`:
- stable execution-context rendering
- answer prompt context builder
- readable fallback answer when execution returns no usable rows

Rework `patent/server/patent/tabular_service.py` so it:
- loads workbook data through the new loader
- profiles workbook schema
- plans the operation
- executes the plan
- renders stable `table_evidence_context`
- keeps `PatentTabularService` as the orchestration boundary and answer-stream owner
- preserves current `execute(...)` outer response shape

- [ ] **Step 4: Re-run tabular route verification**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_tabular_workbook_loader.py patent/tests/test_patent_tabular_schema_profiler.py patent/tests/test_patent_tabular_planner.py patent/tests/test_patent_tabular_executor_renderer.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS for `tabular_qa` direct-path coverage

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular/__init__.py patent/server/patent/tabular/workbook_loader.py patent/server/patent/tabular/schema_profiler.py patent/server/patent/tabular/planner.py patent/server/patent/tabular/executor.py patent/server/patent/tabular/renderer.py patent/server/patent/tabular_service.py patent/tests/test_patent_tabular_workbook_loader.py patent/tests/test_patent_tabular_schema_profiler.py patent/tests/test_patent_tabular_planner.py patent/tests/test_patent_tabular_executor_renderer.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: align patent tabular execution pipeline"
```

## Task 6: Upgrade File-Only Hybrid Integration To Consume Stronger Table Artifacts

**Files:**
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Add failing hybrid tests that prove the new table pipeline is actually consumed**

```python
def test_file_only_hybrid_route_uses_rendered_table_execution_context_not_raw_sheet_dump(tmp_path):
    result = dispatch_patent_file_route(...)
    contract = result["metadata"]["synthesis_contract"]
    assert "匹配工作表" in contract["table_execution_context"]
    assert "文件: data.xlsx" not in contract["table_execution_context"]


def test_http_sync_hybrid_pdf_table_route_keeps_unified_answer_shape_after_tabular_upgrade():
    body = client.post("/api/ask", json=_hybrid_payload("pdf+table")).json()
    assert body["route"] == "hybrid_qa"
    assert body["metadata"]["answer_mode"] == "hybrid_unified_synthesis"


def test_http_stream_hybrid_pdf_table_route_keeps_step_order_and_content_streaming():
    events = collect_stream_events(...)
    assert [event["step"] for event in events if event["type"] == "step"][-1] == "hybrid_answer"
```

- [ ] **Step 2: Run focused hybrid regressions and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: FAIL because hybrid synthesis still assumes the old loose `table_text`-style evidence shape

- [ ] **Step 3: Update hybrid metadata plumbing**

Modify `patent/server/patent/file_routes.py` and `patent/server/patent/tabular_service.py` so:
- file-only `hybrid_qa` receives the rendered execution context from the new tabular pipeline
- `table_evidence_context` remains a stable string field for synthesis compatibility
- step ordering, `used_files`, `query_mode`, and `answer_mode` remain unchanged externally
- no KB-only path is touched

- [ ] **Step 4: Run the full in-scope regression set**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_extraction.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_tabular_workbook_loader.py patent/tests/test_patent_tabular_schema_profiler.py patent/tests/test_patent_tabular_planner.py patent/tests/test_patent_tabular_executor_renderer.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py patent/tests/fastapi_contract/test_ask_contract.py -q`
Expected: PASS

- [ ] **Step 5: Final checkpoint**

Record that:
- Phase A shipped without changing `stage3` or ordinary QA
- Phase B upgraded `tabular_qa` and file-only `hybrid_qa`
- `fastQA` remained untouched
- remaining non-file and KB-only behavior stayed unchanged by test evidence

## Delivery Order

1. Complete Task 1 and Task 2 first. Do not start Phase B until the Phase A regression set is green.
2. Complete Task 3, Task 4, and Task 5 to land direct `tabular_qa` parity.
3. Complete Task 6 last so hybrid integration is validated against the already-upgraded PDF and tabular paths.

## Review Handoff

After saving this plan:

1. Dispatch the plan document to the dedicated reviewer subagent with the spec path and this plan path only.
2. If the reviewer finds issues, fix the document in place and resend the full document to the same reviewer.
3. Only proceed to implementation after the reviewer returns `No findings` or equivalent approval.
