from __future__ import annotations

from email.parser import BytesParser
from email.policy import default

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from app.core.deps import AuthContext
from app.modules.auth.deps import require_admin_context
from app.modules.personnel.import_service import personnel_import_service
from app.modules.personnel.schemas import (
    PersonnelBatchDepartmentUpdateRequest,
    PersonnelBatchDeleteRequest,
    PersonnelBatchForceDeleteRequest,
    PersonnelBatchStatusUpdateRequest,
    PersonnelCreateRequest,
    PersonnelForceDeleteRequest,
    PersonnelStatusUpdateRequest,
    PersonnelUpdateRequest,
)
from app.modules.personnel.repository import REMARKS_UNSET
from app.modules.personnel.service import personnel_service


router = APIRouter(prefix="/api/admin/personnel", tags=["personnel"])


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=personnel_service.status_code_for(result, ok_status=ok_status),
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


@router.get("")
def list_personnel(
    page: int = Query(default=1),
    page_size: int = Query(default=20),
    employee_no: str = Query(default=""),
    full_name: str = Query(default=""),
    status: str = Query(default=""),
    keyword: str = Query(default=""),
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.list_personnel(
            page=page,
            page_size=page_size,
            employee_no=employee_no,
            full_name=full_name,
            status=status,
            keyword=keyword,
        ),
        ok_status=200,
    )


@router.post("")
def create_personnel(
    payload: PersonnelCreateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.create_personnel(
            employee_no=payload.employee_no,
            full_name=payload.full_name,
            verification_code=payload.verification_code,
            primary_department_id=payload.primary_department_id,
            secondary_department_id=payload.secondary_department_id,
            tertiary_department_id=payload.tertiary_department_id,
            status=payload.status,
            remarks=payload.remarks,
        ),
        ok_status=201,
    )


@router.post("/batch-delete")
def batch_delete_personnel(
    payload: PersonnelBatchDeleteRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.batch_delete_personnel(personnel_ids=payload.personnel_ids),
        ok_status=200,
    )


@router.post("/batch-status")
def batch_update_personnel_status(
    payload: PersonnelBatchStatusUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.batch_update_personnel_status(
            personnel_ids=payload.personnel_ids,
            status=payload.status,
        ),
        ok_status=200,
    )


@router.post("/batch-department")
def batch_update_personnel_department(
    payload: PersonnelBatchDepartmentUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.batch_update_personnel_department(
            personnel_ids=payload.personnel_ids,
            primary_department_id=payload.primary_department_id,
            secondary_department_id=payload.secondary_department_id,
            tertiary_department_id=payload.tertiary_department_id,
        ),
        ok_status=200,
    )


@router.post("/batch-force-delete")
def batch_force_delete_personnel(
    payload: PersonnelBatchForceDeleteRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.batch_force_delete_personnel(
            personnel_ids=payload.personnel_ids,
            actor_user_id=_context.user_id,
            admin_password=payload.admin_password,
        ),
        ok_status=200,
    )


@router.put("/{personnel_id}")
def update_personnel(
    personnel_id: int,
    payload: PersonnelUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    remarks_fields = getattr(payload, "model_fields_set", None)
    if remarks_fields is None:
        remarks_fields = getattr(payload, "__fields_set__", set())
    return _respond(
        personnel_service.update_personnel(
            personnel_id=personnel_id,
            full_name=payload.full_name,
            primary_department_id=payload.primary_department_id,
            secondary_department_id=payload.secondary_department_id,
            tertiary_department_id=payload.tertiary_department_id,
            status=payload.status,
            remarks=payload.remarks if "remarks" in remarks_fields else REMARKS_UNSET,
            verification_code=payload.verification_code,
        ),
        ok_status=200,
    )


@router.put("/{personnel_id}/status")
def update_personnel_status(
    personnel_id: int,
    payload: PersonnelStatusUpdateRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.update_personnel_status(
            personnel_id=personnel_id,
            status=payload.status,
        ),
        ok_status=200,
    )


@router.delete("/{personnel_id}")
def delete_personnel(
    personnel_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.delete_personnel(personnel_id=personnel_id),
        ok_status=200,
    )


@router.post("/{personnel_id}/force-delete")
def force_delete_personnel(
    personnel_id: int,
    payload: PersonnelForceDeleteRequest,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.force_delete_personnel(
            personnel_id=personnel_id,
            actor_user_id=_context.user_id,
            admin_password=payload.admin_password,
        ),
        ok_status=200,
    )


@router.get("/{personnel_id}/bindings")
def get_personnel_bindings(
    personnel_id: int,
    _context: AuthContext = Depends(require_admin_context),
):
    return _respond(
        personnel_service.list_bindings(personnel_id=personnel_id),
        ok_status=200,
    )


@router.post("/batch-import")
async def batch_import_personnel(
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
        personnel_import_service.import_personnel(file_bytes=content, filename=filename),
        ok_status=200,
    )


@router.get("/import-template")
def download_import_template(
    format: str = Query(default="xlsx"),
    _context: AuthContext = Depends(require_admin_context),
):
    result = personnel_import_service.template_response(fmt=format)
    if isinstance(result, Response):
        return result
    return _respond(result, ok_status=200)
