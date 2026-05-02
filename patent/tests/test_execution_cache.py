from server.patent.cache_keys import (
    PatentKeyFactory,
    build_file_route_cache_fingerprint,
    build_stage1_cache_fingerprint,
    build_stage2_cache_fingerprint,
    build_stage25_cache_fingerprint,
    build_stage3_cache_fingerprint,
    build_stage4_cache_fingerprint,
)
from server.services.execution_cache import ExecutionCache


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.expiry = {}
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

    def compare_delete(self, key, token):
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

    def compare_set(self, key, expected, replacement, ttl):
        current = self.store.get(key)
        normalized_expected = str(expected or "")
        if current is None:
            if normalized_expected:
                return 0
        elif current != normalized_expected:
            return 0
        self.store[key] = replacement
        self.expiry[key] = ttl
        return 1


class _NoCompareDeleteRedis(_FakeRedis):
    def __getattribute__(self, name):
        if name == "compare_delete":
            raise AttributeError(name)
        return super().__getattribute__(name)


class _NoCompareExpireRedis(_FakeRedis):
    def __getattribute__(self, name):
        if name == "compare_expire":
            raise AttributeError(name)
        return super().__getattribute__(name)


class _RaceCompareSetRedis(_FakeRedis):
    def __init__(self):
        super().__init__()
        self._race_injected = False

    def compare_set(self, key, expected, replacement, ttl):
        current = self.store.get(key)
        if not self._race_injected and current == str(expected or ""):
            self._race_injected = True
            self.store[key] = (
                '{"items":[{"trace_id":"req_123","assistant_content":"First answer","route":"kb_qa"},'
                '{"trace_id":"req_456","assistant_content":"Second answer","route":"kb_qa"}]}'
            )
            self.expiry[key] = ttl
            return 0
        return super().compare_set(key, expected, replacement, ttl)


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


def test_stage_cache_keys_include_stage_namespace_and_input_fingerprint():
    keys = PatentKeyFactory(env="test")
    stage1_fingerprint = build_stage1_cache_fingerprint(
        question="what is patent substitution risk",
        conversation_context={"recent_turns_for_llm": [{"role": "user", "content": "earlier context"}]},
        runtime_signature={"planning_model": "gpt-test", "stage1_prompt": "prompt-v1"},
    )
    stage2_fingerprint = build_stage2_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_plan={"question_type": "comparison", "candidate_recall_queries": ["battery safety"]},
        runtime_signature={"retrieval_version": "v1"},
    )
    stage25_fingerprint = build_stage25_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={"references": ["CN115132975B"]},
        source_ids=["CN115132975B"],
        skipped=True,
        skip_reason="patent_mode_no_md_expansion",
        runtime_signature={"retrieval_version": "v1"},
    )
    stage3_plain = build_stage3_cache_fingerprint(
        retrieval_results={"references": ["CN115132975B"]},
        source_ids=["CN115132975B"],
        force_pdf=False,
        runtime_signature={"catalog_index_version": "v1"},
    )
    stage3_pdf = build_stage3_cache_fingerprint(
        retrieval_results={"references": ["CN115132975B"]},
        source_ids=["CN115132975B"],
        force_pdf=True,
        runtime_signature={"catalog_index_version": "v1"},
    )
    stage4_fingerprint = build_stage4_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={"references": ["CN115132975B"]},
        patent_evidence_bundle={
            "source_ids": ["CN115132975B"],
            "evidence_by_patent_id": {
                "CN115132975B": [{"kind": "table", "text": "cached evidence"}],
            },
        },
        runtime_signature={"answer_model": "v1"},
    )
    file_route_fingerprint = build_file_route_cache_fingerprint(
        question="summarize the selected paper",
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        selected_execution_files=[{"file_id": 11, "file_type": "pdf", "file_name": "battery-paper.pdf"}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11]},
        runtime_signature={"handler": "pdf"},
    )

    assert keys.stage_cache("stage1", stage1_fingerprint).startswith("patent:test:qa-core:cache:stage1:")
    assert keys.stage_cache("stage2", stage2_fingerprint).startswith("patent:test:qa-core:cache:stage2:")
    assert keys.stage_cache("stage25", stage25_fingerprint).startswith("patent:test:qa-core:cache:stage25:")
    assert keys.stage_cache("stage4", stage4_fingerprint).startswith("patent:test:qa-core:cache:stage4:")
    assert stage3_plain != stage3_pdf
    assert keys.file_route_cache(file_route_fingerprint).startswith("patent:test:qa-core:cache:file-route:")
    assert keys.file_route_singleflight(file_route_fingerprint).startswith("patent:test:qa-core:lock:file-route:")


def test_stage_cache_fingerprints_change_when_runtime_signature_changes():
    stage2_v1 = build_stage2_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_plan={"question_type": "comparison"},
        runtime_signature={"retrieval_version": "v1"},
    )
    stage2_v2 = build_stage2_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_plan={"question_type": "comparison"},
        runtime_signature={"retrieval_version": "v2"},
    )

    assert stage2_v1 != stage2_v2


def test_stage2_and_stage3_fingerprints_do_not_change_with_parallel_worker_counts():
    stage2_workers_1 = build_stage2_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_plan={"question_type": "comparison", "candidate_recall_queries": ["battery safety"]},
        runtime_signature={"retrieval_version": "v1"},
    )
    stage2_workers_8 = build_stage2_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_plan={"question_type": "comparison", "candidate_recall_queries": ["battery safety"]},
        runtime_signature={"retrieval_version": "v1"},
    )
    stage3_workers_1 = build_stage3_cache_fingerprint(
        retrieval_results={"references": ["CN115132975B"]},
        source_ids=["CN115132975B"],
        force_pdf=False,
        runtime_signature={"catalog_index_version": "v1"},
    )
    stage3_workers_8 = build_stage3_cache_fingerprint(
        retrieval_results={"references": ["CN115132975B"]},
        source_ids=["CN115132975B"],
        force_pdf=False,
        runtime_signature={"catalog_index_version": "v1"},
    )

    assert stage2_workers_1 == stage2_workers_8
    assert stage3_workers_1 == stage3_workers_8


def test_stage4_fingerprint_changes_when_runtime_signature_changes():
    stage4_v1 = build_stage4_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={"references": ["CN115132975B"]},
        patent_evidence_bundle={
            "source_ids": ["CN115132975B"],
            "evidence_by_patent_id": {
                "CN115132975B": [{"kind": "table", "text": "cached evidence"}],
            },
        },
        runtime_signature={"answer_model": "deepseek-v3.1"},
    )
    stage4_v2 = build_stage4_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={"references": ["CN115132975B"]},
        patent_evidence_bundle={
            "source_ids": ["CN115132975B"],
            "evidence_by_patent_id": {
                "CN115132975B": [{"kind": "table", "text": "cached evidence"}],
            },
        },
        runtime_signature={"answer_model": "deepseek-v3.2"},
    )

    assert stage4_v1 != stage4_v2


def test_stage4_fingerprint_ignores_volatile_upstream_cache_flags_and_timings():
    stable = build_stage4_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "metadata": {"retrieval_backend": "vector_hybrid"},
            "cache_hit": False,
            "timings": {"vector_search_ms": 12},
        },
        patent_evidence_bundle={
            "source_ids": ["CN115132975B"],
            "evidence_by_patent_id": {
                "CN115132975B": [{"kind": "table", "text": "cached evidence"}],
            },
            "cache_hit": False,
            "timings": {"stage3_ms": 14},
        },
        runtime_signature={"answer_model": "deepseek-v3.1"},
    )
    volatile = build_stage4_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "metadata": {"retrieval_backend": "vector_hybrid"},
            "cache_hit": True,
            "timings": {"vector_search_ms": 99},
        },
        patent_evidence_bundle={
            "source_ids": ["CN115132975B"],
            "evidence_by_patent_id": {
                "CN115132975B": [{"kind": "table", "text": "cached evidence"}],
            },
            "cache_hit": True,
            "timings": {"stage3_ms": 98},
        },
        runtime_signature={"answer_model": "deepseek-v3.1"},
    )

    assert stable == volatile


def test_stage25_fingerprint_ignores_volatile_stage2_cache_flags_and_timings():
    stable = build_stage25_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "cache_hit": False,
            "negative_cache_hit": False,
            "timings": {"vector_search_ms": 12},
        },
        source_ids=["CN115132975B"],
        skipped=True,
        skip_reason="patent_mode_no_md_expansion",
        runtime_signature={"retrieval_version": "v1"},
    )
    volatile = build_stage25_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "cache_hit": True,
            "negative_cache_hit": True,
            "timings": {"vector_search_ms": 99},
        },
        source_ids=["CN115132975B"],
        skipped=True,
        skip_reason="patent_mode_no_md_expansion",
        runtime_signature={"retrieval_version": "v1"},
    )

    assert stable == volatile


def test_stage3_fingerprint_ignores_volatile_stage2_cache_flags_and_timings():
    stable = build_stage3_cache_fingerprint(
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "cache_hit": False,
            "negative_cache_hit": False,
            "timings": {"vector_search_ms": 12},
        },
        source_ids=["CN115132975B"],
        force_pdf=False,
        runtime_signature={"catalog_index_version": "v1"},
    )
    volatile = build_stage3_cache_fingerprint(
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "cache_hit": True,
            "negative_cache_hit": True,
            "timings": {"vector_search_ms": 99},
        },
        source_ids=["CN115132975B"],
        force_pdf=False,
        runtime_signature={"catalog_index_version": "v1"},
    )

    assert stable == volatile


def test_stage25_fingerprint_ignores_volatile_stage2_metadata_fields():
    stable = build_stage25_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "metadata": {"retrieval_backend": "vector_hybrid"},
        },
        source_ids=["CN115132975B"],
        skipped=True,
        skip_reason="patent_mode_no_md_expansion",
        runtime_signature={"retrieval_version": "v1"},
    )
    volatile = build_stage25_cache_fingerprint(
        question="what is patent substitution risk",
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "metadata": {
                "retrieval_backend": "vector_hybrid",
                "candidate_patent_ids": ["CN115132975B", "US20240001234A1"],
                "retrieval_plan_queries": ["query-a", "query-b"],
                "localization_fallback": "archive_default_anchor",
            },
        },
        source_ids=["CN115132975B"],
        skipped=True,
        skip_reason="patent_mode_no_md_expansion",
        runtime_signature={"retrieval_version": "v1"},
    )

    assert stable == volatile


def test_stage3_fingerprint_ignores_volatile_stage2_metadata_fields():
    stable = build_stage3_cache_fingerprint(
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "metadata": {"retrieval_backend": "vector_hybrid"},
        },
        source_ids=["CN115132975B"],
        force_pdf=False,
        runtime_signature={"catalog_index_version": "v1"},
    )
    volatile = build_stage3_cache_fingerprint(
        retrieval_results={
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "metadata": {
                "retrieval_backend": "vector_hybrid",
                "candidate_patent_ids": ["CN115132975B", "US20240001234A1"],
                "retrieval_plan_queries": ["query-a", "query-b"],
                "localization_fallback": "archive_default_anchor",
            },
        },
        source_ids=["CN115132975B"],
        force_pdf=False,
        runtime_signature={"catalog_index_version": "v1"},
    )

    assert stable == volatile


def test_execution_cache_roundtrips_stage_payloads_and_singleflight_markers():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.set_stage_cache(stage="stage1", fingerprint="sig-stage1", payload={"deep_answer": "answer"}, ttl_seconds=20) is True
    assert cache.get_stage_cache(stage="stage1", fingerprint="sig-stage1") == {"deep_answer": "answer"}

    stage1_token = cache.claim_stage_singleflight(stage="stage1", fingerprint="sig-stage1", ttl_seconds=20)
    assert isinstance(stage1_token, str) and stage1_token
    assert cache.claim_stage_singleflight(stage="stage1", fingerprint="sig-stage1", ttl_seconds=20) == ""
    assert cache.clear_stage_singleflight(stage="stage1", fingerprint="sig-stage1", token=stage1_token) is True
    assert cache.claim_stage_singleflight(stage="stage2", fingerprint="sig-stage2", ttl_seconds=20)
    assert cache.claim_stage_singleflight(stage="stage25", fingerprint="sig-stage25", ttl_seconds=20)
    assert cache.claim_stage_singleflight(stage="stage3", fingerprint="sig-stage3", ttl_seconds=20)
    assert cache.claim_stage_singleflight(stage="stage4", fingerprint="sig-stage4", ttl_seconds=20)


def test_execution_cache_roundtrips_file_route_payloads_and_singleflight_markers():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    payload = {
        "handler": "pdf",
        "answer_text": "cached pdf summary",
        "metadata": {"answer_mode": "pdf_text_summary"},
    }
    assert cache.set_file_route_cache(fingerprint="sig-file-route", payload=payload, ttl_seconds=20) is True
    assert cache.get_file_route_cache(fingerprint="sig-file-route") == payload

    token = cache.claim_file_route_singleflight(fingerprint="sig-file-route", ttl_seconds=20)
    assert isinstance(token, str) and token
    assert cache.claim_file_route_singleflight(fingerprint="sig-file-route", ttl_seconds=20) == ""
    assert cache.clear_file_route_singleflight(fingerprint="sig-file-route", token=token) is True


def test_execution_cache_only_clears_stage_singleflight_when_owner_token_matches():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    token = cache.claim_stage_singleflight(stage="stage1", fingerprint="sig-stage1", ttl_seconds=20)
    redis.store["patent:test:qa-core:lock:stage1:sig-stage1"] = "other-owner"

    assert cache.clear_stage_singleflight(stage="stage1", fingerprint="sig-stage1", token=token) is False
    assert redis.store["patent:test:qa-core:lock:stage1:sig-stage1"] == "other-owner"


def test_execution_cache_fails_closed_when_atomic_stage_singleflight_release_is_unavailable():
    redis = _NoCompareDeleteRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    token = cache.claim_stage_singleflight(stage="stage1", fingerprint="sig-stage1", ttl_seconds=20)

    assert cache.clear_stage_singleflight(stage="stage1", fingerprint="sig-stage1", token=token) is False
    assert redis.store["patent:test:qa-core:lock:stage1:sig-stage1"] == token
    assert cache.last_error == "atomic compare_delete helper unavailable"


def test_execution_cache_renews_stage_singleflight_when_owner_token_matches():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    token = cache.claim_stage_singleflight(stage="stage1", fingerprint="sig-stage1", ttl_seconds=20)

    assert cache.renew_stage_singleflight(stage="stage1", fingerprint="sig-stage1", token=token, ttl_seconds=45) is True
    assert redis.compare_expire_calls == [("patent:test:qa-core:lock:stage1:sig-stage1", token, 45)]
    assert redis.expiry["patent:test:qa-core:lock:stage1:sig-stage1"] == 45


def test_execution_cache_stage_singleflight_renew_fails_closed_without_atomic_compare_expire():
    redis = _NoCompareExpireRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    token = cache.claim_stage_singleflight(stage="stage1", fingerprint="sig-stage1", ttl_seconds=20)

    assert cache.renew_stage_singleflight(stage="stage1", fingerprint="sig-stage1", token=token, ttl_seconds=45) is False
    assert cache.last_error == "atomic compare_expire helper unavailable"


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


def test_execution_cache_turn_identity_owner_token_guards_clear():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    token = cache.claim_turn_identity(conversation_id=123, trace_id="req_123", ttl_seconds=30, owner_token="owner-1")
    redis.store["patent:test:exec:turn:123:req_123"] = "owner-2"

    assert token is True
    assert cache.clear_turn_identity(conversation_id=123, trace_id="req_123", owner_token="owner-1") is False
    assert redis.store["patent:test:exec:turn:123:req_123"] == "owner-2"


def test_execution_cache_turn_identity_legacy_value_still_clears_without_owner_token():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.claim_turn_identity(conversation_id=123, trace_id="req_123", ttl_seconds=30) is True
    assert cache.clear_turn_identity(conversation_id=123, trace_id="req_123") is True


def test_execution_cache_marks_inflight_with_coord_namespace():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.mark_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=30) is True
    assert cache.mark_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=30) is False
    assert redis.store["patent:test:coord:inflight:123:req_123"] == "1"


def test_execution_cache_inflight_owner_token_guards_renew_and_clear():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.mark_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=30, owner_token="owner-1") is True
    assert redis.store["patent:test:coord:inflight:123:req_123"] == "owner-1"
    redis.store["patent:test:coord:inflight:123:req_123"] = "owner-2"

    assert cache.renew_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=30, owner_token="owner-1") is False
    assert cache.clear_turn_inflight(conversation_id=123, trace_id="req_123", owner_token="owner-1") is False
    assert redis.store["patent:test:coord:inflight:123:req_123"] == "owner-2"


def test_execution_cache_inflight_legacy_value_still_renews_and_clears_without_owner_token():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.mark_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=30) is True
    assert cache.renew_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=45) is True
    assert cache.clear_turn_inflight(conversation_id=123, trace_id="req_123") is True


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


def test_execution_cache_preserves_multiple_pending_overlay_entries():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_123", "assistant_content": "First answer", "route": "kb_qa"},
        ttl_seconds=60,
    )
    cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_456", "assistant_content": "Second answer", "route": "kb_qa"},
        ttl_seconds=60,
    )

    overlays = cache.get_overlay_assistants(user_id=42, conversation_id=123)

    assert [item["trace_id"] for item in overlays] == ["req_123", "req_456"]
    assert cache.get_overlay_assistant(user_id=42, conversation_id=123)["trace_id"] == "req_456"


def test_execution_cache_retries_overlay_append_when_compare_set_detects_race():
    redis = _RaceCompareSetRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_123", "assistant_content": "First answer", "route": "kb_qa"},
        ttl_seconds=60,
    )

    assert (
        cache.set_overlay_assistant(
            user_id=42,
            conversation_id=123,
            payload={"trace_id": "req_789", "assistant_content": "Third answer", "route": "kb_qa"},
            ttl_seconds=60,
        )
        is True
    )

    overlays = cache.get_overlay_assistants(user_id=42, conversation_id=123)

    assert [item["trace_id"] for item in overlays] == ["req_123", "req_456", "req_789"]


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


def test_original_cache_key_includes_section_anchor_format_and_version():
    keys = PatentKeyFactory(env="test")

    key = keys.original_cache("CN123456789A", "claim", "claim:1", "html", "v20260331")

    assert key == "patent:test:original:cache:CN123456789A:claim:claim%3A1:html:v20260331"


def test_execution_cache_roundtrips_original_cache_payload():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert (
        cache.set_original_cache(
            canonical_patent_id="CN123456789A",
            section="claim",
            anchor="claim:1",
            response_format="html",
            original_version="v20260331",
            payload={"content": "<div>claim</div>"},
            ttl_seconds=30,
        )
        is True
    )
    assert (
        cache.get_original_cache(
            canonical_patent_id="CN123456789A",
            section="claim",
            anchor="claim:1",
            response_format="html",
            original_version="v20260331",
        )
        == {"content": "<div>claim</div>"}
    )


def test_original_cache_key_distinguishes_anchor_values_with_trailing_colons():
    keys = PatentKeyFactory(env="test")

    plain = keys.original_cache("CN123456789A", "description", "paragraph:p-1", "html", "v20260331")
    polluted = keys.original_cache("CN123456789A", "description", "paragraph:p-1:", "html", "v20260331")

    assert plain != polluted


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


def test_execution_cache_pending_turn_owner_token_guards_advance_and_clear():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.claim_pending_turn(
        conversation_id=123,
        trace_id="req_123",
        ttl_seconds=30,
        owner_token="owner-1",
    ) is True
    state = cache.get_pending_turn_state(conversation_id=123)
    assert state["trace_id"] == "req_123"
    assert state["owner_token"] == "owner-1"

    assert cache.mark_pending_turn_user_written(
        conversation_id=123,
        trace_id="req_123",
        ttl_seconds=30,
        owner_token="owner-2",
    ) is False
    assert cache.get_pending_turn_state(conversation_id=123)["user_written"] is False

    assert cache.clear_pending_turn(
        conversation_id=123,
        trace_id="req_123",
        owner_token="owner-2",
    ) is False
    assert cache.get_pending_turn(conversation_id=123) == "req_123"

    assert cache.mark_pending_turn_user_written(
        conversation_id=123,
        trace_id="req_123",
        ttl_seconds=30,
        owner_token="owner-1",
    ) is True
    assert cache.get_pending_turn_state(conversation_id=123)["user_written"] is True
    assert cache.clear_pending_turn(
        conversation_id=123,
        trace_id="req_123",
        owner_token="owner-1",
    ) is True


def test_execution_cache_can_transfer_pending_turn_owner_for_same_trace_retry():
    redis = _FakeRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.claim_pending_turn(
        conversation_id=123,
        trace_id="req_123",
        ttl_seconds=30,
        user_written=True,
        owner_token="owner-1",
    ) is True
    assert cache.transfer_pending_turn_owner(
        conversation_id=123,
        trace_id="req_123",
        ttl_seconds=30,
        owner_token="owner-2",
    ) is True

    state = cache.get_pending_turn_state(conversation_id=123)
    assert state["trace_id"] == "req_123"
    assert state["user_written"] is True
    assert state["owner_token"] == "owner-2"
    assert cache.clear_pending_turn(
        conversation_id=123,
        trace_id="req_123",
        owner_token="owner-1",
    ) is False
    assert cache.clear_pending_turn(
        conversation_id=123,
        trace_id="req_123",
        owner_token="owner-2",
    ) is True


def test_execution_cache_refuses_non_atomic_pending_turn_clear():
    redis = _NoCompareDeleteRedis()
    cache = ExecutionCache(redis, PatentKeyFactory(env="test"))

    assert cache.claim_pending_turn(conversation_id=123, trace_id="req_123", ttl_seconds=30) is True
    assert cache.clear_pending_turn(conversation_id=123, trace_id="req_123") is False
    assert cache.get_pending_turn(conversation_id=123) == "req_123"
