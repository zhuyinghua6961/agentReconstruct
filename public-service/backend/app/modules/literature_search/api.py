from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.core.deps import AuthContext
from app.modules.auth.deps import get_optional_auth_context
from app.modules.documents.api import _finalize_quota_softly, _precheck_authenticated_doc_assist, _runtime_from_request
from app.modules.literature_search.schemas import LiteratureSearchRequest
from app.modules.literature_search.service import literature_search_service
from app.modules.quota.service import QuotaGrant
from app.modules.usage_stats import service as usage_stats_service_module
from app.modules.usage_stats.helpers import should_count_search_response


router = APIRouter(tags=["literature-search"])


def _json(payload: dict, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def _logger(request: Request) -> logging.Logger:
    return getattr(request.app, "logger", None) or logging.getLogger(__name__)


def _agent_from_request(request: Request):
    runtime = _runtime_from_request(request)
    return getattr(runtime, "agent", None) if runtime is not None else None


def _handle_search(
    *,
    request: Request,
    query: str,
    query_type: str,
    match_mode: str,
    sources: str,
    limit: int,
    auth: AuthContext | None,
) -> JSONResponse:
    quota = _precheck_authenticated_doc_assist(auth)
    payload, status_code = literature_search_service.search(
        query=query,
        query_type=query_type,
        match_mode=match_mode,
        sources=sources,
        limit=limit,
        agent=_agent_from_request(request),
        logger=_logger(request),
        runtime=_runtime_from_request(request),
    )
    if auth is not None and int(auth.user_id) > 0 and should_count_search_response(payload=payload, status_code=status_code):
        usage_stats_service_module.usage_stats_service.record_event(
            user_id=int(auth.user_id),
            event_type="literature_search",
            metadata={
                "query": query,
                "query_type": query_type,
                "query_type_detected": payload.get("query_type_detected") if isinstance(payload, dict) else None,
                "count": payload.get("count") if isinstance(payload, dict) else None,
            },
        )
    response = _json(payload, status_code)
    return _finalize_quota_softly(grant=quota, result=response, logger=_logger(request))


@router.get("/api/v1/literature_search")
@router.get("/api/literature_search")
def literature_search_get(
    request: Request,
    query: str = Query(default=""),
    query_type: str = Query(default="auto"),
    match_mode: str = Query(default="semantic"),
    sources: str = Query(default="both"),
    limit: int = Query(default=20, ge=1, le=50),
    auth: AuthContext | None = Depends(get_optional_auth_context),
):
    return _handle_search(
        request=request,
        query=query.strip(),
        query_type=query_type,
        match_mode=match_mode,
        sources=sources,
        limit=limit,
        auth=auth,
    )


@router.post("/api/v1/literature_search")
@router.post("/api/literature_search")
async def literature_search_post(
    request: Request,
    auth: AuthContext | None = Depends(get_optional_auth_context),
):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    payload = LiteratureSearchRequest.model_validate(body)
    return _handle_search(
        request=request,
        query=payload.query.strip(),
        query_type=payload.query_type,
        match_mode=payload.match_mode,
        sources=payload.sources,
        limit=payload.limit,
        auth=auth,
    )
