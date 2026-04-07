from fnmatch import fnmatch

from app.services import execution_queue_status as queue_status_module
from app.integrations.redis.service import RedisService
from app.services.execution_queue_status import ExecutionQueueStatusStore


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    def incr(self, key: str):
        next_value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(next_value)
        return next_value

    def delete(self, *keys: str):
        count = 0
        for key in keys:
            if key in self.values:
                count += 1
                self.values.pop(key, None)
                self.ttls.pop(key, None)
            if key in self.sets:
                count += 1
                self.sets.pop(key, None)
            if key in self.zsets:
                count += 1
                self.zsets.pop(key, None)
        return count

    def ttl(self, key: str):
        return self.ttls.get(key, -1)

    def sadd(self, key: str, *values: str):
        members = self.sets.setdefault(key, set())
        before = len(members)
        members.update(values)
        return len(members) - before

    def srem(self, key: str, *values: str):
        members = self.sets.setdefault(key, set())
        removed = 0
        for value in values:
            if value in members:
                members.remove(value)
                removed += 1
        return removed

    def scard(self, key: str):
        return len(self.sets.get(key, set()))

    def smembers(self, key: str):
        return set(self.sets.get(key, set()))

    def zadd(self, key: str, mapping: dict[str, float]):
        scores = self.zsets.setdefault(key, {})
        before = len(scores)
        for member, score in mapping.items():
            scores[member] = float(score)
        return max(0, len(scores) - before)

    def zrem(self, key: str, *members: str):
        scores = self.zsets.setdefault(key, {})
        removed = 0
        for member in members:
            if member in scores:
                scores.pop(member, None)
                removed += 1
        return removed

    def zcard(self, key: str):
        return len(self.zsets.get(key, {}))

    def zrange(self, key: str, start: int, stop: int, withscores: bool = False):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        if stop == -1:
            sliced = items[start:]
        else:
            sliced = items[start : stop + 1]
        if withscores:
            return sliced
        return [member for member, _ in sliced]

    def zrangebyscore(self, key: str, min_score: float, max_score: float):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        return [member for member, score in items if float(min_score) <= score <= float(max_score)]

    def scan_iter(self, *, match: str | None = None):
        for key in list(self.values.keys()):
            if not match or fnmatch(key, str(match)):
                yield key


def _store() -> ExecutionQueueStatusStore:
    return ExecutionQueueStatusStore(redis_service=RedisService.from_prefix(client=None, key_prefix="gateway"))


def test_queue_status_store_round_trips_request_record():
    store = _store()
    record = {
        "request_id": "req_1",
        "status": "queued",
        "trace_id": "trace_1",
        "conversation_id": 11,
        "user_id": 22,
        "requested_mode": "thinking",
        "actual_mode": "fast",
        "route": "pdf_qa",
        "target_backend": "fast",
        "backend_capacity_key": "fast_or_patent",
        "transport_kind": "sse",
        "enqueued_at": "2026-03-30T10:00:00+08:00",
        "execution_snapshot": {"question": "demo"},
    }

    assert store.put_request(record, ttl_seconds=900) is True
    loaded = store.get_request("req_1")

    assert loaded is not None
    assert loaded["request_id"] == "req_1"
    assert loaded["actual_mode"] == "fast"
    assert loaded["execution_snapshot"]["question"] == "demo"


def test_queue_status_store_can_cancel_queued_request():
    store = _store()
    store.put_request(
        {
            "request_id": "req_cancel",
            "status": "queued",
            "cancel_allowed": True,
        },
        ttl_seconds=900,
    )

    cancelled = store.cancel_request("req_cancel", cancelled_at="2026-03-30T10:05:00+08:00")

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_allowed"] is False
    assert cancelled["cancelled_at"] == "2026-03-30T10:05:00+08:00"


def test_queue_status_store_rejects_cancel_for_terminal_state():
    store = _store()
    store.put_request({"request_id": "req_done", "status": "completed"}, ttl_seconds=900)

    assert store.cancel_request("req_done") is None


def test_queue_status_store_rejects_cancel_for_admitted_state():
    store = _store()
    store.put_request({"request_id": "req_live", "status": "admitted", "cancel_allowed": False}, ttl_seconds=900)

    assert store.cancel_request("req_live") is None


def test_queue_status_store_rejects_cancel_when_flag_disabled():
    store = _store()
    store.put_request({"request_id": "req_locked", "status": "queued", "cancel_allowed": False}, ttl_seconds=900)

    assert store.cancel_request("req_locked") is None


def test_queue_status_store_can_cancel_queued_request_with_redis_ttl():
    redis = _FakeRedis()
    store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway")
    )
    store.put_request(
        {"request_id": "req_redis", "status": "queued", "cancel_allowed": True},
        ttl_seconds=900,
    )

    cancelled = store.cancel_request("req_redis", cancelled_at="2026-03-30T10:06:00+08:00")

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert redis.ttl(store.request_key("req_redis")) == 900


def test_queue_status_store_redis_cancel_updates_derived_indexes():
    redis = _FakeRedis()
    store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway")
    )
    store.put_request(
        {"request_id": "req_cancel_idx", "status": "queued", "cancel_allowed": True},
        ttl_seconds=900,
    )

    cancelled = store.cancel_request("req_cancel_idx", cancelled_at="2026-03-30T10:06:00+08:00")
    payload = store.describe()

    assert cancelled is not None
    assert payload["queued_requests"] == 0
    assert payload["cancellable_requests"] == 0
    assert payload["terminal_requests"] == 1


def test_queue_status_store_rejects_cancel_when_redis_compare_and_swap_fails():
    redis = _FakeRedis()
    store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway")
    )
    store.put_request(
        {"request_id": "req_race", "status": "queued", "cancel_allowed": True},
        ttl_seconds=900,
    )
    store.redis_service.compare_and_swap_json = lambda *args, **kwargs: False  # type: ignore[attr-defined]

    assert store.cancel_request("req_race") is None


def test_queue_status_store_persists_terminal_result():
    store = _store()

    assert store.put_result("req_2", {"answer": "alpha"}, ttl_seconds=600) is True
    assert store.get_result("req_2") == {"answer": "alpha"}


def test_queue_status_store_describe_exposes_backend_mode():
    store = _store()
    store.put_request({"request_id": "req_a", "status": "queued"}, ttl_seconds=900)
    store.put_request({"request_id": "req_b", "status": "completed"}, ttl_seconds=900)
    store.put_result("req_b", {"answer": "done"}, ttl_seconds=600)

    payload = store.describe()

    assert payload["available"] is False
    assert payload["storage_mode"] == "memory_fallback"
    assert payload["request_key_example"] == "gateway:admission:request:req_example"
    assert payload["requests_tracked"] == 2
    assert payload["queued_requests"] == 1
    assert payload["results_tracked"] == 1


def test_queue_status_store_describe_exposes_oldest_queued_age(monkeypatch):
    store = _store()
    monkeypatch.setattr(queue_status_module.time, "time", lambda: 100.0)
    store.put_request(
        {
            "request_id": "req_oldest",
            "status": "queued",
            "enqueued_at": "1970-01-01T00:01:10+00:00",
        },
        ttl_seconds=900,
    )

    payload = store.describe()

    assert payload["oldest_queued_age_seconds"] == 30


def test_queue_status_store_memory_request_ttl_expires(monkeypatch):
    store = _store()
    monkeypatch.setattr(queue_status_module.time, "time", lambda: 1000.0)
    store.put_request({"request_id": "req_ttl", "status": "queued", "cancel_allowed": True}, ttl_seconds=10)

    monkeypatch.setattr(queue_status_module.time, "time", lambda: 1011.0)

    record = store.get_request("req_ttl")

    assert record is not None
    assert record["status"] == "expired"
    assert record["cancel_allowed"] is False
    assert record["terminal_sync_pending"] is True


def test_queue_status_store_memory_result_ttl_expires(monkeypatch):
    store = _store()
    monkeypatch.setattr(queue_status_module.time, "time", lambda: 2000.0)
    store.put_result("req_result_ttl", {"answer": "alpha"}, ttl_seconds=5)

    monkeypatch.setattr(queue_status_module.time, "time", lambda: 2006.0)

    assert store.get_result("req_result_ttl") is None


def test_queue_status_store_redis_describe_uses_indexes_not_key_scan():
    redis = _FakeRedis()
    store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway")
    )
    store.put_request({"request_id": "req_idx", "status": "queued"}, ttl_seconds=900)
    store.put_result("req_idx", {"answer": "ok"}, ttl_seconds=600)
    redis.scan_iter = lambda **kwargs: (_ for _ in ()).throw(AssertionError("scan_iter should not be used"))  # type: ignore[attr-defined]

    payload = store.describe()

    assert payload["requests_tracked"] == 1
    assert payload["queued_requests"] == 1
    assert payload["results_tracked"] == 1


def test_queue_status_store_redis_describe_repairs_dirty_indexes():
    redis = _FakeRedis()
    store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway")
    )
    store.put_request({"request_id": "req_dirty", "status": "queued", "cancel_allowed": True}, ttl_seconds=900)
    store.put_result("req_dirty", {"answer": "ok"}, ttl_seconds=600)
    redis.delete(
        store.request_index_key(),
        store.request_expiry_key(),
        store.queued_index_key(),
        store.cancellable_index_key(),
        store.result_index_key(),
        store.result_expiry_key(),
    )
    store.redis_service.set_json(
        store.dirty_flag_key(),
        store.redis_service.get_int(store.clean_version_key(), default=0) + 1,
    )

    payload = store.describe()

    assert payload["requests_tracked"] == 1
    assert payload["queued_requests"] == 1
    assert payload["cancellable_requests"] == 1
    assert payload["results_tracked"] == 1


def test_queue_status_store_keeps_dirty_flag_until_failed_index_write_is_repaired():
    redis = _FakeRedis()
    store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway")
    )
    original_zadd = redis.zadd

    def _failing_zadd(key: str, mapping: dict[str, float]):
        redis.zadd = original_zadd
        raise RuntimeError("zadd failed")

    redis.zadd = _failing_zadd  # type: ignore[assignment]

    assert store.put_request({"request_id": "req_partial", "status": "queued"}, ttl_seconds=900) is True
    assert store._redis_dirty() is True

    payload = store.describe()

    assert payload["requests_tracked"] == 1
    assert payload["queued_requests"] == 1
    assert store._redis_dirty() is False


def test_queue_status_store_dirty_version_does_not_clear_newer_mutation():
    redis = _FakeRedis()
    store = ExecutionQueueStatusStore(
        redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway")
    )

    first_version = store._mark_redis_dirty()
    second_version = store._mark_redis_dirty()

    store._clear_redis_dirty(first_version)

    assert first_version == 1
    assert second_version == 2
    assert store._redis_dirty() is True

    store._clear_redis_dirty(second_version)

    assert store._redis_dirty() is False
