from fnmatch import fnmatch

from app.services import execution_event_relay as relay_module
from app.integrations.redis.service import RedisService
from app.services.execution_event_relay import ExecutionEventRelayStore


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.raw_values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expiry: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and key in self.raw_values:
            return False
        self.values.pop(key, None)
        self.raw_values[key] = value
        if ex is not None:
            self.expiry[key] = int(ex)
        return True

    def incr(self, key: str):
        next_value = int(self.values.get(key, 0)) + 1
        self.values[key] = next_value
        return next_value

    def incrby(self, key: str, amount: int):
        next_value = int(self.values.get(key, 0)) + int(amount)
        self.values[key] = next_value
        return next_value

    def get(self, key: str):
        if key in self.values:
            return self.values[key]
        return self.raw_values.get(key)

    def rpush(self, key: str, value: str):
        values = self.lists.setdefault(key, [])
        values.append(value)
        return len(values)

    def lrange(self, key: str, start: int, stop: int):
        values = list(self.lists.get(key, []))
        if stop == -1:
            return values[start:]
        return values[start : stop + 1]

    def expire(self, key: str, ttl_seconds: int):
        self.expiry[key] = int(ttl_seconds)
        return True

    def ttl(self, key: str):
        return self.expiry.get(key, -1)

    def delete(self, *keys: str):
        count = 0
        for key in keys:
            if key in self.lists:
                self.lists.pop(key, None)
                count += 1
            if key in self.values:
                self.values.pop(key, None)
                count += 1
            if key in self.raw_values:
                self.raw_values.pop(key, None)
                count += 1
            self.expiry.pop(key, None)
            if key in self.sets:
                self.sets.pop(key, None)
                count += 1
            if key in self.zsets:
                self.zsets.pop(key, None)
                count += 1
        return count

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

    def zrangebyscore(self, key: str, min_score: float, max_score: float):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        return [member for member, score in items if float(min_score) <= score <= float(max_score)]

    def zrange(self, key: str, start: int, stop: int, withscores: bool = False):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        if stop == -1:
            sliced = items[start:]
        else:
            sliced = items[start : stop + 1]
        if withscores:
            return sliced
        return [member for member, _ in sliced]

    def scan_iter(self, *, match: str | None = None):
        for key in list(self.lists.keys()):
            if not match or fnmatch(key, str(match)):
                yield key


def _store() -> ExecutionEventRelayStore:
    return ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=None, key_prefix="gateway"))


def test_execution_event_relay_assigns_monotonic_sequences():
    store = _store()

    first = store.append_frame("req_1", {"type": "metadata"}, ttl_seconds=600)
    second = store.append_frame("req_1", {"type": "content", "content": "alpha"}, ttl_seconds=600)

    assert first["sequence"] == 1
    assert second["sequence"] == 2


def test_execution_event_relay_can_resume_after_sequence():
    store = _store()
    store.append_frame("req_2", {"type": "metadata"}, ttl_seconds=600)
    store.append_frame("req_2", {"type": "content", "content": "a"}, ttl_seconds=600)
    store.append_frame("req_2", {"type": "done"}, ttl_seconds=600)

    frames = store.get_frames("req_2", after_sequence=1)

    assert [frame["sequence"] for frame in frames] == [2, 3]
    assert frames[-1]["payload"]["type"] == "done"


def test_execution_event_relay_can_clear_request_frames():
    store = _store()
    store.append_frame("req_3", {"type": "metadata"}, ttl_seconds=600)

    assert store.clear("req_3") >= 1
    assert store.get_frames("req_3", after_sequence=0) == []


def test_execution_event_relay_describe_exposes_backend_mode():
    store = _store()
    store.append_frame("req_a", {"type": "metadata"}, ttl_seconds=600)
    store.append_frame("req_a", {"type": "content"}, ttl_seconds=600)
    store.append_frame("req_b", {"type": "done"}, ttl_seconds=600)

    payload = store.describe()

    assert payload["available"] is False
    assert payload["storage_mode"] == "memory_fallback"
    assert payload["frames_key_example"] == "gateway:relay:req_example:frames"
    assert payload["requests_tracked"] == 2
    assert payload["frames_tracked"] == 3


def test_execution_event_relay_describe_request_exposes_latest_sequence():
    store = _store()
    store.append_frame("req_detail", {"type": "metadata"}, ttl_seconds=600)
    store.append_frame("req_detail", {"type": "done"}, ttl_seconds=600)

    payload = store.describe_request("req_detail")

    assert payload["request_id"] == "req_detail"
    assert payload["frames_tracked"] == 2
    assert payload["latest_sequence"] == 2


def test_execution_event_relay_memory_ttl_expires(monkeypatch):
    store = _store()
    monkeypatch.setattr(relay_module.time, "time", lambda: 3000.0)
    store.append_frame("req_ttl", {"type": "metadata"}, ttl_seconds=5)

    monkeypatch.setattr(relay_module.time, "time", lambda: 3006.0)

    assert store.get_frames("req_ttl", after_sequence=0) == []


def test_execution_event_relay_redis_backend_uses_atomic_sequence_storage():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))

    first = store.append_frame("req_redis", {"type": "metadata"}, ttl_seconds=600)
    second = store.append_frame("req_redis", {"type": "done"}, ttl_seconds=600)

    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert store.redis_service.get_int(store.sequence_key("req_redis"), default=0) == 2
    assert store.redis_service.get_int(store.cursor_key("req_redis"), default=0) == 2
    assert store.redis_service.get_int(store.frame_count_key("req_redis"), default=0) == 2
    assert redis.expiry[store.frames_key("req_redis")] == 600


def test_execution_event_relay_redis_describe_uses_indexes_not_key_scan():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    store.append_frame("req_idx", {"type": "metadata"}, ttl_seconds=600)
    store.append_frame("req_idx", {"type": "done"}, ttl_seconds=600)
    redis.scan_iter = lambda **kwargs: (_ for _ in ()).throw(AssertionError("scan_iter should not be used"))  # type: ignore[attr-defined]

    payload = store.describe()

    assert payload["requests_tracked"] == 1
    assert payload["frames_tracked"] == 2


def test_execution_event_relay_redis_describe_request_uses_sequence_key_not_lrange():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    store.append_frame("req_detail_redis", {"type": "metadata"}, ttl_seconds=600)
    store.append_frame("req_detail_redis", {"type": "done"}, ttl_seconds=600)
    redis.lrange = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("lrange should not be used"))  # type: ignore[attr-defined]

    payload = store.describe_request("req_detail_redis")

    assert payload["frames_tracked"] == 2
    assert payload["latest_sequence"] == 2


def test_execution_event_relay_redis_get_frames_reads_from_sequence_offset():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    store.append_frame("req_offset", {"type": "metadata"}, ttl_seconds=600)
    store.append_frame("req_offset", {"type": "content"}, ttl_seconds=600)
    store.append_frame("req_offset", {"type": "done"}, ttl_seconds=600)
    captured: dict[str, int] = {}
    original_lrange = redis.lrange

    def tracking_lrange(key: str, start: int, stop: int):
        captured["start"] = int(start)
        captured["stop"] = int(stop)
        return original_lrange(key, start, stop)

    redis.lrange = tracking_lrange  # type: ignore[method-assign]

    frames = store.get_frames("req_offset", after_sequence=2)

    assert captured == {"start": 2, "stop": -1}
    assert [frame["sequence"] for frame in frames] == [3]


def test_execution_event_relay_redis_describe_repairs_dirty_indexes():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    store.append_frame("req_dirty", {"type": "metadata"}, ttl_seconds=600)
    store.append_frame("req_dirty", {"type": "done"}, ttl_seconds=600)
    redis.delete(
        store.request_index_key(),
        store.expiry_index_key(),
        store.total_frames_key(),
    )
    store.redis_service.set_json(
        store.dirty_flag_key(),
        store.redis_service.get_int(store.clean_version_key(), default=0) + 1,
    )

    payload = store.describe()

    assert payload["requests_tracked"] == 1
    assert payload["frames_tracked"] == 2


def test_execution_event_relay_keeps_dirty_flag_until_failed_index_write_is_repaired():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    original_zadd = redis.zadd

    def _failing_zadd(key: str, mapping: dict[str, float]):
        redis.zadd = original_zadd
        raise RuntimeError("zadd failed")

    redis.zadd = _failing_zadd  # type: ignore[assignment]

    record = store.append_frame("req_partial", {"type": "metadata"}, ttl_seconds=600)

    assert record["sequence"] == 1
    assert store._redis_dirty() is True

    payload = store.describe()

    assert payload["requests_tracked"] == 1
    assert payload["frames_tracked"] == 1
    assert store._redis_dirty() is False


def test_execution_event_relay_keeps_dirty_flag_when_total_frame_counter_write_fails():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    original_incrby = redis.incrby

    def _failing_incrby(key: str, amount: int):
        redis.incrby = original_incrby
        raise RuntimeError("incrby failed")

    redis.incrby = _failing_incrby  # type: ignore[assignment]

    record = store.append_frame("req_partial_total", {"type": "metadata"}, ttl_seconds=600)

    assert record["sequence"] == 1
    assert store._redis_dirty() is True

    payload = store.describe()

    assert payload["requests_tracked"] == 1
    assert payload["frames_tracked"] == 1
    assert store._redis_dirty() is False


def test_execution_event_relay_dirty_version_does_not_clear_newer_mutation():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))

    first_version = store._mark_redis_dirty()
    second_version = store._mark_redis_dirty()

    store._clear_redis_dirty(first_version)

    assert first_version == 1
    assert second_version == 2
    assert store._redis_dirty() is True

    store._clear_redis_dirty(second_version)

    assert store._redis_dirty() is False


def test_execution_event_relay_reports_actual_frame_count_after_partial_append_failure():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    original_rpush = redis.rpush
    failed = {"done": False}

    def _flaky_rpush(key: str, value: str):
        if not failed["done"]:
            failed["done"] = True
            raise RuntimeError("rpush failed")
        return original_rpush(key, value)

    redis.rpush = _flaky_rpush  # type: ignore[assignment]

    try:
        store.append_frame("req_gap", {"type": "metadata"}, ttl_seconds=600)
    except RuntimeError:
        pass

    record = store.append_frame("req_gap", {"type": "done"}, ttl_seconds=600)
    payload = store.describe_request("req_gap")
    frames = store.get_frames("req_gap", after_sequence=0)

    assert record["sequence"] == 2
    assert [item["sequence"] for item in frames] == [2]
    assert payload["latest_sequence"] == 2
    assert payload["frames_tracked"] == 1


def test_execution_event_relay_clear_reconciles_total_frames_when_frame_count_key_missing():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    store.append_frame("req_clear_gap", {"type": "done"}, ttl_seconds=600)
    redis.delete(store.frame_count_key("req_clear_gap"))

    cleared = store.clear("req_clear_gap")
    payload = store.describe()

    assert cleared >= 1
    assert payload["requests_tracked"] == 0
    assert payload["frames_tracked"] == 0


def test_execution_event_relay_resume_after_sequence_handles_sparse_storage_indexes():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    original_rpush = redis.rpush
    failed = {"done": False}

    def _flaky_rpush(key: str, value: str):
        if not failed["done"]:
            failed["done"] = True
            raise RuntimeError("rpush failed")
        return original_rpush(key, value)

    redis.rpush = _flaky_rpush  # type: ignore[assignment]

    try:
        store.append_frame("req_sparse_after", {"type": "metadata"}, ttl_seconds=600)
    except RuntimeError:
        pass

    store.append_frame("req_sparse_after", {"type": "done"}, ttl_seconds=600)
    frames = store.get_frames("req_sparse_after", after_sequence=1)

    assert [item["sequence"] for item in frames] == [2]


def test_execution_event_relay_drops_duplicate_upstream_seq_and_blocks_post_terminal_frames():
    store = _store()

    first = store.append_frame("req_terminal_guard", {"type": "content", "seq": 7, "content": "hello"}, ttl_seconds=600)
    duplicate = store.append_frame("req_terminal_guard", {"type": "content", "seq": 7, "content": "hello"}, ttl_seconds=600)
    terminal = store.append_frame("req_terminal_guard", {"type": "done", "seq": 8, "final_answer": "hello"}, ttl_seconds=600)
    after_terminal = store.append_frame("req_terminal_guard", {"type": "content", "seq": 9, "content": "should_not_surface"}, ttl_seconds=600)

    frames = store.get_frames("req_terminal_guard", after_sequence=0)

    assert first["sequence"] == 1
    assert duplicate["sequence"] == 1
    assert terminal["sequence"] == 2
    assert after_terminal["sequence"] == 2
    assert [item["payload"]["type"] for item in frames] == ["content", "done"]


def test_execution_event_relay_drops_duplicate_upstream_seq_even_when_seq_less_frames_are_interleaved():
    store = _store()

    first = store.append_frame("req_interleaved_duplicate", {"type": "content", "seq": 7, "content": "hello"}, ttl_seconds=600)
    middle = store.append_frame("req_interleaved_duplicate", {"type": "step", "step": "retrieve", "status": "processing"}, ttl_seconds=600)
    duplicate = store.append_frame("req_interleaved_duplicate", {"type": "content", "seq": 7, "content": "hello"}, ttl_seconds=600)
    terminal = store.append_frame("req_interleaved_duplicate", {"type": "done", "seq": 8, "final_answer": "hello"}, ttl_seconds=600)

    frames = store.get_frames("req_interleaved_duplicate", after_sequence=0)

    assert first["sequence"] == 1
    assert middle["sequence"] == 2
    assert duplicate["ignored"] is True
    assert duplicate["sequence"] == 2
    assert terminal["sequence"] == 3
    assert [item["payload"]["type"] for item in frames] == ["content", "step", "done"]


def test_execution_event_relay_hides_post_terminal_frames_from_an_already_polluted_replay_window():
    store = _store()
    store._memory_frames["req_polluted_terminal"] = [
        {"sequence": 1, "payload": {"type": "content", "content": "hello"}},
        {"sequence": 2, "payload": {"type": "done", "final_answer": "hello"}},
        {"sequence": 3, "payload": {"type": "content", "content": "should_not_surface"}},
    ]
    store._memory_expiry["req_polluted_terminal"] = store._now() + 600
    store._memory_request_ids.add("req_polluted_terminal")
    store._memory_total_frames = 3
    store._memory_latest_sequence["req_polluted_terminal"] = 3

    frames = store.get_frames("req_polluted_terminal", after_sequence=2)

    assert frames == []


def test_execution_event_relay_replay_window_tracks_upstream_seq_seen_before_after_sequence():
    store = _store()
    store._memory_frames["req_polluted_duplicate"] = [
        {"sequence": 1, "payload": {"type": "content", "seq": 7, "content": "hello"}},
        {"sequence": 2, "payload": {"type": "content", "seq": 7, "content": "hello"}},
        {"sequence": 3, "payload": {"type": "done", "seq": 8, "final_answer": "hello"}},
    ]
    store._memory_expiry["req_polluted_duplicate"] = store._now() + 600
    store._memory_request_ids.add("req_polluted_duplicate")
    store._memory_total_frames = 3
    store._memory_latest_sequence["req_polluted_duplicate"] = 3

    frames = store.get_frames("req_polluted_duplicate", after_sequence=1)

    assert [item["sequence"] for item in frames] == [3]
    assert [item["payload"]["type"] for item in frames] == ["done"]


def test_execution_event_relay_redis_keeps_dirty_and_dedupes_when_upstream_sequence_write_fails():
    redis = _FakeRedis()
    store = ExecutionEventRelayStore(redis_service=RedisService.from_prefix(client=redis, key_prefix="gateway"))
    original_set = redis.set
    failed = {"done": False}

    def _flaky_set(key: str, value: str, ex: int | None = None, nx: bool = False):
        if key == store.upstream_sequence_key("req_upstream_key_gap") and not failed["done"]:
            failed["done"] = True
            return False
        return original_set(key, value, ex=ex, nx=nx)

    redis.set = _flaky_set  # type: ignore[assignment]

    first = store.append_frame("req_upstream_key_gap", {"type": "content", "seq": 7, "content": "hello"}, ttl_seconds=600)
    dirty_after_first = store._redis_dirty()
    upstream_after_first = store.redis_service.get_int(store.upstream_sequence_key("req_upstream_key_gap"), default=0)
    middle = store.append_frame(
        "req_upstream_key_gap",
        {"type": "step", "step": "retrieve", "status": "processing"},
        ttl_seconds=600,
    )
    duplicate = store.append_frame("req_upstream_key_gap", {"type": "content", "seq": 7, "content": "hello"}, ttl_seconds=600)
    frames = store.get_frames("req_upstream_key_gap", after_sequence=0)

    assert first["sequence"] == 1
    assert dirty_after_first is True
    assert upstream_after_first == 0
    assert middle["sequence"] == 2
    assert duplicate["ignored"] is True
    assert duplicate["sequence"] == 2
    assert [item["payload"]["type"] for item in frames] == ["content", "step"]
