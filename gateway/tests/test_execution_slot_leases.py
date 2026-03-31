from fnmatch import fnmatch

from app.integrations.redis.service import RedisService
import app.services.execution_slot_leases as slot_leases_module
from app.services.execution_slot_leases import ExecutionSlotLeaseStore


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


def _store() -> ExecutionSlotLeaseStore:
    return ExecutionSlotLeaseStore(redis_service=RedisService.from_prefix(client=None, key_prefix="gateway"))


def test_slot_lease_store_tracks_active_leases_by_capacity():
    store = _store()
    store.acquire(
        request_id="req_fast",
        capacity_key="fast_or_patent",
        owner_id="worker_a",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:00+00:00",
    )
    store.acquire(
        request_id="req_thinking",
        capacity_key="thinking",
        owner_id="worker_b",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:05+00:00",
    )

    payload = store.describe()

    assert payload["active_leases"] == 2
    assert payload["capacity_counts"]["fast_or_patent"] == 1
    assert payload["capacity_counts"]["thinking"] == 1


def test_slot_lease_store_rejects_duplicate_request_id():
    store = _store()
    first = store.acquire(
        request_id="req_dup",
        capacity_key="fast_or_patent",
        owner_id="worker_a",
        ttl_seconds=30,
    )
    second = store.acquire(
        request_id="req_dup",
        capacity_key="fast_or_patent",
        owner_id="worker_b",
        ttl_seconds=30,
    )

    assert first is not None
    assert second is None


def test_slot_lease_store_can_renew_owned_lease():
    redis = _FakeRedis()
    store = ExecutionSlotLeaseStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    store.acquire(
        request_id="req_renew",
        capacity_key="thinking",
        owner_id="worker_a",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:00+00:00",
    )

    renewed = store.renew(
        request_id="req_renew",
        owner_id="worker_a",
        ttl_seconds=45,
        renewed_at="2026-03-30T10:00:10+00:00",
    )

    assert renewed is not None
    assert renewed["last_renewed_at"] == "2026-03-30T10:00:10+00:00"
    assert redis.ttl(store.lease_key("req_renew")) == 45


def test_slot_lease_store_memory_ttl_expires(monkeypatch):
    store = _store()
    monkeypatch.setattr(slot_leases_module.time, "time", lambda: 1000.0)
    store.acquire(
        request_id="req_ttl",
        capacity_key="fast_or_patent",
        owner_id="worker_a",
        ttl_seconds=5,
    )

    monkeypatch.setattr(slot_leases_module.time, "time", lambda: 1006.0)

    assert store.get("req_ttl") is None
    assert store.describe()["active_leases"] == 0


def test_slot_lease_store_redis_describe_uses_indexes_not_key_scan():
    redis = _FakeRedis()
    store = ExecutionSlotLeaseStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    store.acquire(
        request_id="req_idx",
        capacity_key="thinking",
        owner_id="worker_a",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:00+00:00",
    )
    redis.scan_iter = lambda **kwargs: (_ for _ in ()).throw(AssertionError("scan_iter should not be used"))  # type: ignore[attr-defined]

    payload = store.describe()

    assert payload["active_leases"] == 1
    assert payload["capacity_counts"]["thinking"] == 1


def test_slot_lease_store_redis_describe_repairs_dirty_indexes():
    redis = _FakeRedis()
    store = ExecutionSlotLeaseStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    store.acquire(
        request_id="req_dirty",
        capacity_key="thinking",
        owner_id="worker_a",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:00+00:00",
    )
    redis.delete(
        store.lease_expiry_key(),
        store.lease_active_key(),
        store.lease_capacity_names_key(),
        store.lease_acquired_key(),
        store.lease_capacity_key("thinking"),
    )
    store.redis_service.set_json(
        store.dirty_flag_key(),
        store.redis_service.get_int(store.clean_version_key(), default=0) + 1,
    )

    payload = store.describe()

    assert payload["active_leases"] == 1
    assert payload["capacity_counts"]["thinking"] == 1


def test_slot_lease_store_keeps_dirty_flag_until_failed_index_write_is_repaired():
    redis = _FakeRedis()
    store = ExecutionSlotLeaseStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    original_zadd = redis.zadd

    def _failing_zadd(key: str, mapping: dict[str, float]):
        redis.zadd = original_zadd
        raise RuntimeError("zadd failed")

    redis.zadd = _failing_zadd  # type: ignore[assignment]

    assert store.acquire(
        request_id="req_partial",
        capacity_key="thinking",
        owner_id="worker_a",
        ttl_seconds=30,
        acquired_at="2026-03-30T10:00:00+00:00",
    ) is not None
    assert store._redis_dirty() is True

    payload = store.describe()

    assert payload["active_leases"] == 1
    assert payload["capacity_counts"]["thinking"] == 1
    assert store._redis_dirty() is False


def test_slot_lease_store_dirty_version_does_not_clear_newer_mutation():
    redis = _FakeRedis()
    store = ExecutionSlotLeaseStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))

    first_version = store._mark_redis_dirty()
    second_version = store._mark_redis_dirty()

    store._clear_redis_dirty(first_version)

    assert first_version == 1
    assert second_version == 2
    assert store._redis_dirty() is True

    store._clear_redis_dirty(second_version)

    assert store._redis_dirty() is False
