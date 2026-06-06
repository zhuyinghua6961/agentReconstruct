from __future__ import annotations

import threading
import time

from server.services.redis_client import RedisService


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = int(ex)
        return True

    def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            if key in self.values:
                deleted += 1
                self.values.pop(key, None)
                self.expirations.pop(key, None)
        return deleted


def _fake_redis_service() -> RedisService:
    return RedisService.from_prefix(client=_FakeRedis(), key_prefix="highthinking")


def test_run_agent_does_not_reuse_stage_cache_by_default(monkeypatch):
    from agent_core.graph import run_agent
    from server.services import stage_cache as stage_cache_module

    monkeypatch.delenv("HT_QA_STAGE_CACHE_ENABLED", raising=False)
    direct_calls = {"n": 0}
    decompose_calls = {"n": 0}

    monkeypatch.setattr("agent_core.graph.get_llm_client", lambda *args, **kwargs: object())
    monkeypatch.setattr("agent_core.graph.get_async_llm_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_embedding_client", lambda: object())
    monkeypatch.setattr("agent_core.graph.get_or_create_collection", lambda: object())
    monkeypatch.setattr("agent_core.graph._run_pre_answer_retrieval_pipeline", lambda **kwargs: (["a1"], [[]], {"pre_answer_completed_at": 0.1, "retrieval_completed_at": 0.2}))
    monkeypatch.setattr("agent_core.graph.synthesize_answer", lambda **kwargs: "draft")
    service = _fake_redis_service()
    monkeypatch.setattr(stage_cache_module, "get_redis_service", lambda: service)

    def fake_direct_answer(*args, **kwargs):
        direct_calls["n"] += 1
        return "direct"

    def fake_decompose_question(*args, **kwargs):
        decompose_calls["n"] += 1
        return ["q1"]

    monkeypatch.setattr("agent_core.graph.direct_answer", fake_direct_answer)
    monkeypatch.setattr("agent_core.graph.decompose_question", fake_decompose_question)

    first = run_agent("demo", max_check_loops=0)
    second = run_agent("demo", max_check_loops=0)

    assert first.error == ""
    assert second.error == ""
    assert direct_calls["n"] == 2
    assert decompose_calls["n"] == 2


def test_batch_retrieve_does_not_reuse_stage_cache_by_default(monkeypatch):
    from retriever import vector_retriever
    from server.services import stage_cache as stage_cache_module

    monkeypatch.delenv("HT_QA_STAGE_CACHE_ENABLED", raising=False)
    embedding_calls = {"n": 0}

    service = _fake_redis_service()
    monkeypatch.setattr(stage_cache_module, "get_redis_service", lambda: service)
    monkeypatch.setattr("retriever.vector_retriever.get_redis_service", lambda: service)
    monkeypatch.setattr("retriever.vector_retriever.get_embedding_client", lambda: object())
    monkeypatch.setattr("retriever.vector_retriever.get_or_create_collection", lambda: object())

    def fake_embed_texts(texts, client=None):
        _ = client
        embedding_calls["n"] += 1
        return [[0.1] for _ in texts]

    monkeypatch.setattr("retriever.vector_retriever.embed_texts", fake_embed_texts)
    monkeypatch.setattr(
        "retriever.vector_retriever.batch_query_collection",
        lambda query_embeddings, top_k=None, collection=None: {
            "ids": [["a"] for _ in query_embeddings],
            "documents": [["doc"] for _ in query_embeddings],
            "metadatas": [[{"doi": "10.1/a", "title": "Demo", "section_name": "Intro", "chunk_index": 0}] for _ in query_embeddings],
            "distances": [[0.1] for _ in query_embeddings],
        },
    )

    first = vector_retriever.batch_retrieve(["q1"], top_k=3, collection=object(), embedding_client=object())
    second = vector_retriever.batch_retrieve(["q1"], top_k=3, collection=object(), embedding_client=object())

    assert len(first) == 1
    assert len(second) == 1
    assert embedding_calls["n"] == 2


def test_batch_retrieve_singleflights_same_query_when_stage_cache_enabled(monkeypatch):
    from retriever import vector_retriever
    from server.services import stage_cache as stage_cache_module

    monkeypatch.setenv("HT_QA_STAGE_CACHE_ENABLED", "1")
    service = _fake_redis_service()
    monkeypatch.setattr(stage_cache_module, "get_redis_service", lambda: service)
    monkeypatch.setattr("retriever.vector_retriever.get_redis_service", lambda: service)

    started = threading.Event()
    release = threading.Event()
    retrieve_calls = {"n": 0}
    results = [None, None]

    def fake_retrieve(query, top_k=None, collection=None, embedding_client=None):
        _ = (top_k, collection, embedding_client)
        retrieve_calls["n"] += 1
        started.set()
        if retrieve_calls["n"] == 1:
            assert release.wait(timeout=1.0)
        return [
            vector_retriever.RetrievedChunk(
                text=f"chunk:{query}",
                doi="10.1/demo",
                title="Demo",
                section_name="Intro",
                chunk_index=0,
                distance=0.1,
            )
        ]

    monkeypatch.setattr("retriever.vector_retriever.retrieve", fake_retrieve)

    def _worker(slot: int) -> None:
        results[slot] = vector_retriever.batch_retrieve(["q1"], top_k=3, collection=object(), embedding_client=object())

    thread_a = threading.Thread(target=_worker, args=(0,))
    thread_b = threading.Thread(target=_worker, args=(1,))

    thread_a.start()
    assert started.wait(timeout=1.0)
    thread_b.start()
    time.sleep(0.05)
    release.set()
    thread_a.join(timeout=1.0)
    thread_b.join(timeout=1.0)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert retrieve_calls["n"] == 1
    assert results[0][0][0].doi == "10.1/demo"
    assert results[1][0][0].doi == "10.1/demo"
