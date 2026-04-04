# File And Hybrid QA Protocol Spec

## 1. Purpose

This document defines the target architecture and execution contract for file-aware QA in the gateway-based system.

Scope is intentionally limited to file-involved turns:
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

Out of scope:
- plain non-file `kb_qa`
- public-service auth / conversation persistence contract details
- highThinkingQA request contract

Patent note:
- `patent` now consumes the same canonical file-aware contract when `requested_mode=patent`
- `gateway` remains the only file-intent authority
- execution semantics stay backend-local: `fastQA` uses fast knowledge/runtime, `patent` uses patent-local knowledge/runtime

Primary goal:
- make `gateway` the single routing and file-context authority
- make downstream file executors pure executors for file-aware ask turns
- remove duplicate route/file intent judgment from downstream execution services
- define a stable protocol that future backends must adapt to

---

## 2. Background

The current system has already moved major responsibilities into `gateway`, but the file QA boundary is not yet fully clean.

Current problems:
- route/file intent ownership used to be split across `gateway` and downstream executors such as `fastQA`, which created duplicate judgment risk
- `pdf_qa`, `tabular_qa`, and `hybrid_qa` semantics are not frozen as a protocol.
- "mixed QA" is still partially represented as `allow_kb_verification` attached to single-source routes, which creates ambiguous execution semantics.
- frontend-visible stream behavior and file-selection metadata can drift if route authority and frontend-visible short-circuit contracts are not documented precisely.

This spec fixes those problems by defining a strict upstream/downstream contract.

---

## 3. Design Principles

### 3.1 Single authority

`gateway` is the only service allowed to:
- judge whether the turn is file-aware
- choose which file(s) participate in this turn
- decide whether the turn is single-source or multi-source
- decide the final execution route
- decide whether knowledge-base supplementation is enabled

Downstream executors such as `fastQA` and `patent` must not re-decide route or file intent from the raw question.

### 3.2 Explicit source model

Route alone is not enough. The execution request must explicitly state which source families participate.

### 3.3 Execution-only downstream

The downstream file executor is responsible for:
- validating execution prerequisites
- loading files
- running retrieval / execution / synthesis
- emitting stable stream events
- returning citations and source usage metadata

It is not responsible for upstream user-intent interpretation.

### 3.4 Backward-safe migration

The design should allow incremental migration from the current state:
- first freeze the protocol
- then align gateway output to that protocol
- then remove duplicate downstream route/file reinterpretation

---

## 4. Terminology

### 4.1 Route

The top-level executor selected by `gateway`.

Allowed values:
- `kb_qa`
- `pdf_qa`
- `tabular_qa`
- `hybrid_qa`

For this spec, only the last three are in scope.

### 4.2 Source Scope

Describes the actual source families participating in this turn.

Allowed values for file-aware turns:
- `pdf`
- `table`
- `pdf+kb`
- `table+kb`
- `pdf+table`
- `pdf+table+kb`

Rules:
- `pdf_qa` requires `source_scope=pdf`
- `tabular_qa` requires `source_scope=table`
- `hybrid_qa` requires one of:
  - `pdf+kb`
  - `table+kb`
  - `pdf+table`
  - `pdf+table+kb`

### 4.3 Turn Mode

High-level conversational classification.

Allowed values:
- `kb_only`
- `file_only`
- `mixed`

Meaning:
- `kb_only`: no file participates in answering
- `file_only`: file(s) participate, but KB does not
- `mixed`: file(s) and KB both participate

### 4.4 Used Files vs Execution Files

`used_files`:
- file objects chosen by gateway for this turn
- user-facing trace of why those files are in scope

`execution_files`:
- concrete files that the downstream executor should load
- fully normalized execution-time descriptors

In most cases they overlap, but protocol keeps them separate.

### 4.5 Primary File

The main anchor file when one file has privileged context.

Examples:
- single selected PDF in `pdf_qa`
- main table in `table+kb`
- lead PDF for `pdf+table+kb` when the user explicitly references a paper and secondarily mentions a table

---

## 5. Responsibility Boundary

## 5.1 Gateway owns

`gateway` owns all of the following:
- conversation file lookup
- file intent detection
- explicit ref parsing such as `#1`, ordinal references, latest upload, plural file scope
- deleted/missing file clarification
- last-focus reuse policy
- generic file-topic fallback policy
- route selection
- source-scope selection
- turn-mode selection
- knowledge-base participation decision
- final `execution_files` list
- payload normalization before forwarding to the selected backend

### 5.2 Downstream executor owns

The selected downstream file executor owns all of the following:
- contract validation
- verifying route/source_scope compatibility
- validating file-type presence
- loading selected files
- performing PDF retrieval / parsing
- performing table planning / execution
- performing KB retrieval when enabled by source_scope
- answer synthesis
- streaming step / content / done events
- returning source usage and references

### 5.3 Downstream executor must not own

The selected downstream file executor must not:
- decide whether a turn is file-aware from the raw user question
- reinterpret `conversation_id` into selected files
- override route because it thinks another route is better
- select different files than the upstream payload
- silently enable KB because it infers mixed intent
- silently disable KB because it dislikes mixed intent

### 5.4 Validation vs reinterpretation

The selected downstream file executor may reject invalid upstream input.

Examples of valid rejection:
- missing explicit `route` for a file execution payload
- missing explicit `source_scope` for a file route
- missing explicit `turn_mode` for a file route
- `route=pdf_qa` but no PDF file in `execution_files`
- `route=tabular_qa` but no table file in `execution_files`
- `route=hybrid_qa` with `source_scope=pdf+table` but no table file present

The selected downstream file executor may not reinterpret those requests into another route. It must fail with a protocol error.

---

## 6. Route Model

### 6.1 Single-source routes

#### `pdf_qa`
Use when:
- the answer should be based only on PDF file context
- no KB supplementation is intended
- no table execution is required

Required:
- at least one selected PDF
- `source_scope=pdf`
- `turn_mode=file_only`

#### `tabular_qa`
Use when:
- the answer should be based only on table execution
- no KB supplementation is intended
- no PDF textual evidence is required

Required:
- at least one selected table file
- `source_scope=table`
- `turn_mode=file_only`

### 6.2 Hybrid route

#### `hybrid_qa`
Use when any of the following are true:
- file + KB
- PDF + table
- PDF + table + KB

Allowed source scopes:
- `pdf+kb`
- `table+kb`
- `pdf+table`
- `pdf+table+kb`

Rules:
- `hybrid_qa` is the only route that may involve more than one source family
- `hybrid_qa` is the only route that may involve KB together with files
- a file-aware turn that needs both files and KB must not be encoded as `pdf_qa + allow_kb_verification`

This is a deliberate semantic simplification.

---

## 7. Decision Matrix For Gateway

### 7.1 High-level decision order

For each ask turn, `gateway` should evaluate in this order:

1. Is there a file-aware question intent?
2. Which file(s) are in scope?
3. Do those files require clarification before execution?
4. Which source families are actually needed to answer?
5. Which route matches those sources?
6. What is the normalized execution payload?

### 7.2 File-aware decision outcomes

#### Case A: no file participation
- result: out of scope for this spec
- route remains ordinary non-file path

#### Case B: only PDF participation
- route: `pdf_qa`
- source_scope: `pdf`
- turn_mode: `file_only`

#### Case C: only table participation
- route: `tabular_qa`
- source_scope: `table`
- turn_mode: `file_only`

#### Case D: PDF + KB
- route: `hybrid_qa`
- source_scope: `pdf+kb`
- turn_mode: `mixed`

#### Case E: table + KB
- route: `hybrid_qa`
- source_scope: `table+kb`
- turn_mode: `mixed`

#### Case F: PDF + table
- route: `hybrid_qa`
- source_scope: `pdf+table`
- turn_mode: `file_only`

#### Case G: PDF + table + KB
- route: `hybrid_qa`
- source_scope: `pdf+table+kb`
- turn_mode: `mixed`

### 7.3 Gateway heuristics for KB participation

Gateway should mark KB participation only when there is evidence that the user wants:
- domain explanation beyond file facts
- background knowledge not guaranteed to be present in the selected files
- file-content cross-check against the broader knowledge base
- concept explanation, mechanism explanation, or literature-background synthesis

Examples that should trigger KB participation:
- "结合知识库解释"
- "补充一下这个机理"
- "从知识库角度看"
- "结合领域背景说明"
- "帮我解释为什么"

Gateway should not enable KB merely because the question is hard.

### 7.4 Gateway heuristics for PDF+table without KB

Gateway should choose `source_scope=pdf+table` when:
- the user explicitly wants to compare paper claims and table values
- both source types are selected or clearly referenced
- there is no indication that broader KB supplementation is required

### 7.5 Clarification rule

If file selection is ambiguous, gateway must not forward execution.
It must return clarification immediately.

Clarification is required when, for example:
- user says "这篇论文" but multiple candidate PDFs are active
- user references `#3` but file numbering cannot be resolved
- user references a deleted file number
- user references a table generically but multiple tables are equally plausible

---

## 8. Canonical Gateway -> File Executor Request Contract

## 8.1 Required top-level fields

```json
{
  "question": "string",
  "conversation_id": 123,
  "chat_history": [],
  "requested_mode": "fast",
  "actual_mode": "fast",
  "route": "pdf_qa|tabular_qa|hybrid_qa",
  "source_scope": "pdf|table|pdf+kb|table+kb|pdf+table|pdf+table+kb",
  "turn_mode": "file_only|mixed",
  "kb_enabled": true,
  "used_files": [],
  "execution_files": [],
  "selected_file_ids": [1, 2],
  "primary_file_id": 1,
  "trace_id": "req_xxx",
  "file_selection": {},
  "route_reasons": [],
  "route_confidence": 1.0,
  "classifier_used": false,
  "options": {}
}
```

### 8.2 Field semantics

#### `route`
The executor family selected by gateway.

#### `source_scope`
The source-family combination selected by gateway.

#### `kb_enabled`
A normalized convenience boolean.
Rules:
- true if and only if `source_scope` contains `kb`
- false otherwise

#### `selected_file_ids`
Stable upstream record of file ids selected for the turn.

#### `primary_file_id`
Optional but strongly recommended.
Must be one of `selected_file_ids`.

#### `file_selection`
Must preserve the upstream decision trace.
Recommended fields:
- `strategy`
- `selection_semantic`
- `selected_file_ids`
- `clarify_candidates` if applicable before clarification cutoff
- `turn_mode`
- `source_scope`
- `kb_enabled`

#### `route_reasons`
Gateway-owned explainability field describing why the final route was chosen.

#### `route_confidence`
Gateway-owned confidence score. Deterministic rule outputs are typically `1.0`; classifier-assisted ambiguity paths may be lower.

#### `classifier_used`
Boolean trace flag indicating whether the ambiguity-only classifier seam participated in the final gateway decision.

### 8.3 File descriptor schema

Each `execution_files` item should be fully normalized.

Recommended schema:

```json
{
  "file_id": 1,
  "file_name": "paper.pdf",
  "file_type": "pdf|excel|csv",
  "local_path": "/abs/path/to/file",
  "parse_status": "ready|failed|pending",
  "index_status": "ready|failed|pending",
  "processing_stage": "ready|failed|pending",
  "selected_reason": "explicit_ref|latest_upload|last_focus|plural_scope|filename_match",
  "source": "gateway_file_context",
  "file_meta": {
    "doi": "10.xxxx/xxxx",
    "parsed_preview": "optional short preview"
  }
}
```

### 8.4 Contract invariants

The selected downstream file executor must be allowed to assume all of the following are true:
- `route` is final
- `source_scope` is final
- `turn_mode` is final
- `selected_file_ids` and `execution_files` correspond to the same upstream choice
- `kb_enabled` matches `source_scope`
- `execution_files` contains all files needed for execution

---

## 9. File Executor Execution Semantics

Unless otherwise noted, the execution semantics in this section describe the canonical file-aware contract that `fastQA` and `patent` both implement with backend-local knowledge/runtime.

## 9.1 Shared execution pipeline

All file-aware routes should follow the same top-level phases:

1. contract validation
2. source preparation
3. route-specific retrieval / execution
4. synthesis preparation
5. final answer generation
6. reference normalization
7. done event emission

### 9.2 `pdf_qa`

Required source scope:
- `pdf`

Execution stages:
1. validate at least one PDF file exists
2. choose primary PDF and any secondary PDFs
3. load single or multi-PDF content
4. run PDF answer generation
5. generate content stream
6. normalize PDF references / DOI links
7. emit done

Forbidden behavior:
- no KB retrieval
- no table executor invocation
- no route escalation to `hybrid_qa`

### 9.3 `tabular_qa`

Required source scope:
- `table`

Execution stages:
1. validate at least one table file exists
2. load workbook(s)
3. profile schema
4. build execution plan
5. execute table operation
6. generate answer from execution result
7. emit done

Forbidden behavior:
- no KB retrieval
- no PDF evidence retrieval
- no route escalation to `hybrid_qa`

### 9.4 `hybrid_qa`

`hybrid_qa` is a route family with submodes driven by `source_scope`.

#### A. `pdf+kb`
Execution stages:
1. validate at least one PDF exists
2. load / retrieve PDF evidence
3. retrieve KB evidence
4. synthesize with PDF evidence as primary file source
5. return answer with source attribution

#### B. `table+kb`
Execution stages:
1. validate at least one table exists
2. execute deterministic table logic
3. retrieve KB evidence for explanation / cross-check
4. synthesize with table result as primary factual source
5. return answer with source attribution

#### C. `pdf+table`
Execution stages:
1. validate both PDF and table files exist
2. load PDF evidence
3. execute table logic
4. compare / combine file evidence
5. synthesize final answer without KB

#### D. `pdf+table+kb`
Execution stages:
1. validate both PDF and table files exist
2. load PDF evidence
3. execute table logic
4. retrieve KB evidence
5. synthesize all three sources

### 9.5 Evidence priority rules

When source families disagree or compete for emphasis, precedence must be:

1. table execution result
2. selected PDF evidence
3. KB explanation / supplementation

Reason:
- tables are structured, deterministic execution outputs
- PDFs are primary file text evidence
- KB is a supplementary explanation layer

### 9.6 Synthesis discipline

Synthesis prompt / policy must enforce:
- do not replace table facts with KB guesses
- do not replace PDF claims with generic background unless the file is silent
- explicitly mark uncertainty when sources conflict
- preserve traceability to file evidence and KB references

---

## 10. Stream Contract For File-Aware Routes

## 10.1 Event types

Required event types:
- `metadata`
- `step`
- `content`
- `done`
- `error`

### 10.2 Common event rules

Every event should include:
- `trace_id`
- `route`
- `source_scope`

Recommended common fields:
- none beyond route-relevant payload fields and `trace_id`

### 10.3 Metadata

Example:

```json
{
  "type": "metadata",
  "trace_id": "req_xxx",
  "route": "hybrid_qa",
  "source_scope": "pdf+kb",
  "query_mode": "hybrid_qa",
  "requested_mode": "fast",
  "actual_mode": "fast"
}
```

### 10.4 Step

`step` must represent user-meaningful execution milestones.

Recommended canonical step keys:
- `validate_contract`
- `resolve_sources`
- `load_pdf`
- `profile_table`
- `plan_table`
- `execute_table`
- `retrieve_kb`
- `retrieve_pdf_evidence`
- `merge_file_evidence`
- `synthesize`
- `normalize_citations`
- `finalize`

Example:

```json
{
  "type": "step",
  "trace_id": "req_xxx",
  "route": "hybrid_qa",
  "source_scope": "pdf+table+kb",
  "step": "execute_table",
  "status": "success",
  "message": "已完成表格执行，得到 12 条候选结果"
}
```

### 10.5 Content

Incremental answer output.

```json
{
  "type": "content",
  "trace_id": "req_xxx",
  "route": "hybrid_qa",
  "source_scope": "pdf+kb",
  "content": "..."
}
```

### 10.6 Done

Example:

```json
{
  "type": "done",
  "trace_id": "req_xxx",
  "route": "hybrid_qa",
  "source_scope": "pdf+table+kb",
  "final_answer": "...",
  "references": ["10.xxxx/..."],
  "reference_objects": [],
  "pdf_links": [],
  "reference_links": [],
  "used_files": [],
  "file_selection": {},
  "source_usage": {
    "pdf_used": true,
    "table_used": true,
    "kb_used": true
  },
  "timings": {}
}
```

### 10.7 Error

Protocol / execution errors must be explicit.

Example:

```json
{
  "type": "error",
  "trace_id": "req_xxx",
  "route": "hybrid_qa",
  "source_scope": "pdf+table",
  "code": "EXECUTION_FILES_REQUIRED",
  "error": "selected source_scope requires at least one table file",
  "message": "hybrid_qa with source_scope=pdf+table requires at least one table file"
}
```

---

## 11. Error Model

### 11.1 Gateway-side errors

Gateway should own and emit:
- `FILE_SELECTION_CLARIFICATION_REQUIRED`
- `CONVERSATION_FILE_PROVIDER_UNAVAILABLE`
- `FILE_NOT_READY`
- `FILE_PROCESSING_FAILED`
- `FILE_NOT_FOUND`

These errors happen before forwarding.

### 11.2 FastQA protocol errors

FastQA should own and emit:
- `ROUTE_REQUIRED`
- `CONTRACT_FIELD_REQUIRED`
- `CONTRACT_FIELD_INVALID`
- `SOURCE_SCOPE_INVALID`
- `EXECUTION_FILES_REQUIRED`
- `PRIMARY_FILE_INVALID`

### 11.3 FastQA execution errors

Examples:
- `FASTQA_PDF_LOAD_FAILED`
- `FASTQA_TABLE_LOAD_FAILED`
- `FASTQA_TABLE_PLAN_FAILED`
- `FASTQA_KB_RETRIEVAL_FAILED`
- `FASTQA_SYNTHESIS_FAILED`

### 11.4 No silent downgrade policy

FastQA must not silently downgrade:
- `hybrid_qa` -> `pdf_qa`
- `hybrid_qa` -> `tabular_qa`
- `pdf_qa` -> `kb_qa`
- `tabular_qa` -> `kb_qa`

If upstream contract is invalid, fail explicitly.

---

## 12. Logging And Observability

## 12.1 Gateway logs

Gateway should log:
- selected route
- selected source_scope
- selected file ids
- selection strategy
- whether clarification occurred
- forwarded trace_id

### 12.2 FastQA logs

FastQA should log:
- request route
- request source_scope
- execution file counts by type
- primary file id
- validation result
- per-stage start/end
- source usage summary
- error code if failure

### 12.3 Correlation

All logs must be correlated with:
- `trace_id`
- `conversation_id` if present
- `route`
- `source_scope`

---

## 13. Performance And Streaming Expectations

### 13.1 Streaming requirement

For all file-aware routes:
- `metadata` and first `step` should emit immediately after validation
- long-running retrieval or file loading stages must be visible as `step` events
- final answer must stream as incremental `content`, not arrive only in final `done`

### 13.2 Concurrency principles

For future implementation:
- PDF evidence loading and KB retrieval should be parallelizable in `pdf+kb`
- PDF evidence loading and table planning may overlap where safe in `pdf+table+kb`
- deterministic table execution should happen before synthesis

### 13.3 No hidden buffered synthesis

A route must not spend a long interval with no outward event while doing multi-source work.
If final answer generation is slow, at least stage transitions must continue to stream.

---

## 14. Migration Strategy

### 14.1 Phase 1: protocol freeze
- finalize field names and invariants
- freeze route/source_scope semantics
- freeze stream event expectations

### 14.2 Phase 2: gateway alignment
- produce canonical `source_scope`
- produce canonical `file_selection`
- stop encoding mixed file turns as single-source route + flag hacks

### 14.3 Phase 3: fastQA input hardening
- validate `route` + `source_scope`
- preserve gateway-selected files
- stop route reinterpretation

### 14.4 Phase 4: execution alignment
- add or align `pdf+kb`
- add or align `table+kb`
- align `pdf+table`
- align `pdf+table+kb`

### 14.5 Phase 5: duplicate authority removal
- remove downstream file-context authority from fastQA
- keep only validation helpers

---

## 15. Scenario Examples

## 15.1 Pure PDF

User:
- "总结这篇论文的核心结论"

Gateway result:
- `route=pdf_qa`
- `source_scope=pdf`
- `turn_mode=file_only`
- `execution_files=[paper.pdf]`

FastQA behavior:
- load PDF
- answer from PDF only

## 15.2 PDF + KB explanation

User:
- "这篇论文里提到的电压窗口为什么这样设计，结合知识库解释一下"

Gateway result:
- `route=hybrid_qa`
- `source_scope=pdf+kb`
- `turn_mode=mixed`
- `execution_files=[paper.pdf]`
- `kb_enabled=true`

FastQA behavior:
- load PDF evidence
- retrieve KB support
- synthesize explanation

## 15.3 Pure table

User:
- "这个表里哪组效率最高"

Gateway result:
- `route=tabular_qa`
- `source_scope=table`
- `turn_mode=file_only`
- `execution_files=[results.xlsx]`

FastQA behavior:
- profile table
- execute ranking
- answer from deterministic result

## 15.4 Table + KB

User:
- "这个表里循环寿命最差的是哪组，结合知识库解释原因"

Gateway result:
- `route=hybrid_qa`
- `source_scope=table+kb`
- `turn_mode=mixed`
- `execution_files=[results.xlsx]`

FastQA behavior:
- execute table analysis
- retrieve KB explanation
- synthesize with table result as primary fact

## 15.5 PDF + table

User:
- "结合这篇论文和这个表格，判断文中的结论和实验数据是否一致"

Gateway result:
- `route=hybrid_qa`
- `source_scope=pdf+table`
- `turn_mode=file_only`
- `execution_files=[paper.pdf, results.xlsx]`

FastQA behavior:
- retrieve PDF evidence
- execute table logic
- compare and synthesize

## 15.6 PDF + table + KB

User:
- "结合论文、表格和知识库，判断这组设计是否合理，并说明原因"

Gateway result:
- `route=hybrid_qa`
- `source_scope=pdf+table+kb`
- `turn_mode=mixed`
- `execution_files=[paper.pdf, results.xlsx]`

FastQA behavior:
- retrieve PDF evidence
- execute table logic
- retrieve KB explanation
- synthesize three-source answer

## 15.7 Ambiguous PDF reference

User:
- "这篇论文里是怎么解释的"
- active PDFs: three

Gateway result:
- clarification required
- do not forward to fastQA

## 15.8 Deleted file reference

User:
- "看 #2 这篇论文"
- `#2` refers to deleted file

Gateway result:
- clarification / deleted reference error
- do not forward to fastQA

---

## 16. Acceptance Criteria

The target design is accepted when all of the following are true:
- `gateway` is the only route/file-context authority for file-aware turns
- `fastQA` accepts explicit `route + source_scope + execution_files` and does not reinterpret them
- all file+KB turns are represented as `hybrid_qa`, not single-source route plus hidden KB behavior
- frontend can distinguish `pdf_qa`, `tabular_qa`, and hybrid source combinations through stable metadata
- clarification always terminates in gateway before forwarding
- protocol errors are explicit and typed
- stream output remains progressive for all file-aware routes

---

## 17. Recommended Next Artifacts

After this spec, implementation planning should produce:
- a gateway remediation task list
- a fastQA contract hardening task list
- route-specific execution alignment tasks for:
  - `pdf_qa`
  - `tabular_qa`
  - `hybrid_qa`
- a dedicated test matrix covering all source_scope combinations
