# Patent Upstream Connection Reuse FastQA Alignment Spec

## Status

- Date: 2026-04-22
- Scope: spec only, no implementation in this document
- Goal: align `patent` upstream HTTP connection reuse, observability, and warm-connection strategy with the useful parts of `fastQA`, without mechanically copying `fastQA` runtime structure

## Goal

This spec defines how `patent` should absorb the upstream connection optimization work that already proved useful in `fastQA`.

The target is not "copy `fastQA` into `patent`". The target is:

- keep and strengthen the shared upstream client that `patent` already has
- expose enough runtime state to understand whether connection reuse is actually working
- reduce repeated handshake and cold-pool penalties on the short, bursty LLM paths inside `patent`
- preserve safety for long-lived streaming answer paths
- roll the work out in phases so the lowest-risk improvements land first

## Decision Summary

Recommended direction:

1. Treat `patent`'s existing shared `httpx.Client` as the foundation, not as something to replace
2. First upgrade the shared pool and the pool-consuming callers together so timeout depth and pool metrics are actually enforceable
3. Then consider a `patent`-specific hot-lane layer only if post-Phase-1 measurements show real cross-request planning contention inside a worker
4. Do not copy `fastQA`'s rerank hot-pool design, because `patent` does not have the same rerank HTTP dependency shape
5. Keep long-lived streaming generation on the shared pool unless later measurement proves a dedicated lane pool is justified

This is intentionally asymmetric with `fastQA`. The two services do not have the same stage structure, so parity should be achieved at the capability level, not at the file-for-file level.

## Hard Boundaries

The following constraints are mandatory:

- Do not modify any `fastQA` runtime code as part of this work
- Do not import runtime code from `fastQA` into `patent`
- Keep all runtime ownership inside `patent`
- Preserve current `patent` request semantics unless a phase explicitly changes behavior behind config gates
- Do not require a cross-worker shared pool design; each worker may continue to own its own in-process pools
- Do not redesign `patent` retrieval or answer orchestration as part of the first migration phase
- Do not bundle unrelated prompt changes or frontend changes into this work
- Any new pools, gates, or warm-up schedulers must be attached to `app.state`, closed on bootstrap failure, and closed during lifespan shutdown
- Any future long-generation specialization must preserve:
  - stage4 citation sanitization against allowed patent IDs
  - streamed answer behavior where it already exists
  - PDF route/source-scope prompt behavior
  - hybrid `file_over_kb` precedence semantics

## Current State

`patent` already has partial upstream connection reuse. This matters because the migration should build on what exists instead of redoing it.

### Existing Shared Upstream Client

Current provider:

- `patent/server/patent/upstream_http.py`

Current bootstrap path:

- `patent/server_fastapi/app.py`

Current injected consumers:

- `PatentPlanningClient` in `patent/server/patent/runtime.py`
- `PatentAnswerBuilder` in `patent/server/patent/answering.py`
- `PatentPdfAnswerClient` in `patent/server/patent/pdf_service.py`
- `PatentTabularAnswerClient` in `patent/server/patent/tabular_service.py`
- `PatentHybridSynthesisClient` in `patent/server/patent/hybrid_synthesis.py`
- `build_default_patent_runtime(..., http_client=shared_http_client)` in `patent/server/patent/runtime.py`

Practical meaning:

- within one worker, `patent` already reuses a shared `httpx.Client` across multiple LLM call sites
- this already gives `patent` a first-order keepalive benefit
- this reuse does not cross worker boundaries

### Current Config Surface

Current shared-pool config is shallow:

- `PATENT_LLM_HTTP_SHARED_POOL_ENABLED`
- `PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS`
- `PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS`
- `PATENT_LLM_HTTP_MAX_CONNECTIONS`

Config files:

- `patent/config.shared.env.example`

Current runtime ownership:

- `patent/server/patent/upstream_http.py`

Important note:

- these settings are currently read ad hoc in `PatentSharedUpstreamHttpProvider.from_env()`
- they are not modeled in `patent/config.py` `Settings` today

### Current Health Exposure

Current health route:

- `patent/server_fastapi/routers/health.py`

Current runtime bootstrap status:

- `patent/server_fastapi/app.py`

Today, health exposes runtime readiness and several service states, but it does not expose a first-class `shared_llm_pool` component with pool-specific counters and metadata.

### Current Missing Capabilities Relative To FastQA

Compared to the current `fastQA` runtime, `patent` is missing:

- deeper timeout controls for shared upstream HTTP
- pool timeout counters and last pool wait metrics
- a reusable snapshot/status object for the shared pool
- health exposure for shared-pool internals
- dedicated hot lanes for short bursty LLM traffic
- upstream gates that coordinate concurrency against hot-lane readiness
- warm-up scheduling, warm windows, and bootstrap warming

### Important Structural Difference From FastQA

`fastQA` has a distinct stage2 shape with both chat generation bursts and rerank bursts. `patent` does not match that shape.

`patent`'s likely short, bursty candidates are:

- stage1 planning
- stage2 query generation done through the planning client

Important operational nuance:

- stage1 is one planning call per request
- current stage2 query generation is serialized in a list comprehension before retrieval parallelism starts
- any hot-lane or gate work therefore has to be justified by measured cross-request contention, not by assuming `fastQA`'s stage2 parallel shape exists inside one request

`patent`'s likely long-lived or heavier paths are:

- stage4 final answer streaming
- PDF answer generation
- tabular answer generation
- hybrid synthesis

This difference is why `patent` should not blindly adopt both of `fastQA`'s hot-pool types.

## Problem Statement

The current `patent` implementation has the right base idea but remains too weak operationally.

### Problem 1: Shared Pool Config Is Too Narrow

Without explicit connect/read/write/pool timeout control:

- slow failures are harder to bound
- pool saturation is harder to interpret
- different upstream paths cannot share a coherent transport contract

### Problem 2: Shared Reuse Cannot Be Proven In Health

Today it is difficult to answer basic runtime questions from service state alone:

- is the client actually shared or are callers falling back to private clients
- which worker owns which pool
- how often pool exhaustion is happening
- whether keepalive reuse is likely working

### Problem 3: Patent Has No Hot Strategy For Its Short Bursts

Stage1 planning and stage2 query generation are good candidates for warm reusable lanes, but `patent` currently sends them through the general shared pool only.

This means:

- keepalive may help, but there is no guarantee that several ready-to-use warm connections exist at the moment of a planning burst
- cold handshakes can still appear when multiple requests contend for planning/query-generation traffic inside a worker

### Problem 4: Patent Has No Warm-Up Discipline

`patent` currently has no concept of:

- startup bootstrap warm
- periodic keepalive warming
- active warm windows
- degraded lane refresh

This keeps the service more dependent on incidental traffic to establish warm upstream state.

### Problem 5: Long-Running Streams And Short Bursts Share The Same Operational Story

Not every upstream path should be optimized the same way.

If a tiny hot-lane pool were naively reused for long-lived streaming generation, lanes could be occupied for too long and create self-inflicted contention.

Therefore the architecture needs an explicit distinction between:

- short burst traffic that benefits from hot lanes
- long-lived traffic that should remain on the broader shared pool first

## Target Behavior

After this work, `patent` should behave like this:

- every worker owns one explicitly configured shared upstream HTTP pool
- health exposes whether that pool is enabled, ready, shared, and experiencing pool pressure
- planning/query-generation traffic can optionally use a small pre-warmed lane pool
- long-lived generation paths still use the shared pool unless separately enabled
- startup triggers an immediate warm cycle when hot lanes are enabled
- periodic warming is configurable and can be time-windowed
- every new optimization is gated so operators can roll back by configuration

## Proposed Architecture

### A. Enhanced Shared Upstream HTTP Pool

Upgrade `patent/server/patent/upstream_http.py` from a minimal provider into a richer shared-pool runtime object.

Required capabilities:

- explicit config object instead of four loose fields
- support for:
  - connect timeout
  - read timeout
  - stream read timeout
  - write timeout
  - pool timeout
  - keepalive expiry
  - max connections
  - max keepalive connections
- attach lightweight pool metadata to the client instance
- expose a snapshot method with:
  - pool owner
  - client owner
  - shared client id
  - worker pid
  - bootstrap source
  - pool timeout count
  - last pool wait ms
  - max connection limits
  - keepalive expiry

Recommended config names:

- `PATENT_LLM_HTTP_CONNECT_TIMEOUT_SECONDS`
- `PATENT_LLM_HTTP_READ_TIMEOUT_SECONDS`
- `PATENT_LLM_HTTP_STREAM_READ_TIMEOUT_SECONDS`
- `PATENT_LLM_HTTP_WRITE_TIMEOUT_SECONDS`
- `PATENT_LLM_HTTP_POOL_TIMEOUT_SECONDS`
- `PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS`
- `PATENT_LLM_HTTP_MAX_CONNECTIONS`
- `PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS`

Config ownership decision for the upgrade:

- move the new `PATENT_LLM_HTTP_*` settings into `patent/config.py` `Settings`
- keep `upstream_http.py` as the runtime implementation owner
- stop splitting config ownership between ad hoc env reads and app-level health reporting

Critical implementation constraint:

- provider-only changes are not sufficient
- current callers in `runtime.py`, `answering.py`, `pdf_service.py`, `tabular_service.py`, and `hybrid_synthesis.py` pass scalar `timeout=` values on individual requests
- to make connect/read/write/pool/stream timeout depth real, those callers must be updated to consume the richer transport contract instead of flattening it back to one scalar timeout

This phase should be fully useful even if no hot-lane work is enabled later, but only if provider and callers are upgraded together.

### B. First-Class Shared Pool Health Exposure

Add a dedicated `shared_llm_pool` component to `patent` app bootstrap and health output.

Required fields:

- `enabled`
- `ready`
- `status`
- `detail`
- `error`
- `pool_owner`
- `client_owner`
- `shared_client_id`
- `pid`
- `bootstrap_source`
- `pool_timeout_count`
- `pool_wait_ms`
- `max_connections`
- `max_keepalive_connections`
- `keepalive_expiry_seconds`

This mirrors the operational value of `fastQA`'s health design without requiring the exact same module layout.

### C. Pool-Aware Upstream Client Standardization

The LLM clients in `patent` should converge on a consistent transport contract.

Relevant files:

- `patent/server/patent/runtime.py`
- `patent/server/patent/answering.py`
- `patent/server/patent/pdf_service.py`
- `patent/server/patent/tabular_service.py`
- `patent/server/patent/hybrid_synthesis.py`

Required behavior:

- all clients can continue to accept an injected shared client
- logs must clearly distinguish `shared` versus `private` ownership
- pool-aware metrics must be recorded where requests are actually dispatched, not only in the pool object
- fallback to private clients must remain possible when shared bootstrap fails

Practical meaning:

- if `patent` wants `pool_timeout_count` and `pool_wait_ms` semantics similar to `fastQA`, it needs a patent-local request wrapper or equivalent client-side instrumentation layer
- `upstream_http.py` alone cannot observe all request-level wait/timeout outcomes
- this client standardization phase is therefore mandatory for real observability, not a cosmetic cleanup

This phase is less about new behavior and more about making the transport story explicit and measurable.

### D. Patent-Specific Planning Hot Lane Pool

Introduce a dedicated hot-lane pool only if Phase 1 and Phase 2 measurements show real cross-request planning contention that the enhanced shared pool cannot absorb.

Candidate ownership:

- new module under `patent/server/patent/`
- bootstrap wiring in `patent/server_fastapi/app.py`
- runtime injection in `patent/server/patent/runtime.py`

Recommended scope:

- stage1 planning
- stage2 query generation through `PatentPlanningClient`

Why this scope:

- these calls are short
- they can still contend across concurrent requests inside one worker even though current stage2 query generation is serialized within a request
- they benefit from ready-to-use warmed connections
- they are less likely than streaming generation to monopolize tiny lane pools for long durations

Recommended non-scope for the first hot-lane rollout:

- `PatentAnswerBuilder`
- `PatentPdfAnswerClient`
- `PatentTabularAnswerClient`
- `PatentHybridSynthesisClient`

Those paths should continue using the enhanced shared pool first.

### E. Upstream Gate For Planning Lanes

If planning hot lanes are added, pair them with a gate.

Purpose:

- do not let planning bursts overwhelm a half-ready hot-lane pool
- cap concurrency against actual ready lane count
- make queueing explicit instead of accidental

This gate should be narrow:

- planning/query-generation only
- not a global gate across every upstream path

Default recommendation:

- do not schedule a gate in the initial implementation unless the hot-lane phase lands and measurements show that lane readiness, not general shared-pool limits, is the actual contention point

### F. Warm-Up Scheduler

If planning hot lanes are enabled, add:

- bootstrap warm on service start
- periodic keepalive warming
- active warm window support
- jitter and degraded-lane refresh behavior

Recommended defaults should follow the lessons already applied in `fastQA`, but with `patent`-specific config names.

### G. Explicit Non-Goal: No Rerank Hot Pool In The First Patent Migration

`fastQA` has a rerank session hot pool because it has a rerank dependency worth optimizing in parallel with stage2 chat.

`patent` should not add a matching component unless a concrete `patent` rerank-style dependency appears later.

Adding a pool only because `fastQA` has one would be architecture cosplay, not justified design.

## Proposed Runtime Shape

Current simplified shape:

```text
worker
  -> PatentSharedUpstreamHttpProvider
      -> shared httpx.Client
          -> PatentPlanningClient
          -> PatentAnswerBuilder
          -> PatentPdfAnswerClient
          -> PatentTabularAnswerClient
          -> PatentHybridSynthesisClient
```

Target phased shape:

```text
worker
  -> EnhancedPatentSharedUpstreamPool
      -> shared httpx.Client
      -> shared_llm_pool status snapshot
  -> optional PlanningHotLanePool
      -> lane 0 httpx.Client
      -> lane 1 httpx.Client
      -> ...
  -> optional PlanningUpstreamGate

request
  -> stage1/stage2 short planning calls
      -> planning hot lane if enabled
      -> else shared pool
  -> stage4/pdf/tabular/hybrid long generation
      -> shared pool
```

## Phase Plan

### Phase 1: Shared Pool Parity

Scope:

- richer pool config
- richer provider object
- richer config ownership in `patent/config.py` `Settings`
- shared pool health/status
- shared/private ownership visibility
- caller-side timeout contract updates so per-request scalar timeouts stop collapsing the new transport depth
- caller-side pool metric instrumentation so pool wait and timeout counters reflect real request behavior

Why first:

- lowest risk
- helps every worker immediately
- gives the observability needed to judge later phases

Rollback:

- disable shared pool via config and restart/redeploy workers
- keep existing private-client fallback path

### Phase 2: Pool-Aware Client Wiring

Scope:

- standardize injected transport behavior
- make planning/answer clients consistently report transport ownership
- add tests around bootstrap degradation and health output
- ensure request dispatch helpers preserve separate connect/read/write/pool/stream timeout intent instead of flattening back to a single scalar timeout
- ensure request-dispatch metrics are emitted from the actual caller path

Why second:

- stabilizes the transport abstraction before hot-lane specialization

Rollback:

- revert to the Phase 1 shared pool object without hot-lane usage, then restart/redeploy workers

### Phase 3: Planning Hot Lanes And Gate

Scope:

- optional hot-lane pool for `PatentPlanningClient`
- optional gate for planning/query-generation concurrency
- health output for hot-lane state if the hot-lane pool is enabled

Why third:

- now the service has enough instrumentation to justify and tune the lane count
- current `patent` stage2 query generation is serialized inside one request, so this phase should only exist if measured worker-level contention remains after Phase 1 and Phase 2

Rollback:

- disable planning hot pool and gate via config, then restart/redeploy workers
- planning falls back to the shared pool

### Phase 4: Warm-Up Policy

Scope:

- bootstrap warm
- scheduled keepalive warm
- active time window
- jitter and degraded-lane refresh

Why fourth:

- warm-up policy only makes sense once there is a dedicated thing to warm

Rollback:

- disable warm-up but keep hot-lane pool instantiated, then restart/redeploy workers
- or disable hot-lane pool entirely and restart/redeploy workers

### Phase 5: Optional Long-Generation Specialization

Scope:

- only if later evidence proves that stage4/pdf/tabular/hybrid still suffer repeated cold-start cost that the shared pool cannot absorb

Default recommendation:

- do not schedule this phase by default

Reason:

- long-lived streaming paths can occupy dedicated lanes for too long
- premature specialization risks making throughput worse instead of better

Mandatory non-regression boundaries for this phase:

- preserve stage4 citation sanitization and allowed-patent-id filtering
- preserve existing streamed answer behavior
- preserve PDF route/source-scope prompt semantics
- preserve hybrid `file_over_kb` precedence

## Config Strategy

Every new optimization must be independently toggleable.

Minimum gating model:

- shared upstream pool enable flag
- planning hot-lane pool enable flag
- planning upstream gate enable flag or limit=0 semantics
- planning warm-up enable flag
- warm interval and warm window config

This matters because rollout risk is not uniform across phases.

Operational note:

- `patent` reads these controls at bootstrap time
- config rollback is therefore a restart/redeploy operation, not a live in-memory flip

## Testing Strategy

Implementation should prove the following behaviors:

- shared pool config is parsed and clamped correctly
- shared pool snapshot and close semantics are correct
- app bootstrap exposes shared pool status in health
- shared bootstrap failure degrades cleanly to private clients
- planning hot-lane selection reuses lanes correctly
- planning gate respects ready lane count and cancellation
- warm-up scheduler updates status correctly
- disabling a phase by config cleanly falls back to the prior layer
- bootstrap failure closes any new pools/schedulers/gates without leaking threads or clients
- lifespan shutdown closes any new pools/schedulers/gates attached to `app.state`

All runtime verification commands should continue to use the `agent` conda environment, and if service-level tests or process inspection require escalation they should run with escalation rather than inside the sandbox.

## Risks

### Risk 1: Overfitting Patent To FastQA

If the implementation copies `fastQA` structures without respecting `patent`'s different stage shape, complexity will rise without equivalent gain.

Mitigation:

- keep rerank hot-pool work out of scope
- keep long-generation specialization out of the default rollout

### Risk 2: Hot Lanes Starved By Long Requests

If long-lived streams use tiny dedicated lanes, connection reuse can get worse.

Mitigation:

- planning lanes only in the first hot-lane rollout
- keep long-lived traffic on the shared pool first

### Risk 3: False Confidence Without Metrics

A shared client can exist while still not delivering the intended latency benefit.

Mitigation:

- make health and logs expose pool owner, client owner, pool wait, and timeout counters

### Risk 4: Too-Small Pool Defaults

If shared pool limits or lane counts are too low, bursty traffic will still pay handshake or wait costs.

Mitigation:

- expose all relevant limits by config
- roll out observability before aggressive tuning

### Risk 5: Worker-Level Isolation Misunderstood As A Bug

Even after this work, pools remain process-local.

Mitigation:

- document this explicitly in health and spec
- tune lane counts and connection limits per worker, not globally

## Open Questions

These are the only unresolved design questions worth measuring before Phase 3 or later:

- is `PatentPlanningClient` the only short-burst LLM path that materially benefits from hot lanes, or is there a second short-call candidate inside `patent`
- do `patent` file routes show enough repeated handshake cost on long-generation paths to justify any later specialization beyond the shared pool
- should embedding HTTP calls remain explicitly out of scope, or should a separate transport optimization effort cover them later

These questions should be answered by measurement after Phase 1 or Phase 2, not guessed upfront.
