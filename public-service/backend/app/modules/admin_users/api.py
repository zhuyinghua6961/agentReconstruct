from __future__ import annotations

from email.parser import BytesParser
from email.policy import default

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from app.core.deps import AuthContext
from app.modules.admin_users.import_service import admin_users_import_service
from app.modules.admin_users.schemas import (
    BatchChangeUserTypeRequest,
    BatchDeleteUsersRequest,
    UserCreateRequest,
    UserDepartmentUpdateRequest,
    UserPasswordResetRequest,
    UserStatusUpdateRequest,
    UserTypeUpdateRequest,
    UserUsernameUpdateRequest,
)
from app.modules.admin_users.service import admin_users_service
from app.modules.auth.deps import require_admin_context


router = APIRouter(prefix="/api/admin", tags=["admin-users"])


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=admin_users_service.status_code_for(result, ok_status=ok_status),
        content=jsonable_encoder(result),
    )


def _extract_file_from_multipart(*, body: bytes, content_type: str) -> tuple[str, bytes] | dict:
    raw_content_type = str(content_type or "").strip()
    if "multipart/form-data" not in raw_content_type.lower():
        return {"success": False, "error": "未提供文件", "code": "FILE_MISSING"}

    message = BytesParser(policy=default).parsebytes(
        (
            f"Content-Type: {raw_content_type}\r\n"
            "MIME-Version: 1.0\r\n"
            "\r\n"
        ).encode("utf-8")
        + body
    )
    if not message.is_multipart():
        return {"success": False, "error": "未提供文件", "code": "FILE_MISSING"}

    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        if part.get_param("name", header="content-disposition") != "file":
            continue
        filename = str(part.get_filename() or "")
        payload = part.get_payload(decode=True) or b""
        return filename, payload

    return {"success": False, "error": "未提供文件", "code": "FILE_MISSING"}


@router.get("/users")
def list_users(
    page: int = Query(default=1),
    page_size: int = Query(default=10),
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(admin_users_service.list_users(page=page, page_size=page_size), ok_status=200)


@router.post("/users")
def create_user(payload: UserCreateRequest, _context: AuthContext = Depends(require_admin_context)):
    return _respond(
        admin_users_service.create_user(
            username=payload.username,
            password=payload.password,
            user_type=str(payload.user_type),
            primary_department_id=payload.primary_department_id,
            secondary_department_id=payload.secondary_department_id,
        ),
        ok_status=201,
    )


@router.put("/users/{user_id}/department")
def update_user_department(
    user_id: int,
    payload: UserDepartmentUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        admin_users_service.update_department(
            target_user_id=user_id,
            primary_department_id=payload.primary_department_id,
            secondary_department_id=payload.secondary_department_id,
        ),
        ok_status=200,
    )


@router.put("/users/{user_id}/username")
def update_user_username(
    user_id: int,
    payload: UserUsernameUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        admin_users_service.update_username(
            target_user_id=user_id,
            username=payload.username,
        ),
        ok_status=200,
    )


@router.put("/users/{user_id}/password")
def reset_user_password(
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


@router.get("/users/{user_id}/password")
def get_user_password_hint(user_id: int, _context: AuthContext = Depends(require_admin_context)):
    return _respond(admin_users_service.get_password_hint(target_user_id=user_id), ok_status=200)


@router.put("/users/{user_id}/status")
def update_user_status(
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
def update_user_type(
    user_id: int,
    payload: UserTypeUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        admin_users_service.update_type(
            target_user_id=user_id,
            target_type_raw=payload.user_type,
        ),
        ok_status=200,
    )


@router.delete("/users/{user_id}")
def delete_user(user_id: int, context: AuthContext = Depends(require_admin_context)):
    return _respond(
        admin_users_service.delete_user(target_user_id=user_id, actor_user_id=context.user_id),
        ok_status=200,
    )


@router.post("/users/batch-delete")
def batch_delete_users(payload: BatchDeleteUsersRequest, context: AuthContext = Depends(require_admin_context)):
    return _respond(
        admin_users_service.batch_delete_users(target_user_ids=payload.user_ids, actor_user_id=context.user_id),
        ok_status=200,
    )


@router.post("/users/batch-type")
def batch_change_user_type(
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
    context: AuthContext = Depends(require_admin_context),
):
    parsed = _extract_file_from_multipart(
        body=await request.body(),
        content_type=request.headers.get("content-type", ""),
    )
    if isinstance(parsed, dict):
        return _respond(parsed, ok_status=200)

    filename, content = parsed
    return _respond(
        admin_users_import_service.import_users(
            file_bytes=content,
            filename=filename,
            actor_user_id=context.user_id,
        ),
        ok_status=200,
    )


@router.get("/users/import-template")
def download_import_template(
    format: str = Query(default="xlsx"),
    _context: AuthContext = Depends(require_admin_context),
):
    result = admin_users_import_service.template_response(fmt=format)
    if isinstance(result, Response):
        return result
    return _respond(result, ok_status=200)
