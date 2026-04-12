from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import app
import app.routers.qa as qa_router_module


client = TestClient(app)


def _payload() -> dict[str, object]:
    return {
        "question": "10.1000/test 这篇文献是什么？",
        "requested_mode": "fast",
        "route": "kb_qa",
    }


def _enable_graph_kb(monkeypatch):
    monkeypatch.setattr(app.state, "settings", replace(app.state.settings, graph_kb_enabled=True))
    monkeypatch.setattr(app.state, "persist_user_message_hook", None)
    monkeypatch.setattr(app.state, "load_conversation_context_hook", None)
    monkeypatch.setattr(app.state, "persist_assistant_summary_hook", None)
    monkeypatch.setattr(app.state, "persist_assistant_terminal_hook", None)


def test_sync_ask_returns_graph_answer_when_handled(monkeypatch):
    _enable_graph_kb(monkeypatch)
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    def _fake_graph_answer(**kwargs):
        return qa_router_module.GraphKbExecutionResult(
            handled=True,
            answer="graph answer",
            references=("10.1000/test",),
            query_mode="graph_kb",
            template_id="lookup_by_doi",
            result_count=1,
            latency_ms=12.5,
        )

    monkeypatch.setattr(qa_router_module, "try_graph_kb_answer", _fake_graph_answer)
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
    monkeypatch.setattr(qa_router_module, "generation_runtime_is_ready", lambda runtime: True)
    app.state.generation_runtime = object()

    monkeypatch.setattr(
        qa_router_module,
        "try_graph_kb_answer",
        lambda **kwargs: qa_router_module.GraphKbExecutionResult(
            handled=True,
            answer="graph answer",
            references=("10.1000/test",),
            query_mode="graph_kb",
            template_id="lookup_by_doi",
            result_count=1,
            latency_ms=8.5,
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
