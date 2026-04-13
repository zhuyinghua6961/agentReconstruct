# Patent File Q&A PDF/Tabular Alignment Spec

## Status

- Date: 2026-04-13
- Scope: spec only, no implementation in this document
- Write scope: `patent/` directory only
- Goal: align `patent` file-Q&A handling with the effective processing shape of `fastQA` for PDF extraction and tabular execution, without changing `fastQA`

## Goal

This spec defines a two-phase upgrade for the `patent` file-Q&A pipeline:

1. Phase A: migrate the effective PDF extractor behavior from `fastQA` into `patent`
2. Phase B: upgrade `patent` tabular architecture so `tabular_qa` and file-scoped `hybrid_qa` behave closer to `fastQA`

The design goal is processing-path alignment, not shared runtime or cross-service code reuse.

## Hard Boundaries

The following boundaries are mandatory:

- Do not modify any `fastQA` code
- Do not introduce `patent` runtime imports from `fastQA`
- Do not change `patent` ordinary QA behavior outside file-Q&A scope
- Do not change `patent` `kb_qa`
- Do not change non-file ordinary ask behavior
- Do not change `gateway`, `public-service`, or any other service as part of this spec

For this spec, "file-Q&A scope" means only:

- `pdf_qa`
- `tabular_qa`
- file-scoped `hybrid_qa`

This spec does not cover:

- KB-only question answering
- ordinary non-file `patent` asks
- changing `fastQA` routing, prompts, or file services

## Why This Work Is Needed

Current `patent` file-Q&A has two structural gaps relative to `fastQA`:

1. PDF extraction quality is too weak before generation
2. Tabular execution is still text-summary-oriented instead of structure-first

For PDF:

- current `patent` extraction collapses page text into a flat whitespace-normalized string
- current `patent` extraction uses a much smaller default page budget
- downstream `patent` truncation and summary/compare logic assume paragraph and section structure still exists
- this mismatch causes summary requests to over-index on front matter and abstract-like text, while dropping result, conclusion, and limitation evidence

For tabular:

- current `patent` tabular handling is mainly based on lightweight text extraction and summarization
- `fastQA` uses a clearer layered pipeline: workbook loading, profiling, planning, execution, and rendering
- current `patent` hybrid file flow therefore consumes weaker table-side evidence than `fastQA`

## Design Summary

Recommended shape:

- keep all new code inside `patent/server/patent/`
- keep `fastQA` as reference only
- align capability, not implementation ownership
- maintain current outer `patent` file-route contracts where possible
- split the work into two explicit phases

Phase ordering is fixed:

1. Phase A: PDF extractor migration
2. Phase B: tabular architecture upgrade

Phase A is intended to be independently shippable.

## Phase A: PDF Extractor Migration

### Objective

Replace the current `patent` PDF extraction behavior with a `patent`-local implementation that matches the effective extractor behavior used by `fastQA`:

- preserve page boundaries
- preserve paragraph/line structure
- support reference-section exclusion
- use a larger, configurable page budget suitable for file-Q&A summaries

### Non-Goals

Phase A does not include:

- rewriting `patent` answer prompting
- rewriting `patent` summary normalization
- changing `fastQA`
- changing tabular architecture

### Recommended Architecture

Introduce a new `patent`-local PDF extraction module, for example:

- `patent/server/patent/pdf_extraction.py`

This module should own:

- reference-section detection and removal
- raw PDF text extraction
- page-aware text assembly
- extractor configuration defaults for `patent`

The existing `PatentPdfService` should stop owning a hand-written embedded extractor implementation and instead delegate to this new module.

The same extractor should be reused by all in-scope `patent` file-Q&A PDF consumers, especially:

- `PatentPdfService`
- file-route-owned PDF extraction call sites used by `pdf_qa` and file-scoped `hybrid_qa`

### Compatibility Requirements

The following compatibility must be preserved:

- `PatentPdfService(extract_pdf_text_fn=...)` remains supported
- current tests that inject `extract_pdf_text_fn` should not require broad rewrites
- existing `pdf_qa` and file-scoped `hybrid_qa` outer response contracts remain unchanged

Recommended compatibility rule:

- keep the existing injection surface
- adapt the built-in default implementation only
- allow the new extractor implementation to accept reference-exclusion options internally without forcing all callers to change

### Expected Data Flow

Phase A target flow:

1. uploaded PDF enters `patent`
2. `patent` local extractor reads the PDF with page-aware extraction
3. reference-section trimming is applied when appropriate
4. extracted text preserves enough structural boundaries for paragraph/section-aware truncation
5. existing `pdf_contract` truncation and summary/compare normalization consume the improved text within file-Q&A scope

### Acceptance Criteria

Phase A is acceptable when all of the following are true:

- `patent` no longer collapses extracted PDF text into a single whitespace-flat stream before truncation
- extracted PDF content can exclude likely reference tails
- summary/compare truncation can see real paragraph boundaries
- result, conclusion, and limitation-like sections are materially more reachable downstream
- single-PDF literature summary quality improves without widening scope to ordinary QA
- `kb_qa` and ordinary non-file `patent` asks remain unchanged
- no `fastQA` code is modified

## Phase B: Tabular Architecture Upgrade

### Objective

Upgrade `patent` table processing so `tabular_qa` and file-scoped `hybrid_qa` follow a structure-first architecture closer to `fastQA`, instead of relying primarily on lightweight extracted table text.

### Scope

Phase B covers only:

- `patent` `tabular_qa`
- `patent` file-scoped `hybrid_qa` where table files participate

Phase B does not cover:

- `kb_qa`
- ordinary non-file asks
- changing `fastQA`

### Current Gap

Current `PatentTabularService` mixes several responsibilities in one place:

- file loading
- sheet reading
- row selection
- prompt building
- fallback answering
- hybrid evidence shaping

This makes the table path harder to extend and weaker than `fastQA` in three ways:

- no dedicated workbook loading boundary
- no explicit schema/profile layer
- no planner/executor separation for structured operations

### Recommended Architecture

Split `patent` tabular logic into local modules with clear ownership, for example:

- `patent/server/patent/tabular/workbook_loader.py`
- `patent/server/patent/tabular/schema_profiler.py`
- `patent/server/patent/tabular/planner.py`
- `patent/server/patent/tabular/executor.py`
- `patent/server/patent/tabular/renderer.py`

`PatentTabularService` should remain as the orchestration layer, not the implementation sink.

Target responsibility split:

- workbook loader: load CSV/XLS/XLSX/XLSM into normalized workbook structures
- schema profiler: describe sheets, columns, numeric fields, missingness, and sample values
- planner: convert question intent into a structured tabular operation plan
- executor: run the plan against workbook data
- renderer: turn execution result into stable answer context and model prompt input

### Hybrid Integration Requirement

The upgraded tabular path must keep fitting into the current `patent` file-route orchestration shape.

That means `PatentTabularService` still needs to produce stable outputs for:

- direct `tabular_qa` answers
- `hybrid_qa` merge logic in `file_routes.py`

However, after Phase B, the table side should contribute stronger artifacts than a loose text summary:

- structured execution result
- stable rendered execution context
- hybrid-safe metadata that can be merged with PDF evidence

### Expected Data Flow

Phase B target flow:

1. uploaded table file enters `patent`
2. workbook loader materializes workbook data
3. schema profiler builds sheet/column metadata
4. planner resolves the user request into a structured operation
5. executor performs the operation on workbook data
6. renderer produces stable answer context
7. `PatentTabularService` emits steps, answer text, and metadata
8. `file_routes.py` consumes these results for `tabular_qa` or `hybrid_qa`

### Acceptance Criteria

Phase B is acceptable when all of the following are true:

- `patent tabular_qa` is driven by structured workbook execution rather than primarily by lightweight extracted table text
- `patent hybrid_qa` can consume stable table execution context
- current outer file-route behavior remains compatible
- `pdf_qa` scope is not accidentally widened
- `kb_qa` and ordinary non-file asks remain unchanged
- no `fastQA` code is modified

## Cross-Phase Constraints

The two phases must follow these rules:

- Phase A must not be blocked on Phase B
- Phase B may assume Phase A is already complete
- both phases must preserve current `patent` file-route boundaries
- both phases must keep new code local to `patent`
- neither phase may "temporarily" edit `fastQA`

## Risks And Controls

### Risk 1: PDF extractor migration breaks current injection-based tests

Control:

- keep the `extract_pdf_text_fn` injection surface stable
- replace only the default internal implementation

### Risk 2: Phase A quietly changes ordinary QA behavior

Control:

- restrict code changes to file-Q&A consumers only
- explicitly exclude `kb_qa` and ordinary non-file ask paths from the implementation scope

### Risk 3: Tabular refactor becomes an unbounded rewrite

Control:

- keep `PatentTabularService` as the stable orchestration boundary
- move internals behind modular boundaries incrementally

### Risk 4: Hybrid route behavior regresses during tabular upgrade

Control:

- preserve current `file_routes.py` outer contracts
- upgrade table-side artifacts behind the same orchestration interface

### Risk 5: Teams accidentally start sharing runtime code with `fastQA`

Control:

- copy only the needed behavior into `patent`
- do not add runtime imports from `fastQA`
- treat `fastQA` as reference, not dependency

## Validation Strategy

Validation should happen in two layers.

### Phase A validation

- extractor-level tests
- `PatentPdfService` integration tests
- file-route tests for single-PDF summary behavior
- file-route tests for in-scope compare behavior where improved paragraph boundaries must remain visible to compare truncation
- regression checks that non-file `patent` behavior is unchanged

### Phase B validation

- workbook loader tests
- schema/profile tests
- planner tests
- executor tests
- renderer tests
- `PatentTabularService` orchestration tests
- `hybrid_qa` merge-path tests

## Out Of Scope

This spec does not authorize:

- changes to `fastQA`
- changes to `gateway`
- changes to `public-service`
- changes to `patent` non-file ordinary QA
- changes to `patent` `kb_qa`
- prompt-expansion work unrelated to Phase A or Phase B boundaries

## Recommended Execution Order

1. Implement Phase A PDF extractor migration inside `patent`
2. Verify Phase A in isolation
3. Freeze Phase A behavior behind passing tests
4. Implement Phase B tabular architecture upgrade inside `patent`
5. Verify `tabular_qa` and file-scoped `hybrid_qa`

## Implementation Planning Note

The follow-up implementation plan should split these into separate tasks with Phase A first.

Phase A should be the first executable milestone because:

- it has smaller surface area
- it directly addresses current literature-summary quality failures
- it reduces upstream evidence loss before any tabular work begins
