from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask

from app.core.deps import AuthContext
from app.core.errors import AppError
from app.modules.auth.deps import require_auth_context
from app.modules.conversation import service as conversation_service_module
from app.modules.conversation.schemas import (
    AddConversationMessageRequest,
    CreateConversationRequest,
    UpdateConversationTitleRequest,
)
from app.modules.quota.deps import finalize_quota, require_quota
from app.modules.quota.service import QuotaGrant


router = APIRouter(tags=["conversation"])


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    service = conversation_service_module.conversation_service
    return JSONResponse(status_code=service.status_code_for(result, ok_status=ok_status), content=jsonable_encoder(result))


def _enforce_quota_finalize(*, grant: QuotaGrant | None, result: JSONResponse | RedirectResponse | FileResponse) -> None:
    finalize_result = finalize_quota(grant, result=result)
    if isinstance(finalize_result, dict) and finalize_result.get("success") is False:
        raise AppError(
            message=str(finalize_result.get("error") or "quota_finalize_failed"),
            code=str(finalize_result.get("code") or "DB_UNAVAILABLE"),
            status_code=503,
        )


@router.post("/api/v1/conversations")
@router.post("/api/conversations")
def create_conversation(payload: CreateConversationRequest, context: AuthContext = Depends(require_auth_context)):
    return _respond(
        conversation_service_module.conversation_service.create_conversation(
            user_id=context.user_id,
            title=(payload.title or "").strip() or None,
        ),
        ok_status=201,
    )


@router.get("/api/v1/conversations")
@router.get("/api/conversations")
def list_conversations(
    page: int = Query(default=1),
    page_size: int = Query(default=20),
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        conversation_service_module.conversation_service.list_conversations(
            user_id=context.user_id,
            page=page,
            page_size=page_size,
        ),
        ok_status=200,
    )


@router.get("/api/v1/conversations/{conversation_id}")
@router.get("/api/conversations/{conversation_id}")
def get_conversation_detail(conversation_id: int, context: AuthContext = Depends(require_auth_context)):
    return _respond(
        conversation_service_module.conversation_service.get_conversation_detail(
            user_id=context.user_id,
            conversation_id=conversation_id,
        ),
        ok_status=200,
    )


@router.put("/api/v1/conversations/{conversation_id}/title")
@router.put("/api/conversations/{conversation_id}/title")
def update_conversation_title(
    conversation_id: int,
    payload: UpdateConversationTitleRequest,
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        conversation_service_module.conversation_service.update_conversation_title(
            user_id=context.user_id,
            conversation_id=conversation_id,
            title=(payload.title or "").strip() or None,
        ),
        ok_status=200,
    )


@router.post("/api/v1/conversations/{conversation_id}/messages")
@router.post("/api/conversations/{conversation_id}/messages")
def add_conversation_message(
    conversation_id: int,
    payload: AddConversationMessageRequest,
    context: AuthContext = Depends(require_auth_context),
):
    message = payload.message
    return _respond(
        conversation_service_module.conversation_service.add_message(
            user_id=context.user_id,
            conversation_id=conversation_id,
            role=message.role,
            content=message.content,
            metadata=message.metadata,
        ),
        ok_status=201,
    )


@router.delete("/api/v1/conversations/{conversation_id}")
@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, context: AuthContext = Depends(require_auth_context)):
    return _respond(
        conversation_service_module.conversation_service.delete_conversation(
            user_id=context.user_id,
            conversation_id=conversation_id,
        ),
        ok_status=200,
    )


@router.get("/api/v1/conversations/{conversation_id}/files")
@router.get("/api/conversations/{conversation_id}/files")
def list_conversation_files(
    conversation_id: int,
    include_deleted: bool = Query(default=False),
    context: AuthContext = Depends(require_auth_context),
):
    return _respond(
        conversation_service_module.conversation_service.list_uploaded_files(
            user_id=context.user_id,
            conversation_id=conversation_id,
            include_deleted=include_deleted,
        ),
        ok_status=200,
    )


@router.get("/api/v1/conversations/{conversation_id}/files/{file_id}")
@router.get("/api/conversations/{conversation_id}/files/{file_id}")
def get_conversation_file(conversation_id: int, file_id: int, context: AuthContext = Depends(require_auth_context)):
    return _respond(
        conversation_service_module.conversation_service.get_uploaded_file(
            user_id=context.user_id,
            conversation_id=conversation_id,
            file_id=file_id,
        ),
        ok_status=200,
    )


@router.get("/api/v1/conversations/{conversation_id}/files/{file_id}/download")
@router.get("/api/conversations/{conversation_id}/files/{file_id}/download")
def download_conversation_file(
    conversation_id: int,
    file_id: int,
    context: AuthContext = Depends(require_auth_context),
    _quota: QuotaGrant | None = Depends(require_quota("file_view")),
):
    payload, status_code, download = conversation_service_module.conversation_service.resolve_uploaded_file_download(
        user_id=context.user_id,
        conversation_id=conversation_id,
        file_id=file_id,
    )
    if download is None:
        response = JSONResponse(status_code=status_code, content=payload)
        _enforce_quota_finalize(grant=_quota, result=response)
        return response

    mode = str(download.get("mode") or "")
    target = str(download.get("target") or "")
    file_name = str(download.get("file_name") or "file")
    if mode == "redirect":
        response = RedirectResponse(url=target, status_code=302)
        _enforce_quota_finalize(grant=_quota, result=response)
        return response

    background = None
    if mode == "proxy_file":
        background = BackgroundTask(lambda path: os.path.exists(path) and os.remove(path), target)
    response = FileResponse(path=target, filename=file_name, background=background)
    _enforce_quota_finalize(grant=_quota, result=response)
    return response


@router.delete("/api/v1/conversations/{conversation_id}/files/{file_id}")
@router.delete("/api/conversations/{conversation_id}/files/{file_id}")
def delete_conversation_file(conversation_id: int, file_id: int, context: AuthContext = Depends(require_auth_context)):
    return _respond(
        conversation_service_module.conversation_service.remove_uploaded_file(
            user_id=context.user_id,
            conversation_id=conversation_id,
            file_id=file_id,
        ),
        ok_status=200,
    )
