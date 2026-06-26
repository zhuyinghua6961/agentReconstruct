"""Patent browse API proxy routes forwarded to the patent backend."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.auth import AuthContext, GatewayAuthService
from app.services.proxy import ProxyService
from app.services.quota_proxy import QuotaProxyResult, QuotaProxyService
from app.services.usage_stats_client import UsageStatsClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patent-proxy"])

_QUOTA_TYPE = "doc_assist"


def _paths(path: str) -> tuple[str, ...]:
    return (path, path.replace("/api/", "/api/v1/", 1))


_ROUTE_SPECS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (_paths("/api/patent_search"), ("GET", "POST")),
)


async def _optional_auth_context(request: Request) -> AuthContext | None:
    authorization = str(request.headers.get("authorization") or "").strip()
    if not authorization:
        return None
    service: GatewayAuthService | None = getattr(request.app.state, "gateway_auth_service", None)
    if service is None:
        return None
    try:
        return await service.require_auth_context(request)
    except Exception:
        return None


def _sync_json_payload(response) -> dict | None:
    body = getattr(response, "body", None)
    if body in (None, b""):
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _should_count_patent_search_response(response) -> bool:
    if int(getattr(response, "status_code", 500) or 500) >= 400:
        return False
    payload = _sync_json_payload(response)
    if payload is None:
        return False
    if payload.get("code") in {"EMBEDDING_UNAVAILABLE", "RETRIEVAL_RUNTIME_UNAVAILABLE"}:
        return False
    if payload.get("error") and not list(payload.get("items") or []):
        return False
    return True


def _quota_payload_from_finalize(*, quota_type: str, finalize_result: QuotaProxyResult | None) -> dict:
    if finalize_result is not None and finalize_result.success:
        data = finalize_result.payload.get("data") if isinstance(finalize_result.payload.get("data"), dict) else {}
        return {
            "quota_type": quota_type,
            "counted": bool(data.get("counted")),
            "idempotent": bool(data.get("idempotent")),
            "noop": bool(data.get("noop")),
        }
    warning_payload = dict(finalize_result.payload) if finalize_result is not None else {}
    return {
        "quota_type": quota_type,
        "counted": False,
        "warning": {
            "code": str(warning_payload.get("code") or "QUOTA_FINALIZE_FAILED"),
            "error": str(warning_payload.get("error") or "quota_finalize_failed"),
            "message": str(warning_payload.get("message") or warning_payload.get("error") or "quota_finalize_failed"),
        },
    }


def _with_sync_quota_payload(response, *, quota_type: str, finalize_result: QuotaProxyResult | None):
    payload = _sync_json_payload(response)
    if payload is None:
        return response
    payload = dict(payload)
    payload["quota"] = _quota_payload_from_finalize(quota_type=quota_type, finalize_result=finalize_result)
    headers = dict(getattr(response, "headers", {}) or {})
    headers.pop("content-length", None)
    headers.pop("Content-Length", None)
    headers.pop("transfer-encoding", None)
    headers.pop("Transfer-Encoding", None)
    return JSONResponse(
        status_code=int(getattr(response, "status_code", 200) or 200),
        content=payload,
        headers=headers,
    )


async def _abort_quota_grant(
    *,
    request: Request,
    quota_proxy: QuotaProxyService,
    grant_id: str,
) -> None:
    result = await quota_proxy.finalize(request=request, grant_id=str(grant_id), success=False)
    if not result.success:
        logger.warning(
            "gateway patent_search quota finalize failed: grant_id=%s success=false status=%s code=%s error=%s",
            grant_id,
            result.status_code,
            result.payload.get("code"),
            result.payload.get("error"),
        )


async def _proxy_patent_search(request: Request) -> JSONResponse:
    registry = request.app.state.backend_registry
    proxy_service: ProxyService = request.app.state.proxy_service
    quota_proxy: QuotaProxyService = request.app.state.quota_proxy_service

    auth = await _optional_auth_context(request)
    grant_id: str | None = None
    if auth is not None and auth.user_id > 0:
        precheck = await quota_proxy.precheck(
            request=request,
            user_id=auth.user_id,
            quota_type=_QUOTA_TYPE,
            strict_config=True,
        )
        if not precheck.success:
            return JSONResponse(status_code=precheck.status_code, content=precheck.payload)
        grant_data = precheck.payload.get("data") if isinstance(precheck.payload.get("data"), dict) else {}
        grant_id = str(grant_data.get("grant_id") or "").strip() or None

    response = await proxy_service.forward(request=request, target=registry.get("patent"))

    if not grant_id:
        if auth is not None and int(auth.user_id) > 0 and _should_count_patent_search_response(response):
            usage_client: UsageStatsClient | None = getattr(request.app.state, "usage_stats_client", None)
            if usage_client is not None:
                await usage_client.record_event(
                    request=request,
                    user_id=int(auth.user_id),
                    event_type="patent_search",
                )
        return response
    if not _should_count_patent_search_response(response):
        await _abort_quota_grant(request=request, quota_proxy=quota_proxy, grant_id=grant_id)
        return response

    finalize_result = await quota_proxy.finalize(request=request, grant_id=grant_id, success=True)
    if not finalize_result.success:
        logger.warning(
            "gateway patent_search quota finalize failed: grant_id=%s status=%s code=%s error=%s",
            grant_id,
            finalize_result.status_code,
            finalize_result.payload.get("code"),
            finalize_result.payload.get("error"),
        )
    if auth is not None and int(auth.user_id) > 0:
        usage_client: UsageStatsClient | None = getattr(request.app.state, "usage_stats_client", None)
        if usage_client is not None:
            await usage_client.record_event(
                request=request,
                user_id=int(auth.user_id),
                event_type="patent_search",
            )
    return _with_sync_quota_payload(response, quota_type=_QUOTA_TYPE, finalize_result=finalize_result)


for paths, methods in _ROUTE_SPECS:
    for path in paths:
        router.add_api_route(path, _proxy_patent_search, methods=list(methods))
