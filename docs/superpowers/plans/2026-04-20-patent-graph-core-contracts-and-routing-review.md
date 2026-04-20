# Patent Graph Core Contracts And Routing Review

**Reviewed document:** `docs/superpowers/plans/2026-04-20-patent-graph-core-contracts-and-routing.md`

**Reviewer:** `gpt-5.4` high-reasoning subagent

**Final status:** Approved

## Round 1

**Status:** Needs revision

Key findings:

- ownership boundary contradicted itself by claiming not to own planner/executor/renderer pieces while requiring the router task to wire them directly
- stage-1 cache-key work from `cache_keys.py` was missing from the core doc
- task text assumed a nonexistent `settings.graph_kb.v2_enabled` access path inside `PatentKbService`
- payload/context contract details and diagnostics propagation were too implicit
- classifier matrix missed IPC-subclass coverage

Disposition:

- doc updated with explicit prerequisites, primitive-flag wiring, explicit payload contract fields, diagnostics propagation, and IPC-subclass coverage

## Round 2

**Status:** Needs revision

Key findings:

- stage-1 cache-key ownership was still only treated as a prerequisite instead of an owned contract item
- graph metadata propagation was only defined for downgrade paths, not successful `graph_for_rag`
- diagnostics fields on routing/query-plan/evidence contracts still needed explicit mention

Disposition:

- doc updated to own `cache_keys.py` and `test_patent_graph_kb_stage1_cache_keys.py`
- success-path graph metadata added
- diagnostics fields made explicit across the owned model boundary

## Round 3

**Status:** Approved

Conclusion:

- the plan is now coherent for its slice and aligned with both the approved main spec and the actual `PatentKbService` / `PatentExecutor` wiring
