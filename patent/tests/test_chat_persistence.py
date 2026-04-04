from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import time

import pytest

from server.errors import codes
from server.errors.core import APIError
from server.patent.cache_keys import PatentKeyFactory
from server.patent.executor import PatentExecutor
from server.patent.models import PatentRetrievalClaim
from server.schemas.request_models import PatentAskRequest
from server.services.ask_service import AskService
from server.services.chat_persistence import ChatPersistenceService
from server.services.execution_cache import ExecutionCache
from server.services.execution_lock import ExecutionLockManager


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.expiry: dict[str, int | None] = {}
        self.compare_expire_calls: list[tuple[str, str, int]] = []
        self.reject_compare_expire = False

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.expiry[key] = ex
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        removed = self.store.pop(key, None)
        self.expiry.pop(key, None)
        return 1 if removed is not None else 0

    def compare_delete(self, key, token):
        if self.store.get(key) != token:
            return 0
        self.store.pop(key, None)
        self.expiry.pop(key, None)
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

    def compare_expire(self, key, token, ttl):
        self.compare_expire_calls.append((key, token, ttl))
        if self.reject_compare_expire:
            return 0
        if self.store.get(key) != token:
            return 0
        self.expiry[key] = ttl
        return 1


@dataclass
class _FakeAuthorityClient:
    snapshot_payload: dict[str, Any]
    fail_accept: bool = False
    fail_terminal_accept: bool = False
    fail_user_write: bool = False
    fail_snapshot: bool = False
    assistant_accepted: bool = True

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.user_writes: list[dict[str, Any]] = []
        self.snapshot_reads: list[dict[str, Any]] = []
        self.assistant_accepts: list[dict[str, Any]] = []
        self.assistant_terminal_accepts: list[dict[str, Any]] = []

    def write_user_turn(self, **kwargs):
        self.calls.append("user_write")
        self.user_writes.append(dict(kwargs))
        if self.fail_user_write:
            raise RuntimeError("user write failed")
        return {
            "success": True,
            "conversation_id": kwargs["conversation_id"],
            "message_id": "m_user_1",
            "trace_id": kwargs["trace_id"],
            "idempotency_key": f'{kwargs["conversation_id"]}:{kwargs["trace_id"]}:user',
            "created_at": "2026-03-25T12:00:00Z",
            "deduped": len(self.user_writes) > 1,
        }

    def read_context_snapshot(self, **kwargs):
        self.calls.append("snapshot")
        self.snapshot_reads.append(dict(kwargs))
        if self.fail_snapshot:
            raise RuntimeError("snapshot failed")
        return {
            "conversation_id": kwargs["conversation_id"],
            "user_id": kwargs["user_id"],
            "snapshot_version": 7,
            "updated_at": "2026-03-25T12:00:00Z",
            "summary": {},
            "recent_turns": [],
            "conversation_state": {},
            **self.snapshot_payload,
        }

    def accept_assistant_turn_async(self, **kwargs):
        self.calls.append("assistant_accept")
        self.assistant_accepts.append(dict(kwargs))
        if self.fail_accept:
            raise RuntimeError("assistant accept failed")
        return {
            "accepted": self.assistant_accepted,
            "event_id": "evt_1",
            "trace_id": kwargs["trace_id"],
            "idempotency_key": f'{kwargs["conversation_id"]}:{kwargs["trace_id"]}:assistant',
            "status": "accepted" if self.assistant_accepted else "rejected",
        }

    def accept_assistant_terminal_async(self, **kwargs):
        self.calls.append("assistant_terminal_accept")
        self.assistant_terminal_accepts.append(dict(kwargs))
        if self.fail_terminal_accept:
            raise RuntimeError("assistant terminal accept failed")
        return {
            "accepted": True,
            "event_id": "evt_terminal_1",
            "trace_id": kwargs["trace_id"],
            "idempotency_key": f'{kwargs["conversation_id"]}:{kwargs["trace_id"]}:assistant',
            "status": "accepted",
        }


def _make_request(
    *,
    conversation_id: int | None = 123,
    trace_id: str = "req_123",
    chat_history: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    route: str = "kb_qa",
    source_scope: str | None = None,
    turn_mode: str = "kb_only",
    kb_enabled: bool = True,
    allow_kb_verification: bool = False,
    used_files: list[dict[str, Any]] | None = None,
    execution_files: list[dict[str, Any]] | None = None,
    selected_file_ids: list[int] | None = None,
    primary_file_id: int | None = None,
    file_selection: dict[str, Any] | None = None,
) -> PatentAskRequest:
    return PatentAskRequest(
        question="Explain the novelty.",
        conversation_id=conversation_id,
        chat_history=list(chat_history or []),
        requested_mode="patent",
        actual_mode="patent",
        route=route,
        source_scope=source_scope,
        turn_mode=turn_mode,
        kb_enabled=kb_enabled,
        allow_kb_verification=allow_kb_verification,
        used_files=list(used_files or []),
        execution_files=list(execution_files or []),
        selected_file_ids=list(selected_file_ids or []),
        primary_file_id=primary_file_id,
        file_selection=dict(file_selection or {}),
        trace_id=trace_id,
        options=dict(options or {}),
    )


def _build_service(
    *,
    authority_client: _FakeAuthorityClient | None = None,
    redis: _FakeRedis | None = None,
    durable_mode_enabled: bool = True,
    trace_id_factory=None,
    lock_ttl_seconds: int = 30,
    inflight_ttl_seconds: int = 30,
) -> tuple[ChatPersistenceService, _FakeAuthorityClient, _FakeRedis]:
    fake_redis = redis or _FakeRedis()
    authority = authority_client or _FakeAuthorityClient(snapshot_payload={})
    keys = PatentKeyFactory(env="test")
    service = ChatPersistenceService(
        authority_client=authority,
        execution_lock_manager=ExecutionLockManager(fake_redis, key_factory=keys),
        execution_cache=ExecutionCache(fake_redis, keys),
        durable_mode_enabled=durable_mode_enabled,
        lock_ttl_seconds=lock_ttl_seconds,
        inflight_ttl_seconds=inflight_ttl_seconds,
        turn_state_ttl_seconds=300,
        overlay_ttl_seconds=60,
        trace_id_factory=trace_id_factory,
    )
    return service, authority, fake_redis


def test_durable_flow_orders_user_write_snapshot_execute_accept():
    authority = _FakeAuthorityClient(
        snapshot_payload={
            "recent_turns": [{"role": "user", "content": "Earlier question", "trace_id": "req_old"}],
            "conversation_state": {},
        }
    )
    service, authority, redis = _build_service(authority_client=authority)
    execute_contexts = []

    result = service.run_turn(
        request=_make_request(),
        user_id=42,
        execute_turn=lambda context: (
            authority.calls.append("execute"),
            execute_contexts.append(context),
            {"answer_text": "Patent answer", "timings": {"total_ms": 12}},
        )[-1],
    )

    assert authority.calls == ["user_write", "snapshot", "execute", "assistant_accept"]
    assert execute_contexts[0]["chat_history"][0]["content"] == "Earlier question"
    assert result["trace_id"] == "req_123"
    assert result["assistant_accept"]["accepted"] is True
    assert result["context"]["persistence_mode"] == "durable"
    assert redis.get("patent:test:coord:inflight:123:req_123") is None
    assert redis.get("patent:test:exec:conversation-lock:123") is None


def test_prepare_turn_keeps_patent_raw_context_boundary_for_executor_normalization():
    authority = _FakeAuthorityClient(
        snapshot_payload={
            "summary": {"short_summary": "Earlier patent context"},
            "recent_turns": [{"role": "assistant", "content": "Earlier turn", "trace_id": "req_old"}],
            "conversation_state": {"last_turn_route": "kb_qa"},
        }
    )
    service, _, _ = _build_service(authority_client=authority)

    prepared = service.prepare_turn(
        request=_make_request(),
        user_id=42,
    )

    context = prepared["context"]
    assert context["chat_history"] == [{"role": "assistant", "content": "Earlier turn", "trace_id": "req_old", "created_at": "", "message_id": ""}]
    assert context["summary"] == {"short_summary": "Earlier patent context"}
    assert context["conversation_state"] == {"last_turn_route": "kb_qa"}
    assert "recent_turns_for_llm" not in context
    assert "summary_for_llm" not in context


def test_assistant_accept_failure_blocks_success():
    authority = _FakeAuthorityClient(snapshot_payload={}, fail_accept=True)
    service, authority, redis = _build_service(authority_client=authority)

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.AUTHORITY_UNAVAILABLE
    assert authority.calls == ["user_write", "snapshot", "assistant_accept"]
    assert redis.get("patent:test:overlay:assistant:42:123") is None
    assert redis.get("patent:test:coord:inflight:123:req_123") is None
    assert redis.get("patent:test:exec:conversation-lock:123") is None


def test_assistant_accept_translates_reference_strings_into_authority_objects():
    service, authority, _ = _build_service()

    result = service.run_turn(
        request=_make_request(),
        user_id=42,
        execute_turn=lambda context: {
            "answer_text": "Patent answer",
            "references": ["CN123456789A", "US20240001234A1"],
            "timings": {"total_ms": 12},
        },
    )

    assert result["assistant_accept"]["accepted"] is True
    assert authority.assistant_accepts[0]["references"] == [
        {"source_type": "patent", "canonical_patent_id": "CN123456789A"},
        {"source_type": "patent", "canonical_patent_id": "US20240001234A1"},
    ]


def test_durable_flow_maps_mode_origin_and_patent_final_event_fields_to_authority():
    service, authority, _ = _build_service()

    result = service.run_turn(
        request=_make_request(
            options={
                "mode_origin": {
                    "requested_mode": "patent",
                    "execution_backend": "fastQA",
                    "compatibility_route": True,
                }
            }
        ),
        user_id=42,
        execute_turn=lambda context: {
            "answer_text": "Patent answer",
            "metadata": {
                "retrieval_backend": "metadata_lexical",
                "mode_origin": {
                    "requested_mode": "patent",
                    "execution_backend": "fastQA",
                    "compatibility_route": True,
                },
            },
            "references": ["CN123456789A"],
            "reference_objects": [
                {
                    "source_type": "patent",
                    "canonical_patent_id": "CN123456789A",
                    "section_type": "claim",
                }
            ],
            "reference_links": [
                {
                    "type": "original_view",
                    "canonical_patent_id": "CN123456789A",
                    "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1",
                }
            ],
            "original_links": [
                {
                    "type": "original_view",
                    "canonical_patent_id": "CN123456789A",
                    "section": "claim",
                    "claim_number": 1,
                    "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1",
                }
            ],
            "timings": {"total_ms": 12},
        },
    )

    assert result["assistant_accept"]["accepted"] is True
    assert authority.user_writes[0]["mode_origin_requested_mode"] == "patent"
    assert authority.user_writes[0]["mode_origin_execution_backend"] == "fastQA"
    assert authority.user_writes[0]["compatibility_route"] is True
    assert authority.assistant_accepts[0]["metadata"]["mode_origin"]["execution_backend"] == "fastQA"
    assert authority.assistant_accepts[0]["reference_objects"][0]["section_type"] == "claim"
    assert authority.assistant_accepts[0]["reference_links"][0]["type"] == "original_view"
    assert authority.assistant_accepts[0]["original_links"][0]["section"] == "claim"


def test_durable_flow_preserves_file_aware_route_contract_for_authority():
    service, authority, _ = _build_service()

    result = service.run_turn(
        request=_make_request(
            route="hybrid_qa",
            source_scope="pdf+kb",
            turn_mode="mixed",
            kb_enabled=True,
            allow_kb_verification=True,
            used_files=[{"file_id": 11, "file_type": "pdf"}],
            execution_files=[{"file_id": 11, "file_type": "pdf"}],
            selected_file_ids=[11],
            primary_file_id=11,
            file_selection={"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf+kb"},
        ),
        user_id=42,
        execute_turn=lambda context: {
            "answer_text": "Patent file answer",
            "route": "hybrid_qa",
            "source_scope": "pdf+kb",
            "used_files": [{"file_id": 11, "file_type": "pdf"}],
            "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf+kb"},
            "timings": {"total_ms": 12},
        },
    )

    assert result["assistant_accept"]["accepted"] is True
    assert authority.user_writes[0]["route"] == "hybrid_qa"
    assert authority.user_writes[0]["source_scope"] == "pdf+kb"
    assert authority.user_writes[0]["selected_file_ids"] == [11]
    assert authority.snapshot_reads[0]["route"] == "hybrid_qa"
    assert authority.snapshot_reads[0]["source_scope"] == "pdf+kb"
    assert authority.assistant_accepts[0]["route"] == "hybrid_qa"
    assert authority.assistant_accepts[0]["source_scope"] == "pdf+kb"
    assert authority.assistant_accepts[0]["used_files"] == [{"file_id": 11, "file_type": "pdf"}]


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("references", ["bad-reference"]),
        ("reference_objects", {"canonical_patent_id": "CN123456789A"}),
        ("reference_links", {"viewer_uri": "/api/patent/original/CN123456789A"}),
        ("original_links", {"viewer_uri": "/api/patent/original/CN123456789A"}),
        ("used_files", {"file_id": 1}),
    ],
)
def test_durable_sync_rejects_invalid_result_payload_before_assistant_accept(field_name, field_value):
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                field_name: field_value,
                "timings": {"total_ms": 12},
            }

    persistence_service, authority, redis = _build_service()
    ask_service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=persistence_service,
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        ask_service.sync_ask(_make_request(), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR
    assert authority.assistant_accepts == []
    assert authority.assistant_terminal_accepts[0]["terminal_status"] == "failed"
    assert authority.assistant_terminal_accepts[0]["failure"]["code"] == codes.INTERNAL_ERROR
    assert persistence_service.execution_cache.get_turn_result(conversation_id=123, trace_id="req_123") is None
    assert redis.get("patent:test:coord:inflight:123:req_123") is None
    assert redis.get("patent:test:exec:conversation-lock:123") is None


def test_durable_stream_rejects_invalid_result_payload_before_assistant_accept():
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "used_files": {"file_id": 1},
                "timings": {"total_ms": 12},
            }

    persistence_service, authority, redis = _build_service()
    ask_service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=persistence_service,
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(ask_service.stream_ask(_make_request(), user_id=42))

    assert [event["type"] for event in events] == ["metadata", "error"]
    assert authority.assistant_accepts == []
    assert authority.assistant_terminal_accepts[0]["terminal_status"] == "failed"
    assert authority.assistant_terminal_accepts[0]["failure"]["code"] == codes.INTERNAL_ERROR
    assert persistence_service.execution_cache.get_turn_result(conversation_id=123, trace_id="req_123") is None
    assert redis.get("patent:test:coord:inflight:123:req_123") is None
    assert redis.get("patent:test:exec:conversation-lock:123") is None


def test_durable_sync_rejects_failed_execution_result_before_assistant_accept():
    class _FailedExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "",
                "route": "kb_qa",
                "metadata": {
                    "success": False,
                    "failed_stage": "stage4",
                },
                "steps": [
                    {
                        "step": "stage4",
                        "title": "阶段四",
                        "message": "阶段四：答案生成失败",
                        "status": "failed",
                    }
                ],
                "timings": {"stage4": 21},
            }

    persistence_service, authority, redis = _build_service()
    ask_service = AskService(
        patent_executor=_FailedExecutor(),
        persistence_service=persistence_service,
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        ask_service.sync_ask(_make_request(), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR
    assert authority.assistant_accepts == []
    assert authority.assistant_terminal_accepts[0]["terminal_status"] == "failed"
    assert authority.assistant_terminal_accepts[0]["failure"]["stage"] == "stage4"
    assert persistence_service.execution_cache.get_turn_result(conversation_id=123, trace_id="req_123") is None
    assert redis.get("patent:test:coord:inflight:123:req_123") is None
    assert redis.get("patent:test:exec:conversation-lock:123") is None


def test_durable_sync_aborts_persistence_for_real_staged_runtime_failure():
    class _FailingStagedRuntime:
        def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None):
            return {
                "deep_answer": "draft",
                "retrieval_claims": [
                    PatentRetrievalClaim(
                        claim="compare replacement risk",
                        keywords=["battery safety"],
                        preferred_sections=["claims"],
                        filters={},
                    )
                ],
                "retrieval_plan": {},
            }

        def stage2_targeted_retrieval(self, retrieval_plan, *, user_question: str, should_cancel=None, active_stream_count=None):
            return {
                "references": ["CN115132975B"],
                "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "vector_hybrid"},
            }

        def _extract_patent_ids_from_results(self, retrieval_results):
            return ["CN115132975B"]

        def stage25_patent_evidence_expansion(self, *, retrieval_results, user_question: str, source_ids: list[str]):
            return {"skipped": True, "skip_reason": "patent_mode_no_md_expansion", "retrieval_results": retrieval_results}

        def stage3_load_patent_evidence(self, *, retrieval_results, source_ids: list[str], should_cancel=None):
            return {"source_ids": list(source_ids), "evidences": []}

        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle,
            retrieval_results=None,
            should_cancel=None,
            conversation_context=None,
        ):
            return {"success": False, "final_answer": "", "metadata": {"failed_stage": "stage4"}}

    persistence_service, authority, redis = _build_service()
    ask_service = AskService(
        patent_executor=PatentExecutor(runtime=_FailingStagedRuntime()),
        persistence_service=persistence_service,
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        ask_service.sync_ask(_make_request(), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR
    assert authority.assistant_accepts == []
    assert authority.assistant_terminal_accepts[0]["terminal_status"] == "failed"
    assert authority.assistant_terminal_accepts[0]["failure"]["stage"] == "stage4"
    assert persistence_service.execution_cache.get_turn_result(conversation_id=123, trace_id="req_123") is None
    assert redis.get("patent:test:coord:inflight:123:req_123") is None
    assert redis.get("patent:test:exec:conversation-lock:123") is None


def test_durable_sync_rejects_mismatched_references_and_reference_objects_before_assistant_accept():
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "references": ["US20240001234A1"],
                "reference_objects": [
                    {
                        "source_type": "patent",
                        "canonical_patent_id": "CN123456789A",
                    }
                ],
                "timings": {"total_ms": 12},
            }

    persistence_service, authority, redis = _build_service()
    ask_service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=persistence_service,
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        ask_service.sync_ask(_make_request(), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR
    assert authority.assistant_accepts == []
    assert authority.assistant_terminal_accepts[0]["terminal_status"] == "failed"
    assert authority.assistant_terminal_accepts[0]["failure"]["code"] == codes.INTERNAL_ERROR
    assert persistence_service.execution_cache.get_turn_result(conversation_id=123, trace_id="req_123") is None
    assert redis.get("patent:test:coord:inflight:123:req_123") is None
    assert redis.get("patent:test:exec:conversation-lock:123") is None


def test_overlay_merges_when_authority_snapshot_lags():
    authority = _FakeAuthorityClient(
        snapshot_payload={
            "recent_turns": [{"role": "user", "content": "Prior question", "trace_id": "req_prev_user"}],
            "conversation_state": {"last_assistant_trace_id": "req_prev_user"},
        }
    )
    service, _, _ = _build_service(authority_client=authority)
    service.execution_cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_prev_assistant", "route": "kb_qa", "assistant_content": "Pending answer"},
        ttl_seconds=60,
    )

    context = service.load_conversation_context(request=_make_request(), user_id=42, trace_id="req_123")

    assert context["pending_overlay"]["trace_id"] == "req_prev_assistant"
    assert context["chat_history"][-1]["role"] == "assistant"
    assert context["chat_history"][-1]["content"] == "Pending answer"


def test_duplicate_finalization_is_not_reported_twice_for_same_turn():
    service, authority, _ = _build_service()
    request = _make_request()

    first = service.run_turn(
        request=request,
        user_id=42,
        execute_turn=lambda context: {"answer_text": "Patent answer"},
    )
    second = service.run_turn(
        request=request,
        user_id=42,
        execute_turn=lambda context: {"answer_text": "Patent answer"},
    )

    assert first["assistant_accept"]["accepted"] is True
    assert second["assistant_accept"] is None
    assert second["assistant_accept_skipped"] is True
    assert len(authority.assistant_accepts) == 1


def test_cached_replay_preserves_evidence_fields_for_assistant_accept_skipped_turn():
    authority = _FakeAuthorityClient(snapshot_payload={})
    persistence_service, authority, _ = _build_service(authority_client=authority)

    class _EvidenceExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "references": ["CN123456789A"],
                "reference_objects": [
                    {
                        "source_type": "patent",
                        "canonical_patent_id": "CN123456789A",
                        "section_type": "claim",
                    }
                ],
                "reference_links": [
                    {
                        "type": "original_view",
                        "canonical_patent_id": "CN123456789A",
                        "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1",
                    }
                ],
                "original_links": [
                    {
                        "type": "original_view",
                        "canonical_patent_id": "CN123456789A",
                        "section": "claim",
                        "claim_number": 1,
                        "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1",
                    }
                ],
                "timings": {"total_ms": 12},
            }

    ask_service = AskService(
        patent_executor=_EvidenceExecutor(),
        persistence_service=persistence_service,
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    first = ask_service.sync_ask(_make_request(), user_id=42)
    second_events = list(ask_service.stream_ask(_make_request(), user_id=42))

    assert first["references"] == ["CN123456789A"]
    assert len(authority.assistant_accepts) == 1
    assert second_events[-1]["type"] == "done"
    assert second_events[-1]["references"] == ["CN123456789A"]
    assert second_events[-1]["reference_objects"] == [
        {
            "source_type": "patent",
            "canonical_patent_id": "CN123456789A",
            "section_type": "claim",
        }
    ]
    assert second_events[-1]["reference_links"] == [
        {
            "type": "original_view",
            "canonical_patent_id": "CN123456789A",
            "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1",
        }
    ]
    assert second_events[-1]["original_links"] == [
        {
            "type": "original_view",
            "canonical_patent_id": "CN123456789A",
            "section": "claim",
            "claim_number": 1,
            "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1",
        }
    ]


def test_overlay_cleanup_runs_after_authority_converges():
    authority = _FakeAuthorityClient(
        snapshot_payload={
            "recent_turns": [{"role": "assistant", "content": "Stored answer", "trace_id": "req_prev"}],
            "conversation_state": {"last_assistant_trace_id": "req_prev"},
        }
    )
    service, _, _ = _build_service(authority_client=authority)
    service.execution_cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_prev", "route": "kb_qa", "assistant_content": "Pending answer"},
        ttl_seconds=60,
    )

    context = service.load_conversation_context(request=_make_request(), user_id=42, trace_id="req_123")

    assert context["pending_overlay"] is None
    assert service.execution_cache.get_overlay_assistant(user_id=42, conversation_id=123) is None


def test_overlay_merge_keeps_multiple_unconverged_assistant_turns():
    authority = _FakeAuthorityClient(
        snapshot_payload={
            "recent_turns": [{"role": "user", "content": "Prior question", "trace_id": "req_prev_user"}],
            "conversation_state": {"last_assistant_trace_id": "req_prev_user"},
        }
    )
    service, _, _ = _build_service(authority_client=authority)
    service.execution_cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_prev_assistant_1", "route": "kb_qa", "assistant_content": "Pending answer 1"},
        ttl_seconds=60,
    )
    service.execution_cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_prev_assistant_2", "route": "kb_qa", "assistant_content": "Pending answer 2"},
        ttl_seconds=60,
    )

    context = service.load_conversation_context(request=_make_request(), user_id=42, trace_id="req_123")

    assert [item["trace_id"] for item in context["chat_history"][-2:]] == [
        "req_prev_assistant_1",
        "req_prev_assistant_2",
    ]
    assert context["pending_overlay"]["trace_id"] == "req_prev_assistant_2"


def test_overlay_cleanup_does_not_delete_newer_overlay_written_after_snapshot_read():
    authority = _FakeAuthorityClient(
        snapshot_payload={
            "recent_turns": [{"role": "assistant", "content": "Stored answer", "trace_id": "req_prev"}],
            "conversation_state": {"last_assistant_trace_id": "req_prev"},
        }
    )
    service, _, _ = _build_service(authority_client=authority)
    original_delete_overlay = service.execution_cache.delete_overlay_assistant_if_unchanged

    service.execution_cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_prev", "route": "kb_qa", "assistant_content": "Pending answer"},
        ttl_seconds=60,
    )

    def deleting_with_race(**kwargs):
        service.execution_cache.set_overlay_assistant(
            user_id=42,
            conversation_id=123,
            payload={"trace_id": "req_new", "route": "kb_qa", "assistant_content": "New pending answer"},
            ttl_seconds=60,
        )
        return original_delete_overlay(**kwargs)

    service.execution_cache.delete_overlay_assistant_if_unchanged = deleting_with_race

    context = service.load_conversation_context(request=_make_request(), user_id=42, trace_id="req_123")

    assert context["pending_overlay"] is None
    assert service.execution_cache.get_overlay_assistant(user_id=42, conversation_id=123)["trace_id"] == "req_new"


def test_retry_after_user_write_before_accept_converges_on_same_turn():
    service, authority, _ = _build_service()
    request = _make_request()

    with pytest.raises(RuntimeError):
        service.run_turn(
            request=request,
            user_id=42,
            execute_turn=lambda context: (_ for _ in ()).throw(RuntimeError("executor boom")),
        )

    result = service.run_turn(
        request=request,
        user_id=42,
        execute_turn=lambda context: {"answer_text": "Patent answer"},
    )

    assert result["assistant_accept"]["accepted"] is True
    assert [item["trace_id"] for item in authority.user_writes] == ["req_123"]
    assert [item["trace_id"] for item in authority.assistant_accepts] == ["req_123"]


def test_distinct_trace_same_conversation_is_rejected_while_inflight():
    service, _, _ = _build_service()
    seen_error = None

    def execute_turn(context):
        nonlocal seen_error
        with pytest.raises(APIError) as exc_info:
            service.run_turn(
                request=_make_request(trace_id="req_456"),
                user_id=42,
                execute_turn=lambda inner: {"answer_text": "Second answer"},
            )
        seen_error = exc_info.value
        return {"answer_text": "First answer"}

    service.run_turn(
        request=_make_request(trace_id="req_123"),
        user_id=42,
        execute_turn=execute_turn,
    )

    assert seen_error is not None
    assert seen_error.code == codes.PATENT_BUSY
    assert seen_error.retriable is True




def test_busy_path_does_not_clear_foreign_inflight_marker():
    service, _, redis = _build_service()
    service.execution_cache.mark_turn_inflight(conversation_id=123, trace_id="req_123", ttl_seconds=30)

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.PATENT_BUSY
    assert redis.get("patent:test:coord:inflight:123:req_123") == "1"


def test_user_write_failure_maps_to_authority_unavailable():
    authority = _FakeAuthorityClient(snapshot_payload={}, fail_user_write=True)
    service, authority, _ = _build_service(authority_client=authority)

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.AUTHORITY_UNAVAILABLE
    assert authority.calls == ["user_write"]


def test_snapshot_failure_maps_to_authority_unavailable():
    authority = _FakeAuthorityClient(snapshot_payload={}, fail_snapshot=True)
    service, authority, _ = _build_service(authority_client=authority)

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.AUTHORITY_UNAVAILABLE
    assert authority.calls == ["user_write", "snapshot"]

def test_missing_trace_id_is_generated_once_and_reused_for_same_turn():
    service, authority, _ = _build_service(trace_id_factory=lambda: "generated-trace")
    seen_trace_ids = []

    result = service.run_turn(
        request=_make_request(trace_id=""),
        user_id=42,
        execute_turn=lambda context: (
            seen_trace_ids.append(context["trace_id"]),
            {"answer_text": "Patent answer"},
        )[-1],
    )

    assert result["trace_id"] == "generated-trace"
    assert seen_trace_ids == ["generated-trace"]
    assert authority.user_writes[0]["trace_id"] == "generated-trace"
    assert authority.snapshot_reads[0]["trace_id"] == "generated-trace"
    assert authority.assistant_accepts[0]["trace_id"] == "generated-trace"


def test_ephemeral_flow_skips_authority_calls():
    service, authority, _ = _build_service()

    result = service.run_turn(
        request=_make_request(conversation_id=None),
        user_id=None,
        execute_turn=lambda context: {"answer_text": "Patent answer"},
    )

    assert result["context"]["persistence_mode"] == "ephemeral"
    assert authority.calls == []


def test_durable_flow_fails_explicitly_when_disabled():
    service, _, _ = _build_service(durable_mode_enabled=False)

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.DURABLE_MODE_DISABLED


def test_durable_flow_fails_explicitly_when_redis_prerequisites_missing():
    authority = _FakeAuthorityClient(snapshot_payload={})
    keys = PatentKeyFactory(env="test")
    service = ChatPersistenceService(
        authority_client=authority,
        execution_lock_manager=ExecutionLockManager(None, key_factory=keys),
        execution_cache=ExecutionCache(None, keys),
        durable_mode_enabled=True,
    )

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY



def test_split_phase_durable_flow_accepts_after_execution():
    authority = _FakeAuthorityClient(snapshot_payload={})
    service, authority, _ = _build_service(authority_client=authority)

    prepared = service.prepare_turn(request=_make_request(), user_id=42)

    assert authority.calls == ["user_write", "snapshot"]
    assert prepared["assistant_accept_required"] is True

    finalized = service.finalize_turn(
        prepared,
        request=_make_request(),
        execution_result={"answer_text": "Patent answer", "timings": {"total_ms": 12}},
    )

    assert authority.calls == ["user_write", "snapshot", "assistant_accept"]
    assert finalized["assistant_accept"]["accepted"] is True


def test_abort_turn_releases_inflight_and_lock_for_split_phase_streaming():
    service, _, redis = _build_service()

    prepared = service.prepare_turn(request=_make_request(), user_id=42)
    service.abort_turn(prepared)

    assert redis.get("patent:test:coord:inflight:123:req_123") is None
    assert redis.get("patent:test:exec:conversation-lock:123") is None



def test_new_trace_is_blocked_while_previous_failed_turn_is_pending():
    service, authority, _ = _build_service()

    with pytest.raises(RuntimeError):
        service.run_turn(
            request=_make_request(trace_id="req_123"),
            user_id=42,
            execute_turn=lambda context: (_ for _ in ()).throw(RuntimeError("executor boom")),
        )

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(trace_id="req_456"),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.PATENT_BUSY
    assert [item["trace_id"] for item in authority.user_writes] == ["req_123"]
    assert authority.assistant_accepts == []



def test_negative_assistant_accept_does_not_cache_success_result():
    authority = _FakeAuthorityClient(snapshot_payload={}, assistant_accepted=False)
    service, authority, _ = _build_service(authority_client=authority)

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer", "timings": {"total_ms": 12}},
        )

    assert exc_info.value.code == codes.AUTHORITY_UNAVAILABLE
    assert authority.assistant_accepts[0]["trace_id"] == "req_123"
    assert service.execution_cache.get_turn_result(conversation_id=123, trace_id="req_123") is None



def test_runtime_guards_are_renewed_during_long_running_turn():
    service, _, redis = _build_service(lock_ttl_seconds=1, inflight_ttl_seconds=1)

    result = service.run_turn(
        request=_make_request(),
        user_id=42,
        execute_turn=lambda context: (time.sleep(0.35), {"answer_text": "Patent answer"})[-1],
    )

    assert result["assistant_accept"]["accepted"] is True
    assert len(redis.compare_expire_calls) >= 2



def test_runtime_guards_are_renewed_before_execution_starts():
    authority = _FakeAuthorityClient(snapshot_payload={})
    original_snapshot = authority.read_context_snapshot

    def delayed_snapshot(**kwargs):
        time.sleep(0.35)
        return original_snapshot(**kwargs)

    authority.read_context_snapshot = delayed_snapshot
    service, _, redis = _build_service(
        authority_client=authority,
        lock_ttl_seconds=1,
        inflight_ttl_seconds=1,
    )

    result = service.run_turn(
        request=_make_request(),
        user_id=42,
        execute_turn=lambda context: {"answer_text": "Patent answer"},
    )

    assert result["assistant_accept"]["accepted"] is True
    assert len(redis.compare_expire_calls) >= 2


def test_runtime_guard_failure_during_assistant_accept_blocks_success():
    redis = _FakeRedis()

    class _SlowAcceptAuthority(_FakeAuthorityClient):
        def accept_assistant_turn_async(self, **kwargs):
            redis.reject_compare_expire = True
            time.sleep(0.35)
            return super().accept_assistant_turn_async(**kwargs)

    authority = _SlowAcceptAuthority(snapshot_payload={})
    service, _, _ = _build_service(
        authority_client=authority,
        redis=redis,
        lock_ttl_seconds=1,
        inflight_ttl_seconds=1,
    )

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY
    assert service.execution_cache.get_turn_result(conversation_id=123, trace_id="req_123") is None



def test_prepare_phase_guard_failure_blocks_execution():
    redis = _FakeRedis()
    authority = _FakeAuthorityClient(snapshot_payload={})
    original_snapshot = authority.read_context_snapshot
    execute_calls = []

    def delayed_snapshot(**kwargs):
        redis.reject_compare_expire = True
        time.sleep(0.35)
        return original_snapshot(**kwargs)

    authority.read_context_snapshot = delayed_snapshot
    service, _, _ = _build_service(
        authority_client=authority,
        redis=redis,
        lock_ttl_seconds=1,
        inflight_ttl_seconds=1,
    )

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: execute_calls.append(context) or {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY
    assert execute_calls == []


def test_runtime_guard_failure_after_accept_before_cache_blocks_success():
    redis = _FakeRedis()
    service, _, _ = _build_service(
        redis=redis,
        lock_ttl_seconds=1,
        inflight_ttl_seconds=1,
    )
    original_set_turn_result = service.execution_cache.set_turn_result

    def delayed_set_turn_result(**kwargs):
        redis.reject_compare_expire = True
        time.sleep(0.35)
        return original_set_turn_result(**kwargs)

    service.execution_cache.set_turn_result = delayed_set_turn_result

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY


def test_turn_result_commit_failure_fails_closed_and_blocks_same_trace_replay():
    service, authority, _ = _build_service()
    execute_calls = []
    service.execution_cache.set_turn_result = lambda **kwargs: False

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: execute_calls.append("first") or {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY
    assert execute_calls == ["first"]
    assert len(authority.assistant_accepts) == 1

    with pytest.raises(APIError) as replay_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: execute_calls.append("replay") or {"answer_text": "Patent answer"},
        )

    assert replay_info.value.code == codes.SERVICE_NOT_READY
    assert execute_calls == ["first"]


def test_overlay_commit_failure_keeps_pending_turn_blocked_until_overlay_visibility_recovers():
    service, authority, _ = _build_service()
    original_set_overlay_assistant = service.execution_cache.set_overlay_assistant
    service.execution_cache.set_overlay_assistant = lambda **kwargs: False

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY
    assert len(authority.assistant_accepts) == 1
    assert service.execution_cache.get_turn_result(conversation_id=123, trace_id="req_123") is not None
    assert service.execution_cache.get_pending_turn(conversation_id=123) == "req_123"

    service.execution_cache.set_overlay_assistant = original_set_overlay_assistant

    with pytest.raises(APIError) as replay_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert replay_info.value.code == codes.SERVICE_NOT_READY
    assert service.execution_cache.get_pending_turn(conversation_id=123) == "req_123"

    service.execution_cache.set_overlay_assistant(
        user_id=42,
        conversation_id=123,
        payload={"trace_id": "req_123", "route": "kb_qa", "assistant_content": "Patent answer"},
        ttl_seconds=60,
    )

    replayed = service.run_turn(
        request=_make_request(),
        user_id=42,
        execute_turn=lambda context: {"answer_text": "Patent answer"},
    )

    assert replayed["assistant_accept_skipped"] is True
    assert replayed["execution_result"]["answer_text"] == "Patent answer"
    assert service.execution_cache.get_pending_turn(conversation_id=123) == ""


def test_durable_flow_fails_closed_when_atomic_compare_delete_is_unavailable():
    class _NoCompareDeleteRedis(_FakeRedis):
        def __getattribute__(self, name):
            if name == "compare_delete":
                raise AttributeError(name)
            return super().__getattribute__(name)

    redis = _NoCompareDeleteRedis()
    service, _, _ = _build_service(redis=redis)

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY



def test_same_trace_retry_replays_user_write_when_pending_marker_was_claimed_only():
    service, authority, _ = _build_service()
    service.execution_cache.claim_pending_turn(
        conversation_id=123,
        trace_id="req_123",
        ttl_seconds=300,
    )

    result = service.run_turn(
        request=_make_request(trace_id="req_123"),
        user_id=42,
        execute_turn=lambda context: {"answer_text": "Patent answer"},
    )

    assert result["assistant_accept"]["accepted"] is True
    assert [item["trace_id"] for item in authority.user_writes] == ["req_123"]



def test_pending_marker_is_preserved_when_user_write_succeeds_but_marker_advance_fails():
    service, authority, _ = _build_service()
    original_mark = service.execution_cache.mark_pending_turn_user_written
    service.execution_cache.mark_pending_turn_user_written = lambda **kwargs: False

    with pytest.raises(APIError) as exc_info:
        service.run_turn(
            request=_make_request(trace_id="req_123"),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Patent answer"},
        )

    assert exc_info.value.code == codes.SERVICE_NOT_READY
    assert [item["trace_id"] for item in authority.user_writes] == ["req_123"]
    assert service.execution_cache.get_pending_turn(conversation_id=123) == "req_123"

    with pytest.raises(APIError) as blocked_info:
        service.run_turn(
            request=_make_request(trace_id="req_456"),
            user_id=42,
            execute_turn=lambda context: {"answer_text": "Another answer"},
        )

    assert blocked_info.value.code == codes.PATENT_BUSY
    service.execution_cache.mark_pending_turn_user_written = original_mark
