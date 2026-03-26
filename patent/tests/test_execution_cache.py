from server.patent.cache_keys import PatentKeyFactory
from server.services.execution_cache import ExecutionCache


class _FakeRedis:
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

    def compare_delete(self, key, token):
        if self.store.get(key) != token:
            return 0
        self.store.pop(key, None)
        self.expiry.pop(key, None)
        return 1


def test_turn_dedupe_key_uses_conversation_and_trace():
    keys = PatentKeyFactory(env="test")

    key = keys.turn("123", "req_abc")

    assert key == "patent:test:exec:turn:123:req_abc"


def test_inflight_key_uses_conversation_and_trace():
    keys = PatentKeyFactory(env="test")

    key = keys.inflight("123", "req_abc")

    assert key == "patent:test:coord:inflight:123:req_abc"


def test_overlay_key_uses_user_and_conversation():
    keys = PatentKeyFactory(env="test")

    key = keys.overlay_assistant(42, 123)

    assert key == "patent:test:overlay:assistant:42:123"


def test_key_factory_preserves_falsy_string_segments():
    keys = PatentKeyFactory(env="test")

    key = keys.cache(0)

    assert key == "patent:test:exec:cache:0"


def test_execution_cache_persists_overlay_payload_as_json():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_123", "assistant_content": "Patent answer", "route": "kb_qa"},
        ttl_seconds=60,
    )
    loaded = cache.get_overlay_assistant(user_id=42, conversation_id=123)

    assert loaded["trace_id"] == "req_123"
    assert loaded["assistant_content"] == "Patent answer"


def test_execution_cache_claims_turn_identity_with_conversation_and_trace():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.claim_turn_identity(conversation_id=123, trace_id="req_123", ttl_seconds=30) is True
    assert cache.claim_turn_identity(conversation_id=123, trace_id="req_123", ttl_seconds=30) is False
    assert redis.store["patent:test:exec:turn:123:req_123"] == "1"


def test_execution_cache_marks_inflight_with_coord_namespace():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.mark_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=30) is True
    assert cache.mark_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=30) is False
    assert redis.store["patent:test:coord:inflight:123:req_123"] == "1"


def test_execution_cache_clears_overlay_when_authority_catches_up():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_123", "assistant_content": "Patent answer", "route": "kb_qa"},
        ttl_seconds=60,
    )

    assert cache.clear_overlay_if_converged(user_id=42, conversation_id=123, assistant_trace_id="req_123") is True
    assert cache.get_overlay_assistant(user_id=42, conversation_id=123) is None


def test_execution_cache_roundtrips_execution_cache_payload():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.set_execution_cache(normalized_request_key="req-hash", payload={"answer": "ok"}, ttl_seconds=20) is True
    assert cache.get_execution_cache(normalized_request_key="req-hash") == {"answer": "ok"}


def test_execution_cache_roundtrips_retrieval_cache_payload():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.set_retrieval_cache(normalized_query_key="query-hash", payload={"docs": [1, 2]}, ttl_seconds=20) is True
    assert cache.get_retrieval_cache(normalized_query_key="query-hash") == {"docs": [1, 2]}


def test_execution_cache_returns_none_for_corrupted_json():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    redis.store[cache._keys.overlay_assistant(42, 123)] = "not-json"

    assert cache.get_overlay_assistant(user_id=42, conversation_id=123) is None


def test_execution_cache_returns_none_for_corrupted_execution_cache_json():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    redis.store[cache._keys.cache("req-hash")] = "not-json"

    assert cache.get_execution_cache(normalized_request_key="req-hash") is None


def test_execution_cache_returns_none_for_non_utf8_bytes():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    redis.store[cache._keys.cache("req-hash")] = b"\xff\xfe"

    assert cache.get_execution_cache(normalized_request_key="req-hash") is None



def test_pending_turn_key_uses_conversation_scope():
    keys = PatentKeyFactory(env="test")

    key = keys.pending_turn(123)

    assert key == "patent:test:coord:pending-turn:123"


def test_execution_cache_tracks_pending_turn_per_conversation():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.claim_pending_turn(conversation_id=123, trace_id="req_123", ttl_seconds=30) is True
    assert cache.claim_pending_turn(conversation_id=123, trace_id="req_456", ttl_seconds=30) is False
    assert cache.get_pending_turn(conversation_id=123) == "req_123"
    assert cache.clear_pending_turn(conversation_id=123, trace_id="req_456") is False
    assert cache.clear_pending_turn(conversation_id=123, trace_id="req_123") is True
    assert cache.get_pending_turn(conversation_id=123) == ""
