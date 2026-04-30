# Patent FastQA-Style Stage2 B/C Design

**Date:** 2026-04-30

## Scope

This spec designs a staged upgrade for the `patent` service retrieval quality problem observed in the current vector-database QA path.

It covers two rollout levels:

- **B: FastQA-style Stage2 convergence**: add query guardrails, optional rerank, relevance validation, and stricter top-K output control around the existing patent Stage2 retrieval.
- **C: Patent-native Stage2 retrieval**: extend B with patent-level candidate aggregation, section-aware scoring, metric/table evidence scoring, and graph-hint integration.

It does not cover:

- frontend changes
- new external API routes
- replacing the existing patent archive or Chroma vector stores
- replacing Stage4 answer generation
- implementing arbitrary LLM-generated database queries

## Current Findings

The current patent request logs show Stage2 does call an LLM for query generation. For example:

- `resource/logs/dev/patent/patent-app.log:292-336`: `stage2_query_generation` calls `/chat/completions` for 4 retrieval claims.
- `resource/logs/dev/patent/patent-app.log:502-543`: Stage2 query generation succeeds for 5 retrieval claims.

The current patent Stage2 flow is:

1. Stage1 produces `retrieval_claims`.
2. Stage2 calls an LLM once per claim to generate a main query and expansions.
3. Each query searches abstract vectors first.
4. Abstract hits produce candidate patent IDs.
5. Chunk vector search runs inside those candidates, or across the graph candidate set when provided.
6. Matches are deduped and sorted mostly by chunk score, abstract score, and publication date.
7. Stage3 loads evidence for all resulting patent IDs.
8. Stage4 takes the first allowed patent IDs and synthesizes an answer.

Important implementation points:

- Stage2 query generation lives in [`patent/server/patent/stages/retrieval.py`](/home/cqy/worktrees/highThinking/patent/server/patent/stages/retrieval.py).
- Targeted retrieval lives in [`patent/server/patent/retrieval_service.py`](/home/cqy/worktrees/highThinking/patent/server/patent/retrieval_service.py).
- Chroma search currently filters only by `patent_id`, not by section or metric fields, in [`patent/server/patent/runtime.py`](/home/cqy/worktrees/highThinking/patent/server/patent/runtime.py).
- Stage4 defaults to a limited allowed patent set through `PATENT_STAGE4_REFERENCE_TOPK` in [`patent/server/patent/stages/synthesis.py`](/home/cqy/worktrees/highThinking/patent/server/patent/stages/synthesis.py).

The quality issue is that Stage2 expands recall but has weak convergence. Recent examples returned 78 and 83 references, which is too wide for precise answer synthesis.

## Goals

1. Keep the existing staged QA architecture intact.
2. Preserve the current LLM query-generation benefit.
3. Add fastQA-style convergence before Stage3:
   - query guardrails
   - entity locks
   - rerank
   - relevance validation
   - controlled top-K outputs
4. Add patent-native scoring in the C rollout:
   - patent-level aggregation
   - section-aware evidence scoring
   - metric and threshold coverage
   - table evidence boosts
   - graph candidate and constraint boosts
5. Make retrieval diagnostics explicit enough to debug why each patent entered or left the candidate set.

## Non-Goals

1. Do not make Stage2 depend on Stage4 answer generation.
2. Do not force every request through graph retrieval.
3. Do not make broad patent analysis collapse to only exact-ID lookup.
4. Do not use Stage1's `deep_answer` as retrieval truth.
5. Do not silently discard all results when validation is too strict; fall back to the best vector candidates with diagnostics.

## Design B: FastQA-Style Stage2 Convergence

### B1. Runtime Toggles

Add patent-specific toggles, parallel to fastQA but under `PATENT_` names:

- `PATENT_STAGE2_CONVERGENCE_ENABLED=false`
- `PATENT_STAGE2_FORCE_KEYWORD_INJECTION=true`
- `PATENT_STAGE2_ENTITY_LOCK_ENABLED=true`
- `PATENT_STAGE2_RERANK_ENABLED=true`
- `PATENT_STAGE2_RERANK_CANDIDATES=80`
- `PATENT_STAGE2_RERANK_TOP_PATENTS=20`
- `PATENT_STAGE2_MIN_RESULTS_PER_CLAIM=2`
- `PATENT_STAGE2_MAX_RESULTS_PER_CLAIM=5`
- `PATENT_STAGE2_MAX_GLOBAL_PATENTS=20`
- `PATENT_STAGE2_VALIDATION_ENABLED=true`

`PATENT_STAGE2_CONVERGENCE_ENABLED` is the rollout gate. When it is false, the Stage2 behavior and payload counts must remain compatible with the existing implementation except for passive diagnostics that do not affect ranking or filtering.

These toggles should be read near the patent Stage2 boundary, not inside Stage4.

### B1.1. Stage2 Runtime Signature And Cache Safety

All behavior-affecting Stage2 options must be included in the Stage2 runtime signature used by `build_stage2_cache_fingerprint`.

The signature must include at least:

- `PATENT_STAGE2_CONVERGENCE_ENABLED`
- all `PATENT_STAGE2_*` guardrail, rerank, validation, top-K, and C-channel toggles
- rerank provider, model, endpoint family, and rerank adapter version
- guardrail version
- validation version
- patent scoring version
- retrieval channel configuration for C, including direct global chunk search and table metric mode

This is required because the current orchestrator builds the Stage2 cache fingerprint from `runtime_retrieval_signature`. If the new toggles are not part of that signature, old cached Stage2 payloads can bypass the new convergence behavior.

### B2. Query Normalization And Guardrails

Add a patent query constraint layer after LLM query generation and before vector search.

Inputs:

- original user question
- current `PatentRetrievalClaim`
- LLM-generated query list
- graph hints from `conversation_context.graph_kb`

The guardrail must preserve:

- explicit patent IDs
- material names and aliases
- performance metrics
- numeric thresholds and units
- applicant or inventor names when present
- IPC/CPC codes
- user question anchors that the LLM omitted

Examples:

- If user asks for `LFP 放电容量超过 150 mAh/g`, a generated query that only says `cathode material high capacity` must be rewritten or prefixed with `LFP LiFePO4 放电容量 150 mAh/g`.
- If user asks about `tap density > 2 g/cm3`, query variants must preserve `tap density`, `压实密度`, `2 g/cm3`, and related unit variants.

Output metadata should include:

- `stage2_query_guardrail.injected_keywords`
- `stage2_query_guardrail.injected_entities`
- `stage2_query_guardrail.injected_metrics`
- `stage2_query_guardrail.injected_thresholds`
- `stage2_query_guardrail.original_query`
- `stage2_query_guardrail.final_query`

### B3. Wider Candidate Recall

For B, keep the current abstract-first plus chunk search design, but deliberately widen candidate collection before convergence:

- abstract vector top-K per query: current 8 can remain initially, but should be configurable.
- chunk vector top-K per query: current 8 can remain initially, but rerank candidates should collect from all query outputs.
- preserve exact-ID and graph-candidate filtering behavior for B.

Do not send all raw candidates into Stage3. Raw candidates are only the pre-rerank pool.

Graph behavior in B:

- If `conversation_context.graph_kb.stage2_patent_candidates` is present, keep the current hard-filter semantics: vector hits outside that patent set are filtered out.
- If the graph candidate hard filter produces no vector hits, keep the current fallback behavior and mark `metadata.graph_stage2_behavior="fallback_no_vector_hits"`.
- Raw graph candidates and graph constraints must be preserved in metadata for diagnostics.
- B must not reinterpret graph candidates as soft boosts; that behavior is reserved for C.

### B4. Rerank

Add a patent rerank adapter analogous to fastQA's rerank service.

Rerank query:

```text
用户问题: {question}
检索主张: {claim}
必须保留实体: {must_entities}
性能/阈值: {metrics_and_thresholds}
优先证据区段: {preferred_sections}
```

Rerank documents should include enough context:

- title
- publication number
- section label
- snippet text
- table summary when available

Rerank should happen at two levels:

1. **Per-claim rerank**: keep the best snippets/patents for each retrieval claim.
2. **Global rerank or merge**: dedupe by patent ID and keep a controlled patent set.

Fallback behavior:

- If rerank provider is unavailable, use vector order and record `fallback_reason`.
- Do not fail the request just because rerank fails.

### B5. Patent Relevance Validation

Add validation after rerank. This is the patent equivalent of fastQA's `validate_retrieval_relevance`.

Validation score should combine:

- vector/rerank score
- original question keyword coverage
- claim keyword coverage
- entity coverage
- metric coverage
- threshold/unit coverage
- section match boost

Suggested first-pass scoring:

```text
score =
  0.35 * rerank_or_vector_score
  0.20 * entity_coverage
  0.20 * metric_threshold_coverage
  0.15 * claim_keyword_coverage
  0.10 * preferred_section_boost
```

Validation rules:

- Exact patent ID matches should bypass most filtering but still keep diagnostics.
- If user asks for a metric threshold, results with no metric/unit evidence should be downranked.
- If user asks about claims or FTO, claim-section hits should be boosted.
- If user asks about embodiments, examples, or performance, description/table hits should be boosted.
- If validation would leave fewer than `PATENT_STAGE2_MIN_RESULTS_PER_CLAIM`, retain the best fallback candidates and mark `validation_fallback=true`.

No-vector compatibility rules:

- Rerank and validation must not require vector scores. Missing scores should be represented as `None` and converted to a neutral fallback score only inside the scoring helper.
- Exact-ID and default archive matches must not be filtered out solely because they lack vector distances or rerank scores.
- Lexical/no-vector fallback matches should preserve the existing order unless validation has enough text evidence to rank them deterministically.
- If snippet text, distance, or section metadata is missing, validation must keep the candidate when it is needed to satisfy minimum result counts and mark the candidate with `stage2_validation.missing_signal=true`.
- No-vector fallback must still return a Stage2 payload that Stage3 can consume: `documents`, `metadatas`, `distances`, `references`, `reference_objects`, `reference_links`, `original_links`, and `source_ids` must stay structurally valid.

### B6. Stage2 Output Contract

The B rollout should keep the existing public payload shape while adding metadata:

- `documents`
- `metadatas`
- `distances`
- `references`
- `reference_objects`
- `reference_links`
- `original_links`
- `source_ids`
- `metadata`
- `cache_hit`
- `negative_cache_hit`
- `not_found`
- `timings`

Contract invariants:

- Final Stage2 payload fields that carry selected patent evidence must contain only the selected patent set after rerank/validation/top-K contraction.
- The selected patent order is the final patent ranking order. `source_ids`, `references`, `reference_objects`, `reference_links`, and `original_links` must follow that order where those structures are patent-level.
- `documents`, `metadatas`, and `distances` must be synchronously sliced and sorted according to the selected snippets. Their indices must remain aligned.
- `reference_objects`, `reference_links`, and `original_links` must not contain patents absent from `source_ids`.
- Raw pre-rerank candidates may be summarized only in `metadata` diagnostics. They must not leak into `documents`, `references`, `reference_objects`, links, or `source_ids`, because Stage3 consumes those fields as selected evidence.
- If convergence is disabled, the payload must remain compatible with the current shape and ordering.

Add metadata:

- `retrieval_plan_queries`
- `stage2_raw_candidate_count`
- `stage2_reranked_candidate_count`
- `stage2_validated_candidate_count`
- `stage2_per_claim`
- `stage2_rerank`
- `stage2_validation`
- `stage2_filtered_out_sample`
- `stage2_selected_patent_ids`
- `stage2_payload_contract_version`

Default output limits:

- top 3-5 snippets per claim
- top 20 global patent IDs
- top 40-60 global evidence snippets

This should reduce the observed 78/83 references down to a controlled high-confidence set before Stage3.

## Design C: Patent-Native Stage2 Retrieval

Design C builds on B and changes the scoring unit from "retrieved snippet" to "patent candidate with evidence".

### C1. Structured Retrieval Intent

Stage1 should keep `retrieval_claims`. C1 first derives a patent retrieval intent object inside Stage2 from the existing question, claims, keywords, preferred sections, filters, and graph hints. This first version must not change the Stage1 output contract or Stage1 cache fingerprint.

```python
PatentRetrievalIntent(
    question_type: str,
    must_patent_ids: list[str],
    must_entities: list[str],
    materials: list[str],
    metrics: list[str],
    thresholds: list[dict[str, str]],
    ipc_codes: list[str],
    applicants: list[str],
    inventors: list[str],
    preferred_sections: list[str],
    negative_terms: list[str],
)
```

Later, if the project chooses to make Stage1 output `PatentRetrievalIntent` directly, that must be a separate contract change with:

- Stage1 payload backward compatibility.
- Stage1 cache fingerprint updates.
- Stage2 support for both explicit Stage1 intent and locally derived intent.
- Tests for old cached Stage1 payloads that do not contain the new field.

### C2. Multi-Channel Recall

Replace the single abstract-first behavior with explicit channels:

1. `exact_id`: direct patent ID lookup.
2. `graph_candidate`: graph-provided candidate patents.
3. `metadata`: title, abstract, applicant, inventor, IPC/CPC lexical match.
4. `abstract_vector`: current abstract vector search.
5. `chunk_vector_candidate`: chunk search inside candidate patent IDs.
6. `chunk_vector_global`: direct chunk search without abstract candidate gating.
7. `table_metric_boost`: table and measurement evidence scoring for metric-heavy questions.

The C initial rollout treats table evidence as a candidate scoring boost, not as an independent global recall channel. There is currently no documented global table index in the patent service. A true `table_metric` recall channel would require either a table index or a bounded archive scan strategy and should be designed separately.

Each channel should return a normalized candidate object:

```python
PatentStage2CandidateHit(
    patent_id: str,
    document: str,
    section_type: str,
    section_label: str,
    score: float | None,
    channel: str,
    query: str,
    metadata: dict[str, Any],
)
```

### C3. Patent-Level Aggregation

Aggregate all hits into:

```python
PatentCandidateScore(
    patent_id: str,
    title: str,
    best_score: float,
    channel_scores: dict[str, float],
    entity_coverage: float,
    metric_coverage: float,
    threshold_coverage: float,
    section_coverage: dict[str, int],
    graph_boost: float,
    evidence_hits: list[PatentStage2CandidateHit],
    reasons: list[str],
)
```

Suggested scoring:

```text
patent_score =
  0.30 * best_vector_or_rerank_score
  0.20 * entity_coverage
  0.20 * metric_threshold_coverage
  0.10 * section_fit
  0.10 * channel_diversity
  0.10 * graph_or_exact_boost
```

Rationale:

- A patent with one good abstract hit but no metric evidence should not outrank a patent with matched embodiment/table evidence for performance questions.
- Exact-ID or graph-candidate paths should boost, not blindly override, unless the user explicitly asked for those IDs.

### C4. Section-Aware Evidence Selection

After patent-level ranking, choose evidence snippets by section strategy:

- claim / FTO questions: claim snippets first, then description.
- performance questions: table and embodiment snippets first, then abstract.
- comparison questions: keep balanced evidence across compared patents.
- broad analysis: mix abstract, claim, and description.

Evidence selection should enforce:

- no more than 3-5 snippets per patent by default
- at least one high-signal snippet per selected patent when available
- preserve `generated_query`, `claim_text`, channel, and score metadata

### C5. Table And Metric Evidence

For metric-heavy questions, C must use the existing archive/table loaders more directly.

Examples of metric-heavy triggers:

- `容量`, `mAh/g`
- `压实密度`, `tap density`, `g/cm3`
- `倍率`, `C-rate`
- `循环`, `retention`
- `温度`, `SOC`

When triggered:

- load table supplements for the preselected candidate patent pool after vector/metadata/graph recall
- compute metric/threshold coverage against table rows
- boost patent candidates with direct table evidence
- include compact table snippets in Stage3/Stage4 evidence

This is a scoring and evidence-selection boost in the initial C design. It does not introduce global table scan as a recall mechanism. If no table supplement exists for a candidate, the candidate can still rank through claim, description, abstract, exact-ID, graph, or vector evidence, but it should not receive the table boost.

### C6. Graph Hints As Strong Signals

The existing graph RAG payload already exposes:

- `stage2_patent_candidates`
- `stage2_constraints`
- `stage2_entity_hints`
- `stage4_fact_block`

C should use these as scoring and filtering inputs:

- explicit graph candidate patents become candidate pool seeds and optional bounded boosts.
- graph constraints become validation checks where possible.
- entity hints become query guardrail terms.
- graph facts become evidence boost context, not final answer truth.

This is an intentional behavior change from B. B keeps current hard-filter semantics for graph candidates. C changes graph candidates from hard filters to seeds/boosts unless the user explicitly asks for a fixed patent set.

C must use only existing graph payload fields unless a separate graph payload contract change is approved:

- `stage2_patent_candidates`
- `stage2_constraints`
- `stage2_entity_hints`
- `stage4_fact_block`
- `diagnostics`

Because the current graph payload does not expose a normalized confidence score, C must not depend on a new `confidence` field. Instead:

- if `stage2_patent_candidates` is empty, graph contributes entity hints and facts only.
- if `stage2_patent_candidates` is non-empty but vector/metadata evidence disagrees, graph candidates receive a bounded boost but do not automatically replace non-graph candidates.
- if the question contains explicit patent IDs, those exact IDs remain hard constraints.
- if `diagnostics.stage2_behavior` or route metadata says the graph path downgraded, graph candidates should be treated as hints, not filters.

## Architecture

### New Or Updated Components

Recommended B components:

- `patent/server/patent/stages/retrieval.py`
  - add query guardrail helpers
  - add Stage2 runtime toggle resolution
- `patent/server/patent/retrieval_service.py`
  - add rerank hook
  - add relevance validation
  - add per-claim and global top-K control
- `patent/server/patent/runtime.py`
  - wire rerank client or adapter if configured
- `patent/tests/test_patent_retrieval_service.py`
  - cover guardrail, rerank fallback, validation, top-K contraction

Recommended C components:

- `patent/server/patent/retrieval_intent.py`
  - parse and normalize patent retrieval intent
- `patent/server/patent/retrieval_scoring.py`
  - patent-level aggregation and scoring
- `patent/server/patent/retrieval_validation.py`
  - patent-native validation helpers
- `patent/server/patent/retrieval_models.py`
  - add candidate hit and score dataclasses if appropriate

Existing components to preserve:

- `PatentGenerationOrchestrator`
- `PatentRuntime`
- `PatentRetrievalService.targeted_retrieve`
- `PatentStage2RetrievalResult` public shape
- Stage3 and Stage4 contracts

## Rollout Plan

### Phase B1: Diagnostics And Guardrails

Deliver:

- query guardrail helpers
- metadata diagnostics
- no behavior change unless toggles are enabled

Verification:

- tests for entity/metric/threshold injection
- log assertions or metadata assertions for final query visibility

### Phase B2: Rerank Integration

Deliver:

- rerank adapter with graceful fallback
- per-claim rerank top-K
- global top-K patent control

Verification:

- rerank disabled path
- rerank fallback path
- rerank success path with deterministic fake reranker

### Phase B3: Relevance Validation

Deliver:

- patent validation score
- filtered-out diagnostics
- fallback when too few candidates survive

Verification:

- metric-heavy query filters generic snippets
- exact patent ID does not get incorrectly filtered
- top-K contraction reduces source IDs

### Phase C1: Patent Intent And Multi-Channel Recall

Deliver:

- `PatentRetrievalIntent`
- direct chunk global search path
- table/metric trigger detection
- graph hint normalization

Verification:

- direct chunk search can find evidence even when abstract recall misses
- table-heavy questions trigger table evidence collection

### Phase C2: Patent-Level Aggregation

Deliver:

- patent-level candidate scoring
- reason strings for selected patents
- section-aware evidence selection

Verification:

- one patent with multiple weak generic hits does not outrank a patent with metric evidence
- graph candidate boost is bounded
- Stage3 receives a smaller but higher-confidence `source_ids` list

## Logging And Observability

Add Stage2 logs with trace IDs:

- `patent stage2 query guardrail applied`
- `patent stage2 raw recall completed`
- `patent stage2 rerank completed`
- `patent stage2 validation completed`
- `patent stage2 patent aggregation completed`
- `patent stage2 topk selected`

Each request should expose:

- claim count
- raw candidate count
- reranked count
- validated count
- final source ID count
- selected source ID sample
- filtered-out sample with reasons
- fallback flags

The target operational signal is that a normal broad patent request should not silently pass 70-80 source IDs into Stage3 unless explicitly configured.

## Test Strategy

Unit tests:

- query guardrail entity injection
- metric and threshold extraction
- validation scoring
- rerank fallback
- patent-level aggregation
- graph hint integration
- table-heavy trigger detection

Integration tests:

- Stage2 returns a smaller source set than raw recall.
- Stage3 still accepts the new Stage2 payload shape.
- Stage4 allowed patent IDs remain aligned with Stage2 selected patents.
- Cache fingerprints include the new Stage2 runtime signature, toggles, provider versions, guardrail version, validation version, and scoring version.

Regression tests:

- exact patent ID lookup still works.
- no-vector fallback still works.
- graph-for-RAG context still works.
- rerank unavailable does not fail the request.

## Acceptance Criteria

1. With `PATENT_STAGE2_CONVERGENCE_ENABLED=false`, existing Stage2 result counts and ordering remain unchanged for representative tests, excluding passive diagnostics.
2. With B enabled, `len(source_ids) <= PATENT_STAGE2_MAX_GLOBAL_PATENTS` unless exact user-provided patent IDs exceed that limit and are explicitly allowed by the implementation.
3. With B enabled, `references`, `reference_objects`, `reference_links`, `original_links`, and `source_ids` contain only selected patents and stay aligned with final patent ranking.
4. With B enabled, `documents`, `metadatas`, and `distances` have equal lengths and refer only to selected patents.
5. For metric-heavy questions, final guarded queries preserve metric and threshold terms, and metadata records injected metric/threshold terms.
6. If rerank raises or is unavailable, the request succeeds and `metadata.stage2_rerank.fallback_reason` is non-empty.
7. If validation is too strict, the request succeeds with `metadata.stage2_validation.validation_fallback=true` and at least `PATENT_STAGE2_MIN_RESULTS_PER_CLAIM` per surviving claim when raw candidates exist.
8. In no-vector fallback, exact-ID/default matches remain consumable by Stage3 and no validation/rerank logic returns an empty payload solely because vector scores are missing.
9. Under B graph-for-RAG context, graph candidates remain hard filters and fallback metadata matches current behavior when no vector hits survive.
10. Under C graph-for-RAG context, graph candidates act as seeds/boosts unless explicit patent IDs make them hard constraints.
11. Stage3 receives only selected source IDs, not the full raw recall pool.
12. Stage4 `allowed_patent_ids` reflects the selected patent ranking.

## Open Questions

1. Which rerank provider should patent use first: reuse fastQA's DashScope rerank endpoint, or configure a separate patent rerank provider?
2. Should `PATENT_STAGE2_MAX_GLOBAL_PATENTS` default to 20 or a lower value such as 12?
3. Are table supplements complete enough to make table evidence a hard requirement for metric-heavy questions, or should it remain a boost only?
4. Should Stage1 output `PatentRetrievalIntent` directly, or should the first version derive it deterministically from claims and question text?
