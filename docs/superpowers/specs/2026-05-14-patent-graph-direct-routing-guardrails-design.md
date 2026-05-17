# Patent Graph Direct Routing Guardrails Design

**Date:** 2026-05-14

## Goal

Prevent patent graph direct-answer routing from returning deterministic patent lists for questions that ask for synthesis, typical process conditions, material choices, parameter values, or explanatory conclusions.

The graph layer should still answer precise graph lookup questions directly. For broader questions, it should either provide graph constraints to the staged RAG pipeline or skip graph preflight entirely.

## Background

The patent `kb_qa` path now runs graph preflight before the staged patent QA pipeline. When graph preflight returns `direct_answer`, `PatentKbService` short-circuits and returns the graph renderer output without Stage1/Stage2/vector retrieval or LLM synthesis.

That behavior is correct for bounded graph facts such as:

- A single patent's process steps, materials, atmosphere, experiment rows, citations, problem/solution, or inventive scope.
- Applicant, inventor, agency, and IPC list/count queries.
- Explicit material or process patent-list queries.
- Ranking queries that ask for graph frequency.

However, runtime testing showed that broad material/process questions are also being classified as direct answers. These questions ask for synthesized domain knowledge, but the graph layer returns a list of patents because it detects a material or process term.

Representative failing query:

```text
磷酸铁锂固相合成法通常需要哪种保护气氛？
```

Observed runtime result:

```text
query_mode=patent_graph_kb
graph_kb_mode=direct_answer
graph_kb_path_id=list_patents_by_material
answer_prefix=涉及材料 `磷酸铁锂LiFePO4` 的专利包括...
```

The answer is wrong for the user intent. The user asked for a typical protective atmosphere, not for a list of patents involving the material.

## Evidence From Runtime Audit

All examples below were observed against the running patent backend on `127.0.0.1:8010` and the patent graph on `127.0.0.1:8687`.

### Incorrect Direct Answers

These questions returned `query_mode=patent_graph_kb`, `graph_kb_mode=direct_answer`, and a list-style graph template:

| Question | Observed Path | Problem |
| --- | --- | --- |
| `磷酸铁锂固相合成法通常需要哪种保护气氛？` | `list_patents_by_material` | Asks for typical atmosphere, gets material patent list. |
| `磷酸铁锂的保护气氛是什么？` | `list_patents_by_material` | Asks for atmosphere, gets material patent list. |
| `磷酸铁锂固相法的保护气氛是什么？` | `list_patents_by_material` | Asks for process condition, gets material patent list. |
| `磷酸铁锂烧结通常用氮气还是空气？` | `list_patents_by_material` | Asks for choice between atmospheres, gets material patent list. |
| `磷酸铁锂碳包覆通常需要什么气氛？` | `list_patents_by_material` | Asks for process atmosphere, gets material patent list. |
| `磷酸铁锂保护气氛的作用是什么？` | `list_patents_by_material` | Asks for function/effect, gets material patent list. |
| `磷酸铁锂是否需要在空气中烧结？` | `list_patents_by_material` | Asks yes/no condition, gets material patent list. |
| `磷酸铁锂烧结时可以用空气吗？` | `list_patents_by_material` | Asks yes/no condition, gets material patent list. |
| `磷酸铁锂烧结温度是多少？` | `list_patents_by_material` | Asks parameter value, gets material patent list. |
| `磷酸铁锂固相法烧结温度通常是多少？` | `list_patents_by_material` | Asks typical parameter value, gets material patent list. |
| `磷酸铁锂固相合成需要哪些原料？` | `list_patents_by_material` | Asks synthesis inputs, gets product-material patent list. |
| `磷酸铁锂固相合成的原料配比是多少？` | `list_patents_by_material` | Asks formula/ratio, gets material patent list. |
| `烧结需要哪种气氛？` | `list_patents_by_process_term` | Asks process condition, gets process patent list. |
| `碳包覆通常需要什么气氛？` | `list_patents_by_process_term` | Asks process condition, gets process patent list. |
| `喷雾干燥通常需要什么气氛？` | `list_patents_by_process_term` | Asks process condition, gets process patent list. |
| `碳包覆的作用是什么？` | `list_patents_by_process_term` | Asks effect/function, gets process patent list. |
| `喷雾干燥的作用是什么？` | `list_patents_by_process_term` | Asks effect/function, gets process patent list. |
| `涉及磷酸铁锂保护气氛的专利有哪些？` | `list_patents_by_material` | Asks a combined material+facet patent list, but only material is used. |
| `磷酸铁锂相关专利数量是多少？` | `list_patents_by_material` | Count intent is detected, but material count is not supported and list is returned. |

### Correct Direct Answers To Preserve

These graph direct answers are valid and should remain direct:

| Question | Path |
| --- | --- |
| `CN100355122C 采用什么保护气氛？` | `list_patent_atmospheres` |
| `CN100355122C 的工艺步骤是什么？` | `list_patent_process_steps` |
| `CN100355122C 的材料有哪些？` | `list_patent_material_roles` |
| `CN100355122C 的技术问题和技术方案是什么？` | `list_patent_problem_solution` |
| `宁德时代新能源科技股份有限公司有哪些专利？` | `list_patents_by_applicant` |
| `宁德时代新能源科技股份有限公司有多少专利？` | `count_patents_by_applicant` |
| `H01M10 下有哪些专利？` | `list_patents_by_ipc_code_prefix` |
| `H01M10 下有多少专利？` | `count_patents_by_ipc_code_prefix` |
| `磷酸铁锂相关专利有哪些？` | `list_patents_by_material` |

### Graph For RAG Behavior To Preserve

These questions should continue to avoid direct answer and enter staged RAG with graph hints when available:

| Question | Expected Graph Mode | Rule |
| --- | --- | --- |
| `磷酸铁锂的电压是多少？` | `graph_for_rag` | `material_attribute_graph_anchor` |
| `磷酸铁锂固相法为什么要用惰性气氛？` | `graph_for_rag` | `hybrid_graph_anchor` |
| `磷酸铁锂用氮气保护的原因是什么？` | `graph_for_rag` | `hybrid_graph_anchor` |
| `比较 CN100355122C 和 CN100371239C 的工艺步骤差异` | `graph_for_rag` | `multi_patent_compare` |
| `为什么喷雾干燥能提升磷酸铁锂倍率性能？` | `graph_for_rag` | `hybrid_graph_anchor` |
| `磷酸铁锂粒径对倍率性能的影响` | `skip_graph` | `analytical_relation_question` |

## Current Root Cause

The failure comes from three interacting behaviors:

1. Slot extraction detects broad material and process terms such as `磷酸铁锂`, `烧结`, `碳包覆`, and `喷雾干燥`.
2. Template candidate building turns those terms into `list_patents_by_material`, `list_patents_by_material_role`, or `list_patents_by_process_term`.
3. The classifier makes any all-direct-eligible candidate set into `direct_answer`, unless an earlier rule classifies the question as attribute value, why/how, analytical relation, comparison, or another hybrid/semantic case.

As a result, many questions whose surface form contains `是什么`, `通常`, `需要`, `可以`, `作用`, `温度`, or `配比` are treated as graph list requests merely because they contain a known material or process term.

## Requirements

### R1: Add Intent Guardrails Before Candidate Direct Fallback

Before the classifier uses generic candidate directness to choose `direct_answer`, it must identify material/process synthesis questions that require RAG.

The guardrail should apply when all of these are true:

- There is no single patent ID.
- The primary signal is material, material role, process, atmosphere, or a combination of them.
- The question is not an explicit patent list/count/rank request.
- The user is asking for a synthesized value, condition, choice, role, effect, suitability, or method summary.

Matching intent signals should include at least:

- Typicality: `通常`, `一般`, `常用`, `常见`, `优选`, `推荐`
- Requirement/condition: `需要`, `应`, `是否`, `能否`, `可以`, `用什么`, `哪种`, `什么气氛`, `什么条件`
- Choice/comparison without explicit patents: `氮气还是空气`, `A还是B`, `是否需要`
- Effect/function: `作用`, `目的`, `原因`, `为什么`
- Parameter/value: `温度`, `配比`, `比例`, `范围`, `是多少`
- Facet questions not anchored to a patent ID: `保护气氛`, `烧结气氛`, `原料`, `锂源`, `碳源`

The guarded mode should be `graph_for_rag` when the graph can provide useful constraints or hints. It should be `skip_graph` only when no material/process/role candidate exists.

### R2: Preserve Explicit Patent List/Count Direct Answers

Questions that explicitly ask for patents should remain direct when the selected template faithfully represents all constraints in the question.

Examples that remain direct:

- `磷酸铁锂相关专利有哪些？`
- `涉及烧结的专利有哪些？`
- `宁德时代新能源科技股份有限公司有哪些专利？`
- `H01M10 下有哪些专利？`

However, combined-facet patent-list questions must not silently drop the facet.

Example:

```text
涉及磷酸铁锂保护气氛的专利有哪些？
```

This must not direct-answer via only `list_patents_by_material`, because the atmosphere facet is ignored. It should go to `graph_for_rag` unless a template exists that enforces both material and atmosphere.

### R3: Fix Material Count Intent

If a user asks for a material/process/material-role count, the graph layer must not return a list template as a count answer.

Minimum acceptable behavior:

- Route unsupported material/process count questions to `graph_for_rag`.
- Do not return `list_patents_by_material` for `磷酸铁锂相关专利数量是多少？`.

Optional behavior:

- Add count templates for material, material role, and process terms if they can be implemented with bounded Cypher and tested.

The implementation plan should choose the smaller safe option unless count templates are required by product needs.

### R4: Preserve Single-Patent Direct Facet Answers

Single-patent facet questions remain direct. These are bounded graph facts and are exactly what direct answer is for:

- `CN100355122C 采用什么保护气氛？`
- `CN100355122C 的工艺步骤是什么？`
- `CN100355122C 的材料有哪些？`
- `CN100355122C 的技术问题和技术方案是什么？`
- `CN100355122C 的实验数据是什么？`

### R5: Preserve Graph-For-RAG Injection

When a question is downgraded from direct answer to `graph_for_rag`, the existing graph preflight flow must still inject useful graph context into the staged pipeline where possible.

The pipeline should keep using:

- `conversation_context["graph_kb"]`
- `stage2_patent_candidates`
- `stage2_constraints`
- `stage2_entity_hints`
- `stage4_fact_block`

Stage2 should continue to report `graph_stage2_behavior` as `filter_applied`, `hint_only`, or `seed_boost` based on the injected graph payload.

### R6: No Frontend, Gateway, Or Lifecycle Changes

This fix is backend-only inside the patent graph routing path.

Do not change:

- Frontend rendering.
- Gateway routing.
- Service ports or lifecycle scripts.
- Neo4j schema or ingestion.
- Authentication.
- Patent file QA routes.

## Proposed Design

### Approach

Add a classifier-level guardrail that distinguishes explicit graph listing from synthesis-oriented material/process questions.

The guardrail should live near the existing semantic decision logic so routing remains explainable and testable. It should not be implemented as a renderer fallback, because renderer fallback happens after graph execution and cannot recover user intent cleanly.

### Intent Model

Extend slot extraction or classifier helpers with small deterministic predicates:

- `is_explicit_patent_listing_or_count`
- `is_unsupported_material_process_count`
- `is_material_process_synthesis_question`
- `is_combined_facet_listing_that_current_template_drops`

The predicates should be conservative. If uncertain, prefer `graph_for_rag` over direct answer for non-ID material/process questions.

### Decision Order

The desired order is:

1. DOI unsupported -> `skip_graph`
2. No graph signal -> existing no-signal behavior
3. Multi-patent compare -> `graph_for_rag`
4. Analytical relation -> `skip_graph`
5. Material attribute value -> `graph_for_rag`
6. Why/how anchored to graph signal -> `graph_for_rag`
7. New unsupported material/process count -> `graph_for_rag`
8. New material/process synthesis guardrail -> `graph_for_rag`
9. New combined-facet listing guardrail -> `graph_for_rag`
10. Single patent -> `direct_answer`
11. Applicant/inventor/agency/IPC -> `direct_answer`
12. Explicit supported material/process/material-role list -> `direct_answer`
13. Remaining candidates -> existing fallback behavior

### Metadata And Diagnostics

Every new guardrail branch must set a stable `matched_rule` in diagnostics. Suggested values:

- `material_process_synthesis_question`
- `unsupported_material_process_count`
- `combined_facet_listing_requires_rag`

These diagnostics are needed for tests and future live audits.

### Safety

The fix should be narrow:

- It should not remove existing query templates.
- It should not change direct renderer formatting.
- It should not require LLM calls for explicit graph lookup questions.
- It should not introduce broad regexes that classify applicant/IPC/patent-ID questions as synthesis.

## Acceptance Criteria

### Runtime Behavior

The following questions must no longer return `query_mode=patent_graph_kb` with `graph_kb_mode=direct_answer`:

- `磷酸铁锂固相合成法通常需要哪种保护气氛？`
- `磷酸铁锂的保护气氛是什么？`
- `磷酸铁锂固相法的保护气氛是什么？`
- `磷酸铁锂烧结通常用氮气还是空气？`
- `磷酸铁锂碳包覆通常需要什么气氛？`
- `磷酸铁锂保护气氛的作用是什么？`
- `磷酸铁锂是否需要在空气中烧结？`
- `磷酸铁锂烧结时可以用空气吗？`
- `磷酸铁锂烧结温度是多少？`
- `磷酸铁锂固相法烧结温度通常是多少？`
- `磷酸铁锂固相合成需要哪些原料？`
- `磷酸铁锂固相合成的原料配比是多少？`
- `烧结需要哪种气氛？`
- `碳包覆通常需要什么气氛？`
- `喷雾干燥通常需要什么气氛？`
- `碳包覆的作用是什么？`
- `喷雾干燥的作用是什么？`
- `涉及磷酸铁锂保护气氛的专利有哪些？`
- `磷酸铁锂相关专利数量是多少？`

They should be classified as `graph_for_rag` when a graph candidate or constraint can be produced; otherwise `skip_graph` is acceptable.

The following questions must remain direct graph answers:

- `CN100355122C 采用什么保护气氛？`
- `CN100355122C 的工艺步骤是什么？`
- `CN100355122C 的材料有哪些？`
- `CN100355122C 的技术问题和技术方案是什么？`
- `宁德时代新能源科技股份有限公司有哪些专利？`
- `宁德时代新能源科技股份有限公司有多少专利？`
- `H01M10 下有哪些专利？`
- `H01M10 下有多少专利？`
- `磷酸铁锂相关专利有哪些？`
- `涉及烧结的专利有哪些？`

### Test Coverage

Add focused tests for:

- Slot extraction of atmosphere, typicality, requirement, parameter, and effect/function intent terms.
- Classifier routing for all acceptance examples.
- Candidate preservation for explicit list queries.
- `graph_for_rag` metadata path when direct answer is downgraded.
- Regression coverage for the original failing query.

### Manual Verification

After implementation, run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_classifier_v2.py -q
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_service_v2.py -q
curl -sS http://127.0.0.1:8010/api/health
```

Then call `/api/ask` for the original failing query and verify it does not return a direct material list.

## Out Of Scope

- Adding new graph templates for every process parameter.
- Answering atmosphere questions entirely from graph aggregation.
- Changing how Stage1 writes final natural-language answers.
- Changing frontend display of graph references.
- Changing graph ingestion or schema.
- Changing service management scripts.

## Risks

### Risk: Over-Downgrading Useful Direct Lists

Explicit material/process patent-list questions are useful direct answers. The guardrail must check list/count/rank intent before downgrading.

### Risk: Ambiguous Chinese Surface Forms

`有哪些` can mean either “which patents” or “which materials/conditions.” The guardrail should use patent-object hints such as `专利`, `申请`, `授权`, `公开`, `件`, and `项` to distinguish patent listing from synthesis listing.

### Risk: Count Intent Without Count Template

Material/process count intent is currently detected but not supported by count templates. The safe behavior is graph-for-RAG rather than returning a list.

### Risk: Graph-For-RAG With Empty Rows

Some graph-for-RAG paths may execute a template with no rows but still produce constraints. This is acceptable if Stage2 can use the constraints. Tests should assert constraints, not only candidate patent IDs.

## Implementation Notes

Relevant files:

- `patent/server/patent/graph_kb/slots.py`
- `patent/server/patent/graph_kb/classifier_v2.py`
- `patent/server/patent/graph_kb/query_templates.py`
- `patent/server/patent/graph_kb/canonicalizer.py`
- `patent/server/patent/graph_kb/service.py`
- `patent/server/patent/kb_service.py`
- `patent/server/patent/retrieval_service.py`
- `patent/tests/test_patent_graph_kb_classifier_v2.py`
- `patent/tests/test_patent_graph_kb_service_v2.py`

Expected primary code changes are in `slots.py` and `classifier_v2.py`. Other files are listed because tests may need to verify graph-for-RAG payload preservation.

The implementation should not change existing direct renderer behavior.
