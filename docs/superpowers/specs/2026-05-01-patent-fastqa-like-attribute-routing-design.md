# Patent FastQA-Like Attribute Routing Design

**Goal:** Make patent-mode material attribute questions, such as "磷酸铁锂的电压是多少", follow the fastQA-style graph-for-RAG generation flow instead of returning a patent graph direct answer.

**Status:** Draft for review

---

## Background

The same user question currently routes differently across fastQA and patent mode:

- In fastQA, "磷酸铁锂的电压是多少" is classified as `graph_for_rag`, attaches graph evidence, then continues through stage1, stage2, stage25, stage3, and stage4 generation.
- In patent mode, the same question is classified as `direct_answer` with `matched_rule=list_patents_by_material`, executes a graph query, renders a direct patent list, and exits before the generation pipeline.

The patent answer is therefore structurally mismatched to the user intent. The user asked for a material property answer, not a list of patents involving the material.

## Evidence

Patent log for trace `task_3e9d8486276140cb8a74a9cff3dc5286`:

- `patent_graph.classify_done ... mode=direct_answer route_family=precise matched_rule=list_patents_by_material`
- `patent_graph.plan_done ... strategy=parametric intent=list_patents_by_material`
- `patent_graph.route_end ... final_mode=direct_answer template_id=list_patents_by_material`
- `patent kb_service run completed via graph preflight`

fastQA log for trace `task_e8d4989fe9d6467f86421c877c648a51`:

- `graph_kb_v2 classify_done mode=graph_for_rag ... matched_rule=graph_slot_signal ... direct_eligible=False`
- `fastqa graph kb v2 attached graph_for_rag evidence to generation request`
- generation stages run through stage1, stage2, stage25, stage3, and stage4.

Minimal local classifier reproduction:

- Patent slots: `material_terms=('磷酸铁锂',)`, `asks_count=True`, decision `mode='direct_answer'`, `matched_rule='list_patents_by_material'`.
- fastQA slots: `entities=('lifepo4', '磷酸铁锂')`, no legacy template, decision `mode='graph_for_rag'`, `direct_answer_eligible=False`.

## Root Cause

Patent graph classification uses material detection too broadly for direct answers:

- `patent/server/patent/graph_kb/query_templates.py` builds `list_patents_by_material` whenever `slots.material_terms` exists unless the question is recognized as why/how or trend/landscape.
- `list_patents_by_material` is marked `direct_answer_eligible=True`.
- `patent/server/patent/graph_kb/classifier_v2.py` sets `mode=direct_answer` when all candidates are direct-answer eligible.
- `patent/server/patent/graph_kb/slots.py` treats the substring `多少` as a count hint, so `是多少` is accidentally counted as `asks_count=True`.

This makes a material attribute question look like a precise patent-listing query.

## Desired Behavior

Patent mode should distinguish explicit patent graph lookup requests from open material attribute questions.

For "磷酸铁锂的电压是多少":

- The graph preflight may run and may retrieve material-related patent candidates.
- The final graph routing mode must be `graph_for_rag`, not `direct_answer`.
- `kb_service` must inject the graph payload into the generation conversation context when graph RAG injection is enabled.
- The request must continue into the patent staged generation pipeline.
- The user should receive a generated answer grounded by retrieved patent/vector evidence, not a direct list of patent IDs.

## Non-Goals

- Do not disable patent graph preflight globally.
- Do not remove `list_patents_by_material`.
- Do not change fastQA behavior.
- Do not introduce an LLM classifier just for this routing decision.
- Do not rewrite the patent generation pipeline.
- Do not change frontend rendering or API response shape.

## Design

### 1. Add an Attribute Question Signal in Patent Slots

Patent slots need a narrow signal for questions asking about material or performance attributes, not patent lists.

Examples that should be attribute/open-answer signals:

- `磷酸铁锂的电压是多少`
- `磷酸铁锂电压范围是多少`
- `磷酸铁锂容量是多少`
- `磷酸铁锂的倍率性能怎么样`
- `磷酸铁锂的压实密度是多少`

This signal should not be treated as a patent count request. In particular, `是多少` should not imply "how many patents".

The implementation can use a helper in `patent/server/patent/graph_kb/slots.py`, for example:

- Add property or metric hints such as `电压`, `容量`, `比容量`, `倍率`, `压实密度`, `振实密度`, `电导率`, `循环`, `能量密度`, `功率密度`, `性能`.
- Add question-form hints such as `是多少`, `范围是多少`, `怎么样`, `如何`, `表现`.
- Expose a boolean like `asks_attribute_value` or broaden `metric_terms` to include these terms.

The preferred shape is a dedicated boolean, because `metric_terms` currently means graph-specific patent performance terms and may be used by existing experiment-table logic.

### 2. Tighten Count Intent

`asks_count` should represent count/list cardinality intent, not every occurrence of `多少`.

Questions like these should still be count intent:

- `涉及磷酸铁锂的专利有多少`
- `宁德时代有多少专利`
- `A61K 有多少专利`
- `磷酸铁锂相关专利数量是多少`

Questions like these should not be count intent:

- `磷酸铁锂的电压是多少`
- `容量是多少`
- `压实密度是多少`

The implementation should prefer phrase-level count matching over raw substring matching. For example, count intent should require nearby object words such as `专利`, `件`, `项`, `数量`, or graph entity categories, rather than matching `多少` alone.

### 3. Downgrade Material Attribute Queries to Graph-for-RAG

When a question has a material anchor plus an attribute/open-answer signal, patent classifier v2 should classify it as `graph_for_rag`.

This downgrade applies only when the question is not explicitly asking for patent listing or patent counting. Intent precedence is:

1. Concrete patent ID facet questions keep existing single-patent direct-answer behavior.
2. Explicit patent listing/count requests keep direct-answer behavior.
3. Material attribute/open-answer questions use `graph_for_rag`.

For mixed-intent questions, explicit patent listing/count wins. For example, `涉及磷酸铁锂电压相关的专利有哪些` should still be a direct patent-listing answer because the user asked for patents, even though the phrase contains the attribute term `电压`.

Expected decision:

- `mode='graph_for_rag'`
- `route_family='hybrid'` or `route_family='precise'` is acceptable, but the chosen value must be consistent with existing patent graph metadata semantics.
- `matched_rule` should be explicit, such as `material_attribute_graph_anchor`, so logs make the decision auditable.
- Existing graph RAG payload construction should still produce `stage1_context_block`, `stage2_patent_candidates`, and `stage4_fact_block` when graph rows are found.

This mirrors fastQA behavior: graph evidence helps retrieval and planning, but the generation pipeline owns the answer.

### 4. Preserve Explicit Patent Listing Direct Answers

Explicit patent listing and counting queries should remain direct-answer eligible.

These examples should still be direct answers:

- `涉及磷酸铁锂的专利有哪些`
- `有哪些磷酸铁锂相关专利`
- `列出磷酸铁锂相关专利`
- `涉及磷酸铁锂的专利有多少`

This avoids regressing the useful graph direct-answer path for structured patent lookup.

### 5. Keep Single-Patent Facet Direct Answers

Questions anchored by a concrete patent ID should keep current behavior unless the existing classifier already chooses graph-for-RAG for compare or contextual cases.

Examples that should remain direct answers:

- `CNxxxx 的工艺步骤是什么`
- `CNxxxx 的材料角色有哪些`
- `CNxxxx 的实验数据是什么`

This spec only targets broad material attribute questions and accidental count/list routing.

## Proposed File Touches

- `patent/server/patent/graph_kb/slots.py`
  - Add a dedicated attribute/open-answer signal.
  - Tighten count intent extraction so `是多少` is not enough by itself.

- `patent/server/patent/graph_kb/classifier_v2.py`
  - Add an early branch that downgrades material attribute questions to `graph_for_rag`.
  - Add diagnostics with an explicit `matched_rule`.

- `patent/server/patent/graph_kb/query_templates.py`
  - Only change if needed to avoid building `list_patents_by_material` for attribute questions.
  - Prefer keeping candidate generation intact and making the mode decision in the classifier, because graph candidates are still useful as RAG hints.

- `patent/tests/test_patent_graph_kb_slots.py`
  - Add slot tests for `是多少` vs patent-count intent.

- `patent/tests/test_patent_graph_kb_classifier_v2.py`
  - Add classifier tests for material attribute graph-for-RAG and explicit patent listing direct-answer preservation.

- `patent/tests/test_patent_kb_service.py` or `patent/tests/test_patent_graph_kb_service_v2.py`
  - Add or adjust an integration-style test proving `graph_for_rag` payload is injected and direct answer is not returned for the representative question.

## Test Matrix

### Attribute Questions Should Use Graph-for-RAG

- `磷酸铁锂的电压是多少`
- `磷酸铁锂电压范围是多少`
- `磷酸铁锂的压实密度是多少`
- `磷酸铁锂容量是多少`

Expected:

- `classify_patent_graph_question_v2(...).mode == "graph_for_rag"`
- diagnostics `matched_rule` identifies a material attribute graph anchor.
- not `direct_answer`.

### Explicit Patent Material Lookup Should Stay Direct Answer

- `涉及磷酸铁锂的专利有哪些`
- `列出磷酸铁锂相关专利`
- `涉及磷酸铁锂的专利有多少`
- `涉及磷酸铁锂电压相关的专利有哪些`
- `磷酸铁锂电压相关专利数量是多少`

Expected:

- `mode == "direct_answer"`
- material list/count path remains selected.
- explicit listing/count intent wins even when an attribute term is present.

### Non-Material Semantic Questions Should Not Overmatch

- `电压是多少`
- `这个材料电压是多少`

Expected:

- Without a material, patent ID, organization, IPC, or other graph anchor, graph should be skipped or require context resolution according to existing follow-up behavior.
- It must not become `list_patents_by_material`.

### Existing Patent ID Facets Should Stay Direct Answer

- `CN100452491C 的工艺步骤是什么`
- `CN100452491C 的材料角色有哪些`

Expected:

- Existing direct-answer behavior remains intact.

## Logging Requirements

The existing logs are close to sufficient. The implementation should make the new branch observable through existing log fields:

- `patent_graph.classify_done ... mode=graph_for_rag ... matched_rule=material_attribute_graph_anchor`
- `patent_graph.route_end ... final_mode=graph_for_rag`
- `patent kb_service` should log graph RAG metadata as it already does for graph-for-RAG paths.

No new log stream or response field is required.

## Risks

- Over-downgrading explicit patent listing queries would reduce useful graph direct answers. Tests must protect listing/count examples.
- Under-detecting attribute terms would leave the original bug for common variants like `电压范围` or `容量是多少`.
- Tightening `asks_count` could affect applicant, inventor, agency, and IPC count queries. Tests should cover at least one existing count query from each important anchor type if touched broadly.
- Returning `graph_for_rag` with weak candidates is acceptable because generation retrieval still runs, but graph payload should not dominate vector retrieval scoring beyond existing behavior.

## Acceptance Criteria

- Patent classifier routes `磷酸铁锂的电压是多少` to `graph_for_rag`.
- Patent mode no longer returns a graph direct-answer patent list for that question.
- Patent mode still uses graph evidence as RAG context when available.
- Explicit material patent listing/count queries remain direct-answer capable.
- Relevant patent tests pass.
- No fastQA tests or behavior need to change.

## Open Decisions

- Whether `route_family` for material attribute questions should be `hybrid` or `precise`.
  - Recommendation: use `hybrid` because the graph result is evidence for generation, not a complete answer.
- Whether `电压` should be added to `metric_terms` or represented by a new slot.
  - Recommendation: add a dedicated attribute/open-answer boolean to avoid changing single-patent experiment-table routing semantics.
