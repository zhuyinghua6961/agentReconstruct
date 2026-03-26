from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from server.errors import codes
from server.errors.core import APIError, build_error_payload, build_internal_error_payload



def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _handle_api_error(_request, exc: APIError):
        payload = build_error_payload(
            code=exc.code,
            message=exc.message,
            error=exc.error,
            retriable=exc.retriable,
            extra=exc.extra,
        )
        return JSONResponse(content=payload, status_code=int(exc.status_code))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_request, exc: RequestValidationError):
        detail = "; ".join(err.get("msg", "invalid request") for err in exc.errors()) or "invalid request"
        payload = build_error_payload(
            code=codes.INVALID_REQUEST,
            message=detail,
            error="invalid_request",
            retriable=False,
        )
        return JSONResponse(content=payload, status_code=400)

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(_request, exc: Exception):
        logger = getattr(app, "logger", logging.getLogger("patent.server_fastapi"))
        logger.exception("unexpected error: %s", exc)
        return JSONResponse(content=build_internal_error_payload(), status_code=500)
