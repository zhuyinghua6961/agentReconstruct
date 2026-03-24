# File And Hybrid QA Task Breakdown

## Goal

Break the target file/hybrid QA design into independently executable workstreams.

This task document assumes the protocol is defined by:
- [file_hybrid_qa_protocol_spec.md](/home/cqy/worktrees/highThinking/docs/file_hybrid_qa_protocol_spec.md)

Scope remains limited to:
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

Out of scope:
- ordinary non-file `kb_qa`
- highThinkingQA changes
- patent mode changes

---

## 1. Delivery Strategy

Use a staged rollout with explicit boundaries.

Recommended implementation order:
1. freeze protocol fields and invariants
2. align gateway payload production
3. harden fastQA contract validation
4. align `pdf_qa`
5. align `tabular_qa`
6. align `hybrid_qa`
7. remove duplicate downstream authority
8. expand tests and observability
9. run end-to-end verification

This order minimizes confusion because execution changes should not happen before route semantics are frozen.

---

## 2. Workstream Overview

### Workstream A: Protocol And Shared Types
Owner:
- one agent

Purpose:
- freeze request/response/event contract for file-aware turns

Dependencies:
- none

Can run in parallel with:
- documentation-only review

### Workstream B: Gateway Decision Layer
Owner:
- one agent

Purpose:
- make gateway emit canonical `route + source_scope + file_selection + execution_files`

Dependencies:
- Workstream A protocol frozen

### Workstream C: FastQA Contract Hardening
Owner:
- one agent

Purpose:
- make fastQA trust gateway route authority and only validate, not reinterpret

Dependencies:
- Workstream A
- should begin after Workstream B field shape is stable

### Workstream D: PDF Route Alignment
Owner:
- one agent

Purpose:
- align `pdf_qa` execution semantics to the new contract

Dependencies:
- Workstream C validation layer in place

### Workstream E: Tabular Route Alignment
Owner:
- one agent

Purpose:
- align `tabular_qa` execution semantics to the new contract

Dependencies:
- Workstream C validation layer in place

### Workstream F: Hybrid Route Alignment
Owner:
- one agent

Purpose:
- implement and align hybrid submodes:
  - `pdf+kb`
  - `table+kb`
  - `pdf+table`
  - `pdf+table+kb`

Dependencies:
- Workstream C
- benefits from D and E

### Workstream G: Stream Contract And Frontend Compatibility
Owner:
- one agent

Purpose:
- ensure stream events are stable and frontend-consumable for all file-aware routes

Dependencies:
- B, C, D, E, F partially complete

### Workstream H: Test Matrix And End-to-End Verification
Owner:
- one agent or review-focused agent

Purpose:
- lock behavior with route/source_scope coverage

Dependencies:
- all core workstreams complete enough to verify

---

## 3. Multi-Agent Dispatch Recommendation

Recommended parallel allocation after protocol freeze:

- Agent 1: gateway route + source_scope alignment
- Agent 2: fastQA contract validation and duplicate-authority removal
- Agent 3: `pdf_qa` alignment
- Agent 4: `tabular_qa` alignment
- Agent 5: `hybrid_qa` alignment
- Agent 6: review/test matrix and regression audit

Important coordination rule:
- only one agent should own shared request models / shared router signatures at a time
- route-specific agents should not all edit the same router file concurrently without ownership split

Recommended file ownership:
- Agent 1 owns `gateway/app/...`
- Agent 2 owns `fastQA/app/services/request_adapter.py`, `fastQA/app/routers/qa.py`, shared contract helpers
- Agent 3 owns PDF modules under `fastQA/app/modules/qa_pdf/` plus PDF-specific wrapper code
- Agent 4 owns tabular modules under `fastQA/app/modules/qa_tabular/`
- Agent 5 owns hybrid orchestration logic and route-specific synthesis glue
- Agent 6 owns tests and review-only validation notes

---

## 4. Detailed Task List

## Task Group 1: Protocol Freeze

### Task 1.1 Define final file-aware route semantics
Deliverables:
- freeze that `kb_qa` is out of scope
- freeze that file-aware routes are only `pdf_qa`, `tabular_qa`, `hybrid_qa`
- freeze that file+KB is always `hybrid_qa`

Acceptance:
- no remaining ambiguity around `pdf_qa + allow_kb_verification`

### Task 1.2 Define `source_scope`
Deliverables:
- define allowed file-aware source scopes
- define route-to-source_scope compatibility matrix
- define `kb_enabled` derivation rule

Acceptance:
- every file-aware request can be classified into one allowed route/scope pair

### Task 1.3 Define request invariants
Deliverables:
- required request fields
- file descriptor schema
- `primary_file_id` behavior
- `selected_file_ids` and `file_selection` semantics

Acceptance:
- fastQA can validate every request without guessing intent

### Task 1.4 Define stream and error contract
Deliverables:
- canonical event types
- common event fields
- protocol error code list
- clarification ownership rule

Acceptance:
- frontend and gateway can treat all file-aware routes consistently

---

## Task Group 2: Gateway Alignment

### Task 2.1 Audit current gateway file-context resolver against target semantics
Focus:
- identify current single-source and mixed-source heuristics
- identify where source_scope is missing
- identify where deleted/missing clarification differs from target

Acceptance:
- gap list exists for gateway-only changes

### Task 2.2 Produce canonical `source_scope`
Focus:
- extend route decision output to include source_scope
- derive `kb_enabled` from source_scope
- preserve `turn_mode`

Acceptance:
- every forwarded file-aware request includes explicit source_scope

### Task 2.3 Normalize `file_selection`
Focus:
- standardize strategy
- standardize selection semantic
- include selected file ids
- include primary file id when available

Acceptance:
- downstream receives stable selection metadata

### Task 2.4 Clarification cutoff enforcement
Focus:
- ensure ambiguous file turns never reach fastQA
- ensure error contract is typed and stable

Acceptance:
- no forwarded request with unresolved file ambiguity

### Task 2.5 Gateway tests
Need tests for:
- single PDF -> `pdf_qa`
- single table -> `tabular_qa`
- PDF + KB -> `hybrid_qa/pdf+kb`
- table + KB -> `hybrid_qa/table+kb`
- PDF + table -> `hybrid_qa/pdf+table`
- PDF + table + KB -> `hybrid_qa/pdf+table+kb`
- ambiguous reference clarification
- deleted reference clarification

---

## Task Group 3: FastQA Contract Hardening

### Task 3.1 Introduce route/scope validator
Focus:
- reject invalid route/source_scope combinations
- reject missing file families required by source_scope
- reject invalid primary file id

Acceptance:
- bad upstream payloads fail explicitly with protocol errors

### Task 3.2 Remove route reinterpretation from fastQA
Focus:
- stop treating fastQA as a second route authority
- stop overwriting route because downstream resolver thinks otherwise

Acceptance:
- route used for execution always equals gateway route

### Task 3.3 Remove file-selection reinterpretation from fastQA
Focus:
- keep validation helpers only
- no downstream re-selection from question text
- no synthetic conversation-based selection logic for file-aware turns

Acceptance:
- fastQA executes exactly on upstream-selected files

### Task 3.4 Preserve upstream metadata to done event
Focus:
- keep `route`
- keep `source_scope`
- keep `used_files`
- keep `file_selection`
- expose `source_usage`

Acceptance:
- frontend receives stable execution summary matching gateway decision

### Task 3.5 FastQA contract tests
Need tests for:
- route/scope mismatch rejection
- missing-PDF rejection
- missing-table rejection
- invalid primary file rejection
- no downstream route rewrite when route explicit

---

## Task Group 4: PDF Route Alignment

### Task 4.1 Lock `pdf_qa` to `source_scope=pdf`
Focus:
- no KB branch in pure PDF route
- no hybrid behavior leakage

Acceptance:
- `pdf_qa` never consumes KB or table context

### Task 4.2 Standardize PDF multi-file behavior
Focus:
- single PDF
- multiple selected PDFs
- primary file semantics
- reference normalization

Acceptance:
- PDF-only routes have consistent evidence loading and output shape

### Task 4.3 PDF stream behavior audit
Focus:
- immediate metadata
- dispatch step
- progressive content
- final citations

Acceptance:
- no buffered all-at-once answer for PDF-only routes

### Task 4.4 PDF route tests
Need tests for:
- single PDF happy path
- multi-PDF happy path
- no-PDF protocol failure
- done metadata includes source_scope and source_usage

---

## Task Group 5: Tabular Route Alignment

### Task 5.1 Lock `tabular_qa` to `source_scope=table`
Focus:
- no KB retrieval
- no PDF evidence retrieval

Acceptance:
- pure table route remains deterministic + synthesis only

### Task 5.2 Standardize table execution output contract
Focus:
- plan stage
- execute stage
- final answer stage
- references behavior for pure table turns

Acceptance:
- table-only turns emit stable steps and final metadata

### Task 5.3 Tabular route tests
Need tests for:
- single table happy path
- multiple table routing if supported
- no-table protocol failure
- done metadata includes source_scope and source_usage

---

## Task Group 6: Hybrid Route Alignment

### Task 6.1 Implement `pdf+kb` as first-class hybrid submode
Focus:
- PDF evidence retrieval
- KB retrieval
- synthesis order
- source attribution

Acceptance:
- no use of `pdf_qa + hidden KB behavior`

### Task 6.2 Implement `table+kb` as first-class hybrid submode
Focus:
- deterministic table result first
- KB explanation second
- synthesis with priority discipline

Acceptance:
- final answer distinguishes table fact vs KB explanation

### Task 6.3 Align `pdf+table`
Focus:
- PDF evidence and table execution without KB
- compare/merge logic

Acceptance:
- route works without implicit KB dependency

### Task 6.4 Align `pdf+table+kb`
Focus:
- three-source orchestration
- explicit stage order
- stable synthesis prompt / policy

Acceptance:
- all three sources can participate without silent downgrade

### Task 6.5 Hybrid source attribution
Focus:
- expose what sources were actually used
- preserve references per source family where possible

Acceptance:
- frontend can display whether answer used PDF, table, KB

### Task 6.6 Hybrid tests
Need tests for:
- `pdf+kb`
- `table+kb`
- `pdf+table`
- `pdf+table+kb`
- missing required source-family failures
- evidence precedence behavior

---

## Task Group 7: Stream Contract And Frontend Compatibility

### Task 7.1 Canonical step vocabulary
Focus:
- ensure file-aware routes use predictable step keys
- remove random route-specific wording drift

Acceptance:
- frontend can display file-aware progress consistently

### Task 7.2 Ensure progressive output
Focus:
- no long silent periods without step updates
- no final answer arriving only in done

Acceptance:
- all routes stream visible progress before and during synthesis

### Task 7.3 Citation event strategy
Focus:
- decide whether citation is mid-stream or done-only
- keep final done citation payload complete

Acceptance:
- citation behavior is deterministic and documented

### Task 7.4 Frontend compatibility review
Focus:
- verify current frontend can consume metadata/step/content/done/error with route/source_scope additions

Acceptance:
- no breakage in file-aware session rendering

---

## Task Group 8: Duplicate Authority Removal

### Task 8.1 Delete downstream route-authority paths
Focus:
- remove dead or conflicting file-context route logic from fastQA runtime path
- preserve helper code only if needed for validation

Acceptance:
- only gateway decides route

### Task 8.2 Delete downstream file re-selection logic
Focus:
- no second-pass question-based file selection in fastQA

Acceptance:
- fastQA cannot execute on a file the gateway did not select

### Task 8.3 Review and simplify request adapter
Focus:
- adapter should validate and normalize, not infer hidden semantics beyond protocol rules

Acceptance:
- adapter becomes execution-safe rather than route-authoritative

---

## Task Group 9: Test Matrix And Review

### Task 9.1 Build route/source_scope matrix
Required rows:
- `pdf_qa/pdf`
- `tabular_qa/table`
- `hybrid_qa/pdf+kb`
- `hybrid_qa/table+kb`
- `hybrid_qa/pdf+table`
- `hybrid_qa/pdf+table+kb`

Required columns:
- valid payload
- invalid payload
- stream metadata
- step sequence
- content streaming
- done metadata
- source usage
- error code

### Task 9.2 Add gateway-to-fastQA integration tests
Focus:
- verify gateway decision survives through fastQA done event
- verify no downstream route rewrite

### Task 9.3 Add regression review checklist
Checklist should cover:
- deleted file refs
- ambiguous refs
- last-focus reuse
- latest upload selection
- multiple PDFs
- multiple tables
- mixed source precedence
- stream timing regressions

### Task 9.4 Manual end-to-end scenarios
Run and record:
- pure PDF
- pure table
- PDF + KB
- table + KB
- PDF + table
- PDF + table + KB

---

## 5. Suggested Execution Batches

### Batch 1: design-safe contract foundation
Can include:
- Task Group 1
- Task 2.1
- Task 3.1

### Batch 2: gateway and adapter alignment
Can include in parallel:
- gateway alignment tasks
- fastQA contract hardening tasks

### Batch 3: route-specific executors
Can include in parallel with separate owners:
- PDF route alignment
- tabular route alignment
- hybrid route alignment

### Batch 4: frontend/stream and review
Can include in parallel:
- stream contract alignment
- test matrix expansion
- review checklist

### Batch 5: cleanup and authority removal
Do after behavior is verified:
- duplicate authority removal
- dead path cleanup

---

## 6. Risks To Watch

### Risk 1: hidden behavior coupled to current fastQA second-pass resolver
Mitigation:
- introduce validation first
- remove reinterpretation only after gateway payload is complete

### Risk 2: hybrid semantics drift during parallel work
Mitigation:
- freeze route/source_scope matrix before assigning route-specific agents

### Risk 3: frontend assumptions on current event shapes
Mitigation:
- lock event vocabulary and add compatibility review before cleanup

### Risk 4: table and PDF route owners editing same router file
Mitigation:
- assign a single owner for shared router/adapter files
- route-specific owners should mainly edit service modules under their own directories

---

## 7. Definition Of Done

This work is done when:
- gateway is the sole authority for file-aware route and source selection
- fastQA no longer re-decides route or file scope
- all file+KB turns use `hybrid_qa`
- all six file-aware route/source_scope combinations are tested
- stream contract is stable and frontend-compatible
- clarification always terminates before forwarding
- route/source_scope/source_usage are visible in final execution outputs

---

## 8. Recommended First Implementation Slice

If work starts immediately, the safest first slice is:
- Task 1.1 to 1.4
- Task 2.2 to 2.4
- Task 3.1 to 3.4

Reason:
- this freezes the protocol and removes the biggest architectural risk before route-specific behavior is tuned.
