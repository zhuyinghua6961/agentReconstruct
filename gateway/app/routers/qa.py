"""QA routes with gateway-side routing decisions and upstream forwarding."""

from __future__ import annotations

import logging
import json
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.models.ask import AskRequest
from app.providers.conversation_files.base import ConversationFileProviderError
from app.services.conversation_persistence import StreamSummary
from app.services.proxy import ProxyService, StreamingProxyHandle

router = APIRouter(tags=["qa"])
logger = logging.getLogger(__name__)

_ALLOWED_MODES = {"fast", "thinking", "patent"}


def _should_gateway_persist(*, actual_mode: str) -> bool:
    return actual_mode != "thinking"


def _legacy_mode(payload: AskRequest) -> str:
    requested_mode = str(getattr(payload, "requested_mode", "fast") or "fast").strip().lower()
    body_mode = str(getattr(payload, "mode", "") or "").strip().lower()

    if requested_mode in _ALLOWED_MODES and requested_mode != "fast":
        return requested_mode
    if body_mode in _ALLOWED_MODES:
        return body_mode
    if requested_mode in _ALLOWED_MODES:
        return requested_mode
    return "fast"


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
    persistence_service = request.app.state.conversation_persistence_service
    if _should_gateway_persist(actual_mode=route_decision.actual_mode):
        try:
            await persistence_service.persist_user_message(
                request=request,
                conversation_id=payload.conversation_id,
                content=payload.question,
                context_hints={
                    "selected_file_ids": list(route_decision.selected_file_ids),
                    "last_turn_route_hint": route_decision.route,
                },
            )
        except Exception as exc:
            logger.warning("gateway user persistence skipped: %s", exc)
    path = f"/api/{route_decision.actual_mode}/ask"
    response = await proxy_service.forward_json(
        request=request,
        target=registry.get(route_decision.actual_mode),
        path=path,
        payload=upstream_payload,
    )
    if response.status_code < 400 and _should_gateway_persist(actual_mode=route_decision.actual_mode):
        try:
            payload_json = json.loads(response.body.decode("utf-8"))
            data = payload_json.get("data") if isinstance(payload_json, dict) else payload_json
            if isinstance(data, dict):
                summary = persistence_service.new_stream_summary()
                summary.assistant_content = str(data.get("final_answer") or "")
                summary.query_mode = str(data.get("query_mode") or (data.get("metadata") or {}).get("query_mode") or "")
                summary.references = data.get("references") if isinstance(data.get("references"), list) else []
                summary.reference_links = data.get("reference_links") if isinstance(data.get("reference_links"), list) else []
                summary.pdf_links = data.get("pdf_links") if isinstance(data.get("pdf_links"), list) else []
                summary.doi_locations = data.get("doi_locations") if isinstance(data.get("doi_locations"), dict) else {}
                summary.route = str(data.get("route") or "")
                summary.used_files = data.get("used_files") if isinstance(data.get("used_files"), list) else []
                summary.timings = data.get("timings") if isinstance(data.get("timings"), dict) else {}
                summary.trace_id = str(data.get("trace_id") or trace_id)
                summary.file_selection = data.get("file_selection") if isinstance(data.get("file_selection"), dict) else {}
                summary.steps = data.get("steps") if isinstance(data.get("steps"), list) else []
                summary.done_seen = True
                await persistence_service.persist_assistant_summary(
                    request=request,
                    conversation_id=payload.conversation_id,
                    summary=summary,
                )
        except Exception as exc:
            logger.warning("gateway sync ask assistant persistence skipped: %s", exc)
    return response


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
    persistence_service = request.app.state.conversation_persistence_service
    if _should_gateway_persist(actual_mode=route_decision.actual_mode):
        try:
            await persistence_service.persist_user_message(
                request=request,
                conversation_id=payload.conversation_id,
                content=payload.question,
                context_hints={
                    "selected_file_ids": list(route_decision.selected_file_ids),
                    "last_turn_route_hint": route_decision.route,
                },
            )
        except Exception as exc:
            logger.warning("gateway user persistence skipped: %s", exc)
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

    summary: StreamSummary = persistence_service.new_stream_summary()

    async def _persisting_body_iter():
        try:
            if _should_gateway_persist(actual_mode=route_decision.actual_mode):
                async for chunk in persistence_service.extract_stream(
                    body_iter=handle.body_iter(),
                    summary=summary,
                ):
                    yield chunk
            else:
                async for chunk in handle.body_iter():
                    yield chunk
        finally:
            if _should_gateway_persist(actual_mode=route_decision.actual_mode):
                try:
                    await persistence_service.persist_assistant_summary(
                        request=request,
                        conversation_id=payload.conversation_id,
                        summary=summary,
                    )
                except Exception as exc:
                    logger.warning("gateway assistant persistence skipped: %s", exc)

    return StreamingResponse(
        _persisting_body_iter(),
        status_code=handle.status_code,
        media_type=str(handle.headers.get("content-type") or "text/event-stream"),
        headers=handle.headers,
    )


@router.post("/api/ask")
@router.post("/api/v1/ask")
async def ask_legacy(payload: AskRequest, request: Request):
    return await _proxy_ask(request, payload, _legacy_mode(payload))


@router.post("/api/ask_stream")
@router.post("/api/v1/ask_stream")
async def ask_stream_legacy(payload: AskRequest, request: Request):
    return await _proxy_ask_stream(request, payload, _legacy_mode(payload))


@router.post("/api/{mode}/ask")
@router.post("/api/v1/{mode}/ask")
async def ask_mode(mode: str, payload: AskRequest, request: Request):
    if mode not in _ALLOWED_MODES:
        return JSONResponse(status_code=400, content={"success": False, "error": "mode_not_supported"})
    return await _proxy_ask(request, payload, mode)


@router.post("/api/{mode}/ask_stream")
@router.post("/api/v1/{mode}/ask_stream")
async def ask_stream_mode(mode: str, payload: AskRequest, request: Request):
    if mode not in _ALLOWED_MODES:
        return JSONResponse(status_code=400, content={"success": False, "error": "mode_not_supported"})
    return await _proxy_ask_stream(request, payload, mode)
