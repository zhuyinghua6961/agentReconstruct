from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.deps import AuthContext
from app.modules.auth.deps import require_admin_context
from app.modules.departments.schemas import (
    DepartmentStatusUpdateRequest,
    PrimaryDepartmentCreateRequest,
    PrimaryDepartmentRenameRequest,
    SecondaryDepartmentCreateRequest,
    SecondaryDepartmentRenameRequest,
)
from app.modules.departments.service import department_service


router = APIRouter(prefix="/api/admin/departments", tags=["departments"])


def _respond(result: dict, *, ok_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=department_service.status_code_for(result, ok_status=ok_status),
        content=jsonable_encoder(result),
    )


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
