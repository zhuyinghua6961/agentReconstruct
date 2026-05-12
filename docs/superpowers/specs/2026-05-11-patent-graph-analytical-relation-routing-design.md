# Patent Graph Analytical Relation Routing Design

**Goal:** Prevent patent-mode open analytical questions such as `磷酸铁锂磨砂粒径与产品性能间的关系` from being treated as direct patent graph lookups.

**Status:** Draft for review

---

## Background

Patent graph V2 currently runs as a preflight inside `kb_qa`:

```text
PatentKbService.run
  -> graph preflight
  -> direct_answer OR graph_for_rag OR skip_graph
  -> staged patent RAG only when graph does not direct-answer
```

This is correct for structured graph questions, but it overmatches broad material questions. The representative user question is:

```text
磷酸铁锂磨砂粒径与产品性能间的关系
```

The user is asking for a technical relationship between a particle-size/process parameter and product performance. They are not asking for patents that mention `磷酸铁锂`, nor for a structured graph list/count answer.

## Evidence

Local classifier reproduction for the representative question:

```text
slots.material_terms = ('磷酸铁锂',)
asks_attribute_value = False
asks_why_how = False
asks_trend_landscape = False
mode = direct_answer
matched_rule = list_patents_by_material
candidate_path_ids = ('list_patents_by_material',)
```

Relevant current behavior:

- `patent/server/patent/graph_kb/slots.py` recognizes `磷酸铁锂` as a material term, but does not recognize `粒径`, `产品性能`, or `关系` as an analytical relation signal.
- `patent/server/patent/graph_kb/query_templates.py` builds `list_patents_by_material` whenever `slots.material_terms` is present and the question is not classified as why/how or trend/landscape.
- `list_patents_by_material` is direct-answer eligible.
- `patent/server/patent/graph_kb/classifier_v2.py` falls through to the generic candidate branch and returns `mode="direct_answer"` when all candidates are direct-answer eligible.
- `patent/server/patent/kb_service.py` short-circuits staged generation when graph routing returns a handled direct answer.

No matching runtime log for this exact query was found in the local log directories during investigation. The current local services were stale, so the evidence above is from deterministic local classifier and planner execution.

## Root Cause

The graph preflight treats a material mention as enough evidence for a direct patent-listing graph query.

This collapses two different intents:

- explicit structured graph lookup: `涉及磷酸铁锂的专利有哪些`
- open analytical relationship question: `磷酸铁锂磨砂粒径与产品性能间的关系`

The current classifier lacks a negative or downgrade rule for parameter-performance relationship questions, and the template builder offers `list_patents_by_material` too eagerly for material-only anchors.

## Desired Behavior

Analytical material/process/parameter-performance relationship questions should not return a patent graph direct answer.

For the representative question:

```text
磷酸铁锂磨砂粒径与产品性能间的关系
```

Expected graph routing:

```text
mode = skip_graph
matched_rule = analytical_relation_question
```

The request should continue through the normal patent staged generation/RAG path. The answer should be generated from patent/vector evidence and synthesis, not from a graph-rendered patent list.

`candidate_path_ids` may be empty or may still show suppressed diagnostic candidates such as `list_patents_by_material`, depending on whether implementation suppresses template generation or only changes classifier precedence. It must not cause graph execution or a direct graph answer for this question.

This spec intentionally chooses `skip_graph`, not `graph_for_rag`, for this class of questions. The current patent graph has material and performance facts, but it does not yet encode a reliable direct relationship between `粒径` and `产品性能`. Injecting weak graph candidates would risk biasing retrieval toward patents that merely mention the material.

## Non-Goals

- Do not disable patent graph preflight globally.
- Do not remove `list_patents_by_material`.
- Do not change fastQA behavior.
- Do not add an LLM classifier for this routing decision.
- Do not change gateway routing.
- Do not change file routes.
- Do not implement new graph Cypher for parameter-performance causality.
- Do not make `关系` always skip graph when the question has a concrete graph anchor such as patent ID, IPC, applicant, inventor, or agency.
- Do not change the previous material attribute behavior for questions already covered by `material_attribute_graph_anchor`, such as `磷酸铁锂的电压是多少`, unless later product direction explicitly asks for that.

## Design

### 1. Add Analytical Relation Signals to Slot Extraction

Patent slot extraction should expose whether a question is an open relationship or mechanism-analysis question.

Add narrow keyword groups in `patent/server/patent/graph_kb/slots.py`:

- relation terms: `关系`, `相关性`, `关联`, `影响`, `作用`, `机制`
- parameter terms: `粒径`, `颗粒尺寸`, `D50`, `D90`, `磨砂粒径`, `比表面积`, `一次粒径`, `二次粒径`
- performance terms: `产品性能`, `电化学性能`, `倍率性能`, `循环性能`, `容量保持率`, `放电容量`, `压实密度`, `振实密度`

Expose `asks_analytical_relation` as the required routing boolean in slot diagnostics:

```text
asks_analytical_relation = True
```

Recommended predicate:

```text
has relation term
AND has at least one of material/process/parameter/performance anchor
AND does not have explicit patent list/count intent
```

This keeps the signal narrow. The word `关系` alone should not be enough to force graph skipping if the user asks a concrete graph question.

### 2. Make Explicit Patent Lookup Intent Win

Keep direct graph answers for explicit list/count questions:

- `涉及磷酸铁锂的专利有哪些`
- `列出磷酸铁锂相关专利`
- `磷酸铁锂相关专利数量是多少`
- `H01M10 下有哪些专利`
- `宁德时代有哪些磷酸铁锂相关专利`

The classifier should continue to treat these as structured graph questions because the user asked for patent objects.

The existing helper `_is_explicit_patent_listing_or_count(...)` should be reused or tightened so it requires patent-object language such as `专利`, `申请`, `公开`, `授权`, `件`, or `项` together with list/count intent.

Questions with applicant or IPC anchors but no explicit patent list/count wording should not be converted into direct graph answers just because the anchor is present. For example, `宁德时代磷酸铁锂粒径与性能关系` and `H01M10 磷酸铁锂粒径与性能关系` are analytical relation questions, not applicant/IPC patent-list requests. They should route to `skip_graph` in this fix unless product direction later asks for graph-assisted landscape analysis.

### 3. Add a Skip Branch Before Why/How and Generic Candidate Routing

In `patent/server/patent/graph_kb/classifier_v2.py`, add a branch after multi-patent compare handling and before the existing broad why/how `graph_for_rag` branch and the generic `elif candidates:` fallback:

```text
if analytical relation signal
AND no patent ID
AND not explicit patent listing/count
AND not applicant/inventor/agency/IPC list/count intent:
    mode = skip_graph
    route_family = semantic
    matched_rule = analytical_relation_question
```

This branch must run before both of these current routes:

- the why/how branch that turns `影响` and `机制` into `graph_for_rag`;
- the generic candidate fallback that converts material-only candidates into `direct_answer`.

The branch may be placed before single-patent handling only if its predicate explicitly excludes `slots.patent_ids`. The behavior requirement is that a question such as `CN100355122C 的粒径与性能关系是什么` must not be skipped solely because it contains `关系`, `粒径`, or `性能`.

Recommended classifier precedence:

```text
1. empty / DOI / no-signal skip rules
2. multi-patent compare graph_for_rag
3. analytical relation skip for non-patent-ID, non-explicit-list/count questions
4. community/applicant landscape graph_for_rag
5. material attribute graph_for_rag
6. why/how graph_for_rag for remaining anchored questions
7. single-patent direct_answer
8. applicant/inventor/agency/IPC direct_answer
9. generic candidate direct_answer or graph_for_rag
```

The exact code order may differ, but these behavioral precedences must hold.

### 4. Keep Template Generation Conservative But Useful

Preferred implementation: keep `build_patent_template_candidates(...)` unchanged unless tests show the classifier cannot reliably suppress direct answers.

Reason: `list_patents_by_material` remains valuable for explicit material patent lookup. The bug is not that the template exists; the bug is that generic candidate presence is interpreted as direct-answer intent for open analytical questions.

If implementation discovers that keeping candidates pollutes diagnostics or planning, the fallback option is to suppress material-only `list_patents_by_material` candidate generation when `asks_analytical_relation` is true and explicit patent list/count intent is false. This fallback should be used only if classifier-level routing is not sufficient.

### 5. Preserve Existing Attribute Routing

The May 1 attribute routing design already handles questions like:

- `磷酸铁锂的电压是多少`
- `磷酸铁锂容量是多少`
- `磷酸铁锂的压实密度是多少`

Those questions are material attribute value questions and currently route to `graph_for_rag`.

This new analytical relation class is narrower and different:

- `磷酸铁锂磨砂粒径与产品性能间的关系`
- `磷酸铁锂粒径对倍率性能的影响`
- `烧结温度与磷酸铁锂循环性能的关系`

These should route to `skip_graph` unless there is an explicit patent lookup intent.

## Proposed File Touches

- `patent/server/patent/graph_kb/slots.py`
  - Add analytical relation, parameter, and performance signals.
  - Expose diagnostics for the new signal.

- `patent/server/patent/graph_kb/classifier_v2.py`
  - Add a skip branch for analytical relation questions before the why/how graph-for-RAG branch and before the generic candidate direct-answer branch.
  - Preserve patent ID, explicit list/count, applicant, inventor, agency, and IPC direct-answer behavior.

- `patent/tests/test_patent_graph_kb_slots.py`
  - Add slot tests for analytical relation detection.
  - Add negative tests proving explicit patent lookup still has list/count intent.

- `patent/tests/test_patent_graph_kb_classifier_v2.py`
  - Add classifier tests for analytical relation skip behavior.
  - Add preservation tests for explicit material patent list/count direct answers.

- `patent/tests/test_patent_kb_service.py` or `patent/tests/test_patent_graph_kb_service_v2.py`
  - Add a service-level regression proving analytical relation questions are not returned as `patent_graph_kb` direct answers.

## Test Matrix

### Analytical Relation Questions Should Skip Graph

- `磷酸铁锂磨砂粒径与产品性能间的关系`
- `磷酸铁锂粒径对倍率性能的影响`
- `磷酸铁锂颗粒尺寸和循环性能有什么关系`
- `烧结温度与磷酸铁锂循环性能的关系`
- `磷酸铁锂D50对放电容量的影响`
- `宁德时代磷酸铁锂粒径与性能关系`
- `H01M10 磷酸铁锂粒径与性能关系`

Expected:

- `classify_patent_graph_question_v2(...).mode == "skip_graph"`
- diagnostics `matched_rule == "analytical_relation_question"`
- no direct graph answer is returned by `PatentKbService`.

### Explicit Material Patent Lookup Should Stay Direct Answer

- `涉及磷酸铁锂的专利有哪些`
- `列出磷酸铁锂相关专利`
- `磷酸铁锂相关专利数量是多少`
- `涉及磷酸铁锂粒径调控的专利有哪些`
- `磷酸铁锂产品性能相关专利有多少`

Expected:

- `mode == "direct_answer"`
- candidate path remains material/list/count-oriented.
- graph preflight may short-circuit with `query_mode == "patent_graph_kb"` when rows are usable.

### Concrete Patent Anchors Should Not Be Accidentally Skipped

- `CN100355122C 的粒径与性能关系是什么`
- `CN100355122C 的实验数据是什么`
- `CN100355122C 的性能事实是什么`

Expected:

- not skipped merely because the question contains `关系`, `粒径`, or `性能`.
- existing single-patent direct-answer or graph-for-RAG behavior is preserved.

### Non-Anchored Broad Questions Should Continue to Skip Graph

- `粒径和产品性能有什么关系`
- `烧结温度对循环性能的影响`
- `这个材料的粒径和性能关系`

Expected:

- graph should not infer `list_patents_by_material`.
- if diagnostics still show a suppressed material candidate, it must not be executed.
- `skip_graph` or existing context-resolution behavior is acceptable when there is no reliable graph anchor.

## Logging and Diagnostics

The existing graph logs are sufficient if the new branch is represented in current fields:

```text
patent_graph.classify_done ... mode=skip_graph route_family=semantic matched_rule=analytical_relation_question
patent_graph.route_end ... final_mode=skip_graph reason=analytical_relation_question
```

Slot diagnostics should include the analytical relation signal so future routing bugs can be reproduced from logs without re-running the classifier.

## Risks

- Over-skipping explicit patent lookup questions would reduce useful graph direct answers. Tests must protect list/count examples containing `粒径`, `性能`, and `关系`.
- Under-detecting synonyms such as `颗粒尺寸`, `D50`, or `相关性` would leave the bug for common variants.
- Routing to `skip_graph` means graph candidates will not seed retrieval for these questions. This is intentional for this fix because current graph evidence is too coarse for reliable parameter-performance causality.
- Adding broad relation terms can accidentally catch legitimate precise graph queries. The skip predicate must not skip patent-ID questions, and it must not skip applicant, inventor, agency, or IPC questions when they are paired with explicit patent list/count intent. Applicant or IPC names alone do not block analytical skip when the question is still an open parameter-performance relation question.

## Acceptance Criteria

- `磷酸铁锂磨砂粒径与产品性能间的关系` no longer returns a `patent_graph_kb` direct answer.
- The classifier routes the representative question to `skip_graph` with `matched_rule="analytical_relation_question"`.
- Explicit material patent list/count questions still route to direct graph answers.
- Existing material attribute value questions such as `磷酸铁锂的电压是多少` keep their current `graph_for_rag` behavior.
- Single-patent facet questions are not skipped solely because they contain relation, parameter, or performance terms.
- Relevant patent graph and service tests pass.
- No gateway, fastQA, frontend, or Neo4j schema changes are required.

## Future Decision

- Whether future work should add a `graph_for_rag` mode for parameter-performance questions.
  - Recommendation: not in this fix. Add it only after there is a graph evidence strategy that can distinguish real parameter-performance relationships from generic material co-mentions.
