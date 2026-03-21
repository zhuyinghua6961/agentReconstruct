from app.integrations.redis import RedisService
from app.modules.qa_cache import reset_cache_metrics, run_singleflight, snapshot_cache_metrics


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        _ = ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key: str):
        return 1 if self.values.pop(key, None) is not None else 0


def test_singleflight_computes_with_lock_on_first_caller(monkeypatch):
    reset_cache_metrics()
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    calls: list[str] = []

    result = run_singleflight(
        redis_service=service,
        lock_key="agentcode:lock:qa:stage1:a",
        namespace="stage1",
        read_cached_fn=lambda: None,
        compute_fn=lambda: calls.append("compute") or {"ok": True},
    )

    snapshot = snapshot_cache_metrics()
    assert result == {"ok": True}
    assert calls == ["compute"]
    assert snapshot["stage1"]["lock_acquired"] == 1


def test_singleflight_uses_cached_value_when_lock_is_held(monkeypatch):
    reset_cache_metrics()
    client = _FakeRedis()
    client.values["agentcode:lock:qa:stage1:a"] = "other-holder"
    service = RedisService.from_prefix(client=client, key_prefix="agentcode")

    result = run_singleflight(
        redis_service=service,
        lock_key="agentcode:lock:qa:stage1:a",
        namespace="stage1",
        read_cached_fn=lambda: {"cached": True},
        compute_fn=lambda: {"computed": True},
    )

    snapshot = snapshot_cache_metrics()
    assert result == {"cached": True}
    assert snapshot["stage1"]["lock_wait_hit"] == 1


def test_singleflight_skips_lock_when_redis_unavailable():
    reset_cache_metrics()
    service = RedisService.from_prefix(client=None, key_prefix="agentcode")

    result = run_singleflight(
        redis_service=service,
        lock_key="x",
        namespace="stage1",
        read_cached_fn=lambda: None,
        compute_fn=lambda: {"computed": True},
    )

    snapshot = snapshot_cache_metrics()
    assert result == {"computed": True}
    assert snapshot["stage1"]["lock_skipped"] == 1

