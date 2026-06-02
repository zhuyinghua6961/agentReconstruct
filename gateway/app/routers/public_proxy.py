"""Public API proxy routes forwarded to the public backend role."""

from __future__ import annotations

import json

from fastapi import HTTPException
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.services.proxy import ProxyService
from app.services.qa_tasks import QATaskService

_CONVERSATION_LIST_ROUTE_PATHS = {
    "/api/conversations",
    "/api/v1/conversations",
}

_CONVERSATION_DETAIL_ROUTE_PATHS = {
    "/api/conversations/{conversation_id}",
    "/api/v1/conversations/{conversation_id}",
}

_LIVE_PUBLIC_TASK_STATUSES = {"queued", "admitted", "running"}

router = APIRouter(tags=["public-proxy"])

_STREAMING_ROUTE_PATHS = {
    "/api/conversations/{conversation_id}/files/{file_id}/download",
    "/api/upload_pdf",
    "/api/upload_excel",
    "/api/translate_document",
    "/api/view_pdf/{doi:path}",
}


def _is_streaming_route(request: Request) -> bool:
    route = request.scope.get("route")
    route_path = str(getattr(route, "path", "") or "")
    if route_path in {
        "/api/patent/original/{canonical_patent_id}",
        "/api/v1/patent/original/{canonical_patent_id}",
    }:
        requested_section = str(request.query_params.get("section") or "").strip().lower()
        return requested_section in {"", "fulltext"}
    return route_path in _STREAMING_ROUTE_PATHS


async def _proxy_to_public(request: Request) -> Response:
    registry = request.app.state.backend_registry
    proxy_service: ProxyService = request.app.state.proxy_service
    if _is_streaming_route(request):
        handle = await proxy_service.open_request_stream(request=request, target=registry.get_public())
        return StreamingResponse(
            handle.body_iter(),
            status_code=handle.status_code,
            headers=handle.headers,
            media_type=str(handle.headers.get("content-type") or "application/octet-stream"),
        )
    if _is_conversation_detail_read(request):
        task_service = QATaskService(request)
        try:
            conversation_id = int(request.path_params.get("conversation_id") or 0)
        except Exception:
            conversation_id = 0
        if conversation_id > 0:
            await task_service.reconcile_pending_terminal_tasks(conversation_ids={conversation_id})
    response = await proxy_service.forward(request=request, target=registry.get_public())
    return _maybe_enrich_conversation_reads(request=request, response=response)


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", "") or "")


def _is_conversation_list_read(request: Request) -> bool:
    return request.method.upper() == "GET" and _route_path(request) in _CONVERSATION_LIST_ROUTE_PATHS


def _is_conversation_detail_read(request: Request) -> bool:
    return request.method.upper() == "GET" and _route_path(request) in _CONVERSATION_DETAIL_ROUTE_PATHS


def _decode_json_response(response: Response) -> dict | None:
    content_type = str(response.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        return None
    body = getattr(response, "body", None)
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _task_timestamp_sort_key(record: dict) -> tuple[int, str]:
    status = str(record.get("status") or "").strip().lower()
    status_rank = {
        "running": 3,
        "admitted": 2,
        "queued": 1,
    }.get(status, 0)
    for field_name in ("updated_at", "started_at", "admitted_at", "enqueued_at", "created_at"):
        value = str(record.get(field_name) or "").strip()
        if value:
            return (status_rank, value)
    return (status_rank, "")


def _live_task_summary_by_conversation(request: Request) -> dict[int, dict]:
    queue_store = request.app.state.execution_queue_status_store
    task_service = QATaskService(request)
    chosen: dict[int, dict] = {}
    for record in queue_store.list_requests():
        try:
            conversation_id = int(record.get("conversation_id") or 0)
        except Exception:
            continue
        if conversation_id <= 0:
            continue
        if str(record.get("status") or "").strip().lower() not in _LIVE_PUBLIC_TASK_STATUSES:
            continue
        current = chosen.get(conversation_id)
        if current is None or _task_timestamp_sort_key(record) >= _task_timestamp_sort_key(current):
            chosen[conversation_id] = record
    summaries: dict[int, dict] = {}
    for conversation_id, record in chosen.items():
        request_id = str(record.get("request_id") or "").strip()
        if not request_id:
            continue
        try:
            summaries[conversation_id] = task_service.build_task_summary(request_id)
        except HTTPException as exc:
            if int(exc.status_code or 500) == 404:
                continue
            raise
    return summaries


def _json_response_from_payload(response: Response, payload: dict) -> JSONResponse:
    headers = {
        key: value
        for key, value in dict(response.headers).items()
        if key.lower() != "content-length"
    }
    return JSONResponse(
        status_code=int(response.status_code or 200),
        content=payload,
        headers=headers,
    )


def _maybe_enrich_conversation_reads(*, request: Request, response: Response) -> Response:
    if not (_is_conversation_list_read(request) or _is_conversation_detail_read(request)):
        return response
    payload = _decode_json_response(response)
    if not isinstance(payload, dict):
        return response
    data = payload.get("data")
    if not isinstance(data, dict):
        return response
    active_tasks = _live_task_summary_by_conversation(request)
    if _is_conversation_list_read(request):
        conversations = data.get("conversations")
        if not isinstance(conversations, list):
            return response
        enriched_items = []
        for item in conversations:
            if not isinstance(item, dict):
                enriched_items.append(item)
                continue
            enriched = dict(item)
            try:
                conversation_id = int(enriched.get("conversation_id") or 0)
            except Exception:
                conversation_id = 0
            enriched["active_task"] = active_tasks.get(conversation_id)
            enriched_items.append(enriched)
        enriched_payload = dict(payload)
        enriched_data = dict(data)
        enriched_data["conversations"] = enriched_items
        enriched_payload["data"] = enriched_data
        return _json_response_from_payload(response, enriched_payload)
    try:
        conversation_id = int(data.get("conversation_id") or 0)
    except Exception:
        conversation_id = 0
    enriched_payload = dict(payload)
    enriched_data = dict(data)
    enriched_data["active_task"] = active_tasks.get(conversation_id)
    enriched_payload["data"] = enriched_data
    return _json_response_from_payload(response, enriched_payload)


async def _proxy_public(
    request: Request,
    conversation_id: str | None = None,
    file_id: str | None = None,
    user_id: str | None = None,
    quota_type: str | None = None,
    doi: str | None = None,
) -> Response:
    _ = conversation_id, file_id, user_id, quota_type, doi
    return await _proxy_to_public(request)


def _paths(path: str, *, include_v1: bool = True) -> tuple[str, ...]:
    paths = [path]
    if include_v1:
        paths.append(path.replace("/api/", "/api/v1/", 1))
    return tuple(paths)


def _route_name(path: str, methods: tuple[str, ...]) -> str:
    normalized = path.strip("/").replace("/", ":").replace("{", "").replace("}", "")
    return f"public_proxy:{normalized}:{'-'.join(methods).lower()}"


_ROUTE_SPECS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (_paths("/api/auth/login"), ("POST",)),
    (_paths("/api/auth/register"), ("POST",)),
    (_paths("/api/auth/me"), ("GET",)),
    (_paths("/api/auth/departments/tree"), ("GET",)),
    (_paths("/api/auth/department"), ("PUT",)),
    (_paths("/api/auth/username"), ("PUT",)),
    (_paths("/api/auth/personnel-binding"), ("PUT",)),
    (_paths("/api/auth/password"), ("POST", "PUT")),
    (_paths("/api/auth/forgot-password/initiate"), ("POST",)),
    (_paths("/api/auth/forgot-password/verify"), ("POST",)),
    (_paths("/api/auth/security-questions"), ("GET", "POST", "PUT")),
    (_paths("/api/conversations"), ("GET", "POST")),
    (_paths("/api/conversations/{conversation_id}"), ("GET", "DELETE")),
    (_paths("/api/conversations/{conversation_id}/title"), ("PUT",)),
    (_paths("/api/conversations/{conversation_id}/messages"), ("POST",)),
    (_paths("/api/conversations/{conversation_id}/files"), ("GET",)),
    (_paths("/api/conversations/{conversation_id}/files/{file_id}"), ("GET", "DELETE")),
    (_paths("/api/conversations/{conversation_id}/files/{file_id}/download"), ("GET",)),
    (_paths("/api/upload_pdf"), ("POST",)),
    (_paths("/api/upload_excel"), ("POST",)),
    (_paths("/api/clear_pdf"), ("POST",)),
    (_paths("/api/translate"), ("POST",)),
    (_paths("/api/translate_document"), ("POST",)),
    (_paths("/api/kb_info"), ("GET",)),
    (_paths("/api/refresh_kb"), ("POST",)),
    (_paths("/api/clear_cache"), ("POST",)),
    (_paths("/api/background_status"), ("GET",)),
    (_paths("/api/health"), ("GET",)),
    (_paths("/api/literature_content"), ("GET",)),
    (_paths("/api/reference_preview"), ("POST",)),
    (_paths("/api/patent/original/{canonical_patent_id}"), ("GET", "HEAD")),
    (_paths("/api/summarize_pdf/{doi:path}"), ("POST",)),
    (_paths("/api/extract_pdf_text/{doi:path}"), ("GET",)),
    (_paths("/api/check_pdf/{doi:path}"), ("GET",)),
    (_paths("/api/view_pdf/{doi:path}"), ("GET", "HEAD")),
    (_paths("/api/quota/my"), ("GET",)),
    (_paths("/api/quota/configs"), ("GET", "POST")),
    (_paths("/api/quota/configs/{quota_type:path}"), ("PUT",)),
    (_paths("/api/quota/users/{user_id}"), ("GET",)),
    (_paths("/api/quota/reset/{user_id}/{quota_type:path}"), ("POST",)),
    (_paths("/api/admin/model-status", include_v1=False), ("GET",)),
    (_paths("/api/admin/model-status/test", include_v1=False), ("POST",)),
    (_paths("/api/admin/users", include_v1=False), ("GET", "POST")),
    (_paths("/api/admin/users/{user_id}", include_v1=False), ("DELETE",)),
    (_paths("/api/admin/users/{user_id}/username", include_v1=False), ("PUT",)),
    (_paths("/api/admin/users/{user_id}/personnel-binding", include_v1=False), ("PUT", "DELETE")),
    (_paths("/api/admin/users/{user_id}/password", include_v1=False), ("GET", "PUT")),
    (_paths("/api/admin/users/{user_id}/status", include_v1=False), ("PUT",)),
    (_paths("/api/admin/users/{user_id}/type", include_v1=False), ("PUT",)),
    (_paths("/api/admin/users/batch-delete", include_v1=False), ("POST",)),
    (_paths("/api/admin/users/batch-type", include_v1=False), ("POST",)),
    (_paths("/api/admin/users/batch-import", include_v1=False), ("POST",)),
    (_paths("/api/admin/users/import-template", include_v1=False), ("GET",)),
    (_paths("/api/admin/personnel", include_v1=False), ("GET", "POST")),
    (_paths("/api/admin/personnel/{personnel_id}", include_v1=False), ("PUT",)),
    (_paths("/api/admin/personnel/{personnel_id}/status", include_v1=False), ("PUT",)),
    (_paths("/api/admin/personnel/{personnel_id}/bindings", include_v1=False), ("GET",)),
    (_paths("/api/admin/personnel/batch-import", include_v1=False), ("POST",)),
    (_paths("/api/admin/personnel/import-template", include_v1=False), ("GET",)),
    (_paths("/api/admin/departments/tree", include_v1=False), ("GET",)),
    (_paths("/api/admin/departments/primary", include_v1=False), ("POST",)),
    (_paths("/api/admin/departments/primary/{primary_id}", include_v1=False), ("PUT",)),
    (_paths("/api/admin/departments/primary/{primary_id}/status", include_v1=False), ("PUT",)),
    (_paths("/api/admin/departments/secondary", include_v1=False), ("POST",)),
    (_paths("/api/admin/departments/secondary/{secondary_id}", include_v1=False), ("PUT",)),
    (_paths("/api/admin/departments/secondary/{secondary_id}/status", include_v1=False), ("PUT",)),
    (_paths("/api/admin/departments/secondary/{secondary_id}/users", include_v1=False), ("GET",)),
    (_paths("/api/admin/departments/secondary/{secondary_id}/legacy-users", include_v1=False), ("GET",)),
    (_paths("/api/admin/departments/tertiary", include_v1=False), ("POST",)),
    (_paths("/api/admin/departments/tertiary/{tertiary_id}", include_v1=False), ("PUT",)),
    (_paths("/api/admin/departments/tertiary/{tertiary_id}/status", include_v1=False), ("PUT",)),
    (_paths("/api/admin/departments/tertiary/{tertiary_id}/users", include_v1=False), ("GET",)),
    (_paths("/api/admin/departments/batch-import", include_v1=False), ("POST",)),
    (_paths("/api/admin/departments/import-template", include_v1=False), ("GET",)),
)

for paths, methods in _ROUTE_SPECS:
    for path in paths:
        router.add_api_route(
            path,
            _proxy_public,
            methods=list(methods),
            name=_route_name(path, methods),
        )
