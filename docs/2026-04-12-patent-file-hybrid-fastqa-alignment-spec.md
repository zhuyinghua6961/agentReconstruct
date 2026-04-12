# Patent File/Hybrid QA FastQA Alignment Spec

## Goal

Align `Patent` file-question-answering summary behavior with the observed `FastQA` file-QA experience, while keeping implementation fully inside the `Patent` codepath.

The target is not to copy `FastQA` source code literally. The target is to make `Patent` file-QA and hybrid-QA produce answers that are close to `FastQA` in output logic, density, and readability, without inflating prompt size or context size.

## Scope

In scope:

- `Patent` `pdf_qa` summary behavior with `source_scope=pdf` for file-QA questions such as `总结这篇文献`
- `Patent` `hybrid_qa` summary behavior with `source_scope=pdf+table` when the user is asking for a literature-style summary over selected file evidence
- Shared frontend Markdown rendering and message-content styling only if needed to preserve the intended chapter hierarchy and list readability

Conditionally in scope:

- `Patent` tabular-side prompt/answer shaping only when it is required to support `hybrid_qa` + `source_scope=pdf+table` summary synthesis

Explicit source-scope boundary matrix:

- `pdf_qa` + `pdf`: in scope
- `hybrid_qa` + `pdf+table`: in scope
- `hybrid_qa` + `pdf+kb`: out of scope in this spec
- `hybrid_qa` + `table+kb`: out of scope in this spec
- `hybrid_qa` + `pdf+table+kb`: out of scope in this spec
- `tabular_qa` + `table`: out of scope except for minimal shaping strictly required by the `pdf+table` hybrid synthesis path

Out of scope:

- `Patent` `kb_qa`
- standalone `Patent` `tabular_qa` literature-summary redesign
- `Patent` ordinary non-file QA
- `FastQA` code changes of any kind
- changing gateway route selection or route contracts
- changing file-selection semantics or source-scope validation
- compare-mode redesign in this spec

## Hard Constraints

1. Only `Patent` code may change.
2. `FastQA` is reference behavior only.
3. `prompt` size must stay in the same rough budget class as current `Patent`/`FastQA` file-QA prompts.
4. context size must not be increased by solving the problem with longer raw file payloads or large few-shot examples.
5. summary answers must remain strictly grounded in uploaded file evidence.
6. knowledge-base-only behavior is excluded.

## Current-State Findings

### 1. Prompt gap is not the main problem

`Patent` and `FastQA` file-QA summary prompts are already close in core intent:

- both forbid generic knowledge
- both require answers to stay grounded in PDF text
- both ask the model to read the full paper structure, not just the abstract

`Patent` even adds an explicit summary contract:

- `研究目的和背景`
- `研究方法/实验设计`
- `主要发现和结果`
- `结论和意义`
- `注*`

This means the output gap cannot be explained mainly by prompt wording.

### 2. Runtime reality must be respected

The `Patent` service is still described as a phase-1 scaffold with external rollout gates.

Relevant current-state facts:

- standalone `patent/` service behavior exists and is test-covered
- file routes can still be rollout-gated by environment/runtime conditions
- durable non-file traffic has additional external dependencies outside this spec

Therefore this spec only targets the already-existing `Patent` file-route implementations and their in-scope summary outputs. It does not assume a broader route rollout or contract expansion.

### 3. The biggest gap is processing philosophy

`FastQA` file-QA is effectively model-first and formatter-light:

- truncate input
- build prompt
- call model
- stream/clean result

`Patent` file-QA summary is formatter-first after model generation:

- truncate input
- build prompt
- call model
- normalize result through rule-based chapter reconstruction

The current `Patent` summary normalizer:

- extracts a small pool of support points from truncated PDF text and from the model answer
- classifies those points by keyword
- rebuilds a fixed chapter answer with very small per-section budgets

This makes the final answer much thinner than the underlying model answer, even when the model originally produced richer content.

### 4. Hybrid summary in `Patent` is also rule-heavy

For the in-scope file-only hybrid branch, current `Patent` `hybrid_qa` + `source_scope=pdf+table` behavior does not resemble `FastQA`'s model-driven mixed synthesis.

Instead, `Patent` hybrid summary:

- collects already-compressed evidence contexts
- extracts short points
- rebuilds a fixed literature-summary shell

This produces an answer that looks like evidence collation rather than a natural integrated summary.

The KB-including hybrid branches exist, but they are explicitly not alignment targets in this spec.

### 5. Fallback behavior is too weak

When the model does not produce a compliant answer, current `Patent` fallback logic degrades to a shallow extractive summary. This is acceptable for safety, but not for parity with `FastQA`-like output quality.

### 6. The current summary contract is still incomplete relative to the target experience

Current `Patent` summary logic has:

- chaptered structure
- note/disclaimer
- evidence-boundary language

But it still lacks:

- a dedicated `局限性` chapter
- a model-first preservation path for already-good answers
- strong preservation of multi-step method explanations
- enough tolerance for high-density answers that are already well-structured

## Product Decision

The alignment target is `FastQA`-like behavior, not `FastQA` source parity.

That means `Patent` should move toward the following answer-generation philosophy:

1. let the model produce the summary shape first
2. preserve high-quality structured output whenever it already satisfies the contract
3. use normalization as a safety rail, not as the primary author of the final answer
4. keep fallback strict and evidence-bound, but secondary

## Target Behavior

### 1. Model-first, formatter-light summary flow

For `Patent` `pdf_qa` summary questions:

- if the model returns a strong structured answer, preserve it with only minimal normalization
- only repair ordering, missing note text, or small structural inconsistencies when the answer is otherwise usable
- do not compress a rich answer into a low-density keyword-picked summary

### 2. Hybrid summary should be final-answer synthesis, not point stitching

For `Patent` `hybrid_qa` + `source_scope=pdf+table` summary questions:

- the final hybrid summary should be produced from file-side evidence contexts using one final synthesis step
- the result should read like a coherent literature summary, not like a merged evidence checklist
- file evidence remains primary
- table-side evidence may explain or validate, but may not overwrite PDF-backed file facts
- because the current synthesizer function is shared with `pdf+table+kb`, the implementation must isolate the in-scope `pdf+table` path by explicit `source_scope` branching before any KB-merge path changes are applied
- this spec does not authorize behavior changes to the executor-side KB merge flow for `pdf+table+kb`

### 3. Keep the chaptered academic structure

The `Patent` file-summary target structure remains:

- `## 研究目的和背景`
- `## 研究方法/实验设计`
- `## 主要发现和结果`
- `## 结论和意义`
- `## 局限性`
- `注*：所有总结内容均严格基于文件原文中明确提到的信息，未添加任何通用知识或推测内容。`

Rationale:

- `FastQA` reference behavior is model-rich rather than shell-thin
- the current `Patent` four-chapter summary should be extended rather than reverted
- `局限性` is required to match the desired academic-summary quality

### 4. Preserve dense method logic

The `研究方法/实验设计` chapter must preserve logical flow when the source supports it.

Expected behavior:

- a multi-step method explanation should remain multi-step
- nested bullets are allowed when the source supports workflow decomposition
- the formatter must not flatten a well-written method explanation into one short sentence

### 5. Preserve answer density when the model already did the right thing

If the model returns:

- chaptered Markdown
- clear section coverage
- evidence-grounded detailed bullets

then `Patent` should keep that answer mostly intact.

It may still:

- normalize heading order
- append the note if missing
- add explicit evidence-gap language when a chapter is missing

It should not:

- reselect only a handful of points
- discard valid detail just because it exceeds an arbitrary tiny per-section cap

### 6. Preservation, repair, and fallback must use explicit gates

The formatter decision must be executable and testable, not subjective.

Deterministic predicate definitions:

- `known degraded/stub answer` means the normalized answer is empty or contains any currently-recognized degraded marker substring such as `found no matching results`, `未拿到可读`, `未找到可用的知识库`, `未找到匹配`, `无法生成`, `请稍后重试`, `文件不可读`, `暂时无法`
- `non-trivial bullet/paragraph` means a sentence or list item that remains after stripping Markdown/list prefixes and collapsing whitespace, with minimum retained length:
  - at least 10 characters when extracted from the model answer
  - at least 12 characters when extracted from prepared evidence context
- `usable chapter body` means at least one non-trivial bullet/paragraph under that chapter after heading removal

Preserve as-is with only heading-order cleanup and missing required-section injection when all of the following are true:

- the answer is not empty and is not a known degraded/stub answer
- the answer contains the four primary literature-summary headings: `研究目的和背景`, `研究方法/实验设计`, `主要发现和结果`, `结论和意义`
- each detected chapter has non-empty body text after stripping headings

Light repair when all of the following are true:

- preserve-as-is conditions are not met
- the answer is not a known degraded/stub answer
- the answer contains at least 3 of the 4 primary literature-summary headings, or it contains the legacy four-block structure
- the answer contains enough usable body content to retain, meaning at least 3 non-trivial bullets/paragraphs can be extracted from the model answer itself

Conservative repair when all of the following are true:

- preserve-as-is and light-repair conditions are not met
- the answer is not empty
- at least 2 non-trivial bullets/paragraphs can be extracted from the model answer or prepared evidence context

Fallback when any of the following are true:

- the model answer is empty, stubbed, or degraded
- the model answer has no usable summary content after normalization
- conservative repair cannot populate the required chapters with evidence-bound text

Repair rules:

- repair must preserve surviving model-authored content before drawing from prepared evidence context
- preserve/repair may insert missing `局限性` or `注*`
- repair may normalize heading order and heading names
- repair must not apply tiny hard caps that throw away otherwise-valid structured detail by default
- the deterministic thresholds above should be implemented through one shared predicate layer rather than duplicated ad hoc heuristics

## Prompt Strategy

### 1. Align with `FastQA` prompt size class

Do not solve this by adding a long instruction wall.

Prompt changes should stay compact and high-signal:

- preserve the existing full-text reading instruction
- preserve the evidence boundary
- add only the missing high-value contract requirements

Allowed prompt additions:

- require the `局限性` chapter
- require structured method description when the source supports it
- require explicit evidence-gap language when a chapter lacks support
- require concise Markdown headings and lists

Disallowed prompt changes:

- long few-shot examples
- duplicated negative rules that restate the same constraint many times
- large style prescriptions unrelated to answer correctness
- brute-force prompt expansion to compensate for weak formatter design

### 2. Prompt must not become the sole fix

This spec explicitly rejects the idea that matching `FastQA` prompt wording alone is sufficient.

The main fix must come from changing `Patent` answer handling logic.

## Context Strategy

### 1. No context inflation

This spec forbids increasing answer quality by substantially increasing raw context size.

Required:

- keep the current truncation strategy class
- improve content preservation by prioritization, not by larger payloads

Allowed:

- more accurate retention of summary-relevant sections
- better preservation of method/results/conclusion spans for summary-mode questions
- safer section-aware handling for hybrid evidence contexts

Disallowed:

- materially larger `max_pdf_chars` as the primary solution
- injecting additional long derived contexts into the prompt
- appending large few-shot examples

## Formatter Strategy

### 1. Summary normalization must become preservation-oriented

`Patent` summary normalization should be redesigned around this decision order:

1. if answer already satisfies the summary contract, keep it
2. if answer is structurally close and content-rich, repair lightly
3. if answer is weak but still salvageable, repair conservatively
4. if answer is too weak, fall back

### 2. Rule-based compression should be removed as the default path

Current keyword-picked reconstruction is too lossy.

The new formatter should not:

- cap every chapter to tiny fixed counts as the normal case
- rebuild all chapters from a few extracted support lines when the model answer is already better
- collapse detailed method/result structure into generic summary bullets

### 3. Fallback remains strict but secondary

Fallback is still required for safety, but it should be clearly a fallback path rather than the normal author of final answers.

## Hybrid Strategy

### 1. File evidence remains primary

For `Patent` `hybrid_qa` + `source_scope=pdf+table` summary:

- file evidence determines the factual core
- table execution results may contribute concrete evidence where relevant
- KB synthesis is out of scope for this alignment task
- if the existing shared synthesizer remains shared internally, the in-scope `pdf+table` formatting branch must be isolated by `source_scope` so `pdf+table+kb` output stays behaviorally unchanged

### 2. Hybrid summary should resemble a single integrated answer

The final hybrid answer should not look like:

- `PDF 原文证据：...`
- `表格执行结果：...`

inside every section as the dominant output form.

It should read like one answer synthesized from those sources, while still respecting source precedence.

## Frontend Presentation

The current shared Markdown presentation is sufficient as a base.

Frontend work is only required if needed to make the aligned output clearly readable.

Required presentation behaviors:

- chapter headings remain visually distinct
- nested lists remain readable
- `局限性` and note/disclaimer are clearly separated from the main body

This is a rendering refinement task, not a frontend redesign task.

## Acceptance Criteria

All acceptance criteria below apply only to:

- `Patent` `pdf_qa` + `source_scope=pdf` summary requests
- `Patent` `hybrid_qa` + `source_scope=pdf+table` summary requests

They do not apply to:

- standalone `tabular_qa`
- `hybrid_qa` with `pdf+kb`
- `hybrid_qa` with `table+kb`
- `hybrid_qa` with `pdf+table+kb`

### Output logic parity

For the same uploaded paper and the same summary intent:

- `Patent` should no longer feel like a formatter-generated outline while `FastQA` feels like a model-written summary
- `Patent` should preserve multi-step logic and explanatory detail when available

### Structural parity

`Patent` summary answers must include:

- `研究目的和背景`
- `研究方法/实验设计`
- `主要发现和结果`
- `结论和意义`
- `局限性`
- `注*`

### Processing parity

`Patent` must align with the `FastQA` processing philosophy in these ways:

- prompt remains compact
- context remains bounded
- good model answers are preserved rather than heavily rewritten
- fallback is secondary rather than dominant

### Formatter decision parity

For in-scope summary requests, formatter routing must satisfy all of the following:

- answers already satisfying the four primary summary headings are preserved, except for allowed heading cleanup and note injection
- answers with near-complete structure enter repair instead of full reconstruction
- full extractive fallback is only taken for empty, degraded, or unsalvageable answers
- the default path no longer rebuilds every summary from a tiny keyword-picked point set
- degraded-answer detection and non-trivial-content thresholds are deterministic and centrally defined

### Scope safety

No behavior changes are allowed for:

- `FastQA`
- `Patent` knowledge-base-only QA
- `Patent` standalone `tabular_qa`
- `Patent` ordinary non-file QA
- `Patent` `hybrid_qa` with `pdf+kb`, `table+kb`, or `pdf+table+kb`
- unrelated compare behavior

## Implementation Guidance

Primary implementation targets:

- `Patent` PDF summary prompt builder
- `Patent` PDF summary formatter/normalizer
- `Patent` hybrid summary synthesis path for `source_scope=pdf+table` only, with explicit isolation from the existing `pdf+table+kb` merge flow
- optional shared frontend Markdown rendering refinements

Primary non-goal:

- reproducing `FastQA` output by prompt inflation

## Notes

This spec intentionally focuses on logic alignment rather than domain-specific field extraction requirements such as model names, dataset names, or metric labels. Those may appear naturally in good summaries, but they are not the defining alignment criterion for this task.
