"""QA routes with gateway-side routing decisions and upstream forwarding."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.models.ask import AskRequest
from app.providers.conversation_files.base import ConversationFileProviderError
from app.services.proxy import ProxyService, StreamingProxyHandle

router = APIRouter(tags=["qa"])

_ALLOWED_MODES = {"fast", "thinking", "patent"}


async def _resolve(request: Request, payload: AskRequest, mode: str):
    resolver = request.app.state.file_context_resolver
    decision_service = request.app.state.route_decision_service
    conversation_file_service = request.app.state.conversation_file_service
    available_files = await conversation_file_service.list_files(
        conversation_id=payload.conversation_id,
        request=request,
    )
    file_context = resolver.resolve(
        question=payload.question,
        pdf_context=payload.pdf_context,
        available_files=available_files,
    )
    return decision_service.decide(requested_mode=mode, file_context=file_context), file_context


def _normalized_payload(*, payload: AskRequest, route_decision, file_context, trace_id: str) -> dict:
    return {
        "question": payload.question,
        "conversation_id": payload.conversation_id,
        "user_id": payload.user_id,
        "chat_history": [item.model_dump() for item in payload.chat_history],
        "requested_mode": route_decision.requested_mode,
        "actual_mode": route_decision.actual_mode,
        "route": route_decision.route,
        "source_scope": route_decision.source_scope,
        "turn_mode": route_decision.turn_mode,
        "kb_enabled": route_decision.kb_enabled,
        "allow_kb_verification": route_decision.allow_kb_verification,
        "used_files": file_context.used_files,
        "execution_files": file_context.execution_files,
        "selected_file_ids": route_decision.selected_file_ids,
        "primary_file_id": route_decision.primary_file_id,
        "file_selection": route_decision.file_selection,
        "trace_id": trace_id,
        "options": payload.options,
    }


def _conversation_files_error_json(*, trace_id: str, exc: ConversationFileProviderError) -> JSONResponse:
    return JSONResponse(
        status_code=int(getattr(exc, "status_code", 503) or 503),
        content={
            "success": False,
            "code": "CONVERSATION_FILE_PROVIDER_UNAVAILABLE",
            "error": "conversation_file_provider_unavailable",
            "message": str(exc),
            "provider": getattr(exc, "provider", "unknown"),
            "trace_id": trace_id,
        },
    )


def _conversation_files_error_stream(*, trace_id: str, exc: ConversationFileProviderError) -> StreamingResponse:
    def _frames():
        yield (
            'data: {"type":"error","code":"CONVERSATION_FILE_PROVIDER_UNAVAILABLE","error":"conversation_file_provider_unavailable","message":"%s","provider":"%s","retriable":true,"trace_id":"%s"}\n\n'
            % (str(exc).replace('"', '\"'), str(getattr(exc, "provider", "unknown") or "unknown").replace('"', '\"'), trace_id)
        )

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _clarification_json(*, trace_id: str, route_decision) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "code": "FILE_SELECTION_CLARIFICATION_REQUIRED",
            "error": "file_selection_clarification_required",
            "message": route_decision.clarification_message or "File selection requires clarification",
            "trace_id": trace_id,
            "requested_mode": route_decision.requested_mode,
            "actual_mode": route_decision.actual_mode,
            "route": route_decision.route,
        },
    )


def _clarification_stream(*, trace_id: str, route_decision) -> StreamingResponse:
    def _frames():
        yield (
            'data: {"type":"metadata","requested_mode":"%s","actual_mode":"%s","route":"%s","trace_id":"%s"}\n\n'
            % (route_decision.requested_mode, route_decision.actual_mode, route_decision.route, trace_id)
        )
        yield (
            'data: {"type":"error","code":"FILE_SELECTION_CLARIFICATION_REQUIRED","error":"file_selection_clarification_required","message":"%s","retriable":false,"trace_id":"%s"}\n\n'
            % ((route_decision.clarification_message or "File selection requires clarification").replace('"', '\\"'), trace_id)
        )

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _upstream_stream_error_stream(*, trace_id: str, backend: str, exc: Exception) -> StreamingResponse:
    message = str(exc).replace('"', '\\"') or "upstream_stream_unavailable"

    def _frames():
        yield (
            'data: {"type":"error","code":"UPSTREAM_STREAM_UNAVAILABLE","error":"upstream_stream_unavailable","message":"%s","backend":"%s","retriable":true,"trace_id":"%s"}\n\n'
            % (message, backend.replace('"', '\\"'), trace_id)
        )

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Gateway-Backend": backend},
    )


def _upstream_status_error_stream(*, trace_id: str, backend: str, status_code: int, message: str) -> StreamingResponse:
    escaped = str(message or "upstream_error").replace('"', '\\"')

    def _frames():
        yield (
            'data: {"type":"error","code":"UPSTREAM_ERROR","error":"upstream_error","message":"%s","backend":"%s","status_code":%d,"retriable":false,"trace_id":"%s"}\n\n'
            % (escaped, backend.replace('"', '\\"'), int(status_code), trace_id)
        )

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Gateway-Backend": backend},
    )


async def _proxy_ask(request: Request, payload: AskRequest, mode: str) -> JSONResponse:
    trace_id = str(getattr(request.state, "trace_id", "") or "")
    try:
        route_decision, file_context = await _resolve(request, payload, mode)
    except ConversationFileProviderError as exc:
        return _conversation_files_error_json(trace_id=trace_id, exc=exc)
    if route_decision.needs_clarification:
        return _clarification_json(trace_id=trace_id, route_decision=route_decision)

    registry = request.app.state.backend_registry
    proxy_service: ProxyService = request.app.state.proxy_service
    upstream_payload = _normalized_payload(
        payload=payload,
        route_decision=route_decision,
        file_context=file_context,
        trace_id=trace_id,
    )
    path = f"/api/{route_decision.actual_mode}/ask"
    return await proxy_service.forward_json(
        request=request,
        target=registry.get(route_decision.actual_mode),
        path=path,
        payload=upstream_payload,
    )


async def _proxy_ask_stream(request: Request, payload: AskRequest, mode: str):
    trace_id = str(getattr(request.state, "trace_id", "") or "")
    try:
        route_decision, file_context = await _resolve(request, payload, mode)
    except ConversationFileProviderError as exc:
        return _conversation_files_error_stream(trace_id=trace_id, exc=exc)
    if route_decision.needs_clarification:
        return _clarification_stream(trace_id=trace_id, route_decision=route_decision)

    registry = request.app.state.backend_registry
    proxy_service: ProxyService = request.app.state.proxy_service
    upstream_payload = _normalized_payload(
        payload=payload,
        route_decision=route_decision,
        file_context=file_context,
        trace_id=trace_id,
    )
    path = f"/api/{route_decision.actual_mode}/ask_stream"
    try:
        handle: StreamingProxyHandle = await proxy_service.open_json_stream(
            request=request,
            target=registry.get(route_decision.actual_mode),
            path=path,
            payload=upstream_payload,
        )
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        return _upstream_stream_error_stream(trace_id=trace_id, backend=route_decision.actual_mode, exc=exc)

    if handle.status_code >= 400 and "text/event-stream" not in str(handle.headers.get("content-type") or ""):
        body = await handle.upstream.aread()
        await handle.upstream.aclose()
        await handle.client.aclose()
        return _upstream_status_error_stream(
            trace_id=trace_id,
            backend=handle.backend,
            status_code=handle.status_code,
            message=body.decode("utf-8", errors="ignore") or "upstream_error",
        )

    return StreamingResponse(
        handle.body_iter(),
        status_code=handle.status_code,
        media_type=str(handle.headers.get("content-type") or "text/event-stream"),
        headers=handle.headers,
    )


@router.post("/api/fast/ask")
@router.post("/api/v1/fast/ask")
async def ask_fast(payload: AskRequest, request: Request):
    return await _proxy_ask(request, payload, "fast")


@router.post("/api/thinking/ask")
@router.post("/api/v1/thinking/ask")
async def ask_thinking(payload: AskRequest, request: Request):
    return await _proxy_ask(request, payload, "thinking")


@router.post("/api/patent/ask")
@router.post("/api/v1/patent/ask")
async def ask_patent(payload: AskRequest, request: Request):
    return await _proxy_ask(request, payload, "patent")


@router.post("/api/fast/ask_stream")
@router.post("/api/v1/fast/ask_stream")
async def ask_stream_fast(payload: AskRequest, request: Request):
    return await _proxy_ask_stream(request, payload, "fast")


@router.post("/api/thinking/ask_stream")
@router.post("/api/v1/thinking/ask_stream")
async def ask_stream_thinking(payload: AskRequest, request: Request):
    return await _proxy_ask_stream(request, payload, "thinking")


@router.post("/api/patent/ask_stream")
@router.post("/api/v1/patent/ask_stream")
async def ask_stream_patent(payload: AskRequest, request: Request):
    return await _proxy_ask_stream(request, payload, "patent")
