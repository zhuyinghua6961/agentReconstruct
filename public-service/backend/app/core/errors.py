from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass
class AppError(Exception):
    message: str
    code: str = "APP_ERROR"
    status_code: int = 400
    details: dict[str, Any] | None = None


class PermissionDeniedError(AppError):
    def __init__(self, message: str = "permission_denied"):
        super().__init__(message=message, code="PERMISSION_DENIED", status_code=403)


class DatabaseUnavailableError(AppError):
    def __init__(self, message: str = "db_unavailable"):
        super().__init__(message=message, code="DB_UNAVAILABLE", status_code=503)


def _payload(exc: AppError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "error": exc.message,
        "code": exc.code,
    }
    if exc.details:
        payload["details"] = exc.details
    extra_payload = getattr(exc, "extra_payload", None)
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)
    return payload


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_payload(exc))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": "validation_error",
                "code": "VALIDATION_ERROR",
                "details": {"errors": exc.errors()},
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "internal_server_error",
                "code": "INTERNAL_ERROR",
                "details": {"type": type(exc).__name__},
            },
        )
