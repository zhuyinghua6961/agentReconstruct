from fastapi.testclient import TestClient

from server_fastapi.app import create_app
from server.runtime.ordered_task_dispatcher import OrderedTaskDispatcher
from server.runtime.request_context import clear_trace_id, get_trace_id, set_trace_id


def test_stream_slot_limit_rejects_overload():
    dispatcher = OrderedTaskDispatcher(stream_max_concurrent=1, ask_executor_max_workers=2)

    first = dispatcher.try_acquire_stream_slot()
    second = dispatcher.try_acquire_stream_slot()

    assert first is not None
    assert second is None
    assert dispatcher.runtime_state()["stream_slots_available"] == 0


def test_runtime_releases_stream_slot_after_completion():
    dispatcher = OrderedTaskDispatcher(stream_max_concurrent=1, ask_executor_max_workers=2)

    slot = dispatcher.try_acquire_stream_slot()
    assert slot is not None
    slot.release()

    second = dispatcher.try_acquire_stream_slot()
    assert second is not None
    assert dispatcher.runtime_state()["stream_slots_available"] == 0


def test_health_exposes_configured_concurrency_state(monkeypatch):
    monkeypatch.setenv("PATENT_ASK_STREAM_MAX_CONCURRENT", "2")
    monkeypatch.setenv("PATENT_ASK_EXECUTOR_MAX_WORKERS", "3")
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    runtime = payload["components"]["runtime"]
    assert runtime["ready"] is True
    assert runtime["stream_slots_capacity"] == 2
    assert runtime["stream_slots_available"] == 2
    assert runtime["ask_executor_max_workers"] == 3



def test_trace_context_reset_restores_previous_value():
    outer = set_trace_id("req_outer")
    inner = set_trace_id("req_inner")

    assert get_trace_id() == "req_inner"
    clear_trace_id(inner)
    assert get_trace_id() == "req_outer"
    clear_trace_id(outer)

def test_trace_context_reuses_incoming_header(monkeypatch):
    monkeypatch.setenv("PATENT_ASK_STREAM_MAX_CONCURRENT", "2")
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health", headers={"X-Trace-ID": "req_external"})

    assert response.headers["X-Trace-ID"] == "req_external"


def test_trace_context_generates_header_when_missing():
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/health")

    generated = response.headers["X-Trace-ID"]
    assert generated.startswith("req_")
    assert len(generated) == 16
