from __future__ import annotations

from email.parser import BytesParser
from email.policy import default

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from app.core.deps import AuthContext
from app.modules.auth.deps import require_admin_context
from app.modules.departments.import_service import department_import_service
from app.modules.departments.schemas import (
    DepartmentBatchDeleteItem,
    DepartmentBatchDeleteRequest,
    DepartmentBatchForceDeleteRequest,
    DepartmentBatchStatusUpdateRequest,
    DepartmentForceDeleteRequest,
    PrimaryDepartmentCreateRequest,
    PrimaryDepartmentRenameRequest,
    DepartmentStatusUpdateRequest,
    SecondaryDepartmentCreateRequest,
    SecondaryDepartmentRenameRequest,
    TertiaryDepartmentCreateRequest,
    TertiaryDepartmentRenameRequest,
)
from app.modules.departments.service import department_service


router = APIRouter(prefix="/api/admin/departments", tags=["departments"])


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=department_service.status_code_for(result, ok_status=ok_status),
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


@router.get("/tree")
def get_tree(_context: AuthContext = Depends(require_admin_context)):
    return _respond(department_service.get_admin_tree(), ok_status=200)


@router.post("/primary")
def create_primary(payload: PrimaryDepartmentCreateRequest, _context: AuthContext = Depends(require_admin_context)):
    return _respond(department_service.create_primary(name=payload.name), ok_status=201)


@router.put("/primary/{primary_id}")
def rename_primary(
    primary_id: int,
    payload: PrimaryDepartmentRenameRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(department_service.rename_primary(primary_id=primary_id, name=payload.name), ok_status=200)


@router.delete("/primary/{primary_id}")
def delete_primary(
    primary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(department_service.delete_primary(primary_id=primary_id), ok_status=200)


@router.put("/primary/{primary_id}/status")
def update_primary_status(
    primary_id: int,
    payload: DepartmentStatusUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.update_primary_status(primary_id=primary_id, status=payload.status),
        ok_status=200,
    )


@router.post("/batch-delete")
def batch_delete_departments(
    payload: DepartmentBatchDeleteRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.batch_delete_departments(
            items=[item.model_dump() if hasattr(item, "model_dump") else item.dict() for item in payload.items]
        ),
        ok_status=200,
    )


@router.post("/batch-status")
def batch_update_department_status(
    payload: DepartmentBatchStatusUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.batch_update_department_status(
            items=[item.model_dump() if hasattr(item, "model_dump") else item.dict() for item in payload.items],
            status=payload.status,
        ),
        ok_status=200,
    )


@router.post("/batch-force-delete")
def batch_force_delete_departments(
    payload: DepartmentBatchForceDeleteRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.batch_force_delete_departments(
            items=[item.model_dump() if hasattr(item, "model_dump") else item.dict() for item in payload.items],
            actor_user_id=_context.user_id,
            admin_password=payload.admin_password,
        ),
        ok_status=200,
    )


@router.post("/secondary")
def create_secondary(payload: SecondaryDepartmentCreateRequest, _context: AuthContext = Depends(require_admin_context)):
    return _respond(
        department_service.create_secondary(
            primary_department_id=payload.primary_department_id,
            name=payload.name,
        ),
        ok_status=201,
    )


@router.put("/secondary/{secondary_id}")
def rename_secondary(
    secondary_id: int,
    payload: SecondaryDepartmentRenameRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(department_service.rename_secondary(secondary_id=secondary_id, name=payload.name), ok_status=200)


@router.delete("/secondary/{secondary_id}")
def delete_secondary(
    secondary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(department_service.delete_secondary(secondary_id=secondary_id), ok_status=200)


@router.put("/secondary/{secondary_id}/status")
def update_secondary_status(
    secondary_id: int,
    payload: DepartmentStatusUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.update_secondary_status(secondary_id=secondary_id, status=payload.status),
        ok_status=200,
    )


@router.get("/primary/{primary_id}/direct-users")
def get_primary_direct_users(
    primary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.list_primary_direct_users(primary_id=primary_id),
        ok_status=200,
    )


@router.get("/secondary/{secondary_id}/users")
def get_secondary_users(
    secondary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.list_secondary_users(secondary_id=secondary_id),
        ok_status=200,
    )


@router.get("/secondary/{secondary_id}/direct-users")
def get_secondary_direct_users(
    secondary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.list_secondary_direct_users(secondary_id=secondary_id),
        ok_status=200,
    )


@router.get("/secondary/{secondary_id}/legacy-users")
def get_secondary_legacy_users(
    secondary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.list_secondary_legacy_users(secondary_id=secondary_id),
        ok_status=200,
    )


@router.post("/tertiary")
def create_tertiary(payload: TertiaryDepartmentCreateRequest, _context: AuthContext = Depends(require_admin_context)):
    return _respond(
        department_service.create_tertiary(
            secondary_department_id=payload.secondary_department_id,
            name=payload.name,
        ),
        ok_status=201,
    )


@router.put("/tertiary/{tertiary_id}")
def rename_tertiary(
    tertiary_id: int,
    payload: TertiaryDepartmentRenameRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(department_service.rename_tertiary(tertiary_id=tertiary_id, name=payload.name), ok_status=200)


@router.delete("/tertiary/{tertiary_id}")
def delete_tertiary(
    tertiary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(department_service.delete_tertiary(tertiary_id=tertiary_id), ok_status=200)


@router.put("/tertiary/{tertiary_id}/status")
def update_tertiary_status(
    tertiary_id: int,
    payload: DepartmentStatusUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.update_tertiary_status(tertiary_id=tertiary_id, status=payload.status),
        ok_status=200,
    )


@router.get("/tertiary/{tertiary_id}/users")
def get_tertiary_users(
    tertiary_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.list_tertiary_users(tertiary_id=tertiary_id),
        ok_status=200,
    )


@router.post("/{level}/{department_id}/force-delete")
def force_delete_department(
    level: str,
    department_id: int,
    payload: DepartmentForceDeleteRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        department_service.force_delete_department(
            level=level,
            department_id=department_id,
            actor_user_id=_context.user_id,
            admin_password=payload.admin_password,
        ),
        ok_status=200,
    )


@router.post("/batch-import")
async def batch_import_departments(
    request: Request,
    _context: AuthContext = Depends(require_admin_context),
):
    parsed = _extract_file_from_multipart(
        body=await request.body(),
        content_type=request.headers.get("content-type", ""),
    )
    if isinstance(parsed, dict):
        return _respond(parsed, ok_status=200)

    filename, content = parsed
    return _respond(
        department_import_service.import_departments(
            file_bytes=content,
            filename=filename,
        ),
        ok_status=200,
    )


@router.get("/import-template")
def download_import_template(
    format: str = Query(default="xlsx"),
    _context: AuthContext = Depends(require_admin_context),
):
    result = department_import_service.template_response(fmt=format)
    if isinstance(result, Response):
        return result
    return _respond(result, ok_status=200)
