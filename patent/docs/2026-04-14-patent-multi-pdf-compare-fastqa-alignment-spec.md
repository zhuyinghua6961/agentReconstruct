# Patent Multi-PDF Compare Alignment Spec

## Status

- Date: 2026-04-14
- Scope: spec only, no implementation in this document
- Goal: make `patent` multi-PDF compare behave closer to `fastQA` in evidence retention and answer richness, while keeping all changes strictly inside `patent`

## Goal

This spec defines a targeted upgrade for the `patent` multi-PDF compare pipeline.

The objective is not to copy `fastQA` code. The objective is to align the processing shape that makes `fastQA` answers richer:

- keep substantially more per-document evidence before generation
- stop over-compressing compare context
- stop post-processing the model answer into an over-thin shell
- preserve document-specific facts through generation and rendering

The target result is that `patent` multi-PDF compare answers become materially closer to `fastQA` in detail density and usefulness, without changing `fastQA` itself.

## Hard Boundaries

The following boundaries are mandatory and non-negotiable:

- Do not modify any `fastQA` code
- Do not introduce runtime imports from `fastQA` into `patent`
- Do not modify `patent` single-PDF logic
- Do not modify `patent` ordinary non-file QA
- Do not modify `patent` KB-only QA
- Do not modify `patent` tabular architecture as part of this work
- Do not change frontend rendering as part of this spec

For this spec, "do not modify `patent` single-PDF logic" means:

- no functional change to single-PDF summary generation
- no functional change to single-PDF ordinary PDF Q&A
- no change to single-PDF prompt contracts
- no change to single-PDF normalization rules

If a shared helper must be edited because compare and single-PDF paths currently share code, the change must be explicitly gated by `compare_mode` or equivalent so that single-PDF behavior remains unchanged.

## In Scope

Only the following execution slice is in scope:

- `patent` file-Q&A requests that enter `PatentPdfService`
- requests with at least 2 selected PDF documents
- requests detected as compare mode via `is_compare_question(...)`
- compare requests under `pdf_qa`

Primary code ownership is expected in:

- `patent/server/patent/pdf_service.py`
- `patent/server/patent/pdf_contract.py`
- compare-focused tests under `patent/tests/`

## Explicitly Out of Scope

The following are out of scope for this spec:

- any `fastQA` file, prompt, route, or service
- `patent` `hybrid_qa` final synthesis behavior
- `patent` single-PDF summary improvements
- `patent` single-PDF ordinary Q&A improvements
- `patent` tabular compare or tabular planning
- `patent` KB retrieval or generation
- frontend styling or answer component redesign
- general prompt rewrites for all `patent` PDF requests

`hybrid_qa` is intentionally out of scope in this spec even if a hybrid request internally touches the PDF handler, because the final hybrid answer is rewritten later by hybrid synthesis logic outside `PatentPdfService`. Making hybrid compare match `fastQA` would require a separate spec that explicitly allows edits to hybrid synthesis.

## Current Problem

Current `patent` multi-PDF compare answers are much thinner than `fastQA` answers even when both are given the same PDF set.

This is not primarily caused by the PDF extractor. The main issues are in the compare-specific preparation and rendering path inside `patent`.

### Root Cause 1: Compare context budget is too small

Current `patent` compare uses a default `max_pdf_chars=12000`, which is much smaller than the effective `fastQA` multi-PDF budget of `50000`.

Impact:

- each document receives only a small evidence budget
- multi-document comparisons quickly collapse into sparse excerpts
- the model sees too little material to produce rich document-specific comparisons

### Root Cause 2: Compare mode uses a special excerpt selector that is too aggressive

Current `patent` compare does not simply keep continuous text from each document. Instead, it selects a small set of front/methods/tail paragraphs per document and clips them to fit a per-doc budget.

Impact:

- many useful facts never reach the model
- datasets, methods, contributions, and applications are often cut away
- answers fall back to generic "evidence insufficient" language even when the original PDF contains enough material

### Root Cause 3: Internal truncation notes are exposed to the model

Current compare preparation appends a visible truncation note such as "原始 X 字符，保留 Y 字符" into the text passed to generation.

Impact:

- the model reflects internal pipeline limitations back to the user
- answers contain artifacts like "仅保留原始内容的 0.32%"
- user-facing content becomes diagnostic rather than analytical

### Root Cause 4: Compare prompt is overly defensive

The current compare prompt repeatedly instructs the model to say `PDF中未提及` or `原文证据不足` whenever evidence is incomplete.

Impact:

- with already-thin context, the model optimizes for conservative placeholders instead of richer extraction
- the answer style becomes failure-oriented

### Root Cause 5: Compare answer normalization rewrites and thins the model output

Current compare rendering does not lightly validate the model answer. It re-parses it, extracts only a small number of Chinese points, and rebuilds a normalized answer with severe point caps.

Impact:

- even when the model returns richer content, the final answer is compressed again
- per-document sections often keep only 1-2 points
- output richness is bounded by the normalizer, not by the model

### Root Cause 6: Compare validation accepts low-information shells

Current compare validation only requires minimal per-section content to pass.

Impact:

- structurally valid but substantively weak answers are treated as successful
- the system does not force retry, fallback, or failure when content is too thin

### Root Cause 7: Compare mode does not live-stream content

Current compare mode suppresses live content streaming in the PDF renderer path.

Impact:

- the compare experience feels slower than `fastQA`
- users get less visibility into progressive answer generation

## Target Behavior

The target is not literal template parity with `fastQA`. The target is processing parity where it matters:

- preserve enough raw evidence for each document
- let the model synthesize richer compare content from that evidence
- avoid destroying answer richness after generation
- keep compare answers document-specific rather than placeholder-heavy

After this work, `patent` multi-PDF compare should:

- retain a much larger amount of per-document evidence
- avoid compare-specific excerpt loss when not necessary
- stop exposing truncation internals to the model
- produce richer per-document compare sections
- reject empty-shell compare answers instead of normalizing them into "success"
- support streaming behavior closer to the normal PDF answer path

## Design Summary

Recommended design:

1. Keep the entrypoint unchanged: `PatentPdfService.execute(...)`
2. Introduce a compare-only preparation strategy that is much closer to `fastQA` multi-document handling
3. Keep compare prompt constraints concise and evidence-oriented rather than failure-oriented
4. Replace destructive compare answer rebuilding with light validation and minimal normalization
5. Tighten compare quality gates so thin placeholder answers no longer pass
6. Enable compare-mode streaming without affecting single-PDF behavior

This work is intentionally compare-only. Single-PDF behavior remains untouched.

## Proposed Architecture

### A. Compare-Only Context Preparation

Add a compare-only preparation policy inside `patent` that does the following:

- uses a dedicated compare context budget, separate from single-PDF budget
- defaults that compare budget to a level close to `fastQA` multi-PDF handling
- preserves continuous text per document rather than compare-specific sparse excerpts
- keeps document headers so the model can distinguish sources
- emits truncation diagnostics to logs only, not to the model-visible prompt body

Recommended rule:

- if compare mode is active, do not use `_extract_compare_excerpt(...)`
- instead use a balanced continuous-text truncation strategy closer to `fastQA` multi-document truncation
- if the merged content already fits, pass it through unchanged

This is the highest-priority change because it directly determines how much evidence the model can see.

### A1. Compare Context Validation Must Be Reworked Together

Current compare validation is coupled to `_build_compare_paragraph_selection(...)` and assumes the old sparse excerpt-selection strategy.

Therefore Task 2 is not complete unless `validate_compare_context(...)` is also updated.

Required change:

- replace the old target-fragment validation with compare validation that matches the new continuous truncation policy

Minimum compare-context validation after the change:

- every compared document header is still present
- every compared document body is non-empty after truncation
- compare bodies do not contain reference-tail contamination
- each document retains at least `min(1200, max(400, floor(compare_doc_budget * 0.5)))` visible body characters after whitespace normalization, unless the original stripped body is shorter than that threshold

The validator must no longer require old front/tail fragment targets that only exist because of `_build_compare_paragraph_selection(...)`.

### B. Compare-Only Prompt Contract

Keep the compare prompt scoped to structured compare output, but reduce defensive repetition.

Recommended prompt contract:

- require document-by-document comparison
- require Chinese structured output
- require document-specific facts in each major compare section
- do not repeatedly push the model toward `PDF中未提及` unless a dimension is truly unsupported
- explicitly prefer extracting available evidence before declaring insufficiency

Prompt design principle:

- compare prompt should guide synthesis from preserved evidence
- compare prompt should not act like a failure message template

This spec does not require adopting the exact `fastQA` headings. It requires matching the effective richness and evidence use.

### C. Non-Destructive Compare Rendering

Replace the current compare normalizer with a lighter post-processing contract.

Current behavior to avoid:

- rebuild the entire answer from a tiny extracted point set
- cap each document/section to 1-2 Chinese points
- discard valid model content because it is not in the expected micro-shape

Recommended behavior:

- validate the presence of required top-level compare sections
- validate that each document has substantive content in each required section
- preserve the model's original detailed bullets as much as possible
- only normalize headings and minor formatting when needed

The renderer should not be the component that makes the answer thin.

### D. Stronger Compare Quality Gates

Upgrade compare validation so that structurally correct placeholder answers do not pass.

Recommended validation checks:

- each compared document must contribute more than one trivial content point overall
- placeholder phrases such as `PDF中未提及` or `原文证据不足` cannot dominate the answer
- repeated boilerplate across documents should count against success
- answers that mention truncation internals should fail validation

If the answer fails these gates:

- return a clear compare failure or retry path
- do not normalize a thin answer into a successful final compare result

### E. Compare-Mode Streaming

Enable content streaming for compare mode without changing single-PDF behavior.

Recommended behavior:

- compare mode may buffer briefly until section shape is stable
- once stable, stream the generated answer progressively
- final post-processing must remain compatible with streamed output
- emitted compare stream must be prefix-consistent with the final delivered compare answer
- if final normalization changes the body so much that prefix consistency cannot be guaranteed, compare mode must fall back to buffered emission rather than streaming mismatched text

This does not require redesigning the whole streaming subsystem. It requires removing the compare-specific suppression in the PDF render path and ensuring the final validator can work with streamed content.

## Configuration Proposal

Introduce compare-only configuration rather than reusing the single-PDF default budget.

Recommended configuration:

- keep current single-PDF `max_pdf_chars` behavior unchanged
- add a dedicated compare budget setting, for example `PATENT_MULTI_PDF_COMPARE_MAX_CHARS`
- default the compare budget near `50000`
- keep the compare document-count guard unchanged unless future product requirements say otherwise
- configuration precedence must be explicit:
  - constructor injection for compare budget wins in tests or direct instantiation
  - environment/config fallback is used only when compare budget is not injected
  - single-PDF `max_pdf_chars` semantics remain unchanged

This preserves the user's explicit constraint: do not change single-PDF logic.

## File-Level Change Boundaries

### Files Allowed to Change

- `patent/server/patent/pdf_service.py`
- `patent/server/patent/pdf_contract.py`
- compare-specific tests in:
  - `patent/tests/test_patent_file_routes.py`
  - `patent/tests/test_patent_executor.py`
  - `patent/tests/test_patent_pdf_contract.py`
  - `patent/tests/fastapi_contract/test_ask_contract.py`

### Files That Must Not Change

- any file under `fastQA/`
- compare-unrelated `patent` services
- single-PDF-only prompt or summary logic unless the edit is strictly compare-gated
- `patent` tabular services
- `patent` KB services

## Task Breakdown

### Task 1: Compare Scope Guard and Config Separation

Objective:

- isolate compare-only configuration from single-PDF defaults

Deliverables:

- compare-only max-char configuration
- clear code path separation between compare and non-compare preparation

### Task 2: Compare Context Preparation Alignment

Objective:

- replace compare-specific sparse excerpting with continuous balanced truncation

Deliverables:

- compare mode no longer uses `_extract_compare_excerpt(...)`
- compare mode preserves substantially more per-document body text
- truncation diagnostics move to logs instead of prompt text
- `validate_compare_context(...)` is updated or replaced so it validates the new continuous truncation strategy instead of the old excerpt-target strategy

### Task 3: Compare Prompt Contract Simplification

Objective:

- make the compare prompt extraction-oriented rather than placeholder-oriented

Deliverables:

- compare prompt still enforces structure
- compare prompt no longer biases toward empty-shell "未提及" output

### Task 4: Compare Renderer and Validation Upgrade

Objective:

- stop destroying answer richness after generation

Deliverables:

- non-destructive compare normalization
- stronger thin-answer rejection
- validation that fails placeholder-heavy compare output

### Task 5: Compare Streaming Enablement

Objective:

- allow compare answers to stream progressively

Deliverables:

- compare mode no longer blocks live content emission by default
- streamed compare output remains compatible with final validation

### Task 6: Regression Coverage

Objective:

- protect compare-only changes without altering single-PDF expectations

Deliverables:

- tests proving compare path changed
- tests proving single-PDF behavior did not change
- tests proving `fastQA` remains untouched by this work

## Acceptance Criteria

The work is acceptable only if all of the following are true:

- compare mode uses a dedicated default context budget of `50000` characters, while single-PDF default budget remains unchanged
- compare prompt input contains no model-visible truncation note matching either `仅保留原始内容` or `原始 .* 字符，保留 .* 字符`
- for 2-document compare requests that require truncation, each retained document body is non-empty and keeps at least `1200` normalized visible characters unless the original stripped body is shorter than `1200`
- compare rendering preserves more than 2 non-placeholder content bullets per document across the three main compare sections combined
- compare validation rejects answers where placeholder lines containing `PDF中未提及` or `原文证据不足` account for more than 50% of extracted per-document content bullets
- compare validation rejects answers that echo truncation internals back to the user
- compare mode either streams prefix-consistent final text or explicitly falls back to buffered final emission
- single-PDF `patent` behavior remains unchanged
- `fastQA` code remains unchanged

## Verification Requirements

Verification must cover at least these cases:

- `pdf_qa` compare with 2 PDFs
- compare request where documents are long enough to require truncation
- compare request where one document has weaker evidence
- regression check for single-PDF summary
- regression check for single-PDF ordinary PDF Q&A
- regression check for multiple PDFs selected with a non-compare question that targets one document via document index or file label

Required evidence:

- prepared compare context uses the compare-only budget and retains continuous body text for each compared document
- final answer no longer contains truncation-ratio artifacts
- final answer preserves multiple document-specific points instead of collapsing to one placeholder per section
- non-compare answers are unchanged

## Risks

### Risk 1: Richer compare context increases prompt size

Mitigation:

- use a compare-only budget
- keep balanced per-document allocation
- prefer continuous clipping over full raw concatenation

### Risk 2: Looser rendering may allow malformed answers through

Mitigation:

- keep structural validation
- strengthen content-quality validation
- reject placeholder-heavy output

### Risk 3: Shared helper edits accidentally affect single-PDF logic

Mitigation:

- gate every behavioral change behind compare mode
- add explicit regression tests for single-PDF paths

## Non-Goals for This Spec

This spec does not attempt to:

- redesign the final compare headings
- merge `patent` and `fastQA` implementations
- change `hybrid_qa` final answer synthesis
- upgrade single-PDF summary depth
- refactor unrelated PDF or file-route architecture

## Summary

The core fix is not "write a bigger prompt." The core fix is to stop the `patent` compare pipeline from starving and then re-thinning its own evidence.

This spec therefore proposes a compare-only upgrade that:

- preserves more per-document evidence before generation
- simplifies compare prompting
- removes destructive answer rebuilding
- tightens low-information failure detection
- enables compare-mode streaming

All changes remain inside `patent`, and all single-PDF and `fastQA` logic remains untouched.
