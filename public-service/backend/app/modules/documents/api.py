from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from app.core.deps import AuthContext
from app.core.errors import AppError
from app.modules.auth.deps import get_optional_auth_context, require_auth_context
from app.modules.documents.schemas import ReferencePreviewRequest, TranslateRequest
from app.modules.documents.service import documents_service
from app.modules.quota.deps import finalize_quota, precheck_quota, require_quota
from app.modules.quota.service import QuotaGrant


router = APIRouter(tags=["documents"])


def _json(payload: dict, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def _runtime_from_request(request: Request):
    return getattr(request.app.state, "runtime", None)


def _agent_from_request(request: Request):
    runtime = _runtime_from_request(request)
    return getattr(runtime, "agent", None) if runtime is not None else None


def _logger(request: Request) -> logging.Logger:
    return getattr(request.app, "logger", None) or logging.getLogger(__name__)


def _patent_original_response(*, result: dict[str, object], head_only: bool) -> Response:
    status_code = int(result.get("status_code") or 200)
    headers = {str(key): str(value) for key, value in dict(result.get("headers") or {}).items()}
    media_type = str(result.get("media_type") or "application/json")
    body_iter = result.get("body_iter")
    body = result.get("body")
    if head_only:
        return Response(status_code=status_code, headers=headers, media_type=media_type)
    if body_iter is not None:
        return StreamingResponse(body_iter, status_code=status_code, headers=headers, media_type=media_type)
    if isinstance(body, (bytes, bytearray)):
        return Response(content=bytes(body), status_code=status_code, headers=headers, media_type=media_type)
    if isinstance(body, str):
        return Response(content=body, status_code=status_code, headers=headers, media_type=media_type)
    return JSONResponse(status_code=status_code, content=dict(body or {}), headers=headers)


def _enforce_quota_finalize(*, grant: QuotaGrant | None, result: Response | dict[str, object]) -> None:
    finalize_result = finalize_quota(grant, result=result)
    if isinstance(finalize_result, dict) and finalize_result.get("success") is False:
        raise AppError(
            message=str(finalize_result.get("error") or "quota_finalize_failed"),
            code=str(finalize_result.get("code") or "DB_UNAVAILABLE"),
            status_code=503,
        )


def _attach_quota_warning(result: Response, *, warning: str) -> Response:
    if isinstance(result, JSONResponse):
        try:
            payload = json.loads(result.body.decode("utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            payload["quota_counted"] = False
            payload["quota_warning"] = warning
            return JSONResponse(status_code=result.status_code, content=payload, headers=dict(result.headers))
    result.headers["x-quota-counted"] = "false"
    result.headers["x-quota-warning"] = warning
    return result


def _finalize_quota_softly(
    *,
    grant: QuotaGrant | None,
    result: Response,
    logger: logging.Logger,
) -> Response:
    finalize_result = finalize_quota(grant, result=result)
    if isinstance(finalize_result, dict) and finalize_result.get("success") is False:
        warning = str(finalize_result.get("error") or finalize_result.get("code") or "quota_finalize_failed")
        logger.warning("documents quota finalize soft-failed: %s", warning)
        return _attach_quota_warning(result, warning=warning)
    return result


def _precheck_authenticated_doc_assist(auth: AuthContext | None) -> QuotaGrant | None:
    if auth is None:
        return None
    return precheck_quota(user_id=auth.user_id, quota_type="doc_assist", strict_config=True)


@router.get("/api/v1/view_pdf/{doi:path}")
@router.head("/api/v1/view_pdf/{doi:path}")
@router.get("/api/view_pdf/{doi:path}")
@router.head("/api/view_pdf/{doi:path}")
def view_pdf(
    doi: str,
    request: Request,
    _context: AuthContext = Depends(require_auth_context),
    _quota: QuotaGrant | None = Depends(require_quota("file_view")),
):
    payload, status_code, pdf_path = documents_service.view_pdf_path(doi, _logger(request))
    if pdf_path is None:
        response = _json(payload, status_code)
        return _finalize_quota_softly(grant=_quota, result=response, logger=_logger(request))
    filename = Path(pdf_path).name
    if str(request.method or "").upper() == "HEAD":
        response = Response(
            status_code=200,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            },
        )
        return _finalize_quota_softly(grant=_quota, result=response, logger=_logger(request))
    response = FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        },
    )
    return _finalize_quota_softly(grant=_quota, result=response, logger=_logger(request))


@router.get("/api/v1/patent/original/{canonical_patent_id}")
@router.head("/api/v1/patent/original/{canonical_patent_id}")
@router.get("/api/patent/original/{canonical_patent_id}")
@router.head("/api/patent/original/{canonical_patent_id}")
def view_patent_original(
    canonical_patent_id: str,
    request: Request,
    section: str = Query(default="fulltext"),
    claim_number: int | None = Query(default=None),
    paragraph_id: str | None = Query(default=None),
    format: str | None = Query(default=None),
    _context: AuthContext = Depends(require_auth_context),
    _quota: QuotaGrant | None = Depends(require_quota("file_view")),
):
    result = documents_service.patent_original_view(
        canonical_patent_id=canonical_patent_id,
        section=section,
        claim_number=claim_number,
        paragraph_id=paragraph_id,
        response_format=format,
        head_only=str(request.method or "").upper() == "HEAD",
        logger=_logger(request),
    )
    response = _patent_original_response(result=dict(result or {}), head_only=str(request.method or "").upper() == "HEAD")
    return _finalize_quota_softly(grant=_quota, result=response, logger=_logger(request))


@router.post("/api/v1/summarize_pdf/{doi:path}")
@router.post("/api/summarize_pdf/{doi:path}")
def summarize_pdf(
    doi: str,
    request: Request,
    _context: AuthContext = Depends(require_auth_context),
    _quota: QuotaGrant | None = Depends(require_quota("doc_assist", strict_config=True)),
):
    payload, status_code = documents_service.summarize_pdf(doi, _logger(request))
    response = _json(payload, status_code)
    return _finalize_quota_softly(grant=_quota, result=response, logger=_logger(request))


@router.get("/api/v1/extract_pdf_text/{doi:path}")
@router.get("/api/extract_pdf_text/{doi:path}")
def extract_pdf_text(
    doi: str,
    request: Request,
    auth: AuthContext | None = Depends(get_optional_auth_context),
):
    quota = _precheck_authenticated_doc_assist(auth)
    payload, status_code = documents_service.extract_pdf_text(doi, _logger(request))
    response = _json(payload, status_code)
    return _finalize_quota_softly(grant=quota, result=response, logger=_logger(request))


@router.post("/api/v1/translate")
@router.post("/api/translate")
def translate_text(
    payload: TranslateRequest,
    request: Request,
    _context: AuthContext = Depends(require_auth_context),
    _quota: QuotaGrant | None = Depends(require_quota("doc_assist", strict_config=True)),
):
    result, status_code = documents_service.translate(texts=payload.texts, logger=_logger(request))
    response = _json(result, status_code)
    return _finalize_quota_softly(grant=_quota, result=response, logger=_logger(request))


@router.get("/api/v1/check_pdf/{doi:path}")
@router.get("/api/check_pdf/{doi:path}")
def check_pdf(doi: str):
    payload, status_code = documents_service.check_pdf(doi)
    return _json(payload, status_code)


@router.get("/api/v1/literature_content")
@router.get("/api/literature_content")
def get_literature_content(
    request: Request,
    doi: str = Query(default=""),
    auth: AuthContext | None = Depends(get_optional_auth_context),
):
    quota = _precheck_authenticated_doc_assist(auth)
    agent = _agent_from_request(request)
    payload, status_code = documents_service.literature_content(
        doi=doi.strip(),
        agent=agent,
        logger=_logger(request),
        runtime=_runtime_from_request(request),
    )
    response = _json(payload, status_code)
    return _finalize_quota_softly(grant=quota, result=response, logger=_logger(request))


@router.get("/api/v1/reference_preview")
@router.get("/api/reference_preview")
def get_reference_preview_get(
    request: Request,
    dois: list[str] = Query(default_factory=list),
    dois_text: str = Query(default=""),
    max_items: int | None = Query(default=None),
    auth: AuthContext | None = Depends(get_optional_auth_context),
):
    quota = _precheck_authenticated_doc_assist(auth)
    agent = _agent_from_request(request)
    payload, status_code = documents_service.reference_preview(
        dois_text=dois_text,
        doi_list=dois,
        max_items=max_items,
        agent=agent,
        logger=_logger(request),
        runtime=_runtime_from_request(request),
    )
    response = _json(payload, status_code)
    return _finalize_quota_softly(grant=quota, result=response, logger=_logger(request))


@router.post("/api/v1/reference_preview")
@router.post("/api/reference_preview")
def get_reference_preview_post(
    payload: ReferencePreviewRequest,
    request: Request,
    auth: AuthContext | None = Depends(get_optional_auth_context),
):
    quota = _precheck_authenticated_doc_assist(auth)
    agent = _agent_from_request(request)
    result, status_code = documents_service.reference_preview(
        dois_text=payload.dois_text,
        doi_list=payload.doi_list,
        max_items=payload.max_items,
        agent=agent,
        logger=_logger(request),
        runtime=_runtime_from_request(request),
    )
    response = _json(result, status_code)
    return _finalize_quota_softly(grant=quota, result=response, logger=_logger(request))
