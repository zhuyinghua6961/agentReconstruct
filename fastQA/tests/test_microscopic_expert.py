from __future__ import annotations

from contextlib import contextmanager
from threading import Event
import time

from app.modules.microscopic_expert import MicroscopicSemanticExpert


def test_microscopic_expert_returns_empty_results_when_backend_unavailable(monkeypatch):
    monkeypatch.setattr("app.modules.microscopic_expert.CHROMADB_AVAILABLE", False)

    expert = MicroscopicSemanticExpert()
    result = expert.search("lfp", n_results=3)

    assert expert.available is False
    assert result["documents"] == []
    assert "unavailable" in result["rerank"]["reason"]


def test_microscopic_expert_search_wires_rerank_function(monkeypatch):
    calls = {}

    def _fake_run_semantic_search(**kwargs):
        calls.update(kwargs)
        return {"documents": ["doc"], "metadatas": [], "distances": [], "ids": [], "rerank": {"enabled": True}}

    monkeypatch.setattr("app.modules.microscopic_expert.run_semantic_search", _fake_run_semantic_search)
    monkeypatch.setattr("app.modules.microscopic_expert.CHROMADB_AVAILABLE", True)

    expert = MicroscopicSemanticExpert.__new__(MicroscopicSemanticExpert)
    expert.available = True
    expert.embedding_model = object()
    expert.collection = object()
    expert.translator = None
    expert.client = None

    result = expert.search("lfp", n_results=4, use_rerank=True, rerank_candidates=12)

    assert result["rerank"]["enabled"] is True
    assert calls["use_rerank"] is True
    assert calls["rerank_candidates"] == 12
    assert callable(calls["rerank_fn"])


def test_microscopic_expert_leases_rerank_session_when_pool_available(monkeypatch):
    calls = {}

    class _LanePool:
        def __init__(self) -> None:
            self.lease_called = False

        @contextmanager
        def lease_lane(self, *, trace_label=None):
            self.lease_called = True
            yield type("Lane", (), {"session": "leased-session"})()

    def _fake_rerank_documents(**kwargs):
        calls.update(kwargs)
        return {"documents": ["doc"], "metadatas": [], "rerank_scores": [0.9], "fallback": False, "provider": "test"}

    monkeypatch.setattr("app.modules.microscopic_expert.rerank_documents_impl", _fake_rerank_documents)
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_MODEL", "qwen3-vl-rerank")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_BASE_URL", "https://dashscope.aliyuncs.com")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_TIMEOUT", "20")

    expert = MicroscopicSemanticExpert.__new__(MicroscopicSemanticExpert)
    expert.rerank_session_pool = _LanePool()

    result = expert._rerank_documents(query="lfp", documents=["doc1"], metadatas=[{"doi": "10.1/a"}], top_n=1)

    assert result["provider"] == "test"
    assert expert.rerank_session_pool.lease_called is True
    assert calls["session"] == "leased-session"


def test_microscopic_expert_wraps_rerank_http_call_with_gate(monkeypatch):
    calls = {}

    class _Gate:
        def __init__(self) -> None:
            self.enter_called = False
            self.trace_labels: list[str | None] = []

        @contextmanager
        def enter(self, *, trace_label=None):
            self.enter_called = True
            self.trace_labels.append(trace_label)
            yield

    def _fake_rerank_documents(**kwargs):
        calls.update(kwargs)
        return {"documents": ["doc"], "metadatas": [], "rerank_scores": [0.9], "fallback": False, "provider": "test"}

    monkeypatch.setattr("app.modules.microscopic_expert.rerank_documents_impl", _fake_rerank_documents)

    expert = MicroscopicSemanticExpert.__new__(MicroscopicSemanticExpert)
    expert.rerank_session_pool = None
    gate = _Gate()

    result = expert._rerank_documents(
        query="lfp",
        documents=["doc1"],
        metadatas=[{"doi": "10.1/a"}],
        top_n=1,
        rerank_gate=gate,
        trace_label="claim_1",
    )

    assert result["provider"] == "test"
    assert gate.enter_called is True
    assert gate.trace_labels == ["claim_1"]


def test_microscopic_expert_aborts_rerank_lane_when_cancelled(monkeypatch):
    started = Event()
    cancel_request = Event()

    class _LanePool:
        def __init__(self) -> None:
            self.abort_calls: list[tuple[int, str]] = []

        @contextmanager
        def lease_lane(self, *, trace_label=None):
            yield type("Lane", (), {"session": "leased-session", "lane_id": 0})()

        def abort_lane(self, lane_id: int, *, error_summary: str = "cancelled") -> None:
            self.abort_calls.append((lane_id, error_summary))

    def _fake_rerank_documents(**kwargs):
        started.set()
        while not cancel_request.is_set():
            time.sleep(0.01)
        time.sleep(0.2)
        return {"documents": ["doc"], "metadatas": [], "rerank_scores": [0.9], "fallback": False, "provider": "test"}

    monkeypatch.setattr("app.modules.microscopic_expert.rerank_documents_impl", _fake_rerank_documents)

    expert = MicroscopicSemanticExpert.__new__(MicroscopicSemanticExpert)
    expert.rerank_session_pool = _LanePool()

    def _should_cancel() -> bool:
        if started.is_set():
            cancel_request.set()
        return cancel_request.is_set()

    try:
        expert._rerank_documents(
            query="lfp",
            documents=["doc1"],
            metadatas=[{"doi": "10.1/a"}],
            top_n=1,
            should_cancel=_should_cancel,
        )
    except Exception as exc:
        assert exc.__class__.__name__ == "Stage2UpstreamGateCancelled"
    else:  # pragma: no cover - enforced by failing test before fix
        raise AssertionError("expected rerank cancellation to abort the leased lane")

    assert expert.rerank_session_pool.abort_calls == [(0, "cancelled")]


def test_microscopic_expert_cancellation_keeps_background_rerank_bound_to_original_session(monkeypatch):
    started = Event()
    allow_call = Event()
    cancel_request = Event()
    recorded_session = Event()
    sessions_used: list[object] = []

    class _Lane:
        def __init__(self) -> None:
            self.session = object()
            self.lane_id = 0

    class _LanePool:
        def __init__(self) -> None:
            self.lane = _Lane()

        @contextmanager
        def lease_lane(self, *, trace_label=None):
            yield self.lane

        def abort_lane(self, lane_id: int, *, error_summary: str = "cancelled") -> None:
            assert lane_id == 0
            self.lane.session = object()

    def _fake_rerank_documents(**kwargs):
        started.set()
        allow_call.wait(1.0)
        sessions_used.append(kwargs["session"])
        recorded_session.set()
        raise RuntimeError("closed session")

    monkeypatch.setattr("app.modules.microscopic_expert.rerank_documents_impl", _fake_rerank_documents)

    expert = MicroscopicSemanticExpert.__new__(MicroscopicSemanticExpert)
    expert.rerank_session_pool = _LanePool()
    original_session = expert.rerank_session_pool.lane.session

    def _should_cancel() -> bool:
        if started.is_set():
            cancel_request.set()
        return cancel_request.is_set()

    try:
        expert._rerank_documents(
            query="lfp",
            documents=["doc1"],
            metadatas=[{"doi": "10.1/a"}],
            top_n=1,
            should_cancel=_should_cancel,
        )
    except Exception as exc:
        assert exc.__class__.__name__ == "Stage2UpstreamGateCancelled"
    else:  # pragma: no cover - enforced by failing test before fix
        raise AssertionError("expected rerank cancellation to abort the leased lane")

    allow_call.set()
    assert recorded_session.wait(1.0) is True
    assert sessions_used == [original_session]
