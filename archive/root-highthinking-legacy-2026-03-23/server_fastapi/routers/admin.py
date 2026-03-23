"""FastAPI admin user routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from server.services.admin_users_import_service import admin_users_import_service
from server.services.admin_users_service import admin_users_service
from server_fastapi.admin_schemas import (
    BatchChangeUserTypeRequest,
    BatchDeleteUsersRequest,
    UserCreateRequest,
    UserPasswordResetRequest,
    UserStatusUpdateRequest,
    UserTypeUpdateRequest,
)
from server_fastapi.auth.deps import AuthContext, require_admin_context

router = APIRouter(prefix="/api/admin")


def _respond(result: dict, *, ok_status: int):
    return JSONResponse(
        status_code=admin_users_service.status_code_for(result, ok_status=ok_status),
        content=jsonable_encoder(result),
    )


@router.get("/users")
async def list_users(
    page: int = Query(default=1),
    page_size: int = Query(default=10),
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(admin_users_service.list_users(page=page, page_size=page_size), ok_status=200)


@router.post("/users")
async def create_user(payload: UserCreateRequest, _context: AuthContext = Depends(require_admin_context)):
    return _respond(
        admin_users_service.create_user(username=payload.username, password=payload.password, user_type=str(payload.user_type)),
        ok_status=201,
    )


@router.put("/users/{user_id}/password")
async def reset_user_password(
    user_id: int,
    payload: UserPasswordResetRequest,
    context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        admin_users_service.reset_password(
            target_user_id=user_id,
            actor_user_id=context.user_id,
            new_password=payload.new_password,
        ),
        ok_status=200,
    )


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    payload: UserStatusUpdateRequest,
    context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        admin_users_service.update_status(
            target_user_id=user_id,
            actor_user_id=context.user_id,
            status=payload.status,
        ),
        ok_status=200,
    )


@router.put("/users/{user_id}/type")
async def update_user_type(
    user_id: int,
    payload: UserTypeUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        admin_users_service.update_type(target_user_id=user_id, target_type_raw=payload.user_type),
        ok_status=200,
    )


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, context: AuthContext = Depends(require_admin_context)):
    return _respond(
        admin_users_service.delete_user(target_user_id=user_id, actor_user_id=context.user_id),
        ok_status=200,
    )


@router.post("/users/batch-delete")
async def batch_delete_users(payload: BatchDeleteUsersRequest, context: AuthContext = Depends(require_admin_context)):
    return _respond(
        admin_users_service.batch_delete_users(target_user_ids=payload.user_ids, actor_user_id=context.user_id),
        ok_status=200,
    )


@router.post("/users/batch-type")
async def batch_change_user_type(
    payload: BatchChangeUserTypeRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        admin_users_service.batch_change_user_type(target_user_ids=payload.user_ids, target_type_raw=payload.user_type),
        ok_status=200,
    )


@router.post("/users/batch-import")
async def batch_import_users(
    request: Request,
    file: UploadFile = File(...),
    context: AuthContext = Depends(require_admin_context),
):
    _ = request
    content = await file.read()
    return _respond(
        admin_users_import_service.import_users(
            file_bytes=content,
            filename=str(file.filename or ""),
            actor_user_id=context.user_id,
        ),
        ok_status=200,
    )


@router.get("/users/import-template")
async def download_import_template(
    format: str = Query(default="xlsx"),
    _context: AuthContext = Depends(require_admin_context),
):
    result = admin_users_import_service.template_response(fmt=format)
    if not isinstance(result, dict):
        return result
    return _respond(result, ok_status=200)
