from types import SimpleNamespace

from app.integrations.redis import RedisService
from app.modules.qa_cache import (
    build_stage1_cache_key,
    build_stage1_lock_key,
    cache_stage1_result,
    get_cached_stage1_result,
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


def _runtime():
    runtime = SimpleNamespace(model="qwen-max", stage1_prompt="plan prompt")
    runtime._get_vector_db_context_for_prompt = lambda: "topic-a\ntopic-b"
    return runtime


def test_stage1_cache_roundtrip_uses_runtime_signature():
    reset_cache_metrics()
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()

    cache_key = build_stage1_cache_key(redis_service=service, runtime=runtime, question=" What is LFP? ")
    lock_key = build_stage1_lock_key(redis_service=service, runtime=runtime, question=" What is LFP? ")

    assert cache_key.startswith("agentcode:cache:qa:stage1:")
    assert lock_key.startswith("agentcode:lock:qa:stage1:")

    cached = cache_stage1_result(
        redis_service=service,
        runtime=runtime,
        question=" What is LFP? ",
        stage1_result={"success": True, "deep_answer": "answer", "retrieval_claims": [{"claim": "lfp"}]},
    )

    assert cached is True
    assert get_cached_stage1_result(redis_service=service, runtime=runtime, question="What   is   LFP?") == {
        "success": True,
        "deep_answer": "answer",
        "retrieval_claims": [{"claim": "lfp"}],
    }
    assert snapshot_cache_metrics()["stage1"]["cache_write"] == 1


def test_stage1_cache_rejects_invalid_payloads():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()

    assert cache_stage1_result(redis_service=service, runtime=runtime, question="q", stage1_result={"success": False}) is False
    assert get_cached_stage1_result(redis_service=service, runtime=runtime, question="q") is None

