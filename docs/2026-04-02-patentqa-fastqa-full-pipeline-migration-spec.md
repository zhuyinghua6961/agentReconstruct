# PatentQA Full-Pipeline Migration Spec

## Document Status

- Last updated: 2026-04-02
- Status: spec only
- Goal: define how `patentQA` stops being a simplified retrieval backend and becomes a full `fastQA`-style staged QA system for patent-domain questions
- Primary audience: `patentQA`, `gateway`, `frontend-vue`, `public-service`, and implementation reviewers

---

## 1. Problem Statement

The current `patentQA` service is no longer a stub, but it still does not implement the same end-to-end execution model as `fastQA`.

Current `patentQA` behavior is roughly:

1. receive normalized request
2. run a direct patent retrieval path
3. synthesize an answer from retrieved evidence
4. return sync or SSE output

This is not sufficient for the target system.

The target is to migrate the full `fastQA` knowledge-base pipeline shape into `patentQA`, including:

1. stage 1 pre-answer and retrieval planning
2. stage 2 patent-native targeted retrieval
3. stage 2.5 reserved MD-expansion boundary
4. stage 3 source-evidence loading and table attachment
5. stage 4 synthesis based on stage 1 draft plus stage 3 evidence
6. streaming progress/events that match the `fastQA` operational pattern

The migration must preserve patent-domain correctness rather than mechanically reusing paper-domain contracts.

Scope note:

- this spec revision only aligns the QA core path from question intake to answer synthesis
- persistence, durable transcript ownership, gateway/public-service shell behavior, and other surrounding platform concerns remain `patentQA`'s own implementation unless explicitly stated otherwise

---

## 2. Design Goals

### 2.1 Primary goals

`patentQA` must:

1. reuse the proven `fastQA` execution skeleton where it is domain-agnostic
2. replace paper/DOI/PDF assumptions with patent-native contracts
3. force same-patent table evidence into the evidence bundle when any text chunk from that patent is selected
4. produce staged SSE output comparable to `fastQA`
5. remain compatible with current `gateway -> patentQA` caller-facing contracts
6. keep the current `patentQA` persistence and surrounding shell behavior intact while only redesigning the QA core

### 2.2 Non-goals

This spec does not attempt to:

1. merge `patentQA` into `fastQA`
2. add patent file upload or spreadsheet QA
3. redesign `gateway` routing policy for non-`kb_qa` patent turns
4. define a legal-opinion workflow
5. solve patent data acquisition outside the already available resource roots
6. redesign patent durable persistence, transcript ownership, or non-QA platform shell behavior

### 2.3 Preserved external contract

For the first migration increment, compatibility does not mean "roughly similar behavior". It means preserving the current strict patent Phase 1 caller-facing contract unless a later spec explicitly changes it.

The request contract that must remain preserved is the current one enforced by `patent/server/schemas/request_models.py`:

1. `requested_mode=patent`
2. `actual_mode=patent`
3. `route=kb_qa`
4. `turn_mode=kb_only`
5. `source_scope=kb`
6. `allow_kb_verification=false`
7. `used_files=[]`
8. `execution_files=[]`
9. `selected_file_ids=[]`
10. `primary_file_id=null`
11. `file_selection={}`
12. non-empty `trace_id` is required

The response and stream contract that must remain preserved for the first increment is the current one enforced by `patent/server/schemas/response_models.py`:

1. sync success response keeps the current patent fields and validation rules
2. stream event types remain `metadata`, `step`, `content`, `done`, `error`, plus `heartbeat` if needed operationally
3. `references` remain canonical patent identifiers, not paper-style citation labels
4. `timings`, `references`, `reference_objects`, `reference_links`, and `original_links` remain final-payload concepts carried by the sync response and `done` event
5. frontend and gateway integration must not require a new transport or a new patent-only outer envelope in the first increment

---

## 3. Core Conclusion

The correct migration strategy is:

1. keep the `fastQA` outer orchestration pattern
2. introduce a patent-specific staged runtime contract under that orchestration
3. redesign the intermediate evidence objects so they are patent-native
4. redesign stage semantics where the paper-domain meaning does not map to patent data

Boundary rule:

- only the QA core stages should be adapted from the reference flow
- persistence, transport, and other surrounding behaviors should remain on the current `patentQA` side of the boundary

This means `patentQA` should not continue growing around the current simplified path:

- `AskService -> PatentExecutor -> PatentRetrievalService -> answer builder`

as its long-term primary execution chain.

Instead, `patentQA` should evolve toward:

- `router -> conversation context -> patent kb service -> staged orchestrator -> patent generation runtime -> staged SSE/result contract`

The current retrieval service can still be reused internally, but it becomes a stage implementation detail rather than the whole system.

---

## 4. What Can Be Reused vs. What Must Be Redesigned

### 4.1 Reuse directly

The following `fastQA` concepts are reusable with minimal semantic change:

1. router-level request normalization and authority context loading
2. conversation context compression and injection discipline
3. `QaKbService`-style service boundary
4. stage orchestrator pattern
5. stage-level cache and singleflight layout
6. sync and stream dual execution shape
7. internal staged progress semantics

### 4.2 Reuse with adaptation

The following pieces should be structurally reused but semantically adapted:

1. stage 1 pre-answer contract
2. stage 2 retrieval aggregation
3. stage 2.5 evidence expansion
4. stage 3 evidence loading
5. stage 4 synthesis
6. reference object normalization
7. stage timing and stage status metadata

### 4.3 Must be redesigned

The following pieces must be redesigned because the paper-domain assumptions are wrong for patents:

1. DOI-centric retrieval result semantics
2. PDF-chunk-only stage 3 source model
3. DOI citation alignment in stage 4
4. top reference policy based on DOI citations
5. paper-centric synthesis prompts
6. reference link builders that assume PDF downloads or DOI previews

### 4.4 First-increment boundary

The first implementation increment should stay patent-specific at the external boundary.

That means:

1. reuse `fastQA` as the execution skeleton, not as the caller-facing contract
2. preserve the existing patent request/response/event schema for `gateway` and `frontend-vue`
3. map internal staged semantics onto current patent events, especially mapping progress into `step` events
4. defer cross-domain KB protocol generalization until patent staged parity is proven end to end
5. keep persistence, durable-mode handling, and platform shell behavior on the current `patentQA` implementation path

---

## 5. Existing FastQA Execution Model To Preserve

The target migration preserves the following `fastQA` execution logic:

1. route enters through a normalized KB ask path
2. conversation context is built before stage execution
3. stage 1 generates both:
   - a draft answer
   - structured retrieval plan
4. stage 2 executes targeted retrieval rather than a single flat search
5. stage 2.5 remains an explicit stage boundary even if a domain-specific implementation no-ops there
6. stage 3 builds the source evidence payload used by synthesis
7. stage 4 synthesizes from:
   - original user question
   - stage 1 deep answer
   - stage 3 evidence payload
   - retrieval metadata
   - conversation context
8. streaming path emits stage-level progress and final done payload

This exact staged logic is the baseline for parity.

Important patent-specific constraint:

- parity means preserving the staged execution shape, not reusing the `fastQA` stage 2.5 resource model
- parity also does not mean copying `fastQA`'s paper-style "claim-driven retrieval" semantics into patents
- `patentQA` does not assume a dedicated `md_expansion` vector database analogous to `fastQA`
- patent stage 2.5 is a reserved stage boundary and should default to skipped/no-op when no patent-side MD expert exists
- patent stage 2 must be built around candidate-patent recall plus in-patent evidence localization
- patent table attachment belongs to stage 3 by default, not to a synthetic patent MD-expansion stage

---

## 6. Patent-Specific Execution Model

### 6.1 Stage overview

The patent version must use the following staged semantics.

#### Stage 1: Pre-answer and patent retrieval planning

Input:

- user question
- normalized conversation context

Output:

- `deep_answer`
- `retrieval_plan`

The retrieval plan must remain structured and support at least:

- `question_type`
- `analysis_axes`
- `explicit_patent_ids`
- `candidate_recall_queries`
- `evidence_localization_queries`
- `preferred_sections`
- `filters`

Patent-specific requirement:

- stage 1 prompt must ask for patent-analysis-oriented retrieval plans rather than literature-review-oriented plans
- when the user asks about substitution risk, timing window, freedom-to-operate style signals, or technical route comparison, the retrieval plan should explicitly include those decision axes

Patent-specific anti-copy rule:

- stage 1 should not emit paper-style "one claim -> one retrieval query -> merge" plans as the main patent abstraction
- stage 1 should instead identify which patents to recall and which parts of each recalled patent must later be grounded

#### Stage 2: Patent-native targeted retrieval

Input:

- structured `retrieval_plan`
- current question

Output:

- `patent_retrieval_results`

This stage must retrieve against patent data sources, not paper metadata.

This stage must be designed as a two-step patent retrieval flow inside one stage:

1. candidate patent recall
2. in-patent evidence localization

Stage 2.1 candidate patent recall must support:

1. exact patent-id resolution when explicit identifiers are present
2. abstract-level vector recall for general patent questions
3. metadata lexical fallback over title, abstract, applicant, inventor, and classification fields
4. candidate-set ranking and deduplication at the patent level
5. extracting an ordered candidate `patent_id` list from abstract-hit metadata for downstream chunk localization

Stage 2.2 in-patent evidence localization must support:

1. chunk-level vector localization constrained to the recalled candidate patent set
2. section typing across claims vs description paragraphs
3. top-evidence selection at the section/snippet level rather than only at the patent level
4. fallback to archive-backed default claim/description anchors when vector localization is weak but candidate recall is confident
5. merging abstract-hit and chunk-hit evidence into one deduplicated retrieval payload

Reference-flow alignment rule:

1. for each retrieval instruction, stage 2 may first let the model derive a search query from the user question plus the current retrieval-plan item
2. stage 2 then executes a dual search:
   - abstract DB for coarse recall
   - chunk DB for constrained fine localization under `patent_id in candidate_set`
3. results from multiple retrieval-plan items are merged and deduplicated into one patent retrieval result set
4. stage 3 source ids must be extracted from patent metadata using `patent_id`, not paper-style DOI rules

The output must be normalized into a patent-native retrieval result object rather than a generic `documents/metadatas/distances` paper shape.

Patent-specific hard rule:

- stage 2 should not be modeled as "run several claim queries and merge hits" just because `fastQA` does that for papers
- the primary retrieval unit is the patent, and the primary grounding unit is the intra-patent section/snippet

#### Stage 2.5: Reserved MD-expansion boundary

This stage exists to preserve orchestration parity with `fastQA`, but it should default to skipped/no-op in patent mode.

Input:

- stage 2 retrieval results
- current patent runtime capabilities

Output:

- unchanged retrieval payload
- or an explicit "skipped" stage marker in metadata

Resource assumption for this stage:

- `patentQA` currently has abstract and chunk vector databases plus patent archive assets
- `patentQA` does not require or assume a third `md_expansion` vector database for stage 2.5
- patent mode does not enable a dedicated MD expert in the reference flow

This stage must:

1. preserve the stage boundary in orchestration and timings
2. default to a no-op / skipped implementation in patent mode
3. avoid introducing a patent-only MD vector expansion dependency
4. pass stage 2 retrieval results through to stage 3 unchanged unless a future patent-specific expansion design is separately specified

Patent-specific hard rule:

- stage 2.5 must not be repurposed into an invented patent MD-vector retrieval pass
- same-patent table evidence is still mandatory, but it is attached in stage 3 by default

#### Stage 3: Patent evidence bundle loading

This stage replaces the paper-domain notion of "load PDF chunks" with a patent-native evidence assembly step.

Input:

- stage 2 retrieval results
- selected patent ids / source ids
- optional patent-side config controlling PDF fallback

Output:

- `patent_evidence_bundle`

Default stage 3 behavior:

1. aggregate stage 2 retrieval snippets by `patent_id`
2. keep a bounded number of deduplicated matched snippets per patent
3. load same-patent `*_tables.json` from the patent original root and append them as table evidence
4. avoid opening PDFs by default

Optional stage 3 behavior:

1. when a patent-side config equivalent to `PATENT_STAGE3_FORCE_PDF=true` is enabled, read local patent PDFs for selected patents
2. still attach same-patent tables after PDF extraction
3. allow PDF-derived chunks and stage 2 retrieval snippets to coexist if the implementation chooses to preserve both

The `patent_evidence_bundle` must contain, per selected patent:

1. matched description snippets
2. matched claim snippets
3. abstract and title when useful
4. same-patent table supplements
5. original-view anchors
6. retrieval scores and section provenance

This stage is responsible for constructing the exact evidence payload used by synthesis.

#### Stage 4: Patent evidence synthesis

Input:

- user question
- stage 1 `deep_answer`
- stage 2 retrieval summary
- stage 3 `patent_evidence_bundle`
- conversation context

Output:

- streamed answer chunks
- final structured synthesis result

The stage 4 prompt must be patent-specific. It must instruct the model to:

1. reason from patent evidence rather than academic-paper evidence
2. distinguish background/legal boilerplate from substantive technical evidence
3. explicitly incorporate table data into analysis when available
4. prefer evidence-backed statements over general narrative filler
5. build patent references, not DOI citations

---

## 7. Patent-Native Intermediate Contracts

### 7.1 Rationale

The current `fastQA` intermediate objects assume:

- `doi`
- `pdf_chunks`
- `reference_objects` shaped around papers

This cannot be the primary contract in `patentQA`.

### 7.2 Required patent-native objects

The pipeline should standardize on these intermediate object families.

#### `PatentRetrievalPlan`

Fields:

- `question_type: str`
- `analysis_axes: list[str]`
- `explicit_patent_ids: list[str]`
- `candidate_recall_queries: list[str]`
- `evidence_localization_queries: list[str]`
- `preferred_sections: list[str]`
- `filters: dict[str, object]`

#### `PatentRetrievalHit`

Fields:

- `canonical_patent_id: str`
- `publication_number: str | None`
- `application_number: str | None`
- `title: str`
- `country: str | None`
- `kind_code: str | None`
- `matched_section_type: str`
- `matched_section_label: str`
- `matched_snippet: str`
- `claim_number: int | None`
- `paragraph_id: str | None`
- `abstract_score: float | None`
- `chunk_score: float | None`
- `provider: str`

#### `PatentEvidenceBundle`

Fields:

- `canonical_patent_id: str`
- `title: str`
- `abstract_text: str`
- `matched_evidence: list[PatentMatchedEvidence]`
- `table_supplements: list[PatentTableSupplement]`
- `reference_object: dict[str, object]`
- `reference_link: dict[str, object] | None`
- `original_links: list[dict[str, object]]`
- `scores: dict[str, float | None]`

#### `PatentSynthesisResult`

Fields:

- `success: bool`
- `final_answer: str`
- `references: list[str]`
- `reference_objects: list[dict[str, object]]`
- `reference_links: list[dict[str, object]]`
- `original_links: list[dict[str, object]]`
- `metadata: dict[str, object]`

### 7.3 Compatibility rule

The outer sync-success and `done` payloads should keep the current patent high-level shape used today, while the inner reference semantics become patent-native.

Specifically:

- `references` becomes patent-id oriented
- `reference_objects` becomes patent evidence oriented
- `reference_links` becomes original-view oriented
- `doi_locations` must not be carried over as a primary concept

---

## 8. Patent Runtime Contract

### 8.1 Required abstraction

`patentQA` must introduce a dedicated staged runtime interface. The simplest acceptable implementation is:

- either a new `PatentGenerationRuntime` protocol
- or an adapted `GenerationRuntime` implementation whose semantics are redefined for patents

### 8.2 Required methods

The runtime must provide methods equivalent to:

1. `stage1_pre_answer_and_planning`
2. `stage2_targeted_retrieval`
3. `stage25_patent_evidence_expansion`
4. `stage3_load_patent_evidence`
5. `stage4_synthesis_with_patent_evidence`
6. `_extract_patent_ids_from_results`

For patent mode, `stage25_patent_evidence_expansion` may be implemented as a no-op that preserves stage accounting and forwards retrieval payloads unchanged.

### 8.3 Naming decision

The implementation should prefer explicit patent naming internally rather than paper-domain names like `load_pdf_chunks`.

Allowed compatibility approach:

- keep the orchestrator structure
- add a patent-specialized orchestrator or adapter layer

Avoid:

- leaving patent code permanently behind paper-domain method names
- building the patent pipeline around semantic lies such as "patent tables are pdf chunks"

---

## 9. Conversation Context Contract

`patentQA` must inherit the `fastQA` approach of compressed context building, but the normalization boundary must be explicit.

Scope boundary for this section:

- conversation context is discussed here only because the QA pipeline consumes it
- the persistence system that produces raw context remains the existing `patentQA` implementation, not part of the imported reference QA flow

The context contract should include:

1. `recent_turns_for_llm`
2. `summary_for_llm`
3. `conversation_state`
4. `source_selection`

Context must be injected into:

1. stage 1 prompt
2. stage 4 synthesis prompt

### 9.1 Context normalization boundary

The raw durable context source remains `patent/server/services/chat_persistence.py`.

That service currently yields patent-side context primitives such as:

1. merged `chat_history`
2. durable `summary`
3. durable `conversation_state`
4. `snapshot`
5. `pending_overlay`
6. `pending_overlays`

The first migration increment must add one explicit normalization boundary between raw persistence context and staged prompt context.

Recommended ownership rule:

1. `ChatPersistenceService` remains the owner of raw authority snapshot loading, overlay merging, and durability semantics
2. a patent conversation-context builder, modeled on `fastQA/app/services/conversation_context_builder.py`, becomes the owner of converting raw context into:
   - `recent_turns_for_llm`
   - `summary_for_llm`
   - `conversation_state`
   - `source_selection`
3. the orchestrator/runtime consumes only the normalized context object, not raw snapshot payloads

### 9.2 Pending overlay rule

`pending_overlay` and `pending_overlays` are part of durable patent correctness and must not be silently dropped.

For the first increment:

1. overlay merging remains inside `ChatPersistenceService`
2. normalized `recent_turns_for_llm` should reflect the merged assistant content after overlay application
3. raw `pending_overlay` metadata does not need to be injected into prompts directly
4. stage prompts consume the already-merged conversation view, not overlay bookkeeping internals

### 9.3 Patent-specific context rule

- context should influence question disambiguation and continuity
- context must not override retrieved patent evidence

---

## 10. SSE And Caller-Facing Contract

### 10.1 First-increment stream contract

The stream path must emit stage-level progress comparable to `fastQA`, but preserve the current patent caller-facing event schema.

Recommended stage messages:

1. `阶段一：生成预回答与专利检索规划`
2. `阶段二：双库检索候选专利并定位证据片段`
3. `阶段二点五：跳过MD扩展（专利模式）`
4. `阶段三：聚合检索证据并补入同专利表格`
5. `阶段四：综合专利证据生成分析答案`

### 10.2 Event categories

For the first increment, the patent KB pipeline must emit the current patent event types:

1. `metadata`
2. `step`
3. `content`
4. `done`
5. `error`

Internal orchestrator stages may still use `fastQA`-style thinking/progress semantics, but they must be mapped onto external `step` events before leaving `patentQA`.

### 10.3 Event payload boundary

The first increment must preserve the current payload boundary:

1. `metadata` carries routing and mode-identification fields needed to start the stream
2. `step` carries human-readable stage progress via `title` and `message`
3. `content` carries answer text increments or the final assembled answer body, depending on implementation detail
4. `done` carries the final structured payload including timings and references
5. `error` carries current patent error fields and `trace_id`

### 10.4 Metadata and done contract

The `metadata` event must carry at least:

- `requested_mode`
- `actual_mode`
- `route`
- `query_mode`
- `source_scope`
- `trace_id`

The `done` payload must carry at least:

- `requested_mode`
- `actual_mode`
- `route`
- `query_mode`
- `source_scope`
- `trace_id`
- `timings`
- `references`
- `reference_objects`
- `reference_links`
- `original_links`

In the first increment, refs and timings should remain `done`-payload concepts rather than being pushed into `metadata`.

### 10.5 Frontend parity rule

The patent stream should be renderable by the same frontend event consumption path as `fastQA`, with only reference rendering differences, not a wholly separate streaming transport.

---

## 11. Reference And Original-View Contract

`patentQA` stage output must standardize on original-view-capable references.

Each selected patent should be able to produce:

1. a canonical patent reference object
2. a viewer URI
3. section-aware anchors

Reference objects must support:

- claim anchors
- description paragraph anchors
- table provenance

`viewer_uri` must remain gateway-facing:

- `/api/patent/original/{canonical_patent_id}?section=...`

The pipeline must not regress to DOI- or PDF-link-centric contracts.

---

## 12. Cache And Singleflight Design

To match `fastQA` operationally, `patentQA` should be stage-cached instead of only whole-query cached.

Patent-specific cache note:

- stage caching does not imply that every stage is vector-database-backed
- in `patentQA`, stage 2.5 is expected to be a no-op in default patent mode rather than an archive/table expansion cache boundary

### 12.1 Required stage caches

1. stage 1 cache
   - question + conversation summary shape -> `deep_answer` + `retrieval_plan`
2. stage 2 cache
   - retrieval plan -> patent retrieval results
3. stage 2.5 cache
   - optional skipped/no-op marker or pass-through payload identity in patent mode
4. stage 3 cache
   - retrieval results + selected patent ids + patent-side PDF mode -> evidence bundles

### 12.2 Required singleflight boundaries

Singleflight should protect:

1. stage 1 planning
2. stage 2 retrieval
3. stage 2.5 no-op/skip bookkeeping if the stage remains explicit in the orchestrator
4. stage 3 evidence loading

Stage 4 synthesis may remain uncached or weakly cached depending on product requirements, but it should not block the stage-based cache migration.

---

## 13. Migration Architecture

### 13.1 Preferred architecture

The preferred target architecture is:

- `patent/server_fastapi/routers/ask.py`
  - caller-facing normalization and existing patent shell guardrails
- `patent/server/services/chat_persistence.py`
  - existing patent persistence and transcript integration
- `patent/server/patent/kb_service.py` or equivalent
  - patent KB entry service mirroring `fastQA` KB service
- `patent/server/patent/orchestrators/generation.py` or equivalent adapter
  - stage sequencing
- `patent/server/patent/runtime.py`
  - patent staged runtime implementation
- `patent/server/patent/stages/*`
  - thin stage wrappers if needed

### 13.2 Transitional rule

During migration, the existing `PatentRetrievalService` can be reused as:

- part of stage 2 retrieval
- and part of stage 3 evidence assembly inputs

But it should not remain the sole top-level execution abstraction.

Transitional resource rule:

- do not introduce a new patent `md_expansion` vector database merely to mirror `fastQA`
- first use the existing patent resources already present in the repo:
  - abstract vector DB
  - chunk vector DB
  - archive assets including `*_tables.json`, claims, description snippets, and bibliography

### 13.3 De-emphasize current simplified path

The current simplified path should become a compatibility or fallback implementation, not the long-term main architecture.

### 13.4 Result integration boundary

For the first increment, `AskService` and `PatentResultBuilder` remain in place as the caller-facing assembly boundary.

This means the new staged orchestrator/runtime must still produce an `execution_result` object compatible with the current patent result-building flow used by:

1. `patent/server/services/ask_service.py`
2. `patent/server/patent/result_builder.py`

At minimum, the staged pipeline must be able to populate:

1. `answer_text`
2. `steps`
3. `timings`
4. `references`
5. `reference_objects`
6. `reference_links`
7. `original_links`
8. `metadata`
9. `used_files`
10. `file_selection`
11. `route`

The first increment should not replace `AskService` or `PatentResultBuilder` with a new external contract layer. It should make the new staged patent pipeline produce a compatible `execution_result`, then let the existing sync/SSE builder publish the result.

This is also the intended scope boundary:

- QA-core behavior is redesigned inside the staged runtime/orchestrator
- persistence, request lifecycle, and caller-facing publication remain on the current patent-side implementation path

---

## 14. Implementation Sequence

Recommended order:

1. freeze the preserved patent external contract for request parsing, sync payloads, and stream events
2. freeze the non-QA shell boundary so persistence and surrounding platform behavior remain patent-native
3. define the patent context-normalization boundary from durable raw context to staged prompt context
4. define the patent staged runtime contract
5. introduce patent-native intermediate evidence models
6. port orchestrator skeleton from `fastQA`
7. implement patent stage 1
8. redesign stage 2 into patent-level candidate recall plus in-patent evidence localization
9. model patent stage 2.5 as skipped/no-op for MD expansion while preserving the stage boundary in the orchestrator
10. implement stage 3 as retrieval-result aggregation plus table attachment, with optional PDF fallback controlled by patent-side config
11. redesign patent synthesis prompt and stage 4 result contract so it still yields a compatible `execution_result`
12. rewire sync/SSE ask path to the new orchestrator while keeping `AskService`, `PatentResultBuilder`, and current patent persistence behavior

---

## 15. Acceptance Criteria

The migration is not considered complete unless all of the following are true.

1. `patentQA` receives a KB patent question and executes through explicit stage 1/2/2.5/3/4 logic
2. stage 1 returns both `deep_answer` and a structured patent retrieval plan
3. stage 2 performs patent-level candidate recall plus in-patent evidence localization rather than one flat retrieval call
4. stage 2.5 is explicitly represented but defaults to skipped/no-op in patent mode rather than invoking an MD expert
5. stage 3 always injects same-patent table evidence when available and builds a patent-native evidence bundle rather than a fake PDF chunk bag
6. stage 4 synthesizes from stage 1 draft plus stage 3 patent evidence
7. sync and stream responses both carry patent-native references
8. sync and stream responses preserve the current patent caller-facing contract from `request_models.py` and `response_models.py`
9. frontend can consume the stream through the existing patent event schema without introducing a new transport or new outer envelope
10. persistence and durable patent mode remain on the current patent-side implementation path and are not redesigned by this QA spec
11. `AskService` and `PatentResultBuilder` remain able to publish the new staged result through a compatible `execution_result`
12. the implementation no longer depends on DOI/PDF semantics in its core patent path
13. the implementation does not require a patent-side `md_expansion` vector database for stage 2.5 correctness

---

## 16. Explicit Anti-Goals And Failure Modes

The migration must avoid these failure modes.

1. mechanically copying `fastQA` files while keeping paper-domain names and contracts intact
2. representing patent tables as an optional postprocessing step instead of core evidence
3. leaving stage 3 as a paper-specific `pdf_chunks` abstraction in the long term
4. keeping stage 4 prompts focused on literature synthesis instead of patent analysis
5. preserving DOI-centric reference normalization in the patent path
6. allowing direct-text match answers to bypass table supplementation when the selected patent has tables
7. changing external patent request or stream contracts implicitly while claiming only an internal pipeline migration
8. letting raw durable snapshot or overlay payloads leak directly into stage implementations without an explicit normalization boundary
9. introducing a synthetic patent `md_expansion` vector-store dependency without a separate spec and data-production plan
10. modeling patent retrieval as a paper-style claim-query fanout instead of patent recall plus intra-patent grounding
11. accidentally redesigning persistence or other non-QA shell behavior while only intending to adopt a QA-process reference flow
12. moving mandatory table attachment out of stage 3 while also skipping stage 2.5, which would leave the reference flow without a deterministic table-loading boundary

---

## 17. Open Decisions

These decisions should be resolved during implementation planning, not deferred indefinitely.

1. whether stage 2.5 and stage 3 should remain separate modules or be collapsed after patent evidence modeling is stabilized
2. whether patent stage 4 should use:
   - a dedicated prompt family
   - or a parameterized shared synthesis framework with domain-specific prompt templates

The implementation plan must choose one option for each of these and keep the scope narrow.

First-increment planning decision:

1. use patent-specific KB service/orchestrator files or a patent adapter layer in the first increment
2. do not generalize the existing KB orchestrator into a domain-agnostic base in this plan
3. defer cross-domain generalization to a later follow-up spec after patent staged parity is proven in production-like testing

---

## 18. Final Recommendation

Treat `fastQA` as the execution skeleton, not as the domain model.

That means:

1. preserve the staged orchestration pattern
2. redesign the runtime contract around patent evidence
3. make table supplementation a first-class stage requirement
4. keep caller-facing sync/SSE parity with `fastQA`
5. stop investing further in the current simplified direct retrieval path as the primary architecture

This is the minimum design that satisfies the user's requirement of "full fastQA-like process migrated into patentQA" without forcing patent semantics into paper-domain abstractions.
