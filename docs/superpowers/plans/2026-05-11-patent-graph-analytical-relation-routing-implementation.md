# Patent Graph Analytical Relation Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route patent-mode analytical parameter-performance relation questions such as `磷酸铁锂磨砂粒径与产品性能间的关系` away from patent graph direct answers while preserving explicit graph list/count behavior.

**Architecture:** Add a deterministic analytical-relation signal in patent graph slot extraction, then use classifier precedence to return `skip_graph` before existing why/how graph-for-RAG and generic candidate direct-answer fallbacks can overmatch. Keep graph query templates unchanged unless tests prove classifier-level routing is insufficient.

**Tech Stack:** Python dataclasses, pytest, patent graph KB V2 modules under `patent/server/patent/graph_kb/`, patent service tests under `patent/tests/`.

**Spec:** `docs/superpowers/specs/2026-05-11-patent-graph-analytical-relation-routing-design.md`

**Execution Constraint:** Do not commit during implementation unless the user explicitly asks for a commit.

---

## File Structure

- Modify `patent/server/patent/graph_kb/slots.py`
  - Add `asks_analytical_relation: bool` to `PatentGraphQuestionSlots`.
  - Add relation, parameter, and performance keyword helpers.
  - Populate the new slot and expose it in `diagnostics()`.

- Modify `patent/server/patent/graph_kb/classifier_v2.py`
  - Add helper predicate for analytical relation questions.
  - Add a `skip_graph` branch before existing why/how graph-for-RAG and generic candidate fallback routing.
  - Preserve patent-ID, explicit patent list/count, material attribute, and existing direct-answer behavior.

- Modify `patent/tests/test_patent_graph_kb_slots.py`
  - Add slot tests for analytical relation detection.
  - Add negative tests for explicit patent lookup and non-anchored broad relation wording.

- Modify `patent/tests/test_patent_graph_kb_classifier_v2.py`
  - Add classifier tests for analytical relation skip behavior.
  - Add preservation tests for explicit material patent lookup, material attribute graph-for-RAG, and single-patent anchors.

- Modify `patent/tests/test_patent_graph_kb_service_v2.py`
  - Add router-level regression proving analytical relation skip does not execute a graph query.

- Modify `patent/tests/test_patent_kb_service.py`
  - Add service-level regression proving a `skip_graph` analytical relation question proceeds through staged generation and does not return `patent_graph_kb`.

---

### Task 1: Slot-Level Analytical Relation Signal

**Files:**
- Modify: `patent/server/patent/graph_kb/slots.py`
- Test: `patent/tests/test_patent_graph_kb_slots.py`

- [ ] **Step 1: Add failing slot tests**

Append these tests to `patent/tests/test_patent_graph_kb_slots.py`:

```python
def test_extracts_analytical_relation_intent_for_parameter_performance_questions():
    cases = [
        "磷酸铁锂磨砂粒径与产品性能间的关系",
        "磷酸铁锂粒径对倍率性能的影响",
        "磷酸铁锂颗粒尺寸和循环性能有什么关系",
        "烧结温度与磷酸铁锂循环性能的关系",
        "磷酸铁锂D50对放电容量的影响",
        "宁德时代磷酸铁锂粒径与性能关系",
        "H01M10 磷酸铁锂粒径与性能关系",
    ]

    for question in cases:
        slots = extract_patent_graph_slots(question)
        assert slots.asks_analytical_relation is True


def test_analytical_relation_signal_does_not_replace_explicit_patent_lookup_intent():
    cases = [
        "涉及磷酸铁锂粒径调控的专利有哪些",
        "磷酸铁锂产品性能相关专利有多少",
        "H01M10 下有哪些专利",
    ]

    for question in cases:
        slots = extract_patent_graph_slots(question)
        assert slots.asks_list or slots.asks_count


def test_relation_word_without_anchor_is_not_analytical_relation_signal():
    slots = extract_patent_graph_slots("前者和后者之间有什么关系")

    assert slots.asks_analytical_relation is False
```

- [ ] **Step 2: Run slot tests and verify the new tests fail**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_slots.py -q
```

Expected: FAIL with `AttributeError` for `asks_analytical_relation`, because the slot does not exist yet.

- [ ] **Step 3: Add the slot field and diagnostics**

In `patent/server/patent/graph_kb/slots.py`, add the field to `PatentGraphQuestionSlots` near the other `asks_*` booleans:

```python
    asks_analytical_relation: bool = False
```

Add it to `diagnostics()`:

```python
            "asks_analytical_relation": self.asks_analytical_relation,
```

- [ ] **Step 4: Add analytical relation keyword helpers**

In `slots.py`, add constants near the existing hint tuples:

```python
_RELATION_ANALYSIS_TERMS = ("关系", "相关性", "关联", "影响", "作用", "机制")
_PARAMETER_TERMS = ("粒径", "颗粒尺寸", "d50", "d90", "磨砂粒径", "比表面积", "一次粒径", "二次粒径", "烧结温度")
_PERFORMANCE_RELATION_TERMS = (
    "性能",
    "产品性能",
    "电化学性能",
    "倍率性能",
    "循环性能",
    "容量保持率",
    "放电容量",
    "压实密度",
    "振实密度",
)
```

Add the helper after `_asks_count_intent(...)`:

```python
def _asks_analytical_relation(
    text: str,
    *,
    material_terms: tuple[str, ...],
    process_terms: tuple[str, ...],
    metric_terms: tuple[str, ...],
) -> bool:
    has_relation = _contains_any(text, _RELATION_ANALYSIS_TERMS)
    has_parameter = _contains_any(text, _PARAMETER_TERMS)
    has_performance = bool(metric_terms) or _contains_any(text, _PERFORMANCE_RELATION_TERMS)
    has_material_or_process_context = bool(material_terms or process_terms)
    return bool(has_relation and has_parameter and has_performance and has_material_or_process_context)
```

This helper intentionally requires a parameter signal. It should catch `粒径/D50/烧结温度 + 性能 + 关系/影响` questions, but it should not catch generic material-impact questions such as `石墨烯材料对性能有什么影响？`; those keep the existing `graph_for_rag` behavior. The helper also intentionally does not check explicit patent list/count intent. The classifier owns final precedence so diagnostics can still show that a list/count question contains relation words.

- [ ] **Step 5: Populate the slot**

In `extract_patent_graph_slots(...)`, compute the value after `material_terms`, `process_terms`, and `metric_terms` are available. In the returned `PatentGraphQuestionSlots(...)`, add:

```python
        asks_analytical_relation=_asks_analytical_relation(
            text,
            material_terms=material_terms,
            process_terms=process_terms,
            metric_terms=metric_terms,
        ),
```

- [ ] **Step 6: Run slot tests and verify pass**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_slots.py -q
```

Expected: PASS.

---

### Task 2: Classifier Precedence for Analytical Relation Skip

**Files:**
- Modify: `patent/server/patent/graph_kb/classifier_v2.py`
- Test: `patent/tests/test_patent_graph_kb_classifier_v2.py`

- [ ] **Step 1: Add failing classifier tests**

Append these tests to `patent/tests/test_patent_graph_kb_classifier_v2.py`:

```python
def test_classifier_v2_skips_analytical_relation_questions_before_graph_candidates():
    cases = [
        "磷酸铁锂磨砂粒径与产品性能间的关系",
        "磷酸铁锂粒径对倍率性能的影响",
        "磷酸铁锂颗粒尺寸和循环性能有什么关系",
        "烧结温度与磷酸铁锂循环性能的关系",
        "磷酸铁锂D50对放电容量的影响",
        "宁德时代磷酸铁锂粒径与性能关系",
        "H01M10 磷酸铁锂粒径与性能关系",
    ]

    for question in cases:
        decision = classify_patent_graph_question_v2(question=question, conversation_context={})
        assert decision.mode == "skip_graph"
        assert decision.route_family == "semantic"
        assert decision.diagnostics["matched_rule"] == "analytical_relation_question"


def test_classifier_v2_preserves_explicit_material_patent_lookup_with_relation_terms():
    cases = [
        "涉及磷酸铁锂粒径调控的专利有哪些",
        "磷酸铁锂产品性能相关专利有多少",
        "宁德时代有哪些磷酸铁锂粒径相关专利",
        "H01M10 下有哪些磷酸铁锂粒径相关专利",
    ]

    for question in cases:
        decision = classify_patent_graph_question_v2(question=question, conversation_context={})
        assert decision.mode == "direct_answer"
        assert decision.route_family == "precise"


def test_classifier_v2_preserves_single_patent_relation_question():
    decision = classify_patent_graph_question_v2(
        question="CN100355122C 的粒径与性能关系是什么",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"


def test_classifier_v2_preserves_generic_material_impact_graph_for_rag():
    decision = classify_patent_graph_question_v2(
        question="石墨烯材料对性能有什么影响？",
        conversation_context={},
    )

    assert decision.mode == "graph_for_rag"
    assert decision.route_family == "hybrid"
    assert decision.diagnostics["matched_rule"] == "hybrid_graph_anchor"
```

- [ ] **Step 2: Run classifier tests and verify new failures**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_classifier_v2.py -q
```

Expected: FAIL. At least the representative query currently routes to `direct_answer`, and `影响` variants may route to existing `graph_for_rag`.

- [ ] **Step 3: Add classifier helper predicates**

In `patent/server/patent/graph_kb/classifier_v2.py`, keep the existing `_is_explicit_patent_listing_or_count(...)` and add a new helper near `_is_material_attribute_question(...)`:

```python
def _is_analytical_relation_question(slots: PatentGraphQuestionSlots) -> bool:
    return bool(slots.asks_analytical_relation)
```

If `_is_explicit_patent_listing_or_count(...)` is too narrow for applicant/IPC wording, tighten it to preserve explicit patent-object list/count intent:

```python
def _is_explicit_patent_listing_or_count(slots: PatentGraphQuestionSlots) -> bool:
    return any(hint in slots.normalized_question for hint in _PATENT_DOMAIN_OBJECT_HINTS) and bool(slots.asks_list or slots.asks_count)
```

Do not make applicant, inventor, agency, or IPC names alone count as explicit patent lookup. The question must ask for patent objects or counts.

- [ ] **Step 4: Add analytical skip branch before why/how routing**

In `classify_patent_graph_question_v2(...)`, add this `elif` after the existing multi-patent compare branch and before the applicant-landscape, material-attribute, and why/how branches:

```python
    elif (
        _is_analytical_relation_question(slots)
        and not slots.patent_ids
        and not _is_explicit_patent_listing_or_count(slots)
    ):
        decision = PatentGraphSemanticDecision(
            mode="skip_graph",
            route_family="semantic",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="analytical_relation_question", candidates=candidates),
        )
```

This placement is required because `影响` and `机制` already trigger `slots.asks_why_how`; placing the new branch after the why/how branch would fail the spec.

- [ ] **Step 5: Run classifier tests and verify pass**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_classifier_v2.py -q
```

Expected: PASS.

---

### Task 3: Router-Level No-Execution Regression

**Files:**
- Test: `patent/tests/test_patent_graph_kb_service_v2.py`

- [ ] **Step 1: Add a router test proving skip returns before graph execution**

Append this test to `patent/tests/test_patent_graph_kb_service_v2.py`:

```python
def test_route_v2_analytical_relation_question_skips_without_graph_execution(monkeypatch):
    def _fail_execute(**kwargs):
        raise AssertionError("skip_graph analytical relation should not execute a graph query")

    monkeypatch.setattr(patent_graph_service, "execute_patent_prepared_query", _fail_execute)

    result = route_patent_graph_kb_v2(
        question="磷酸铁锂磨砂粒径与产品性能间的关系",
        conversation_context={},
        neo4j_client=object(),
        max_rows=10,
    )

    assert result.mode == "skip_graph"
    assert result.direct_result is None
    assert result.rag_payload is None
    assert result.diagnostics["tri_state_mode"] == "skip_graph"
    assert result.diagnostics["matched_rule"] == "analytical_relation_question"
```

- [ ] **Step 2: Run the router test and verify it fails before implementation**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_service_v2.py::test_route_v2_analytical_relation_question_skips_without_graph_execution -q
```

Expected before Task 2 implementation: FAIL because the current classifier does not skip the representative query and the monkeypatched execute function raises. If Task 2 has already been implemented, this test should PASS immediately.

- [ ] **Step 3: Run the full graph service V2 tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_service_v2.py -q
```

Expected: PASS.

---

### Task 4: PatentKbService Staged Fallback Regression

**Files:**
- Test: `patent/tests/test_patent_kb_service.py`

- [ ] **Step 1: Add service-level regression**

Append this test to `patent/tests/test_patent_kb_service.py`:

```python
def test_kb_service_analytical_relation_skip_graph_uses_staged_runtime():
    captured = {}

    class _RecordingOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            captured["question"] = question
            captured["conversation_context"] = conversation_context
            return PatentQaExecutionResult(
                success=True,
                final_answer="generated analytical relation answer",
                metadata=PatentQaExecutionMetadata(route="kb_qa", query_mode="patent staged qa"),
                raw={
                    "references": [],
                    "reference_objects": [],
                    "reference_links": [],
                    "original_links": [],
                    "metadata": {},
                    "steps": [],
                },
            )

    service = PatentKbService(
        orchestrator=_RecordingOrchestrator(),
        graph_kb_client=object(),
        graph_kb_enabled=True,
        graph_kb_v2_enabled=True,
        graph_kb_rag_injection_enabled=True,
        graph_kb_service_v2=lambda **kwargs: PatentGraphRoutingResult(
            mode="skip_graph",
            diagnostics={
                "matched_rule": "analytical_relation_question",
                "tri_state_mode": "skip_graph",
            },
        ),
    )

    execution_result = service.run(
        request=_make_request(question="磷酸铁锂磨砂粒径与产品性能间的关系"),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert captured["question"] == "磷酸铁锂磨砂粒径与产品性能间的关系"
    assert captured["conversation_context"] == {"recent_turns_for_llm": []}
    assert execution_result["answer_text"] == "generated analytical relation answer"
    assert execution_result["query_mode"] == "patent_kb_qa"
    assert execution_result["metadata"].get("graph_kb_mode") is None
    assert execution_result["metadata"].get("query_mode") != "patent_graph_kb"
```

- [ ] **Step 2: Run the new service test**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_kb_service.py::test_kb_service_analytical_relation_skip_graph_uses_staged_runtime -q
```

Expected: PASS. This test uses an injected router returning `skip_graph`, so it verifies service behavior independently of classifier implementation.

- [ ] **Step 3: Run relevant service tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_kb_service.py -q
```

Expected: PASS.

---

### Task 5: Focused Verification and Regression Sweep

**Files:**
- Verify only.

- [ ] **Step 1: Run focused graph KB tests**

Run:

```bash
PYTHONPATH=patent pytest \
  patent/tests/test_patent_graph_kb_slots.py \
  patent/tests/test_patent_graph_kb_classifier_v2.py \
  patent/tests/test_patent_graph_kb_service_v2.py \
  patent/tests/test_patent_kb_service.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run local classifier smoke check**

Run:

```bash
PYTHONPATH=patent python -c "from server.patent.graph_kb.classifier_v2 import classify_patent_graph_question_v2; qs=['磷酸铁锂磨砂粒径与产品性能间的关系','磷酸铁锂粒径对倍率性能的影响','涉及磷酸铁锂的专利有哪些','磷酸铁锂的电压是多少','CN100355122C 的粒径与性能关系是什么']; [print(q, classify_patent_graph_question_v2(question=q, conversation_context={}).mode, classify_patent_graph_question_v2(question=q, conversation_context={}).diagnostics.get('matched_rule')) for q in qs]"
```

Expected output shape:

```text
磷酸铁锂磨砂粒径与产品性能间的关系 skip_graph analytical_relation_question
磷酸铁锂粒径对倍率性能的影响 skip_graph analytical_relation_question
涉及磷酸铁锂的专利有哪些 direct_answer list_patents_by_material
磷酸铁锂的电压是多少 graph_for_rag material_attribute_graph_anchor
CN100355122C 的粒径与性能关系是什么 direct_answer patent_lookup
```

- [ ] **Step 3: Check working tree**

Run:

```bash
git status --short
```

Expected: only the intended files for this plan, the spec/plan docs, and any pre-existing unrelated untracked files are present.

- [ ] **Step 4: Do not commit unless requested**

If the user explicitly asks for a commit later, use a conventional commit such as:

```bash
git add \
  patent/server/patent/graph_kb/slots.py \
  patent/server/patent/graph_kb/classifier_v2.py \
  patent/tests/test_patent_graph_kb_slots.py \
  patent/tests/test_patent_graph_kb_classifier_v2.py \
  patent/tests/test_patent_graph_kb_service_v2.py \
  patent/tests/test_patent_kb_service.py \
  docs/superpowers/specs/2026-05-11-patent-graph-analytical-relation-routing-design.md \
  docs/superpowers/plans/2026-05-11-patent-graph-analytical-relation-routing-implementation.md
git commit -m "fix: skip patent graph for analytical relation questions"
```
