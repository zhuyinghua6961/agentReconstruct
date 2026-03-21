from __future__ import annotations

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
