# Patent FastQA-Like Attribute Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route patent-mode material attribute questions such as `磷酸铁锂的电压是多少` through `graph_for_rag` and staged generation, while preserving explicit patent list/count direct answers.

**Architecture:** Add a narrow attribute/open-answer signal in patent slot extraction, tighten count intent so `是多少` is not treated as patent counting, and add classifier precedence that keeps explicit patent lookup direct but downgrades material attribute questions to `graph_for_rag`. Existing graph RAG payload injection and staged generation flow remain unchanged.

**Tech Stack:** Python dataclasses, pytest, patent graph KB v2 modules under `patent/server/patent/graph_kb/`, patent service tests under `patent/tests/`.

**Spec:** `docs/superpowers/specs/2026-05-01-patent-fastqa-like-attribute-routing-design.md`

**Execution Constraint:** Do not commit during implementation unless the user explicitly asks for a commit.

---

## File Structure

- Modify `patent/server/patent/graph_kb/slots.py`
  - Add `asks_attribute_value: bool` to `PatentGraphQuestionSlots`.
  - Add helper logic for material/performance attribute questions.
  - Replace raw `多少` count matching with phrase-level patent count intent.

- Modify `patent/server/patent/graph_kb/classifier_v2.py`
  - Add explicit precedence for patent ID facets, explicit patent listing/count, then material attribute graph-for-RAG.
  - Add diagnostics `matched_rule="material_attribute_graph_anchor"` for the new branch.

- Modify `patent/tests/test_patent_graph_kb_slots.py`
  - Add focused slot tests for `是多少` attribute questions and patent count questions.

- Modify `patent/tests/test_patent_graph_kb_classifier_v2.py`
  - Add classifier regression tests for material attribute questions, explicit listing/count preservation, and mixed-intent precedence.

- Modify `patent/tests/test_patent_kb_service.py`
  - Add a service-level regression proving the representative question proceeds through graph-for-RAG injection instead of direct answer short-circuit.

---

### Task 1: Slot Intent Extraction

**Files:**
- Modify: `patent/server/patent/graph_kb/slots.py`
- Test: `patent/tests/test_patent_graph_kb_slots.py`

- [ ] **Step 1: Add failing slot tests**

Append tests to `patent/tests/test_patent_graph_kb_slots.py`:

```python
def test_extracts_material_attribute_value_intent_without_counting():
    cases = [
        "磷酸铁锂的电压是多少？",
        "磷酸铁锂电压范围是多少？",
        "磷酸铁锂容量是多少？",
        "磷酸铁锂的压实密度是多少？",
    ]

    for question in cases:
        slots = extract_patent_graph_slots(question)
        assert "磷酸铁锂" in slots.material_terms
        assert slots.asks_attribute_value is True
        assert slots.asks_count is False


def test_extracts_patent_count_intent_without_confusing_attribute_value():
    count_cases = [
        "涉及磷酸铁锂的专利有多少？",
        "磷酸铁锂相关专利数量是多少？",
        "宁德时代有多少专利？",
        "H01M10 下有多少专利？",
    ]

    for question in count_cases:
        slots = extract_patent_graph_slots(question)
        assert slots.asks_count is True
```

- [ ] **Step 2: Run slot tests and verify the new test fails**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_slots.py -q
```

Expected: FAIL because `PatentGraphQuestionSlots` does not yet expose `asks_attribute_value`, and `是多少` currently makes `asks_count=True`.

- [ ] **Step 3: Add the new slot field**

In `PatentGraphQuestionSlots`, add:

```python
asks_attribute_value: bool = False
```

In `diagnostics()`, add:

```python
"asks_attribute_value": self.asks_attribute_value,
```

- [ ] **Step 4: Add attribute and count helpers**

In `slots.py`, add constants near the existing hint tuples:

```python
_ATTRIBUTE_VALUE_TERMS = (
    "电压",
    "电压范围",
    "容量",
    "比容量",
    "放电容量",
    "倍率",
    "倍率性能",
    "压实密度",
    "振实密度",
    "电导率",
    "循环",
    "循环性能",
    "能量密度",
    "功率密度",
    "性能",
)
_ATTRIBUTE_VALUE_QUESTION_HINTS = ("是多少", "范围是多少", "怎么样", "如何", "表现")
_COUNT_OBJECT_HINTS = ("专利", "件", "项", "申请", "授权", "公开")
```

Add helper functions:

```python
def _asks_attribute_value(text: str) -> bool:
    return _contains_any(text, _ATTRIBUTE_VALUE_TERMS) and _contains_any(text, _ATTRIBUTE_VALUE_QUESTION_HINTS)


def _asks_count_intent(text: str) -> bool:
    if "数量" in text or "统计" in text:
        return True
    if "有多少" in text and _contains_any(text, _COUNT_OBJECT_HINTS):
        return True
    if "多少" in text and _contains_any(text, _COUNT_OBJECT_HINTS):
        return True
    return False
```

Then set the slot:

```python
asks_count=_asks_count_intent(text),
asks_attribute_value=_asks_attribute_value(text),
```

This intentionally makes `容量是多少` non-count while preserving `有多少专利`.

- [ ] **Step 5: Run slot tests and verify pass**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_slots.py -q
```

Expected: PASS.

---

### Task 2: Classifier Precedence and Routing

**Files:**
- Modify: `patent/server/patent/graph_kb/classifier_v2.py`
- Test: `patent/tests/test_patent_graph_kb_classifier_v2.py`

- [ ] **Step 1: Add failing classifier tests**

Append tests to `patent/tests/test_patent_graph_kb_classifier_v2.py`:

```python
def test_classifier_v2_routes_material_attribute_questions_to_graph_for_rag():
    cases = [
        "磷酸铁锂的电压是多少？",
        "磷酸铁锂电压范围是多少？",
        "磷酸铁锂的压实密度是多少？",
        "磷酸铁锂容量是多少？",
    ]

    for question in cases:
        decision = classify_patent_graph_question_v2(question=question, conversation_context={})
        assert decision.mode == "graph_for_rag"
        assert decision.route_family == "hybrid"
        assert decision.diagnostics["matched_rule"] == "material_attribute_graph_anchor"
        assert decision.diagnostics["candidate_path_ids"][0] == "list_patents_by_material"


def test_classifier_v2_keeps_explicit_material_patent_lookup_direct_with_attribute_terms():
    cases = [
        "涉及磷酸铁锂的专利有哪些？",
        "涉及磷酸铁锂的专利有多少？",
        "涉及磷酸铁锂电压相关的专利有哪些？",
        "磷酸铁锂电压相关专利数量是多少？",
    ]

    for question in cases:
        decision = classify_patent_graph_question_v2(question=question, conversation_context={})
        assert decision.mode == "direct_answer"
        assert decision.route_family == "precise"
        assert decision.diagnostics["candidate_path_ids"][0] == "list_patents_by_material"


def test_classifier_v2_keeps_single_patent_attribute_question_direct():
    decision = classify_patent_graph_question_v2(
        question="CN100355122C 中磷酸铁锂的电压是多少？",
        conversation_context={},
    )

    assert decision.mode == "direct_answer"
    assert decision.route_family == "precise"
    assert decision.diagnostics["matched_rule"] == "patent_lookup"
```

- [ ] **Step 2: Run classifier tests and verify new failures**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_classifier_v2.py -q
```

Expected: FAIL because attribute material questions still classify as `direct_answer`.

- [ ] **Step 3: Add helper predicates in classifier**

In `patent/server/patent/graph_kb/classifier_v2.py`, add local helpers near `_diagnostics`:

```python
def _is_explicit_patent_listing_or_count(slots: PatentGraphQuestionSlots) -> bool:
    text = slots.normalized_question
    return "专利" in text and bool(slots.asks_list or slots.asks_count)


def _is_material_attribute_question(slots: PatentGraphQuestionSlots) -> bool:
    return bool(slots.material_terms and slots.asks_attribute_value)
```

- [ ] **Step 4: Add the precedence branch**

In `classify_patent_graph_question_v2`, add the material-attribute branch after the existing single-patent branch and before applicant/inventor/agency/IPC/candidate direct-answer handling:

```python
    elif _is_material_attribute_question(slots) and not slots.patent_ids and not _is_explicit_patent_listing_or_count(slots):
        decision = PatentGraphSemanticDecision(
            mode="graph_for_rag",
            route_family="hybrid",
            standalone=standalone,
            diagnostics=_diagnostics(slots, matched_rule="material_attribute_graph_anchor", candidates=candidates),
        )
```

Do not place this before the multi-patent compare or single-patent branches. Do not place it after the generic `elif candidates` branch, because that is the direct-answer fallback causing the current bug.

- [ ] **Step 5: Run classifier tests and verify pass**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_classifier_v2.py -q
```

Expected: PASS.

---

### Task 3: Service-Level Graph-for-RAG Handoff Regression

**Files:**
- Modify: `patent/tests/test_patent_kb_service.py`

- [ ] **Step 1: Add a service-level handoff regression test**

Add a test near `test_kb_service_v2_graph_for_rag_injects_context_when_enabled`:

```python
def test_kb_service_material_attribute_question_uses_graph_for_rag_injection():
    captured = {}

    class _RecordingOrchestrator:
        def run(self, *, question: str, runtime, conversation_context=None) -> PatentQaExecutionResult:
            captured["question"] = question
            captured["conversation_context"] = conversation_context
            return PatentQaExecutionResult(
                success=True,
                final_answer="generated patent answer",
                metadata=PatentQaExecutionMetadata(route="kb_qa", query_mode="patent staged qa"),
                raw={"references": [], "reference_objects": [], "reference_links": [], "original_links": [], "metadata": {}, "steps": []},
            )

    payload = PatentGraphRagPayload(
        stage1_context_block="graph_mode: graph_for_rag",
        stage2_patent_candidates=("CN100355122C",),
        stage4_fact_block="- graph fact",
        stage4_graph_candidate_patent_ids=("CN100355122C",),
        cache_fingerprint="graph:attribute",
        diagnostics={"matched_rule": "material_attribute_graph_anchor", "strategy": "parametric"},
    )

    def _router(**kwargs):
        assert kwargs["question"] == "磷酸铁锂的电压是多少"
        return PatentGraphRoutingResult(
            mode="graph_for_rag",
            rag_payload=payload,
            diagnostics={"matched_rule": "material_attribute_graph_anchor", "strategy": "parametric"},
        )

    service = PatentKbService(
        orchestrator=_RecordingOrchestrator(),
        graph_kb_client=object(),
        graph_kb_enabled=True,
        graph_kb_v2_enabled=True,
        graph_kb_rag_injection_enabled=True,
        graph_kb_service_v2=_router,
    )

    execution_result = service.run(
        request=_make_request(question="磷酸铁锂的电压是多少"),
        runtime=_FakeStagedRuntime(),
        conversation_context={"recent_turns_for_llm": []},
    )

    assert captured["conversation_context"]["graph_kb"]["mode"] == "graph_for_rag"
    assert captured["conversation_context"]["graph_kb"]["cache_fingerprint"] == "graph:attribute"
    assert execution_result["answer_text"] == "generated patent answer"
    assert execution_result["metadata"]["graph_kb_mode"] == "graph_for_rag"
```

This test uses an injected router because the service unit should verify graph-for-RAG handoff, not Neo4j execution.
It may pass before the classifier implementation because the router is injected; that is acceptable. The classifier red/green coverage is in Task 2, while this test protects the service contract that `graph_for_rag` routes continue into staged generation with injected graph context.

- [ ] **Step 2: Run the service test**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_kb_service.py::test_kb_service_material_attribute_question_uses_graph_for_rag_injection -q
```

Expected: PASS if the existing service graph-for-RAG handoff is intact. If it fails, inspect whether the test imports already include `PatentQaExecutionResult`, `PatentQaExecutionMetadata`, `PatentGraphRagPayload`, and `PatentGraphRoutingResult`; add imports only if missing.

- [ ] **Step 3: Run adjacent service tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_kb_service.py -q
```

Expected: PASS.

---

### Task 4: Focused Regression Suite

**Files:**
- No additional production files expected.
- Tests from Tasks 1-3.

- [ ] **Step 1: Run focused patent graph and service tests**

Run:

```bash
PYTHONPATH=patent pytest \
  patent/tests/test_patent_graph_kb_slots.py \
  patent/tests/test_patent_graph_kb_classifier_v2.py \
  patent/tests/test_patent_kb_service.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run broader patent graph KB tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_*.py -q
```

Expected: PASS.

- [ ] **Step 3: Run a local classifier smoke check**

Run:

```bash
PYTHONPATH=patent python -c "from server.patent.graph_kb.classifier_v2 import classify_patent_graph_question_v2; qs=['磷酸铁锂的电压是多少','涉及磷酸铁锂的专利有哪些','涉及磷酸铁锂电压相关的专利有哪些']; [print(q, classify_patent_graph_question_v2(question=q, conversation_context={}).mode, classify_patent_graph_question_v2(question=q, conversation_context={}).diagnostics.get('matched_rule')) for q in qs]"
```

Expected output includes:

```text
磷酸铁锂的电压是多少 graph_for_rag material_attribute_graph_anchor
涉及磷酸铁锂的专利有哪些 direct_answer list_patents_by_material
涉及磷酸铁锂电压相关的专利有哪些 direct_answer list_patents_by_material
```

- [ ] **Step 4: Check worktree**

Run:

```bash
git status --short
```

Expected: only the intended code/test/doc files plus any pre-existing untracked local files.

---

## Implementation Notes

- Prefer classifier-level routing over suppressing `list_patents_by_material` candidates in `query_templates.py`, because graph candidates are still useful for RAG injection.
- Keep `route_family="hybrid"` for `material_attribute_graph_anchor` to reflect that graph output is evidence, not the final answer.
- Keep diagnostics explicit. The log should show:

```text
patent_graph.classify_done ... mode=graph_for_rag route_family=hybrid matched_rule=material_attribute_graph_anchor
patent_graph.route_end ... final_mode=graph_for_rag
```

- Do not change fastQA files.
- Do not change frontend code.
- Do not add LLM classification.
- Do not commit unless the user asks.

## Review Gate

After implementation and tests pass, request code review with a 5.5 high subagent using focused context:

- Spec: `docs/superpowers/specs/2026-05-01-patent-fastqa-like-attribute-routing-design.md`
- Plan: `docs/superpowers/plans/2026-05-01-patent-fastqa-like-attribute-routing-implementation.md`
- Diff: current uncommitted changes
- Review focus: routing precedence, regression risk for direct patent list/count, test coverage, and unintended fastQA/frontend changes.
