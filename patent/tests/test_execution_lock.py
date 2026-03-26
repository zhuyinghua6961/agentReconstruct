from server.services.execution_lock import ExecutionLockManager


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.expiry = {}
        self.compare_delete_calls = []
        self.compare_expire_calls = []

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.expiry[key] = ex
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def expire(self, key, ttl):
        if key not in self.store:
            return False
        self.expiry[key] = ttl
        return True

    def compare_delete(self, key, token):
        self.compare_delete_calls.append((key, token))
        if self.store.get(key) != token:
            return 0
        self.store.pop(key, None)
        self.expiry.pop(key, None)
        return 1

    def compare_expire(self, key, token, ttl):
        self.compare_expire_calls.append((key, token, ttl))
        if self.store.get(key) != token:
            return 0
        self.expiry[key] = ttl
        return 1


class _FakeRedisWithoutCompare:
    def __init__(self):
        self.store = {}
        self.expiry = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.expiry[key] = ex
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def expire(self, key, ttl):
        if key not in self.store:
            return False
        self.expiry[key] = ttl
        return True


class _BrokenCompareRedis(_FakeRedis):
    def compare_delete(self, key, token):
        raise RuntimeError("compare-delete-boom")

    def compare_expire(self, key, token, ttl):
        raise RuntimeError("compare-expire-boom")


def test_conversation_lock_rejects_second_owner():
    redis = _FakeRedis()
    manager = ExecutionLockManager(redis)

    first = manager.acquire_conversation_lock(123, ttl_seconds=30)
    second = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert first is not None
    assert second is None


def test_conversation_lock_release_requires_owner_token():
    redis = _FakeRedis()
    manager = ExecutionLockManager(redis)

    handle = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert handle is not None
    assert manager.release(handle.key, "wrong-token") is False
    assert manager.release(handle.key, handle.token) is True


def test_conversation_lock_can_renew_existing_owner():
    redis = _FakeRedis()
    manager = ExecutionLockManager(redis)
    handle = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert handle is not None
    assert manager.renew(handle.key, handle.token, ttl_seconds=45) is True
    assert redis.expiry[handle.key] == 45


def test_release_uses_atomic_compare_delete_when_available():
    redis = _FakeRedis()
    manager = ExecutionLockManager(redis)
    handle = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert handle is not None
    assert manager.release(handle.key, handle.token) is True
    assert redis.compare_delete_calls == [(handle.key, handle.token)]


def test_renew_uses_atomic_compare_expire_when_available():
    redis = _FakeRedis()
    manager = ExecutionLockManager(redis)
    handle = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert handle is not None
    assert manager.renew(handle.key, handle.token, ttl_seconds=45) is True
    assert redis.compare_expire_calls == [(handle.key, handle.token, 45)]


def test_release_fails_closed_without_atomic_compare_delete_support():
    redis = _FakeRedisWithoutCompare()
    manager = ExecutionLockManager(redis)
    handle = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert handle is not None
    assert manager.release(handle.key, handle.token) is False
    assert redis.get(handle.key) == handle.token
    assert manager.last_error == "atomic compare_delete helper unavailable"


def test_renew_fails_closed_without_atomic_compare_expire_support():
    redis = _FakeRedisWithoutCompare()
    manager = ExecutionLockManager(redis)
    handle = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert handle is not None
    assert manager.renew(handle.key, handle.token, ttl_seconds=45) is False
    assert redis.expiry[handle.key] == 30
    assert manager.last_error == "atomic compare_expire helper unavailable"


def test_release_records_atomic_compare_delete_exception():
    redis = _BrokenCompareRedis()
    manager = ExecutionLockManager(redis)
    handle = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert handle is not None
    assert manager.release(handle.key, handle.token) is False
    assert "compare-delete-boom" in manager.last_error


def test_renew_records_atomic_compare_expire_exception():
    redis = _BrokenCompareRedis()
    manager = ExecutionLockManager(redis)
    handle = manager.acquire_conversation_lock(123, ttl_seconds=30)

    assert handle is not None
    assert manager.renew(handle.key, handle.token, ttl_seconds=45) is False
    assert "compare-expire-boom" in manager.last_error
