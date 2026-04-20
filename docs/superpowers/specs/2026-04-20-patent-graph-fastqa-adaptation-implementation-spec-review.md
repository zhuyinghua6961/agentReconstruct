# Patent Graph FastQA Adaptation Implementation Spec Review

**Reviewed document:** `docs/superpowers/specs/2026-04-20-patent-graph-fastqa-adaptation-implementation-spec.md`

**Reviewer:** `gpt-5.4` high-reasoning subagent

**Final status:** Approved

## Round 1

**Status:** Needs revision

Key findings:

- missing stage-1 cache-key scope in `patent/server/patent/cache_keys.py`
- stage-4 prompt path incorrectly scoped only to `stages/synthesis.py`; needed `patent/server/patent/answering.py`
- no degraded-path story for `graph_for_rag` when stage-1 planning is unavailable or parse fails
- missing `IPC` subclass builder coverage
- `PatentGraphEvidenceBundle` omitted `diagnostics`

Disposition:

- main spec updated to include `cache_keys.py`, `answering.py`, graph-aware degraded planning fallback, IPC subclass support, and bundle diagnostics

## Round 2

**Status:** Needs revision

Key findings:

- stage-4 graph candidate IDs were still described too close to the citation whitelist
- payload-level diagnostics were still implicit in the context schema

Disposition:

- main spec updated so graph candidate IDs are non-citable grounding hints separate from retrieval-backed `allowed_patent_ids`
- `PatentGraphRagPayload.diagnostics` added explicitly

## Round 3

**Status:** Needs revision

Key finding:

- the non-citable stage-4 graph candidate field was described narratively but not yet defined in the payload model and injected context contract

Disposition:

- `stage4_graph_candidate_patent_ids` added to both `PatentGraphRagPayload` and the normalized `conversation_context["graph_kb"]` schema

## Round 4

**Status:** Approved

Conclusion:

- the spec is now complete, feasible against the current patent pipeline, consistent with existing code, and explicit about cache isolation, stage integration, diagnostics propagation, and citation safety
