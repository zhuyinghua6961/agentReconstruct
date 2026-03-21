from app.integrations.redis import RedisLockManager, RedisService, build_key_factory


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

    def expire(self, key: str, seconds: int):
        if key not in self.values:
            return False
        self.expirations[key] = int(seconds)
        return True

    def ttl(self, key: str):
        return self.expirations.get(key)


def test_build_key_factory_namespaces_segments():
    factory = build_key_factory("agentcode")

    assert factory.join("qa", "stage1", "abc") == "agentcode:qa:stage1:abc"
    assert factory.cache("conversation", "list", 1) == "agentcode:cache:conversation:list:1"
    assert factory.lock("qa", "stage1", "abc") == "agentcode:lock:qa:stage1:abc"
    assert factory.stream("ask", "fast") == "agentcode:stream:ask:fast"


def test_redis_service_json_roundtrip_and_expire():
    client = _FakeRedis()
    service = RedisService.from_prefix(client=client, key_prefix="agentcode")
    key = service.prefixed("cache", "qa", "stage1")

    assert service.set_json(key, {"ok": True}, ttl_seconds=30) is True
    assert client.expirations[key] == 30
    assert service.get_json(key) == {"ok": True}
    assert service.expire(key, 15) is True
    assert client.expirations[key] == 15
    assert service.ttl(key) == 15
    assert service.delete(key) == 1
    assert service.get_json(key, default={"fallback": True}) == {"fallback": True}


def test_redis_service_gracefully_degrades_without_client():
    service = RedisService.from_prefix(client=None, key_prefix="agentcode")
    key = service.prefixed("cache", "qa")

    assert service.available is False
    assert service.get_json(key, default={}) == {}
    assert service.set_json(key, {"value": 1}) is False
    assert service.expire(key, 10) is False
    assert service.ttl(key) is None
    assert service.delete(key) == 0


def test_redis_lock_manager_acquire_and_release():
    client = _FakeRedis()
    manager = RedisLockManager(client)

    handle = manager.acquire("agentcode:lock:qa:stage1:x", ttl_seconds=12)

    assert handle is not None
    assert client.expirations[handle.key] == 12
    assert manager.release(handle) is True
    assert handle.key not in client.values


def test_redis_lock_manager_rejects_competing_lock_and_wrong_release():
    client = _FakeRedis()
    manager = RedisLockManager(client)

    first = manager.acquire("agentcode:lock:qa:stage1:x", ttl_seconds=5)
    second = manager.acquire("agentcode:lock:qa:stage1:x", ttl_seconds=5)

    assert first is not None
    assert second is None

    client.values[first.key] = "someone-else"
    assert manager.release(first) is False
