from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from server.errors import codes
from server.errors.core import APIError
from server.patent.original_service import parse_original_request
from server.runtime.request_context import get_trace_id

router = APIRouter()


def _get_original_service(request: Request) -> Any:
    service = getattr(request.app.state, "original_service", None)
    if service is None:
        raise APIError(
            code=codes.SERVICE_NOT_READY,
            message="patent original service is not ready",
            status_code=503,
            error="service_not_ready",
            retriable=True,
        )
    return service


def _ensure_original_route_compatibility_enabled(request: Request) -> None:
    raise APIError(
        code=codes.SERVICE_NOT_READY,
        message="patent local original route is disabled; use the gateway/public original route",
        status_code=503,
        error="service_not_ready",
        retriable=False,
    )


def _respond_from_original_result(*, result: dict[str, object], head_only: bool) -> Response:
    status_code = int(result.get("status_code") or 200)
    headers = {str(key): str(value) for key, value in dict(result.get("headers") or {}).items()}
    kind = str(result.get("kind") or "content").strip()
    if kind == "redirect":
        redirect_url = str(result.get("redirect_url") or "").strip()
        if not redirect_url:
            raise APIError(
                code=codes.INTERNAL_ERROR,
                message="redirect result missing redirect_url",
                status_code=500,
                error="internal_error",
                retriable=False,
            )
        return RedirectResponse(url=redirect_url, status_code=status_code, headers=headers)

    payload = result.get("payload")
    if isinstance(payload, dict):
        response = JSONResponse(content=payload, status_code=status_code, headers=headers)
    elif isinstance(payload, str):
        content_type = str(headers.get("Content-Type") or headers.get("content-type") or "text/plain; charset=utf-8")
        if content_type.startswith("text/html"):
            response = HTMLResponse(content=payload, status_code=status_code, headers=headers)
        else:
            response = PlainTextResponse(content=payload, status_code=status_code, headers=headers)
    else:
        response = Response(content=b"", status_code=status_code, headers=headers)

    if head_only:
        response.body = b""
    return response


def _parse_original_request_or_raise(
    *,
    canonical_patent_id: str,
    section: str | None,
    claim_number: str | None,
    paragraph_id: str | None,
    response_format: str | None,
):
    try:
        return parse_original_request(
            canonical_patent_id=canonical_patent_id,
            section=section,
            claim_number=claim_number,
            paragraph_id=paragraph_id,
            response_format=response_format,
        )
    except ValueError as exc:
        raise APIError(
            code=codes.INVALID_REQUEST,
            message=str(exc),
            status_code=400,
            error="invalid_request",
            retriable=False,
        ) from exc


def _serve_original(
    request: Request,
    *,
    canonical_patent_id: str,
    section: str | None,
    claim_number: str | None,
    paragraph_id: str | None,
    response_format: str | None,
    head_only: bool,
) -> Response:
    _ensure_original_route_compatibility_enabled(request)
    trace_id = str(get_trace_id() or "")
    original_request = _parse_original_request_or_raise(
        canonical_patent_id=canonical_patent_id,
        section=section,
        claim_number=claim_number,
        paragraph_id=paragraph_id,
        response_format=response_format,
    )
    result = _get_original_service(request).get_original_view(
        canonical_patent_id=original_request.canonical_patent_id,
        section=original_request.section,
        claim_number=original_request.claim_number,
        paragraph_id=original_request.paragraph_id,
        response_format=original_request.response_format,
        head_only=head_only,
        trace_id=trace_id,
    )
    return _respond_from_original_result(result=dict(result or {}), head_only=head_only)


@router.api_route("/api/patent/original/{canonical_patent_id}", methods=["GET", "HEAD"])
@router.api_route("/api/v1/patent/original/{canonical_patent_id}", methods=["GET", "HEAD"])
async def patent_original(
    request: Request,
    canonical_patent_id: str,
    section: str | None = Query(default=None),
    claim_number: str | None = Query(default=None),
    paragraph_id: str | None = Query(default=None),
    format: str | None = Query(default=None),
):
    return _serve_original(
        request,
        canonical_patent_id=canonical_patent_id,
        section=section,
        claim_number=claim_number,
        paragraph_id=paragraph_id,
        response_format=format,
        head_only=request.method.upper() == "HEAD",
    )
