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

    assert cache_key.startswith("agentcode:cache:stage1:")
    assert lock_key.startswith("agentcode:lock:stage1:")

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


def test_stage1_cache_does_not_store_json_parse_fallback_payload():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()

    cached = cache_stage1_result(
        redis_service=service,
        runtime=runtime,
        question="q",
        stage1_result={
            "success": False,
            "deep_answer": "",
            "retrieval_claims": [],
            "upstream_error": {
                "code": "STAGE1_JSON_INVALID",
                "error": "stage1_json_invalid",
                "message": "大模型输出 json 不规范，请重试",
            },
            "raw_response": "```json ... ```",
        },
    )

    assert cached is False
    assert get_cached_stage1_result(redis_service=service, runtime=runtime, question="q") is None

def test_stage1_cache_key_changes_when_conversation_context_changes():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()

    first_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={"recent_turns_for_llm": [{"role": "assistant", "content": "上一轮在讨论LFP"}]},
    )
    second_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={"recent_turns_for_llm": [{"role": "assistant", "content": "上一轮在讨论NCM"}]},
    )

    assert first_key != second_key


def test_stage1_cache_key_ignores_non_prompt_context_fields():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()

    first_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={
            "recent_turns_for_llm": [{"role": "assistant", "content": "上一轮在讨论LFP"}],
            "summary_for_llm": {"short_summary": "讨论LFP优缺点"},
            "conversation_state": {"last_turn_route": "kb_qa"},
            "source_selection": {"source_scope": "pdf+kb", "selected_file_ids": [1, 2]},
        },
    )
    second_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={
            "recent_turns_for_llm": [{"role": "assistant", "content": "上一轮在讨论LFP"}],
            "summary_for_llm": {"short_summary": "讨论LFP优缺点"},
            "conversation_state": {"last_turn_route": "hybrid_qa"},
            "source_selection": {"source_scope": "kb", "selected_file_ids": [9]},
        },
    )

    assert first_key == second_key


def test_stage1_cache_key_normalizes_prompt_equivalent_whitespace_and_empty_turns():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()

    first_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={
            "recent_turns_for_llm": [
                {"role": "assistant", "content": "  上一轮   在讨论LFP  "},
                {"role": "assistant", "content": "   "},
            ],
            "summary_for_llm": {
                "short_summary": " 讨论  LFP优缺点 ",
                "open_threads": ["  倍率性能  ", "   "],
            },
        },
    )
    second_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={
            "recent_turns_for_llm": [
                {"role": "assistant", "content": "上一轮 在讨论LFP"},
            ],
            "summary_for_llm": {
                "short_summary": "讨论 LFP优缺点",
                "open_threads": ["倍率性能"],
            },
        },
    )

    assert first_key == second_key


def test_stage1_cache_roundtrip_reuses_prompt_equivalent_context():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()
    stage1_result = {"success": True, "deep_answer": "answer", "retrieval_claims": [{"claim": "lfp"}]}

    cached = cache_stage1_result(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={
            "recent_turns_for_llm": [{"role": "assistant", "content": "  上一轮   在讨论LFP  "}],
            "summary_for_llm": {"short_summary": " 讨论  LFP优缺点 "},
            "conversation_state": {"last_turn_route": "kb_qa"},
        },
        stage1_result=stage1_result,
    )

    assert cached is True
    assert get_cached_stage1_result(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={
            "recent_turns_for_llm": [{"role": "assistant", "content": "上一轮 在讨论LFP"}],
            "summary_for_llm": {"short_summary": "讨论 LFP优缺点"},
            "source_selection": {"source_scope": "kb"},
        },
    ) == stage1_result


def test_stage1_cache_key_changes_when_graph_payload_changes():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()

    first_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        graph_cache_fingerprint="none",
    )
    second_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        graph_cache_fingerprint="graph:abc",
    )

    assert first_key != second_key


def test_stage1_cache_key_distinguishes_non_equivalent_open_threads_spacing():
    service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _runtime()

    first_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={
            "summary_for_llm": {"open_threads": ["a   b"]},
        },
    )
    second_key = build_stage1_cache_key(
        redis_service=service,
        runtime=runtime,
        question="那它的缺点呢",
        conversation_context={
            "summary_for_llm": {"open_threads": ["a b"]},
        },
    )

    assert first_key != second_key
