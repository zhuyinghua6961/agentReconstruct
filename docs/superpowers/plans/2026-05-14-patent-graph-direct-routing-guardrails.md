# Patent Graph Direct Routing Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent patent graph direct-answer routing from returning material/process patent lists for synthesis-oriented questions while preserving precise graph direct answers.

**Architecture:** Add conservative intent guardrails in the patent graph classifier before the generic direct-candidate fallback. Keep direct renderers and query templates unchanged except where tests prove a small helper is needed. Downgraded material/process questions should become `graph_for_rag` when graph constraints or hints are available.

**Tech Stack:** Python, pytest, FastAPI patent backend, Neo4j-backed patent graph query layer.

---

## Source Spec

- `docs/superpowers/specs/2026-05-14-patent-graph-direct-routing-guardrails-design.md`

## File Structure

- Modify `patent/server/patent/graph_kb/slots.py`
  - Responsible for deterministic question slot and intent signal extraction.
  - Add only small intent helpers or slot fields if needed for guardrails.
- Modify `patent/server/patent/graph_kb/classifier_v2.py`
  - Responsible for tri-state graph routing: `direct_answer`, `graph_for_rag`, `skip_graph`.
  - Add guardrail predicates and insert them before candidate direct fallback.
- Possibly modify `patent/server/patent/graph_kb/query_templates.py`
  - Only if needed to preserve graph-for-RAG constraints for downgraded material/process questions.
  - Do not add material/process count templates in this implementation unless tests show routing cannot be made safe without them.
- Test `patent/tests/test_patent_graph_kb_classifier_v2.py`
  - Primary unit test coverage for classifier decisions and diagnostics.
- Test `patent/tests/test_patent_graph_kb_service_v2.py`
  - Route-level coverage for graph preflight/direct-vs-RAG behavior.

No frontend, gateway, lifecycle script, Neo4j schema, ingestion, auth, or file-route files should be changed.

## Implementation Policy

Direct-answer is allowed only when the selected direct template faithfully represents every explicit constraint in the user question. If a material/process direct template would silently ignore a facet such as atmosphere, source role, condition, parameter, or count intent, route to `graph_for_rag`.

Intent tests may assert either new slot fields in `slots.py` or classifier helper behavior. Prefer classifier helper tests if the implementation can avoid expanding the public slot dataclass.

---

### Task 1: Add Classifier Tests For Misrouted Questions

**Files:**
- Modify: `patent/tests/test_patent_graph_kb_classifier_v2.py`

- [ ] **Step 1: Inspect existing classifier test patterns**

Run:

```bash
sed -n '1,260p' patent/tests/test_patent_graph_kb_classifier_v2.py
```

Expected: Identify current helper names, assertion style, and existing direct/RAG/skip cases.

- [ ] **Step 2: Update stale material/process count expectations**

Find existing tests that assert material/process count questions remain `direct_answer`, especially cases like:

```python
"磷酸铁锂相关专利数量是多少？"
"磷酸铁锂电压相关专利数量是多少？"
"磷酸铁锂电压相关申请数量是多少？"
"磷酸铁锂电压相关授权数量是多少？"
"磷酸铁锂电压相关公开数量是多少？"
```

Change those expectations so unsupported material/process count questions route to `graph_for_rag` with `matched_rule == "unsupported_material_process_count"` unless the question is applicant, inventor, agency, IPC, or another count template that actually exists.

Keep explicit list expectations separate from count expectations:

```python
list_decision = classify_patent_graph_question_v2(question="磷酸铁锂相关专利有哪些？")
assert list_decision.mode == "direct_answer"
assert list_decision.diagnostics["matched_rule"] == "list_patents_by_material"

count_decision = classify_patent_graph_question_v2(question="磷酸铁锂相关专利数量是多少？")
assert count_decision.mode == "graph_for_rag"
assert count_decision.diagnostics["matched_rule"] == "unsupported_material_process_count"
```

This removes contradictory test expectations before implementation.

- [ ] **Step 3: Add failing parametrized tests for synthesis questions**

Add a parametrized test that asserts these questions are not direct answers:

```python
@pytest.mark.parametrize(
    "question",
    [
        "磷酸铁锂固相合成法通常需要哪种保护气氛？",
        "磷酸铁锂的保护气氛是什么？",
        "磷酸铁锂固相法的保护气氛是什么？",
        "磷酸铁锂烧结通常用氮气还是空气？",
        "磷酸铁锂碳包覆通常需要什么气氛？",
        "磷酸铁锂保护气氛的作用是什么？",
        "磷酸铁锂是否需要在空气中烧结？",
        "磷酸铁锂烧结时可以用空气吗？",
        "磷酸铁锂烧结温度是多少？",
        "磷酸铁锂固相法烧结温度通常是多少？",
        "磷酸铁锂固相合成需要哪些原料？",
        "磷酸铁锂固相合成的原料配比是多少？",
        "烧结需要哪种气氛？",
        "碳包覆通常需要什么气氛？",
        "喷雾干燥通常需要什么气氛？",
        "碳包覆的作用是什么？",
        "喷雾干燥的作用是什么？",
        "涉及磷酸铁锂保护气氛的专利有哪些？",
        "磷酸铁锂相关专利数量是多少？",
    ],
)
def test_material_process_synthesis_questions_do_not_direct_answer(question):
    decision = classify_patent_graph_question_v2(question=question)

    assert decision.mode in {"graph_for_rag", "skip_graph"}
    assert decision.mode != "direct_answer"
```

If existing tests avoid broad parametrized assertions, split into focused groups:

- atmosphere/condition questions
- parameter/value questions
- material-input questions
- process-effect questions
- combined-facet list/count questions

- [ ] **Step 4: Add diagnostics expectations**

For questions with material/process/role candidates, assert `graph_for_rag` and a stable `matched_rule`:

```python
decision = classify_patent_graph_question_v2(question="磷酸铁锂固相合成法通常需要哪种保护气氛？")

assert decision.mode == "graph_for_rag"
assert decision.diagnostics["matched_rule"] == "material_process_synthesis_question"
```

For unsupported material/process count:

```python
decision = classify_patent_graph_question_v2(question="磷酸铁锂相关专利数量是多少？")

assert decision.mode == "graph_for_rag"
assert decision.diagnostics["matched_rule"] == "unsupported_material_process_count"
```

For combined facet listing:

```python
decision = classify_patent_graph_question_v2(question="涉及磷酸铁锂保护气氛的专利有哪些？")

assert decision.mode == "graph_for_rag"
assert decision.diagnostics["matched_rule"] == "combined_facet_listing_requires_rag"
```

- [ ] **Step 5: Add preservation tests for valid direct answers**

Assert these stay direct:

```python
@pytest.mark.parametrize(
    ("question", "matched_rule"),
    [
        ("CN100355122C 采用什么保护气氛？", "single_patent_atmosphere"),
        ("CN100355122C 的工艺步骤是什么？", "single_patent_process"),
        ("CN100355122C 的材料有哪些？", "single_patent_materials"),
        ("CN100355122C 的技术问题和技术方案是什么？", "single_patent_problem_solution"),
        ("宁德时代新能源科技股份有限公司有哪些专利？", "applicant_listing"),
        ("宁德时代新能源科技股份有限公司有多少专利？", "applicant_count"),
        ("H01M10 下有哪些专利？", "ipc_code_prefix_listing"),
        ("H01M10 下有多少专利？", "ipc_code_prefix_count"),
        ("磷酸铁锂相关专利有哪些？", "list_patents_by_material"),
        ("涉及烧结的专利有哪些？", "list_patents_by_process_term"),
        ("涉及碳源的专利有哪些？", "list_patents_by_material_role"),
        ("涉及main材料角色的专利有哪些？", "list_patents_by_material_role"),
    ],
)
def test_supported_precise_graph_questions_remain_direct(question, matched_rule):
    decision = classify_patent_graph_question_v2(question=question)

    assert decision.mode == "direct_answer"
    assert decision.diagnostics["matched_rule"] == matched_rule
```

- [ ] **Step 6: Run tests and verify they fail**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_classifier_v2.py -q
```

Expected: New misroute tests fail because current classifier still returns `direct_answer` for material/process synthesis cases.

---

### Task 2: Implement Conservative Material/Process Guardrails

**Files:**
- Modify: `patent/server/patent/graph_kb/classifier_v2.py`
- Possibly modify: `patent/server/patent/graph_kb/slots.py`

- [ ] **Step 1: Add private classifier helper predicates**

In `classifier_v2.py`, add private helpers near existing predicates:

```python
_SYNTHESIS_CONDITION_HINTS = (
    "通常",
    "一般",
    "常用",
    "常见",
    "优选",
    "推荐",
    "需要",
    "应",
    "是否",
    "能否",
    "可以",
    "用什么",
    "哪种",
    "什么气氛",
    "什么条件",
)
_SYNTHESIS_VALUE_HINTS = ("温度", "配比", "比例", "范围", "是多少")
_SYNTHESIS_EFFECT_HINTS = ("作用", "目的", "原因", "为什么")
_SYNTHESIS_FACET_HINTS = ("保护气氛", "烧结气氛", "原料", "锂源", "碳源")
```

Use existing slot flags where possible:

- `slots.asks_why_how`
- `slots.asks_attribute_value`
- `slots.asks_atmosphere`
- `slots.asks_process`
- `slots.asks_materials`
- `slots.material_terms`
- `slots.material_role_terms`
- `slots.process_terms`
- `slots.atmosphere_terms`

- [ ] **Step 2: Implement explicit candidate signal helper**

Add:

```python
def _has_material_process_candidate(slots: PatentGraphQuestionSlots) -> bool:
    return bool(
        slots.material_terms
        or slots.material_role_terms
        or slots.process_terms
        or slots.atmosphere_terms
    )
```

This helper is for broad non-ID material/process routing only. Do not use it to downgrade applicant, IPC, inventor, agency, or single-patent questions.

- [ ] **Step 3: Implement unsupported count helper**

Add:

```python
def _is_unsupported_material_process_count(slots: PatentGraphQuestionSlots) -> bool:
    return (
        bool(slots.asks_count)
        and _has_material_process_candidate(slots)
        and not slots.patent_ids
        and not slots.applicant_names
        and not slots.inventor_names
        and not slots.agency_names
        and not slots.ipc_full_codes
        and not slots.ipc_code_prefixes
        and not slots.ipc_prefixes
    )
```

This intentionally routes material/process count to RAG until count templates exist.

- [ ] **Step 4: Implement synthesis helper**

Add:

```python
def _is_material_process_synthesis_question(slots: PatentGraphQuestionSlots) -> bool:
    text = slots.normalized_question
    if not _has_material_process_candidate(slots):
        return False
    if slots.patent_ids:
        return False
    if _is_explicit_patent_listing_or_count(slots):
        return False
    if slots.asks_rank:
        return False
    return bool(
        slots.asks_atmosphere
        or slots.asks_attribute_value
        or slots.asks_why_how
        or any(hint in text for hint in _SYNTHESIS_CONDITION_HINTS)
        or any(hint in text for hint in _SYNTHESIS_VALUE_HINTS)
        or any(hint in text for hint in _SYNTHESIS_EFFECT_HINTS)
        or any(hint in text for hint in _SYNTHESIS_FACET_HINTS)
        or ("还是" in text and bool(slots.atmosphere_terms or slots.process_terms))
    )
```

Adjust exact condition names to match local style. Keep it conservative and easy to test.

- [ ] **Step 5: Implement combined-facet helper**

Add:

```python
def _is_combined_facet_listing_that_current_template_drops(slots: PatentGraphQuestionSlots) -> bool:
    if not _is_explicit_patent_listing_or_count(slots):
        return False
    if slots.patent_ids:
        return False
    candidate_paths = tuple(str(item.get("path_id") or "") for item in build_patent_template_candidates(slots, limit=20))
    primary_path = candidate_paths[0] if candidate_paths else ""

    # Count only constraints that the selected direct template would drop.
    # Do not count overlapping material/material-role extraction as two facets.
    dropped_constraints = 0
    if bool(slots.atmosphere_terms or slots.asks_atmosphere) and primary_path != "list_patent_atmospheres":
        dropped_constraints += 1
    if bool(slots.process_terms) and primary_path not in {"list_patents_by_process_term"}:
        dropped_constraints += 1
    if bool(slots.material_role_terms) and primary_path not in {"list_patents_by_material_role"}:
        dropped_constraints += 1
    material_role_terms = {str(item).strip().lower() for item in slots.material_role_terms}
    non_role_material_terms = tuple(
        item
        for item in slots.material_terms
        if str(item).strip().lower() not in material_role_terms
    )
    if bool(non_role_material_terms) and primary_path not in {"list_patents_by_material"}:
        dropped_constraints += 1
    return dropped_constraints > 0
```

This prevents `涉及磷酸铁锂保护气氛的专利有哪些？` from using only the material template. It must not downgrade explicit material-role list queries merely because terms such as `碳源` or `锂源` are extracted as both `material_terms` and `material_role_terms`; role terms covered by `list_patents_by_material_role` are considered satisfied, not dropped material constraints.

If the implementation can reuse the existing `candidates` tuple instead of rebuilding candidates, prefer passing it into the helper:

```python
def _is_combined_facet_listing_that_current_template_drops(
    slots: PatentGraphQuestionSlots,
    candidates: tuple[dict[str, Any], ...],
) -> bool:
    ...
```

- [ ] **Step 6: Insert guardrail branches in decision order**

In `classify_patent_graph_question_v2`, insert after existing material attribute / why-how / analytical relation handling and before `len(slots.patent_ids) == 1` or candidate fallback, matching the spec order:

```python
elif _is_unsupported_material_process_count(slots):
    decision = PatentGraphSemanticDecision(
        mode="graph_for_rag",
        route_family="hybrid",
        standalone=standalone,
        diagnostics=_diagnostics(slots, matched_rule="unsupported_material_process_count", candidates=candidates),
    )
elif _is_material_process_synthesis_question(slots):
    decision = PatentGraphSemanticDecision(
        mode="graph_for_rag",
        route_family="hybrid",
        standalone=standalone,
        diagnostics=_diagnostics(slots, matched_rule="material_process_synthesis_question", candidates=candidates),
    )
elif _is_combined_facet_listing_that_current_template_drops(slots):
    decision = PatentGraphSemanticDecision(
        mode="graph_for_rag",
        route_family="hybrid",
        standalone=standalone,
        diagnostics=_diagnostics(slots, matched_rule="combined_facet_listing_requires_rag", candidates=candidates),
    )
```

If a helper has no candidates and no useful graph signal, return `skip_graph` instead of `graph_for_rag`. The preferred behavior for all listed material/process failing examples is `graph_for_rag` when candidates exist.

- [ ] **Step 7: Run classifier tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_classifier_v2.py -q
```

Expected: All classifier tests pass.

---

### Task 3: Verify Graph-For-RAG Payload Preservation

**Files:**
- Modify: `patent/tests/test_patent_graph_kb_service_v2.py`
- Possibly modify: `patent/server/patent/graph_kb/query_templates.py`
- Possibly modify: `patent/server/patent/graph_kb/canonicalizer.py`

- [ ] **Step 1: Inspect existing route-level tests**

Run:

```bash
sed -n '1,340p' patent/tests/test_patent_graph_kb_service_v2.py
```

Expected: Identify fake Neo4j client patterns and current assertions for `direct_answer`, `graph_for_rag`, and metadata.

- [ ] **Step 2: Add runtime test for original failing query**

Add a test that calls `route_patent_graph_kb_v2` with a fake client that returns rows for `list_patents_by_material` and asserts the result is not direct:

```python
def test_original_atmosphere_question_routes_graph_for_rag(fake_neo4j_client):
    result = route_patent_graph_kb_v2(
        question="磷酸铁锂固相合成法通常需要哪种保护气氛？",
        conversation_context={},
        neo4j_client=fake_neo4j_client,
        max_rows=20,
        timeout_ms=3000,
        trace_id="test",
    )

    assert result.mode == "graph_for_rag"
    assert result.direct_result is None
    assert result.rag_payload is not None
    assert result.diagnostics["matched_rule"] == "material_process_synthesis_question"
```

Use the repository's existing fake client shape. Do not introduce network-dependent tests.

- [ ] **Step 3: Assert graph constraints or candidates survive**

For material question:

```python
constraints = list(result.rag_payload.stage2_constraints)
assert any(
    item.field == "material.name"
    and item.operator == "contains"
    and item.value == "磷酸铁锂"
    for item in constraints
)
```

For process question:

```python
result = route_patent_graph_kb_v2(
    question="烧结需要哪种气氛？",
    conversation_context={},
    neo4j_client=fake_neo4j_client,
    max_rows=20,
    timeout_ms=3000,
    trace_id="test",
)
assert result.mode == "graph_for_rag"
assert result.rag_payload is not None
```

If existing canonicalizer does not expose constraints when the graph query has no rows, adjust the minimal code path so selected candidate constraints are still available for `graph_for_rag`. Keep this change narrow.

- [ ] **Step 4: Add preservation test for direct single-patent atmosphere**

Use fake rows for `list_patent_atmospheres` and assert it still direct-renders:

```python
result = route_patent_graph_kb_v2(
    question="CN100355122C 采用什么保护气氛？",
    conversation_context={},
    neo4j_client=fake_neo4j_client,
    max_rows=20,
    timeout_ms=3000,
    trace_id="test",
)

assert result.mode == "direct_answer"
assert result.direct_result is not None
assert result.direct_result.template_id == "list_patent_atmospheres"
```

- [ ] **Step 5: Run runtime tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_service_v2.py -q
```

Expected: All runtime tests pass.

---

### Task 4: Run Focused Regression Suite

**Files:**
- No file changes expected.

- [ ] **Step 1: Run classifier tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_classifier_v2.py -q
```

Expected: Pass.

- [ ] **Step 2: Run graph route-level tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_graph_kb_service_v2.py -q
```

Expected: Pass.

- [ ] **Step 3: Run graph model/canonicalizer tests if present**

Run:

```bash
PYTHONPATH=patent pytest \
  patent/tests/test_patent_graph_kb_models.py \
  patent/tests/test_patent_graph_kb_canonicalizer.py \
  -q
```

Expected: Pass, or report if either file does not exist in this worktree.

- [ ] **Step 4: Run live health check without restarting services**

Run:

```bash
curl -sS http://127.0.0.1:8010/api/health
```

Expected JSON contains:

```json
{
  "success": true,
  "service": "patent",
  "status": "ok",
  "patent_graph_kb_enabled": true,
  "patent_graph_kb_ready": true
}
```

Do not run start or stop scripts.

---

### Task 5: Manual Live API Verification

**Files:**
- No file changes expected.

- [ ] **Step 1: Verify original failing query no longer graph-direct answers**

Run:

```bash
curl -sS -m 120 -X POST http://127.0.0.1:8010/api/ask \
  -H 'Content-Type: application/json' \
  --data '{
    "question": "磷酸铁锂固相合成法通常需要哪种保护气氛？",
    "conversation_id": null,
    "chat_history": [],
    "requested_mode": "patent",
    "actual_mode": "patent",
    "route": "kb_qa",
    "source_scope": "kb",
    "turn_mode": "kb_only",
    "kb_enabled": true,
    "allow_kb_verification": false,
    "used_files": [],
    "execution_files": [],
    "selected_file_ids": [],
    "primary_file_id": null,
    "file_selection": {},
    "trace_id": "manual-patent-graph-guardrail-001",
    "options": {}
  }' | jq '{
    success,
    query_mode,
    graph_mode: .metadata.graph_kb_mode,
    graph_path: .metadata.graph_kb_path_id,
    stage2_behavior: (.metadata.graph_kb_stage2_behavior // .metadata.graph_stage2_behavior),
    answer_prefix: (.final_answer | tostring | gsub("\\n"; " ") | .[0:180])
  }'
```

Expected:

- `query_mode` is not `patent_graph_kb` direct short-circuit, or `metadata.graph_kb_mode` is `graph_for_rag`.
- `answer_prefix` does not begin with `涉及材料`.
- If graph metadata is present, `graph_path` may still indicate a material/process graph anchor.

- [ ] **Step 2: Verify direct single-patent atmosphere still works**

Run the same payload shape with:

```json
"question": "CN100355122C 采用什么保护气氛？",
"trace_id": "manual-patent-graph-guardrail-002"
```

Expected:

```text
query_mode=patent_graph_kb
metadata.graph_kb_mode=direct_answer
metadata.graph_kb_path_id=list_patent_atmospheres
final_answer contains 专利 `CN100355122C` 的气氛条件包括
```

- [ ] **Step 3: Verify explicit material patent list still works**

Run the same payload shape with:

```json
"question": "磷酸铁锂相关专利有哪些？",
"trace_id": "manual-patent-graph-guardrail-003"
```

Expected:

```text
query_mode=patent_graph_kb
metadata.graph_kb_mode=direct_answer
metadata.graph_kb_path_id=list_patents_by_material
final_answer begins with 涉及材料
```

- [ ] **Step 4: Commit implementation**

After tests and manual verification pass:

```bash
git add \
  patent/server/patent/graph_kb/slots.py \
  patent/server/patent/graph_kb/classifier_v2.py \
  patent/server/patent/graph_kb/query_templates.py \
  patent/server/patent/graph_kb/canonicalizer.py \
  patent/tests/test_patent_graph_kb_classifier_v2.py \
  patent/tests/test_patent_graph_kb_service_v2.py
git commit -m "fix: guard patent graph direct routing"
```

Only include files actually changed. Do not commit unrelated worktree changes.

---

## Review Requirements During Implementation

After each implementation task:

1. Run the task-specific tests.
2. Request spec compliance review against this plan and the source spec.
3. Fix blocking review findings.
4. Request code quality review.
5. Fix critical or important findings before moving to the next task.

Before final handoff:

1. Run the full focused regression suite in Task 4.
2. Run the manual live API checks in Task 5 if local services are available.
3. Request final review of the complete diff.
