# fastQA Four-Route Graph QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the user-visible `precise` / `semantic` / `hybrid` / `community` knowledge graph QA experience inside refactored fastQA `kb_qa`, while preserving gateway file routing and generation-driven RAG fallback.

**Architecture:** Gateway remains the file-vs-KB router. fastQA `kb_qa` owns knowledge intent classification, graph planning, direct graph rendering, and graph-to-RAG evidence injection. The existing `direct_answer` / `graph_for_rag` / `skip_graph` tri-state remains the execution contract, with route-specific graph planners replacing unsafe generic query assumptions.

**Tech Stack:** Python 3.10, FastAPI, Neo4j Python driver via existing graph client, Chroma-backed generation-driven RAG, pytest.

---

## Spec And Scope References

Primary spec:

- `docs/graph/fastqa_four_route_graph_qa_spec.md`

Supporting observations:

- `docs/graph/fastqa_neo4j_schema_observations.md`
- `docs/graph/fastqa_graph_query_patterns.md`
- `docs/graph/fastqa_graph_routing_current_state.md`
- `docs/audit/知识图谱问答流程.md`

V1 scope reminder:

- `precise`: direct graph answer only for DOI and high-confidence simple list/count paths; numeric ranking can downgrade to graph-for-RAG unless parser-backed.
- `semantic`: Chroma/RAG primary; graph optional and skipped when no useful graph slots exist.
- `hybrid`: graph evidence seeds/enriches RAG.
- `community`: must no longer unconditional `skip_graph`; V1 must produce community graph evidence, with direct rendering only for simple list/profile routes if tests cover it.
- LLM-generated Cypher is out of scope for V1.

## V1 Route Matrix

This table is the implementation boundary. Do not expand V1 beyond it without updating the spec and tests.

| Route Family | Planner Intent | V1 Execution | Direct-Answer Eligible |
| --- | --- | --- | --- |
| `precise` | `lookup_by_doi` | direct answer | yes |
| `precise` | `expand_doi_context` | direct answer or graph-for-RAG | yes when DOI rows are valid |
| `precise` | `list_by_title_or_material` | direct answer or graph-for-RAG | yes for simple bounded lists |
| `precise` | `list_by_raw_material` | direct answer or graph-for-RAG | yes for simple bounded lists |
| `precise` | `list_by_carbon_source` | direct answer or graph-for-RAG | yes if rows have valid DOI/title evidence |
| `precise` | `list_by_process_method` | direct answer or graph-for-RAG | yes if rows are clean |
| `precise` | `count_by_structured_field` | direct answer or graph-for-RAG | yes for supported count fields |
| `precise` | `numeric_property_query` | graph-for-RAG by default | only after parser-backed test coverage |
| `semantic` | `semantic_optional_graph_hints` | skip graph or graph-for-RAG | no |
| `hybrid` | `structured_filter_plus_synthesis` | graph-for-RAG | no, unless reclassified as precise |
| `community` | `community_representatives` | graph-for-RAG; optional direct list/profile | yes only for simple list/profile |
| `community` | `community_mechanism_or_network` | graph-for-RAG | no |

## Metadata Alignment

Current metadata already includes some graph V2 keys. V1 should standardize these fields while preserving old keys during transition.

| Spec Field | Current/Target Source | Notes |
| --- | --- | --- |
| `graph_pipeline_version` | existing diagnostics | keep current key |
| `knowledge_route_family` | new canonical key from classifier | also keep `legacy_route_family` as compatibility alias |
| `tri_state_mode` | existing diagnostics | keep current key |
| `graph_strategy` | planner strategy | current key may be `strategy`; expose canonical key |
| `graph_intent` | planner intent | add to metadata |
| `graph_result_count` | execution/canonicalized rows | add to done metadata |
| `graph_confidence` | classifier/planner confidence | add if available |
| `graph_doi_candidates_count` | RAG payload/bundle | add count, not full list |
| `graph_filtered_doi_count` | canonicalizer | add after DOI validation |
| `graph_suspicious_doi_count` | canonicalizer | add after DOI validation |
| `graph_fallback_reason` | diagnostics | map existing fallback reasons |
| `graph_direct_answer_eligible` | planner/direct renderer | boolean |
| `graph_rag_injection_enabled` | settings | expose in metadata |
| `doi_source` | existing pipeline metadata | keep current behavior |

## File Structure

Expected production files to modify:

- `fastQA/app/core/config.py`
  - Add optional feature flags and defaults if needed.
- `fastQA/app/modules/graph_kb/models.py`
  - Add route family, slot, query path, parser, canonicalization, and metadata fields.
- `fastQA/app/modules/graph_kb/schema_registry.py`
  - Expand registry to observed field-bucket schema and allowlists.
- `fastQA/app/modules/graph_kb/classifier_v2.py`
  - Replace hard-coded shallow rules with slot-aware deterministic classifier.
- `fastQA/app/modules/graph_kb/query_strategy.py`
  - Select route-specific strategies from route family and slots.
- `fastQA/app/modules/graph_kb/planner_v2.py`
  - Replace generic candidate query dependence with route-specific prebuilt paths.
- `fastQA/app/modules/graph_kb/guardrail.py`
  - Support explicit V1 query shapes and reject unsafe dynamic relationship patterns.
- `fastQA/app/modules/graph_kb/canonicalizer.py`
  - Canonicalize DOI/entity/fact rows into bundle fields.
- `fastQA/app/modules/graph_kb/rag_adapter.py`
  - Build richer `GraphRagPayload` for Stage1/Stage2/Stage4.
- `fastQA/app/modules/graph_kb/direct_renderer.py`
  - Render safe direct answers for DOI/list/count/simple community summaries.
- `fastQA/app/modules/graph_kb/service.py`
  - Assemble diagnostics, confidence, direct downgrade, and metadata.
- `fastQA/app/routers/qa.py`
  - Merge standardized graph metadata into stream events.

Expected new production files:

- `fastQA/app/modules/graph_kb/slots.py`
  - Deterministic slot extraction for DOI, entities, fields, numeric operators, limits, and community signals.
- `fastQA/app/modules/graph_kb/value_parsers.py`
  - Numeric and structured field parsers.
- `fastQA/app/modules/graph_kb/community_labels.py`
  - Deterministic community label generation from representative rows.
- `fastQA/app/modules/graph_kb/doi_quality.py`
  - DOI validation and suspicious DOI filtering.
- `fastQA/app/modules/graph_kb/query_templates.py`
  - Centralized prebuilt V1 Cypher templates and expected row schemas.
- `fastQA/app/modules/graph_kb/metadata.py`
  - Metadata normalization and compatibility aliases.

Expected test files to modify or create:

- `fastQA/tests/test_graph_kb_models.py`
- `fastQA/tests/test_graph_kb_schema_registry.py`
- `fastQA/tests/test_graph_kb_classifier_v2.py`
- `fastQA/tests/test_graph_kb_planner_v2.py`
- `fastQA/tests/test_graph_kb_guardrail.py`
- `fastQA/tests/test_graph_kb_canonicalizer.py`
- `fastQA/tests/test_graph_kb_rag_adapter.py`
- `fastQA/tests/test_graph_kb_executor_v2.py`
- `fastQA/tests/test_graph_kb_service.py`
- `fastQA/tests/test_fastqa_kb_graph_integration.py`

Expected new test files:

- `fastQA/tests/test_graph_kb_slots.py`
- `fastQA/tests/test_graph_kb_value_parsers.py`
- `fastQA/tests/test_graph_kb_community_labels.py`
- `fastQA/tests/test_graph_kb_doi_quality.py`
- `fastQA/tests/test_graph_kb_query_templates.py`
- `fastQA/tests/test_graph_kb_metadata.py`

---

### Task 1: Lock Route Contract And Models

**Files:**
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Create: `fastQA/app/modules/graph_kb/metadata.py`
- Modify: `fastQA/app/modules/graph_kb/service.py`
- Modify: `fastQA/app/routers/qa.py`
- Test: `fastQA/tests/test_graph_kb_models.py`
- Test: `fastQA/tests/test_graph_kb_metadata.py`

- [ ] **Step 1: Write model tests for route family and tri-state contract**

Add tests asserting the canonical route families and execution modes:

```python
def test_graph_route_family_values_are_stable():
    assert GraphRouteFamily.PRECISE.value == "precise"
    assert GraphRouteFamily.SEMANTIC.value == "semantic"
    assert GraphRouteFamily.HYBRID.value == "hybrid"
    assert GraphRouteFamily.COMMUNITY.value == "community"


def test_graph_execution_mode_values_are_stable():
    assert GraphExecutionMode.DIRECT_ANSWER.value == "direct_answer"
    assert GraphExecutionMode.GRAPH_FOR_RAG.value == "graph_for_rag"
    assert GraphExecutionMode.SKIP_GRAPH.value == "skip_graph"
```

- [ ] **Step 2: Write metadata normalization tests**

Create `fastQA/tests/test_graph_kb_metadata.py`:

```python
from app.modules.graph_kb.metadata import build_graph_route_metadata


def test_metadata_exposes_canonical_and_compatibility_route_keys():
    metadata = build_graph_route_metadata(
        route_family="community",
        tri_state_mode="graph_for_rag",
        strategy="community_representatives",
        intent="community_representatives",
        result_count=3,
        rag_injection_enabled=True,
    )

    assert metadata["knowledge_route_family"] == "community"
    assert metadata["legacy_route_family"] == "community"
    assert metadata["tri_state_mode"] == "graph_for_rag"
    assert metadata["graph_strategy"] == "community_representatives"
    assert metadata["graph_intent"] == "community_representatives"
    assert metadata["graph_result_count"] == 3
    assert metadata["graph_rag_injection_enabled"] is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_models.py tests/test_graph_kb_metadata.py -q
```

Expected:

- fail because enums/model fields and `metadata.py` do not exist yet.

- [ ] **Step 4: Add route model fields**

In `models.py`, add or extend:

```python
from enum import Enum


class GraphRouteFamily(str, Enum):
    PRECISE = "precise"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    COMMUNITY = "community"


class GraphExecutionMode(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    GRAPH_FOR_RAG = "graph_for_rag"
    SKIP_GRAPH = "skip_graph"
```

Extend `SemanticDecision` without breaking existing tests:

```python
route_family: str = ""
confidence: float = 0.0
slots: dict[str, Any] = field(default_factory=dict)
direct_answer_eligible: bool = False
fallback_reason: str = ""
```

If `SemanticDecision` is a dataclass without `field` imported, import it from `dataclasses`.

- [ ] **Step 5: Implement metadata normalizer**

Create `metadata.py`:

```python
from __future__ import annotations

from typing import Any


def build_graph_route_metadata(
    *,
    route_family: str = "",
    tri_state_mode: str = "",
    strategy: str = "",
    intent: str = "",
    result_count: int | None = None,
    confidence: float | None = None,
    doi_candidates_count: int | None = None,
    filtered_doi_count: int | None = None,
    suspicious_doi_count: int | None = None,
    fallback_reason: str = "",
    direct_answer_eligible: bool | None = None,
    rag_injection_enabled: bool | None = None,
    doi_source: str = "none",
    graph_pipeline_version: str = "v2",
) -> dict[str, Any]:
    route = str(route_family or "").strip()
    payload: dict[str, Any] = {
        "graph_pipeline_version": str(graph_pipeline_version or "v2"),
        "knowledge_route_family": route,
        "legacy_route_family": route,
        "tri_state_mode": str(tri_state_mode or ""),
        "graph_strategy": str(strategy or ""),
        "graph_intent": str(intent or ""),
        "graph_fallback_reason": str(fallback_reason or ""),
        "doi_source": str(doi_source or "none"),
    }
    if result_count is not None:
        payload["graph_result_count"] = int(result_count)
    if confidence is not None:
        payload["graph_confidence"] = float(confidence)
    if doi_candidates_count is not None:
        payload["graph_doi_candidates_count"] = int(doi_candidates_count)
    if filtered_doi_count is not None:
        payload["graph_filtered_doi_count"] = int(filtered_doi_count)
    if suspicious_doi_count is not None:
        payload["graph_suspicious_doi_count"] = int(suspicious_doi_count)
    if direct_answer_eligible is not None:
        payload["graph_direct_answer_eligible"] = bool(direct_answer_eligible)
    if rag_injection_enabled is not None:
        payload["graph_rag_injection_enabled"] = bool(rag_injection_enabled)
    return payload
```

- [ ] **Step 6: Wire metadata helper into service/router**

In `service.py`, when building diagnostics in `route_graph_kb_v2`, include:

- `knowledge_route_family`,
- `graph_strategy`,
- `graph_intent`,
- `graph_confidence`,
- `graph_direct_answer_eligible`.

In `routers/qa.py`, update `_graph_v2_metadata(...)` to call `build_graph_route_metadata(...)` and preserve the existing keys returned today.

- [ ] **Step 7: Run focused tests**

Run:

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_models.py tests/test_graph_kb_metadata.py -q
```

Expected:

- pass.

- [ ] **Step 8: Run current graph service metadata tests**

Run:

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_service.py tests/test_fastqa_kb_graph_integration.py -q
```

Expected:

- pass or fail only on assertions that need compatibility metadata updates.

- [ ] **Step 9: Commit**

```bash
git add fastQA/app/modules/graph_kb/models.py fastQA/app/modules/graph_kb/metadata.py fastQA/app/modules/graph_kb/service.py fastQA/app/routers/qa.py fastQA/tests/test_graph_kb_models.py fastQA/tests/test_graph_kb_metadata.py fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py
git commit -m "feat: define graph route contract metadata"
```

---

### Task 2: Expand Schema Registry And Guardrail Allowlist

**Files:**
- Modify: `fastQA/app/modules/graph_kb/schema_registry.py`
- Modify: `fastQA/app/modules/graph_kb/guardrail.py`
- Test: `fastQA/tests/test_graph_kb_schema_registry.py`
- Test: `fastQA/tests/test_graph_kb_guardrail.py`

- [ ] **Step 1: Add schema registry tests for observed field-bucket schema**

In `test_graph_kb_schema_registry.py`, assert required fields:

```python
def test_registry_covers_v1_field_bucket_schema():
    registry = build_default_schema_registry()

    for field in [
        "paper.doi",
        "paper.title",
        "material.sample_name",
        "raw_material.name",
        "process.method",
        "process.calcination",
        "process.milling",
        "process.sintering",
        "process.drying",
        "recipe.carbon_source",
        "recipe.doping_elements",
        "performance.discharge_capacity_child",
        "performance.compaction_density",
        "community.id",
    ]:
        assert registry.get_field(field) is not None
```

- [ ] **Step 2: Add allowlist tests**

```python
def test_registry_allowlist_contains_v1_labels_and_relations():
    registry = build_default_schema_registry()

    for label in ["carbon_source", "calcination", "milling", "discharge_capacity", "compaction_density"]:
        assert label in registry.allowed_labels

    for rel in ["carbon_source", "calcination", "milling", "discharge_capacity", "key_process_parameters"]:
        assert rel in registry.allowed_relations
```

- [ ] **Step 3: Add guardrail tests for explicit V1 paths**

In `test_graph_kb_guardrail.py`, add:

```python
def test_guardrail_accepts_explicit_carbon_source_query():
    registry = build_default_schema_registry()
    cypher = (
        "MATCH (d:doi)-[:recipe]->(:recipe)-[:carbon_source]->(cs:carbon_source) "
        "WHERE toLower(cs.name) CONTAINS toLower($term) "
        "RETURN d.name AS doi, cs.name AS carbon_source LIMIT 20"
    )
    result = inspect_cypher(cypher, registry)
    assert result.allowed


def test_guardrail_rejects_unknown_dynamic_relationship_type():
    registry = build_default_schema_registry()
    cypher = (
        "MATCH (d:doi)-[:recipe]->(:recipe)-[r]->(v) "
        "WHERE type(r) IN ['carbon_source', 'evil_relation'] "
        "RETURN v.name AS value LIMIT 20"
    )
    result = inspect_cypher(cypher, registry)
    assert not result.allowed
```

- [ ] **Step 4: Run tests to verify failures**

Run:

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_schema_registry.py tests/test_graph_kb_guardrail.py -q
```

Expected:

- fail due missing fields/allowlist/dynamic `type(r)` handling.

- [ ] **Step 5: Expand `LogicalFieldSpec` only if needed**

If current `LogicalFieldSpec` cannot express property-only community fields, extend it conservatively:

```python
property_name: str = "name"
direct_answer_eligible: bool = False
rag_eligible: bool = True
```

Do not break existing callers. Provide defaults.

- [ ] **Step 6: Expand default registry**

Add the field families from the spec exactly. Keep existing field names as compatibility aliases where needed.

Example:

```python
"recipe.carbon_source": LogicalFieldSpec(
    logical_name="recipe.carbon_source",
    label="carbon_source",
    relation_path=("recipe", "carbon_source"),
    description="Carbon source values linked through the recipe bucket.",
),
"performance.discharge_capacity_child": LogicalFieldSpec(
    logical_name="performance.discharge_capacity_child",
    label="discharge_capacity",
    relation_path=("name", "discharge_capacity", "discharge_capacity"),
    value_kind="numeric_text",
    description="Useful child capacity values under placeholder capacity bucket nodes.",
),
"community.id": LogicalFieldSpec(
    logical_name="community.id",
    label="",
    property_name="louvainCommunityId",
    value_kind="integer",
    description="Louvain community identifier stored as a node property.",
),
```

- [ ] **Step 7: Update allowlists**

Add all V1 labels and relationships from the spec. Avoid adding empty ontology labels unless required for backward compatibility.

- [ ] **Step 8: Update guardrail dynamic relationship handling**

If `guardrail.py` parses relationship names from `type(r) IN [...]`, require every string literal in the list to be allowlisted.

If the parser cannot safely inspect that shape in V1, reject it and ensure query templates avoid it for direct-answer paths.

- [ ] **Step 9: Run focused tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_schema_registry.py tests/test_graph_kb_guardrail.py -q
```

Expected:

- pass.

- [ ] **Step 10: Run planner/executor tests for compatibility**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_planner_v2.py tests/test_graph_kb_executor_v2.py -q
```

Expected:

- pass, after updating assertions for expanded allowlists if needed.

- [ ] **Step 11: Commit**

```bash
git add fastQA/app/modules/graph_kb/schema_registry.py fastQA/app/modules/graph_kb/guardrail.py fastQA/tests/test_graph_kb_schema_registry.py fastQA/tests/test_graph_kb_guardrail.py fastQA/tests/test_graph_kb_planner_v2.py fastQA/tests/test_graph_kb_executor_v2.py
git commit -m "feat: expand graph schema registry for field buckets"
```

---

### Task 3: Add Slot Extraction And Classifier V2 Rewrite

**Files:**
- Create: `fastQA/app/modules/graph_kb/slots.py`
- Modify: `fastQA/app/modules/graph_kb/classifier_v2.py`
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Test: `fastQA/tests/test_graph_kb_slots.py`
- Test: `fastQA/tests/test_graph_kb_classifier_v2.py`

- [ ] **Step 1: Write slot extraction tests**

Create `test_graph_kb_slots.py`:

```python
from app.modules.graph_kb.slots import extract_graph_slots


def test_extracts_doi():
    slots = extract_graph_slots("10.1021/jp1005692 这篇文献是什么？")
    assert slots.doi == "10.1021/jp1005692"
    assert slots.doi_intent == "lookup"


def test_extracts_doi_expansion_intent():
    slots = extract_graph_slots("展开 10.1021/jp1005692 的测试、工艺和原料信息")
    assert slots.doi == "10.1021/jp1005692"
    assert slots.doi_intent == "expand"


def test_extracts_carbon_source_and_entity():
    slots = extract_graph_slots("列出使用蔗糖作为碳源的 LiFePO4 文献")
    assert "sucrose" in slots.recipe_terms["carbon_source"] or "蔗糖" in slots.recipe_terms["carbon_source"]
    assert "lifepo4" in slots.entities


def test_extracts_numeric_property_threshold():
    slots = extract_graph_slots("放电容量超过150 mAh/g的LFP有哪些特点？")
    assert slots.property_field == "discharge_capacity"
    assert slots.operator in {">", ">="}
    assert slots.threshold == 150
    assert slots.analysis_signal is True


def test_extracts_community_signal():
    slots = extract_graph_slots("LiFePO4的关系网络和机制关联是什么？")
    assert slots.community_signal is True
    assert "lifepo4" in slots.entities


def test_extracts_count_signal_for_structured_field():
    slots = extract_graph_slots("统计使用 sucrose 作为碳源的文献数量")
    assert slots.count_signal is True
    assert "sucrose" in slots.recipe_terms["carbon_source"]
```

- [ ] **Step 2: Write classifier examples from spec**

Update `test_graph_kb_classifier_v2.py`:

```python
def test_classifier_routes_community_to_graph_for_rag_not_skip():
    decision = classify_graph_question_v2(question="LiFePO4的关系网络和机制关联是什么？", conversation_context={})
    assert decision.legacy_route == "community"
    assert decision.mode == "graph_for_rag"


def test_classifier_routes_hybrid_capacity_analysis():
    decision = classify_graph_question_v2(question="放电容量超过150 mAh/g的LFP有哪些特点？", conversation_context={})
    assert decision.legacy_route == "hybrid"
    assert decision.mode == "graph_for_rag"


def test_classifier_routes_semantic_without_graph_slots_to_skip():
    decision = classify_graph_question_v2(question="为什么电池安全性很重要？", conversation_context={})
    assert decision.legacy_route == "semantic"
    assert decision.mode == "skip_graph"
```

- [ ] **Step 3: Run tests to verify failures**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_slots.py tests/test_graph_kb_classifier_v2.py -q
```

Expected:

- fail because slot extractor does not exist and community currently skips graph.

- [ ] **Step 4: Implement `GraphQuestionSlots`**

In `models.py` or `slots.py`, define a focused dataclass:

```python
@dataclass(frozen=True)
class GraphQuestionSlots:
    doi: str = ""
    doi_intent: str = ""  # "lookup" | "expand"
    entities: tuple[str, ...] = ()
    title_terms: tuple[str, ...] = ()
    material_terms: tuple[str, ...] = ()
    raw_material_terms: tuple[str, ...] = ()
    recipe_terms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    process_terms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    property_field: str = ""
    operator: str = ""
    threshold: float | None = None
    unit: str = ""
    ranking: str = ""
    limit: int | None = None
    community_signal: bool = False
    analysis_signal: bool = False
    enumeration_signal: bool = False
    count_signal: bool = False
```

- [ ] **Step 5: Implement deterministic slot extraction**

In `slots.py`, implement:

- DOI regex,
- DOI expansion keywords:
  - `展开`,
  - `上下文`,
  - `测试`,
  - `工艺`,
  - `原料`,
  - `配方`,
  - `设备`,
  - `context`,
  - `expand`,
- entity alias normalization,
- property keyword map,
- numeric operator map,
- carbon source keyword mapping:
  - `蔗糖` -> `sucrose`,
  - keep original Chinese term too if useful,
- community keyword detection,
- analysis keyword detection,
- enumeration/list/count/top-k detection.

Keep it deterministic and small.

- [ ] **Step 6: Rewrite classifier mapping**

In `classifier_v2.py`:

- call `extract_graph_slots(question)`,
- use route priority from spec:
  1. DOI,
  2. file context override,
  3. community,
  4. hybrid,
  5. precise,
  6. semantic,
  7. default semantic.

Mapping requirements:

```python
if slots.doi and slots.doi_intent == "expand":
    legacy_route = "precise"
    mode = "direct_answer" if expansion_direct_template_exists else "graph_for_rag"
elif slots.doi and legacy_template_exists:
    legacy_route = "precise"
    mode = "direct_answer"
elif slots.doi:
    legacy_route = "precise"
    mode = "graph_for_rag"
elif slots.community_signal:
    legacy_route = "community"
    mode = "graph_for_rag"
elif slots.property_field and slots.analysis_signal:
    legacy_route = "hybrid"
    mode = "graph_for_rag"
elif slots.enumeration_signal or slots.property_field or slots.recipe_terms or slots.process_terms:
    legacy_route = "precise"
    mode = "graph_for_rag"
elif semantic_keywords and not useful_graph_slots:
    legacy_route = "semantic"
    mode = "skip_graph"
```

Keep file-context override behavior: no direct answer if file context is present.

- [ ] **Step 7: Run focused tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_slots.py tests/test_graph_kb_classifier_v2.py -q
```

Expected:

- pass.

- [ ] **Step 8: Run planner tests for classifier compatibility**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_planner_v2.py -q
```

Expected:

- pass after updating expected community behavior from `skip_graph` to graph-for-RAG.

- [ ] **Step 9: Commit**

```bash
git add fastQA/app/modules/graph_kb/slots.py fastQA/app/modules/graph_kb/classifier_v2.py fastQA/app/modules/graph_kb/models.py fastQA/tests/test_graph_kb_slots.py fastQA/tests/test_graph_kb_classifier_v2.py fastQA/tests/test_graph_kb_planner_v2.py
git commit -m "feat: classify graph kb four route intents"
```

---

### Task 4: Add DOI Quality And Value Parsers

**Files:**
- Create: `fastQA/app/modules/graph_kb/doi_quality.py`
- Create: `fastQA/app/modules/graph_kb/value_parsers.py`
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Test: `fastQA/tests/test_graph_kb_doi_quality.py`
- Test: `fastQA/tests/test_graph_kb_value_parsers.py`

- [ ] **Step 1: Write DOI quality tests**

```python
from app.modules.graph_kb.doi_quality import classify_doi_quality


def test_valid_doi():
    result = classify_doi_quality("10.1021/jp1005692")
    assert result.status == "valid"


def test_truncated_doi_is_suspicious():
    result = classify_doi_quality("10.1007/s12598-")
    assert result.status == "suspicious"


def test_glued_doi_is_suspicious_or_invalid():
    result = classify_doi_quality("10.1039/d2nj04292dReceived")
    assert result.status in {"suspicious", "invalid"}
```

- [ ] **Step 2: Write parser tests**

```python
from app.modules.graph_kb.value_parsers import parse_capacity, parse_density, parse_conductivity, parse_retention


def test_parse_density_g_cm3():
    parsed = parse_density("3.19 g/cm³ at 250 MPa loading")
    assert parsed.value == 3.19
    assert parsed.unit == "g/cm3"
    assert parsed.confidence >= 0.8


def test_parse_capacity_with_rate_prefix():
    parsed = parse_capacity("0.5C_initial_141.2 mA h g⁻¹")
    assert parsed.value == 141.2
    assert parsed.unit == "mAh/g"
    assert parsed.context["rate"] == "0.5C"


def test_parse_retention_cycles():
    parsed = parse_retention("98.5% capacity retention after 500 cycles at 0.2 A g⁻¹")
    assert parsed.value == 98.5
    assert parsed.context["cycles"] == 500


def test_parse_placeholder_has_low_confidence():
    parsed = parse_capacity("discharge_capacity1_10.1021/jp1005692")
    assert parsed.value is None
    assert parsed.confidence == 0
```

- [ ] **Step 3: Run tests to verify failures**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_doi_quality.py tests/test_graph_kb_value_parsers.py -q
```

Expected:

- fail because modules do not exist.

- [ ] **Step 4: Add parser result models**

In `models.py` or `value_parsers.py`:

```python
@dataclass(frozen=True)
class ParsedGraphValue:
    original: str
    value: float | None = None
    unit: str = ""
    confidence: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
```

For DOI:

```python
@dataclass(frozen=True)
class DoiQuality:
    doi: str
    status: str
    reason: str = ""
```

- [ ] **Step 5: Implement DOI quality**

Rules:

- strict DOI regex starts with `10.` and has a slash,
- flag known truncated prefixes,
- flag glued suffixes like `Received`, `Cite`, `Journal` as suspicious,
- empty or no slash -> invalid.

- [ ] **Step 6: Implement parsers**

Keep parsers regex-based and conservative.

Normalize superscript units:

- `cm⁻³`, `cm−3`, `cm-3` -> `cm3` denominator notation where applicable.
- `mA h g⁻¹`, `mAh g-1`, `mAh/g` -> `mAh/g`.

Do not parse when value is placeholder-only.

- [ ] **Step 7: Run focused tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_doi_quality.py tests/test_graph_kb_value_parsers.py -q
```

Expected:

- pass.

- [ ] **Step 8: Commit**

```bash
git add fastQA/app/modules/graph_kb/doi_quality.py fastQA/app/modules/graph_kb/value_parsers.py fastQA/app/modules/graph_kb/models.py fastQA/tests/test_graph_kb_doi_quality.py fastQA/tests/test_graph_kb_value_parsers.py
git commit -m "feat: add graph doi quality and value parsers"
```

---

### Task 5: Centralize V1 Query Templates

**Files:**
- Create: `fastQA/app/modules/graph_kb/query_templates.py`
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Test: `fastQA/tests/test_graph_kb_query_templates.py`
- Test: `fastQA/tests/test_graph_kb_guardrail.py`

- [ ] **Step 1: Write query template tests**

```python
from app.modules.graph_kb.query_templates import build_v1_query_paths


def test_carbon_source_template_uses_explicit_path():
    paths = build_v1_query_paths(intent="list_by_carbon_source", slots={"carbon_source_terms": ("sucrose",)}, limit=20)
    assert paths
    cypher = paths[0].cypher
    assert "[:recipe]" in cypher
    assert "[:carbon_source]" in cypher
    assert "type(r)" not in cypher


def test_capacity_template_uses_two_hop_child_path():
    paths = build_v1_query_paths(intent="numeric_property_query", slots={"property_field": "discharge_capacity", "title_terms": ("lifepo4",)}, limit=20)
    cypher = " ".join(path.cypher for path in paths)
    assert "[:discharge_capacity]->" in cypher


def test_count_template_uses_structured_field_path():
    paths = build_v1_query_paths(intent="count_by_structured_field", slots={"field": "recipe.carbon_source", "carbon_source_terms": ("sucrose",)}, limit=20)
    assert paths
    cypher = paths[0].cypher
    assert "count(DISTINCT d)" in cypher
    assert "[:carbon_source]" in cypher


def test_doi_expansion_template_is_distinct_from_lookup():
    lookup = build_v1_query_paths(intent="lookup_by_doi", slots={"doi": "10.1021/jp1005692"}, limit=20)
    expansion = build_v1_query_paths(intent="expand_doi_context", slots={"doi": "10.1021/jp1005692"}, limit=20)
    assert lookup[0].path_id != expansion[0].path_id
    assert "title" in lookup[0].cypher
    assert "bucket" in expansion[0].cypher or "value" in expansion[0].cypher
```

- [ ] **Step 2: Write guardrail compatibility test**

```python
def test_all_v1_templates_pass_guardrail():
    registry = build_default_schema_registry()
    for intent, slots in SAMPLE_TEMPLATE_CASES:
        for path in build_v1_query_paths(intent=intent, slots=slots, limit=20):
            assert inspect_cypher(path.cypher, registry).allowed, path.path_id
```

- [ ] **Step 3: Run tests to verify failures**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_query_templates.py tests/test_graph_kb_guardrail.py -q
```

Expected:

- fail because `query_templates.py` does not exist.

- [ ] **Step 4: Add query path model if needed**

In `models.py`:

```python
@dataclass(frozen=True)
class GraphQueryPath:
    path_id: str
    cypher: str
    params: dict[str, Any] = field(default_factory=dict)
    expected_columns: tuple[str, ...] = ()
    direct_answer_eligible: bool = False
```

If `GraphQueryPlanV2` already accepts dict candidate paths, either keep dict compatibility or add conversion in planner.

- [ ] **Step 5: Implement V1 templates**

Create explicit templates for:

- `lookup_by_doi`,
- `expand_doi_context`,
- `list_by_title_or_material`,
- `list_by_raw_material`,
- `list_by_carbon_source`,
- `list_by_process_method`,
- `count_by_structured_field`,
- `numeric_property_query` for:
  - `compaction_density`,
  - `tap_density`,
  - `discharge_capacity` two-hop child,
  - `cycling_stability`,
  - `conductivity`,
- `community_find_by_term`,
- `community_representative_titles`,
- `community_representative_methods`,
- `community_profile`.

Template rule:

- filters must be placed before later `OPTIONAL MATCH` or after `WITH`,
- every query has `LIMIT $limit` or literal `LIMIT`,
- user values are params only.

- [ ] **Step 6: Run focused tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_query_templates.py tests/test_graph_kb_guardrail.py -q
```

Expected:

- pass.

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/modules/graph_kb/query_templates.py fastQA/app/modules/graph_kb/models.py fastQA/tests/test_graph_kb_query_templates.py fastQA/tests/test_graph_kb_guardrail.py
git commit -m "feat: add explicit graph query templates"
```

---

### Task 6: Rewrite Planner V2 Around Route-Specific Paths

**Files:**
- Modify: `fastQA/app/modules/graph_kb/planner_v2.py`
- Modify: `fastQA/app/modules/graph_kb/query_strategy.py`
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Test: `fastQA/tests/test_graph_kb_planner_v2.py`

- [ ] **Step 1: Write planner tests for route matrix**

Add cases:

```python
def test_planner_precise_carbon_source_uses_carbon_source_path():
    decision = classify_graph_question_v2(question="列出使用蔗糖作为碳源的文献", conversation_context={})
    plan = build_graph_query_plan_v2(question="列出使用蔗糖作为碳源的文献", decision=decision, schema_registry=build_default_schema_registry())
    assert plan.intent == "list_by_carbon_source"
    assert any(path["path_id"] == "recipe.carbon_source" for path in plan.parametric_slots["candidate_queries"])


def test_planner_precise_count_uses_count_intent():
    question = "统计使用 sucrose 作为碳源的文献数量"
    decision = classify_graph_question_v2(question=question, conversation_context={})
    plan = build_graph_query_plan_v2(question=question, decision=decision, schema_registry=build_default_schema_registry())
    assert plan.intent == "count_by_structured_field"
    assert any(path["path_id"].endswith(".count") for path in plan.parametric_slots["candidate_queries"])


def test_planner_distinguishes_doi_lookup_and_expansion():
    lookup_q = "10.1021/jp1005692 这篇文献是什么？"
    expand_q = "展开 10.1021/jp1005692 的测试、工艺和原料信息"
    lookup_decision = classify_graph_question_v2(question=lookup_q, conversation_context={})
    expand_decision = classify_graph_question_v2(question=expand_q, conversation_context={})
    lookup_plan = build_graph_query_plan_v2(question=lookup_q, decision=lookup_decision, schema_registry=build_default_schema_registry())
    expand_plan = build_graph_query_plan_v2(question=expand_q, decision=expand_decision, schema_registry=build_default_schema_registry())
    assert lookup_plan.intent == "lookup_by_doi"
    assert expand_plan.intent == "expand_doi_context"


def test_planner_community_has_community_paths():
    decision = classify_graph_question_v2(question="LiFePO4的关系网络和机制关联是什么？", conversation_context={})
    plan = build_graph_query_plan_v2(question="LiFePO4的关系网络和机制关联是什么？", decision=decision, schema_registry=build_default_schema_registry())
    assert plan.intent.startswith("community")
    assert any("community" in path["path_id"] for path in plan.parametric_slots["candidate_queries"])


def test_semantic_no_graph_slots_returns_none():
    decision = classify_graph_question_v2(question="为什么电池安全性很重要？", conversation_context={})
    plan = build_graph_query_plan_v2(question="为什么电池安全性很重要？", decision=decision, schema_registry=build_default_schema_registry())
    assert plan is None
```

- [ ] **Step 2: Run planner tests to verify failures**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_planner_v2.py -q
```

Expected:

- fail because planner still returns generic `schema.primary/schema.support`.

- [ ] **Step 3: Update strategy selection**

In `query_strategy.py`:

- `template` only for proven legacy templates such as DOI lookup.
- `route_template` for V1 explicit query templates.
- `none` for `skip_graph`.

Avoid using `llm_cypher` as the default production strategy for V1.

- [ ] **Step 4: Update planner intent mapping**

In `planner_v2.py`, map slots to intents:

```python
if slots.doi:
    intent = "expand_doi_context" if slots.doi_intent == "expand" else "lookup_by_doi"
elif route == "community":
    intent = "community_representatives"
elif route == "hybrid":
    intent = "structured_filter_plus_synthesis"
elif slots.count_signal:
    intent = "count_by_structured_field"
elif slots.recipe_terms.get("carbon_source"):
    intent = "list_by_carbon_source"
elif slots.process_terms.get("method"):
    intent = "list_by_process_method"
elif slots.property_field:
    intent = "numeric_property_query"
else:
    intent = "list_by_title_or_material"
```

- [ ] **Step 5: Convert `GraphQueryPath` to executor-compatible dicts**

If executor expects dictionaries:

```python
{"path_id": path.path_id, "cypher": path.cypher, "params": path.params}
```

Keep expected columns and direct eligibility in `parametric_slots` diagnostics.

- [ ] **Step 6: Run focused planner tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_planner_v2.py -q
```

Expected:

- pass.

- [ ] **Step 7: Run executor tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_executor_v2.py -q
```

Expected:

- pass; adjust tests if path structure changed while preserving executor behavior.

- [ ] **Step 8: Commit**

```bash
git add fastQA/app/modules/graph_kb/planner_v2.py fastQA/app/modules/graph_kb/query_strategy.py fastQA/app/modules/graph_kb/models.py fastQA/tests/test_graph_kb_planner_v2.py fastQA/tests/test_graph_kb_executor_v2.py
git commit -m "feat: plan graph queries by route intent"
```

---

### Task 7: Canonicalize Graph Rows For Direct Rendering And RAG

**Files:**
- Create: `fastQA/app/modules/graph_kb/community_labels.py`
- Modify: `fastQA/app/modules/graph_kb/canonicalizer.py`
- Modify: `fastQA/app/modules/graph_kb/rag_adapter.py`
- Modify: `fastQA/app/modules/graph_kb/models.py`
- Test: `fastQA/tests/test_graph_kb_community_labels.py`
- Test: `fastQA/tests/test_graph_kb_canonicalizer.py`
- Test: `fastQA/tests/test_graph_kb_rag_adapter.py`

- [ ] **Step 1: Write canonicalizer tests**

```python
def test_canonicalizer_filters_suspicious_dois_for_direct_rendering():
    plan = GraphQueryPlanV2(strategy="route_template", intent="list_by_carbon_source")
    rows = [
        {"doi": "10.1021/jp1005692", "title": "Valid", "carbon_source": "sucrose"},
        {"doi": "10.1007/s12598-", "title": "Suspicious", "carbon_source": "sucrose"},
    ]
    bundle = canonicalize_graph_rows(plan=plan, rows=rows)
    assert "10.1021/jp1005692" in bundle.doi_candidates
    assert "10.1007/s12598-" not in bundle.direct_render_dois
    assert bundle.diagnostics["suspicious_doi_count"] == 1


def test_canonicalizer_preserves_original_capacity_text_and_parse_result():
    plan = GraphQueryPlanV2(strategy="route_template", intent="numeric_property_query")
    rows = [{"doi": "10.1/test", "capacity": "0.5C_initial_141.2 mA h g⁻¹"}]
    bundle = canonicalize_graph_rows(plan=plan, rows=rows)
    assert "141.2" in bundle.facts[0]
    assert bundle.render_slots["rows"][0]["original_value"] == "0.5C_initial_141.2 mA h g⁻¹"


def test_canonicalizer_maps_count_row_to_render_slots():
    plan = GraphQueryPlanV2(strategy="route_template", intent="count_by_structured_field")
    rows = [{"count": 69, "field_label": "carbon_source", "term": "sucrose"}]
    bundle = canonicalize_graph_rows(plan=plan, rows=rows)
    assert bundle.render_slots["count"] == 69
    assert bundle.render_slots["field_label"] == "carbon_source"
    assert bundle.render_slots["term"] == "sucrose"
    assert bundle.render_slots["direct_answerable"] is True
```

- [ ] **Step 2: Write deterministic community label tests**

Create `fastQA/tests/test_graph_kb_community_labels.py`:

```python
from app.modules.graph_kb.community_labels import build_community_label


def test_builds_label_from_title_and_method_without_raw_id():
    label = build_community_label(
        community_id=585242,
        titles=("High performance LiFePO4 cathode material",),
        materials=("LiFePO4/C",),
        methods=("LiFePO4 solvothermal synthesis",),
    )

    assert "LiFePO4" in label
    assert "solvothermal" in label.lower() or "synthesis" in label.lower()
    assert "585242" not in label


def test_builds_generic_label_when_representatives_are_sparse():
    label = build_community_label(community_id=1, titles=(), materials=(), methods=())
    assert label
    assert "1" not in label
```

- [ ] **Step 3: Write RAG adapter tests**

```python
def test_rag_adapter_includes_route_specific_entity_hints():
    bundle = GraphEvidenceBundle(
        doi_candidates=("10.1021/jp1005692",),
        facts=("carbon_source=sucrose doi=10.1021/jp1005692",),
        render_slots={"rows": [{"carbon_source": "sucrose", "title": "A title"}]},
    )
    payload = build_graph_rag_payload(decision=decision, plan=plan, bundle=bundle)
    assert "sucrose" in payload.stage2_entity_hints["carbon_sources"]
    assert "carbon_source" in payload.stage4_fact_block
```

- [ ] **Step 4: Run tests to verify failures**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_community_labels.py tests/test_graph_kb_canonicalizer.py tests/test_graph_kb_rag_adapter.py -q
```

Expected:

- fail due missing diagnostics/render slots/entity buckets.

- [ ] **Step 5: Extend evidence bundle model**

Add optional fields:

```python
direct_render_dois: tuple[str, ...] = ()
diagnostics: dict[str, Any] = field(default_factory=dict)
entity_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
```

Keep existing fields backward compatible.

- [ ] **Step 6: Implement deterministic community labels**

Create `community_labels.py`:

```python
def build_community_label(
    *,
    community_id: int | str | None,
    titles: tuple[str, ...] = (),
    materials: tuple[str, ...] = (),
    methods: tuple[str, ...] = (),
) -> str:
    ...
```

Rules:

- prefer recognizable material/entity tokens from materials and titles;
- add method/process token when available;
- fallback to a neutral label such as `related literature cluster`;
- never include raw `community_id` in the returned user-facing label.

- [ ] **Step 7: Implement canonical row handlers**

In `canonicalizer.py`, handle intents:

- DOI lookup,
- list by carbon source,
- list by process method,
- count by structured field,
- numeric property,
- community representatives.

Rules:

- validate DOI quality,
- build `doi_candidates` for RAG from valid and optionally suspicious candidates based on policy,
- build `direct_render_dois` from valid only,
- build `facts` compactly,
- fill `render_slots["rows"]` with canonical rows.
- for `count_by_structured_field`, map the first row's `count`, `field_label`, and `term` into `render_slots`; set `render_slots["direct_answerable"] = True` only for supported field labels;
- for community rows, collect representative titles/materials/methods and call `build_community_label(...)`;
- store the resulting label in `bundle.entity_hints["community_labels"]` and `render_slots["community_label"]`.

- [ ] **Step 8: Update RAG adapter entity hints**

Collect:

- `materials`,
- `titles`,
- `raw_materials`,
- `carbon_sources`,
- `process_methods`,
- `performance_fields`,
- `community_labels`.

Cap each list to a small number, e.g. 5.

- [ ] **Step 9: Run focused tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_community_labels.py tests/test_graph_kb_canonicalizer.py tests/test_graph_kb_rag_adapter.py -q
```

Expected:

- pass.

- [ ] **Step 10: Commit**

```bash
git add fastQA/app/modules/graph_kb/community_labels.py fastQA/app/modules/graph_kb/canonicalizer.py fastQA/app/modules/graph_kb/rag_adapter.py fastQA/app/modules/graph_kb/models.py fastQA/tests/test_graph_kb_community_labels.py fastQA/tests/test_graph_kb_canonicalizer.py fastQA/tests/test_graph_kb_rag_adapter.py
git commit -m "feat: canonicalize graph evidence for rag"
```

---

### Task 8: Direct Renderer For Safe Precise And Simple Community Answers

**Files:**
- Modify: `fastQA/app/modules/graph_kb/direct_renderer.py`
- Modify: `fastQA/app/modules/graph_kb/service.py`
- Test: `fastQA/tests/test_graph_kb_direct_renderer.py` if present, otherwise create it.
- Test: `fastQA/tests/test_graph_kb_service.py`

- [ ] **Step 1: Write renderer tests**

Create or update `test_graph_kb_direct_renderer.py`:

```python
def test_renders_carbon_source_list_direct_answer():
    decision = SemanticDecision(mode="direct_answer", legacy_route="precise")
    plan = GraphQueryPlanV2(strategy="route_template", intent="list_by_carbon_source")
    bundle = GraphEvidenceBundle(
        doi_candidates=("10.1021/jp1005692",),
        direct_render_dois=("10.1021/jp1005692",),
        render_slots={
            "rows": [
                {
                    "doi": "10.1021/jp1005692",
                    "title": "Example title",
                    "carbon_source": "sucrose",
                }
            ]
        },
    )
    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)
    assert result.handled
    assert "sucrose" in result.answer
    assert "10.1021/jp1005692" in result.answer


def test_numeric_without_parser_confidence_downgrades():
    decision = SemanticDecision(mode="direct_answer", legacy_route="precise")
    plan = GraphQueryPlanV2(strategy="route_template", intent="numeric_property_query")
    bundle = GraphEvidenceBundle(render_slots={"rows": [{"original_value": "unknown"}]})
    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)
    assert not result.handled
    assert result.metadata["reason"] == "direct_renderer_unavailable"


def test_renders_count_direct_answer():
    decision = SemanticDecision(mode="direct_answer", legacy_route="precise")
    plan = GraphQueryPlanV2(strategy="route_template", intent="count_by_structured_field")
    bundle = GraphEvidenceBundle(render_slots={"count": 69, "field_label": "carbon_source", "term": "sucrose"})
    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)
    assert result.handled
    assert "69" in result.answer


def test_community_direct_answer_uses_label_not_raw_id():
    decision = SemanticDecision(mode="direct_answer", legacy_route="community")
    plan = GraphQueryPlanV2(strategy="route_template", intent="community_representatives")
    bundle = GraphEvidenceBundle(
        render_slots={
            "community_label": "LiFePO4 solvothermal synthesis cluster",
            "community_id": 585242,
            "rows": [{"doi": "10.1039/c4ra15767b", "title": "High performance LiFePO4 cathode"}],
        }
    )
    result = render_direct_answer(decision=decision, plan=plan, bundle=bundle)
    assert result.handled
    assert "LiFePO4" in result.answer
    assert "585242" not in result.answer
```

- [ ] **Step 2: Run tests to verify failures**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_direct_renderer.py tests/test_graph_kb_service.py -q
```

Expected:

- fail due renderer support gaps.

- [ ] **Step 3: Implement direct renderers**

Support:

- `lookup_by_doi`,
- `expand_doi_context`,
- `list_by_carbon_source`,
- `list_by_process_method`,
- `list_by_raw_material`,
- `count_by_structured_field`,
- simple `community_representatives` only when rows have titles/materials/methods and no mechanism synthesis requirement.

Do not direct-render:

- semantic,
- hybrid,
- community mechanism/network explanations,
- numeric ranking without parser confidence.

- [ ] **Step 4: Update service direct downgrade behavior**

In `service.py`, if `decision.mode == "direct_answer"` but renderer returns not handled:

- set `direct_fallback_reason`,
- set `legacy_template_fallback_used` only for true legacy fallback, not every renderer downgrade,
- return graph-for-RAG with payload.

- [ ] **Step 5: Run focused tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_direct_renderer.py tests/test_graph_kb_service.py -q
```

Expected:

- pass.

- [ ] **Step 6: Commit**

```bash
git add fastQA/app/modules/graph_kb/direct_renderer.py fastQA/app/modules/graph_kb/service.py fastQA/tests/test_graph_kb_direct_renderer.py fastQA/tests/test_graph_kb_service.py
git commit -m "feat: render safe graph direct answers"
```

---

### Task 9: Integrate Graph Evidence Through KB Route And Streams

**Files:**
- Modify: `fastQA/app/modules/graph_kb/service.py`
- Modify: `fastQA/app/routers/qa.py`
- Modify: `fastQA/app/modules/qa_kb/stages/planning.py`
- Modify: `fastQA/app/modules/qa_kb/stages/retrieval.py`
- Modify: `fastQA/app/modules/qa_kb/stages/synthesis.py`
- Test: `fastQA/tests/test_fastqa_kb_graph_integration.py`
- Test: `fastQA/tests/test_qa_kb_service_runtime.py`
- Test: `fastQA/tests/test_generation_stage1_planning.py`
- Test: `fastQA/tests/test_generation_stage2_retrieval.py`
- Test: `fastQA/tests/test_generation_stage4_synthesis.py`

- [ ] **Step 1: Write integration tests for four route examples**

In `test_fastqa_kb_graph_integration.py`, use fake graph routing/service dependencies to assert:

- DOI direct answer stops before RAG.
- carbon source precise graph-for-RAG attaches payload.
- semantic no-slot skips graph and runs RAG without payload.
- hybrid attaches graph payload.
- community attaches graph payload.

Example assertion:

```python
def test_community_route_attaches_graph_payload_to_generation(monkeypatch):
    # arrange fake route_graph_kb_v2 returning mode="graph_for_rag"
    # assert qa_kb_service.iter_answer_events receives request.graph_evidence
```

- [ ] **Step 2: Write stream metadata tests**

Assert `metadata` and `done` events include:

- `knowledge_route_family`,
- `tri_state_mode`,
- `graph_strategy`,
- `graph_intent`,
- `graph_rag_injection_enabled`.

- [ ] **Step 3: Run integration tests to verify failures**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_fastqa_kb_graph_integration.py tests/test_qa_kb_service_runtime.py -q
```

Expected:

- fail on missing metadata or route behavior.

- [ ] **Step 4: Wire standardized metadata**

In `routers/qa.py`:

- call the metadata helper,
- merge metadata into `metadata` and `done` events,
- preserve existing keys expected by clients.

- [ ] **Step 5: Verify Stage1/Stage2/Stage4 graph payload flow**

The code already passes:

- Stage1 `graph_context`,
- Stage2 `graph_evidence`,
- Stage4 `graph_fact_block`.

Add tests only where missing. Avoid rewrites unless needed.

- [ ] **Step 6: Run focused pipeline tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_generation_stage1_planning.py tests/test_generation_stage2_retrieval.py tests/test_generation_stage4_synthesis.py tests/test_qa_kb_service_runtime.py -q
```

Expected:

- pass.

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/modules/graph_kb/service.py fastQA/app/routers/qa.py fastQA/app/modules/qa_kb/stages/planning.py fastQA/app/modules/qa_kb/stages/retrieval.py fastQA/app/modules/qa_kb/stages/synthesis.py fastQA/tests/test_fastqa_kb_graph_integration.py fastQA/tests/test_qa_kb_service_runtime.py fastQA/tests/test_generation_stage1_planning.py fastQA/tests/test_generation_stage2_retrieval.py fastQA/tests/test_generation_stage4_synthesis.py
git commit -m "feat: integrate four route graph evidence into kb qa"
```

---

### Task 10: Configuration, Rollout Defaults, And Documentation

**Files:**
- Modify: `fastQA/app/core/config.py`
- Modify: `resource/config/services/fastQA/config.shared.env`
- Modify: `resource/config/services/fastQA/config.env.example`
- Modify: `docs/graph/fastqa_graph_routing_current_state.md`
- Modify: `docs/graph/fastqa_graph_query_patterns.md`
- Test: `fastQA/tests/test_env_loader.py`

- [ ] **Step 1: Write config tests**

In `test_env_loader.py`, add assertions for defaults:

```python
def test_graph_four_route_flags_have_conservative_defaults(monkeypatch):
    monkeypatch.delenv("FASTQA_GRAPH_DIRECT_ANSWER_MIN_CONFIDENCE", raising=False)
    monkeypatch.delenv("FASTQA_GRAPH_MAX_DOI_CANDIDATES", raising=False)
    monkeypatch.delenv("FASTQA_GRAPH_ALLOW_SUSPICIOUS_DOI_FOR_RAG", raising=False)
    monkeypatch.delenv("FASTQA_GRAPH_COMMUNITY_ROUTE_ENABLED", raising=False)
    monkeypatch.delenv("FASTQA_GRAPH_PRECISE_NUMERIC_ENABLED", raising=False)

    config = _reload_config_module()
    settings = config.get_settings()

    assert settings.graph_kb_enabled is True
    assert settings.graph_kb_v2_enabled is True
    assert settings.graph_kb_rag_injection_enabled is True
    assert settings.graph_direct_answer_min_confidence >= 0.0
    assert settings.graph_max_doi_candidates > 0
    assert settings.graph_community_route_enabled is True
```

Use the existing `_reload_config_module()` helper already defined near the top of `fastQA/tests/test_env_loader.py`. Do not invent a new settings reload helper.

- [ ] **Step 2: Run config tests to verify failures**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_env_loader.py -q
```

Expected:

- fail if new fields are missing.

- [ ] **Step 3: Add optional config fields**

In `config.py`, add if needed:

- `graph_direct_answer_min_confidence`,
- `graph_max_doi_candidates`,
- `graph_allow_suspicious_doi_for_rag`,
- `graph_community_route_enabled`,
- `graph_precise_numeric_enabled`.

Use existing env naming style:

- `FASTQA_GRAPH_DIRECT_ANSWER_MIN_CONFIDENCE`
- `FASTQA_GRAPH_MAX_DOI_CANDIDATES`
- `FASTQA_GRAPH_ALLOW_SUSPICIOUS_DOI_FOR_RAG`
- `FASTQA_GRAPH_COMMUNITY_ROUTE_ENABLED`
- `FASTQA_GRAPH_PRECISE_NUMERIC_ENABLED`

Defaults:

- conservative direct answer threshold,
- small DOI candidate cap,
- suspicious DOI disabled for direct answer,
- community route enabled once tests pass,
- precise numeric graph evidence enabled, direct numeric gated by parser confidence.

- [ ] **Step 4: Update env examples**

Add public, non-secret defaults to:

- `resource/config/services/fastQA/config.shared.env`
- `resource/config/services/fastQA/config.env.example`

Do not add credentials.

- [ ] **Step 5: Update docs**

Update:

- `docs/graph/fastqa_graph_routing_current_state.md` with final V1 route behavior.
- `docs/graph/fastqa_graph_query_patterns.md` with any final query template changes.

- [ ] **Step 6: Run config tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_env_loader.py -q
```

Expected:

- pass.

- [ ] **Step 7: Commit**

```bash
git add fastQA/app/core/config.py resource/config/services/fastQA/config.shared.env resource/config/services/fastQA/config.env.example docs/graph/fastqa_graph_routing_current_state.md docs/graph/fastqa_graph_query_patterns.md fastQA/tests/test_env_loader.py
git commit -m "chore: add graph route rollout settings"
```

---

### Task 11: Full Verification

**Files:**
- No production changes unless failures expose a real issue.
- Test: all relevant fastQA graph and QA tests.

- [ ] **Step 1: Run graph test suite**

```bash
cd fastQA && PYTHONPATH=. pytest \
  tests/test_graph_kb_models.py \
  tests/test_graph_kb_schema_registry.py \
  tests/test_graph_kb_slots.py \
  tests/test_graph_kb_classifier_v2.py \
  tests/test_graph_kb_query_templates.py \
  tests/test_graph_kb_guardrail.py \
  tests/test_graph_kb_doi_quality.py \
  tests/test_graph_kb_value_parsers.py \
  tests/test_graph_kb_canonicalizer.py \
  tests/test_graph_kb_rag_adapter.py \
  tests/test_graph_kb_planner_v2.py \
  tests/test_graph_kb_executor_v2.py \
  tests/test_graph_kb_service.py \
  tests/test_fastqa_kb_graph_integration.py \
  -q
```

Expected:

- pass.

- [ ] **Step 2: Run generation pipeline graph integration tests**

```bash
cd fastQA && PYTHONPATH=. pytest \
  tests/test_generation_stage1_planning.py \
  tests/test_generation_stage2_retrieval.py \
  tests/test_generation_stage4_synthesis.py \
  tests/test_qa_kb_service_runtime.py \
  tests/test_qa_generation_orchestrator.py \
  -q
```

Expected:

- pass.

- [ ] **Step 3: Run broader fastQA relevant tests**

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_env_loader.py tests/test_qa_kb_models.py tests/test_qa_kb_service.py tests/test_qa_kb_context_usage.py -q
```

Expected:

- pass.

- [ ] **Step 4: Optional live Neo4j smoke tests**

Only run when local Neo4j is available and explicitly intended for local verification:

```bash
cd fastQA && PYTHONPATH=. pytest tests/test_graph_kb_runtime.py -q
```

Expected:

- pass or skip if the test is designed to skip without Neo4j.

- [ ] **Step 5: Manual route smoke cases**

If a dev server is already running or can be started safely, manually test:

- `10.1021/jp1005692 这篇文献是什么？`
- `列出使用蔗糖作为碳源的文献`
- `压实密度最高的LFP材料有哪些？`
- `放电容量超过150 mAh/g的LFP有哪些特点？`
- `为什么碳包覆会影响LFP倍率性能？`
- `LiFePO4的关系网络和机制关联是什么？`

Expected:

- route metadata matches the V1 route matrix,
- direct answers only for safe cases,
- graph-for-RAG cases continue to final RAG synthesis,
- community does not unconditional skip graph.

- [ ] **Step 6: Commit verification-only adjustments**

Only if fixes were needed:

```bash
git add <fixed-files>
git commit -m "fix: stabilize graph route verification"
```

---

## Implementation Notes

- Do not modify gateway graph routing. Gateway should remain file-vs-KB.
- Do not add credentials to docs or env examples.
- Do not rely on empty ontology labels.
- Do not implement free-form LLM Cypher in V1.
- Do not direct-render numeric rankings unless parser confidence is tested.
- Do not use raw community IDs as user-facing semantic labels.
- Keep graph failure fallback to generation-driven RAG.
- Preserve existing `legacy_route_family` metadata as a compatibility alias while adding `knowledge_route_family`.

## Review Checkpoints

Request review after:

1. Task 3, because route classification drives all downstream behavior.
2. Task 6, because query planning and guardrail shape determine safety.
3. Task 9, because stream integration affects user-visible behavior.
4. Task 11, before merge.

Use `superpowers:requesting-code-review` for code review during implementation.
