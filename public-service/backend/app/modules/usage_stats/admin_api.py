from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from app.core.deps import AuthContext
from app.modules.auth.deps import require_admin_context
from app.modules.usage_stats import service as usage_stats_service_module


router = APIRouter(prefix="/api/admin", tags=["admin-usage-stats"])


def _respond(result: dict, *, ok_status: int = 200) -> JSONResponse:
    status = ok_status
    if not result.get("success"):
        code = str(result.get("code") or "")
        if code == "VALIDATION_ERROR":
            status = 400
        elif code == "DB_UNAVAILABLE":
            status = 503
        else:
            status = 500
    return JSONResponse(status_code=status, content=jsonable_encoder(result))


def _parse_date(value: str, *, field_name: str) -> date | JSONResponse:
    text = str(value or "").strip()
    if not text:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": f"{field_name}_required", "code": "VALIDATION_ERROR"},
        )
    try:
        return date.fromisoformat(text)
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": f"invalid_{field_name}", "code": "VALIDATION_ERROR"},
        )


def _optional_positive_int(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


@router.get("/usage-stats")
def get_usage_stats(
    from_date: str = Query(alias="from"),
    to_date: str = Query(alias="to"),
    page: int = Query(default=1),
    page_size: int = Query(default=20),
    keyword: str = Query(default=""),
    primary_department_id: int | None = Query(default=None),
    secondary_department_id: int | None = Query(default=None),
    tertiary_department_id: int | None = Query(default=None),
    sort_by: str = Query(default="last_active_at"),
    sort_order: str = Query(default="desc"),
    _context: AuthContext = Depends(require_admin_context),
):
    parsed_from = _parse_date(from_date, field_name="from")
    if isinstance(parsed_from, JSONResponse):
        return parsed_from
    parsed_to = _parse_date(to_date, field_name="to")
    if isinstance(parsed_to, JSONResponse):
        return parsed_to
    return _respond(
        usage_stats_service_module.usage_stats_service.list_usage_stats(
            stat_from=parsed_from,
            stat_to=parsed_to,
            page=page,
            page_size=page_size,
            keyword=keyword,
            primary_department_id=_optional_positive_int(primary_department_id),
            secondary_department_id=_optional_positive_int(secondary_department_id),
            tertiary_department_id=_optional_positive_int(tertiary_department_id),
            sort_by=sort_by,
            sort_order=sort_order,
        )
    )


@router.get("/usage-stats/export")
def export_usage_stats(
    from_date: str = Query(alias="from"),
    to_date: str = Query(alias="to"),
    keyword: str = Query(default=""),
    primary_department_id: int | None = Query(default=None),
    secondary_department_id: int | None = Query(default=None),
    tertiary_department_id: int | None = Query(default=None),
    sort_by: str = Query(default="last_active_at"),
    sort_order: str = Query(default="desc"),
    format: str = Query(default="xlsx", alias="format"),
    _context: AuthContext = Depends(require_admin_context),
):
    parsed_from = _parse_date(from_date, field_name="from")
    if isinstance(parsed_from, JSONResponse):
        return parsed_from
    parsed_to = _parse_date(to_date, field_name="to")
    if isinstance(parsed_to, JSONResponse):
        return parsed_to
    result = usage_stats_service_module.usage_stats_service.export_usage_stats(
        stat_from=parsed_from,
        stat_to=parsed_to,
        fmt=format,
        keyword=keyword,
        primary_department_id=_optional_positive_int(primary_department_id),
        secondary_department_id=_optional_positive_int(secondary_department_id),
        tertiary_department_id=_optional_positive_int(tertiary_department_id),
        sort_by=sort_by,
        sort_order=sort_order,
    )
    if isinstance(result, Response):
        return result
    return _respond(result)
