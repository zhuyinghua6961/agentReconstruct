from app.integrations.redis import RedisService
from app.modules.qa_cache import (
    build_stage3_cache_key,
    build_stage3_lock_key,
    cache_stage3_result,
    get_cached_stage3_result,
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


def test_stage3_cache_roundtrip_uses_doi_set_signature(monkeypatch):
    monkeypatch.setenv("QA_CACHE_EPOCH", "3")
    monkeypatch.setenv("PAPERS_DATA_EPOCH", "11")
    reset_cache_metrics()
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    dois = ["10.2/test", "10.1/test"]

    cache_key = build_stage3_cache_key(
        redis_service=service,
        dois=dois,
        max_chunks_per_doi=3,
    )
    lock_key = build_stage3_lock_key(
        redis_service=service,
        dois=dois,
        max_chunks_per_doi=3,
    )

    assert cache_key.startswith("agentcode:cache:qa:stage3:3:11:")
    assert lock_key.startswith("agentcode:lock:qa:stage3:3:11:")

    cached = cache_stage3_result(
        redis_service=service,
        dois=dois,
        max_chunks_per_doi=3,
        stage3_result={
            "10.1/test": [{"text": "pdf evidence", "score": 0.2}],
            "10.2/test": [{"text": "pdf evidence 2", "page": 5}],
        },
    )

    assert cached is True
    assert get_cached_stage3_result(
        redis_service=service,
        dois=["10.1/test", "10.2/test"],
        max_chunks_per_doi=3,
    ) == {
        "10.1/test": [{"text": "pdf evidence", "score": 0.2}],
        "10.2/test": [{"text": "pdf evidence 2", "page": 5}],
    }
    assert snapshot_cache_metrics()["stage3"]["cache_write"] == 1


def test_stage3_cache_rejects_invalid_payloads():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")

    assert cache_stage3_result(
        redis_service=service,
        dois=["10.1/test"],
        max_chunks_per_doi=3,
        stage3_result=[],
    ) is False
    assert get_cached_stage3_result(
        redis_service=service,
        dois=["10.1/test"],
        max_chunks_per_doi=3,
    ) is None
