from types import SimpleNamespace

from app.integrations.redis import RedisService
from app.modules.qa_cache import (
    build_stage25_cache_key,
    build_stage25_lock_key,
    cache_stage25_result,
    get_cached_stage25_result,
    reset_cache_metrics,
    snapshot_cache_metrics,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        _ = nx
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = int(ex)
        return True


def test_stage25_cache_roundtrip_uses_retrieval_signature(monkeypatch):
    monkeypatch.setenv("QA_CACHE_EPOCH", "5")
    monkeypatch.setenv("KB_DATA_EPOCH", "9")
    monkeypatch.setenv("QA_STAGE25_MD_MAX_DOIS", "7")
    monkeypatch.setenv("QA_STAGE25_MD_CHUNKS_PER_DOI", "4")
    monkeypatch.setenv("QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED", "0")
    reset_cache_metrics()
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = SimpleNamespace(model="qwen-max")
    retrieval_results = {
        "documents": ["doc-a"],
        "metadatas": [{"doi": "10.1/test"}],
        "distances": [0.12],
        "claim_to_results": {"claim": ["doc-a"]},
        "unique_count": 1,
        "total_count": 1,
    }
    dois = ["10.2/test", "10.1/test"]

    cache_key = build_stage25_cache_key(
        redis_service=service,
        runtime=runtime,
        question=" What is LFP? ",
        retrieval_results=retrieval_results,
        dois=dois,
    )
    lock_key = build_stage25_lock_key(
        redis_service=service,
        runtime=runtime,
        question=" What is LFP? ",
        retrieval_results=retrieval_results,
        dois=dois,
    )

    assert cache_key.startswith("agentcode:cache:qa:stage25:5:9:")
    assert lock_key.startswith("agentcode:lock:qa:stage25:5:9:")

    cached = cache_stage25_result(
        redis_service=service,
        runtime=runtime,
        question=" What is LFP? ",
        retrieval_results=retrieval_results,
        dois=dois,
        stage25_result={
            "enabled": True,
            "applied": True,
            "md_chunks_by_doi": {"10.1/test": [{"text": "md evidence", "score": 0.9}]},
            "stats": {"hit_doi_count": 1, "total_md_chunks": 1, "fallback_reason": ""},
        },
    )

    assert cached is True
    assert get_cached_stage25_result(
        redis_service=service,
        runtime=runtime,
        question="What   is   LFP?",
        retrieval_results=retrieval_results,
        dois=["10.1/test", "10.2/test"],
    ) == {
        "enabled": True,
        "applied": True,
        "md_chunks_by_doi": {"10.1/test": [{"text": "md evidence", "score": 0.9}]},
        "stats": {"hit_doi_count": 1, "total_md_chunks": 1, "fallback_reason": ""},
    }
    assert snapshot_cache_metrics()["stage25"]["cache_write"] == 1


def test_stage25_cache_rejects_invalid_payloads():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = SimpleNamespace(model="qwen-max")

    assert cache_stage25_result(
        redis_service=service,
        runtime=runtime,
        question="q",
        retrieval_results={"documents": []},
        dois=["10.1/test"],
        stage25_result=[],
    ) is False
    assert (
        get_cached_stage25_result(
            redis_service=service,
            runtime=runtime,
            question="q",
            retrieval_results={"documents": []},
            dois=["10.1/test"],
        )
        is None
    )
