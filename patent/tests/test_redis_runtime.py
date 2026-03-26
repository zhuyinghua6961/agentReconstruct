from server.services.redis_client import bootstrap_redis_state, build_redis_bindings, redact_redis_url


class _ScriptRedisClient:
    def __init__(self):
        self.store = {}
        self.expiry = {}

    def ping(self):
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def expire(self, key, ttl):
        if key not in self.store:
            return 0
        self.expiry[key] = ttl
        return 1

    def register_script(self, script):
        def runner(*, keys, args):
            key = keys[0]
            token = args[0]
            if "DEL" in script:
                if self.store.get(key) != token:
                    return 0
                self.store.pop(key, None)
                self.expiry.pop(key, None)
                return 1
            ttl = int(args[1])
            if self.store.get(key) != token:
                return 0
            self.expiry[key] = ttl
            return 1

        return runner


class _EvalRedisClient:
    def __init__(self):
        self.store = {}
        self.expiry = {}

    def ping(self):
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def expire(self, key, ttl):
        if key not in self.store:
            return 0
        self.expiry[key] = ttl
        return 1

    def eval(self, script, numkeys, key, token, ttl=None):
        assert numkeys == 1
        if "DEL" in script:
            if self.store.get(key) != token:
                return 0
            self.store.pop(key, None)
            self.expiry.pop(key, None)
            return 1
        if self.store.get(key) != token:
            return 0
        self.expiry[key] = int(ttl)
        return 1


class _NoAtomicRedisClient:
    def ping(self):
        return True


class _FakeRedisModule:
    class Redis:
        @staticmethod
        def from_url(*args, **kwargs):
            return _ScriptRedisClient()


class _EvalRedisModule:
    class Redis:
        @staticmethod
        def from_url(*args, **kwargs):
            return _EvalRedisClient()


class _NoAtomicRedisModule:
    class Redis:
        @staticmethod
        def from_url(*args, **kwargs):
            return _NoAtomicRedisClient()


class _State:
    pass


def test_redact_redis_url_masks_password():
    assert redact_redis_url("redis://user:secret@localhost:6379/0") == "redis://user:***@localhost:6379/0"


def test_bootstrap_redis_sets_component_status(monkeypatch):
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_URL", "redis://user:secret@localhost:6379/0")
    monkeypatch.setenv("PATENT_REDIS_KEY_PREFIX", "tenant-a")
    monkeypatch.setenv("PATENT_ENV", "test")

    state = _State()
    bootstrap_redis_state(state, redis_lib=_FakeRedisModule())

    status = state.component_status["redis"]
    assert status["ready"] is True
    assert status["enabled"] is True
    assert status["url"] == "redis://user:***@localhost:6379/0"
    assert status["key_prefix"] == "tenant-a"
    assert state.redis_bindings.available is True
    assert state.redis_key_factory.cache("abc") == "tenant-a:test:exec:cache:abc"
    assert callable(state.redis_bindings.client.compare_delete)
    assert callable(state.redis_bindings.client.compare_expire)


def test_build_redis_bindings_marks_disabled_config(monkeypatch):
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "false")
    monkeypatch.delenv("PATENT_REDIS_KEY_PREFIX", raising=False)

    bindings = build_redis_bindings()

    assert bindings.enabled is False
    assert bindings.available is False
    assert bindings.detail == "redis disabled by config"
    assert bindings.key_prefix == "patent"


def test_build_redis_bindings_attaches_atomic_compare_helpers(monkeypatch):
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")

    bindings = build_redis_bindings(redis_lib=_FakeRedisModule())

    assert bindings.available is True
    client = bindings.client
    client.store["lock-key"] = "owner-token"

    assert client.compare_expire("lock-key", "owner-token", 45) == 1
    assert client.expiry["lock-key"] == 45
    assert client.compare_delete("lock-key", "owner-token") == 1
    assert client.get("lock-key") is None


def test_build_redis_bindings_attaches_atomic_compare_helpers_via_eval(monkeypatch):
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")

    bindings = build_redis_bindings(redis_lib=_EvalRedisModule())

    assert bindings.available is True
    client = bindings.client
    client.store["lock-key"] = "owner-token"

    assert client.compare_expire("lock-key", "owner-token", 30) == 1
    assert client.expiry["lock-key"] == 30
    assert client.compare_delete("lock-key", "owner-token") == 1


def test_build_redis_bindings_rejects_clients_without_atomic_compare_support(monkeypatch):
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")

    bindings = build_redis_bindings(redis_lib=_NoAtomicRedisModule())

    assert bindings.enabled is True
    assert bindings.available is False
    assert bindings.client is None
    assert bindings.detail == "redis client missing atomic compare support"
    assert "register_script or eval" in bindings.error
