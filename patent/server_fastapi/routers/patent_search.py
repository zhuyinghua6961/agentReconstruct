from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from server.patent.browse_search import build_patent_browse_search_service
from server.schemas.patent_search_models import PatentSearchRequest


router = APIRouter(tags=["patent-search"])
_LOGGER = logging.getLogger("patent.patent_search_router")


def _json(payload: dict, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def _search_service(request: Request):
    runtime = getattr(request.app.state, "patent_runtime", None)
    service = getattr(request.app.state, "patent_browse_search_service", None)
    if service is not None:
        return service
    return build_patent_browse_search_service(runtime)


def _handle_search(
    *,
    request: Request,
    query: str,
    query_type: str,
    sources: str,
    limit: int,
    method: str,
) -> JSONResponse:
    _LOGGER.info(
        "patent_search request method=%s query_len=%s query_type=%s sources=%s limit=%s",
        method,
        len(str(query or "")),
        query_type,
        sources,
        limit,
    )
    payload, status_code = _search_service(request).search(
        query=query,
        query_type=query_type,
        sources=sources,
        limit=limit,
    )
    cache_meta = dict(payload.get("cache_meta") or {}) if isinstance(payload, dict) else {}
    rerank_meta = dict(payload.get("rerank") or {}) if isinstance(payload, dict) else {}
    _LOGGER.info(
        "patent_search response method=%s status=%s count=%s backend=%s cache_hit=%s "
        "rerank_applied=%s code=%s error=%s",
        method,
        status_code,
        payload.get("count", 0) if isinstance(payload, dict) else 0,
        payload.get("retrieval_backend", "") if isinstance(payload, dict) else "",
        cache_meta.get("hit"),
        rerank_meta.get("applied"),
        payload.get("code", "") if isinstance(payload, dict) else "",
        payload.get("error", "") if isinstance(payload, dict) else "",
    )
    return _json(payload, status_code)


@router.get("/api/v1/patent_search")
@router.get("/api/patent_search")
def patent_search_get(
    request: Request,
    query: str = Query(default=""),
    query_type: str = Query(default="auto"),
    sources: str = Query(default="both"),
    limit: int = Query(default=20, ge=1, le=50),
):
    return _handle_search(
        request=request,
        query=query.strip(),
        query_type=query_type,
        sources=sources,
        limit=limit,
        method="GET",
    )


@router.post("/api/v1/patent_search")
@router.post("/api/patent_search")
async def patent_search_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    parsed = PatentSearchRequest.model_validate(body)
    return _handle_search(
        request=request,
        query=str(parsed.query or "").strip(),
        query_type=parsed.query_type,
        sources=parsed.sources,
        limit=parsed.limit,
        method="POST",
    )
