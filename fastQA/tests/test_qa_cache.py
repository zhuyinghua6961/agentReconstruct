from __future__ import annotations

from dataclasses import dataclass

from app.integrations.redis import RedisService, build_key_factory
from app.modules.qa_cache import (
    build_stage1_cache_key,
    build_stage1_lock_key,
    build_stage2_cache_key,
    cache_stage1_result,
    cache_stage2_result,
    get_cached_stage1_result,
    get_cached_stage2_result,
    reset_cache_metrics,
    run_singleflight,
    snapshot_cache_metrics,
)


class _FakeRedisClient:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value, ex=None, nx: bool = False):
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    def delete(self, *keys: str):
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
        return count

    def expire(self, key: str, ttl: int):
        return key in self._data and ttl >= 1

    def ttl(self, key: str):
        return 60 if key in self._data else None


@dataclass
class _Runtime:
    model: str = "qwen-test"
    stage1_prompt: str = "prompt-v1"

    def _get_vector_db_context_for_prompt(self) -> str:
        return "vector-context"


def _redis_service() -> RedisService:
    return RedisService(client=_FakeRedisClient(), key_factory=build_key_factory("agentcode"))


def test_stage1_cache_round_trip_and_lock_key_shape(monkeypatch):
    monkeypatch.setenv("QA_CACHE_EPOCH", "7")
    redis_service = _redis_service()
    runtime = _Runtime()
    key = build_stage1_cache_key(redis_service=redis_service, runtime=runtime, question="What is LFP?")
    lock_key = build_stage1_lock_key(redis_service=redis_service, runtime=runtime, question="What is LFP?")
    assert key.startswith("agentcode:cache:stage1:7:kb_qa:qwen-test:")
    assert lock_key.startswith("agentcode:lock:stage1:7:kb_qa:qwen-test:")

    payload = {"success": True, "deep_answer": "answer", "retrieval_claims": [{"claim": "a"}]}
    assert cache_stage1_result(redis_service=redis_service, runtime=runtime, question="What is LFP?", stage1_result=payload) is True
    assert get_cached_stage1_result(redis_service=redis_service, runtime=runtime, question="What is LFP?") == payload


def test_stage2_cache_round_trip(monkeypatch):
    monkeypatch.setenv("QA_CACHE_EPOCH", "2")
    monkeypatch.setenv("KB_DATA_EPOCH", "9")
    redis_service = _redis_service()
    runtime = _Runtime()
    claims = [{"claim": "cycle life", "keywords": ["lfp"], "preferred_sections": ["results"]}]
    key = build_stage2_cache_key(
        redis_service=redis_service,
        runtime=runtime,
        question="Explain cycle life",
        retrieval_claims=claims,
        n_results_per_claim=6,
    )
    assert key.startswith("agentcode:cache:stage2:2:9:1:kb_qa:qwen-test:6:")

    payload = {
        "success": True,
        "documents": ["doc-1"],
        "metadatas": [{"doi": "10.1"}],
        "distances": [0.1],
        "claim_to_results": {"cycle life": ["doc-1"]},
        "unique_count": 1,
        "total_count": 1,
    }
    assert cache_stage2_result(
        redis_service=redis_service,
        runtime=runtime,
        question="Explain cycle life",
        retrieval_claims=claims,
        n_results_per_claim=6,
        stage2_result=payload,
    ) is True
    assert (
        get_cached_stage2_result(
            redis_service=redis_service,
            runtime=runtime,
            question="Explain cycle life",
            retrieval_claims=claims,
            n_results_per_claim=6,
        )
        == payload
    )


def test_singleflight_uses_lock_then_cached_value(monkeypatch):
    monkeypatch.setenv("QA_CACHE_LOCK_ENABLED", "1")
    monkeypatch.setenv("QA_CACHE_WAIT_MS", "50")
    reset_cache_metrics()

    redis_service = _redis_service()
    runtime = _Runtime()
    lock_key = build_stage1_lock_key(redis_service=redis_service, runtime=runtime, question="hello")
    redis_service.client.set(lock_key, "taken", ex=30, nx=True)
    cached = {"success": True}
    calls = {"compute": 0, "read": 0}

    def _read_cached():
        calls["read"] += 1
        return cached

    def _compute():
        calls["compute"] += 1
        return {"fallback": True}

    result = run_singleflight(
        redis_service=redis_service,
        lock_key=lock_key,
        namespace="stage1",
        read_cached_fn=_read_cached,
        compute_fn=_compute,
    )

    assert result == cached
    assert calls["compute"] == 0
    assert calls["read"] >= 1
    metrics = snapshot_cache_metrics()
    assert metrics["stage1"]["lock_wait_hit"] >= 1
