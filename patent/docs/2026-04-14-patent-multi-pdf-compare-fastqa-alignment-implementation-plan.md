# Patent Multi-PDF Compare Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade only `patent` multi-PDF compare under `pdf_qa` so it retains more per-document evidence, generates richer compare answers, and streams safely, without modifying any `fastQA` code or changing any `patent` single-PDF behavior.

**Architecture:** Keep the entrypoint in `PatentPdfService`, but split the compare-only work into five controlled layers: compare budget/config gating, compare context preparation, compare prompt contract, compare answer normalization/quality gates, and compare streaming. All behavioral changes must be explicitly guarded by `compare_mode` so non-compare and single-PDF paths remain bit-for-bit compatible in outward behavior.

**Tech Stack:** Python, pytest, existing `patent` file-route orchestration, existing `PatentPdfService` / `pdf_contract` compare helpers, existing SSE/streaming route tests.

---

## Constraints And References

**Hard constraints:**
- Only modify `patent` compare logic for multi-PDF `pdf_qa`
- Do not modify any file under `fastQA/`
- Do not import runtime code from `fastQA/`
- Do not modify `patent` single-PDF summary behavior
- Do not modify `patent` single-PDF ordinary PDF Q&A behavior
- Do not modify `patent` `hybrid_qa` final synthesis behavior
- Do not modify `patent` KB-only QA
- Do not modify `patent` tabular behavior
- Any shared helper change must be gated by `compare_mode`
- All pytest commands during implementation must run with escalated permissions, never in sandbox

**Primary references:**
- Spec: `patent/docs/2026-04-14-patent-multi-pdf-compare-fastqa-alignment-spec.md`
- Compare service path: `patent/server/patent/pdf_service.py`
- Compare prompt/truncation path: `patent/server/patent/pdf_contract.py`
- Existing compare tests:
  - `patent/tests/test_patent_pdf_contract.py`
  - `patent/tests/test_patent_file_routes.py`
  - `patent/tests/test_patent_executor.py`
  - `patent/tests/fastapi_contract/test_ask_contract.py`

## File Structure Map

**Files to modify**
- `patent/server/patent/pdf_service.py`
- `patent/server/patent/pdf_contract.py`
- `patent/tests/test_patent_pdf_contract.py`
- `patent/tests/test_patent_file_routes.py`
- `patent/tests/test_patent_executor.py`
- `patent/tests/fastapi_contract/test_ask_contract.py`

**Files intentionally not modified**
- any file under `fastQA/`
- `patent/server/patent/executor.py`
- `patent/server/patent/file_routes.py`
- any `patent` KB or hybrid synthesis module
- any tabular module

## Verification Discipline

All implementation-time test commands below must be run with escalated permissions. Use the same cache/temp routing pattern everywhere:

```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest ...
```

The work must preserve these invariants throughout:

- multi-PDF compare uses compare-only settings
- multi-PDF non-compare targeted-document flow still works
- single-PDF summary remains unchanged
- single-PDF ordinary PDF Q&A remains unchanged

## Task 1: Separate Compare-Only Budget And Scope Guards

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/tests/test_patent_file_routes.py`

- [ ] **Step 1: Add failing tests for compare-only budget and non-compare isolation**

```python
def test_pdf_service_compare_mode_uses_dedicated_max_chars_budget(tmp_path):
    service = PatentPdfService(max_pdf_chars=12000)
    result = service._prepare_answer_input(
        question="比较这两篇文献的方法差异",
        pdf_text=very_long_multi_doc_text(),
        pdf_documents=two_pdf_docs(tmp_path),
        selected_file_labels=["paper-a.pdf", "paper-b.pdf"],
        available_file_labels=["paper-a.pdf", "paper-b.pdf"],
        compare_mode=True,
    )
    assert len(result["prepared_pdf_text"]) > 12000


def test_pdf_service_non_compare_multi_selected_question_still_targets_single_document(tmp_path):
    service = PatentPdfService()
    result = dispatch_patent_file_route(
        contract=single_doc_question_with_two_selected_pdfs_contract(tmp_path),
        pdf_service=service,
    )
    assert result["metadata"]["answer_mode"] != "pdf_text_compare"
```

- [ ] **Step 2: Run the targeted scope tests and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_file_routes.py -q -k "compare_mode_uses_dedicated_max_chars_budget or non_compare_multi_selected_question_still_targets_single_document"`

Expected: FAIL because compare currently reuses the single `max_pdf_chars=12000` budget and the new assertions do not hold.

- [ ] **Step 3: Implement compare-only budget separation in `PatentPdfService`**

Implement in `patent/server/patent/pdf_service.py`:
- add a dedicated compare budget field, for example `_compare_max_pdf_chars`
- keep existing single-PDF `max_pdf_chars` semantics unchanged
- preserve backward-compatible constructor semantics for existing compare tests:
  - `PatentPdfService(max_pdf_chars=...)` must continue to affect compare mode unless a compare-specific constructor argument is explicitly provided
  - if a compare-specific constructor argument is provided, it wins for compare mode only
- add environment/config fallback only for compare budget when not injected
- pass compare budget only when `compare_mode=True`

- [ ] **Step 4: Re-run the targeted scope tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_file_routes.py -q -k "compare_mode_uses_dedicated_max_chars_budget or non_compare_multi_selected_question_still_targets_single_document"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/tests/test_patent_file_routes.py
git commit -m "feat: split patent compare pdf budget"
```

## Task 2: Replace Compare Sparse Excerpting With Continuous Truncation

**Files:**
- Modify: `patent/server/patent/pdf_contract.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/test_patent_file_routes.py`

- [ ] **Step 1: Add failing truncation and validator tests for the new compare contract**

```python
def test_compare_truncation_keeps_continuous_body_text_for_each_document():
    prepared = smart_truncate_pdf_content(
        very_long_multi_doc_text(),
        50000,
        logger=fake_logger,
        is_compare=True,
    )
    sections = _split_multi_doc_sections(prepared)
    assert len(sections) == 2
    assert all(len(re.sub(r"\s+", " ", body).strip()) >= 1200 for _header, body in sections)


def test_compare_truncation_does_not_expose_model_visible_truncation_note():
    prepared = smart_truncate_pdf_content(
        very_long_multi_doc_text(),
        50000,
        logger=fake_logger,
        is_compare=True,
    )
    assert "仅保留原始内容" not in prepared
    assert re.search(r"原始\\s*\\d+\\s*字符.*保留\\s*\\d+\\s*字符", prepared) is None


def test_validate_compare_context_accepts_continuous_truncation_without_old_excerpt_targets():
    prepared = build_continuous_compare_prepared_text()
    validate_compare_context(prepared, original_documents)
```

- [ ] **Step 2: Run the truncation-focused tests and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_contract.py -q -k "continuous_body_text or model_visible_truncation_note or accepts_continuous_truncation"`

Expected: FAIL because compare still uses `_extract_compare_excerpt(...)`, still appends truncation notes, and validator still depends on old excerpt-target assumptions.

- [ ] **Step 3: Implement continuous compare truncation**

Implement in `patent/server/patent/pdf_contract.py`:
- keep non-compare truncation unchanged
- when `compare_mode=True`, stop calling `_extract_compare_excerpt(...)`
- use balanced continuous clipping per document
- keep document headers
- move truncation diagnostics to logs only, not prompt-visible text

- [ ] **Step 4: Replace compare validator logic**

Implement in `patent/server/patent/pdf_contract.py`:
- update or replace `validate_compare_context(...)`
- remove dependence on `_build_compare_paragraph_selection(...)`
- validate document headers, non-empty retained bodies, reference-tail exclusion, and retained normalized body length threshold
- keep errors compare-specific and readable

- [ ] **Step 5: Update file-route tests that encode the old validator contract**

Update in `patent/tests/test_patent_file_routes.py`:
- revise or replace tests that assert failure based on old excerpt-anchor semantics
- keep failure coverage for genuinely invalid compare context
- align file-route expectations with the new continuous-truncation validator instead of old front/tail slice preservation

- [ ] **Step 6: Re-run the truncation-focused tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py -q -k "continuous_body_text or model_visible_truncation_note or accepts_continuous_truncation or compare_excerpt"`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/pdf_contract.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py
git commit -m "feat: align patent compare truncation"
```

## Task 3: Simplify The Compare Prompt Contract

**Files:**
- Modify: `patent/server/patent/pdf_contract.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`

- [ ] **Step 1: Add failing prompt-contract tests**

```python
def test_compare_prompt_prefers_document_specific_extraction_before_insufficiency():
    prompt = build_patent_pdf_answer_prompt(
        question="比较这两篇文献",
        pdf_content="==== 文献 1: a ====\nA\n\n==== 文献 2: b ====\nB",
        kb_section="",
        is_summary=False,
        is_compare=True,
        selected_file_labels=["a.pdf", "b.pdf"],
        route_hint="pdf_qa",
        source_scope="pdf",
    )
    assert "优先提取可确认的逐篇证据" in prompt


def test_compare_prompt_does_not_repeat_placeholder_guidance_excessively():
    prompt = build_patent_pdf_answer_prompt(...)
    assert prompt.count("PDF中未提及") <= 1
    assert prompt.count("原文证据不足") <= 1
```

- [ ] **Step 2: Run the prompt tests and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_contract.py -q -k "prefers_document_specific_extraction or does_not_repeat_placeholder_guidance_excessively"`

Expected: FAIL because compare prompt currently repeats defensive insufficiency guidance.

- [ ] **Step 3: Implement the compare-only prompt rewrite**

Implement in `patent/server/patent/pdf_contract.py`:
- keep non-compare prompt logic unchanged
- keep five-section compare structure unchanged unless absolutely required for parser compatibility
- reduce repeated insufficiency instructions
- add explicit instruction to extract document-specific evidence before declaring insufficiency
- preserve strict no-hallucination rules

- [ ] **Step 4: Re-run the prompt tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_contract.py -q -k "prefers_document_specific_extraction or does_not_repeat_placeholder_guidance_excessively"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/pdf_contract.py patent/tests/test_patent_pdf_contract.py
git commit -m "feat: simplify patent compare prompt"
```

## Task 4: Replace Destructive Compare Rebuilding With Light Validation

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/tests/test_patent_file_routes.py`

- [ ] **Step 1: Add failing normalization and quality-gate tests**

```python
def test_compare_normalization_preserves_rich_document_bullets_instead_of_collapsing_to_one_per_section(tmp_path):
    service = PatentPdfService(answer_question_fn=lambda **_kwargs: rich_compare_answer())
    result = dispatch_patent_file_route(contract=compare_contract(tmp_path), pdf_service=service)
    assert result["answer_text"].count("- ") >= 8


def test_compare_validation_rejects_placeholder_dominant_answer(tmp_path):
    service = PatentPdfService(answer_question_fn=lambda **_kwargs: placeholder_compare_answer())
    result = dispatch_patent_file_route(contract=compare_contract(tmp_path), pdf_service=service)
    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"


def test_compare_validation_rejects_answer_that_leaks_truncation_internals(tmp_path):
    service = PatentPdfService(answer_question_fn=lambda **_kwargs: leaked_truncation_compare_answer())
    result = dispatch_patent_file_route(contract=compare_contract(tmp_path), pdf_service=service)
    assert result["metadata"]["answer_mode"] == "pdf_compare_unavailable"
```

- [ ] **Step 2: Run the compare-normalization tests and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_file_routes.py -q -k "preserves_rich_document_bullets or placeholder_dominant_answer or leaks_truncation_internals"`

Expected: FAIL because current compare normalization rebuilds and thins the answer, and low-information shells can still pass.

- [ ] **Step 3: Implement non-destructive compare normalization**

Implement in `patent/server/patent/pdf_service.py`:
- keep section-presence validation
- preserve rich original bullets where possible
- stop capping each document/section to 1-2 extracted Chinese points
- keep compare-specific logic isolated from summary and ordinary PDF logic

- [ ] **Step 4: Implement stronger compare quality gates**

Implement in `patent/server/patent/pdf_service.py`:
- reject placeholder-dominant compare answers
- reject truncation-internal echoes
- require more than trivial per-document content across the three main compare sections
- keep failure mode as compare failure response, not exception leakage

- [ ] **Step 5: Re-run the compare-normalization tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_file_routes.py -q -k "preserves_rich_document_bullets or placeholder_dominant_answer or leaks_truncation_internals"`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/tests/test_patent_file_routes.py
git commit -m "feat: strengthen patent compare normalization"
```

## Task 5: Enable Safe Compare Streaming With Final Parity

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Add failing streaming parity tests for compare mode**

```python
def test_executor_pdf_compare_streaming_generator_keeps_prefix_consistent_final_parity(tmp_path):
    streamed_chunks = []
    service = PatentPdfService(answer_question_fn=streaming_rich_compare_answer)
    result = executor.execute_with_progress(..., content_callback=streamed_chunks.append)
    streamed_answer = "".join(streamed_chunks)
    assert result["answer_text"].startswith(streamed_answer) or streamed_answer == result["answer_text"]


def test_http_stream_pdf_compare_partial_stream_falls_back_to_buffered_final_if_normalization_changes_shape(monkeypatch, tmp_path):
    response = client.post("/api/ask_stream", json=_pdf_compare_payload())
    events = _stream_events(response)
    final_answer = _final_content(events)
    streamed_answer = "".join(event["content"] for event in events if event["type"] == "content")
    assert streamed_answer == final_answer
```

- [ ] **Step 2: Run the compare streaming tests and verify failure**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py -q -k "compare_streaming_generator_keeps_prefix_consistent_final_parity or partial_stream_falls_back_to_buffered_final"`

Expected: FAIL because compare mode currently suppresses live content emission.

- [ ] **Step 3: Implement compare-mode streaming**

Implement in `patent/server/patent/pdf_service.py`:
- remove unconditional compare-mode suppression in `_emit_stream_piece(...)`
- allow compare streaming only when final parity can be preserved
- if final normalization would rewrite content too heavily, buffer and emit only final compare text
- keep existing non-compare streaming logic unchanged

- [ ] **Step 4: Update stale compare-stream tests that lock in buffered-final ordering**

Update in:
- `patent/tests/test_patent_executor.py`
- `patent/tests/fastapi_contract/test_ask_contract.py`

Replace or revise current compare-stream tests that assert `pdf_answer` success occurs before the first content chunk.

New expected behavior:
- for compare responses that can stream safely, content may appear before final success
- for compare responses that must buffer to preserve parity, final emitted content must still equal the final answer
- tests must assert parity and ordering rules that match the new Task 5 contract, not the old buffered-only contract

- [ ] **Step 5: Re-run the compare streaming tests**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py -q -k "compare_streaming_generator_keeps_prefix_consistent_final_parity or partial_stream_falls_back_to_buffered_final"`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: stream patent compare answers safely"
```

## Task 6: Run The Full Compare Regression Set

**Files:**
- Modify if needed: `patent/tests/test_patent_pdf_contract.py`
- Modify if needed: `patent/tests/test_patent_file_routes.py`
- Modify if needed: `patent/tests/test_patent_executor.py`
- Modify if needed: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Run the contract and service compare suite**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py -q -k "compare or targeted_document"`

Expected: PASS

- [ ] **Step 2: Run the executor compare suite**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_executor.py -q -k "compare"`

Expected: PASS

- [ ] **Step 3: Run the API stream compare suite**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/fastapi_contract/test_ask_contract.py -q -k "pdf_compare"`

Expected: PASS

- [ ] **Step 4: Run the non-compare regression spot checks**

Run: `env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py -q -k "single_pdf or non_compare or summary"`

Expected: PASS

- [ ] **Step 5: Commit the final compare alignment batch**

```bash
git add patent/server/patent/pdf_service.py patent/server/patent/pdf_contract.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: align patent multi pdf compare with fastqa shape"
```

## Execution Notes

- Do not touch `fastQA` even for reference cleanups
- Do not widen scope into `hybrid_qa` synthesis
- If a shared helper edit risks changing single-PDF output, split the helper or add an explicit compare-only branch instead of changing shared behavior in place
- Prefer updating existing compare tests over adding broad new test files unless the existing files become unreadable
- Request code review after each task before moving on

## Done Criteria

The plan is complete when all six tasks are done and the following are true:

- compare uses a dedicated `50000` default budget
- compare prompt input no longer leaks truncation notes
- compare truncation keeps continuous per-document bodies with validator parity
- compare answers preserve richer document-specific content
- placeholder-heavy compare answers fail instead of passing
- compare streaming is either prefix-consistent or buffered-final
- single-PDF non-compare behavior is unchanged
