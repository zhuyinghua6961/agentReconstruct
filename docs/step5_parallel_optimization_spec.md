# Step 5 Parallel Optimization Spec

## Goal

Optimize the final verification stage without reducing answer accuracy. The current `Step 5` in [agent_core/graph.py](/home/cqy/worktrees/highThinking/agent_core/graph.py) is a strict `check -> revise -> recheck` loop, so only part of it is safe to parallelize.

## Current Structure

`Step 5` now exposes two visible sub-steps:

1. `step5_check`: citation verification
2. `step5_revise`: targeted revision when issues are found

Loop shape:

1. Run `check_answer()` on the full draft
2. If passed, finish
3. If failed, run `revise_answer()` on the full answer
4. Repeat until pass or `MAX_CHECK_LOOPS`

## Hard Constraints

The following must stay serial:

- Verification loop order: round `n+1` depends on the revised answer from round `n`
- Full-answer revision: multiple revisers editing the same answer in parallel will create merge conflicts and consistency drift
- Final answer commit: only one checked answer should be promoted to `final_answer`

## Safe Parallelization Targets

### 1. Evidence Preparation

Before `check_answer()`, build verifier inputs in parallel:

- extract citations from draft
- group retrieved chunks by DOI / section
- deduplicate passages
- map `claim -> candidate evidence`

Expected gain:

- lower checker prompt size
- less repeated formatting work

### 2. Sub-Check Fan-Out

Split verification into smaller independent checks:

- by citation block
- by paragraph
- by claim cluster

Execution model:

1. a coordinator slices the answer
2. multiple checker workers validate slices in parallel
3. a reducer merges issues
4. one final checker can optionally do a whole-answer sanity pass

Guardrails:

- reducer must deduplicate issues
- every issue must carry stable fields: `claim`, `citation`, `problem`, `span`
- final pass stays single-threaded

### 3. Programmatic Pre-Checks

Run non-LLM validation in parallel before LLM checker:

- citation format validation
- DOI existence in retrieved evidence
- missing citation detection
- duplicate citation detection

This should filter obvious problems and reduce LLM load.

## Not Recommended

Avoid these unless accuracy requirements are relaxed:

- parallel `revise_answer()` per issue
- parallel full rounds of `check -> revise`
- racing multiple revisers and picking one output

These approaches will likely introduce answer inconsistency and citation regressions.

## Recommended Rollout

### Phase 1

- keep current serial loop
- add structured `step5_check` / `step5_revise` telemetry
- collect timing for:
  - evidence prep
  - checker call
  - reviser call

Status:

- Implemented in current repo for request-level Step 5 timing split:
  - `step5_check_total`
  - `step5_revise_total`
  - `step5_check_loop_<n>`
  - `step5_revise_loop_<n>`
  - `step5_issue_total`
  - `step5_revise_rounds`
- Live SSE now exposes `step5_check` and `step5_revise` progress events

### Phase 2

- add parallel programmatic pre-checks
- add evidence preparation cache for a single request

Status:

- Partially implemented:
  - conservative programmatic pre-check for cited DOI existence
  - if answer cites a DOI that does not exist in retrieved evidence, checker now returns an issue without spending an LLM call
- Not yet implemented:
  - parallel pre-check execution
  - evidence preparation cache reuse across checker rounds

### Phase 3

- add parallel sub-check workers
- keep one reducer and one final whole-answer sanity check

### Phase 4

- only if needed, experiment with section-scoped revision
- require strong regression tests before enabling by default

## Success Metrics

- reduce `step5_check_revise` latency by 25%-40%
- no drop in citation pass rate
- no increase in forced-output cases
- stable final-answer length and reference coverage

## Test Requirements

- golden cases where checker passes in one round
- cases with multiple citation issues
- deduplication tests for merged issues
- regression tests ensuring final answer remains deterministic enough for review
- end-to-end SSE checks confirming `step5_check` and `step5_revise` events remain ordered
