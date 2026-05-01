from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
import app.modules.graph_kb.service as graph_kb_service
import app.routers.qa as qa_router_module
from app.modules.graph_kb.models import GraphKbExecutionResult, GraphRoutingResult


client = TestClient(app)


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


def _payload() -> dict[str, object]:
    return {
        "question": "10.1000/test 这篇文献是什么？",
        "requested_mode": "fast",
        "route": "kb_qa",
    }


def _enable_graph_kb(monkeypatch):
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=False))
    monkeypatch.setattr(app.state, "persist_user_message_hook", None)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", None)
    monkeypatch.setattr(app.state, "persist_assistant_summary_hook", None)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", None)


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


def test_sync_ask_returns_graph_answer_when_handled(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(
        app.state,
        "settings",
        replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=True),
    )
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    def _fake_graph_route(**kwargs):
        return GraphRoutingResult(
            mode="direct_answer",
            diagnostics={"tri_state_mode": "direct_answer"},
            direct_result=qa_router_module.GraphKbExecutionResult(
                handled=True,
                answer="graph answer",
                references=("10.1000/test",),
                query_mode="graph_kb",
                template_id="lookup_by_doi",
                result_count=1,
                latency_ms=12.5,
            ),
        )

    monkeypatch.setattr(qa_router_module, "route_graph_kb_v2", _fake_graph_route)
    monkeypatch.setattr(
        qa_router_module.qa_kb_service,
        "iter_answer_events",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generation path should not run")),
    )

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["route"] == "kb_qa"
    assert payload["final_answer"] == "graph answer"
    assert payload["references"] == ["10.1000/test"]
    assert payload["query_mode"] == "graph_kb"
    assert payload["reference_links"] == [{"doi": "10.1000/test", "pdf_url": "/api/v1/view_pdf/10.1000/test"}]
    assert payload["pdf_links"] == payload["reference_links"]
    assert payload["doi_locations"] == {}


def test_sync_ask_uses_graph_direct_answer_when_mode_is_direct_answer(monkeypatch):
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
            diagnostics={"legacy_route": "precise"},
        ),
    )
    monkeypatch.setattr(
        qa_router_module.qa_kb_service,
        "iter_answer_events",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generation path should not run")),
    )

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_answer"] == "graph v2 answer"
    assert payload["query_mode"] == "graph_kb"
    assert payload["references"] == ["10.1000/test"]
    assert payload["reference_links"] == [{"doi": "10.1000/test", "pdf_url": "/api/v1/view_pdf/10.1000/test"}]
    assert payload["metadata"]["graph_rag_injected"] is False


def test_sync_ask_graph_v2_metadata_exposes_pipeline_version_and_legacy_route_family(monkeypatch):
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
                "graph_pipeline_version": "v2",
                "legacy_route_family": "precise",
                "tri_state_mode": "direct_answer",
                "neo4j_client": "neo4jgraph",
                "doi_source": "none",
                "legacy_template_fallback_used": False,
            },
        ),
    )
    monkeypatch.setattr(
        qa_router_module.qa_kb_service,
        "iter_answer_events",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generation path should not run")),
    )

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["graph_pipeline_version"] == "v2"
    assert payload["metadata"]["knowledge_route_family"] == "precise"
    assert payload["metadata"]["legacy_route_family"] == "precise"
    assert payload["metadata"]["tri_state_mode"] == "direct_answer"
    assert payload["metadata"]["graph_execution_mode"] == "direct_answer"
    assert payload["metadata"]["graph_rag_injection_enabled"] is True
    assert payload["metadata"]["graph_rag_injected"] is False
    assert payload["metadata"]["legacy_template_fallback_used"] is False


def test_sync_ask_graph_v2_metadata_exposes_strategy_and_intent(monkeypatch):
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
                "graph_pipeline_version": "v2",
                "knowledge_route_family": "precise",
                "legacy_route_family": "precise",
                "tri_state_mode": "direct_answer",
                "graph_strategy": "template",
                "graph_intent": "lookup_by_doi",
                "graph_result_count": 1,
            },
        ),
    )
    monkeypatch.setattr(
        qa_router_module.qa_kb_service,
        "iter_answer_events",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generation path should not run")),
    )

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    metadata = response.json()["metadata"]
    assert metadata["graph_strategy"] == "template"
    assert metadata["graph_intent"] == "lookup_by_doi"
    assert metadata["graph_result_count"] == 1


def test_sync_ask_graph_v2_metadata_exposes_neo4j_client_choice(monkeypatch):
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
                "graph_pipeline_version": "v2",
                "legacy_route_family": "precise",
                "tri_state_mode": "direct_answer",
                "neo4j_client": "neo4jgraph",
                "doi_source": "none",
                "legacy_template_fallback_used": False,
            },
        ),
    )
    monkeypatch.setattr(
        qa_router_module.qa_kb_service,
        "iter_answer_events",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generation path should not run")),
    )

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["neo4j_client"] == "neo4jgraph"


def test_sync_ask_skips_graph_v2_when_feature_flag_disabled(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=False))
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    monkeypatch.setattr(
        qa_router_module,
        "route_graph_kb_v2",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("graph v2 should not run when disabled")),
    )
    monkeypatch.setattr(
        qa_router_module,
        "try_graph_kb_answer",
        lambda **kwargs: GraphKbExecutionResult(handled=False, fallback_reason="legacy_skip"),
    )

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation when v2 disabled"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_answer"] == "generation when v2 disabled"
    assert payload["query_mode"] == "生成驱动检索"


def test_sync_ask_goes_straight_to_generation_when_mode_is_skip_graph(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=True))
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    monkeypatch.setattr(
        qa_router_module,
        "route_graph_kb_v2",
        lambda **kwargs: GraphRoutingResult(mode="skip_graph", diagnostics={"legacy_route": "semantic"}),
    )
    monkeypatch.setattr(
        qa_router_module,
        "try_graph_kb_answer",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("legacy graph path should not run")),
    )

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation after skip_graph"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_answer"] == "generation after skip_graph"
    assert payload["query_mode"] == "生成驱动检索"
    assert payload["metadata"]["graph_rag_injection_enabled"] is True
    assert payload["metadata"]["graph_rag_injected"] is False


def test_sync_ask_passes_graph_payload_into_generation_when_mode_is_graph_for_rag(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=True, graph_kb_rag_injection_enabled=True))
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        qa_router_module,
        "route_graph_kb_v2",
        lambda **kwargs: GraphRoutingResult(
            mode="graph_for_rag",
            rag_payload=qa_router_module.GraphRagPayload(
                stage1_context_block="doi:10.1000/test",
                stage2_doi_candidates=("10.1000/test",),
                stage4_fact_block="structured graph facts",
                cache_fingerprint="graph:abc",
            ),
            diagnostics={"legacy_route": "semantic"},
        ),
    )

    def _fake_generation(**kwargs):
        captured["request"] = kwargs["request"]
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation with graph evidence"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["query_mode"] == "生成驱动检索"
    assert payload["metadata"]["graph_rag_injection_enabled"] is True
    assert payload["metadata"]["graph_rag_injected"] is True
    assert captured["request"].graph_evidence is not None


def test_sync_ask_respects_hidden_disabled_rag_injection_flag(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(
        app.state,
        "settings",
        replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=True, graph_kb_rag_injection_enabled=False),
    )
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        qa_router_module,
        "route_graph_kb_v2",
        lambda **kwargs: GraphRoutingResult(
            mode="graph_for_rag",
            rag_payload=qa_router_module.GraphRagPayload(
                stage1_context_block="doi:10.1000/test",
                stage2_doi_candidates=("10.1000/test",),
                stage4_fact_block="structured graph facts",
                cache_fingerprint="graph:abc",
            ),
            diagnostics={"legacy_route": "semantic"},
        ),
    )

    def _fake_generation(**kwargs):
        captured["request"] = kwargs["request"]
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation without graph evidence"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert captured["request"].graph_evidence is None
    assert payload["metadata"]["graph_rag_injection_enabled"] is False
    assert payload["metadata"]["graph_rag_injected"] is False


def test_community_route_attaches_graph_payload_to_generation(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, graph_kb_enabled=True, graph_kb_v2_enabled=True, graph_kb_rag_injection_enabled=True))
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        qa_router_module,
        "route_graph_kb_v2",
        lambda **kwargs: GraphRoutingResult(
            mode="graph_for_rag",
            rag_payload=qa_router_module.GraphRagPayload(
                stage1_context_block="graph_route: community",
                stage2_entity_hints={"community_labels": ("LiFePO4 synthesis cluster",)},
                stage4_fact_block="community graph facts",
                cache_fingerprint="graph:community",
            ),
            diagnostics={
                "knowledge_route_family": "community",
                "legacy_route_family": "community",
                "tri_state_mode": "graph_for_rag",
                "graph_strategy": "v1_template",
                "graph_intent": "community_find_by_term",
            },
        ),
    )

    def _fake_generation(**kwargs):
        captured["request"] = kwargs["request"]
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "community generation"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/ask", json={**_payload(), "question": "LiFePO4的关系网络和机制关联是什么？"})

    assert response.status_code == 200
    assert captured["request"].graph_evidence is not None
    assert captured["request"].graph_evidence.stage2_entity_hints["community_labels"] == ("LiFePO4 synthesis cluster",)
    assert response.json()["metadata"]["knowledge_route_family"] == "community"


def test_sync_ask_falls_back_to_generation_when_graph_not_handled(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    monkeypatch.setattr(
        qa_router_module,
        "try_graph_kb_answer",
        lambda **kwargs: qa_router_module.GraphKbExecutionResult(handled=False, fallback_reason="skip"),
    )

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation answer"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["final_answer"] == "generation answer"
    assert payload["query_mode"] == "生成驱动检索"


def test_sync_ask_falls_back_to_generation_when_real_graph_path_filters_all_invalid_dois(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()
    app.state.neo4j_client = object()

    monkeypatch.setattr(
        graph_kb_service,
        "execute_graph_kb_plan",
        lambda *args, **kwargs: [
            {
                "doi": "10.1007/s12598-",
                "title": "Broken Paper",
                "matched_raw_materials": ["LiFePO4 powder"],
            }
        ],
    )

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation answer after invalid graph doi"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post(
        "/api/ask",
        json={
            "question": "有哪些使用LiFePO4作为原料的文献？",
            "requested_mode": "fast",
            "route": "kb_qa",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["final_answer"] == "generation answer after invalid graph doi"
    assert payload["query_mode"] == "生成驱动检索"
    assert payload["references"] == []


def test_sync_ask_falls_back_to_generation_for_material_wording_not_supported_by_graph(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()
    app.state.neo4j_client = object()

    captured_question: dict[str, object] = {}

    def _fake_generation(**kwargs):
        captured_question["question"] = kwargs["request"].question
        yield {"type": "metadata", "query_mode": "生成驱动检索", "route": "kb_qa"}
        yield {"type": "content", "content": "generation fallback for material wording"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post(
        "/api/ask",
        json={
            "question": "有哪些使用LiFePO4作为材料的文献？",
            "requested_mode": "fast",
            "route": "kb_qa",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["final_answer"] == "generation fallback for material wording"
    assert payload["query_mode"] == "生成驱动检索"
    assert captured_question["question"] == "有哪些使用LiFePO4作为材料的文献？"


def test_sync_ask_falls_back_to_generation_when_graph_raises(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    monkeypatch.setattr(
        qa_router_module,
        "try_graph_kb_answer",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    def _fake_generation(**kwargs):
        yield {"type": "metadata", "query_mode": "kb_qa", "route": "kb_qa"}
        yield {"type": "content", "content": "fallback answer"}
        yield {"type": "done", "route": "kb_qa", "references": []}

    monkeypatch.setattr(qa_router_module.qa_kb_service, "iter_answer_events", _fake_generation)

    response = client.post("/api/ask", json=_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["final_answer"] == "fallback answer"


def test_stream_ask_emits_graph_metadata_step_content_and_done(monkeypatch):
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
            mode="direct_answer",
            diagnostics={"tri_state_mode": "direct_answer"},
            direct_result=qa_router_module.GraphKbExecutionResult(
                handled=True,
                answer="graph answer",
                references=("10.1000/test",),
                query_mode="graph_kb",
                template_id="lookup_by_doi",
                result_count=1,
                latency_ms=8.5,
            ),
        ),
    )
    monkeypatch.setattr(
        qa_router_module.qa_kb_service,
        "iter_answer_events",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generation path should not run")),
    )

    response = client.post("/api/v1/ask", json=_payload())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "metadata"' in response.text
    assert '"query_mode": "graph_kb"' in response.text
    assert '"type": "step"' in response.text
    assert '阶段一：识别知识图谱意图' in response.text
    assert '阶段二：执行图谱检索' in response.text
    assert '阶段三：整理图谱结果' in response.text
    assert '"type": "content"' in response.text
    assert '"type": "done"' in response.text
    assert '"reference_links"' in response.text
    assert '"pdf_links"' in response.text
    assert '"doi_locations"' in response.text


def test_iter_graph_kb_events_emits_three_success_steps():
    events = list(
        qa_router_module._iter_graph_kb_events(
            result=qa_router_module.GraphKbExecutionResult(
                handled=True,
                answer="graph answer",
                references=("10.1000/test",),
                query_mode="graph_kb",
                template_id="expand_doi_context_by_doi",
                result_count=2,
                latency_ms=8.5,
            ),
            trace_id="trace-1",
            route="kb_qa",
        )
    )

    steps = [event for event in events if event.get("type") == "step"]

    assert len(steps) == 3
    assert [step["step"] for step in steps] == [
        "graph_intent",
        "graph_query",
        "graph_answer",
    ]
    assert all(step["status"] == "success" for step in steps)
    assert steps[1]["detail"] == "按 DOI 展开测试/工艺"
    assert steps[2]["data"]["count"] == 2

    done_event = next(event for event in events if event.get("type") == "done")
    assert done_event["references"] == ["10.1000/test"]
    assert "reference_links" not in done_event
    assert "pdf_links" not in done_event
    assert "doi_locations" not in done_event
