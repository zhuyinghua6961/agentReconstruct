# fastQA Graph RAG Step Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a clear "图谱检索" processing step in the existing frontend steps panel whenever fastQA attempts graph KB routing before falling through to generation-driven RAG.

**Architecture:** Keep the frontend unchanged and reuse the existing SSE `type=step` contract consumed by `frontend-vue/src/views/Home.vue`. Add a small graph-step event helper in `fastQA/app/routers/qa.py`, emit a `graph_retrieval` processing step before synchronous graph routing starts, then update the same step to `success` after graph routing resolves to `direct_answer`, `graph_for_rag`, or `skip_graph`. Existing graph direct-answer steps remain intact.

**Tech Stack:** Python 3.10, FastAPI, Server-Sent Events, pytest, Vue 3 existing step renderer.

---

## Current Behavior

- The frontend already renders any assistant message `steps` received from SSE `type=step`.
- `fastQA/app/routers/qa.py` already emits three graph steps for `direct_answer` through `_iter_graph_kb_events()`:
  - `graph_intent`
  - `graph_query`
  - `graph_answer`
- The `graph_for_rag` path currently executes `route_graph_kb_v2()`, attaches `GraphRagPayload` to `QaKbRequest.graph_evidence`, then streams ordinary generation stages.
- The `graph_for_rag` path only merges graph diagnostics into metadata and `done`; it does not emit a user-visible graph step.
- Because `route_graph_kb_v2()` is synchronous, the only way to show that graph retrieval is currently happening is to yield a step before calling it.

## Scope

In scope:

- Add a user-visible `graph_retrieval` step for graph KB attempts in fastQA `kb_qa`.
- Keep the text explicit and non-stage-based: "图谱检索：...".
- Preserve existing `direct_answer` graph steps.
- Make the direct-answer V2 stream contract explicit: it will include one new generic `graph_retrieval` step before the existing `graph_intent`, `graph_query`, and `graph_answer` steps.
- Preserve current graph metadata behavior.
- Add focused backend SSE tests.

Out of scope:

- No frontend code changes.
- No config changes.
- No gateway changes.
- No graph planner, classifier, executor, or RAG retrieval behavior changes.
- No new SSE event type.
- No renaming of existing direct-answer steps.

## Event Contract

Use the existing step shape:

```json
{
  "type": "step",
  "step": "graph_retrieval",
  "title": "图谱检索",
  "message": "图谱检索：识别图谱意图并查询结构化知识",
  "detail": "正在尝试从知识图谱获取结构化线索",
  "status": "processing",
  "data": {}
}
```

After `route_graph_kb_v2()` returns, emit another event with the same `step` key so the frontend updates the existing row:

```json
{
  "type": "step",
  "step": "graph_retrieval",
  "title": "图谱检索",
  "message": "图谱检索：已获取结构化线索，转入文献检索与生成",
  "detail": "图谱命中 3 条结构化结果，候选 DOI 2 个",
  "status": "success",
  "data": {
    "count": 3,
    "doi_candidates_count": 2,
    "mode": "graph_for_rag"
  }
}
```

For `skip_graph`, keep the step successful unless graph routing raises an exception. The graph attempt completed and the system intentionally fell back to normal generation:

```json
{
  "type": "step",
  "step": "graph_retrieval",
  "title": "图谱检索",
  "message": "图谱检索：未命中可用结构化线索，转入文献检索",
  "detail": "未找到可直接用于增强生成的图谱线索",
  "status": "success",
  "data": {
    "count": 0,
    "doi_candidates_count": 0,
    "mode": "skip_graph"
  }
}
```

If `route_graph_kb_v2()` raises, emit `error` status for the same step before continuing fallback generation:

```json
{
  "type": "step",
  "step": "graph_retrieval",
  "title": "图谱检索",
  "message": "图谱检索：结构化查询失败，转入文献检索",
  "detail": "图谱检索失败，已自动降级为常规生成链路",
  "status": "error",
  "error": "short error message",
  "data": {
    "mode": "error"
  }
}
```

## File Structure

Modify:

- `fastQA/app/routers/qa.py`
  - Add a small helper that builds graph retrieval step events from mode and diagnostics.
  - Emit processing/success/error step events around `route_graph_kb_v2()`.

Test:

- `fastQA/tests/test_fastqa_kb_graph_integration.py`
  - Add stream tests for `graph_for_rag`, `skip_graph`, and graph-routing exception fallback.
  - Extend or reuse existing fake generation fixtures.

No frontend files should be modified. The existing frontend step renderer already handles the event contract.

## Acceptance Criteria

1. In streaming `kb_qa`, graph V2 attempts emit a `graph_retrieval` processing step before graph routing blocks.
2. `graph_for_rag` emits a second `graph_retrieval` success step that includes result counts from diagnostics.
3. `skip_graph` emits a second `graph_retrieval` success step with clear fallback text.
4. Graph routing exceptions emit a `graph_retrieval` error step, then continue the existing generation fallback.
5. Direct graph answer emits the new generic `graph_retrieval` step and still emits existing `graph_intent`, `graph_query`, and `graph_answer` steps.
6. Existing graph metadata fields still merge into metadata and `done` events.
7. Existing sync `/api/ask` JSON payload behavior remains unchanged except for any already ignored intermediate step events.

---

### Task 1: Add Graph Step Contract Tests

**Files:**
- Modify: `fastQA/tests/test_fastqa_kb_graph_integration.py`

- [ ] **Step 1: Add a local SSE parsing helper if one does not already exist**

Add near the existing test helpers:

```python
import json
from types import SimpleNamespace


def _sse_payloads(text: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for frame in str(text or "").split("\n\n"):
        data_lines = [
            line.removeprefix("data:").strip()
            for line in frame.splitlines()
            if line.strip().startswith("data:")
        ]
        if not data_lines:
            continue
        payloads.append(json.loads("\n".join(data_lines)))
    return payloads


class _FakeRequest:
    def __init__(self, app_instance, path: str = "/api/v1/ask_stream"):
        self.app = app_instance
        self.headers = {}
        self.url = SimpleNamespace(path=path)

    async def is_disconnected(self) -> bool:
        return False
```

- [ ] **Step 2: Write failing generator-level test proving processing is yielded before graph routing blocks**

Add this test. It is the core timing guard: it advances `_iter_route_events()` directly and proves the processing event is yielded before `route_graph_kb_v2()` is called.

```python
def test_iter_route_events_yields_graph_processing_step_before_route_graph_call(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(
        app.state,
        "settings",
        replace(
            app.state.settings,
            graph_kb_enabled=True,
            graph_kb_v2_enabled=True,
            graph_kb_rag_injection_enabled=True,
        ),
    )
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()
    route_calls: list[str] = []

    def _fake_route_graph(**kwargs):
        route_calls.append("called")
        return GraphRoutingResult(
            mode="graph_for_rag",
            rag_payload=qa_router_module.GraphRagPayload(
                stage1_context_block="graph_route_family: hybrid",
                stage2_doi_candidates=("10.1000/test",),
                stage4_fact_block="structured graph facts",
                cache_fingerprint="graph:test",
            ),
            diagnostics={
                "tri_state_mode": "graph_for_rag",
                "graph_execution_mode": "graph_for_rag",
                "graph_result_count": 1,
                "graph_doi_candidates_count": 1,
            },
        )

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation with graph evidence"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module, "route_graph_kb_v2", _fake_route_graph)
    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    adapted_request = qa_router_module.GatewayAskRequest(
        question="10.1000/test 这篇文献是什么？",
        requested_mode="fast",
        actual_mode="fast",
        route="kb_qa",
        trace_id="trace-graph-step",
    )
    iterator = qa_router_module._iter_route_events(
        request=_FakeRequest(app),
        adapted_request=adapted_request,
        route="kb_qa",
        file_context=None,
        should_cancel=lambda: False,
    )

    first = next(iterator)

    assert first["type"] == "step"
    assert first["step"] == "graph_retrieval"
    assert first["status"] == "processing"
    assert route_calls == []

    remaining = list(iterator)
    assert route_calls == ["called"]
    assert any(
        event.get("type") == "step"
        and event.get("step") == "graph_retrieval"
        and event.get("status") == "success"
        for event in remaining
    )
```

- [ ] **Step 3: Run the generator-level test and verify it fails**

Run:

```bash
cd fastQA && pytest tests/test_fastqa_kb_graph_integration.py::test_iter_route_events_yields_graph_processing_step_before_route_graph_call -q
```

Expected: FAIL because the first yielded event is not a `graph_retrieval` processing step before the graph router runs.

- [ ] **Step 4: Write failing stream test for graph_for_rag visible steps**

Add this test:

```python
def test_stream_ask_graph_for_rag_emits_graph_retrieval_step(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(
        app.state,
        "settings",
        replace(
            app.state.settings,
            graph_kb_enabled=True,
            graph_kb_v2_enabled=True,
            graph_kb_rag_injection_enabled=True,
        ),
    )
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    monkeypatch.setattr(
        qa_router_module,
        "route_graph_kb_v2",
        lambda **kwargs: GraphRoutingResult(
            mode="graph_for_rag",
            rag_payload=qa_router_module.GraphRagPayload(
                stage1_context_block="graph_route_family: hybrid",
                stage2_doi_candidates=("10.1000/test", "10.1000/other"),
                stage4_fact_block="structured graph facts",
                cache_fingerprint="graph:test",
            ),
            diagnostics={
                "legacy_route_family": "hybrid",
                "tri_state_mode": "graph_for_rag",
                "graph_execution_mode": "graph_for_rag",
                "graph_strategy": "multi_stage",
                "graph_intent": "hybrid_property_analysis",
                "graph_result_count": 3,
                "graph_doi_candidates_count": 2,
            },
        ),
    )

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation with graph evidence"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/v1/ask_stream", json=_payload())

    assert response.status_code == 200
    payloads = _sse_payloads(response.text)
    graph_steps = [
        payload
        for payload in payloads
        if payload.get("type") == "step" and payload.get("step") == "graph_retrieval"
    ]
    content_index = next(
        idx
        for idx, payload in enumerate(payloads)
        if payload.get("type") == "content" and payload.get("content") == "generation with graph evidence"
    )
    graph_step_indices = [
        idx
        for idx, payload in enumerate(payloads)
        if payload.get("type") == "step" and payload.get("step") == "graph_retrieval"
    ]
    assert [step["status"] for step in graph_steps] == ["processing", "success"]
    assert graph_step_indices[0] < graph_step_indices[1] < content_index
    assert graph_steps[0]["title"] == "图谱检索"
    assert "识别图谱意图" in graph_steps[0]["message"]
    assert "转入文献检索与生成" in graph_steps[1]["message"]
    assert graph_steps[1]["data"]["count"] == 3
    assert graph_steps[1]["data"]["doi_candidates_count"] == 2
```

- [ ] **Step 5: Run the test and verify it fails**

Run:

```bash
cd fastQA && pytest tests/test_fastqa_kb_graph_integration.py::test_stream_ask_graph_for_rag_emits_graph_retrieval_step -q
```

Expected: FAIL because no `graph_retrieval` step is emitted.

- [ ] **Step 6: Write failing stream test for skip_graph visible fallback**

Add this test:

```python
def test_stream_ask_skip_graph_emits_graph_retrieval_fallback_step(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(
        app.state,
        "settings",
        replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=True),
    )
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    monkeypatch.setattr(
        qa_router_module,
        "route_graph_kb_v2",
        lambda **kwargs: GraphRoutingResult(
            mode="skip_graph",
            diagnostics={
                "tri_state_mode": "skip_graph",
                "graph_execution_mode": "skip_graph",
                "graph_result_count": 0,
                "graph_doi_candidates_count": 0,
                "graph_fallback_reason": "no_useful_graph_slots",
            },
        ),
    )

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation after skip"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/v1/ask_stream", json=_payload())

    assert response.status_code == 200
    payloads = _sse_payloads(response.text)
    graph_steps = [
        payload
        for payload in payloads
        if payload.get("type") == "step" and payload.get("step") == "graph_retrieval"
    ]
    content_index = next(
        idx
        for idx, payload in enumerate(payloads)
        if payload.get("type") == "content" and payload.get("content") == "generation after skip"
    )
    graph_step_indices = [
        idx
        for idx, payload in enumerate(payloads)
        if payload.get("type") == "step" and payload.get("step") == "graph_retrieval"
    ]
    assert [step["status"] for step in graph_steps] == ["processing", "success"]
    assert graph_step_indices[0] < graph_step_indices[1] < content_index
    assert "未命中可用结构化线索" in graph_steps[1]["message"]
    assert graph_steps[1]["data"]["mode"] == "skip_graph"
```

- [ ] **Step 7: Run the test and verify it fails**

Run:

```bash
cd fastQA && pytest tests/test_fastqa_kb_graph_integration.py::test_stream_ask_skip_graph_emits_graph_retrieval_fallback_step -q
```

Expected: FAIL because no `graph_retrieval` step is emitted.

---

### Task 2: Emit Graph Retrieval Steps Around Graph V2 Routing

**Files:**
- Modify: `fastQA/app/routers/qa.py`
- Test: `fastQA/tests/test_fastqa_kb_graph_integration.py`

- [ ] **Step 1: Add a graph retrieval step helper**

Add near `_graph_v2_metadata()` or another graph helper block in `fastQA/app/routers/qa.py`:

```python
def _coerce_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return max(0, parsed)


def _graph_retrieval_step_event(
    *,
    status: str,
    mode: str = "",
    diagnostics: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    source = dict(diagnostics or {})
    normalized_status = str(status or "processing").strip().lower() or "processing"
    normalized_mode = str(mode or source.get("graph_execution_mode") or source.get("tri_state_mode") or "").strip()
    result_count = _coerce_non_negative_int(source.get("graph_result_count"))
    doi_candidates_count = _coerce_non_negative_int(source.get("graph_doi_candidates_count"))

    if normalized_status == "processing":
        message = "图谱检索：识别图谱意图并查询结构化知识"
        detail = "正在尝试从知识图谱获取结构化线索"
    elif normalized_status == "error":
        message = "图谱检索：结构化查询失败，转入文献检索"
        detail = "图谱检索失败，已自动降级为常规生成链路"
    elif normalized_mode == "skip_graph":
        message = "图谱检索：未命中可用结构化线索，转入文献检索"
        detail = "未找到可直接用于增强生成的图谱线索"
    elif normalized_mode == "direct_answer":
        message = "图谱检索：已命中结构化答案"
        detail = f"图谱命中 {result_count} 条结构化结果"
    else:
        message = "图谱检索：已获取结构化线索，转入文献检索与生成"
        detail = f"图谱命中 {result_count} 条结构化结果，候选 DOI {doi_candidates_count} 个"

    payload: dict[str, Any] = {
        "type": "step",
        "step": "graph_retrieval",
        "title": "图谱检索",
        "message": message,
        "detail": detail,
        "status": normalized_status if normalized_status in {"processing", "success", "error"} else "processing",
        "data": {
            "count": result_count,
            "doi_candidates_count": doi_candidates_count,
            "mode": normalized_mode or ("error" if normalized_status == "error" else ""),
        },
    }
    if error:
        payload["error"] = str(error)
    return payload
```

- [ ] **Step 2: Emit processing before `route_graph_kb_v2()`**

In the `if graph_enabled and graph_v2_enabled:` branch of `_iter_route_events()`, before calling `route_graph_kb_v2()`, add:

```python
yield _graph_retrieval_step_event(status="processing")
```

- [ ] **Step 3: Emit success after `route_graph_kb_v2()` returns**

Immediately after `graph_v2_metadata` is initialized and enriched with `graph_rag_injected=False`, add:

```python
yield _graph_retrieval_step_event(
    status="success",
    mode=routing_result.mode,
    diagnostics=routing_result.diagnostics,
)
```

Keep this before the `direct_answer`, `graph_for_rag`, and `skip_graph` branches so every resolved graph V2 attempt updates the same visible row.

- [ ] **Step 4: Emit error before exception fallback**

Inside the existing `except Exception as exc:` for graph V2 routing, before the warning log or immediately after it, add:

```python
yield _graph_retrieval_step_event(
    status="error",
    mode="error",
    diagnostics={},
    error=str(exc) or exc.__class__.__name__,
)
```

Then preserve the current fallback behavior.

- [ ] **Step 5: Run focused tests**

Run:

```bash
cd fastQA && pytest tests/test_fastqa_kb_graph_integration.py::test_iter_route_events_yields_graph_processing_step_before_route_graph_call tests/test_fastqa_kb_graph_integration.py::test_stream_ask_graph_for_rag_emits_graph_retrieval_step tests/test_fastqa_kb_graph_integration.py::test_stream_ask_skip_graph_emits_graph_retrieval_fallback_step -q
```

Expected: PASS.

---

### Task 3: Protect Direct Answer And Error Fallback Behavior

**Files:**
- Modify: `fastQA/tests/test_fastqa_kb_graph_integration.py`
- Modify if needed: `fastQA/app/routers/qa.py`

- [ ] **Step 1: Add test that direct answer still emits existing graph direct steps**

Extend or add a streaming test based on the existing direct-answer fixtures:

```python
def test_stream_ask_direct_answer_keeps_existing_graph_steps(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=True))
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    monkeypatch.setattr(
        qa_router_module,
        "route_graph_kb_v2",
        lambda **kwargs: GraphRoutingResult(
            mode="direct_answer",
            direct_result=GraphKbExecutionResult(
                handled=True,
                answer="graph v2 answer",
                references=("10.1000/test",),
                query_mode="graph_kb",
                template_id="lookup_by_doi",
                result_count=1,
            ),
            diagnostics={
                "tri_state_mode": "direct_answer",
                "graph_execution_mode": "direct_answer",
                "graph_result_count": 1,
            },
        ),
    )
    monkeypatch.setattr(
        qa_router_module.qa_kb_service,
        "iter_answer_events",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generation path should not run")),
    )

    response = client.post("/api/v1/ask_stream", json=_payload())

    assert response.status_code == 200
    payloads = _sse_payloads(response.text)
    step_keys = [payload.get("step") for payload in payloads if payload.get("type") == "step"]
    assert "graph_retrieval" in step_keys
    assert "graph_intent" in step_keys
    assert "graph_query" in step_keys
    assert "graph_answer" in step_keys
```

- [ ] **Step 2: Add graph routing exception fallback test**

Add:

```python
def test_stream_ask_graph_v2_exception_emits_error_step_then_generation(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=True))
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    def _raise_graph_error(**kwargs):
        raise RuntimeError("neo4j timeout")

    monkeypatch.setattr(qa_router_module, "route_graph_kb_v2", _raise_graph_error)

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "fallback generation"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/v1/ask_stream", json=_payload())

    assert response.status_code == 200
    payloads = _sse_payloads(response.text)
    graph_steps = [
        payload
        for payload in payloads
        if payload.get("type") == "step" and payload.get("step") == "graph_retrieval"
    ]
    assert [step["status"] for step in graph_steps] == ["processing", "error"]
    assert graph_steps[1]["error"] == "neo4j timeout"
    assert any(payload.get("type") == "content" and payload.get("content") == "fallback generation" for payload in payloads)
```

- [ ] **Step 3: Run direct-answer and error fallback tests**

Run:

```bash
cd fastQA && pytest tests/test_fastqa_kb_graph_integration.py::test_stream_ask_direct_answer_keeps_existing_graph_steps tests/test_fastqa_kb_graph_integration.py::test_stream_ask_graph_v2_exception_emits_error_step_then_generation -q
```

Expected: PASS.

- [ ] **Step 4: Run full graph integration test file**

Run:

```bash
cd fastQA && pytest tests/test_fastqa_kb_graph_integration.py -q
```

Expected: PASS.

---

### Task 4: Verify Frontend Contract Without Editing Frontend

**Files:**
- No production frontend file changes.
- Optional Test: `frontend-vue/src/views/Home.structure.test.js` only if an implementer wants an explicit guard that `type=step` continues to render generically.

- [ ] **Step 1: Confirm no frontend implementation is needed**

Read these existing locations:

- `frontend-vue/src/views/Home.vue` `applyGatewayEvent()` handles `data.type === 'step'`.
- `frontend-vue/src/views/Home.vue` steps panel renders `entry.message.steps`.

No change should be made unless tests show the generic step path no longer works.

- [ ] **Step 2: Run existing frontend structure checks if frontend files were touched**

Only if a frontend file was modified, run:

```bash
cd frontend-vue && node --test src/views/Home.structure.test.js
cd frontend-vue && npm run build
```

Expected: PASS.

For this plan's intended backend-only implementation, these commands are optional.

---

### Task 5: Final Verification

**Files:**
- No new files beyond tests and `fastQA/app/routers/qa.py`.

- [ ] **Step 1: Run focused backend verification**

Run:

```bash
cd fastQA && pytest tests/test_fastqa_kb_graph_integration.py -q
```

Expected: PASS.

- [ ] **Step 2: Run graph service regression tests**

Run:

```bash
cd fastQA && pytest tests/test_graph_kb_service.py tests/test_graph_kb_classifier_v2.py tests/test_graph_kb_rag_adapter.py -q
```

Expected: PASS.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git diff -- fastQA/app/routers/qa.py fastQA/tests/test_fastqa_kb_graph_integration.py
```

Expected:

- `fastQA/app/routers/qa.py` only adds graph step helper/events around graph V2 routing.
- `fastQA/tests/test_fastqa_kb_graph_integration.py` only adds focused stream tests and an SSE parsing helper.
- No frontend, gateway, config, graph planner, or graph executor files changed.

- [ ] **Step 4: Commit**

Run:

```bash
git add fastQA/app/routers/qa.py fastQA/tests/test_fastqa_kb_graph_integration.py
git commit -m "feat: show graph retrieval step during fastqa rag"
```

Expected: commit succeeds with only the planned implementation files staged.
