from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def not_implemented_response(*, module: str, request: Request, detail: str = "") -> JSONResponse:
    payload: dict[str, Any] = {
        "success": False,
        "error": "not_implemented_yet",
        "code": "NOT_IMPLEMENTED_YET",
        "module": module,
        "method": request.method,
        "path": request.url.path,
        "phase": "skeleton",
    }
    if detail:
        payload["detail"] = detail
    return JSONResponse(status_code=501, content=payload)
