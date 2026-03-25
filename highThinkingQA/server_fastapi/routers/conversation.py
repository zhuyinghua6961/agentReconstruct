"""FastAPI conversation CRUD routes."""

# Deprecated: this router is no longer registered in the current architecture.
# Conversation and file HTTP APIs are owned by public-service behind gateway public proxy.


from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask

from server.errors.core import raise_invalid_request
from server.services.conversation.conversation_service import conversation_service
from server.storage.file_delivery_service import resolve_uploaded_file_delivery
from server_fastapi.auth.deps import AuthContext, require_auth_context
from server_fastapi.http import read_json_payload, to_bool

router = APIRouter()


def _status_from_code(code: str) -> int:
    mapping = {
        "NOT_FOUND": 404,
        "VALIDATION_ERROR": 400,
        "DB_UNAVAILABLE": 503,
    }
    return int(mapping.get(str(code or ""), 500))


def _json_result(result: dict, *, default_status: int = 200):
    if result.get("success"):
        return JSONResponse(content=jsonable_encoder(result), status_code=default_status)
    status = _status_from_code(str(result.get("code") or ""))
    return JSONResponse(content=jsonable_encoder(result), status_code=status)


def _cleanup_file(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass


@router.post("/api/v1/conversations")
@router.post("/api/conversations")
async def create_conversation(
    request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    payload = await read_json_payload(request)
    payload = payload if isinstance(payload, dict) else {}
    title = str(payload.get("title") or "").strip() or None
    result = conversation_service.create_conversation(user_id=int(context.user_id), title=title)
    return _json_result(result, default_status=200)


@router.get("/api/v1/conversations")
@router.get("/api/conversations")
async def list_conversations(request: Request, context: AuthContext = Depends(require_auth_context)):
    try:
        page = int(request.query_params.get("page", "1"))
        page_size = int(request.query_params.get("page_size", "20"))
    except Exception:
        raise_invalid_request("page/page_size must be integer")

    result = conversation_service.list_conversations(user_id=int(context.user_id), page=page, page_size=page_size)
    return _json_result(result, default_status=200)


@router.get("/api/v1/conversations/{conversation_id}")
@router.get("/api/conversations/{conversation_id}")
async def get_conversation_detail(
    conversation_id: int,
    _request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    result = conversation_service.get_conversation_detail(
        user_id=int(context.user_id),
        conversation_id=int(conversation_id),
    )
    return _json_result(result, default_status=200)


@router.post("/api/v1/conversations/{conversation_id}/messages")
@router.post("/api/conversations/{conversation_id}/messages")
async def add_conversation_message(
    conversation_id: int,
    request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    payload = await read_json_payload(request)
    payload = payload if isinstance(payload, dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    role = str(message.get("role") or "").strip().lower()
    content = str(message.get("content") or "")
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    result = conversation_service.add_message(
        user_id=int(context.user_id),
        conversation_id=int(conversation_id),
        role=role,
        content=content,
        metadata=metadata,
    )
    return _json_result(result, default_status=200)


@router.delete("/api/v1/conversations/{conversation_id}")
@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    _request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    result = conversation_service.delete_conversation(
        user_id=int(context.user_id),
        conversation_id=int(conversation_id),
    )
    return _json_result(result, default_status=200)


@router.get("/api/v1/conversations/{conversation_id}/files")
@router.get("/api/conversations/{conversation_id}/files")
async def list_conversation_files(
    conversation_id: int,
    request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    include_deleted = to_bool(request.query_params.get("include_deleted"), default=False)
    result = conversation_service.list_uploaded_files(
        user_id=int(context.user_id),
        conversation_id=int(conversation_id),
        include_deleted=include_deleted,
    )
    return _json_result(result, default_status=200)


@router.get("/api/v1/conversations/{conversation_id}/files/{file_id}")
@router.get("/api/conversations/{conversation_id}/files/{file_id}")
async def get_conversation_file(
    conversation_id: int,
    file_id: int,
    _request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    result = conversation_service.get_uploaded_file(
        user_id=int(context.user_id),
        conversation_id=int(conversation_id),
        file_id=int(file_id),
    )
    return _json_result(result, default_status=200)


@router.get("/api/v1/conversations/{conversation_id}/files/{file_id}/download")
@router.get("/api/conversations/{conversation_id}/files/{file_id}/download")
async def download_conversation_file(
    conversation_id: int,
    file_id: int,
    request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    result = conversation_service.get_uploaded_file(
        user_id=int(context.user_id),
        conversation_id=int(conversation_id),
        file_id=int(file_id),
    )
    if not result.get("success"):
        return _json_result(result, default_status=200)

    file_row = result.get("data") if isinstance(result.get("data"), dict) else {}
    plan = resolve_uploaded_file_delivery(file_row=file_row or {}, logger=request.app.logger)
    if plan is None:
        return JSONResponse(
            content={"success": False, "error": "file_unavailable", "code": "FILE_UNAVAILABLE"},
            status_code=404,
        )
    if plan.kind == "redirect" and plan.redirect_url:
        return RedirectResponse(url=plan.redirect_url, status_code=302)
    if plan.kind != "file" or not plan.local_path:
        return JSONResponse(
            content={"success": False, "error": "file_unavailable", "code": "FILE_UNAVAILABLE"},
            status_code=404,
        )

    background = BackgroundTask(_cleanup_file, plan.cleanup_path) if plan.cleanup_path else None
    return FileResponse(
        path=plan.local_path,
        filename=plan.download_name,
        background=background,
    )


@router.delete("/api/v1/conversations/{conversation_id}/files/{file_id}")
@router.delete("/api/conversations/{conversation_id}/files/{file_id}")
async def delete_conversation_file(
    conversation_id: int,
    file_id: int,
    _request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    result = conversation_service.remove_uploaded_file(
        user_id=int(context.user_id),
        conversation_id=int(conversation_id),
        file_id=int(file_id),
    )
    return _json_result(result, default_status=200)
