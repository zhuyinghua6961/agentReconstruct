from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.modules.documents.schemas import ReferencePreviewRequest
from app.modules.documents.service import documents_service

router = APIRouter(tags=["documents"])


def _json(payload: dict, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def _logger_from_request(request: Request):
    return getattr(request.app, "logger", None) or getattr(request.app.state, "logger", None)


def _papers_dir_from_request(request: Request):
    settings = getattr(request.app.state, "settings", None)
    return getattr(settings, "papers_dir", None)


def _preview_agent_from_request(request: Request):
    runtime = getattr(request.app.state, "generation_runtime", None)
    literature_expert = getattr(runtime, "literature_expert", None) if runtime is not None else None
    if literature_expert is None:
        return None
    return SimpleNamespace(semantic_expert=literature_expert)


def _normalize_query_doi_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


@router.get("/api/v1/view_pdf/{doi:path}")
@router.head("/api/v1/view_pdf/{doi:path}")
@router.get("/api/view_pdf/{doi:path}")
@router.head("/api/view_pdf/{doi:path}")
def view_pdf(doi: str, request: Request):
    payload, status_code, pdf_path = documents_service.view_pdf_path(
        doi=doi,
        papers_dir=_papers_dir_from_request(request),
        logger=_logger_from_request(request),
    )
    if pdf_path is None:
        return _json(payload, status_code)

    filename = Path(pdf_path).name
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    if str(request.method or "").upper() == "HEAD":
        return Response(status_code=200, media_type="application/pdf", headers=headers)
    return FileResponse(path=str(pdf_path), media_type="application/pdf", headers=headers)


@router.get("/api/v1/check_pdf/{doi:path}")
@router.get("/api/check_pdf/{doi:path}")
def check_pdf(doi: str, request: Request):
    payload, status_code = documents_service.check_pdf(
        doi=doi,
        papers_dir=_papers_dir_from_request(request),
        logger=_logger_from_request(request),
    )
    return _json(payload, status_code)


@router.get("/api/v1/extract_pdf_text/{doi:path}")
@router.get("/api/extract_pdf_text/{doi:path}")
def extract_pdf_text(doi: str, request: Request):
    payload, status_code = documents_service.extract_pdf_text(
        doi=doi,
        logger=_logger_from_request(request),
        papers_dir=_papers_dir_from_request(request),
    )
    return _json(payload, status_code)


@router.get("/api/v1/reference_preview")
@router.get("/api/reference_preview")
def reference_preview_get(
    request: Request,
    dois: list[str] | None = None,
    doi: list[str] | None = None,
    dois_text: str = Query(default=""),
    max_items: int | None = Query(default=None),
):
    query_params = getattr(request, "query_params", None)
    query_dois = query_params.getlist("dois") if query_params is not None else []
    query_doi_aliases = query_params.getlist("doi") if query_params is not None else []
    payload, status_code = documents_service.reference_preview(
        dois_text=dois_text,
        doi_list=[
            *_normalize_query_doi_list(query_dois or dois),
            *_normalize_query_doi_list(query_doi_aliases or doi),
        ],
        max_items=max_items,
        agent=_preview_agent_from_request(request),
        logger=_logger_from_request(request),
        papers_dir=_papers_dir_from_request(request),
    )
    return _json(payload, status_code)


@router.post("/api/v1/reference_preview")
@router.post("/api/reference_preview")
def reference_preview_post(payload: ReferencePreviewRequest, request: Request):
    result, status_code = documents_service.reference_preview(
        dois_text=payload.dois_text,
        doi_list=payload.resolved_doi_list(),
        max_items=payload.max_items,
        agent=_preview_agent_from_request(request),
        logger=_logger_from_request(request),
        papers_dir=_papers_dir_from_request(request),
    )
    return _json(result, status_code)
