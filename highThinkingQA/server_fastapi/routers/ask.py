"""FastAPI ask/ask_stream routes."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from server.errors import codes
from server.errors.core import APIError, raise_invalid_request
from server.runtime.request_context import get_trace_id
from server.schemas.request_models import (
    ModeMismatchRequestError,
    ModeNotSupportedRequestError,
    parse_ask_request,
)
from server.services.ask_service import (
    AskServiceError,
    AskTimeoutError,
    ModeNotImplementedError,
    ModeNotSupportedError,
    execute_ask,
    stream_ask_events,
)
from server.services import chat_persistence
from server_fastapi.auth.deps import AuthContext, require_auth_context
from server_fastapi.http import read_json_payload

router = APIRouter()


def _acquire_slot_or_raise(request: Request):
    semaphore = request.app.state.ask_slots
    if semaphore.acquire(blocking=False):
        return semaphore
    raise APIError(
        code=codes.ASK_STREAM_BUSY,
        message=f"too many running requests, max={request.app.state.config['ASK_STREAM_MAX_CONCURRENT']}",
        status_code=429,
        error="server_busy",
        retriable=True,
    )


@contextmanager
def _ask_slot_guard(request: Request) -> Iterator[None]:
    semaphore = _acquire_slot_or_raise(request)
    try:
        yield
    finally:
        semaphore.release()


def _to_sse_line(payload: dict, *, seq: int) -> str:
    data = dict(payload)
    data.setdefault("seq", seq)
    data.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _handle_service_error(exc: Exception) -> APIError:
    if isinstance(exc, ModeNotImplementedError):
        return APIError(
            code=codes.NOT_IMPLEMENTED,
            message=str(exc),
            status_code=501,
            error="not_implemented",
            retriable=False,
        )
    if isinstance(exc, ModeNotSupportedError):
        return APIError(
            code=codes.MODE_NOT_SUPPORTED,
            message=str(exc),
            status_code=400,
            error="mode_not_supported",
            retriable=False,
        )
    if isinstance(exc, AskTimeoutError):
        return APIError(
            code=codes.UPSTREAM_TIMEOUT,
            message=str(exc),
            status_code=504,
            error="upstream_timeout",
            retriable=True,
        )
    if isinstance(exc, AskServiceError):
        return APIError(
            code=codes.UPSTREAM_ERROR,
            message=str(exc),
            status_code=502,
            error="upstream_error",
            retriable=True,
        )
    return APIError(
        code=codes.INTERNAL_ERROR,
        message="internal server error",
        status_code=500,
        error="internal_error",
        retriable=False,
    )


async def _parse_request_or_raise(request: Request, *, forced_mode: str | None = None):
    payload = await read_json_payload(request)
    try:
        return parse_ask_request(payload, forced_mode=forced_mode)
    except ModeMismatchRequestError as exc:
        raise APIError(
            code=codes.MODE_MISMATCH,
            message=str(exc),
            status_code=400,
            error="invalid_request",
            retriable=False,
        )
    except ModeNotSupportedRequestError as exc:
        raise APIError(
            code=codes.MODE_NOT_SUPPORTED,
            message=str(exc),
            status_code=400,
            error="mode_not_supported",
            retriable=False,
        )
    except ValueError as exc:
        raise_invalid_request(str(exc))


def _bind_auth_context(ask_request, context: AuthContext):
    if ask_request.user_id is not None and int(ask_request.user_id) != int(context.user_id):
        raise_invalid_request("user_id in token and body are inconsistent")
    return replace(ask_request, user_id=int(context.user_id))


def _chat_persist_enabled(request: Request) -> bool:
    return bool(request.app.state.config.get("CHAT_PERSIST_ENABLED", False))


def _chat_persist_async_enabled(request: Request) -> bool:
    return bool(request.app.state.config.get("CHAT_PERSIST_ASYNC", True))


def _conversation_id_int(value) -> int | None:
    if value is None:
        return None
    try:
        cid = int(value)
    except Exception:
        return None
    if cid <= 0:
        return None
    return cid


def _persistence_key(*, user_id: int, conversation_id: int) -> str:
    return f"conversation:{int(user_id)}:{int(conversation_id)}"



def _persist_user_message_if_needed(*, request: Request, ask_request) -> None:
    if not _chat_persist_enabled(request):
        return
    user_id = int(ask_request.user_id) if ask_request.user_id else None
    conversation_id = _conversation_id_int(ask_request.conversation_id)
    if not user_id or not conversation_id:
        return
    chat_persistence.persist_user_message(
        user_id=user_id,
        conversation_id=conversation_id,
        question=str(ask_request.question or ""),
        trace_id=str(getattr(ask_request, "trace_id", "") or ""),
        route=str(getattr(ask_request, "route", "thinking_qa") or "thinking_qa"),
        requested_mode=str(getattr(ask_request, "requested_mode", getattr(ask_request, "mode", "thinking")) or "thinking"),
        actual_mode=str(getattr(ask_request, "actual_mode", getattr(ask_request, "mode", "thinking")) or "thinking"),
        payload=ask_request,
        async_enabled=_chat_persist_async_enabled(request),
    )


def _persist_assistant_message_if_needed(*, request: Request, ask_request, summary: dict) -> None:
    if not _chat_persist_enabled(request):
        return
    user_id = int(ask_request.user_id) if ask_request.user_id else None
    conversation_id = _conversation_id_int(ask_request.conversation_id)
    if not user_id or not conversation_id:
        return
    if not bool(summary.get("done_seen")):
        request.app.logger.info("skip assistant persistence: stream finished before done event")
        return
    content = str(summary.get("assistant_content") or "").strip()
    if not content:
        return
    chat_persistence.persist_assistant_summary(
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id=str(getattr(ask_request, "trace_id", "") or ""),
        route=str(getattr(ask_request, "route", "thinking_qa") or "thinking_qa"),
        requested_mode=str(getattr(ask_request, "requested_mode", getattr(ask_request, "mode", "thinking")) or "thinking"),
        actual_mode=str(getattr(ask_request, "actual_mode", getattr(ask_request, "mode", "thinking")) or "thinking"),
        summary=dict(summary or {}),
        async_enabled=_chat_persist_async_enabled(request),
    )


def _build_stream_response(*, request: Request, ask_request, trace_id: str, slot) -> StreamingResponse:
    def _generate():
        seq = 0
        summary_lock = threading.Lock()
        assistant_persisted = False
        summary = {
            "assistant_content": "",
            "query_mode": "",
            "references": [],
            "reference_objects": [],
            "reference_links": [],
            "pdf_links": [],
            "doi_locations": {},
            "steps": [],
            "route": "",
            "used_files": [],
            "timings": {},
            "trace_id": trace_id,
            "file_selection": {},
            "done_seen": False,
        }

        def _ingest_done_payload(payload: dict) -> None:
            refs = payload.get("references")
            if isinstance(refs, list):
                summary["references"] = refs
            reference_objects = payload.get("reference_objects")
            if isinstance(reference_objects, list):
                summary["reference_objects"] = reference_objects
            ref_links = payload.get("reference_links")
            if isinstance(ref_links, list):
                summary["reference_links"] = ref_links
            pdf_links = payload.get("pdf_links")
            if isinstance(pdf_links, list):
                summary["pdf_links"] = pdf_links
            doi_locations = payload.get("doi_locations")
            if isinstance(doi_locations, dict):
                summary["doi_locations"] = doi_locations
            final_answer = str(payload.get("final_answer") or "").strip()
            if final_answer:
                summary["assistant_content"] = final_answer
            summary["route"] = str(payload.get("route") or summary["route"])
            used_files = payload.get("used_files")
            if isinstance(used_files, list):
                summary["used_files"] = used_files
            timings = payload.get("timings")
            if isinstance(timings, dict):
                summary["timings"] = timings
            summary["trace_id"] = str(payload.get("trace_id") or summary["trace_id"])
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            query_mode = str(
                payload.get("query_mode")
                or metadata.get("query_mode")
                or summary["query_mode"]
            ).strip()
            if query_mode:
                summary["query_mode"] = query_mode
            file_selection = payload.get("file_selection")
            if isinstance(file_selection, dict):
                summary["file_selection"] = file_selection
            summary["done_seen"] = True

        def _persist_summary_once() -> None:
            nonlocal assistant_persisted
            if assistant_persisted:
                return
            if not bool(summary.get("done_seen")):
                return
            content = str(summary.get("assistant_content") or "").strip()
            if not content:
                return
            _persist_assistant_message_if_needed(request=request, ask_request=ask_request, summary=dict(summary))
            assistant_persisted = True

        def _completion_callback(payload: dict) -> None:
            with summary_lock:
                _ingest_done_payload(payload)
                _persist_summary_once()

        try:
            for payload in stream_ask_events(
                request=ask_request,
                timeout_seconds=int(request.app.state.config["ASK_TIMEOUT_SECONDS"]),
                heartbeat_seconds=int(request.app.state.config["SSE_HEARTBEAT_SECONDS"]),
                trace_id=trace_id,
                completion_callback=_completion_callback,
            ):
                event_type = str(payload.get("type") or "")
                if event_type == "content":
                    with summary_lock:
                        summary["assistant_content"] += str(payload.get("content") or "")
                elif event_type == "metadata":
                    with summary_lock:
                        summary["query_mode"] = str(payload.get("query_mode") or summary["query_mode"])
                elif event_type == "step":
                    with summary_lock:
                        summary_steps = summary.get("steps")
                        if isinstance(summary_steps, list):
                            summary_steps.append(
                                {
                                    "step": payload.get("step"),
                                    "message": payload.get("message"),
                                    "status": payload.get("status"),
                                    "data": payload.get("data"),
                                }
                            )
                elif event_type == "done":
                    with summary_lock:
                        _ingest_done_payload(payload)
                seq += 1
                yield _to_sse_line(payload, seq=seq)
        except Exception as exc:  # pragma: no cover - defensive
            mapped = _handle_service_error(exc)
            yield _to_sse_line(
                {
                    "type": "error",
                    "code": mapped.code,
                    "error": mapped.error,
                    "message": mapped.message,
                    "retriable": mapped.retriable,
                    "trace_id": trace_id,
                },
                seq=seq + 1,
            )
        finally:
            try:
                with summary_lock:
                    _persist_summary_once()
            except Exception as exc:  # pragma: no cover
                request.app.logger.warning("assistant persistence hook failed: %s", exc)
            slot.release()

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/v1/ask")
@router.post("/api/ask")
async def ask_v1(request: Request, context: AuthContext = Depends(require_auth_context)):
    ask_request = _bind_auth_context(await _parse_request_or_raise(request), context)
    trace_id = get_trace_id()

    with _ask_slot_guard(request):
        _persist_user_message_if_needed(request=request, ask_request=ask_request)
        try:
            data = execute_ask(
                request=ask_request,
                timeout_seconds=int(request.app.state.config["ASK_TIMEOUT_SECONDS"]),
                trace_id=trace_id,
            )
        except Exception as exc:  # pragma: no cover - transport-level mapping
            raise _handle_service_error(exc)
        try:
            _persist_assistant_message_if_needed(
                request=request,
                ask_request=ask_request,
                summary={
                    "assistant_content": str(data.get("final_answer") or ""),
                    "query_mode": str((data.get("metadata") or {}).get("query_mode") or ""),
                    "references": data.get("references") or [],
                    "steps": [],
                    "done_seen": True,
                },
            )
        except Exception as exc:  # pragma: no cover
            request.app.logger.warning("assistant persistence for ask failed: %s", exc)

    return JSONResponse(content={"success": True, "data": data, "trace_id": trace_id}, status_code=200)


@router.post("/api/v1/{mode}/ask")
@router.post("/api/{mode}/ask")
async def ask_v1_mode(
    mode: str,
    request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    ask_request = _bind_auth_context(await _parse_request_or_raise(request, forced_mode=mode), context)
    trace_id = get_trace_id()

    with _ask_slot_guard(request):
        _persist_user_message_if_needed(request=request, ask_request=ask_request)
        try:
            data = execute_ask(
                request=ask_request,
                timeout_seconds=int(request.app.state.config["ASK_TIMEOUT_SECONDS"]),
                trace_id=trace_id,
            )
        except Exception as exc:  # pragma: no cover - transport-level mapping
            raise _handle_service_error(exc)
        try:
            _persist_assistant_message_if_needed(
                request=request,
                ask_request=ask_request,
                summary={
                    "assistant_content": str(data.get("final_answer") or ""),
                    "query_mode": str((data.get("metadata") or {}).get("query_mode") or ""),
                    "references": data.get("references") or [],
                    "steps": [],
                    "done_seen": True,
                },
            )
        except Exception as exc:  # pragma: no cover
            request.app.logger.warning("assistant persistence for ask failed: %s", exc)

    return JSONResponse(content={"success": True, "data": data, "trace_id": trace_id}, status_code=200)


@router.post("/api/v1/ask_stream")
@router.post("/api/ask_stream")
async def ask_stream_v1(request: Request, context: AuthContext = Depends(require_auth_context)):
    ask_request = _bind_auth_context(await _parse_request_or_raise(request), context)
    trace_id = get_trace_id()
    slot = _acquire_slot_or_raise(request)
    _persist_user_message_if_needed(request=request, ask_request=ask_request)
    return _build_stream_response(request=request, ask_request=ask_request, trace_id=trace_id, slot=slot)


@router.post("/api/v1/{mode}/ask_stream")
@router.post("/api/{mode}/ask_stream")
async def ask_stream_v1_mode(
    mode: str,
    request: Request,
    context: AuthContext = Depends(require_auth_context),
):
    ask_request = _bind_auth_context(await _parse_request_or_raise(request, forced_mode=mode), context)
    trace_id = get_trace_id()
    slot = _acquire_slot_or_raise(request)
    _persist_user_message_if_needed(request=request, ask_request=ask_request)
    return _build_stream_response(request=request, ask_request=ask_request, trace_id=trace_id, slot=slot)
