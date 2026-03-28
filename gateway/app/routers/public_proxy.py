"""Public API proxy routes forwarded to the public backend role."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from app.services.proxy import ProxyService

router = APIRouter(tags=["public-proxy"])

_STREAMING_ROUTE_PATHS = {
    "/api/conversations/{conversation_id}/files/{file_id}/download",
    "/api/upload_pdf",
    "/api/upload_excel",
    "/api/view_pdf/{doi:path}",
}


def _is_streaming_route(request: Request) -> bool:
    route = request.scope.get("route")
    route_path = str(getattr(route, "path", "") or "")
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
    return await proxy_service.forward(request=request, target=registry.get_public())


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
    (_paths("/api/kb_info"), ("GET",)),
    (_paths("/api/refresh_kb"), ("POST",)),
    (_paths("/api/clear_cache"), ("POST",)),
    (_paths("/api/background_status"), ("GET",)),
    (_paths("/api/health"), ("GET",)),
    (_paths("/api/literature_content"), ("GET",)),
    (_paths("/api/reference_preview"), ("POST",)),
    (_paths("/api/summarize_pdf/{doi:path}"), ("POST",)),
    (_paths("/api/extract_pdf_text/{doi:path}"), ("GET",)),
    (_paths("/api/check_pdf/{doi:path}"), ("GET",)),
    (_paths("/api/view_pdf/{doi:path}"), ("GET", "HEAD")),
    (_paths("/api/quota/my"), ("GET",)),
    (_paths("/api/quota/configs"), ("GET", "POST")),
    (_paths("/api/quota/configs/{quota_type:path}"), ("PUT",)),
    (_paths("/api/quota/users/{user_id}"), ("GET",)),
    (_paths("/api/quota/reset/{user_id}/{quota_type:path}"), ("POST",)),
    (_paths("/api/admin/users", include_v1=False), ("GET", "POST")),
    (_paths("/api/admin/users/{user_id}", include_v1=False), ("DELETE",)),
    (_paths("/api/admin/users/{user_id}/password", include_v1=False), ("GET", "PUT")),
    (_paths("/api/admin/users/{user_id}/status", include_v1=False), ("PUT",)),
    (_paths("/api/admin/users/{user_id}/type", include_v1=False), ("PUT",)),
    (_paths("/api/admin/users/batch-delete", include_v1=False), ("POST",)),
    (_paths("/api/admin/users/batch-type", include_v1=False), ("POST",)),
    (_paths("/api/admin/users/batch-import", include_v1=False), ("POST",)),
    (_paths("/api/admin/users/import-template", include_v1=False), ("GET",)),
)

for paths, methods in _ROUTE_SPECS:
    for path in paths:
        router.add_api_route(
            path,
            _proxy_public,
            methods=list(methods),
            name=_route_name(path, methods),
        )
