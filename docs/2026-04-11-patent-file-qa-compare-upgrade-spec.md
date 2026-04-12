# Patent File-QA Compare Upgrade Spec

## Goal

Upgrade the `Patent` file-QA multi-PDF compare answer so its output quality, structure, and presentation align with the desired FastQA-style compare experience shown in the reference screenshots, while keeping the scope limited to the `patent` file-QA compare path.

This spec does not cover single-document literature summary, ordinary non-file QA, or unrelated `fastQA` generic QA behavior.

## Scope

In scope:

- `Patent` multi-PDF compare detection, prompt contract, fallback shaping, route output, executor streaming parity, and frontend Markdown presentation for compare answers
- Compare answers generated from selected PDF files in the `patent` mode file-QA path
- Shared frontend Markdown rendering and styling changes required to display the new compare structure correctly

Out of scope:

- Single-document literature summary behavior
- Normal non-file QA behavior
- KB-only patent QA behavior
- Table-only compare behavior in `fastQA` tabular modules
- Rebuilding compare answers as a dedicated frontend component; the preferred path remains shared Markdown rendering unless Markdown proves insufficient

## Current-State Findings

### Patent compare is currently contract-driven and structurally fixed

The current compare answer contract is explicitly shaped in the `Patent` PDF compare path, not emergent from a purely model-generated answer:

- Compare prompt instructions live in [patent/server/patent/pdf_contract.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_contract.py)
- Compare answer fallback and reshaping live in [patent/server/patent/pdf_service.py](/home/cqy/worktrees/highThinking/patent/server/patent/pdf_service.py)
- Route/executor behavior is locked by [patent/tests/test_patent_file_routes.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_file_routes.py), [patent/tests/test_patent_executor.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_executor.py), and [patent/tests/test_patent_pdf_contract.py](/home/cqy/worktrees/highThinking/patent/tests/test_patent_pdf_contract.py)

Today the compare fallback is normalized to:

- `各自概要`
- `相同点`
- `差异点`
- `总结`

This logic is centered in `_ensure_compare_answer_structure()`.

### The current “各自概要” quality issue is real and structural

The low-quality `各自概要` behavior is not just a prompt issue. Current fallback shaping extracts short raw per-document snippets from the prepared multi-PDF text and repackages them into chaptered document outlines. That means:

- it can surface English abstract fragments directly
- it can retain extraction artifacts such as broken words or truncation
- it does not guarantee a high-quality Chinese summary per document

This is the main technical reason the current compare answer can look unusable even when the outer structure appears correct.

### FastQA is a reference for answer style, not an existing compare contract to reuse directly

The repository does not currently contain a stable FastQA multi-PDF compare contract equivalent to the current `Patent` compare shaping. FastQA multi-PDF PDF behavior is closer to “merge selected documents and let the model answer” than to the current Patent compare fallback that rewrites output into fixed labeled sections.

Therefore, “align to FastQA” in this spec means:

- align to the desired answer experience and screenshot structure
- not necessarily copy an existing FastQA compare backend contract verbatim

## Product Decision

The compare answer should keep a lightweight synthesis layer instead of becoming only three isolated difference blocks.

Required retained modules:

- `相同点`
- `总结`

Required changes:

- `相同点` remains, but shorter than the primary compare blocks
- `总结` remains as a compact closing synthesis, not a second full compare essay
- do not introduce a compare table into the target Patent compare template; the target contract is section-based structured text

Recommended target order:

1. `具体内容对比`
2. `研究方法差异`
3. `应用领域差异`
4. `相同点`
5. `总结`

## Target Output Contract

### Document-count policy

The target compare experience is optimized for two selected PDFs.

Required behavior by selected compare document count:

- `2 documents`: full rich contract is required for all five modules
- `3 to 4 documents`: keep the same five top-level modules, but allow compaction:
  - `具体内容对比` still includes every selected document
  - `研究方法差异` and `应用领域差异` may use shorter per-document bullets
  - each document still needs at least one distinguishing fact where evidence exists
- `more than 4 documents`: this spec does not require generating the rich compare contract; the backend may fail closed and ask the user to narrow the comparison set

This bound exists because the current compare path already accepts more than two selected PDFs, and the new contract must remain implementable instead of expanding unboundedly into `3 x N` per-document module blocks.

### Module 1: 具体内容对比

Purpose:

- Replace the current bad `各自概要`
- Give a high-quality Chinese summary of each selected paper’s core content

Required structure:

- Top-level heading: `## 具体内容对比`
- Per-document subheadings:
  - `### 文献 #1 核心内容（根据PDF原文）`
  - `### 文献 #2 核心内容（根据PDF原文）`
- Under each document, use bullet points to summarize:
  - core research problem
  - main technical route or experimental idea
  - major result or claim
  - high-level conclusion or contribution

Quality requirements:

- Must be Chinese summary text
- Must not directly dump English abstract text
- Must not contain obvious truncation artifacts or broken words
- Must stay grounded in the original PDF evidence only

### Module 2: 研究方法差异

Purpose:

- Replace the current mixed “差异点/文献概要” behavior with a method-centric comparison block

Required structure:

- Top-level heading: `## 研究方法差异`
- Per-document subheadings:
  - `### 文献 #1 采用的研究方法`
  - `### 文献 #2 采用的研究方法`
- Under each document, list method-level facts using bullets

Expected content examples:

- experimental techniques such as `XRD`, `ICP`, `TOF-SIMS`
- simulation tools such as `COMSOL`
- algorithmic or modeling frameworks such as `LLM`, `STMR`
- dataset, apparatus, or validation approach when explicitly present

### Module 3: 应用领域差异

Purpose:

- Introduce a dedicated block for the practical or domain-facing context of each paper

Required structure:

- Top-level heading: `## 应用领域差异`
- Per-document subheadings:
  - `### 文献 #1 关注的应用领域`
  - `### 文献 #2 关注的应用领域`
- Under each document, list application context or target scenario using bullets

If the source PDFs do not support this dimension:

- keep the module
- explicitly state that the original text did not provide enough evidence

### Module 4: 相同点

Purpose:

- Preserve the shared baseline across compared papers

Required structure:

- Top-level heading: `## 相同点`
- 2 to 4 bullets max

Constraints:

- shorter than the first three modules
- should summarize real overlaps only
- should not repeat each paper’s full summary

### Module 5: 总结

Purpose:

- Provide a compact closing synthesis

Required structure:

- Top-level heading: `## 总结`
- 2 to 4 bullets max

Constraints:

- should answer “what is the practical takeaway from comparing these papers”
- should not restate the full answer
- should be noticeably shorter than the primary compare modules

## Formatting and Presentation Requirements

The compare answer must remain valid Markdown and be renderable by the shared chat rendering path.

Required formatting:

- use Markdown headings
- use standard Markdown list markers (`-`) so the frontend can render bullet lists
- allow nested bullets where needed
- preserve technical terms in their original form when present in source text

Frontend presentation target:

- blue section headings
- clear per-document subheadings
- readable bullet spacing and indentation
- no compare-table dependency in the target answer contract

The system should not depend on a Patent-only frontend rendering branch unless Markdown proves insufficient for a requirement that cannot be expressed cleanly through shared rendering.

## Behavioral Requirements

### Evidence discipline

- The answer must stay strictly grounded in uploaded PDF text
- If a dimension is unsupported, the answer must state that evidence is insufficient
- The system must not fabricate missing application domains or methods

### Compare failure semantics

- If compare context is incomplete and a valid multi-document comparison cannot be formed, the system must still fail closed
- The system must not pretend compare success when only one document is readable

### Language quality

- The output must be Chinese prose and bullet points
- Direct English abstract dumping is not acceptable as the final compare summary
- If the system cannot form a compliant Chinese compare answer from source evidence, it must prefer an explicit compare failure over emitting visibly bad English-fragment fallback text

## Architecture Direction

### Backend

The `Patent` compare path should move from the current fallback contract:

- `各自概要`
- `相同点`
- `差异点`
- `总结`

to the new compare contract:

- `具体内容对比`
- `研究方法差异`
- `应用领域差异`
- `相同点`
- `总结`

This requires coordinated changes in:

- compare prompt wording
- compare fallback reshaping
- compare output validation tests
- compare streaming parity tests

The compare upgrade must preserve these existing shared-surface contracts:

- single-document literature summary behavior remains unchanged
- non-summary single-document patent PDF QA remains unchanged
- non-compare patent file-QA routes remain unchanged
- shared frontend Markdown rendering must remain compatible with non-compare answers

### Frontend

The existing shared Markdown rendering path should remain the default:

- answer text
- shared Markdown normalization and rendering
- shared chat HTML mount path

Frontend work should focus on:

- ensuring the new compare headings and per-document subheadings render correctly
- ensuring nested list structure remains stable
- ensuring compare output still looks intentional without introducing a compare-specific chat component unless necessary

Because the frontend surfaces are shared, any compare-specific rendering refinement must be implemented as shared Markdown-safe behavior, not as a `Patent`-only `Home.vue` branch.

### Streaming semantics

This spec does not require changing the current compare event timing semantics.

Required behavior for this scope:

- compare answers may continue to be buffered server-side
- executor and route streaming parity still require the final compare success step to be emitted before the first compare content chunk
- “streaming parity” in this spec means the final compare answer structure must match across direct, buffered, and streamed generation paths, not that compare content must become incrementally streamed token-by-token

### Fallback quality strategy

The existing extractive compare reshaper is not sufficient for the new quality bar by itself.

Required fallback policy:

- if the model already returns a compliant Chinese compare answer in the target structure, keep it
- if the model returns malformed section order or missing sections, the backend may repair structure
- if the model returns raw English abstract fragments, truncation artifacts, or otherwise low-quality per-document compare text, the backend must not surface that directly as the final compare answer
- repaired compare output must be regenerated into Chinese section text from extracted evidence instead of merely re-emitting short raw PDF snippets
- if extracted evidence is too weak to regenerate a minimally acceptable Chinese compare answer, fail closed with an explicit compare failure rather than emitting bad fallback text

## Testing Implications

Backend tests must be updated because current tests still encode the old compare contract.

Expected test migration areas:

- compare prompt contract assertions
- compare fallback structure assertions
- route-level compare answer structure checks
- executor compare streaming parity and failure behavior
- regression assertions that non-compare patent PDF contracts remain unchanged
- regression assertions that shared frontend Markdown rendering for ordinary answers remains intact

Frontend tests must cover:

- compare headings and per-document subheadings rendering in order
- nested bullet rendering
- absence of table dependence in the new target compare structure

## Acceptance Criteria

The upgrade is accepted only if all of the following are true:

1. Patent multi-PDF compare answers no longer use `各自概要` as the primary compare module.
2. The answer contains these top-level sections in order:
   - `具体内容对比`
   - `研究方法差异`
   - `应用领域差异`
   - `相同点`
   - `总结`
3. For two-document compare, each selected document has its own subheading within the first three sections.
4. The output is Chinese and does not directly dump raw English abstract text as the compare summary.
5. The compare table is not part of the target compare answer contract.
6. Compare failure behavior still fails closed when required document context is missing.
7. For three- to four-document compare, the same five top-level sections remain, but compact per-document bullets are acceptable.
8. For compare requests beyond the bounded rich-contract limit, the backend fails closed or explicitly asks the user to narrow the comparison set.
9. Streaming and final answers converge to the same structural contract while preserving current compare step-before-content timing semantics.
10. The frontend shared Markdown path renders the new compare structure correctly without regressing ordinary answer rendering.

## Non-Goals

- This spec does not require pixel-perfect recreation of a screenshot-specific icon treatment.
- This spec does not require building a dedicated compare card component in the frontend.
- This spec does not require changing single-document summary behavior again.

## Open Risks

- Some PDFs may not explicitly contain “application domain” information; the module must still remain evidence-bound rather than becoming speculative.
- Current compare tests are numerous and tightly coupled to the old contract, so the migration must be planned carefully to avoid partial state.
- If model quality alone cannot reliably produce high-quality Chinese per-document compare blocks, fallback shaping may need stronger extraction and reorganization than the current snippet-based logic.
