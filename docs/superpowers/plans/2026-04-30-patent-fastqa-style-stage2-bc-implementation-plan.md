# Patent FastQA-Style Stage2 B/C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve patent Stage2 retrieval quality by adding fastQA-style convergence first, then patent-native candidate aggregation and evidence scoring.

**Architecture:** Keep the existing patent staged QA path and Stage2 payload shape. Add a rollout-gated convergence layer around Stage2 query output, candidate collection, rerank, validation, top-K contraction, and diagnostics. Then add C-stage helpers for patent retrieval intent, patent-level scoring, direct global chunk recall, and table/metric boosts without changing Stage1 output in the first version.

**Tech Stack:** Python, pytest, existing `patent/server/patent/` modules, Chroma vector callbacks, existing execution cache and graph-for-RAG context.

---

## Source Spec

- Design spec: [`docs/superpowers/specs/2026-04-30-patent-fastqa-style-stage2-bc-design.md`](/home/cqy/worktrees/highThinking/docs/superpowers/specs/2026-04-30-patent-fastqa-style-stage2-bc-design.md)

## File Structure

Create:

- `patent/server/patent/stage2_controls.py`  
  Owns `PatentStage2RuntimeToggles`, environment parsing, rollout gate, and stable runtime signature payload.

- `patent/server/patent/retrieval_guardrails.py`  
  Owns query normalization, entity/metric/threshold extraction, and query guardrail application.

- `patent/server/patent/retrieval_validation.py`  
  Owns patent-native relevance validation and fallback rules for missing scores/no-vector results.

- `patent/server/patent/retrieval_scoring.py`  
  Owns C-stage retrieval intent derivation, candidate-hit models, patent-level aggregation, section-aware evidence selection, and table/metric boost scoring.

Modify:

- `patent/server/patent/stages/retrieval.py`  
  Apply guardrails after LLM query generation; pass toggles and diagnostics into retrieval service.

- `patent/server/patent/retrieval_service.py`  
  Add optional rerank hook, validation, top-K contraction, patent-level C path, and payload alignment helpers.

- `patent/server/patent/runtime.py`  
  Wire Stage2 controls, rerank adapter configuration, runtime signature, and direct global chunk search support.

- `patent/server/patent/orchestrators/generation.py`  
  Include Stage2 runtime signature in Stage2 cache fingerprint without including parallel worker counts.

- `patent/server/patent/retrieval_models.py`  
  Add dataclasses only if the new C-stage helper types need shared model definitions.

Test:

- `patent/tests/test_patent_stage2_controls.py`
- `patent/tests/test_patent_retrieval_guardrails.py`
- `patent/tests/test_patent_retrieval_validation.py`
- `patent/tests/test_patent_retrieval_scoring.py`
- extend `patent/tests/test_patent_retrieval_service.py`
- extend `patent/tests/test_patent_generation_orchestrator.py`
- extend `patent/tests/test_execution_cache.py` or `patent/tests/test_patent_graph_kb_stage1_cache_keys.py`

## Implementation Tasks

### Task 1: Stage2 Runtime Toggles And Cache Signature

**Files:**
- Create: `patent/server/patent/stage2_controls.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Test: `patent/tests/test_patent_stage2_controls.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`

- [ ] **Step 1: Write failing tests for toggle defaults and signature fields**

Create `patent/tests/test_patent_stage2_controls.py`:

```python
from __future__ import annotations

from server.patent.stage2_controls import (
    build_stage2_runtime_signature,
    resolve_stage2_runtime_toggles,
)


def test_stage2_convergence_rollout_gate_defaults_off(monkeypatch):
    for key in (
        "PATENT_STAGE2_CONVERGENCE_ENABLED",
        "PATENT_STAGE2_RERANK_ENABLED",
        "PATENT_STAGE2_VALIDATION_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)

    toggles = resolve_stage2_runtime_toggles()

    assert toggles.convergence_enabled is False
    assert toggles.rerank_enabled is True
    assert toggles.validation_enabled is True


def test_stage2_runtime_signature_includes_behavior_affecting_fields(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "12")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_MODEL", "gte-rerank-v2")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENDPOINT_FAMILY", "dashscope-text-rerank")

    signature = build_stage2_runtime_signature(base_signature={"retrieval_version": "retrieval-v2"})

    assert signature["retrieval_version"] == "retrieval-v2"
    assert signature["stage2_convergence_enabled"] is True
    assert signature["stage2_max_global_patents"] == 12
    assert signature["stage2_rerank_provider"] == "dashscope"
    assert signature["stage2_rerank_model"] == "gte-rerank-v2"
    assert signature["stage2_rerank_endpoint_family"] == "dashscope-text-rerank"
    assert signature["stage2_rerank_adapter_version"]
    assert signature["stage2_guardrail_version"]
    assert signature["stage2_validation_version"]
    assert signature["stage2_scoring_version"]
```

Extend `patent/tests/test_patent_generation_orchestrator.py` near the runtime-signature tests:

```python
def test_orchestrator_stage2_fingerprint_includes_stage2_runtime_signature(monkeypatch):
    captured = {}

    class _Runtime(_FakeRuntime):
        def stage2_runtime_signature(self):
            return {
                "stage2_convergence_enabled": True,
                "stage2_guardrail_version": "guardrail-v1",
                "stage2_max_global_patents": 12,
            }

    def _capture_stage2(**kwargs):
        captured["runtime_signature"] = dict(kwargs.get("runtime_signature") or {})
        return "stage2-fingerprint"

    monkeypatch.setattr("server.patent.orchestrators.generation.build_stage2_cache_fingerprint", _capture_stage2)

    PatentGenerationOrchestrator().run(question="q", runtime=_Runtime(), conversation_context={})

    assert captured["runtime_signature"]["stage2_convergence_enabled"] is True
    assert captured["runtime_signature"]["stage2_guardrail_version"] == "guardrail-v1"
    assert captured["runtime_signature"]["stage2_max_global_patents"] == 12
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_stage2_controls.py patent/tests/test_patent_generation_orchestrator.py::test_orchestrator_stage2_fingerprint_includes_stage2_runtime_signature -q
```

Expected: fail because `server.patent.stage2_controls` and `stage2_runtime_signature()` do not exist.

- [ ] **Step 3: Implement Stage2 controls**

Create `patent/server/patent/stage2_controls.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any


STAGE2_GUARDRAIL_VERSION = "patent-stage2-guardrail-v1"
STAGE2_VALIDATION_VERSION = "patent-stage2-validation-v1"
STAGE2_SCORING_VERSION = "patent-stage2-scoring-v1"
STAGE2_PAYLOAD_CONTRACT_VERSION = "patent-stage2-payload-v2"
STAGE2_RERANK_ADAPTER_VERSION = "patent-stage2-rerank-adapter-v1"


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = int(default)
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class PatentStage2RuntimeToggles:
    convergence_enabled: bool
    force_keyword_injection_enabled: bool
    entity_lock_enabled: bool
    rerank_enabled: bool
    rerank_candidates: int
    rerank_top_patents: int
    min_results_per_claim: int
    max_results_per_claim: int
    max_global_patents: int
    validation_enabled: bool
    c_patent_scoring_enabled: bool
    c_global_chunk_recall_enabled: bool
    c_table_metric_boost_enabled: bool
    rerank_provider: str
    rerank_model: str
    rerank_endpoint_family: str


def resolve_stage2_runtime_toggles() -> PatentStage2RuntimeToggles:
    return PatentStage2RuntimeToggles(
        convergence_enabled=_env_bool("PATENT_STAGE2_CONVERGENCE_ENABLED", False),
        force_keyword_injection_enabled=_env_bool("PATENT_STAGE2_FORCE_KEYWORD_INJECTION", True),
        entity_lock_enabled=_env_bool("PATENT_STAGE2_ENTITY_LOCK_ENABLED", True),
        rerank_enabled=_env_bool("PATENT_STAGE2_RERANK_ENABLED", True),
        rerank_candidates=_env_int("PATENT_STAGE2_RERANK_CANDIDATES", 80, minimum=5, maximum=200),
        rerank_top_patents=_env_int("PATENT_STAGE2_RERANK_TOP_PATENTS", 20, minimum=1, maximum=100),
        min_results_per_claim=_env_int("PATENT_STAGE2_MIN_RESULTS_PER_CLAIM", 2, minimum=0, maximum=20),
        max_results_per_claim=_env_int("PATENT_STAGE2_MAX_RESULTS_PER_CLAIM", 5, minimum=1, maximum=50),
        max_global_patents=_env_int("PATENT_STAGE2_MAX_GLOBAL_PATENTS", 20, minimum=1, maximum=200),
        validation_enabled=_env_bool("PATENT_STAGE2_VALIDATION_ENABLED", True),
        c_patent_scoring_enabled=_env_bool("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", False),
        c_global_chunk_recall_enabled=_env_bool("PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED", False),
        c_table_metric_boost_enabled=_env_bool("PATENT_STAGE2_C_TABLE_METRIC_BOOST_ENABLED", False),
        rerank_provider=str(os.getenv("PATENT_STAGE2_RERANK_PROVIDER", "none")).strip().lower() or "none",
        rerank_model=str(os.getenv("PATENT_STAGE2_RERANK_MODEL", "")).strip(),
        rerank_endpoint_family=str(os.getenv("PATENT_STAGE2_RERANK_ENDPOINT_FAMILY", "")).strip(),
    )


def build_stage2_runtime_signature(*, base_signature: dict[str, Any] | None = None) -> dict[str, Any]:
    toggles = resolve_stage2_runtime_toggles()
    signature = dict(base_signature or {})
    signature.update(
        {
            **{f"stage2_{key}": value for key, value in asdict(toggles).items()},
            "stage2_guardrail_version": STAGE2_GUARDRAIL_VERSION,
            "stage2_validation_version": STAGE2_VALIDATION_VERSION,
            "stage2_scoring_version": STAGE2_SCORING_VERSION,
            "stage2_payload_contract_version": STAGE2_PAYLOAD_CONTRACT_VERSION,
            "stage2_rerank_adapter_version": STAGE2_RERANK_ADAPTER_VERSION,
        }
    )
    return signature
```

Modify `PatentRuntime` in `patent/server/patent/runtime.py`:

```python
from server.patent.stage2_controls import build_stage2_runtime_signature

def stage2_runtime_signature(self) -> dict[str, Any]:
    return build_stage2_runtime_signature(
        base_signature={
            "runtime_type": type(self).__name__,
            "retrieval_version": getattr(self.retrieval_service, "retrieval_version", ""),
            "catalog_index_version": getattr(self.retrieval_service, "catalog_index_version", ""),
            "stage2_query_model": self.planning_model,
        }
    )
```

Modify `PatentGenerationOrchestrator.run()` to prefer `runtime.stage2_runtime_signature()` when present, while preserving the existing fallback and still excluding worker counts.

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_stage2_controls.py patent/tests/test_patent_generation_orchestrator.py::test_orchestrator_stage2_fingerprint_includes_stage2_runtime_signature -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Do not commit unless the user explicitly re-enables commits. If commits are allowed later:

```bash
git add patent/server/patent/stage2_controls.py patent/server/patent/runtime.py patent/server/patent/orchestrators/generation.py patent/tests/test_patent_stage2_controls.py patent/tests/test_patent_generation_orchestrator.py
git commit -m "feat: add patent stage2 runtime controls"
```

### Task 2: Query Guardrails

**Files:**
- Create: `patent/server/patent/retrieval_guardrails.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Test: `patent/tests/test_patent_retrieval_guardrails.py`
- Extend: `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Write failing tests for metric/entity injection**

Create `patent/tests/test_patent_retrieval_guardrails.py`:

```python
from __future__ import annotations

from server.patent.models import PatentRetrievalClaim
from server.patent.retrieval_guardrails import apply_patent_stage2_query_guardrails
from server.patent.stage2_controls import PatentStage2RuntimeToggles


def _toggles(**overrides):
    defaults = dict(
        convergence_enabled=True,
        force_keyword_injection_enabled=True,
        entity_lock_enabled=True,
        rerank_enabled=False,
        rerank_candidates=20,
        rerank_top_patents=10,
        min_results_per_claim=1,
        max_results_per_claim=3,
        max_global_patents=10,
        validation_enabled=True,
        c_patent_scoring_enabled=False,
        c_global_chunk_recall_enabled=False,
        c_table_metric_boost_enabled=False,
        rerank_provider="none",
        rerank_model="",
        rerank_endpoint_family="",
    )
    defaults.update(overrides)
    return PatentStage2RuntimeToggles(**defaults)


def test_guardrail_preserves_lfp_capacity_threshold():
    guarded = apply_patent_stage2_query_guardrails(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claim=PatentRetrievalClaim(claim="碳包覆改性实现高容量", keywords=["LiFePO4"]),
        queries=["carbon coated cathode material high capacity"],
        toggles=_toggles(),
        graph_context=None,
    )

    assert guarded.queries
    final_query = guarded.queries[0]
    assert "LFP" in final_query or "LiFePO4" in final_query
    assert "150" in final_query
    assert "mAh/g" in final_query
    assert guarded.diagnostics["injected_thresholds"]


def test_guardrail_is_noop_when_convergence_disabled():
    guarded = apply_patent_stage2_query_guardrails(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claim=PatentRetrievalClaim(claim="x", keywords=[]),
        queries=["plain query"],
        toggles=_toggles(convergence_enabled=False),
        graph_context=None,
    )

    assert guarded.queries == ["plain query"]
    assert guarded.diagnostics["enabled"] is False
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_guardrails.py -q
```

Expected: fail because `retrieval_guardrails.py` does not exist.

- [ ] **Step 3: Implement guardrail helper**

Create `retrieval_guardrails.py` with:

- `PatentGuardrailResult`
- `extract_patent_query_terms(question, claim, graph_context)`
- `apply_patent_stage2_query_guardrails(...)`

Implementation rules:

```python
@dataclass(frozen=True)
class PatentGuardrailResult:
    queries: list[str]
    diagnostics: dict[str, Any]


def apply_patent_stage2_query_guardrails(...):
    if not toggles.convergence_enabled:
        return PatentGuardrailResult(queries=_normalize_query_list(queries), diagnostics={"enabled": False})
    # extract patent IDs, metrics, thresholds, materials, IPC/applicant/inventor graph hints
    # prefix missing terms to each query
    # dedupe and return diagnostics
```

Keep extraction deterministic and conservative. Do not call an LLM here.

- [ ] **Step 4: Wire guardrail into Stage2 query generation path**

Modify `run_stage2_targeted_retrieval()` in `patent/server/patent/stages/retrieval.py`:

- Resolve toggles once.
- Build frozen claim queries as today.
- If convergence is enabled, pass each claim's generated queries through guardrails.
- Pass guardrail diagnostics into `retrieval_service.targeted_retrieve(...)` via a new optional `stage2_query_diagnostics` argument.

If adding the optional argument to `targeted_retrieve`, default it to `None` to preserve existing tests.

- [ ] **Step 5: Run targeted tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_guardrails.py patent/tests/test_patent_retrieval_service.py::test_stage2_query_generation_is_frozen_serially_before_parallel_dispatch -q
```

Expected: pass.

### Task 3: Rerank Adapter And Graceful Fallback

**Files:**
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/stages/retrieval.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Test: extend `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Write failing tests for rerank fallback and success ordering**

Add to `patent/tests/test_patent_retrieval_service.py`:

```python
def test_stage2_convergence_rerank_failure_falls_back_with_metadata(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "true")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "battery thermal abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "battery thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.2},
        ],
    )

    def _broken_rerank(**kwargs):
        raise RuntimeError("rerank down")

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "battery thermal", "keywords": []}],
        user_question="battery thermal",
        frozen_claim_queries=[["battery thermal"]],
        rerank_fn=_broken_rerank,
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadata"]["stage2_rerank"]["fallback_reason"] == "request_failed"


def test_stage2_convergence_rerank_success_reorders_and_limits_patents(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_TOP_PATENTS", "1")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "1")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode chunk", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.2},
        ],
    )

    def _fake_rerank(*, query, documents, metadatas, top_n, **kwargs):
        del query, kwargs
        # Reverse vector order and return only the requested top item.
        return {
            "documents": [documents[1]],
            "metadatas": [metadatas[1]],
            "rerank_scores": [0.99],
            "fallback": False,
            "provider": "fake",
        }

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "electrode", "keywords": []}],
        user_question="electrode",
        frozen_claim_queries=[["electrode"]],
        rerank_fn=_fake_rerank,
    )

    assert payload["source_ids"] == ["US20240001234A1"]
    assert payload["references"] == ["US20240001234A1"]
    assert payload["metadata"]["stage2_rerank"]["applied"] is True
    assert payload["metadata"]["stage2_rerank"]["provider"] == "fake"
```

- [ ] **Step 1b: Write failing runtime wrapper test proving rerank reaches real Stage2**

Add to `patent/tests/test_patent_retrieval_service.py` near the existing `PatentRuntime.stage2_targeted_retrieval()` wrapper tests:

```python
def test_runtime_stage2_targeted_retrieval_passes_rerank_fn_to_wrapper(monkeypatch):
    captured = {}

    def _fake_run_stage2_targeted_retrieval(**kwargs):
        captured.update(kwargs)
        return {"documents": [], "metadatas": [], "distances": [], "references": [], "source_ids": [], "metadata": {}}

    monkeypatch.setattr("server.patent.runtime.run_stage2_targeted_retrieval", _fake_run_stage2_targeted_retrieval)

    runtime = PatentRuntime(
        retrieval_service=_service(),
        resources=[],
        planning_client=None,
        planning_model="",
    )

    def _rerank(**kwargs):
        return {"documents": [], "metadatas": [], "rerank_scores": []}

    runtime.stage2_rerank_fn = _rerank

    runtime.stage2_targeted_retrieval(
        [PatentRetrievalClaim(claim="battery thermal", keywords=[])],
        user_question="battery thermal",
    )

    assert captured["rerank_fn"] is _rerank
```

Add a second wrapper-level test for `run_stage2_targeted_retrieval()` itself:

```python
def test_run_stage2_targeted_retrieval_passes_rerank_fn_to_service():
    class _Service:
        def targeted_retrieve(self, **kwargs):
            self.kwargs = kwargs
            return {"documents": [], "metadatas": [], "distances": [], "references": [], "source_ids": [], "metadata": {}}

    service = _Service()

    def _rerank(**kwargs):
        return {"documents": [], "metadatas": [], "rerank_scores": []}

    run_stage2_targeted_retrieval(
        retrieval_service=service,
        retrieval_claims=[PatentRetrievalClaim(claim="battery thermal", keywords=[])],
        user_question="battery thermal",
        rerank_fn=_rerank,
    )

    assert service.kwargs["rerank_fn"] is _rerank
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_service.py::test_stage2_convergence_rerank_failure_falls_back_with_metadata patent/tests/test_patent_retrieval_service.py::test_stage2_convergence_rerank_success_reorders_and_limits_patents patent/tests/test_patent_retrieval_service.py::test_runtime_stage2_targeted_retrieval_passes_rerank_fn_to_wrapper patent/tests/test_patent_retrieval_service.py::test_run_stage2_targeted_retrieval_passes_rerank_fn_to_service -q
```

Expected: fail because `rerank_fn` is unsupported by service and wrapper signatures, and metadata is absent.

- [ ] **Step 3: Implement rerank hook**

Modify `PatentRetrievalService.targeted_retrieve()` and `_targeted_retrieve_from_claims()`:

- Add optional `rerank_fn: Callable[..., dict[str, Any]] | None = None`.
- Collect raw candidates per claim.
- If convergence and rerank are enabled, call `rerank_fn(query=..., documents=..., metadatas=..., top_n=...)`.
- Map rerank output back to candidate metadata by returned document/metadata identity. If the provider returns only documents and metadata, rebuild selected `_MatchedReference` items from the matching raw candidates. If the provider returns index-based output, use indices first.
- Apply `PATENT_STAGE2_RERANK_TOP_PATENTS` before global `PATENT_STAGE2_MAX_GLOBAL_PATENTS`.
- If rerank raises, preserve vector order and set:

```python
metadata["stage2_rerank"] = {
    "enabled": True,
    "applied": False,
    "fallback": True,
    "fallback_reason": "request_failed",
}
```

If rerank is disabled:

```python
metadata["stage2_rerank"] = {"enabled": False, "applied": False, "fallback": False}
```

- [ ] **Step 4: Wire runtime rerank adapter**

Modify `run_stage2_targeted_retrieval()` in `patent/server/patent/stages/retrieval.py`:

- Add optional `rerank_fn: Callable[..., dict[str, Any]] | None = None`.
- Pass `rerank_fn=rerank_fn` into `retrieval_service.targeted_retrieve(...)`.

Modify `PatentRuntime` in `patent/server/patent/runtime.py`:

- Add a runtime attribute `stage2_rerank_fn: Any | None = None`.
- In `stage2_targeted_retrieval()`, pass `rerank_fn=self.stage2_rerank_fn` to `run_stage2_targeted_retrieval(...)`.
- Provide `stage2_rerank_fn` only when a provider is configured. First implementation can support only `provider="none"` and injected tests. Do not add network calls unless an approved provider config exists.

- [ ] **Step 5: Run tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_service.py::test_stage2_convergence_rerank_failure_falls_back_with_metadata patent/tests/test_patent_retrieval_service.py::test_stage2_convergence_rerank_success_reorders_and_limits_patents patent/tests/test_patent_retrieval_service.py::test_runtime_stage2_targeted_retrieval_passes_rerank_fn_to_wrapper patent/tests/test_patent_retrieval_service.py::test_run_stage2_targeted_retrieval_passes_rerank_fn_to_service -q
```

Expected: pass.

### Task 4: Relevance Validation And No-Vector Compatibility

**Files:**
- Create: `patent/server/patent/retrieval_validation.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Test: `patent/tests/test_patent_retrieval_validation.py`
- Extend: `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Write failing validation tests**

Create `patent/tests/test_patent_retrieval_validation.py`:

```python
from __future__ import annotations

from server.patent.retrieval_validation import validate_patent_stage2_candidates


def test_validation_keeps_metric_candidate_and_filters_generic_candidate():
    candidates = [
        {
            "document": "LiFePO4 LFP 放电容量 156 mAh/g，实施例1。",
            "metadata": {"patent_id": "CN1", "section_type": "description"},
            "score": 0.8,
        },
        {
            "document": "A generic cathode material has good performance.",
            "metadata": {"patent_id": "CN2", "section_type": "abstract"},
            "score": 0.9,
        },
    ]

    result = validate_patent_stage2_candidates(
        candidates=candidates,
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        claim_text="LFP 放电容量超过 150 mAh/g",
        min_results=1,
    )

    assert [item["metadata"]["patent_id"] for item in result.selected] == ["CN1"]
    assert result.diagnostics["filtered_count"] == 1


def test_validation_keeps_no_vector_candidate_when_needed():
    candidates = [
        {
            "document": "Claim 1: exact archive fallback evidence.",
            "metadata": {"patent_id": "CN123456789A", "section_type": "claim", "exact_id_match": True},
            "score": None,
        }
    ]

    result = validate_patent_stage2_candidates(
        candidates=candidates,
        user_question="CN123456789A",
        claim_text="CN123456789A",
        min_results=1,
    )

    assert result.selected
    assert result.diagnostics["validation_fallback"] is False
```

- [ ] **Step 1b: Write a failing targeted no-vector compatibility test**

Add to `patent/tests/test_patent_retrieval_service.py`:

```python
def test_stage2_convergence_targeted_no_vector_fallback_keeps_stage3_payload(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_VALIDATION_ENABLED", "true")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        identity_registry={"CN123456789A": "CN123456789A"},
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[
            {
                "claim": "Summarize CN123456789A thermal management",
                "keywords": ["CN123456789A"],
                "preferred_sections": ["claims"],
            }
        ],
        user_question="Summarize CN123456789A",
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["references"] == ["CN123456789A"]
    assert isinstance(payload["documents"], list)
    assert isinstance(payload["metadatas"], list)
    assert isinstance(payload["distances"], list)
    assert isinstance(payload["reference_objects"], list)
    assert isinstance(payload["reference_links"], list)
    assert isinstance(payload["original_links"], list)
    assert payload["metadata"]["stage2_validation"]["validation_fallback"] in {False, True}
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_validation.py patent/tests/test_patent_retrieval_service.py::test_stage2_convergence_targeted_no_vector_fallback_keeps_stage3_payload -q
```

Expected: fail because module does not exist and targeted no-vector convergence metadata is absent.

- [ ] **Step 3: Implement validation helper**

Create:

```python
@dataclass(frozen=True)
class PatentValidationResult:
    selected: list[dict[str, Any]]
    filtered: list[dict[str, Any]]
    diagnostics: dict[str, Any]
```

Implement `validate_patent_stage2_candidates(...)` with deterministic scoring:

- normalize text
- extract metric and threshold tokens from question and claim
- compute entity/metric/threshold/section coverage
- preserve exact ID candidates
- if selected count is below `min_results`, restore best candidates and set `validation_fallback=True`

- [ ] **Step 4: Integrate validation into Stage2**

In `PatentRetrievalService._targeted_retrieve_from_claims()`:

- Convert `_MatchedReference` objects into candidate dicts for validation.
- Apply validation only when convergence and validation toggles are enabled.
- Preserve selected matches only.
- Write metadata:

```python
metadata["stage2_validation"] = {
    "enabled": True,
    "validated_count": len(selected),
    "filtered_count": len(filtered),
    "validation_fallback": bool(...),
}
metadata["stage2_filtered_out_sample"] = [...]
```

Also handle the no-vector branches explicitly:

- If `_targeted_retrieve_from_claims()` takes the exact-ID/no-vector early return into `_targeted_retrieve_from_plan()`, normalize that returned payload before returning it from the convergence path.
- `_targeted_retrieve_from_plan()` must preserve Stage3-consumable fields: `documents`, `metadatas`, `distances`, `references`, `reference_objects`, `reference_links`, `original_links`, and `source_ids`.
- When convergence is enabled on a no-vector payload, add `metadata["stage2_validation"]` with `enabled=True`, `validated_count`, `filtered_count=0`, and a deterministic `validation_fallback` value.
- Add no-vector diagnostics such as `metadata["stage2_no_vector_fallback"]=True` and `metadata["stage2_missing_vector_signal"]="exact_id_or_archive_fallback"` so fallback behavior is visible without changing the selected source IDs.
- Do not drop exact identifier matches during validation; exact IDs are always selected candidates.

- [ ] **Step 5: Run validation and no-vector tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_validation.py patent/tests/test_patent_retrieval_service.py::test_stage2_convergence_targeted_no_vector_fallback_keeps_stage3_payload patent/tests/test_patent_retrieval_service.py::test_exact_identifier_retrieval_returns_patent_evidence_and_original_links -q
```

Expected: pass.

### Task 5: Payload Top-K Contraction And Alignment

**Files:**
- Modify: `patent/server/patent/retrieval_service.py`
- Test: extend `patent/tests/test_patent_retrieval_service.py`
- Test: extend `patent/tests/test_patent_stage3_evidence_loading.py`

- [ ] **Step 1: Write failing payload alignment test**

Add to `patent/tests/test_patent_retrieval_service.py`:

```python
def test_stage2_convergence_contracts_payload_to_selected_patents(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "1")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode chunk", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.2},
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "battery thermal", "keywords": []}],
        user_question="battery thermal",
        frozen_claim_queries=[["battery thermal"]],
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["references"] == ["CN123456789A"]
    assert all(item["patent_id"] == "CN123456789A" for item in payload["metadatas"])
    assert len(payload["documents"]) == len(payload["metadatas"]) == len(payload["distances"])
    assert payload["metadata"]["stage2_raw_candidate_count"] >= 2
    assert payload["metadata"]["stage2_selected_patent_ids"] == ["CN123456789A"]
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_service.py::test_stage2_convergence_contracts_payload_to_selected_patents -q
```

Expected: fail because no top-K contraction exists.

- [ ] **Step 3: Implement payload contraction helper**

In `PatentRetrievalService`, add a helper:

```python
def _contract_stage2_payload_to_selected_patents(
    self,
    payload: dict[str, Any],
    *,
    selected_patent_ids: list[str],
    selected_matches: list[_MatchedReference],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    ...
```

Requirements:

- `documents/metadatas/distances` come from `selected_matches`.
- `references/source_ids` match `selected_patent_ids`.
- `reference_objects/reference_links/original_links` filtered to selected patents.
- raw pool counts stay in metadata only.
- set `metadata.stage2_payload_contract_version`.

- [ ] **Step 4: Run Stage2 and Stage3 compatibility tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_service.py::test_stage2_convergence_contracts_payload_to_selected_patents patent/tests/test_patent_stage3_evidence_loading.py::test_stage3_evidence_loading_groups_stage2_documents_caps_retrieval_chunks_and_attaches_table_markdown -q
```

Expected: pass.

### Task 6: B Graph Semantics And Disabled-Gate Regression

**Files:**
- Modify: `patent/server/patent/retrieval_service.py`
- Test: extend `patent/tests/test_patent_retrieval_service.py`

- [ ] **Step 1: Write graph hard-filter and disabled gate tests**

Add:

```python
def test_stage2_b_keeps_graph_candidate_hard_filter_when_convergence_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode chunk", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.2},
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "thermal", "keywords": []}],
        user_question="thermal",
        frozen_claim_queries=[["thermal"]],
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert payload["source_ids"] == ["CN123456789A"]
    assert payload["metadata"]["graph_stage2_behavior"] == "filter_applied"


def test_stage2_convergence_disabled_preserves_existing_wide_output(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "false")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "1")
    monkeypatch.setenv("PATENT_STAGE2_VALIDATION_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "true")

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "thermal abstract", "distance": 0.1},
            {"patent_id": "US20240001234A1", "document": "electrode abstract", "distance": 0.2},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {
                "patent_id": patent_id,
                "document": f"{patent_id} chunk",
                "source_file": "说明书.txt",
                "chunk_index": index,
                "distance": 0.1 + index,
            }
            for index, patent_id in enumerate(list(patent_ids or []))
        ],
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "thermal electrode", "keywords": []}],
        user_question="thermal electrode",
        frozen_claim_queries=[["thermal electrode"]],
    )

    assert payload["source_ids"] == ["CN123456789A", "US20240001234A1"]
    assert payload["references"] == ["CN123456789A", "US20240001234A1"]
    assert "stage2_validation" not in payload.get("metadata", {})
    assert "stage2_rerank" not in payload.get("metadata", {})
```

- [ ] **Step 2: Run tests and verify failures if behavior is not yet explicit**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_service.py -k "graph_candidate_hard_filter or convergence_disabled" -q
```

Expected: graph may already pass; disabled gate should verify no top-K contraction.

- [ ] **Step 3: Implement explicit branching**

In `PatentRetrievalService._targeted_retrieve_from_claims()`:

- If `toggles.convergence_enabled` is false, keep current path and return current payload plus non-behavioral diagnostics only if safe.
- If true and B mode, preserve graph hard filtering exactly as current behavior.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_service.py -k "graph_candidate_hard_filter or convergence_disabled or graph_stage2_behavior" -q
```

Expected: pass.

### Task 7: C Retrieval Intent And Patent-Level Scoring

**Files:**
- Create: `patent/server/patent/retrieval_scoring.py`
- Modify: `patent/server/patent/retrieval_service.py`
- Test: `patent/tests/test_patent_retrieval_scoring.py`

- [ ] **Step 1: Write failing C scoring tests**

Create `patent/tests/test_patent_retrieval_scoring.py`:

```python
from __future__ import annotations

from server.patent.retrieval_scoring import (
    aggregate_patent_candidates,
    derive_patent_retrieval_intent,
)


def test_derive_intent_extracts_metrics_thresholds_and_materials():
    intent = derive_patent_retrieval_intent(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claims=[{"claim": "LFP 高容量", "keywords": ["LiFePO4"]}],
        graph_context=None,
    )

    assert "LFP" in intent.materials or "LiFePO4" in intent.materials
    assert intent.metrics
    assert any("150" in item["value"] for item in intent.thresholds)


def test_patent_level_aggregation_boosts_metric_evidence_over_generic_abstract():
    hits = [
        {
            "patent_id": "CN1",
            "document": "LiFePO4 放电容量 156 mAh/g 实施例",
            "section_type": "description",
            "score": 0.75,
            "channel": "chunk_vector_candidate",
            "metadata": {},
        },
        {
            "patent_id": "CN2",
            "document": "Cathode material with good performance",
            "section_type": "abstract",
            "score": 0.85,
            "channel": "abstract_vector",
            "metadata": {},
        },
    ]
    intent = derive_patent_retrieval_intent(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claims=[],
        graph_context=None,
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent)

    assert ranked[0].patent_id == "CN1"
    assert "metric_threshold_match" in ranked[0].reasons


def test_c_graph_candidates_are_bounded_boosts_not_hard_filters():
    hits = [
        {
            "patent_id": "CN123456789A",
            "document": "Graph seeded patent with weak generic battery text",
            "section_type": "abstract",
            "score": 0.40,
            "channel": "graph_candidate",
            "metadata": {},
        },
        {
            "patent_id": "US20240001234A1",
            "document": "LiFePO4 放电容量 156 mAh/g 实施例",
            "section_type": "description",
            "score": 0.80,
            "channel": "chunk_vector_global",
            "metadata": {},
        },
    ]
    intent = derive_patent_retrieval_intent(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claims=[],
        graph_context={"stage2_patent_candidates": ["CN123456789A"]},
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent)

    assert {item.patent_id for item in ranked} == {"CN123456789A", "US20240001234A1"}
    assert ranked[0].patent_id == "US20240001234A1"
    assert any("graph_candidate_boost" in item.reasons for item in ranked if item.patent_id == "CN123456789A")


def test_c_explicit_patent_ids_remain_hard_constraints():
    hits = [
        {"patent_id": "CN123456789A", "document": "explicit id evidence", "section_type": "claim", "score": 0.3, "channel": "exact_id", "metadata": {}},
        {"patent_id": "US20240001234A1", "document": "better generic evidence", "section_type": "description", "score": 0.9, "channel": "chunk_vector_global", "metadata": {}},
    ]
    intent = derive_patent_retrieval_intent(
        user_question="请总结 CN123456789A",
        retrieval_claims=[],
        graph_context={"stage2_patent_candidates": ["US20240001234A1"]},
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent)

    assert [item.patent_id for item in ranked] == ["CN123456789A"]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_scoring.py -q
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement retrieval scoring module**

Create dataclasses:

```python
@dataclass(frozen=True)
class PatentRetrievalIntent:
    ...

@dataclass(frozen=True)
class PatentCandidateScore:
    ...
```

Implement:

- `derive_patent_retrieval_intent(...)`
- `aggregate_patent_candidates(...)`
- `select_evidence_for_patents(...)`

Keep C disabled by default through `PATENT_STAGE2_C_PATENT_SCORING_ENABLED=false`.

Graph rules in this module:

- Graph candidate hits are seeds/boosts only when C scoring is enabled.
- The graph boost must be bounded so a weak graph candidate cannot outrank a strong metric/vector candidate solely because it came from graph.
- Explicit patent IDs extracted from the user question remain hard constraints and filter out non-explicit candidates.

Service integration rule:

- When `PATENT_STAGE2_C_PATENT_SCORING_ENABLED=true`, `retrieval_service.py` must bypass B's graph hard-filter branch during recall. Graph candidates should be converted into bounded seed/boost hits and added to the candidate pool, not used as the only allowed `patent_ids` filter.
- Explicit patent IDs from the user question are still hard constraints before scoring.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_scoring.py -q
```

Expected: pass.

### Task 8: C Direct Global Chunk Recall And Table Metric Boost

**Files:**
- Modify: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/retrieval_scoring.py`
- Test: extend `patent/tests/test_patent_retrieval_service.py`
- Test: extend `patent/tests/test_patent_retrieval_scoring.py`

- [ ] **Step 1: Write failing global chunk recall test that cannot pass on current abstract-first behavior**

Add to `test_patent_retrieval_service.py`:

```python
def test_stage2_c_global_chunk_recall_finds_better_evidence_outside_abstract_candidates(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED", "true")

    chunk_calls: list[list[str] | None] = []

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "generic thermal abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: (
            chunk_calls.append(list(patent_ids) if patent_ids is not None else None)
            or (
                [
                    {"patent_id": "CN123456789A", "document": "generic thermal chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.2},
                ]
                if patent_ids
                else [
                    {"patent_id": "US20240001234A1", "document": "Anode porosity control at high C-rate", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.05},
                ]
            )
        ),
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "anode porosity high C-rate", "keywords": []}],
        user_question="anode porosity high C-rate",
        frozen_claim_queries=[["anode porosity high C-rate"]],
    )

    assert None in chunk_calls
    assert payload["source_ids"] == ["US20240001234A1"]
```

- [ ] **Step 1b: Write failing service-level C graph seed/boost test**

Add to `test_patent_retrieval_service.py`:

```python
def test_stage2_c_graph_candidates_do_not_hard_filter_strong_vector_candidates(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED", "true")

    chunk_calls: list[list[str] | None] = []

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "generic graph-seeded abstract", "distance": 0.4},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: (
            chunk_calls.append(list(patent_ids) if patent_ids is not None else None)
            or (
                [
                    {"patent_id": "CN123456789A", "document": "generic graph chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.4},
                ]
                if patent_ids
                else [
                    {"patent_id": "US20240001234A1", "document": "LiFePO4 放电容量 156 mAh/g 实施例", "source_file": "说明书.txt", "chunk_index": 1, "distance": 0.02},
                ]
            )
        ),
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "LFP 放电容量超过 150 mAh/g", "keywords": ["LFP"]}],
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        frozen_claim_queries=[["LFP discharge capacity 150 mAh/g"]],
        context={"graph_kb": {"stage2_patent_candidates": ["CN123456789A"]}},
    )

    assert None in chunk_calls
    assert set(payload["metadata"]["stage2_raw_candidate_patent_ids"]) >= {"CN123456789A", "US20240001234A1"}
    assert payload["source_ids"][0] == "US20240001234A1"
    assert any(
        "graph_candidate_boost" in item["reasons"]
        for item in payload["metadata"]["stage2_patent_scores"]
        if item["patent_id"] == "CN123456789A"
    )
```

- [ ] **Step 2: Write failing table boost tests**

Add to `patent/tests/test_patent_retrieval_scoring.py`:

```python
def test_table_metric_boost_changes_ranking_only_for_candidate_pool():
    hits = [
        {
            "patent_id": "CN_TABLE",
            "document": "LiFePO4 embodiment table candidate",
            "section_type": "description",
            "score": 0.60,
            "channel": "chunk_vector_candidate",
            "metadata": {
                "table_supplements": [
                    {
                        "table_title": "表1 放电容量",
                        "rows": [{"材料": "LFP", "放电容量": "156 mAh/g"}],
                    }
                ]
            },
        },
        {
            "patent_id": "CN_NO_TABLE",
            "document": "LiFePO4 generic high capacity abstract",
            "section_type": "abstract",
            "score": 0.75,
            "channel": "abstract_vector",
            "metadata": {},
        },
    ]
    intent = derive_patent_retrieval_intent(
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        retrieval_claims=[],
        graph_context=None,
    )

    ranked = aggregate_patent_candidates(hits=hits, intent=intent, table_metric_boost_enabled=True)

    assert ranked[0].patent_id == "CN_TABLE"
    assert "table_metric_match" in ranked[0].reasons
```

Add a service-level bounded-loader test to `patent/tests/test_patent_retrieval_service.py`:

```python
def test_stage2_c_table_boost_loads_tables_only_for_candidate_pool(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_PATENT_SCORING_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_C_TABLE_METRIC_BOOST_ENABLED", "true")

    loaded_tables: list[str] = []

    service = PatentRetrievalService(
        catalog_records=_catalog(),
        abstract_vector_search=lambda question, top_k: [
            {"patent_id": "CN123456789A", "document": "LFP capacity abstract", "distance": 0.2},
            {"patent_id": "US20240001234A1", "document": "generic electrode abstract", "distance": 0.1},
        ],
        chunk_vector_search=lambda question, patent_ids, top_k: [
            {"patent_id": patent_id, "document": f"{patent_id} chunk", "source_file": "说明书.txt", "chunk_index": 0, "distance": 0.2}
            for patent_id in list(patent_ids or [])
        ],
        table_loader=lambda patent_id: (
            loaded_tables.append(patent_id)
            or (
                [{"table_title": "表1 放电容量", "rows": [{"材料": "LFP", "放电容量": "156 mAh/g"}]}]
                if patent_id == "CN123456789A"
                else []
            )
        ),
    )

    payload = service.targeted_retrieve(
        retrieval_claims=[{"claim": "LFP 放电容量超过 150 mAh/g", "keywords": ["LFP"]}],
        user_question="找 LFP 放电容量超过 150 mAh/g 的专利",
        frozen_claim_queries=[["LFP discharge capacity 150 mAh/g"]],
    )

    assert set(loaded_tables) <= set(payload["metadata"]["stage2_raw_candidate_patent_ids"])
    assert payload["source_ids"][0] == "CN123456789A"
    assert "table_metric_match" in payload["metadata"]["stage2_patent_scores"][0]["reasons"]
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_service.py::test_stage2_c_global_chunk_recall_finds_better_evidence_outside_abstract_candidates patent/tests/test_patent_retrieval_service.py::test_stage2_c_graph_candidates_do_not_hard_filter_strong_vector_candidates patent/tests/test_patent_retrieval_service.py::test_stage2_c_table_boost_loads_tables_only_for_candidate_pool patent/tests/test_patent_retrieval_scoring.py -q
```

Expected: global chunk and table boost tests fail until C path is wired.

- [ ] **Step 4: Implement C global chunk recall**

In `_targeted_retrieve_from_claims()`:

- If C global chunk recall is enabled, run `_run_chunk_vector_search(query, None, top_k)` in addition to candidate chunk search.
- Mark metadata `stage2_channel="chunk_vector_global"`.
- Aggregate these hits through the C scoring path when C patent scoring is enabled, or merge them into B candidate pool when only global chunk recall is enabled.
- Ensure the global chunk search is additional. Do not replace candidate chunk search, and do not run it when only B convergence is enabled.
- When C patent scoring is enabled and graph context contains `stage2_patent_candidates`, do not use those graph candidates as the exclusive vector/chunk `patent_ids` filter. Add them as `channel="graph_candidate"` hits with bounded boost metadata and let `aggregate_patent_candidates()` rank them against vector/global chunk evidence.
- Apply hard filtering only for explicit patent IDs parsed from the user question or retrieval plan.

- [ ] **Step 5: Implement table metric boost as bounded candidate boost**

Do not add global table scan. Use table supplements only for candidate patents already in the pool:

- load tables through existing table loader or `_load_table_supplements`
- compute metric/threshold coverage
- add score boost and reason
- add compact table evidence only for selected patents
- record `metadata.stage2_raw_candidate_patent_ids` and `metadata.stage2_patent_scores` so tests can prove table loading is bounded to the candidate pool and scoring reasons are visible

- [ ] **Step 6: Run C tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_retrieval_service.py::test_stage2_c_global_chunk_recall_finds_better_evidence_outside_abstract_candidates patent/tests/test_patent_retrieval_service.py::test_stage2_c_graph_candidates_do_not_hard_filter_strong_vector_candidates patent/tests/test_patent_retrieval_service.py::test_stage2_c_table_boost_loads_tables_only_for_candidate_pool patent/tests/test_patent_retrieval_scoring.py -q
```

Expected: pass.

### Task 9: End-To-End Stage2/Stage3/Stage4 Alignment

**Files:**
- Modify: `patent/server/patent/retrieval_service.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Test: extend `patent/tests/test_patent_generation_orchestrator.py`
- Test: extend `patent/tests/test_patent_stage3_evidence_loading.py`

- [ ] **Step 1: Write failing orchestrator alignment test**

Add to `test_patent_generation_orchestrator.py`:

```python
def test_orchestrator_stage3_receives_selected_stage2_source_ids_only():
    class _Runtime(_FakeRuntime):
        def stage2_targeted_retrieval(self, retrieval_plan, *, user_question, should_cancel=None, active_stream_count=None, conversation_context=None):
            self.calls.append("stage2")
            return {
                "documents": ["selected doc"],
                "metadatas": [{"patent_id": "CN115132975B"}],
                "distances": [0.1],
                "references": ["CN115132975B"],
                "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                "reference_links": [],
                "original_links": [],
                "source_ids": ["CN115132975B"],
                "metadata": {
                    "stage2_raw_candidate_count": 80,
                    "stage2_selected_patent_ids": ["CN115132975B"],
                },
            }

    runtime = _Runtime()
    result = PatentGenerationOrchestrator().run(question="q", runtime=runtime, conversation_context={})

    assert result.raw["stage3"]["source_ids"] == ["CN115132975B"]
    assert result.metadata.source_ids == ["CN115132975B"]
```

- [ ] **Step 2: Run test**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_generation_orchestrator.py::test_orchestrator_stage3_receives_selected_stage2_source_ids_only -q
```

Expected: likely pass already; keep as regression.

- [ ] **Step 3: Add log assertions if useful**

Extend existing logging tests to assert Stage2 logs include raw/selected counts when convergence is enabled.

- [ ] **Step 4: Run integration tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_stage3_evidence_loading.py -q
```

Expected: pass.

### Task 10: Full Verification

**Files:**
- No new files unless test failures require targeted fixes.

- [ ] **Step 1: Run focused patent retrieval suite**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_stage2_controls.py patent/tests/test_patent_retrieval_guardrails.py patent/tests/test_patent_retrieval_validation.py patent/tests/test_patent_retrieval_scoring.py patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_generation_orchestrator.py patent/tests/test_patent_stage3_evidence_loading.py -q
```

Expected: pass.

- [ ] **Step 2: Run cache regression tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_execution_cache.py patent/tests/test_patent_graph_kb_stage1_cache_keys.py -q
```

Expected: pass.

- [ ] **Step 3: Run relevant service tests**

Run:

```bash
PYTHONPATH=patent pytest patent/tests/test_patent_kb_service.py patent/tests/test_patent_graph_kb_rag_adapter.py -q
```

Expected: pass.

- [ ] **Step 4: Inspect git diff**

Run:

```bash
git diff --stat
git diff -- patent/server/patent/stage2_controls.py patent/server/patent/retrieval_guardrails.py patent/server/patent/retrieval_validation.py patent/server/patent/retrieval_scoring.py patent/server/patent/stages/retrieval.py patent/server/patent/retrieval_service.py patent/server/patent/runtime.py patent/server/patent/orchestrators/generation.py
```

Expected: only Stage2 B/C files and tests changed.

## Rollout Notes

- Keep `PATENT_STAGE2_CONVERGENCE_ENABLED=false` until B tests pass and a live request can confirm candidate contraction.
- Enable B before C.
- Enable C flags one by one:
  1. `PATENT_STAGE2_C_PATENT_SCORING_ENABLED`
  2. `PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED`
  3. `PATENT_STAGE2_C_TABLE_METRIC_BOOST_ENABLED`
- Do not enable graph candidates as soft boosts in B. That behavior belongs to C only.

## Implementation Handoff

Use `superpowers:subagent-driven-development` for execution. Suggested split:

1. Worker A: Task 1 and Task 2.
2. Worker B: Task 3 and Task 4.
3. Worker C: Task 5 and Task 6.
4. Worker D: Task 7 and Task 8.
5. Main agent: Task 9 and Task 10 integration review.

Workers are not alone in the codebase. Each worker must avoid reverting unrelated edits and must report changed files.
