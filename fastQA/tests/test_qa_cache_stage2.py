from types import SimpleNamespace

from app.integrations.redis import RedisService
from app.modules.qa_cache import (
    build_stage2_cache_key,
    build_stage2_lock_key,
    cache_stage2_result,
    get_cached_stage2_result,
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


def test_stage2_cache_roundtrip_normalizes_claims(monkeypatch):
    reset_cache_metrics()
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = SimpleNamespace(model="qwen-max")
    claims = [{"claim": "lfp voltage", "keywords": ["LFP", "voltage"], "preferred": ["abstract"]}]

    cache_key = build_stage2_cache_key(
        redis_service=service,
        runtime=runtime,
        question="What is LFP voltage?",
        retrieval_claims=claims,
        n_results_per_claim=8,
    )
    lock_key = build_stage2_lock_key(
        redis_service=service,
        runtime=runtime,
        question="What is LFP voltage?",
        retrieval_claims=claims,
        n_results_per_claim=8,
    )

    assert cache_key.startswith("agentcode:cache:stage2:")
    assert lock_key.startswith("agentcode:lock:stage2:")

    cached = cache_stage2_result(
        redis_service=service,
        runtime=runtime,
        question="What is LFP voltage?",
        retrieval_claims=claims,
        n_results_per_claim=8,
        stage2_result={
            "success": True,
            "documents": ["doc-a"],
            "metadatas": [{"doi": "10.1/test"}],
            "distances": [0.12],
            "claim_to_results": {"lfp voltage": ["doc-a"]},
            "unique_count": 1,
            "total_count": 1,
        },
    )

    assert cached is True
    assert get_cached_stage2_result(
        redis_service=service,
        runtime=runtime,
        question="What   is LFP voltage?",
        retrieval_claims=[{"claim": "lfp voltage", "keywords": ["LFP", "voltage"], "preferred_sections": ["abstract"]}],
        n_results_per_claim=8,
    ) == {
        "success": True,
        "documents": ["doc-a"],
        "metadatas": [{"doi": "10.1/test"}],
        "distances": [0.12],
        "claim_to_results": {"lfp voltage": ["doc-a"]},
        "unique_count": 1,
        "total_count": 1,
    }
    assert snapshot_cache_metrics()["stage2"]["cache_write"] == 1


def test_stage2_cache_rejects_invalid_payloads():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = SimpleNamespace(model="qwen-max")

    assert cache_stage2_result(
        redis_service=service,
        runtime=runtime,
        question="q",
        retrieval_claims=["claim"],
        n_results_per_claim=3,
        stage2_result={"success": False},
    ) is False
    assert get_cached_stage2_result(
        redis_service=service,
        runtime=runtime,
        question="q",
        retrieval_claims=["claim"],
        n_results_per_claim=3,
    ) is None


def test_stage2_cache_key_changes_when_graph_doi_candidates_change():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = SimpleNamespace(model="qwen-max")
    claims = [{"claim": "lfp voltage"}]

    first_key = build_stage2_cache_key(
        redis_service=service,
        runtime=runtime,
        question="What is LFP voltage?",
        retrieval_claims=claims,
        n_results_per_claim=8,
        graph_cache_fingerprint="graph:a",
    )
    second_key = build_stage2_cache_key(
        redis_service=service,
        runtime=runtime,
        question="What is LFP voltage?",
        retrieval_claims=claims,
        n_results_per_claim=8,
        graph_cache_fingerprint="graph:b",
    )

    assert first_key != second_key
