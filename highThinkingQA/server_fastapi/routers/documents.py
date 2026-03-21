"""FastAPI document routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

from server.services.documents_service import documents_service
from server_fastapi.auth.deps import AuthContext, get_optional_auth_context, require_auth_context
from server_fastapi.http import read_json_payload

router = APIRouter()


@router.get("/api/v1/view_pdf/{doi:path}")
@router.head("/api/v1/view_pdf/{doi:path}")
@router.get("/api/view_pdf/{doi:path}")
@router.head("/api/view_pdf/{doi:path}")
async def view_pdf(
    doi: str,
    request: Request,
    _context: AuthContext | None = Depends(get_optional_auth_context),
):
    payload, status_code, pdf_path = documents_service.view_pdf_path(doi, request.app.logger)
    if pdf_path is None:
        return JSONResponse(content=payload, status_code=status_code)
    filename = Path(pdf_path).name
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/api/v1/translate")
@router.post("/api/translate")
async def translate_text(request: Request, _context: AuthContext = Depends(require_auth_context)):
    payload = await read_json_payload(request)
    payload = payload if isinstance(payload, dict) else {}
    result, status_code = documents_service.translate(
        texts=payload.get("texts") if isinstance(payload.get("texts"), list) else [],
        logger=request.app.logger,
    )
    return JSONResponse(content=result, status_code=status_code)


@router.post("/api/v1/summarize_pdf/{doi:path}")
@router.post("/api/summarize_pdf/{doi:path}")
async def summarize_pdf(doi: str, request: Request, _context: AuthContext = Depends(require_auth_context)):
    payload, status_code = documents_service.summarize_pdf(doi, request.app.logger)
    return JSONResponse(content=payload, status_code=status_code)


@router.get("/api/v1/extract_pdf_text/{doi:path}")
@router.get("/api/extract_pdf_text/{doi:path}")
async def extract_pdf_text(doi: str, request: Request):
    payload, status_code = documents_service.extract_pdf_text(doi, request.app.logger)
    return JSONResponse(content=payload, status_code=status_code)


@router.get("/api/v1/check_pdf/{doi:path}")
@router.get("/api/check_pdf/{doi:path}")
async def check_pdf(doi: str, request: Request):
    payload, status_code = documents_service.check_pdf(doi, request.app.logger)
    return JSONResponse(content=payload, status_code=status_code)
