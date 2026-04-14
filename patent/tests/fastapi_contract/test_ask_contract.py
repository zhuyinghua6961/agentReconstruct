import asyncio
import concurrent.futures
import logging
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from pydantic import ValidationError

from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.pdf_service import PatentPdfService
from server.patent.tabular_service import PatentTabularService
from server.schemas.authority_models import (
    AuthorityAssistantAsyncRequest,
    AuthorityContextSnapshotQuery,
    AuthorityUserWriteRequest,
)
from server.schemas.request_models import ProtocolMismatchRequestError, parse_patent_request
from server.schemas.response_models import ContentEvent, DoneEvent, MetadataEvent, PatentSyncSuccess
from server.services.mode_profiles import get_patent_mode_profile



def _base_payload() -> dict:
    return {
        "question": "Explain the patent novelty.",
        "conversation_id": "123",
        "chat_history": [],
        "requested_mode": "patent",
        "actual_mode": "patent",
        "route": "kb_qa",
        "source_scope": None,
        "turn_mode": "kb_only",
        "kb_enabled": False,
        "allow_kb_verification": False,
        "used_files": [],
        "execution_files": [],
        "selected_file_ids": [],
        "primary_file_id": None,
        "file_selection": {},
        "trace_id": "req_123",
        "options": {},
    }


def _file_payload() -> dict:
    payload = _base_payload()
    payload.update(
        {
            "route": "hybrid_qa",
            "turn_mode": "mixed",
            "source_scope": "pdf+kb",
            "kb_enabled": True,
            "allow_kb_verification": True,
            "used_files": [{"file_id": 11, "file_type": "pdf"}],
            "execution_files": [{"file_id": 11, "file_type": "pdf"}],
            "selected_file_ids": [11],
            "primary_file_id": 11,
            "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf+kb"},
        }
    )
    return payload


def _pdf_payload() -> dict:
    payload = _base_payload()
    payload.update(
        {
            "conversation_id": None,
            "route": "pdf_qa",
            "turn_mode": "file_only",
            "source_scope": "pdf",
            "kb_enabled": False,
            "allow_kb_verification": False,
            "used_files": [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            "execution_files": [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
            "selected_file_ids": [11],
            "primary_file_id": 11,
            "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"},
        }
    )
    return payload


def _pdf_compare_payload() -> dict:
    payload = _pdf_payload()
    payload["question"] = "对比一下这两篇文献的内容"
    payload["used_files"] = [
        {"file_id": 11, "file_type": "pdf", "file_name": "paper-a.pdf"},
        {"file_id": 12, "file_type": "pdf", "file_name": "paper-b.pdf"},
    ]
    payload["execution_files"] = [
        {"file_id": 11, "file_type": "pdf", "file_name": "paper-a.pdf"},
        {"file_id": 12, "file_type": "pdf", "file_name": "paper-b.pdf"},
    ]
    payload["selected_file_ids"] = [11, 12]
    payload["primary_file_id"] = 11
    payload["file_selection"] = {"strategy": "explicit_selection", "selected_file_ids": [11, 12], "source_scope": "pdf"}
    return payload


def _build_valid_compare_answer(labels: list[str]) -> str:
    lines: list[str] = ["## 具体内容对比"]
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 核心内容（根据PDF原文）",
                f"- {label}：围绕方案 {index} 展开研究，并给出明确的中文结论。",
            ]
        )

    lines.append("")
    lines.append("## 研究方法差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 采用的研究方法",
                f"- {label}：采用表征测试与性能验证结合的方法，重点分析方案 {index}。",
            ]
        )

    lines.append("")
    lines.append("## 应用领域差异")
    for index, label in enumerate(labels, start=1):
        lines.extend(
            [
                "",
                f"### 文献 #{index} 关注的应用领域",
                f"- {label}：面向应用方向 {index} 的性能优化场景。",
            ]
        )

    lines.extend(
        [
            "",
            "## 相同点",
            "- 所有文献都提供了可比较的实验结论。",
            "",
            "## 总结",
            "- 这些文献展示了不同技术路线下的差异化优化方向。",
        ]
    )
    return "\n".join(lines)


def _tabular_payload() -> dict:
    payload = _base_payload()
    payload.update(
        {
            "conversation_id": None,
            "route": "tabular_qa",
            "turn_mode": "file_only",
            "source_scope": "table",
            "kb_enabled": False,
            "allow_kb_verification": False,
            "used_files": [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}],
            "execution_files": [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}],
            "selected_file_ids": [33],
            "primary_file_id": 33,
            "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [33], "source_scope": "table"},
        }
    )
    return payload


def _write_csv(path: Path) -> None:
    path.write_text(
        "material,capacity_mAh,note\n"
        "LMFP,120,stable\n"
        "LFP,115,safe\n"
        "NCM,140,higher energy\n",
        encoding="utf-8",
    )


def _hybrid_payload(source_scope: str = "pdf+table+kb") -> dict:
    payload = _base_payload()
    execution_files = [
        {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
        {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
    ]
    selected_file_ids = [11, 33]
    if source_scope == "pdf+kb":
        execution_files = [execution_files[0]]
        selected_file_ids = [11]
    elif source_scope == "table+kb":
        execution_files = [execution_files[1]]
        selected_file_ids = [33]
    payload.update(
        {
            "conversation_id": None,
            "route": "hybrid_qa",
            "turn_mode": "mixed" if "kb" in source_scope else "file_only",
            "source_scope": source_scope,
            "kb_enabled": "kb" in source_scope.split("+"),
            "allow_kb_verification": "kb" in source_scope.split("+"),
            "used_files": list(execution_files),
            "execution_files": list(execution_files),
            "selected_file_ids": list(selected_file_ids),
            "primary_file_id": selected_file_ids[0],
            "file_selection": {"strategy": "explicit_selection", "selected_file_ids": list(selected_file_ids), "source_scope": source_scope},
        }
    )
    return payload


def _sample_reference_object() -> dict:
    return {
        "source_type": "patent",
        "canonical_patent_id": "CN123456789A",
        "publication_number": "CN123456789A",
        "application_number": None,
        "country": "CN",
        "kind_code": "A",
        "title": "A patent title",
        "section_type": "claim",
        "section_label": "Claim 1",
        "anchor": {"claim_number": 1, "paragraph_id": None},
        "snippet": "A patent snippet",
        "provider": "patent_source_x",
        "original_available": True,
        "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html",
    }


def _sample_reference_link() -> dict:
    return {
        "type": "original_view",
        "label": "View claim 1",
        "canonical_patent_id": "CN123456789A",
        "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html",
        "redirect_url": None,
    }


def _sample_original_link() -> dict:
    return {
        "type": "original_view",
        "label": "View claim 1",
        "canonical_patent_id": "CN123456789A",
        "section": "claim",
        "claim_number": 1,
        "paragraph_id": None,
        "viewer_uri": "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html",
        "redirect_url": None,
    }



def test_patent_request_rejects_non_kb_only_payload():
    payload = _base_payload()
    payload["turn_mode"] = "file_only"

    with pytest.raises(ProtocolMismatchRequestError, match="turn_mode"):
        parse_patent_request(payload)


@pytest.mark.parametrize(
    ("route", "turn_mode", "source_scope", "execution_files", "selected_file_ids", "kb_enabled", "allow_kb_verification"),
    [
        ("pdf_qa", "file_only", "pdf", [{"file_id": 11, "file_type": "pdf"}], [11], False, False),
        ("tabular_qa", "file_only", "table", [{"file_id": 33, "file_type": "xlsx"}], [33], False, False),
        ("hybrid_qa", "mixed", "pdf+kb", [{"file_id": 11, "file_type": "pdf"}], [11], True, True),
        ("hybrid_qa", "file_only", "pdf+table", [{"file_id": 11, "file_type": "pdf"}, {"file_id": 33, "file_type": "xlsx"}], [11, 33], False, False),
    ],
)
def test_patent_request_accepts_file_aware_routes(
    route,
    turn_mode,
    source_scope,
    execution_files,
    selected_file_ids,
    kb_enabled,
    allow_kb_verification,
):
    payload = _base_payload()
    payload.update(
        {
            "route": route,
            "turn_mode": turn_mode,
            "source_scope": source_scope,
            "kb_enabled": kb_enabled,
            "allow_kb_verification": allow_kb_verification,
            "used_files": list(execution_files),
            "execution_files": list(execution_files),
            "selected_file_ids": list(selected_file_ids),
            "primary_file_id": selected_file_ids[0],
            "file_selection": {
                "strategy": "explicit_selection",
                "selected_file_ids": list(selected_file_ids),
                "source_scope": source_scope,
            },
        }
    )

    request = parse_patent_request(payload)

    assert request.route == route
    assert request.turn_mode == turn_mode
    assert request.source_scope == source_scope
    assert request.execution_files == execution_files
    assert request.selected_file_ids == selected_file_ids
    assert request.primary_file_id == selected_file_ids[0]


@pytest.mark.parametrize(
    ("route", "turn_mode", "source_scope", "execution_files", "selected_file_ids", "message"),
    [
        ("pdf_qa", "file_only", "table", [{"file_id": 11, "file_type": "pdf"}], [11], "source_scope"),
        ("hybrid_qa", "mixed", "kb", [{"file_id": 11, "file_type": "pdf"}], [11], "source_scope"),
        ("pdf_qa", "file_only", "pdf", [], [], "execution_files"),
        ("hybrid_qa", "mixed", "pdf+kb", [{"file_id": 11, "file_type": "pdf"}], [11], "allow_kb_verification"),
        ("pdf_qa", "file_only", "pdf", [{"file_id": 11, "file_type": "pdf"}, {"file_id": 33, "file_type": "xlsx"}], [11, 33], "selected_file_ids"),
    ],
)
def test_patent_request_rejects_invalid_file_route_combinations(
    route,
    turn_mode,
    source_scope,
    execution_files,
    selected_file_ids,
    message,
):
    payload = _base_payload()
    payload.update(
        {
            "route": route,
            "turn_mode": turn_mode,
            "source_scope": source_scope,
            "kb_enabled": "kb" in source_scope.split("+"),
            "allow_kb_verification": False,
            "used_files": list(execution_files),
            "execution_files": list(execution_files),
            "selected_file_ids": list(selected_file_ids),
            "primary_file_id": selected_file_ids[0] if selected_file_ids else None,
            "file_selection": {"strategy": "explicit_selection"},
        }
    )

    with pytest.raises((ProtocolMismatchRequestError, ValueError), match=message):
        parse_patent_request(payload)





def test_patent_request_requires_exact_protocol_literals():
    payload = _base_payload()
    payload["requested_mode"] = "PATENT"

    with pytest.raises(ProtocolMismatchRequestError, match="requested_mode"):
        parse_patent_request(payload)

    payload = _base_payload()
    payload["route"] = " kb_qa "

    with pytest.raises(ProtocolMismatchRequestError, match="route"):
        parse_patent_request(payload)

def test_patent_request_normalizes_conversation_id_and_mode_classification():
    durable_request = parse_patent_request(_base_payload())

    assert durable_request.conversation_id == 123
    assert durable_request.persistence_mode == "durable"
    assert durable_request.source_scope == "kb"

    ephemeral_payload = _base_payload()
    ephemeral_payload["conversation_id"] = None
    ephemeral_request = parse_patent_request(ephemeral_payload)

    assert ephemeral_request.conversation_id is None
    assert ephemeral_request.persistence_mode == "ephemeral"
    assert ephemeral_request.source_scope == "kb"


@pytest.mark.parametrize("conversation_id", ["opaque-id", "opaque-ephemeral", "", "0", "-1", False, True])
def test_patent_request_rejects_invalid_conversation_id_instead_of_downgrading_to_ephemeral(conversation_id):
    payload = _base_payload()
    payload["conversation_id"] = conversation_id

    with pytest.raises(ValueError, match="conversation_id"):
        parse_patent_request(payload)


@pytest.mark.parametrize("selected_file_ids", [[True], [1.2], ["1"], [1, False]])
def test_patent_request_rejects_non_integer_selected_file_ids(selected_file_ids):
    payload = _pdf_payload()
    payload["selected_file_ids"] = selected_file_ids
    payload["primary_file_id"] = 11

    with pytest.raises(ValueError, match="selected_file_ids"):
        parse_patent_request(payload)


@pytest.mark.parametrize("primary_file_id", [True, 1.2, "11"])
def test_patent_request_rejects_non_integer_primary_file_id(primary_file_id):
    payload = _pdf_payload()
    payload["primary_file_id"] = primary_file_id

    with pytest.raises(ValueError, match="primary_file_id"):
        parse_patent_request(payload)


def test_patent_request_requires_empty_file_selection_in_phase1():
    payload = _base_payload()
    payload["file_selection"] = {"selected": [1]}

    with pytest.raises(ProtocolMismatchRequestError, match="file_selection must be empty"):
        parse_patent_request(payload)





def test_patent_request_requires_string_question_and_trace_id():
    payload = _base_payload()
    payload["question"] = 123

    with pytest.raises(ValueError, match="question must be a string"):
        parse_patent_request(payload)

    payload = _base_payload()
    payload["trace_id"] = {"bad": "value"}

    with pytest.raises(ValueError, match="trace_id must be a string"):
        parse_patent_request(payload)



def test_schema_models_reject_extra_fields_and_enforce_object_item_shapes():
    with pytest.raises(ValidationError):
        PatentSyncSuccess(
            final_answer="Patent answer",
            query_mode="patent_kb_qa",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            source_scope="kb",
            timings={},
            metadata={},
            references=[],
            reference_objects=[],
            reference_links=[],
            original_links=[],
            used_files=[],
            file_selection={},
            trace_id="req_123",
            unexpected=True,
        )

    with pytest.raises(ValidationError):
        PatentSyncSuccess(
            final_answer="Patent answer",
            query_mode="patent_kb_qa",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            source_scope="kb",
            timings={},
            metadata={},
            references=[],
            reference_objects=["not-an-object"],
            reference_links=[],
            original_links=[],
            used_files=[],
            file_selection={},
            trace_id="req_123",
        )

    with pytest.raises(ValidationError):
        AuthorityAssistantAsyncRequest(
            conversation_id=123,
            user_id=42,
            trace_id="req_123",
            idempotency_key="123:req_123:assistant",
            final_event={
                "done_seen": True,
                "answer_text": "Patent answer",
                "references": ["not-an-object"],
                "used_files": ["not-an-object"],
                "timings": {},
            },
        )

def test_sync_success_shape_matches_patent_contract():
    response = PatentSyncSuccess(
        final_answer="Patent answer",
        query_mode="patent_kb_qa",
        route="kb_qa",
        requested_mode="patent",
        actual_mode="patent",
        source_scope="kb",
        timings={},
        metadata={},
        references=["CN123456789A"],
        reference_objects=[_sample_reference_object()],
        reference_links=[_sample_reference_link()],
        original_links=[_sample_original_link()],
        used_files=[],
        file_selection={},
        trace_id="req_123",
    )

    payload = response.model_dump()
    assert payload["success"] is True
    assert payload["requested_mode"] == "patent"
    assert payload["actual_mode"] == "patent"
    assert payload["route"] == "kb_qa"
    assert payload["source_scope"] == "kb"
    assert payload["references"] == ["CN123456789A"]
    assert payload["reference_objects"][0]["canonical_patent_id"] == "CN123456789A"
    assert payload["original_links"][0]["section"] == "claim"
    assert payload["trace_id"] == "req_123"


def test_file_aware_sync_and_done_models_accept_non_kb_route_metadata():
    response = PatentSyncSuccess(
        final_answer="Patent file answer",
        query_mode="patent_hybrid_qa",
        route="hybrid_qa",
        requested_mode="patent",
        actual_mode="patent",
        source_scope="pdf+kb",
        timings={},
        metadata={},
        references=["CN123456789A"],
        reference_objects=[_sample_reference_object()],
        reference_links=[_sample_reference_link()],
        original_links=[_sample_original_link()],
        used_files=[{"file_id": 11, "file_type": "pdf"}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11]},
        trace_id="req_file_123",
    )
    done = DoneEvent(
        final_answer="Patent file answer",
        query_mode="patent_hybrid_qa",
        route="hybrid_qa",
        requested_mode="patent",
        actual_mode="patent",
        source_scope="pdf+kb",
        timings={},
        references=["CN123456789A"],
        reference_objects=[_sample_reference_object()],
        reference_links=[_sample_reference_link()],
        original_links=[_sample_original_link()],
        metadata={},
        trace_id="req_file_123",
        used_files=[{"file_id": 11, "file_type": "pdf"}],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11]},
        seq=3,
        ts="2026-03-25T12:00:00Z",
    )

    assert response.model_dump()["route"] == "hybrid_qa"
    assert response.model_dump()["source_scope"] == "pdf+kb"
    assert done.model_dump()["used_files"][0]["file_id"] == 11



def test_stream_events_require_seq_and_ts():
    with pytest.raises(ValidationError):
        MetadataEvent(
            requested_mode="patent",
            actual_mode="patent",
            route="kb_qa",
            query_mode="patent_kb_qa",
            source_scope="kb",
            metadata={},
            trace_id="req_123",
        )

    with pytest.raises(ValidationError):
        DoneEvent(
            final_answer="done",
            query_mode="patent_kb_qa",
            route="kb_qa",
            requested_mode="patent",
            actual_mode="patent",
            source_scope="kb",
            timings={},
            references=[],
            reference_objects=[],
            reference_links=[],
            original_links=[],
            metadata={},
            trace_id="req_123",
            used_files=[],
            file_selection={},
            seq=1,
        )

    event = ContentEvent(content="chunk", seq=2, ts="2026-03-25T12:00:00Z")
    assert event.seq == 2
    assert event.ts == "2026-03-25T12:00:00Z"


def test_content_event_supports_structured_patent_stream_fields():
    event = ContentEvent(
        content="chunk",
        content_role="final",
        content_source="pdf",
        content_stream_id="final:answer",
        replace_stream=True,
        seq=2,
        ts="2026-03-25T12:00:00Z",
    )

    assert event.content_role == "final"
    assert event.content_source == "pdf"
    assert event.content_stream_id == "final:answer"
    assert event.content_phase == "snapshot"
    assert event.replace_stream is True


def test_content_event_rejects_invalid_preview_shapes():
    with pytest.raises(ValidationError):
        ContentEvent(
            content="chunk",
            content_role="preview",
            content_source="pdf",
            content_phase="start",
            seq=2,
            ts="2026-03-25T12:00:00Z",
        )

    with pytest.raises(ValidationError):
        ContentEvent(
            content="chunk",
            content_role="preview",
            content_source="pdf",
            content_stream_id="pdf:primary",
            seq=2,
            ts="2026-03-25T12:00:00Z",
        )


def test_done_event_shape_matches_patent_contract():
    event = DoneEvent(
        final_answer="Patent answer",
        query_mode="patent_kb_qa",
        route="kb_qa",
        requested_mode="patent",
        actual_mode="patent",
        source_scope="kb",
        timings={},
        references=["CN123456789A"],
        reference_objects=[_sample_reference_object()],
        reference_links=[_sample_reference_link()],
        original_links=[_sample_original_link()],
        metadata={},
        trace_id="req_123",
        used_files=[],
        file_selection={},
        seq=3,
        ts="2026-03-25T12:00:00Z",
    )

    payload = event.model_dump()
    assert payload["references"] == ["CN123456789A"]
    assert payload["reference_objects"][0]["canonical_patent_id"] == "CN123456789A"
    assert payload["reference_links"][0]["type"] == "original_view"
    assert payload["original_links"][0]["section"] == "claim"
    assert payload["metadata"] == {}



def test_authority_models_match_patent_contract():
    user_write = AuthorityUserWriteRequest(
        conversation_id=123,
        user_id=42,
        trace_id="req_123",
        route="hybrid_qa",
        source_scope="pdf+kb",
        idempotency_key="123:req_123:user",
        message={"role": "user", "content": "Explain the patent novelty."},
        context_hints={
            "selected_file_ids": [11],
            "last_turn_route_hint": "hybrid_qa",
            "mode_origin_requested_mode": "patent",
            "mode_origin_execution_backend": "patentQA",
            "compatibility_route": False,
        },
    )
    snapshot_query = AuthorityContextSnapshotQuery(
        user_id=42,
        trace_id="req_123",
        route="hybrid_qa",
        source_scope="pdf+kb",
    )
    assistant_async = AuthorityAssistantAsyncRequest(
        conversation_id=123,
        user_id=42,
        trace_id="req_123",
        route="hybrid_qa",
        source_scope="pdf+kb",
        idempotency_key="123:req_123:assistant",
        final_event={
            "done_seen": True,
            "answer_text": "Patent answer",
            "steps": [],
            "metadata": {
                "mode_origin": {
                    "requested_mode": "patent",
                    "execution_backend": "patentQA",
                    "compatibility_route": False,
                }
            },
            "references": [{"source_type": "patent", "canonical_patent_id": "CN123456789A"}],
            "reference_objects": [_sample_reference_object()],
            "reference_links": [_sample_reference_link()],
            "original_links": [_sample_original_link()],
            "used_files": [{"file_id": 11, "file_type": "pdf"}],
            "timings": {},
        },
    )

    assert user_write.source_service == "patentQA"
    assert user_write.route == "hybrid_qa"
    assert user_write.source_scope == "pdf+kb"
    assert user_write.context_hints.mode_origin_execution_backend == "patentQA"
    assert snapshot_query.actual_mode == "patent"
    assert snapshot_query.source_scope == "pdf+kb"
    assert assistant_async.final_event.done_seen is True
    assert assistant_async.route == "hybrid_qa"
    assert assistant_async.source_scope == "pdf+kb"
    assert assistant_async.final_event.metadata["mode_origin"]["compatibility_route"] is False
    assert assistant_async.final_event.original_links[0]["section"] == "claim"

from dataclasses import replace

from server.errors import codes
from server.errors.core import APIError
from server.patent.executor import PatentExecutor
from server.patent.models import PatentRetrievalPlan
from server.patent.retrieval_models import PatentCatalogRecord, PatentClaim, PatentDescriptionSnippet
from server.patent.retrieval_service import PatentRetrievalService
from server.patent.stages.synthesis import run_stage4_synthesis_with_patent_evidence
from server.schemas.response_models import ErrorEvent
from server.services.ask_service import AskService
from server.runtime.request_context import clear_trace_id, set_trace_id
from server_fastapi.app import create_app
from server_fastapi.routers.ask import _build_streaming_response


class _FakePersistenceService:
    def __init__(self, *, fail_accept: bool = False, context: dict | None = None):
        self.fail_accept = fail_accept
        self.context = dict(context or {})
        self.calls = []

    def prepare_turn(self, *, request, user_id):
        context = {
            "persistence_mode": request.persistence_mode,
            "conversation_id": request.conversation_id,
            "trace_id": request.trace_id,
            "chat_history": list(request.chat_history),
            "summary": {},
            "conversation_state": {},
            "pending_overlay": None,
            "snapshot": None,
        }
        context.update(self.context)
        self.calls.append({"op": "prepare", "trace_id": request.trace_id, "user_id": user_id})
        return {
            "trace_id": request.trace_id,
            "context": context,
            "assistant_accept": None,
            "assistant_accept_required": request.is_durable,
            "assistant_accept_skipped": False,
        }

    def finalize_turn(self, prepared_turn, *, request, execution_result):
        self.calls.append({"op": "finalize", "trace_id": prepared_turn["trace_id"], "user_id": None})
        if self.fail_accept:
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message="assistant accept failed",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            )
        return {
            "trace_id": prepared_turn["trace_id"],
            "context": prepared_turn["context"],
            "execution_result": dict(execution_result or {}),
            "assistant_accept": {"accepted": True},
            "assistant_accept_required": request.is_durable,
            "assistant_accept_skipped": False,
        }

    def abort_turn(self, prepared_turn):
        self.calls.append({"op": "abort", "trace_id": prepared_turn.get("trace_id"), "user_id": None})


class _StageRuntime:
    def __init__(self) -> None:
        self.stage1_contexts: list[dict[str, object] | None] = []

    def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
        self.stage1_contexts.append(conversation_context)
        return {
            "deep_answer": f"draft:{user_question}",
            "retrieval_claims": [
                PatentRetrievalClaim(
                    claim="compare replacement risk",
                    keywords=["battery safety"],
                    preferred_sections=["claims", "description"],
                    filters={},
                )
            ],
            "retrieval_plan": PatentRetrievalPlan(
                question_type="comparison",
                candidate_recall_queries=["battery safety"],
            ),
        }

    def stage2_targeted_retrieval(self, retrieval_plan, *, user_question: str, should_cancel=None, active_stream_count=None) -> dict[str, object]:
        return {
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "reference_links": [],
            "original_links": [],
            "metadata": {"retrieval_backend": "vector_hybrid"},
        }

    def _extract_patent_ids_from_results(self, retrieval_results: dict[str, object]) -> list[str]:
        return list(retrieval_results.get("references") or [])

    def stage25_patent_evidence_expansion(self, *, retrieval_results: dict[str, object], user_question: str, source_ids: list[str]) -> dict[str, object]:
        return {
            "skipped": True,
            "skip_reason": "patent_mode_no_md_expansion",
            "retrieval_results": retrieval_results,
        }

    def stage3_load_patent_evidence(self, *, retrieval_results: dict[str, object], source_ids: list[str], should_cancel=None) -> dict[str, object]:
        return {
            "source_ids": list(source_ids),
            "evidence_by_patent_id": {
                "CN115132975B": [
                    {
                        "kind": "patent_metadata",
                        "title": "一种锂离子电池及动力车辆",
                        "abstract_text": "通过 LMFP/LFP/三元复配改善充电安全与低 SOC 放电功率。",
                        "publication_number": "CN115132975B",
                    },
                    {
                        "kind": "matched_snippet",
                        "section_type": "claim",
                        "section_label": "Claim 1",
                        "text": "一种锂离子电池，其正极活性材料包括 LMFP、LFP 与三元材料。",
                        "anchor": {"claim_number": 1, "paragraph_id": None},
                        "scores": {"chunk_score": 0.91},
                    },
                ]
            },
        }

    def stage4_synthesis_with_patent_evidence(
        self,
        *,
        user_question: str,
        deep_answer: str,
        patent_evidence_bundle: dict[str, object],
        retrieval_results: dict[str, object] | None = None,
        should_cancel=None,
        conversation_context=None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "final_answer": "staged ask answer",
            "references": ["CN115132975B"],
            "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
            "reference_links": [],
            "original_links": [],
            "metadata": {"retrieval_backend": "vector_hybrid"},
        }


class _Stage1OnlyRuntime:
    def __init__(self) -> None:
        self.stage1_contexts: list[dict[str, object] | None] = []

    def stage1_pre_answer_and_planning(self, user_question: str, conversation_context=None) -> dict[str, object]:
        self.stage1_contexts.append(conversation_context)
        return {
            "deep_answer": f"stage1 only:{user_question}",
            "retrieval_claims": [],
            "retrieval_plan": PatentRetrievalPlan(),
            "fallback": "json_parse_failed",
        }

    def stage2_targeted_retrieval(self, retrieval_plan, *, user_question: str, should_cancel=None, active_stream_count=None) -> dict[str, object]:
        raise AssertionError("stage2 should not run when stage1 produced no retrieval claims")

    def _extract_patent_ids_from_results(self, retrieval_results: dict[str, object]) -> list[str]:
        raise AssertionError("source extraction should not run when stage1 produced no retrieval claims")

    def stage25_patent_evidence_expansion(self, *, retrieval_results: dict[str, object], user_question: str, source_ids: list[str]) -> dict[str, object]:
        raise AssertionError("stage25 should not run when stage1 produced no retrieval claims")

    def stage3_load_patent_evidence(self, *, retrieval_results: dict[str, object], source_ids: list[str], should_cancel=None) -> dict[str, object]:
        raise AssertionError("stage3 should not run when stage1 produced no retrieval claims")

    def stage4_synthesis_with_patent_evidence(
        self,
        *,
        user_question: str,
        deep_answer: str,
        patent_evidence_bundle: dict[str, object],
        retrieval_results: dict[str, object] | None = None,
        should_cancel=None,
        conversation_context=None,
    ) -> dict[str, object]:
        raise AssertionError("stage4 should not run when stage1 produced no retrieval claims")



def _make_retrieval_service() -> PatentRetrievalService:
    return PatentRetrievalService(
        identity_registry={"CN123456789A": "CN123456789A"},
        catalog_records=[
            PatentCatalogRecord(
                canonical_patent_id="CN123456789A",
                publication_number="CN123456789A",
                application_number="CN202410001234X",
                title="Battery thermal management system for electric vehicles",
                abstract_text="A thermal control system for electric vehicle battery packs.",
                applicant_names=["Example Battery Co"],
                inventor_names=["Alice Inventor"],
                ipc_codes=["H01M10/613"],
                cpc_codes=["H01M10/613"],
                claims=[PatentClaim(claim_number=1, text="A battery thermal management system configured for electric vehicles.")],
                description_snippets=[PatentDescriptionSnippet(paragraph_id="p-001", text="Battery temperature control.")],
                country="CN",
                kind_code="A",
                publication_date="2024-01-01",
                provider="patent_source_x",
                original_available=True,
            )
        ],
        retrieval_version="retrieval-v1",
        catalog_index_version="catalog-v1",
    )


def test_ask_service_sync_payload_matches_contract():
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_FakePersistenceService(
            context={
                "chat_history": [{"role": "assistant", "content": "Earlier turn", "trace_id": "req_prev"}],
                "summary": {"short_summary": "Earlier patent context"},
            }
        ),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    payload = service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    validated = PatentSyncSuccess.model_validate(payload)
    assert validated.final_answer == "Patent Phase 1 stub answer: Explain the patent novelty."
    assert validated.requested_mode == "patent"
    assert validated.route == "kb_qa"
    assert validated.query_mode == "patent_kb_qa"
    assert validated.metadata["steps"][0]["step"] == "context_ready"
    assert "最近 1 条消息" in validated.metadata["steps"][0]["message"]
    assert validated.trace_id == "req_123"



def test_stream_done_is_emitted_only_after_accept_success():
    request = parse_patent_request(_base_payload())
    success_service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_FakePersistenceService(
            context={
                "chat_history": [{"role": "assistant", "content": "Earlier turn", "trace_id": "req_prev"}],
                "summary": {"short_summary": "Earlier patent context"},
            }
        ),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )
    success_events = list(success_service.stream_ask(request, user_id=42))

    assert success_events[0]["type"] == "metadata"
    assert success_events[0]["query_mode"] == "patent_kb_qa"
    assert isinstance(success_events[0]["metadata"]["telemetry"]["backend_stream_opened_at_ms"], int)
    assert success_events[1]["type"] == "step"
    assert success_events[1]["step"] == "context_ready"
    assert "最近 1 条消息" in success_events[1]["message"]
    assert success_events[-1]["type"] == "done"
    assert success_events[-1]["query_mode"] == "patent_kb_qa"
    assert success_events[-1]["metadata"]["steps"][0]["step"] == "context_ready"
    assert all(event["seq"] == index for index, event in enumerate(success_events))

    failure_service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_FakePersistenceService(fail_accept=True),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )
    failure_events = list(failure_service.stream_ask(replace(request, trace_id="req_456"), user_id=42))

    assert failure_events[0]["type"] == "metadata"
    assert failure_events[-1]["type"] == "error"
    ErrorEvent.model_validate(failure_events[-1])
    assert all(event["type"] != "done" for event in failure_events)


def test_stream_ask_with_staged_runtime_preserves_shell_contract_and_normalizes_context():
    runtime = _StageRuntime()
    service = AskService(
        patent_executor=PatentExecutor(runtime=runtime),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events[0]["type"] == "metadata"
    assert events[1]["type"] == "step"
    assert events[-1]["type"] == "done"
    assert events[-1]["final_answer"] == "staged ask answer"
    assert events[-1]["references"] == ["CN115132975B"]
    assert runtime.stage1_contexts == [
        {
            "recent_turns_for_llm": [],
            "summary_for_llm": {},
            "conversation_state": {},
            "source_selection": {"source_scope": "kb", "selected_file_ids": []},
        }
    ]


def test_stream_ask_emits_stage_progress_before_later_stage_completion():
    stage2_entered = threading.Event()
    allow_stage2_finish = threading.Event()

    class _SlowStageRuntime(_StageRuntime):
        def stage2_targeted_retrieval(self, retrieval_plan, *, user_question: str, should_cancel=None, active_stream_count=None) -> dict[str, object]:
            stage2_entered.set()
            assert allow_stage2_finish.wait(timeout=2), "stage2 finish gate was not released"
            return super().stage2_targeted_retrieval(
                retrieval_plan,
                user_question=user_question,
                should_cancel=should_cancel,
                active_stream_count=active_stream_count,
            )

    service = AskService(
        patent_executor=PatentExecutor(runtime=_SlowStageRuntime()),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    stream = service.stream_ask(parse_patent_request(_base_payload()), user_id=42)
    assert next(stream)["type"] == "metadata"
    assert next(stream)["step"] == "context_ready"

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(next, stream)
        progress_event = future.result(timeout=0.5)
        assert progress_event["type"] == "step"
        assert progress_event["step"] == "stage1"
        assert progress_event["status"] == "processing"
        allow_stage2_finish.set()

    remaining_events = list(stream)
    assert stage2_entered.is_set() is True
    assert any(event["type"] == "step" and event.get("step") == "stage4" for event in remaining_events)
    assert remaining_events[-1]["type"] == "done"


def test_gateway_owned_stream_ask_emits_stage1_before_slow_prepare_turn_finishes():
    prepare_started = threading.Event()
    allow_prepare_finish = threading.Event()

    class _SlowGatewayPersistence(_FakePersistenceService):
        def prepare_turn(self, *, request, user_id):
            prepare_started.set()
            assert allow_prepare_finish.wait(timeout=2), "prepare_turn finish gate was not released"
            return super().prepare_turn(request=request, user_id=user_id)

    request_payload = _base_payload()
    request_payload["options"] = {
        "gateway_task_execution": True,
        "gateway_owned_persistence": True,
    }
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SlowGatewayPersistence(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    stream = service.stream_ask(parse_patent_request(request_payload), user_id=42)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        first_event = pool.submit(next, stream).result(timeout=0.5)
        assert first_event["type"] == "metadata"
        assert prepare_started.is_set() is True

        second_event = pool.submit(next, stream).result(timeout=0.5)
        assert second_event["type"] == "step"
        assert second_event["step"] == "stage1"
        assert second_event["status"] == "processing"

        allow_prepare_finish.set()

    remaining_events = list(stream)
    assert any(event["type"] == "step" and event.get("step") == "context_ready" for event in remaining_events)
    assert remaining_events[-1]["type"] == "done"


def test_gateway_owned_stream_ask_failure_still_emits_stage1_before_prepare_error():
    prepare_started = threading.Event()
    allow_prepare_finish = threading.Event()

    class _FailingGatewayPersistence(_FakePersistenceService):
        def prepare_turn(self, *, request, user_id):
            prepare_started.set()
            assert allow_prepare_finish.wait(timeout=2), "prepare_turn finish gate was not released"
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message="context snapshot failed",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            )

    request_payload = _base_payload()
    request_payload["options"] = {
        "gateway_task_execution": True,
        "gateway_owned_persistence": True,
    }
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_FailingGatewayPersistence(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    stream = service.stream_ask(parse_patent_request(request_payload), user_id=42)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        first_event = pool.submit(next, stream).result(timeout=0.5)
        assert first_event["type"] == "metadata"
        assert prepare_started.is_set() is True

        second_event = pool.submit(next, stream).result(timeout=0.5)
        assert second_event["type"] == "step"
        assert second_event["step"] == "stage1"
        assert second_event["status"] == "processing"

        allow_prepare_finish.set()

    remaining_events = list(stream)
    assert remaining_events[-1]["type"] == "error"


def test_stream_ask_emits_streaming_content_before_done_when_stage4_streams():
    class _StreamingStageRuntime(_StageRuntime):
        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle: dict[str, object],
            retrieval_results: dict[str, object] | None = None,
            should_cancel=None,
            content_callback=None,
            conversation_context=None,
        ) -> dict[str, object]:
            if callable(content_callback):
                content_callback("streamed ")
                content_callback("answer")
            return {
                "success": True,
                "final_answer": "streamed answer",
                "references": ["CN115132975B"],
                "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "vector_hybrid"},
            }

    service = AskService(
        patent_executor=PatentExecutor(runtime=_StreamingStageRuntime()),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    content_events = [event for event in events if event["type"] == "content"]
    assert [event["content"] for event in content_events] == ["streamed ", "answer"]
    assert events.index(content_events[0]) < len(events) - 1
    assert events[-1]["type"] == "done", events
    assert events[-1]["final_answer"] == "streamed answer"


def test_stream_ask_replayed_pdf_turn_with_capability_emits_typed_final_content():
    class _ReplayPersistence(_FakePersistenceService):
        def prepare_turn(self, *, request, user_id):
            prepared = super().prepare_turn(request=request, user_id=user_id)
            prepared["assistant_accept_skipped"] = True
            prepared["execution_result"] = {
                "answer_text": "replayed pdf answer",
                "route": request.route,
                "query_mode": "patent_pdf_qa",
                "source_scope": request.source_scope,
                "timings": {"pdf_ms": 3},
                "metadata": {"answer_mode": "pdf_text_summary"},
                "used_files": list(request.used_files),
                "file_selection": dict(request.file_selection),
            }
            return prepared

    request_payload = _pdf_payload()
    request_payload["options"] = {"patent_stream_capability": "preview_v1"}
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_ReplayPersistence(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(request_payload), user_id=42))

    content_events = [event for event in events if event["type"] == "content"]
    assert len(content_events) == 1
    assert content_events[0]["content"] == "replayed pdf answer"
    assert content_events[0]["content_role"] == "final"
    assert content_events[0]["content_source"] == "pdf"
    assert content_events[0]["content_phase"] == "snapshot"


def test_stream_ask_emits_first_chunk_latency_telemetry_in_metadata_and_done():
    class _StreamingStageRuntime(_StageRuntime):
        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle: dict[str, object],
            retrieval_results: dict[str, object] | None = None,
            should_cancel=None,
            content_callback=None,
            conversation_context=None,
        ) -> dict[str, object]:
            if callable(content_callback):
                content_callback("streamed ")
                content_callback("answer")
            return {
                "success": True,
                "final_answer": "streamed answer",
                "references": ["CN115132975B"],
                "reference_objects": [{"canonical_patent_id": "CN115132975B"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "vector_hybrid"},
            }

    service = AskService(
        patent_executor=PatentExecutor(runtime=_StreamingStageRuntime()),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    metadata_telemetry = events[0]["metadata"]["telemetry"]
    done_telemetry = events[-1]["metadata"]["telemetry"]

    assert isinstance(metadata_telemetry["backend_stream_opened_at_ms"], int)
    assert metadata_telemetry["backend_stream_opened_at_ms"] > 0
    assert isinstance(done_telemetry["backend_stream_opened_at_ms"], int)
    assert isinstance(done_telemetry["first_step_at_ms"], int)
    assert isinstance(done_telemetry["first_content_at_ms"], int)
    assert done_telemetry["backend_stream_opened_at_ms"] <= done_telemetry["first_step_at_ms"]
    assert done_telemetry["first_step_at_ms"] <= done_telemetry["first_content_at_ms"]


def test_stream_ask_strips_raw_patent_id_from_streaming_and_done_payload():
    class _ReadableCitationStageRuntime(_StageRuntime):
        class _StreamingBuilder:
            def __call__(self, **kwargs):
                raise AssertionError("stream path should be used")

            def stream(self, *, question, retrieval_outcome, context):
                del question, retrieval_outcome, context
                yield "结论来自专利 (patent_id=CN115132975B)。"
                yield "另有外部引用 (patent_id=CN000000000A)。"

        def stage4_synthesis_with_patent_evidence(
            self,
            *,
            user_question: str,
            deep_answer: str,
            patent_evidence_bundle: dict[str, object],
            retrieval_results: dict[str, object] | None = None,
            should_cancel=None,
            content_callback=None,
            conversation_context=None,
        ) -> dict[str, object]:
            del should_cancel
            return run_stage4_synthesis_with_patent_evidence(
                user_question=user_question,
                deep_answer=deep_answer,
                patent_evidence_bundle=patent_evidence_bundle,
                retrieval_results=retrieval_results,
                answer_builder=self._StreamingBuilder(),
                content_callback=content_callback,
                conversation_context=conversation_context,
            )

    service = AskService(
        patent_executor=PatentExecutor(runtime=_ReadableCitationStageRuntime()),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))
    content_text = "".join(event["content"] for event in events if event["type"] == "content")
    done_event = events[-1]

    assert "patent_id=" not in content_text
    assert "CN115132975B" in content_text
    assert done_event["type"] == "done"
    assert "patent_id=" not in done_event["final_answer"]
    assert "CN115132975B" in done_event["final_answer"]


def test_stream_ask_short_circuits_after_stage1_when_no_retrieval_claims_are_available():
    runtime = _Stage1OnlyRuntime()
    service = AskService(
        patent_executor=PatentExecutor(runtime=runtime),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert [event["type"] for event in events] == ["metadata", "step", "step", "content", "done"]
    assert events[1]["step"] == "context_ready"
    assert events[2]["step"] == "stage1"
    assert events[2]["status"] == "processing"
    assert events[2]["message"] == "阶段一：生成深度预回答与检索规划..."
    assert events[3]["content"] == "stage1 only:Explain the patent novelty."
    assert events[-1]["final_answer"] == "stage1 only:Explain the patent novelty."
    assert events[-1]["metadata"]["stage1_short_circuit"] is True


class _SplitPhasePersistenceService:
    def __init__(
        self,
        *,
        fail_accept: bool = False,
        resolved_trace_id: str | None = None,
        finalize_without_accept: bool = False,
    ):
        self.fail_accept = fail_accept
        self.resolved_trace_id = resolved_trace_id
        self.finalize_without_accept = finalize_without_accept
        self.calls = []
        self.aborted = []

    def prepare_turn(self, *, request, user_id):
        trace_id = self.resolved_trace_id or request.trace_id
        context = {
            "persistence_mode": request.persistence_mode,
            "conversation_id": request.conversation_id,
            "trace_id": trace_id,
            "chat_history": list(request.chat_history),
            "summary": {},
            "conversation_state": {},
            "pending_overlay": None,
            "snapshot": None,
        }
        self.calls.append({"op": "prepare", "trace_id": trace_id, "user_id": user_id})
        return {
            "trace_id": trace_id,
            "context": context,
            "assistant_accept_required": request.is_durable,
            "assistant_accept_skipped": False,
        }

    def finalize_turn(self, prepared_turn, *, request, execution_result):
        trace_id = prepared_turn["trace_id"]
        context = prepared_turn["context"]
        self.calls.append({"op": "finalize", "trace_id": trace_id, "user_id": None})
        if self.fail_accept:
            raise APIError(
                code=codes.AUTHORITY_UNAVAILABLE,
                message="assistant accept failed",
                status_code=503,
                error="authority_unavailable",
                retriable=True,
            )
        return {
            "trace_id": trace_id,
            "context": context,
            "execution_result": dict(execution_result or {}),
            "assistant_accept": None if self.finalize_without_accept else {"accepted": True},
            "assistant_accept_skipped": False,
            "assistant_accept_required": request.is_durable,
        }

    def abort_turn(self, prepared_turn):
        self.aborted.append(dict(prepared_turn or {}))


def test_stream_emits_progress_before_accept_failure_when_split_phase_is_available():
    request = parse_patent_request(_base_payload())
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(fail_accept=True),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(request, user_id=42))

    assert [event["type"] for event in events] == ["metadata", "step", "content", "error"]
    assert events[1]["step"] == "context_ready"
    assert events[2]["content"] == "Patent Phase 1 stub answer: Explain the patent novelty."
    assert all(event["type"] != "done" for event in events)


def test_stream_uses_resolved_trace_id_before_first_frame():
    request = parse_patent_request(_base_payload())
    request = replace(request, trace_id="")
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(resolved_trace_id="req_generated"),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(request, user_id=42))

    assert events[0]["trace_id"] == "req_generated"
    assert events[-1]["trace_id"] == "req_generated"


def test_stream_logs_task_correlation_id(caplog):
    request = parse_patent_request(_base_payload())
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with caplog.at_level(logging.INFO, logger="patent.ask_service"):
        events = list(service.stream_ask(request, user_id=42))

    assert events[0]["trace_id"] == "req_123"
    assert any("trace_id=req_123" in record.getMessage() for record in caplog.records)


def test_sync_logs_resolved_task_correlation_id(caplog):
    request = parse_patent_request(_base_payload())
    request = replace(request, trace_id="")
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(resolved_trace_id="req_generated"),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with caplog.at_level(logging.INFO, logger="patent.ask_service"):
        payload = service.sync_ask(request, user_id=42)

    assert payload["trace_id"] == "req_generated"
    assert any("sync_ask start trace_id=req_generated" in record.getMessage() for record in caplog.records)
    assert any("sync_ask complete trace_id=req_generated" in record.getMessage() for record in caplog.records)


def test_sync_run_turn_fallback_executes_with_resolved_trace_id():
    class _TraceCaptureExecutor:
        def __init__(self):
            self.seen_trace_ids = []

        def execute(self, *, request, context):
            self.seen_trace_ids.append(str(request.trace_id))
            return {
                "answer_text": "ok",
                "route": request.route,
                "source_scope": request.source_scope,
                "references": [],
                "reference_objects": [],
                "reference_links": [],
                "original_links": [],
                "used_files": [],
                "steps": [],
                "timings": {},
                "metadata": {"success": True},
            }

    class _RunTurnOnlyPersistenceService:
        def run_turn(self, *, request, user_id, execute_turn):
            resolved_trace_id = "req_generated"
            context = {"trace_id": resolved_trace_id}
            execution_result = execute_turn(context)
            return {
                "trace_id": resolved_trace_id,
                "context": context,
                "execution_result": execution_result,
                "assistant_accept": {"accepted": True},
                "assistant_accept_required": bool(request.is_durable),
                "assistant_accept_skipped": False,
            }

    request = parse_patent_request(_base_payload())
    request = replace(request, trace_id="")
    executor = _TraceCaptureExecutor()
    service = AskService(
        patent_executor=executor,
        persistence_service=_RunTurnOnlyPersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    payload = service.sync_ask(request, user_id=42)

    assert payload["trace_id"] == "req_generated"
    assert executor.seen_trace_ids == ["req_generated"]


def test_stream_refuses_done_when_assistant_accept_signal_is_missing():
    request = parse_patent_request(_base_payload())
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(finalize_without_accept=True),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(request, user_id=42))

    assert events[-1]["type"] == "error"
    assert all(event["type"] != "done" for event in events)


def test_sync_ask_rejects_failed_execution_payload():
    class _FailedExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "",
                "route": "kb_qa",
                "metadata": {"success": False, "failed_stage": "stage4"},
                "steps": [{"step": "stage4", "title": "阶段四", "message": "阶段四：答案生成失败", "status": "failed"}],
                "timings": {},
            }

    persistence = _SplitPhasePersistenceService()
    service = AskService(
        patent_executor=_FailedExecutor(),
        persistence_service=persistence,
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR
    assert [call["op"] for call in persistence.calls] == ["prepare"]
    assert len(persistence.aborted) == 1


def test_stream_rejects_failed_execution_payload_with_terminal_error():
    class _FailedExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "",
                "route": "kb_qa",
                "metadata": {"success": False, "failed_stage": "stage4"},
                "steps": [{"step": "stage4", "title": "阶段四", "message": "阶段四：答案生成失败", "status": "failed"}],
                "timings": {},
            }

    persistence = _SplitPhasePersistenceService()
    service = AskService(
        patent_executor=_FailedExecutor(),
        persistence_service=persistence,
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert [event["type"] for event in events] == ["metadata", "step", "error"]
    assert events[1]["step"] == "context_ready"
    assert events[-1]["error"] == "internal_error"
    assert [call["op"] for call in persistence.calls] == ["prepare"]
    assert len(persistence.aborted) == 1


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("references", "CN123456789A"),
        ("reference_objects", {"canonical_patent_id": "CN123456789A"}),
        ("reference_links", {"viewer_uri": "/api/patent/original/CN123456789A"}),
        ("original_links", {"viewer_uri": "/api/patent/original/CN123456789A"}),
        ("used_files", {"file_id": 1}),
    ],
)
def test_sync_ask_rejects_non_list_result_containers(field_name, field_value):
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                field_name: field_value,
                "timings": {},
            }

    service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("references", "CN123456789A"),
        ("reference_objects", {"canonical_patent_id": "CN123456789A"}),
        ("reference_links", {"viewer_uri": "/api/patent/original/CN123456789A"}),
        ("original_links", {"viewer_uri": "/api/patent/original/CN123456789A"}),
        ("used_files", {"file_id": 1}),
    ],
)
def test_stream_rejects_non_list_result_containers(field_name, field_value):
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                field_name: field_value,
                "timings": {},
            }

    service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events[0]["type"] == "metadata"
    assert events[-1]["type"] == "error"
    assert all(event["type"] != "done" for event in events)


def test_sync_ask_normalizes_legacy_query_mode_to_patent_kb_qa():
    class _LegacyExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "query_mode": "patent",
                "timings": {},
            }

    service = AskService(
        patent_executor=_LegacyExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    payload = service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    assert payload["query_mode"] == "patent_kb_qa"


def test_stream_done_normalizes_legacy_query_mode_to_patent_kb_qa():
    class _LegacyExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "query_mode": "patent",
                "timings": {},
            }

    service = AskService(
        patent_executor=_LegacyExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events[0]["type"] == "metadata"
    assert events[0]["query_mode"] == "patent_kb_qa"
    assert events[-1]["type"] == "done", events
    assert events[-1]["query_mode"] == "patent_kb_qa"


def test_sync_ask_normalizes_legacy_query_mode_to_route_specific_file_mode():
    class _LegacyFileExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent file answer",
                "route": "hybrid_qa",
                "source_scope": "pdf+kb",
                "query_mode": "patent",
                "used_files": [{"file_id": 11, "file_type": "pdf"}],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf+kb"},
                "timings": {},
            }

    service = AskService(
        patent_executor=_LegacyFileExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    payload = service.sync_ask(parse_patent_request(_file_payload()), user_id=42)

    assert payload["query_mode"] == "patent_hybrid_qa"
    assert payload["route"] == "hybrid_qa"
    assert payload["source_scope"] == "pdf+kb"


def test_stream_metadata_and_done_preserve_file_aware_route_metadata():
    class _FileExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent file answer",
                "route": "hybrid_qa",
                "source_scope": "pdf+kb",
                "query_mode": "patent",
                "used_files": [{"file_id": 11, "file_type": "pdf"}],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf+kb"},
                "timings": {},
            }

    service = AskService(
        patent_executor=_FileExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_file_payload()), user_id=42))

    assert events[0]["type"] == "metadata"
    assert events[0]["route"] == "hybrid_qa"
    assert events[0]["source_scope"] == "pdf+kb"
    assert events[0]["query_mode"] == "patent_hybrid_qa"
    assert events[-1]["type"] == "done"
    assert events[-1]["route"] == "hybrid_qa"
    assert events[-1]["source_scope"] == "pdf+kb"
    assert events[-1]["query_mode"] == "patent_hybrid_qa"


def test_sync_ask_file_kb_merge_keeps_shell_reference_contract():
    class _EvidencePdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None):
            return {
                "answer_text": "file contribution",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "references": ["FILE-REF-1"],
                "reference_objects": [{"source_type": "pdf", "file_id": 11}],
                "reference_links": [{"type": "pdf_view", "file_id": 11}],
                "original_links": [{"type": "pdf_original", "file_id": 11}],
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
                "timings": {},
            }

    class _EvidenceKbService:
        def run(self, *, request, runtime=None, conversation_context=None):
            return {
                "answer_text": "kb contribution",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": request.source_scope,
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [{"type": "original_view", "canonical_patent_id": "CN123456789A"}],
                "original_links": [{"type": "original_view", "canonical_patent_id": "CN123456789A"}],
                "timings": {},
            }

    service = AskService(
        patent_executor=PatentExecutor(
            kb_service=_EvidenceKbService(),
            pdf_service=_EvidencePdfService(),
        ),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    payload = service.sync_ask(parse_patent_request(_file_payload()), user_id=42)

    assert payload["references"] == ["CN123456789A"]
    assert payload["reference_objects"] == [{"canonical_patent_id": "CN123456789A"}]
    assert payload["reference_links"] == [
        {"type": "pdf_view", "file_id": 11},
        {"type": "original_view", "canonical_patent_id": "CN123456789A"},
    ]


def test_sync_ask_derives_references_from_reference_objects_when_missing():
    class _ReferenceObjectExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "reference_objects": [_sample_reference_object()],
                "timings": {},
            }

    service = AskService(
        patent_executor=_ReferenceObjectExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    payload = service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    assert payload["references"] == ["CN123456789A"]
    assert payload["reference_objects"][0]["canonical_patent_id"] == "CN123456789A"


def test_stream_done_derives_references_from_reference_objects_when_missing():
    class _ReferenceObjectExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "reference_objects": [_sample_reference_object()],
                "timings": {},
            }

    service = AskService(
        patent_executor=_ReferenceObjectExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events[-1]["type"] == "done"
    assert events[-1]["references"] == ["CN123456789A"]
    assert events[-1]["reference_objects"][0]["canonical_patent_id"] == "CN123456789A"


def test_sync_ask_rejects_mismatched_references_and_reference_objects():
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "references": ["US20240001234A1"],
                "reference_objects": [_sample_reference_object()],
                "timings": {},
            }

    service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR


def test_sync_ask_maps_result_builder_validation_errors_to_api_error():
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "query_mode": "patent",
                "references": ["bad-reference"],
                "timings": {},
            }

    service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    assert exc_info.value.code == codes.INTERNAL_ERROR


def test_stream_maps_result_builder_validation_errors_to_terminal_error():
    class _BrokenExecutor:
        def execute(self, *, request, context):
            return {
                "answer_text": "Patent answer",
                "route": "kb_qa",
                "query_mode": "patent",
                "references": ["bad-reference"],
                "timings": {},
            }

    service = AskService(
        patent_executor=_BrokenExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events[0]["type"] == "metadata"
    assert events[-1]["type"] == "error"
    assert all(event["type"] != "done" for event in events)


def test_sync_ask_maps_timeout_to_retriable_504():
    class _TimeoutExecutor:
        def execute(self, *, request, context):
            raise TimeoutError("singleflight wait timed out for stage1")

    service = AskService(
        patent_executor=_TimeoutExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with pytest.raises(APIError) as exc_info:
        service.sync_ask(parse_patent_request(_base_payload()), user_id=42)

    assert exc_info.value.status_code == 504
    assert exc_info.value.error == "timeout"
    assert exc_info.value.retriable is True


def test_stream_maps_timeout_to_terminal_timeout_error():
    class _TimeoutExecutor:
        def execute(self, *, request, context):
            raise TimeoutError("singleflight wait timed out for stage1")

    service = AskService(
        patent_executor=_TimeoutExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events[-1]["type"] == "error"
    assert events[-1]["error"] == "timeout"
    assert events[-1]["code"] == codes.INTERNAL_ERROR
    assert events[-1]["message"] == "patent execution timed out"



def test_stream_maps_metadata_builder_failures_to_terminal_error():
    request = parse_patent_request(_base_payload())
    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_SplitPhasePersistenceService(),
        now_factory=lambda: (_ for _ in ()).throw(RuntimeError("clock boom")),
    )

    events = list(service.stream_ask(request, user_id=42))

    assert events == [
        {
            "type": "error",
            "code": codes.INTERNAL_ERROR,
            "error": "internal_error",
            "message": "internal server error",
            "trace_id": "req_123",
            "seq": 0,
            "ts": "1970-01-01T00:00:00Z",
        }
    ]



def test_stream_maps_prepare_time_failures_to_terminal_error():
    class _PrepareFailurePersistence:
        def prepare_turn(self, *, request, user_id):
            raise APIError(
                code=codes.PATENT_BUSY,
                message="durable patent turn is already in flight",
                status_code=409,
                error="patent_busy",
                retriable=True,
            )

        def abort_turn(self, prepared_turn):
            return None

    service = AskService(
        patent_executor=PatentExecutor(),
        persistence_service=_PrepareFailurePersistence(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    events = list(service.stream_ask(parse_patent_request(_base_payload()), user_id=42))

    assert events == [
        {
            "type": "error",
            "code": codes.PATENT_BUSY,
            "error": "patent_busy",
            "message": "durable patent turn is already in flight",
            "trace_id": "req_123",
            "seq": 0,
            "ts": "2026-03-26T00:00:00Z",
        }
    ]



TEST_JWT_SECRET = "patent-test-secret"


def _make_auth_token(user_id: int, *, secret: str = TEST_JWT_SECRET) -> str:
    serializer = URLSafeTimedSerializer(secret)
    return serializer.dumps({"user_id": user_id, "role": "user"}, salt="highthinking.auth.access")


def _stream_events(response) -> list[dict]:
    payloads = []
    for chunk in response.text.strip().split("\n\n"):
        item = chunk.strip()
        if not item:
            continue
        assert item.startswith("data: ")
        payloads.append(__import__("json").loads(item[6:]))
    return payloads



class _RouteFakeAskService:
    def __init__(self):
        self.sync_calls = []
        self.stream_calls = []

    def sync_ask(self, request, *, user_id):
        self.sync_calls.append(
            {
                "trace_id": request.trace_id,
                "user_id": user_id,
                "route": request.route,
                "source_scope": request.source_scope,
            }
        )
        return {
            "success": True,
            "final_answer": "route stub",
            "query_mode": get_patent_mode_profile(request.route).query_mode,
            "route": request.route,
            "requested_mode": "patent",
            "actual_mode": "patent",
            "source_scope": request.source_scope,
            "timings": {},
            "metadata": {"conversation_id": request.conversation_id},
            "references": [],
            "reference_objects": [],
            "reference_links": [],
            "original_links": [],
            "used_files": list(request.used_files),
            "file_selection": dict(request.file_selection),
            "trace_id": request.trace_id,
        }

    def stream_ask(self, request, *, user_id):
        self.stream_calls.append(
            {
                "trace_id": request.trace_id,
                "user_id": user_id,
                "route": request.route,
                "source_scope": request.source_scope,
            }
        )
        return iter(
            [
                {
                    "type": "metadata",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "route": request.route,
                    "query_mode": get_patent_mode_profile(request.route).query_mode,
                    "source_scope": request.source_scope,
                    "metadata": {},
                    "trace_id": request.trace_id,
                    "seq": 0,
                    "ts": "2026-03-26T00:00:00Z",
                },
                {
                    "type": "done",
                    "final_answer": "route stub",
                    "query_mode": get_patent_mode_profile(request.route).query_mode,
                    "route": request.route,
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "source_scope": request.source_scope,
                    "timings": {},
                    "references": [],
                    "reference_objects": [],
                    "trace_id": request.trace_id,
                    "used_files": list(request.used_files),
                    "reference_links": [],
                    "original_links": [],
                    "metadata": {},
                    "file_selection": dict(request.file_selection),
                    "seq": 1,
                    "ts": "2026-03-26T00:00:00Z",
                },
            ]
        )


class _RaisingStreamAskService:
    def __init__(self, exc, *, emit_metadata_first: bool = False, metadata_trace_id: str | None = None):
        self.exc = exc
        self.emit_metadata_first = emit_metadata_first
        self.metadata_trace_id = metadata_trace_id

    def sync_ask(self, request, *, user_id):
        raise self.exc

    def stream_ask(self, request, *, user_id):
        if self.emit_metadata_first:
            def _generator():
                yield {
                    "type": "metadata",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "route": "kb_qa",
                    "query_mode": "patent_kb_qa",
                    "source_scope": "kb",
                    "metadata": {},
                    "trace_id": self.metadata_trace_id or request.trace_id,
                    "seq": 0,
                    "ts": "2026-03-26T00:00:00Z",
                }
                raise self.exc
            return _generator()
        raise self.exc


class _ResolvedTraceStreamAskService:
    def sync_ask(self, request, *, user_id):
        raise NotImplementedError

    def stream_ask(self, request, *, user_id):
        return iter(
            [
                {
                    "type": "metadata",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "route": "kb_qa",
                    "query_mode": "patent_kb_qa",
                    "source_scope": "kb",
                    "metadata": {},
                    "trace_id": "req_resolved",
                    "seq": 0,
                    "ts": "2026-03-26T00:00:00Z",
                },
                {
                    "type": "content",
                    "content": "route stub",
                    "seq": 1,
                    "ts": "2026-03-26T00:00:00Z",
                },
                {
                    "type": "done",
                    "final_answer": "route stub",
                    "query_mode": "patent_kb_qa",
                    "route": "kb_qa",
                    "requested_mode": "patent",
                    "actual_mode": "patent",
                    "source_scope": "kb",
                    "timings": {},
                    "references": [],
                    "reference_objects": [],
                    "used_files": [],
                    "reference_links": [],
                    "original_links": [],
                    "metadata": {},
                    "file_selection": {},
                    "seq": 2,
                    "ts": "2026-03-26T00:00:00Z",
                },
            ]
        )



def test_patent_route_aliases_all_dispatch_to_patent_ask():
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    payload = _base_payload()
    payload["conversation_id"] = None

    sync_paths = ["/api/ask", "/api/v1/ask", "/api/patent/ask", "/api/v1/patent/ask"]
    stream_paths = ["/api/ask_stream", "/api/v1/ask_stream", "/api/patent/ask_stream", "/api/v1/patent/ask_stream"]

    with TestClient(app) as client:
        for route in sync_paths:
            response = client.post(route, json=payload)
            assert response.status_code == 200
            assert response.json()["final_answer"] == "route stub"
        for route in stream_paths:
            response = client.post(route, json=payload)
            assert response.status_code == 200
            events = _stream_events(response)
            assert events[0]["type"] == "metadata"
            assert events[-1]["type"] == "done"

    assert len(fake.sync_calls) == 4
    assert len(fake.stream_calls) == 4
    assert all(call["user_id"] is None for call in fake.sync_calls + fake.stream_calls)


def test_create_app_bootstraps_patent_executor_with_staged_runtime(monkeypatch):
    runtime = _StageRuntime()
    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda: runtime)

    app = create_app()

    assert app.state.patent_runtime is runtime
    assert app.state.ask_service._patent_executor._runtime is runtime


def test_ephemeral_sync_ask_returns_success_without_authority_calls():
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = None
    payload["kb_enabled"] = True

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["final_answer"]
    assert "Patent Phase 1 stub answer" not in body["final_answer"]
    assert body["requested_mode"] == "patent"
    assert body["actual_mode"] == "patent"


def test_ephemeral_sync_ask_returns_service_not_ready_when_runtime_bootstrap_missing(monkeypatch):
    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda: None)
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = None
    payload["kb_enabled"] = True

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY


def test_ephemeral_file_only_routes_still_work_when_runtime_bootstrap_missing(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda: None)
    app = create_app()

    with TestClient(app) as client:
        pdf_response = client.post("/api/ask", json=_pdf_payload())
        table_response = client.post("/api/ask", json=_tabular_payload())
        hybrid_response = client.post("/api/ask", json=_hybrid_payload("pdf+table"))

    assert pdf_response.status_code == 200
    assert table_response.status_code == 200
    assert hybrid_response.status_code == 200


def test_http_sync_ask_with_real_retrieval_service_preserves_viewer_uri_contract():
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = None
    payload["question"] = "Please summarize CN123456789A"
    app.state.ask_service = AskService(
        patent_executor=PatentExecutor(retrieval_service=_make_retrieval_service()),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["references"] == ["CN123456789A"]
    assert body["reference_objects"][0]["viewer_uri"] == "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html"
    assert body["original_links"][0]["viewer_uri"] == "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html"


def test_http_stream_ask_with_real_retrieval_service_preserves_viewer_uri_contract():
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = None
    payload["question"] = "Please summarize CN123456789A"
    app.state.ask_service = AskService(
        patent_executor=PatentExecutor(retrieval_service=_make_retrieval_service()),
        persistence_service=_FakePersistenceService(),
        now_factory=lambda: "2026-03-26T00:00:00Z",
    )

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[0]["type"] == "metadata"
    assert events[-1]["type"] == "done"
    assert events[-1]["references"] == ["CN123456789A"]
    assert events[-1]["reference_objects"][0]["viewer_uri"] == "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html"
    assert events[-1]["original_links"][0]["viewer_uri"] == "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html"


def test_durable_stream_busy_conversation_returns_busy_error(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    app.state.ask_service = _RaisingStreamAskService(
        APIError(
            code=codes.PATENT_BUSY,
            message="durable patent turn is already in flight",
            status_code=409,
            error="patent_busy",
            retriable=True,
        )
    )
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == codes.PATENT_BUSY
    assert all(event["type"] != "done" for event in events)


def test_ephemeral_file_sync_request_dispatches_by_default_after_rollout_open(monkeypatch):
    monkeypatch.delenv("PATENT_FILE_ROUTES_ENABLED", raising=False)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    payload = _file_payload()
    payload["conversation_id"] = None

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    assert response.json()["route"] == "hybrid_qa"
    assert fake.sync_calls == [
        {
            "trace_id": "req_123",
            "user_id": None,
            "route": "hybrid_qa",
            "source_scope": "pdf+kb",
        }
    ]


def test_ephemeral_file_sync_request_is_blocked_when_patent_file_route_gate_is_off(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "false")
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    payload = _file_payload()
    payload["conversation_id"] = None

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 503
    assert response.json()["code"] == codes.PATENT_FILE_ROUTE_DISABLED
    assert response.json()["retriable"] is False
    assert fake.sync_calls == []


def test_ephemeral_file_sync_request_dispatches_when_patent_file_route_gate_is_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    payload = _file_payload()
    payload["conversation_id"] = None

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "hybrid_qa"
    assert body["source_scope"] == "pdf+kb"
    assert body["query_mode"] == "patent_hybrid_qa"
    assert fake.sync_calls == [
        {
            "trace_id": "req_123",
            "user_id": None,
            "route": "hybrid_qa",
            "source_scope": "pdf+kb",
        }
    ]


def test_durable_file_stream_request_dispatches_when_patent_file_route_gate_is_enabled(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_file_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[0]["type"] == "metadata"
    assert events[0]["route"] == "hybrid_qa"
    assert events[0]["source_scope"] == "pdf+kb"


def test_durable_file_only_request_skips_runtime_readiness_when_runtime_bootstrap_missing(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    monkeypatch.setattr("server_fastapi.app.build_default_patent_runtime", lambda: None)
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask",
            json=_pdf_payload() | {"conversation_id": "123"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert fake.sync_calls == [
        {
            "trace_id": "req_123",
            "user_id": 42,
            "route": "pdf_qa",
            "source_scope": "pdf",
        }
    ]


def test_http_sync_pdf_route_uses_real_patent_pdf_handler_when_gate_is_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    with TestClient(app) as client:
        response = client.post("/api/ask", json=_pdf_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "pdf_qa"
    assert body["source_scope"] == "pdf"
    assert body["query_mode"] == "patent_pdf_qa"
    assert body["final_answer"]
    assert body["used_files"] == [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}]
    assert body["file_selection"] == {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"}


def test_http_sync_pdf_route_summarizes_readable_local_pdf_instead_of_stub(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
        answer_question_fn=lambda **kwargs: "真实总结：本文提出硅负极包覆方法，并报告循环寿命改善。",
    )
    payload = _pdf_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert "真实总结" in body["final_answer"]
    assert "Patent PDF route answered from selected PDF content" not in body["final_answer"]
    assert body["metadata"]["answer_mode"] == "pdf_text_summary"
    assert "local_path" not in body["used_files"][0]


def test_http_stream_pdf_route_uses_real_patent_pdf_handler_when_gate_is_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=_pdf_payload())

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[0]["type"] == "metadata"
    assert events[0]["route"] == "pdf_qa"
    assert events[0]["source_scope"] == "pdf"
    assert events[0]["query_mode"] == "patent_pdf_qa"
    assert events[-1]["type"] == "done"
    assert events[-1]["route"] == "pdf_qa"
    assert events[-1]["source_scope"] == "pdf"
    assert events[-1]["query_mode"] == "patent_pdf_qa"
    assert events[-1]["final_answer"]
    assert events[-1]["used_files"] == [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}]
    assert events[-1]["file_selection"] == {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"}


def test_http_stream_pdf_route_summarizes_readable_local_pdf_instead_of_stub(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
        answer_question_fn=lambda **kwargs: "真实总结：本文提出硅负极包覆方法，并报告循环寿命改善。",
    )
    payload = _pdf_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    assert "真实总结" in events[-1]["final_answer"]
    assert "Patent PDF route answered from selected PDF content" not in events[-1]["final_answer"]
    assert events[-1]["metadata"]["answer_mode"] == "pdf_text_summary"


def test_http_stream_pdf_route_emits_incremental_content_before_done(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
        answer_question_fn=lambda **kwargs: "真实总结：本文提出硅负极包覆方法，并报告循环寿命改善与倍率性能提升。",
    )
    payload = _pdf_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]

    assert len(content_events) >= 2
    assert "".join(event["content"] for event in content_events) == events[-1]["final_answer"]
    assert events.index(content_events[0]) < len(events) - 1
    final_success_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] == "pdf_answer" and event["status"] == "success"
    )
    assert final_success_index < events.index(content_events[0])
    assert events[-1]["type"] == "done"


def test_http_stream_pdf_route_with_stream_capability_emits_only_final_pdf_content(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    answer_text = "真实总结：本文提出硅负极包覆方法，并报告循环寿命改善与倍率性能提升，同时给出了清晰的实验验证过程。"
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
        answer_question_fn=lambda **kwargs: answer_text,
    )
    payload = _pdf_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=payload,
            headers={"X-Patent-Stream-Capability": "preview_v1"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]

    assert content_events
    assert all(event["content_role"] == "final" for event in content_events)
    assert all(event["content_source"] == "pdf" for event in content_events)
    assert all(event["content_phase"] in {"start", "delta", "end", "snapshot"} for event in content_events)
    assert not any(event["content_role"] == "preview" for event in content_events)
    assert "".join(event["content"] for event in content_events) == events[-1]["final_answer"]


def test_http_stream_pdf_route_without_stream_capability_keeps_legacy_untyped_content(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
        answer_question_fn=lambda **kwargs: "真实总结：本文提出硅负极包覆方法，并报告循环寿命改善与倍率性能提升。",
    )
    payload = _pdf_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]

    assert content_events
    assert all("content_role" not in event for event in content_events)
    assert all("content_source" not in event for event in content_events)


def test_http_stream_pdf_route_cache_hit_with_stream_capability_replays_single_final_snapshot(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    class _CacheHitExecutionCache:
        available = True

        def get_file_route_cache(self, *, fingerprint: str):
            return {
                "handler": "pdf",
                "answer_text": "缓存命中：这是一段来自 PDF 文件问答缓存的最终答案。",
                "route": "pdf_qa",
                "query_mode": "patent_pdf_qa",
                "source_scope": "pdf",
                "steps": [{"step": "dispatch", "title": "进入文件分支", "message": "进入 PDF 问答分支", "status": "success"}],
                "metadata": {"answer_mode": "pdf_text_summary"},
                "timings": {"patent_pdf_route_ms": 1},
                "used_files": [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}],
                "selected_file_ids": [11],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"},
                "kb_enabled": False,
            }

    class _ExplodingPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            raise AssertionError("pdf service should not run on file-route cache hit")

    app.state.ask_service._patent_executor._execution_cache = _CacheHitExecutionCache()
    app.state.ask_service._patent_executor._pdf_service = _ExplodingPdfService()

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_pdf_payload(),
            headers={"X-Patent-Stream-Capability": "preview_v1"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]

    assert len(content_events) == 1
    assert content_events[0]["content_role"] == "final"
    assert content_events[0]["content_source"] == "pdf"
    assert content_events[0]["content_phase"] == "snapshot"
    assert content_events[0]["content"] == events[-1]["final_answer"]


def test_http_stream_hybrid_pdf_table_without_stream_capability_keeps_legacy_untyped_content(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    class _StreamingPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            if callable(content_callback):
                content_callback("PDF 预览：材料设计与实验背景。")
            return {
                "answer_text": "PDF 文件结论：该文献给出了完整的实验设计。",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "pdf_answer", "title": "PDF", "message": "ok", "status": "success"}],
                "metadata": {"answer_mode": "pdf_text_summary"},
                "timings": {"pdf_ms": 2},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "selected_file_ids": [item.file_id for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
                "kb_enabled": include_kb,
            }

    class _StreamingTabularService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            if callable(content_callback):
                content_callback("表格预览：容量与循环寿命指标。")
            return {
                "answer_text": "表格文件结论：指标对比显示容量和循环寿命存在差异。",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "tabular_answer", "title": "表格", "message": "ok", "status": "success"}],
                "metadata": {"answer_mode": "table_summary"},
                "timings": {"tabular_ms": 2},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "selected_file_ids": [item.file_id for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
                "kb_enabled": include_kb,
            }

    app.state.ask_service._patent_executor._pdf_service = _StreamingPdfService()
    app.state.ask_service._patent_executor._tabular_service = _StreamingTabularService()

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=_hybrid_payload("pdf+table"))

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]

    assert content_events
    assert all("content_role" not in event for event in content_events)
    assert all("content_source" not in event for event in content_events)


def test_http_stream_hybrid_pdf_table_with_stream_capability_emits_preview_before_final(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    class _StreamingPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            if callable(content_callback):
                content_callback("PDF 预览：材料设计与实验背景。")
            return {
                "answer_text": "PDF 文件结论：该文献给出了完整的实验设计。",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "pdf_answer", "title": "PDF", "message": "ok", "status": "success"}],
                "metadata": {"answer_mode": "pdf_text_summary"},
                "timings": {"pdf_ms": 2},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "selected_file_ids": [item.file_id for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
                "kb_enabled": include_kb,
            }

    class _StreamingTabularService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            if callable(content_callback):
                content_callback("表格预览：容量与循环寿命指标。")
            return {
                "answer_text": "表格文件结论：指标对比显示容量和循环寿命存在差异。",
                "route": contract.route,
                "query_mode": "patent_hybrid_qa",
                "source_scope": contract.source_scope,
                "steps": [{"step": "tabular_answer", "title": "表格", "message": "ok", "status": "success"}],
                "metadata": {"answer_mode": "table_summary"},
                "timings": {"tabular_ms": 2},
                "used_files": [item.as_payload() for item in contract.selected_execution_files],
                "selected_file_ids": [item.file_id for item in contract.selected_execution_files],
                "file_selection": dict(contract.file_selection),
                "kb_enabled": include_kb,
            }

    app.state.ask_service._patent_executor._pdf_service = _StreamingPdfService()
    app.state.ask_service._patent_executor._tabular_service = _StreamingTabularService()

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_hybrid_payload("pdf+table"),
            headers={"X-Patent-Stream-Capability": "preview_v1"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]
    preview_indices = [
        index
        for index, event in enumerate(events)
        if event["type"] == "content" and event.get("content_role") == "preview"
    ]
    final_indices = [
        index
        for index, event in enumerate(events)
        if event["type"] == "content" and event.get("content_role") == "final"
    ]

    assert content_events
    assert preview_indices
    assert final_indices
    assert {"pdf", "table"} <= {event["content_source"] for event in content_events if event.get("content_role") == "preview"}
    assert all(event["content_source"] == "hybrid" for event in content_events if event.get("content_role") == "final")
    assert min(preview_indices) < min(final_indices)
    assert not any(index > min(final_indices) for index in preview_indices)
    assert events[-1]["type"] == "done"


def test_http_stream_hybrid_route_cache_hit_with_stream_capability_replays_single_final_snapshot(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    class _CacheHitExecutionCache:
        available = True

        def get_file_route_cache(self, *, fingerprint: str):
            return {
                "handler": "hybrid",
                "answer_text": "缓存命中：这是来自 hybrid 文件问答缓存的最终答案。",
                "route": "hybrid_qa",
                "query_mode": "patent_hybrid_qa",
                "source_scope": "pdf+table",
                "steps": [{"step": "dispatch", "title": "进入文件分支", "message": "进入 Hybrid 问答分支", "status": "success"}],
                "metadata": {"answer_mode": "hybrid_summary"},
                "timings": {"patent_hybrid_route_ms": 1},
                "used_files": [
                    {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
                    {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
                ],
                "selected_file_ids": [11, 33],
                "file_selection": {"strategy": "explicit_selection", "selected_file_ids": [11, 33], "source_scope": "pdf+table"},
                "kb_enabled": False,
            }

    class _ExplodingPdfService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            raise AssertionError("pdf service should not run on hybrid file-route cache hit")

    class _ExplodingTabularService:
        def execute(self, *, contract, include_kb: bool, progress_callback=None, content_callback=None):
            raise AssertionError("tabular service should not run on hybrid file-route cache hit")

    app.state.ask_service._patent_executor._execution_cache = _CacheHitExecutionCache()
    app.state.ask_service._patent_executor._pdf_service = _ExplodingPdfService()
    app.state.ask_service._patent_executor._tabular_service = _ExplodingTabularService()

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_hybrid_payload("pdf+table"),
            headers={"X-Patent-Stream-Capability": "preview_v1"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]

    assert len(content_events) == 1
    assert content_events[0]["content_role"] == "final"
    assert content_events[0]["content_source"] == "hybrid"
    assert content_events[0]["content_phase"] == "snapshot"
    assert content_events[0]["content"] == events[-1]["final_answer"]


def test_http_stream_pdf_route_emits_live_step_events_before_done(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
        answer_question_fn=lambda **kwargs: "真实总结：本文提出硅负极包覆方法，并报告循环寿命改善。",
    )
    payload = _pdf_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    step_events = [event for event in events if event["type"] == "step"]

    assert [event["step"] for event in step_events] == [
        "context_ready",
        "dispatch",
        "pdf_extract",
        "pdf_extract",
        "pdf_answer",
        "pdf_answer",
    ]
    assert events[-1]["type"] == "done", events[-1]


def test_http_stream_pdf_compare_route_emits_context_and_compare_steps_before_done(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
    )
    payload = _pdf_compare_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path_a)
    payload["execution_files"][1]["local_path"] = str(pdf_path_b)
    payload["used_files"][0]["local_path"] = str(pdf_path_a)
    payload["used_files"][1]["local_path"] = str(pdf_path_b)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    step_events = [event for event in events if event["type"] == "step"]

    assert [event["step"] for event in step_events] == [
        "context_ready",
        "dispatch",
        "pdf_extract",
        "pdf_extract",
        "multi_pdf_compare",
        "multi_pdf_compare",
        "pdf_answer",
        "pdf_answer",
    ]
    assert [step["step"] for step in events[-1]["metadata"]["steps"]] == [
        "context_ready",
        "dispatch",
        "pdf_extract",
        "multi_pdf_compare",
        "pdf_answer",
    ]
    assert "paper-a.pdf" in events[-1]["metadata"]["prepared_pdf_text"]
    assert "paper-b.pdf" in events[-1]["metadata"]["prepared_pdf_text"]
    assert events[-1]["metadata"]["steps"] == [dict(step) for step in events[-1]["metadata"]["steps"]]
    assert events[-1]["type"] == "done"


def test_http_stream_pdf_unreadable_fallback_emits_final_steps_before_failure_body(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "",
        answer_question_fn=lambda **kwargs: "不应该进入成功生成",
    )
    payload = _pdf_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    first_content_index = next(index for index, event in enumerate(events) if event["type"] == "content")
    final_step_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] in {"pdf_extract", "pdf_answer"}
    )
    assert final_step_index < first_content_index


def test_http_stream_pdf_generator_emits_content_before_final_success(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")

    def _streaming_answer(**kwargs):
        yield "真实总结：本文提出硅负极"
        yield "包覆方法，并报告循环寿命改善。"

    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
        answer_question_fn=_streaming_answer,
    )
    payload = _pdf_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    first_content_index = next(index for index, event in enumerate(events) if event["type"] == "content")
    running_index = min(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] == "pdf_answer" and event["status"] == "running"
    )
    final_success_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] == "pdf_answer" and event["status"] == "success"
    )
    last_content_index = max(index for index, event in enumerate(events) if event["type"] == "content")
    assert running_index < first_content_index
    assert first_content_index < final_success_index
    assert last_content_index < final_success_index


def test_http_stream_pdf_compare_success_emits_content_before_final_success(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
        ),
        answer_question_fn=lambda **kwargs: _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"]),
    )
    payload = _pdf_compare_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path_a)
    payload["execution_files"][1]["local_path"] = str(pdf_path_b)
    payload["used_files"][0]["local_path"] = str(pdf_path_a)
    payload["used_files"][1]["local_path"] = str(pdf_path_b)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    first_content_index = next(index for index, event in enumerate(events) if event["type"] == "content")
    final_success_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] == "pdf_answer" and event["status"] == "success"
    )
    streamed_answer = "".join(event["content"] for event in events if event["type"] == "content")
    assert first_content_index < final_success_index
    assert streamed_answer == events[-1]["final_answer"]


def test_http_stream_pdf_compare_generator_keeps_prefix_consistent_final_parity(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")

    def _streaming_compare(**kwargs):
        answer = _build_valid_compare_answer(["paper-a.pdf", "paper-b.pdf"])
        midpoint = len(answer) // 2
        yield answer[:midpoint]
        yield answer[midpoint:]

    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
        ),
        answer_question_fn=_streaming_compare,
    )
    payload = _pdf_compare_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path_a)
    payload["execution_files"][1]["local_path"] = str(pdf_path_b)
    payload["used_files"][0]["local_path"] = str(pdf_path_a)
    payload["used_files"][1]["local_path"] = str(pdf_path_b)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    first_content_index = next(index for index, event in enumerate(events) if event["type"] == "content")
    final_success_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] == "pdf_answer" and event["status"] == "success"
    )
    streamed_answer = "".join(event["content"] for event in events if event["type"] == "content")
    assert first_content_index < final_success_index
    assert streamed_answer == events[-1]["final_answer"]


def test_http_stream_pdf_compare_partial_stream_falls_back_to_buffered_final_if_normalization_changes_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")

    def _out_of_order_stream(**kwargs):
        answer = (
            "## 相同点\n"
            "- 所有文献都提供了可比较的实验结论。\n\n"
            "## 总结\n"
            "- 这些文献展示了不同技术路线下的差异化优化方向。\n\n"
            "## 应用领域差异\n"
            "### 文献 #1 关注的应用领域\n"
            "- paper-a.pdf：面向应用方向 1 的性能优化场景。\n"
            "### 文献 #2 关注的应用领域\n"
            "- paper-b.pdf：面向应用方向 2 的性能优化场景。\n\n"
            "## 研究方法差异\n"
            "### 文献 #1 采用的研究方法\n"
            "- paper-a.pdf：采用表征测试与性能验证结合的方法，重点分析方案 1。\n"
            "### 文献 #2 采用的研究方法\n"
            "- paper-b.pdf：采用表征测试与性能验证结合的方法，重点分析方案 2。\n\n"
            "## 具体内容对比\n"
            "### 文献 #1 核心内容（根据PDF原文）\n"
            "- paper-a.pdf：围绕方案 1 展开研究，并给出明确的中文结论。\n"
            "### 文献 #2 核心内容（根据PDF原文）\n"
            "- paper-b.pdf：围绕方案 2 展开研究，并给出明确的中文结论。"
        )
        midpoint = len(answer) // 2
        yield answer[:midpoint]
        yield answer[midpoint:]

    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
        ),
        answer_question_fn=_out_of_order_stream,
    )
    payload = _pdf_compare_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path_a)
    payload["execution_files"][1]["local_path"] = str(pdf_path_b)
    payload["used_files"][0]["local_path"] = str(pdf_path_a)
    payload["used_files"][1]["local_path"] = str(pdf_path_b)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    final_answer = events[-1]["final_answer"]
    streamed_answer = "".join(event["content"] for event in events if event["type"] == "content")
    first_content_index = next(index for index, event in enumerate(events) if event["type"] == "content")
    final_success_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] == "pdf_answer" and event["status"] == "success"
    )

    assert first_content_index < final_success_index
    assert streamed_answer == final_answer
    assert streamed_answer.startswith("## 具体内容对比")


def test_http_stream_pdf_compare_failure_emits_error_steps_before_failure_body(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else ""
        ),
        answer_question_fn=lambda **kwargs: "不应该进入成功比较生成",
    )
    payload = _pdf_compare_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path_a)
    payload["execution_files"][1]["local_path"] = str(pdf_path_b)
    payload["used_files"][0]["local_path"] = str(pdf_path_a)
    payload["used_files"][1]["local_path"] = str(pdf_path_b)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    step_events = [event for event in events if event["type"] == "step"]
    content_events = [event for event in events if event["type"] == "content"]

    assert ("multi_pdf_compare", "error") in {(event["step"], event["status"]) for event in step_events}
    assert ("pdf_answer", "error") in {(event["step"], event["status"]) for event in step_events}
    first_content_index = next(index for index, event in enumerate(events) if event["type"] == "content")
    last_error_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] in {"multi_pdf_compare", "pdf_answer"} and event["status"] == "error"
    )
    assert last_error_index < first_content_index
    assert "无法完成完整比较" in events[-1]["final_answer"]
    assert content_events


def test_http_stream_pdf_compare_all_unreadable_emits_compare_error_steps_before_failure_body(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "",
        answer_question_fn=lambda **kwargs: "不应该进入成功比较生成",
    )
    payload = _pdf_compare_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path_a)
    payload["execution_files"][1]["local_path"] = str(pdf_path_b)
    payload["used_files"][0]["local_path"] = str(pdf_path_a)
    payload["used_files"][1]["local_path"] = str(pdf_path_b)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    step_events = [event for event in events if event["type"] == "step"]
    content_events = [event for event in events if event["type"] == "content"]

    assert ("multi_pdf_compare", "error") in {(event["step"], event["status"]) for event in step_events}
    assert ("pdf_answer", "error") in {(event["step"], event["status"]) for event in step_events}
    first_content_index = next(index for index, event in enumerate(events) if event["type"] == "content")
    last_error_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] in {"multi_pdf_compare", "pdf_answer"} and event["status"] == "error"
    )
    assert last_error_index < first_content_index
    assert "无法完成完整比较" in events[-1]["final_answer"]
    assert [step["step"] for step in events[-1]["metadata"]["steps"]] == [
        "context_ready",
        "dispatch",
        "pdf_extract",
        "multi_pdf_compare",
        "pdf_answer",
    ]


def test_http_stream_pdf_compare_partial_stream_error_does_not_leak_partial_content(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")

    def _broken_compare_stream(**kwargs):
        yield "partial compare body "
        raise RuntimeError("stream interrupted")

    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A.\n\nResults A observed.\n\nConclusion A final."
            if path == str(pdf_path_a)
            else "Abstract B.\n\nResults B observed.\n\nConclusion B final."
        ),
        answer_question_fn=_broken_compare_stream,
    )
    payload = _pdf_compare_payload()
    payload["execution_files"][0]["local_path"] = str(pdf_path_a)
    payload["execution_files"][1]["local_path"] = str(pdf_path_b)
    payload["used_files"][0]["local_path"] = str(pdf_path_a)
    payload["used_files"][1]["local_path"] = str(pdf_path_b)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    step_events = [event for event in events if event["type"] == "step"]
    content_events = [event for event in events if event["type"] == "content"]

    assert ("pdf_answer", "error") in {(event["step"], event["status"]) for event in step_events}
    assert "无法完成完整比较" in events[-1]["final_answer"]
    assert all("partial compare body" not in event["content"] for event in content_events)


def test_http_sync_tabular_route_uses_real_patent_tabular_handler_when_gate_is_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    with TestClient(app) as client:
        response = client.post("/api/ask", json=_tabular_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "tabular_qa"
    assert body["source_scope"] == "table"
    assert body["query_mode"] == "patent_tabular_qa"
    assert body["final_answer"]
    assert body["used_files"] == [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}]
    assert body["file_selection"] == {"strategy": "explicit_selection", "selected_file_ids": [33], "source_scope": "table"}


def test_http_sync_tabular_route_summarizes_readable_local_table_instead_of_stub(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)
    app.state.ask_service._patent_executor._tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 更安全，NCM 能量更高。",
    )
    payload = _tabular_payload()
    payload["execution_files"][0].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})
    payload["used_files"][0].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert "真实表格总结" in body["final_answer"]
    assert "Patent tabular route answered from selected table content" not in body["final_answer"]
    assert body["metadata"]["answer_mode"] == "table_execution_summary"
    assert "匹配工作表" in body["metadata"]["table_evidence_context"]
    assert "local_path" not in body["used_files"][0]


def test_http_stream_tabular_route_with_stream_capability_emits_only_final_table_content(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)
    answer_text = "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh，并且表格结果显示不同材料在容量和备注字段上存在清晰差异。"
    app.state.ask_service._patent_executor._tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: answer_text,
    )
    payload = _tabular_payload()
    payload["execution_files"][0].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})
    payload["used_files"][0].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=payload,
            headers={"X-Patent-Stream-Capability": "preview_v1"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]

    assert content_events
    assert all(event["content_role"] == "final" for event in content_events)
    assert all(event["content_source"] == "table" for event in content_events)
    assert all(event["content_phase"] in {"start", "delta", "end", "snapshot"} for event in content_events)
    assert not any(event["content_role"] == "preview" for event in content_events)
    assert "".join(event["content"] for event in content_events) == events[-1]["final_answer"]


@pytest.mark.parametrize(
    ("source_scope", "expected_used_files"),
    [
        ("pdf+kb", [{"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"}]),
        ("table+kb", [{"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"}]),
        (
            "pdf+table",
            [
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
                {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
            ],
        ),
        (
            "pdf+table+kb",
            [
                {"file_id": 11, "file_type": "pdf", "file_name": "spec.pdf"},
                {"file_id": 33, "file_type": "xlsx", "file_name": "claims.xlsx"},
            ],
        ),
    ],
)
def test_http_stream_hybrid_routes_use_real_patent_handlers_when_gate_is_enabled(monkeypatch, source_scope, expected_used_files):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=_hybrid_payload(source_scope))

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[0]["type"] == "metadata"
    assert events[0]["route"] == "hybrid_qa"
    assert events[0]["source_scope"] == source_scope
    assert events[0]["query_mode"] == "patent_hybrid_qa"
    assert events[-1]["type"] == "done"
    assert events[-1]["route"] == "hybrid_qa"
    assert events[-1]["source_scope"] == source_scope
    assert events[-1]["query_mode"] == "patent_hybrid_qa"
    assert events[-1]["final_answer"]
    assert events[-1]["used_files"] == expected_used_files
    assert events[-1]["file_selection"]["source_scope"] == source_scope


def test_http_sync_hybrid_route_uses_real_pdf_and_table_content_instead_of_stub(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
    )
    app.state.ask_service._patent_executor._tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
    )
    payload = _hybrid_payload("pdf+table")
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)
    payload["execution_files"][1].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})
    payload["used_files"][1].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert "LMFP/LFP" in body["final_answer"]
    assert "120mAh" in body["final_answer"]
    assert "PDF 部分：" not in body["final_answer"]
    assert "表格部分：" not in body["final_answer"]
    assert "Patent hybrid route combined selected PDF and table files" not in body["final_answer"]
    assert body["metadata"]["answer_mode"] == "hybrid_unified_synthesis"


def test_http_sync_hybrid_pdf_table_kb_route_keeps_context_dispatch_and_unified_answer(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
    )
    app.state.ask_service._patent_executor._tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
    )

    class _HybridKbService:
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "知识库补充：相关专利族强调热稳定性和倍率性能的平衡。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据指出该路线强调热稳定性与倍率性能平衡。",
                    "kb_reference_instruction": "引用知识库时使用 CN123456789A。",
                },
                "timings": {"kb_ms": 7},
            }

    app.state.ask_service._patent_executor._kb_service = _HybridKbService()
    payload = _hybrid_payload("pdf+table+kb")
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)
    payload["execution_files"][1].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})
    payload["used_files"][1].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["answer_mode"] == "hybrid_unified_synthesis"
    assert "LMFP/LFP" in body["final_answer"]
    assert "120mAh" in body["final_answer"]
    assert "知识库补充" in body["final_answer"]
    assert "CN123456789A" in body["final_answer"]
    assert "匹配工作表:" not in body["final_answer"]
    assert "执行操作:" not in body["final_answer"]
    assert "文件:" not in body["final_answer"]
    assert [step["step"] for step in body["metadata"]["steps"][:2]] == ["context_ready", "dispatch"]
    assert body["metadata"]["steps"][-1]["step"] == "hybrid_answer"
    assert body["metadata"]["steps"][-1]["status"] == "success"


def test_http_sync_pdf_route_with_two_selected_files_and_single_target_question_uses_only_first_document(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path_a = tmp_path / "paper-a.pdf"
    pdf_path_b = tmp_path / "paper-b.pdf"
    pdf_path_a.write_bytes(b"%PDF-1.4\nplaceholder\n")
    pdf_path_b.write_bytes(b"%PDF-1.4\nplaceholder\n")
    seen_inputs: list[str] = []
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: (
            "Abstract A unique fact.\n\nResults A report 15% efficiency improvement."
            if path == str(pdf_path_a)
            else "Abstract B unique fact.\n\nResults B report 200-cycle retention."
        ),
        answer_question_fn=lambda **kwargs: seen_inputs.append(str(kwargs.get("pdf_text") or "")) or "第一篇文献总结：聚焦效率提升。",
    )
    payload = _pdf_compare_payload()
    payload["question"] = "请总结第一篇文献的研究内容"
    payload["execution_files"][0]["local_path"] = str(pdf_path_a)
    payload["execution_files"][1]["local_path"] = str(pdf_path_b)
    payload["used_files"][0]["local_path"] = str(pdf_path_a)
    payload["used_files"][1]["local_path"] = str(pdf_path_b)

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["answer_mode"] == "pdf_text_summary"
    assert len(seen_inputs) == 1
    assert "paper-a.pdf" in seen_inputs[0]
    assert "15% efficiency improvement" in seen_inputs[0]
    assert "paper-b.pdf" not in seen_inputs[0]
    assert "200-cycle retention" not in seen_inputs[0]


def test_http_stream_hybrid_route_emits_file_progress_steps_before_done(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
    )
    app.state.ask_service._patent_executor._tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
    )
    payload = _hybrid_payload("pdf+table")
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)
    payload["execution_files"][1].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})
    payload["used_files"][1].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    step_events = [event for event in events if event["type"] == "step"]

    assert [event["step"] for event in step_events] == [
        "context_ready",
        "dispatch",
        "pdf_extract",
        "pdf_extract",
        "pdf_answer",
        "pdf_answer",
        "tabular_load",
        "tabular_load",
        "tabular_answer",
        "tabular_answer",
        "hybrid_answer",
        "hybrid_answer",
    ]
    assert events[-1]["type"] == "done"


def test_http_stream_hybrid_pdf_kb_route_emits_incremental_content_before_done(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper proposes a silicon anode coating method and reports improved cycle life.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：本文提出硅负极包覆方法，并报告循环寿命改善。",
    )

    class _StreamingKbService:
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            answer_text = "这是知识库补充：该方向在专利布局上集中于包覆材料和热稳定性。"
            if callable(content_callback):
                content_callback(answer_text[:14])
                content_callback(answer_text[14:])
            return {
                "answer_text": answer_text,
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"title": "Patent KB", "message": "KB participated."}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {"retrieval_backend": "patent-local-kb"},
                "timings": {"kb_ms": 7},
            }

    app.state.ask_service._patent_executor._kb_service = _StreamingKbService()
    payload = _hybrid_payload("pdf+kb")
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    content_events = [event for event in events if event["type"] == "content"]

    assert len(content_events) >= 3
    assert "".join(event["content"] for event in content_events) == events[-1]["final_answer"]
    assert "Patent KB participation:" not in "".join(event["content"] for event in content_events)
    assert events.index(content_events[0]) < len(events) - 1
    final_hybrid_success_index = max(
        index
        for index, event in enumerate(events)
        if event["type"] == "step" and event["step"] == "hybrid_answer" and event["status"] == "success"
    )
    assert events.index(content_events[0]) < final_hybrid_success_index
    assert events[-1]["type"] == "done"


def test_http_stream_hybrid_pdf_table_kb_route_emits_only_final_unified_hybrid_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()
    pdf_path = tmp_path / "spec.pdf"
    csv_path = tmp_path / "claims.csv"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    _write_csv(csv_path)
    app.state.ask_service._patent_executor._pdf_service = PatentPdfService(
        extract_pdf_text_fn=lambda path, max_pages=10: "This paper studies LMFP/LFP blending and reports safer charging behavior.",
        answer_question_fn=lambda **kwargs: "真实 PDF 总结：LMFP/LFP 复配改善了充电安全性。",
    )
    app.state.ask_service._patent_executor._tabular_service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "真实表格总结：LMFP 120mAh，LFP 115mAh，NCM 140mAh。",
    )

    class _HybridKbService:
        def run(self, *, request, runtime=None, conversation_context=None, progress_callback=None, content_callback=None):
            return {
                "answer_text": "知识库补充：相关专利族强调热稳定性和倍率性能的平衡。",
                "route": request.route,
                "query_mode": "patent_hybrid_qa",
                "steps": [{"step": "stage4", "title": "阶段四", "message": "ok", "status": "success"}],
                "references": ["CN123456789A"],
                "reference_objects": [{"canonical_patent_id": "CN123456789A"}],
                "reference_links": [],
                "original_links": [],
                "metadata": {
                    "retrieval_backend": "patent-local-kb",
                    "kb_evidence_context": "知识库证据指出该路线强调热稳定性与倍率性能平衡。",
                    "kb_reference_instruction": "引用知识库时仅可使用这些专利号：CN123456789A",
                },
                "timings": {"kb_ms": 7},
            }

    app.state.ask_service._patent_executor._kb_service = _HybridKbService()
    payload = _hybrid_payload("pdf+table+kb")
    payload["execution_files"][0]["local_path"] = str(pdf_path)
    payload["used_files"][0]["local_path"] = str(pdf_path)
    payload["execution_files"][1].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})
    payload["used_files"][1].update({"file_type": "csv", "file_name": "claims.csv", "local_path": str(csv_path)})

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    assert response.status_code == 200
    events = _stream_events(response)
    step_events = [event for event in events if event["type"] == "step"]

    assert [event["step"] for event in step_events] == [
        "context_ready",
        "dispatch",
        "pdf_extract",
        "pdf_extract",
        "pdf_answer",
        "pdf_answer",
        "tabular_load",
        "tabular_load",
        "tabular_answer",
        "tabular_answer",
        "kb_evidence",
        "kb_evidence",
        "hybrid_answer",
        "hybrid_answer",
    ]
    assert sum(1 for event in step_events if event["step"] == "hybrid_answer") == 2
    assert events[-1]["type"] == "done"


def test_durable_sync_request_is_blocked_by_route_gate_before_auth_or_service(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake

    with TestClient(app) as client:
        response = client.post("/api/ask", json=_base_payload())

    assert response.status_code == 503
    assert response.json()["code"] == codes.DURABLE_MODE_DISABLED
    assert fake.sync_calls == []


def test_durable_stream_request_is_blocked_by_route_gate_before_auth_or_service(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "false")
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=_base_payload())

    assert response.status_code == 503
    assert response.json()["code"] == codes.DURABLE_MODE_DISABLED
    assert fake.stream_calls == []


def test_durable_request_requires_auth_after_rollout_gate_is_enabled(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake

    with TestClient(app) as client:
        response = client.post("/api/ask", json=_base_payload())

    assert response.status_code == 401
    assert response.json()["code"] == codes.TOKEN_MISSING
    assert fake.sync_calls == []


def test_durable_sync_request_blocks_when_dependencies_are_not_ready(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY
    assert fake.sync_calls == []


def test_durable_stream_request_blocks_when_dependencies_are_not_ready(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY
    assert fake.stream_calls == []


def test_durable_stream_gateway_owned_headers_are_injected_into_request_options(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()
    class _OptionCaptureAskService:
        def __init__(self):
            self.stream_calls = []

        def sync_ask(self, request, *, user_id):
            raise NotImplementedError

        def stream_ask(self, request, *, user_id):
            self.stream_calls.append(
                {
                    "trace_id": request.trace_id,
                    "user_id": user_id,
                    "route": request.route,
                    "source_scope": request.source_scope,
                    "options": dict(request.options or {}),
                }
            )
            return iter(
                [
                    {
                        "type": "metadata",
                        "requested_mode": "patent",
                        "actual_mode": "patent",
                        "route": request.route,
                        "query_mode": get_patent_mode_profile(request.route).query_mode,
                        "source_scope": request.source_scope,
                        "metadata": {},
                        "trace_id": request.trace_id,
                        "seq": 0,
                        "ts": "2026-03-26T00:00:00Z",
                    },
                    {
                        "type": "done",
                        "final_answer": "route stub",
                        "query_mode": get_patent_mode_profile(request.route).query_mode,
                        "route": request.route,
                        "requested_mode": "patent",
                        "actual_mode": "patent",
                        "source_scope": request.source_scope,
                        "timings": {},
                        "references": [],
                        "reference_objects": [],
                        "trace_id": request.trace_id,
                        "used_files": list(request.used_files),
                        "reference_links": [],
                        "original_links": [],
                        "metadata": {},
                        "file_selection": dict(request.file_selection),
                        "seq": 1,
                        "ts": "2026-03-26T00:00:00Z",
                    },
                ]
            )

    fake = _OptionCaptureAskService()
    app.state.ask_service = fake
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Gateway-Task-Execution": "1",
                "X-Gateway-Owned-Persistence": "1",
            },
        )

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[-1]["type"] == "done"
    assert fake.stream_calls[0]["options"]["gateway_task_execution"] is True
    assert fake.stream_calls[0]["options"]["gateway_owned_persistence"] is True


def test_durable_sync_gateway_owned_headers_are_injected_into_request_options(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()

    class _OptionCaptureSyncAskService(_RouteFakeAskService):
        def __init__(self):
            super().__init__()
            self.sync_options = {}

        def sync_ask(self, request, *, user_id):
            self.sync_options = dict(request.options or {})
            return super().sync_ask(request, user_id=user_id)

    fake = _OptionCaptureSyncAskService()
    app.state.ask_service = fake
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask",
            json=_base_payload(),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Gateway-Task-Execution": "1",
                "X-Gateway-Owned-Persistence": "1",
            },
        )

    assert response.status_code == 200
    assert fake.sync_options["gateway_task_execution"] is True
    assert fake.sync_options["gateway_owned_persistence"] is True


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Gateway-Task-Execution": "1"},
        {"X-Gateway-Owned-Persistence": "1"},
    ],
)
def test_durable_stream_single_gateway_header_does_not_enable_gateway_owned_options(monkeypatch, headers):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()

    class _OptionCaptureAskService:
        def __init__(self):
            self.stream_options = {}

        def sync_ask(self, request, *, user_id):
            raise NotImplementedError

        def stream_ask(self, request, *, user_id):
            self.stream_options = dict(request.options or {})
            return iter(
                [
                    {
                        "type": "metadata",
                        "requested_mode": "patent",
                        "actual_mode": "patent",
                        "route": request.route,
                        "query_mode": get_patent_mode_profile(request.route).query_mode,
                        "source_scope": request.source_scope,
                        "metadata": {},
                        "trace_id": request.trace_id,
                        "seq": 0,
                        "ts": "2026-03-26T00:00:00Z",
                    },
                    {
                        "type": "done",
                        "final_answer": "route stub",
                        "query_mode": get_patent_mode_profile(request.route).query_mode,
                        "route": request.route,
                        "requested_mode": "patent",
                        "actual_mode": "patent",
                        "source_scope": request.source_scope,
                        "timings": {},
                        "references": [],
                        "reference_objects": [],
                        "trace_id": request.trace_id,
                        "used_files": list(request.used_files),
                        "reference_links": [],
                        "original_links": [],
                        "metadata": {},
                        "file_selection": dict(request.file_selection),
                        "seq": 1,
                        "ts": "2026-03-26T00:00:00Z",
                    },
                ]
            )

    fake = _OptionCaptureAskService()
    app.state.ask_service = fake
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    token = _make_auth_token(42)
    request_headers = {"Authorization": f"Bearer {token}"}
    request_headers.update(headers)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers=request_headers,
        )

    assert response.status_code == 200
    if "X-Gateway-Task-Execution" in headers:
        assert fake.stream_options["gateway_task_execution"] is True
        assert "gateway_owned_persistence" not in fake.stream_options
    else:
        assert fake.stream_options["gateway_owned_persistence"] is True
        assert "gateway_task_execution" not in fake.stream_options


def test_durable_stream_body_gateway_owned_options_are_ignored_without_trusted_headers(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()

    class _OptionCaptureAskService(_RouteFakeAskService):
        def __init__(self):
            super().__init__()
            self.stream_options = {}

        def stream_ask(self, request, *, user_id):
            self.stream_options = dict(request.options or {})
            return super().stream_ask(request, user_id=user_id)

    fake = _OptionCaptureAskService()
    app.state.ask_service = fake
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    token = _make_auth_token(42)
    payload = _base_payload()
    payload["options"] = {
        "gateway_task_execution": True,
        "gateway_owned_persistence": True,
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "gateway_task_execution" not in fake.stream_options
    assert "gateway_owned_persistence" not in fake.stream_options


def test_file_stream_capability_header_is_injected_for_file_routes(monkeypatch):
    monkeypatch.setenv("PATENT_FILE_ROUTES_ENABLED", "true")
    app = create_app()

    class _OptionCaptureAskService(_RouteFakeAskService):
        def __init__(self):
            super().__init__()
            self.stream_options = {}

        def stream_ask(self, request, *, user_id):
            self.stream_options = dict(request.options or {})
            return super().stream_ask(request, user_id=user_id)

    fake = _OptionCaptureAskService()
    app.state.ask_service = fake
    payload = _hybrid_payload("pdf+kb")

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=payload,
            headers={"X-Patent-Stream-Capability": "preview_v1"},
        )

    assert response.status_code == 200
    assert fake.stream_options["patent_stream_capability"] == "preview_v1"


def test_stream_capability_header_is_ignored_for_standalone_kb_route(monkeypatch):
    app = create_app()

    class _OptionCaptureAskService(_RouteFakeAskService):
        def __init__(self):
            super().__init__()
            self.stream_options = {}

        def stream_ask(self, request, *, user_id):
            self.stream_options = dict(request.options or {})
            return super().stream_ask(request, user_id=user_id)

    fake = _OptionCaptureAskService()
    app.state.ask_service = fake
    payload = _base_payload()
    payload["conversation_id"] = None

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=payload,
            headers={"X-Patent-Stream-Capability": "preview_v1"},
        )

    assert response.status_code == 200
    assert "patent_stream_capability" not in fake.stream_options


def test_durable_stream_requires_auth_before_dependency_readiness(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=_base_payload())

    assert response.status_code == 401
    assert response.json()["code"] == codes.TOKEN_MISSING
    assert fake.stream_calls == []


def test_durable_request_blocks_when_runtime_dispatcher_degrades_after_start(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    app = create_app()
    fake = _RouteFakeAskService()
    app.state.ask_service = fake
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    runtime_state = dict(app.state.runtime_dispatcher.runtime_state())
    app.state.runtime_dispatcher.runtime_state = lambda: {**runtime_state, "ready": False}
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY
    assert fake.sync_calls == []


def test_stream_renewal_failure_emits_terminal_error_not_done(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    app.state.ask_service = _RaisingStreamAskService(
        APIError(
            code=codes.SERVICE_NOT_READY,
            message="durable patent runtime guard renewal failed",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        ),
        emit_metadata_first=True,
    )
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    events = _stream_events(response)
    assert events[0]["type"] == "metadata"
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == codes.SERVICE_NOT_READY
    assert all(event["type"] != "done" for event in events)


def test_stream_terminal_error_uses_latest_resolved_trace_id(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "true")
    monkeypatch.setenv("PATENT_DURABLE_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("PATENT_AUTHORITY_INTERNAL_TOKEN", "secret-token")
    app = create_app()
    app.state.component_status["redis"]["ready"] = True
    app.state.component_status["authority"]["ready"] = True
    app.state.ask_service = _RaisingStreamAskService(
        APIError(
            code=codes.SERVICE_NOT_READY,
            message="durable patent runtime guard renewal failed",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        ),
        emit_metadata_first=True,
        metadata_trace_id="req_resolved",
    )
    token = _make_auth_token(42)

    with TestClient(app) as client:
        response = client.post(
            "/api/ask_stream",
            json=_base_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )

    events = _stream_events(response)
    assert events[0]["trace_id"] == "req_resolved"
    assert events[-1]["type"] == "error"
    assert events[-1]["trace_id"] == "req_resolved"


def test_stream_terminal_error_uses_middleware_trace_before_first_frame():
    service = _RaisingStreamAskService(
        APIError(
            code=codes.SERVICE_NOT_READY,
            message="durable patent runtime guard renewal failed",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        )
    )
    request = type(
        "_Request",
        (),
        {"app": type("_App", (), {"state": type("_State", (), {"ask_service": service, "runtime_dispatcher": None})()})()},
    )()
    ask_request = type("_AskRequest", (), {"trace_id": ""})()
    token = set_trace_id("req_generated")
    try:
        response = _build_streaming_response(request=request, ask_request=ask_request, user_id=42)

        async def _collect_body() -> str:
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
            return "".join(chunks)

        payloads = []
        for chunk in asyncio.run(_collect_body()).strip().split("\n\n"):
            item = chunk.strip()
            if not item:
                continue
            assert item.startswith("data: ")
            payloads.append(__import__("json").loads(item[6:]))
    finally:
        clear_trace_id(token)

    assert payloads[-1]["type"] == "error"
    assert payloads[-1]["trace_id"] == "req_generated"


def test_success_stream_carries_resolved_trace_id_to_later_frames():
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = None
    app.state.ask_service = _ResolvedTraceStreamAskService()

    with TestClient(app) as client:
        response = client.post("/api/ask_stream", json=payload)

    events = _stream_events(response)
    assert [event["trace_id"] for event in events] == ["req_resolved", "req_resolved", "req_resolved"]
    assert events[-1]["type"] == "done"


def test_ephemeral_request_still_runs_when_durable_redis_path_is_unavailable(monkeypatch):
    monkeypatch.setenv("PATENT_DURABLE_MODE_ENABLED", "true")
    monkeypatch.setenv("PATENT_REDIS_ENABLED", "false")
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = None
    payload["kb_enabled"] = True

    with TestClient(app) as client:
        response = client.post("/api/patent/ask", json=payload)

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["final_answer"]
    assert "Patent Phase 1 stub answer" not in response.json()["final_answer"]


def test_http_request_rejects_invalid_conversation_id_instead_of_downgrading_to_ephemeral():
    app = create_app()
    payload = _base_payload()
    payload["conversation_id"] = "opaque-ephemeral"

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 400
    assert response.json()["code"] == codes.INVALID_REQUEST
    assert "conversation_id" in response.json()["message"]


def test_http_request_rejects_non_empty_file_selection_in_phase1():
    app = create_app()
    payload = _base_payload()
    payload["file_selection"] = {"selected": [1]}

    with TestClient(app) as client:
        response = client.post("/api/ask", json=payload)

    assert response.status_code == 400
    assert response.json()["code"] == codes.PROTOCOL_MISMATCH
    assert "file_selection" in response.json()["message"]
