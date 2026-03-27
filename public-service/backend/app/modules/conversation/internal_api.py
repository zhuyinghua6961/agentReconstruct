from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.errors import AppError
from app.modules.conversation import service as conversation_service_module
from app.modules.conversation.authority_schemas import (
    AuthorityAssistantAsyncRequest,
    AuthorityContextSnapshotRequest,
    AuthorityContextSnapshotResponse,
    AuthorityConversationState,
    AuthorityConversationSummary,
    AuthorityUserWriteRequest,
)


router = APIRouter(tags=["conversation-internal"])

_INTERNAL_TOKEN_ENV = "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN"
_SOURCE_SERVICE_POLICY: dict[str, dict[str, set[str]]] = {
    "fastQA": {
        "requested_modes": {"fast", "thinking"},
        "actual_modes": {"fast"},
    },
    "highThinkingQA": {
        "requested_modes": {"thinking"},
        "actual_modes": {"thinking"},
    },
}


@dataclass(frozen=True)
class InternalAuthorityCaller:
    service_name: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _expected_internal_token() -> str:
    token = str(os.getenv(_INTERNAL_TOKEN_ENV, "") or "").strip()
    if token:
        return token
    if str(os.getenv("APP_ENV", "") or "").strip().lower() == "test":
        return "authority-test-token"
    return ""


def require_internal_authority(
    service_name: str | None = Header(default=None, alias="X-Internal-Service-Name"),
    service_token: str | None = Header(default=None, alias="X-Internal-Service-Token"),
) -> InternalAuthorityCaller:
    caller = str(service_name or "").strip()
    token = str(service_token or "").strip()
    if not caller or not token:
        raise AppError(message="internal_auth_missing", code="INTERNAL_AUTH_MISSING", status_code=401)

    expected_token = _expected_internal_token()
    if not expected_token or token != expected_token:
        raise AppError(message="internal_auth_invalid", code="INTERNAL_AUTH_INVALID", status_code=401)

    return InternalAuthorityCaller(service_name=caller)


def _conversation_service():
    return conversation_service_module.conversation_service


def _raise_service_error(*, result: dict, ok_status: int) -> None:
    if result.get("success"):
        return
    service = _conversation_service()
    raise AppError(
        message=str(result.get("error") or "internal_authority_error"),
        code=str(result.get("code") or "INTERNAL_AUTHORITY_ERROR"),
        status_code=service.status_code_for(result, ok_status=ok_status),
    )


def _enforce_path_conversation_id(*, path_conversation_id: int, payload_conversation_id: int) -> None:
    if int(path_conversation_id) != int(payload_conversation_id):
        raise AppError(message="conversation_id_mismatch", code="CONVERSATION_ID_MISMATCH", status_code=400)


def _enforce_source_service_policy(
    *,
    caller_service_name: str,
    source_service: str,
    requested_mode: str,
    actual_mode: str,
) -> None:
    policy = _SOURCE_SERVICE_POLICY.get(str(source_service))
    allowed_requested_modes = set(policy.get("requested_modes") or ()) if isinstance(policy, dict) else set()
    allowed_actual_modes = set(policy.get("actual_modes") or ()) if isinstance(policy, dict) else set()
    if (
        caller_service_name != source_service
        or not policy
        or requested_mode not in allowed_requested_modes
        or actual_mode not in allowed_actual_modes
    ):
        raise AppError(
            message="internal_source_service_forbidden",
            code="INTERNAL_SOURCE_SERVICE_FORBIDDEN",
            status_code=403,
        )


def _enforce_idempotency_key(*, idempotency_key: str, conversation_id: int, trace_id: str, operation: str) -> None:
    expected = f"{conversation_id}:{trace_id}:{operation}"
    if str(idempotency_key).strip() != expected:
        raise AppError(message="idempotency_key_invalid", code="IDEMPOTENCY_KEY_INVALID", status_code=400)


@router.post("/internal/conversations/{conversation_id}/messages/user")
def append_user_message(
    conversation_id: int,
    payload: AuthorityUserWriteRequest,
    caller: InternalAuthorityCaller = Depends(require_internal_authority),
):
    _enforce_path_conversation_id(path_conversation_id=conversation_id, payload_conversation_id=payload.conversation_id)
    _enforce_source_service_policy(
        caller_service_name=caller.service_name,
        source_service=payload.source_service,
        requested_mode=payload.requested_mode,
        actual_mode=payload.actual_mode,
    )
    _enforce_idempotency_key(
        idempotency_key=payload.idempotency_key,
        conversation_id=payload.conversation_id,
        trace_id=payload.trace_id,
        operation="user",
    )

    result = _conversation_service().add_authority_user_message(
        user_id=payload.user_id,
        conversation_id=payload.conversation_id,
        trace_id=payload.trace_id,
        source_service=payload.source_service,
        route=payload.route,
        requested_mode=payload.requested_mode,
        actual_mode=payload.actual_mode,
        idempotency_key=payload.idempotency_key,
        content=payload.message.content,
        context_hints=payload.context_hints.model_dump(),
    )
    _raise_service_error(result=result, ok_status=201)
    return JSONResponse(status_code=201, content=jsonable_encoder(result))


@router.get("/internal/conversations/{conversation_id}/context-snapshot")
def get_context_snapshot(
    conversation_id: int,
    user_id: int = Query(..., gt=0),
    trace_id: str = Query(..., min_length=1),
    source_service: str = Query(..., min_length=1),
    route: str = Query(..., min_length=1),
    requested_mode: str = Query(..., min_length=1),
    actual_mode: str = Query(..., min_length=1),
    caller: InternalAuthorityCaller = Depends(require_internal_authority),
):
    request_contract = AuthorityContextSnapshotRequest(
        conversation_id=conversation_id,
        user_id=user_id,
        trace_id=trace_id,
        source_service=source_service,
        route=route,
        requested_mode=requested_mode,
        actual_mode=actual_mode,
    )
    _enforce_source_service_policy(
        caller_service_name=caller.service_name,
        source_service=request_contract.source_service,
        requested_mode=request_contract.requested_mode,
        actual_mode=request_contract.actual_mode,
    )

    result = _conversation_service().get_conversation_context_snapshot(
        user_id=request_contract.user_id,
        conversation_id=request_contract.conversation_id,
    )
    _raise_service_error(result=result, ok_status=200)
    payload = result.get("data") if isinstance(result.get("data"), dict) else {}
    raw_conversation_state = payload.get("conversation_state") if isinstance(payload.get("conversation_state"), dict) else {}
    response = AuthorityContextSnapshotResponse(
        conversation_id=int(payload.get("conversation_id") or request_contract.conversation_id),
        user_id=int(payload.get("user_id") or request_contract.user_id),
        snapshot_version=int(payload.get("snapshot_version") or 0),
        updated_at=payload.get("updated_at") or _utc_now(),
        summary=payload.get("summary") or AuthorityConversationSummary(),
        recent_turns=payload.get("recent_turns") or [],
        conversation_state=AuthorityConversationState(
            last_turn_route=str(raw_conversation_state.get("last_turn_route") or "").strip() or None,
            last_focus_file_ids=list(raw_conversation_state.get("last_focus_file_ids") or []),
            last_assistant_trace_id=str(raw_conversation_state.get("last_assistant_trace_id") or "").strip() or None,
        ),
    )
    return JSONResponse(status_code=200, content=jsonable_encoder(response.model_dump()))


@router.post("/internal/conversations/{conversation_id}/messages/assistant-async")
def accept_assistant_event(
    conversation_id: int,
    payload: AuthorityAssistantAsyncRequest,
    caller: InternalAuthorityCaller = Depends(require_internal_authority),
):
    _enforce_path_conversation_id(path_conversation_id=conversation_id, payload_conversation_id=payload.conversation_id)
    _enforce_source_service_policy(
        caller_service_name=caller.service_name,
        source_service=payload.source_service,
        requested_mode=payload.requested_mode,
        actual_mode=payload.actual_mode,
    )
    _enforce_idempotency_key(
        idempotency_key=payload.idempotency_key,
        conversation_id=payload.conversation_id,
        trace_id=payload.trace_id,
        operation="assistant",
    )

    result = _conversation_service().accept_authority_assistant_async(
        user_id=payload.user_id,
        conversation_id=payload.conversation_id,
        trace_id=payload.trace_id,
        source_service=payload.source_service,
        route=payload.route,
        requested_mode=payload.requested_mode,
        actual_mode=payload.actual_mode,
        idempotency_key=payload.idempotency_key,
        final_event=payload.final_event.model_dump(),
    )
    _raise_service_error(result=result, ok_status=202)
    return JSONResponse(
        status_code=202,
        content={
            "accepted": True,
            "event_id": str(result.get("event_id") or f"assistant-async:{payload.conversation_id}:{payload.trace_id}"),
            "trace_id": payload.trace_id,
            "idempotency_key": payload.idempotency_key,
            "status": str(result.get("status") or "accepted"),
        },
    )
