from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.core.deps import AuthContext
from app.core.errors import AppError
from app.modules.auth.deps import require_auth_context
from app.modules.documents.schemas import ReferencePreviewRequest, TranslateRequest
from app.modules.documents.service import documents_service
from app.modules.quota.deps import finalize_quota, require_quota
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


def _enforce_quota_finalize(*, grant: QuotaGrant | None, result: Response | dict[str, object]) -> None:
    finalize_result = finalize_quota(grant, result=result)
    if isinstance(finalize_result, dict) and finalize_result.get("success") is False:
        raise AppError(
            message=str(finalize_result.get("error") or "quota_finalize_failed"),
            code=str(finalize_result.get("code") or "DB_UNAVAILABLE"),
            status_code=503,
        )


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
        _enforce_quota_finalize(grant=_quota, result=response)
        return response
    filename = Path(pdf_path).name
    if str(request.method or "").upper() == "HEAD":
        response = Response(
            status_code=200,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            },
        )
        _enforce_quota_finalize(grant=_quota, result=response)
        return response
    response = FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        },
    )
    _enforce_quota_finalize(grant=_quota, result=response)
    return response


@router.post("/api/v1/summarize_pdf/{doi:path}")
@router.post("/api/summarize_pdf/{doi:path}")
def summarize_pdf(
    doi: str,
    request: Request,
    _context: AuthContext = Depends(require_auth_context),
    _quota: QuotaGrant | None = Depends(require_quota("pdf_summary", strict_config=True)),
):
    payload, status_code = documents_service.summarize_pdf(doi, _logger(request))
    response = _json(payload, status_code)
    _enforce_quota_finalize(grant=_quota, result=response)
    return response


@router.get("/api/v1/extract_pdf_text/{doi:path}")
@router.get("/api/extract_pdf_text/{doi:path}")
def extract_pdf_text(doi: str, request: Request):
    payload, status_code = documents_service.extract_pdf_text(doi, _logger(request))
    return _json(payload, status_code)


@router.post("/api/v1/translate")
@router.post("/api/translate")
def translate_text(
    payload: TranslateRequest,
    request: Request,
    _context: AuthContext = Depends(require_auth_context),
    _quota: QuotaGrant | None = Depends(require_quota("text_translate", strict_config=True)),
):
    result, status_code = documents_service.translate(texts=payload.texts, logger=_logger(request))
    response = _json(result, status_code)
    _enforce_quota_finalize(grant=_quota, result=response)
    return response


@router.get("/api/v1/check_pdf/{doi:path}")
@router.get("/api/check_pdf/{doi:path}")
def check_pdf(doi: str):
    payload, status_code = documents_service.check_pdf(doi)
    return _json(payload, status_code)


@router.get("/api/v1/literature_content")
@router.get("/api/literature_content")
def get_literature_content(request: Request, doi: str = Query(default="")):
    agent = _agent_from_request(request)
    payload, status_code = documents_service.literature_content(
        doi=doi.strip(),
        agent=agent,
        logger=_logger(request),
        runtime=_runtime_from_request(request),
    )
    return _json(payload, status_code)


@router.get("/api/v1/reference_preview")
@router.get("/api/reference_preview")
def get_reference_preview_get(
    request: Request,
    dois: list[str] = Query(default_factory=list),
    dois_text: str = Query(default=""),
    max_items: int | None = Query(default=None),
):
    agent = _agent_from_request(request)
    payload, status_code = documents_service.reference_preview(
        dois_text=dois_text,
        doi_list=dois,
        max_items=max_items,
        agent=agent,
        logger=_logger(request),
        runtime=_runtime_from_request(request),
    )
    return _json(payload, status_code)


@router.post("/api/v1/reference_preview")
@router.post("/api/reference_preview")
def get_reference_preview_post(payload: ReferencePreviewRequest, request: Request):
    agent = _agent_from_request(request)
    result, status_code = documents_service.reference_preview(
        dois_text=payload.dois_text,
        doi_list=payload.doi_list,
        max_items=payload.max_items,
        agent=agent,
        logger=_logger(request),
        runtime=_runtime_from_request(request),
    )
    return _json(result, status_code)
