"""QA routes with gateway-side routing decisions and upstream forwarding."""

from __future__ import annotations

import asyncio
import json
import logging
import httpx
import time
from typing import Any
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.models.ask import AskRequest
from app.providers.conversation_files.base import ConversationFileProviderError
from app.services.file_route_gates import file_status_json_response as _file_status_json_response
from app.services.file_route_gates import route_context_payload as _route_context_payload
from app.services.proxy import ProxyService, StreamingProxyHandle
from app.services.quota_proxy import QuotaProxyResult, QuotaProxyService
from app.services.usage_stats_client import UsageStatsClient
from app.services.sse_frames import SSEFrameBuffer, parse_sse_json_frame
from app.utils.user_errors import humanize_exception, sse_escape_message, user_message_for_code

router = APIRouter(tags=["qa"])
logger = logging.getLogger(__name__)

_ALLOWED_MODES = {"fast", "thinking", "patent"}
_FILE_ROUTES = {"pdf_qa", "tabular_qa", "hybrid_qa"}


def _format_log_fields(**fields: Any) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _log_gateway_event(event: str, **fields: Any) -> None:
    suffix = _format_log_fields(**fields)
    logger.info("gateway %s%s", event, f" {suffix}" if suffix else "")


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
        "needs_clarification": route_decision.needs_clarification,
        "used_files": file_context.used_files,
        "execution_files": route_decision.execution_files,
        "selected_file_ids": route_decision.selected_file_ids,
        "strategy": route_decision.strategy,
        "primary_file_id": route_decision.primary_file_id,
        "file_selection": route_decision.file_selection,
        "route_reasons": route_decision.route_reasons,
        "route_confidence": route_decision.route_confidence,
        "classifier_used": route_decision.classifier_used,
        "trace_id": trace_id,
        "options": payload.options,
    }


def _log_route_decision(*, trace_id: str, route_decision) -> None:
    logger.info(
        "gateway route decision trace_id=%s requested_mode=%s actual_mode=%s route=%s turn_mode=%s source_scope=%s selected_file_ids=%s strategy=%s route_reasons=%s classifier_used=%s route_confidence=%s",
        trace_id,
        route_decision.requested_mode,
        route_decision.actual_mode,
        route_decision.route,
        route_decision.turn_mode,
        route_decision.source_scope,
        list(route_decision.selected_file_ids or []),
        route_decision.strategy,
        list(route_decision.route_reasons or []),
        route_decision.classifier_used,
        route_decision.route_confidence,
    )


def _quota_type_for_route(route_decision) -> str | None:
    if str(route_decision.route or "").strip().lower() in _FILE_ROUTES:
        return "file_qa"
    return "ask_query"


def _normalized_positive_user_id(value) -> int | None:
    try:
        user_id = int(value)
    except Exception:
        return None
    return user_id if user_id > 0 else None


def _conversation_id_int(value: Any) -> int | None:
    try:
        conversation_id = int(value)
    except Exception:
        return None
    return conversation_id if conversation_id > 0 else None


async def _record_ask_usage_activity(
    request: Request,
    *,
    user_id: int | None,
    quota_type: str | None,
    trace_id: str,
    conversation_id: int | None,
    success: bool,
) -> None:
    if not success:
        return
    normalized_type = str(quota_type or "").strip().lower()
    if normalized_type not in {"ask_query", "file_qa"}:
        return
    if user_id is None:
        return
    client: UsageStatsClient | None = getattr(request.app.state, "usage_stats_client", None)
    if client is None:
        return
    await client.record_event(
        request=request,
        user_id=int(user_id),
        event_type=normalized_type,
        trace_id=trace_id,
        conversation_id=conversation_id,
    )


def _sync_json_payload(response: JSONResponse | StreamingResponse | object) -> dict | None:
    body = getattr(response, "body", None)
    if body in (None, b""):
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _should_count_sync_response(response) -> bool:
    if int(getattr(response, "status_code", 500) or 500) >= 400:
        return False
    payload = _sync_json_payload(response)
    if payload is None:
        return False
    if payload.get("success") is False:
        return False
    if payload.get("error"):
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
    grant_id: str | None,
    success: bool = False,
) -> None:
    if not str(grant_id or "").strip():
        return
    result = await asyncio.shield(quota_proxy.finalize(request=request, grant_id=str(grant_id), success=success))
    if not result.success:
        logger.warning(
            "gateway quota finalize failed: grant_id=%s success=%s status=%s code=%s error=%s",
            grant_id,
            success,
            result.status_code,
            result.payload.get("code"),
            result.payload.get("error"),
        )


def _encode_sse_payload(payload: dict, *, prefix_lines: list[str] | None = None) -> bytes:
    lines = [line for line in list(prefix_lines or []) if str(line or "").strip()]
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _stream_error_frame_bytes(*, trace_id: str, backend: str, exc: Exception) -> bytes:
    message = sse_escape_message(
        humanize_exception(exc, code="UPSTREAM_STREAM_UNAVAILABLE", error="upstream_stream_unavailable")
    )
    return (
        'data: {"type":"error","code":"UPSTREAM_STREAM_UNAVAILABLE","error":"upstream_stream_unavailable","message":"%s","backend":"%s","retriable":true,"trace_id":"%s"}\n\n'
        % (message, backend.replace('"', '\\"'), trace_id)
    ).encode("utf-8")


async def _stream_with_quota(
    *,
    handle: StreamingProxyHandle,
    request: Request,
    quota_proxy: QuotaProxyService,
    grant_id: str | None,
    quota_type: str | None,
    trace_id: str,
    backend: str,
    user_id: int | None = None,
    conversation_id: int | None = None,
):
    has_quota_grant = bool(str(grant_id or "").strip() and str(quota_type or "").strip())

    frame_buffer = SSEFrameBuffer()
    done_payload: dict | None = None
    done_prefix_lines: list[str] = []
    stream_started = time.perf_counter()
    saw_error_event = False
    finalized = False
    stream_error: Exception | None = None
    first_step_logged = False
    first_content_logged = False
    try:
        async for chunk in handle.body_iter():
            outbound_frames: list[str] = []
            for frame in frame_buffer.feed(chunk):
                payload, prefix_lines = parse_sse_json_frame(frame)
                if isinstance(payload, dict):
                    payload_type = str(payload.get("type") or "").strip().lower()
                    if payload_type == "error":
                        saw_error_event = True
                    if payload_type == "step" and not first_step_logged:
                        first_step_logged = True
                        _log_gateway_event(
                            "ask_stream first_step",
                            trace_id=trace_id,
                            backend=backend,
                            step=str(payload.get("step") or ""),
                            elapsed_ms=round((time.perf_counter() - stream_started) * 1000, 3),
                        )
                    if payload_type == "content" and not first_content_logged:
                        content = str(payload.get("content") or payload.get("delta") or "")
                        if content:
                            first_content_logged = True
                            _log_gateway_event(
                                "ask_stream first_content",
                                trace_id=trace_id,
                                backend=backend,
                                content_chars=len(content),
                                elapsed_ms=round((time.perf_counter() - stream_started) * 1000, 3),
                            )
                    if payload_type == "done":
                        done_payload = payload
                        done_prefix_lines = prefix_lines
                        continue
                outbound_frames.append(frame)
            for frame in outbound_frames:
                yield f"{frame}\n\n".encode("utf-8")
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        stream_error = exc
    except BaseException:
        if not finalized:
            await _abort_quota_grant(
                request=request,
                quota_proxy=quota_proxy,
                grant_id=grant_id,
                success=done_payload is not None and not saw_error_event,
            )
            finalized = True
        raise

    if stream_error is not None:
        if done_payload is None:
            if not finalized:
                await _abort_quota_grant(request=request, quota_proxy=quota_proxy, grant_id=grant_id)
                finalized = True
            yield _stream_error_frame_bytes(trace_id=trace_id, backend=backend, exc=stream_error)
            return

    buffer = frame_buffer.flush()
    if buffer is not None:
        payload, prefix_lines = parse_sse_json_frame(buffer)
        if isinstance(payload, dict):
            payload_type = str(payload.get("type") or "").strip().lower()
            if payload_type == "error":
                saw_error_event = True
            if payload_type == "done":
                done_payload = payload
                done_prefix_lines = prefix_lines
            else:
                yield buffer.encode("utf-8")
        else:
            yield buffer.encode("utf-8")

    stream_success = done_payload is not None and not saw_error_event
    finalize_result: QuotaProxyResult | None = None
    if has_quota_grant:
        finalize_result = await asyncio.shield(
            quota_proxy.finalize(
                request=request,
                grant_id=str(grant_id),
                success=stream_success,
            )
        )
        finalized = True
        if not finalize_result.success:
            logger.warning(
                "gateway stream quota finalize failed: grant_id=%s quota_type=%s status=%s code=%s error=%s",
                grant_id,
                quota_type,
                finalize_result.status_code,
                finalize_result.payload.get("code"),
                finalize_result.payload.get("error"),
            )
    await _record_ask_usage_activity(
        request=request,
        user_id=user_id,
        quota_type=quota_type,
        trace_id=trace_id,
        conversation_id=conversation_id,
        success=stream_success,
    )
    if done_payload is not None:
        done_payload = dict(done_payload)
        if has_quota_grant:
            done_payload["quota"] = _quota_payload_from_finalize(
                quota_type=str(quota_type),
                finalize_result=finalize_result,
            )
        _log_gateway_event(
            "ask_stream finalized",
            trace_id=trace_id,
            backend=backend,
            quota_type=quota_type,
            success=stream_success,
            elapsed_ms=round((time.perf_counter() - stream_started) * 1000, 3),
        )
        yield _encode_sse_payload(done_payload, prefix_lines=done_prefix_lines)


def _conversation_files_error_json(*, trace_id: str, exc: ConversationFileProviderError) -> JSONResponse:
    return JSONResponse(
        status_code=int(getattr(exc, "status_code", 503) or 503),
        content={
            "success": False,
            "code": "CONVERSATION_FILE_PROVIDER_UNAVAILABLE",
            "error": "conversation_file_provider_unavailable",
            "message": humanize_exception(
                exc,
                code="CONVERSATION_FILE_PROVIDER_UNAVAILABLE",
                error="conversation_file_provider_unavailable",
            ),
            "provider": getattr(exc, "provider", "unknown"),
            "trace_id": trace_id,
        },
    )


def _conversation_files_error_stream(*, trace_id: str, exc: ConversationFileProviderError) -> StreamingResponse:
    message = sse_escape_message(
        humanize_exception(
            exc,
            code="CONVERSATION_FILE_PROVIDER_UNAVAILABLE",
            error="conversation_file_provider_unavailable",
        )
    )

    def _frames():
        yield (
            'data: {"type":"error","code":"CONVERSATION_FILE_PROVIDER_UNAVAILABLE","error":"conversation_file_provider_unavailable","message":"%s","provider":"%s","retriable":true,"trace_id":"%s"}\n\n'
            % (message, str(getattr(exc, "provider", "unknown") or "unknown").replace('"', '\\"'), trace_id)
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
            "message": route_decision.clarification_message or user_message_for_code(
                "FILE_SELECTION_CLARIFICATION_REQUIRED"
            ),
            "trace_id": trace_id,
            "requested_mode": route_decision.requested_mode,
            "actual_mode": route_decision.actual_mode,
            "route": route_decision.route,
            "needs_clarification": route_decision.needs_clarification,
            "detail": {
                "clarify_candidates": list(route_decision.clarify_candidates or []),
                **_route_context_payload(route_decision),
            },
        },
    )


def _clarification_stream(*, trace_id: str, route_decision) -> StreamingResponse:
    def _frames():
        yield (
            'data: {"type":"metadata","requested_mode":"%s","actual_mode":"%s","route":"%s","needs_clarification":%s,"clarify_candidates":%s,"source_scope":%s,"selected_file_ids":%s,"strategy":%s,"file_selection":%s,"route_reasons":%s,"route_confidence":%s,"classifier_used":%s,"trace_id":"%s"}\n\n'
            % (
                route_decision.requested_mode,
                route_decision.actual_mode,
                route_decision.route,
                "true" if route_decision.needs_clarification else "false",
                json.dumps(list(route_decision.clarify_candidates or []), ensure_ascii=False),
                json.dumps(route_decision.source_scope, ensure_ascii=False),
                json.dumps(list(route_decision.selected_file_ids or []), ensure_ascii=False),
                json.dumps(route_decision.strategy, ensure_ascii=False),
                json.dumps(dict(route_decision.file_selection or {}), ensure_ascii=False),
                json.dumps(list(route_decision.route_reasons or []), ensure_ascii=False),
                json.dumps(route_decision.route_confidence, ensure_ascii=False),
                json.dumps(route_decision.classifier_used, ensure_ascii=False),
                trace_id,
            )
        )
        yield (
            'data: {"type":"error","code":"FILE_SELECTION_CLARIFICATION_REQUIRED","error":"file_selection_clarification_required","message":"%s","retriable":false,"trace_id":"%s"}\n\n'
            % (
                sse_escape_message(
                    route_decision.clarification_message
                    or user_message_for_code("FILE_SELECTION_CLARIFICATION_REQUIRED")
                ),
                trace_id,
            )
        )

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _file_status_json(*, trace_id: str, route_decision) -> JSONResponse:
    return _file_status_json_response(trace_id=trace_id, route_decision=route_decision)


def _file_status_stream(*, trace_id: str, route_decision) -> StreamingResponse:
    def _frames():
        yield (
            'data: {"type":"metadata","requested_mode":"%s","actual_mode":"%s","route":"%s","source_scope":%s,"selected_file_ids":%s,"strategy":%s,"file_selection":%s,"route_reasons":%s,"route_confidence":%s,"classifier_used":%s,"trace_id":"%s"}\n\n'
            % (
                route_decision.requested_mode,
                route_decision.actual_mode,
                route_decision.route,
                json.dumps(route_decision.source_scope, ensure_ascii=False),
                json.dumps(list(route_decision.selected_file_ids or []), ensure_ascii=False),
                json.dumps(route_decision.strategy, ensure_ascii=False),
                json.dumps(dict(route_decision.file_selection or {}), ensure_ascii=False),
                json.dumps(list(route_decision.route_reasons or []), ensure_ascii=False),
                json.dumps(route_decision.route_confidence, ensure_ascii=False),
                json.dumps(route_decision.classifier_used, ensure_ascii=False),
                trace_id,
            )
        )
        yield (
            'data: {"type":"error","code":"%s","error":"%s","message":"%s","retriable":%s,"trace_id":"%s"}\n\n'
            % (
                (route_decision.status_code or "FILE_STATE_BLOCKED").replace('"', '\\"'),
                (route_decision.status_error or "file_state_blocked").replace('"', '\\"'),
                (route_decision.status_message or route_decision.status_code or "file_state_blocked").replace('"', '\\"'),
                "true" if route_decision.status_retriable else "false",
                trace_id,
            )
        )

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _patent_file_routes_disabled(*, request: Request, route_decision) -> bool:
    settings = getattr(request.app.state, "settings", None)
    if route_decision.requested_mode != "patent":
        return False
    if str(route_decision.route or "").strip().lower() not in _FILE_ROUTES:
        return False
    return not bool(getattr(settings, "patent_file_routes_enabled", False))


def _patent_file_route_disabled_json(*, trace_id: str, route_decision) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "code": "PATENT_FILE_ROUTE_DISABLED",
            "error": "patent_file_route_disabled",
            "message": user_message_for_code("PATENT_FILE_ROUTE_DISABLED"),
            "retriable": False,
            "trace_id": trace_id,
            "requested_mode": route_decision.requested_mode,
            "actual_mode": route_decision.actual_mode,
            "route": route_decision.route,
            "detail": _route_context_payload(route_decision),
        },
        headers={"X-Gateway-Backend": route_decision.actual_mode},
    )


def _patent_file_route_disabled_stream(*, trace_id: str, route_decision) -> StreamingResponse:
    def _frames():
        yield (
            'data: {"type":"metadata","requested_mode":"%s","actual_mode":"%s","route":"%s","source_scope":%s,"selected_file_ids":%s,"strategy":%s,"file_selection":%s,"route_reasons":%s,"route_confidence":%s,"classifier_used":%s,"trace_id":"%s"}\n\n'
            % (
                route_decision.requested_mode,
                route_decision.actual_mode,
                route_decision.route,
                json.dumps(route_decision.source_scope, ensure_ascii=False),
                json.dumps(list(route_decision.selected_file_ids or []), ensure_ascii=False),
                json.dumps(route_decision.strategy, ensure_ascii=False),
                json.dumps(dict(route_decision.file_selection or {}), ensure_ascii=False),
                json.dumps(list(route_decision.route_reasons or []), ensure_ascii=False),
                json.dumps(route_decision.route_confidence, ensure_ascii=False),
                json.dumps(route_decision.classifier_used, ensure_ascii=False),
                trace_id,
            )
        )
        yield (
            'data: {"type":"error","code":"PATENT_FILE_ROUTE_DISABLED","error":"patent_file_route_disabled","message":"%s","retriable":false,"trace_id":"%s"}\n\n'
            % (sse_escape_message(user_message_for_code("PATENT_FILE_ROUTE_DISABLED")), trace_id)
        )

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Gateway-Backend": route_decision.actual_mode},
    )


def _upstream_stream_error_stream(*, trace_id: str, backend: str, exc: Exception) -> StreamingResponse:
    message = sse_escape_message(
        humanize_exception(exc, code="UPSTREAM_STREAM_UNAVAILABLE", error="upstream_stream_unavailable")
    )

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


def _quota_precheck_error_stream(*, trace_id: str, route_decision, result: QuotaProxyResult) -> StreamingResponse:
    payload = dict(result.payload or {})
    message = humanize_exception(
        str(payload.get("message") or payload.get("error") or "quota_precheck_failed"),
        code=str(payload.get("code") or "QUOTA_PRECHECK_FAILED"),
        error=str(payload.get("error") or "quota_precheck_failed"),
    )
    code = str(payload.get("code") or "QUOTA_PRECHECK_FAILED")
    error = str(payload.get("error") or "quota_precheck_failed")
    data = payload.get("data")

    def _frames():
        yield (
            'data: {"type":"metadata","requested_mode":"%s","actual_mode":"%s","route":"%s","trace_id":"%s"}\n\n'
            % (route_decision.requested_mode, route_decision.actual_mode, route_decision.route, trace_id)
        )
        error_payload = {
            "type": "error",
            "code": code,
            "error": error,
            "message": message,
            "retriable": False,
            "trace_id": trace_id,
        }
        if isinstance(data, dict):
            error_payload["data"] = data
        yield _encode_sse_payload(error_payload)

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Gateway-Backend": route_decision.actual_mode},
    )


def _upstream_status_error_stream(*, trace_id: str, backend: str, status_code: int, message: str) -> StreamingResponse:
    escaped = sse_escape_message(
        humanize_exception(message or "upstream_error", code="UPSTREAM_ERROR", error="upstream_error")
    )

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
    started = time.perf_counter()
    trace_id = str(getattr(request.state, "trace_id", "") or "")
    _log_gateway_event("ask start", trace_id=trace_id, requested_mode=mode)
    try:
        route_decision, file_context = await _resolve(request, payload, mode)
    except ConversationFileProviderError as exc:
        return _conversation_files_error_json(trace_id=trace_id, exc=exc)
    _log_route_decision(trace_id=trace_id, route_decision=route_decision)
    _log_gateway_event(
        "ask route resolved",
        trace_id=trace_id,
        requested_mode=route_decision.requested_mode,
        actual_mode=route_decision.actual_mode,
        route=route_decision.route,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
    )
    if route_decision.needs_clarification:
        return _clarification_json(trace_id=trace_id, route_decision=route_decision)
    if route_decision.status_code:
        return _file_status_json(trace_id=trace_id, route_decision=route_decision)
    if _patent_file_routes_disabled(request=request, route_decision=route_decision):
        return _patent_file_route_disabled_json(trace_id=trace_id, route_decision=route_decision)

    registry = request.app.state.backend_registry
    proxy_service: ProxyService = request.app.state.proxy_service
    quota_proxy: QuotaProxyService = request.app.state.quota_proxy_service
    upstream_payload = _normalized_payload(
        payload=payload,
        route_decision=route_decision,
        file_context=file_context,
        trace_id=trace_id,
    )
    quota_type = _quota_type_for_route(route_decision)
    grant_id: str | None = None
    user_id = _normalized_positive_user_id(payload.user_id)
    if quota_type is not None and user_id is not None:
        precheck = await quota_proxy.precheck(
            request=request,
            user_id=user_id,
            quota_type=quota_type,
            strict_config=False,
        )
        if not precheck.success:
            logger.warning(
                "gateway quota precheck failed: mode=%s route=%s quota_type=%s status=%s code=%s error=%s",
                route_decision.actual_mode,
                route_decision.route,
                quota_type,
                precheck.status_code,
                precheck.payload.get("code"),
                precheck.payload.get("error"),
            )
            return JSONResponse(status_code=precheck.status_code, content=precheck.payload)
        grant_data = precheck.payload.get("data") if isinstance(precheck.payload.get("data"), dict) else {}
        grant_id = str(grant_data.get("grant_id") or "").strip() or None
        _log_gateway_event(
            "ask quota precheck completed",
            trace_id=trace_id,
            quota_type=quota_type,
            grant_id=grant_id or "-",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )
    path = f"/api/{route_decision.actual_mode}/ask"
    response = await proxy_service.forward_json(
        request=request,
        target=registry.get(route_decision.actual_mode),
        path=path,
        payload=upstream_payload,
    )
    if quota_type is None or not grant_id:
        if _should_count_sync_response(response):
            await _record_ask_usage_activity(
                request=request,
                user_id=user_id,
                quota_type=quota_type,
                trace_id=trace_id,
                conversation_id=_conversation_id_int(payload.conversation_id),
                success=True,
            )
        _log_gateway_event(
            "ask completed",
            trace_id=trace_id,
            backend=route_decision.actual_mode,
            route=route_decision.route,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        return response
    if not _should_count_sync_response(response):
        await _abort_quota_grant(request=request, quota_proxy=quota_proxy, grant_id=grant_id)
        return response
    finalize_result = await quota_proxy.finalize(request=request, grant_id=grant_id, success=True)
    if not finalize_result.success:
        logger.warning(
            "gateway sync quota finalize failed: grant_id=%s quota_type=%s status=%s code=%s error=%s",
            grant_id,
            quota_type,
            finalize_result.status_code,
            finalize_result.payload.get("code"),
            finalize_result.payload.get("error"),
        )
    await _record_ask_usage_activity(
        request=request,
        user_id=user_id,
        quota_type=quota_type,
        trace_id=trace_id,
        conversation_id=_conversation_id_int(payload.conversation_id),
        success=True,
    )
    _log_gateway_event(
        "ask completed",
        trace_id=trace_id,
        backend=route_decision.actual_mode,
        route=route_decision.route,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
    )
    return _with_sync_quota_payload(response, quota_type=quota_type, finalize_result=finalize_result)


async def _proxy_ask_stream(request: Request, payload: AskRequest, mode: str):
    started = time.perf_counter()
    trace_id = str(getattr(request.state, "trace_id", "") or "")
    _log_gateway_event("ask_stream start", trace_id=trace_id, requested_mode=mode)
    try:
        route_decision, file_context = await _resolve(request, payload, mode)
    except ConversationFileProviderError as exc:
        return _conversation_files_error_stream(trace_id=trace_id, exc=exc)
    _log_route_decision(trace_id=trace_id, route_decision=route_decision)
    _log_gateway_event(
        "ask_stream route resolved",
        trace_id=trace_id,
        requested_mode=route_decision.requested_mode,
        actual_mode=route_decision.actual_mode,
        route=route_decision.route,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
    )
    if route_decision.needs_clarification:
        return _clarification_stream(trace_id=trace_id, route_decision=route_decision)
    if route_decision.status_code:
        return _file_status_stream(trace_id=trace_id, route_decision=route_decision)
    if _patent_file_routes_disabled(request=request, route_decision=route_decision):
        return _patent_file_route_disabled_stream(trace_id=trace_id, route_decision=route_decision)

    registry = request.app.state.backend_registry
    proxy_service: ProxyService = request.app.state.proxy_service
    quota_proxy: QuotaProxyService = request.app.state.quota_proxy_service
    upstream_payload = _normalized_payload(
        payload=payload,
        route_decision=route_decision,
        file_context=file_context,
        trace_id=trace_id,
    )
    quota_type = _quota_type_for_route(route_decision)
    grant_id: str | None = None
    user_id = _normalized_positive_user_id(payload.user_id)
    if quota_type is not None and user_id is not None:
        precheck = await quota_proxy.precheck(
            request=request,
            user_id=user_id,
            quota_type=quota_type,
            strict_config=False,
        )
        if not precheck.success:
            logger.warning(
                "gateway quota precheck failed: mode=%s route=%s quota_type=%s status=%s code=%s error=%s",
                route_decision.actual_mode,
                route_decision.route,
                quota_type,
                precheck.status_code,
                precheck.payload.get("code"),
                precheck.payload.get("error"),
            )
            return _quota_precheck_error_stream(trace_id=trace_id, route_decision=route_decision, result=precheck)
        grant_data = precheck.payload.get("data") if isinstance(precheck.payload.get("data"), dict) else {}
        grant_id = str(grant_data.get("grant_id") or "").strip() or None
        _log_gateway_event(
            "ask_stream quota precheck completed",
            trace_id=trace_id,
            quota_type=quota_type,
            grant_id=grant_id or "-",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
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
        await _abort_quota_grant(request=request, quota_proxy=quota_proxy, grant_id=grant_id)
        return _upstream_stream_error_stream(trace_id=trace_id, backend=route_decision.actual_mode, exc=exc)

    if handle.status_code >= 400 and "text/event-stream" not in str(handle.headers.get("content-type") or ""):
        body = await handle.upstream.aread()
        await handle.upstream.aclose()
        await handle.client.aclose()
        await _abort_quota_grant(request=request, quota_proxy=quota_proxy, grant_id=grant_id)
        return _upstream_status_error_stream(
            trace_id=trace_id,
            backend=handle.backend,
            status_code=handle.status_code,
            message=humanize_exception(
                body.decode("utf-8", errors="ignore") or "upstream_error",
                code="UPSTREAM_ERROR",
                error="upstream_error",
            ),
        )
    _log_gateway_event(
        "ask_stream upstream opened",
        trace_id=trace_id,
        backend=handle.backend,
        route=route_decision.route,
        status_code=handle.status_code,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
    )

    return StreamingResponse(
        _stream_with_quota(
            handle=handle,
            request=request,
            quota_proxy=quota_proxy,
            grant_id=grant_id,
            quota_type=quota_type,
            trace_id=trace_id,
            backend=handle.backend,
            user_id=user_id,
            conversation_id=_conversation_id_int(payload.conversation_id),
        ),
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
